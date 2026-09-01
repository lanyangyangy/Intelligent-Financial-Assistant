from __future__ import annotations

import re
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.agents.orchestrator import AgentOrchestrator
from app.common.security.roles import STAFF_ROLE_CODES
from app.ports.agent import AgentContext
from app.schemas.agents import AgentState

# ---------------------------------------------------------------------------
# Global intent routing (功能设计文档 7.2): the supervisor decides which of the
# five domain agents handles a user message.
#
# Routing distinguishes *questions* (咨询类 → customer_service) from
# *commands* (执行类 → business_operator). Policy/regulatory questions stay
# with the customer service agent even when they mention AML/risk keywords;
# risk_monitor is only selected for explicit monitoring scans.
# ---------------------------------------------------------------------------

# 执行类操作动词：有这些词视为"执行指令"
ACTION_VERBS = [
    "帮我",
    "请帮我",
    "下单",
    "执行申购",
    "执行赎回",
    "转账到",
    "买入",
    "卖出",
]
# 咨询类疑问/流程表达：优先客服
QUESTION_WORDS = [
    "怎么",
    "如何",
    "多久",
    "几天",
    "什么手续",
    "有什么要求",
    "是什么",
    "需要什么",
    "什么材料",
    "需要哪些",
    "哪些",
    "介绍",
    "流程",
    "费用",
    "到账",
    "规定",
    "政策",
    "新规",
    "监管",
    "合规",
    "办法",
    "条例",
    "反洗钱要求",
    "你好",
    "您好",
    "谢谢",
    "转人工",
    "人工客服",
]
# 明确风控监测语境（仅"扫描/监测"类动作，避免与业务操作"可疑上报"冲突）
RISK_SCAN_WORDS = [
    "风控扫描",
    "风控监测",
    "预警",
    "反洗钱监测",
    "扫描一下",
    "交易监测",
    "扫描我的交易",
    # 需求 2.2 风控监测典型场景："监测到50万大额转账→触发大额交易规则"。
    # 风控专员手动描述监测场景时也能路由到风控监测（该判断位于业务操作
    # 之前，避免被"转账/申购"等操作词抢占）。
    "监测到",
    "监测",
    "触发规则",
]
ADVISOR_WORDS = ["推荐", "投顾", "配置", "持仓", "画像", "适合", "资产配置", "建议"]
ANALYTICS_WORDS = [
    "统计",
    "有多少",
    "多少笔",
    "平均",
    "汇总",
    "查询数据",
    "报表",
    "AUM",
    "分析数据",
]
ANALYTICS_QUERY_WORDS = [
    "持有哪些",
    "持仓查询",
    "当前持仓",
    "收益率",
    "平均收益",
    "交易记录",
    "最近交易",
    "转账记录",
    "最近30天",
    "近30天",
    "30天内",
    "30天",
    "最近一个月",
    "查看交易",
    "查交易",
    "产品总数",
    "在售产品",
    "产品数量",
    "各类型产品",
    "产品类型",
    "查看工单",
    "待处理",
    "工单状态",
    "工单列表",
]
# 业务操作动词（Phase 3 F3.4：8 种意图全覆盖）
OPERATION_WORDS = [
    "申购",
    "赎回",
    "转账",
    "购买",
    "认购",
    "操作",
    "下单",
    "上报",
    "可疑上报",
    "工单",
    "投诉",
    "风评重做",
    "重新测评",
    "重新做风险",
    "重新做风评",
    "重新做风险评估",
    "重新做风险测评",
    "做风评",
    "做风险评估",
    "做风险测评",
    "风评测试",
    "风评",
    "风险测评",
    "信息更新",
    "改手机号",
    "更新信息",
    "转到",
    "划转",
    "查净值",
    "最新净值",
    "净值多少",
    "查一下净值",
    "查下净值",
    "改成",
    # 注意：不要用裸 "净值" 作为触发词——"高净值客户"会误命中，导致
    # 投顾对比分析被错误路由到业务操作。产品净值查询用
    # "查净值 / 最新净值 / 净值多少" 精确匹配。
]

# 执行类业务操作词（真正落库的高风险动作）：越权角色命中时明确拒绝
# （路由到 business_operator → chat 层 403），而不是静默回退到数据分析
# 执行 NL2SQL 造成误导。查询类（查看工单/工单列表/净值查询）不在此列，
# 仍按原回退逻辑处理。
STRICT_OPERATION_WORDS = [
    "申购",
    "赎回",
    "转账",
    "购买",
    "认购",
    "下单",
    "上报",
    "可疑上报",
    "投诉",
    "风评重做",
    "重新测评",
    "重新做风险",
    "重新做风评",
    "重新做风险评估",
    "重新做风险测评",
    "做风评",
    "做风险评估",
    "做风险测评",
    "风评测试",
    "风评",
    "风险测评",
    "信息更新",
    "改手机号",
    "更新信息",
    "转到",
    "划转",
    "改成",
]

# 业务相关语境（金融/交易/账户类）：识别不到八大业务时，只要消息与
# 业务相关（贷款/存款/开户/保险/外汇/挂失/理财规划等），就路由到
# business_operator 触发"目前没有该业务"兜底；纯闲聊（你好/天气/吃饭）
# 不触发，仍走客服。
BUSINESS_CONTEXT_WORDS = [
    # 存贷款
    "贷款",
    "借款",
    "融资",
    "房贷",
    "车贷",
    "存款",
    "定期",
    "活期",
    "储蓄",
    "利息",
    # 账户/卡
    "开户",
    "销户",
    "办卡",
    "开卡",
    "信用卡",
    "挂失",
    "补卡",
    "解冻账户",
    "冻结账户",
    # 其他金融业务
    "保险",
    "保单",
    "理赔",
    "外汇",
    "结汇",
    "购汇",
    "汇率",
    "黄金",
    "贵金属",
    "信托",
    "私募",
    "代发工资",
    "对公业务",
    "承兑",
    "票据",
    # 通用业务动词（可能指向未覆盖业务）
    "办理",
    "申请",
    "开通",
    "签约",
    "注销",
    "变更",
    "登记",
    "查询业务",
]

# 二次确认响应词：用户回复"确认/取消"等纯确认文本时，路由到业务操作
# Agent 消费会话最近待确认操作（避免被客服闲聊抢占）。仅对业务操作
# 允许角色（理财顾问/客户经理/风控专员）生效；仅匹配短文本（纯确认/取消）。
CONFIRM_RESPONSE_WORDS = [
    "确认",
    "确认执行",
    "确认无误",
    "同意",
    "是的",
    "执行",
    "确定",
    "好的",
    "取消",
    "取消操作",
    "放弃",
    "不确认",
    "不要执行",
    "撤销",
]

# 产品信息咨询词（净值/收益率/起投/风险等属性）：客户角色下命中这些词
# 应走客服 RAG 咨询，而不是业务操作的“产品查询”（仅员工可用）。
CUSTOMER_PRODUCT_INQUIRY_WORDS = [
    "净值",
    "收益率",
    "年化",
    "起投",
    "风险等级",
    "投资期限",
    "募集期",
    "费率",
    "手续费",
    "赎回规则",
]

# 仅内部员工可用的 Agent（需求文档 2.2 职责说明）：
#   投顾助手→理财顾问、风控监测→风控专员、数据分析→内部员工、业务操作→客户经理
EMPLOYEE_ONLY_AGENTS = frozenset(
    {"investment_advisor", "risk_monitor", "data_analyst", "business_operator"}
)

# 专用 Agent → 允许的业务角色（需求文档 2.2 服务对象 + 功能设计文档 6.2 意图权限）：
#   - 投顾助手：仅理财顾问
#   - 风控监测：仅风控专员
#   - 业务操作：理财顾问 + 客户经理 + 风控专员（功能设计文档 6.2：
#     申购/赎回/风评→理财顾问、转账/信息更新/工单→客户经理、
#     可疑上报→风控专员；意图级权限由 operations_agent.PERMISSION_MATRIX 判定）
#   - 数据分析：内部员工通用（不在此映射，单独按权限码校验）
#   - 智能客服：兜底（员工也可咨询知识库/政策）
# 系统管理员（is_super_admin）拥有全部专用 Agent。
AGENT_REQUIRED_ROLES: dict[str, set[str]] = {
    "investment_advisor": {"financial_advisor"},
    "risk_monitor": {"risk_specialist"},
    "business_operator": {"customer_manager", "financial_advisor", "risk_specialist"},
}


def staff_agent_allowed(
    agent: str, *, employee_role: str | None = None, is_super_admin: bool = False
) -> bool:
    """判断员工是否有权使用指定 Agent（需求文档 2.2 服务对象边界）。

    - 智能客服：所有角色可用（知识库/政策/FAQ 咨询兜底）
    - 数据分析：所有内部员工可用（具体接口再按 analytics:read 权限码校验）
    - 专用 Agent（投顾/风控/业务操作）：按角色集合判断可用
    - 系统管理员：全部可用
    - 未指定角色（如测试/无角色上下文）：兼容旧行为，全部可用
    """
    if is_super_admin or not employee_role:
        return True
    required = AGENT_REQUIRED_ROLES.get(agent)
    if required is None:
        return True  # customer_service / data_analyst
    return employee_role in required


def route_message(
    message: str,
    *,
    allow_data_analysis: bool = True,
    is_customer: bool = False,
    employee_role: str | None = None,
    is_super_admin: bool = False,
) -> list[str]:
    """Deterministic keyword routing; returns the ordered candidate agents.

    需求文档 2.2 职责边界：
    - 客户账号：只能使用智能客服 Agent
    - 员工账号：路由结果受角色约束（理财顾问→投顾、风控专员→风控、
      客户经理→业务操作）；越权意图自动回退到该角色可用的 Agent
      （数据分析 / 智能客服兜底）。
    """
    if is_customer:
        return ["customer_service"]

    def _pick(agent: str) -> str:
        """角色过滤：无权限的专用 Agent 回退到该角色可用 Agent。"""
        if staff_agent_allowed(
            agent, employee_role=employee_role, is_super_admin=is_super_admin
        ):
            return agent
        # 无权限：数据分析（若允许）或客服兜底
        if allow_data_analysis and staff_agent_allowed(
            "data_analyst", employee_role=employee_role, is_super_admin=is_super_admin
        ):
            return "data_analyst"
        return "customer_service"

    text = message.lower()
    is_question = any(w in text for w in QUESTION_WORDS)
    is_action = any(w in text for w in ACTION_VERBS)

    # 二次确认响应优先：用户回复"确认/取消"等纯确认文本（去除标点后
    # 很短），路由到业务操作 Agent 消费会话待确认操作。仅业务操作允许
    # 角色生效，避免"确认收货""如何取消订单"等被误路由。
    stripped = re.sub(r"[\s，。！？!?、,.~～]+$", "", message.strip())
    if (
        len(stripped) <= 8
        and any(w in stripped for w in CONFIRM_RESPONSE_WORDS)
        and (
            is_super_admin
            or staff_agent_allowed(
                "business_operator",
                employee_role=employee_role,
                is_super_admin=is_super_admin,
            )
        )
    ):
        return ["business_operator"]

    # 风控扫描优先：含"扫描/监测/预警"等明确风控语境（如"风控扫描一下
    # 客户的交易记录"）优先路由风控监测 Agent，避免被"交易记录"等
    # 数据分析关键词抢走。
    if any(w in text for w in RISK_SCAN_WORDS):
        return [_pick("risk_monitor")]

    # 数据分析查询必须在"工单"等操作关键词之前判断。只有内部员工允许
    # 进入 NL2SQL，客户仍由客服/投顾处理自己的咨询。
    if allow_data_analysis and (
        any(w.lower() in text for w in ANALYTICS_QUERY_WORDS)
        or any(w.lower() in text for w in ANALYTICS_WORDS)
    ):
        return [_pick("data_analyst")]

    # 1) 业务操作执行优先（"上报可疑交易/创建工单/风评重做"等操作指令）
    if any(w in text for w in OPERATION_WORDS) and (
        is_action
        or not is_question
        or any(
            w in text
            for w in [
                "上报",
                "工单",
                "投诉",
                "重做",
                "把",
                "改成",
                "转到",
                "查一下",
                "查下",
            ]
        )
    ):
        # 执行类业务操作（申购/赎回/转账/上报等）：越权角色明确拒绝，
        # 路由到 business_operator 由 chat 层 403 提示，不回退数据分析。
        if any(w in text for w in STRICT_OPERATION_WORDS) and not (
            is_super_admin
            or staff_agent_allowed(
                "business_operator",
                employee_role=employee_role,
                is_super_admin=is_super_admin,
            )
        ):
            return ["business_operator"]
        return [_pick("business_operator")]
    # 2) 风控监测扫描（明确语境）
    if any(w in text for w in RISK_SCAN_WORDS):
        return [_pick("risk_monitor")]
    # 3) 投顾推荐/配置
    if any(w in text for w in ADVISOR_WORDS):
        return [_pick("investment_advisor")]
    # 4) 数据分析统计（上方已按权限处理）
    # 5) 业务相关但非八大业务：命中金融/交易/账户语境（贷款/存款/开户/
    #    保险/外汇等）但不在八大业务操作词内 → 路由到 business_operator
    #    触发"目前没有该业务"兜底。仅对业务操作允许角色生效；咨询类
    #    （"贷款怎么办理""利息是多少"）仍走客服咨询，不触发兜底。
    if (
        any(w in text for w in BUSINESS_CONTEXT_WORDS)
        and not is_question
        and (
            is_super_admin
            or staff_agent_allowed(
                "business_operator",
                employee_role=employee_role,
                is_super_admin=is_super_admin,
            )
        )
    ):
        return ["business_operator"]
    # 6) 客服兜底（含所有咨询类问题、纯闲聊）
    return [_pick("customer_service")]


class SupervisorGraph:
    """LangGraph supervisor over the five domain agents.

    The supervisor routes a user message to the best-matching agent
    (deterministic keywords, LLM-ready) and executes it through the
    AgentOrchestrator, collecting the result into AgentState.
    """

    def __init__(
        self,
        orchestrator: AgentOrchestrator | None = None,
        max_iterations: int = 4,
        max_tool_calls: int = 8,
    ) -> None:
        self.orchestrator = orchestrator or AgentOrchestrator()
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls

    async def supervisor_node(
        self, state: AgentState, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        selected = state.selected_agents or route_message(
            state.user_message,
            allow_data_analysis=state.role in STAFF_ROLE_CODES
            or state.role == "super_admin",
        )
        context = AgentContext(
            request_id=state.request_id,
            user_id=state.user_id,
            role=state.role,
            metadata={"customer_id": state.customer_id, "trace_id": state.trace_id},
        )
        results: list[dict] = []
        for agent_name in selected:
            result = await self.orchestrator.run(
                agent_name, state.user_message, context
            )
            results.append(result.model_dump())
            if result.status == "success" and result.summary:
                break
        return {
            "selected_agents": selected,
            "agent_results": results,
            "iteration_count": state.iteration_count + 1,
            "final_response": results[0]["summary"] if results else None,
        }

    def build(self):
        graph = StateGraph(AgentState)
        graph.add_node("supervisor", self.supervisor_node)
        graph.add_edge(START, "supervisor")
        graph.add_edge("supervisor", END)
        return graph.compile()
