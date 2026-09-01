from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

from app.agents.customer_agent import CustomerAgent
from app.infrastructure.agent_event_bus import (
    EVENT_LARGE_TRANSACTION,
    EVENT_RISK_ALERT,
    EVENT_SUSPICIOUS_INTENT,
    AgentEventBus,
)
from app.ports.agent import AgentContext
from app.services.agent_event_service import CrossAgentEventSubscriber


class FakeRedis:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.processed: set[str] = set()

    async def publish(self, channel: str, message: str) -> int:
        self.messages.append((channel, message))
        return 1

    async def set(self, key: str, value: str, *, nx=False, ex=None):
        if nx and key in self.processed:
            return False
        self.processed.add(key)
        return True

    async def delete(self, key: str) -> int:
        self.processed.discard(key)
        return 1


def test_agent_event_bus_publishes_standard_envelope():
    redis = FakeRedis()

    event = asyncio.run(
        AgentEventBus(redis).publish(
            EVENT_LARGE_TRANSACTION,
            event_type="large_transaction",
            source_agent="business_operator",
            payload={"customer_id": "customer-1", "amount": 50000},
        )
    )

    channel, raw = redis.messages[0]
    assert channel == EVENT_LARGE_TRANSACTION
    assert json.loads(raw) == event
    assert event["event_id"]
    assert event["payload"]["amount"] == 50000


def test_customer_agent_detects_only_explicit_suspicious_indicators():
    assert CustomerAgent._suspicious_indicator("如何绕过风控拆分交易") == "绕过风控"
    assert CustomerAgent._suspicious_indicator("请解释反洗钱政策") is None


def test_customer_agent_publishes_suspicious_intent_for_authenticated_customer():
    agent = object.__new__(CustomerAgent)
    agent.settings = SimpleNamespace(redis_url="redis://unused")
    redis = FakeRedis()
    agent._redis = redis

    asyncio.run(
        agent._publish_suspicious_intent(
            "如何绕过风控拆分交易",
            "policy_explain",
            0.9,
            AgentContext(request_id="request-1", user_id="customer-1"),
        )
    )

    channel, raw = redis.messages[0]
    assert channel == EVENT_SUSPICIOUS_INTENT
    assert json.loads(raw)["payload"]["customer_id"] == "customer-1"


def test_cross_agent_subscriber_routes_all_screenshot_relations():
    subscriber = CrossAgentEventSubscriber(None, FakeRedis())
    advisor_handler = AsyncMock()
    customer_handler = AsyncMock()
    large_handler = AsyncMock()
    suspicious_handler = AsyncMock()
    subscriber._handle_risk_alert_for_advisor = advisor_handler
    subscriber._handle_risk_alert_for_customer_service = customer_handler
    subscriber._handle_large_transaction_for_risk = large_handler
    subscriber._handle_suspicious_intent_for_risk = suspicious_handler

    async def run() -> None:
        await subscriber.dispatch(
            EVENT_RISK_ALERT,
            {"event_id": "risk-1", "payload": {"alert_level": "high"}},
        )
        await subscriber.dispatch(
            EVENT_LARGE_TRANSACTION,
            {"event_id": "large-1", "payload": {"customer_id": "customer-1"}},
        )
        await subscriber.dispatch(
            EVENT_SUSPICIOUS_INTENT,
            {"event_id": "intent-1", "payload": {"customer_id": "customer-1"}},
        )

    asyncio.run(run())

    advisor_handler.assert_awaited_once()
    customer_handler.assert_awaited_once()
    large_handler.assert_awaited_once()
    suspicious_handler.assert_awaited_once()


def test_cross_agent_subscriber_deduplicates_same_event():
    redis = FakeRedis()
    subscriber = CrossAgentEventSubscriber(None, redis)
    handler = AsyncMock()
    subscriber._handle_large_transaction_for_risk = handler

    async def run() -> None:
        event = {"event_id": "large-1", "payload": {"customer_id": "customer-1"}}
        await subscriber.dispatch(EVENT_LARGE_TRANSACTION, event)
        await subscriber.dispatch(EVENT_LARGE_TRANSACTION, event)

    asyncio.run(run())

    handler.assert_awaited_once()
