from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    agent: str | None = Field(
        default=None, description="可选：指定 Agent，默认由 supervisor 路由"
    )
    agent_type: str | None = Field(
        default=None,
        description="统一入口 Agent 类型别名，例如 analyst；兼容旧字段 agent",
    )
    confirmed: bool = Field(default=False, description="二次确认标记（业务操作 Agent）")
    request_id: str | None = Field(
        default=None,
        max_length=128,
        description="幂等键：同一操作人同一 request_id 24h 内重复提交返回首次结果",
    )
    decision: str | None = Field(
        default=None,
        description="结构化二次确认决策：confirm / cancel（配合 confirmation_id）",
    )
    confirmation_id: str | None = Field(
        default=None,
        max_length=64,
        description="待确认操作凭据（业务操作 Agent 返回 confirmation_id 后回传）",
    )
    selected_customer_id: int | str | None = Field(
        default=None,
        description="重名消歧：业务操作 Agent 返回 candidates 后，选中目标客户的数字 ID",
    )
    session_id: str | None = Field(
        default=None,
        description="会话 ID：同一会话多轮对话保持上下文（Redis 短期记忆）",
    )
    archive: bool = Field(
        default=False, description="会话结束后归档到 conversation_archive"
    )
    extract_profile: bool = Field(
        default=False,
        description="用户已同意从本轮对话提取明确画像信息并写入标签",
    )
    customer_id: int | str | None = Field(
        default=None,
        description="投顾/业务操作等 Agent 的目标客户标识（兼容数字 ID 与用户名）；"
        "缺省时客户 Agent 使用当前登录用户，员工 Agent 使用操作人自身",
    )
    target_customer_id: int | str | None = Field(
        default=None,
        description="投顾对比分析等场景的第二个客户标识（如「对比A和B的持仓」中的 B）",
    )


class AnalystChatRequest(BaseModel):
    """数据分析专用请求；用户身份始终从 Bearer Token 获取。"""

    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, max_length=128)
    user_id: int | str | None = Field(
        default=None,
        description="兼容旧客户端字段；服务端不信任该值，以当前登录员工为准",
    )
    archive: bool = Field(
        default=False, description="是否结束会话并清理 Redis 短期记忆"
    )


class ChatResponse(BaseModel):
    agent: str
    summary: str
    status: str
    data: dict = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)
    confidence: float = 0.0
    requires_confirmation: bool = False
    next_action: str | None = None
    session_id: str | None = None
    history_turns: int = 0


class AnalystChatResponse(BaseModel):
    """兼容需求文档的 reply/sql/query_result 响应。"""

    reply: str
    sql: str | None = None
    sql_statement: str | None = Field(
        default=None, description="兼容 NL2SQL Tool 的 SQL 字段命名"
    )
    query_result: list[dict] = Field(default_factory=list)
    interpretation: str = ""
    session_id: str
    intent: str | None = None
    row_count: int = 0
    truncated: bool = False
    cache_hit: bool = False
    status: str = "success"
