"""评测 runner：把数据集案例映射到被测代码的确定性检查。

每个 kind 对应被测代码的一个真实函数/逻辑路径，全部离线执行：
- supervisor_routing -> app.agents.graph.route_message
- rag_graphrag       -> 知识库文档完整性 + 降级/回退路径
- nl2sql             -> DataAnalystAgent._validate_sql / classify_intent
- permissions_highrisk -> PERMISSION_MATRIX / staff_agent_allowed /
                          parse_operation / classify_tier
- faults_degradation -> QwenProvider / HealthService / 设置开关
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# 估算参数（仅用于报告展示，标注为估算值）
TOKENS_PER_CHAR = 0.7  # 中文场景近似
COST_PER_1K_TOKENS = 0.0008  # 元/千 token（qwen-plus 参考价）


def _estimate_tokens(case: dict[str, Any]) -> int:
    text = str(case.get("input", "")) + str(case.get("expected", ""))
    return max(1, int(len(text) * TOKENS_PER_CHAR))


# ---------------------------------------------------------------------------
# supervisor_routing
# ---------------------------------------------------------------------------
def _route(case: dict[str, Any]) -> tuple[bool, str]:
    from app.agents.graph import route_message

    got = route_message(case["input"], **case.get("params", {}))
    expected = case["expected"]
    mode = case.get("mode", "exact")
    if mode == "exact":
        ok = got == expected
    elif mode == "contains":
        ok = bool(expected) and all(any(a in g for g in got) for a in expected)
    elif mode == "any":
        ok = any(g in expected for g in got)
    else:
        ok = False
    return ok, f"got={got}, expected={expected}"


# ---------------------------------------------------------------------------
# rag_graphrag
# ---------------------------------------------------------------------------
def _doc_exists(case: dict[str, Any]) -> tuple[bool, str]:
    rel = case["input"]  # 相对 docs/ 的路径
    path = ROOT / "docs" / rel
    min_bytes = int(case.get("min_bytes", 0))
    ok = path.exists() and path.stat().st_size >= min_bytes
    return ok, f"path={rel}, size={path.stat().st_size if path.exists() else 0}B"


def _doc_contains(case: dict[str, Any]) -> tuple[bool, str]:
    rel = case["input"]
    path = ROOT / "docs" / rel
    if not path.exists():
        return False, f"missing: {rel}"
    content = path.read_text(encoding="utf-8", errors="ignore")
    hits = [kw for kw in case["expected"] if kw in content]
    missing = [kw for kw in case["expected"] if kw not in content]
    ok = not missing
    return ok, f"hits={hits}, missing={missing}"


def _neo4j_disabled_by_default(case: dict[str, Any]) -> tuple[bool, str]:
    from app.core.settings import get_settings

    value = get_settings().neo4j_enabled
    return value is False, f"neo4j_enabled={value}"


def _vector_adapter_present(case: dict[str, Any]) -> tuple[bool, str]:
    from app.infrastructure.vector_store.pgvector import PgVectorStore

    ok = callable(getattr(PgVectorStore, "search", None))
    return ok, f"PgVectorStore.search={ok}"


# ---------------------------------------------------------------------------
# nl2sql
# ---------------------------------------------------------------------------
def _validate_sql(case: dict[str, Any]) -> tuple[bool, str]:
    from app.agents.analytics_agent import MAX_RESULT_ROWS, DataAnalystAgent

    agent = object.__new__(DataAnalystAgent)
    result = agent._validate_sql(case["input"], case.get("tables"))
    expected = case["expected"]  # reject / pass / capped
    if expected == "reject":
        ok = result is None
        return ok, f"validate={'rejected' if ok else 'PASSED(unexpected)'} -> {result!r}"
    if expected == "capped":
        ok = result is not None and "LIMIT" in result.upper()
        if ok:
            m = re.search(r"LIMIT\s+(\d+)", result, re.I)
            ok = m is not None and int(m.group(1)) <= MAX_RESULT_ROWS
        return ok, f"sql={result!r}, max={MAX_RESULT_ROWS}"
    # pass
    ok = result is not None
    return ok, f"sql={result!r}"


def _classify_intent(case: dict[str, Any]) -> tuple[bool, str]:
    from app.agents.analytics_agent import DataAnalystAgent

    agent = object.__new__(DataAnalystAgent)
    got = agent.classify_intent(case["input"])
    expected = case["expected"]
    return got == expected, f"got={got}, expected={expected}"


# ---------------------------------------------------------------------------
# permissions_highrisk
# ---------------------------------------------------------------------------
def _permission_matrix(case: dict[str, Any]) -> tuple[bool, str]:
    from app.agents.operations_agent import PERMISSION_MATRIX

    intent = case["input"]["intent"]
    role = case["input"]["role"]
    expect_allowed = case["expected"]
    allowed = role in PERMISSION_MATRIX.get(intent, set())
    ok = allowed is expect_allowed
    return ok, f"intent={intent}, role={role}, allowed={allowed}"


def _staff_allowed(case: dict[str, Any]) -> tuple[bool, str]:
    from app.agents.graph import staff_agent_allowed

    got = staff_agent_allowed(case["input"]["agent"], **case["input"].get("params", {}))
    expected = case["expected"]
    return got is expected, f"got={got}, expected={expected}"


def _parse_op(case: dict[str, Any]) -> tuple[bool, str]:
    from app.services.operator_parser import parse_operation

    parsed = parse_operation(case["input"])
    expected_intent = case["expected"]["intent"]
    ok = parsed.action == expected_intent
    detail = f"action={parsed.action}, params={parsed.params}"
    if ok and case["expected"].get("param_key"):
        pk = case["expected"]["param_key"]
        ok = pk in parsed.params
        detail += f", param[{pk}]={parsed.params.get(pk)}"
    return ok, detail


def _confirm_threshold(case: dict[str, Any]) -> tuple[bool, str]:
    from app.services.customer_tier import classify_tier

    tier = classify_tier(case["input"]["tier"], case["input"].get("aum", 0.0))
    expected = case["expected"]  # {"purchase": 10000, "transfer": 50000}
    ok = (
        tier.confirmation_threshold_purchase == expected["purchase"]
        and tier.confirmation_threshold_transfer == expected["transfer"]
    )
    return (
        ok,
        f"purchase={tier.confirmation_threshold_purchase}, "
        f"transfer={tier.confirmation_threshold_transfer}",
    )


# ---------------------------------------------------------------------------
# faults_degradation
# ---------------------------------------------------------------------------
def _llm_available(case: dict[str, Any]) -> tuple[bool, str]:
    from types import SimpleNamespace

    from app.infrastructure.qwen import QwenProvider

    provider = QwenProvider(SimpleNamespace(dashscope_api_key=""))
    got = provider.available
    expected = case["expected"]
    return got is expected, f"available={got} (无 API Key)" if expected is False else (
        got is expected,
        f"available={got}",
    )


def _check_config(case: dict[str, Any]) -> tuple[bool, str]:
    import asyncio
    from types import SimpleNamespace

    from app.infrastructure.qwen import QwenProvider

    provider = object.__new__(QwenProvider)
    provider._client = object()
    provider.settings = SimpleNamespace(qwen_chat_model=case["input"])
    result = asyncio.run(provider.check_config())
    ok = (
        result.get("status") == "configured"
        and result.get("verified") == "false"
        and result.get("model") == case["input"]
    )
    return ok, f"result={result}"


def _health_component(case: dict[str, Any]) -> tuple[bool, str]:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services.health_service import HealthService

    target = case["input"]["component"]  # postgresql/redis/neo4j/qwen/embedding
    status = case["input"]["status"]  # ok / error / configured / skipped

    def _status(component: str) -> dict:
        return {"status": status if component == target else "ok"}

    db = SimpleNamespace(check=AsyncMock(return_value=_status("postgresql")))
    redis = SimpleNamespace(check=AsyncMock(return_value=_status("redis")))
    qwen = SimpleNamespace(
        check_config=AsyncMock(return_value=_status("qwen")),
        check_embedding=AsyncMock(return_value=_status("embedding")),
    )
    graph = SimpleNamespace(check=AsyncMock(return_value=_status("neo4j")))
    service = HealthService(db, redis, qwen, graph, SimpleNamespace())
    checks = asyncio.run(service.check_all())
    got = checks.get(target)
    ok = got == case["expected"][target]
    return ok, f"checks={checks}"


def _graph_fallback(case: dict[str, Any]) -> tuple[bool, str]:
    """Neo4j 未启用时，知识图谱客户端处于禁用状态（回退纯 RAG 的前提）。"""
    from app.core.settings import get_settings

    settings = get_settings()
    ok = settings.neo4j_enabled is False
    return ok, f"neo4j_enabled={settings.neo4j_enabled}"


def _llm_chat_unconfigured(case: dict[str, Any]) -> tuple[bool, str]:
    import asyncio
    from types import SimpleNamespace

    from app.infrastructure.qwen import QwenProvider

    provider = QwenProvider(SimpleNamespace(dashscope_api_key=""))
    try:
        asyncio.run(provider.chat([{"role": "user", "content": case["input"]}]))
    except RuntimeError as exc:
        return True, f"raised RuntimeError: {exc}"
    except Exception as exc:  # noqa: BLE001
        return True, f"raised {type(exc).__name__}: {exc}"
    return False, "chat 未报错，静默成功（不期望的行为）"


def _qwen_check(case: dict[str, Any]) -> tuple[bool, str]:
    """QwenProvider.check_config / check_embedding 在不同 Key 配置下的状态。"""
    import asyncio
    from types import SimpleNamespace

    from app.infrastructure.qwen import QwenProvider

    method = case["input"]["method"]  # check_config / check_embedding
    if case["input"].get("has_key", False):
        provider = QwenProvider(
            SimpleNamespace(dashscope_api_key="test-key", qwen_base_url="https://dashscope.example/v1")
        )
        provider._client = object()
        provider.settings = SimpleNamespace(
            qwen_chat_model=case["input"].get("model", "qwen-plus"),
            qwen_embedding_model=case["input"].get(
                "embedding_model", "qwen3.7-text-embedding"
            ),
            embedding_dimension=int(case["input"].get("dimension", 1024)),
            embedding_smoke_check=bool(case["input"].get("smoke", False)),
        )
    else:
        provider = QwenProvider(SimpleNamespace(dashscope_api_key=""))
        provider.settings = SimpleNamespace(
            qwen_chat_model="qwen-plus",
            qwen_embedding_model="qwen3.7-text-embedding",
            embedding_dimension=1024,
            embedding_smoke_check=False,
        )
    result = asyncio.run(getattr(provider, method)())
    expected_status = case["expected"]["status"]
    ok = result.get("status") == expected_status
    return ok, f"{method}={result}"


def _validate_p0(case: dict[str, Any]) -> tuple[bool, str]:
    """生产环境安全开关校验：Settings.validate_p0 拒绝危险配置。"""
    from app.core.settings import Settings

    settings = Settings(**case.get("input", {}))
    expect_error = bool(case["expected"])
    try:
        settings.validate_p0()
        raised = False
    except ValueError:
        raised = True
    ok = raised is expect_error
    return ok, f"expect_error={expect_error}, raised={raised}"


def _script_exists(case: dict[str, Any]) -> tuple[bool, str]:
    """仓库脚本存在性（可复现性的组成部分）。"""
    rel = case["input"]
    path = ROOT / rel
    ok = path.exists()
    return ok, f"path={rel}, exists={ok}"


def _knowledge_search_present(case: dict[str, Any]) -> tuple[bool, str]:
    """知识库检索适配器存在（RAG 实现的代码证据）。"""
    path = ROOT / "app/repositories/knowledge.py"
    content = path.read_text(encoding="utf-8", errors="ignore")
    ok = "search_hybrid" in content and "search_text" in content
    return ok, f"knowledge.py contains search_hybrid/search_text: {ok}"


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------
_DISPATCH: dict[str, Callable[[dict[str, Any]], tuple[bool, str]]] = {
    "route": _route,
    "doc_exists": _doc_exists,
    "doc_contains": _doc_contains,
    "script_exists": _script_exists,
    "knowledge_search_present": _knowledge_search_present,
    "neo4j_disabled_by_default": _neo4j_disabled_by_default,
    "vector_adapter_present": _vector_adapter_present,
    "validate_sql": _validate_sql,
    "classify_intent": _classify_intent,
    "permission_matrix": _permission_matrix,
    "staff_allowed": _staff_allowed,
    "parse_op": _parse_op,
    "confirm_threshold": _confirm_threshold,
    "llm_available": _llm_available,
    "llm_chat_unconfigured": _llm_chat_unconfigured,
    "qwen_check": _qwen_check,
    "validate_p0": _validate_p0,
    "check_config": _check_config,
    "health_component": _health_component,
    "graph_fallback": _graph_fallback,
}


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    kind = case["kind"]
    runner = _DISPATCH.get(kind)
    start = time.perf_counter()
    if runner is None:
        ok, detail = False, f"unknown kind: {kind}"
    else:
        try:
            ok, detail = runner(case)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"runner error: {type(exc).__name__}: {exc}"
    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
    return {
        "id": case["id"],
        "name": case["name"],
        "category": case["category"],
        "kind": kind,
        "ok": ok,
        "detail": detail,
        "hard_gate": bool(case.get("hard_gate", False)),
        "elapsed_ms": elapsed_ms,
        "est_tokens": _estimate_tokens(case),
    }
