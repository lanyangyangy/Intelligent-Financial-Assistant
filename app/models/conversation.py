from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func  # noqa: F401
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user_id import UserId


class ConversationArchive(Base):
    """会话归档（Phase 4 F4.2）：完整对话流水 + 工具调用记录。

    短期记忆（Redis 会话）在会话结束后归档至此表，支持按 session_id /
    user_id 查询历史对话，为长期记忆和审计提供底座。
    """

    __tablename__ = "conversation_archive"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[int] = mapped_column(
        UserId,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    agent_type: Mapped[str] = mapped_column(String(32), default="")
    messages_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
