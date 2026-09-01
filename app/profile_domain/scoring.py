from __future__ import annotations

from decimal import Decimal

from app.profile_domain.models import DimensionScores, RiskLevel


def calculate_total_score(scores: DimensionScores) -> Decimal:
    if not isinstance(scores, DimensionScores):
        raise ValueError("scores must be a DimensionScores instance")

    total_score = (
        scores.basic_attribute_score
        + scores.investment_experience_score
        + scores.risk_preference_score
        + scores.behavior_stability_score
    )
    if not total_score.is_finite() or not Decimal("0") <= total_score <= Decimal("100"):
        raise ValueError("total score must be between 0 and 100")
    return total_score


def classify_risk_level(total_score: Decimal) -> RiskLevel:
    if not isinstance(total_score, Decimal):
        raise ValueError("total_score must be a Decimal")
    if not total_score.is_finite() or not Decimal("0") <= total_score <= Decimal("100"):
        raise ValueError("total_score must be a finite Decimal between 0 and 100")

    # 投资者风险画像研判规则 第十一条：0-25 C1 / 26-40 C2 / 41-60 C3 / 61-80 C4 / 81-100 C5
    if total_score <= Decimal("25"):
        return RiskLevel.C1
    if total_score <= Decimal("40"):
        return RiskLevel.C2
    if total_score <= Decimal("60"):
        return RiskLevel.C3
    if total_score <= Decimal("80"):
        return RiskLevel.C4
    return RiskLevel.C5


def map_questionnaire_to_preference_score(score: int) -> Decimal:
    if isinstance(score, bool) or not isinstance(score, int):
        raise ValueError("questionnaire score must be an integer")
    decimal_score = Decimal(score)
    if not Decimal("20") <= decimal_score <= Decimal("100"):
        raise ValueError("questionnaire score must be between 20 and 100")

    if decimal_score <= Decimal("35"):
        return Decimal("5")
    if decimal_score <= Decimal("50"):
        return Decimal("10")
    if decimal_score <= Decimal("65"):
        return Decimal("15")
    if decimal_score <= Decimal("80"):
        return Decimal("20")
    return Decimal("25")
