"""P1 集成验证：confirmation_id 双轨确认协议端到端（真实 DB + Redis）。

流程：首次大额申购 → requires_confirmation + confirmation_id
      → decision=confirm + confirmation_id → 执行落库
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
        amount = max(20000, int(float(product.minimum_amount)))
        print(
            f"advisor={advisor.username} customer={customer.display_name} "
            f"product={product.name} amount={amount}"
        )

        # STEP 1: 首次大额申购（> 1 万阈值）→ 需二次确认
        ctx1 = AgentContext(
            request_id="p1-step1",
            user_id=advisor.id,
            role="financial_advisor",
            metadata={
                "customer_id": customer.id,
                "is_super_admin": False,
                "session_id": "p1-verify",
            },
        )
        msg = f"帮客户{customer.display_name}申购20000元的{product.name}"
        r1 = await agent.run(msg, ctx1)
        cid = r1.data.get("confirmation_id") if r1.data else None
        print(
            f"STEP1 requires_confirmation={r1.requires_confirmation} confirmation_id={cid}"
        )
        print(f"STEP1 reply={r1.summary[:60]}")
        assert r1.requires_confirmation and cid, "首次大额申购应要求二次确认并返回凭据"

        # STEP 2: 凭 confirmation_id 确认 → 执行落库
        ctx2 = AgentContext(
            request_id="p1-step2",
            user_id=advisor.id,
            role="financial_advisor",
            metadata={
                "customer_id": customer.id,
                "is_super_admin": False,
                "session_id": "p1-verify",
                "decision": "confirm",
                "confirmation_id": cid,
            },
        )
        r2 = await agent.run("确认", ctx2)
        print(f"STEP2 status={r2.status} order_no={(r2.data or {}).get('order_no')}")
        print(f"STEP2 reply={r2.summary[:80]}")
        assert r2.status == "success" and r2.data.get("order_no"), "确认后应执行成功"

        # STEP 3: 同一凭据重复确认 → 应提示过期（防重复确认）
        ctx3 = AgentContext(
            request_id="p1-step3",
            user_id=advisor.id,
            role="financial_advisor",
            metadata={
                "customer_id": customer.id,
                "is_super_admin": False,
                "session_id": "p1-verify",
                "decision": "confirm",
                "confirmation_id": cid,
            },
        )
        r3 = await agent.run("确认", ctx3)
        print(
            f"STEP3 status={r3.status} expired={(r3.data or {}).get('confirmation_expired')}"
        )
        assert (r3.data or {}).get("confirmation_expired"), "重复确认应提示凭据已过期"

        # STEP 4: cancel 协议
        ctx4 = AgentContext(
            request_id="p1-step4",
            user_id=advisor.id,
            role="financial_advisor",
            metadata={
                "customer_id": customer.id,
                "is_super_admin": False,
                "session_id": "p1-verify",
                "decision": "confirm",
                "confirmation_id": "nonexistent",
            },
        )
        r4 = await agent.run("确认", ctx4)
        print(f"STEP4 expired={(r4.data or {}).get('confirmation_expired')}")
        assert (r4.data or {}).get("confirmation_expired")

        print("P1 VERIFY OK")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
