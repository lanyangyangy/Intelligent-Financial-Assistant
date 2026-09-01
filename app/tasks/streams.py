from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis
from redis.exceptions import ResponseError

from app.common.logging.config import get_logger
from app.core.settings import Settings


@dataclass(frozen=True)
class QueueMessage:
    stream: str
    group: str
    message_id: str
    data: dict[str, str]


logger = get_logger(__name__)


class RedisStreams:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = redis.from_url(settings.redis_url, decode_responses=True)

    async def ensure_group(self, stream: str, group: str) -> None:
        try:
            await self.client.xgroup_create(stream, group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def publish(self, stream: str, data: dict[str, Any]) -> str:
        encoded = {
            key: value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            for key, value in data.items()
        }
        message_id = await self.client.xadd(stream, encoded, maxlen=10000, approximate=True)
        logger.info("redis_publish stream=%s message_id=%s keys=%s", stream, message_id, list(data.keys()))
        return message_id

    async def consume_once(self, stream: str, group: str, consumer: str) -> list[QueueMessage]:
        await self.ensure_group(stream, group)
        messages = await self.client.xreadgroup(
            group,
            consumer,
            {stream: ">"},
            count=10,
            block=self.settings.task_block_ms,
        )
        result = [
            QueueMessage(stream=stream, group=group, message_id=message_id, data=data)
            for _, entries in messages
            for message_id, data in entries
        ]
        if result:
            logger.info("redis_consume stream=%s group=%s consumer=%s count=%s", stream, group, consumer, len(result))
        return result

    async def claim_pending(self, stream: str, group: str, consumer: str, idle_ms: int = 30000) -> list[QueueMessage]:
        await self.ensure_group(stream, group)
        pending = await self.client.xpending_range(stream, group, min="-", max="+", count=10)
        ids = [item["message_id"] for item in pending if item.get("time_since_delivered", 0) >= idle_ms]
        if not ids:
            return []
        claimed = await self.client.xclaim(stream, group, consumer, min_idle_time=idle_ms, message_ids=ids)
        result = [
            QueueMessage(stream=stream, group=group, message_id=message_id, data=data)
            for message_id, data in claimed
        ]
        if result:
            logger.info("redis_claim stream=%s group=%s consumer=%s count=%s", stream, group, consumer, len(result))
        return result

    async def ack(self, message: QueueMessage) -> int:
        result = await self.client.xack(message.stream, message.group, message.message_id)
        logger.info("redis_ack stream=%s group=%s message_id=%s result=%s", message.stream, message.group, message.message_id, result)
        return result

    async def close(self) -> None:
        await self.client.aclose()
