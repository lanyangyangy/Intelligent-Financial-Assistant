"""F2.1 客户画像系统标准接口。

按需求文档路径挂载（/api 前缀，非 /api/v1）：
  - POST /api/profile/create            画像录入（创建/更新 CustomerProfile + 标签）
  - GET  /api/profile/{customer_id}     画像查询（Cache-Aside：先 Redis 后 MySQL 回填）
  - PUT  /api/profile/{customer_id}     画像更新（增量更新标签，冲突治理 + 审计）
  - GET  /api/profile/{customer_id}/conflicts               冲突审计记录查询
  - POST /api/profile/{customer_id}/conflicts/{conflict_id}/resolve  冲突人工解析

写入表：customer_profile（对应需求文档 fin_customer_profile）+ customer_profile_tag
缓存：  profile:v2:{customer_id}（Redis，TTL 7 天，与 /me/enhanced 共用）
"""

from __future__ import annotations

import json
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.common.response import ApiResponse
from app.common.security.auth import current_user
from app.common.security.roles import is_customer_user
from app.models.auth import User
from app.models.profile import CustomerProfile
from app.profile_domain.tag_governance import ProfileTagCode
from app.services.profile_cache_service import ProfileCacheService
from app.services.profile_calculation_service import ProfileCalculationService
from app.services.profile_tag_service import TagConflictService, TagGovernanceService

router = APIRouter(prefix="/profile", tags=["profile-f21"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ProfileTagInput(BaseModel):
    """单个画像标签（F2.1：风险偏好、资产规模、投资经验等）。"""

    tag_code: str = Field(min_length=1, max_length=64)
    tag_value: object
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    source_type: str = Field(default="MANAGER_ENTERED", max_length=32)
    extraction_method: str = Field(default="MANUAL", max_length=32)
    evidence_quote: str = Field(default="", max_length=500)

    @field_validator("tag_code")
    @classmethod
    def validate_tag_code(cls, value: str) -> str:
        try:
            ProfileTagCode(value.upper().replace("-", "_"))
        except ValueError as exc:
            raise ValueError(
                f"unsupported tag_code: {value}，可选值见 ProfileTagCode"
            ) from exc
        return value.upper().replace("-", "_")


class ProfileCreateRequest(BaseModel):
    """画像录入：customer_id 必填（兼容数字 ID 与用户名），基础属性 + 标签。"""

    customer_id: int | str
    age: int | None = Field(default=None, ge=0, le=150)
    occupation: str = Field(default="", max_length=128)
    region: str = Field(default="", max_length=128)
    education_level: str = Field(
        default="",
        max_length=32,
        description="HIGH_SCHOOL_OR_BELOW/COLLEGE/BACHELOR/MASTER_OR_ABOVE",
    )
    annual_income: float | None = Field(default=None, ge=0)
    investment_experience_years: int = Field(default=0, ge=0, le=80)
    investment_goal: str = Field(default="balanced", max_length=255)
    liquidity_preference: str = Field(default="medium", max_length=16)
    tags: list[ProfileTagInput] = Field(default_factory=list, max_length=50)


class ProfileUpdateRequest(BaseModel):
    """画像更新：增量更新标签 + 可选基础字段。"""

    tags: list[ProfileTagInput] = Field(default_factory=list, max_length=50)
    age: int | None = Field(default=None, ge=0, le=150)
    occupation: str | None = Field(default=None, max_length=128)
    education_level: str | None = Field(default=None, max_length=32)
    annual_income: float | None = Field(default=None, ge=0)
    investment_experience_years: int | None = Field(default=None, ge=0, le=80)
    investment_goal: str | None = Field(default=None, max_length=255)


# ---------------------------------------------------------------------------
# 权限与用户解析
# ---------------------------------------------------------------------------
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


def _assert_can_view(actor: User, target: User) -> None:
    """查看权限：员工（customer:read）或本人。"""
    if actor.id == target.id or actor.is_super_admin:
        return
    if is_customer_user(actor):
        raise HTTPException(status_code=403, detail="permission denied")
    permissions = {p.code for role in actor.roles for p in role.permissions}
    if "customer:read" not in permissions:
        raise HTTPException(status_code=403, detail="permission denied")


def _assert_can_edit(actor: User, target: User) -> None:
    """编辑权限：员工（customer:write）或本人。"""
    if actor.id == target.id or actor.is_super_admin:
        return
    if is_customer_user(actor):
        raise HTTPException(status_code=403, detail="permission denied")
    permissions = {p.code for role in actor.roles for p in role.permissions}
    if "customer:write" not in permissions:
        raise HTTPException(status_code=403, detail="permission denied")


def _load_json(value: str | None) -> object:
    if not value:
        return {}
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# 画像装配（供 GET 返回完整画像）
# ---------------------------------------------------------------------------
async def _build_profile_view(session, user: User) -> dict:
    """装配完整画像：主表字段 + 四维分 + 限制 + 标签（含置信度）。"""
    from app.services.profile_tag_service import TagQueryService

    profile = (
        await session.execute(
            select(CustomerProfile).where(CustomerProfile.user_id == user.id)
        )
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="customer profile not found")
    tags = await TagQueryService().list_tags(session, user.id)
    if not tags:
        # 兼容：首次读取自动回填系统推导标签
        await ProfileCalculationService().calculate(session, user.id)
        tags = await TagQueryService().list_tags(session, user.id)
        await session.commit()
    return {
        "customer_id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "profile_status": profile.profile_status,
        "profile_version": profile.profile_version,
        "risk_level": profile.risk_level,
        "risk_score": profile.risk_score,
        "model_risk_score": profile.model_risk_score,
        "suitability_confidence": float(profile.suitability_confidence or 0),
        "recommendation_confidence": float(profile.recommendation_confidence or 0),
        "max_allowed_product_risk": profile.max_allowed_product_risk,
        "restriction_codes": _load_json(profile.restriction_codes_json),
        "dimension_scores": _load_json(profile.dimension_scores_json),
        "age": profile.age,
        "occupation": profile.occupation,
        "education_level": profile.education_level,
        "annual_income": float(profile.annual_income)
        if profile.annual_income is not None
        else None,
        "investment_experience_years": profile.investment_experience_years,
        "investment_goal": profile.investment_goal,
        "customer_tier": profile.customer_tier,
        "tags": tags,
    }


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
@router.post("/create", response_model=ApiResponse[dict])
async def create_profile(
    request: Request,
    payload: ProfileCreateRequest,
    actor: Annotated[User, Depends(current_user)],
) -> ApiResponse[dict]:
    """画像录入：创建/更新 CustomerProfile + 写入标签（冲突治理），并触发研判计算。"""
    target = await _resolve_user(request, payload.customer_id)
    _assert_can_edit(actor, target)

    async with request.app.state.database.session_factory() as session:
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == target.id)
            )
        ).scalar_one_or_none()
        created = profile is None
        if profile is None:
            profile = CustomerProfile(id=str(uuid4()), user_id=target.id)
            session.add(profile)
        for key in (
            "age",
            "occupation",
            "region",
            "education_level",
            "annual_income",
            "investment_experience_years",
            "investment_goal",
            "liquidity_preference",
        ):
            value = getattr(payload, key)
            if value is not None:
                setattr(profile, key, value)

        applications: list[dict] = []
        if payload.tags:
            from decimal import Decimal

            from app.profile_domain.tag_governance import ExtractedProfileTag

            governance = TagGovernanceService()
            for tag in payload.tags:
                extracted = [
                    ExtractedProfileTag(
                        tag_code=ProfileTagCode(tag.tag_code),
                        tag_value=tag.tag_value,
                        confidence=Decimal(str(tag.confidence)),
                        evidence_quote=tag.evidence_quote
                        or f"F2.1 profile create: {tag.tag_code}",
                    )
                ]
                applications.extend(
                    await governance.apply_tags(
                        session,
                        target.id,
                        extracted,
                        source_type=tag.source_type,
                        extraction_method=tag.extraction_method,
                    )
                )

        try:
            await ProfileCalculationService().calculate(session, target.id)
        except ValueError:
            # 基础数据不足时跳过计算，保留 PROVISIONAL
            pass
        await session.commit()

    if hasattr(request.app.state, "redis"):
        await ProfileCacheService(request.app.state.redis).invalidate(target.id)

    return ApiResponse(
        data={
            "customer_id": target.id,
            "created": created,
            "applications": applications,
        }
    )


@router.get("/{customer_id}", response_model=ApiResponse[dict])
async def get_profile(
    request: Request,
    customer_id: str,
    actor: Annotated[User, Depends(current_user)],
) -> ApiResponse[dict]:
    """画像查询：Cache-Aside（先 Redis 缓存，未命中查 MySQL 并回填）。"""
    target = await _resolve_user(request, customer_id)
    _assert_can_view(actor, target)

    async def _loader(session, user_id: str) -> dict:
        return await _build_profile_view(session, target)

    if hasattr(request.app.state, "redis"):
        cache = ProfileCacheService(request.app.state.redis)
        async with request.app.state.database.session_factory() as session:
            data = await cache.get_or_load(session, target.id, _loader)
    else:
        async with request.app.state.database.session_factory() as session:
            data = await _loader(session, target.id)
    return ApiResponse(data=data)


@router.put("/{customer_id}", response_model=ApiResponse[dict])
async def update_profile(
    request: Request,
    customer_id: str,
    payload: ProfileUpdateRequest,
    actor: Annotated[User, Depends(current_user)],
) -> ApiResponse[dict]:
    """画像更新：增量更新标签（冲突治理 + 审计），并重新研判计算。"""
    target = await _resolve_user(request, customer_id)
    _assert_can_edit(actor, target)

    async with request.app.state.database.session_factory() as session:
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == target.id)
            )
        ).scalar_one_or_none()
        if profile is None:
            raise HTTPException(status_code=404, detail="customer profile not found")
        for key in (
            "age",
            "occupation",
            "education_level",
            "annual_income",
            "investment_experience_years",
            "investment_goal",
        ):
            value = getattr(payload, key)
            if value is not None:
                setattr(profile, key, value)

        applications: list[dict] = []
        if payload.tags:
            from decimal import Decimal

            from app.profile_domain.tag_governance import ExtractedProfileTag

            governance = TagGovernanceService()
            for tag in payload.tags:
                extracted = [
                    ExtractedProfileTag(
                        tag_code=ProfileTagCode(tag.tag_code),
                        tag_value=tag.tag_value,
                        confidence=Decimal(str(tag.confidence)),
                        evidence_quote=tag.evidence_quote
                        or f"F2.1 profile update: {tag.tag_code}",
                    )
                ]
                applications.extend(
                    await governance.apply_tags(
                        session,
                        target.id,
                        extracted,
                        source_type=tag.source_type,
                        extraction_method=tag.extraction_method,
                    )
                )

        try:
            await ProfileCalculationService().calculate(session, target.id)
        except ValueError:
            pass
        await session.commit()

    if hasattr(request.app.state, "redis"):
        await ProfileCacheService(request.app.state.redis).invalidate(target.id)

    return ApiResponse(
        data={
            "customer_id": target.id,
            "applications": applications,
        }
    )


@router.get("/{customer_id}/conflicts", response_model=ApiResponse[dict])
async def list_profile_conflicts(
    request: Request,
    customer_id: str,
    actor: Annotated[User, Depends(current_user)],
    status: str = "",
) -> ApiResponse[dict]:
    """查询标签冲突审计记录（保留冲突记录用于审计）。"""
    target = await _resolve_user(request, customer_id)
    _assert_can_view(actor, target)
    async with request.app.state.database.session_factory() as session:
        conflicts = await TagConflictService().list_conflicts(
            session, target.id, status=status or None
        )
    return ApiResponse(
        data={
            "customer_id": target.id,
            "conflicts": [c.model_dump() for c in conflicts],
            "total": len(conflicts),
        }
    )


@router.post(
    "/{customer_id}/conflicts/{conflict_id}/resolve", response_model=ApiResponse[dict]
)
async def resolve_profile_conflict(
    request: Request,
    customer_id: str,
    conflict_id: str,
    payload: dict,
    actor: Annotated[User, Depends(current_user)],
) -> ApiResponse[dict]:
    """人工解析 OPEN 冲突：selected_side=left|right，写入生效标签并关闭记录。"""
    target = await _resolve_user(request, customer_id)
    _assert_can_edit(actor, target)
    selected_side = str(payload.get("selected_side", "")).lower()
    if selected_side not in {"left", "right"}:
        raise HTTPException(status_code=422, detail="selected_side must be left|right")
    async with request.app.state.database.session_factory() as session:
        view = await TagConflictService().resolve_conflict(
            session,
            conflict_id,
            user_id=target.id,
            selected_side=selected_side,
            resolution_note=str(payload.get("resolution_note", "")),
        )
        try:
            await ProfileCalculationService().calculate(session, target.id)
        except ValueError:
            pass
        await session.commit()
    if hasattr(request.app.state, "redis"):
        await ProfileCacheService(request.app.state.redis).invalidate(target.id)
    return ApiResponse(data=view.model_dump())
