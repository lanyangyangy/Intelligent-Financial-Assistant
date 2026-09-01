from __future__ import annotations

# ruff: noqa: E402, I001

import argparse
import asyncio
import json
import math
import re
import sys
import time
import types
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from datasets import Dataset
from langchain_openai import ChatOpenAI
from openai import OpenAI

EVAL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.repositories.knowledge import KnowledgeRepository, _get_bge_reranker
from eval.rag_finance_metrics import DATASET_PATH

REPORT_JSON = EVAL_ROOT / "reports" / "pgvector_ragas_metrics.json"
REPORT_MD = EVAL_ROOT / "reports" / "pgvector_ragas_metrics.md"


def _install_ragas_vertexai_import_shim() -> None:
    """Ragas imports an optional VertexAI class that is absent in this env."""
    module_name = "langchain_community.chat_models.vertexai"
    if module_name in sys.modules:
        return
    module = types.ModuleType(module_name)

    class ChatVertexAI:  # pragma: no cover - import shim only
        pass

    module.ChatVertexAI = ChatVertexAI
    sys.modules[module_name] = module


@dataclass(frozen=True)
class LiveCase:
    case_id: str
    category: str
    question: str
    expected_terms: list[str]


class DashScopeRagasEmbeddings:
    def __init__(self) -> None:
        _install_ragas_vertexai_import_shim()
        from ragas.embeddings.base import BaseRagasEmbeddings

        settings = get_settings()

        class _Embeddings(BaseRagasEmbeddings):
            def __init__(self) -> None:
                super().__init__()
                self.settings = settings
                self.client = OpenAI(
                    api_key=settings.dashscope_api_key,
                    base_url=settings.qwen_base_url,
                )

            def embed_query(self, text: str) -> list[float]:
                return self.embed_documents([str(text)])[0]

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                clean_texts = ["" if text is None else str(text) for text in texts]
                vectors: list[list[float]] = []
                for start in range(0, len(clean_texts), 10):
                    batch = clean_texts[start : start + 10]
                    response = self.client.embeddings.create(
                        model=self.settings.qwen_embedding_model,
                        input=batch,
                        dimensions=self.settings.embedding_dimension,
                    )
                    vectors.extend(item.embedding for item in response.data)
                return vectors

            async def aembed_query(self, text: str) -> list[float]:
                return await asyncio.to_thread(self.embed_query, text)

            async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
                return await asyncio.to_thread(self.embed_documents, texts)

        self.instance = _Embeddings()


def load_cases(path: Path = DATASET_PATH, sample_count: int | None = None) -> list[LiveCase]:
    cases: list[LiveCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        cases.append(
            LiveCase(
                case_id=raw["case_id"],
                category=raw["category"],
                question=raw["question"],
                expected_terms=list(dict.fromkeys(raw["expected_terms"])),
            )
        )
    return cases[:sample_count] if sample_count else cases


def _reference(case: LiveCase) -> str:
    return "该问题的正确答案应包含以下关键信息：" + "；".join(case.expected_terms)


def _strong_entities(text: str) -> list[str]:
    return [
        match
        for match in re.findall(
            r"WM-[A-Z0-9-]+|CUST-\d{4}-\d{5}|AML-[A-Z]+-\d{3}|SUIT-\d{3}|恒信[\u4e00-\u9fff]+[0-9]{3}号",
            text,
            flags=re.IGNORECASE,
        )
    ]


def _rerank_contexts(question: str, contexts: list[tuple[str, float]], top_k: int) -> list[str]:
    entities = _strong_entities(question)
    rescored: list[tuple[float, int, str]] = []
    for index, (content, score) in enumerate(contexts):
        bonus = 0.0
        if entities:
            if any(entity in content for entity in entities):
                bonus += 1.0
            else:
                bonus -= 0.5
        rescored.append((score + bonus, index, content))
    rescored.sort(key=lambda item: (-item[0], item[1]))
    return [content for _score, _index, content in rescored[:top_k]]


def _search_category(case: LiveCase) -> str | None:
    if case.category in {"product", "faq"}:
        return case.category
    if case.category in {"policy", "risk_rule"}:
        return "policy"
    return None


async def _answer_with_context(provider: QwenProvider, question: str, contexts: list[str]) -> str:
    context_text = "\n\n".join(contexts)
    prompt = (
        "问题："
        + question
        + "\n\n检索上下文：\n"
        + context_text
        + "\n\n请只基于检索上下文回答。若上下文没有依据，回答“未检索到依据”。"
        "回答要求：一句话，包含关键数值或风险等级，不使用表情符号。"
    )
    return await provider.chat(
        [
            {
                "role": "system",
                "content": "你是财富管理投顾助手，回答必须忠实于给定上下文。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=256,
    )


def _term_recall(expected_terms: list[str], contexts: list[str]) -> float:
    merged = "\n".join(contexts)
    if not expected_terms:
        return 0.0
    return sum(1 for term in expected_terms if term in merged) / len(expected_terms)


def _top3_hit(expected_terms: list[str], contexts: list[str]) -> bool:
    return _term_recall(expected_terms, contexts[:3]) >= 0.8


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, max(0, math.ceil(len(sorted_values) * pct / 100) - 1))
    return sorted_values[index]


async def build_ragas_rows(cases: list[LiveCase], top_k: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = get_settings()
    database = Database(settings)
    provider = QwenProvider(settings)
    repo = KnowledgeRepository(database)
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    try:
        for index, case in enumerate(cases, start=1):
            start = time.perf_counter()
            query_embedding = (await provider.embed([case.question]))[0]
            results = await repo.search_hybrid(
                query=case.question,
                query_embedding=query_embedding,
                top_k=top_k,
                category=_search_category(case),
            )
            contexts = [chunk.content for chunk, _score in results]
            answer = await _answer_with_context(provider, case.question, contexts)
            elapsed_ms = (time.perf_counter() - start) * 1000
            latencies.append(elapsed_ms)
            rows.append(
                {
                    "case_id": case.case_id,
                    "category": case.category,
                    "user_input": case.question,
                    "question": case.question,
                    "response": answer,
                    "answer": answer,
                    "retrieved_contexts": contexts,
                    "contexts": contexts,
                    "reference": _reference(case),
                    "ground_truth": _reference(case),
                    "expected_terms": case.expected_terms,
                    "top3_hit": _top3_hit(case.expected_terms, contexts),
                    "context_term_recall": _term_recall(case.expected_terms, contexts),
                    "elapsed_ms": round(elapsed_ms, 3),
                }
            )
            print(
                f"[{index}/{len(cases)}] {case.case_id} "
                f"hit={rows[-1]['top3_hit']} recall={rows[-1]['context_term_recall']:.2f} "
                f"elapsed_ms={rows[-1]['elapsed_ms']}"
            )
    finally:
        await provider.close()
        await database.dispose()
    retrieval_summary = {
        "top3_hit_rate": round(
            sum(1 for row in rows if row["top3_hit"]) / len(rows), 4
        )
        if rows
        else 0.0,
        "context_term_recall": round(
            sum(row["context_term_recall"] for row in rows) / len(rows), 4
        )
        if rows
        else 0.0,
        "latency_ms": {
            "p50": round(_percentile(latencies, 50), 3),
            "p95": round(_percentile(latencies, 95), 3),
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
    }
    return rows, retrieval_summary


def run_ragas(rows: list[dict[str, Any]], batch_size: int) -> dict[str, float | None]:
    _install_ragas_vertexai_import_shim()
    from ragas import evaluate
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

    settings = get_settings()
    llm = ChatOpenAI(
        api_key=settings.dashscope_api_key,
        base_url=settings.qwen_base_url,
        model=settings.qwen_chat_model,
        temperature=0.0,
        max_retries=2,
        timeout=120,
    )
    embeddings = DashScopeRagasEmbeddings().instance
    dataset = Dataset.from_list(
        [
            {
                "user_input": row["user_input"],
                "response": row["response"],
                "retrieved_contexts": row["retrieved_contexts"],
                "reference": row["reference"],
            }
            for row in rows
        ]
    )
    result = evaluate(
        dataset,
        metrics=[context_precision, context_recall, faithfulness, answer_relevancy],
        llm=llm,
        embeddings=embeddings,
        raise_exceptions=False,
        show_progress=True,
        batch_size=batch_size,
    )
    values: dict[str, float | None] = {}
    raw = getattr(result, "_repr_dict", {})
    for key, value in raw.items():
        try:
            numeric = float(value)
            values[key] = None if math.isnan(numeric) else round(numeric, 4)
        except (TypeError, ValueError):
            values[key] = None
    return values


def write_report(report: dict[str, Any]) -> None:
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    retrieval = report["retrieval_metrics"]
    ragas_metrics = report["ragas_metrics"]
    lines = [
        "# pgvector + embedding + Ragas 评测报告",
        "",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 数据集：`{report['dataset']}`",
        f"- 用例数：`{report['total_cases']}`",
        f"- TopK：`{report['top_k']}`",
        f"- Embedding：`{report['embedding_model']}`",
        f"- Judge LLM：`{report['chat_model']}`",
        "",
        "## 检索指标",
        "",
        "| 指标 | 当前值 |",
        "| --- | ---: |",
        f"| Top3 候选命中率 | {retrieval['top3_hit_rate'] * 100:.1f}% |",
        f"| Context Term Recall | {retrieval['context_term_recall'] * 100:.1f}% |",
        f"| 端到端检索+生成 P50 / P95 | {retrieval['latency_ms']['p50']}ms / {retrieval['latency_ms']['p95']}ms |",
        "",
        "## Ragas 指标",
        "",
        "| 指标 | 当前值 |",
        "| --- | ---: |",
    ]
    for key in ["context_precision", "context_recall", "faithfulness", "answer_relevancy"]:
        value = ragas_metrics.get(key)
        display = "n/a" if value is None else f"{value * 100:.1f}%"
        lines.append(f"| {key} | {display} |")
    lines.extend(["", "## 样例", ""])
    for row in report["cases"][:8]:
        lines.append(
            f"- `{row['case_id']}` {row['user_input']} -> "
            f"Top3={'命中' if row['top3_hit'] else '未命中'}，"
            f"term_recall={row['context_term_recall'] * 100:.1f}%"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live pgvector + embedding + Ragas evaluation")
    parser.add_argument("--sample-count", type=int, default=30, help="Number of cases to evaluate")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--ragas-batch-size", type=int, default=4)
    args = parser.parse_args()

    settings = get_settings()
    cases = load_cases(sample_count=args.sample_count)
    rows, retrieval_metrics = asyncio.run(build_ragas_rows(cases, args.top_k))
    ragas_metrics = run_ragas(rows, batch_size=args.ragas_batch_size)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": DATASET_PATH.as_posix(),
        "total_cases": len(cases),
        "top_k": args.top_k,
        "embedding_model": settings.qwen_embedding_model,
        "chat_model": settings.qwen_chat_model,
        "retrieval_strategy": {
            "vector_weight": settings.hybrid_vector_weight,
            "keyword_weight": settings.hybrid_keyword_weight,
            "bm25_weight": settings.hybrid_bm25_weight,
            "exact_weight": settings.hybrid_exact_weight,
            "field_intent_weight": settings.hybrid_field_intent_weight,
            "entity_window_weight": settings.hybrid_entity_window_weight,
            "candidate_k": settings.hybrid_candidate_k,
            "bm25_pool_size": settings.hybrid_bm25_pool_size,
            "bge_reranker_enabled": settings.bge_reranker_enabled,
            "bge_reranker_model": settings.bge_reranker_model,
            "bge_reranker_allow_download": settings.bge_reranker_allow_download,
            "bge_reranker_backend": _get_bge_reranker(
                settings.bge_reranker_enabled,
                settings.bge_reranker_model,
                settings.bge_reranker_allow_download,
            ).backend,
        },
        "retrieval_metrics": retrieval_metrics,
        "ragas_metrics": ragas_metrics,
        "cases": rows,
    }
    write_report(report)
    print("retrieval_metrics=" + json.dumps(retrieval_metrics, ensure_ascii=False))
    print("ragas_metrics=" + json.dumps(ragas_metrics, ensure_ascii=False))
    print(f"report={REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
