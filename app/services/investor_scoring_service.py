from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import (
    CustomerAssetSnapshot,
    CustomerHolding,
    CustomerProfile,
    CustomerRiskAssessment,
    Product,
)
from app.models.trading import Order, Trade

# ---------------------------------------------------------------------------
# 投资者风险画像研判规则（JR-RULE-2024-001 V2.3）四维加权评分
#
#   综合得分 = 维度一×25% + 维度二×25% + 维度三×30% + 维度四×20%
#   维度一 基础属性：满分25分（年龄/学历/职业/收入/资产 5 项均值）
#   维度二 投资经验：满分25分（年限/产品类型/频率/收益 4 项均值）
#   维度三 风险偏好：满分30分（问卷分映射 + 情绪化扣分 + 亏损承受调整）
#   维度四 行为异常：满分20分（异常计分）
# ---------------------------------------------------------------------------

# ---- 维度一：基础属性评分表（第五条）----
AGE_SCORES = [
    (18, 25, 8),
    (26, 35, 10),
    (36, 45, 9),
    (46, 55, 7),
    (56, 65, 5),
]  # (lo, hi, score)


def age_score(age: int | None) -> int:
    if not age:
        return 3  # 信息缺失保守 3 分
    if age < 18:
        return 2
    if age > 65:
        return 3
    for lo, hi, score in AGE_SCORES:
        if lo <= age <= hi:
            return score
    return 3


EDUCATION_SCORES = {
    "HIGH_SCHOOL_OR_BELOW": 4,
    "COLLEGE": 6,
    "BACHELOR": 8,
    "MASTER_OR_ABOVE": 10,
}


def education_score(level: str | None) -> int:
    if not level:
        return 4  # 缺失按保守
    key = level.strip().upper().replace("-", "_")
    if key in {"高中及以下", "高中", "中专"}:
        key = "HIGH_SCHOOL_OR_BELOW"
    elif key in {"大专", "专科"}:
        key = "COLLEGE"
    elif key in {"本科", "学士"}:
        key = "BACHELOR"
    elif key in {"硕士", "博士", "研究生", "硕士及以上"}:
        key = "MASTER_OR_ABOVE"
    return EDUCATION_SCORES.get(key, 4)


# 中文职业 → 研判规则职业类型分
OCCUPATION_SCORES = {
    "公务员": 10,
    "事业单位": 10,
    "事业单位员工": 10,
    "事业单位职工": 10,
    "国企": 9,
    "国企员工": 9,
    "国企职工": 9,
    "上市公司": 9,
    "上市公司员工": 9,
    "上市公司职工": 9,
    "医生": 8,
    "律师": 8,
    "工程师": 8,
    "技术人员": 8,
    "中小企业员工": 6,
    "中小企业职工": 6,
    "企业员工": 6,
    "自由职业": 5,
    "个体户": 5,
    "个体工商户": 5,
    "个体经营": 5,
    "退休": 4,
    "无业": 2,
    "无固定职业": 2,
}
# 画像标签标准值 → 分
OCCUPATION_TAG_SCORES = {
    "civil_servant": 10,
    "public_institution": 10,
    "state_owned_employee": 9,
    "listed_company_employee": 9,
    "doctor": 8,
    "lawyer": 8,
    "engineer": 8,
    "sme_employee": 6,
    "self_employed": 5,
    "retired": 4,
    "unemployed": 2,
}


def occupation_score(occupation: str | None) -> int:
    if not occupation:
        return 3  # 缺失保守
    text = occupation.strip()
    if text in OCCUPATION_TAG_SCORES:
        return OCCUPATION_TAG_SCORES[text]
    if text in OCCUPATION_SCORES:
        return OCCUPATION_SCORES[text]
    # 关键词匹配
    for keyword, score in OCCUPATION_SCORES.items():
        if keyword and keyword in text:
            return score
    return 3


def income_score(annual_income: float | None) -> int:
    if annual_income is None:
        return 3  # 缺失按最低工资估算，保守 3 分
    income = float(annual_income)
    if income < 100_000:
        return 3
    if income < 300_000:
        return 5
    if income < 500_000:
        return 7
    if income < 1_000_000:
        return 8
    if income < 3_000_000:
        return 9
    return 10


def asset_score(investable_asset: float | None) -> int:
    if investable_asset is None:
        return 2
    asset = float(investable_asset)
    if asset < 50_000:
        return 2
    if asset < 200_000:
        return 4
    if asset < 500_000:
        return 6
    if asset < 1_000_000:
        return 7
    if asset < 5_000_000:
        return 8
    if asset < 10_000_000:
        return 9
    return 10


def basic_dimension_score(
    age: int | None,
    education: str | None,
    occupation: str | None,
    annual_income: float | None,
    investable_asset: float | None,
) -> Decimal:
    """维度一 = (年龄+学历+职业+收入+资产) ÷ 5 ÷ 10 × 25（满分25）"""
    parts = [
        age_score(age),
        education_score(education),
        occupation_score(occupation),
        income_score(annual_income),
        asset_score(investable_asset),
    ]
    avg = Decimal(sum(parts)) / Decimal(5)
    return (avg / Decimal(10) * Decimal(25)).quantize(Decimal("0.01"))


# ---- 维度二：投资经验评分表（第六条）----
def experience_years_score(years: int | None) -> int:
    if years is None or years == 0:
        return 2
    if years < 1:
        return 4
    if years < 3:
        return 6
    if years < 5:
        return 8
    if years < 10:
        return 9
    return 10


# 产品类型（按风险等级取最高项）
PRODUCT_TYPE_SCORES = {
    "R1": 4,  # 货币基金/国债
    "R2": 5,  # 纯债/银行理财
    "R3": 7,  # 混合/指数
    "R4": 8,  # 股票/股基/ETF
    "R5": 10,  # 期货/私募/结构化
}


def product_type_score(product_risk_levels: list[str]) -> int:
    """持有产品类型复杂度（取最高项）。"""
    if not product_risk_levels:
        return 2  # 仅银行存款
    best = 2
    for raw in product_risk_levels:
        level = str(raw).upper().replace("C", "R")
        best = max(best, PRODUCT_TYPE_SCORES.get(level, 2))
    return best


def trade_frequency_score(order_count_30d: int) -> int:
    """交易频率：极低频/低频/中频/高频。"""
    if order_count_30d == 0:
        return 5  # 极低频
    if order_count_30d <= 3:
        return 7  # 低频（月均1-3次）
    if order_count_30d <= 10:
        return 8  # 中频
    return 6  # 高频（过度交易扣分）


def return_score(profit_loss_pct: float | None) -> int:
    """近三年平均年化收益。"""
    if profit_loss_pct is None:
        return 3  # 无历史记录
    if profit_loss_pct < -15:
        return 3
    if profit_loss_pct < -5:
        return 4
    if profit_loss_pct < 5:
        return 6
    if profit_loss_pct < 15:
        return 8
    return 9


def experience_dimension_score(
    years: int | None,
    product_risk_levels: list[str],
    order_count_30d: int,
    profit_loss_pct: float | None,
) -> Decimal:
    """维度二 = (年限+产品类型+频率+收益) ÷ 4 ÷ 10 × 25（满分25）"""
    parts = [
        experience_years_score(years),
        product_type_score(product_risk_levels),
        trade_frequency_score(order_count_30d),
        return_score(profit_loss_pct),
    ]
    avg = Decimal(sum(parts)) / Decimal(4)
    return (avg / Decimal(10) * Decimal(25)).quantize(Decimal("0.01"))


# ---- 维度三：风险偏好（第七条）----
def questionnaire_dimension_score(questionnaire_score: int | None) -> int:
    """问卷得分 → 维度三基础分（5/10/15/20/25）。"""
    if questionnaire_score is None:
        return 5  # 保守
    score = int(questionnaire_score)
    if score <= 35:
        return 5  # C1 保守型
    if score <= 50:
        return 10  # C2 稳健型
    if score <= 65:
        return 15  # C3 平衡型
    if score <= 80:
        return 20  # C4 进取型
    return 25  # C5 激进型


# 情绪化交易扣分（7.2）
EMOTIONAL_PENALTIES = {
    "chase_rise_sell_fall": -3,  # 追涨杀跌
    "panic_redeem": -5,  # 恐慌赎回
    "fomo_add": -2,  # FOMO 式加仓
    "frequent_strategy_change": -3,  # 频繁改策略
}


def loss_tolerance_adjustment(max_loss_pct: float | None) -> int:
    """亏损承受能力调整（7.3）。"""
    if max_loss_pct is None:
        return 0  # 基准
    if max_loss_pct <= 0:
        return -5  # 不能承受任何亏损
    if max_loss_pct <= 5:
        return -2
    if max_loss_pct <= 20:
        return 0
    if max_loss_pct <= 40:
        return 3
    return 5


def preference_dimension_score(
    questionnaire_score: int | None,
    emotional_flags: list[str],
    max_loss_pct: float | None,
) -> Decimal:
    """维度三 = 问卷映射 + 情绪化扣分 + 亏损承受调整（上限30，下限0）"""
    score = questionnaire_dimension_score(questionnaire_score)
    for flag in emotional_flags:
        score += EMOTIONAL_PENALTIES.get(flag, 0)
    score += loss_tolerance_adjustment(max_loss_pct)
    return Decimal(max(0, min(30, score)))


# ---- 维度四：行为异常（第八条）----
def behavior_dimension_score(anomaly_severities: list[str]) -> Decimal:
    """异常计分（满分20）：无异常20 / 1-2低15 / 1-2中10 / 3+中5 / 任何高0"""
    if not anomaly_severities:
        return Decimal("20")
    if "high" in anomaly_severities:
        return Decimal("0")
    medium = anomaly_severities.count("medium")
    low = anomaly_severities.count("low")
    if medium >= 3:
        return Decimal("5")
    if medium >= 1:
        return Decimal("10")
    if low >= 1:
        return Decimal("15")
    return Decimal("20")


def total_score(
    basic: Decimal, experience: Decimal, preference: Decimal, behavior: Decimal
) -> Decimal:
    """综合 = 25% + 25% + 30% + 20%"""
    raw = (
        basic * Decimal("0.25")
        + experience * Decimal("0.25")
        + preference * Decimal("0.30")
        + behavior * Decimal("0.20")
    )
    return raw.quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# 数据装配（从持久层读取客户数据计算四维分）
# ---------------------------------------------------------------------------
class InvestorScoringService:
    """按研判规则计算客户四维评分并输出明细（可解释）。"""

    async def score_customer(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        behavior_anomalies: list[dict] | None = None,
        emotional_flags: list[str] | None = None,
    ) -> dict:
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user_id)
            )
        ).scalar_one_or_none()
        if profile is None:
            raise ValueError("customer profile not found")
        risk = (
            (
                await session.execute(
                    select(CustomerRiskAssessment)
                    .where(
                        CustomerRiskAssessment.user_id == user_id,
                        CustomerRiskAssessment.status.in_(["active", "provisional"]),
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
        order_count_30d = (
            await session.execute(
                select(func.count(Order.id)).where(Order.user_id == user_id)
            )
        ).scalar() or 0

        # 持有产品风险等级（用于复杂度分）
        product_risk_levels: list[str] = []
        profit_pcts: list[float] = []
        for holding in holdings:
            product = (
                await session.execute(
                    select(Product).where(Product.id == holding.product_id)
                )
            ).scalar_one_or_none()
            if product is not None:
                product_risk_levels.append(product.risk_level)
            if holding.cost_amount:
                profit_pcts.append(
                    float(holding.profit_loss) / float(holding.cost_amount) * 100
                )
        avg_profit_pct = sum(profit_pcts) / len(profit_pcts) if profit_pcts else None

        # 问卷得分 → 风险偏好
        questionnaire_score = risk.score if risk else None

        # 亏损承受（从画像标签 MAXIMUM_LOSS_TOLERANCE_PCT 或问卷答案）
        max_loss_pct = self._extract_loss_tolerance(risk)

        # 行为信号：优先使用外部传入（风控 Agent），否则从交易数据内建检测
        anomalies = behavior_anomalies
        flags = emotional_flags
        if anomalies is None or flags is None:
            builtin_anomalies, builtin_flags = await self._detect_behavior_signals(
                session, user_id
            )
            anomalies = anomalies if anomalies is not None else builtin_anomalies
            flags = flags if flags is not None else builtin_flags

        anomaly_severities = [a.get("severity", "low") for a in anomalies]

        basic = basic_dimension_score(
            profile.age,
            profile.education_level,
            profile.occupation,
            float(profile.annual_income) if profile.annual_income is not None else None,
            float(asset.investable_asset) if asset else None,
        )
        experience = experience_dimension_score(
            profile.investment_experience_years,
            product_risk_levels,
            int(order_count_30d),
            avg_profit_pct,
        )
        preference = preference_dimension_score(
            questionnaire_score, flags, max_loss_pct
        )
        behavior = behavior_dimension_score(anomaly_severities)

        total = total_score(basic, experience, preference, behavior)

        return {
            "dimensions": {
                "basic": {"score": float(basic), "weight": 0.25},
                "experience": {"score": float(experience), "weight": 0.25},
                "preference": {"score": float(preference), "weight": 0.30},
                "behavior": {"score": float(behavior), "weight": 0.20},
            },
            "total_score": float(total),
            "anomalies": anomalies,
            "emotional_flags": flags,
            "breakdown": {
                "age": age_score(profile.age),
                "education": education_score(profile.education_level),
                "occupation": occupation_score(profile.occupation),
                "income": income_score(
                    float(profile.annual_income)
                    if profile.annual_income is not None
                    else None
                ),
                "asset": asset_score(float(asset.investable_asset) if asset else None),
                "experience_years": experience_years_score(
                    profile.investment_experience_years
                ),
                "product_type": product_type_score(product_risk_levels),
                "trade_frequency": trade_frequency_score(int(order_count_30d)),
                "return": return_score(avg_profit_pct),
                "questionnaire": questionnaire_dimension_score(questionnaire_score),
                "loss_tolerance": loss_tolerance_adjustment(max_loss_pct),
            },
            "has_assessment": risk is not None,
        }

    async def _detect_behavior_signals(
        self, session: AsyncSession, user_id: str
    ) -> tuple[list[dict], list[str]]:
        """内建行为异常（第八条 8.1）与情绪化交易（7.2）检测。"""
        orders = list(
            (await session.execute(select(Order).where(Order.user_id == user_id)))
            .scalars()
            .all()
        )
        trades = list(
            (await session.execute(select(Trade).where(Trade.user_id == user_id)))
            .scalars()
            .all()
        )

        anomalies: list[dict] = []

        # 频繁赎回：30 天内赎回 ≥ 5 次（中）
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
        if account_total > 0 and any(d > account_total * 0.5 for d in by_day.values()):
            anomalies.append(
                {
                    "code": "LARGE_CONCENTRATED_TRADE",
                    "label": "大额集中交易",
                    "severity": "medium",
                }
            )

        # 非正常时段交易：0-6 点 ≥ 3 次（低）
        night_count = sum(1 for t in trades if 0 <= t.executed_at.hour < 6)
        if night_count >= 3:
            anomalies.append(
                {"code": "NIGHT_TRADING", "label": "非正常时段交易", "severity": "low"}
            )

        # 分散转出：≥ 5 个不同账户（高）
        if len({o.account_id for o in orders if o.side == "sell"}) >= 5:
            anomalies.append(
                {"code": "SCATTERED_OUTFLOW", "label": "分散转出", "severity": "high"}
            )

        flags: list[str] = []

        # 频繁改策略：买卖方向切换 > 3 次（7.2）
        side_changes = 0
        prev_side: str | None = None
        for order in sorted(orders, key=lambda o: o.created_at):
            if prev_side is not None and order.side != prev_side:
                side_changes += 1
            prev_side = order.side
        if side_changes > 3:
            flags.append("frequent_strategy_change")

        # 恐慌赎回：单笔赎回 > 存量 50%（7.2）
        sell_amounts = [float(o.amount) for o in orders if o.side == "sell"]
        if (
            sell_amounts
            and sum(sell_amounts) > 0
            and any(a > sum(sell_amounts) * 0.5 for a in sell_amounts)
        ):
            flags.append("panic_redeem")

        # FOMO 加仓：单笔申购 > 平均 3 倍（7.2）
        buy_amounts = [float(o.amount) for o in orders if o.side == "buy"]
        if len(buy_amounts) >= 3 and any(
            a > (sum(buy_amounts) / len(buy_amounts)) * 3 for a in buy_amounts
        ):
            flags.append("fomo_add")

        return anomalies, flags

    @staticmethod
    def _extract_loss_tolerance(risk: CustomerRiskAssessment | None) -> float | None:
        if risk is None or not risk.answers_json:
            return None
        import json

        try:
            answers = json.loads(risk.answers_json)
        except (json.JSONDecodeError, TypeError):
            return None
        # 问卷 q9：A 不能接受任何亏损(1) / B 10%以内(2) / C 10%-30%(3) / D 30%以上(4)
        answer = answers.get("q9", answers.get("9"))
        if answer is None:
            return None
        return {
            "A": 0,
            "a": 0,
            "B": 5,
            "b": 5,
            "C": 20,
            "c": 20,
            "D": 40,
            "d": 40,
        }.get(str(answer), 20)
