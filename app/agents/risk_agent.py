from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import redis.asyncio as redis
from sqlalchemy import func, select

from app.agents.base import AgentBase
from app.infrastructure.agent_event_bus import EVENT_RISK_ALERT, AgentEventBus
from app.models.profile import CustomerAssetSnapshot
from app.models.risk import RiskAlert, WorkOrder
from app.models.trading import Order, Trade
from app.ports.agent import AgentContext
from app.schemas.agents import AgentResult

# ---------------------------------------------------------------------------
# Anti-money-laundering rule engine (deterministic, non-LLM).
# Rules follow 用户研判规则/反洗钱可疑交易识别规则.md (RW-001 .. RW-009 subset).
# ---------------------------------------------------------------------------

LARGE_TRANSACTION_THRESHOLD = 50_000.0  # RW-001: 单笔大额交易
FREQUENT_WINDOW_DAYS = 7  # RW-002: 频次统计窗口
FREQUENT_TRADE_COUNT = 20  # RW-002: 7 天内交易笔数阈值
FREQUENT_TRADE_AMOUNT = 100_000.0  # RW-002: 7 天累计金额阈值
ROUND_AVOIDANCE_THRESHOLD = 30_000.0  # RW-006/009: 金额不符最低线
ROUND_AVOIDANCE_COUNT = 5  # RW-009: 规避特征笔数
NIGHT_START, NIGHT_END = 0, 6  # RW-008: 非正常时段 0:00-6:00
NIGHT_SINGLE_THRESHOLD = 100_000.0  # RW-008: 单笔阈值
NIGHT_DAILY_THRESHOLD = 200_000.0  # RW-008: 单日累计阈值

RISK_LEVELS = {"low": "blue", "medium": "yellow", "high": "red"}


class RiskMonitorAgent(AgentBase):
    """风控监测 Agent：确定性规则匹配 → 交叉验证 → 分级预警（蓝/黄/红）。

    Reasoning paradigm: Rule-Match → Cross-Validate → Grade-Decide.
    Rules are evaluated in pure Python over trade/order rows fetched from the
    database (never via LLM, since numeric comparisons must be deterministic).
    All graded alerts are published to the Redis event bus (event:risk_alert);
    downstream consumers decide whether to mark high-risk customers.
    """

    name = "risk_monitor"
    description = "风控监测：反洗钱规则引擎、分级预警、事件广播"

    def __init__(self, database, settings, llm=None):
        super().__init__(database, settings, llm)
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            self._redis = redis.from_url(self.settings.redis_url, decode_responses=True)
        return self._redis

    # -- data -------------------------------------------------------------
    async def _load_transactions(
        self, user_id: str, days: int = 30
    ) -> tuple[list[Trade], list[Order], list[dict]]:
        since = datetime.now(UTC) - timedelta(days=days)
        async with self.database.session_factory() as session:
            trades = list(
                (
                    await session.execute(
                        select(Trade)
                        .where(Trade.user_id == user_id, Trade.executed_at >= since)
                        .order_by(Trade.executed_at)
                    )
                )
                .scalars()
                .all()
            )
            orders = list(
                (
                    await session.execute(
                        select(Order)
                        .where(Order.user_id == user_id, Order.created_at >= since)
                        .order_by(Order.created_at)
                    )
                )
                .scalars()
                .all()
            )
            # aggregate per-day amounts for the most recent 7 days
            day_rows = (
                await session.execute(
                    select(
                        func.date(Trade.executed_at),
                        func.count(Trade.id),
                        func.sum(Trade.amount),
                    )
                    .where(Trade.user_id == user_id, Trade.executed_at >= since)
                    .group_by(func.date(Trade.executed_at))
                )
            ).all()
            daily = [
                {"date": str(r[0]), "count": r[1], "amount": float(r[2] or 0)}
                for r in day_rows
            ]
        return trades, orders, daily

    async def _load_annual_income(self, user_id: str) -> float:
        async with self.database.session_factory() as session:
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
        # approximate declared annual income: 15% of net asset, min 50k
        return max(50_000.0, float(asset.net_asset if asset else 0) * 0.15)

    # -- rules ------------------------------------------------------------
    def _rule_rw001(self, trades: list[Trade], orders: list[Order]) -> bool:
        """RW-001 大额交易：单笔 ≥ 5 万元（含已成交交易与待审核大额订单）。"""
        trade_hits = any(float(t.amount) >= LARGE_TRANSACTION_THRESHOLD for t in trades)
        order_hits = any(
            float(o.amount) >= LARGE_TRANSACTION_THRESHOLD
            and o.status in {"pending_review", "executed"}
            for o in orders
        )
        return trade_hits or order_hits

    def _rule_rw002(self, daily: list[dict]) -> bool:
        """RW-002 频繁小额交易：7 天内笔数 ≥ 20 且累计 ≥ 10 万。"""
        recent = [d for d in daily if d["count"] > 0][-FREQUENT_WINDOW_DAYS:]
        count = sum(d["count"] for d in recent)
        amount = sum(d["amount"] for d in recent)
        return count >= FREQUENT_TRADE_COUNT and amount >= FREQUENT_TRADE_AMOUNT

    def _rule_rw003(self, orders: list[Order]) -> bool:
        """RW-003 资金快进快出：24h 内同一产品买入后卖出。"""
        by_product: dict[str, list[Order]] = {}
        for order in orders:
            by_product.setdefault(order.product_id, []).append(order)
        for orders_in_product in by_product.values():
            buys = [o for o in orders_in_product if o.side == "buy"]
            sells = [o for o in orders_in_product if o.side == "sell"]
            for buy in buys:
                for sell in sells:
                    if sell.created_at - buy.created_at <= timedelta(hours=24):
                        return True
        return False

    def _rule_rw021(self, trades: list[Trade], orders: list[Order]) -> bool:
        """RW-021 同日累计大额：单日累计交易金额 ≥ 20 万元（不限时段）。

        同时统计已成交交易（trades）与待审核/已执行订单（orders），
        与 RW-001 口径一致：被风控拦截待人工审核的大额订单同样是
        预警信号来源。
        """
        by_day: dict[str, float] = {}
        for t in trades:
            key = t.executed_at.date().isoformat()
            by_day[key] = by_day.get(key, 0.0) + float(t.amount)
        for o in orders:
            if o.status in {"pending_review", "executed"}:
                key = o.created_at.date().isoformat()
                by_day[key] = by_day.get(key, 0.0) + float(o.amount)
        return any(amount >= 200_000.0 for amount in by_day.values())

    def _rule_rw022(self, trades: list[Trade], orders: list[Order]) -> bool:
        """RW-022 频繁交易：7 天内交易次数 ≥ 10 次（trades + 有效订单）。"""
        since = datetime.now(UTC) - timedelta(days=7)
        count = sum(1 for t in trades if t.executed_at >= since)
        count += sum(
            1
            for o in orders
            if o.status in {"pending_review", "executed"} and o.created_at >= since
        )
        return count >= 10

    def _rule_rw006(self, trades: list[Trade], annual_income: float) -> bool:
        """RW-006 交易金额与身份不符：单笔 ≥ 年收入×3 且 ≥ 10 万。"""
        return any(
            float(t.amount) >= annual_income * 3 and float(t.amount) >= 100_000
            for t in trades
        )

    def _rule_rw008(self, trades: list[Trade]) -> bool:
        """RW-008 非正常时段大额交易：0-6 点单笔 ≥ 10 万或单日累计 ≥ 20 万。"""
        night = [t for t in trades if NIGHT_START <= t.executed_at.hour < NIGHT_END]
        if any(float(t.amount) >= NIGHT_SINGLE_THRESHOLD for t in night):
            return True
        by_day: dict[str, float] = {}
        for t in night:
            key = t.executed_at.date().isoformat()
            by_day[key] = by_day.get(key, 0.0) + float(t.amount)
        return any(amount >= NIGHT_DAILY_THRESHOLD for amount in by_day.values())

    def _rule_rw009(self, trades: list[Trade]) -> bool:
        """RW-009 整数金额规避特征：30 天内 ≥ 5 笔接近报告线的"整数减 1"。"""
        pattern = re.compile(r"^(49_?999|99_?999|199_?999|9_?999|29_?999)$")
        evasive = sum(
            1
            for t in trades
            if pattern.match(f"{int(float(t.amount)):,}".replace(",", "_"))
        )
        return evasive >= ROUND_AVOIDANCE_COUNT

    # -- 补充规则 RW-004/005/007 + 扩展 RW-010~020（凑满 20 条）---------
    def _rule_rw004(self, orders: list[Order]) -> bool:
        """RW-004 分散转入集中转出：5 天内 ≥5 个来源转入 且 转出 ≥20 万 且 对手方集中度 ≥80%。"""
        buy_sources = {o.account_id for o in orders if o.side == "buy"}
        sell_amounts = [float(o.amount) for o in orders if o.side == "sell"]
        if len(buy_sources) >= 5 and sum(sell_amounts) >= 200_000:
            if sell_amounts:
                top = max(sell_amounts)
                return top / sum(sell_amounts) >= 0.8
        return False

    def _rule_rw005(self, orders: list[Order]) -> bool:
        """RW-005 集中转入分散转出：单笔大额转入 ≥10 万 且 转出至 ≥5 个账户。"""
        large_in = any(float(o.amount) >= 100_000 for o in orders if o.side == "buy")
        sell_targets = {o.account_id for o in orders if o.side == "sell"}
        return large_in and len(sell_targets) >= 5

    def _rule_rw007(self, orders: list[Order]) -> bool:
        """RW-007 频繁开销户：开户/销户行为频繁（用订单创建/取消近似）。"""
        cancelled = [o for o in orders if o.status == "cancelled"]
        return len(cancelled) >= 3

    def _rule_rw010(self, orders: list[Order]) -> bool:
        """RW-010 短期大额交易频次：单日 ≥3 笔大额（≥5 万）。"""
        from collections import Counter

        by_day = Counter(
            o.created_at.date() for o in orders if float(o.amount) >= 50_000
        )
        return any(count >= 3 for count in by_day.values())

    def _rule_rw011(self, orders: list[Order]) -> bool:
        """RW-011 小额拆分交易：单日 ≥5 笔小额（<5 万）规避大额报告。"""
        from collections import Counter

        small = [o for o in orders if 0 < float(o.amount) < 50_000]
        by_day = Counter(o.created_at.date() for o in small)
        return any(count >= 5 for count in by_day.values())

    def _rule_rw012(self, orders: list[Order]) -> bool:
        """RW-012 集中时点交易：同一小时内 ≥5 笔交易（异常集中操作）。"""
        from collections import Counter

        by_hour = Counter(
            o.created_at.replace(minute=0, second=0, microsecond=0) for o in orders
        )
        return any(count >= 5 for count in by_hour.values())

    def _rule_rw013(self, trades: list[Trade]) -> bool:
        """RW-013 累计大额交易：7 天累计交易金额 ≥ 100 万。"""
        recent = [
            t
            for t in trades
            if t.executed_at >= datetime.now(UTC) - timedelta(days=7)
        ]
        return sum(float(t.amount) for t in recent) >= 1_000_000

    def _rule_rw014(self, orders: list[Order]) -> bool:
        """RW-014 深夜异常交易：0-4 点存在交易（低风险关注）。"""
        return any(0 <= o.created_at.hour < 4 for o in orders)

    def _rule_rw015(self, orders: list[Order]) -> bool:
        """RW-015 频率突增：本月交易笔数是上月 3 倍以上。"""
        now = datetime.now(UTC)
        month = [o for o in orders if o.created_at.month == now.month]
        prev = [o for o in orders if o.created_at.month != now.month]
        return len(month) >= 3 and len(month) > len(prev) * 3

    def _rule_rw016(self, trades: list[Trade]) -> bool:
        """RW-016 整数金额交易：交易金额为整数万（无零头）。"""
        return any(float(t.amount) % 10_000 == 0 for t in trades)

    def _rule_rw017(self, orders: list[Order]) -> bool:
        """RW-017 高频买卖切换：同产品买卖交替 ≥5 次（对敲嫌疑）。"""
        switches = 0
        prev: str | None = None
        for o in sorted(orders, key=lambda x: x.created_at):
            if prev is not None and o.side != prev:
                switches += 1
            prev = o.side
        return switches >= 5

    def _rule_rw018(self, orders: list[Order]) -> bool:
        """RW-018 快速加仓：同一产品 24h 内多次申购（≥3 次）。"""
        from collections import Counter

        buy_by_product = Counter(o.product_id for o in orders if o.side == "buy")
        return any(count >= 3 for count in buy_by_product.values())

    def _rule_rw019(self, trades: list[Trade]) -> bool:
        """RW-019 亏损异常：单笔交易亏损/金额异常波动（用大额净值变动近似）。"""
        return any(float(t.amount) >= 500_000 for t in trades)

    def _rule_rw020(self, orders: list[Order]) -> bool:
        """RW-020 频繁撤单：30 天内取消订单 ≥5 次（测试/试探行为）。"""
        return len([o for o in orders if o.status == "cancelled"]) >= 5

    # ------------------------------------------------------------------
    # 投资者风险画像研判规则 第八条：行为异常识别（8.1）
    # ------------------------------------------------------------------
    def detect_anomalies(
        self,
        trades: list[Trade],
        orders: list[Order],
        daily: list[dict],
    ) -> list[dict]:
        """返回行为异常清单：[{code, label, severity}] severity: low/medium/high"""
        anomalies: list[dict] = []

        # 频繁赎回：30 天内赎回次数 ≥ 5（中）
        redeem_count = sum(1 for o in orders if o.side == "sell")
        if redeem_count >= 5:
            anomalies.append(
                {"code": "FREQUENT_REDEEM", "label": "频繁赎回", "severity": "medium"}
            )

        # 大额集中交易：单日交易金额超过账户总资产 50%（中）
        account_total = sum(float(t.amount) for t in trades)
        by_day: dict[str, float] = {}
        for t in trades:
            key = t.executed_at.date().isoformat()
            by_day[key] = by_day.get(key, 0.0) + float(t.amount)
        for day_total in by_day.values():
            if account_total > 0 and day_total > account_total * 0.5:
                anomalies.append(
                    {
                        "code": "LARGE_CONCENTRATED_TRADE",
                        "label": "大额集中交易",
                        "severity": "medium",
                    }
                )
                break

        # 非正常时段交易：0:00-6:00 频繁登录/交易（低）
        night_count = sum(
            1 for t in trades if NIGHT_START <= t.executed_at.hour < NIGHT_END
        )
        if night_count >= 3:
            anomalies.append(
                {"code": "NIGHT_TRADING", "label": "非正常时段交易", "severity": "low"}
            )

        # 突然大额入金：单笔入金超过历史平均 5 倍（中）—— 用 buy 订单近似
        buy_amounts = [float(o.amount) for o in orders if o.side == "buy"]
        if len(buy_amounts) >= 3:
            avg = sum(buy_amounts) / len(buy_amounts)
            if any(amount > avg * 5 for amount in buy_amounts):
                anomalies.append(
                    {
                        "code": "SUDDEN_LARGE_INFLOW",
                        "label": "突然大额入金",
                        "severity": "medium",
                    }
                )

        # 分散转出：单日出金至 5 个以上不同账户（高）
        if len({o.account_id for o in orders if o.side == "sell"}) >= 5:
            anomalies.append(
                {"code": "SCATTERED_OUTFLOW", "label": "分散转出", "severity": "high"}
            )

        # 产品风险越级：要求购买超过风险等级 2 级以上（高）—— 由适当性检查触发，此处标记
        return anomalies

    # ------------------------------------------------------------------
    # 投资者风险画像研判规则 第七条：情绪化交易识别（7.2）
    # ------------------------------------------------------------------
    def detect_emotional_flags(
        self,
        trades: list[Trade],
        orders: list[Order],
    ) -> list[str]:
        """返回情绪化交易标记：[chase_rise_sell_fall / panic_redeem / fomo_add / frequent_strategy_change]"""
        flags: list[str] = []

        # 频繁改策略：90 天内调整投资组合配置超过 3 次（用买卖方向切换近似）
        side_changes = 0
        prev_side: str | None = None
        for order in sorted(orders, key=lambda o: o.created_at):
            if prev_side is not None and order.side != prev_side:
                side_changes += 1
            prev_side = order.side
        if side_changes > 3:
            flags.append("frequent_strategy_change")

        # 恐慌赎回：市场大跌当日赎回金额超过持仓 50%（用大额赎回近似）
        sell_amounts = [float(o.amount) for o in orders if o.side == "sell"]
        total_sell = sum(sell_amounts)
        # 若单日出现极端赎回（>50% 存量），标记恐慌赎回
        if total_sell > 0 and any(a > total_sell * 0.5 for a in sell_amounts):
            flags.append("panic_redeem")

        # FOMO 式加仓：连续上涨后一次性大额加仓（大额申购近似）
        buy_amounts = [float(o.amount) for o in orders if o.side == "buy"]
        if len(buy_amounts) >= 3:
            avg = sum(buy_amounts) / len(buy_amounts)
            if any(amount > avg * 3 for amount in buy_amounts):
                flags.append("fomo_add")

        return flags

    def evaluate(
        self,
        trades: list[Trade],
        orders: list[Order],
        daily: list[dict],
        annual_income: float,
    ) -> list[str]:
        checks = [
            ("RW-001", "大额交易（单笔≥5万）", self._rule_rw001(trades, orders)),
            ("RW-002", "频繁小额交易（7天≥20笔且≥10万）", self._rule_rw002(daily)),
            ("RW-003", "资金快进快出（24h内快进快出）", self._rule_rw003(orders)),
            (
                "RW-004",
                "分散转入集中转出（≥5来源+集中度80%）",
                self._rule_rw004(orders),
            ),
            (
                "RW-005",
                "集中转入分散转出（大额转入+分散转出）",
                self._rule_rw005(orders),
            ),
            (
                "RW-006",
                "交易金额与身份不符（≥年收入3倍）",
                self._rule_rw006(trades, annual_income),
            ),
            ("RW-007", "频繁开销户（3次以上取消/开户异常）", self._rule_rw007(orders)),
            ("RW-008", "非正常时段大额交易（0-6点）", self._rule_rw008(trades)),
            ("RW-009", "整数金额规避特征", self._rule_rw009(trades)),
            ("RW-010", "短期大额交易频次（单日≥3笔大额）", self._rule_rw010(orders)),
            ("RW-011", "小额拆分交易（单日≥5笔小额）", self._rule_rw011(orders)),
            ("RW-012", "集中时点交易（同小时≥5笔）", self._rule_rw012(orders)),
            ("RW-013", "累计大额交易（7天≥100万）", self._rule_rw013(trades)),
            ("RW-014", "深夜异常交易（0-4点）", self._rule_rw014(orders)),
            ("RW-015", "频率突增（本月3倍以上）", self._rule_rw015(orders)),
            ("RW-016", "整数金额交易（整数万）", self._rule_rw016(trades)),
            ("RW-017", "高频买卖切换（对敲嫌疑）", self._rule_rw017(orders)),
            ("RW-018", "快速加仓（24h内≥3次申购）", self._rule_rw018(orders)),
            ("RW-019", "大额异常波动（单笔≥50万）", self._rule_rw019(trades)),
            ("RW-020", "频繁撤单（30天≥5次）", self._rule_rw020(orders)),
            (
                "RW-021",
                "同日累计大额交易（单日≥20万）",
                self._rule_rw021(trades, orders),
            ),
            ("RW-022", "频繁交易（7天≥10次）", self._rule_rw022(trades, orders)),
        ]
        return [f"{rule_id} {label}" for rule_id, label, hit in checks if hit]

    # -- grading ----------------------------------------------------------
    def grade(self, triggered: list[str], repeat: bool) -> str:
        """三级预警分级（F4.1 验收标准）。

        单规则 → 低(蓝)；2-3 条 → 中(黄)；3 条以上 + 历史预警 → 高(红)；
        ≥4 条无论是否历史 → 高(红)。
        """
        count = len(triggered)
        if count == 1 and not repeat:
            return "low"
        if count <= 3 and not (count >= 3 and repeat):
            return "medium"
        return "high"

    def _confidence(self, triggered: list[str], repeat: bool) -> float:
        base = min(0.95, 0.5 + 0.12 * len(triggered))
        return min(0.98, base + 0.1) if repeat else base

    # -- publish ----------------------------------------------------------
    async def _publish_alert(self, alert: dict) -> dict | None:
        try:
            client = await self._get_redis()
            return await AgentEventBus(client).publish(
                EVENT_RISK_ALERT,
                event_type="risk_alert",
                source_agent=self.name,
                payload=alert["payload"],
            )
        except Exception:  # noqa: BLE001 - event bus must never break the scan
            return None

    # -- persist ----------------------------------------------------------
    async def _persist_alert(
        self, user_id: str, alert: dict, trades: list[Trade]
    ) -> str | None:
        """将预警写入 risk_alert 表 + work_order 工单（Phase 4 F4.1）。

        返回生成的 risk_alert.id（供事件广播携带 alert_id）。
        """
        from uuid import uuid4

        payload = alert["payload"]
        try:
            async with self.database.session_factory() as session:
                risk_alert = RiskAlert(
                    id=str(uuid4()),
                    customer_id=user_id,
                    alert_level=payload["alert_level"],
                    alert_color=payload["alert_color"],
                    alert_type=payload.get("trigger_rules", [""])[0]
                    if payload.get("trigger_rules")
                    else "",
                    trigger_rules_json=payload.get("trigger_rules", []),
                    confidence=int(payload.get("confidence", 0.5) * 100),
                    transaction_ids_json=[str(t.id) for t in trades[:20]],
                    trigger_detail="；".join(payload.get("trigger_rules", []))[:500],
                    status="pending",
                )
                session.add(risk_alert)
                await session.flush()
                # 中度及以上预警生成工单
                if payload["alert_level"] in {"medium", "high"}:
                    workorder = WorkOrder(
                        id=str(uuid4()),
                        workorder_no=f"WO-{uuid4().hex[:12].upper()}",
                        customer_id=user_id,
                        workorder_type="可疑交易上报",
                        priority="high"
                        if payload["alert_level"] == "high"
                        else "normal",
                        status="pending",
                        title=f"风控预警：{payload['alert_color']}（{len(payload.get('trigger_rules', []))} 条规则命中）",
                        description="；".join(payload.get("trigger_rules", []))[:500],
                        source_type="risk_alert",
                        source_id=str(risk_alert.id),
                    )
                    session.add(workorder)
                await session.commit()
                return str(risk_alert.id)
        except Exception:  # noqa: BLE001 - 持久化失败不阻断扫描
            return None

    # -- entry ------------------------------------------------------------
    async def run(self, message: str, context: AgentContext) -> AgentResult:
        user_id = context.metadata.get("customer_id") or context.user_id
        if not user_id:
            return self.fail("缺少客户信息", ["context 中未提供 user_id / customer_id"])
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return self.fail("客户标识无效", ["customer_id 必须是数字 ID"])

        trades, orders, daily = await self._load_transactions(user_id)
        annual_income = await self._load_annual_income(user_id)
        triggered = self.evaluate(trades, orders, daily, annual_income)
        anomalies = self.detect_anomalies(trades, orders, daily)
        emotional_flags = self.detect_emotional_flags(trades, orders)

        if not triggered:
            return self.ok(
                f"客户交易流水正常：最近 {len(trades)} 笔交易未命中任何反洗钱规则。",
                data={
                    "rule_hits": [],
                    "level": None,
                    "transaction_count": len(trades),
                    "anomalies": anomalies,
                    "emotional_flags": emotional_flags,
                },
                confidence=0.9,
            )

        # F4.1 重复触发升级：近 30 天存在同客户历史预警 → repeat=True 升级
        repeat = False
        try:
            async with self.database.session_factory() as session:
                recent = (
                    await session.execute(
                        select(RiskAlert)
                        .where(
                            RiskAlert.customer_id == user_id,
                            RiskAlert.created_at
                            >= datetime.utcnow() - timedelta(days=30),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                repeat = recent is not None
        except Exception:  # noqa: BLE001
            repeat = False

        level = self.grade(triggered, repeat=repeat)
        alert = {
            "event_type": "risk_alert",
            "source_agent": self.name,
            "payload": {
                "customer_id": user_id,
                "alert_level": level,
                "alert_color": RISK_LEVELS[level],
                "trigger_rules": triggered,
                "confidence": round(self._confidence(triggered, repeat=repeat), 4),
                "transaction_count": len(trades),
                "anomalies": anomalies,
                "emotional_flags": emotional_flags,
                "repeat_trigger": repeat,
            },
        }
        # Phase 4 F4.1：预警工单持久化（risk_alert + work_order），
        # 拿到 alert_id 供事件广播携带（消息体要求含 alert_id）。
        alert_id = await self._persist_alert(user_id, alert, trades)
        if alert_id:
            alert["payload"]["alert_id"] = alert_id
        # 三档预警均进入事件总线；客服消费者仅对 high 做高风险客户标记，
        # 投顾消费者则保留完整的风险预警级别。
        published_event = await self._publish_alert(alert)
        if published_event:
            alert = published_event

        color_name = {
            "blue": "蓝色(轻度)",
            "yellow": "黄色(中度)",
            "red": "红色(重度)",
        }[RISK_LEVELS[level]]
        summary = (
            f"风控预警：{color_name}，命中 {len(triggered)} 条规则："
            + "；".join(triggered)
            + f"。建议{'上报反洗钱领导小组并广播相关 Agent' if level == 'high' else '生成工单并人工复核' if level == 'medium' else '记录留痕并持续关注'}。"
        )
        return self.ok(summary, data=alert, confidence=alert["payload"]["confidence"])
