from datetime import datetime

from pydantic import BaseModel, Field


class RiskQuestionOption(BaseModel):
    key: str = Field(min_length=1, max_length=1)
    text: str
    score: int = Field(ge=0, le=100)


class RiskQuestionItem(BaseModel):
    q: int = Field(ge=1)
    dimension: str
    question: str
    options: list[RiskQuestionOption] = Field(min_length=4, max_length=4)


class RiskQuestionnaireResponse(BaseModel):
    questionnaire_id: str
    version: str
    total_questions: int
    dimensions: list[str] = Field(default_factory=list)
    items: list[RiskQuestionItem]


class RiskAnswer(BaseModel):
    q: int = Field(ge=1)
    a: str = Field(min_length=1, max_length=1)


class RiskAssessmentRequest(BaseModel):
    customer_id: int | str
    answers: list[RiskAnswer] = Field(min_length=16, max_length=16)


class RiskAssessmentResult(BaseModel):
    customer_id: int | str
    score: int = Field(ge=0, le=100)
    risk_level: str
    level_name: str
    answered: int
    expired_at: datetime | None = None


class SuitabilityCheckRequest(BaseModel):
    customer_id: int | str
    product_id: str = Field(min_length=1)


class SuitabilityCheckResult(BaseModel):
    customer_id: int | str
    product_id: str
    product_name: str | None = None
    customer_risk_level: str
    product_risk_level: str
    matched: bool
    warning: str | None = None
    max_allowed_product_risk: str | None = None


class RiskAlertHandleRequest(BaseModel):
    note: str = Field(default="", max_length=500)


class RiskMonitorRequest(BaseModel):
    """F4.1 交易事件接收：POST /api/risk/monitor 请求体。"""

    customer_id: int | str
    transaction_id: str = Field(default="", max_length=128)
    amount: float = Field(gt=0)
    transaction_type: str = Field(default="transfer", max_length=32)
    timestamp: datetime | None = None
