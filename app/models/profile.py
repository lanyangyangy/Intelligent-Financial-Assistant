from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.user_id import UserId


class CustomerEnterpriseVerification(Base):
    __tablename__ = "customer_enterprise_verification"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        UserId,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    company_name: Mapped[str] = mapped_column(String(255))
    registration_no: Mapped[str] = mapped_column(String(128), default="")
    legal_representative: Mapped[str] = mapped_column(String(128), default="")
    contact_phone: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    review_note: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[int | None] = mapped_column(
        UserId, ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CustomerProfile(Base):
    __tablename__ = "customer_profile"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        UserId,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occupation: Mapped[str] = mapped_column(String(128), default="")
    region: Mapped[str] = mapped_column(String(128), default="")
    customer_type: Mapped[str] = mapped_column(String(64), default="individual")
    customer_tier: Mapped[str] = mapped_column(
        String(64), default="ordinary", index=True
    )
    investment_experience_years: Mapped[int] = mapped_column(Integer, default=0)
    investment_goal: Mapped[str] = mapped_column(String(255), default="")
    investment_horizon_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    liquidity_preference: Mapped[str] = mapped_column(String(16), default="medium")
    source_type: Mapped[str] = mapped_column(String(32), default="synthetic")
    # ---- 研判规则五维评分补充字段（投资者风险画像研判规则 第五条）----
    education_level: Mapped[str] = mapped_column(
        String(32),
        default="",
        comment="学历：HIGH_SCHOOL_OR_BELOW/COLLEGE/BACHELOR/MASTER_OR_ABOVE",
    )
    annual_income: Mapped[float | None] = mapped_column(
        Numeric(18, 2), nullable=True, comment="家庭年收入（元）"
    )
    # ---- 画像增强字段（移植自外部用户画像数据分析后端）----
    profile_status: Mapped[str] = mapped_column(
        String(32), default="PROVISIONAL", index=True
    )
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    suitability_confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    recommendation_confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    max_allowed_product_risk: Mapped[str] = mapped_column(String(8), default="R1")
    restriction_codes_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    dimension_scores_json: Mapped[str] = mapped_column(Text, default="{}")
    model_risk_score: Mapped[int] = mapped_column(Integer, default=0)
    # ---- F2.2 风险评估（需求：更新 fin_customer_profile 的 risk_level / risk_score）----
    risk_level: Mapped[str] = mapped_column(String(8), default="C1", index=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CustomerProfileTag(Base):
    """画像标签：由问卷/会话抽取/KYC 等多源生成，带置信度与证据溯源。"""

    __tablename__ = "customer_profile_tag"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        UserId,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    tag_code: Mapped[str] = mapped_column(String(64), index=True)
    tag_value_json: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    source_type: Mapped[str] = mapped_column(String(32), default="USER_STATED")
    extraction_method: Mapped[str] = mapped_column(String(32), default="RULE")
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    evidence_quote: Mapped[str] = mapped_column(Text, default="")
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CustomerProfileTagConflict(Base):
    """标签冲突审计：来源覆盖/同源更新/人工复核时保留新旧值供审计追溯。

    对应需求 F2.1「新标签与旧标签冲突时的处理策略」：
      - 来源置信度高的覆盖低的（如 AI 评估 > 用户自述）
      - 相同来源则新数据覆盖旧数据
      - 保留冲突记录用于审计
    """

    __tablename__ = "customer_profile_tag_conflict"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        UserId,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    tag_code: Mapped[str] = mapped_column(String(64), index=True)
    left_value_json: Mapped[str] = mapped_column(Text, default="")  # 旧值
    right_value_json: Mapped[str] = mapped_column(Text, default="")  # 新值
    left_source: Mapped[str] = mapped_column(String(32), default="")
    right_source: Mapped[str] = mapped_column(String(32), default="")
    left_method: Mapped[str] = mapped_column(String(32), default="")
    right_method: Mapped[str] = mapped_column(String(32), default="")
    left_confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    right_confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    status: Mapped[str] = mapped_column(
        String(16), default="OPEN", index=True
    )  # OPEN / RESOLVED
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    requires_customer_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)


class CustomerRiskAssessment(Base):
    __tablename__ = "customer_risk_assessment"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        UserId,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    risk_level: Mapped[str] = mapped_column(String(8), default="C1", index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    answers_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="questionnaire")
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CustomerSubjectiveProfile(Base):
    __tablename__ = "customer_subjective_profile"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        UserId,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    field_name: Mapped[str] = mapped_column(String(128), index=True)
    field_value: Mapped[str] = mapped_column(Text)
    source_text: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(32), default="user_declared")
    confirmed: Mapped[bool] = mapped_column(default=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CustomerAssetSnapshot(Base):
    __tablename__ = "customer_asset_snapshot"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        UserId,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    total_asset: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    cash_balance: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    investable_asset: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    liability: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    net_asset: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    snapshot_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), default="synthetic")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Product(Base):
    __tablename__ = "product"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    product_type: Mapped[str] = mapped_column(String(64), default="fund")
    risk_level: Mapped[str] = mapped_column(String(16), default="C1", index=True)
    term_days: Mapped[int] = mapped_column(Integer, default=0)
    minimum_amount: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    liquidity: Mapped[str] = mapped_column(String(64), default="medium")
    description: Mapped[str] = mapped_column(Text, default="")
    target_customer_type: Mapped[str] = mapped_column(String(64), default="individual")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="synthetic")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProductSuitabilityRule(Base):
    __tablename__ = "product_suitability_rule"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("product.id", ondelete="CASCADE"), index=True
    )
    minimum_risk_level: Mapped[str] = mapped_column(String(16), default="C1")
    investor_type: Mapped[str] = mapped_column(String(64), default="individual")
    minimum_investable_asset: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    rule_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RiskRule(Base):
    __tablename__ = "risk_rule"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    rule_type: Mapped[str] = mapped_column(String(32), default="suitability")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    risk_level: Mapped[str] = mapped_column(String(32), default="medium")
    enabled: Mapped[bool] = mapped_column(default=True)
    version: Mapped[str] = mapped_column(String(32), default="1.0")
    source_document: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CustomerHolding(Base):
    __tablename__ = "customer_holding"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        UserId,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("product.id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    cost_amount: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    market_value: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    profit_loss: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    holding_days: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CustomerProfileVersion(Base):
    """画像版本快照（对应外部后端 fin_profile_calculation）。

    每次画像计算（calculate）写入一条历史快照，用于画像版本时间线展示，
    支持版本对比与审计回溯。
    """

    __tablename__ = "customer_profile_version"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        UserId,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    profile_version: Mapped[int] = mapped_column(Integer, index=True)
    reason: Mapped[str] = mapped_column(String(64), default="calculate")
    model_risk_score: Mapped[int] = mapped_column(Integer, default=0)
    model_risk_level: Mapped[str] = mapped_column(String(8), default="C1")
    profile_status: Mapped[str] = mapped_column(String(32), default="PROVISIONAL")
    suitability_confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    max_allowed_product_risk: Mapped[str] = mapped_column(String(8), default="R1")
    dimension_scores_json: Mapped[str] = mapped_column(Text, default="{}")
    restriction_codes_json: Mapped[str] = mapped_column(Text, default="[]")
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
