"""客户分层确认阈值测试（customer_tier.py 纯逻辑，unit）。

零售 1万/5万、金卡 2万/8万、私行 10万/50万。
"""
from __future__ import annotations

import pytest

from app.services.customer_tier import classify_tier

pytestmark = pytest.mark.unit


def test_tier_thresholds_mapping() -> None:
    """分层确认阈值：零售 1万/5万 → 私行 10万/50万。"""
    retail = classify_tier("普通", 0.0)
    assert (retail.confirmation_threshold_purchase, retail.confirmation_threshold_transfer) == (10_000, 50_000)
    private = classify_tier("私行", 0.0)
    assert (private.confirmation_threshold_purchase, private.confirmation_threshold_transfer) == (100_000, 500_000)
    gold = classify_tier("金卡", 0.0)
    assert (gold.confirmation_threshold_purchase, gold.confirmation_threshold_transfer) == (20_000, 80_000)
