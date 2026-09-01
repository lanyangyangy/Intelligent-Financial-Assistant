from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import (
    CustomerAssetSnapshot,
    CustomerProfile,
    CustomerProfileTag,
    CustomerRiskAssessment,
    Product,
)
from app.profile_domain.confidence import (
    calculate_dimension_confidence,
    calculate_recommendation_confidence,
    calculate_suitability_confidence,
    resolve_profile_status,
)
from app.profile_domain.models import (
    BusinessType,
    DimensionScores,
    EvidenceSourceType,
    ExtractionMethod,
    ProductRiskLevel,
    ProductRiskSnapshot,
    ProfileEvidence,
    ProfileSnapshot,
    ProfileStatus,
    ProfileStatusContext,
    SuitabilityContext,
)
from app.profile_domain.restrictions import decide_suitability, resolve_product_limit
from app.profile_domain.scoring import calculate_total_score, classify_risk_level
from app.profile_domain.tag_governance import (
    ExtractedProfileTag,
    ProfileTagCode,
    asset_scale,
)
from app.services.investor_scoring_service import InvestorScoringService
from app.services.profile_tag_service import TagGovernanceService


def utcnow() -> datetime:
    return datetime.now(UTC)


def _goal_tag_value(goal: str) -> str | None:
    value = goal.strip()
    if not value:
        return None
    if any(keyword in value for keyword in ("保值", "传承")):
        return "CAPITAL_PRESERVATION"
    if any(keyword in value for keyword in ("稳健", "收益", "均衡")):
        return "STEADY_GROWTH"
    if any(keyword in value for keyword in ("增长", "成长")):
        return "LONG_TERM_GROWTH"
    if any(keyword in value for keyword in ("高风险", "激进", "高收益")):
        return "HIGH_RETURN"
    return None


def _liquidity_tag_value(preference: str) -> str:
    value = preference.strip().lower()
    if value in {"high", "高"}:
        return "HIGH"
    if value in {"low", "低"}:
        return "LOW"
    return "MEDIUM"


class ProfileCalculationService:
    """Build a ProfileSnapshot from persisted customer data using the ported
    domain modules (scoring / confidence / restrictions)."""

    @staticmethod
    def _build_system_tags(
        profile: CustomerProfile, asset: CustomerAssetSnapshot | None
    ) -> list[ExtractedProfileTag]:
        """将已确认的基础资料和资产快照投影为可展示的系统画像标签。"""
        evidence = "系统根据已保存的个人资料和最近资产快照生成。"
        tags: list[ExtractedProfileTag] = []
        goal = _goal_tag_value(profile.investment_goal)
        if goal:
            tags.append(
                ExtractedProfileTag(
                    tag_code=ProfileTagCode.INVESTMENT_GOAL,
                    tag_value=goal,
                    confidence=Decimal("0.90"),
                    evidence_quote=evidence,
                )
            )
        tags.append(
            ExtractedProfileTag(
                tag_code=ProfileTagCode.LIQUIDITY_NEED,
                tag_value=_liquidity_tag_value(profile.liquidity_preference),
                confidence=Decimal("0.85"),
                evidence_quote=evidence,
            )
        )
        if profile.investment_experience_years > 0:
            tags.append(
                ExtractedProfileTag(
                    tag_code=ProfileTagCode.INVESTMENT_EXPERIENCE_YEARS,
                    tag_value=profile.investment_experience_years,
                    confidence=Decimal("0.85"),
                    evidence_quote=evidence,
                )
            )
        if profile.annual_income is not None:
            tags.append(
                ExtractedProfileTag(
                    tag_code=ProfileTagCode.HOUSEHOLD_ANNUAL_INCOME,
                    tag_value=float(profile.annual_income),
                    confidence=Decimal("0.85"),
                    evidence_quote=evidence,
                )
            )
        if asset is not None:
            total_asset = float(asset.total_asset)
            tags.extend(
                [
                    ExtractedProfileTag(
                        tag_code=ProfileTagCode.TOTAL_ASSETS,
                        tag_value=total_asset,
                        confidence=Decimal("0.95"),
                        evidence_quote=evidence,
                    ),
                    ExtractedProfileTag(
                        tag_code=ProfileTagCode.INVESTABLE_ASSETS,
                        tag_value=float(asset.investable_asset),
                        confidence=Decimal("0.95"),
                        evidence_quote=evidence,
                    ),
                    ExtractedProfileTag(
                        tag_code=ProfileTagCode.ASSET_SCALE,
                        tag_value=asset_scale(total_asset),
                        confidence=Decimal("0.90"),
                        evidence_quote=evidence,
                    ),
                ]
            )
        return tags

    async def calculate(self, session: AsyncSession, user_id: str) -> ProfileSnapshot:
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user_id)
            )
        ).scalar_one_or_none()
        if profile is None:
            raise ValueError("customer profile not found")
        risk = (
            (
                await session.execute(
                    select(CustomerRiskAssessment)
                    .where(
                        CustomerRiskAssessment.user_id == user_id,
                        CustomerRiskAssessment.status.in_(["active", "provisional"]),
                    )
                    .order_by(CustomerRiskAssessment.assessed_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        asset = (
            (
                await session.execute(
                    select(CustomerAssetSnapshot)
                    .where(CustomerAssetSnapshot.user_id == user_id)
                    .order_by(CustomerAssetSnapshot.snapshot_time.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

        # ---- dimension scores（投资者风险画像研判规则 四维加权）----
        investor = await InvestorScoringService().score_customer(session, user_id)
        dims = investor["dimensions"]
        dimension_scores = DimensionScores(
            basic_attribute_score=Decimal(str(dims["basic"]["score"])),
            investment_experience_score=Decimal(str(dims["experience"]["score"])),
            risk_preference_score=Decimal(str(dims["preference"]["score"])),
            behavior_stability_score=Decimal(str(dims["behavior"]["score"])),
        )
        total_score = calculate_total_score(dimension_scores)
        model_level = classify_risk_level(total_score)

        # ---- evidence (per dimension) ----
        questionnaire_evidence = ProfileEvidence(
            source_type=EvidenceSourceType.QUESTIONNAIRE,
            extraction_method=ExtractionMethod.DIRECT,
            field_validated=True,
            freshness_decay=Decimal("1"),
            evidence_ref=f"assessment:{risk.id if risk else 'none'}",
        )
        profile_evidence = ProfileEvidence(
            source_type=EvidenceSourceType.KYC,
            extraction_method=ExtractionMethod.RULE,
            field_validated=True,
            freshness_decay=Decimal("1"),
            evidence_ref=f"profile:{profile.id}",
        )
        dimension_evidence = {
            "BASIC": [profile_evidence],
            "EXPERIENCE": [questionnaire_evidence],
            "PREFERENCE": [questionnaire_evidence],
            "BEHAVIOR": [profile_evidence],
        }

        # ---- confidence ----
        dimension_conf: dict[str, Decimal] = {}
        for key, evidence in dimension_evidence.items():
            dimension_conf[key] = calculate_dimension_confidence(evidence)
        completeness = (
            Decimal("0.6")
            if risk is None or risk.status == "provisional"
            else Decimal("0.9")
        )
        # F4.3 基础置信分工具：按证据来源计算基础分并合并进综合置信度，
        # 使 BaseConfidenceCalcTool 参与画像置信度链路（原先为孤立模块）。
        from app.services.confidence_rank_service import BaseConfidenceCalcTool

        base_tool = BaseConfidenceCalcTool()
        base_map = {
            EvidenceSourceType.QUESTIONNAIRE.value: "风评问卷",
            EvidenceSourceType.KYC.value: "AI对话提取",
            EvidenceSourceType.USER_STATED.value: "用户自述",
            EvidenceSourceType.DEFAULT.value: "默认值",
        }
        base_scores = [
            Decimal(
                str(
                    base_tool.calc(
                        source=base_map.get(evidence.source_type.value, "默认值"),
                        freshness_decay=float(evidence.freshness_decay),
                        conflict_count=sum(
                            item.status.value == "CONFLICTED" for item in evidence_list
                        ),
                    )
                )
            )
            for evidence_list in dimension_evidence.values()
            for evidence in evidence_list
        ]
        if base_scores:
            dimension_conf["BASE_CALC"] = Decimal(
                str(sum(base_scores) / len(base_scores))
            ).quantize(Decimal("0.01"))
        suitability_conf = calculate_suitability_confidence(
            dimension_conf, completeness
        )
        recommendation_conf = calculate_recommendation_confidence(
            {"risk_match": Decimal("0.8")}, completeness
        )

        # ---- status ----
        assessment_expired = bool(
            risk is None
            or risk.expires_at is None
            or risk.expires_at <= utcnow()
        )
        has_required = profile.age is not None
        # 存在待复核标签（NEEDS_REVIEW）视为关键冲突，画像需人工审核
        pending_conflict_count = (
            await session.execute(
                select(func.count())
                .select_from(CustomerProfileTag)
                .where(
                    CustomerProfileTag.user_id == user_id,
                    CustomerProfileTag.status == "NEEDS_REVIEW",
                )
            )
        ).scalar_one()
        status = resolve_profile_status(
            ProfileStatusContext(
                assessment_expired=assessment_expired,
                has_required_data=has_required,
                has_critical_conflict=pending_conflict_count > 0,
                suitability_confidence=suitability_conf,
                minimum_suitability_confidence=Decimal("0.80"),
            )
        )
        # F2.1 硬性门槛熔断：年龄 < 18 或 > 80 → 需特批（标记为"需人工审核"）
        if profile.age is not None and (profile.age < 18 or profile.age > 80):
            status = ProfileStatus.NEEDS_REVIEW

        # ---- restriction ----
        check_time = utcnow()
        restriction = resolve_product_limit(
            model_level=model_level,
            age=profile.age or 30,
            assessment_expires_at=risk.expires_at if risk else None,
            check_time=check_time,
            monthly_income=Decimal("0"),
            total_assets=Decimal(str(asset.total_asset)) if asset else None,
        )

        # 画像版本自增（F2.1 画像版本生成）
        profile.profile_version = (profile.profile_version or 1) + 1

        snapshot = ProfileSnapshot(
            customer_id=user_id,
            profile_version=profile.profile_version,
            dimension_scores=dimension_scores,
            model_risk_score=total_score,
            model_risk_level=model_level,
            suitability_confidence=suitability_conf,
            recommendation_confidence=recommendation_conf,
            profile_status=status,
            max_allowed_product_risk=restriction.max_allowed_product_risk,
            restriction_codes=restriction.restriction_codes,
            profile_tags=[],
            assessment_valid_until=risk.expires_at.date()
            if risk and risk.expires_at
            else None,
            assessment_expires_at=risk.expires_at if risk else None,
            generated_at=check_time,
            model_version="PROFILE_MODEL_1.0",
            rule_version="SUITABILITY_RULE_1.0",
            trace_id=str(uuid4()),
        )

        # persist into the CustomerProfile row
        profile.profile_status = status.value
        profile.suitability_confidence = float(suitability_conf)
        profile.recommendation_confidence = float(recommendation_conf)
        profile.model_risk_score = int(total_score)
        profile.max_allowed_product_risk = (
            restriction.max_allowed_product_risk.value
            if restriction.max_allowed_product_risk
            else "R1"
        )
        profile.restriction_codes_json = json.dumps(
            restriction.restriction_codes, ensure_ascii=False
        )
        profile.dimension_scores_json = json.dumps(
            {
                "dimensions": {
                    k: {"score": v["score"], "weight": v["weight"]}
                    for k, v in dims.items()
                },
                "breakdown": investor["breakdown"],
            },
            ensure_ascii=False,
        )
        profile.evidence_json = json.dumps(
            {
                key: [e.model_dump() for e in evidence]
                for key, evidence in dimension_evidence.items()
            },
            ensure_ascii=False,
            default=str,
        )
        await TagGovernanceService().apply_tags(
            session,
            user_id,
            self._build_system_tags(profile, asset),
            source_type="SYSTEM_BEHAVIOR",
            extraction_method="RULE",
        )
        # F2.1 画像版本生成：每次计算写入版本快照（对应外部 fin_profile_calculation）
        from app.models.profile import CustomerProfileVersion

        session.add(
            CustomerProfileVersion(
                id=str(uuid4()),
                user_id=user_id,
                profile_version=profile.profile_version,
                reason="calculate",
                model_risk_score=int(total_score),
                model_risk_level=model_level.value,
                profile_status=status.value,
                suitability_confidence=float(suitability_conf),
                max_allowed_product_risk=restriction.max_allowed_product_risk.value
                if restriction.max_allowed_product_risk
                else "R1",
                dimension_scores_json=profile.dimension_scores_json,
                restriction_codes_json=json.dumps(
                    restriction.restriction_codes, ensure_ascii=False
                ),
                snapshot_json=json.dumps(
                    snapshot.model_dump(), ensure_ascii=False, default=str
                ),
                created_at=check_time,
            )
        )
        await session.flush()
        return snapshot

    async def check_product_suitability(
        self,
        session: AsyncSession,
        user_id: str,
        product_id: str | None,
        business_type: BusinessType = BusinessType.PURCHASE,
    ) -> dict:
        snapshot = await self.calculate(session, user_id)
        product = None
        if product_id:
            product_row = (
                await session.execute(select(Product).where(Product.id == product_id))
            ).scalar_one_or_none()
            if product_row is None:
                raise ValueError("product not found")
            product = ProductRiskSnapshot(
                product_id=str(product_row.id),
                risk_level=ProductRiskLevel(
                    product_row.risk_level.upper().replace("C", "R")
                ),
                risk_version="PRODUCT_RISK_1.0",
            )
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user_id)
            )
        ).scalar_one()
        asset = (
            (
                await session.execute(
                    select(CustomerAssetSnapshot)
                    .where(CustomerAssetSnapshot.user_id == user_id)
                    .order_by(CustomerAssetSnapshot.snapshot_time.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        context = SuitabilityContext(
            profile=snapshot,
            age=profile.age or 30,
            monthly_income=(
                Decimal(str(profile.annual_income)) / Decimal("12")
                if profile.annual_income is not None
                else None
            ),
            total_assets=(
                Decimal(str(asset.total_asset))
                if asset and asset.total_asset is not None
                else None
            ),
        )
        result = decide_suitability(context, product, business_type, utcnow())
        return result.model_dump()
