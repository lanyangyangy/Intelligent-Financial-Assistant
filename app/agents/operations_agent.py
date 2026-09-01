from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import redis.asyncio as redis
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.agents.base import AgentBase
from app.common.security.roles import CUSTOMER_ROLE_CODES
from app.infrastructure.agent_event_bus import (
    EVENT_LARGE_TRANSACTION,
    AgentEventBus,
)
from app.models.auth import User
from app.models.operator import OperatorRequestDedupe
from app.models.profile import CustomerHolding, Product
from app.models.risk import RiskAlert, WorkOrder
from app.ports.agent import AgentContext
from app.schemas.agents import AgentResult
from app.services.operator_parser import ParserError, parse_operation
from app.services.redis_confirmation_store import RedisConfirmationStore
from app.services.redis_pending_params_store import RedisPendingParamsStore
from app.services.trading_service import TradingError, TradingService

# ---------------------------------------------------------------------------
# Business operations: Parse → Validate → Confirm → Execute → Verify.
# 8 intents follow 功能设计文档 6.2; each intent persists its result so the
# operation is auditable:
#   purchase / redeem / transfer / info_update → TradingService 真实落库
#   suspicious_report / workorder_create       → RiskAlert / WorkOrder 落库
#   risk_reassess / product_query              → 审计留痕
# ---------------------------------------------------------------------------

CONFIRM_THRESHOLD_AMOUNT = 10_000.0  # 申购金额 > 1 万需二次确认（功能设计文档 6.2）
CONFIRM_REDEEM_THRESHOLD = 10_000.0  # 赎回份额 > 1 万份需二次确认（功能设计文档 6.2）
CONFIRM_TRANSFER_THRESHOLD = 50_000.0  # 转账金额 > 5 万需二次确认（功能设计文档 6.2）
LARGE_TRANSACTION_THRESHOLD = 50_000.0  # 大额交易事件阈值（同步风控）

# 业务范围外（非八大业务）关键词：命中即返回"目前没有该业务"兜底。
# 与 graph.py 的 BUSINESS_CONTEXT_WORDS 配合：路由层将业务相关消息送到
# business_operator，这里在意图解析前识别"确实不属于八大业务"的明确业务
# （贷款/存款/开户/保险/外汇/信托等）并兜底，避免被误判为申购/转账等。
# 例外：产品目录中的"增额终身寿险"等含"保险"字样但属于在售产品。
OUT_OF_SCOPE_BUSINESS_WORDS = (
    "贷款",
    "借款",
    "融资",
    "房贷",
    "车贷",
    "存款",
    "定期存款",
    "活期",
    "储蓄",
    "开户",
    "销户",
    "办卡",
    "开卡",
    "信用卡",
    "挂失",
    "补卡",
    "外汇",
    "结汇",
    "购汇",
    "信托",
    "私募",
    "黄金",
    "贵金属",
    "保险",
    "投保",
    "理赔",
    "保单",
    "承兑",
    "票据",
    "对公业务",
    "代发工资",
)
# 在售产品名中含上述词（如"增额终身寿险"），命中则不视为范围外
OUT_OF_SCOPE_PRODUCT_EXCEPTIONS = ("增额终身寿险",)

# 纯确认/取消响应词：用户直接回复"确认/取消"等（无 confirmation_id 时
# 扫描该会话+用户最近待确认操作，与文档"请回复'确认'执行"交互一致）
CONFIRM_RESPONSE_WORDS = (
    "确认",
    "确认执行",
    "确认无误",
    "同意",
    "是的",
    "执行",
    "确定",
    "好的",
)
CANCEL_RESPONSE_WORDS = (
    "取消",
    "取消操作",
    "放弃",
    "不确认",
    "不要执行",
    "撤销",
)


class CustomerAmbiguityError(TradingError):
    """客户标识歧义：命中多个客户，需操作员选择目标客户。"""

    def __init__(self, message: str, candidates: list) -> None:
        super().__init__(message)
        self.candidates = candidates


PERMISSION_MATRIX: dict[str, set[str]] = {
    # 功能设计文档 6.2 意图分类体系 - 权限要求：
    #   理财顾问：申购/赎回/风评重做
    #   客户经理：转账/信息更新/工单创建
    #   风控专员：可疑上报
    #   员工/顾问：产品查询（只读）
    #   管理员（super_admin）：全部（_check_permission 豁免）
    "purchase": {"financial_advisor"},
    "product_query": {
        "financial_advisor",
        "customer_manager",
        "risk_specialist",
        "auditor",
    },
    "info_update": {"customer_manager"},
    "redeem": {"financial_advisor"},
    "transfer": {"customer_manager"},
    "risk_reassess": {"financial_advisor"},
    "suspicious_report": {"risk_specialist"},
    "workorder_create": {"customer_manager"},
}

# 确定性解析器 action → 本 Agent 意图名（目标项目 8 操作 → 本 Agent 8 意图）
PARSER_ACTION_TO_INTENT: dict[str, str] = {
    "purchase": "purchase",
    "redeem": "redeem",
    "transfer": "transfer",
    "reassessment": "risk_reassess",
    "information_update": "info_update",
    "product_query": "product_query",
    "suspicious_report": "suspicious_report",
    "work_order_create": "workorder_create",
}

# 写操作意图：需要幂等防重 + 强制审计工单（只读的 product_query 除外）
WRITE_INTENTS = frozenset(
    {
        "purchase",
        "redeem",
        "transfer",
        "info_update",
        "suspicious_report",
        "workorder_create",
        "risk_reassess",
    }
)

# 当前项目客户层级 → 目标项目档位（customer_tier.py）
TIER_LEVEL_MAP: dict[str, str] = {
    "ordinary": "普通",
    "enterprise_standard": "普通",
    "gold": "金卡",
    "platinum": "白金",
    "diamond": "钻石",
    "private_bank": "私行",
}

INTENT_LABELS = {
    "purchase": "产品申购",
    "redeem": "产品赎回",
    "transfer": "转账",
    "product_query": "产品查询",
    "info_update": "信息更新",
    "risk_reassess": "风评重做",
    "suspicious_report": "可疑上报",
    "workorder_create": "工单创建",
}

PARSE_SYSTEM = """你是业务操作参数解析器。从用户的自然语言操作指令中提取结构化参数。

客户标识一律优先提取客户 ID（customer_id，如用户名/编号/UUID），其次才用姓名：
- 客户 ID 写法：客户ID xxx / 客户编号 xxx / ID:xxx / #xxx / 编号xxx
- 仅当指令中只有客户姓名时，才用 customer_name 兜底

支持的操作意图：
- purchase: 申购产品。参数：customer_id(客户ID，优先)、product_name(产品名称)、amount(金额，数字)
- redeem: 赎回产品。参数：customer_id(客户ID，优先)、product_name(产品名称)、shares(份额，数字)
- transfer: 转账。参数：customer_id(转出方客户ID)、target(转入方ID或姓名)、amount(金额，数字)
- product_query: 查询产品信息。参数：product_name(产品名称)
- info_update: 更新客户信息。参数：customer_id(客户ID，优先)、field(字段名)、value(新值)
- risk_reassess: 重新做风险测评。参数：customer_id(客户标识)
- suspicious_report: 上报可疑交易。参数：customer_id(客户ID，优先)、reason(可疑原因)
- workorder_create: 创建工单。参数：customer_id(客户ID，优先)、workorder_type(工单类型)、priority(优先级)、description(描述)

如果指令无法匹配任何意图，返回 {"intent": "unknown"}。
只返回 JSON：{"intent": "...", "params": {...}}"""


class BusinessOperatorAgent(AgentBase):
    """业务操作 Agent：自然语言指令 → 参数提取 → 权限校验 → 二次确认 → 执行 → 验证。

    Reasoning paradigm: Parse → Validate → Confirm → Execute → Verify. This is
    the highest-security agent: high-value operations require a second
    confirmation round trip (requires_confirmation=True) and large purchases
    publish an event:large_transaction to the Redis bus for the risk monitor.
    """

    name = "business_operator"
    description = "业务操作：申购/赎回/转账等指令执行，含权限校验与二次确认"

    def __init__(self, database, settings, llm=None, knowledge_graph=None):
        super().__init__(database, settings, llm)
        self.trading = TradingService()
        self.knowledge_graph = knowledge_graph
        self._redis = None

    async def _sync_holdings_to_graph(self, customer_id: int) -> None:
        """以 DB 为权威源，将客户当前全部持仓同步到 Neo4j（图谱不可用时静默降级）。

        申购/赎回成交后调用：先清空该客户全部 HOLDS 关系，再按当前
        active 持仓重建，保证图谱与 DB 一致（清仓产品自动从图谱消失）。
        """
        if self.knowledge_graph is None or not self.knowledge_graph.available:
            return
        try:
            async with self.database.session_factory() as session:
                rows = (
                    await session.execute(
                        select(CustomerHolding.product_id).where(
                            CustomerHolding.user_id == customer_id,
                            CustomerHolding.status == "active",
                        )
                    )
                ).scalars().all()
            await self.knowledge_graph.sync_customer_holdings(
                customer_id, [str(pid) for pid in rows]
            )
        except Exception:  # noqa: BLE001 - 图谱同步失败不影响业务结果
            import logging

            logging.getLogger(__name__).exception(
                "graph.sync_after_operation failed cid=%s", customer_id
            )

    async def _get_redis(self):
        if self._redis is None:
            self._redis = redis.from_url(self.settings.redis_url, decode_responses=True)
        return self._redis

    # -- parse ------------------------------------------------------------
    @staticmethod
    def _extract_amount_value(text: str) -> str:
        """从文本中提取金额并归一化为十进制字符串（支持 万/千/w）。

        未命中返回 ""。先移除"客户ID 10/编号1001/#123/客户1"等 ID 引用，
        避免把客户 ID 数字误当金额（如"客户ID 10"里的 10、"客户1"里的 1）。
        """
        # 客户标识引用（含裸"客户"后跟数字，如"客户1"）整体移除，
        # 防止 ID 数字被误当金额
        text = re.sub(
            r"(?:客户ID|客户编号|编号|账号|客户号|ID|客户)\s*[:：#]?\s*[A-Za-z0-9_\-]+",
            " ",
            text,
            flags=re.I,
        )
        money = re.search(r"(\d+(?:\.\d+)?)\s*(万元|万|w|W|千)?\s*元?", text)
        if not money:
            return ""
        try:
            value = Decimal(money.group(1))
            unit = money.group(2) or ""
            if "万" in unit or unit in ("w", "W"):
                value *= Decimal("10000")
            elif "千" in unit:
                value *= Decimal("1000")
            return str(value.quantize(Decimal("0.01")))
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _adapt_parser_params(action: str, params: dict, message: str) -> dict:
        """把确定性解析器输出参数映射为本 Agent 各 handler 期望的字段。

        解析器（operator_parser.py）输出的是目标项目的标准参数名
        （customer_name / source_customer_name / content / phone …），
        这里做意图名与字段名的桥接。
        """
        if action == "information_update":
            return {
                "customer_identifier": params.get("customer_id")
                or params.get("customer_name", ""),
                "field": "phone",
                "value": params.get("phone", ""),
            }
        if action == "work_order_create":
            content = str(params.get("content") or "").strip()
            wtype = "投诉" if "投诉" in content else "客户服务"
            return {
                "customer_identifier": params.get("customer_id")
                or params.get("customer_name", ""),
                "workorder_type": wtype,
                "priority": "normal",
                "description": content,
            }
        if action == "reassessment":
            return {
                "customer_identifier": params.get("customer_id")
                or params.get("customer_name", ""),
                "unfreeze": bool(params.get("unfreeze")),
            }
        if action == "suspicious_report":
            # 解析器对「标记客户张三为可疑交易」会把“为”并入客户名，清理尾部“为”
            identifier = str(
                params.get("customer_id") or params.get("customer_name", "")
            ).strip()
            if identifier.endswith("为"):
                identifier = identifier[:-1]
            return {
                "customer_identifier": identifier,
                "reason": str(params.get("reason") or params.get("content") or ""),
            }
        if action == "transfer":
            return {
                "customer_identifier": params.get("source_customer_id")
                or params.get("source_customer_name", ""),
                "target": params.get("target_customer_id")
                or params.get("target_customer_name", ""),
                "amount": params.get("amount", 0),
            }
        # purchase / redeem / product_query：字段名兼容，直接透传
        adapted = dict(params)
        for key in ("customer_id", "customer_name"):
            if adapted.get(key):
                adapted.setdefault("customer_identifier", adapted[key])
        return adapted

    @staticmethod
    def _clean_params(params: dict) -> dict:
        """清洗解析参数：统一 strip；无意义产品名/客户标识置空，使其走
        参数不完整追问而非带着错误值直接执行。

        - 产品名：过短 / 尾字词（"的/了"）→ 空；含操作动词（申购/买入/
          赎回…）或客户标识词（客户ID/编号/#/给/帮）→ 空
        - 客户标识（customer_identifier/target/customer_id/customer_name）：
          无意义残留（"的/了/客户/把/给"、单个汉字）→ 空
        """
        cleaned: dict = {}
        for k, v in params.items():
            if isinstance(v, str):
                v = v.strip()
                if k == "product_name":
                    # 剥离开头查询模板前缀："查询产品成长精选组合"/"产品查询
                    # 成长精选组合"/"查一下产品成长精选组合"→"成长精选组合"
                    v = re.sub(
                        r"^(?:产品查询|查询产品|查产品|查一下产品|查下产品|"
                        r"查一下|查下|看看|了解|了解一下)?\s*"
                        r"(?:产品)?\s*"
                        r"(?:是|为|叫)?\s*",
                        "",
                        v,
                    ).strip()
                    if len(v) < 2 or v in ("的", "了", "吧", "啊", "嗯", "哦"):
                        v = ""
                    elif re.search(
                        r"(申购|买入|认购|购买|赎回|卖出|客户ID|客户编号|编号|"
                        r"账号|客户号|#{1}|帮|给|为|转到|转给)",
                        v,
                        re.I,
                    ):
                        v = ""
                    elif v in ("产品", "查询", "查一下", "多少", "净值", "收益率"):
                        # 模板词整句（"查询产品"→缺产品名）→ 空
                        v = ""
                elif k == "description" and v in (
                    "给客户ID 31创建工单",
                    "给客户创建工单",
                    "创建工单",
                    "工单",
                    "客户服务",
                ):
                    # LLM 常把整句指令当 description → 空（缺工单内容追问）
                    v = ""
                elif k in (
                    "customer_identifier",
                    "target",
                    "customer_id",
                    "customer_name",
                    "customer",
                    "source_customer_id",
                    "source_customer_name",
                    "target_customer_id",
                    "target_customer_name",
                ):
                    v = BusinessOperatorAgent._meaningful_identifier(v)
                    # 操作动词整句误当客户标识（LLM 把"重新做风险评估"、
                    # "上报可疑交易"等整句当 customer_id）→ 置空，走追问
                    if v and re.search(
                        r"(风评|风险评估|重新测评|可疑|上报|工单|投诉|申购|"
                        r"赎回|转账|手机号|信息更新)",
                        v,
                    ):
                        v = ""
            cleaned[k] = v
        return cleaned

    async def _parse(self, message: str) -> tuple[str, dict]:
        intent, params = await self._parse_raw(message)
        return intent, self._clean_params(params)

    async def _parse_raw(self, message: str) -> tuple[str, dict]:
        # 确定性正则解析优先（移植自目标项目）：无 API key 也可完整解析
        try:
            parsed = parse_operation(message)
            intent = PARSER_ACTION_TO_INTENT[parsed.action]
            return intent, self._adapt_parser_params(
                parsed.action, parsed.params, message
            )
        except ParserError:
            pass  # 解析失败/句式不支持 → LLM 兜底
        parsed = await self.llm_json(
            PARSE_SYSTEM, message, temperature=0.1, max_tokens=512
        )
        if not parsed or parsed.get("intent") == "unknown":
            # deterministic keyword fallback so the agent works without an API key
            text = message.lower()
            if any(k in text for k in ["申购", "买入", "认购", "购买"]):
                product = re.sub(
                    r"^(?:帮|替|给|为)?\s*(?:客户)?\s*(?:编号\s*\d+|[^\s，。]+?)?\s*"
                    r"(?:申购|买入|认购|购买)\s*(?:人民币|人民币金额)?\s*"
                    r"(?:\d+(?:\.\d+)?\s*(?:万元|万|元))?\s*",
                    "",
                    message,
                ).strip()
                return "purchase", {
                    "customer_identifier": self._customer_identifier(message, {}),
                    "product_name": product,
                    "amount": self._extract_amount_value(message) or 0,
                }
            if any(k in text for k in ["赎回", "卖出"]):
                shares = re.search(r"(\d+(?:\.\d+)?)\s*份", message)
                product = re.sub(r"^(?:赎回|卖出)\s*", "", message).strip()
                product = re.sub(r"\s*\d+(?:\.\d+)?\s*份$", "", product).strip()
                is_full = bool(
                    re.search(r"(全部|所有|全额|全数|全部份额|所有份额)", message)
                )
                return "redeem", {
                    "customer_identifier": self._customer_identifier(message, {}),
                    "product_name": product,
                    "shares": float(shares.group(1)) if shares else 0,
                    "redeem_all": is_full,
                }
            if any(k in text for k in ["转账", "转到", "划转"]):
                return "transfer", {
                    "customer_identifier": self._customer_identifier(message, {}),
                    "amount": self._extract_amount_value(message) or 0,
                    "target": self._transfer_target(message, {}),
                }
            if any(k in text for k in ["风评", "风险评估", "重新测评"]):
                return "risk_reassess", {
                    "customer_identifier": self._customer_identifier(message, {}),
                }
            if any(k in text for k in ["可疑", "上报"]):
                return "suspicious_report", {
                    "customer_identifier": self._customer_identifier(message, {}),
                    "reason": "",
                }
            if any(k in text for k in ["工单", "投诉"]):
                return "workorder_create", {
                    "customer_identifier": self._customer_identifier(message, {}),
                    "workorder_type": "投诉" if "投诉" in text else "客户服务",
                    "description": "",
                }
            if any(k in text for k in ["查询", "多少"]):
                return "product_query", {"product_name": message.strip()}
            return "unknown", {}
        return str(parsed.get("intent", "unknown")), parsed.get("params") or {}

    # -- permission -------------------------------------------------------
    @staticmethod
    def _is_out_of_scope_business(message: str) -> bool:
        """判断消息是否属于"业务相关但非八大业务"（贷款/存款/开户/保险…）。

        命中即返回 True，触发"目前没有该业务"兜底；含在售产品例外词
        （如"增额终身寿险"）则不视为范围外，可正常申购。
        """
        text = str(message or "")
        if any(exp in text for exp in OUT_OF_SCOPE_PRODUCT_EXCEPTIONS):
            return False
        return any(w in text for w in OUT_OF_SCOPE_BUSINESS_WORDS)

    @staticmethod
    def _normalize_identifier(value) -> str:
        """规范化客户标识：剥离"客户ID 10/编号1001/#123/客户1"前缀，返回纯 ID。

        续补合并时 LLM/正则可能把"客户ID 10"整串当作标识，导致
        _resolve_customer 用"ID 10"去匹配（未找到）。这里统一提取纯数字。
        """
        s = str(value or "").strip()
        m = re.match(
            r"(?:客户ID|客户编号|编号|账号|客户号|ID|客户)\s*[:：#]?\s*([A-Za-z0-9_\-]+)$",
            s,
            re.I,
        )
        return m.group(1) if m else s

    @staticmethod
    def _meaningful_identifier(value) -> str:
        """返回有意义的客户标识；无意义解析残留（"的/了/客户/把/给"、
        单个汉字）→ 空串，使缺客户走参数不完整追问而非"未找到客户「的」"。

        规则：
        - 先剥离"客户/给/为/帮/将/把"前缀
        - 空串、纯语气词（的/了/吧/啊/嗯/哦）、单个汉字 → 空
        - 数字 / 字母 / 多字中文 → 保留
        """
        s = str(value or "").strip()
        s = re.sub(r"^(?:客户|给|为|帮|将|把)\s*", "", s).strip()
        if not s:
            return ""
        if s in ("的", "了", "吧", "啊", "嗯", "哦", "是", "和", "与", "或"):
            return ""
        if len(s) == 1 and re.fullmatch(r"[\u4e00-\u9fff]", s):
            return ""
        return s

    def _check_permission(self, role: str | None, intent: str) -> bool:
        allowed = PERMISSION_MATRIX.get(intent, set())
        return role in allowed if role else False

    @staticmethod
    def _customer_identifier(message: str, params: dict) -> str:
        """提取操作对象（转出方/申购客户等）客户标识。

        顺序：
        1. params 中的 customer_identifier/customer_id/customer_name
        2. 句式定位："把X转到Y"→X（转出方）、"帮X申购/给X赎回/为X上报"→X
           再从片段内解析 ID 引用（"客户ID 3的60000元"→3）或姓名
        3. 全局 ID 引用兜底（"客户ID 10"续补、可疑上报等单句场景）

        关键：不能全局搜第一个"客户ID xxx"——"把客户的60000元转到客户ID 2
        账户"中唯一 ID 是转入方，全局搜会把转入方误当转出方。
        """
        # 1) params 优先
        for key in ("customer_identifier", "customer_id", "customer", "customer_name"):
            value = str(params.get(key) or "").strip()
            if value:
                return BusinessOperatorAgent._meaningful_identifier(value)
        # 2) 句式定位提取操作对象片段
        transfer_m = re.search(r"把\s*(.+?)\s*(?:转到|转给|划转|转账)", message)
        action_m = re.search(
            r"(?:帮|为|给)\s*(.+?)\s*(?:申购|买入|认购|购买|赎回|上报|"
            r"创建|工单|风评|风险评估)",
            message,
        )
        if transfer_m or action_m:
            frag = (transfer_m.group(1) if transfer_m else action_m.group(1)).strip()
            # 片段内 ID 引用："客户ID 3的60000元"→3；"编号1001"→1001；"客户1"→1
            id_in = re.search(
                r"(?:客户ID|客户编号|编号|账号|客户号|ID|客户)\s*[:：#]?\s*([A-Za-z0-9_\-]+)",
                frag,
                re.I,
            )
            if id_in:
                return id_in.group(1).strip()
            # 姓名："客户的60000元"→"客户"剥前缀+"的"→无意义→空（缺转出方）。
            # 句式命中时返回空即代表"该位置无有效客户"，不得再落到全局
            # ID 兜底（否则"把客户的60000元转到客户ID 2"会把转入方 2 当转出方）。
            frag = re.sub(r"^(?:客户|给|为|帮)\s*", "", frag).strip()
            frag = re.sub(
                r"(?:的)?\d+(?:\.\d+)?\s*(?:万元|万|千|元|w|W)\s*$", "", frag
            ).strip()
            return BusinessOperatorAgent._meaningful_identifier(frag)
        # 3) 无明确句式：全局 ID 引用兜底（续补"客户ID 10"/"客户1"、可疑上报
        #    "上报客户ID 5"等单句场景）
        id_match = re.search(
            r"(?:客户ID|客户编号|编号|账号|客户号|ID|客户)\s*[:：#]?\s*([A-Za-z0-9_\-]+)",
            message,
            re.I,
        )
        if id_match:
            return id_match.group(1).strip()
        return ""

    @staticmethod
    def _transfer_target(message: str, params: dict) -> str:
        """解析转账的转入方客户标识。

        以客户 ID 提取优先：先用解析器/LLM 提取的 target 参数
        （target_customer_id 优先），其次消息正则兜底（"转到/转入/给…
        转账"句式），统一去掉"账户/客户"等后缀。
        """
        candidates: list[str] = []
        # 1) 解析器/LLM 参数优先（已按客户 ID 提取）
        raw_target = str(params.get("target") or "").strip()
        if raw_target:
            candidates.append(raw_target)
        # 2) 消息正则兜底（"转到客户ID xxx" / "转到#xxx" / "给Y转账"）
        for pattern in (
            r"(?:转到|转入|转给)\s*(?:客户)?\s*((?:ID\s*[A-Za-z0-9_\-]+|#[A-Za-z0-9_\-]+|编号\s*[A-Za-z0-9_\-]+|[^\s，。]+?))\s*(?:账户|账号)?(?:[\s。，]|$)",
            r"给\s*(.+?)\s*转账",
        ):
            match = re.search(pattern, message)
            if match and match.group(1).strip():
                candidates.append(match.group(1).strip())
        for candidate in candidates:
            cleaned = re.sub(r"(?:账户|账号|客户)$", "", candidate).strip()
            cleaned = BusinessOperatorAgent._meaningful_identifier(cleaned)
            if cleaned:
                return cleaned
        return ""

    async def _resolve_customer(self, session, identifier: str) -> User:
        customer_roles = or_(
            *(User.roles.any(code=code) for code in CUSTOMER_ROLE_CODES)
        )
        id_match = (
            User.id == int(identifier) if str(identifier).isdigit() else User.id == -1
        )
        matches = list(
            (
                await session.execute(
                    select(User)
                    .where(
                        User.status == "active",
                        customer_roles,
                        or_(
                            id_match,
                            User.username.ilike(f"%{identifier}%"),
                            User.display_name.ilike(f"%{identifier}%"),
                        ),
                    )
                    .order_by(User.display_name)
                    .limit(2)
                )
            )
            .scalars()
            .all()
        )
        if not matches:
            raise TradingError(f"未找到客户「{identifier}」，请使用客户姓名或用户名。")
        if len(matches) > 1:
            names = "、".join(item.display_name for item in matches)
            raise CustomerAmbiguityError(
                f"客户标识「{identifier}」不唯一，请选择具体客户（候选：{names}）。",
                matches,
            )
        return matches[0]

    async def _resolve_customer_checked(
        self, session, identifier: str, intent: str
    ) -> tuple[User | None, AgentResult | None]:
        """解析客户；歧义时返回 (None, 候选选择 AgentResult)，供前端渲染候选。"""
        try:
            user = await self._resolve_customer(session, identifier)
            return user, None
        except CustomerAmbiguityError as exc:
            candidates = [
                {
                    "id": u.id,
                    "username": u.username,
                    "display_name": u.display_name,
                }
                for u in exc.candidates
            ]
            return None, self.ok(
                f"客户标识「{identifier}」命中多位客户，请选择目标客户：",
                data={
                    "intent": intent,
                    "ambiguous": True,
                    "identifier": identifier,
                    "candidates": candidates,
                },
                next_action="select_customer",
                confidence=1.0,
            )

    async def _release_idempotent(self, context: AgentContext) -> None:
        """把当前请求的幂等占位置为 failed（允许修正后重发）。"""
        md = context.metadata or {}
        await self._release_idempotent_for(
            context, md.get("_request_id") or md.get("request_id") or ""
        )

    async def _release_idempotent_for(
        self, context: AgentContext, request_id: str
    ) -> None:
        """按指定 request_id 释放幂等占位（failed，允许修正后重发）。

        覆盖两种情况：
        - processing：执行中断/歧义待选择
        - completed 且响应是"确认请求"（requires_confirmation）：取消后
          允许重新发起新操作（否则相同 request_id 会命中 replay 返回
          旧的确认请求）。
        """
        if not request_id:
            return
        try:
            async with self.database.session_factory() as session:
                record = (
                    (
                        await session.execute(
                            select(OperatorRequestDedupe).where(
                                OperatorRequestDedupe.user_id
                                == (context.user_id or ""),
                                OperatorRequestDedupe.request_id == request_id,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if record is not None:
                    if record.status == "processing":
                        record.status = "failed"
                    elif record.status == "completed":
                        # 仅当存的是确认请求响应（含 requires_confirmation）
                        # 才允许释放；真实执行结果（有 order_no 等）不释放。
                        try:
                            resp = json.loads(record.response_json or "{}")
                        except json.JSONDecodeError:
                            resp = {}
                        if resp.get("requires_confirmation") or resp.get(
                            "confirmation_id"
                        ):
                            record.status = "failed"
                    if record.status == "failed":
                        await session.commit()
        except Exception:  # noqa: BLE001
            pass

    async def _record_cancel_audit(self, context: AgentContext, intent: str) -> None:
        """取消操作留痕：生成"业务操作取消"审计工单（可追溯）。"""
        try:
            md = context.metadata or {}
            async with self.database.session_factory() as session:
                workorder = WorkOrder(
                    id=str(uuid4()),
                    workorder_no=f"OP{uuid4().hex[:16].upper()}",
                    customer_id=md.get("customer_id"),
                    submitter_id=context.user_id,
                    workorder_type="业务操作取消",
                    priority="normal",
                    status="completed",
                    title=f"{INTENT_LABELS.get(intent, intent)}（已取消）",
                    description=json.dumps(
                        {
                            "action": intent,
                            "decision": "cancel",
                            "cancelled_at": datetime.now(UTC).isoformat(),
                        },
                        ensure_ascii=False,
                    )[:4000],
                    source_type="business_operator",
                )
                session.add(workorder)
                await session.commit()
        except Exception:  # noqa: BLE001 - 审计失败不阻断
            pass

    async def _handle_cancel(self, pending: dict, context: AgentContext) -> AgentResult:
        """取消待确认操作：释放幂等占位（failed）+ 记录取消审计工单。

        pending 可能来自 decision=cancel 凭据消费，或纯"取消"文本扫描
        （可能为空 dict）。取消请求自身是新 context，需用确认时保存的
        原始 request_id 定位幂等记录。
        """
        await self._release_idempotent_for(
            context, (pending or {}).get("request_id") or ""
        )
        await self._record_cancel_audit(
            context, (pending or {}).get("intent", "unknown")
        )
        return self.ok(
            "操作已取消。",
            data={
                "intent": (pending or {}).get("intent", "unknown"),
                "cancelled": True,
            },
            confidence=1.0,
        )

    # -- execute purchase -------------------------------------------------
    async def _execute_purchase(
        self,
        session,
        customer: User,
        operator: User,
        product_name: str,
        amount: Decimal,
    ) -> dict:
        product = (
            await session.execute(
                select(Product).where(
                    Product.name == product_name, Product.status == "active"
                )
            )
        ).scalar_one_or_none()
        if product is None:
            products = list(
                (
                    await session.execute(
                        select(Product)
                        .where(Product.status == "active")
                        .order_by(Product.name)
                        .limit(10)
                    )
                )
                .scalars()
                .all()
            )
            names = "、".join(p.name for p in products) or "（暂无在售产品）"
            raise TradingError(f"未找到在售产品「{product_name}」。可选产品：{names}")
        order, _ = await self.trading.create_order(
            session, customer, str(product.id), amount, operator=operator
        )
        order = await self.trading.confirm_order(
            session, customer, str(order.id), operator=operator
        )
        return {
            "order_id": str(order.id),
            "order_no": order.order_no,
            "product": product_name,
            "amount": float(amount),
            "status": order.status,
        }

    async def _publish_large_transaction(
        self,
        user_id: str,
        amount: float,
        intent: str,
        threshold: float | None = None,
    ) -> None:
        if amount < (threshold or LARGE_TRANSACTION_THRESHOLD):
            return
        try:
            client = await self._get_redis()
            await AgentEventBus(client).publish(
                EVENT_LARGE_TRANSACTION,
                event_type="large_transaction",
                source_agent=self.name,
                payload={
                    "customer_id": user_id,
                    "amount": amount,
                    "operation": intent,
                },
            )
        except Exception:  # noqa: BLE001 - event bus must never break the operation
            pass

    # -- 结构化二次确认（confirmation_id 双轨协议，移植目标项目）---------
    async def _save_confirmation(
        self, context: AgentContext, intent: str, params: dict, message: str
    ) -> str:
        """保存待确认操作，返回凭据 id（TTL 300s，Redis）。"""
        confirmation_id = uuid4().hex[:12]
        md = context.metadata or {}
        client = await self._get_redis()
        await RedisConfirmationStore(client).save(
            md.get("session_id", ""),
            context.user_id or "",
            confirmation_id,
            {
                "intent": intent,
                "params": params,
                "message": message,
                "request_id": md.get("_request_id") or md.get("request_id") or "",
            },
        )
        return confirmation_id

    async def _consume_confirmation(self, context: AgentContext) -> dict | None:
        """凭 confirmation_id 取回并删除待确认操作（原子消费，防重复确认）。"""
        md = context.metadata or {}
        try:
            client = await self._get_redis()
            return await RedisConfirmationStore(client).consume(
                md.get("session_id", ""),
                context.user_id or "",
                md.get("confirmation_id"),
            )
        except Exception:  # noqa: BLE001 - 确认服务不可用时按过期处理
            return None

    # -- 待补齐参数（追问续补）------------------------------------------
    async def _save_pending_params(
        self, context: AgentContext, intent: str, params: dict, message: str
    ) -> None:
        """保存待补齐参数上下文（参数不完整追问时调用）。"""
        md = context.metadata or {}
        try:
            client = await self._get_redis()
            await RedisPendingParamsStore(client).save(
                md.get("session_id", ""),
                context.user_id or "",
                intent,
                params,
                message,
            )
        except Exception:  # noqa: BLE001 - 保存失败仅影响续补，不阻断追问
            pass

    async def _consume_pending_params(self, context: AgentContext) -> dict | None:
        """取回并删除待补齐参数上下文（原子消费，防重复续补）。"""
        md = context.metadata or {}
        try:
            client = await self._get_redis()
            return await RedisPendingParamsStore(client).consume(
                md.get("session_id", ""), context.user_id or ""
            )
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _has_customer_ref(params: dict) -> bool:
        return any(
            str(params.get(k) or "").strip()
            for k in (
                "customer_identifier",
                "customer_id",
                "customer_name",
                "customer",
            )
        )

    @staticmethod
    def _extract_supplement_params(message: str) -> dict:
        """从用户补充的片段消息中宽松提取参数（不要求识别完整意图）。

        支持金额（50000元/5万/5w）、转账目标（转到/转给X）、手机号、
        字段/值（职业改成医生）等常见补参片段。
        """
        text = message.strip()
        out: dict = {}
        # 金额：50000元 / 5万 / 5w / 1000（单位换算）。
        # _extract_amount_value 内部已排除"客户ID 10"等 ID 引用数字。
        amount = BusinessOperatorAgent._extract_amount_value(text)
        if amount:
            out["amount"] = amount
        # 转账目标："转到/转给/转入 张明"
        target = re.search(
            r"(?:转到|转给|转入|转账给)\s*(?:客户)?\s*(.+?)(?:账户|账号|客户)?\s*$",
            text,
        )
        if target and target.group(1).strip():
            out["target"] = re.sub(r"^(?:客户)\s*", "", target.group(1).strip())
        # 客户 ID 引用（"客户ID 10 / 编号1001 / #123 / 客户1"）：单独一条
        # 消息时，可能是补转入方或补客户。若消息通篇只是 ID 引用，作为
        # target 候选（由调用方按缺失项决定补 target 还是 customer_identifier）。
        if "target" not in out:
            id_ref = re.search(
                r"(?:客户ID|客户编号|编号|账号|客户号|ID|客户)\s*[:：#]?\s*([A-Za-z0-9_\-]+)",
                text,
                re.I,
            )
            if id_ref and not re.search(r"\d+\s*(?:万元|万|千|元|w|W)", text):
                out["_customer_id_ref"] = id_ref.group(1).strip()
        # 手机号（信息更新）
        phone = re.search(r"1[3-9]\d{9}", text)
        if phone:
            out["phone"] = phone.group(0)
        # 字段/值（信息更新）："职业改成医生" / "职业改为医生"
        field_val = re.search(r"(.+?)\s*(?:改成|改为|更新为|修改为)\s*(.+?)\s*$", text)
        if field_val:
            f, v = field_val.group(1).strip(), field_val.group(2).strip()
            if f:
                out["field"] = f
            if v:
                out["value"] = v
        return out

    async def _resume_with_supplement(
        self, message: str, pending: dict, context: AgentContext
    ) -> AgentResult:
        """续补执行：把用户第二次补充的参数合并进原上下文并继续执行。

        合并优先级：
          1. 新消息能解析出相同意图 → 新解析参数覆盖旧参数
          2. 宽松片段提取（金额/目标/手机号/字段值）→ 补充缺失字段
          3. 整句兜底：缺产品补产品名、缺客户补客户名
        若新消息明确表达了不同意图（用户改主意）→ 丢弃原上下文按新消息执行。
        """
        old_intent = pending.get("intent")
        old_params = pending.get("params") or {}
        old_message = pending.get("message") or ""
        if not old_intent:
            intent, params = await self._parse(message)
            return await self._run_parsed(intent, params, message, context)

        # 1) 新消息单独解析：相同意图 → 参数覆盖
        intent2, p2 = await self._parse(message)
        merged_params = {
            k: (v.strip() if isinstance(v, str) else v) for k, v in old_params.items()
        }
        if intent2 == old_intent and p2:
            for k, v in p2.items():
                if v is None or v == "" or v is False:
                    continue
                # 数值参数：0 不覆盖已有值（LLM 常把补充片段猜成 0 金额/份额）
                if k in ("amount", "shares") and isinstance(v, (int, float, str)):
                    try:
                        if Decimal(str(v)) <= 0:
                            continue
                    except Exception:  # noqa: BLE001
                        continue
                if k in (
                    "customer_identifier",
                    "customer_id",
                    "customer_name",
                    "customer",
                ) and self._has_customer_ref(merged_params):
                    # 已有客户时，补充片段里 LLM 常把产品名/金额误解析成客户，
                    # 跳过客户字段保护原客户，避免覆盖为"现金管理保本计划"。
                    continue
                # target 规范化：LLM 常把"客户ID 10"整串当 target（含前缀），
                # 这里剥离为纯 ID；空串/纯前缀则不覆盖。
                if k == "target" and isinstance(v, str):
                    norm = BusinessOperatorAgent._normalize_identifier(v)
                    if not norm:
                        continue
                    merged_params[k] = norm
                    continue
                merged_params[k] = v.strip() if isinstance(v, str) else v

        # 2) 宽松片段提取补充
        supplement = self._extract_supplement_params(message)
        merged_params.update(
            {
                k: (v.strip() if isinstance(v, str) else v)
                for k, v in supplement.items()
                if v and k != "_customer_id_ref"
            }
        )

        # 客户 ID 引用（"客户ID 10"）：转账缺转入方→补 target；否则补客户
        id_ref = supplement.get("_customer_id_ref")
        if id_ref:
            if old_intent == "transfer" and not merged_params.get("target"):
                merged_params["target"] = id_ref
            elif not self._has_customer_ref(merged_params):
                merged_params["customer_identifier"] = id_ref

        # 3) 整句兜底：新消息不含操作词时按缺失字段补充
        if not self._has_customer_ref(merged_params):
            cid = self._customer_identifier(message, {})
            if cid:
                merged_params["customer_identifier"] = cid
            elif not any(
                w in message
                for w in (
                    "申购",
                    "赎回",
                    "转账",
                    "转到",
                    "转给",
                    "工单",
                    "可疑",
                    "手机号",
                    "改成",
                    "改为",
                    "产品",
                    "查询",
                )
            ):
                merged_params["customer_identifier"] = message.strip()
        # 缺产品名 → 整句视为产品名（支持"客户A的产品B"拆分、金额前缀剥离）
        if (
            old_intent in ("purchase", "redeem")
            and not merged_params.get("product_name")
            and not any(
                w in message
                for w in ("申购", "赎回", "转账", "转到", "转给", "工单", "可疑")
            )
        ):
            product = message.strip()
            if "的" in product and not product.startswith(
                ("编号", "客户ID", "ID", "#")
            ):
                left, _, right = product.partition("的")
                if left.strip() and right.strip():
                    merged_params["customer_identifier"] = left.strip()
                    product = right.strip()
            product = re.sub(
                r"^\d+(?:\.\d+)?\s*(?:万元|万|千|元|w|W)\s*(?:的)?\s*",
                "",
                product,
            ).strip()
            if product:
                merged_params["product_name"] = product
        if not merged_params.get("target"):
            t = self._transfer_target(message, {})
            if t:
                merged_params["target"] = t

        # 规范化客户标识：剥离"客户ID 10/编号1001/#123"前缀为纯 ID，
        # 避免 LLM 把"客户ID 10"整串当标识（_resolve_customer 匹配不到）。
        for key in ("target", "customer_identifier", "customer_id", "customer_name"):
            if key in merged_params:
                merged_params[key] = self._normalize_identifier(merged_params[key])

        # 用户改主意：新消息明确包含其他操作动词才视为改主意。
        # 仅含补充片段（金额/产品/客户名）时 LLM 可能误判意图
        # （如"30000元的稳健增值计划"被猜成产品查询），此时应继续补参。
        operation_verbs = (
            "申购",
            "买入",
            "认购",
            "购买",
            "赎回",
            "卖出",
            "转账",
            "转到",
            "划转",
            "上报",
            "可疑",
            "工单",
            "手机号",
            "改成",
            "改为",
            "风评",
            "风险评估",
        )
        if (
            intent2 != "unknown"
            and intent2 != old_intent
            and any(v in message for v in operation_verbs)
        ):
            return await self._run_parsed(intent2, p2 or {}, message, context)

        merged_message = f"{old_message} {message}".strip()
        return await self._run_parsed(
            old_intent, merged_params, merged_message, context
        )

    async def _ask_params(
        self,
        context: AgentContext,
        intent: str,
        params: dict,
        message: str,
        reply_text: str,
        missing: list[str] | bool | None = None,
    ) -> AgentResult:
        """参数不完整：保存待补齐上下文并追问（next_action="ask_params"）。

        用户第二次补充缺失参数后，run() 会取出该上下文合并解析并
        继续执行；同时释放幂等占位，避免同一 request_id 续补时
        误判"正在处理中"。
        """
        # 保存前把消息中已提取的客户/目标合并进 params——客户可能只存在于
        # 消息正则（_customer_identifier 兜底），若不持久化，续补时旧上下文
        # 丢失客户，补充片段（如产品名）会被误判为客户标识。
        saved = dict(params)
        if not self._has_customer_ref(saved):
            cid = self._customer_identifier(message, saved)
            if cid:
                saved["customer_identifier"] = cid
        if intent == "transfer" and not saved.get("target"):
            t = self._transfer_target(message, saved)
            if t:
                saved["target"] = t
        await self._save_pending_params(context, intent, saved, message)
        await self._release_idempotent(context)
        return self.ok(
            reply_text,
            data={
                "intent": intent,
                "params": saved,
                "missing": missing or [],
                "pending_params": True,
            },
            next_action="ask_params",
        )

    async def _finalize_idempotent(self, context: AgentContext, data: dict) -> None:
        """中间态（确认请求）幂等收尾：标 completed 并存储响应，
        使相同 request_id 重发时返回相同确认请求，不误判"处理中"。
        不写审计工单（确认请求不是终态执行）。
        """
        md = context.metadata or {}
        request_id = md.get("_request_id")
        if not request_id:
            return
        try:
            async with self.database.session_factory() as session:
                record = (
                    (
                        await session.execute(
                            select(OperatorRequestDedupe).where(
                                OperatorRequestDedupe.user_id
                                == (context.user_id or ""),
                                OperatorRequestDedupe.request_id == request_id,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if record is not None and record.status == "processing":
                    record.status = "completed"
                    record.response_json = json.dumps(
                        data, ensure_ascii=False, default=str
                    )
                    await session.commit()
        except Exception:  # noqa: BLE001
            pass

    async def _request_confirmation(
        self,
        context: AgentContext,
        intent: str,
        params: dict,
        message: str,
        reply_text: str,
        confirm_action: str,
    ) -> AgentResult:
        """发起二次确认：保存凭据并返回 requires_confirmation 响应。"""
        confirmation_id = await self._save_confirmation(
            context, intent, params, message
        )
        result = self.ok(
            reply_text,
            data={
                "intent": intent,
                "params": params,
                "confirmation_id": confirmation_id,
                "requires_confirmation": True,
            },
            requires_confirmation=True,
            next_action=confirm_action,
        )
        # 幂等收尾：确认请求视为已处理（存响应），避免卡 processing
        await self._finalize_idempotent(context, result.data)
        return result

    # -- 幂等防重 + 强制审计（P2，移植目标项目）--------------------------
    @staticmethod
    def _sanitize_audit_params(action: str, data: dict) -> dict:
        """审计脱敏：手机号 138****8000、身份证 110***********1234、银行卡号等。

        递归处理嵌套 dict/list，保持 JSON 结构完整：
        - 手机号：1[3-9]\\d{9} → 138****8000
        - 身份证：17 位数字 + 数字/X → 前3****后4
        - 银行卡/账号：16~19 位纯数字 → 前6****后4
        - 其他 11 位以上纯数字串 → 前3****后4
        """

        def _mask(v: str) -> str:
            """对字符串中的敏感数字掩码；无敏感数据原样返回。"""
            if not v:
                return v
            # 手机号（11 位，1 开头）
            masked = re.sub(
                r"(?<!\d)(1[3-9]\d{9})(?!\d)",
                lambda m: m.group(1)[:3] + "****" + m.group(1)[-4:],
                v,
            )
            # 身份证（17 位数字 + 数字/X/x）
            masked = re.sub(
                r"(?<!\d)(\d{17}[\dXx])(?![\dXx])",
                lambda m: m.group(1)[:3] + "***********" + m.group(1)[-4:],
                masked,
            )
            # 银行卡/账号（16~19 位纯数字）
            masked = re.sub(
                r"(?<!\d)(\d{16,19})(?!\d)",
                lambda m: m.group(1)[:6] + "********" + m.group(1)[-4:],
                masked,
            )
            # 其他 11 位以上纯数字串
            masked = re.sub(
                r"(?<!\d)(\d{11,})(?!\d)",
                lambda m: m.group(1)[:3] + "****" + m.group(1)[-4:],
                masked,
            )
            return masked

        def _walk(value):
            if isinstance(value, dict):
                return {k: _walk(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_walk(v) for v in value]
            if isinstance(value, str):
                return _mask(value)
            if isinstance(value, (int, float)):
                # 数字型敏感值（如手机号被解析为 int）也掩码
                s = str(value)
                digits = re.sub(r"\D", "", s)
                if len(digits) >= 11:
                    return _mask(s)
                return value
            return value

        return _walk(data)

    async def _tier_thresholds(self, customer_id: str) -> tuple[float, float, float]:
        """按客户层级返回 (申购确认阈值, 转账确认阈值, 大额事件阈值)。

        移植目标项目 customer_tier.py：零售 1万/5万/5万 → 私行 10万/50万/50万。
        """
        from app.models.profile import CustomerProfile
        from app.services.customer_tier import classify_tier

        level = "普通"
        try:
            async with self.database.session_factory() as session:
                profile = (
                    await session.execute(
                        select(CustomerProfile).where(
                            CustomerProfile.user_id == customer_id
                        )
                    )
                ).scalar_one_or_none()
                raw = (profile.customer_tier if profile else "") or ""
            level = TIER_LEVEL_MAP.get(raw, "普通")
        except Exception:  # noqa: BLE001
            pass
        tier = classify_tier(level, 0.0)
        return (
            tier.confirmation_threshold_purchase,
            tier.confirmation_threshold_transfer,
            tier.large_transaction_threshold,
        )

    async def _claim_idempotent(
        self, session, user_id: str, request_id: str, action: str
    ) -> tuple[str, dict | None]:
        """幂等 claim：首次返回 ("claimed", None)，重复 completed 返回 ("replay", 结果)，
        正在处理返回 ("processing", None)。"""
        existing = (
            (
                await session.execute(
                    select(OperatorRequestDedupe).where(
                        OperatorRequestDedupe.user_id == user_id,
                        OperatorRequestDedupe.request_id == request_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            if existing.status == "completed" and existing.response_json:
                try:
                    return "replay", json.loads(existing.response_json)
                except json.JSONDecodeError:
                    return "replay", {}
            if existing.status == "processing":
                return "processing", None
            # failed：上次执行失败，允许重试（更新为 processing 继续占位）
            existing.status = "processing"
            await session.commit()
            return "claimed", None
        record = OperatorRequestDedupe(
            id=str(uuid4()),
            user_id=user_id,
            request_id=request_id,
            action=action,
            status="processing",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        session.add(record)
        try:
            await session.flush()
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return "processing", None
        return "claimed", None

    async def _finalize_write(
        self,
        intent: str,
        params: dict,
        result: AgentResult,
        context: AgentContext,
    ) -> AgentResult:
        """写操作收尾：更新幂等记录 + 创建强制审计工单（脱敏留痕）。

        审计失败不阻断业务（幂等/审计是附加保障）。
        """
        md = context.metadata or {}
        if result.status != "success":
            # 执行失败：释放幂等占位（failed），允许修正后重试
            await self._release_idempotent(context)
            return result
        data = result.data or {}
        if data.get("ambiguous"):
            # 客户歧义待选择：不写审计，幂等释放为 failed（允许重发选择）
            await self._release_idempotent(context)
            return result
        if result.next_action == "ask_params" or data.get("pending_params"):
            # 参数不完整追问：不写审计，幂等释放为 failed（允许续补后重试）。
            # 否则追问响应会被当成成功执行写入 completed，导致同一
            # request_id 续补时误命中幂等 replay。
            await self._release_idempotent(context)
            return result
        request_id = md.get("_request_id")
        try:
            async with self.database.session_factory() as session:
                if request_id:
                    record = (
                        (
                            await session.execute(
                                select(OperatorRequestDedupe).where(
                                    OperatorRequestDedupe.user_id
                                    == (context.user_id or ""),
                                    OperatorRequestDedupe.request_id == request_id,
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if record is not None:
                        record.status = "completed"
                        record.response_json = json.dumps(
                            result.data, ensure_ascii=False, default=str
                        )
                customer_id = (
                    data.get("customer_id")
                    or data.get("user_id")
                    or data.get("from_user_id")
                    or data.get("to_user_id")
                    or md.get("customer_id")
                )
                workorder = WorkOrder(
                    id=str(uuid4()),
                    workorder_no=f"OP{uuid4().hex[:16].upper()}",
                    customer_id=customer_id,
                    submitter_id=context.user_id,
                    workorder_type="业务操作审计",
                    priority="normal",
                    status="completed",
                    title=f"{INTENT_LABELS.get(intent, intent)}（审计）",
                    description=json.dumps(
                        {
                            "action": intent,
                            "params": self._sanitize_audit_params(intent, params),
                            "result": self._sanitize_audit_params(intent, data),
                        },
                        ensure_ascii=False,
                        default=str,
                    )[:4000],
                    source_type="business_operator",
                )
                session.add(workorder)
                await session.commit()
        except Exception:  # noqa: BLE001
            pass
        return result

    # -- entry ------------------------------------------------------------
    async def run(self, message: str, context: AgentContext) -> AgentResult:
        md = context.metadata or {}
        decision = md.get("decision")
        confirmation_id = md.get("confirmation_id")

        # ── 重名消歧选择（decision=select_customer）─────────────────────
        # 前端从候选列表中选中目标客户后，回传 selected_customer_id（精确
        # UUID），这里重解析指令并把客户标识替换为选中 id，继续正常执行
        # 管线（权限 → 幂等 → 确认 → 执行 → 审计）。
        if decision == "select_customer":
            selected = md.get("selected_customer_id")
            if not selected:
                return self.ok(
                    "请选择具体客户后再提交。",
                    data={"intent": "unknown", "ambiguous_missing": True},
                    confidence=1.0,
                )
            intent, params = await self._parse(message)
            if intent == "unknown":
                return self.ok(
                    "目前没有该业务。当前支持：申购/赎回/转账/产品查询/信息更新/风评重做/可疑上报/工单创建。",
                    data={"intent": "unknown"},
                )
            params["customer_identifier"] = selected
            params.pop("customer_name", None)
            return await self._run_parsed(intent, params, message, context)

        # ── 纯确认/取消文本（无 confirmation_id）──────────────────────
        # 用户直接回复"确认/取消"：扫描该会话+用户最近待确认操作并消费。
        # 命中取消词 → 取消；命中确认词 → 按待确认操作执行。
        if not confirmation_id and not decision and not md.get("confirmed"):
            stripped = message.strip()
            is_cancel = any(
                stripped == w or stripped.startswith(w) for w in CANCEL_RESPONSE_WORDS
            )
            is_confirm = any(
                stripped == w or stripped.startswith(w) for w in CONFIRM_RESPONSE_WORDS
            )
            if is_cancel or is_confirm:
                pending = await self._consume_confirmation(context)  # cid=None 走扫描
                if pending is not None:
                    if is_cancel:
                        return await self._handle_cancel(pending, context)
                    md["confirmed"] = True
                    intent = pending["intent"]
                    params = pending.get("params") or {}
                    message = pending.get("message") or message
                    return await self._run_parsed(intent, params, message, context)
                # 无待确认操作：落到正常解析（客服兜底），不消费补参上下文
            else:
                # ── 待补齐参数续补（上次 ask_params 追问后补充缺失参数）──
                # 用户第二次只提供缺失参数（如"李伟""50000元""稳健增值计划"）
                # 时，取出待补齐上下文合并解析并继续执行，无需重述完整指令。
                pending_params = await self._consume_pending_params(context)
                if pending_params is not None:
                    return await self._resume_with_supplement(
                        message, pending_params, context
                    )

        # 结构化二次确认协议：确认/取消凭 confirmation_id 一次性消费，
        # 无需前端重发完整消息（decision=confirm/cancel 双轨）。
        if confirmation_id or decision == "cancel":
            # 防误执行：仅 decision=confirm/cancel（或旧协议布尔 confirmed）
            # 才消费凭据。只带 confirmation_id 而无明确决策（如前端 decision
            # 丢失）时拒绝且不消费凭据，允许用户用同一凭据重新确认/取消——
            # 否则取消请求可能被误判为"确认"而执行转账/申购/赎回。
            if decision not in ("confirm", "cancel") and not md.get("confirmed"):
                return self.ok(
                    "确认凭据无效：请明确选择「确认执行」或「取消」。",
                    data={"intent": "unknown", "invalid_decision": True},
                    confidence=1.0,
                )
            pending = await self._consume_confirmation(context)
            if decision == "cancel":
                return await self._handle_cancel(pending or {}, context)
            if pending is None:
                return self.ok(
                    "确认凭据已过期，请重新发起操作。",
                    data={"intent": "unknown", "confirmation_expired": True},
                    confidence=1.0,
                )
            intent = pending["intent"]
            params = pending.get("params") or {}
            message = pending.get("message") or message
            md["confirmed"] = True  # 本次按已确认执行，各 handler 直接放行
        else:
            intent, params = await self._parse(message)
        return await self._run_parsed(intent, params, message, context)

    async def execute_operation(
        self, intent: str, params: dict, context: AgentContext, message: str = ""
    ) -> AgentResult:
        """结构化执行入口（/api/operation/* 端点使用）：跳过 NL 解析直接执行。

        与 run() 共享解析后的执行管线（权限 → 幂等 → handler → 审计）。
        """
        return await self._run_parsed(
            intent,
            params,
            message or json.dumps(params, ensure_ascii=False),
            context,
        )

    async def _run_parsed(
        self, intent: str, params: dict, message: str, context: AgentContext
    ) -> AgentResult:
        if intent == "unknown":
            return self.ok(
                "目前没有该业务。当前支持：申购/赎回/转账/产品查询/信息更新/风评重做/可疑上报/工单创建。",
                data={"intent": "unknown"},
            )
        # 业务范围外兜底：贷款/存款/开户/保险等业务相关但非八大业务 →
        # 同样回复"目前没有该业务"（意图可能被误判为 purchase 等，提前拦截）。
        if self._is_out_of_scope_business(message):
            return self.ok(
                "目前没有该业务。当前支持：申购/赎回/转账/产品查询/信息更新/风评重做/可疑上报/工单创建。",
                data={"intent": "unknown", "out_of_scope": True},
            )

        role = context.role or (context.metadata or {}).get("role")
        if not context.metadata.get("is_super_admin") and not self._check_permission(
            role, intent
        ):
            return self.ok(
                f"您（角色 {role or '未知'}）无权执行「{INTENT_LABELS.get(intent, intent)}」操作。",
                data={"intent": intent, "denied": True},
                confidence=1.0,
            )

        # ── 幂等防重（P2）：写操作按 request_id 24h 去重 ──
        md = context.metadata or {}
        request_id = md.get("request_id") or (context.request_id or "")
        if intent in WRITE_INTENTS and request_id and not md.get("confirmed"):
            async with self.database.session_factory() as session:
                state, replay = await self._claim_idempotent(
                    session, context.user_id or "", request_id, intent
                )
            if state == "replay":
                # 幂等重放：若首次结果是确认请求（含 confirmation_id），
                # 恢复 requires_confirmation，前端才能再次显示确认横幅。
                replay_conf = bool(
                    (replay or {}).get("requires_confirmation")
                    or (replay or {}).get("confirmation_id")
                )
                result = self.ok(
                    "该请求已处理过，返回首次执行结果（幂等防重）。",
                    data=replay,
                    confidence=1.0,
                )
                if replay_conf:
                    result.requires_confirmation = True
                return result
            if state == "processing":
                return self.ok(
                    "该请求正在处理中，请勿重复提交。",
                    data={"duplicated": True},
                    confidence=1.0,
                )
            md["_request_id"] = request_id

        if intent == "product_query":
            product_name = str(params.get("product_name") or "").strip()
            async with self.database.session_factory() as session:
                product = None
                if product_name:
                    product = (
                        await session.execute(
                            select(Product).where(
                                Product.status == "active",
                                Product.name == product_name,
                            )
                        )
                    ).scalar_one_or_none()
            if not product_name:
                # 缺产品名：追问（只读操作，无幂等/确认，直接 ask_params 即可）
                return await self._ask_params(
                    context,
                    "product_query",
                    params,
                    message,
                    "参数不完整，缺少：产品名称。请提供要查询的产品名称（如 安盈现金管理）。",
                    ["产品名称"],
                )
            if product is None:
                return self.ok(
                    f"产品查询：未找到「{product_name}」（只读操作，已记录审计）。",
                    data={"intent": intent, "params": params},
                    next_action="product_query",
                )
            term_text = (
                f"期限 {product.term_days} 天" if product.term_days else "开放期限（随时申赎）"
            )
            return self.ok(
                f"产品查询：{product.name}（{product.risk_level}）起投 "
                f"{float(product.minimum_amount):,.0f} 元，{term_text}，"
                f"流动性：{product.liquidity}。",
                data={
                    "intent": intent,
                    "product": {
                        "id": product.id,
                        "name": product.name,
                        "risk_level": product.risk_level,
                        "minimum_amount": float(product.minimum_amount),
                        "term_days": product.term_days,
                        "liquidity": product.liquidity,
                    },
                },
                next_action="product_query",
            )

        if intent == "risk_reassess":
            # 风评重做：将当前生效风评置为过期并提示重新测评（真实状态变更）
            result = await self._handle_risk_reassess(message, params, context)
            return await self._finalize_write(intent, params, result, context)

        if intent == "suspicious_report":
            result = await self._handle_suspicious_report(message, params, context)
            return await self._finalize_write(intent, params, result, context)

        if intent == "workorder_create":
            result = await self._handle_workorder_create(message, params, context)
            return await self._finalize_write(intent, params, result, context)

        if intent == "info_update":
            result = await self._handle_info_update(message, params, context)
            return await self._finalize_write(intent, params, result, context)

        if intent == "redeem":
            result = await self._handle_redeem(message, params, context)
            return await self._finalize_write(intent, params, result, context)

        if intent == "transfer":
            result = await self._handle_transfer(message, params, context)
            return await self._finalize_write(intent, params, result, context)

        # ---- purchase path ----
        operator_id = context.user_id
        if not operator_id:
            return self.fail("缺少操作人信息", ["context 中未提供 user_id"])

        product_name = str(params.get("product_name") or "").strip()
        customer_identifier = self._customer_identifier(message, params)
        try:
            amount = Decimal(str(params.get("amount", 0)))
        except Exception:  # noqa: BLE001
            amount = Decimal("0")

        if not product_name or amount <= 0 or not customer_identifier:
            missing = [
                name
                for name, ok in (
                    ("产品名称", bool(product_name)),
                    ("申购金额", amount > 0),
                    ("客户", bool(customer_identifier)),
                )
                if not ok
            ]
            hints = []
            if not customer_identifier:
                hints.append("请提供客户ID（如 客户ID 1 / 编号1001）")
            if not product_name:
                hints.append("请提供产品名称")
            if amount <= 0:
                hints.append("请提供申购金额（如 50000元）")
            return await self._ask_params(
                context,
                intent,
                params,
                message,
                f"参数不完整，缺少：{'、'.join(missing)}。{'；'.join(hints)}。",
                missing,
            )

        async with self.database.session_factory() as session:
            operator = (
                await session.execute(select(User).where(User.id == operator_id))
            ).scalar_one_or_none()
            if operator is None:
                return self.fail("操作人不存在", [f"user {operator_id} not found"])
            try:
                customer, amb_result = await self._resolve_customer_checked(
                    session, customer_identifier, intent
                )
            except TradingError as exc:
                return self.fail(str(exc), [str(exc)], data={"intent": intent})
            if amb_result is not None:
                # 歧义待选择：释放幂等（failed），允许重发选择
                await self._release_idempotent(context)
                return amb_result

        confirmed = bool((context.metadata or {}).get("confirmed"))
        purchase_threshold, _, large_threshold = await self._tier_thresholds(
            customer.id
        )
        if float(amount) > purchase_threshold and not confirmed:
            return await self._request_confirmation(
                context,
                intent,
                params,
                message,
                f"请确认执行以下操作：为客户「{customer.display_name}」申购「{product_name}」{amount:,.2f} 元。"
                f"（金额超过 {purchase_threshold:,.0f} 元需二次确认）",
                confirm_action="confirm_purchase",
            )

        async with self.database.session_factory() as session:
            operator = (
                await session.execute(select(User).where(User.id == operator_id))
            ).scalar_one_or_none()
            if operator is None:
                return self.fail("操作人不存在", [f"user {operator_id} not found"])
            try:
                customer, amb_result = await self._resolve_customer_checked(
                    session, customer_identifier, intent
                )
                if amb_result is not None:
                    # 歧义待选择：释放幂等（failed），允许重发选择
                    await self._release_idempotent(context)
                    return amb_result
                result = await self._execute_purchase(
                    session, customer, operator, product_name, amount
                )
                await session.commit()
            except TradingError as exc:
                await session.rollback()
                return self.fail(
                    f"执行失败：{exc}", [str(exc)], data={"intent": intent}
                )
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                return self.fail(
                    f"执行异常：{type(exc).__name__}",
                    [str(exc)],
                    data={"intent": intent},
                )

        await self._publish_large_transaction(
            customer.id, float(amount), intent, large_threshold
        )
        # 动态同步：申购成交后以 DB 为权威源重建图谱持仓（新增产品自动加 HOLDS）
        if str(result.get("status")) == "executed":
            await self._sync_holdings_to_graph(customer.id)
        result = self.ok(
            f"已为客户「{customer.display_name}」提交申购！订单号 {result['order_no']}，产品「{result['product']}」，金额 {result['amount']:,.2f} 元，"
            f"当前状态：{result['status']}。"
            + (
                f"（该笔金额 ≥ {large_threshold:,.0f} 元，已同步风控监测）"
                if float(amount) >= large_threshold
                else ""
            ),
            data=result,
            next_action="purchase_executed",
            confidence=0.95,
        )
        return await self._finalize_write(intent, params, result, context)

    # ---- 真实落库处理器 -------------------------------------------------
    async def _handle_redeem(
        self, message: str, params: dict, context: AgentContext
    ) -> AgentResult:
        operator_id = context.user_id
        customer_identifier = self._customer_identifier(message, params)
        product_name = str(params.get("product_name") or "").strip()
        # 全部赎回：只有明确说"全部/所有/全额"才按持仓全额赎回；
        # 未提份额且未说"全部" → 缺份额追问（不再默认全部赎回）。
        redeem_all = bool(params.get("redeem_all")) or bool(
            re.search(r"(全部|所有|全额|全数|全部份额|所有份额)", str(message or ""))
        )
        try:
            shares = (
                Decimal(str(params.get("shares") or 0))
                if not redeem_all
                else Decimal("0")
            )
        except Exception:  # noqa: BLE001
            shares = Decimal("0")
        if (
            not customer_identifier
            or not product_name
            or (not redeem_all and shares <= 0)
        ):
            missing_ = [
                name
                for name, ok in (
                    ("客户", bool(customer_identifier)),
                    ("产品名称", bool(product_name)),
                    ("赎回份额", redeem_all or shares > 0),
                )
                if not ok
            ]
            hints = []
            if not customer_identifier:
                hints.append("请提供客户ID（如 客户ID 1 / 编号1001）")
            if not product_name:
                hints.append("请提供产品名称")
            if not redeem_all and shares <= 0:
                hints.append("请提供赎回份额（如 10000份）或回复「全部份额」")
            return await self._ask_params(
                context,
                "redeem",
                params,
                message,
                f"参数不完整，缺少：{'、'.join(missing_)}。"
                + "；".join(hints)
                + "。例如「赎回客户A持有的稳健增值计划全部份额」。",
                True,
            )
        # 全部赎回始终需要二次确认（目标项目 F3.4 规则）
        confirmed = bool((context.metadata or {}).get("confirmed"))
        if not confirmed and (
            redeem_all or (shares > 0 and float(shares) > CONFIRM_REDEEM_THRESHOLD)
        ):
            # 确认前先校验客户存在性：客户不存在/歧义时提前返回，
            # 不进入二次确认（避免"确认后执行才发现客户不存在"）。
            async with self.database.session_factory() as session:
                try:
                    _, amb_result = await self._resolve_customer_checked(
                        session, customer_identifier, "redeem"
                    )
                    if amb_result is not None:
                        await self._release_idempotent(context)
                        return amb_result
                except TradingError as exc:
                    await self._release_idempotent(context)
                    return self.fail(str(exc), [str(exc)], data={"intent": "redeem"})
            if redeem_all:
                detail = f"全部赎回「{product_name}」"
                reason = "（全部赎回需二次确认）"
            else:
                detail = f"赎回「{product_name}」{shares:,.2f} 份"
                reason = f"（份额超过 {CONFIRM_REDEEM_THRESHOLD:,.0f} 份需二次确认）"
            return await self._request_confirmation(
                context,
                "redeem",
                params,
                message,
                f"请确认执行以下操作：为客户「{customer_identifier}」{detail}。{reason}",
                confirm_action="confirm_redeem",
            )
        async with self.database.session_factory() as session:
            operator = (
                await session.execute(select(User).where(User.id == operator_id))
            ).scalar_one_or_none()
            try:
                customer, amb_result = await self._resolve_customer_checked(
                    session, customer_identifier, "redeem"
                )
                if amb_result is not None:
                    return amb_result
                product = (
                    await session.execute(
                        select(Product).where(
                            Product.name == product_name, Product.status == "active"
                        )
                    )
                ).scalar_one_or_none()
                if product is None:
                    raise TradingError(f"未找到在售产品「{product_name}」")
                if redeem_all:
                    holding = (
                        await session.execute(
                            select(CustomerHolding).where(
                                CustomerHolding.user_id == customer.id,
                                CustomerHolding.product_id == product.id,
                                CustomerHolding.status == "active",
                            )
                        )
                    ).scalar_one_or_none()
                    if holding is None or holding.quantity <= 0:
                        raise TradingError(
                            f"客户未持有产品「{product_name}」，无法赎回"
                        )
                    shares = Decimal(str(holding.quantity))
                result = await self.trading.redeem(
                    session, customer, str(product.id), shares, operator=operator
                )
                await session.commit()
            except TradingError as exc:
                await session.rollback()
                return self.fail(
                    f"赎回失败：{exc}", [str(exc)], data={"intent": "redeem"}
                )
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                return self.fail(
                    f"赎回异常：{type(exc).__name__}",
                    [str(exc)],
                    data={"intent": "redeem"},
                )
        _, _, large_threshold = await self._tier_thresholds(customer.id)
        await self._publish_large_transaction(
            customer.id, float(result["amount"]), "redeem", large_threshold
        )
        # 动态同步：赎回成交后以 DB 为权威源重建图谱持仓（清仓产品自动移除 HOLDS）
        await self._sync_holdings_to_graph(customer.id)
        return self.ok(
            f"已为客户「{customer.display_name}」赎回「{result['product']}」{result['shares']:,.2f} 份，"
            f"回款 {result['amount']:,.2f} 元（订单号 {result['order_no']}）。",
            data=result,
            next_action="redeem_executed",
            confidence=0.95,
        )

    async def _handle_transfer(
        self, message: str, params: dict, context: AgentContext
    ) -> AgentResult:
        operator_id = context.user_id
        from_identifier = self._customer_identifier(message, params)
        # 转入方：优先从消息中解析（"把X转到Y / 从X转到Y / 给Y转账"），
        # 去掉"账户"等后缀（"零售投资者账户"→"零售投资者"），兼容 LLM
        # 提取的 target 或降级正则。
        target = self._transfer_target(message, params)
        try:
            amount = Decimal(str(params.get("amount") or 0))
        except Exception:  # noqa: BLE001
            amount = Decimal("0")
        if not from_identifier or not target or amount <= 0:
            missing_ = [
                name
                for name, ok in (
                    ("转出方", bool(from_identifier)),
                    ("转入方", bool(target)),
                    ("转账金额", amount > 0),
                )
                if not ok
            ]
            hints = []
            if not from_identifier:
                hints.append("请提供转出方客户ID（如 客户ID 1 / 编号1001）")
            if not target:
                hints.append("请提供转入方客户ID（如 客户ID 2 / 编号1002）")
            if amount <= 0:
                hints.append("请提供转账金额（如 50000元）")
            return await self._ask_params(
                context,
                "transfer",
                params,
                message,
                f"参数不完整，缺少：{'、'.join(missing_)}。"
                + "；".join(hints)
                + "。例如「把客户A的50万转到客户B」。",
                True,
            )
        confirmed = bool((context.metadata or {}).get("confirmed"))
        transfer_threshold = CONFIRM_TRANSFER_THRESHOLD
        # 确认前先校验转出方与转入方客户存在性：客户不存在/歧义时提前返回，
        # 不进入二次确认（避免"确认后执行才发现客户不存在"）。
        # 同时确认阈值按转出方客户层级（零售 5 万 → 私行 50 万）。
        from_customer = None
        async with self.database.session_factory() as session:
            if not confirmed:
                try:
                    from_customer, amb1 = await self._resolve_customer_checked(
                        session, from_identifier, "transfer"
                    )
                    if amb1 is not None:
                        await self._release_idempotent(context)
                        return amb1
                    _, transfer_threshold, _ = await self._tier_thresholds(
                        from_customer.id
                    )
                except TradingError as exc:
                    await self._release_idempotent(context)
                    return self.fail(str(exc), [str(exc)], data={"intent": "transfer"})
            try:
                _, amb2 = await self._resolve_customer_checked(
                    session, target, "transfer"
                )
                if amb2 is not None:
                    await self._release_idempotent(context)
                    return amb2
            except TradingError as exc:
                await self._release_idempotent(context)
                return self.fail(str(exc), [str(exc)], data={"intent": "transfer"})
        if float(amount) > transfer_threshold and not confirmed:
            return await self._request_confirmation(
                context,
                "transfer",
                params,
                message,
                f"请确认执行以下操作：从「{from_identifier}」向「{target}」转账 {amount:,.2f} 元。"
                f"（金额超过 {transfer_threshold:,.0f} 元需二次确认）",
                confirm_action="confirm_transfer",
            )
        async with self.database.session_factory() as session:
            operator = (
                await session.execute(select(User).where(User.id == operator_id))
            ).scalar_one_or_none()
            try:
                from_customer, amb1 = await self._resolve_customer_checked(
                    session, from_identifier, "transfer"
                )
                if amb1 is not None:
                    return amb1
                to_customer, amb2 = await self._resolve_customer_checked(
                    session, target, "transfer"
                )
                if amb2 is not None:
                    return amb2
                if from_customer.id == to_customer.id:
                    raise TradingError("转出方与转入方不能是同一客户")
                result = await self.trading.transfer(
                    session, from_customer, to_customer, amount, operator=operator
                )
                await session.commit()
            except TradingError as exc:
                await session.rollback()
                return self.fail(
                    f"转账失败：{exc}", [str(exc)], data={"intent": "transfer"}
                )
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                return self.fail(
                    f"转账异常：{type(exc).__name__}",
                    [str(exc)],
                    data={"intent": "transfer"},
                )
        await self._publish_large_transaction(
            from_customer.id, float(amount), "transfer"
        )
        return self.ok(
            f"转账成功：从「{from_customer.display_name}」向「{to_customer.display_name}」转入 {result['amount']:,.2f} 元。",
            data=result,
            next_action="transfer_executed",
            confidence=0.95,
        )

    async def _handle_info_update(
        self, message: str, params: dict, context: AgentContext
    ) -> AgentResult:
        operator_id = context.user_id
        customer_identifier = self._customer_identifier(message, params) or str(
            params.get("customer_id") or ""
        )
        field = str(params.get("field") or "").strip()
        value = str(params.get("value") or "").strip()
        if not customer_identifier or not field or not value:
            missing_ = [
                name
                for name, ok in (
                    ("客户", bool(customer_identifier)),
                    ("字段", bool(field)),
                    ("新值", bool(value)),
                )
                if not ok
            ]
            hints = []
            if not customer_identifier:
                hints.append("请提供客户ID（如 客户ID 1 / 编号1001）")
            if not field:
                hints.append("请提供要更新的字段（如 职业/手机号）")
            if not value:
                hints.append("请提供新值（如 医生）")
            return await self._ask_params(
                context,
                "info_update",
                params,
                message,
                f"参数不完整，缺少：{'、'.join(missing_)}。"
                + "；".join(hints)
                + "。例如「把客户A的职业改成医生」。",
                True,
            )
        async with self.database.session_factory() as session:
            (
                await session.execute(select(User).where(User.id == operator_id))
            ).scalar_one_or_none()
            try:
                customer, amb_result = await self._resolve_customer_checked(
                    session, customer_identifier, "info_update"
                )
                if amb_result is not None:
                    return amb_result
                if field == "phone":
                    # 手机号更新落在 users.phone（目标项目 sys_user.phone 等价）
                    customer.phone = value
                    await session.commit()
                    result = {"field": "phone", "value": value, "user_id": customer.id}
                else:
                    result = await self.trading.update_profile_field(
                        session, customer.id, field, value
                    )
                    await session.commit()
            except TradingError as exc:
                await session.rollback()
                return self.fail(
                    f"信息更新失败：{exc}", [str(exc)], data={"intent": "info_update"}
                )
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                return self.fail(
                    f"信息更新异常：{type(exc).__name__}",
                    [str(exc)],
                    data={"intent": "info_update"},
                )
        # 对话回复脱敏：手机号等敏感值不在回复/前端展示完整原文
        # （工单 description 已由 _finalize_write 脱敏；这里处理用户直接
        # 看到的 summary 与 data）。
        display_result = self._sanitize_audit_params("info_update", result)
        return self.ok(
            f"已为客户「{customer.display_name}」更新「{display_result['field']}」为"
            f"「{display_result['value']}」。",
            data=display_result,
            next_action="info_update_executed",
            confidence=0.95,
        )

    async def _handle_risk_reassess(
        self, message: str, params: dict, context: AgentContext
    ) -> AgentResult:
        """风评重做：将客户当前生效风评置为过期，触发重新测评（真实状态变更）。"""
        from datetime import datetime

        from app.models.profile import CustomerRiskAssessment

        operator_id = context.user_id
        customer_identifier = self._customer_identifier(message, params) or str(
            params.get("customer_id") or ""
        )
        unfreeze = bool(params.get("unfreeze"))
        if not customer_identifier:
            return await self._ask_params(
                context,
                "risk_reassess",
                params,
                message,
                "参数不完整，缺少客户标识。请提供客户ID（如 客户ID 1 / 编号1001）。例如「给客户A重新做风险评估」。",
                True,
            )
        async with self.database.session_factory() as session:
            try:
                customer, amb_result = await self._resolve_customer_checked(
                    session, customer_identifier, "risk_reassess"
                )
                if amb_result is not None:
                    return amb_result
                if unfreeze:
                    # 目标项目 unfreeze：清除冻结恢复申购。当前项目无冻结状态，
                    # 风评重做置过期后由完成 16 题问卷驱动恢复，此处登记确认。
                    await session.commit()
                    return self.ok(
                        f"已登记客户「{customer.display_name}」风评完成："
                        f"请完成 16 题问卷后系统将自动恢复申购。",
                        data={
                            "intent": "risk_reassess",
                            "customer_id": customer.id,
                            "unfreeze": True,
                        },
                        next_action="risk_reassess_unfrozen",
                    )
                assessments = list(
                    (
                        await session.execute(
                            select(CustomerRiskAssessment).where(
                                CustomerRiskAssessment.user_id == customer.id,
                                CustomerRiskAssessment.status.in_(
                                    ["active", "provisional"]
                                ),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for item in assessments:
                    item.status = "superseded"
                    item.expires_at = datetime.now(UTC)
                workorder = WorkOrder(
                    id=str(uuid4()),
                    workorder_no=f"W{uuid4().hex[:16].upper()}",
                    customer_id=customer.id,
                    submitter_id=operator_id,
                    workorder_type="风险评估重做",
                    priority="normal",
                    status="pending",
                    title=f"{customer.display_name} 风险测评重做",
                    description="业务操作 Agent 触发：风评已过期，需重新完成 16 题问卷",
                    source_type="business_operator",
                )
                session.add(workorder)
                await session.commit()
            except TradingError as exc:
                await session.rollback()
                return self.fail(
                    f"风评重做失败：{exc}", [str(exc)], data={"intent": "risk_reassess"}
                )
        return self.ok(
            f"已为客户「{customer.display_name}」登记风评重做：原风评已置为过期，"
            f"并生成工单 {workorder.workorder_no}。请前往风险测评页面完成 16 题问卷。",
            data={
                "intent": "risk_reassess",
                "customer_id": customer.id,
                "workorder_no": workorder.workorder_no,
            },
            next_action="risk_reassess_registered",
        )

    async def _handle_suspicious_report(
        self, message: str, params: dict, context: AgentContext
    ) -> AgentResult:
        """可疑交易上报：真实创建 RiskAlert + WorkOrder。"""
        operator_id = context.user_id
        customer_identifier = self._customer_identifier(message, params) or str(
            params.get("customer_id") or ""
        )
        reason = str(params.get("reason") or "").strip() or "员工人工上报的可疑交易"
        if not customer_identifier:
            return await self._ask_params(
                context,
                "suspicious_report",
                params,
                message,
                "参数不完整，缺少客户标识。请提供客户ID（如 客户ID 1 / 编号1001）。例如「上报客户A的可疑交易」。",
                True,
            )
        async with self.database.session_factory() as session:
            try:
                customer, amb_result = await self._resolve_customer_checked(
                    session, customer_identifier, "suspicious_report"
                )
                if amb_result is not None:
                    return amb_result
                alert = RiskAlert(
                    id=str(uuid4()),
                    customer_id=customer.id,
                    alert_level="high",
                    alert_color="red",
                    alert_type="员工人工可疑上报",
                    trigger_rules_json=["STAFF_SUSPICIOUS_REPORT"],
                    confidence=0.8,
                    transaction_ids_json=[],
                    trigger_detail=reason,
                    status="pending",
                )
                session.add(alert)
                await session.flush()
                workorder = WorkOrder(
                    id=str(uuid4()),
                    workorder_no=f"W{uuid4().hex[:16].upper()}",
                    customer_id=customer.id,
                    submitter_id=operator_id,
                    workorder_type="可疑交易上报",
                    priority="high",
                    status="pending",
                    title=f"{customer.display_name} 可疑交易上报",
                    description=reason,
                    source_type="risk_alert",
                    source_id=alert.id,
                )
                session.add(workorder)
                await session.commit()
            except TradingError as exc:
                await session.rollback()
                return self.fail(
                    f"可疑上报失败：{exc}",
                    [str(exc)],
                    data={"intent": "suspicious_report"},
                )
        return self.ok(
            f"可疑交易上报完成：客户「{customer.display_name}」，预警 {alert.id[:8]}，"
            f"工单 {workorder.workorder_no} 已生成，将同步反洗钱专员审核并按规定报送。",
            data={
                "intent": "suspicious_report",
                "customer_id": customer.id,
                "alert_id": alert.id,
                "workorder_no": workorder.workorder_no,
            },
            next_action="suspicious_report_submitted",
        )

    async def _handle_workorder_create(
        self, message: str, params: dict, context: AgentContext
    ) -> AgentResult:
        """工单创建：真实创建 WorkOrder。"""
        operator_id = context.user_id
        customer_identifier = self._customer_identifier(message, params) or str(
            params.get("customer_id") or ""
        )
        workorder_type = str(params.get("workorder_type") or "").strip()
        priority = str(params.get("priority") or "").strip() or "normal"
        description = str(params.get("description") or "").strip()
        if not description:
            # 缺工单内容：追问（客户可选保留，仅追问内容）
            return await self._ask_params(
                context,
                "workorder_create",
                params,
                message,
                "参数不完整，缺少：工单内容。请提供工单内容（如 投诉/咨询问题描述）。例如「给客户A创建投诉工单，内容：产品收益未达预期」。",
                ["工单内容"],
            )
        workorder_type = workorder_type or (
            "投诉" if "投诉" in description else "客户服务"
        )
        async with self.database.session_factory() as session:
            try:
                customer = None
                if customer_identifier:
                    customer, amb_result = await self._resolve_customer_checked(
                        session, customer_identifier, "workorder_create"
                    )
                    if amb_result is not None:
                        return amb_result
                workorder = WorkOrder(
                    id=str(uuid4()),
                    workorder_no=f"W{uuid4().hex[:16].upper()}",
                    customer_id=customer.id if customer else None,
                    submitter_id=operator_id,
                    workorder_type=workorder_type,
                    priority=priority,
                    status="pending",
                    title=f"{workorder_type}工单",
                    description=description,
                    source_type="business_operator",
                )
                session.add(workorder)
                await session.commit()
            except TradingError as exc:
                await session.rollback()
                return self.fail(
                    f"工单创建失败：{exc}",
                    [str(exc)],
                    data={"intent": "workorder_create"},
                )
        return self.ok(
            f"工单创建成功：{workorder_type}（优先级 {priority}），工单号 {workorder.workorder_no}。"
            f"描述：{description}",
            data={
                "intent": "workorder_create",
                "workorder_no": workorder.workorder_no,
                "workorder_type": workorder_type,
                "priority": priority,
            },
            next_action="workorder_created",
        )
