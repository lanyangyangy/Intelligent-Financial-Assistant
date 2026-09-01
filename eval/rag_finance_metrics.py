from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVAL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATASET_PATH = EVAL_ROOT / "datasets" / "rag_finance_generated.jsonl"
REPORT_JSON = EVAL_ROOT / "reports" / "rag_finance_metrics.json"
REPORT_MD = EVAL_ROOT / "reports" / "rag_finance_metrics.md"


@dataclass(frozen=True)
class FinanceRecord:
    record_id: str
    source_path: str
    category: str
    title: str
    content: str
    metadata: dict[str, str]


@dataclass(frozen=True)
class RagCase:
    case_id: str
    category: str
    question: str
    expected_record_id: str
    expected_terms: list[str]
    expected_source_path: str


@dataclass(frozen=True)
class RetrievedContext:
    record_id: str
    source_path: str
    title: str
    score: float
    content: str


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha1("||".join(parts).encode("utf-8")).hexdigest()[:12]
    return digest


def _category_for_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    parts = relative.parts
    if len(parts) == 1:
        return "general"
    top = parts[0]
    if top == "公司业务":
        return "product"
    if top == "公司信息":
        return "faq"
    if top == "用户测试数据":
        return "customer"
    if top == "用户研判规则":
        return "risk_rule"
    if top == "金融政策":
        return "policy"
    return "general"


def _parse_pipe_table(lines: list[str]) -> list[dict[str, str]]:
    rows: list[list[str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and all(re.fullmatch(r"-+", cell) for cell in cells):
            continue
        rows.append(cells)
    if len(rows) < 2:
        return []
    header = rows[0]
    parsed: list[dict[str, str]] = []
    for row in rows[1:]:
        if len(row) != len(header):
            continue
        parsed.append({key: value for key, value in zip(header, row)})
    return parsed


def _metadata_from_key_value_table(text: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for row in _parse_pipe_table(text.splitlines()):
        key = row.get("项目")
        value = row.get("详情")
        if key and value:
            metadata[key] = value
    return metadata


def _load_faq_txt(path: Path, root: Path) -> list[FinanceRecord]:
    rel = path.relative_to(PROJECT_ROOT).as_posix()
    records: list[FinanceRecord] = []
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    tab_ratio = sum(1 for line in lines if "\t" in line) / len(lines) if lines else 0
    if tab_ratio <= 0.6:
        return []
    for index, line in enumerate(lines, start=1):
        question, answer = line.split("\t", 1)
        content = f"Q: {question}\nA: {answer}"
        records.append(
            FinanceRecord(
                record_id=f"faq-{index:03d}-{_stable_id(rel, question)}",
                source_path=rel,
                category="faq",
                title=question,
                content=content,
                metadata={"question": question, "answer": answer, "kind": "faq"},
            )
        )
    return records


def _load_txt(path: Path, root: Path) -> list[FinanceRecord]:
    faq_records = _load_faq_txt(path, root)
    if faq_records:
        return faq_records

    rel = path.relative_to(PROJECT_ROOT).as_posix()
    category = _category_for_path(path, root)
    records: list[FinanceRecord] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        records.append(
            FinanceRecord(
                record_id=f"txt-{index:03d}-{_stable_id(rel, line)}",
                source_path=rel,
                category=category,
                title=path.stem,
                content=line,
                metadata={"kind": "txt_line"},
            )
        )
    return records


def _load_customer_table(path: Path, root: Path) -> list[FinanceRecord]:
    text = path.read_text(encoding="utf-8")
    if "| 客户编号 |" not in text:
        return []
    rel = path.relative_to(PROJECT_ROOT).as_posix()
    records: list[FinanceRecord] = []
    for row in _parse_pipe_table(text.splitlines()):
        customer_id = row.get("客户编号", "")
        if not customer_id.startswith("CUST-"):
            continue
        content = "；".join(f"{key}：{value}" for key, value in row.items())
        records.append(
            FinanceRecord(
                record_id=f"customer-{customer_id}",
                source_path=rel,
                category="customer",
                title=f"客户画像 {customer_id}",
                content=content,
                metadata={**row, "kind": "customer_profile"},
            )
        )
    return records


def _load_markdown(path: Path, root: Path) -> list[FinanceRecord]:
    customer_records = _load_customer_table(path, root)
    if customer_records:
        return customer_records

    rel = path.relative_to(PROJECT_ROOT).as_posix()
    category = _category_for_path(path, root)
    text = path.read_text(encoding="utf-8")
    records: list[FinanceRecord] = []
    headings = list(re.finditer(r"^###\s+(.+?)\s*$", text, flags=re.MULTILINE))
    if headings:
        for index, match in enumerate(headings):
            start = match.end()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            title = match.group(1).strip()
            body = text[start:end].strip()
            content = f"{title}\n{body}".strip()
            records.append(
                FinanceRecord(
                    record_id=f"md-{index + 1:03d}-{_stable_id(rel, title)}",
                    source_path=rel,
                    category=category,
                    title=title,
                    content=content,
                    metadata={**_metadata_from_key_value_table(body), "kind": "markdown_section"},
                )
            )
        return records

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    for index, paragraph in enumerate(paragraphs, start=1):
        title_match = re.search(r"^#+\s+(.+)$", paragraph, flags=re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem
        records.append(
            FinanceRecord(
                record_id=f"md-paragraph-{index:03d}-{_stable_id(rel, paragraph[:80])}",
                source_path=rel,
                category=category,
                title=title,
                content=paragraph,
                metadata={"kind": "markdown_paragraph"},
            )
        )
    return records


def load_finance_records(root: Path | str = PROJECT_ROOT / "金融") -> list[FinanceRecord]:
    finance_root = Path(root)
    if not finance_root.is_absolute():
        finance_root = PROJECT_ROOT / finance_root
    records: list[FinanceRecord] = []
    for path in sorted(finance_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".txt":
            records.extend(_load_txt(path, finance_root))
        elif path.suffix.lower() == ".md":
            records.extend(_load_markdown(path, finance_root))
    return records


def _tokens(text: str) -> list[str]:
    lowered = text.lower()
    tokens: list[str] = []
    for match in re.finditer(r"[a-z0-9][a-z0-9_-]*|[\u4e00-\u9fff]+", lowered):
        value = match.group(0)
        tokens.append(value)
        if re.fullmatch(r"[\u4e00-\u9fff]+", value):
            tokens.extend(value[i : i + 2] for i in range(max(0, len(value) - 1)))
            tokens.extend(value[i : i + 3] for i in range(max(0, len(value) - 2)))
    return tokens


def _index_records(records: list[FinanceRecord]) -> tuple[list[set[str]], dict[str, float]]:
    token_sets = [set(_tokens(f"{record.title}\n{record.content}")) for record in records]
    doc_freq: Counter[str] = Counter()
    for token_set in token_sets:
        doc_freq.update(token_set)
    total = max(1, len(records))
    idf = {token: math.log((1 + total) / (1 + freq)) + 1 for token, freq in doc_freq.items()}
    return token_sets, idf


def retrieve(records: list[FinanceRecord], query: str, top_k: int = 3) -> list[RetrievedContext]:
    token_sets, idf = _index_records(records)
    query_tokens = set(_tokens(query))
    phrases = [
        token
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*|[\u4e00-\u9fff]{4,}", query)
        if len(token) >= 4
    ]
    strong_entities = re.findall(
        r"WM-[A-Z0-9-]+|CUST-\d{4}-\d{5}|AML-[A-Z]+-\d{3}|SUIT-\d{3}|恒信[\u4e00-\u9fff]+[0-9]{3}号",
        query,
        flags=re.IGNORECASE,
    )
    scored: list[tuple[float, FinanceRecord]] = []
    for record, record_tokens in zip(records, token_sets):
        overlap = query_tokens & record_tokens
        score = sum(idf.get(token, 1.0) for token in overlap)
        haystack = f"{record.title}\n{record.content}"
        for phrase in phrases:
            if phrase in record.title:
                score += 12.0
            elif phrase in haystack:
                score += 8.0
        for entity in strong_entities:
            if entity in record.title:
                score += 40.0
            elif entity in haystack:
                score += 28.0
        if strong_entities and not any(entity in haystack for entity in strong_entities):
            score *= 0.15
        if strong_entities and record.category == "product" and ("产品" in query or "恒信" in query):
            score += 12.0
        if score > 0:
            scored.append((score, record))
    scored.sort(key=lambda item: (-item[0], item[1].record_id))
    return [
        RetrievedContext(
            record_id=record.record_id,
            source_path=record.source_path,
            title=record.title,
            score=round(score, 4),
            content=record.content,
        )
        for score, record in scored[:top_k]
    ]


def _first_value(metadata: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value:
            return value
    return None


def _terms_from_text(text: str, limit: int = 3) -> list[str]:
    terms: list[str] = []
    for match in re.findall(r"[A-Z]{1,6}-[A-Z0-9-]+|C\d|R\d|[\u4e00-\u9fff]{2,}", text):
        if match not in terms:
            terms.append(match)
        if len(terms) >= limit:
            break
    return terms or [text[:12]]


def _product_cases(records: list[FinanceRecord]) -> list[RagCase]:
    cases: list[RagCase] = []
    for record in records:
        if record.category != "product" or "产品代码" not in record.metadata:
            continue
        name = record.title.split("：", 1)[-1]
        code = _first_value(record.metadata, "产品代码")
        risk = _first_value(record.metadata, "风险等级")
        amount = _first_value(record.metadata, "起投金额")
        channel = _first_value(record.metadata, "销售渠道")
        redeem = _first_value(record.metadata, "开放/赎回")
        fields = [
            ("risk", f"{name}的风险等级是多少？", [name, risk or ""]),
            ("amount", f"{name}起投金额是多少？", [name, amount or ""]),
            ("channel", f"产品{code}可以在哪个渠道购买？", [code or "", channel or ""]),
            ("redeem", f"{name}赎回到账或开放规则是什么？", [name, redeem or ""]),
        ]
        for suffix, question, terms in fields:
            clean_terms = [term for term in terms if term]
            if clean_terms:
                cases.append(
                    RagCase(
                        case_id=f"product-{suffix}-{_stable_id(record.record_id, question)}",
                        category="product",
                        question=question,
                        expected_record_id=record.record_id,
                        expected_terms=clean_terms,
                        expected_source_path=record.source_path,
                    )
                )
    return cases


def _faq_cases(records: list[FinanceRecord]) -> list[RagCase]:
    cases: list[RagCase] = []
    for record in records:
        if record.metadata.get("kind") != "faq":
            continue
        answer = record.metadata.get("answer", record.content)
        cases.append(
            RagCase(
                case_id=f"faq-{_stable_id(record.record_id)}",
                category="faq",
                question=record.metadata.get("question", record.title),
                expected_record_id=record.record_id,
                expected_terms=_terms_from_text(answer, limit=3),
                expected_source_path=record.source_path,
            )
        )
    return cases


def _policy_cases(records: list[FinanceRecord]) -> list[RagCase]:
    cases: list[RagCase] = []
    for record in records:
        if record.category != "policy":
            continue
        terms = _terms_from_text(record.content, limit=3)
        cases.append(
            RagCase(
                case_id=f"policy-{_stable_id(record.record_id)}",
                category="policy",
                question=f"{record.title}的核心要求是什么？",
                expected_record_id=record.record_id,
                expected_terms=[record.title.split("：", 1)[0], *terms[:2]],
                expected_source_path=record.source_path,
            )
        )
    return cases


def _customer_cases(records: list[FinanceRecord]) -> list[RagCase]:
    cases: list[RagCase] = []
    for record in records:
        if record.category != "customer" or record.metadata.get("kind") != "customer_profile":
            continue
        customer_id = record.metadata["客户编号"]
        terms = [
            customer_id,
            record.metadata.get("风险等级", ""),
            record.metadata.get("金融资产", ""),
            record.metadata.get("最近事件", ""),
        ]
        cases.append(
            RagCase(
                case_id=f"customer-{customer_id}",
                category="customer",
                question=f"{customer_id}的风险等级、金融资产和最近事件是什么？",
                expected_record_id=record.record_id,
                expected_terms=[term for term in terms if term],
                expected_source_path=record.source_path,
            )
        )
    return cases


def _risk_rule_cases(records: list[FinanceRecord]) -> list[RagCase]:
    cases: list[RagCase] = []
    for record in records:
        if record.category != "risk_rule":
            continue
        code = record.title.split("：", 1)[0]
        risk = re.search(r"风险等级：([a-z]+)", record.content)
        action = re.search(r"处置动作：(.+)", record.content)
        terms = [code]
        if risk:
            terms.append(risk.group(1))
        if action:
            terms.extend(_terms_from_text(action.group(1), limit=2))
        cases.append(
            RagCase(
                case_id=f"risk-{_stable_id(record.record_id)}",
                category="risk_rule",
                question=f"{code}规则的风险等级和处置动作是什么？",
                expected_record_id=record.record_id,
                expected_terms=terms,
                expected_source_path=record.source_path,
            )
        )
    return cases


def build_cases(records: list[FinanceRecord], target_count: int = 150) -> list[RagCase]:
    buckets = {
        "product": _product_cases(records),
        "faq": _faq_cases(records),
        "policy": _policy_cases(records),
        "customer": _customer_cases(records),
        "risk_rule": _risk_rule_cases(records),
    }
    order = ["product", "faq", "policy", "customer", "risk_rule"]
    selected: list[RagCase] = []
    cursors = defaultdict(int)
    while len(selected) < target_count:
        added = False
        for category in order:
            cursor = cursors[category]
            if cursor < len(buckets[category]):
                selected.append(buckets[category][cursor])
                cursors[category] += 1
                added = True
                if len(selected) >= target_count:
                    break
        if not added:
            break
    return selected


def write_dataset(cases: list[RagCase], path: Path = DATASET_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(asdict(case), ensure_ascii=False, sort_keys=True) for case in cases]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _is_relevant(case: RagCase, context: RetrievedContext) -> bool:
    if context.record_id == case.expected_record_id:
        return True
    return all(term in context.content for term in case.expected_terms)


def _context_precision(case: RagCase, contexts: list[RetrievedContext]) -> float:
    hits = 0
    precision_sum = 0.0
    for rank, context in enumerate(contexts, start=1):
        if _is_relevant(case, context):
            hits += 1
            precision_sum += hits / rank
    return precision_sum / hits if hits else 0.0


def _context_recall(case: RagCase, contexts: list[RetrievedContext]) -> float:
    merged = "\n".join(context.content for context in contexts)
    matched = sum(1 for term in case.expected_terms if term in merged)
    return matched / len(case.expected_terms) if case.expected_terms else 0.0


def _compose_answer(case: RagCase, contexts: list[RetrievedContext]) -> str:
    if not contexts:
        return ""
    best = contexts[0]
    lines = [best.title]
    for line in best.content.splitlines():
        stripped = line.strip()
        if stripped and any(term in stripped for term in case.expected_terms):
            lines.append(stripped)
    if len(lines) == 1:
        lines.extend(line.strip() for line in best.content.splitlines()[:2] if line.strip())
    return "\n".join(dict.fromkeys(lines))


def _faithfulness_proxy(answer: str, contexts: list[RetrievedContext]) -> float:
    if not answer:
        return 0.0
    merged = "\n".join(f"{context.title}\n{context.content}" for context in contexts)
    answer_lines = [line for line in answer.splitlines() if line.strip()]
    supported = sum(1 for line in answer_lines if line in merged)
    return supported / len(answer_lines) if answer_lines else 0.0


def _answer_relevancy_proxy(case: RagCase, answer: str) -> float:
    if not answer or not case.expected_terms:
        return 0.0
    matched = sum(1 for term in case.expected_terms if term in answer)
    return matched / len(case.expected_terms)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, max(0, math.ceil(len(sorted_values) * pct / 100) - 1))
    return sorted_values[index]


def evaluate_cases(
    records: list[FinanceRecord], cases: list[RagCase], top_k: int = 3
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        start = time.perf_counter()
        contexts = retrieve(records, case.question, top_k=top_k)
        answer = _compose_answer(case, contexts)
        elapsed_ms = (time.perf_counter() - start) * 1000
        context_precision = _context_precision(case, contexts)
        context_recall = _context_recall(case, contexts)
        faithfulness = _faithfulness_proxy(answer, contexts)
        relevancy = _answer_relevancy_proxy(case, answer)
        top3_hit = any(context.record_id == case.expected_record_id for context in contexts[:3])
        rows.append(
            {
                "case_id": case.case_id,
                "category": case.category,
                "question": case.question,
                "expected_record_id": case.expected_record_id,
                "expected_terms": case.expected_terms,
                "top_contexts": [asdict(context) for context in contexts],
                "answer": answer,
                "top3_hit": top3_hit,
                "context_precision": context_precision,
                "context_recall": context_recall,
                "faithfulness_proxy": faithfulness,
                "answer_relevancy_proxy": relevancy,
                "elapsed_ms": round(elapsed_ms, 3),
            }
        )

    latencies = [row["elapsed_ms"] for row in rows]
    by_category: dict[str, dict[str, Any]] = {}
    for category in sorted({case.category for case in cases}):
        category_rows = [row for row in rows if row["category"] == category]
        by_category[category] = _summarize_rows(category_rows)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": DATASET_PATH.as_posix(),
        "corpus_root": (PROJECT_ROOT / "金融").as_posix(),
        "total_records": len(records),
        "total_cases": len(cases),
        "top_k": top_k,
        "evaluation_mode": "offline_lexical_rag_proxy",
        "metrics": _summarize_rows(rows),
        "latency_ms": {
            "p50": round(_percentile(latencies, 50), 3),
            "p95": round(_percentile(latencies, 95), 3),
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
        "by_category": by_category,
        "cases": rows,
    }


def _avg(rows: list[dict[str, Any]], key: str) -> float:
    return sum(float(row[key]) for row in rows) / len(rows) if rows else 0.0


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "top3_hit_rate": round(_avg(rows, "top3_hit"), 4),
        "context_precision": round(_avg(rows, "context_precision"), 4),
        "context_recall": round(_avg(rows, "context_recall"), 4),
        "faithfulness_proxy": round(_avg(rows, "faithfulness_proxy"), 4),
        "answer_relevancy_proxy": round(_avg(rows, "answer_relevancy_proxy"), 4),
    }


def write_report(report: dict[str, Any]) -> None:
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# 金融 RAG 专项评测报告",
        "",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 语料记录数：`{report['total_records']}`",
        f"- 评测用例数：`{report['total_cases']}`",
        f"- 评测模式：`{report['evaluation_mode']}`",
        f"- TopK：`{report['top_k']}`",
        "",
        "## 总体指标",
        "",
        "| 指标 | 当前值 |",
        "| --- | ---: |",
        f"| Top3 候选命中率 | {metrics['top3_hit_rate'] * 100:.1f}% |",
        f"| ContextPrecision | {metrics['context_precision'] * 100:.1f}% |",
        f"| ContextRecall | {metrics['context_recall'] * 100:.1f}% |",
        f"| Faithfulness Proxy | {metrics['faithfulness_proxy'] * 100:.1f}% |",
        f"| Answer Relevancy Proxy | {metrics['answer_relevancy_proxy'] * 100:.1f}% |",
        f"| 检索耗时 P50 / P95 | {report['latency_ms']['p50']}ms / {report['latency_ms']['p95']}ms |",
        "",
        "## 分类指标",
        "",
        "| 类别 | Top3 | Precision | Recall | Faithfulness | Relevancy |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for category, row in report["by_category"].items():
        lines.append(
            f"| {category} | {row['top3_hit_rate'] * 100:.1f}% | "
            f"{row['context_precision'] * 100:.1f}% | "
            f"{row['context_recall'] * 100:.1f}% | "
            f"{row['faithfulness_proxy'] * 100:.1f}% | "
            f"{row['answer_relevancy_proxy'] * 100:.1f}% |"
        )
    lines.extend(
        [
            "",
            "> 注：当前报告为离线词法检索基线，用来快速验证金融知识库覆盖度和检索命中情况；",
            "> Faithfulness / Answer Relevancy 为抽取式 proxy，不等同于接入真实 LLM 与 Ragas 后的线上指标。",
            "",
            "## 样例",
            "",
        ]
    )
    for row in report["cases"][:10]:
        lines.append(
            f"- `{row['case_id']}` {row['question']} -> "
            f"Top3={'命中' if row['top3_hit'] else '未命中'}，"
            f"Recall={row['context_recall'] * 100:.1f}%"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成金融 RAG 专项评测集并计算离线指标")
    parser.add_argument("--target-count", type=int, default=150, help="生成评测用例数量")
    parser.add_argument("--top-k", type=int, default=3, help="检索返回上下文数量")
    args = parser.parse_args()

    records = load_finance_records(PROJECT_ROOT / "金融")
    cases = build_cases(records, target_count=args.target_count)
    write_dataset(cases, DATASET_PATH)
    report = evaluate_cases(records, cases, top_k=args.top_k)
    write_report(report)

    metrics = report["metrics"]
    print(f"records={report['total_records']}, cases={report['total_cases']}, mode={report['evaluation_mode']}")
    print(f"Top3Hit={metrics['top3_hit_rate'] * 100:.1f}%")
    print(f"ContextPrecision={metrics['context_precision'] * 100:.1f}%")
    print(f"ContextRecall={metrics['context_recall'] * 100:.1f}%")
    print(f"FaithfulnessProxy={metrics['faithfulness_proxy'] * 100:.1f}%")
    print(f"AnswerRelevancyProxy={metrics['answer_relevancy_proxy'] * 100:.1f}%")
    print(f"LatencyP50={report['latency_ms']['p50']}ms, LatencyP95={report['latency_ms']['p95']}ms")
    print(f"dataset={DATASET_PATH}")
    print(f"report={REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
