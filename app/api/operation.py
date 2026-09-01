"""结构化业务操作端点（移植自 Financial System-业务操作agent）。

POST /api/operation/purchase   — 产品申购（结构化 JSON，免 NL 解析）
POST /api/operation/redeem     — 产品赎回
POST /api/operation/transfer   — 客户间转账
PUT  /api/operation/contact    — 更新客户手机号

这些端点接受结构化参数，绕过 NL → 正则 → 确认流水线，直接复用
BusinessOperatorAgent 的执行管线（RBAC → 幂等 → 二次确认 → 执行 → 审计），
供前端操作台或其它 Agent 直接调用。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.common.middleware.trace import get_trace_id
from app.common.response import ApiResponse
from app.common.security.auth import current_user
from app.common.security.roles import is_customer_user
from app.models.auth import User
from app.models.profile import CustomerHolding
from app.models.risk import WorkOrder
from app.ports.agent import AgentContext

router = APIRouter(prefix="/operation", tags=["operation"])


# ── Request schemas ─────────────────────────────────────────────────────


class PurchaseRequest(BaseModel):
    product_name: str = Field(min_length=1, max_length=255)
    amount: float = Field(gt=0)
    customer_id: int | str | None = Field(default=None)
    customer_name: str | None = Field(default=None, max_length=128)
    session_id: str = Field(default="operation-api", max_length=64)
    request_id: str | None = Field(default=None, max_length=128)


class RedeemRequest(BaseModel):
    product_name: str = Field(min_length=1, max_length=255)
    shares: float | None = Field(default=None, gt=0)
    redeem_all: bool = False
    customer_id: int | str | None = Field(default=None)
    customer_name: str | None = Field(default=None, max_length=128)
    session_id: str = Field(default="operation-api", max_length=64)
    request_id: str | None = Field(default=None, max_length=128)


class TransferRequest(BaseModel):
    amount: float = Field(gt=0)
    source_customer_id: int | str | None = Field(default=None)
    source_customer_name: str | None = Field(default=None, max_length=128)
    target_customer_id: int | str | None = Field(default=None)
    target_customer_name: str | None = Field(default=None, max_length=128)
    session_id: str = Field(default="operation-api", max_length=64)
    request_id: str | None = Field(default=None, max_length=128)


class ContactUpdateRequest(BaseModel):
    phone: str = Field(min_length=11, max_length=11)
    customer_id: int | str | None = Field(default=None)
    customer_name: str | None = Field(default=None, max_length=128)
    session_id: str = Field(default="operation-api", max_length=64)
    request_id: str | None = Field(default=None, max_length=128)


# ── Helpers ─────────────────────────────────────────────────────────────


def _require_staff(user: User) -> None:
    if is_customer_user(user):
        raise HTTPException(status_code=403, detail="仅内部员工可使用操作端点")


def _context(
    request: Request, user: User, session_id: str, request_id: str | None
) -> AgentContext:
    return AgentContext(
        request_id=get_trace_id() or "",
        user_id=user.id,
        role=next((r.code for r in user.roles), None),
        metadata={
            "is_super_admin": user.is_super_admin,
            "session_id": session_id or "operation-api",
            "request_id": request_id,
        },
    )


def _agent(request: Request):
    return request.app.state.agent_orchestrator.get("business_operator")


def _identifier(body: BaseModel, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = getattr(body, key, None)
        if value:
            return str(value)
    return ""


async def _resolve_customer_id(request: Request, customer_id: int | str | None, customer_name: str | None) -> int | None:
    """将 operation 请求中的客户标识解析为用户 id（优先 id，其次按姓名/用户名查库）。"""
    if customer_id is not None:
        return int(customer_id)
    if customer_name:
        async with request.app.state.database.session_factory() as session:
            user = (
                await session.execute(
                    select(User).where(
                        User.display_name == customer_name, User.status == "active"
                    )
                )
            ).scalar_one_or_none()
            return user.id if user else None
    return None


async def _sync_holdings(request: Request, customer_id: int | None) -> None:
    """以 DB 为权威源，将客户当前全部持仓同步到 Neo4j（图谱不可用时静默降级）。"""
    if customer_id is None:
        return
    async with request.app.state.database.session_factory() as session:
        rows = (
            await session.execute(
                select(CustomerHolding.product_id).where(
                    CustomerHolding.user_id == customer_id,
                    CustomerHolding.status == "active",
                )
            )
        ).scalars().all()
    await request.app.state.knowledge_graph.sync_customer_holdings(
        customer_id, [str(pid) for pid in rows]
    )


# ── Endpoints ───────────────────────────────────────────────────────────


@router.post("/purchase")
async def purchase(
    request: Request,
    body: PurchaseRequest,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse:
    _require_staff(user)
    params = {
        "product_name": body.product_name,
        "amount": body.amount,
    }
    identifier = _identifier(body, ("customer_id", "customer_name"))
    if not identifier:
        raise HTTPException(
            status_code=400, detail="缺少客户标识（customer_id 或 customer_name）"
        )
    params["customer_identifier"] = identifier
    result = await _agent(request).execute_operation(
        "purchase", params, _context(request, user, body.session_id, body.request_id)
    )
    # 动态同步：申购成交后以 DB 为权威源重建该客户图谱持仓关系
    cid = await _resolve_customer_id(request, body.customer_id, body.customer_name)
    await _sync_holdings(request, cid)
    return ApiResponse(data=result.model_dump(), trace_id=get_trace_id())


@router.post("/redeem")
async def redeem(
    request: Request,
    body: RedeemRequest,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse:
    _require_staff(user)
    params = {
        "product_name": body.product_name,
        "shares": body.shares,
        "redeem_all": body.redeem_all,
    }
    identifier = _identifier(body, ("customer_id", "customer_name"))
    if not identifier:
        raise HTTPException(
            status_code=400, detail="缺少客户标识（customer_id 或 customer_name）"
        )
    params["customer_identifier"] = identifier
    result = await _agent(request).execute_operation(
        "redeem", params, _context(request, user, body.session_id, body.request_id)
    )
    # 动态同步：赎回后以 DB 为权威源重建该客户图谱持仓关系（清仓时自动移除 HOLDS）
    cid = await _resolve_customer_id(request, body.customer_id, body.customer_name)
    await _sync_holdings(request, cid)
    return ApiResponse(data=result.model_dump(), trace_id=get_trace_id())


@router.post("/transfer")
async def transfer(
    request: Request,
    body: TransferRequest,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse:
    _require_staff(user)
    source = _identifier(body, ("source_customer_id", "source_customer_name"))
    target = _identifier(body, ("target_customer_id", "target_customer_name"))
    if not source or not target:
        raise HTTPException(status_code=400, detail="缺少转出方或转入方客户标识")
    params = {
        "customer_identifier": source,
        "target": target,
        "amount": body.amount,
    }
    result = await _agent(request).execute_operation(
        "transfer", params, _context(request, user, body.session_id, body.request_id)
    )
    # 动态同步：转账后同步转出方持仓关系到图谱
    cid = await _resolve_customer_id(
        request, body.source_customer_id, body.source_customer_name
    )
    await _sync_holdings(request, cid)
    return ApiResponse(data=result.model_dump(), trace_id=get_trace_id())


@router.put("/contact")
async def update_contact(
    request: Request,
    body: ContactUpdateRequest,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse:
    _require_staff(user)
    identifier = _identifier(body, ("customer_id", "customer_name"))
    if not identifier:
        raise HTTPException(
            status_code=400, detail="缺少客户标识（customer_id 或 customer_name）"
        )
    params = {
        "customer_identifier": identifier,
        "field": "phone",
        "value": body.phone,
    }
    result = await _agent(request).execute_operation(
        "info_update", params, _context(request, user, body.session_id, body.request_id)
    )
    return ApiResponse(data=result.model_dump(), trace_id=get_trace_id())


@router.get("/audit-orders")
async def list_audit_orders(
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> ApiResponse:
    """业务操作审计工单列表（员工可见，无需 risk:write；供操作工作台面板使用）。"""
    _require_staff(user)
    async with request.app.state.database.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(WorkOrder)
                    .where(WorkOrder.workorder_type == "业务操作审计")
                    .order_by(WorkOrder.created_at.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
    return ApiResponse(
        data=[
            {
                "id": w.id,
                "workorder_no": w.workorder_no,
                "workorder_type": w.workorder_type,
                "customer_id": w.customer_id,
                "submitter_id": w.submitter_id,
                "status": w.status,
                "created_at": w.created_at.isoformat() if w.created_at else None,
            }
            for w in rows
        ],
        trace_id=get_trace_id(),
    )
