from datetime import datetime

from pydantic import BaseModel, Field


class ProfileRequest(BaseModel):
    age: int | None = Field(default=None, ge=0, le=150)
    occupation: str = ""
    region: str = ""
    investment_experience_years: int = Field(default=0, ge=0)
    investment_goal: str = "balanced"
    investment_horizon_years: int | None = Field(default=None, ge=0, le=100)
    liquidity_preference: str = "medium"
    # F2.1 我的信息：学历/年收入（外部后端 profile-data 字段）
    education_level: str = Field(default="", max_length=32)
    annual_income: float | None = Field(default=None, ge=0)


class ProfileResponse(ProfileRequest):
    id: str
    user_id: int | str
    source_type: str
    created_at: datetime
    updated_at: datetime


class AssetSnapshotRequest(BaseModel):
    total_asset: float = Field(ge=0)
    cash_balance: float = Field(ge=0)
    investable_asset: float = Field(ge=0)
    liability: float = Field(ge=0)
    net_asset: float = Field(ge=0)
    source_type: str = "synthetic"


class AssetSnapshotResponse(AssetSnapshotRequest):
    id: str
    user_id: int | str
    snapshot_time: datetime
    created_at: datetime


class ProductRequest(BaseModel):
    name: str
    product_type: str = "fund"
    target_customer_type: str = "individual"
    target_customer_tiers: str = "ordinary,gold,platinum,diamond,private_bank"
    risk_level: str = "C1"
    term_days: int = 0
    minimum_amount: float = 0
    liquidity: str = "medium"
    description: str = ""
    status: str = "active"


class ProductResponse(ProductRequest):
    id: str
    source_type: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class ProductListResponse(BaseModel):
    items: list[ProductResponse]
    total: int


class HoldingResponse(BaseModel):
    id: str
    user_id: int | str
    product_id: str
    quantity: float
    cost_amount: float
    market_value: float
    profit_loss: float
    holding_days: int
    status: str
    created_at: datetime
    updated_at: datetime


class ProfileSummary(BaseModel):
    profile: ProfileResponse | None
    latest_asset: AssetSnapshotResponse | None
    holdings: list[HoldingResponse]


class SuitabilityResult(BaseModel):
    product_id: str
    product_name: str
    matched: bool
    reasons: list[str]


class CustomerTierResponse(BaseModel):
    user_id: int | str
    customer_type: str
    customer_tier: str
    investable_asset: float
    reasons: list[str]


class RecommendationResponse(BaseModel):
    user_id: int | str
    matches: list[SuitabilityResult]
    excluded: list[SuitabilityResult]


class StaffCustomerListItem(BaseModel):
    id: int | str
    username: str
    display_name: str
    status: str
    profile: ProfileResponse | None = None
    latest_asset: AssetSnapshotResponse | None = None
    customer_tier: str = "ordinary"
    tier_reasons: list[str] = []
    risk_level: str | None = None
    risk_score: int | None = None
    risk_status: str | None = None
    holding_count: int = 0


class StaffCustomerListResponse(BaseModel):
    items: list[StaffCustomerListItem]
    total: int


class StaffCustomerDetail(ProfileSummary):
    user: StaffCustomerListItem


class EnterpriseVerificationRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=255)
    registration_no: str = Field(default="", max_length=128)
    legal_representative: str = Field(default="", max_length=128)
    contact_phone: str = Field(default="", max_length=64)


class EnterpriseVerificationResponse(EnterpriseVerificationRequest):
    id: str
    user_id: int | str
    status: str
    review_note: str
    reviewed_by: int | str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RiskAssessmentRequest(BaseModel):
    investment_experience_years: int = Field(ge=0, le=80)
    max_loss_tolerance: int = Field(ge=0, le=100)
    investment_horizon_years: int = Field(ge=0, le=100)
    liquidity_need: str = Field(default="medium", pattern="^(high|medium|low)$")
    investment_goal: str = Field(
        default="balanced",
        pattern="^(capital_preservation|income|balanced|growth|aggressive)$",
    )
    risk_willingness: int = Field(ge=0, le=100)


class RiskAssessmentResponse(BaseModel):
    id: str
    user_id: int | str
    risk_level: str
    score: int
    answers: dict
    status: str
    source_type: str
    assessed_at: datetime
    expires_at: datetime | None = None
