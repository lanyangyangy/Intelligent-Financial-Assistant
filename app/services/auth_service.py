from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.core.settings import Settings
from app.db.session import Database
from app.models.auth import RefreshSession, Role, User
from app.schemas.auth import RegisterRequest, RegisterResponse, RegisterType

password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        return False


def now():
    return datetime.now(UTC)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    def __init__(self, database: Database, settings: Settings):
        self.database, self.settings = database, settings

    def create_access_token(self, user: User) -> str:
        roles = [r.code for r in user.roles]
        payload = {
            "sub": str(user.id),
            "username": user.username,
            "roles": roles,
            "is_super_admin": user.is_super_admin,
            "type": "access",
            "exp": now() + timedelta(minutes=self.settings.jwt_expire_minutes),
        }
        return jwt.encode(
            payload, self.settings.jwt_secret, algorithm=self.settings.jwt_algorithm
        )

    async def register(self, request: RegisterRequest) -> RegisterResponse:
        if (
            request.register_type == RegisterType.employee
            and request.company_code != self.settings.employee_registration_code
        ):
            raise ValueError("invalid company registration code")
        async with self.database.session_factory() as session:
            existing = (
                await session.execute(
                    select(User).where(User.username == request.username)
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise ValueError("username already exists")
            role_code = (
                "retail_investor"
                if request.register_type == RegisterType.customer
                else "employee_pending"
            )
            role = (
                await session.execute(select(Role).where(Role.code == role_code))
            ).scalar_one_or_none()
            if role is None:
                role = Role(
                    id=str(uuid4()),
                    code=role_code,
                    name="零售投资者"
                    if role_code == "retail_investor"
                    else "待分配员工",
                )
                session.add(role)
                await session.flush()
            user = User(
                username=request.username,
                password_hash=hash_password(request.password),
                display_name=request.display_name,
                status="active",
                is_super_admin=False,
            )
            user.roles.append(role)
            session.add(user)
            await session.commit()
            return RegisterResponse(
                id=str(user.id),
                username=user.username,
                display_name=user.display_name,
                account_type=request.register_type,
                status=user.status,
                role=role_code,
            )

    async def login(self, username: str, password: str) -> dict:
        async with self.database.session_factory() as session:
            user = (
                await session.execute(
                    select(User)
                    .options(selectinload(User.roles))
                    .where(User.username == username)
                )
            ).scalar_one_or_none()
            if (
                not user
                or user.status != "active"
                or not verify_password(password, user.password_hash)
            ):
                raise ValueError("invalid credentials")
            refresh = secrets.token_urlsafe(64)
            session.add(
                RefreshSession(
                    id=str(uuid4()),
                    user_id=user.id,
                    token_hash=token_hash(refresh),
                    expires_at=now()
                    + timedelta(days=self.settings.refresh_token_expire_days),
                )
            )
            await session.commit()
            return {
                "access_token": self.create_access_token(user),
                "refresh_token": refresh,
                "token_type": "bearer",
                "expires_in": self.settings.jwt_expire_minutes * 60,
            }

    async def refresh(self, refresh_token: str) -> dict:
        async with self.database.session_factory() as session:
            row = (
                await session.execute(
                    select(RefreshSession).where(
                        RefreshSession.token_hash == token_hash(refresh_token),
                        RefreshSession.revoked_at.is_(None),
                        RefreshSession.expires_at > now(),
                    )
                )
            ).scalar_one_or_none()
            if not row:
                raise ValueError("invalid refresh token")
            user = (
                await session.execute(
                    select(User)
                    .options(selectinload(User.roles))
                    .where(User.id == row.user_id)
                )
            ).scalar_one_or_none()
            if not user or user.status != "active":
                raise ValueError("user inactive")
            row.revoked_at = now()
            new_refresh = secrets.token_urlsafe(64)
            session.add(
                RefreshSession(
                    id=str(uuid4()),
                    user_id=user.id,
                    token_hash=token_hash(new_refresh),
                    expires_at=now()
                    + timedelta(days=self.settings.refresh_token_expire_days),
                )
            )
            await session.commit()
            return {
                "access_token": self.create_access_token(user),
                "refresh_token": new_refresh,
                "token_type": "bearer",
                "expires_in": self.settings.jwt_expire_minutes * 60,
            }

    async def revoke(self, refresh_token: str) -> None:
        async with self.database.session_factory() as session:
            await session.execute(
                update(RefreshSession)
                .where(
                    RefreshSession.token_hash == token_hash(refresh_token),
                    RefreshSession.revoked_at.is_(None),
                )
                .values(revoked_at=now())
            )
            await session.commit()
