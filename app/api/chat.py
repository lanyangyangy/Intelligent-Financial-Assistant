import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy import select

from app.agents.graph import (
    AGENT_REQUIRED_ROLES,
    EMPLOYEE_ONLY_AGENTS,
    route_message,
    staff_agent_allowed,
)
from app.agents.orchestrator import AgentOrchestrator
from app.common.middleware.trace import get_trace_id
from app.common.response import ApiResponse
from app.common.security.auth import current_user, require_staff_permission
from app.common.security.roles import is_customer_user, is_staff_user
from app.models.auth import User
from app.models.conversation import ConversationArchive
from app.ports.agent import AgentContext
from app.schemas.agents import AgentResult
from app.schemas.chat import (
    AnalystChatRequest,
    AnalystChatResponse,
    ChatRequest,
    ChatResponse,
)
from app.services.profile_conversation_service import ProfileConversationService
from app.services.session_memory_service import SessionMemoryService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

AGENT_TYPE_ALIASES = {
    "analyst": "data_analyst",
    "data_analyst": "data_analyst",
    "customer": "customer_service",
    "customer_service": "customer_service",
    "advisor": "investment_advisor",
    "investment_advisor": "investment_advisor",
    "risk": "risk_monitor",
    "risk_monitor": "risk_monitor",
    "operator": "business_operator",
    "business_operator": "business_operator",
}


def resolve_agent_name(agent: str | None, agent_type: str | None) -> str | None:
    """统一旧字段 agent 与需求字段 agent_type，保留旧调用兼容性。"""
    value = agent or agent_type
    if not value:
        return None
    normalized = value.strip().lower()
    return AGENT_TYPE_ALIASES.get(normalized, value)


def _build_context(
    request: Request, user: User, customer_id: str | None = None
) -> AgentContext:
    """构造 Agent 上下文。

    - 客户账号：customer_id 恒为当前登录用户（只能操作自己）
    - 员工账号：customer_id 缺省时使用操作人自身；指定时解析为目标客户
      （uuid 或用户名），供投顾/业务操作等 Agent 按指定客户工作。
    """
    target_id = user.id
    if customer_id and not is_customer_user(user):
        target_id = customer_id
    elif not customer_id:
        target_id = user.id
    return AgentContext(
        request_id=get_trace_id() or "",
        user_id=user.id,
        role=next((r.code for r in user.roles), None),
        metadata={"customer_id": target_id, "is_super_admin": user.is_super_admin},
    )


async def _resolve_customer_from_message(request: Request, message: str) -> str | None:
    """员工指令中解析客户名（如"为零售投资者推荐"）→ 客户 ID。

    支持用户名（retail_investor_demo / high_net_worth_demo）与中文名
    （零售投资者 / 高净值客户等）。仅投顾/业务操作场景需要指定客户时使用。
    """

    from app.common.security.roles import CUSTOMER_ROLE_CODES
    from app.models.profile import CustomerProfile

    text = message.lower()
    keywords = {
        "零售投资者": "retail_investor_demo",
        "零售客户": "retail_investor_demo",
        "普通投资者": "retail_investor_demo",
        "高净值客户": "high_net_worth_demo",
        "高净值": "high_net_worth_demo",
    }
    username = None
    for keyword, candidate in keywords.items():
        if keyword in text:
            username = candidate
            break
    if not username:
        for candidate in ("retail_investor_demo", "high_net_worth_demo"):
            if candidate in text:
                username = candidate
                break
    if not username:
        return None
    try:
        async with request.app.state.database.session_factory() as session:
            from sqlalchemy import or_, select

            customer_roles = or_(
                *(User.roles.any(code=code) for code in CUSTOMER_ROLE_CODES)
            )
            user_row = (
                await session.execute(
                    select(User).where(
                        User.status == "active",
                        customer_roles,
                        User.username == username,
                    )
                )
            ).scalar_one_or_none()
            if user_row is None:
                return None
            profile_exists = (
                await session.execute(
                    select(CustomerProfile).where(
                        CustomerProfile.user_id == user_row.id
                    )
                )
            ).scalar_one_or_none()
            return user_row.id if profile_exists is not None else None
    except Exception:  # noqa: BLE001 - 解析失败仅影响自动识别，不阻断对话
        return None


def _ensure_analyst_access(user: User) -> None:
    if not is_staff_user(user):
        raise HTTPException(status_code=403, detail="staff permission required")
    if user.is_super_admin:
        return
    permissions = {
        permission.code for role in user.roles for permission in role.permissions
    }
    if "analytics:read" not in permissions:
        raise HTTPException(status_code=403, detail="analytics permission required")


@router.get("/chat/history/{session_id}", response_model=ApiResponse[list[dict]])
async def get_chat_history(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    session_id: str = Path(min_length=1, max_length=128),
) -> ApiResponse[list[dict]]:
    """恢复当前用户的聊天窗口记录，优先读取 Redis，超时后回退审计归档。"""
    if hasattr(request.app.state, "redis"):
        history = await SessionMemoryService(request.app.state.redis).get_history(
            session_id
        )
        if history:
            return ApiResponse(data=history)

    async with request.app.state.database.session_factory() as session:
        archives = list(
            (
                await session.execute(
                    select(ConversationArchive)
                    .where(
                        ConversationArchive.session_id == session_id,
                        ConversationArchive.user_id == user.id,
                    )
                    .order_by(ConversationArchive.archived_at.asc())
                )
            )
            .scalars()
            .all()
        )
    history = [
        message
        for archive in archives
        for message in archive.messages_json
        if isinstance(message, dict)
        and message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
    ]
    return ApiResponse(data=history[-20:])


async def _run_chat(
    request: Request,
    user: User,
    message: str,
    *,
    session_id: str | None = None,
    archive: bool = False,
    confirmed: bool = False,
    request_id: str | None = None,
    decision: str | None = None,
    confirmation_id: str | None = None,
    selected_customer_id: str | None = None,
    forced_agent: str | None = None,
    customer_id: str | None = None,
    target_customer_id: str | None = None,
    extract_profile: bool = False,
) -> tuple[AgentResult, str, int]:
    # 员工未显式指定目标客户时，尝试从消息中解析客户名（如"为零售投资者推荐"）。
    # 供投顾/业务操作 Agent 按指定客户工作（customer_id 兜底解析）。
    if not customer_id and not is_customer_user(user):
        customer_id = await _resolve_customer_from_message(request, message)
    orchestrator: AgentOrchestrator = request.app.state.agent_orchestrator
    context = _build_context(request, user, customer_id=customer_id)
    if confirmed:
        context.metadata["confirmed"] = True
    if request_id:
        context.metadata["request_id"] = request_id
    if decision:
        context.metadata["decision"] = decision
    if confirmation_id:
        context.metadata["confirmation_id"] = confirmation_id
    if selected_customer_id:
        context.metadata["selected_customer_id"] = selected_customer_id
    if target_customer_id:
        context.metadata["target_customer_id"] = target_customer_id

    actual_session_id = session_id or str(uuid4())
    context.metadata["session_id"] = actual_session_id
    memory: SessionMemoryService | None = None
    history_turns = 0
    if hasattr(request.app.state, "redis"):
        # 即使客户端未传 session_id，也保存当前轮次审计记录；传入 session_id
        # 时才读取 Redis 历史，避免新会话互相串联。
        memory = SessionMemoryService(request.app.state.redis)
        if session_id:
            history = await memory.get_history(actual_session_id)
            history_turns = len(history)
            if history:
                context.metadata["history_context"] = memory.format_context(history)

    if forced_agent:
        agent_name = forced_agent
    elif hasattr(request.app.state, "supervisor_router"):
        agent_name = request.app.state.supervisor_router(
            message,
            allow_data_analysis=is_staff_user(user),
            is_customer=is_customer_user(user),
            employee_role=next((r.code for r in user.roles), None),
            is_super_admin=user.is_super_admin,
        )[0]
    else:
        agent_name = route_message(
            message,
            allow_data_analysis=is_staff_user(user),
            employee_role=next((r.code for r in user.roles), None),
            is_super_admin=user.is_super_admin,
        )[0]

    # 二次确认路由：请求带 confirmation_id 或 decision（confirm/cancel/
    # select_customer）时，message 可能只是补充片段或原始指令的简化，
    # 路由层可能投到客服/投顾。只有业务操作才消费这些凭据，强制路由回
    # business_operator（否则取消请求会被客服接走，业务操作的取消逻辑
    # 不执行，甚至 LLM 把消息编成"已执行"）。
    if (
        agent_name != "business_operator"
        and not is_customer_user(user)
        and (confirmation_id or decision in ("confirm", "cancel", "select_customer"))
    ):
        agent_name = "business_operator"

    # 待补齐参数续补：该会话存在待补齐的业务操作上下文时，强制路由回
    # business_operator（用户第二次只发补充片段如"5000元""转到王芳账户"
    # 时，路由层看不到操作动词会误投客服/其他 Agent，导致续补失效）。
    if (
        agent_name != "business_operator"
        and not is_customer_user(user)
        and hasattr(request.app.state, "redis")
    ):
        try:
            from app.services.redis_pending_params_store import (
                RedisPendingParamsStore,
            )

            pending = await RedisPendingParamsStore(
                request.app.state.redis.client
            ).peek(actual_session_id, str(user.id))
            if pending is not None:
                agent_name = "business_operator"
        except Exception:  # noqa: BLE001 - 续补路由失败不影响正常对话
            pass

    # 需求文档 2.2 职责边界：客户只能使用智能客服 Agent。
    # 即便客户端显式指定了投顾/风控/数据分析/业务操作，也拒绝并返回 403。
    if is_customer_user(user):
        if agent_name in EMPLOYEE_ONLY_AGENTS:
            raise HTTPException(
                status_code=403,
                detail="仅内部员工可使用该 Agent（客户智能助手仅支持智能客服）",
            )
        agent_name = "customer_service"
    # 员工角色边界：专用 Agent（投顾/风控/业务操作）仅对应业务角色可用，
    # 系统管理员拥有全部。显式指定越权 Agent 时拒绝。
    elif not staff_agent_allowed(
        agent_name,
        employee_role=next((r.code for r in user.roles), None),
        is_super_admin=user.is_super_admin,
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                f"您的角色无权使用该 Agent（{AGENT_REQUIRED_ROLES[agent_name]} 专属，"
                "请使用您的职责对应 Agent）"
            ),
        )

    if agent_name == "data_analyst":
        _ensure_analyst_access(user)

    profile_extraction = None
    if extract_profile and is_customer_user(user):
        try:
            profile_extraction = await ProfileConversationService(
                request.app.state.database,
                request.app.state.settings,
                request.app.state.qwen,
                request.app.state.redis
                if hasattr(request.app.state, "redis")
                else None,
            ).extract_and_apply(user.id, message)
        except Exception:  # noqa: BLE001 - 画像抽取不能阻断正常对话
            logger.warning("profile_extraction_after_chat_failed", exc_info=True)

    result = await orchestrator.run(agent_name, message, context)
    if profile_extraction is not None:
        result.data["profile_extraction"] = profile_extraction
        if profile_extraction["tags"]:
            result.summary += (
                "\n\n已从本轮对话提取 "
                f"{len(profile_extraction['tags'])} 个画像标签并完成治理。"
            )
            if profile_extraction["conflict_ids"]:
                result.summary += (
                    f"发现 {len(profile_extraction['conflict_ids'])} 项标签冲突，"
                    "已保留记录，待确认后生效。"
                )

    if memory is not None:
        await memory.append(actual_session_id, "user", message)
        await memory.append(actual_session_id, "assistant", result.summary)
        async with request.app.state.database.session_factory() as session:
            if archive:
                await memory.archive(
                    session,
                    actual_session_id,
                    user.id,
                    history=await memory.get_history(actual_session_id),
                    agent_type=result.agent_name,
                    tool_calls=result.tool_calls,
                    summary=result.summary,
                    clear=True,
                )
            else:
                await memory.archive_turn(
                    session,
                    actual_session_id,
                    user.id,
                    message,
                    result.summary,
                    agent_type=result.agent_name,
                    tool_calls=result.tool_calls,
                    summary=result.summary,
                )
            await session.commit()

    return result, actual_session_id, history_turns


@router.post("/chat", response_model=ApiResponse[ChatResponse])
async def chat(
    request: Request, payload: ChatRequest, user: Annotated[User, Depends(current_user)]
) -> ApiResponse[ChatResponse]:
    result, session_id, history_turns = await _run_chat(
        request,
        user,
        payload.message,
        session_id=payload.session_id,
        archive=payload.archive,
        confirmed=payload.confirmed,
        request_id=payload.request_id,
        decision=payload.decision,
        confirmation_id=payload.confirmation_id,
        selected_customer_id=payload.selected_customer_id,
        forced_agent=resolve_agent_name(payload.agent, payload.agent_type),
        customer_id=payload.customer_id,
        target_customer_id=payload.target_customer_id,
        extract_profile=payload.extract_profile,
    )
    response = ChatResponse(
        agent=result.agent_name,
        summary=result.summary,
        status=result.status,
        data=result.data,
        evidence=result.evidence,
        confidence=result.confidence,
        requires_confirmation=result.requires_confirmation,
        next_action=result.next_action,
        session_id=session_id,
        history_turns=history_turns,
    )
    return ApiResponse(data=response, trace_id=get_trace_id())


@router.post("/chat/analyst", response_model=ApiResponse[AnalystChatResponse])
async def analyst_chat(
    request: Request,
    payload: AnalystChatRequest,
    user: Annotated[User, Depends(require_staff_permission("analytics:read"))],
) -> ApiResponse[AnalystChatResponse]:
    result, session_id, _ = await _run_chat(
        request,
        user,
        payload.message,
        session_id=payload.session_id,
        archive=payload.archive,
        forced_agent="data_analyst",
    )
    data = result.data
    query_result = data.get("query_result", data.get("rows", []))
    response = AnalystChatResponse(
        reply=result.summary,
        sql=data.get("sql"),
        sql_statement=data.get("sql_statement", data.get("sql")),
        query_result=query_result if isinstance(query_result, list) else [],
        interpretation=data.get("interpretation", result.summary),
        session_id=session_id,
        intent=data.get("intent"),
        row_count=int(data.get("row_count", len(query_result or []))),
        truncated=bool(data.get("truncated", False)),
        cache_hit=bool(data.get("cache_hit", False)),
        status=result.status,
    )
    return ApiResponse(data=response, trace_id=get_trace_id())
