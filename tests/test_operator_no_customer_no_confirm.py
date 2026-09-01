"""转账/赎回客户不存在时不触发二次确认（防止确认后执行才报错）。

项目 pytest 未启用 pytest-asyncio，用 asyncio.run 同步包装。
"""
import asyncio

import pytest
from sqlalchemy import select

from app.agents.operations_agent import BusinessOperatorAgent
from app.core.settings import get_settings
from app.db.schema import ensure_schema
from app.db.session import Database
from app.models.auth import User
from app.ports.agent import AgentContext

pytestmark = pytest.mark.integration


def _ctx(rid, user_id, role, customer_id, session="t"):
    return AgentContext(
        request_id=rid,
        user_id=user_id,
        role=role,
        metadata={"customer_id": customer_id, "is_super_admin": False, "session_id": session},
    )


def test_transfer_missing_target_does_not_confirm(requires_postgres):
    """转入方不存在：直接 error，不进入二次确认。"""
    async def go() -> None:
        settings = get_settings()
        db = Database(settings)
        await ensure_schema(db.engine)
        agent = BusinessOperatorAgent(db, settings, None)
        try:
            async with db.session_factory() as s:
                manager = (await s.execute(select(User).where(User.username.ilike("%manager%"), User.status == "active"))).scalars().first()
                customer = (await s.execute(select(User).where(User.username == "retail_investor_demo"))).scalar_one()
            assert manager and customer
            r = await agent.run(
                "把李伟的60000元转到张明账户",
                _ctx("t1", manager.id, "customer_manager", customer.id),
            )
            assert r.status == "error"
            assert not r.requires_confirmation
            assert "未找到客户「张明」" in r.summary
        finally:
            await db.dispose()

    asyncio.run(go())


def test_transfer_missing_source_does_not_confirm(requires_postgres):
    """转出方不存在：直接 error，不进入二次确认。"""
    async def go() -> None:
        settings = get_settings()
        db = Database(settings)
        await ensure_schema(db.engine)
        agent = BusinessOperatorAgent(db, settings, None)
        try:
            async with db.session_factory() as s:
                manager = (await s.execute(select(User).where(User.username.ilike("%manager%"), User.status == "active"))).scalars().first()
                customer = (await s.execute(select(User).where(User.username == "retail_investor_demo"))).scalar_one()
            assert manager and customer
            r = await agent.run(
                "把不存在的客户甲的60000元转到李伟账户",
                _ctx("t2", manager.id, "customer_manager", customer.id),
            )
            assert r.status == "error"
            assert not r.requires_confirmation
            assert "未找到客户「不存在的客户甲」" in r.summary
        finally:
            await db.dispose()

    asyncio.run(go())


def test_redeem_missing_customer_does_not_confirm(requires_postgres):
    """赎回客户不存在（全部赎回）：直接 error，不进入二次确认。"""
    async def go() -> None:
        settings = get_settings()
        db = Database(settings)
        await ensure_schema(db.engine)
        agent = BusinessOperatorAgent(db, settings, None)
        try:
            async with db.session_factory() as s:
                advisor = (await s.execute(select(User).where(User.username.ilike("%advisor%"), User.status == "active"))).scalars().first()
                customer = (await s.execute(select(User).where(User.username == "retail_investor_demo"))).scalar_one()
            assert advisor and customer
            r = await agent.run(
                "赎回不存在的客户甲持有的国债逆回购优选全部份额",
                _ctx("t3", advisor.id, "financial_advisor", customer.id),
            )
            assert r.status == "error"
            assert not r.requires_confirmation
            assert "未找到客户" in r.summary
        finally:
            await db.dispose()

    asyncio.run(go())
