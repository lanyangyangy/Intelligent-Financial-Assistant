from __future__ import annotations

import asyncio

import pytest

from app.agents.analytics_agent import FEW_SHOTS, INTENT_TABLES, DataAnalystAgent

pytestmark = pytest.mark.unit
from app.agents.graph import route_message
from app.api.chat import resolve_agent_name
from app.ports.agent import AgentContext


class FakeLLM:
    available = True

    def __init__(self) -> None:
        self.system_prompt = ""

    async def chat(self, messages, **kwargs):  # noqa: ANN001
        self.system_prompt = messages[0]["content"]
        return "SELECT COUNT(*) AS product_count FROM product"


class InMemoryAnalyst(DataAnalystAgent):
    def __init__(self) -> None:
        super().__init__(database=None, settings=None, llm=FakeLLM())
        self.execute_count = 0

    async def _load_schema(self, tables: list[str]) -> str:
        return "TABLE product -- 理财产品\n  product.id uuid"

    async def _execute_sql(self, sql: str) -> list[dict]:
        self.execute_count += 1
        return [{"product_count": 6}]

    async def _interpret(self, message: str, rows: list[dict]) -> str:
        return "当前在售产品共 6 个。"


def test_all_requirement_intents_are_classified():
    agent = object.__new__(DataAnalystAgent)
    cases = {
        "客户目前持有哪些产品？": "holdings_query",
        "各产品类型的平均收益率是多少？": "return_stats",
        "最近一个月的交易记录": "transaction_query",
        "AUM超过100万的客户有多少个？": "customer_stats",
        "当前在售产品总数": "product_stats",
        "查看待处理工单": "workorder_query",
    }
    assert {message: agent.classify_intent(message) for message in cases} == cases


def test_named_holdings_query_extracts_customer_without_confusing_product_type():
    assert DataAnalystAgent._extract_customer_identifier(
        "查询零售投资者持有哪些产品？"
    ) == "零售投资者"
    assert DataAnalystAgent._extract_customer_identifier(
        "客户目前持有哪些产品？"
    ) is None


def test_return_stats_distinguishes_products_from_product_types():
    product_sql = DataAnalystAgent._return_stats_sql("查询各产品平均收益率")
    type_sql = DataAnalystAgent._return_stats_sql("查询各产品类型平均收益率")
    assert "GROUP BY p.id, p.name" in product_sql
    assert "GROUP BY p.product_type" in type_sql


def test_sql_validation_is_read_only_and_caps_limit():
    agent = object.__new__(DataAnalystAgent)
    assert agent._validate_sql("SELECT 1", ["product"]) == "SELECT 1 LIMIT 100"
    assert agent._validate_sql("SELECT 1 LIMIT 200", ["product"]) == "SELECT 1 LIMIT 100"
    assert agent._validate_sql("SELECT 1; SELECT 2", ["product"]) is None
    assert agent._validate_sql("SELECT 1 INTO TEMP tmp", ["product"]) is None
    assert agent._validate_sql("DROP TABLE product", ["product"]) is None


def test_generated_plural_table_is_normalized_to_project_schema():
    agent = object.__new__(DataAnalystAgent)
    sql = agent._normalize_table_aliases(
        "SELECT COUNT(*) FROM products", ["product"]
    )
    assert sql == "SELECT COUNT(*) FROM product"
    assert agent._validate_sql(sql, ["product"]) == (
        "SELECT COUNT(*) FROM product LIMIT 100"
    )


def test_requirement_examples_are_all_executable_read_only_sql():
    agent = object.__new__(DataAnalystAgent)
    for intent, (_, sql) in FEW_SHOTS.items():
        validated = agent._validate_sql(sql, INTENT_TABLES[intent])
        assert validated is not None, intent
        assert validated.endswith("LIMIT 100"), intent


def test_few_shot_prompt_contains_executable_examples():
    llm = FakeLLM()
    agent = DataAnalystAgent(database=None, settings=None, llm=llm)
    asyncio.run(
        agent._generate_sql(
            "当前在售产品总数",
            "TABLE product\n  product.status varchar",
            agent._few_shots_for_intent("product_stats"),
        )
    )
    assert "Few-shot 示例" in llm.system_prompt
    assert "FROM product" in llm.system_prompt


def test_every_intent_injects_at_least_three_few_shot_examples():
    agent = object.__new__(DataAnalystAgent)

    for intent in INTENT_TABLES:
        assert agent._few_shots_for_intent(intent).count("示例") >= 3, intent


def test_local_result_cache_prevents_duplicate_execution():
    agent = InMemoryAnalyst()
    context = AgentContext(request_id="test", user_id="employee-1", role="auditor")

    first = asyncio.run(agent.run("当前在售产品总数", context))
    second = asyncio.run(agent.run("当前在售产品总数", context))

    assert first.status == "success"
    assert second.status == "success"
    assert first.data["cache_hit"] is False
    assert second.data["cache_hit"] is True
    assert second.data["sql_statement"] == second.data["sql"]
    assert agent.execute_count == 1


def test_agent_type_alias_routes_to_internal_agent_name():
    assert resolve_agent_name(None, "analyst") == "data_analyst"
    assert resolve_agent_name("data_analyst", None) == "data_analyst"
    assert resolve_agent_name("analyst", None) == "data_analyst"


def test_supervisor_routes_analytics_queries_only_when_allowed():
    assert route_message("最近一个月的交易记录", allow_data_analysis=True) == [
        "data_analyst"
    ]
    assert route_message("AUM超过100万的客户数", allow_data_analysis=True) == [
        "data_analyst"
    ]
    assert route_message("各类型产品数量", allow_data_analysis=True) == [
        "data_analyst"
    ]
    assert route_message("最近一个月的交易记录", allow_data_analysis=False) == [
        "customer_service"
    ]
    assert route_message("AUM超过100万的客户数", allow_data_analysis=False) == [
        "customer_service"
    ]
    assert route_message("各类型产品数量", allow_data_analysis=False) == [
        "customer_service"
    ]
    assert route_message("创建工单", allow_data_analysis=True) == [
        "business_operator"
    ]
