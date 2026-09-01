from app.repositories.knowledge import (
    BgeReranker,
    _bm25_rank,
    _entity_window_intent_score,
    _extract_strong_entities,
    _field_intent_score,
    _rrf_fuse,
)


def test_extract_strong_entities_keeps_finance_ids_and_product_names() -> None:
    product_name = "\u6052\u4fe1\u77ed\u503a\u589e\u5f3a001\u53f7"
    entities = _extract_strong_entities(
        f"please explain AML-RW-001\u89c4\u5219 and check WM-3361-20260001 {product_name} for CUST-2026-00001"
    )

    assert entities == [
        "AML-RW-001",
        "WM-3361-20260001",
        product_name,
        "CUST-2026-00001",
    ]


def test_bm25_rank_prefers_exact_rule_context() -> None:
    documents = {
        "rule-1": "AML-RW-001 seven day high frequency rule threshold review",
        "rule-2": "AML-RW-002 quick in quick out transfer review threshold",
        "product": "WM-3361-20260001 R2 minimum amount 20000",
    }

    ranked = _bm25_rank("AML-RW-001 review threshold", documents)

    assert ranked[0][0] == "rule-1"
    assert ranked[0][1] > ranked[1][1]


def test_rrf_fuse_promotes_entity_exact_recall() -> None:
    scores = _rrf_fuse(
        [
            ["semantic-close", "exact-rule"],
            ["keyword-close", "exact-rule"],
            ["exact-rule"],
        ],
        weights=[1.0, 0.8, 2.5],
    )

    assert max(scores, key=scores.get) == "exact-rule"


def test_field_intent_score_prefers_customer_asset_event_fields() -> None:
    query = "CUST-2026-00007\u7684\u98ce\u9669\u7b49\u7ea7\u3001\u91d1\u878d\u8d44\u4ea7\u548c\u6700\u8fd1\u4e8b\u4ef6"
    complete_profile = "| CUST-2026-00007 | C3 | 305,000 \u5143 | \u4e2d\u98ce\u9669\u9884\u8b66 |"
    compact_profile = "CUST-2026-00007 \u98ce\u9669\u7b49\u7ea7 C3 \u91d1\u878d\u8d44\u4ea7 710,000 \u5143"

    assert _field_intent_score(query, complete_profile) > _field_intent_score(query, compact_profile)


def test_entity_window_intent_score_requires_value_near_customer_id() -> None:
    query = "CUST-2026-00020\u7684\u98ce\u9669\u7b49\u7ea7\u3001\u91d1\u878d\u8d44\u4ea7\u548c\u6700\u8fd1\u4e8b\u4ef6"
    correct_row = "| CUST-2026-00020 | C1 | 760,000 \u5143 | \u73b0\u91d1\u7ba1\u7406 | \u65e0 |"
    wrong_window = "| CUST-2026-00019 | C5 | 725,000 \u5143 | \u5927\u989d\u7533\u8d2d\u590d\u6838 | " + (
        "x" * 260
    ) + "| CUST-2026-00020 | C1 |"

    entities = ["CUST-2026-00020"]

    assert _entity_window_intent_score(query, correct_row, entities) > _entity_window_intent_score(
        query,
        wrong_window,
        entities,
    )


def test_disabled_bge_reranker_preserves_candidates() -> None:
    reranker = BgeReranker(enabled=False, model_name="BAAI/bge-reranker-base")
    candidates = [("chunk-a", 0.3), ("chunk-b", 0.2)]

    assert reranker.rerank("query", candidates, content_getter=lambda item: item[0]) == candidates
