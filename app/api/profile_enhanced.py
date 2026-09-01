from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.common.response import ApiResponse
from app.common.security.auth import (
    current_user,
    require_permission,
)
from app.common.security.roles import is_customer_user
from app.models.auth import User
from app.models.profile import CustomerProfile
from app.profile_domain.models import BusinessType, ProfileSnapshot
from app.services.profile_cache_service import ProfileCacheService
from app.services.profile_calculation_service import ProfileCalculationService
from app.services.profile_conversation_service import ProfileConversationService
from app.services.profile_tag_service import TagConflictService, TagQueryService

router = APIRouter(prefix="/profile", tags=["profile-enhanced"])


async def _resolve_user(request: Request, customer_id: str) -> User:
    """customer_id 兼容整数 ID 与用户名。"""
    async with request.app.state.database.session_factory() as session:
        user = None
        if str(customer_id).isdigit():
            user = (
                await session.execute(select(User).where(User.id == int(customer_id)))
            ).scalar_one_or_none()
        if user is None:
            user = (
                await session.execute(select(User).where(User.username == customer_id))
            ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="customer not found")
    return user


class ConversationProfileRequest(BaseModel):
    conversation_text: str = Field(min_length=10, max_length=8000)
    customer_id: int | str | None = Field(
        default=None,
        description="目标客户（员工指定，兼容数字 ID/用户名）；缺省为操作者本人",
    )


class ConversationProfileResponse(BaseModel):
    summary: str
    extraction_mode: str
    model_name: str
    prompt_version: str
    tags: list[dict] = Field(default_factory=list)
    applications: list[dict] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)
    profile_status: str | None = None


class ProfileCalculateResponse(BaseModel):
    snapshot: ProfileSnapshot
    tags: list[dict] = Field(default_factory=list)


@router.post(
    "/me/conversation-profile", response_model=ApiResponse[ConversationProfileResponse]
)
async def extract_conversation_profile(
    request: Request,
    payload: ConversationProfileRequest,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse[ConversationProfileResponse]:
    """从客服对话中抽取客户画像标签（AI + 规则降级），应用冲突治理，
    并自动同步主画像字段（F2.1：LLM 信息抽取 → 结构化标签 → 写入画像表 + 缓存）。"""
    target = user
    if payload.customer_id:
        if is_customer_user(user) or not _has_customer_read_permission(user):
            raise HTTPException(
                status_code=403, detail="customer:read permission required"
            )
        target = await _resolve_user(request, payload.customer_id)
    result = await ProfileConversationService(
        request.app.state.database,
        request.app.state.settings,
        request.app.state.qwen,
        request.app.state.redis if hasattr(request.app.state, "redis") else None,
    ).extract_and_apply(target.id, payload.conversation_text)

    return ApiResponse(
        data=ConversationProfileResponse(
            summary=result["summary"],
            extraction_mode=result["extraction_mode"],
            model_name=result["model_name"],
            prompt_version=result["prompt_version"],
            tags=result["tags"],
            applications=result["applications"],
            conflict_ids=result["conflict_ids"],
            profile_status=result["profile_status"],
        )
    )


def _has_customer_read_permission(user: User) -> bool:
    return user.is_super_admin or any(
        permission.code == "customer:read"
        for role in user.roles
        for permission in role.permissions
    )


class ProductSuitabilityCheckRequest(BaseModel):
    product_id: str = Field(min_length=1, max_length=128)
    business_type: BusinessType = BusinessType.PURCHASE


async def _conflict_payload(request: Request, user_id: str) -> dict:
    async with request.app.state.database.session_factory() as session:
        conflicts = await TagConflictService().list_conflicts(session, user_id)
    return {
        "customer_id": user_id,
        "conflicts": [conflict.model_dump(mode="json") for conflict in conflicts],
        "total": len(conflicts),
    }


@router.get("/me/conflicts", response_model=ApiResponse[dict])
async def my_profile_conflicts(
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse[dict]:
    """客户本人查询画像标签冲突记录。"""
    return ApiResponse(data=await _conflict_payload(request, user.id))


@router.get("/staff/customer/{user_id}/conflicts", response_model=ApiResponse[dict])
async def staff_profile_conflicts(
    request: Request,
    user_id: str,
    user: Annotated[User, Depends(require_permission("customer:read"))],
) -> ApiResponse[dict]:
    """员工查询指定客户的画像标签冲突记录。"""
    target = await _resolve_user(request, user_id)
    return ApiResponse(data=await _conflict_payload(request, target.id))


@router.post("/me/calculate", response_model=ApiResponse[ProfileCalculateResponse])
async def calculate_profile(
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse[ProfileCalculateResponse]:
    """重新计算客户画像（四维评分 → 置信度 → 状态机 → 适当性限制）。"""
    service = ProfileCalculationService()
    async with request.app.state.database.session_factory() as session:
        try:
            snapshot = await service.calculate(session, user.id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        tags = await TagQueryService().list_tags(session, user.id)
        await session.commit()
    if hasattr(request.app.state, "redis"):
        await ProfileCacheService(request.app.state.redis).invalidate(user.id)
    return ApiResponse(data=ProfileCalculateResponse(snapshot=snapshot, tags=tags))


@router.post(
    "/staff/customer/{user_id}/calculate",
    response_model=ApiResponse[ProfileCalculateResponse],
)
async def staff_calculate_profile(
    request: Request,
    user_id: str,
    user: Annotated[User, Depends(require_permission("customer:read"))],
) -> ApiResponse[ProfileCalculateResponse]:
    """员工端：重新计算指定客户画像（四维评分 → 置信度 → 状态机 → 适当性限制）。

    与 /me/calculate 的区别是作用于目标客户而非当前登录员工，
    供理财顾问在客户管理中为客户重算画像。
    """
    service = ProfileCalculationService()
    async with request.app.state.database.session_factory() as session:
        try:
            snapshot = await service.calculate(session, user_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        tags = await TagQueryService().list_tags(session, user_id)
        await session.commit()
    if hasattr(request.app.state, "redis"):
        await ProfileCacheService(request.app.state.redis).invalidate(user_id)
    return ApiResponse(data=ProfileCalculateResponse(snapshot=snapshot, tags=tags))


@router.get("/me/enhanced", response_model=ApiResponse[dict])
async def get_enhanced_profile(
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse[dict]:
    """查看客户画像增强信息：状态、置信度、限制、标签（Cache-Aside 中期记忆）。"""

    async def _loader(session, user_id: str) -> dict:
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user_id)
            )
        ).scalar_one_or_none()
        if profile is None:
            raise HTTPException(status_code=404, detail="customer profile not found")
        tags = await TagQueryService().list_tags(session, user_id)
        if not tags:
            # 兼容历史演示数据：此前已计算画像但没有标签时，首次读取自动
            # 回填由基础资料和资产快照推导的系统标签。
            await ProfileCalculationService().calculate(session, user_id)
            tags = await TagQueryService().list_tags(session, user_id)
            await session.commit()
        restriction_codes = _load_json(profile.restriction_codes_json)
        # 规则升级后，历史画像可能仍保存旧的 R3 上限和旧高龄限制码。
        # 首次读取时自动重算，保证客户端个人画像面板与当前硬性熔断规则一致。
        if (
            profile.age is not None
            and profile.age > 80
            and (
                profile.max_allowed_product_risk != "R2"
                or not isinstance(restriction_codes, list)
                or "AGE_OVER_80_R2_LIMIT" not in restriction_codes
            )
        ):
            await ProfileCalculationService().calculate(session, user_id)
            await session.commit()
            restriction_codes = _load_json(profile.restriction_codes_json)
        return {
            "profile_status": profile.profile_status,
            "profile_version": profile.profile_version,
            "suitability_confidence": profile.suitability_confidence,
            "recommendation_confidence": profile.recommendation_confidence,
            "max_allowed_product_risk": profile.max_allowed_product_risk,
            "model_risk_score": profile.model_risk_score,
            "model_risk_level": profile.risk_level or "C1",
            "restriction_codes": restriction_codes,
            "dimension_scores": _load_json(profile.dimension_scores_json),
            "tags": tags,
        }

    # Phase 4 F4.2：Cache-Aside 中期记忆（先缓存，未命中回源并回填）
    if hasattr(request.app.state, "redis"):
        cache = ProfileCacheService(request.app.state.redis)
        async with request.app.state.database.session_factory() as session:
            data = await cache.get_or_load(session, user.id, _loader)
    else:
        async with request.app.state.database.session_factory() as session:
            data = await _loader(session, user.id)
    return ApiResponse(data=data)


@router.get("/staff/customer/{user_id}/enhanced", response_model=ApiResponse[dict])
async def staff_enhanced_profile(
    request: Request,
    user_id: str,
    user: Annotated[User, Depends(require_permission("customer:read"))],
) -> ApiResponse[dict]:
    async with request.app.state.database.session_factory() as session:
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user_id)
            )
        ).scalar_one_or_none()
        if profile is None:
            raise HTTPException(status_code=404, detail="customer profile not found")
        tags = await TagQueryService().list_tags(session, user_id)
        return ApiResponse(
            data={
                "user_id": user_id,
                "profile_status": profile.profile_status,
                "profile_version": profile.profile_version,
                "suitability_confidence": profile.suitability_confidence,
                "recommendation_confidence": profile.recommendation_confidence,
                "max_allowed_product_risk": profile.max_allowed_product_risk,
                "model_risk_score": profile.model_risk_score,
                "model_risk_level": profile.risk_level or "C1",
                "restriction_codes": _load_json(profile.restriction_codes_json),
                "dimension_scores": _load_json(profile.dimension_scores_json),
                "customer_tier": profile.customer_tier,
                "customer_type": profile.customer_type,
                "tags": tags,
            }
        )


@router.get("/{user_id}/conflicts", response_model=ApiResponse[list[dict]])
async def profile_conflicts(
    request: Request,
    user_id: str,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse[list[dict]]:
    """查询客户的画像标签冲突审计记录。

    - 客户本人只能查自己的冲突
    - 员工（customer:read）可查任意客户
    """
    if is_customer_user(user) and str(user.id) != user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    async with request.app.state.database.session_factory() as session:
        conflicts = await TagConflictService().list_conflicts(session, user_id)
    return ApiResponse(data=[item.model_dump() for item in conflicts])


# ---------------------------------------------------------------------------
# F2.1 个人画像完整功能（对应外部"用户画像数据分析后端"验收台）
#   - GET  /profile/me/info     我的信息（基本资料 + KYC 状态 + 资产摘要）
#   - GET  /profile/me/history  画像版本历史（每次计算一条快照）
#   - GET  /profile/me/products 产品目录 + 可购买/拒绝判断（适当性）
# ---------------------------------------------------------------------------


@router.get("/me/info", response_model=ApiResponse[dict])
async def my_profile_info(
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse[dict]:
    """我的信息：基本资料 + KYC/资料提交状态 + 资产摘要 + 企业验证状态。"""
    return await _profile_info_payload(request, user)


@router.get("/staff/customer/{user_id}/info", response_model=ApiResponse[dict])
async def staff_customer_info(
    request: Request,
    user_id: str,
    user: Annotated[User, Depends(require_permission("customer:read"))],
) -> ApiResponse[dict]:
    """员工端：指定客户的我的信息（供画像增强弹窗展示）。"""
    target = await _resolve_user(request, user_id)
    return await _profile_info_payload(request, target)


async def _profile_info_payload(request: Request, user: User) -> ApiResponse[dict]:
    """我的信息装配（客户本人或员工指定客户共用）。"""
    from app.models.profile import (
        CustomerAssetSnapshot,
        CustomerEnterpriseVerification,
    )

    async with request.app.state.database.session_factory() as session:
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user.id)
            )
        ).scalar_one_or_none()
        asset = (
            (
                await session.execute(
                    select(CustomerAssetSnapshot)
                    .where(CustomerAssetSnapshot.user_id == user.id)
                    .order_by(CustomerAssetSnapshot.snapshot_time.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        kyc = (
            await session.execute(
                select(CustomerEnterpriseVerification).where(
                    CustomerEnterpriseVerification.user_id == user.id
                )
            )
        ).scalar_one_or_none()

    basic = {
        "display_name": user.display_name,
        "username": user.username,
        "age": profile.age if profile else None,
        "occupation": profile.occupation if profile else "",
        "education_level": profile.education_level if profile else "",
        "annual_income": float(profile.annual_income)
        if profile and profile.annual_income is not None
        else None,
        "investment_experience_years": profile.investment_experience_years
        if profile
        else 0,
        "investment_goal": profile.investment_goal if profile else "",
        "liquidity_preference": profile.liquidity_preference if profile else "medium",
        "region": profile.region if profile else "",
        "customer_tier": profile.customer_tier if profile else "ordinary",
        "customer_type": profile.customer_type if profile else "individual",
    }
    kyc_status = (
        "approved"
        if kyc and kyc.status == "approved"
        else "pending"
        if kyc and kyc.status == "pending"
        else "submitted"
        if profile is not None
        else "not_submitted"
    )
    return ApiResponse(
        data={
            "basic": basic,
            "kyc": {
                "status": kyc_status,
                "enterprise_verified": bool(kyc and kyc.status == "approved"),
                "enterprise_status": kyc.status if kyc else None,
            },
            "asset": {
                "total_asset": float(asset.total_asset) if asset else None,
                "investable_asset": float(asset.investable_asset) if asset else None,
                "cash_balance": float(asset.cash_balance) if asset else None,
                "net_asset": float(asset.net_asset) if asset else None,
                "liability": float(asset.liability) if asset else None,
                "snapshot_time": asset.snapshot_time.isoformat() if asset else None,
            }
            if asset
            else None,
        }
    )


@router.get("/me/history", response_model=ApiResponse[list[dict]])
async def my_profile_history(
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse[list[dict]]:
    """画像版本历史：按计算时间倒序返回全部版本快照。"""

    from app.models.profile import CustomerProfileVersion

    async with request.app.state.database.session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(CustomerProfileVersion)
                    .where(CustomerProfileVersion.user_id == user.id)
                    .order_by(CustomerProfileVersion.profile_version.desc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
        return ApiResponse(
            data=[
                {
                    "profile_version": r.profile_version,
                    "reason": r.reason,
                    "model_risk_score": r.model_risk_score,
                    "model_risk_level": r.model_risk_level,
                    "profile_status": r.profile_status,
                    "suitability_confidence": float(r.suitability_confidence),
                    "max_allowed_product_risk": r.max_allowed_product_risk,
                    "dimension_scores": _load_json(r.dimension_scores_json),
                    "restriction_codes": _load_json(r.restriction_codes_json),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        )


@router.get("/me/products", response_model=ApiResponse[dict])
async def my_product_suitability(
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse[dict]:
    """产品目录 + 可购买/拒绝判断：每产品给出适当性结论与原因。"""
    from app.models.profile import CustomerRiskAssessment, Product

    async with request.app.state.database.session_factory() as session:
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user.id)
            )
        ).scalar_one_or_none()
        assessment = (
            (
                await session.execute(
                    select(CustomerRiskAssessment)
                    .where(
                        CustomerRiskAssessment.user_id == user.id,
                        CustomerRiskAssessment.status.in_(["active", "provisional"]),
                    )
                    .order_by(CustomerRiskAssessment.assessed_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        products = list(
            (
                await session.execute(
                    select(Product).where(
                        Product.status == "active", Product.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )

    # 客户风险等级：风评 → 画像 → 默认 C1
    customer_level = (
        (assessment.risk_level if assessment else None)
        or (profile.risk_level if profile else None)
        or "C1"
    )
    order_map = {"C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5}
    customer_order = order_map.get(str(customer_level).upper(), 1)
    # 研判规则：客户可购买 ≤ C+1 档（C5 封顶 R5）
    max_allowed_order = min(customer_order + 1, 5)
    max_allowed = f"R{max_allowed_order}"
    # 购买权限硬门槛：无正式风评（active 且未过期）→ 阻断购买。
    # provisional（系统默认保守 C1）不阻断，仅提示完成正式测评。
    from datetime import datetime

    has_formal_assessment = bool(
        assessment
        and assessment.status == "active"
        and assessment.source_type == "questionnaire"
    )
    purchase_blocked = False
    purchase_blocked_reason = None
    if not has_formal_assessment:
        if assessment is None or assessment.status == "provisional":
            purchase_blocked = True
            purchase_blocked_reason = "尚未完成正式风险测评，当前为保守临时画像；请完成 16 题问卷后解锁高等级产品"
        else:
            purchase_blocked = True
            purchase_blocked_reason = "风评状态异常，请重新完成风险测评"
    elif assessment.expires_at is not None and assessment.expires_at < datetime.now(
        UTC
    ):
        purchase_blocked = True
        purchase_blocked_reason = "风评已过期，请重新完成风险测评"

    # F2.1 硬性门槛熔断（客户需求 2026-08-04）：
    #   - 年龄 < 18：不允许购买任何产品（UNDER_AGE 全阻断）
    #   - 年龄 > 80：可购 R1/R2；R3 需人工复核（不可直接购买）；
    #     R4 及以上不允许购买
    customer_age = profile.age if profile else None
    under_age = customer_age is not None and customer_age < 18
    over_80 = customer_age is not None and customer_age > 80
    if under_age:
        purchase_blocked = True
        purchase_blocked_reason = (
            f"未满 18 周岁（当前 {customer_age} 岁），依法不允许购买理财产品"
        )
    age_over_80_max_order = 2  # >80 直接可购上限 R2
    age_over_80_review_order = 3  # R3 需人工复核
    if over_80:
        max_allowed_order = min(max_allowed_order, age_over_80_max_order)
        max_allowed = f"R{max_allowed_order}"

    items = []
    for p in products:
        product_raw = str(p.risk_level).upper().replace("C", "R")
        product_order = {"R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5}.get(
            product_raw, 1
        )
        matched = product_order <= max_allowed_order
        # 目标客户类型过滤：企业专属产品对个人客户不可购买
        target_type = p.target_customer_type or "individual"
        customer_type = (profile.customer_type if profile else None) or "individual"
        type_matched = (
            target_type == "all"
            or target_type == "individual"
            or target_type == customer_type
        )
        reasons = []
        # 高龄：R3 复核 / R4+ 拒绝
        if over_80:
            if product_order == age_over_80_review_order:
                reasons.append(
                    "年龄超过 80 岁，R3 产品需人工复核后方可购买（当前不可直接下单）"
                )
            elif product_order > age_over_80_review_order:
                reasons.append("年龄超过 80 岁，不允许购买 R4 及以上产品")
        if purchase_blocked:
            reasons.append(purchase_blocked_reason)
        elif matched and type_matched:
            reasons.append(
                f"客户风险等级 {customer_level}，可购买 {max_allowed} 及以下产品（{product_raw} 匹配）"
            )
        elif not matched:
            reasons.append(
                f"客户风险等级 {customer_level} 仅可购买 {max_allowed} 及以下，{product_raw} 超出范围"
            )
        if not type_matched:
            reasons.append(f"仅限 {target_type} 客户购买")
        if p.minimum_amount and p.minimum_amount > 0:
            reasons.append(f"起投金额 {p.minimum_amount:,.0f} 元")

        # 最终购买判定：风评/未成年阻断、类型匹配、风险匹配、高龄 R4+ 拒绝
        age_over_80_rejected = over_80 and product_order > age_over_80_review_order
        age_over_80_review = over_80 and product_order == age_over_80_review_order
        allowed = (
            matched
            and not purchase_blocked
            and type_matched
            and not under_age
            and not age_over_80_rejected
            and not age_over_80_review
        )
        items.append(
            {
                "product_id": str(p.id),
                "name": p.name,
                "product_type": p.product_type,
                "risk_level": product_raw,
                "term_days": p.term_days,
                "minimum_amount": float(p.minimum_amount or 0),
                "liquidity": p.liquidity,
                "description": p.description or "",
                "purchase_allowed": allowed,
                "needs_review": (
                    age_over_80_review
                    and not purchase_blocked
                    and matched
                    and type_matched
                ),
                "reasons": reasons,
            }
        )

    return ApiResponse(
        data={
            "customer_risk_level": customer_level,
            "max_allowed_product_risk": max_allowed,
            "purchase_blocked": purchase_blocked,
            "purchase_blocked_reason": purchase_blocked_reason,
            "items": items,
            "total": len(items),
        }
    )


@router.post("/me/suitability-check", response_model=ApiResponse[dict])
async def my_suitability_check(
    request: Request,
    payload: ProductSuitabilityCheckRequest,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse[dict]:
    """对客户选定的单个产品执行适当性检查。"""
    async with request.app.state.database.session_factory() as session:
        try:
            result = await ProfileCalculationService().check_product_suitability(
                session,
                user.id,
                payload.product_id,
                payload.business_type,
            )
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if hasattr(request.app.state, "redis"):
        await ProfileCacheService(request.app.state.redis).invalidate(user.id)
    return ApiResponse(data=result)


def _load_json(value: str) -> object:
    import json

    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
