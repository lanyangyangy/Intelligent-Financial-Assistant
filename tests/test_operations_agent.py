from __future__ import annotations

import pytest

from app.agents.operations_agent import BusinessOperatorAgent

pytestmark = pytest.mark.unit


def test_purchase_target_is_extracted_from_staff_instruction():
    assert BusinessOperatorAgent._customer_identifier(
        "帮零售投资者申购3000元的现金管理保本计划", {}
    ) == "零售投资者"


def test_explicit_customer_parameter_has_priority():
    assert BusinessOperatorAgent._customer_identifier(
        "帮客户A申购10万元产品", {"customer_identifier": "retail_investor_demo"}
    ) == "retail_investor_demo"
