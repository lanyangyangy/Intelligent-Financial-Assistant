from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.common.logging.config import get_logger
from app.infrastructure.agent_event_bus import (
    EVENT_CHANNELS,
    EVENT_LARGE_TRANSACTION,
    EVENT_RISK_ALERT,
    EVENT_SUSPICIOUS_INTENT,
)
from app.models.profile import CustomerProfile, CustomerProfileTag

logger = get_logger(__name__)

PROCESSED_EVENT_TTL_SECONDS = 7 * 24 * 60 * 60
LARGE_TRANSACTION_WINDOW_SECONDS = 7 * 24 * 60 * 60
RISK_ALERT_SCORE = {"low": 30, "medium": 60, "high": 90}
RISK_ALERT_MARKER = "CROSS_AGENT_RISK_ALERT"
HIGH_RISK_MARKER = "HIGH_RISK_CUSTOMER"
SUSPICIOUS_INTENT_MARKER = "SUSPICIOUS_INTENT"


class CrossAgentEventSubscriber:
    """跨 Agent Redis Pub/Sub 消费者。

    一个订阅进程按频道分发事件，再由对应的领域处理器更新自己的数据。
    处理器之间不互相调用，保持截图要求的事件解耦关系。
    """

    def __init__(self, database, redis_client) -> None:
        self.database = database
        self.redis = redis_client
        self.pubsub = None
        self.task: asyncio.Task | None = None

    async def start(self) -> None:
        if self.task is not None and not self.task.done():
            return
        self.pubsub = self.redis.pubsub(ignore_subscribe_messages=True)
        await self.pubsub.subscribe(*EVENT_CHANNELS)
        self.task = asyncio.create_task(
            self._consume(), name="cross-agent-event-subscriber"
        )
        logger.info("cross_agent_event_subscriber_started channels=%s", EVENT_CHANNELS)

    async def stop(self) -> None:
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if self.pubsub is not None:
            with suppress(Exception):
                await self.pubsub.unsubscribe(*EVENT_CHANNELS)
            with suppress(Exception):
                await self.pubsub.close()
            self.pubsub = None
        logger.info("cross_agent_event_subscriber_stopped")

    async def _consume(self) -> None:
        if self.pubsub is None:
            return
        while True:
            message = await self.pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if not message:
                continue
            try:
                channel = str(message.get("channel", ""))
                raw = message.get("data")
                event = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(event, dict):
                    raise ValueError("event payload must be a JSON object")
                await self.dispatch(channel, event)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad event must not stop the bus
                logger.exception("cross_agent_event_failed message=%s", message)

    async def dispatch(self, channel: str, event: dict) -> None:
        """按截图中的四条消费关系分发事件。"""
        if channel not in EVENT_CHANNELS:
            logger.warning("cross_agent_event_ignored channel=%s", channel)
            return

        event_id = event.get("event_id")
        processed_key = (
            self._processed_key(channel, str(event_id)) if event_id else None
        )
        if processed_key and not await self.redis.set(
            processed_key, "1", nx=True, ex=PROCESSED_EVENT_TTL_SECONDS
        ):
            return
        try:
            if channel == EVENT_RISK_ALERT:
                await self._handle_risk_alert_for_advisor(event)
                await self._handle_risk_alert_for_customer_service(event)
            elif channel == EVENT_LARGE_TRANSACTION:
                await self._handle_large_transaction_for_risk(event)
            elif channel == EVENT_SUSPICIOUS_INTENT:
                await self._handle_suspicious_intent_for_risk(event)
        except Exception:
            if processed_key:
                with suppress(Exception):
                    await self.redis.delete(processed_key)
            raise

    async def _handle_risk_alert_for_advisor(self, event: dict) -> None:
        """投顾 Agent：更新客户的跨 Agent 风险标记。"""
        payload = self._payload(event)
        customer_id = self._customer_id(payload)
        level = self._risk_level(payload)
        if not customer_id or not level:
            return

        now = datetime.now(UTC)
        async with self.database.session_factory() as session:
            profile = (
                await session.execute(
                    select(CustomerProfile).where(
                        CustomerProfile.user_id == customer_id
                    )
                )
            ).scalar_one_or_none()
            if profile is None:
                logger.warning(
                    "risk_alert_profile_missing customer_id=%s", customer_id
                )
                return

            marker = await self._get_active_tag(session, customer_id, RISK_ALERT_MARKER)
            value = {
                "level": level,
                "score": RISK_ALERT_SCORE[level],
                "alert_color": payload.get("alert_color"),
                "trigger_rules": payload.get("trigger_rules", []),
                "last_event_id": event.get("event_id"),
                "updated_at": now.isoformat(),
            }
            self._upsert_tag(
                session,
                marker,
                customer_id=customer_id,
                tag_code=RISK_ALERT_MARKER,
                value=value,
                confidence=float(payload.get("confidence", 0.8)),
                evidence="风控 Agent 通过 Redis Pub/Sub 发布风险预警",
                now=now,
            )
            await session.commit()

    async def _handle_risk_alert_for_customer_service(self, event: dict) -> None:
        """客服 Agent：对高风险客户增加可检索的高风险标记。"""
        payload = self._payload(event)
        customer_id = self._customer_id(payload)
        if not customer_id or self._risk_level(payload) != "high":
            return

        now = datetime.now(UTC)
        async with self.database.session_factory() as session:
            marker = await self._get_active_tag(session, customer_id, HIGH_RISK_MARKER)
            existing = self._tag_value(marker)
            value = {
                "marked": True,
                "reason": "risk_alert",
                "alert_count": int(existing.get("alert_count", 0)) + 1,
                "last_event_id": event.get("event_id"),
                "updated_at": now.isoformat(),
            }
            self._upsert_tag(
                session,
                marker,
                customer_id=customer_id,
                tag_code=HIGH_RISK_MARKER,
                value=value,
                confidence=1.0,
                evidence="客服 Agent 根据风险预警标记高风险客户",
                now=now,
            )
            await session.commit()

    async def _handle_large_transaction_for_risk(self, event: dict) -> None:
        """风控 Agent：用 Redis ZSET 累计客户 7 天大额交易频次。"""
        payload = self._payload(event)
        customer_id = self._customer_id(payload)
        event_id = str(event.get("event_id") or "")
        if not customer_id or not event_id:
            return

        key = f"risk:large_transaction:{customer_id}"
        now = datetime.now(UTC).timestamp()
        await self.redis.zadd(key, {event_id: now})
        await self.redis.zremrangebyscore(
            key, 0, now - LARGE_TRANSACTION_WINDOW_SECONDS
        )
        await self.redis.expire(key, LARGE_TRANSACTION_WINDOW_SECONDS)
        count = await self.redis.zcard(key)
        logger.info(
            "large_transaction_accumulated customer_id=%s count_7d=%s",
            customer_id,
            count,
        )

    async def _handle_suspicious_intent_for_risk(self, event: dict) -> None:
        """风控 Agent：累计可疑意图并保留最近一次行为证据。"""
        payload = self._payload(event)
        customer_id = self._customer_id(payload)
        if not customer_id:
            return

        now = datetime.now(UTC)
        async with self.database.session_factory() as session:
            marker = await self._get_active_tag(
                session, customer_id, SUSPICIOUS_INTENT_MARKER
            )
            existing = self._tag_value(marker)
            value = {
                "count": int(existing.get("count", 0)) + 1,
                "intent": payload.get("intent", ""),
                "indicator": payload.get("indicator", ""),
                "last_event_id": event.get("event_id"),
                "last_seen_at": now.isoformat(),
            }
            self._upsert_tag(
                session,
                marker,
                customer_id=customer_id,
                tag_code=SUSPICIOUS_INTENT_MARKER,
                value=value,
                confidence=float(payload.get("confidence", 0.7)),
                evidence="客服 Agent 识别到可疑意图并通过 Redis Pub/Sub 上报",
                now=now,
            )
            await session.commit()

    @staticmethod
    def _payload(event: dict) -> dict:
        payload = event.get("payload", {})
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _customer_id(payload: dict) -> str | None:
        value = payload.get("customer_id") or payload.get("user_id")
        return str(value) if value else None

    @staticmethod
    def _risk_level(payload: dict) -> str | None:
        value = str(payload.get("alert_level", "")).lower()
        return value if value in RISK_ALERT_SCORE else None

    @staticmethod
    def _processed_key(channel: str, event_id: str) -> str:
        return f"agent:event:processed:{channel}:{event_id}"

    @staticmethod
    async def _get_active_tag(session, user_id: str, tag_code: str):
        return (
            (
                await session.execute(
                    select(CustomerProfileTag).where(
                        CustomerProfileTag.user_id == user_id,
                        CustomerProfileTag.tag_code == tag_code,
                        CustomerProfileTag.status == "ACTIVE",
                    )
                )
            )
            .scalars()
            .first()
        )

    @staticmethod
    def _tag_value(tag: CustomerProfileTag | None) -> dict:
        if tag is None:
            return {}
        try:
            value = json.loads(tag.tag_value_json or "{}")
        except (TypeError, json.JSONDecodeError):
            value = {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _upsert_tag(
        session,
        tag: CustomerProfileTag | None,
        *,
        customer_id: str,
        tag_code: str,
        value: dict,
        confidence: float,
        evidence: str,
        now: datetime,
    ) -> None:
        if tag is None:
            tag = CustomerProfileTag(
                id=str(uuid4()),
                user_id=customer_id,
                tag_code=tag_code,
                status="ACTIVE",
                effective_at=now,
            )
            session.add(tag)
        tag.tag_value_json = json.dumps(value, ensure_ascii=False, default=str)
        tag.confidence = max(0.0, min(1.0, confidence))
        tag.source_type = "SYSTEM_BEHAVIOR"
        tag.extraction_method = "RULE"
        tag.evidence_quote = evidence[:500]
        tag.status = "ACTIVE"
        tag.effective_at = tag.effective_at or now
        tag.updated_at = now
