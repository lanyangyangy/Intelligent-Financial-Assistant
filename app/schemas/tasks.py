from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    task_type: str = Field(min_length=1, max_length=100)
    aggregate_type: str = Field(default="system", max_length=64)
    aggregate_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)


class TaskResponse(BaseModel):
    id: str
    task_type: str
    status: str
    attempt: int
    max_attempts: int
    created_at: datetime
