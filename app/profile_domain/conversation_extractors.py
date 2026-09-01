from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Protocol

from app.core.settings import Settings
from app.infrastructure.qwen import QwenProvider
from app.profile_domain.tag_governance import (
    ConversationExtractionResult,
    ExtractedProfileTag,
    ExtractionMode,
    ProfileTagCode,
    asset_scale,
)

PROMPT_VERSION = "PROFILE_TAGS_1.1"

_AMOUNT_PATTERN = r"(\d+(?:\.\d+)?)\s*(亿元|亿|万元|万|元)"


def _amount_value(match: re.Match[str]) -> float:
    value = float(match.group(1))
    unit = match.group(2)
    if unit in {"亿", "亿元"}:
        return value * 100_000_000
    if unit in {"万", "万元"}:
        return value * 10_000
    return value


_NUMERIC_TAG_CODES = {
    ProfileTagCode.MONTHLY_INCOME,
    ProfileTagCode.TOTAL_ASSETS,
    ProfileTagCode.HOUSEHOLD_ANNUAL_INCOME,
    ProfileTagCode.TOTAL_LIABILITIES,
    ProfileTagCode.INVESTABLE_ASSETS,
    ProfileTagCode.INVESTMENT_EXPERIENCE_YEARS,
    ProfileTagCode.MAXIMUM_LOSS_TOLERANCE_PCT,
}
_CODE_ALIASES = {
    "职业": "OCCUPATION",
    "月收入": "MONTHLY_INCOME",
    "总资产": "TOTAL_ASSETS",
    "家庭年收入": "HOUSEHOLD_ANNUAL_INCOME",
    "总负债": "TOTAL_LIABILITIES",
    "学历": "EDUCATION_LEVEL",
    "投资经验": "INVESTMENT_EXPERIENCE_YEARS",
    "可投资资产": "INVESTABLE_ASSETS",
}
_TEXT_VALUE_ALIASES = {
    "公务员": "civil_servant",
    "事业单位": "public_institution",
    "事业单位员工": "public_institution",
    "国企": "state_owned_employee",
    "国企员工": "state_owned_employee",
    "上市公司": "listed_company_employee",
    "上市公司员工": "listed_company_employee",
    "工程师": "engineer",
    "医生": "doctor",
    "律师": "lawyer",
    "个体户": "self_employed",
    "个体经营": "self_employed",
    "退休": "retired",
    "无业": "unemployed",
    "高中": "HIGH_SCHOOL_OR_BELOW",
    "高中及以下": "HIGH_SCHOOL_OR_BELOW",
    "中专": "HIGH_SCHOOL_OR_BELOW",
    "大专": "COLLEGE",
    "专科": "COLLEGE",
    "本科": "BACHELOR",
    "硕士": "MASTER_OR_ABOVE",
    "研究生": "MASTER_OR_ABOVE",
    "博士": "MASTER_OR_ABOVE",
    "保本": "CAPITAL_PRESERVATION",
    "保值": "CAPITAL_PRESERVATION",
    "稳健": "STEADY_GROWTH",
    "稳定增值": "STEADY_GROWTH",
    "长期增长": "LONG_TERM_GROWTH",
    "高收益": "HIGH_RETURN",
    "不能承受": "NONE",
    "不能接受": "NONE",
    "小幅亏损": "LOW",
    "少量亏损": "LOW",
    "一定波动": "MEDIUM",
    "中等风险": "MEDIUM",
    "较大波动": "HIGH",
    "高风险": "HIGH",
    "流动性高": "HIGH",
    "流动性一般": "MEDIUM",
    "流动性低": "LOW",
}


def _coerce_amount(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip().replace(",", "").replace("，", "")
    match = re.fullmatch(rf"{_AMOUNT_PATTERN}(?:元)?", text)
    if match:
        return _amount_value(match)
    try:
        number = float(text.rstrip("元%"))
    except ValueError:
        return None
    return number if number >= 0 else None


def _parse_llm_json(content: object) -> dict[str, object]:
    if not isinstance(content, str):
        return {}
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL
        ).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_llm_tags(
    raw_tags: object, conversation_text: str
) -> list[ExtractedProfileTag]:
    if not isinstance(raw_tags, list):
        return []
    result: list[ExtractedProfileTag] = []
    seen: set[ProfileTagCode] = set()
    for raw_tag in raw_tags:
        if not isinstance(raw_tag, dict):
            continue
        raw_code = str(raw_tag.get("tag_code", "")).strip()
        code_name = _CODE_ALIASES.get(raw_code, raw_code.upper().replace("-", "_"))
        try:
            code = ProfileTagCode(code_name)
        except ValueError:
            continue
        if code is ProfileTagCode.ASSET_SCALE or code in seen:
            continue
        quote = raw_tag.get("evidence_quote")
        if not isinstance(quote, str) or not quote or quote not in conversation_text:
            continue
        try:
            confidence = min(
                Decimal(str(raw_tag.get("confidence", "0.60"))), Decimal("0.60")
            )
        except (InvalidOperation, ValueError):
            continue
        value = raw_tag.get("tag_value")
        if code in _NUMERIC_TAG_CODES:
            value = _coerce_amount(value)
            if value is None:
                continue
            if code is ProfileTagCode.INVESTMENT_EXPERIENCE_YEARS and value > 80:
                continue
            if code is ProfileTagCode.MAXIMUM_LOSS_TOLERANCE_PCT and value > 100:
                continue
        elif code is ProfileTagCode.PREFERRED_PRODUCT_TYPES:
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                continue
            value = [str(item) for item in value if str(item).strip()]
            if not value:
                continue
        elif isinstance(value, str):
            value = _TEXT_VALUE_ALIASES.get(value.strip(), value.strip())
        try:
            result.append(
                ExtractedProfileTag(
                    tag_code=code,
                    tag_value=value,
                    confidence=confidence,
                    evidence_quote=quote,
                )
            )
            seen.add(code)
        except (ValueError, TypeError):
            continue
    return result


class ConversationExtractor(Protocol):
    async def extract(self, conversation_text: str) -> ConversationExtractionResult: ...


class RuleDemoConversationExtractor:
    """Deterministic rule-based extractor; works without any LLM API key."""

    async def extract(self, conversation_text: str) -> ConversationExtractionResult:
        tags: dict[ProfileTagCode, ExtractedProfileTag] = {}

        def add(
            code: ProfileTagCode, value: object, quote: str, confidence: str = "0.60"
        ) -> None:
            tags[code] = ExtractedProfileTag(
                tag_code=code,
                tag_value=value,
                confidence=Decimal(confidence),
                evidence_quote=quote[:500],
            )

        occupation_mapping = {
            "公务员": "civil_servant",
            "事业单位员工": "public_institution",
            "事业单位职工": "public_institution",
            "国企员工": "state_owned_employee",
            "国企职工": "state_owned_employee",
            "上市公司员工": "listed_company_employee",
            "上市公司职工": "listed_company_employee",
            "医生": "doctor",
            "律师": "lawyer",
            "工程师": "engineer",
            "中小企业员工": "sme_employee",
            "中小企业职工": "sme_employee",
            "个体经营": "self_employed",
            "个体户": "self_employed",
            "退休": "retired",
            "无业": "unemployed",
        }
        occupation_terms = "|".join(map(re.escape, occupation_mapping))
        if occupation_match := re.search(
            rf"(?:职业(?:是|为)?|我是|目前是|从事)\s*({occupation_terms})",
            conversation_text,
        ):
            occupation = occupation_match.group(1)
            add(
                ProfileTagCode.OCCUPATION,
                occupation_mapping[occupation],
                occupation_match.group(0),
            )

        education_mapping = {
            "高中及以下": "HIGH_SCHOOL_OR_BELOW",
            "高中": "HIGH_SCHOOL_OR_BELOW",
            "中专": "HIGH_SCHOOL_OR_BELOW",
            "大专": "COLLEGE",
            "专科": "COLLEGE",
            "本科": "BACHELOR",
            "硕士研究生": "MASTER_OR_ABOVE",
            "硕士": "MASTER_OR_ABOVE",
            "研究生": "MASTER_OR_ABOVE",
            "博士": "MASTER_OR_ABOVE",
        }
        education_terms = "|".join(map(re.escape, education_mapping))
        education_match = re.search(
            rf"(?:最高)?学历(?:是|为)?\s*({education_terms})|我是\s*({education_terms})(?:学历|毕业)",
            conversation_text,
        )
        if education_match:
            education = education_match.group(1) or education_match.group(2)
            add(
                ProfileTagCode.EDUCATION_LEVEL,
                education_mapping[education],
                education_match.group(0),
            )

        amount_fields = (
            (ProfileTagCode.MONTHLY_INCOME, r"月收入|每月收入|月薪"),
            (ProfileTagCode.TOTAL_ASSETS, r"总资产|资产总额"),
            (
                ProfileTagCode.HOUSEHOLD_ANNUAL_INCOME,
                r"家庭年收入|家庭年度收入|全家年收入",
            ),
            (
                ProfileTagCode.TOTAL_LIABILITIES,
                r"总负债|负债总额|债务总额|(?:目前)?负债",
            ),
        )
        for code, label_pattern in amount_fields:
            if amount_match := re.search(
                rf"(?:{label_pattern})(?:是|为|约|大约|有|共计|大概|达到|：|:)?\s*{_AMOUNT_PATTERN}",
                conversation_text,
            ):
                add(code, _amount_value(amount_match), amount_match.group(0))

        goal_patterns = [
            (r"保本|保值|本金安全", "CAPITAL_PRESERVATION"),
            (r"稳健|稳定增值", "STEADY_GROWTH"),
            (r"长期增长|长期投资|养老", "LONG_TERM_GROWTH"),
            (r"高收益|高回报|激进", "HIGH_RETURN"),
        ]
        for pattern, value in goal_patterns:
            if match := re.search(pattern, conversation_text):
                add(ProfileTagCode.INVESTMENT_GOAL, value, match.group(0))
                break

        loss_patterns = [
            (r"不能(?:接受|承受)?(?:任何)?亏损|本金不能损失", "NONE"),
            (r"小幅亏损|少量亏损|低风险", "LOW"),
            (r"一定(?:的)?(?:亏损|波动)|中等风险", "MEDIUM"),
            (r"较大(?:亏损|波动)|高风险", "HIGH"),
        ]
        for pattern, value in loss_patterns:
            if match := re.search(pattern, conversation_text):
                add(ProfileTagCode.LOSS_TOLERANCE, value, match.group(0))
                break
        if match := re.search(
            r"(?:亏损|回撤|损失)[^。；，,]{0,12}?(\d+(?:\.\d+)?)\s*%|"
            r"(\d+(?:\.\d+)?)\s*%[^。；，,]{0,8}?(?:亏损|回撤|损失)",
            conversation_text,
        ):
            add(
                ProfileTagCode.MAXIMUM_LOSS_TOLERANCE_PCT,
                float(match.group(1) or match.group(2)),
                match.group(0),
            )

        liquidity_patterns = [
            (r"随时(?:要|会)?用钱|流动性(?:要求|需求)?高|短期要用", "HIGH"),
            (r"流动性(?:要求|需求)?一般|保留部分现金", "MEDIUM"),
            (r"长期不用|短期(?:内)?不用|流动性(?:要求|需求)?低", "LOW"),
        ]
        for pattern, value in liquidity_patterns:
            if match := re.search(pattern, conversation_text):
                add(ProfileTagCode.LIQUIDITY_NEED, value, match.group(0))
                break

        experience_match = re.search(
            r"(?:投资|理财)(?:经验)?(?:是|为|有|大约|约|：|:)?\s*(\d+(?:\.\d+)?)\s*年|"
            r"(\d+(?:\.\d+)?)\s*年(?:的)?(?:投资|理财)经验",
            conversation_text,
        )
        if experience_match:
            value = float(experience_match.group(1) or experience_match.group(2))
            add(
                ProfileTagCode.INVESTMENT_EXPERIENCE_YEARS,
                value,
                experience_match.group(0),
            )

        asset_match = re.search(
            r"(?:可投资(?:资产|资金)?|用于投资(?:的)?(?:资产|资金)?)[^。；，,]{0,10}?"
            + _AMOUNT_PATTERN,
            conversation_text,
        )
        if asset_match:
            amount = _amount_value(asset_match)
            add(ProfileTagCode.INVESTABLE_ASSETS, amount, asset_match.group(0))
            add(ProfileTagCode.ASSET_SCALE, asset_scale(amount), asset_match.group(0))

        product_mapping = {
            "基金": "FUND",
            "股票": "EQUITY",
            "债券": "BOND",
            "银行理财": "BANK_WEALTH",
            "结构化": "STRUCTURED_PRODUCT",
        }
        matched_products = [
            (keyword, label)
            for keyword, label in product_mapping.items()
            if keyword in conversation_text
        ]
        products = [label for _, label in matched_products]
        if products:
            starts = [
                conversation_text.index(keyword) for keyword, _ in matched_products
            ]
            ends = [
                conversation_text.index(keyword) + len(keyword)
                for keyword, _ in matched_products
            ]
            quote = conversation_text[min(starts) : max(ends)]
            add(ProfileTagCode.PREFERRED_PRODUCT_TYPES, products, quote)

        return ConversationExtractionResult(
            extraction_mode=ExtractionMode.RULE_DEMO,
            model_name="rule-demo-profile-tags",
            prompt_version=PROMPT_VERSION,
            summary=f"提取到 {len(tags)} 个可验证标签",
            tags=list(tags.values()),
        )


_SYSTEM_PROMPT = """You extract explicit customer facts from Chinese financial advisor conversations.
Return one JSON object with keys summary and tags. Each tag has tag_code, tag_value, confidence, evidence_quote.
Allowed tag_code values:
OCCUPATION, MONTHLY_INCOME, TOTAL_ASSETS, HOUSEHOLD_ANNUAL_INCOME, TOTAL_LIABILITIES,
EDUCATION_LEVEL, INVESTMENT_GOAL, LOSS_TOLERANCE, MAXIMUM_LOSS_TOLERANCE_PCT,
LIQUIDITY_NEED, PREFERRED_PRODUCT_TYPES, INVESTMENT_EXPERIENCE_YEARS, INVESTABLE_ASSETS.

Normalization rules:
- Money values must be non-negative numbers in CNY yuan. Convert 万 to 10000 and 亿 to 100000000.
- OCCUPATION must be one of: civil_servant, public_institution, state_owned_employee,
  listed_company_employee, doctor, lawyer, engineer, sme_employee, self_employed, retired, unemployed.
- EDUCATION_LEVEL must be one of: HIGH_SCHOOL_OR_BELOW, COLLEGE, BACHELOR, MASTER_OR_ABOVE.
- INVESTMENT_GOAL must be one of: CAPITAL_PRESERVATION, STEADY_GROWTH, LONG_TERM_GROWTH, HIGH_RETURN.
- LOSS_TOLERANCE must be one of: NONE, LOW, MEDIUM, HIGH.
- LIQUIDITY_NEED must be one of: LOW, MEDIUM, HIGH.
- PREFERRED_PRODUCT_TYPES must be a non-empty list of normalized product strings.

Only extract facts explicitly stated by the customer. evidence_quote must be an exact substring of the input.
Never guess missing values, never derive ASSET_SCALE, and never output C1-C5 or a risk score.
Use confidence at most 0.60. Omit uncertain tags."""


class QwenConversationExtractor:
    """LLM extractor backed by the host app's QwenProvider (DashScope)."""

    def __init__(self, provider: QwenProvider, model: str | None = None) -> None:
        self._provider = provider
        self._model = model or provider.settings.qwen_chat_model

    async def extract(self, conversation_text: str) -> ConversationExtractionResult:
        if not self._provider.available:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured")
        raw = await self._provider.chat(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": conversation_text},
            ],
            temperature=0.0,
            max_tokens=1024,
        )
        parsed = _parse_llm_json(raw)
        validated_tags = _normalize_llm_tags(parsed.get("tags"), conversation_text)
        investable_assets = next(
            (
                tag
                for tag in validated_tags
                if tag.tag_code is ProfileTagCode.INVESTABLE_ASSETS
            ),
            None,
        )
        if investable_assets is not None and not any(
            tag.tag_code is ProfileTagCode.ASSET_SCALE for tag in validated_tags
        ):
            validated_tags.append(
                ExtractedProfileTag(
                    tag_code=ProfileTagCode.ASSET_SCALE,
                    tag_value=asset_scale(float(investable_assets.tag_value)),
                    confidence=investable_assets.confidence,
                    evidence_quote=investable_assets.evidence_quote,
                )
            )
        return ConversationExtractionResult(
            extraction_mode=ExtractionMode.OPENAI_COMPATIBLE,
            model_name=self._model,
            prompt_version=PROMPT_VERSION,
            summary=str(
                parsed.get("summary") or f"提取到 {len(validated_tags)} 个可验证标签"
            ),
            tags=validated_tags,
        )


def create_conversation_extractor(
    settings: Settings,
    provider: QwenProvider | None = None,
) -> ConversationExtractor:
    """LLM extractor when the API key exists, otherwise the deterministic rules."""
    llm = provider or QwenProvider(settings)
    if llm.available:
        return QwenConversationExtractor(llm)
    return RuleDemoConversationExtractor()
