from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, insert, select

from app.common.security.roles import ALL_PERMISSION_CODES
from app.core.settings import Settings
from app.db.session import Database
from app.models.auth import Permission, Role, User, role_permissions, user_roles
from app.services.auth_service import hash_password

DEMO_ACCOUNTS = [
    (
        "retail_investor_demo",
        "retail_investor",
        "零售投资者演示账号",
        False,
        "Demo@2026RetailInvestor",
    ),
    (
        "minor_investor_demo",
        "retail_investor",
        "未成年投资者熔断测试账号",
        False,
        "Demo@2026Minor",
    ),
    (
        "senior_investor_demo",
        "retail_investor",
        "高龄投资者熔断测试账号",
        False,
        "Demo@2026Senior",
    ),
    (
        "expired_assessment_demo",
        "retail_investor",
        "风评过期熔断测试账号",
        False,
        "Demo@2026Expired",
    ),
    (
        "financial_advisor_demo",
        "financial_advisor",
        "理财顾问演示账号",
        False,
        "Demo@2026FinancialAdvisor",
    ),
    (
        "risk_specialist_demo",
        "risk_specialist",
        "风控专员演示账号",
        False,
        "Demo@2026RiskSpecialist",
    ),
    (
        "customer_manager_demo",
        "customer_manager",
        "客户经理演示账号",
        False,
        "Demo@2026CustomerManager",
    ),
    ("auditor_demo", "auditor", "审计演示账号", False, "Demo@2026Auditor"),
    # 超级管理员是系统维护标记，不作为业务角色；其业务角色仍归入审计只读组。
    ("super_admin_demo", "auditor", "系统维护演示账号", True, "Demo@2026SuperAdmin"),
    (
        "high_net_worth_demo",
        "high_net_worth_customer",
        "高净值客户演示账号",
        False,
        "Demo@2026HighNetWorth",
    ),
]
DEMO_ACCOUNT_PASSWORDS = {item[0]: item[4] for item in DEMO_ACCOUNTS}
PERMISSIONS = [
    ("analytics:read", "使用数据分析"),
    ("customer:read", "查看客户"),
    ("customer:write", "管理客户"),
    ("product:read", "查看产品"),
    ("product:write", "管理产品"),
    ("risk:read", "查看风险"),
    ("risk:write", "管理风险"),
    ("asset:read", "查看资产"),
    ("order:read", "查看订单"),
    ("order:write", "管理订单"),
    ("audit:read", "查看审计"),
    ("admin:write", "管理系统"),
]
ROLE_PERMISSIONS = {
    "retail_investor": ["product:read", "asset:read", "order:read"],
    "high_net_worth_customer": ["product:read", "asset:read", "order:read"],
    "financial_advisor": [
        "analytics:read",
        "customer:read",
        "product:read",
        "asset:read",
        "order:read",
        "risk:read",
    ],
    "risk_specialist": [
        "analytics:read",
        "customer:read",
        "product:read",
        "order:read",
        "risk:read",
        "risk:write",
    ],
    "customer_manager": [
        "analytics:read",
        "customer:read",
        "customer:write",
        "product:read",
        "asset:read",
        "order:read",
        "order:write",
    ],
    "auditor": [
        "analytics:read",
        "customer:read",
        "product:read",
        "risk:read",
        "asset:read",
        "order:read",
        "audit:read",
    ],
    "super_admin": sorted(ALL_PERMISSION_CODES),
    "employee_pending": [],
}

ROLE_NAMES = {
    "retail_investor": "零售投资者",
    "high_net_worth_customer": "高净值客户",
    "financial_advisor": "理财顾问",
    "risk_specialist": "风控专员",
    "customer_manager": "客户经理",
    "auditor": "审计",
    "super_admin": "系统管理员",
    "employee_pending": "待分配员工",
}

LEGACY_ROLE_TARGETS = {
    "customer": "retail_investor",
    "customer_service": "financial_advisor",
    "risk_manager": "risk_specialist",
    "operations": "customer_manager",
    "super_admin": "auditor",
}
LEGACY_ROLE_CODES = set(LEGACY_ROLE_TARGETS) | {"product_manager"}
OBSOLETE_DEMO_ACCOUNTS = {
    "customer_demo",
    "customer_service_demo",
    "product_manager_demo",
    "risk_manager_demo",
    "operations_demo",
    "hNW_demo",
    "enterprise_demo",
}


async def ensure_auth_seed(database: Database, settings: Settings) -> None:
    if not settings.demo_accounts_enabled or settings.app_env not in {
        "development",
        "dev",
        "local",
    }:
        return
    async with database.session_factory() as session:
        permission_map = {}
        # Seed must be safe under reloads and concurrent startup workers.
        # Flush each newly created permission before the next lookup so the
        # SELECT sees pending rows and does not enqueue duplicate INSERTs.
        for code, name in PERMISSIONS:
            permission = (
                await session.execute(select(Permission).where(Permission.code == code))
            ).scalar_one_or_none()
            if permission is None:
                permission = Permission(id=str(uuid4()), code=code, name=name)
                session.add(permission)
                await session.flush()
            elif permission.name != name:
                permission.name = name
            permission_map[code] = permission
        await session.flush()
        role_map = {}
        role_codes = {role_code for _, role_code, _, _, _ in DEMO_ACCOUNTS} | {
            "employee_pending"
        }
        for role_code in role_codes:
            role = (
                await session.execute(select(Role).where(Role.code == role_code))
            ).scalar_one_or_none()
            if role is None:
                role = Role(id=str(uuid4()), code=role_code, name=ROLE_NAMES[role_code])
                session.add(role)
            else:
                await session.refresh(role, attribute_names=["permissions"])
                role.name = ROLE_NAMES[role_code]
            role.permissions.clear()
            role.permissions.extend(
                permission_map[code] for code in ROLE_PERMISSIONS[role_code]
            )
            role_map[role_code] = role
        await session.flush()

        # 将已有开发库中的旧角色迁移到新的业务角色；没有对应业务角色的
        # 产品经理角色降级为待分配员工，避免旧权限继续生效。
        for legacy_code in LEGACY_ROLE_CODES:
            legacy_role = (
                await session.execute(select(Role).where(Role.code == legacy_code))
            ).scalar_one_or_none()
            if legacy_role is None:
                continue
            target_role = role_map.get(
                LEGACY_ROLE_TARGETS.get(legacy_code, "employee_pending")
            )
            user_ids = list(
                (
                    await session.execute(
                        select(User.id).where(User.roles.any(code=legacy_code))
                    )
                )
                .scalars()
                .all()
            )
            for user_id in user_ids:
                await session.execute(
                    delete(user_roles).where(
                        user_roles.c.user_id == user_id,
                        user_roles.c.role_id == legacy_role.id,
                    )
                )
                if target_role is not None:
                    already_assigned = (
                        await session.execute(
                            select(user_roles.c.user_id).where(
                                user_roles.c.user_id == user_id,
                                user_roles.c.role_id == target_role.id,
                            )
                        )
                    ).scalar_one_or_none()
                    if already_assigned is None:
                        await session.execute(
                            insert(user_roles).values(
                                user_id=user_id, role_id=target_role.id
                            )
                        )
            await session.execute(
                delete(role_permissions).where(
                    role_permissions.c.role_id == legacy_role.id
                )
            )
            await session.execute(
                delete(user_roles).where(user_roles.c.role_id == legacy_role.id)
            )
            await session.execute(
                delete(Role.__table__).where(Role.id == legacy_role.id)
            )
        await session.flush()

        for (
            username,
            role_code,
            display_name,
            is_super_admin,
            password,
        ) in DEMO_ACCOUNTS:
            user = (
                await session.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
            if user is None:
                user = User(
                    username=username,
                    password_hash=hash_password(password),
                    display_name=display_name,
                    is_super_admin=is_super_admin,
                    status="active",
                )
                session.add(user)
            else:
                await session.refresh(user, attribute_names=["roles"])
                user.display_name = display_name
                user.status = "active"
                user.deleted_at = None
            user.roles.clear()
            user.roles.append(role_map[role_code])
            user.is_super_admin = is_super_admin

        # 旧演示账号不再作为可登录账号保留，避免后台继续出现多余角色。
        for username in OBSOLETE_DEMO_ACCOUNTS:
            user = (
                await session.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
            if user is None:
                continue
            await session.refresh(user, attribute_names=["roles"])
            user.roles.clear()
            user.status = "deleted"
            user.deleted_at = datetime.now(UTC)
            user.is_super_admin = False
        await session.commit()
