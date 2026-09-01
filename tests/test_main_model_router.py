from __future__ import annotations

import asyncio

import pytest

from app.core.settings import Settings
from app.main import close_application_resources, create_model_router

pytestmark = pytest.mark.unit


class FakeProvider:
    @property
    def available(self) -> bool:
        return True


def test_create_model_router_wires_deepseek_as_the_simple_chat_provider(monkeypatch):
    qwen = FakeProvider()
    deepseek = FakeProvider()
    monkeypatch.setattr("app.main.QwenProvider", lambda settings: qwen)
    monkeypatch.setattr("app.main.DeepSeekProvider", lambda settings: deepseek)

    created_qwen, created_deepseek, router = create_model_router(
        Settings(model_router_default="deepseek")
    )

    assert created_qwen is qwen
    assert created_deepseek is deepseek
    assert router.qwen is qwen
    assert router.deepseek is deepseek
    assert router.default_provider == "deepseek"


class ClosingResource:
    def __init__(self, calls: list[str], name: str, *, fail: bool = False) -> None:
        self.calls = calls
        self.name = name
        self.fail = fail

    async def close(self) -> None:
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError(f"{self.name} close failed")


class EventSubscriber(ClosingResource):
    async def stop(self) -> None:
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError(f"{self.name} stop failed")


class Database(ClosingResource):
    async def dispose(self) -> None:
        self.calls.append(self.name)
        if self.fail:
            raise RuntimeError(f"{self.name} dispose failed")


def test_shutdown_attempts_every_resource_after_an_earlier_close_failure():
    calls: list[str] = []

    asyncio.run(
        close_application_resources(
            EventSubscriber(calls, "events", fail=True),
            ClosingResource(calls, "graph"),
            ClosingResource(calls, "redis"),
            ClosingResource(calls, "router"),
            Database(calls, "database"),
        )
    )

    assert calls == ["events", "graph", "redis", "router", "database"]
