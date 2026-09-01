from __future__ import annotations

import redis.asyncio as redis

from app.agents.base import AgentBase
from app.common.logging.config import get_logger
from app.infrastructure.agent_event_bus import EVENT_SUSPICIOUS_INTENT, AgentEventBus
from app.ports.agent import AgentContext
from app.repositories.knowledge import KnowledgeRepository
from app.schemas.agents import AgentResult

logger = get_logger(__name__)

SUSPICIOUS_INTENT_KEYWORDS = (
    "规避反洗钱",
    "绕过风控",
    "逃避监管",
    "拆分交易",
    "分拆交易",
    "洗钱",
    "套现",
    "借名开户",
    "借名持有",
    "代持",
    "匿名开户",
    "不留痕",
)

# Intent classification: keyword rules used when the LLM is unavailable.
INTENT_KEYWORDS: dict[str, list[str]] = {
    "product_inquiry": [
        "产品",
        "收益",
        "收益率",
        "利率",
        "起投",
        "风险等级",
        "净值",
        "认购",
        "申购",
        "理财",
        "基金",
        "债券",
        "保险",
    ],
    "policy_explain": [
        "政策",
        "监管",
        "合规",
        "新规",
        "规定",
        "办法",
        "要求",
        "适当性",
        "反洗钱",
        "资管新规",
        "销售办法",
        "管理条例",
    ],
    "faq": [
        "流程",
        "手续",
        "怎么",
        "如何",
        "多久",
        "几天",
        "什么时候",
        "到账",
        "费用",
        "操作",
        "步骤",
    ],
    "transfer_human": ["人工", "客服", "转人工", "投诉", "经理"],
    "chitchat": [
        "你好",
        "您好",
        "谢谢",
        "感谢",
        "再见",
        "拜拜",
        "嗨",
        "hello",
        "hi",
        "在吗",
    ],
}

INTENT_LABELS = {
    "product_inquiry": "产品咨询",
    "policy_explain": "政策解读",
    "faq": "FAQ",
    "chitchat": "闲聊",
    "transfer_human": "转人工",
}

CLASSIFY_SYSTEM = """你是智能财富管家的意图分类器。从以下 5 类中选择用户意图：
- product_inquiry: 询问具体产品信息（名称/收益率/风险/起投金额）
- policy_explain: 询问政策、法规、监管要求
- faq: 操作流程、常见问题（申购流程、确认时间、手续费）
- chitchat: 问候、感谢、闲聊
- transfer_human: 要求转人工

只返回 JSON: {"intent": "...", "confidence": 0.X}"""


class CustomerAgent(AgentBase):
    """智能客服 Agent：意图分类 → 知识库检索 → LLM 生成（含来源引用）。

    Reasoning paradigm: Retrieve-then-Generate. When the LLM is unavailable
    (no API key or upstream failure) it degrades to returning the retrieved
    knowledge chunks directly so the demo keeps working.
    """

    name = "customer_service"
    description = "智能客服：产品咨询、政策解读、FAQ 回复、闲聊与转人工"

    def __init__(self, database, settings, llm=None):
        super().__init__(database, settings, llm)
        self.knowledge = KnowledgeRepository(database)
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            self._redis = redis.from_url(self.settings.redis_url, decode_responses=True)
        return self._redis

    @staticmethod
    def _suspicious_indicator(message: str) -> str | None:
        text = message.lower()
        # “反洗钱”本身是正常的合规咨询，不应因为包含“洗钱”二字被上报；
        # 但“规避反洗钱”等明确规避表达仍保留在上面的精确关键词中。
        text_without_compliance_term = text.replace("反洗钱", "")
        for keyword in SUSPICIOUS_INTENT_KEYWORDS:
            if keyword in text and keyword != "洗钱":
                return keyword
        return next(
            (
                keyword
                for keyword in SUSPICIOUS_INTENT_KEYWORDS
                if keyword == "洗钱" and keyword in text_without_compliance_term
            ),
            None,
        )

    async def _publish_suspicious_intent(
        self, message: str, intent: str, confidence: float, context: AgentContext
    ) -> None:
        indicator = self._suspicious_indicator(message)
        customer_id = (context.metadata or {}).get("customer_id") or context.user_id
        if not indicator or not customer_id:
            return
        try:
            client = await self._get_redis()
            await AgentEventBus(client).publish(
                EVENT_SUSPICIOUS_INTENT,
                event_type="suspicious_intent",
                source_agent=self.name,
                payload={
                    "customer_id": str(customer_id),
                    "intent": intent,
                    "indicator": indicator,
                    "confidence": confidence,
                    "message_excerpt": message[:300],
                },
            )
        except Exception:  # noqa: BLE001 - event bus failure must not block客服
            logger.exception("suspicious_intent_publish_failed")

    # -- intent -----------------------------------------------------------
    async def classify_intent(self, message: str) -> tuple[str, float]:
        parsed = await self.llm_json(CLASSIFY_SYSTEM, message)
        if parsed and parsed.get("intent") in INTENT_LABELS:
            return str(parsed["intent"]), float(parsed.get("confidence", 0.8))
        # deterministic fallback
        text = message.lower()
        for intent, keywords in INTENT_KEYWORDS.items():
            if any(k in text for k in keywords):
                return intent, 0.7
        return "chitchat", 0.5

    def _collection_for(self, intent: str) -> str | None:
        return {
            "product_inquiry": "product",
            "policy_explain": "policy",
            "faq": "faq",
        }.get(intent)

    # -- retrieval --------------------------------------------------------
    async def _retrieve(self, query: str, category: str | None, top_k: int = 5):
        try:
            provider = self.llm
            embedding = await provider.embed([query])
            return await self.knowledge.search_hybrid(
                query=query,
                query_embedding=embedding[0],
                top_k=top_k,
                category=category,
            )
        except Exception:  # noqa: BLE001 - fall back to lexical search
            chunks = await self.knowledge.search_text(
                query=query, top_k=top_k, category=category
            )
            return [(chunk, 1.0) for chunk in chunks]

    def _source_title(self, chunk) -> str:
        doc = getattr(chunk, "document", None)
        return (
            getattr(doc, "file_name", "内部知识库") if doc is not None else "内部知识库"
        )

    def _fallback_reply(self, intent: str) -> str:
        return (
            "抱歉，我暂时没有检索到与该问题高度匹配的资料。为确保信息的准确性，"
            "建议您联系人工客服（400-XXX-XXXX），或留下联系方式由客户经理跟进。"
        )

    # -- product directory fallback --------------------------------------
    async def _product_directory_fallback(self, message: str) -> str | None:
        """知识库未命中时，从数据库 product 目录检索具体产品回答。

        按产品名称（支持部分匹配）查找在售产品，返回结构化产品信息：
        风险等级、期限、起投金额、流动性、简介。未匹配到任何产品返回 None，
        由上层继续走兜底话术。
        """
        from sqlalchemy import select

        from app.models.profile import Product

        try:
            async with self.database.session_factory() as session:
                products = list(
                    (
                        await session.execute(
                            select(Product).where(
                                Product.status == "active",
                                Product.deleted_at.is_(None),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
        except Exception:  # noqa: BLE001 - 产品目录查询失败不影响主流程
            return None
        # 按产品名/类型/简介关键词匹配：消息文本包含产品名即命中
        text_lower = message.lower()
        matches = [
            p
            for p in products
            if (
                (p.name and p.name.lower() in text_lower)
                or (
                    p.product_type
                    and p.product_type.lower().replace("_", "")
                    in text_lower.replace(" ", "")
                )
            )
        ][:3]
        # 产品名不完整时，允许产品名前缀匹配（如消息提到"安鑫"）
        if not matches:
            matches = [
                p
                for p in products
                if (p.name and len(p.name) >= 2 and p.name[:2] in text_lower)
            ][:3]
        if not matches:
            return None

        risk_labels = {
            "R1": "低风险",
            "R2": "稳健",
            "R3": "平衡",
            "R4": "进取",
            "R5": "高风险",
        }
        liquidity_labels = {"high": "高流动性", "medium": "中流动性", "low": "低流动性"}
        lines = []
        for p in matches:
            risk_raw = str(p.risk_level or "C1").upper().replace("C", "R")
            term_text = (
                f"投资期限 {p.term_days} 天" if p.term_days else "开放期限（随时申赎）"
            )
            lines.append(
                f"· {p.name}（{p.product_type or '理财产品'}，"
                f"{risk_labels.get(risk_raw, risk_raw)}）\n"
                f"  {term_text}，起投 {float(p.minimum_amount or 0):,.0f} 元，"
                f"{liquidity_labels.get(p.liquidity or '', '中流动性')}。\n"
                f"  简介：{p.description or '暂无简介'}"
            )
        return (
            "为您查询到以下在售产品信息（数据来源：产品目录）：\n"
            + "\n".join(lines)
            + "\n\n如需了解购买资格或适当性匹配，欢迎继续咨询。"
        )

    # -- handler ----------------------------------------------------------
    async def _handle_knowledge(
        self, intent: str, message: str, history_context: str = ""
    ) -> tuple[str, list[dict]]:
        category = self._collection_for(intent)
        # 结合历史上下文检索：若用户问题是上下文引用（如"风险高吗"），
        # 用最近一条用户消息 + 当前问题组合查询
        query = message
        if history_context:
            last_user_line = next(
                (
                    line.split(":", 1)[1].strip()
                    for line in reversed(history_context.split("\n"))
                    if line.startswith("用户:")
                ),
                "",
            )
            if last_user_line:
                query = f"{last_user_line} {message}"
        hits = await self._retrieve(query, category)
        evidence: list[dict] = []
        # 产品咨询：消息命中数据库 product 目录中的具体产品名时，优先返回
        # 结构化产品信息（知识库只有通用产品类别，回答不了"安鑫短期理财
        # 净值/收益率/起投"这类具体产品问题）。
        if intent == "product_inquiry":
            db_reply = await self._product_directory_fallback(message)
            if db_reply:
                return db_reply, []
        if not hits:
            return self._fallback_reply(intent), evidence
        for chunk, score in hits[:3]:
            evidence.append(
                {
                    "source": self._source_title(chunk),
                    "content": chunk.content[:500],
                    "score": round(float(score), 4),
                }
            )
        context = "\n".join(
            f"【来源{i + 1}：{self._source_title(chunk)}】\n{chunk.content}"
            for i, (chunk, _) in enumerate(hits[:3])
        )
        system = f"""你是XX科技的智能财富管家。请基于以下知识回答用户问题：

{context}

回答要求：
1. 仅基于上述知识回答，不要编造信息
2. 回答末尾注明引用来源：【来源：《文档名》】
3. 如果知识不足以回答问题，诚实告知并建议联系人工客服
4. 语言友好、专业、简洁"""
        reply = await self.llm_chat(system, message, temperature=0.3)
        if not reply:
            sources = "；".join({e["source"] for e in evidence})
            reply = f"{evidence[0]['content'][:300]}（来源：《{sources}》）"
        return reply, evidence

    async def run(self, message: str, context: AgentContext) -> AgentResult:
        intent, confidence = await self.classify_intent(message)
        await self._publish_suspicious_intent(message, intent, confidence, context)
        history = (context.metadata or {}).get("history_context", "")
        history_block = f"\n\n【历史对话上下文】\n{history}" if history else ""

        if intent == "chitchat":
            reply = await self.llm_chat(
                "你是友好的财富管家助手。用户只是打招呼/闲聊，请简短友好回应，并自然引导到理财话题。"
                + history_block,
                message,
                temperature=0.7,
            )
            return self.ok(
                reply
                or "您好！我是智能财富管家，可以为您解答产品、政策与业务问题。有什么可以帮您？",
                data={"intent": intent},
                confidence=confidence,
            )

        if intent == "transfer_human":
            return self.ok(
                "好的，正在为您转接人工客服，请稍候。您也可以拨打客服热线 400-XXX-XXXX。",
                data={"intent": intent},
                next_action="transfer_human",
                confidence=confidence,
            )

        reply, evidence = await self._handle_knowledge(
            intent, message, history_context=history_block
        )
        return self.ok(
            reply,
            data={"intent": intent, "intent_label": INTENT_LABELS[intent]},
            evidence=evidence,
            confidence=confidence,
        )
