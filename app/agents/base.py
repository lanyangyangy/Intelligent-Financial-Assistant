from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from app.core.settings import Settings
from app.db.session import Database
from app.infrastructure.model_router import ChatProvider
from app.infrastructure.qwen import QwenProvider
from app.ports.agent import AgentContext
from app.schemas.agents import AgentResult


class AgentBase(ABC):
    """Base class shared by the five domain agents.

    统一执行骨架（功能设计文档 1.1）：记忆召回 → 意图路由 → 核心逻辑 →
    置信度校验 → 结果输出 → 数据沉淀 → 事件广播。

    Provides:
    - a unified LLM chat entry with graceful degradation when the DashScope
      API key is missing or the upstream call fails (agents then fall back to
      deterministic rules/templates so the P0 demo keeps working);
    - typed keyword extraction via JSON with a safe parser;
    - convenient session access through the injected Database;
    - hook methods for memory recall / confidence check / streaming /
      data sink / event broadcast with safe no-op defaults.
    """

    name: str = "base"
    description: str = ""

    def __init__(
        self, database: Database, settings: Settings, llm: ChatProvider | None = None
    ) -> None:
        self.database = database
        self.settings = settings
        self.llm = llm or QwenProvider(settings)

    @abstractmethod
    async def run(self, message: str, context: AgentContext) -> AgentResult:
        """Handle one user/event message and produce an AgentResult."""

    # -- 统一执行骨架钩子（默认安全 no-op，子类按需覆写）----------------
    async def recall_memory(self, context: AgentContext) -> dict:
        """短期/中期/长期记忆召回钩子。默认返回空；子类注入画像/图谱/会话。"""
        return {}

    def check_confidence(
        self, result: AgentResult, context: AgentContext
    ) -> AgentResult:
        """置信度校验钩子：低于意图阈值时置为低置信并提示澄清。"""
        return result

    async def stream_output(
        self, result: AgentResult, context: AgentContext
    ) -> AgentResult:
        """流式输出钩子（SSE 由 API 层实现，Agent 侧保留扩展点）。"""
        return result

    async def persist_turn(self, result: AgentResult, context: AgentContext) -> None:
        """数据沉淀钩子：归档会话/工具调用。默认空实现，API 层已统一归档。"""
        return None

    async def broadcast_event(
        self, event_type: str, payload: dict[str, Any], context: AgentContext
    ) -> None:
        """事件广播钩子（Redis Pub/Sub）。默认安全 no-op，子类按需覆写。"""
        return None

    # -- LLM helpers ------------------------------------------------------
    async def llm_chat(
        self, system: str, user: str, temperature: float = 0.3, max_tokens: int = 1024
    ) -> str | None:
        """Call the LLM; return None when the provider is unavailable or fails."""
        if not self.llm.available:
            return None
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            route_chat = getattr(self.llm, "chat_with_routing", None)
            if route_chat is not None:
                return await route_chat(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    agent_name=self.name,
                )
            return await self.llm.chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception:  # noqa: BLE001 - agents must not crash on upstream failures
            return None

    async def llm_json(
        self, system: str, user: str, temperature: float = 0.1, max_tokens: int = 512
    ) -> dict | None:
        """Ask the LLM for a JSON object and parse it safely (strips fences)."""
        raw = await self.llm_chat(
            system, user, temperature=temperature, max_tokens=max_tokens
        )
        if not raw:
            return None
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # tolerate a leading/trailing fragment around the JSON object
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    return None
            return None

    def ok(
        self,
        summary: str,
        *,
        data: dict | None = None,
        evidence: list[dict] | None = None,
        requires_confirmation: bool = False,
        next_action: str | None = None,
        confidence: float = 0.8,
    ) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            status="success",
            summary=summary,
            data=data or {},
            evidence=evidence or [],
            confidence=confidence,
            requires_confirmation=requires_confirmation,
            next_action=next_action,
        )

    def fail(
        self, summary: str, errors: list[str], *, data: dict | None = None
    ) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            status="error",
            summary=summary,
            data=data or {},
            errors=errors,
        )
