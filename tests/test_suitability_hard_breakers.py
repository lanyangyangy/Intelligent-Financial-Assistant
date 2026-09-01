from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.profile_domain.models import (
    BusinessType,
    DimensionScores,
    ProductRiskLevel,
    ProductRiskSnapshot,
    ProfileSnapshot,
    ProfileStatus,
    RiskLevel,
    SuitabilityContext,
)
from app.profile_domain.restrictions import decide_suitability, resolve_product_limit


def _check_time() -> datetime:
    return datetime.now(UTC)


def test_under_age_is_blocked():
    result = resolve_product_limit(
        RiskLevel.C3,
        age=17,
        assessment_expires_at=_check_time() + timedelta(days=30),
        check_time=_check_time(),
        monthly_income=Decimal("10000"),
        total_assets=Decimal("150000"),
    )

    assert result.purchase_permission == "BLOCKED"
    assert "UNDER_AGE" in result.restriction_codes


def test_age_over_80_caps_product_limit():
    result = resolve_product_limit(
        RiskLevel.C4,
        age=81,
        assessment_expires_at=_check_time() + timedelta(days=30),
        check_time=_check_time(),
        monthly_income=Decimal("25000"),
        total_assets=Decimal("2000000"),
    )

    # 需求（2026-08-04）：>80 可购 R1/R2；R3 需人工复核；R4+ 不允许
    assert result.max_allowed_product_risk is ProductRiskLevel.R2
    assert result.purchase_permission == "APPROVAL_REQUIRED"
    assert result.restriction_codes == ["AGE_OVER_80_R2_LIMIT"]


def test_expired_assessment_is_blocked():
    result = resolve_product_limit(
        RiskLevel.C4,
        age=45,
        assessment_expires_at=_check_time() - timedelta(days=1),
        check_time=_check_time(),
        monthly_income=Decimal("30000"),
        total_assets=Decimal("800000"),
    )

    assert result.purchase_permission == "BLOCKED"
    assert "ASSESSMENT_EXPIRED" in result.restriction_codes


def test_age_over_80_uses_product_risk_tiers_instead_of_reviewing_everything():
    now = _check_time()
    profile = ProfileSnapshot(
        customer_id="senior-test",
        profile_version=1,
        dimension_scores=DimensionScores(
            basic_attribute_score=Decimal("20"),
            investment_experience_score=Decimal("20"),
            risk_preference_score=Decimal("20"),
            behavior_stability_score=Decimal("15"),
        ),
        model_risk_score=Decimal("75"),
        model_risk_level=RiskLevel.C4,
        suitability_confidence=Decimal("0.9"),
        recommendation_confidence=Decimal("0.9"),
        profile_status=ProfileStatus.NEEDS_REVIEW,
        max_allowed_product_risk=ProductRiskLevel.R2,
        restriction_codes=["AGE_OVER_80_R2_LIMIT"],
        assessment_expires_at=now + timedelta(days=30),
        generated_at=now,
        model_version="test",
        rule_version="test",
        trace_id="test-trace",
    )
    context = SuitabilityContext(
        profile=profile,
        age=81,
        monthly_income=Decimal("25000"),
        total_assets=Decimal("2000000"),
    )

    decisions = {}
    restriction_codes = {}
    for level in ProductRiskLevel:
        result = decide_suitability(
            context,
            ProductRiskSnapshot(
                product_id=f"product-{level.value}",
                risk_level=level,
                risk_version="test",
            ),
            BusinessType.PURCHASE,
            now,
        )
        decisions[level] = result.decision.value
        restriction_codes[level] = result.restriction_codes

    assert decisions[ProductRiskLevel.R1] == "PASS"
    assert decisions[ProductRiskLevel.R2] == "PASS"
    assert decisions[ProductRiskLevel.R3] == "REVIEW_REQUIRED"
    assert decisions[ProductRiskLevel.R4] == "REJECT"
    assert decisions[ProductRiskLevel.R5] == "REJECT"
    assert restriction_codes[ProductRiskLevel.R1] == ["AGE_OVER_80_R2_LIMIT"]
    assert restriction_codes[ProductRiskLevel.R2] == ["AGE_OVER_80_R2_LIMIT"]
    assert restriction_codes[ProductRiskLevel.R3] == [
        "AGE_OVER_80_R2_LIMIT",
        "AGE_OVER_80_R3_APPROVAL_REQUIRED",
    ]
    assert restriction_codes[ProductRiskLevel.R4] == [
        "AGE_OVER_80_R2_LIMIT",
        "AGE_OVER_80_R4_R5_REJECTED",
    ]
    assert restriction_codes[ProductRiskLevel.R5] == [
        "AGE_OVER_80_R2_LIMIT",
        "AGE_OVER_80_R4_R5_REJECTED",
    ]
