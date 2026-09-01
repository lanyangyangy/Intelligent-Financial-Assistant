from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.profile_domain.models import (
    BusinessType,
    ProductRiskLevel,
    ProductRiskSnapshot,
    ProfileStatus,
    RestrictionEffect,
    RestrictionResult,
    RiskLevel,
    SuitabilityCheckResult,
    SuitabilityContext,
    SuitabilityDecision,
)

# 适当性映射（投资者风险画像研判规则 第十一条）：
#   C1 → R1-R2, C2 → R1-R3, C3 → R1-R4, C4 → R1-R5, C5 → R1-R5
RISK_LIMIT_BY_CUSTOMER = {
    RiskLevel.C1: ProductRiskLevel.R2,
    RiskLevel.C2: ProductRiskLevel.R3,
    RiskLevel.C3: ProductRiskLevel.R4,
    RiskLevel.C4: ProductRiskLevel.R5,
    RiskLevel.C5: ProductRiskLevel.R5,
}

PRODUCT_RANK = {
    ProductRiskLevel.R1: 1,
    ProductRiskLevel.R2: 2,
    ProductRiskLevel.R3: 3,
    ProductRiskLevel.R4: 4,
    ProductRiskLevel.R5: 5,
}

PURCHASE_BUSINESS_TYPES = frozenset(
    {
        BusinessType.PURCHASE,
        BusinessType.ADDITIONAL_PURCHASE,
        BusinessType.NEW_RECURRING_INVESTMENT,
    }
)

DECISION_RULE_VERSION = "SUITABILITY_RULE_1.0"


def _validate_check_time(check_time: datetime) -> None:
    if check_time.tzinfo is None or check_time.utcoffset() is None:
        raise ValueError("check_time must be timezone-aware")


def _append_unique(codes: list[str], *new_codes: str) -> None:
    for code in new_codes:
        if code and code not in codes:
            codes.append(code)


def resolve_product_limit(
    model_level: RiskLevel,
    age: int,
    assessment_expires_at: datetime | None,
    check_time: datetime,
    *,
    monthly_income: Decimal | None = None,
    total_assets: Decimal | None = None,
) -> RestrictionResult:
    if not isinstance(model_level, RiskLevel):
        raise ValueError("model_level must be a RiskLevel")
    if isinstance(age, bool) or not isinstance(age, int) or not 0 <= age <= 150:
        raise ValueError("age must be an integer between 0 and 150")
    _validate_check_time(check_time)
    if assessment_expires_at is not None and (
        assessment_expires_at.tzinfo is None
        or assessment_expires_at.utcoffset() is None
    ):
        raise ValueError("assessment_expires_at must be timezone-aware")
    for field_name, value in (
        ("monthly_income", monthly_income),
        ("total_assets", total_assets),
    ):
        if value is not None and (
            not isinstance(value, Decimal) or not value.is_finite() or value < 0
        ):
            raise ValueError(f"{field_name} must be a finite non-negative Decimal")

    maximum = RISK_LIMIT_BY_CUSTOMER[model_level]
    codes: list[str] = []
    purchase_permission = "ALLOWED"

    # F2.1 硬性门槛熔断（客户需求 2026-08-04）：
    #   - 年龄 < 18：不允许购买任何产品（BLOCKED + UNDER_AGE）
    #   - 年龄 > 80：直接可购 R1/R2；R3 需人工复核；R4 及以上不允许购买。
    #     画像级上限按“无需审批即可购买”计算为 R2，单产品检查再按
    #     当前产品等级返回 REVIEW_REQUIRED 或 REJECT。
    if age < 18:
        purchase_permission = "BLOCKED"
        codes.append("UNDER_AGE")

    if age > 80:
        # 直接购买上限钳制到 R2；R3 需审批，R4/R5 拒绝由单产品检查判定。
        if PRODUCT_RANK[maximum] > PRODUCT_RANK[ProductRiskLevel.R2]:
            codes.append("AGE_OVER_80_R2_LIMIT")
        maximum = min(
            maximum,
            ProductRiskLevel.R2,
            key=PRODUCT_RANK.__getitem__,
        )
        if purchase_permission == "ALLOWED":
            # R1/R2 直接购买，R3 需人工复核
            purchase_permission = "APPROVAL_REQUIRED"

    if (
        monthly_income == Decimal("0")
        and total_assets is not None
        and total_assets < Decimal("10000")
    ):
        maximum = min(
            maximum,
            ProductRiskLevel.R2,
            key=PRODUCT_RANK.__getitem__,
        )
        codes.append("NO_INCOME_LOW_ASSETS_R2_LIMIT")

    if assessment_expires_at is None:
        purchase_permission = "BLOCKED"
        codes.append("ASSESSMENT_MISSING")
    elif check_time > assessment_expires_at:
        purchase_permission = "BLOCKED"
        codes.append("ASSESSMENT_EXPIRED")

    return RestrictionResult(
        max_allowed_product_risk=maximum,
        purchase_permission=purchase_permission,
        restriction_codes=codes,
    )


def _build_result(
    *,
    context: SuitabilityContext,
    product: ProductRiskSnapshot | None,
    business_type: BusinessType,
    decision: SuitabilityDecision,
    restriction_codes: list[str],
    maximum: ProductRiskLevel | None,
) -> SuitabilityCheckResult:
    profile = context.profile
    return SuitabilityCheckResult(
        decision=decision,
        customer_id=profile.customer_id,
        product_id=product.product_id if product else None,
        business_type=business_type,
        profile_version=profile.profile_version,
        model_risk_level=profile.model_risk_level,
        product_risk_level=product.risk_level if product else None,
        product_risk_version=product.risk_version if product else None,
        profile_status=profile.profile_status,
        suitability_confidence=profile.suitability_confidence,
        max_allowed_product_risk=maximum,
        restriction_codes=restriction_codes,
        decision_rule_version=DECISION_RULE_VERSION,
        trace_id=profile.trace_id,
    )


def decide_suitability(
    context: SuitabilityContext,
    product: ProductRiskSnapshot | None,
    business_type: BusinessType,
    check_time: datetime,
) -> SuitabilityCheckResult:
    if not isinstance(context, SuitabilityContext):
        raise ValueError("context must be a SuitabilityContext")
    if not isinstance(business_type, BusinessType):
        raise ValueError("business_type must be a BusinessType")
    _validate_check_time(check_time)
    if business_type in PURCHASE_BUSINESS_TYPES and product is None:
        raise ValueError("product is required for purchase business")

    profile = context.profile
    restriction_result = resolve_product_limit(
        model_level=profile.model_risk_level,
        age=context.age,
        assessment_expires_at=profile.assessment_expires_at,
        check_time=check_time,
        monthly_income=context.monthly_income,
        total_assets=context.total_assets,
    )
    codes = list(profile.restriction_codes)
    _append_unique(codes, *restriction_result.restriction_codes)

    applicable_external = [
        restriction
        for restriction in context.external_restrictions
        if business_type in restriction.business_types
    ]
    blocking = [
        restriction
        for restriction in applicable_external
        if restriction.effect is RestrictionEffect.BLOCK
    ]
    if blocking:
        external_codes = [restriction.code for restriction in blocking]
        codes = external_codes + [code for code in codes if code not in external_codes]
        return _build_result(
            context=context,
            product=product,
            business_type=business_type,
            decision=SuitabilityDecision.REJECT,
            restriction_codes=codes,
            maximum=restriction_result.max_allowed_product_risk,
        )

    reviews = [
        restriction
        for restriction in applicable_external
        if restriction.effect is RestrictionEffect.REVIEW
    ]
    if reviews:
        _append_unique(codes, *(restriction.code for restriction in reviews))
        return _build_result(
            context=context,
            product=product,
            business_type=business_type,
            decision=SuitabilityDecision.REVIEW_REQUIRED,
            restriction_codes=codes,
            maximum=restriction_result.max_allowed_product_risk,
        )

    if business_type is BusinessType.ACCOUNT_OPENING:
        decision = (
            SuitabilityDecision.REJECT if context.age < 18 else SuitabilityDecision.PASS
        )
        return _build_result(
            context=context,
            product=None,
            business_type=business_type,
            decision=decision,
            restriction_codes=codes,
            maximum=restriction_result.max_allowed_product_risk,
        )

    if context.age < 18 and business_type in PURCHASE_BUSINESS_TYPES:
        return _build_result(
            context=context,
            product=product,
            business_type=business_type,
            decision=SuitabilityDecision.REJECT,
            restriction_codes=codes,
            maximum=restriction_result.max_allowed_product_risk,
        )

    if business_type not in PURCHASE_BUSINESS_TYPES:
        return _build_result(
            context=context,
            product=product,
            business_type=business_type,
            decision=SuitabilityDecision.PASS,
            restriction_codes=codes,
            maximum=restriction_result.max_allowed_product_risk,
        )

    if (
        profile.profile_status in {ProfileStatus.EXPIRED, ProfileStatus.INCOMPLETE}
        or restriction_result.purchase_permission == "BLOCKED"
    ):
        if profile.profile_status is ProfileStatus.INCOMPLETE:
            _append_unique(codes, "PROFILE_INCOMPLETE")
        return _build_result(
            context=context,
            product=product,
            business_type=business_type,
            decision=SuitabilityDecision.PROFILE_REFRESH_REQUIRED,
            restriction_codes=codes,
            maximum=restriction_result.max_allowed_product_risk,
        )

    # 80 岁以上客户的硬性熔断是按产品风险分层处理：R1/R2 可购买，
    # R3 需要审批，R4/R5 拒绝。不能先被画像 NEEDS_REVIEW 状态拦截，
    # 否则低风险产品也会全部变成 REVIEW_REQUIRED。
    if context.age > 80:
        age_codes = {
            "AGE_OVER_80_R2_LIMIT",
            "AGE_OVER_80_R3_APPROVAL_REQUIRED",
            "AGE_OVER_80_R4_R5_REJECTED",
        }
        # 画像级限制码描述的是整体上限，单产品结果不能把未命中的
        # R3/R4 分支一起展示，否则 R1/R2 也会看起来像被拦截。
        codes = [code for code in codes if code not in age_codes]
        product_rank = PRODUCT_RANK[product.risk_level]
        _append_unique(codes, "AGE_OVER_80_R2_LIMIT")
        if product_rank >= PRODUCT_RANK[ProductRiskLevel.R4]:
            _append_unique(codes, "AGE_OVER_80_R4_R5_REJECTED")
            return _build_result(
                context=context,
                product=product,
                business_type=business_type,
                decision=SuitabilityDecision.REJECT,
                restriction_codes=codes,
                maximum=restriction_result.max_allowed_product_risk,
            )
        if product_rank == PRODUCT_RANK[ProductRiskLevel.R3]:
            _append_unique(codes, "AGE_OVER_80_R3_APPROVAL_REQUIRED")
            return _build_result(
                context=context,
                product=product,
                business_type=business_type,
                decision=SuitabilityDecision.REVIEW_REQUIRED,
                restriction_codes=codes,
                maximum=restriction_result.max_allowed_product_risk,
            )

    if profile.profile_status in {
        ProfileStatus.NEEDS_REVIEW,
        ProfileStatus.PROVISIONAL,
    }:
        age_over_80_low_risk = (
            context.age > 80
            and PRODUCT_RANK[product.risk_level] <= PRODUCT_RANK[ProductRiskLevel.R2]
        )
        if age_over_80_low_risk:
            return _build_result(
                context=context,
                product=product,
                business_type=business_type,
                decision=SuitabilityDecision.PASS,
                restriction_codes=codes,
                maximum=restriction_result.max_allowed_product_risk,
            )
        status_code = (
            "EVIDENCE_CONFLICT"
            if profile.profile_status is ProfileStatus.NEEDS_REVIEW
            else "LOW_CONFIDENCE"
        )
        _append_unique(codes, status_code)
        return _build_result(
            context=context,
            product=product,
            business_type=business_type,
            decision=SuitabilityDecision.REVIEW_REQUIRED,
            restriction_codes=codes,
            maximum=restriction_result.max_allowed_product_risk,
        )

    if (
        "NO_INCOME_LOW_ASSETS_R2_LIMIT" in restriction_result.restriction_codes
        and PRODUCT_RANK[product.risk_level]
        > PRODUCT_RANK[restriction_result.max_allowed_product_risk]
    ):
        return _build_result(
            context=context,
            product=product,
            business_type=business_type,
            decision=SuitabilityDecision.REJECT,
            restriction_codes=codes,
            maximum=restriction_result.max_allowed_product_risk,
        )

    normal_limit = RISK_LIMIT_BY_CUSTOMER[profile.model_risk_level]
    if PRODUCT_RANK[product.risk_level] > PRODUCT_RANK[normal_limit]:
        _append_unique(codes, "SUITABILITY_MISMATCH")
        return _build_result(
            context=context,
            product=product,
            business_type=business_type,
            decision=SuitabilityDecision.REJECT,
            restriction_codes=codes,
            maximum=restriction_result.max_allowed_product_risk,
        )

    return _build_result(
        context=context,
        product=product,
        business_type=business_type,
        decision=SuitabilityDecision.PASS,
        restriction_codes=codes,
        maximum=restriction_result.max_allowed_product_risk,
    )
