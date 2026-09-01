from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from app.common.logging.config import get_logger

logger = get_logger(__name__)


class ChatProvider(Protocol):
    @property
    def available(self) -> bool: ...

    async def chat(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024
    ) -> str: ...

    async def chat_stream(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024
    ) -> AsyncIterator[str]: ...


class ModelRouter:
    """Select a chat model deterministically and fall back on upstream errors."""

    _COMPLEX_AGENTS = frozenset(
        {
            "investment_advisor",
            "data_analyst",
            "risk_monitor",
            "business_operator",
        }
    )
    _COMPLEX_TASK_TYPES = frozenset(
        {
            "compliance_review",
            "nl2sql",
            "operation_parse",
            "portfolio_analysis",
            "recommendation",
            "risk_review",
        }
    )
    _SIMPLE_TASK_TYPES = frozenset({"chitchat", "faq"})
    _COMPLEX_KEYWORDS = (
        "postgresql",
        "sql",
        "合规",
        "适当性",
        "推荐",
        "组合",
        "对比",
        "风险",
    )

    def __init__(
        self,
        qwen: ChatProvider,
        deepseek: ChatProvider | None = None,
        *,
        default_provider: str = "qwen",
    ) -> None:
        if default_provider not in {"qwen", "deepseek"}:
            raise ValueError("default_provider must be 'qwen' or 'deepseek'")
        self.qwen = qwen
        self.deepseek = deepseek
        self.default_provider = default_provider

    @property
    def available(self) -> bool:
        return any(provider.available for provider in self._providers())

    def selected_provider_name(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        *,
        agent_name: str | None = None,
        task_type: str | None = None,
    ) -> str:
        """Expose the deterministic decision for logs, tests, and observability."""
        preferred = self._preferred_provider(
            messages,
            max_tokens=max_tokens,
            agent_name=agent_name,
            task_type=task_type,
        )
        for name in self._provider_order(preferred):
            provider = self._provider(name)
            if provider is not None and provider.available:
                return name
        return preferred

    async def chat_with_routing(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        *,
        agent_name: str | None = None,
        task_type: str | None = None,
    ) -> str:
        preferred = self._preferred_provider(
            messages,
            max_tokens=max_tokens,
            agent_name=agent_name,
            task_type=task_type,
        )
        last_error: Exception | None = None
        for name in self._provider_order(preferred):
            provider = self._provider(name)
            if provider is None or not provider.available:
                continue
            try:
                return await provider.chat(
                    messages, temperature=temperature, max_tokens=max_tokens
                )
            except Exception as exc:  # noqa: BLE001 - try the configured fallback
                last_error = exc
                logger.warning(
                    "model_router_fallback agent=%s task_type=%s failed_provider=%s "
                    "error_type=%s",
                    agent_name,
                    task_type,
                    name,
                    type(exc).__name__,
                )
        if last_error is not None:
            raise last_error
        raise RuntimeError("No configured chat provider is available")

    async def chat(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024
    ) -> str:
        return await self.chat_with_routing(
            messages, temperature=temperature, max_tokens=max_tokens
        )

    async def chat_stream(
        self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 1024
    ) -> AsyncIterator[str]:
        async for chunk in self.chat_stream_with_routing(
            messages, temperature=temperature, max_tokens=max_tokens
        ):
            yield chunk

    async def chat_stream_with_routing(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 1024,
        *,
        agent_name: str | None = None,
        task_type: str | None = None,
    ) -> AsyncIterator[str]:
        preferred = self._preferred_provider(
            messages,
            max_tokens=max_tokens,
            agent_name=agent_name,
            task_type=task_type,
        )
        last_error: Exception | None = None
        for name in self._provider_order(preferred):
            provider = self._provider(name)
            if provider is None or not provider.available:
                continue
            yielded = False
            try:
                async for chunk in provider.chat_stream(
                    messages, temperature=temperature, max_tokens=max_tokens
                ):
                    yielded = True
                    yield chunk
                return
            except Exception as exc:  # noqa: BLE001 - do not splice two live streams
                if yielded:
                    raise
                last_error = exc
                logger.warning(
                    "model_router_stream_fallback failed_provider=%s error_type=%s",
                    name,
                    type(exc).__name__,
                )
        if last_error is not None:
            raise last_error
        raise RuntimeError("No configured chat provider is available")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Keep all knowledge-base vectors in the Qwen embedding space."""
        embed = getattr(self.qwen, "embed", None)
        if embed is None:
            raise RuntimeError("Qwen provider does not implement embeddings")
        return await embed(texts)

    async def close(self) -> None:
        closed: set[int] = set()
        for provider in self._providers():
            if id(provider) in closed:
                continue
            closed.add(id(provider))
            close = getattr(provider, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception as exc:  # noqa: BLE001 - shutdown must close other providers
                    logger.warning(
                        "model_router_provider_close_failed error_type=%s",
                        type(exc).__name__,
                    )

    def _preferred_provider(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1024,
        agent_name: str | None = None,
        task_type: str | None = None,
    ) -> str:
        normalized_agent = (agent_name or "").lower()
        normalized_task = (task_type or "").lower()
        prompt = "\n".join(str(message.get("content", "")) for message in messages).lower()

        if (
            normalized_agent in self._COMPLEX_AGENTS
            or normalized_task in self._COMPLEX_TASK_TYPES
            or max_tokens >= 1200
            or len(prompt) >= 800
            or any(keyword in prompt for keyword in self._COMPLEX_KEYWORDS)
        ):
            return "qwen"
        if normalized_agent == "customer_service" or normalized_task in self._SIMPLE_TASK_TYPES:
            return "deepseek"
        return self.default_provider

    def _provider_order(self, preferred: str) -> tuple[str, str]:
        fallback = "deepseek" if preferred == "qwen" else "qwen"
        return preferred, fallback

    def _provider(self, name: str) -> ChatProvider | None:
        if name == "qwen":
            return self.qwen
        if name == "deepseek":
            return self.deepseek
        return None

    def _providers(self) -> tuple[ChatProvider, ...]:
        return (self.qwen,) if self.deepseek is None else (self.qwen, self.deepseek)
