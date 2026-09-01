from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
from openai import AsyncOpenAI

from app.common.logging.config import get_logger
from app.core.settings import Settings

logger = get_logger(__name__)


class DeepSeekProvider:
    """DeepSeek chat provider using its OpenAI-compatible API."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = (
            AsyncOpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                http_client=httpx.AsyncClient(
                    trust_env=False, timeout=httpx.Timeout(120.0, connect=30.0)
                ),
            )
            if settings.deepseek_api_key and settings.deepseek_chat_model
            else None
        )

    @property
    def available(self) -> bool:
        return self._client is not None

    async def chat(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024
    ) -> str:
        if self._client is None:
            raise RuntimeError(self._configuration_error())
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = await self._client.chat.completions.create(
                    model=self.settings.deepseek_chat_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body=self._thinking_body(),
                )
                return response.choices[0].message.content or ""
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "deepseek_chat_retry attempt=%s error_type=%s",
                    attempt,
                    type(exc).__name__,
                )
                if attempt < 3:
                    await asyncio.sleep(2 ** (attempt - 1))
        raise last_error or RuntimeError("DeepSeek chat request failed")

    async def chat_stream(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024
    ) -> AsyncIterator[str]:
        if self._client is None:
            raise RuntimeError(self._configuration_error())
        stream = await self._client.chat.completions.create(
            model=self.settings.deepseek_chat_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            extra_body=self._thinking_body(),
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta

    async def check_config(self) -> dict[str, str]:
        if self._client is None:
            return {"status": "skipped", "reason": self._configuration_error()}
        return {
            "status": "configured",
            "model": self.settings.deepseek_chat_model,
            "verified": "false",
        }

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    def _configuration_error(self) -> str:
        if not self.settings.deepseek_api_key:
            return "DEEPSEEK_API_KEY is not configured"
        return "DEEPSEEK_CHAT_MODEL is not configured"

    def _thinking_body(self) -> dict[str, dict[str, str]]:
        mode = "enabled" if self.settings.deepseek_thinking_enabled else "disabled"
        return {"thinking": {"type": mode}}
