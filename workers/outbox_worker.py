from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.settings import get_settings
from app.db.session import Database
from app.models.outbox import OutboxEvent
from app.tasks.streams import RedisStreams
from app.common.logging import configure_logging
from app.common.logging.config import get_logger

logger = get_logger(__name__)



def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OutboxPublisher:
    def __init__(self, database: Database, queue: RedisStreams, settings) -> None:
        self.database = database
        self.queue = queue
        self.settings = settings

    async def publish_once(self, limit: int = 20) -> int:
        async with self.database.session_factory() as session:
            result = await session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.status == "pending")
                .order_by(OutboxEvent.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            events = list(result.scalars().all())
            if not events:
                return 0
            for event in events:
                event.attempt += 1
            await session.commit()

        published = 0
        for event in events:
            try:
                payload = dict(event.payload_json or {})
                payload.update({
                    "event_id": str(event.id),
                    "event_type": event.event_type,
                    "aggregate_type": event.aggregate_type,
                    "aggregate_id": str(event.aggregate_id) if event.aggregate_id else "",
                })
                stream = self._stream_for(event.event_type)
                message_id = await self.queue.publish(stream, payload)
                await self._mark_published(str(event.id), message_id)
                published += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("outbox publish failed event_id=%s", event.id)
                await self._mark_failed(str(event.id), f"{type(exc).__name__}: {exc}")
        logger.info("outbox_publish_cycle selected=%s published=%s", len(events), published)
        return published

    async def _mark_published(self, event_id: str, message_id: str) -> None:
        async with self.database.session_factory() as session:
            result = await session.execute(select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update())
            event = result.scalar_one_or_none()
            if event is None:
                return
            event.status = "published"
            event.published_at = utcnow()
            event.error_message = None
            event.updated_at = utcnow()
            event.payload_json = {**(event.payload_json or {}), "redis_message_id": message_id}
            await session.commit()

    async def _mark_failed(self, event_id: str, error_message: str) -> None:
        async with self.database.session_factory() as session:
            result = await session.execute(select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update())
            event = result.scalar_one_or_none()
            if event is None:
                return
            if event.attempt >= self.settings.task_max_attempts:
                event.status = "dead"
            else:
                event.status = "pending"
            event.error_message = error_message[:4000]
            event.updated_at = utcnow()
            await session.commit()

    def _stream_for(self, event_type: str) -> str:
        if event_type.startswith("risk."):
            suffix = "risk"
        elif event_type.startswith("memory."):
            suffix = "memory"
        elif event_type.startswith("knowledge."):
            suffix = "ingestion"
        else:
            suffix = "tasks"
        return f"{self.settings.redis_stream_prefix}:{suffix}"


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    queue = RedisStreams(settings)
    publisher = OutboxPublisher(database, queue, settings)
    logger.info("outbox publisher started")
    try:
        while True:
            count = await publisher.publish_once()
            if count == 0:
                await asyncio.sleep(1)
    finally:
        await queue.close()
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
