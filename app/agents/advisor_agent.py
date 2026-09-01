from __future__ import annotations

import re

from sqlalchemy import or_, select

from app.agents.base import AgentBase
from app.common.security.roles import CUSTOMER_ROLE_CODES
from app.models.auth import User
from app.models.profile import (
    CustomerAssetSnapshot,
    CustomerHolding,
    CustomerProfile,
    CustomerRiskAssessment,
    Product,
)
from app.ports.agent import AgentContext
from app.schemas.agents import AgentResult
from app.services.profile_tag_service import TagQueryService
from app.services.suitability_service import SuitabilityService

RISK_ORDER = {
    "C1": 1,
    "C2": 2,
    "C3": 3,
    "C4": 4,
    "C5": 5,
    "R1": 1,
    "R2": 2,
    "R3": 3,
    "R4": 4,
    "R5": 5,
}


class AdvisorAgent(AgentBase):
    """投顾助手 Agent：客户画像读取 → 适当性过滤 → 综合排序 → 个性化推荐理由。

    Reasoning paradigm: Constraint-Satisfy + Graph-Reason. Hard suitability
    filters run first (regulatory red line), then a multi-factor rank and an
    LLM-generated personalised rationale per product.
    """

    name = "investment_advisor"
    description = "投顾助手：产品推荐、持仓分析、资产配置建议"

    def __init__(self, database, settings, llm=None, knowledge_graph=None):
        super().__init__(database, settings, llm)
        self.suitability = SuitabilityService()
        self.knowledge_graph = knowledge_graph

    # -- graph 增强（GraphRAG，Phase 3 F3.2）----------------------------
    async def _graph_enhance(self, product_ids: list[str]) -> dict:
        """图谱增强：查询产品行业归属，避免行业过度集中。"""
        if self.knowledge_graph is None or not self.knowledge_graph.available:
            return {}
        industry_map: dict[str, str] = {}
        try:
            for pid in product_ids[:6]:
                rows = await self.knowledge_graph.get_product_industry_by_id(pid)
                if rows and rows[0].get("industry"):
                    industry_map[pid] = rows[0]["industry"]
        except Exception:  # noqa: BLE001 - 图谱失败降级
            pass
        return industry_map

    async def _graph_diversification(
        self, product_ids: list[str], held_industries: set[str]
    ) -> dict[str, float]:
        """图谱分散度评分：候选产品行业与客户现有持仓行业重叠度。

        重叠（行业已过度集中）→ 低分散分；不重叠 → 高分散分（补足配置）。
        无图谱数据时返回 0.5 中性值。
        """
        industry_map = await self._graph_enhance(product_ids)
        scores: dict[str, float] = {}
        for pid in product_ids:
            industry = industry_map.get(pid)
            if not industry:
                scores[pid] = 0.5  # 图谱缺失：中性
                continue
            # 与现有持仓行业重叠 → 分散度低；不重叠 → 分散度高
            scores[pid] = 0.2 if industry in held_industries else 1.0
        return scores

    # -- profile ----------------------------------------------------------
    async def _normalize_user_id(self, identifier) -> str | None:
        """把客户标识规范化为数字 ID 字符串。

        数字 / 数字字符串 → 原样返回；"客户2 / 客户ID 2 / 编号2 / #2"→
        剥离前缀取数字；用户名 / 中文名 → 查库解析为数字 ID；
        解析不到 → None。防止非数字字符串直接与 UserId（整数）列比较
        导致 asyncpg DataError（invalid input for query argument）。
        """
        if not identifier:
            return None
        s = str(identifier).strip()
        # 客户标识前缀剥离："客户2 / 客户ID 2 / 客户编号2 / 编号2 / ID:2 / #2"
        id_match = re.match(
            r"(?:客户ID|客户编号|编号|账号|客户号|ID|客户)\s*[:：#]?\s*([A-Za-z0-9_\-]+)$",
            s,
            re.I,
        )
        if id_match:
            return id_match.group(1)
        if s.isdigit():
            return s
        try:
            customer_roles = or_(
                *(User.roles.any(code=code) for code in CUSTOMER_ROLE_CODES)
            )
            async with self.database.session_factory() as session:
                user = (
                    await session.execute(
                        select(User).where(
                            User.status == "active",
                            customer_roles,
                            or_(User.username == s, User.display_name == s),
                        )
                    )
                ).scalar_one_or_none()
            return str(user.id) if user is not None else None
        except Exception:  # noqa: BLE001 - 解析失败仅影响自动识别，不阻断对话
            return None

    async def _load_profile(self, user_id: str) -> dict | None:
        # 防御：非数字标识（如用户名）无法匹配整数主键，直接返回 None 走降级
        if not user_id or not str(user_id).isdigit():
            return None
        user_id = int(user_id)
        async with self.database.session_factory() as session:
            profile = (
                await session.execute(
                    select(CustomerProfile).where(CustomerProfile.user_id == user_id)
                )
            ).scalar_one_or_none()
            risk = (
                (
                    await session.execute(
                        select(CustomerRiskAssessment)
                        .where(
                            CustomerRiskAssessment.user_id == user_id,
                            CustomerRiskAssessment.status.in_(
                                ["active", "provisional"]
                            ),
                        )
                        .order_by(CustomerRiskAssessment.assessed_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            asset = (
                (
                    await session.execute(
                        select(CustomerAssetSnapshot)
                        .where(CustomerAssetSnapshot.user_id == user_id)
                        .order_by(CustomerAssetSnapshot.snapshot_time.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            holdings = list(
                (
                    await session.execute(
                        select(CustomerHolding).where(
                            CustomerHolding.user_id == user_id,
                            CustomerHolding.status == "active",
                        )
                    )
                )
                .scalars()
                .all()
            )
            if profile is None:
                return None
            tags = await TagQueryService().list_tags(session, user_id)
            tag_map = {
                tag["tag_code"]: tag["value"]
                for tag in tags
                if tag["status"] == "ACTIVE"
            }
            # 跨 Agent 风控信号（风控 Agent 通过 Redis Pub/Sub 写入画像标签）
            risk_alert: dict | None = None
            raw_alert = tag_map.get("CROSS_AGENT_RISK_ALERT")
            if isinstance(raw_alert, dict) and raw_alert.get("level"):
                risk_alert = dict(raw_alert)
                # 合并标签置信度，供投顾展示/决策
                risk_alert["confidence"] = float(
                    next(
                        (
                            tag["confidence"]
                            for tag in tags
                            if tag["tag_code"] == "CROSS_AGENT_RISK_ALERT"
                        ),
                        0,
                    )
                    or 0,
                )
            return {
                "user_id": user_id,
                "customer_type": profile.customer_type,
                "customer_tier": profile.customer_tier,
                "risk_level": risk.risk_level if risk else "C1",
                "investment_goal": profile.investment_goal
                or str(tag_map.get("INVESTMENT_GOAL", "未填写")),
                "investment_horizon_years": profile.investment_horizon_years,
                "total_asset": float(asset.total_asset) if asset else 0.0,
                "investable_asset": float(asset.investable_asset) if asset else 0.0,
                "holding_count": len(holdings),
                "profile_status": profile.profile_status,
                "suitability_confidence": float(profile.suitability_confidence or 0),
                "tags": tags,
                "occupation": tag_map.get("OCCUPATION"),
                "loss_tolerance": tag_map.get("LOSS_TOLERANCE"),
                "preferred_products": tag_map.get("PREFERRED_PRODUCT_TYPES"),
                "asset_scale": tag_map.get("ASSET_SCALE"),
                "risk_alert": risk_alert,
            }

    # -- recommend --------------------------------------------------------
    async def _resolve_customer_from_message(self, message: str) -> str | None:
        """从投顾指令中解析客户名并解析为客户 ID（兜底，供未传 customer_id 时使用）。

        支持：retail_investor_demo / 零售投资者 / 零售客户 / 高净值客户 /
        high_net_worth_demo 等用户名或显示名关键词。
        """
        text = message.lower()
        keywords = {
            "零售投资者": "retail_investor_demo",
            "零售客户": "retail_investor_demo",
            "普通投资者": "retail_investor_demo",
            "高净值": "high_net_worth_demo",
            "高净值客户": "high_net_worth_demo",
        }
        matched_username = None
        for keyword, username in keywords.items():
            if keyword in text:
                matched_username = username
                break
        if not matched_username:
            # 直接含用户名（如 "为 retail_investor_demo 推荐"）
            for username in ("retail_investor_demo", "high_net_worth_demo"):
                if username in text:
                    matched_username = username
                    break
        if not matched_username:
            # "客户2 / 客户ID 2 / 编号2"：数字 ID 引用 → 直接返回数字 ID
            id_match = re.search(
                r"(?:客户ID|客户编号|编号|账号|客户号|ID|客户)\s*[:：#]?\s*(\d+)",
                text,
                re.I,
            )
            if id_match:
                return id_match.group(1)
            # 客户显示名/用户名兜底（如"李伟"/"liwei"）：消息中包含客户姓名时
            # 查库解析为数字 ID，否则返回 None 走降级
            return await self._resolve_customer_by_display_name(message, text)
        try:
            async with self.database.session_factory() as session:
                customer_roles = or_(
                    *(User.roles.any(code=code) for code in CUSTOMER_ROLE_CODES)
                )
                user = (
                    await session.execute(
                        select(User).where(
                            User.status == "active",
                            customer_roles,
                            User.username == matched_username,
                        )
                    )
                ).scalar_one_or_none()
                if user is None:
                    return None
                # 确认该客户有画像
                profile_exists = (
                    await session.execute(
                        select(CustomerProfile).where(
                            CustomerProfile.user_id == user.id
                        )
                    )
                ).scalar_one_or_none()
                return str(user.id) if profile_exists is not None else None
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception("resolve_customer_failed: %s", exc)
            return None

    async def _resolve_customer_by_display_name(
        self, message: str, text: str
    ) -> str | None:
        """消息中包含客户显示名/用户名（如"李伟"/"liwei"）时查库解析为数字 ID。

        仅在关键词/用户名/数字 ID 均未命中时调用；按名称长度降序匹配，
        避免短名被长名包含时的误匹配（如"高磊" vs "高磊磊"）。
        """
        try:
            async with self.database.session_factory() as session:
                customer_roles = or_(
                    *(User.roles.any(code=code) for code in CUSTOMER_ROLE_CODES)
                )
                users = (
                    await session.execute(
                        select(User).where(
                            User.status == "active",
                            customer_roles,
                        )
                    )
                ).scalars().all()
            candidates: list[tuple[int, str]] = []
            for u in users:
                if u.display_name and u.display_name in message:
                    candidates.append((len(u.display_name), str(u.id)))
                elif u.username and u.username in text:
                    candidates.append((len(u.username), str(u.id)))
            if not candidates:
                return None
            candidates.sort(key=lambda item: item[0], reverse=True)
            return candidates[0][1]
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception(
                "resolve_customer_by_display_name_failed: %s", exc
            )
            return None

    async def _resolve_two_customers_from_message(self, message: str) -> list[str]:
        """对比场景：从消息中解析两个客户 ID（如"对比零售投资者和高净值客户的持仓"）。

        按消息中出现顺序返回两个有画像的客户 ID；不足两个返回空。
        """
        text = message.lower()
        candidates = [
            ("retail_investor_demo", ["零售投资者", "零售客户", "普通投资者"]),
            ("high_net_worth_demo", ["高净值客户", "高净值"]),
        ]
        found: list[str] = []
        try:
            async with self.database.session_factory() as session:
                # 1) 现有演示客户关键词（零售投资者/高净值客户等）
                for username, keywords in candidates:
                    if any(k in text for k in keywords) or username in text:
                        customer_roles = or_(
                            *(User.roles.any(code=code) for code in CUSTOMER_ROLE_CODES)
                        )
                        user = (
                            await session.execute(
                                select(User).where(
                                    User.status == "active",
                                    customer_roles,
                                    User.username == username,
                                )
                            )
                        ).scalar_one_or_none()
                        if user is not None:
                            profile_exists = (
                                await session.execute(
                                    select(CustomerProfile).where(
                                        CustomerProfile.user_id == user.id
                                    )
                                )
                            ).scalar_one_or_none()
                            if profile_exists is not None:
                                found.append(str(user.id))
                # 2) 数字客户 ID 引用（"客户11和客户12/客户ID 11、12/编号11、12"）
                if len(found) < 2:
                    id_matches = re.findall(
                        r"(?:客户ID|客户编号|编号|账号|客户号|ID|客户)\s*[:：#]?\s*(\d+)",
                        text,
                        re.I,
                    )
                    for id_str in id_matches:
                        if len(found) >= 2:
                            break
                        if id_str not in found:
                            user = (
                                await session.execute(
                                    select(User).where(
                                        User.status == "active",
                                        User.id == int(id_str),
                                    )
                                )
                            ).scalar_one_or_none()
                            if user is not None:
                                profile_exists = (
                                    await session.execute(
                                        select(CustomerProfile).where(
                                            CustomerProfile.user_id == user.id
                                        )
                                    )
                                ).scalar_one_or_none()
                                if profile_exists is not None:
                                    found.append(str(user.id))
                # 3) 中文名/用户名拆分（"对比李伟和王芳的持仓"/"对比 liwei 与
                #    wangfang"）：用"和/与/跟/、"分隔，各取一段查库解析。
                if len(found) < 2:
                    for sep in ("和", "与", "跟", "、", ","):
                        if sep not in text:
                            continue
                        left, _, right = text.partition(sep)
                        # 从左右片段中截取客户名区域（"对比X的持仓"→X）
                        name_a = re.sub(
                            r"^(?:对比|比较|看下|看一下|查看)\s*", "", left
                        ).strip()
                        name_a = re.sub(r"的?持仓.*$", "", name_a).strip()
                        name_b = re.sub(r"的?持仓.*$", "", right).strip()
                        for name in (name_a, name_b):
                            if len(found) >= 2 or not name:
                                continue
                            user = (
                                await session.execute(
                                    select(User).where(
                                        User.status == "active",
                                        or_(
                                            User.username == name,
                                            User.display_name == name,
                                        ),
                                    )
                                )
                            ).scalar_one_or_none()
                            if user is not None:
                                profile_exists = (
                                    await session.execute(
                                        select(CustomerProfile).where(
                                            CustomerProfile.user_id == user.id
                                        )
                                    )
                                ).scalar_one_or_none()
                                if profile_exists is not None:
                                    found.append(str(user.id))
                        if len(found) >= 2:
                            break
        except Exception as exc:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception(
                "resolve_two_customers_failed: %s", exc
            )
            return []
        return found

    async def _recommend(self, message: str, user_id: str) -> dict:
        profile = await self._load_profile(user_id)
        if profile is None:
            return {
                "found": False,
                "reply": "未找到该客户的画像，建议先完成风险测评（风评）后再进行产品推荐。"
                "如需为客户推荐，请在指令中写明客户名称，例如「为零售投资者推荐几款产品」或「为 high_net_worth_demo 推荐产品」。",
            }

        # ---- 跨 Agent 风控联动（F4：风控红色预警 → 投顾暂停/降级推荐）----
        risk_alert = profile.get("risk_alert")
        risk_level = str(risk_alert.get("level", "")).lower() if risk_alert else ""
        risk_reply_hint: str | None = None
        if risk_level == "high":
            rules = (
                "、".join(map(str, risk_alert.get("trigger_rules", []) or []))
                or "风控规则"
            )
            return {
                "found": False,
                "profile": profile,
                "risk_alert": risk_alert,
                "blocked_by_risk": True,
                "reply": (
                    f"⚠️ 该客户存在红色风控预警（命中规则：{rules}）。"
                    "根据适当性管理要求，建议先核实风险、处理预警工单后再进行产品推荐，"
                    "避免向高风险客户推荐超出其承受能力的产品。"
                ),
            }
        if risk_level in {"medium", "low"}:
            risk_reply_hint = (
                f"⚠️ 注意：该客户当前存在{'中度' if risk_level == 'medium' else '轻度'}风控预警，"
                "以下推荐已适当降低产品风险档位，建议人工复核后再与客户沟通。"
            )

        async with self.database.session_factory() as session:
            recommendation = await self.suitability.recommend(session, user_id)
            matched_ids = [str(x.product_id) for x in recommendation.matches]
            products = list(
                (
                    await session.execute(
                        select(Product).where(Product.status == "active")
                    )
                )
                .scalars()
                .all()
            )
            candidates = [p for p in products if str(p.id) in matched_ids]
            # 风控降级：中/轻度预警客户过滤 R4/R5 高波动产品，降低推荐风险
            if risk_level in {"medium", "low"}:
                candidates = [
                    p
                    for p in candidates
                    if RISK_ORDER.get(str(p.risk_level).upper().replace("C", "R"), 5)
                    <= 3
                ]

        # ---- 客户现有持仓行业（图谱分散度用）----
        held_industries: set[str] = set()
        try:
            portfolio_data = await self._portfolio(user_id)
            held_product_ids = [str(h["product_id"]) for h in portfolio_data["items"]]
            held_industry_map = await self._graph_enhance(held_product_ids[:10])
            held_industries = {v for v in held_industry_map.values() if v}
        except Exception:  # noqa: BLE001
            held_industries = set()

        # ---- 候选池收益归一化（参考项目 Min-Max）----
        # 收益用产品描述中提取的"年化/收益率"数值（无则 0.5 中性）
        def _extract_return(product: Product) -> float | None:
            text = f"{product.description} {product.name}"
            import re as _re

            m = _re.search(r"(\d+(?:\.\d+)?)\s*%", text)
            if m:
                return float(m.group(1))
            return None

        returns = [_extract_return(p) for p in candidates]
        valid_returns = [r for r in returns if r is not None]
        min_r = min(valid_returns) if valid_returns else 0
        max_r = max(valid_returns) if valid_returns else 0

        def _return_score(product: Product) -> float:
            r = _extract_return(product)
            if r is None:
                return 0.5
            if max_r <= min_r:
                return 0.5
            return min(1.0, max(0.0, (r - min_r) / (max_r - min_r)))

        # ---- 候选评分（5 因子，参考项目权重）----
        cand_ids = [str(p.id) for p in candidates]
        graph_scores = await self._graph_diversification(cand_ids, held_industries)
        # 客户风险档位（C1=1 ... C5=5）
        customer_risk_order = RISK_ORDER.get(
            str(profile.get("risk_level", "C1")).upper().replace("C", "R"), 1
        )

        def _score(product: Product) -> dict:
            risk_raw = str(product.risk_level).upper().replace("C", "R")
            product_risk_order = RISK_ORDER.get(risk_raw, 1)
            # 风险匹配：客户与产品风险差值越小分越高（参考项目 1 - |diff|/4）
            risk_match = max(
                0.0, 1.0 - abs(customer_risk_order - product_risk_order) / 4
            )
            term_days = int(product.term_days or 0)
            preferred_term = int(profile.get("investment_horizon_years") or 3) * 365
            term_match = (
                max(0.0, 1.0 - abs(term_days - preferred_term) / preferred_term)
                if preferred_term > 0 and term_days > 0
                else 0.5
            )
            return {
                "return": 0.30 * _return_score(product),
                "risk_match": 0.25 * risk_match,
                "term_match": 0.15 * term_match,
                "min_amount": 0.15
                * min(1, float(product.minimum_amount or 0) / 100000),
                "graph_signal": 0.15 * graph_scores.get(str(product.id), 0.5),
            }

        scored = [
            (
                p,
                sum(_score(p).values()),
                _score(p),
            )
            for p in candidates
        ]
        # 同类型去重：每产品类型最多 2 个（参考项目）
        type_count: dict[str, int] = {}
        ranked_final: list[tuple] = []
        for p, total, breakdown in sorted(scored, key=lambda x: -x[1]):
            ptype = p.product_type or "其他"
            if type_count.get(ptype, 0) >= 2:
                continue
            type_count[ptype] = type_count.get(ptype, 0) + 1
            ranked_final.append((p, total, breakdown))
        ranked = ranked_final[:3]

        products_out = [
            {
                "product_id": str(p.id),
                "name": p.name,
                "product_type": p.product_type,
                "risk_level": str(p.risk_level).upper().replace("C", "R"),
                "term_days": p.term_days,
                "minimum_amount": float(p.minimum_amount or 0),
                "liquidity": p.liquidity,
                "description": p.description or "",
                "final_score": round(total, 4),
                "score_breakdown": {k: round(v, 4) for k, v in breakdown.items()},
            }
            for p, total, breakdown in ranked
        ]
        # GraphRAG 增强：图谱查询产品行业
        industry_map = await self._graph_enhance(
            [p["product_id"] for p in products_out]
        )
        for product in products_out:
            product["industry"] = industry_map.get(product["product_id"])
        reasons = await self._generate_reasons(profile, products_out)

        # ---- 排除明细 + 原因码（参考项目可解释性）----
        excluded = []
        for p in products:
            if str(p.id) in matched_ids:
                continue
            reasons_excluded = []
            risk_raw = str(p.risk_level).upper().replace("C", "R")
            customer_r = RISK_ORDER.get(
                str(profile.get("risk_level", "C1")).upper().replace("C", "R"), 1
            )
            product_r = RISK_ORDER.get(risk_raw, 1)
            if product_r > customer_r + 1:
                reasons_excluded.append("risk_level")
            target_type = p.target_customer_type or "individual"
            if target_type not in {"all", "individual"} and target_type != profile.get(
                "customer_type", "individual"
            ):
                reasons_excluded.append("customer_type")
            if p.minimum_amount and float(p.minimum_amount) > float(
                profile.get("investable_asset", 0)
            ):
                reasons_excluded.append("minimum_amount")
            excluded.append(
                {
                    "product_id": str(p.id),
                    "name": p.name,
                    "risk_level": risk_raw,
                    "reasons": reasons_excluded or ["suitability"],
                }
            )

        # ---- 合规护栏：拦截违规承诺，校验引用白名单产品 ----
        from app.agents.advisor_guardrails import guard_reply

        reply_lines = []
        if risk_reply_hint:
            reply_lines.append(risk_reply_hint)
        reply_lines.append(
            f"基于客户画像（风险等级 {profile['risk_level']}，可投资资产 {profile['investable_asset']:,.0f} 元），为您推荐以下产品："
        )
        for product, reason in zip(products_out, reasons):
            term_text = (
                f"{product['term_days']} 天"
                if product["term_days"]
                else "开放期限（随时申赎）"
            )
            reply_lines.append(
                f"- {product['name']}（风险 {product['risk_level']}，{term_text}，起投 {product['minimum_amount']:,.0f} 元）：{reason}"
            )
        if excluded:
            reply_lines.append(
                f"（另有 {len(excluded)} 只产品因适当性不匹配被过滤，可展开查看原因）"
            )
        raw_reply = "\n".join(reply_lines)
        guard = guard_reply(
            raw_reply,
            allowed_product_names=tuple(p["name"] for p in products_out),
        )
        reply = guard.safe_reply if not guard.allowed else raw_reply

        return {
            "found": True,
            "profile": profile,
            "products": products_out,
            "reasons": reasons,
            "reply": reply,
            "excluded": excluded,
            "excluded_count": len(excluded),
            "guard": guard.reason,
            "risk_alert": risk_alert,
            "confidence": 0.65 if risk_level in {"medium", "low"} else 0.85,
        }

    async def _generate_reasons(self, profile: dict, products: list[dict]) -> list[str]:
        if not products:
            return ["暂无匹配产品，建议降低收益预期或先完成风险测评。"]
        tag_lines = []
        if profile.get("occupation"):
            tag_lines.append(f"客户职业：{profile['occupation']}")
        if profile.get("loss_tolerance"):
            tag_lines.append(f"客户亏损容忍度：{profile['loss_tolerance']}")
        if profile.get("preferred_products"):
            tag_lines.append(
                f"客户偏好产品：{', '.join(map(str, profile['preferred_products']))}"
            )
        if profile.get("profile_status"):
            tag_lines.append(
                f"画像状态：{profile['profile_status']}（适当性置信度 {float(profile.get('suitability_confidence', 0)):.0%}）"
            )
        extra = ("\n" + "\n".join(tag_lines)) if tag_lines else ""
        template = """根据以下客户画像，为推荐的产品生成个性化推荐理由：
客户风险等级：{risk_level}
客户资产规模：{total_asset}
客户投资目标：{goal}
客户持仓数：{holding_count}
{extra}

推荐产品：{product_name}（风险 {product_risk}，{term_text}，所属行业：{industry}）
请用一句话说明"为什么这个产品适合这位客户"，引用画像信息。"""
        reasons: list[str] = []
        for product in products:
            term_text = (
                f"{product['term_days']}天"
                if product["term_days"]
                else "开放期限（随时申赎）"
            )
            prompt = template.format(
                risk_level=profile.get("risk_level", "C1"),
                total_asset=profile.get("total_asset", 0),
                goal=profile.get("investment_goal", "稳健增值"),
                holding_count=profile.get("holding_count", 0),
                extra=extra,
                product_name=product["name"],
                product_risk=product["risk_level"],
                term_text=term_text,
                industry=product.get("industry") or "未归类",
            )
            reply = await self.llm_chat(
                "你是资深投资顾问。基于客户画像生成个性化、合规的推荐理由，不要承诺收益。",
                prompt,
                temperature=0.4,
                max_tokens=256,
            )
            reasons.append(
                reply
                or f"{product['name']}：风险等级与客户适配，符合其投资目标（{profile['investment_goal']}）。"
            )
        return reasons

    # -- portfolio --------------------------------------------------------
    async def _portfolio(self, user_id: str) -> dict:
        async with self.database.session_factory() as session:
            holdings = list(
                (
                    await session.execute(
                        select(CustomerHolding).where(
                            CustomerHolding.user_id == user_id,
                            CustomerHolding.status == "active",
                        )
                    )
                )
                .scalars()
                .all()
            )
            items = []
            for h in holdings:
                product = (
                    await session.execute(
                        select(Product).where(Product.id == h.product_id)
                    )
                ).scalar_one_or_none()
                # 产品类型/风险等级映射（产品表存 C1-C5，语义上映射 R1-R5）
                risk_raw = str(product.risk_level).upper() if product else ""
                risk = (
                    risk_raw.replace("C", "R") if risk_raw.startswith("C") else risk_raw
                )
                items.append(
                    {
                        "product": product.name if product else h.product_id,
                        "product_id": h.product_id,
                        "product_type": product.product_type if product else "",
                        "risk_level": risk,
                        "liquidity": product.liquidity if product else "",
                        "term_days": product.term_days if product else 0,
                        "quantity": float(h.quantity),
                        "market_value": float(h.market_value),
                        "cost_amount": float(h.cost_amount),
                        "profit_loss": float(h.profit_loss),
                        "profit_loss_pct": round(
                            float(h.profit_loss) / float(h.cost_amount) * 100, 2
                        )
                        if h.cost_amount
                        else 0.0,
                        "holding_days": h.holding_days,
                    }
                )
            total_value = sum(i["market_value"] for i in items)
            total_cost = sum(i["cost_amount"] for i in items)
            # 各产品占比
            for item in items:
                item["weight"] = (
                    round(item["market_value"] / total_value * 100, 1)
                    if total_value
                    else 0.0
                )
            # 按风险等级聚合分布
            risk_distribution: dict[str, float] = {}
            type_distribution: dict[str, float] = {}
            for item in items:
                risk_distribution[item["risk_level"]] = (
                    risk_distribution.get(item["risk_level"], 0) + item["weight"]
                )
                type_distribution[item["product_type"] or "未分类"] = (
                    type_distribution.get(item["product_type"] or "未分类", 0)
                    + item["weight"]
                )
            return {
                "items": items,
                "total_market_value": total_value,
                "total_cost": total_cost,
                "total_profit_loss": round(total_value - total_cost, 2),
                "risk_distribution": risk_distribution,
                "type_distribution": type_distribution,
            }

    async def _analyze_portfolio(self, user_id: str) -> dict:
        """持仓详细分析：产品明细 + 集中度 + 风险分布 + LLM 解读。"""
        portfolio = await self._portfolio(user_id)
        if not portfolio["items"]:
            return {"found": False, "reply": "客户当前无持仓，暂无持仓分析。"}
        profile = await self._load_profile(user_id)
        risk_level = profile.get("risk_level", "C1") if profile else "C1"

        # 集中度：前 3 大持仓占比
        sorted_items = sorted(
            portfolio["items"], key=lambda x: x["market_value"], reverse=True
        )
        top3_weight = round(sum(i["weight"] for i in sorted_items[:3]), 1)
        top_product = sorted_items[0]
        # 盈亏统计
        gainers = [i for i in portfolio["items"] if i["profit_loss"] > 0]
        losers = [i for i in portfolio["items"] if i["profit_loss"] < 0]
        total_pnl = portfolio["total_profit_loss"]
        total_pnl_pct = (
            round(total_pnl / portfolio["total_cost"] * 100, 2)
            if portfolio["total_cost"]
            else 0.0
        )
        # 风险分布摘要
        risk_dist = portfolio["risk_distribution"]
        risk_desc = "、".join(f"{k}级产品 {v}%" for k, v in sorted(risk_dist.items()))
        # 行业集中度：图谱增强（产品行业归属）
        industry_map = await self._graph_enhance(
            [i["product_id"] for i in portfolio["items"]]
        )
        industry_weight: dict[str, float] = {}
        for item in portfolio["items"]:
            industry = industry_map.get(item["product_id"]) or "未归类"
            industry_weight[industry] = (
                industry_weight.get(industry, 0) + item["weight"]
            )
        industry_desc = "、".join(
            f"{k} {v}%" for k, v in sorted(industry_weight.items(), key=lambda x: -x[1])
        )

        # 构建 LLM 详细解读
        lines = []
        for i in sorted_items:
            lines.append(
                f"- {i['product']}（{i['product_type'] or '未分类'}，风险{i['risk_level']}）："
                f"市值 {i['market_value']:,.0f} 元（占比 {i['weight']}%），"
                f"成本 {i['cost_amount']:,.0f} 元，"
                f"盈亏 {i['profit_loss']:+,.0f} 元（{i['profit_loss_pct']:+.2f}%），"
                f"持有 {i['holding_days']} 天，流动性{'高' if i['liquidity'] == 'high' else '中' if i['liquidity'] == 'medium' else '低' if i['liquidity'] == 'low' else i['liquidity'] or '中'}"
            )
        product_detail = "\n".join(lines)

        prompt = f"""请作为资深投资顾问，对以下客户持仓进行专业、详细的分析解读（300字左右）：
客户风险等级：{risk_level}
投资目标：{profile.get("investment_goal", "稳健增值") if profile else "稳健增值"}
总持仓市值：{portfolio["total_market_value"]:,.0f} 元
总盈亏：{total_pnl:+,.0f} 元（{total_pnl_pct:+.2f}%）
前3大持仓集中度：{top3_weight}%（最大持仓：{top_product["product"]} {top_product["weight"]}%）
风险分布：{risk_desc}
行业分布：{industry_desc}
盈利产品：{len(gainers)} 只，亏损产品：{len(losers)} 只

持仓明细：
{product_detail}

分析要求：
1. 资产配置结构与风险等级匹配度
2. 集中度风险提示（行业/产品是否过度集中）
3. 盈利与亏损产品的表现归因
4. 给出 2-3 条具体优化建议（如分散、补充流动性等）"""

        analysis = await self.llm_chat(
            "你是资深投资顾问，输出专业且简洁的持仓分析，不要承诺收益。",
            prompt,
            temperature=0.4,
            max_tokens=600,
        )

        return {
            "found": True,
            "analysis": analysis
            or f"客户持仓市值 {portfolio['total_market_value']:,.0f} 元，"
            f"盈亏 {total_pnl:+,.0f} 元（{total_pnl_pct:+.2f}%），"
            f"前3大持仓集中度 {top3_weight}%，风险分布：{risk_desc}。",
            "portfolio": portfolio,
            "top3_weight": top3_weight,
            "total_pnl_pct": total_pnl_pct,
            "industry_distribution": industry_weight,
        }

    # -- asset allocation（F3.3 资产配置建议，真实落库画像标签）-----------
    # 产品类型 → 配置大类映射（用于当前配置 vs 目标配置偏差诊断）
    _TYPE_TO_CATEGORY = {
        "cash_management": "货币",
        "money_fund": "货币",
        "currency": "货币",
        "fixed_income": "债券",
        "bond_fund": "债券",
        "debt": "债券",
        "equity_fund": "股票",
        "balanced_fund": "股票",
        "private_strategy": "股票",
        "qdii_fund": "股票",
        "stock": "股票",
    }

    async def _asset_allocation(self, user_id: str) -> dict:
        """根据客户画像风险等级与资产规模生成配置比例建议，并写入画像标签。

        附带当前持仓 vs 目标配置的偏差诊断（参考项目 allocation.py 思路）：
        - adjustments：每类别的当前比例/目标比例/偏差（百分点）/建议动作（±5% 阈值）
        - review_required：任一类别偏差 ≥ 20 个百分点时置真，提示人工复核
        """
        from decimal import Decimal

        from app.profile_domain.tag_governance import (
            ExtractedProfileTag,
            ProfileTagCode,
        )
        from app.services.profile_tag_service import TagGovernanceService

        profile = await self._load_profile(user_id)
        if profile is None:
            return {"found": False, "reply": "该客户暂无画像，无法生成资产配置建议。"}
        risk = str(profile.get("risk_level") or "C1").upper()
        # 保守→激进 五档配置模板
        TEMPLATES = {
            "C1": {"货币": 40, "债券": 40, "股票": 10, "现金": 10},
            "C2": {"货币": 30, "债券": 50, "股票": 10, "现金": 10},
            "C3": {"货币": 20, "债券": 40, "股票": 30, "现金": 10},
            "C4": {"货币": 10, "债券": 30, "股票": 50, "现金": 10},
            "C5": {"货币": 5, "债券": 20, "股票": 65, "现金": 10},
        }
        allocation = TEMPLATES.get(risk, TEMPLATES["C3"])

        # ---- 当前配置 vs 目标配置偏差诊断 ----
        current_amounts: dict[str, float] = {}
        try:
            portfolio = await self._portfolio(user_id)
            for item in portfolio["items"]:
                category = self._TYPE_TO_CATEGORY.get(item["product_type"], "其他")
                current_amounts[category] = (
                    current_amounts.get(category, 0.0) + item["market_value"]
                )
            portfolio_total = sum(current_amounts.values())
        except Exception:  # noqa: BLE001 - 持仓读取失败不影响目标配置输出
            current_amounts, portfolio_total = {}, 0.0

        adjustments = []
        review_required = False
        for category, target_ratio in allocation.items():
            current_ratio = (
                current_amounts.get(category, 0.0) / portfolio_total * 100
                if portfolio_total
                else 0.0
            )
            deviation = round(current_ratio - target_ratio, 2)
            if abs(deviation) >= 20:
                review_required = True
            adjustments.append(
                {
                    "category": category,
                    "current_ratio": round(current_ratio, 2),
                    "target_ratio": target_ratio,
                    "deviation": deviation,
                    "action": (
                        "increase"
                        if deviation < -5
                        else "reduce"
                        if deviation > 5
                        else "hold"
                    ),
                }
            )

        async with self.database.session_factory() as session:
            governance = TagGovernanceService()
            await governance.apply_tags(
                session,
                user_id,
                [
                    ExtractedProfileTag(
                        tag_code=ProfileTagCode.INVESTMENT_GOAL,
                        tag_value={
                            "C1": "CAPITAL_PRESERVATION",
                            "C2": "STEADY_GROWTH",
                            "C3": "STEADY_GROWTH",
                            "C4": "LONG_TERM_GROWTH",
                            "C5": "HIGH_RETURN",
                        }.get(risk, "STEADY_GROWTH"),
                        confidence=Decimal("0.8"),
                        evidence_quote=f"资产配置建议：{risk} 风险等级配置模板",
                    )
                ],
                source_type="SYSTEM_BEHAVIOR",
                extraction_method="RULE",
            )
            await session.commit()
        return {
            "found": True,
            "risk_level": risk,
            "allocation": allocation,
            "total_asset": profile.get("total_asset", 0),
            "investable_asset": profile.get("investable_asset", 0),
            "current_allocation": current_amounts,
            "portfolio_total": portfolio_total,
            "adjustments": adjustments,
            "review_required": review_required,
        }

    # -- comparison（F3.3 对比分析：图谱交集查询）--------------------------
    async def _comparison(self, user_id_a: str, user_id_b: str) -> dict:
        """对比两位客户的共同持仓、风险差异与行业集中度差异。

        兼容原有 common/only_a/only_b 字段，新增：
        - risk_difference：两位客户风险等级差异
        - concentration：各自行业集中度（图谱增强）
        - advice：分客户差异化建议
        """
        portfolio_a = await self._portfolio(user_id_a)
        portfolio_b = await self._portfolio(user_id_b)
        products_a = {item["product"] for item in portfolio_a["items"]}
        products_b = {item["product"] for item in portfolio_b["items"]}
        common = sorted(products_a & products_b)
        only_a = sorted(products_a - products_b)
        only_b = sorted(products_b - products_a)

        # 风险等级差异
        profile_a = await self._load_profile(user_id_a)
        profile_b = await self._load_profile(user_id_b)
        risk_a = profile_a.get("risk_level") if profile_a else None
        risk_b = profile_b.get("risk_level") if profile_b else None

        # 行业集中度差异（图谱增强）
        async def _concentration(portfolio: dict) -> dict:
            industry_map = await self._graph_enhance_holdings(portfolio)
            if not industry_map:
                return {"top_industry": None, "top_ratio": 0.0, "level": "no_data"}
            total = sum(industry_map.values()) or 1.0
            top_industry = max(industry_map, key=industry_map.get)
            return {
                "top_industry": top_industry,
                "top_ratio": round(industry_map[top_industry] / total * 100, 1),
                "level": (
                    "high"
                    if industry_map[top_industry] / total >= 0.5
                    else "medium"
                    if industry_map[top_industry] / total >= 0.35
                    else "normal"
                ),
            }

        def _advice(risk: str | None, concentration: dict) -> list[str]:
            advice = []
            if concentration.get("level") == "high":
                advice.append("行业集中度较高，建议分散配置以降低单一行业风险。")
            if concentration.get("level") == "no_data":
                advice.append("缺少图谱行业数据，建议补充后再评估集中度。")
            if risk in {"C1", "C2"} and concentration.get("level") in {
                "medium",
                "high",
            }:
                advice.append("客户风险承受能力较低，持仓集中度偏高，建议重点关注。")
            return advice

        conc_a = await _concentration(portfolio_a)
        conc_b = await _concentration(portfolio_b)

        return {
            "found": True,
            "common": common,
            "only_a": only_a,
            "only_b": only_b,
            "risk_difference": {
                "a": risk_a,
                "b": risk_b,
                "different": risk_a != risk_b,
            },
            "concentration": {"a": conc_a, "b": conc_b},
            "advice": {
                "a": _advice(risk_a, conc_a),
                "b": _advice(risk_b, conc_b),
            },
        }

    async def _graph_enhance_holdings(self, portfolio: dict) -> dict[str, float]:
        """将持仓按图谱行业聚合市值（用于对比集中度）。"""
        industry_map: dict[str, float] = {}
        try:
            product_ids = [item["product_id"] for item in portfolio.get("items", [])]
            industry_by_pid = await self._graph_enhance(product_ids[:10])
            for item in portfolio.get("items", []):
                industry = industry_by_pid.get(item["product_id"])
                if industry:
                    industry_map[industry] = (
                        industry_map.get(industry, 0.0) + item["market_value"]
                    )
        except Exception:  # noqa: BLE001 - 图谱失败降级为空
            return {}
        return industry_map

    async def run(self, message: str, context: AgentContext) -> AgentResult:
        user_id = context.metadata.get("customer_id") or context.user_id
        if not user_id:
            return self.fail("缺少客户信息", ["context 中未提供 user_id / customer_id"])
        user_id = await self._normalize_user_id(user_id)
        if not user_id:
            return self.fail(
                "未找到目标客户",
                ["customer_id 未匹配到有效客户（数字 ID 或用户名）"],
            )

        # 投顾场景：消息中常以客户名指代目标客户（如"为零售投资者推荐"）。
        # 当 customer_id 是员工本人（无画像）或未指定时，从消息中解析客户名。
        profile = await self._load_profile(user_id)
        if profile is None:
            resolved = await self._resolve_customer_from_message(message)
            if resolved:
                user_id = resolved
                profile = await self._load_profile(user_id)

        text = message.lower()
        # 对比分析："共同持仓/有什么不同/对比" → 需要两个客户标识
        if any(k in text for k in ["对比", "共同持仓", "有什么不同"]):
            target = (context.metadata.get("target_customer_id") or "").strip()
            target = await self._normalize_user_id(target)
            # 未显式传 target 时，从消息中解析两个客户（如"零售投资者和高净值客户的持仓"）
            if not target:
                customer_ids = await self._resolve_two_customers_from_message(message)
                if len(customer_ids) == 2:
                    user_id, target = customer_ids
            if not target:
                return self.ok(
                    "对比分析需要两个客户。请在消息中指定两个客户，例如"
                    "「对比零售投资者和高净值客户的持仓」，或通过 target_customer_id 传入。",
                    data={"found": False, "need_target": True},
                )
            result = await self._comparison(user_id, target)
            if not result["found"]:
                return self.ok(result["reply"], data={"found": False})
            lines = [
                f"客户持仓对比：共同持有 {len(result['common'])} 只产品"
                + (f"（{'、'.join(result['common'])}）" if result["common"] else ""),
            ]
            if result["only_a"]:
                lines.append(f"仅 A 持有：{'、'.join(result['only_a'])}")
            if result["only_b"]:
                lines.append(f"仅 B 持有：{'、'.join(result['only_b'])}")
            # 风险等级差异（新增）
            risk_diff = result.get("risk_difference", {})
            if risk_diff.get("a") or risk_diff.get("b"):
                if risk_diff.get("different"):
                    lines.append(
                        f"风险等级差异：A 为 {risk_diff.get('a') or '未测评'}，"
                        f"B 为 {risk_diff.get('b') or '未测评'}，两位客户风险承受能力不同，"
                        "建议分别复核适当性。"
                    )
                else:
                    lines.append(f"风险等级一致：均为 {risk_diff.get('a')}。")
            # 行业集中度提示
            for side, label in (("a", "A"), ("b", "B")):
                conc = result.get("concentration", {}).get(side, {})
                if conc.get("top_industry") and conc.get("level") == "high":
                    lines.append(
                        f"客户 {label} 行业集中度偏高：{conc['top_industry']} "
                        f"占 {conc['top_ratio']}%。"
                    )
            return self.ok("\n".join(lines), data=result, confidence=0.85)

        # 资产配置建议："配置比例/怎么分配/资产建议"
        if any(k in text for k in ["配置", "怎么分配", "资产建议", "比例建议"]):
            result = await self._asset_allocation(user_id)
            if not result["found"]:
                return self.ok(result["reply"], data={"found": False})
            alloc = " + ".join(f"{k} {v}%" for k, v in result["allocation"].items())
            return self.ok(
                f"基于客户风险等级 {result['risk_level']}，建议配置比例：{alloc}。"
                "（已写入画像标签，供后续推荐引用）",
                data=result,
                confidence=0.85,
            )

        if any(k in text for k in ["持仓", "集中", "分布", "持仓分析"]):
            portfolio_analysis = await self._analyze_portfolio(user_id)
            if not portfolio_analysis["found"]:
                return self.ok(portfolio_analysis["reply"], data={"found": False})
            # 结构化数据：总览 + 明细 + 集中度 + 风险分布
            data = {
                "found": True,
                "analysis": portfolio_analysis["analysis"],
                "portfolio": portfolio_analysis["portfolio"],
                "top3_weight": portfolio_analysis["top3_weight"],
                "total_pnl_pct": portfolio_analysis["total_pnl_pct"],
                "industry_distribution": portfolio_analysis.get(
                    "industry_distribution", {}
                ),
            }
            summary = (
                f"客户当前持有 {len(portfolio_analysis['portfolio']['items'])} 只产品，"
                f"总市值 {portfolio_analysis['portfolio']['total_market_value']:,.2f} 元，"
                f"总盈亏 {portfolio_analysis['portfolio']['total_profit_loss']:+,.2f} 元"
                f"（{portfolio_analysis['total_pnl_pct']:+.2f}%）。\n\n"
                f"{portfolio_analysis['analysis']}"
            )
            return self.ok(summary, data=data, confidence=0.9)

        result = await self._recommend(message, user_id)
        if not result["found"]:
            data: dict = {"profile_found": False}
            if result.get("blocked_by_risk"):
                data["blocked_by_risk"] = True
                data["risk_alert"] = result.get("risk_alert")
                data["profile"] = result.get("profile")
            return self.ok(result["reply"], data=data, confidence=0.5)
        profile = result["profile"]
        return self.ok(
            result["reply"],
            data={
                "products": result["products"],
                "reasons": result["reasons"],
                "excluded": result.get("excluded", []),
                "excluded_count": result.get("excluded_count", 0),
                "guard": result.get("guard"),
                "profile": profile,
                "risk_alert": result.get("risk_alert"),
            },
            confidence=result.get("confidence", 0.85),
        )
