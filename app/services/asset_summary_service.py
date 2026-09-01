from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import CustomerAssetSnapshot
from app.repositories.trading import (
    SqlAlchemyAccountRepository,
    SqlAlchemyAssetRepository,
    SqlAlchemyHoldingRepository,
)


class AssetSummaryService:
    """Build one consistent, mock-safe wealth view from account + active holdings."""

    def __init__(
        self, account_repository=None, holding_repository=None, asset_repository=None
    ):
        self.accounts = account_repository or SqlAlchemyAccountRepository()
        self.holdings = holding_repository or SqlAlchemyHoldingRepository()
        self.assets = asset_repository or SqlAlchemyAssetRepository()

    async def calculate(self, session: AsyncSession, user_id: str) -> dict:
        account = await self.accounts.get_by_user(session, user_id)
        holdings_value = await self.holdings.sum_market_value(session, user_id)
        holdings_value = Decimal(str(holdings_value or 0))
        available = Decimal(str(account.available_balance if account else 0))
        frozen = Decimal(str(account.frozen_balance if account else 0))
        cash = available + frozen
        liability = Decimal("0")
        previous = await self.assets.latest(session, user_id)
        if previous is not None:
            liability = Decimal(str(previous.liability or 0))
        total = cash + holdings_value
        return {
            "total_asset": total,
            "cash_balance": cash,
            "investable_asset": total,
            "liability": liability,
            "net_asset": total - liability,
        }

    async def snapshot(
        self, session: AsyncSession, user_id: str, source_type: str = "derived"
    ) -> CustomerAssetSnapshot:
        values = await self.calculate(session, user_id)
        snapshot = CustomerAssetSnapshot(
            id=str(uuid4()), user_id=user_id, source_type=source_type, **values
        )
        return await self.assets.save(session, snapshot)

    async def latest_or_derived(
        self, session: AsyncSession, user_id: str
    ) -> CustomerAssetSnapshot:
        values = await self.calculate(session, user_id)
        latest = await self.assets.latest(session, user_id)
        if latest is None:
            latest = CustomerAssetSnapshot(
                id=str(uuid4()),
                user_id=user_id,
                source_type="derived",
                snapshot_time=datetime.now(UTC),
                created_at=datetime.now(UTC),
                **values,
            )
            session.add(latest)
        else:
            for key, value in values.items():
                setattr(latest, key, value)
            latest.source_type = "derived"
            # Legacy rows may have been created before server defaults were
            # applied. Ensure response serialization remains valid.
            if latest.snapshot_time is None:
                latest.snapshot_time = datetime.now(UTC)
            if latest.created_at is None:
                latest.created_at = datetime.now(UTC)
        # 关键：必须 commit，否则快照更新只存在于事务内存中，session 关闭
        # 时回滚，customer_asset_snapshot 永远停留旧值——转账/申购后余额
        # 变了但资产快照（前端"现金余额/资产摘要"数据源）不更新。
        await session.commit()
        return latest
