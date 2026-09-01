from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update

from app.core.settings import get_settings
from app.db.session import Database
from app.models.task import AsyncTask
from app.tasks.streams import QueueMessage, RedisStreams

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskExecutor:
    def __init__(self, database: Database, queue: RedisStreams, settings) -> None:
        self.database = database
        self.queue = queue
        self.settings = settings

    async def mark_started(self, task_id: str) -> AsyncTask | None:
        async with self.database.session_factory() as session:
            result = await session.execute(select(AsyncTask).where(AsyncTask.id == task_id).with_for_update())
            task = result.scalar_one_or_none()
            if task is None or task.status in {"success", "dead", "cancelled"}:
                return task
            task.status = "running"
            task.attempt += 1
            task.started_at = utcnow()
            task.error_message = None
            await session.commit()
            return task

    async def mark_success(self, task_id: str, result_json: dict) -> None:
        async with self.database.session_factory() as session:
            await session.execute(
                update(AsyncTask)
                .where(AsyncTask.id == task_id)
                .values(status="success", result_json=result_json, error_message=None, finished_at=utcnow(), updated_at=utcnow())
            )
            await session.commit()

    async def mark_failure(self, task: AsyncTask, error_message: str) -> bool:
        terminal = task.attempt >= task.max_attempts
        async with self.database.session_factory() as session:
            await session.execute(
                update(AsyncTask)
                .where(AsyncTask.id == str(task.id))
                .values(
                    status="dead" if terminal else "failed",
                    error_message=error_message[:4000],
                    finished_at=utcnow() if terminal else None,
                    updated_at=utcnow(),
                )
            )
            await session.commit()
        if terminal:
            await self.queue.publish(
                f"{self.settings.redis_stream_prefix}:dead-letter",
                {"task_id": str(task.id), "task_type": task.task_type, "error": error_message},
            )
        return terminal

    async def execute(self, message: QueueMessage) -> None:
        task_id = message.data.get("task_id", "")
        try:
            UUID(task_id)
        except ValueError:
            raise ValueError(f"invalid task_id: {task_id}")
        task = await self.mark_started(task_id)
        if task is None or task.status in {"success", "dead", "cancelled"}:
            return
        try:
            await self.mark_success(task_id, {"worker": "p0", "task_type": task.task_type, "message_id": message.message_id, "payload": task.payload_json})
        except Exception as exc:  # noqa: BLE001
            await self.mark_failure(task, f"{type(exc).__name__}: {exc}")
            raise


async def main() -> None:
    settings = get_settings()
    database = Database(settings)
    queue = RedisStreams(settings)
    stream = f"{settings.redis_stream_prefix}:tasks"
    group = settings.redis_consumer_group
    consumer = settings.redis_consumer_name
    await queue.ensure_group(stream, group)
    executor = TaskExecutor(database, queue, settings)
    logger.info("worker started stream=%s group=%s consumer=%s", stream, group, consumer)
    try:
        while True:
            messages = await queue.claim_pending(stream, group, consumer)
            messages.extend(await queue.consume_once(stream, group, consumer))
            for message in messages:
                try:
                    await executor.execute(message)
                except Exception:
                    logger.exception("task processing failed message_id=%s", message.message_id)
                    continue
                await queue.ack(message)
    finally:
        await queue.close()
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
