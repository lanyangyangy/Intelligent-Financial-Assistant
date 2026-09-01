"""输出合规护栏（借鉴参考项目 wealth-advisor-full app/advisor/guardrails.py）。

拦截 LLM 推荐理由/回复中的违规承诺（"保证收益/稳赚不赔/零风险"等），
并校验回复引用的产品属于推荐白名单。违规时返回 SAFE_FALLBACK 合规话术。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 违规承诺措辞（与否定词组合检测：如"不保证收益"合法、"保证收益"违规）
PROHIBITED_CLAIMS = (
    "保证收益",
    "稳赚不赔",
    "保本保收益",
    "零风险",
    "绝对收益",
    "肯定赚",
)
NEGATIONS = ("不保证", "不承诺", "并非", "不能", "不代表", "不存在", "不是", "无法保证")
CLAUSE_BOUNDARY_PATTERN = re.compile(
    r"[。！？!?；;，,\n]|然而|可是|但是|不过|仍然|但|却|仍"
)
SAFE_FALLBACK = (
    "本次仅展示已通过适当性校验的候选产品及可核验证据，不保证收益；"
    "产品净值可能波动，请由持牌投顾结合客户实际情况复核。"
)


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    safe_reply: str
    reason: str | None = None


def _contains_positive_claim(reply: str) -> bool:
    for clause in CLAUSE_BOUNDARY_PATTERN.split(reply):
        for claim in PROHIBITED_CLAIMS:
            start = 0
            while True:
                claim_index = clause.find(claim, start)
                if claim_index < 0:
                    break
                claim_end = claim_index + len(claim)
                negated = False
                for negation in NEGATIONS:
                    negation_index = clause.rfind(negation, 0, claim_end)
                    if negation_index < 0:
                        continue
                    negation_end = negation_index + len(negation)
                    directly_precedes = (
                        negation_end <= claim_index
                        and not clause[negation_end:claim_index].strip()
                    )
                    overlaps_claim = (
                        negation_index <= claim_index < negation_end <= claim_end
                    )
                    if directly_precedes or overlaps_claim:
                        negated = True
                        break
                if not negated:
                    return True
                start = claim_end
    return False


def guard_reply(
    reply: str,
    allowed_products: tuple[tuple[str, str], ...] | None = None,
    *,
    allowed_product_names: tuple[str, ...] | None = None,
) -> GuardResult:
    """校验投顾回复合规性。

    - allowed_products: ((code, name), ...) 推荐白名单（本项目无产品码，code 可空）
    - allowed_product_names: 产品名白名单（本项目用产品名匹配）
    """
    if allowed_products is None:
        allowed_products = tuple(("", name) for name in (allowed_product_names or ()))

    allowed_names = {name for _, name in allowed_products if name}
    if _contains_positive_claim(reply):
        return GuardResult(False, SAFE_FALLBACK, "prohibited_claim")

    # 引用白名单产品：回复中至少提到一个推荐产品名
    references_candidate = bool(
        allowed_names and any(name and name in reply for name in allowed_names)
    )
    if allowed_names and not references_candidate:
        return GuardResult(False, SAFE_FALLBACK, "missing_candidate_reference")
    return GuardResult(True, reply)
