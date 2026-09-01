"""Deterministic intent and parameter extraction for eight business operations.

移植自 Financial System-业务操作agent（app/service/operator_parser.py）。
与目标项目差异：customer_id 用字符串（当前项目主键为 UUID），
编号\d+ 模式保留但转为字符串，兼容当前 _resolve_customer 的 User.id 比较。
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from app.schemas.operator import ParsedOperation


class ParserError(Exception):
    """确定性解析失败（参数缺失/句式不支持），触发 LLM 兜底或追问。"""


def _money(value: str, unit: str | None) -> str:
    """Return normalized amount as a decimal string."""
    d = Decimal(value)
    if unit and "万" in unit:
        d *= Decimal("10000")
    return str(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# 客户 ID 识别：优先按 ID 提取参数（而非姓名），支持多种写法：
#   编号1001 / 编号 1001 / 客户ID retail_investor_demo / 客户ID:xxx /
#   ID:xxx / ID：xxx / #xxx / 账号 xxx / 客户号 xxx
_CUSTOMER_ID_RES = (
    re.compile(
        r"^(?:客户)?\s*(?:ID|编号|账号|客户号)\s*[:：#]?\s*([A-Za-z0-9_\-]+)", re.I
    ),
    re.compile(r"^#\s*([A-Za-z0-9_\-]+)"),
)


def _customer_reference(value: str, prefix: str = "") -> dict[str, str]:
    """把客户引用解析为 customer_id（优先）或 customer_name（兜底）。

    以客户 ID 提取参数为本：命中 ID 格式（编号/客户ID/# 等）→ customer_id；
    无法识别 ID 时才回退 customer_name（姓名存在重名歧义，由
    _resolve_customer_checked 兜底选择）。
    """
    normalized = value.strip()
    for pattern in _CUSTOMER_ID_RES:
        match = pattern.match(normalized)
        if match:
            return {f"{prefix}customer_id": match.group(1)}
    # 纯编号（兼容旧句式"编号123"）
    identifier = re.fullmatch(r"编号\s*([A-Za-z0-9_\-]+)", normalized)
    if identifier:
        return {f"{prefix}customer_id": identifier.group(1)}
    return {f"{prefix}customer_name": normalized}


def _strip_customer_prefix(name: str) -> str:
    """Remove leading 客户/替/给/帮/为/将/把 from a customer reference."""
    return re.sub(r"^(?:客户|替|给|帮|为|将|把)\s*", "", name.strip())


def parse_operation(message: str) -> ParsedOperation:
    text = message.strip()

    # ── Transfer ────────────────────────────────────────────────────
    if any(word in text for word in ("转账", "转到", "转给", "向客户")) and not any(
        w in text for w in ("可疑", "上报", "可疑上报", "标记")
    ):
        # Pattern: source amount 转到/转给 target
        mid_amount = re.search(
            r"(?:把|从|替|帮)?\s*(?:客户)?\s*(.+?)(?:的)?\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*(万元|万|元)\s*"
            r"(?:转到|转给|向|转账给)\s*(?:客户)?\s*(.+?)(?:账户|账)?$",
            text,
        )
        # Pattern: source 转到 target amount
        tail_amount = re.search(
            r"(?:把|从|替|帮)?\s*(?:客户)?\s*(.+?)(?:的)?\s*"
            r"(?:转到|转给|转给|向|转账给)\s*(?:客户)?\s*(.+?)(?:账户|账)?\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*(万元|万|元)",
            text,
        )
        # Pattern: 从 source 向 target 转账 amount
        inline = re.search(
            r"从\s*(.+?)\s*向\s*"
            r"(.+?)\s*(?:转账)\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*(万元|万|元)",
            text,
        )
        if inline:
            source, target, value, unit = inline.groups()
        elif mid_amount:
            source, value, unit, target = mid_amount.groups()
        elif tail_amount:
            source, target, value, unit = tail_amount.groups()
        else:
            raise ParserError("无法提取转账参数")
        return ParsedOperation(
            action="transfer",
            params={
                **_customer_reference(_strip_customer_prefix(source), "source_"),
                "amount": _money(value, unit),
                **_customer_reference(_strip_customer_prefix(target), "target_"),
            },
        )

    # ── Information Update ──────────────────────────────────────────
    if any(word in text for word in ("手机号", "电话", "信息更新", "更新客户", "改成")):
        phone = re.search(r"1[3-9]\d{9}", text)
        if not phone:
            raise ParserError("无法提取手机号")
        # Extract customer: look for "客户XXX" or text before phone-related keywords
        customer = re.search(
            r"(?:把|替|给|帮|为|客户)\s*((?:编号\s*\d+|.+?))"
            r"(?=\s*(?:的?(?:手机号|电话|手机|信息)|改成|改成手机号))",
            text,
        )
        if not customer:
            # Try: XXX 的? 手机号 / 电话
            customer = re.search(
                r"(.+?)(?=\s*(?:的?(?:手机号|电话|手机|信息|改成)))",
                text,
            )
        if not customer:
            raise ParserError("无法提取客户")
        return ParsedOperation(
            action="information_update",
            params={
                **_customer_reference(_strip_customer_prefix(customer.group(1))),
                "phone": phone.group(0),
            },
        )

    # ── Reassessment ────────────────────────────────────────────────
    if any(
        word in text
        for word in (
            "风险评估",
            "重做风评",
            "重新风评",
            "风评重做",
            "风评完成",
            "解冻",
            "解除冻结",
            "恢复申购",
            "风评通过",
            "完成了风险评估",
            "完成风险评估",
            "风险评估完成",
            "已经完成风评",
            "已完成风评",
        )
    ):
        is_unfreeze = any(
            word in text
            for word in (
                "风评完成",
                "解冻",
                "解除冻结",
                "恢复申购",
                "风评通过",
                "完成了风险评估",
                "完成风险评估",
                "风险评估完成",
                "已完成风险评估",
                "已完成风评",
                "已经完成风评",
                "完成风评",
                "完成了风评",
                "已经完成",
                "已完成",
                "已经完成风险评估",
                "完成了",
            )
        )
        customer = re.search(
            r"(?:客户|替|给|帮|为)\s*"
            r"((?:编号\s*[A-Za-z0-9_\-]+|客户ID\s*[A-Za-z0-9_\-]+|"
            r"ID\s*[A-Za-z0-9_\-]+|#[A-Za-z0-9_\-]+|[^\s，。]+?))"
            r"(?=\s*(?:重新|需要|重做|风险评估|风评重做|风评|风险|风评完成|解冻|解除冻结|恢复申购|风评通过|完成了|已完成|完成|已经))",
            text,
        )
        if not customer:
            raise ParserError("无法提取客户")
        cust_text = _strip_customer_prefix(customer.group(1))
        params = _customer_reference(cust_text)
        if is_unfreeze:
            params["unfreeze"] = True
        return ParsedOperation(action="reassessment", params=params)

    # ── Suspicious Report ───────────────────────────────────────────
    if any(word in text for word in ("可疑交易", "标记为可疑", "可疑上报", "可疑")):
        customer = re.search(
            r"(?:上报|客户|替|给|帮|将|把)\s*((?:编号\s*\d+|.+?))"
            r"(?=\s*(?:的?(?:可疑|标记|上报)))",
            text,
        )
        if not customer:
            # Pattern: "可疑交易 客户X"
            customer = re.search(
                r"(?:可疑交易|可疑上报|标记为可疑)\s*(?:客户)?\s*((?:编号\s*\d+|.+?))"
                r"(?=\s*(?:涉及|原因|，|。|$))",
                text,
            )
        if not customer:
            raise ParserError("无法提取客户")
        cust_text = _strip_customer_prefix(customer.group(1))
        reason_match = re.search(
            r"[，,]?\s*(?:原因|理由|说明)(?:是|为|：|:)\s*([^\s，。；;]+.*?)(?:$|。|；|;)",
            text,
        )
        reason = reason_match.group(1) if reason_match else None
        tids_match = re.findall(r"(?:交易|流水)\s*(?:ID|号|编号)?\s*(\d+)", text)
        tids = [int(t) for t in tids_match] if tids_match else None
        params = _customer_reference(cust_text)
        if reason:
            params["reason"] = reason
            params["content"] = reason
        if tids:
            params["transaction_ids"] = tids
        return ParsedOperation(action="suspicious_report", params=params)

    # ── Work Order Create ───────────────────────────────────────────
    if "工单" in text:
        customer = re.search(
            r"(?:客户|替|给|帮|为)\s*"
            r"((?:编号\s*[A-Za-z0-9_\-]+|客户ID\s*[A-Za-z0-9_\-]+|"
            r"ID\s*[A-Za-z0-9_\-]+|#[A-Za-z0-9_\-]+|[^\s，。]+?))"
            r"(?=\s*(?:创建|建一个|建立一个|工单|发起))",
            text,
        )
        if not customer:
            raise ParserError("无法提取客户")
        cust_text = _strip_customer_prefix(customer.group(1))
        # Content: after "内容是" / "问题" / "原因", or rest of text
        content_match = re.search(
            r"(?:内容是|内容|问题是|问题|原因是|原因)(?:是|为|：|:)?\s*(.+?)(?:$|。|；)",
            text,
        )
        content = content_match.group(1) if content_match else ""
        if not content.strip():
            fallback = re.sub(
                r"(?:为客户?\s*(?:编号\s*\d+|[^\s，。]+?)\s*"
                r"(?:创建|建一个|建立一个|发起)?工单，?\s*)",
                "",
                text,
            )
            content = fallback.strip()
        if not content.strip():
            raise ParserError("工单内容不能为空，请说明工单内容")
        return ParsedOperation(
            action="work_order_create",
            params={**_customer_reference(cust_text), "content": content.strip()},
        )

    # ── Purchase ────────────────────────────────────────────────────
    if any(word in text for word in ("申购", "购买", "买入")):
        match = re.search(
            r"(?:帮|替|给|为)?\s*(?:客户)?\s*"
            r"(?:编号\s*(\d+)|(.+?))\s*"
            r"(?:(?:申购|购买|买入)\s*)"
            r"(?:人民币|人民币金额)?\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*(万元|万|元)(?:的)?\s*"
            r"(.+)$",
            text,
        )
        if not match:
            raise ParserError("无法提取申购参数")
        if match.group(1) is not None:
            cust_ref = {"customer_id": str(match.group(1))}
        else:
            # 以客户 ID 提取参数优先：识别"客户ID/ID/#/编号"→ customer_id，
            # 仅当无法识别 ID 时才用姓名兜底（_customer_reference）
            cust_ref = _customer_reference(
                _strip_customer_prefix(match.group(2)).strip()
            )
        return ParsedOperation(
            action="purchase",
            params={
                **cust_ref,
                "amount": _money(match.group(3), match.group(4)),
                "product_name": match.group(5).strip(),
            },
        )

    # ── Redeem ──────────────────────────────────────────────────────
    if any(word in text for word in ("赎回", "卖出")):
        # Full redeem: use literal delimiter "持有的" or "持有" to split
        is_full = bool(re.search(r"(?:全部份额|全部|所有)\s*$", text))
        cust_name = None
        product_name = None
        full_shares: float | None = None
        full_redeem_all: bool = False

        # Try "赎回客户 X 持有的 Y 全部份额" pattern
        m = re.search(
            r"(?:赎回|卖出)\s*(?:客户)?\s*(.+?)\s*"
            r"(?:持有的|持有|所持)\s*(.+?)\s*"
            r"(?:全部份额|全部|所有)?$",
            text,
        )
        if m:
            cust_name = m.group(1).strip()
            product_name = m.group(2).strip()
            # If the original had "全部份额|全部|所有", it's a full redeem
            if is_full:
                full_shares = None
                full_redeem_all = True
            # Otherwise check if it ends with "N份"
            else:
                share_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*份$", text)
                if share_match:
                    full_shares = float(share_match.group(1))
                else:
                    full_shares = None
                    full_redeem_all = True

        # Simpler pattern: "客户X 卖出/赎回 Y N份"
        if cust_name is None:
            m = re.search(
                r"(?:客户)?\s*([^\s，。]+?)\s*"
                r"(?:卖出|赎回)\s*(.+?)\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*份$",
                text,
            )
            if m:
                cust_name = m.group(1).strip()
                product_name = m.group(2).strip()
                full_shares = float(m.group(3))
                full_redeem_all = False
            else:
                raise ParserError("无法提取赎回参数")

        cust_name = _strip_customer_prefix(cust_name)
        if not product_name:
            raise ParserError("无法提取赎回参数")

        params: dict = {
            **_customer_reference(cust_name),
            "product_name": product_name.strip(),
            "shares": full_shares,
        }
        if full_redeem_all:
            params["redeem_all"] = True
        return ParsedOperation(action="redeem", params=params)

    # ── Product Query ───────────────────────────────────────────────
    if any(word in text for word in ("净值", "产品详情", "查一下", "查询", "帮我查")):
        product = re.sub(
            r"^(?:查一下|查询|查|帮我查|帮我查一下)\s*",
            "",
            text,
        )
        product = re.sub(r"(?:的)?(?:最新净值|产品详情|净值)$", "", product).strip()
        # Remove leading 一下 if present
        product = re.sub(r"^一下\s*", "", product)
        code_match = re.search(r"(?:产品代码|代码)\s*([A-Za-z]+\d+)", product)
        if code_match:
            return ParsedOperation(
                action="product_query",
                params={"product_code": code_match.group(1)},
            )
        # Only treat trailing single-letter+digits as product_code if they
        # look like a standalone identifier (not part of the product name)
        trailing_code = re.search(r"\s+([A-Za-z]+\d+)$", product)
        if trailing_code:
            return ParsedOperation(
                action="product_query",
                params={"product_code": trailing_code.group(1)},
            )
        if not product:
            raise ParserError("请指定要查询的产品名称或代码")
        return ParsedOperation(
            action="product_query",
            params={"product_name": product},
        )

    raise ParserError("暂不支持该业务操作")
