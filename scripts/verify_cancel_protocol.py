"""二次确认取消协议端到端验证（真实 DB + Redis）。

流程：
1. 理财顾问大额申购（2万>1万）→ 确认请求 + confirmation_id
2. decision=cancel + confirmation_id → 操作取消（不执行）
3. 取消后释放幂等：相同 request_id 重发申购 → 可重新进入确认（非 replay）
4. 取消审计工单已生成
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
from app.models.operator import OperatorRequestDedupe  # noqa: E402
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
        amount = max(20000, int(float(product.minimum_amount)))
        msg = f"帮客户{customer.display_name}申购{amount}元的{product.name}"
        print(f"advisor={advisor.username} product={product.name} amount={amount}")

        base_md = {
            "customer_id": customer.id,
            "is_super_admin": False,
            "session_id": "cancel-verify",
        }

        # STEP 1: 首次大额申购 → 确认请求
        ctx1 = AgentContext(
            request_id="cancel-s1",
            user_id=advisor.id,
            role="financial_advisor",
            metadata={**base_md, "request_id": "CANCEL-001"},
        )
        r1 = await agent.run(msg, ctx1)
        cid = r1.data.get("confirmation_id") if r1.data else None
        print(f"STEP1 requires_confirmation={r1.requires_confirmation} cid={cid}")
        assert r1.requires_confirmation and cid

        # STEP 2: 取消
        ctx2 = AgentContext(
            request_id="cancel-s2",
            user_id=advisor.id,
            role="financial_advisor",
            metadata={**base_md, "decision": "cancel", "confirmation_id": cid},
        )
        r2 = await agent.run("取消", ctx2)
        print(f"STEP2 cancelled={(r2.data or {}).get('cancelled')}")
        assert (r2.data or {}).get("cancelled") is True

        # STEP 3: 取消后幂等已释放 → 相同 request_id 重发可重新进入确认（非 replay）
        ctx3 = AgentContext(
            request_id="cancel-s3",
            user_id=advisor.id,
            role="financial_advisor",
            metadata={**base_md, "request_id": "CANCEL-001"},
        )
        r3 = await agent.run(msg, ctx3)
        cid3 = r3.data.get("confirmation_id") if r3.data else None
        print(
            f"STEP3 requires_confirmation={r3.requires_confirmation} "
            f"replay={bool((r3.data or {}).get('order_no'))}"
        )
        assert r3.requires_confirmation and cid3, "取消后应可重新发起确认（幂等已释放）"

        # STEP 4: 取消审计工单
        async with db.session_factory() as s:
            cancels = (
                (
                    await s.execute(
                        select(WorkOrder)
                        .where(
                            WorkOrder.workorder_type == "业务操作取消",
                            WorkOrder.submitter_id == advisor.id,
                        )
                        .order_by(WorkOrder.created_at.desc())
                        .limit(3)
                    )
                )
                .scalars()
                .all()
            )
        print(f"STEP4 cancel_audit_orders={len(cancels)}")
        assert cancels, "取消应生成审计工单"
        print(f"STEP4 latest.title={cancels[0].title}")

        # STEP 5: 幂等表无 processing 残留
        async with db.session_factory() as s:
            processing = (
                (
                    await s.execute(
                        select(OperatorRequestDedupe).where(
                            OperatorRequestDedupe.user_id == advisor.id,
                            OperatorRequestDedupe.status == "processing",
                        )
                    )
                )
                .scalars()
                .all()
            )
        print(f"STEP5 processing_left={len(processing)}")
        assert not processing, "取消后不应有 processing 残留"

        print("CANCEL VERIFY OK")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
