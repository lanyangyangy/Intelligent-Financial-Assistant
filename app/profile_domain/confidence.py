from __future__ import annotations

from decimal import Decimal

from app.profile_domain.models import (
    EvidenceSourceType,
    EvidenceStatus,
    ExtractionMethod,
    ProfileEvidence,
    ProfileStatus,
    ProfileStatusContext,
)

ZERO = Decimal("0")
ONE = Decimal("1")
COUNT_BONUS = Decimal("0.05")
COUNT_BONUS_CAP = Decimal("0.30")
CONFLICT_PENALTY = Decimal("0.10")

CONTROLLED_SOURCES = frozenset(
    {
        EvidenceSourceType.QUESTIONNAIRE,
        EvidenceSourceType.KYC,
        EvidenceSourceType.SYSTEM_BEHAVIOR,
        EvidenceSourceType.EXTERNAL_VERIFIED,
    }
)
CONTROLLED_METHODS = frozenset(
    {ExtractionMethod.DIRECT, ExtractionMethod.RULE, ExtractionMethod.IMPORT}
)


def _evidence_policy(evidence: ProfileEvidence) -> tuple[int, Decimal]:
    if evidence.extraction_method is ExtractionMethod.AI:
        if (
            not evidence.field_validated
            or evidence.source_type is EvidenceSourceType.DEFAULT
        ):
            raise ValueError(
                "AI evidence must be validated and cannot use DEFAULT source"
            )
        return 3, Decimal("0.60")

    if (
        evidence.source_type in CONTROLLED_SOURCES
        and evidence.extraction_method in CONTROLLED_METHODS
    ):
        return 4, Decimal("0.90")

    if (
        evidence.source_type is EvidenceSourceType.USER_STATED
        and evidence.extraction_method
        in {ExtractionMethod.DIRECT, ExtractionMethod.MANUAL}
    ):
        return 2, Decimal("0.40")

    if (
        evidence.source_type is EvidenceSourceType.MANAGER_ENTERED
        and evidence.extraction_method
        in {ExtractionMethod.MANUAL, ExtractionMethod.IMPORT}
    ):
        return 2, Decimal("0.40")

    if (
        evidence.source_type is EvidenceSourceType.DEFAULT
        and evidence.extraction_method is ExtractionMethod.RULE
    ):
        return 1, Decimal("0.20")

    raise ValueError("unsupported evidence source and extraction method combination")


def _clamp(value: Decimal) -> Decimal:
    return min(max(value, ZERO), ONE)


def calculate_dimension_confidence(evidence: list[ProfileEvidence]) -> Decimal:
    if not evidence:
        raise ValueError("evidence must not be empty")

    usable = [
        item
        for item in evidence
        if item.status not in {EvidenceStatus.REJECTED, EvidenceStatus.EXPIRED}
        and item.freshness_decay > ZERO
    ]
    classified = [(item, *_evidence_policy(item)) for item in usable]
    active = [
        (item, tier, base)
        for item, tier, base in classified
        if item.status is EvidenceStatus.ACTIVE
    ]
    if not active:
        raise ValueError("at least one usable ACTIVE evidence item is required")

    selected_tier = max(tier for _, tier, _ in active)
    selected = [entry for entry in active if entry[1] == selected_tier]
    primary, _, base_confidence = max(
        selected,
        key=lambda entry: entry[2] * entry[0].freshness_decay,
    )
    current_base = base_confidence * primary.freshness_decay
    count_bonus = min(Decimal(len(selected)) * COUNT_BONUS, COUNT_BONUS_CAP)
    conflict_count = sum(
        item.status is EvidenceStatus.CONFLICTED for item, _, _ in classified
    )
    conflict_penalty = Decimal(conflict_count) * CONFLICT_PENALTY

    return _clamp(current_base + count_bonus - conflict_penalty)


DEFAULT_SUITABILITY_CONFIDENCE_THRESHOLD = Decimal("0.80")
DEFAULT_RECOMMENDATION_CONFIDENCE_THRESHOLD = Decimal("0.60")


def _validate_unit_decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValueError(f"{field_name} must be a Decimal")
    if not value.is_finite() or not ZERO <= value <= ONE:
        raise ValueError(f"{field_name} must be a finite Decimal between 0 and 1")
    return value


def _calculate_aggregate_confidence(
    values: dict[str, Decimal],
    completeness_factor: Decimal,
) -> Decimal:
    if not isinstance(values, dict) or not values:
        raise ValueError("confidence values must be a non-empty dict")

    validated = [
        _validate_unit_decimal(value, f"confidence[{name}]")
        for name, value in values.items()
    ]
    factor = _validate_unit_decimal(completeness_factor, "completeness_factor")
    average = sum(validated, ZERO) / Decimal(len(validated))
    return _clamp(average * factor)


def calculate_suitability_confidence(
    dimensions: dict[str, Decimal],
    completeness_factor: Decimal,
) -> Decimal:
    return _calculate_aggregate_confidence(dimensions, completeness_factor)


def calculate_recommendation_confidence(
    signals: dict[str, Decimal],
    completeness_factor: Decimal,
) -> Decimal:
    return _calculate_aggregate_confidence(signals, completeness_factor)


def resolve_profile_status(context: ProfileStatusContext) -> ProfileStatus:
    if not isinstance(context, ProfileStatusContext):
        raise ValueError("context must be a ProfileStatusContext instance")
    if context.assessment_expired:
        return ProfileStatus.EXPIRED
    if not context.has_required_data:
        return ProfileStatus.INCOMPLETE
    if context.has_critical_conflict:
        return ProfileStatus.NEEDS_REVIEW
    if context.suitability_confidence < context.minimum_suitability_confidence:
        return ProfileStatus.PROVISIONAL
    return ProfileStatus.VALID
