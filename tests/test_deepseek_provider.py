from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.settings import Settings
from app.infrastructure.deepseek import DeepSeekProvider

pytestmark = pytest.mark.unit


class RecordingCompletions:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class OneChunkStream:
    def __aiter__(self):
        return self

    async def __anext__(self):
        if hasattr(self, "sent"):
            raise StopAsyncIteration
        self.sent = True
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="OK"))]
        )


def test_deepseek_provider_is_optional_when_api_key_is_missing():
    provider = DeepSeekProvider(Settings(deepseek_api_key=""))

    assert provider.available is False
    assert asyncio.run(provider.check_config()) == {
        "status": "skipped",
        "reason": "DEEPSEEK_API_KEY is not configured",
    }


def test_deepseek_config_reports_model_without_making_a_live_request():
    provider = object.__new__(DeepSeekProvider)
    provider._client = object()
    provider.settings = SimpleNamespace(deepseek_chat_model="deepseek-v4-flash")

    assert asyncio.run(provider.check_config()) == {
        "status": "configured",
        "model": "deepseek-v4-flash",
        "verified": "false",
    }


def test_model_router_default_is_configurable():
    settings = Settings(model_router_default="deepseek")

    assert settings.model_router_default == "deepseek"


def test_invalid_model_router_default_fails_settings_validation():
    with pytest.raises(ValueError, match="MODEL_ROUTER_DEFAULT"):
        Settings(model_router_default="invalid").validate_p0()


def test_deepseek_defaults_to_flash_with_thinking_disabled():
    settings = Settings(_env_file=None)

    assert settings.deepseek_chat_model == "deepseek-v4-flash"
    assert settings.deepseek_thinking_enabled is False


class FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))]
        )


def test_deepseek_chat_disables_thinking_when_configured():
    completions = FakeCompletions()
    provider = object.__new__(DeepSeekProvider)
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    provider.settings = SimpleNamespace(
        deepseek_chat_model="deepseek-v4-flash",
        deepseek_thinking_enabled=False,
    )

    response = asyncio.run(
        provider.chat([{"role": "user", "content": "hello"}])
    )

    assert response == "OK"
    assert completions.calls == [
        {
            "model": "deepseek-v4-flash",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.3,
            "max_tokens": 1024,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
    ]


def test_deepseek_chat_disables_thinking_by_default():
    completions = RecordingCompletions(
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])
    )
    provider = object.__new__(DeepSeekProvider)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider.settings = SimpleNamespace(
        deepseek_chat_model="deepseek-v4-flash", deepseek_thinking_enabled=False
    )

    assert asyncio.run(provider.chat([{"role": "user", "content": "hello"}])) == "OK"
    assert completions.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_deepseek_stream_disables_thinking_by_default():
    completions = RecordingCompletions(OneChunkStream())
    provider = object.__new__(DeepSeekProvider)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider.settings = SimpleNamespace(
        deepseek_chat_model="deepseek-v4-flash", deepseek_thinking_enabled=False
    )

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in provider.chat_stream(
                [{"role": "user", "content": "hello"}]
            )
        ]

    assert asyncio.run(collect()) == ["OK"]
    assert completions.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_deepseek_chat_enables_thinking_when_configured():
    completions = RecordingCompletions(
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])
    )
    provider = object.__new__(DeepSeekProvider)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider.settings = SimpleNamespace(
        deepseek_chat_model="deepseek-v4-flash", deepseek_thinking_enabled=True
    )

    assert asyncio.run(provider.chat([{"role": "user", "content": "hello"}])) == "OK"
    assert completions.calls[0]["extra_body"] == {"thinking": {"type": "enabled"}}


def test_deepseek_stream_enables_thinking_when_configured():
    completions = RecordingCompletions(OneChunkStream())
    provider = object.__new__(DeepSeekProvider)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider.settings = SimpleNamespace(
        deepseek_chat_model="deepseek-v4-flash", deepseek_thinking_enabled=True
    )

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in provider.chat_stream(
                [{"role": "user", "content": "hello"}]
            )
        ]

    assert asyncio.run(collect()) == ["OK"]
    assert completions.calls[0]["extra_body"] == {"thinking": {"type": "enabled"}}
