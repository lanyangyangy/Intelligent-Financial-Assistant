"""客户不存在时不触发二次确认 验证脚本（真实 DB + Redis）。

场景：
1. 转入方不存在（"把李伟的60000元转到张明账户"）→ 直接 fail，不进入二次确认
2. 转出方不存在 → 直接 fail，不进入二次确认
3. 双方存在 + 超阈值 → 正常进入二次确认
4. 赎回客户不存在 + 全部赎回 → 直接 fail，不进入二次确认
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
from app.ports.agent import AgentContext  # noqa: E402


async def main() -> None:
    settings = get_settings()
    db = Database(settings)
    await ensure_schema(db.engine)
    agent = BusinessOperatorAgent(db, settings, None)
    try:
        async with db.session_factory() as s:
            manager = (
                (
                    await s.execute(
                        select(User).where(
                            User.username.ilike("%manager%"), User.status == "active"
                        )
                    )
                )
                .scalars()
                .first()
            )
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
        assert manager is not None and advisor is not None and customer is not None
        base_md = {
            "customer_id": customer.id,
            "is_super_admin": False,
            "session_id": "no-customer-verify",
        }

        def ctx(rid: str) -> AgentContext:
            return AgentContext(
                request_id=rid,
                user_id=manager.id,
                role="customer_manager",
                metadata={**base_md, "request_id": f"NC-{rid}"},
            )

        # STEP 1: 转入方不存在 → 直接 fail，不进入二次确认
        r1 = await agent.run("把李伟的60000元转到张明账户", ctx("s1"))
        print(f"STEP1 status={r1.status} confirmation={r1.requires_confirmation}")
        print(f"STEP1 summary={r1.summary[:60]}")
        assert r1.status == "error" and not r1.requires_confirmation
        assert "未找到客户「张明」" in r1.summary

        # STEP 2: 转出方不存在 → 直接 fail，不进入二次确认
        r2 = await agent.run("把不存在的客户甲的60000元转到李伟账户", ctx("s2"))
        print(f"STEP2 status={r2.status} confirmation={r2.requires_confirmation}")
        print(f"STEP2 summary={r2.summary[:60]}")
        assert r2.status == "error" and not r2.requires_confirmation

        # STEP 3: 双方存在 + 超阈值 → 正常进入二次确认
        r3 = await agent.run("把李伟的60000元转到王芳账户", ctx("s3"))
        cid3 = r3.data.get("confirmation_id") if r3.data else None
        print(f"STEP3 confirmation={r3.requires_confirmation} cid={cid3}")
        assert r3.requires_confirmation and cid3

        # STEP 4: 赎回不存在的客户 + 全部赎回 → 直接 fail，不进入二次确认（理财顾问）
        r4 = await agent.run(
            "赎回不存在的客户甲持有的国债逆回购优选全部份额",
            AgentContext(
                request_id="s4",
                user_id=advisor.id,
                role="financial_advisor",
                metadata={**base_md, "request_id": "NC-s4"},
            ),
        )
        print(f"STEP4 status={r4.status} confirmation={r4.requires_confirmation}")
        print(f"STEP4 summary={r4.summary[:60]}")
        assert r4.status == "error" and not r4.requires_confirmation
        assert "未找到客户" in r4.summary

        print("NO-CUSTOMER VERIFY OK")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
