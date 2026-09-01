from datetime import datetime

from pydantic import BaseModel, Field


class AdminUserResponse(BaseModel):
    id: int | str
    username: str
    display_name: str
    status: str
    is_super_admin: bool
    roles: list[str]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class AdminUserListResponse(BaseModel):
    items: list[AdminUserResponse]
    total: int


class AdminRoleResponse(BaseModel):
    id: str
    code: str
    name: str
    permissions: list[str]


class AdminPermissionResponse(BaseModel):
    id: str
    code: str
    name: str


class AdminRoleUpdateRequest(BaseModel):
    roles: list[str] = Field(min_length=1)


class AdminRolePermissionsUpdateRequest(BaseModel):
    permissions: list[str] = Field(default_factory=list)


class EnterpriseVerificationReviewRequest(BaseModel):
    approved: bool
    note: str = ""
