from enum import StrEnum

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


class MeResponse(BaseModel):
    id: int | str
    username: str
    display_name: str
    roles: list[str]
    permissions: list[str]
    is_super_admin: bool


class RegisterType(StrEnum):
    customer = "customer"
    employee = "employee"


class RegisterRequest(BaseModel):
    register_type: RegisterType
    username: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    company_code: str | None = Field(default=None, max_length=64)


class RegisterResponse(BaseModel):
    id: int | str
    username: str
    display_name: str
    account_type: RegisterType
    status: str
    role: str
