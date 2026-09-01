from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import CustomerProfile, CustomerRiskAssessment
from app.repositories.profile import SqlAlchemyRiskAssessmentRepository

# ---------------------------------------------------------------------------
# F2.2 风险评估：16 题问卷（Mock），覆盖收入 / 投资经验 / 风险承受力 /
# 投资目标 / 流动性等维度。每题 4 个选项（A/B/C/D），分值 1-4：
#   A=1 最保守 → D=4 最激进
# 总分 raw ∈ [16, 64]，归一化到 [0, 100]。
# ---------------------------------------------------------------------------

QUESTIONNAIRE_VERSION = "RISK_QUESTIONNAIRE_V1"

RISK_QUESTIONNAIRE: list[dict] = [
    # ---- 收入维度（4 题）----
    {
        "q": 1,
        "dimension": "income",
        "question": "您的家庭年收入大约处于哪个区间？",
        "options": [
            {"key": "A", "text": "10万元以下", "score": 1},
            {"key": "B", "text": "10万-30万元", "score": 2},
            {"key": "C", "text": "30万-100万元", "score": 3},
            {"key": "D", "text": "100万元以上", "score": 4},
        ],
    },
    {
        "q": 2,
        "dimension": "income",
        "question": "您的月收入中，可自由支配（非生活必需）的比例约为？",
        "options": [
            {"key": "A", "text": "不足10%", "score": 1},
            {"key": "B", "text": "10%-30%", "score": 2},
            {"key": "C", "text": "30%-50%", "score": 3},
            {"key": "D", "text": "超过50%", "score": 4},
        ],
    },
    {
        "q": 3,
        "dimension": "income",
        "question": "您目前可用于投资的总资产规模为？",
        "options": [
            {"key": "A", "text": "10万元以下", "score": 1},
            {"key": "B", "text": "10万-50万元", "score": 2},
            {"key": "C", "text": "50万-200万元", "score": 3},
            {"key": "D", "text": "200万元以上", "score": 4},
        ],
    },
    {
        "q": 4,
        "dimension": "income",
        "question": "您的收入来源稳定性如何？",
        "options": [
            {"key": "A", "text": "收入波动较大", "score": 1},
            {"key": "B", "text": "收入基本稳定", "score": 2},
            {"key": "C", "text": "收入较为充足", "score": 3},
            {"key": "D", "text": "收入丰厚且有结余", "score": 4},
        ],
    },
    # ---- 投资经验维度（4 题）----
    {
        "q": 5,
        "dimension": "experience",
        "question": "您从事投资理财的经验年限是？",
        "options": [
            {"key": "A", "text": "1年以下", "score": 1},
            {"key": "B", "text": "1-3年", "score": 2},
            {"key": "C", "text": "3-8年", "score": 3},
            {"key": "D", "text": "8年以上", "score": 4},
        ],
    },
    {
        "q": 6,
        "dimension": "experience",
        "question": "您最熟悉的投资产品类型是？",
        "options": [
            {"key": "A", "text": "存款、货币基金", "score": 1},
            {"key": "B", "text": "债券、银行理财", "score": 2},
            {"key": "C", "text": "混合型基金", "score": 3},
            {"key": "D", "text": "股票、期货等", "score": 4},
        ],
    },
    {
        "q": 7,
        "dimension": "experience",
        "question": "您对金融市场与投资产品的了解程度是？",
        "options": [
            {"key": "A", "text": "基本不了解", "score": 1},
            {"key": "B", "text": "了解常见产品", "score": 2},
            {"key": "C", "text": "较为了解并持续学习", "score": 3},
            {"key": "D", "text": "非常熟悉各类工具", "score": 4},
        ],
    },
    {
        "q": 8,
        "dimension": "experience",
        "question": "您进行投资决策的频率是？",
        "options": [
            {"key": "A", "text": "很少主动操作", "score": 1},
            {"key": "B", "text": "每年几次", "score": 2},
            {"key": "C", "text": "每月几次", "score": 3},
            {"key": "D", "text": "每周频繁操作", "score": 4},
        ],
    },
    # ---- 风险承受力维度（4 题）----
    {
        "q": 9,
        "dimension": "risk_tolerance",
        "question": "您能承受的最大投资亏损幅度是？",
        "options": [
            {"key": "A", "text": "不能接受任何亏损", "score": 1},
            {"key": "B", "text": "10%以内", "score": 2},
            {"key": "C", "text": "10%-30%", "score": 3},
            {"key": "D", "text": "30%以上", "score": 4},
        ],
    },
    {
        "q": 10,
        "dimension": "risk_tolerance",
        "question": "当投资出现短期波动时，您的心理状态通常是？",
        "options": [
            {"key": "A", "text": "难以接受，立即赎回", "score": 1},
            {"key": "B", "text": "较为焦虑，考虑调整", "score": 2},
            {"key": "C", "text": "可以接受，继续持有", "score": 3},
            {"key": "D", "text": "视为机会，可能加仓", "score": 4},
        ],
    },
    {
        "q": 11,
        "dimension": "risk_tolerance",
        "question": "您是否准备了专门的应急资金（可应对6个月以上开支）？",
        "options": [
            {"key": "A", "text": "没有准备", "score": 1},
            {"key": "B", "text": "准备不足", "score": 2},
            {"key": "C", "text": "已有3-6个月应急金", "score": 3},
            {"key": "D", "text": "应急资金充足", "score": 4},
        ],
    },
    {
        "q": 12,
        "dimension": "risk_tolerance",
        "question": "面对「高收益高波动」与「低收益低波动」，您更倾向？",
        "options": [
            {"key": "A", "text": "绝对优先保本", "score": 1},
            {"key": "B", "text": "稳健为主、小幅进取", "score": 2},
            {"key": "C", "text": "平衡收益与风险", "score": 3},
            {"key": "D", "text": "追求高收益、容忍高波动", "score": 4},
        ],
    },
    # ---- 投资目标维度（3 题）----
    {
        "q": 13,
        "dimension": "goal",
        "question": "您投资理财的首要目标是？",
        "options": [
            {"key": "A", "text": "本金安全与保值", "score": 1},
            {"key": "B", "text": "稳健增值", "score": 2},
            {"key": "C", "text": "资产长期增长", "score": 3},
            {"key": "D", "text": "追求高回报", "score": 4},
        ],
    },
    {
        "q": 14,
        "dimension": "goal",
        "question": "您对投资年化收益率的期望是？",
        "options": [
            {"key": "A", "text": "跑赢通胀即可", "score": 1},
            {"key": "B", "text": "3%-6%", "score": 2},
            {"key": "C", "text": "6%-12%", "score": 3},
            {"key": "D", "text": "12%以上", "score": 4},
        ],
    },
    {
        "q": 15,
        "dimension": "goal",
        "question": "您计划本次投资的资金可投资期限是？",
        "options": [
            {"key": "A", "text": "1年以内", "score": 1},
            {"key": "B", "text": "1-3年", "score": 2},
            {"key": "C", "text": "3-5年", "score": 3},
            {"key": "D", "text": "5年以上", "score": 4},
        ],
    },
    # ---- 流动性维度（1 题）----
    {
        "q": 16,
        "dimension": "liquidity",
        "question": "您对投资资金的流动性（随时取用）需求是？",
        "options": [
            {"key": "A", "text": "随时可能需要使用", "score": 1},
            {"key": "B", "text": "部分资金短期要用", "score": 2},
            {"key": "C", "text": "中短期内无需使用", "score": 3},
            {"key": "D", "text": "长期闲置不用", "score": 4},
        ],
    },
]

DIMENSIONS = ["income", "experience", "risk_tolerance", "goal", "liquidity"]

# 风险等级判定（投资者风险画像研判规则 第十一条）
#   C1: 0-25分, C2: 26-40分, C3: 41-60分, C4: 61-80分, C5: 81-100分
RISK_LEVEL_RULES: list[tuple[int, str, str]] = [
    (0, "C1", "保守型"),
    (26, "C2", "稳健型"),
    (41, "C3", "平衡型"),
    (61, "C4", "进取型"),
    (81, "C5", "激进型"),
]

# 适当性匹配（C 客户 → 允许的 R 产品等级上限数值）
RISK_LEVEL_ORDER = {"C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5}
PRODUCT_RISK_ORDER = {"R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5}


class RiskQuestionnaireService:
    """16 题问卷定义、计分与等级判定（F2.2）。"""

    def get_questionnaire(self) -> dict:
        return {
            "questionnaire_id": "RISK-Q-2024-001",
            "version": QUESTIONNAIRE_VERSION,
            "total_questions": len(RISK_QUESTIONNAIRE),
            "dimensions": DIMENSIONS,
            "items": RISK_QUESTIONNAIRE,
        }

    def score(self, answers: list[dict]) -> tuple[int, str, str]:
        """answers: [{q, a}] → (score 0-100, level, level_name)"""
        q_map = {item["q"]: item for item in RISK_QUESTIONNAIRE}
        raw = 0
        answered = 0
        for answer in answers:
            item = q_map.get(answer["q"])
            if item is None:
                raise ValueError(f"unknown question q={answer['q']}")
            option = next(
                (o for o in item["options"] if o["key"] == answer["a"]),
                None,
            )
            if option is None:
                raise ValueError(
                    f"invalid option {answer['a']} for question {answer['q']} (expect A/B/C/D)"
                )
            raw += option["score"]
            answered += 1
        # raw ∈ [16, 64] → normalize to [0, 100]
        normalized = round(
            (raw - len(RISK_QUESTIONNAIRE)) * 100 / (len(RISK_QUESTIONNAIRE) * 3)
        )
        normalized = max(0, min(100, normalized))
        for threshold, level, level_name in reversed(RISK_LEVEL_RULES):
            if normalized >= threshold:
                return normalized, level, level_name
        return 0, "C1", "保守型"

    async def assess(
        self,
        session: AsyncSession,
        user_id: str,
        answers: list[dict],
    ) -> tuple[CustomerRiskAssessment, CustomerProfile]:
        score, level, _ = self.score(answers)
        repository = SqlAlchemyRiskAssessmentRepository()
        await repository.supersede_active(session, user_id)
        item = CustomerRiskAssessment(
            id=str(uuid4()),
            user_id=user_id,
            risk_level=level,
            score=score,
            answers_json=json.dumps(
                {f"q{a['q']}": a["a"] for a in answers}, ensure_ascii=False
            ),
            status="active",
            source_type="questionnaire",
            assessed_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=365),
        )
        session.add(item)
        # 同步更新客户画像（fin_customer_profile 的 risk_level / risk_score）
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user_id)
            )
        ).scalar_one_or_none()
        if profile is None:
            profile = CustomerProfile(id=str(uuid4()), user_id=user_id)
            session.add(profile)
        profile.risk_level = level
        profile.risk_score = score
        await session.flush()
        return item, profile
