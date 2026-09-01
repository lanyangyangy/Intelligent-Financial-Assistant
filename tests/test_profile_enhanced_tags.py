from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.profile_domain.tag_governance import ProfileTagCode
from app.services.profile_calculation_service import ProfileCalculationService

pytestmark = pytest.mark.unit


def test_system_tags_cover_profile_and_asset_evidence():
    profile = SimpleNamespace(
        investment_goal="稳健增值",
        liquidity_preference="中等",
        investment_experience_years=3,
        annual_income=Decimal("240000"),
    )
    asset = SimpleNamespace(
        total_asset=Decimal("305000"),
        investable_asset=Decimal("260000"),
    )

    tags = ProfileCalculationService._build_system_tags(profile, asset)
    values = {tag.tag_code: tag.tag_value for tag in tags}

    assert values[ProfileTagCode.INVESTMENT_GOAL] == "STEADY_GROWTH"
    assert values[ProfileTagCode.LIQUIDITY_NEED] == "MEDIUM"
    assert values[ProfileTagCode.INVESTMENT_EXPERIENCE_YEARS] == 3
    assert values[ProfileTagCode.HOUSEHOLD_ANNUAL_INCOME] == 240000.0
    assert values[ProfileTagCode.TOTAL_ASSETS] == 305000.0
    assert values[ProfileTagCode.INVESTABLE_ASSETS] == 260000.0
    assert values[ProfileTagCode.ASSET_SCALE] == "100K_TO_500K"
