from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.schemas.agents import AgentResult


@dataclass(frozen=True)
class AgentContext:
    request_id: str
    user_id: str | None = None
    role: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

class Agent(Protocol):
    name: str
    async def run(self, message: str, context: AgentContext) -> AgentResult: ...
