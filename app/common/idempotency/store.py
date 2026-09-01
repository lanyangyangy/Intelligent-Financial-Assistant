from __future__ import annotations

import hashlib
import json
from typing import Any

import redis.asyncio as redis


class IdempotencyStore:
    def __init__(self, client: redis.Redis, ttl_seconds: int = 86400) -> None:
        self.client = client
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def key(scope: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
        return f"idempotency:{scope}:{digest}"

    async def get(self, scope: str, idempotency_key: str) -> dict[str, Any] | None:
        raw = await self.client.get(self.key(scope, idempotency_key))
        return json.loads(raw) if raw else None

    async def set(self, scope: str, idempotency_key: str, value: dict[str, Any]) -> None:
        await self.client.setex(self.key(scope, idempotency_key), self.ttl_seconds, json.dumps(value, ensure_ascii=False))
