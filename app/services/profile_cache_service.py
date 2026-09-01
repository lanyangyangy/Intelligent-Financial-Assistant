from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.redis_client import RedisClient

# ---------------------------------------------------------------------------
# 中期记忆（Phase 4 F4.2）：画像缓存 Cache-Aside 模式
#   Key: profile:{customer_id}，TTL 7 天
#   读取：先查 Redis 缓存，未命中则查 MySQL 并回填缓存
#   写入：更新 MySQL 后主动失效/更新缓存
# ---------------------------------------------------------------------------

PROFILE_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 天
PROFILE_CACHE_VERSION = "v4"


class ProfileCacheService:
    """客户画像 Cache-Aside 缓存。"""

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    @staticmethod
    def _key(customer_id: str) -> str:
        # v4 刷新适当性硬性熔断结果，避免复用旧版本的高龄限制码缓存。
        return f"profile:{PROFILE_CACHE_VERSION}:{customer_id}"

    async def get(self, customer_id: str) -> dict | None:
        """Cache-Aside 读：命中缓存直接返回。"""
        try:
            raw = await self._redis.client.get(self._key(customer_id))
            if raw:
                return json.loads(raw)
        except Exception:  # noqa: BLE001 - 缓存失败降级直查库
            pass
        return None

    async def set(self, customer_id: str, data: dict) -> None:
        """回填缓存（写穿透）。"""
        try:
            await self._redis.client.set(
                self._key(customer_id),
                json.dumps(data, ensure_ascii=False, default=str),
                ex=PROFILE_CACHE_TTL_SECONDS,
            )
        except Exception:  # noqa: BLE001
            pass

    async def invalidate(self, customer_id: str) -> None:
        """失效缓存（数据变更后）。"""
        try:
            await self._redis.client.delete(self._key(customer_id))
        except Exception:  # noqa: BLE001
            pass

    async def get_or_load(
        self,
        session: AsyncSession,
        customer_id: str,
        loader,
    ) -> dict:
        """Cache-Aside 读：先缓存 → 未命中加载 → 回填。"""
        cached = await self.get(customer_id)
        if cached is not None:
            cached["_cache_hit"] = True
            return cached
        data = await loader(session, customer_id)
        if data:
            await self.set(customer_id, data)
        data["_cache_hit"] = False
        return data
