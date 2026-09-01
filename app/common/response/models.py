from typing import TypeVar

from pydantic import BaseModel, Field, model_validator

T = TypeVar("T")

# 错误码 → HTTP 语义码（需求文档 6.1 错误码定义）
_ERROR_CODE_MAP = {
    "VALIDATION_ERROR": 400,
    "AUTH_ERROR": 401,
    "PERMISSION_DENIED": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "INTERNAL_ERROR": 500,
    "LLM_ERROR": 1001,
    "KNOWLEDGE_NO_RESULT": 1002,
    "SQL_GENERATE_FAILED": 1003,
    "RISK_RULE_TRIGGERED": 1004,
    "SUITABILITY_MISMATCH": 1005,
}


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class ApiResponse[T](BaseModel):
    """统一响应格式（需求文档 6.1）。

    同时携带两套字段：
      - 兼容现有前端：success / data / error / trace_id
      - 需求文档标准：code / message / data / trace_id
    """

    success: bool = True
    data: T | None = None
    error: ErrorDetail | None = None
    trace_id: str | None = None
    code: int = 200
    message: str = "success"

    @model_validator(mode="after")
    def _sync_legacy_fields(self) -> "ApiResponse":
        if self.error is not None:
            self.code = _ERROR_CODE_MAP.get(
                self.error.code, _ERROR_CODE_MAP.get("INTERNAL_ERROR", 500)
            )
            self.message = self.error.message
        else:
            self.code = 200
            self.message = "success"
        return self
