"""业务操作幂等记录（移植目标项目 OperatorRequestDedupe）。

request_id 24h 去重：同一操作人同一请求键在 24h 内重复提交时，
直接返回首次执行结果，防止网络重试导致重复下单/转账。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user_id import UserId


class OperatorRequestDedupe(Base):
    __tablename__ = "operator_request_dedupe"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(UserId, index=True)
    request_id: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="processing")
    response_json: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index(
            "uq_operator_dedupe_user_request",
            "user_id",
            "request_id",
            unique=True,
        ),
    )
