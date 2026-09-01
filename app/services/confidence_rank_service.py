from __future__ import annotations

# ---------------------------------------------------------------------------
# 综合置信分重排（Phase 4 F4.3）：5 因子加权排序记忆单元
#
#   final_score = w_semantic*semantic + w_timeliness*timeliness
#               + w_accuracy*accuracy + w_base*base - w_conflict*conflict
# 4 种场景权重：
#   产品推荐 / 风险研判 / 客户画像 / 知识检索
# ---------------------------------------------------------------------------

SCENARIO_WEIGHTS: dict[str, dict[str, float]] = {
    "产品推荐": {
        "semantic": 0.3,
        "timeliness": 0.2,
        "accuracy": 0.25,
        "base": 0.15,
        "conflict": 0.1,
    },
    "风险研判": {
        "semantic": 0.15,
        "timeliness": 0.3,
        "accuracy": 0.25,
        "base": 0.2,
        "conflict": 0.1,
    },
    "客户画像": {
        "semantic": 0.2,
        "timeliness": 0.25,
        "accuracy": 0.2,
        "base": 0.25,
        "conflict": 0.1,
    },
    "知识检索": {
        "semantic": 0.35,
        "timeliness": 0.15,
        "accuracy": 0.2,
        "base": 0.2,
        "conflict": 0.1,
    },
}

DEFAULT_SCENARIO = "知识检索"

# 时效衰减：30 天内线性衰减
TIMELINESS_HALF_LIFE_DAYS = 30.0


def _calc_timeliness(age_days: float) -> float:
    """时效分：越新越高，30 天半衰期衰减。"""
    if age_days <= 0:
        return 1.0
    return max(0.0, 1.0 - age_days / (2 * TIMELINESS_HALF_LIFE_DAYS))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class FinalConfidenceRankTool:
    """综合置信分重排工具（与需求文档 F4.3 一致）。"""

    def rank(self, memory_units: list[dict], scenario: str) -> list[dict]:
        """按场景权重综合排序记忆单元（原地附加 final_score，返回排序后列表）。"""
        weights = SCENARIO_WEIGHTS.get(scenario, SCENARIO_WEIGHTS[DEFAULT_SCENARIO])
        for unit in memory_units:
            semantic = float(unit.get("semantic_similarity", 0.5))
            timeliness = _calc_timeliness(float(unit.get("age_days", 0)))
            accuracy = float(unit.get("historical_accuracy", 0.5))
            base = float(unit.get("confidence_score", 0.5))
            conflict_count = int(unit.get("conflict_count", 0))
            final_score = (
                weights["semantic"] * semantic
                + weights["timeliness"] * timeliness
                + weights["accuracy"] * accuracy
                + weights["base"] * base
                - weights["conflict"] * conflict_count
            )
            unit["final_score"] = _clamp(final_score)
        return sorted(memory_units, key=lambda x: x["final_score"], reverse=True)


class BaseConfidenceCalcTool:
    """基础置信分计算工具（四档来源初始值 + 增益/惩罚 + 时效衰减）。"""

    SOURCE_INITIAL = {
        "风评问卷": 0.9,
        "AI对话提取": 0.6,
        "用户自述": 0.4,
        "默认值": 0.2,
    }
    CONFLICT_PENALTY = 0.1

    def calc(
        self,
        tag: str | None = None,
        *,
        source: str = "默认值",
        evidence_count: int = 1,
        conflict_count: int = 0,
        age_days: float = 0,
        freshness_decay: float | None = None,
    ) -> float:
        """基础置信分 = (初始值 + 证据加成 - 冲突惩罚) * 时效衰减，钳制 [0,1]。

        与需求 F4.3 严格对齐：
        - 来源初始值：风评问卷 0.9 / AI对话提取 0.6 / 用户自述 0.4 / 默认值 0.2
        - 证据累积增益：每次 +0.05，上限 +0.3（需求原文 min(evidence_count*0.05, 0.3)）
        - 冲突惩罚：每次 -0.1
        - 时间衰减：每年 20%（age_days/365*0.2）；也可外部注入 freshness_decay
        """
        initial = self.SOURCE_INITIAL.get(source, self.SOURCE_INITIAL["默认值"])
        evidence_bonus = min(0.3, max(0, evidence_count - 1) * 0.05)
        decay = (
            freshness_decay
            if freshness_decay is not None
            else max(0.0, 1 - age_days / 365 * 0.2)
        )
        score = (
            initial + evidence_bonus - self.CONFLICT_PENALTY * conflict_count
        ) * decay
        return _clamp(score)

    def batch_calc(self, tags: list[dict]) -> list[float]:
        """批量计算置信分。"""
        return [self.calc(**tag) for tag in tags]


class MemoryUnitValidator:
    """记忆单元校验器 — 六维属性校验（F4.3）。

    校验记忆单元的风险等级/产品类型/收入区间枚举、风险评分/置信度数值
    范围、创建/更新时间逻辑；可扩展收入区间、时间逻辑等。
    """

    VALID_RISK_LEVELS = ["保守型", "稳健型", "平衡型", "进取型", "激进型"]
    VALID_PRODUCT_TYPES = [
        "货币基金",
        "债券基金",
        "混合基金",
        "股票基金",
        "银行理财",
        "保险产品",
        "信托产品",
        "结构性存款",
    ]
    VALID_INCOME_RANGES = ["10万以下", "10-50万", "50-100万", "100-500万", "500万以上"]

    def validate(self, unit: dict) -> dict:
        """校验记忆单元合法性。

        :return: {"valid": True/False, "errors": [...]}
        """
        errors: list[str] = []

        # 1. 枚举值合法性校验
        risk_level = unit.get("risk_level")
        if risk_level and risk_level not in self.VALID_RISK_LEVELS:
            errors.append(f"无效风险等级: {risk_level}")
        product_type = unit.get("product_type")
        if product_type and product_type not in self.VALID_PRODUCT_TYPES:
            errors.append(f"无效产品类型: {product_type}")
        income_range = unit.get("income_range")
        if income_range and income_range not in self.VALID_INCOME_RANGES:
            errors.append(f"无效收入区间: {income_range}")

        # 2. 数值范围校验
        risk_score = unit.get("risk_score")
        if risk_score is not None:
            try:
                score = float(risk_score)
                if not (0 <= score <= 100):
                    errors.append(f"风险评分超出范围: {risk_score}")
            except (TypeError, ValueError):
                errors.append(f"风险评分非数值: {risk_score}")
        confidence_score = unit.get("confidence_score")
        if confidence_score is not None:
            try:
                conf = float(confidence_score)
                if not (0.0 <= conf <= 1.0):
                    errors.append(f"置信度超出范围: {confidence_score}")
            except (TypeError, ValueError):
                errors.append(f"置信度非数值: {confidence_score}")

        # 3. 时间逻辑校验
        create_time = unit.get("create_time")
        update_time = unit.get("update_time")
        if create_time and update_time:
            try:
                if update_time < create_time:
                    errors.append("更新时间早于创建时间")
            except TypeError:
                # 非 datetime 对象无法比较（如字符串），跳过时间逻辑校验
                pass

        return {"valid": len(errors) == 0, "errors": errors}

    def validate_batch(self, units: list[dict]) -> list[dict]:
        """批量校验记忆单元，返回各单元校验结果。"""
        return [self.validate(unit) for unit in units]
