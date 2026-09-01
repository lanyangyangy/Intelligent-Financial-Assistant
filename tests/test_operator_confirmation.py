"""结构化二次确认协议测试（RedisConfirmationStore 单测，需 Redis 6380）。

项目 pytest 未启用 pytest-asyncio，因此用 asyncio.run 同步包装。
"""
import asyncio

import pytest

from app.services.redis_confirmation_store import RedisConfirmationStore

pytestmark = pytest.mark.integration


@pytest.fixture
# Redis 不可用时自动跳过，保证无 Docker 环境默认测试可稳定通过
def redis_client(requires_redis):
    import redis.asyncio as redis

    from app.core.settings import get_settings

    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


def test_save_and_consume(redis_client) -> None:
    async def go() -> None:
        store = RedisConfirmationStore(redis_client)
        await store.save(
            "test-s1", "u1", "cid-1", {"intent": "purchase", "params": {"amount": "20000"}}
        )
        payload = await store.consume("test-s1", "u1", "cid-1")
        assert payload == {"intent": "purchase", "params": {"amount": "20000"}}
        # 幂等消费：同一凭据第二次为空
        assert await store.consume("test-s1", "u1", "cid-1") is None

    asyncio.run(go())


def test_consume_without_pending(redis_client) -> None:
    async def go() -> None:
        store = RedisConfirmationStore(redis_client)
        assert await store.consume("test-s2", "u1", "missing") is None

    asyncio.run(go())


def test_consume_expired(redis_client) -> None:
    async def go() -> None:
        store = RedisConfirmationStore(redis_client, ttl_seconds=1)
        await store.save("test-s3", "u1", "cid-3", {"intent": "redeem"})
        await asyncio.sleep(1.2)
        assert await store.consume("test-s3", "u1", "cid-3") is None

    asyncio.run(go())


def test_legacy_scan_consume(redis_client) -> None:
    """无 confirmation_id 时扫描该会话+用户的待确认记录（Legacy 兼容）。"""
    async def go() -> None:
        store = RedisConfirmationStore(redis_client)
        await store.save(
            "test-s4", "u1", "cid-a", {"intent": "transfer", "params": {"amount": "60000"}}
        )
        payload = await store.consume("test-s4", "u1")
        assert payload == {"intent": "transfer", "params": {"amount": "60000"}}

    asyncio.run(go())
