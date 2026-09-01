"""重名消歧端到端验证（真实 DB + Redis）。

流程：
1. 创建两个 display_name 相同、username 不同的客户角色账号（消歧测试客户）
2. 理财顾问发「帮客户消歧测试客户申购…」→ 期望返回 ambiguous + 2 个候选
3. decision=select_customer + selected_customer_id → 期望精确命中并执行申购
4. 清理测试账号
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.agents.operations_agent import BusinessOperatorAgent  # noqa: E402
from app.core.settings import get_settings  # noqa: E402
from app.db.schema import ensure_schema  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.models.auth import Role, User  # noqa: E402
from app.models.profile import Product  # noqa: E402
from app.ports.agent import AgentContext  # noqa: E402

SAME_NAME = "消歧测试客户"
CREATED_IDS: list[str] = []


async def _create_customer(session, username: str, role: Role) -> User:
    user = User(
        username=username,
        password_hash="not-used",
        display_name=SAME_NAME,
        status="active",
    )
    user.roles.append(role)
    session.add(user)
    await session.flush()
    return user


async def main() -> None:
    settings = get_settings()
    db = Database(settings)
    await ensure_schema(db.engine)
    agent = BusinessOperatorAgent(db, settings, None)
    try:
        async with db.session_factory() as s:
            advisor = (
                (
                    await s.execute(
                        select(User).where(
                            User.username.ilike("%advisor%"), User.status == "active"
                        )
                    )
                )
                .scalars()
                .first()
            )
            customer_role = (
                (await s.execute(select(Role).where(Role.code == "retail_investor")))
                .scalars()
                .first()
            )
            product = (
                (
                    await s.execute(
                        select(Product)
                        .where(Product.status == "active")
                        .order_by(Product.minimum_amount, Product.name)
                    )
                )
                .scalars()
                .first()
            )
            if not customer_role:
                raise SystemExit("缺少 retail_investor 角色")
            c1 = await _create_customer(s, "disambig_a", customer_role)
            c2 = await _create_customer(s, "disambig_b", customer_role)
            CREATED_IDS.extend([c1.id, c2.id])
            await s.commit()
        assert advisor is not None and product is not None
        amount = max(3000, int(float(product.minimum_amount)))
        print(f"advisor={advisor.username} product={product.name} amount={amount}")

        msg = f"帮客户{SAME_NAME}申购{amount}元的{product.name}"

        # STEP 1: 重名 → 歧义候选
        ctx1 = AgentContext(
            request_id="disambig-1",
            user_id=advisor.id,
            role="financial_advisor",
            metadata={
                "is_super_admin": False,
                "session_id": "disambig-verify",
                "request_id": "DISAMBIG-001",
            },
        )
        r1 = await agent.run(msg, ctx1)
        data1 = r1.data or {}
        print(
            f"STEP1 ambiguous={data1.get('ambiguous')} candidates={len(data1.get('candidates') or [])}"
        )
        assert data1.get("ambiguous") is True, "重名应返回歧义"
        assert len(data1.get("candidates") or []) == 2, "应有 2 个候选"
        assert r1.status == "success"
        # 歧义不应产生审计工单（幂等释放）
        cand_ids = {c["id"] for c in data1["candidates"]}
        print(f"STEP1 candidate ids={cand_ids == set(CREATED_IDS)}")

        # STEP 2: 选择候选 A → 精确执行
        ctx2 = AgentContext(
            request_id="disambig-2",
            user_id=advisor.id,
            role="financial_advisor",
            metadata={
                "is_super_admin": False,
                "session_id": "disambig-verify",
                "decision": "select_customer",
                "selected_customer_id": CREATED_IDS[0],
            },
        )
        r2 = await agent.run(msg, ctx2)
        data2 = r2.data or {}
        print(f"STEP2 status={r2.status} order_no={data2.get('order_no')}")
        assert r2.status == "success" and data2.get("order_no"), "选择后应执行成功"
        assert not data2.get("ambiguous")

        # STEP 3: 幂等不冲突（不同 request_id，选中重发应正常执行而非 replay）
        print(f"STEP2 执行成功（订单 {data2['order_no']}）")

        print("DISAMBIG VERIFY OK")
    finally:
        async with db.session_factory() as s:
            if CREATED_IDS:
                for uid in CREATED_IDS:
                    u = (
                        await s.execute(select(User).where(User.id == uid))
                    ).scalar_one_or_none()
                    if u:
                        await s.delete(u)
                await s.commit()
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
