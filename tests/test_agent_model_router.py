from __future__ import annotations

import asyncio

import pytest

from app.agents.base import AgentBase

pytestmark = pytest.mark.unit


class RecordingRouter:
    available = True

    def __init__(self) -> None:
        self.call: dict | None = None

    async def chat_with_routing(self, messages: list[dict], **kwargs) -> str:
        self.call = {"messages": messages, **kwargs}
        return "routed response"


class CustomerServiceAgent(AgentBase):
    name = "customer_service"

    async def run(self, message, context):
        raise NotImplementedError


def test_agent_base_passes_agent_name_to_model_router():
    router = RecordingRouter()
    agent = CustomerServiceAgent(database=None, settings=None, llm=router)

    response = asyncio.run(agent.llm_chat("system prompt", "user question"))

    assert response == "routed response"
    assert router.call is not None
    assert router.call["agent_name"] == "customer_service"
    assert router.call["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user question"},
    ]
