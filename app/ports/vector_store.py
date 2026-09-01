from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class VectorDocument:
    id: str
    text: str
    vector: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class VectorHit:
    id: str
    score: float
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

class VectorStore(Protocol):
    async def upsert(self, documents: list[VectorDocument]) -> None: ...
    async def search(self, vector: list[float], top_k: int = 5, filters: dict[str, Any] | None = None) -> list[VectorHit]: ...
    async def delete(self, ids: list[str]) -> None: ...
