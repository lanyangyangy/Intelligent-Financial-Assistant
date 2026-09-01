from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse[T](BaseModel):
    success: bool = True
    data: T
    error: dict | None = None
    trace_id: str | None = None
