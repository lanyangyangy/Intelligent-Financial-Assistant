from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class AccountResponse(BaseModel):
    id: str
    account_no: str
    currency: str
    available_balance: Decimal
    frozen_balance: Decimal
    status: str


class OrderCreateRequest(BaseModel):
    product_id: str
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    idempotency_key: str | None = Field(default=None, max_length=128)


class OrderStatusHistoryResponse(BaseModel):
    id: str
    from_status: str | None
    to_status: str
    operator_user_id: int | str
    note: str
    created_at: datetime


class OrderResponse(BaseModel):
    id: str
    order_no: str
    user_id: int | str
    product_id: str
    product_name: str | None = None
    amount: Decimal
    quantity: Decimal
    status: str
    side: str
    review_note: str
    failure_reason: str
    created_at: datetime
    updated_at: datetime
    history: list[OrderStatusHistoryResponse] = []


class OrderActionRequest(BaseModel):
    note: str = ""


class TradeResponse(BaseModel):
    id: str
    trade_no: str
    order_id: str
    product_id: str
    amount: Decimal
    quantity: Decimal
    executed_at: datetime


class OrderListResponse(BaseModel):
    items: list[OrderResponse]
    total: int
