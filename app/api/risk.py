import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.common.response import ApiResponse
from app.common.security.auth import current_user, require_staff_permission
from app.models.auth import User
from app.models.profile import CustomerProfile, CustomerRiskAssessment, Product
from app.models.trading import Order, Trade
from app.schemas.risk import (
    RiskAlertHandleRequest,
    RiskAssessmentRequest,
    RiskAssessmentResult,
    RiskMonitorRequest,
    RiskQuestionnaireResponse,
    SuitabilityCheckRequest,
    SuitabilityCheckResult,
)
from app.services.risk_questionnaire_service import (
    PRODUCT_RISK_ORDER,
    RISK_LEVEL_ORDER,
    RiskQuestionnaireService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/risk", tags=["risk"])


async def _resolve_user(request: Request, customer_id: str) -> User:
    """customer_id 兼容整数 ID 与用户名。"""
    async with request.app.state.database.session_factory() as session:
        user = None
        if str(customer_id).isdigit():
            user = (
                await session.execute(select(User).where(User.id == int(customer_id)))
            ).scalar_one_or_none()
        if user is None:
            user = (
                await session.execute(select(User).where(User.username == customer_id))
            ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="customer not found")
    return user


@router.get("/questionnaire", response_model=ApiResponse[RiskQuestionnaireResponse])
async def get_questionnaire(
    request: Request,
    _user: Annotated[User, Depends(current_user)],
) -> ApiResponse[RiskQuestionnaireResponse]:
    """返回 16 道风评题目（Mock），覆盖收入/经验/风险承受力/目标/流动性维度，每题 4 选项。"""
    data = RiskQuestionnaireService().get_questionnaire()
    return ApiResponse(data=RiskQuestionnaireResponse(**data))


@router.post("/assessment", response_model=ApiResponse[RiskAssessmentResult])
async def submit_assessment(
    request: Request,
    payload: RiskAssessmentRequest,
    _user: Annotated[User, Depends(current_user)],
) -> ApiResponse[RiskAssessmentResult]:
    """提交风评答案：计总分 → 判定 C1-C5 → 写入 fin_risk_assessment → 更新画像 risk_level/risk_score。"""
    user = await _resolve_user(request, payload.customer_id)
    service = RiskQuestionnaireService()
    try:
        score, level, level_name = service.score(
            [{"q": a.q, "a": a.a} for a in payload.answers]
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    async with request.app.state.database.session_factory() as session:
        assessment, profile = await service.assess(
            session, user.id, [{"q": a.q, "a": a.a} for a in payload.answers]
        )
        # F2.1 风评 ↔ 画像联动：提交后触发画像重算（四维评分/状态/可购上限同步）
        try:
            from app.services.profile_calculation_service import (
                ProfileCalculationService,
            )

            await ProfileCalculationService().calculate(session, user.id)
        except Exception:  # noqa: BLE001 - 画像重算失败不阻断风评提交
            logger.warning("profile_recalculate_after_assessment_failed", exc_info=True)
        await session.commit()
        result = RiskAssessmentResult(
            customer_id=user.id,
            score=score,
            risk_level=level,
            level_name=level_name,
            answered=len(payload.answers),
            expired_at=assessment.expires_at,
        )
    # 失效画像缓存，个人画像页下次读取即刷新
    if hasattr(request.app.state, "redis"):
        from app.services.profile_cache_service import ProfileCacheService

        await ProfileCacheService(request.app.state.redis).invalidate(user.id)
    logger.info(
        "risk_assessment_completed customer=%s score=%s level=%s",
        user.id,
        score,
        level,
    )
    return ApiResponse(data=result)


@router.post("/suitability-check", response_model=ApiResponse[SuitabilityCheckResult])
async def suitability_check(
    request: Request,
    payload: SuitabilityCheckRequest,
    _user: Annotated[User, Depends(current_user)],
) -> ApiResponse[SuitabilityCheckResult]:
    """适当性匹配：客户风险等级（C1-C5）vs 产品风险等级（R1-R5）。
    C1→R1、C2→R1-R2、C3→R1-R3、C4→R1-R4、C5→R1-R5；不匹配返回警告并记录日志。"""
    user = await _resolve_user(request, payload.customer_id)
    async with request.app.state.database.session_factory() as session:
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user.id)
            )
        ).scalar_one_or_none()
        assessment = (
            (
                await session.execute(
                    select(CustomerRiskAssessment)
                    .where(
                        CustomerRiskAssessment.user_id == user.id,
                        CustomerRiskAssessment.status == "active",
                    )
                    .order_by(CustomerRiskAssessment.assessed_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        product = (
            await session.execute(
                select(Product).where(Product.id == payload.product_id)
            )
        ).scalar_one_or_none()

    if product is None:
        raise HTTPException(status_code=404, detail="product not found")

    customer_level = (
        (assessment.risk_level if assessment else None)
        or (profile.risk_level if profile else None)
        or "C1"
    )
    customer_order = RISK_LEVEL_ORDER.get(customer_level, 1)

    # 产品风险等级：本系统存 C 前缀（C1-C5 风险），映射为 R1-R5 语义
    product_raw = str(product.risk_level).upper().replace("C", "R")
    product_order = PRODUCT_RISK_ORDER.get(product_raw, 1)

    # 研判规则 第十一条：客户可购买 ≤ C+1 档的产品（C5 封顶 R5）
    #   C1→R1-R2, C2→R1-R3, C3→R1-R4, C4→R1-R5, C5→R1-R5
    max_allowed_order = min(customer_order + 1, 5)
    matched = product_order <= max_allowed_order
    max_allowed = f"R{max_allowed_order}"
    warning = None
    if not matched:
        warning = (
            f"客户风险等级 {customer_level} 不匹配产品风险等级 {product_raw}："
            f"客户仅可购买 {max_allowed} 及以下风险等级的产品。"
        )
        logger.warning(
            "suitability_mismatch customer=%s customer_level=%s product=%s product_level=%s",
            user.id,
            customer_level,
            product.id,
            product_raw,
        )

    return ApiResponse(
        data=SuitabilityCheckResult(
            customer_id=user.id,
            product_id=payload.product_id,
            product_name=product.name,
            customer_risk_level=customer_level,
            product_risk_level=product_raw,
            matched=matched,
            warning=warning,
            max_allowed_product_risk=max_allowed,
        )
    )


@router.get("/alerts", response_model=ApiResponse[list[dict]])
async def list_alerts(
    request: Request,
    _user: Annotated[User, Depends(require_staff_permission("risk:write"))],
    limit: int = 50,
) -> ApiResponse[list[dict]]:
    """预警列表（Phase 4 F4.1：GET /api/risk/alerts）。"""
    from app.models.risk import RiskAlert

    async with request.app.state.database.session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(RiskAlert).order_by(RiskAlert.created_at.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return ApiResponse(
            data=[
                {
                    "id": r.id,
                    "customer_id": r.customer_id,
                    "alert_level": r.alert_level,
                    "alert_color": r.alert_color,
                    "alert_type": r.alert_type,
                    "trigger_rules": r.trigger_rules_json,
                    "confidence": r.confidence,
                    "status": r.status,
                    "handle_note": r.handle_note,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        )


@router.put("/alerts/{alert_id}/handle", response_model=ApiResponse[dict])
async def handle_alert(
    request: Request,
    alert_id: str,
    user: Annotated[User, Depends(require_staff_permission("risk:write"))],
    payload: RiskAlertHandleRequest,
) -> ApiResponse[dict]:
    """处理预警（仅风控专员和系统管理员）。"""
    from app.models.risk import RiskAlert

    async with request.app.state.database.session_factory() as session:
        alert = (
            await session.execute(select(RiskAlert).where(RiskAlert.id == alert_id))
        ).scalar_one_or_none()
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")
        alert.status = "confirmed"
        alert.handle_note = payload.note or "已确认"
        alert.handler_id = user.id
        alert.handled_at = datetime.now(UTC)
        await session.commit()
    return ApiResponse(data={"id": alert_id, "status": "confirmed"})


@router.get("/work-orders", response_model=ApiResponse[list[dict]])
async def list_work_orders(
    request: Request,
    _user: Annotated[User, Depends(require_staff_permission("risk:write"))],
    limit: int = 50,
) -> ApiResponse[list[dict]]:
    """工单列表（Phase 4 F4.1：biz_work_order）。"""
    from app.models.risk import WorkOrder

    async with request.app.state.database.session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(WorkOrder).order_by(WorkOrder.created_at.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return ApiResponse(
            data=[
                {
                    "id": r.id,
                    "workorder_no": r.workorder_no,
                    "customer_id": r.customer_id,
                    "workorder_type": r.workorder_type,
                    "priority": r.priority,
                    "status": r.status,
                    "title": r.title,
                    "description": r.description,
                    "source_type": r.source_type,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        )


@router.get("/alert/{alert_id}", response_model=ApiResponse[dict])
async def get_alert(
    request: Request,
    alert_id: str,
    _user: Annotated[User, Depends(require_staff_permission("risk:write"))],
) -> ApiResponse[dict]:
    """预警详情（Phase 4 F4.1：GET /api/risk/alert/{alert_id}）。"""
    from app.models.risk import RiskAlert, WorkOrder

    async with request.app.state.database.session_factory() as session:
        alert = (
            await session.execute(select(RiskAlert).where(RiskAlert.id == alert_id))
        ).scalar_one_or_none()
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")
        workorder = (
            await session.execute(
                select(WorkOrder).where(WorkOrder.source_id == alert.id)
            )
        ).scalar_one_or_none()
        return ApiResponse(
            data={
                "id": alert.id,
                "customer_id": alert.customer_id,
                "alert_level": alert.alert_level,
                "alert_color": alert.alert_color,
                "alert_type": alert.alert_type,
                "trigger_rules": alert.trigger_rules_json,
                "trigger_detail": alert.trigger_detail,
                "confidence": alert.confidence,
                "transaction_ids": alert.transaction_ids_json,
                "status": alert.status,
                "handle_note": alert.handle_note,
                "handler_id": alert.handler_id,
                "handled_at": alert.handled_at.isoformat()
                if alert.handled_at
                else None,
                "created_at": alert.created_at.isoformat()
                if alert.created_at
                else None,
                "workorder_no": workorder.workorder_no if workorder else None,
            }
        )


@router.post("/monitor", response_model=ApiResponse[dict])
async def risk_monitor(
    request: Request,
    payload: RiskMonitorRequest,
    user: Annotated[User, Depends(require_staff_permission("risk:write"))],
) -> ApiResponse[dict]:
    """F4.1 交易事件接收：POST /api/risk/monitor。

    接收单笔交易事件，将其并入客户近 30 天真实交易后，用反洗钱规则引擎
    （22 条 RW 规则，含 R001 单笔大额/R002 同日累计/R003 频繁交易/R004
    快进快出）完整匹配，结合历史预警升级分级，生成预警 + 工单 + 广播。
    """
    from uuid import uuid4

    from app.agents.risk_agent import RISK_LEVELS, RiskMonitorAgent
    from app.infrastructure.agent_event_bus import EVENT_RISK_ALERT, AgentEventBus
    from app.models.risk import RiskAlert, WorkOrder

    # 解析客户
    customer = await _resolve_user(request, payload.customer_id)
    amount = payload.amount
    ts = payload.timestamp or datetime.now(UTC)

    # 用完整规则引擎：当前事件并入客户近 30 天真实交易后做全量匹配，
    # 使同日累计（R002）/7天频次（R003）/快进快出（R004）等跨交易规则生效。
    engine = RiskMonitorAgent(
        request.app.state.database, request.app.state.settings, None
    )
    trades, orders, daily = await engine._load_transactions(customer.id)
    # 把当前事件构造为 Trade + Order 并入（模拟该笔刚发生，同时满足
    # 基于 trades 与基于 orders 的规则口径）
    current_trade = Trade(
        id=str(uuid4()),
        trade_no=payload.transaction_id or f"TXN-{uuid4().hex[:12].upper()}",
        order_id=str(uuid4()),
        user_id=customer.id,
        product_id="",
        amount=amount,
        quantity=1,
        executed_at=ts,
    )
    current_order = Order(
        id=str(uuid4()),
        order_no=f"O{uuid4().hex[:12].upper()}",
        user_id=customer.id,
        account_id="",
        product_id="",
        amount=amount,
        quantity=1,
        status="pending_review",
        side="buy" if payload.transaction_type in {"purchase", "申购"} else "sell",
        created_at=ts,
        updated_at=ts,
    )
    merged_trades = list(trades) + [current_trade]
    merged_orders = list(orders) + [current_order]
    annual_income = await engine._load_annual_income(customer.id)
    triggered = engine.evaluate(merged_trades, merged_orders, daily, annual_income)
    # 仅保留与当前事件相关的命中（同日累计/7天频次等含当前交易的规则）
    if not triggered:
        return ApiResponse(
            data={
                "customer_id": customer.id,
                "transaction_id": payload.transaction_id,
                "rule_hits": [],
                "alert_level": None,
                "message": "交易未命中风控规则",
            }
        )

    # 近 30 天历史预警 → repeat 升级
    repeat = False
    async with request.app.state.database.session_factory() as session:
        recent = (
            await session.execute(
                select(RiskAlert)
                .where(
                    RiskAlert.customer_id == customer.id,
                    RiskAlert.created_at
                    >= datetime.now(UTC) - timedelta(days=30),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        repeat = recent is not None

    # 分级：单规则→低(蓝)；2-3 条或 repeat→中(黄)；3条+repeat 或 ≥4 条→高(红)
    count = len(triggered)
    if count == 1 and not repeat:
        level = "low"
    elif count <= 3 and not (count >= 3 and repeat):
        level = "medium"
    else:
        level = "high"
    color = RISK_LEVELS[level]
    confidence = min(0.98, 0.5 + 0.12 * count + (0.1 if repeat else 0))

    async with request.app.state.database.session_factory() as session:
        alert = RiskAlert(
            id=str(uuid4()),
            customer_id=customer.id,
            alert_level=level,
            alert_color=color,
            alert_type=triggered[0].split(" ")[0],
            trigger_rules_json=triggered,
            confidence=int(confidence * 100),
            transaction_ids_json=[payload.transaction_id]
            if payload.transaction_id
            else [],
            trigger_detail="；".join(triggered),
            status="pending",
        )
        session.add(alert)
        await session.flush()
        workorder_no = None
        if level in {"medium", "high"}:
            workorder = WorkOrder(
                id=str(uuid4()),
                workorder_no=f"WO-{uuid4().hex[:12].upper()}",
                customer_id=customer.id,
                workorder_type="可疑交易上报",
                priority="high" if level == "high" else "normal",
                status="pending",
                title=f"风控预警：{color}（{count} 条规则命中）",
                description="；".join(triggered),
                source_type="risk_alert",
                source_id=str(alert.id),
            )
            session.add(workorder)
            workorder_no = workorder.workorder_no
        await session.commit()

        # Redis 事件广播（含 alert_id，供投顾/客服 Agent 消费）
        try:
            import redis.asyncio as redis_async

            from app.core.settings import get_settings

            client = redis_async.from_url(
                get_settings().redis_url, decode_responses=True
            )
            await AgentEventBus(client).publish(
                EVENT_RISK_ALERT,
                event_type="risk_alert",
                source_agent="risk_monitor",
                payload={
                    "alert_id": str(alert.id),
                    "customer_id": customer.id,
                    "alert_level": level,
                    "alert_color": color,
                    "trigger_rules": triggered,
                    "confidence": round(confidence, 4),
                },
            )
        except Exception:  # noqa: BLE001 - 广播失败不阻断
            pass

    return ApiResponse(
        data={
            "alert_id": str(alert.id),
            "customer_id": customer.id,
            "transaction_id": payload.transaction_id,
            "rule_hits": triggered,
            "alert_level": level,
            "alert_color": color,
            "confidence": round(confidence, 4),
            "workorder_no": workorder_no,
        }
    )
