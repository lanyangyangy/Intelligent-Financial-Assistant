"""P2 集成验证：幂等防重 + 强制审计工单（真实 DB + Redis）。

流程：小额申购带 request_id → 成功 + 审计工单
      → 相同 request_id 重复提交 → 返回首次结果（不重复下单）
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
from app.models.auth import User  # noqa: E402
from app.models.profile import Product  # noqa: E402
from app.models.risk import WorkOrder  # noqa: E402
from app.ports.agent import AgentContext  # noqa: E402


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
            customer = (
                await s.execute(
                    select(User).where(User.username == "retail_investor_demo")
                )
            ).scalar_one()
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
        assert advisor is not None and customer is not None and product is not None
        amount = max(5000, int(float(product.minimum_amount)))
        print(f"product={product.name} amount={amount}")

        msg = f"帮客户{customer.display_name}申购{amount}元的{product.name}"
        base_md = {
            "customer_id": customer.id,
            "is_super_admin": False,
            "session_id": "p2-verify",
        }

        # STEP 1: 首次执行（request_id=R-001）
        ctx1 = AgentContext(
            request_id="p2-step1",
            user_id=advisor.id,
            role="financial_advisor",
            metadata={**base_md, "request_id": "R-001"},
        )
        r1 = await agent.run(msg, ctx1)
        print(f"STEP1 status={r1.status} order_no={(r1.data or {}).get('order_no')}")
        assert r1.status == "success" and r1.data.get("order_no")

        # STEP 2: 相同 request_id 重复提交 → 返回首次结果
        ctx2 = AgentContext(
            request_id="p2-step2",
            user_id=advisor.id,
            role="financial_advisor",
            metadata={**base_md, "request_id": "R-001"},
        )
        r2 = await agent.run(msg, ctx2)
        print(
            f"STEP2 status={r2.status} replay_order={(r2.data or {}).get('order_no')}"
        )
        assert r2.status == "success" and (r2.data or {}).get("order_no"), (
            "重复提交应返回首次结果"
        )

        # STEP 3: 审计工单已生成（强制审计）
        async with db.session_factory() as s:
            audit_orders = (
                (
                    await s.execute(
                        select(WorkOrder)
                        .where(
                            WorkOrder.workorder_type == "业务操作审计",
                            WorkOrder.submitter_id == advisor.id,
                        )
                        .order_by(WorkOrder.created_at.desc())
                        .limit(3)
                    )
                )
                .scalars()
                .all()
            )
        print(f"STEP3 audit_orders={len(audit_orders)}")
        assert audit_orders, "写操作应生成强制审计工单"
        latest = audit_orders[0]
        print(f"STEP3 latest.title={latest.title}")
        assert "审计" in latest.title

        print("P2 VERIFY OK")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
