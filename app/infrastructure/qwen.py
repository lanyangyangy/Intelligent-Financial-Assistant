from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
from openai import AsyncOpenAI

from app.common.logging.config import get_logger
from app.core.settings import Settings

logger = get_logger(__name__)


class QwenProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = (
            AsyncOpenAI(
                api_key=settings.dashscope_api_key,
                base_url=settings.qwen_base_url,
                http_client=httpx.AsyncClient(
                    trust_env=False, timeout=httpx.Timeout(120.0, connect=30.0)
                ),
            )
            if settings.dashscope_api_key
            else None
        )

    @property
    def available(self) -> bool:
        return self._client is not None

    async def chat(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024
    ) -> str:
        """Single-turn LLM completion with exponential backoff retry.

        ``messages`` follows the OpenAI chat format, e.g.
        ``[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]``.
        """
        if self._client is None:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured")
        # Phase 5 F5.3：指数退避重试（间隔 1s / 2s / 4s，最多 3 次）
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = await self._client.chat.completions.create(
                    model=self.settings.qwen_chat_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "chat_retry attempt=%s error_type=%s", attempt, type(exc).__name__
                )
                if attempt < 3:
                    await asyncio.sleep(2 ** (attempt - 1))  # 1s / 2s
        raise last_error or RuntimeError("chat request failed")

    async def chat_stream(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024
    ) -> AsyncIterator[str]:
        """Streaming chat completion (SSE-compatible chunk iterator)."""
        if self._client is None:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured")
        stream = await self._client.chat.completions.create(
            model=self.settings.qwen_chat_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta

    async def check_config(self) -> dict[str, str]:
        if self._client is None:
            return {
                "status": "skipped",
                "reason": "DASHSCOPE_API_KEY is not configured",
            }
        return {
            "status": "configured",
            "model": self.settings.qwen_chat_model,
            "verified": "false",
        }

    async def check_embedding(self) -> dict[str, str]:
        details = {
            "dimension": str(self.settings.embedding_dimension),
            "model": self.settings.qwen_embedding_model,
        }
        if self._client is None:
            return {
                "status": "skipped",
                "reason": "DASHSCOPE_API_KEY is not configured",
                **details,
            }
        if not self.settings.embedding_smoke_check:
            return {
                "status": "configured",
                "verified": "false",
                "reason": "live embedding check is disabled",
                **details,
            }
        try:
            await self.embed(["health check"])
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "reason": type(exc).__name__,
                **details,
            }
        return {"status": "ok", "verified": "true", **details}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._client is None:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured")
        # DashScope may reset connections for the OpenAI-compatible endpoint
        # when a batch contains multiple inputs. Send one text per request and
        # preserve ordering; this is slower but deterministic for ingestion.
        vectors: list[list[float]] = []
        logger.info(
            "embedding_started model=%s text_count=%s",
            self.settings.qwen_embedding_model,
            len(texts),
        )
        for text in texts:
            response = None
            last_error: Exception | None = None
            for attempt in range(1, 4):
                try:
                    response = await self._client.embeddings.create(
                        model=self.settings.qwen_embedding_model,
                        input=text,
                        dimensions=self.settings.embedding_dimension,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.warning(
                        "embedding_retry attempt=%s error_type=%s",
                        attempt,
                        type(exc).__name__,
                    )
            if response is None:
                raise last_error or RuntimeError("embedding request failed")
            vectors.append(response.data[0].embedding)
        logger.info(
            "embedding_completed model=%s text_count=%s dimension=%s",
            self.settings.qwen_embedding_model,
            len(vectors),
            len(vectors[0]) if vectors else 0,
        )
        if any(len(vector) != self.settings.embedding_dimension for vector in vectors):
            raise ValueError(
                f"embedding dimension must be {self.settings.embedding_dimension}"
            )
        return vectors

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
