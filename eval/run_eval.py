"""Agent 评测主入口：一条命令生成机器可读 JSON 和人可读 Markdown 报告。

用法（repo root，project venv）：
    .venv\\Scripts\\python.exe eval\\run_eval.py
    .venv\\Scripts\\python.exe eval\\run_eval.py --category nl2sql   # 单类别

输出：
    eval/reports/eval_report.json   机器可读指标
    eval/reports/eval_report.md     人可读报告

指标：各类别成功率、总体成功率、P50/P95 耗时、Token 与成本估算、
硬门禁（安全案例）单独统计，不被平均分掩盖。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# 保证从任意 cwd 启动都能 import 项目包与 eval 包
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.runners import COST_PER_1K_TOKENS, run_case  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
DATASETS_DIR = EVAL_DIR / "datasets"
REPORTS_DIR = EVAL_DIR / "reports"

CATEGORY_LABELS = {
    "supervisor_routing": "Supervisor 路由",
    "rag_graphrag": "RAG / GraphRAG",
    "nl2sql": "NL2SQL 安全护栏",
    "permissions_highrisk": "权限与高风险操作",
    "faults_degradation": "故障与降级",
}


def load_cases(category: str | None = None) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(DATASETS_DIR.glob("*.jsonl")):
        if category and category not in path.name:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            case["category"] = CATEGORY_LABELS.get(case["category"], case["category"])
            cases.append(case)
    return cases


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = max(0, min(len(sorted_values) - 1, int(len(sorted_values) * pct / 100)))
    return sorted_values[index]


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r["ok"])
    latency = [r["elapsed_ms"] for r in results]
    tokens = sum(r["est_tokens"] for r in results)

    by_category: dict[str, dict[str, Any]] = {}
    for r in results:
        cat = r["category"]
        bucket = by_category.setdefault(
            cat, {"total": 0, "passed": 0, "hard_gate_total": 0, "hard_gate_passed": 0}
        )
        bucket["total"] += 1
        bucket["passed"] += int(r["ok"])
        if r["hard_gate"]:
            bucket["hard_gate_total"] += 1
            bucket["hard_gate_passed"] += int(r["ok"])

    hard_gate_total = sum(
        b["hard_gate_total"] for b in by_category.values()
    )
    hard_gate_passed = sum(
        b["hard_gate_passed"] for b in by_category.values()
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_version": "2026-08-v1",
        "prompt_version": "n/a（离线确定性评测，不依赖 Prompt）",
        "model_version": "n/a（离线确定性评测，不调用 LLM）",
        "total": total,
        "passed": passed,
        "success_rate": round(passed / total, 4) if total else 0.0,
        "hard_gate_total": hard_gate_total,
        "hard_gate_passed": hard_gate_passed,
        "hard_gate_success_rate": (
            round(hard_gate_passed / hard_gate_total, 4) if hard_gate_total else 1.0
        ),
        "latency_ms": {
            "p50": round(_percentile(latency, 50), 2),
            "p95": round(_percentile(latency, 95), 2),
            "max": round(max(latency), 2) if latency else 0.0,
        },
        "tokens_estimated": tokens,
        "cost_estimated_yuan": round(tokens * COST_PER_1K_TOKENS / 1000, 4),
        "by_category": {
            cat: {
                "label": cat,
                "total": b["total"],
                "passed": b["passed"],
                "success_rate": round(b["passed"] / b["total"], 4) if b["total"] else 0.0,
                "hard_gate_total": b["hard_gate_total"],
                "hard_gate_passed": b["hard_gate_passed"],
            }
            for cat, b in by_category.items()
        },
        "cases": results,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Agent 评测报告")
    lines.append("")
    lines.append(f"- 生成时间：`{summary['generated_at']}`")
    lines.append(f"- 数据集版本：`{summary['dataset_version']}`")
    lines.append("- 评测方式：**离线确定性评测**（不调用真实 LLM / 数据库 / Redis）")
    lines.append("- 模型 / Prompt 版本：n/a（评测直接调用被测代码路径，100% 可复现）")
    lines.append("")
    lines.append("## 总体指标")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("| --- | --- |")
    lines.append(
        f"| 用例总数 | {summary['total']} |"
    )
    lines.append(
        f"| 通过 | {summary['passed']} |"
    )
    lines.append(
        f"| 成功率 | **{summary['success_rate'] * 100:.1f}%** |"
    )
    lines.append(
        f"| 安全硬门禁 | {summary['hard_gate_passed']}/{summary['hard_gate_total']} "
        f"（{summary['hard_gate_success_rate'] * 100:.1f}%）|"
    )
    lines.append(
        f"| 耗时 P50 / P95 | {summary['latency_ms']['p50']}ms / "
        f"{summary['latency_ms']['p95']}ms |"
    )
    lines.append(
        f"| Token 估算 | {summary['tokens_estimated']:,} |"
    )
    lines.append(
        f"| 成本估算（元） | {summary['cost_estimated_yuan']} |"
    )
    lines.append("")
    lines.append("> 硬门禁为安全类用例（越权拦截、SQL 注入拦截、降级正确性等），"
                 "任一失败即整体不合格，不能被平均分掩盖。")
    lines.append("")
    lines.append("## 分项指标")
    lines.append("")
    lines.append("| 类别 | 通过/总数 | 成功率 | 硬门禁 |")
    lines.append("| --- | --- | --- | --- |")
    for cat, b in summary["by_category"].items():
        lines.append(
            f"| {b['label']} | {b['passed']}/{b['total']} | "
            f"{b['success_rate'] * 100:.1f}% | "
            f"{b['hard_gate_passed']}/{b['hard_gate_total']} |"
        )
    lines.append("")
    lines.append("## 逐用例明细")
    lines.append("")
    for r in summary["cases"]:
        status = "✅" if r["ok"] else "❌"
        gate = " 🔒" if r["hard_gate"] else ""
        lines.append(
            f"- {status}{gate} `{r['id']}` {r['name']} "
            f"（{r['elapsed_ms']}ms，{r['est_tokens']} token）"
        )
        lines.append(f"  - {r['detail']}")
    lines.append("")
    lines.append("---")
    lines.append("生成命令：`.venv\\Scripts\\python.exe eval\\run_eval.py`")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 离线评测")
    parser.add_argument(
        "--category",
        choices=[p.stem for p in DATASETS_DIR.glob("*.jsonl")],
        help="只评测指定类别",
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="任一用例失败（含硬门禁失败）时以非零退出码退出，供 CI 使用",
    )
    args = parser.parse_args()

    cases = load_cases(args.category)
    if not cases:
        print("没有可评测的用例")
        return 1

    results = [run_case(case) for case in cases]
    summary = summarize(results)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "eval_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (REPORTS_DIR / "eval_report.md").write_text(
        render_markdown(summary), encoding="utf-8"
    )

    print(f"总用例 {summary['total']}，通过 {summary['passed']}，"
          f"成功率 {summary['success_rate'] * 100:.1f}%")
    print(f"硬门禁 {summary['hard_gate_passed']}/{summary['hard_gate_total']} "
          f"（{summary['hard_gate_success_rate'] * 100:.1f}%）")
    for cat, b in summary["by_category"].items():
        print(f"  {b['label']}: {b['passed']}/{b['total']} "
              f"({b['success_rate'] * 100:.1f}%)")
    print(f"报告已生成：{REPORTS_DIR / 'eval_report.md'}")
    if args.require_pass and summary["passed"] != summary["total"]:
        print("评测存在失败用例，--require-pass 生效，退出码 1")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
