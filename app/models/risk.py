from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user_id import UserId


class RiskAlert(Base):
    """风控预警（Phase 4 F4.1）：规则命中后生成的预警记录。"""

    __tablename__ = "risk_alert"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        UserId,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    alert_level: Mapped[str] = mapped_column(String(16), index=True)  # low/medium/high
    alert_color: Mapped[str] = mapped_column(
        String(8), default="blue"
    )  # blue/yellow/red
    alert_type: Mapped[str] = mapped_column(String(64), default="")
    trigger_rules_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    confidence: Mapped[float] = mapped_column(Integer, default=0)
    transaction_ids_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    trigger_detail: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(
        String(32), default="pending", index=True
    )  # pending/confirmed/resolved
    handle_note: Mapped[str] = mapped_column(Text, default="")
    handler_id: Mapped[int | None] = mapped_column(
        UserId, ForeignKey("users.id"), nullable=True
    )
    handled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class WorkOrder(Base):
    """工单（Phase 4 F4.1）：预警/投诉/可疑上报等流程载体。"""

    __tablename__ = "work_order"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workorder_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_id: Mapped[int | None] = mapped_column(
        UserId, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    submitter_id: Mapped[int | None] = mapped_column(
        UserId, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    handler_id: Mapped[int | None] = mapped_column(
        UserId, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    workorder_type: Mapped[str] = mapped_column(
        String(64), index=True
    )  # 可疑交易上报/投诉/风险预警
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    status: Mapped[str] = mapped_column(
        String(32), default="pending", index=True
    )  # pending/processing/resolved/closed
    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(32), default="risk_alert")
    source_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )  # 关联 risk_alert.id
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
