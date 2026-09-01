from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import Database
from app.models.auth import Role, User
from app.models.trading import Account


async def ensure_trading_seed(database: Database) -> None:
    async with database.session_factory() as session:
        user = (
            await session.execute(
                select(User)
                .options(selectinload(User.roles))
                .where(User.username == "retail_investor_demo")
            )
        ).scalar_one_or_none()
        if user is None:
            return
        customer_role = (
            await session.execute(select(Role).where(Role.code == "retail_investor"))
        ).scalar_one_or_none()
        if customer_role is None or customer_role not in user.roles:
            return
        account = (
            await session.execute(select(Account).where(Account.user_id == user.id))
        ).scalar_one_or_none()
        if account is None:
            account = Account(
                id=str(uuid4()),
                user_id=user.id,
                account_no=f"AC{user.id:08d}",
                currency="CNY",
                available_balance=Decimal("200000.00"),
                frozen_balance=Decimal("0.00"),
                status="active",
            )
            session.add(account)
        else:
            account.available_balance = Decimal("200000.00")
            account.frozen_balance = Decimal("0.00")
            account.status = "active"
        # 不在应用启动时清空订单和成交记录：数据分析示例需要可查询的
        # 历史成交数据，客户真实订单也不应因服务重启而丢失。
        await session.commit()
