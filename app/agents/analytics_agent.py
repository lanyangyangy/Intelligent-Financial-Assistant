from __future__ import annotations

import hashlib
import json
import re
import time
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, or_, select, text

from app.agents.base import AgentBase
from app.common.security.roles import CUSTOMER_ROLE_CODES
from app.models.auth import User
from app.ports.agent import AgentContext
from app.schemas.agents import AgentResult

# ---------------------------------------------------------------------------
# NL2SQL: Generate -> Validate -> Execute -> Interpret.
# ---------------------------------------------------------------------------

MAX_RESULT_ROWS = 100
INTERPRET_PREVIEW_ROWS = 10
NL2SQL_CACHE_TTL_SECONDS = 600
NL2SQL_CACHE_VERSION = "v4"

DANGEROUS_PATTERN = re.compile(
    r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|GRANT|REVOKE|CREATE|MERGE)\b",
    re.I,
)
ONLY_SELECT = re.compile(r"^\s*SELECT\b", re.I)
BLOCKED_SELECT_PATTERN = re.compile(
    r"\b(INTO|FOR\s+(?:UPDATE|NO\s+KEY\s+UPDATE|SHARE|KEY\s+SHARE))\b", re.I
)
TABLE_REFERENCE_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:ONLY\s+)?(?:public\.)?[\"`]?"
    r"([A-Za-z_][A-Za-z0-9_$]*)[\"`]?",
    re.I,
)
LIMIT_PATTERN = re.compile(r"\bLIMIT\s+(ALL|\d+)", re.I)

# The requirement document uses fin_* names, while this project uses the
# current model names below. The prompt always exposes the current names; the
# aliases are a defensive compatibility layer for old few-shot/model output.
TABLE_ALIASES = {
    "products": "product",
    "fin_product": "product",
    "fin_holdings": "customer_holding",
    "fin_transaction": "trades",
    "fin_transactions": "trades",
    "sys_user": "users",
    "fin_customer_profile": "customer_profile",
    "biz_work_order": "work_order",
}

# intent -> relevant tables in the project's actual PostgreSQL schema.
INTENT_TABLES: dict[str, list[str]] = {
    "holdings_query": ["customer_holding", "product", "users"],
    "return_stats": ["customer_holding", "product", "trades"],
    "transaction_query": ["orders", "trades", "product"],
    "customer_stats": [
        "users",
        "customer_profile",
        "customer_asset_snapshot",
        "customer_holding",
    ],
    "product_stats": ["product"],
    "workorder_query": ["work_order", "users"],
}

INTENT_KEYWORDS: dict[str, list[str]] = {
    "workorder_query": ["工单状态", "待处理", "查看工单", "工单列表", "工单"],
    "holdings_query": ["持仓", "持有", "持有哪些"],
    "return_stats": ["收益率", "平均收益", "收益", "盈利", "亏损"],
    "transaction_query": ["交易记录", "最近交易", "转账记录", "交易", "订单", "成交"],
    "customer_stats": ["客户总数", "有多少客户", "客户", "用户", "AUM", "资产"],
    "product_stats": ["产品总数", "在售产品", "产品", "类型"],
}

INTENT_LABELS = {
    "holdings_query": "持仓查询",
    "return_stats": "收益统计",
    "transaction_query": "交易记录",
    "customer_stats": "客户统计",
    "product_stats": "产品统计",
    "workorder_query": "工单查询",
}

TABLE_DESCRIPTIONS = {
    "customer_holding": "客户持仓、数量、成本、市值和盈亏",
    "product": "理财产品，status=active 表示在售",
    "users": "系统用户和内部员工账号",
    "trades": "已成交交易记录",
    "orders": "申购/交易订单记录",
    "customer_profile": "客户画像与风险等级",
    "customer_asset_snapshot": "客户资产快照，total_asset 为总资产",
    "work_order": "风险预警、投诉和可疑交易工单",
}

# 每个场景有一个确定可执行的示例；实际注入时最多放 3 个，控制 Prompt
# 长度，同时保证当前意图始终有对应 Few-shot。
FEW_SHOTS = {
    "holdings_query": (
        "客户目前持有哪些产品？",
        "SELECT p.name, h.quantity, h.market_value "
        "FROM customer_holding h JOIN product p ON p.id = h.product_id "
        "WHERE h.status = 'active'",
    ),
    "return_stats": (
        "各产品类型的平均收益率是多少？",
        "SELECT p.product_type, "
        "AVG(h.profit_loss / NULLIF(h.cost_amount, 0)) AS average_return_rate "
        "FROM customer_holding h JOIN product p ON p.id = h.product_id "
        "GROUP BY p.product_type",
    ),
    "transaction_query": (
        "最近30天的交易记录",
        "SELECT t.trade_no, p.name, t.amount, t.executed_at "
        "FROM trades t JOIN product p ON p.id = t.product_id "
        "WHERE t.executed_at >= CURRENT_DATE - INTERVAL '30 days' "
        "ORDER BY t.executed_at DESC",
    ),
    "customer_stats": (
        "AUM超过100万的客户有多少个？",
        "SELECT COUNT(DISTINCT u.id) AS customer_count "
        "FROM users u JOIN customer_asset_snapshot a ON a.user_id = u.id "
        "WHERE a.total_asset > 1000000",
    ),
    "product_stats": (
        "当前在售产品总数",
        "SELECT COUNT(*) AS product_count FROM product WHERE status = 'active'",
    ),
    "workorder_query": (
        "查看待处理工单",
        "SELECT w.workorder_no, w.status, w.priority, w.title "
        "FROM work_order w WHERE w.status IN ('pending', 'processing') "
        "ORDER BY w.created_at DESC",
    ),
}


class DataAnalystAgent(AgentBase):
    """数据分析 Agent：自然语言 -> SQL -> 安全校验 -> 执行 -> 解读。"""

    name = "data_analyst"
    description = "数据分析：NL2SQL 查询持仓、收益、交易、客户、产品与工单"

    def __init__(self, database, settings, llm=None, cache=None):
        super().__init__(database, settings, llm)
        self._schema_cache: dict[str, str] = {}
        self._local_result_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._redis = getattr(cache, "client", cache)

    # -- intent & schema --------------------------------------------------
    def classify_intent(self, message: str) -> str:
        text_lower = message.lower()
        for intent, keywords in INTENT_KEYWORDS.items():
            if any(keyword.lower() in text_lower for keyword in keywords):
                return intent
        return "product_stats"

    async def _load_schema(self, tables: list[str]) -> str:
        key = ",".join(sorted(tables))
        if key in self._schema_cache:
            return self._schema_cache[key]

        async with self.database.session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT table_name, column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name IN :tables
                    ORDER BY table_name, ordinal_position
                    """
                ).bindparams(bindparam("tables", expanding=True)),
                {"tables": tuple(tables)},
            )
            rows = result.all()

        lines: list[str] = []
        current_table = None
        for table_name, column_name, data_type in rows:
            if table_name != current_table:
                description = TABLE_DESCRIPTIONS.get(table_name, "")
                lines.append(f"TABLE {table_name} -- {description}")
                current_table = table_name
            lines.append(f"  {table_name}.{column_name} {data_type}")
        schema = "\n".join(lines)
        self._schema_cache[key] = schema
        return schema

    @staticmethod
    def _few_shots_for_intent(intent: str) -> str:
        # 每个意图至少注入 3 个可执行示例，满足需求文档的 3-5 个范围。
        # 共享基础统计示例，避免为每个意图重复维护大量 Prompt 文本。
        ordered = [intent, "product_stats", "customer_stats", "holdings_query"]
        examples: list[str] = []
        seen: set[str] = set()
        for selected_intent in ordered:
            if selected_intent not in FEW_SHOTS or selected_intent in seen:
                continue
            seen.add(selected_intent)
            question, sql = FEW_SHOTS[selected_intent]
            examples.append(f"示例 {len(examples) + 1}：\n问题：{question}\nSQL：{sql}")
        return "\n\n".join(examples)

    @staticmethod
    def _extract_customer_identifier(message: str) -> str | None:
        """提取持仓问题中的客户称呼，避免模型把客户名当成产品属性。"""
        match = re.search(
            r"(?:查询|查看|分析)?\s*(.+?)\s*(?:目前|当前)?\s*持有(?:哪些|哪一些)?",
            message,
        )
        if not match:
            return None
        identifier = re.sub(r"^[的和与]?客户", "", match.group(1).strip())
        if identifier in {"", "客户", "所有客户", "各客户", "我的"}:
            return None
        return identifier

    async def _resolve_named_customer(self, message: str) -> str | None:
        """从消息中解析具体客户名（用户名或中文名）→ 客户 user_id UUID。

        仅在消息明确提及唯一客户时返回（如 "liwei / 李伟"）；
        泛化指代（所有客户/各客户/我的）返回 None，交由 LLM 生成全局查询。
        """
        customer_roles = or_(
            *(User.roles.any(code=code) for code in CUSTOMER_ROLE_CODES)
        )
        async with self.database.session_factory() as session:
            users = list(
                (
                    await session.execute(
                        select(User.id, User.username, User.display_name).where(
                            User.status == "active", customer_roles
                        )
                    )
                ).all()
            )
        text_lower = message.lower()
        matches: list[str] = []
        for uid, username, display_name in users:
            hit = (username and username.lower() in text_lower) or (
                display_name and display_name.lower() in text_lower
            )
            if hit:
                matches.append(str(uid))
        if len(matches) == 1:
            return matches[0]
        return None

    async def _named_transaction_sql(self, message: str) -> str | None:
        """交易记录：消息指定了具体客户时，先用客户名解析出 user_id UUID，
        再生成正确的 SQL（避免 LLM 把用户名 liwei 当作 UUID 导致 0 行）。"""
        customer_id = await self._resolve_named_customer(message)
        if not customer_id:
            return None
        return (
            "SELECT t.trade_no, p.name, t.amount, t.quantity, t.executed_at "
            "FROM trades t JOIN product p ON p.id = t.product_id "
            f"WHERE t.user_id = '{customer_id}' "
            "AND t.executed_at >= CURRENT_DATE - INTERVAL '30 days' "
            "ORDER BY t.executed_at DESC LIMIT 100"
        )

    async def _named_holdings_sql(self, message: str) -> str | None:
        identifier = self._extract_customer_identifier(message)
        if not identifier:
            return None
        customer_roles = or_(
            *(User.roles.any(code=code) for code in CUSTOMER_ROLE_CODES)
        )
        async with self.database.session_factory() as session:
            matches = list(
                (
                    await session.execute(
                        select(User.id)
                        .where(
                            User.status == "active",
                            customer_roles,
                            or_(
                                User.username.ilike(f"%{identifier}%"),
                                User.display_name.ilike(f"%{identifier}%"),
                            ),
                        )
                        .limit(2)
                    )
                )
                .scalars()
                .all()
            )
        if len(matches) != 1:
            return None
        customer_id = str(matches[0])
        return (
            "SELECT p.name, h.quantity, h.market_value, h.profit_loss "
            "FROM customer_holding h "
            "JOIN product p ON p.id = h.product_id "
            "JOIN users u ON u.id = h.user_id "
            f"WHERE h.status = 'active' AND u.id = '{customer_id}' "
            "ORDER BY p.name"
        )

    @staticmethod
    def _return_stats_sql(message: str) -> str:
        """按用户表达选择产品维度或产品类型维度，避免两者混为一谈。"""
        if "产品类型" in message or "产品类别" in message or "类别" in message:
            return (
                "SELECT p.product_type, "
                "AVG(h.profit_loss / NULLIF(h.cost_amount, 0)) AS average_return_rate "
                "FROM customer_holding h JOIN product p ON p.id = h.product_id "
                "WHERE h.status = 'active' GROUP BY p.product_type"
            )
        return (
            "SELECT p.id, p.name, "
            "AVG(h.profit_loss / NULLIF(h.cost_amount, 0)) AS average_return_rate "
            "FROM customer_holding h JOIN product p ON p.id = h.product_id "
            "WHERE h.status = 'active' GROUP BY p.id, p.name ORDER BY p.name"
        )

    async def _generate_sql(
        self, message: str, schema: str, few_shots: str = ""
    ) -> str | None:
        system = f"""你是一个 PostgreSQL SQL 专家。根据表结构和示例，将自然语言查询转为一条只读 SQL。

表结构（必须使用下列实际表名，禁止自行改成复数或 fin_* 旧表名）：
{schema}

Few-shot 示例：
{few_shots}

要求：
- 只输出一条 SELECT 语句，禁止多语句、分号、SELECT INTO、FOR UPDATE 和任何写操作
- 只能使用表结构中出现的表名和字段名
- 当前在售产品使用 product.status = 'active'
- 金额字段为 numeric，日期字段为 timestamptz
- 日期计算使用 CURRENT_DATE 或 NOW() - INTERVAL 'N days'
- 输出只返回 SQL，不要 Markdown、解释或代码围栏"""
        return await self.llm_chat(system, message, temperature=0.1, max_tokens=512)

    @staticmethod
    def _has_unquoted_semicolon(sql: str) -> bool:
        quote: str | None = None
        index = 0
        while index < len(sql):
            char = sql[index]
            if quote:
                if char == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
            elif char in {"'", '"'}:
                quote = char
            elif char == ";":
                return True
            index += 1
        return False

    @staticmethod
    def _normalize_table_aliases(sql: str, tables: list[str]) -> str:
        normalized = sql
        allowed = set(tables)
        for alias, target in TABLE_ALIASES.items():
            if target not in allowed:
                continue
            pattern = rf"(\b(?:FROM|JOIN)\s+)(?:public\.)?{re.escape(alias)}\b"
            normalized = re.sub(pattern, rf"\1{target}", normalized, flags=re.I)
        return normalized

    def _validate_sql(self, sql: str, tables: list[str] | None = None) -> str | None:
        sql = sql.strip().strip("`")
        if sql.startswith("```sql"):
            sql = sql[6:].strip()
        if sql.endswith("```"):
            sql = sql[:-3].rstrip()
        if sql.endswith(";"):
            sql = sql[:-1].rstrip()
        if not sql or not ONLY_SELECT.match(sql):
            return None
        if self._has_unquoted_semicolon(sql):
            return None
        if DANGEROUS_PATTERN.search(sql) or BLOCKED_SELECT_PATTERN.search(sql):
            return None

        if tables is not None:
            allowed_tables = {table.lower() for table in tables}
            for referenced_table in TABLE_REFERENCE_PATTERN.findall(sql):
                if referenced_table.lower() not in allowed_tables:
                    return None

        limit_matches = list(LIMIT_PATTERN.finditer(sql))
        if re.search(r"\bLIMIT\b", sql, re.I) and not limit_matches:
            return None

        if limit_matches:

            def cap_limit(match: re.Match[str]) -> str:
                value = match.group(1).lower()
                capped = (
                    MAX_RESULT_ROWS
                    if value == "all"
                    else min(int(value), MAX_RESULT_ROWS)
                )
                return f"LIMIT {capped}"

            sql = LIMIT_PATTERN.sub(cap_limit, sql)
        else:
            sql = f"{sql} LIMIT {MAX_RESULT_ROWS}"
        return sql

    async def _execute_sql(self, sql: str) -> list[dict]:
        async with self.database.session_factory() as session:
            result = await session.execute(text(sql))
            columns = list(result.keys())
            rows = [dict(zip(columns, row)) for row in result.all()]

        normalized: list[dict] = []
        for row in rows[:MAX_RESULT_ROWS]:
            item: dict[str, Any] = {}
            for key, value in row.items():
                if isinstance(value, Decimal):
                    item[key] = str(value)
                elif isinstance(value, (dict, list, tuple)):
                    item[key] = str(value)
                elif hasattr(value, "isoformat"):
                    item[key] = value.isoformat()
                else:
                    item[key] = value
            normalized.append(item)
        return normalized

    # -- cache ------------------------------------------------------------
    @staticmethod
    def _cache_key(message: str, schema: str, context: AgentContext) -> str:
        scope = f"{context.role or ''}:{context.user_id or ''}"
        return hashlib.md5(
            f"{message.strip().lower()}:{schema}:{scope}".encode()
        ).hexdigest()

    async def _cache_get(self, cache_key: str) -> dict[str, Any] | None:
        redis_key = f"nl2sql:{NL2SQL_CACHE_VERSION}:{cache_key}"
        if self._redis is not None:
            try:
                cached = await self._redis.get(redis_key)
                if cached:
                    return json.loads(cached)
            except Exception:  # noqa: BLE001 - cache failure must not block queries
                pass

        local = self._local_result_cache.get(cache_key)
        if local is None:
            return None
        expires_at, value = local
        if expires_at <= time.monotonic():
            self._local_result_cache.pop(cache_key, None)
            return None
        return value

    async def _cache_set(self, cache_key: str, value: dict[str, Any]) -> None:
        self._local_result_cache[cache_key] = (
            time.monotonic() + NL2SQL_CACHE_TTL_SECONDS,
            value,
        )
        if self._redis is not None:
            try:
                await self._redis.set(
                    f"nl2sql:{NL2SQL_CACHE_VERSION}:{cache_key}",
                    json.dumps(value, ensure_ascii=False, default=str),
                    ex=NL2SQL_CACHE_TTL_SECONDS,
                )
            except Exception:  # noqa: BLE001 - local cache remains available
                pass

    # -- interpret --------------------------------------------------------
    async def _interpret(self, message: str, rows: list[dict]) -> str:
        if not rows:
            return "查询完成，但没有匹配的数据。"
        preview = json.dumps(
            rows[:INTERPRET_PREVIEW_ROWS], ensure_ascii=False, default=str
        )
        system = f"""你是金融数据分析师。根据用户问题和查询结果生成简洁的自然语言解读。

用户问题：{message}
查询结果前 {min(INTERPRET_PREVIEW_ROWS, len(rows))} 行（总计 {len(rows)} 行）：
{preview}

要求：给出关键结论，数值带千分位分隔符，50 字以内。"""
        reply = await self.llm_chat(
            system, "请解读上述查询结果。", temperature=0.2, max_tokens=256
        )
        if not reply:
            if len(rows) == 1:
                reply = "查询到 1 条记录：" + "，".join(
                    f"{key}={value}" for key, value in rows[0].items()
                )
            else:
                reply = f"查询完成，共返回 {len(rows)} 行数据（已展示前 10 行）。"
        return reply

    # -- entry ------------------------------------------------------------
    async def run(self, message: str, context: AgentContext) -> AgentResult:
        intent = self.classify_intent(message)
        tables = INTENT_TABLES[intent]
        schema = await self._load_schema(tables)
        cache_key = self._cache_key(message, schema, context)

        cached = await self._cache_get(cache_key)
        if cached is not None:
            cached_data = {**cached, "cache_key": cache_key, "cache_hit": True}
            cached_data.setdefault("sql_statement", cached_data.get("sql"))
            result = self.ok(
                cached_data["interpretation"], data=cached_data, confidence=0.85
            )
            result.tool_calls = [{"name": "nl2sql_cache", "status": "hit"}]
            return result

        few_shots = self._few_shots_for_intent(intent)
        if intent == "holdings_query":
            raw_sql = await self._named_holdings_sql(message)
        elif intent == "return_stats":
            raw_sql = self._return_stats_sql(message)
        elif intent == "transaction_query":
            raw_sql = await self._named_transaction_sql(message)
        else:
            raw_sql = None
        if raw_sql is None:
            raw_sql = await self._generate_sql(message, schema, few_shots)
        if not raw_sql:
            return self.fail(
                "LLM 不可用，无法生成 SQL", ["DASHSCOPE_API_KEY 未配置或上游调用失败"]
            )

        normalized_sql = self._normalize_table_aliases(raw_sql, tables)
        sql = self._validate_sql(normalized_sql, tables)
        if sql is None:
            result = self.ok(
                "不允许执行该操作：仅支持只读 SELECT 查询。",
                data={
                    "intent": intent,
                    "intent_label": INTENT_LABELS[intent],
                    "sql": raw_sql,
                    "sql_statement": raw_sql,
                    "blocked": True,
                    "cache_hit": False,
                },
                confidence=1.0,
            )
            result.tool_calls = [{"name": "sql_safety", "status": "rejected"}]
            return result

        try:
            rows = await self._execute_sql(sql)
        except Exception as exc:  # noqa: BLE001 - agent returns a controlled result
            return self.fail(
                f"SQL 执行失败：{type(exc).__name__}",
                [str(exc)],
                data={
                    "sql": sql,
                    "sql_statement": sql,
                    "cache_hit": False,
                },
            )

        interpretation = await self._interpret(message, rows)
        data = {
            "intent": intent,
            "intent_label": INTENT_LABELS[intent],
            "sql": sql,
            "sql_statement": sql,
            "query_result": rows,
            "rows": rows,
            "row_count": len(rows),
            "truncated": len(rows) == MAX_RESULT_ROWS,
            "interpretation": interpretation,
            "cache_key": cache_key,
            "cache_hit": False,
        }
        await self._cache_set(cache_key, data)
        result = self.ok(interpretation, data=data, confidence=0.8)
        result.tool_calls = [
            {"name": "nl2sql", "status": "success", "intent": intent},
            {"name": "sql_safety", "status": "passed"},
            {"name": "sql_executor", "status": "success", "row_count": len(rows)},
            {"name": "result_interpretation", "status": "success"},
        ]
        return result
