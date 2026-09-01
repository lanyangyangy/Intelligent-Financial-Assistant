from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.common.security.roles import is_customer_user
from app.core.settings import get_settings
from app.models.auth import Role, User

bearer = HTTPBearer(auto_error=False)


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authorization required"
        )
    settings = get_settings()
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid access token") from exc
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="invalid token type")
    sub = payload.get("sub")
    try:
        uid = int(sub) if sub is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="invalid access token") from None
    if uid is None:
        raise HTTPException(status_code=401, detail="invalid access token")
    async with request.app.state.database.session_factory() as session:
        user = (
            await session.execute(
                select(User)
                .options(selectinload(User.roles).selectinload(Role.permissions))
                .where(User.id == uid, User.status == "active")
            )
        ).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="user inactive")
        return user


def require_permission(code: str):
    async def dependency(user: Annotated[User, Depends(current_user)]) -> User:
        if user.is_super_admin:
            return user
        permissions = {p.code for role in user.roles for p in role.permissions}
        if code not in permissions:
            raise HTTPException(status_code=403, detail="permission denied")
        return user

    return dependency


def require_staff_permission(code: str):
    permission_dependency = require_permission(code)

    async def dependency(user: User = Depends(permission_dependency)) -> User:
        if is_customer_user(user):
            raise HTTPException(status_code=403, detail="staff permission required")
        return user

    return dependency


async def staff_customer_user(user: Annotated[User, Depends(current_user)]) -> User:
    if user.is_super_admin:
        return user
    permissions = {p.code for role in user.roles for p in role.permissions}
    if "customer:read" not in permissions or is_customer_user(user):
        raise HTTPException(status_code=403, detail="staff permission required")
    return user
