"""业务操作 Agent 数据结构（移植自 Financial System-业务操作agent）。

包含确定性解析器输出、确认协议、结构化操作响应等类型定义。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

OperatorAction = Literal[
    "purchase",
    "redeem",
    "transfer",
    "reassessment",
    "information_update",
    "product_query",
    "suspicious_report",
    "work_order_create",
]


class ParsedOperation(BaseModel):
    """确定性解析器输出：8 种业务操作 + 结构化参数。"""

    action: OperatorAction
    params: dict[str, Any] = Field(default_factory=dict)


class ConfirmationDetail(BaseModel):
    """二次确认凭据：由 Redis 保存待确认操作，确认/取消时凭 id 消费。"""

    id: str
    expires_at: str | None = None
    reason: str | None = None
