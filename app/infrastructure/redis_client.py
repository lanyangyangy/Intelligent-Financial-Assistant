import redis.asyncio as redis

from app.core.settings import Settings


class RedisClient:
    def __init__(self, settings: Settings) -> None:
        self.client = redis.from_url(settings.redis_url, decode_responses=True)

    async def check(self) -> dict[str, str]:
        try:
            await self.client.ping()
            return {"status": "ok"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": type(exc).__name__}

    async def close(self) -> None:
        await self.client.aclose()
