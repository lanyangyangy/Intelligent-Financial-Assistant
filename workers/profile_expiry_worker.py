from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.common.logging import configure_logging
from app.common.logging.config import get_logger
from app.core.settings import get_settings
from app.db.session import Database
from app.models.profile import CustomerProfile, CustomerRiskAssessment

logger = get_logger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssessmentExpiryScheduler:
    """Periodically mark profiles whose risk assessment has expired.

    Ported from the external profile service worker. A profile becomes
    EXPIRED when its latest active assessment has an expires_at in the past
    (or is missing entirely).
    """

    def __init__(self, database: Database, interval_seconds: int = 300) -> None:
        self.database = database
        self.interval_seconds = interval_seconds
        self._stop = asyncio.Event()

    async def scan_once(self) -> dict:
        now = utcnow()
        expired = 0
        async with self.database.session_factory() as session:
            assessments = list(
                (
                    await session.execute(
                        select(CustomerRiskAssessment).where(
                            CustomerRiskAssessment.status.in_(["active", "provisional"])
                        )
                    )
                )
                .scalars()
                .all()
            )
            expired_ids = {
                a.user_id
                for a in assessments
                if a.expires_at is None or a.expires_at < now
            }
            profiles = list(
                (
                    await session.execute(
                        select(CustomerProfile).where(
                            CustomerProfile.user_id.in_(expired_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for profile in profiles:
                if profile.profile_status != "EXPIRED":
                    profile.profile_status = "EXPIRED"
                    expired += 1
            await session.commit()
        return {"expired": expired}

    async def run_forever(self) -> None:
        logger.info(
            "assessment expiry scheduler started interval=%ss", self.interval_seconds
        )
        while not self._stop.is_set():
            try:
                result = await self.scan_once()
                if result["expired"]:
                    logger.info(
                        "assessment expiry scan marked %s profile(s) EXPIRED",
                        result["expired"],
                    )
            except Exception:  # noqa: BLE001
                logger.exception("assessment expiry scan failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    scheduler = AssessmentExpiryScheduler(database)
    try:
        await scheduler.run_forever()
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
