from __future__ import annotations

import time
from dataclasses import dataclass

import redis.asyncio as redis

from app.common.exceptions import RateLimitError


@dataclass(frozen=True)
class RateLimitPolicy:
    limit: int
    window_seconds: int


class RedisRateLimiter:
    def __init__(self, client: redis.Redis) -> None:
        self.client = client

    async def check(self, key: str, policy: RateLimitPolicy) -> tuple[bool, int]:
        bucket = int(time.time() // policy.window_seconds)
        redis_key = f"rate:{key}:{bucket}"
        count = await self.client.incr(redis_key)
        if count == 1:
            await self.client.expire(redis_key, policy.window_seconds)
        remaining = max(0, policy.limit - int(count))
        return count <= policy.limit, remaining

    async def enforce(self, key: str, policy: RateLimitPolicy) -> None:
        allowed, _ = await self.check(key, policy)
        if not allowed:
            raise RateLimitError()
