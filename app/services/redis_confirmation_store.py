"""结构化二次确认存储（移植自 Financial System-业务操作agent）。

key = confirmation:{session_id}:{user_id}:{confirmation_id}
TTL 300s，保存待确认操作的完整意图与参数；确认/取消时凭 confirmation_id
一次性消费（GET + DELETE 原子化），防止重复确认与串话。

与目标项目差异：本实现保存的是本 Agent 的 (intent, params) 而非
ParsedOperation 对象，确认时无需二次映射。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from redis.asyncio import Redis


class RedisConfirmationStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 300) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _key(session_id: str, user_id: str, confirmation_id: str) -> str:
        return f"confirmation:{session_id}:{user_id}:{confirmation_id}"

    async def save(
        self,
        session_id: str,
        user_id: str,
        confirmation_id: str,
        payload: dict[str, Any],
    ) -> None:
        """保存待确认操作。payload 含 intent/params/message。"""
        await self.redis.set(
            self._key(session_id, user_id, confirmation_id),
            json.dumps(
                {
                    "confirmation_id": confirmation_id,
                    "session_id": session_id,
                    "user_id": user_id,
                    "payload": payload,
                    "created_at": datetime.now().isoformat(),
                },
                ensure_ascii=False,
            ),
            ex=self.ttl_seconds,
        )

    async def consume(
        self,
        session_id: str,
        user_id: str,
        confirmation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """取回并删除待确认操作。

        confirmation_id 指定时精确匹配；否则扫描该会话+用户的全部待确认
        记录（Legacy 兼容：只取第一条非空）。
        """
        if confirmation_id is not None:
            key = self._key(session_id, user_id, confirmation_id)
            async with self.redis.pipeline(transaction=True) as pipe:
                await pipe.get(key)
                await pipe.delete(key)
                value, _ = await pipe.execute()
        else:
            pattern = f"confirmation:{session_id}:{user_id}:*"
            cursor = 0
            keys: list[bytes] = []
            while True:
                cursor, batch = await self.redis.scan(cursor, match=pattern, count=10)
                keys.extend(batch)
                if cursor == 0:
                    break
            if not keys:
                return None
            async with self.redis.pipeline(transaction=True) as pipe:
                for k in keys:
                    await pipe.get(k)
                for k in keys:
                    await pipe.delete(k)
                results = await pipe.execute()
                values = results[: len(keys)]
                value = next((v for v in values if v is not None), None)
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode()
        data = json.loads(value)
        return data.get("payload")
