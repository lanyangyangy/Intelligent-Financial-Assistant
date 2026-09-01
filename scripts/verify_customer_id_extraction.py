"""客户 ID 优先参数提取端到端验证（真实 DB + Redis）。

场景：理财顾问用「客户ID <username>」发起申购 → 解析器提取 customer_id
      → 精确命中客户（无姓名歧义）→ 大额二次确认 → 确认执行落库。
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
        print(f"advisor={advisor.username} product={product.name} amount={amount}")

        # STEP 1: 用客户 ID 申购（customer_id 优先提取）
        msg = f"帮客户ID {customer.username} 申购{amount}元的{product.name}"
        ctx1 = AgentContext(
            request_id="cid-s1",
            user_id=advisor.id,
            role="financial_advisor",
            metadata={
                "customer_id": customer.id,
                "is_super_admin": False,
                "session_id": "cid-verify",
                "request_id": "CID-001",
            },
        )
        r1 = await agent.run(msg, ctx1)
        cid = r1.data.get("confirmation_id") if r1.data else None
        print(f"STEP1 confirmation={r1.requires_confirmation} cid={cid}")
        assert r1.requires_confirmation and cid, "客户ID大额申购应进入二次确认"

        # STEP 2: 确认执行（客户 ID 精确命中，无姓名歧义）
        ctx2 = AgentContext(
            request_id="cid-s2",
            user_id=advisor.id,
            role="financial_advisor",
            metadata={
                "customer_id": customer.id,
                "is_super_admin": False,
                "session_id": "cid-verify",
                "decision": "confirm",
                "confirmation_id": cid,
            },
        )
        r2 = await agent.run("确认", ctx2)
        print(f"STEP2 status={r2.status} order_no={(r2.data or {}).get('order_no')}")
        assert r2.status == "success" and r2.data.get("order_no"), "确认后应执行成功"
        assert not (r2.data or {}).get("ambiguous"), "客户ID不应触发姓名歧义"

        # STEP 3: 解析器确认 customer_id 提取
        from app.services.operator_parser import parse_operation

        parsed = parse_operation(msg)
        print(f"STEP3 parsed_customer_id={parsed.params.get('customer_id')}")
        assert parsed.params.get("customer_id") == customer.username
        assert "customer_name" not in parsed.params

        print("CUSTOMER-ID VERIFY OK")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
