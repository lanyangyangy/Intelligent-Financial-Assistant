from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.common.response import ApiResponse
from app.common.security.auth import require_permission
from app.common.security.roles import (
    BUSINESS_ROLE_CODES,
    CUSTOMER_ROLE_CODES,
    DEPRECATED_ROLE_CODES,
    INTERNAL_ROLE_CODES,
)
from app.models.audit import AuditLog
from app.models.auth import Permission, Role, User
from app.models.profile import CustomerEnterpriseVerification, CustomerProfile
from app.schemas.admin import *
from app.schemas.profile import EnterpriseVerificationResponse

router = APIRouter(prefix="/admin", tags=["admin"])


def ensure_admin(user: User) -> User:
    if not user.is_super_admin and not any(
        role.code == "super_admin" for role in user.roles
    ):
        raise HTTPException(status_code=403, detail="admin permission required")
    return user


def user_response(user: User) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        status=user.status,
        is_super_admin=user.is_super_admin,
        roles=[role.code for role in user.roles],
        created_at=user.created_at,
        updated_at=user.updated_at,
        deleted_at=user.deleted_at,
    )


@router.get("/users", response_model=ApiResponse[AdminUserListResponse])
async def list_users(
    request: Request,
    q: str = Query(""),
    role: str = Query(""),
    status: str = Query("active", pattern="^(active|deleted|all)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: Annotated[User, Depends(require_permission("admin:write"))] = None,
):
    ensure_admin(user)
    async with request.app.state.database.session_factory() as session:
        query = (
            select(User)
            .options(selectinload(User.roles))
            .order_by(User.created_at.desc())
        )
        if status == "active":
            query = query.where(User.deleted_at.is_(None), User.status != "deleted")
        elif status == "deleted":
            query = query.where(User.deleted_at.is_not(None), User.status == "deleted")
        if q.strip():
            needle = f"%{q.strip()}%"
            query = query.where(
                or_(User.username.ilike(needle), User.display_name.ilike(needle))
            )
        if role.strip():
            query = query.where(User.roles.any(code=role.strip()))
        total = (
            await session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        users = list(
            (await session.execute(query.offset(offset).limit(limit))).scalars().all()
        )
        return ApiResponse(
            data=AdminUserListResponse(
                total=total, items=[user_response(item) for item in users]
            )
        )


@router.get("/enterprise-verifications", response_model=ApiResponse[list[dict]])
async def list_enterprise_verifications(
    request: Request,
    status: str = Query("pending"),
    user: Annotated[User, Depends(require_permission("admin:write"))] = None,
):
    ensure_admin(user)
    async with request.app.state.database.session_factory() as session:
        query = select(CustomerEnterpriseVerification).order_by(
            CustomerEnterpriseVerification.created_at.desc()
        )
        if status in {"pending", "approved", "rejected"}:
            query = query.where(CustomerEnterpriseVerification.status == status)
        rows = list((await session.execute(query)).scalars().all())
        return ApiResponse(
            data=[
                {
                    **EnterpriseVerificationResponse.model_validate(
                        row, from_attributes=True
                    ).model_dump(mode="json")
                }
                for row in rows
            ]
        )


@router.post(
    "/enterprise-verifications/{verification_id}/review",
    response_model=ApiResponse[dict],
)
async def review_enterprise_verification(
    request: Request,
    verification_id: str,
    payload: EnterpriseVerificationReviewRequest,
    user: Annotated[User, Depends(require_permission("admin:write"))] = None,
):
    ensure_admin(user)
    async with request.app.state.database.session_factory() as session:
        item = (
            await session.execute(
                select(CustomerEnterpriseVerification).where(
                    CustomerEnterpriseVerification.id == verification_id
                )
            )
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="verification not found")
        if item.status == "approved" and payload.approved:
            raise HTTPException(status_code=409, detail="verification already approved")
        item.status = "approved" if payload.approved else "rejected"
        item.review_note = payload.note
        item.reviewed_by = user.id
        item.reviewed_at = datetime.now(UTC)
        if payload.approved:
            profile = (
                await session.execute(
                    select(CustomerProfile).where(
                        CustomerProfile.user_id == item.user_id
                    )
                )
            ).scalar_one_or_none()
            if profile is None:
                profile = CustomerProfile(id=str(uuid4()), user_id=item.user_id)
                session.add(profile)
                await session.flush()
            profile.customer_type = "enterprise"
            profile.customer_tier = "enterprise_standard"
            profile.source_type = "enterprise_verified"
        session.add(
            AuditLog(
                id=str(uuid4()),
                actor_user_id=user.id,
                action="approve" if payload.approved else "reject",
                resource_type="enterprise_verification",
                resource_id=item.id,
                detail=payload.note,
            )
        )
        await session.commit()
        await session.refresh(item)
        return ApiResponse(
            data=EnterpriseVerificationResponse.model_validate(
                item, from_attributes=True
            ).model_dump(mode="json")
        )


@router.get("/roles", response_model=ApiResponse[list[AdminRoleResponse]])
async def list_roles(
    request: Request,
    user: Annotated[User, Depends(require_permission("admin:write"))] = None,
):
    ensure_admin(user)
    async with request.app.state.database.session_factory() as session:
        roles = [
            role
            for role in (
                await session.execute(
                    select(Role)
                    .options(selectinload(Role.permissions))
                    .order_by(Role.code)
                )
            )
            .scalars()
            .all()
            if role.code not in INTERNAL_ROLE_CODES
            and role.code not in DEPRECATED_ROLE_CODES
        ]
        return ApiResponse(
            data=[
                AdminRoleResponse(
                    id=role.id,
                    code=role.code,
                    name=role.name,
                    permissions=[permission.code for permission in role.permissions],
                )
                for role in roles
            ]
        )


@router.get("/recycle-bin", response_model=ApiResponse[dict])
async def recycle_bin(
    request: Request,
    module: str = Query("user", pattern="^(user|product)$"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: Annotated[User, Depends(require_permission("admin:write"))] = None,
):
    ensure_admin(user)
    async with request.app.state.database.session_factory() as session:
        if module == "user":
            query = (
                select(User)
                .options(selectinload(User.roles))
                .where(User.deleted_at.is_not(None), User.status == "deleted")
                .order_by(User.deleted_at.desc())
            )
            total = (
                await session.execute(
                    select(func.count()).select_from(query.subquery())
                )
            ).scalar_one()
            rows = list(
                (await session.execute(query.offset(offset).limit(limit)))
                .scalars()
                .all()
            )
            items = [
                {
                    "id": row.id,
                    "name": row.display_name,
                    "key": row.username,
                    "status": row.status,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    "deleted_at": row.deleted_at,
                    "roles": [role.code for role in row.roles],
                }
                for row in rows
            ]
        else:
            from app.models.profile import Product

            query = (
                select(Product)
                .where(Product.deleted_at.is_not(None), Product.status == "deleted")
                .order_by(Product.deleted_at.desc())
            )
            total = (
                await session.execute(
                    select(func.count()).select_from(query.subquery())
                )
            ).scalar_one()
            rows = list(
                (await session.execute(query.offset(offset).limit(limit)))
                .scalars()
                .all()
            )
            items = [
                {
                    "id": row.id,
                    "name": row.name,
                    "key": row.product_type,
                    "status": row.status,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    "deleted_at": row.deleted_at,
                    "risk_level": row.risk_level,
                }
                for row in rows
            ]
        return ApiResponse(data={"items": items, "total": total, "module": module})


@router.get("/audit-logs", response_model=ApiResponse[dict])
async def audit_logs(
    request: Request,
    action: str = Query(""),
    resource_type: str = Query(""),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: Annotated[User, Depends(require_permission("audit:read"))] = None,
):
    async with request.app.state.database.session_factory() as session:
        query = select(AuditLog).order_by(AuditLog.created_at.desc())
        if action.strip():
            query = query.where(AuditLog.action == action.strip())
        if resource_type.strip():
            query = query.where(AuditLog.resource_type == resource_type.strip())
        total = (
            await session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = list(
            (await session.execute(query.offset(offset).limit(limit))).scalars().all()
        )
        return ApiResponse(
            data={
                "items": [
                    {
                        "id": row.id,
                        "actor_user_id": row.actor_user_id,
                        "action": row.action,
                        "resource_type": row.resource_type,
                        "resource_id": row.resource_id,
                        "detail": row.detail,
                        "created_at": row.created_at,
                    }
                    for row in rows
                ],
                "total": total,
            }
        )


@router.get("/permissions", response_model=ApiResponse[list[AdminPermissionResponse]])
async def list_permissions(
    request: Request,
    user: Annotated[User, Depends(require_permission("admin:write"))] = None,
):
    ensure_admin(user)
    async with request.app.state.database.session_factory() as session:
        rows = list(
            (await session.execute(select(Permission).order_by(Permission.code)))
            .scalars()
            .all()
        )
        return ApiResponse(
            data=[
                AdminPermissionResponse(
                    id=permission.id, code=permission.code, name=permission.name
                )
                for permission in rows
            ]
        )


@router.put("/users/{user_id}/roles", response_model=ApiResponse[AdminUserResponse])
async def update_user_roles(
    request: Request,
    user_id: str,
    payload: AdminRoleUpdateRequest,
    user: Annotated[User, Depends(require_permission("admin:write"))] = None,
):
    ensure_admin(user)
    if any(role_code not in BUSINESS_ROLE_CODES for role_code in payload.roles):
        raise HTTPException(status_code=400, detail="unknown role")
    if (
        str(user.id) == user_id
        and not user.is_super_admin
        and "super_admin" not in payload.roles
    ):
        raise HTTPException(
            status_code=400, detail="cannot remove your own super_admin role"
        )
    async with request.app.state.database.session_factory() as session:
        uid = int(user_id) if str(user_id).isdigit() else -1
        target = (
            await session.execute(
                select(User).options(selectinload(User.roles)).where(User.id == uid)
            )
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="user not found")
        roles = list(
            (await session.execute(select(Role).where(Role.code.in_(payload.roles))))
            .scalars()
            .all()
        )
        if len(roles) != len(set(payload.roles)):
            raise HTTPException(status_code=400, detail="unknown role")
        target.roles = roles
        target.is_super_admin = (
            user.is_super_admin
            if str(user.id) == user_id
            else "super_admin" in payload.roles
        )
        await session.commit()
        await session.refresh(target)
        return ApiResponse(data=user_response(target))


@router.put("/users/{user_id}/status", response_model=ApiResponse[AdminUserResponse])
async def update_user_status(
    request: Request,
    user_id: str,
    status: str = Query(..., pattern="^(active|disabled)$"),
    user: Annotated[User, Depends(require_permission("admin:write"))] = None,
):
    ensure_admin(user)
    if str(user.id) == user_id and status != "active":
        raise HTTPException(status_code=400, detail="cannot disable your own account")
    uid = int(user_id) if str(user_id).isdigit() else -1
    async with request.app.state.database.session_factory() as session:
        target = (
            await session.execute(
                select(User).options(selectinload(User.roles)).where(User.id == uid)
            )
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="user not found")
        target.status = status
        await session.commit()
        await session.refresh(target)
        return ApiResponse(data=user_response(target))


@router.delete("/users/{user_id}", response_model=ApiResponse[AdminUserResponse])
async def soft_delete_user(
    request: Request,
    user_id: str,
    user: Annotated[User, Depends(require_permission("admin:write"))] = None,
):
    ensure_admin(user)
    if str(user.id) == user_id:
        raise HTTPException(status_code=400, detail="cannot delete your own account")
    uid = int(user_id) if str(user_id).isdigit() else -1
    async with request.app.state.database.session_factory() as session:
        target = (
            await session.execute(
                select(User).options(selectinload(User.roles)).where(User.id == uid)
            )
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="user not found")
        target.status = "deleted"
        target.deleted_at = datetime.now(UTC)
        session.add(
            AuditLog(
                id=str(uuid4()),
                actor_user_id=user.id,
                action="soft_delete",
                resource_type="user",
                resource_id=str(target.id),
                detail=target.username,
            )
        )
        await session.commit()
        await session.refresh(target)
        # 动态同步：从图谱移除该客户节点及其关系（图谱不可用时静默降级）
        if any(r.code in CUSTOMER_ROLE_CODES for r in target.roles):
            await request.app.state.knowledge_graph.delete_customer(target.id)
        return ApiResponse(data=user_response(target))


@router.put("/users/{user_id}/restore", response_model=ApiResponse[AdminUserResponse])
async def restore_user(
    request: Request,
    user_id: str,
    user: Annotated[User, Depends(require_permission("admin:write"))] = None,
):
    ensure_admin(user)
    uid = int(user_id) if str(user_id).isdigit() else -1
    async with request.app.state.database.session_factory() as session:
        target = (
            await session.execute(
                select(User).options(selectinload(User.roles)).where(User.id == uid)
            )
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="user not found")
        target.status = "active"
        target.deleted_at = None
        session.add(
            AuditLog(
                id=str(uuid4()),
                actor_user_id=user.id,
                action="restore",
                resource_type="user",
                resource_id=str(target.id),
                detail=target.username,
            )
        )
        await session.commit()
        await session.refresh(target)
        # 动态同步：恢复客户节点到图谱（图谱不可用时静默降级）
        if any(r.code in CUSTOMER_ROLE_CODES for r in target.roles):
            await request.app.state.knowledge_graph.sync_customer(
                target.id, target.display_name, target.username
            )
        return ApiResponse(data=user_response(target))


@router.put(
    "/roles/{role_id}/permissions", response_model=ApiResponse[AdminRoleResponse]
)
async def update_role_permissions(
    request: Request,
    role_id: str,
    payload: AdminRolePermissionsUpdateRequest,
    user: Annotated[User, Depends(require_permission("admin:write"))] = None,
):
    ensure_admin(user)
    async with request.app.state.database.session_factory() as session:
        role = (
            await session.execute(
                select(Role)
                .options(selectinload(Role.permissions))
                .where(Role.id == role_id)
            )
        ).scalar_one_or_none()
        if role is None:
            raise HTTPException(status_code=404, detail="role not found")
        permissions = list(
            (
                await session.execute(
                    select(Permission).where(Permission.code.in_(payload.permissions))
                )
            )
            .scalars()
            .all()
        )
        if len(permissions) != len(set(payload.permissions)):
            raise HTTPException(status_code=400, detail="unknown permission")
        if role.code == "super_admin":
            raise HTTPException(
                status_code=400, detail="super_admin permissions are fixed"
            )
        role.permissions = permissions
        await session.commit()
        await session.refresh(role)
        return ApiResponse(
            data=AdminRoleResponse(
                id=role.id,
                code=role.code,
                name=role.name,
                permissions=[permission.code for permission in role.permissions],
            )
        )


@router.post("/recalculate-confidence", response_model=ApiResponse[dict])
async def recalculate_confidence(
    request: Request,
    user: Annotated[User, Depends(require_permission("admin:write"))] = None,
):
    """手动触发全量置信度校准（F4.3 周期校准任务：POST /api/admin/recalculate-confidence）。

    调用 workers/confidence_calibration_worker.ConfidenceCalibrationWorker.calibrate_once
    对全部 ACTIVE 画像标签重算置信度并回收低置信/超龄标签。
    """
    ensure_admin(user)
    from workers.confidence_calibration_worker import ConfidenceCalibrationWorker

    worker = ConfidenceCalibrationWorker(request.app.state.database)
    try:
        result = await worker.calibrate_once()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"calibration failed: {exc}"
        ) from exc
    session_factory = request.app.state.database.session_factory
    async with session_factory() as session:
        session.add(
            AuditLog(
                id=str(uuid4()),
                actor_user_id=user.id,
                action="recalculate_confidence",
                resource_type="profile_tag",
                resource_id="all",
                detail=f"recalibrated={result['recalibrated']} archived={result['archived']}",
            )
        )
        await session.commit()
    return ApiResponse(data=result)
