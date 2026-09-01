from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

EVENT_RISK_ALERT = "event:risk_alert"
EVENT_LARGE_TRANSACTION = "event:large_transaction"
EVENT_SUSPICIOUS_INTENT = "event:suspicious_intent"

EVENT_CHANNELS = (
    EVENT_RISK_ALERT,
    EVENT_LARGE_TRANSACTION,
    EVENT_SUSPICIOUS_INTENT,
)


class AgentEventBus:
    """Redis Pub/Sub 事件总线的统一发布入口。

    事件统一携带 event_id 和 occurred_at，消费者可以据此做幂等处理和
    审计定位。发布失败由调用方按 Agent 的降级策略处理，不能阻断主业务。
    """

    def __init__(self, client) -> None:
        self.client = client

    async def publish(
        self,
        channel: str,
        *,
        event_type: str,
        source_agent: str,
        payload: dict,
    ) -> dict:
        if channel not in EVENT_CHANNELS:
            raise ValueError(f"unsupported agent event channel: {channel}")
        event = {
            "event_id": str(uuid4()),
            "event_type": event_type,
            "source_agent": source_agent,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": payload,
        }
        await self.client.publish(
            channel, json.dumps(event, ensure_ascii=False, default=str)
        )
        return event

