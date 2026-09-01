from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProfileTagCode(StrEnum):
    OCCUPATION = "OCCUPATION"
    MONTHLY_INCOME = "MONTHLY_INCOME"
    TOTAL_ASSETS = "TOTAL_ASSETS"
    HOUSEHOLD_ANNUAL_INCOME = "HOUSEHOLD_ANNUAL_INCOME"
    TOTAL_LIABILITIES = "TOTAL_LIABILITIES"
    EDUCATION_LEVEL = "EDUCATION_LEVEL"
    INVESTMENT_GOAL = "INVESTMENT_GOAL"
    LOSS_TOLERANCE = "LOSS_TOLERANCE"
    MAXIMUM_LOSS_TOLERANCE_PCT = "MAXIMUM_LOSS_TOLERANCE_PCT"
    LIQUIDITY_NEED = "LIQUIDITY_NEED"
    PREFERRED_PRODUCT_TYPES = "PREFERRED_PRODUCT_TYPES"
    INVESTMENT_EXPERIENCE_YEARS = "INVESTMENT_EXPERIENCE_YEARS"
    INVESTABLE_ASSETS = "INVESTABLE_ASSETS"
    ASSET_SCALE = "ASSET_SCALE"


class ExtractionMode(StrEnum):
    RULE_DEMO = "RULE_DEMO"
    OPENAI_COMPATIBLE = "OPENAI_COMPATIBLE"


class TagDecision(StrEnum):
    CREATED = "CREATED"
    UPDATED_SAME_SOURCE = "UPDATED_SAME_SOURCE"
    REPLACED_LOWER_PRIORITY = "REPLACED_LOWER_PRIORITY"
    IGNORED_LOWER_PRIORITY = "IGNORED_LOWER_PRIORITY"
    UNCHANGED = "UNCHANGED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ExtractedProfileTag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag_code: ProfileTagCode
    tag_value: Any
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    evidence_quote: str = Field(min_length=1, max_length=500)

    @field_validator("tag_value")
    @classmethod
    def validate_value(cls, value: Any, info) -> Any:
        code = info.data.get("tag_code")
        enum_values = {
            ProfileTagCode.OCCUPATION: {
                "civil_servant",
                "public_institution",
                "state_owned_employee",
                "listed_company_employee",
                "doctor",
                "lawyer",
                "engineer",
                "sme_employee",
                "self_employed",
                "retired",
                "unemployed",
            },
            ProfileTagCode.EDUCATION_LEVEL: {
                "HIGH_SCHOOL_OR_BELOW",
                "COLLEGE",
                "BACHELOR",
                "MASTER_OR_ABOVE",
            },
            ProfileTagCode.INVESTMENT_GOAL: {
                "CAPITAL_PRESERVATION",
                "STEADY_GROWTH",
                "LONG_TERM_GROWTH",
                "HIGH_RETURN",
            },
            ProfileTagCode.LOSS_TOLERANCE: {"NONE", "LOW", "MEDIUM", "HIGH"},
            ProfileTagCode.LIQUIDITY_NEED: {"LOW", "MEDIUM", "HIGH"},
            ProfileTagCode.ASSET_SCALE: {
                "BELOW_100K",
                "100K_TO_500K",
                "500K_TO_1M",
                "1M_TO_5M",
                "ABOVE_5M",
            },
        }
        if code in enum_values and value not in enum_values[code]:
            raise ValueError(f"unsupported value for {code}")
        if code in {
            ProfileTagCode.MAXIMUM_LOSS_TOLERANCE_PCT,
            ProfileTagCode.INVESTMENT_EXPERIENCE_YEARS,
            ProfileTagCode.INVESTABLE_ASSETS,
            ProfileTagCode.MONTHLY_INCOME,
            ProfileTagCode.TOTAL_ASSETS,
            ProfileTagCode.HOUSEHOLD_ANNUAL_INCOME,
            ProfileTagCode.TOTAL_LIABILITIES,
        }:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
            ):
                raise ValueError(f"{code} must be a non-negative number")
            if code is ProfileTagCode.MAXIMUM_LOSS_TOLERANCE_PCT and value > 100:
                raise ValueError("loss tolerance percentage must not exceed 100")
            if code is ProfileTagCode.INVESTMENT_EXPERIENCE_YEARS and value > 80:
                raise ValueError("investment experience must not exceed 80 years")
        if code is ProfileTagCode.PREFERRED_PRODUCT_TYPES:
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(item, str) and item.strip() for item in value)
            ):
                raise ValueError(
                    "preferred product types must be a non-empty string list"
                )
        return value


class ConversationExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_mode: ExtractionMode
    model_name: str = Field(min_length=1, max_length=64)
    prompt_version: str = Field(min_length=1, max_length=32)
    summary: str = Field(default="", max_length=500)
    tags: list[ExtractedProfileTag] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def tag_codes_must_be_unique(self) -> ConversationExtractionResult:
        codes = [tag.tag_code for tag in self.tags]
        if len(codes) != len(set(codes)):
            raise ValueError("extracted tag codes must be unique")
        return self


class ProfileTagView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag_id: str | int
    customer_id: int | str
    tag_code: ProfileTagCode
    tag_value: Any
    confidence: Decimal
    source_type: str
    extraction_method: str
    status: str
    effective_at: datetime
    updated_at: datetime


class TagApplicationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: ProfileTagView
    decision: TagDecision
    conflict_id: str | int | None = None


class TagConflictView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_id: str | int
    customer_id: int | str
    tag_code: ProfileTagCode
    left_value: Any
    right_value: Any
    left_source: str
    right_source: str
    left_method: str
    right_method: str
    left_confidence: Decimal
    right_confidence: Decimal
    status: str
    resolution: str | None
    detected_at: datetime
    resolved_at: datetime | None


TAG_DIMENSION = {
    ProfileTagCode.OCCUPATION: "BASIC",
    ProfileTagCode.MONTHLY_INCOME: "BASIC",
    ProfileTagCode.TOTAL_ASSETS: "BASIC",
    ProfileTagCode.HOUSEHOLD_ANNUAL_INCOME: "BASIC",
    ProfileTagCode.TOTAL_LIABILITIES: "BASIC",
    ProfileTagCode.EDUCATION_LEVEL: "BASIC",
    ProfileTagCode.INVESTMENT_GOAL: "PREFERENCE",
    ProfileTagCode.LOSS_TOLERANCE: "PREFERENCE",
    ProfileTagCode.MAXIMUM_LOSS_TOLERANCE_PCT: "PREFERENCE",
    ProfileTagCode.LIQUIDITY_NEED: "PREFERENCE",
    ProfileTagCode.PREFERRED_PRODUCT_TYPES: "EXPERIENCE",
    ProfileTagCode.INVESTMENT_EXPERIENCE_YEARS: "EXPERIENCE",
    ProfileTagCode.INVESTABLE_ASSETS: "BASIC",
    ProfileTagCode.ASSET_SCALE: "BASIC",
}


def source_priority(source_type: str, extraction_method: str) -> int:
    source = source_type.upper()
    method = extraction_method.upper()
    # 优先级必须与画像标签置信度契约一致：问卷/KYC 90% > LLM 对话 60%
    # > 用户自述 40% > 系统推导/默认值 20%。系统规则只是推导结果，
    # 不能因为“系统生成”就压过用户明确提供的更高置信度信息。
    if source in {"KYC", "QUESTIONNAIRE", "EXTERNAL_VERIFIED"}:
        return 400
    if source == "USER_STATED" and method == "AI":
        return 300
    if source in {"USER_STATED", "MANAGER_ENTERED"}:
        return 200
    if source in {"DEFAULT", "SYSTEM_BEHAVIOR"}:
        return 100
    raise ValueError("unsupported tag source or extraction method")


def asset_scale(amount: float) -> str:
    if amount < 100_000:
        return "BELOW_100K"
    if amount < 500_000:
        return "100K_TO_500K"
    if amount < 1_000_000:
        return "500K_TO_1M"
    if amount < 5_000_000:
        return "1M_TO_5M"
    return "ABOVE_5M"
