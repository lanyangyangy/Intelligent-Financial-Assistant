from pydantic import BaseModel, Field


class AgentState(BaseModel):
    request_id: str
    trace_id: str
    user_id: int | str | None = None
    customer_id: int | str | None = None
    role: str | None = None
    user_message: str
    messages: list[dict] = Field(default_factory=list)
    selected_agents: list[str] = Field(default_factory=list)
    agent_results: list[dict] = Field(default_factory=list)
    tool_calls: list[dict] = Field(default_factory=list)
    confirmed_memory_candidates: list[dict] = Field(default_factory=list)
    pending_confirmation: dict | None = None
    iteration_count: int = 0
    tool_call_count: int = 0
    token_budget: int = 0
    error: str | None = None
    final_response: str | None = None


class AgentResult(BaseModel):
    agent_name: str
    status: str
    summary: str
    data: dict = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)
    tool_calls: list[dict] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_confirmation: bool = False
    next_action: str | None = None
    errors: list[str] = Field(default_factory=list)
