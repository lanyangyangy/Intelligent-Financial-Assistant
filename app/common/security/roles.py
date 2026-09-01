"""业务账号角色定义与统一角色判断。"""

from __future__ import annotations

from collections.abc import Iterable

from app.models.auth import User

# 业务账号角色以需求文档中的三类内部员工、两类外部客户为准。
STAFF_ROLE_CODES = frozenset({
    "financial_advisor",
    "risk_specialist",
    "customer_manager",
    "auditor",
})
CUSTOMER_ROLE_CODES = frozenset({
    "retail_investor",
    "high_net_worth_customer",
})
BUSINESS_ROLE_CODES = STAFF_ROLE_CODES | CUSTOMER_ROLE_CODES | {"auditor"}
DEPRECATED_ROLE_CODES = frozenset({
    "customer",
    "customer_service",
    "product_manager",
    "risk_manager",
    "operations",
    "super_admin",
})

# 仅用于账号注册/系统管理流程，不作为业务演示角色展示。
INTERNAL_ROLE_CODES = frozenset({"employee_pending", "super_admin"})
ALL_PERMISSION_CODES = frozenset({
    "analytics:read",
    "customer:read",
    "customer:write",
    "product:read",
    "product:write",
    "risk:read",
    "risk:write",
    "asset:read",
    "order:read",
    "order:write",
    "audit:read",
    "admin:write",
})


def has_any_role(user: User, role_codes: Iterable[str]) -> bool:
    expected = set(role_codes)
    return any(role.code in expected for role in user.roles)


def is_customer_user(user: User) -> bool:
    return has_any_role(user, CUSTOMER_ROLE_CODES)


def is_staff_user(user: User) -> bool:
    return user.is_super_admin or has_any_role(user, STAFF_ROLE_CODES)
