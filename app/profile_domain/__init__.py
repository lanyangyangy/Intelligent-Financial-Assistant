"""Profile domain logic ported from the external 用户画像数据分析后端.

Pure Python domain: scoring, confidence, restrictions, tag governance.
Persistence is provided by the host app (app/models + repositories).
"""

from app.profile_domain.confidence import (
    calculate_dimension_confidence,
    calculate_recommendation_confidence,
    calculate_suitability_confidence,
    resolve_profile_status,
)
from app.profile_domain.models import (
    BusinessType,
    DimensionScores,
    EvidenceSourceType,
    ExtractionMethod,
    ProductRiskLevel,
    ProductRiskSnapshot,
    ProfileEvidence,
    ProfileSnapshot,
    ProfileStatus,
    ProfileStatusContext,
    RestrictionResult,
    RiskLevel,
    SuitabilityCheckResult,
    SuitabilityContext,
    SuitabilityDecision,
)
from app.profile_domain.restrictions import (
    DECISION_RULE_VERSION,
    decide_suitability,
    resolve_product_limit,
)
from app.profile_domain.scoring import (
    calculate_total_score,
    classify_risk_level,
    map_questionnaire_to_preference_score,
)

__all__ = [
    "BusinessType",
    "DECISION_RULE_VERSION",
    "DimensionScores",
    "EvidenceSourceType",
    "ExtractionMethod",
    "ProfileEvidence",
    "ProfileSnapshot",
    "ProfileStatus",
    "ProfileStatusContext",
    "ProductRiskLevel",
    "ProductRiskSnapshot",
    "RestrictionResult",
    "RiskLevel",
    "SuitabilityCheckResult",
    "SuitabilityContext",
    "SuitabilityDecision",
    "calculate_dimension_confidence",
    "calculate_recommendation_confidence",
    "calculate_suitability_confidence",
    "calculate_total_score",
    "classify_risk_level",
    "decide_suitability",
    "map_questionnaire_to_preference_score",
    "resolve_product_limit",
    "resolve_profile_status",
]
