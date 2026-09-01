from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.redis_client import RedisClient

# ---------------------------------------------------------------------------
# 短期记忆（Phase 4 F4.2）：基于 Redis 的会话上下文管理
#   Key:  session:{session_id}:messages （Redis List）
#   TTL:  30 分钟（每次对话续期），最长 24 小时
#   Token 预算：最多保留最近 N 轮对话
# 会话结束后归档到 conversation_archive 表（MySQL）
# ---------------------------------------------------------------------------

SESSION_TTL_SECONDS = 30 * 60  # 30 分钟续期
SESSION_MAX_TURNS = 10  # 最多保留 10 轮（用户+助手各算 1 轮）
SESSION_MAX_MESSAGES = 20
SESSION_TOKEN_BUDGET = 4096  # Phase 4 F4.2：Token 预算（超出截断旧消息）


class SessionMemoryService:
    """Redis 短期会话记忆：追加消息、读取历史、续期、归档。"""

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    @staticmethod
    def _key(session_id: str) -> str:
        return f"session:{session_id}:messages"

    async def append(self, session_id: str, role: str, content: str) -> None:
        """追加一条消息并续期；超出轮数时裁剪旧消息。"""
        key = self._key(session_id)
        client = self._redis.client
        message = json.dumps(
            {
                "role": role,
                "content": content,
                "ts": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
        )
        await client.rpush(key, message)
        # 裁剪：最多保留 MAX_MESSAGES 条
        length = await client.llen(key)
        if length > SESSION_MAX_MESSAGES:
            await client.ltrim(key, length - SESSION_MAX_MESSAGES, -1)
        # 续期 30 分钟
        await client.expire(key, SESSION_TTL_SECONDS)

    async def get_history(
        self, session_id: str, limit: int = SESSION_MAX_MESSAGES
    ) -> list[dict]:
        """读取最近 N 条会话消息（旧→新）。"""
        key = self._key(session_id)
        client = self._redis.client
        raw = await client.lrange(key, -limit, -1)
        history = []
        for item in raw:
            try:
                history.append(json.loads(item))
            except (json.JSONDecodeError, TypeError):
                continue
        return history

    async def clear(self, session_id: str) -> None:
        await self._redis.client.delete(self._key(session_id))

    def format_context(self, history: list[dict], max_turns: int = 6) -> str:
        """将历史消息格式化为 Agent 可用的上下文文本（Token 预算 4096 截断）。"""
        if not history:
            return ""
        lines = []
        recent = history[-max_turns * 2 :]
        total_chars = 0
        budget_chars = SESSION_TOKEN_BUDGET * 2  # 中文约 2 字符/token 粗略估算
        for msg in reversed(recent):
            speaker = "用户" if msg.get("role") == "user" else "助手"
            line = f"{speaker}: {msg.get('content', '')}"
            if total_chars + len(line) > budget_chars:
                break
            lines.append(line)
            total_chars += len(line)
        return "\n".join(reversed(lines))

    # -- 会话归档 ------------------------------------------------------
    async def archive(
        self,
        session: AsyncSession,
        session_id: str,
        user_id: str,
        history: list[dict] | None = None,
        *,
        agent_type: str = "",
        tool_calls: list[dict] | None = None,
        summary: str = "",
        clear: bool = True,
    ) -> None:
        """将会话写入 conversation_archive 表，并按需清理 Redis。"""
        from app.models.conversation import ConversationArchive

        history = history or await self.get_history(session_id)
        if not history:
            return
        archive = ConversationArchive(
            id=str(uuid4()),
            session_id=session_id,
            user_id=user_id,
            agent_type=agent_type,
            messages_json=history,
            message_count=len(history),
            tool_calls_json=tool_calls or [],
            summary=summary,
            archived_at=datetime.now(UTC),
        )
        session.add(archive)
        await session.flush()
        if clear:
            await self.clear(session_id)

    async def archive_turn(
        self,
        session: AsyncSession,
        session_id: str,
        user_id: str,
        user_message: str,
        assistant_message: str,
        *,
        agent_type: str = "",
        tool_calls: list[dict] | None = None,
        summary: str = "",
    ) -> None:
        """按轮次写入审计记录，不清理 Redis，保证多轮上下文继续可用。"""
        await self.archive(
            session,
            session_id,
            user_id,
            history=[
                {
                    "role": "user",
                    "content": user_message,
                    "ts": datetime.now(UTC).isoformat(),
                },
                {
                    "role": "assistant",
                    "content": assistant_message,
                    "ts": datetime.now(UTC).isoformat(),
                },
            ],
            agent_type=agent_type,
            tool_calls=tool_calls,
            summary=summary,
            clear=False,
        )
