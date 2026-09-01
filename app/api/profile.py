import json
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select

from app.common.response import ApiResponse
from app.common.security.auth import (
    current_user,
    require_permission,
    staff_customer_user,
)
from app.common.security.roles import CUSTOMER_ROLE_CODES, is_customer_user
from app.infrastructure.knowledge_graph import _PRODUCT_INDUSTRY
from app.models.audit import AuditLog
from app.models.auth import User
from app.models.profile import (
    CustomerAssetSnapshot,
    CustomerEnterpriseVerification,
    CustomerHolding,
    CustomerProfile,
    CustomerRiskAssessment,
    Product,
)
from app.schemas.profile import *
from app.services.asset_summary_service import AssetSummaryService
from app.services.customer_tier_service import CustomerTierService
from app.services.profile_cache_service import ProfileCacheService
from app.services.risk_assessment_service import RiskAssessmentService
from app.services.suitability_service import SuitabilityService

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/me", response_model=ApiResponse[ProfileSummary])
async def my_summary(request: Request, user: Annotated[User, Depends(current_user)]):
    async with request.app.state.database.session_factory() as s:
        profile = (
            await s.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user.id)
            )
        ).scalar_one_or_none()
        asset = await AssetSummaryService().latest_or_derived(s, user.id)
        holdings = list(
            (
                await s.execute(
                    select(CustomerHolding).where(
                        CustomerHolding.user_id == user.id,
                        CustomerHolding.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )
        return ApiResponse(
            data=ProfileSummary(
                profile=ProfileResponse.model_validate(profile, from_attributes=True)
                if profile
                else None,
                latest_asset=AssetSnapshotResponse.model_validate(
                    asset, from_attributes=True
                )
                if asset
                else None,
                holdings=[
                    HoldingResponse.model_validate(h, from_attributes=True)
                    for h in holdings
                ],
            )
        )


@router.put("/me", response_model=ApiResponse[ProfileResponse])
async def update_profile(
    request: Request,
    payload: ProfileRequest,
    user: Annotated[User, Depends(current_user)],
):
    async with request.app.state.database.session_factory() as s:
        obj = (
            await s.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user.id)
            )
        ).scalar_one_or_none()
        if obj is None:
            obj = CustomerProfile(id=str(uuid4()), user_id=user.id)
            s.add(obj)
        for k, v in payload.model_dump().items():
            setattr(obj, k, v)
        s.add(
            AuditLog(
                id=str(uuid4()),
                actor_user_id=user.id,
                action="update",
                resource_type="customer_profile",
                resource_id=obj.id,
                detail="customer editable profile updated",
            )
        )
        await s.commit()
        if hasattr(request.app.state, "redis"):
            await ProfileCacheService(request.app.state.redis).invalidate(user.id)
        await s.refresh(obj)
        return ApiResponse(
            data=ProfileResponse.model_validate(obj, from_attributes=True)
        )


@router.post(
    "/me/assets", response_model=ApiResponse[AssetSnapshotResponse], status_code=201
)
async def create_asset(
    request: Request,
    payload: AssetSnapshotRequest,
    user: Annotated[User, Depends(current_user)],
):
    async with request.app.state.database.session_factory() as s:
        obj = CustomerAssetSnapshot(
            id=str(uuid4()), user_id=user.id, **payload.model_dump()
        )
        s.add(obj)
        await s.commit()
        if hasattr(request.app.state, "redis"):
            await ProfileCacheService(request.app.state.redis).invalidate(user.id)
        await s.refresh(obj)
        return ApiResponse(
            data=AssetSnapshotResponse.model_validate(obj, from_attributes=True)
        )


@router.get("/staff/products", response_model=ApiResponse[ProductListResponse])
async def staff_products(
    request: Request,
    user: Annotated[User, Depends(require_permission("product:write"))],
    q: str = "",
    status: str = "active",
    limit: int = 20,
    offset: int = 0,
):
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    async with request.app.state.database.session_factory() as s:
        query = select(Product)
        if status == "active":
            query = query.where(
                Product.deleted_at.is_(None), Product.status != "deleted"
            )
        elif status == "deleted":
            query = query.where(
                Product.deleted_at.is_not(None), Product.status == "deleted"
            )
        if q.strip():
            needle = f"%{q.strip()}%"
            query = query.where(
                or_(Product.name.ilike(needle), Product.product_type.ilike(needle))
            )
        if status not in {"active", "deleted", "all"} and status.strip():
            query = query.where(Product.status == status.strip())
        total = (
            await s.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = list(
            (await s.execute(query.order_by(Product.name).offset(offset).limit(limit)))
            .scalars()
            .all()
        )
        return ApiResponse(
            data=ProductListResponse(
                items=[
                    ProductResponse.model_validate(row, from_attributes=True)
                    for row in rows
                ],
                total=total,
            )
        )


@router.post(
    "/staff/products", response_model=ApiResponse[ProductResponse], status_code=201
)
async def create_staff_product(
    request: Request,
    payload: ProductRequest,
    user: Annotated[User, Depends(require_permission("product:write"))],
):
    async with request.app.state.database.session_factory() as s:
        # 过滤 Product ORM 不存在的字段（如 schema 中的 target_customer_tiers）
        fields = {k: v for k, v in payload.model_dump().items() if k != "target_customer_tiers"}
        product = Product(id=str(uuid4()), **fields)
        s.add(product)
        await s.commit()
        await s.refresh(product)
        # 动态同步：新增产品节点到图谱（图谱不可用时静默降级）
        await request.app.state.knowledge_graph.sync_product(
            product.id,
            product.name,
            product.product_type,
            product.risk_level,
            fund_manager=f"{product.name[:2]}基金经理",
            industry=_PRODUCT_INDUSTRY.get(product.name, "其他"),
        )
        return ApiResponse(
            data=ProductResponse.model_validate(product, from_attributes=True)
        )


@router.put("/staff/products/{product_id}", response_model=ApiResponse[ProductResponse])
async def update_staff_product(
    request: Request,
    product_id: str,
    payload: ProductRequest,
    user: Annotated[User, Depends(require_permission("product:write"))],
):
    async with request.app.state.database.session_factory() as s:
        product = (
            await s.execute(
                select(Product).where(
                    Product.id == product_id, Product.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if product is None:
            raise HTTPException(status_code=404, detail="product not found")
        fields = {k: v for k, v in payload.model_dump().items() if k != "target_customer_tiers"}
        for key, value in fields.items():
            setattr(product, key, value)
        await s.commit()
        await s.refresh(product)
        # 动态同步：产品信息变更后更新图谱节点（图谱不可用时静默降级）
        await request.app.state.knowledge_graph.sync_product(
            product.id,
            product.name,
            product.product_type,
            product.risk_level,
            fund_manager=f"{product.name[:2]}基金经理",
            industry=_PRODUCT_INDUSTRY.get(product.name, "其他"),
        )
        return ApiResponse(
            data=ProductResponse.model_validate(product, from_attributes=True)
        )


@router.delete(
    "/staff/products/{product_id}", response_model=ApiResponse[ProductResponse]
)
async def delete_staff_product(
    request: Request,
    product_id: str,
    user: Annotated[User, Depends(require_permission("product:write"))],
):
    async with request.app.state.database.session_factory() as s:
        product = (
            await s.execute(
                select(Product).where(
                    Product.id == product_id, Product.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if product is None:
            raise HTTPException(status_code=404, detail="product not found")
        product.status = "deleted"
        product.deleted_at = datetime.now(UTC)
        s.add(
            AuditLog(
                id=str(uuid4()),
                actor_user_id=user.id,
                action="soft_delete",
                resource_type="product",
                resource_id=product.id,
                detail=product.name,
            )
        )
        await s.commit()
        await s.refresh(product)
        # 动态同步：从图谱移除产品节点及其关系（图谱不可用时静默降级）
        await request.app.state.knowledge_graph.delete_product(product.id)
        return ApiResponse(
            data=ProductResponse.model_validate(product, from_attributes=True)
        )


@router.put(
    "/staff/products/{product_id}/restore", response_model=ApiResponse[ProductResponse]
)
async def restore_staff_product(
    request: Request,
    product_id: str,
    user: Annotated[User, Depends(require_permission("product:write"))],
):
    async with request.app.state.database.session_factory() as s:
        product = (
            await s.execute(select(Product).where(Product.id == product_id))
        ).scalar_one_or_none()
        if product is None:
            raise HTTPException(status_code=404, detail="product not found")
        product.status = "active"
        product.deleted_at = None
        s.add(
            AuditLog(
                id=str(uuid4()),
                actor_user_id=user.id,
                action="restore",
                resource_type="product",
                resource_id=product.id,
                detail=product.name,
            )
        )
        await s.commit()
        await s.refresh(product)
        # 动态同步：产品恢复后重建图谱节点（图谱不可用时静默降级）
        await request.app.state.knowledge_graph.sync_product(
            product.id,
            product.name,
            product.product_type,
            product.risk_level,
            fund_manager=f"{product.name[:2]}基金经理",
            industry=_PRODUCT_INDUSTRY.get(product.name, "其他"),
        )
        return ApiResponse(
            data=ProductResponse.model_validate(product, from_attributes=True)
        )


@router.get("/products", response_model=ApiResponse[list[ProductResponse]])
async def products(request: Request):
    async with request.app.state.database.session_factory() as s:
        rows = list(
            (
                await s.execute(
                    select(Product)
                    .where(Product.status == "active", Product.deleted_at.is_(None))
                    .order_by(Product.name)
                )
            )
            .scalars()
            .all()
        )
        return ApiResponse(
            data=[ProductResponse.model_validate(x, from_attributes=True) for x in rows]
        )


@router.get("/staff/customers", response_model=ApiResponse[StaffCustomerListResponse])
async def staff_customers(
    request: Request,
    user: Annotated[User, Depends(staff_customer_user)],
    q: str = "",
    tier: str = "",
    customer_type: str = "",
    limit: int = 50,
    offset: int = 0,
):
    limit = min(max(limit, 1), 100)
    async with request.app.state.database.session_factory() as s:
        customer_role_filter = or_(
            *(User.roles.any(code=code) for code in CUSTOMER_ROLE_CODES)
        )
        query = select(User).where(User.status == "active", customer_role_filter)
        if q.strip():
            needle = f"%{q.strip()}%"
            query = query.where(
                or_(User.username.ilike(needle), User.display_name.ilike(needle))
            )
        # 层级和客户类型需要结合实时资产计算，不能在数据库分页后再过滤，
        # 否则会出现“当前页没有数据但后续页有数据”以及 total 不准确的问题。
        users = list((await s.execute(query.order_by(User.created_at))).scalars().all())
        items = []
        for customer in users:
            profile = (
                await s.execute(
                    select(CustomerProfile).where(
                        CustomerProfile.user_id == customer.id
                    )
                )
            ).scalar_one_or_none()
            asset = await AssetSummaryService().latest_or_derived(s, customer.id)
            holding_count = (
                await s.execute(
                    select(func.count())
                    .select_from(CustomerHolding)
                    .where(
                        CustomerHolding.user_id == customer.id,
                        CustomerHolding.status == "active",
                    )
                )
            ).scalar_one()
            tier_data = await CustomerTierService().calculate(
                s, customer.id, persist=True
            )
            if tier.strip() and tier_data["customer_tier"] != tier.strip():
                continue
            if (
                customer_type.strip()
                and tier_data["customer_type"] != customer_type.strip()
            ):
                continue
            risk = (
                (
                    await s.execute(
                        select(CustomerRiskAssessment)
                        .where(
                            CustomerRiskAssessment.user_id == customer.id,
                            CustomerRiskAssessment.status == "active",
                        )
                        .order_by(CustomerRiskAssessment.assessed_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            items.append(
                StaffCustomerListItem(
                    id=customer.id,
                    username=customer.username,
                    display_name=customer.display_name,
                    status=customer.status,
                    profile=ProfileResponse.model_validate(
                        profile, from_attributes=True
                    )
                    if profile
                    else None,
                    latest_asset=AssetSnapshotResponse.model_validate(
                        asset, from_attributes=True
                    )
                    if asset
                    else None,
                    customer_tier=tier_data["customer_tier"],
                    tier_reasons=tier_data["reasons"],
                    risk_level=risk.risk_level if risk else None,
                    risk_score=risk.score if risk else None,
                    risk_status=risk.status if risk else None,
                    holding_count=holding_count,
                )
            )
        total = len(items)
        page_items = items[offset : offset + limit]
        return ApiResponse(
            data=StaffCustomerListResponse(items=page_items, total=total)
        )


@router.get("/staff/customer/{user_id}", response_model=ApiResponse[ProfileSummary])
async def staff_summary(
    request: Request, user_id: str, user: Annotated[User, Depends(staff_customer_user)]
):
    async with request.app.state.database.session_factory() as s:
        profile = (
            await s.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user_id)
            )
        ).scalar_one_or_none()
        asset = await AssetSummaryService().latest_or_derived(s, user_id)
        holdings = list(
            (
                await s.execute(
                    select(CustomerHolding).where(
                        CustomerHolding.user_id == user_id,
                        CustomerHolding.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )
        return ApiResponse(
            data=ProfileSummary(
                profile=ProfileResponse.model_validate(profile, from_attributes=True)
                if profile
                else None,
                latest_asset=AssetSnapshotResponse.model_validate(
                    asset, from_attributes=True
                )
                if asset
                else None,
                holdings=[
                    HoldingResponse.model_validate(h, from_attributes=True)
                    for h in holdings
                ],
            )
        )


@router.get(
    "/me/risk-assessment", response_model=ApiResponse[RiskAssessmentResponse | None]
)
async def my_risk_assessment(
    request: Request, user: Annotated[User, Depends(current_user)]
):
    async with request.app.state.database.session_factory() as session:
        item = (
            (
                await session.execute(
                    select(CustomerRiskAssessment)
                    .where(
                        CustomerRiskAssessment.user_id == user.id,
                        CustomerRiskAssessment.status == "active",
                    )
                    .order_by(CustomerRiskAssessment.assessed_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        return ApiResponse(
            data=RiskAssessmentResponse(
                id=item.id,
                user_id=item.user_id,
                risk_level=item.risk_level,
                score=item.score,
                answers=json.loads(item.answers_json or "{}"),
                status=item.status,
                source_type=item.source_type,
                assessed_at=item.assessed_at,
                expires_at=item.expires_at,
            )
            if item
            else None
        )


@router.post("/me/risk-assessment", response_model=ApiResponse[RiskAssessmentResponse])
async def submit_risk_assessment(
    request: Request,
    payload: RiskAssessmentRequest,
    user: Annotated[User, Depends(current_user)],
):
    async with request.app.state.database.session_factory() as session:
        item = await RiskAssessmentService().assess(
            session, user.id, payload.model_dump()
        )
        session.add(
            AuditLog(
                id=str(uuid4()),
                actor_user_id=user.id,
                action="submit",
                resource_type="risk_assessment",
                resource_id=item.id,
                detail=f"risk_level={item.risk_level};score={item.score}",
            )
        )
        # F2.1 风评 ↔ 画像联动：提交后触发画像重算
        try:
            from app.services.profile_calculation_service import (
                ProfileCalculationService,
            )

            await ProfileCalculationService().calculate(session, user.id)
        except Exception:  # noqa: BLE001
            pass
        await session.commit()
        if hasattr(request.app.state, "redis"):
            await ProfileCacheService(request.app.state.redis).invalidate(user.id)
        await session.refresh(item)
        return ApiResponse(
            data=RiskAssessmentResponse(
                id=item.id,
                user_id=item.user_id,
                risk_level=item.risk_level,
                score=item.score,
                answers=payload.model_dump(),
                status=item.status,
                source_type=item.source_type,
                assessed_at=item.assessed_at,
                expires_at=item.expires_at,
            )
        )


@router.get(
    "/me/enterprise-verification",
    response_model=ApiResponse[EnterpriseVerificationResponse | None],
)
async def my_enterprise_verification(
    request: Request, user: Annotated[User, Depends(current_user)]
):
    async with request.app.state.database.session_factory() as session:
        item = (
            await session.execute(
                select(CustomerEnterpriseVerification).where(
                    CustomerEnterpriseVerification.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        return ApiResponse(
            data=EnterpriseVerificationResponse.model_validate(
                item, from_attributes=True
            )
            if item
            else None
        )


@router.post(
    "/me/enterprise-verification",
    response_model=ApiResponse[EnterpriseVerificationResponse],
    status_code=201,
)
async def submit_enterprise_verification(
    request: Request,
    payload: EnterpriseVerificationRequest,
    user: Annotated[User, Depends(current_user)],
):
    if not is_customer_user(user):
        raise HTTPException(status_code=403, detail="customer account required")
    async with request.app.state.database.session_factory() as session:
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user.id)
            )
        ).scalar_one_or_none()
        if profile is not None and profile.customer_type == "enterprise":
            raise HTTPException(
                status_code=409, detail="customer is already an enterprise customer"
            )
        item = (
            await session.execute(
                select(CustomerEnterpriseVerification).where(
                    CustomerEnterpriseVerification.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if item is not None and item.status == "approved":
            raise HTTPException(
                status_code=409, detail="enterprise verification already approved"
            )
        if item is None:
            item = CustomerEnterpriseVerification(
                id=str(uuid4()), user_id=user.id, **payload.model_dump()
            )
            session.add(item)
        else:
            for key, value in payload.model_dump().items():
                setattr(item, key, value)
            item.status = "pending"
            item.review_note = ""
            item.reviewed_by = None
            item.reviewed_at = None
        session.add(
            AuditLog(
                id=str(uuid4()),
                actor_user_id=user.id,
                action="submit",
                resource_type="enterprise_verification",
                resource_id=item.id,
                detail=payload.company_name,
            )
        )
        await session.commit()
        await session.refresh(item)
        return ApiResponse(
            data=EnterpriseVerificationResponse.model_validate(
                item, from_attributes=True
            )
        )


@router.get("/me/tier", response_model=ApiResponse[CustomerTierResponse])
async def customer_tier(request: Request, user: Annotated[User, Depends(current_user)]):
    async with request.app.state.database.session_factory() as session:
        data = await CustomerTierService().calculate(
            session, str(user.id), persist=True
        )
        await session.commit()
        return ApiResponse(data=CustomerTierResponse(**data))


@router.get("/me/recommendations", response_model=ApiResponse[RecommendationResponse])
async def recommendations(
    request: Request, user: Annotated[User, Depends(current_user)]
):
    async with request.app.state.database.session_factory() as session:
        data = await SuitabilityService().recommend(session, str(user.id))
        return ApiResponse(data=data)
