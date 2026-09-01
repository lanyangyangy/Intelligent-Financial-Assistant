from pathlib import Path

from eval.rag_finance_metrics import (
    build_cases,
    evaluate_cases,
    load_finance_records,
    write_dataset,
)


def test_build_cases_creates_broad_finance_rag_dataset() -> None:
    records = load_finance_records(Path("金融"))

    cases = build_cases(records, target_count=150)

    assert len(cases) >= 120
    assert {case.category for case in cases} >= {
        "product",
        "faq",
        "policy",
        "customer",
        "risk_rule",
    }
    assert all(case.expected_terms for case in cases)
    assert all(case.expected_record_id for case in cases)


def test_finance_rag_metrics_report_expected_core_metrics() -> None:
    records = load_finance_records(Path("金融"))
    cases = build_cases(records, target_count=120)

    report = evaluate_cases(records, cases, top_k=3)

    assert report["total_cases"] >= 120
    assert report["metrics"]["top3_hit_rate"] >= 0.8
    assert report["metrics"]["context_precision"] >= 0.75
    assert report["metrics"]["context_recall"] >= 0.8
    assert report["metrics"]["faithfulness_proxy"] >= 0.8
    assert report["metrics"]["answer_relevancy_proxy"] >= 0.8
    assert report["latency_ms"]["p95"] >= report["latency_ms"]["p50"]
    assert report["by_category"]["product"]["top3_hit_rate"] >= 0.8


def test_write_dataset_uses_jsonl_records(tmp_path: Path) -> None:
    records = load_finance_records(Path("金融"))
    cases = build_cases(records, target_count=20)
    output = tmp_path / "rag_finance_generated.jsonl"

    write_dataset(cases, output)

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(cases)
    assert lines[0].startswith("{")
