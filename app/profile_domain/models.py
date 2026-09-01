from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RiskLevel(StrEnum):
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"
    C5 = "C5"


class ProfileStatus(StrEnum):
    VALID = "VALID"
    PROVISIONAL = "PROVISIONAL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    INCOMPLETE = "INCOMPLETE"
    EXPIRED = "EXPIRED"


class ProfileStatusContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_expired: bool = False
    has_required_data: bool = True
    has_critical_conflict: bool = False
    suitability_confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    minimum_suitability_confidence: Decimal = Field(
        default=Decimal("0.80"),
        ge=Decimal("0"),
        le=Decimal("1"),
    )


class RestrictionCode(StrEnum):
    NONE = "NONE"
    UNDER_AGE = "UNDER_AGE"
    AGE_OVER_80_R2_LIMIT = "AGE_OVER_80_R2_LIMIT"
    AGE_OVER_80_R3_APPROVAL_REQUIRED = "AGE_OVER_80_R3_APPROVAL_REQUIRED"
    AGE_OVER_80_R4_R5_REJECTED = "AGE_OVER_80_R4_R5_REJECTED"
    ASSESSMENT_EXPIRED = "ASSESSMENT_EXPIRED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
    PROFILE_INCOMPLETE = "PROFILE_INCOMPLETE"


class ProductRiskLevel(StrEnum):
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"
    R5 = "R5"


class BusinessType(StrEnum):
    ACCOUNT_OPENING = "ACCOUNT_OPENING"
    PURCHASE = "PURCHASE"
    ADDITIONAL_PURCHASE = "ADDITIONAL_PURCHASE"
    NEW_RECURRING_INVESTMENT = "NEW_RECURRING_INVESTMENT"
    QUERY = "QUERY"
    CANCEL = "CANCEL"
    REDEEM = "REDEEM"


class SuitabilityDecision(StrEnum):
    PASS = "PASS"
    REJECT = "REJECT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    PROFILE_REFRESH_REQUIRED = "PROFILE_REFRESH_REQUIRED"


class RestrictionEffect(StrEnum):
    BLOCK = "BLOCK"
    REVIEW = "REVIEW"


class EvidenceSourceType(StrEnum):
    QUESTIONNAIRE = "QUESTIONNAIRE"
    KYC = "KYC"
    SYSTEM_BEHAVIOR = "SYSTEM_BEHAVIOR"
    USER_STATED = "USER_STATED"
    MANAGER_ENTERED = "MANAGER_ENTERED"
    EXTERNAL_VERIFIED = "EXTERNAL_VERIFIED"
    DEFAULT = "DEFAULT"


class ExtractionMethod(StrEnum):
    DIRECT = "DIRECT"
    RULE = "RULE"
    IMPORT = "IMPORT"
    AI = "AI"
    MANUAL = "MANUAL"


class EvidenceStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CONFLICTED = "CONFLICTED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class ProfileEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: EvidenceSourceType
    extraction_method: ExtractionMethod
    field_validated: bool = True
    freshness_decay: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    status: EvidenceStatus = EvidenceStatus.ACTIVE
    evidence_ref: str = Field(min_length=1)


class DimensionScores(BaseModel):
    basic_attribute_score: Decimal = Field(ge=Decimal("0"), le=Decimal("25"))
    investment_experience_score: Decimal = Field(ge=Decimal("0"), le=Decimal("25"))
    risk_preference_score: Decimal = Field(ge=Decimal("0"), le=Decimal("30"))
    behavior_stability_score: Decimal = Field(ge=Decimal("0"), le=Decimal("20"))


class ProfileSnapshot(BaseModel):
    customer_id: int | str
    profile_version: int = Field(ge=1)
    dimension_scores: DimensionScores
    model_risk_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    model_risk_level: RiskLevel
    suitability_confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    recommendation_confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    profile_status: ProfileStatus
    max_allowed_product_risk: ProductRiskLevel | None
    restriction_codes: list[str] = Field(default_factory=list)
    profile_tags: list[dict] = Field(default_factory=list)
    assessment_valid_until: date | None = None
    assessment_expires_at: datetime | None = None
    generated_at: datetime
    model_version: str = Field(min_length=1)
    rule_version: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)

    @field_validator("assessment_expires_at", "generated_at")
    @classmethod
    def datetimes_must_be_timezone_aware(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value


class ExternalRestriction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    effect: RestrictionEffect
    business_types: frozenset[BusinessType] = Field(min_length=1)


class RestrictionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_allowed_product_risk: ProductRiskLevel | None
    purchase_permission: str = Field(pattern=r"^(ALLOWED|BLOCKED|APPROVAL_REQUIRED)$")
    query_permission: str = Field(default="ALLOWED", pattern=r"^(ALLOWED|BLOCKED)$")
    redeem_permission: str = Field(default="ALLOWED", pattern=r"^(ALLOWED|BLOCKED)$")
    restriction_codes: list[str] = Field(default_factory=list)


class QuestionnaireAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    option_id: str = Field(min_length=1)


class QuestionnaireSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int | str
    questionnaire_version: str = Field(min_length=1, max_length=32)
    answers: tuple[QuestionnaireAnswer, ...] = Field(min_length=1)
    questionnaire_score: Decimal | None = Field(
        default=None, ge=Decimal("20"), le=Decimal("100")
    )
    completed_at: datetime

    @field_validator("completed_at")
    @classmethod
    def completed_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("completed_at must be timezone-aware")
        return value


class ProductRiskSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    risk_level: ProductRiskLevel
    risk_version: str = Field(min_length=1)


class ProfileCalculationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int | str
    age: int = Field(ge=0, le=150)
    monthly_income: Decimal | None = Field(default=None, ge=Decimal("0"))
    total_assets: Decimal | None = Field(default=None, ge=Decimal("0"))
    dimension_scores: DimensionScores
    dimension_evidence: dict[str, list[ProfileEvidence]] = Field(
        min_length=4, max_length=4
    )
    recommendation_signals: dict[str, Decimal] = Field(min_length=1)
    completeness_factor: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    has_required_data: bool
    has_critical_conflict: bool
    latest_assessment_id: int | None = Field(default=None, ge=1)
    assessment_valid_until: date | None = None
    assessment_expires_at: datetime | None = None
    external_restrictions: tuple[ExternalRestriction, ...] = ()
    model_version: str = Field(min_length=1)
    rule_version: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    profile_tags: list[dict] = Field(default_factory=list)
    input_snapshot: dict[str, object]

    @model_validator(mode="after")
    def validate_dimension_evidence(self) -> ProfileCalculationInput:
        expected = {"BASIC", "EXPERIENCE", "PREFERENCE", "BEHAVIOR"}
        if set(self.dimension_evidence) != expected:
            raise ValueError("dimension_evidence must contain all four dimensions")
        if any(not items for items in self.dimension_evidence.values()):
            raise ValueError("each dimension must contain at least one evidence item")
        if self.assessment_expires_at is not None and (
            self.assessment_expires_at.tzinfo is None
            or self.assessment_expires_at.utcoffset() is None
        ):
            raise ValueError("assessment_expires_at must be timezone-aware")
        return self


class SuitabilityContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: ProfileSnapshot
    age: int = Field(ge=0, le=150)
    monthly_income: Decimal | None = Field(default=None, ge=Decimal("0"))
    total_assets: Decimal | None = Field(default=None, ge=Decimal("0"))
    external_restrictions: tuple[ExternalRestriction, ...] = ()


class SuitabilityCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: SuitabilityDecision
    customer_id: int | str
    product_id: str | None
    business_type: BusinessType
    profile_version: int = Field(ge=1)
    model_risk_level: RiskLevel
    product_risk_level: ProductRiskLevel | None
    product_risk_version: str | None
    profile_status: ProfileStatus
    suitability_confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    max_allowed_product_risk: ProductRiskLevel | None
    restriction_codes: list[str] = Field(default_factory=list)
    decision_rule_version: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
