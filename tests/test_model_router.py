from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from functools import wraps

import pytest

from app.infrastructure.model_router import ModelRouter

pytestmark = pytest.mark.unit


def async_test(function):
    @wraps(function)
    def runner():
        return asyncio.run(function())

    return runner


class FakeProvider:
    def __init__(self, name: str, *, available: bool = True) -> None:
        self.name = name
        self._available = available
        self.calls: list[dict] = []

    @property
    def available(self) -> bool:
        return self._available

    async def chat(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024
    ) -> str:
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return f"{self.name}:ok"

    async def chat_stream(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024
    ) -> AsyncIterator[str]:
        yield await self.chat(messages, temperature=temperature, max_tokens=max_tokens)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append({"embed": texts})
        return [[0.0, 1.0] for _ in texts]

    async def close(self) -> None:
        self.calls.append({"close": True})


class FailingProvider(FakeProvider):
    async def chat(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024
    ) -> str:
        self.calls.append(
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        raise RuntimeError("upstream unavailable")


class FailingCloseProvider(FakeProvider):
    async def close(self) -> None:
        self.calls.append({"close": True})
        raise RuntimeError("close failed")


@async_test
async def test_customer_service_simple_question_prefers_deepseek():
    qwen = FakeProvider("qwen")
    deepseek = FakeProvider("deepseek")
    router = ModelRouter(qwen=qwen, deepseek=deepseek)

    result = await router.chat_with_routing(
        [{"role": "user", "content": "What is the minimum investment amount?"}],
        agent_name="customer_service",
        task_type="faq",
    )

    assert result == "deepseek:ok"
    assert len(deepseek.calls) == 1
    assert not qwen.calls


@async_test
async def test_advisor_and_sql_tasks_use_qwen():
    qwen = FakeProvider("qwen")
    deepseek = FakeProvider("deepseek")
    router = ModelRouter(qwen=qwen, deepseek=deepseek)

    recommendation = await router.chat_with_routing(
        [{"role": "user", "content": "Recommend a balanced portfolio."}],
        agent_name="investment_advisor",
        task_type="recommendation",
    )
    sql = await router.chat_with_routing(
        [{"role": "system", "content": "You are a PostgreSQL SQL expert."}],
        agent_name="data_analyst",
    )

    assert recommendation == "qwen:ok"
    assert sql == "qwen:ok"
    assert len(qwen.calls) == 2
    assert not deepseek.calls


@async_test
async def test_router_falls_back_to_qwen_when_deepseek_is_unavailable():
    qwen = FakeProvider("qwen")
    deepseek = FakeProvider("deepseek", available=False)
    router = ModelRouter(qwen=qwen, deepseek=deepseek)

    result = await router.chat_with_routing(
        [{"role": "user", "content": "Hello"}],
        agent_name="customer_service",
        task_type="chitchat",
    )

    assert result == "qwen:ok"
    assert len(qwen.calls) == 1
    assert not deepseek.calls


@async_test
async def test_router_falls_back_when_deepseek_request_fails():
    qwen = FakeProvider("qwen")
    deepseek = FailingProvider("deepseek")
    router = ModelRouter(qwen=qwen, deepseek=deepseek)

    result = await router.chat_with_routing(
        [{"role": "user", "content": "Hello"}],
        agent_name="customer_service",
        task_type="chitchat",
    )

    assert result == "qwen:ok"
    assert len(deepseek.calls) == 1
    assert len(qwen.calls) == 1


@async_test
async def test_router_falls_back_to_deepseek_when_qwen_request_fails():
    qwen = FailingProvider("qwen")
    deepseek = FakeProvider("deepseek")
    router = ModelRouter(qwen=qwen, deepseek=deepseek)

    result = await router.chat_with_routing(
        [{"role": "user", "content": "Recommend a balanced portfolio."}],
        agent_name="investment_advisor",
        task_type="recommendation",
    )

    assert result == "deepseek:ok"
    assert len(qwen.calls) == 1
    assert len(deepseek.calls) == 1


@async_test
async def test_large_generation_budget_prefers_qwen():
    qwen = FakeProvider("qwen")
    deepseek = FakeProvider("deepseek")
    router = ModelRouter(qwen=qwen, deepseek=deepseek, default_provider="deepseek")

    result = await router.chat_with_routing(
        [{"role": "user", "content": "Provide a detailed recommendation."}],
        max_tokens=1400,
    )

    assert result == "qwen:ok"
    assert len(qwen.calls) == 1
    assert not deepseek.calls


@async_test
async def test_customer_service_stream_prefers_deepseek():
    qwen = FakeProvider("qwen")
    deepseek = FakeProvider("deepseek")
    router = ModelRouter(qwen=qwen, deepseek=deepseek)

    chunks = [
        chunk
        async for chunk in router.chat_stream_with_routing(
            [{"role": "user", "content": "Hello"}],
            agent_name="customer_service",
            task_type="chitchat",
        )
    ]

    assert chunks == ["deepseek:ok"]
    assert len(deepseek.calls) == 1
    assert not qwen.calls


@async_test
async def test_stream_falls_back_to_deepseek_before_first_qwen_chunk():
    qwen = FailingProvider("qwen")
    deepseek = FakeProvider("deepseek")
    router = ModelRouter(qwen=qwen, deepseek=deepseek)

    chunks = [
        chunk
        async for chunk in router.chat_stream_with_routing(
            [{"role": "user", "content": "Recommend a balanced portfolio."}],
            agent_name="investment_advisor",
            task_type="recommendation",
        )
    ]

    assert chunks == ["deepseek:ok"]
    assert len(qwen.calls) == 1
    assert len(deepseek.calls) == 1


@async_test
async def test_embeddings_always_delegate_to_qwen():
    qwen = FakeProvider("qwen")
    deepseek = FakeProvider("deepseek")
    router = ModelRouter(qwen=qwen, deepseek=deepseek)

    vectors = await router.embed(["fixed-income product"])

    assert vectors == [[0.0, 1.0]]
    assert qwen.calls == [{"embed": ["fixed-income product"]}]
    assert not deepseek.calls


@async_test
async def test_close_attempts_all_providers_when_one_close_fails():
    qwen = FailingCloseProvider("qwen")
    deepseek = FakeProvider("deepseek")
    router = ModelRouter(qwen=qwen, deepseek=deepseek)

    await router.close()

    assert qwen.calls == [{"close": True}]
    assert deepseek.calls == [{"close": True}]
