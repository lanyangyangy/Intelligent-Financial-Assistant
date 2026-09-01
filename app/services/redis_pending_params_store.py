"""待补齐参数存储（业务操作追问续补）。

业务操作 Agent 参数提取不完整时（next_action="ask_params"），把
(intent, params, message) 保存到 Redis（key = pending_params:{session}:{user}，
TTL 300s）。用户第二次补充缺失参数后，Agent 取出该上下文，将新消息
中补充的参数合并到原参数并继续执行，无需重新完整陈述指令。

与 RedisConfirmationStore 的区别：confirmation 是"已完整、待二次确认"，
pending_params 是"参数不完整、待补充"。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from redis.asyncio import Redis


class RedisPendingParamsStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 300) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _key(session_id: str, user_id: str) -> str:
        return f"pending_params:{session_id}:{user_id}"

    async def save(
        self,
        session_id: str,
        user_id: str,
        intent: str,
        params: dict[str, Any],
        message: str,
    ) -> None:
        """保存待补齐参数上下文（覆盖旧值，续 TTL）。"""
        await self.redis.set(
            self._key(session_id, user_id),
            json.dumps(
                {
                    "intent": intent,
                    "params": params,
                    "message": message,
                    "created_at": datetime.now().isoformat(),
                },
                ensure_ascii=False,
                default=str,
            ),
            ex=self.ttl_seconds,
        )

    async def consume(self, session_id: str, user_id: str) -> dict[str, Any] | None:
        """取回并删除待补齐上下文（原子消费，防重复续补）。"""
        key = self._key(session_id, user_id)
        async with self.redis.pipeline(transaction=True) as pipe:
            await pipe.get(key)
            await pipe.delete(key)
            value, _ = await pipe.execute()
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode()
        return json.loads(value)

    async def peek(self, session_id: str, user_id: str) -> dict[str, Any] | None:
        """仅查看不消费（供诊断/调试）。"""
        value = await self.redis.get(self._key(session_id, user_id))
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode()
        return json.loads(value)
