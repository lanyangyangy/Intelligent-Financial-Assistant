import asyncio
import json
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.agents.graph import (
    AGENT_REQUIRED_ROLES,
    EMPLOYEE_ONLY_AGENTS,
    staff_agent_allowed,
)
from app.api.chat import _ensure_analyst_access, _run_chat, resolve_agent_name
from app.common.security.auth import current_user
from app.common.security.roles import is_customer_user, is_staff_user
from app.infrastructure.model_router import ModelRouter
from app.models.auth import User
from app.schemas.chat import ChatRequest
from app.services.session_memory_service import SessionMemoryService

router = APIRouter(tags=["chat-stream"])

# 分片和间隔需要足够明显，浏览器才能在每次 SSE 到达后完成一次渲染。
STREAM_CHUNK_SIZE = 4
STREAM_CHUNK_DELAY_SECONDS = 0.08


def _can_fallback_after_stream_failure(chunks: list[str]) -> bool:
    return not chunks


async def _stream_text(text: str):
    """将已生成的 Agent 结果拆成可见的 SSE 增量，保持统一打字机体验。"""
    for start in range(0, len(text), STREAM_CHUNK_SIZE):
        yield text[start : start + STREAM_CHUNK_SIZE]
        if start + STREAM_CHUNK_SIZE < len(text):
            # 没有让出事件循环时，多个 yield 可能被网络层合并，浏览器看不到增量效果。
            await asyncio.sleep(STREAM_CHUNK_DELAY_SECONDS)


async def _persist_stream_turn(
    request: Request,
    user: User,
    session_id: str,
    message: str,
    summary: str,
    agent_name: str,
) -> None:
    """持久化直接由 SSE 输出的客服回答，保证重进窗口可恢复。"""
    if not hasattr(request.app.state, "redis"):
        return
    try:
        memory = SessionMemoryService(request.app.state.redis)
        await memory.append(session_id, "user", message)
        await memory.append(session_id, "assistant", summary)
        async with request.app.state.database.session_factory() as session:
            await memory.archive_turn(
                session,
                session_id,
                user.id,
                message,
                summary,
                agent_type=agent_name,
                tool_calls=[],
                summary=summary,
            )
            await session.commit()
    except Exception:
        # 对话持久化失败不能中断已经开始输出的 SSE 回答。
        return


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    payload: ChatRequest,
    user: Annotated[User, Depends(current_user)],
) -> StreamingResponse:
    """SSE 流式输出（Phase 5 F5.3）：聊天助手逐字返回，打字机效果。

    仅对智能客服/闲聊场景走 LLM 流式；其余 Agent 返回其结构化 summary。
    """
    provider: ModelRouter = request.app.state.model_router
    agent_name = resolve_agent_name(payload.agent, payload.agent_type) or (
        request.app.state.supervisor_router(
            payload.message,
            allow_data_analysis=is_staff_user(user),
            is_customer=is_customer_user(user),
            employee_role=next((r.code for r in user.roles), None),
            is_super_admin=user.is_super_admin,
        )[0]
        if hasattr(request.app.state, "supervisor_router")
        else "customer_service"
    )
    # 二次确认路由：请求带 confirmation_id 或 decision（confirm/cancel/
    # select_customer）时，message 可能只是补充片段或原始指令的简化，
    # 路由层可能投到客服/投顾。只有业务操作才消费这些凭据，强制路由回
    # business_operator（否则取消请求会被客服接走，业务操作的取消逻辑
    # 不执行，甚至 LLM 把消息编成"已执行"）。
    if (
        agent_name != "business_operator"
        and not is_customer_user(user)
        and (
            payload.confirmation_id
            or payload.decision in ("confirm", "cancel", "select_customer")
        )
    ):
        agent_name = "business_operator"

    # 待补齐参数续补：该会话存在待补齐的业务操作上下文时，强制路由回
    # business_operator（用户第二次只发补充片段如"5000元""现金管理保本计划"
    # 时，路由层看不到操作动词会误投客服并走 LLM 流式，导致续补失效）。
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
            ).peek(payload.session_id or "", str(user.id))
            if pending is not None:
                agent_name = "business_operator"
        except Exception:  # noqa: BLE001 - 续补路由失败不影响正常对话
            pass
    # 需求文档 2.2 职责边界：客户只能使用智能客服 Agent。
    # 显式指定员工专用 Agent 时拒绝；未指定时强制客服。
    if is_customer_user(user):
        if agent_name in EMPLOYEE_ONLY_AGENTS:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=403,
                detail="仅内部员工可使用该 Agent（客户智能助手仅支持智能客服）",
            )
        agent_name = "customer_service"
    # 员工角色边界：专用 Agent 仅对应业务角色可用，系统管理员拥有全部。
    elif not staff_agent_allowed(
        agent_name,
        employee_role=next((r.code for r in user.roles), None),
        is_super_admin=user.is_super_admin,
    ):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=403,
            detail=(
                f"您的角色无权使用该 Agent（{AGENT_REQUIRED_ROLES[agent_name]} 专属，"
                "请使用您的职责对应 Agent）"
            ),
        )
    if agent_name == "data_analyst":
        _ensure_analyst_access(user)
    actual_session_id = payload.session_id or str(uuid4())

    async def event_stream():
        # 先发送 agent 元信息
        yield f"event: meta\ndata: {json.dumps({'agent': agent_name}, ensure_ascii=False)}\n\n"

        if (
            agent_name == "customer_service"
            and provider.available
            and not payload.extract_profile
        ):
            system = (
                "你是XX科技的智能财富管家。请友好、专业、简洁地回答用户问题，"
                "涉及产品/政策时如知识不足请诚实说明并建议联系人工客服。"
            )
            try:
                chunks: list[str] = []
                async for chunk in provider.chat_stream_with_routing(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": payload.message},
                    ],
                    temperature=0.4,
                    agent_name=agent_name,
                ):
                    chunks.append(chunk)
                    yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
                await _persist_stream_turn(
                    request,
                    user,
                    actual_session_id,
                    payload.message,
                    "".join(chunks),
                    agent_name,
                )
                yield "data: [DONE]\n\n"
                return
            except Exception:  # noqa: BLE001 - stream may fail before or after output
                if not _can_fallback_after_stream_failure(chunks):
                    yield (
                        "event: error\ndata: "
                        + json.dumps({"code": "STREAM_INTERRUPTED"})
                        + "\n\n"
                    )
                    yield "data: [DONE]\n\n"
                    return

        # 非客服 Agent 或流式不可用：复用普通聊天流程，写入 Redis 和审计归档。
        result, _, _ = await _run_chat(
            request,
            user,
            payload.message,
            session_id=actual_session_id,
            archive=payload.archive,
            confirmed=payload.confirmed,
            request_id=payload.request_id,
            decision=payload.decision,
            confirmation_id=payload.confirmation_id,
            selected_customer_id=payload.selected_customer_id,
            forced_agent=agent_name,
            extract_profile=payload.extract_profile,
        )
        # SSE 元信息：二次确认凭据（前端据此显示确认/取消按钮）
        result_data = result.data or {}
        yield (
            "event: meta\ndata: "
            + json.dumps(
                {
                    "agent": result.agent_name,
                    "requires_confirmation": result.requires_confirmation,
                    "confirmation_id": result_data.get("confirmation_id"),
                    "status": result.status,
                    "data": result_data,
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )
        async for chunk in _stream_text(result.summary):
            yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
