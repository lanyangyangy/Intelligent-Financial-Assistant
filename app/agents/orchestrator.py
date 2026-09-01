from __future__ import annotations

from app.ports.agent import Agent, AgentContext
from app.schemas.agents import AgentResult


class AgentOrchestrator:
    def __init__(self, agents: list[Agent] | None = None):
        self._agents = {agent.name: agent for agent in (agents or [])}

    def register(self, agent: Agent) -> None:
        self._agents[agent.name] = agent

    def get(self, name: str) -> Agent | None:
        return self._agents.get(name)

    async def run(self, name: str, message: str, context: AgentContext) -> AgentResult:
        """统一执行骨架：记忆召回 → 核心逻辑 → 置信度校验 → 数据沉淀 → 事件广播。

        各钩子默认安全 no-op，Agent 按需覆写（AgentBase 提供）。
        """
        agent = self.get(name)
        if agent is None:
            return AgentResult(
                agent_name=name,
                status="error",
                summary="agent not registered",
                errors=[f"unknown agent: {name}"],
            )
        # 1. 记忆召回钩子（短期/中期/长期；画像/图谱/会话上下文）
        recalled = await agent.recall_memory(context)
        if recalled and context.metadata:
            context.metadata["recalled_memory"] = recalled
        # 2. 核心逻辑
        result = await agent.run(message, context)
        # 3. 置信度校验钩子
        result = agent.check_confidence(result, context)
        # 4. 数据沉淀钩子
        await agent.persist_turn(result, context)
        return result
