from __future__ import annotations

import logging

from neo4j import AsyncDriver, AsyncGraphDatabase
from sqlalchemy import select

from app.core.settings import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Neo4j 知识图谱（Phase 3 F3.1 / F3.2）
# 节点：Product / Customer / RiskLevel / Industry / FundManager
# 关系：HOLDS / SUITABLE_FOR / BELONGS_TO / MANAGED_BY / LISTED_IN
# ---------------------------------------------------------------------------

# 产品 → 行业 映射（Mock，符合金融业务逻辑；动态同步与全量重建共用）
# 覆盖全部演示产品，避免大量节点落入"其他"
_PRODUCT_INDUSTRY = {
    "天利货币基金A": "货币市场",
    "安盈现金管理": "货币市场",
    "稳利结构性存款1号": "固定收益",
    "鑫达纯债债券A": "债券市场",
    "安鑫短期理财": "固定收益",
    "增利债券精选": "债券市场",
    "稳健增值计划": "债券市场",
    "安享季季鑫": "固定收益",
    "平衡配置组合": "公募基金",
    "优选固收+": "债券市场",
    "红利指数增强": "权益市场",
    "安联稳健平衡": "公募基金",
    "沪深300指数增强": "权益市场",
    "成长精选组合": "权益市场",
    "科技先锋混合": "权益市场",
    "医疗健康主题基金": "权益市场",
    "新能源产业基金": "权益市场",
    "港股通精选": "权益市场",
    "私行进取策略": "私募股权",
    "全球配置私募": "私募股权",
    "量化对冲私募": "私募股权",
    "安泰养老目标": "公募基金",
    "教育金储蓄计划": "保险",
    "增额终身寿险": "保险",
    "年金保险计划": "保险",
    "美元债精选": "债券市场",
    "国债逆回购优选": "货币市场",
    "黄金ETF联接": "商品",
    "大宗商品CTA": "私募股权",
    "企业现金管理计划": "货币市场",
    "现金管理保本计划": "货币市场",
}

# 产品类型 → 投资团队 映射（基金经理按团队聚合，避免每产品一个"XX基金经理"）
_PRODUCT_TYPE_TEAM = {
    "现金管理": "现金管理投资团队",
    "cash_management": "现金管理投资团队",
    "corporate_cash": "现金管理投资团队",
    "货币基金": "现金管理投资团队",
    "货币": "现金管理投资团队",
    "结构性存款": "固定收益投资团队",
    "银行理财": "固定收益投资团队",
    "债券基金": "固定收益投资团队",
    "fixed_income": "固定收益投资团队",
    "QDII债券": "固定收益投资团队",
    "债券": "固定收益投资团队",
    "储蓄保险": "保险资管团队",
    "保险产品": "保险资管团队",
    "养老基金": "养老FOF投资团队",
    "混合基金": "公募基金投资团队",
    "balanced_fund": "公募基金投资团队",
    "股票基金": "权益投资团队",
    "equity_fund": "权益投资团队",
    "指数基金": "权益投资团队",
    "QDII基金": "权益投资团队",
    "商品基金": "另类投资团队",
    "私募基金": "私募股权投资团队",
    "private_strategy": "私募股权投资团队",
}


def fund_team_for(product_type: str, product_name: str = "") -> str:
    """按产品类型聚合到投资团队；未匹配类型回退为产品名前缀经理。"""
    team = _PRODUCT_TYPE_TEAM.get(product_type or "")
    if team:
        return team
    return f"{product_name[:2]}基金经理"


class KnowledgeGraphService:
    """Neo4j 图谱：数据导入 + Cypher 查询 Tool。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._driver: AsyncDriver | None = None
        self._enabled = False

    async def connect(self) -> None:
        try:
            self._driver = AsyncGraphDatabase.driver(
                self._settings.neo4j_uri,
                auth=(self._settings.neo4j_user, self._settings.neo4j_password),
            )
            await self._driver.verify_connectivity()
            self._enabled = True
            logger.info("neo4j connected uri=%s", self._settings.neo4j_uri)
        except Exception:  # noqa: BLE001 - 图谱不可用时系统降级为纯 RAG
            self._enabled = False
            logger.warning("neo4j unavailable, graph features disabled")

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    @property
    def available(self) -> bool:
        return self._enabled and self._driver is not None

    async def check(self) -> dict[str, str]:
        if not self._settings.neo4j_enabled:
            return {"status": "skipped", "reason": "Neo4j is disabled"}
        if not self.available:
            return {"status": "error", "reason": "Neo4j is unavailable"}
        try:
            await self._run("RETURN 1 AS ok")
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "reason": type(exc).__name__}
        return {"status": "ok"}

    async def _run(self, cypher: str, params: dict | None = None) -> list[dict]:
        if not self.available:
            logger.warning("neo4j graph unavailable, returning empty query result")
            return []
        assert self._driver is not None
        async with self._driver.session() as session:
            result = await session.run(cypher, params or {})
            records = await result.data()
            return [dict(record) for record in records]

    # -- 数据导入 ---------------------------------------------------------
    async def import_products(self, products: list[dict]) -> int:
        """products: [{id, name, product_type, risk_level, fund_manager}]"""
        if not products:
            return 0
        count = 0
        async with (
            self._driver.session() if self.available else _noop_session() as session
        ):  # type: ignore[union-attr]
            for p in products:
                await session.run(
                    """
                    MERGE (prod:Product {product_id: $id})
                    SET prod.name = $name,
                        prod.product_type = $product_type,
                        prod.risk_level = $risk_level
                    MERGE (rl:RiskLevel {level: $risk_level})
                    MERGE (prod)-[:HAS_RISK_LEVEL]->(rl)
                    """,
                    {
                        "id": p.get("id"),
                        "name": p.get("name"),
                        "product_type": p.get("product_type", ""),
                        "risk_level": p.get("risk_level", "R1"),
                    },
                )
                if p.get("fund_manager"):
                    await session.run(
                        """
                        MERGE (fm:FundManager {name: $name})
                        MERGE (prod:Product {product_id: $id})
                        MERGE (prod)-[:MANAGED_BY]->(fm)
                        """,
                        {"id": p.get("id"), "name": p.get("fund_manager")},
                    )
                count += 1
        return count

    async def import_customers(self, customers: list[dict]) -> int:
        """customers: [{id, risk_level, name?, username?}]"""
        if not customers:
            return 0
        count = 0
        async with (
            self._driver.session() if self.available else _noop_session() as session
        ):  # type: ignore[union-attr]
            for c in customers:
                customer_id = c.get("id")
                customer_key = str(customer_id)
                name = c.get("name") or c.get("display_name") or c.get("customer_name")
                username = c.get("username") or c.get("account_name")
                await session.run(
                    """
                    MERGE (cust:Customer {customer_id: $customer_key})
                    SET cust.id = $id,
                        cust.customer_id = $customer_key,
                        cust.name = $name,
                        cust.username = $username,
                        cust.display_name = $display_name
                    MERGE (rl:RiskLevel {name: $risk_level})
                    MERGE (cust)-[:HAS_RISK_LEVEL]->(rl)
                    """,
                    {
                        "id": customer_id,
                        "customer_key": customer_key,
                        "name": name,
                        "username": username,
                        "display_name": name,
                        "risk_level": str(c.get("risk_level", "C1")).upper(),
                    },
                )
                count += 1
        return count

    async def import_holdings(self, holdings: list[dict]) -> int:
        """holdings: [{customer_id, product_id}]"""
        if not holdings:
            return 0
        count = 0
        async with (
            self._driver.session() if self.available else _noop_session() as session
        ):  # type: ignore[union-attr]
            for h in holdings:
                customer_key = str(h.get("customer_id"))
                product_key = str(h.get("product_id"))
                await session.run(
                    """
                    MATCH (cust:Customer)
                    WHERE toString(cust.customer_id) = $cid OR toString(cust.id) = $cid
                    MATCH (prod:Product {product_id: $pid})
                    MERGE (cust)-[:INVESTS_IN]->(prod)
                    """,
                    {"cid": customer_key, "pid": product_key},
                )
                count += 1
        return count

    async def import_industries(self, industries: list[dict]) -> int:
        """industries: [{product_id, industry}]"""
        if not industries:
            return 0
        count = 0
        async with (
            self._driver.session() if self.available else _noop_session() as session
        ):  # type: ignore[union-attr]
            for item in industries:
                await session.run(
                    """
                    MATCH (prod:Product {product_id: $pid})
                    MERGE (ind:Industry {name: $industry})
                    MERGE (prod)-[:BELONGS_TO]->(ind)
                    """,
                    {
                        "pid": item.get("product_id"),
                        "industry": item.get("industry", "其他"),
                    },
                )
                count += 1
        return count

    # -- Cypher Tool（F3.1 封装）-----------------------------------------
    async def get_customer_products(self, customer_name: str) -> list[dict]:
        """查询客户持仓产品（多跳：客户→持仓→产品）。"""
        return await self._run(
            """
            MATCH (cust:Customer)-[:INVESTS_IN]->(prod:Product)
            WHERE toString(cust.customer_id) = $name
               OR toString(cust.id) = $name
               OR toLower(coalesce(cust.name, '')) = toLower($name)
               OR toLower(coalesce(cust.display_name, '')) = toLower($name)
               OR toLower(coalesce(cust.username, '')) = toLower($name)
            RETURN prod.product_id AS product_id, prod.name AS name,
                   prod.risk_level AS risk_level, prod.product_type AS product_type
            """,
            {"name": customer_name},
        )

    async def get_product_industry(self, product_name: str) -> list[dict]:
        """查询产品所属行业。"""
        return await self._run(
            """
            MATCH (prod:Product)-[:BELONGS_TO]->(ind:Industry)
            WHERE prod.name = $name
            RETURN ind.name AS industry
            """,
            {"name": product_name},
        )

    async def get_product_industry_by_id(self, product_id: str) -> list[dict]:
        """按 product_id 查询产品所属行业。"""
        return await self._run(
            """
            MATCH (prod:Product {product_id: $pid})-[:BELONGS_TO]->(ind:Industry)
            RETURN ind.name AS industry
            """,
            {"pid": product_id},
        )

    async def get_suitable_products(self, risk_level: str) -> list[dict]:
        """查询适当性匹配产品（按风险等级）。"""
        level = str(risk_level).upper().replace("C", "R")
        return await self._run(
            """
            MATCH (prod:Product)
            WHERE prod.risk_level = $level
            RETURN prod.product_id AS product_id, prod.name AS name,
                   prod.risk_level AS risk_level
            """,
            {"level": level},
        )

    async def get_industry_distribution(self, customer_name: str) -> list[dict]:
        """客户持仓行业分布（多跳：客户→产品→行业）。"""
        return await self._run(
            """
            MATCH (cust:Customer)-[:INVESTS_IN]->(prod:Product)-[:BELONGS_TO]->(ind:Industry)
            WHERE toString(cust.customer_id) = $name
               OR toString(cust.id) = $name
               OR toLower(coalesce(cust.name, '')) = toLower($name)
               OR toLower(coalesce(cust.display_name, '')) = toLower($name)
               OR toLower(coalesce(cust.username, '')) = toLower($name)
            RETURN ind.name AS industry, count(prod) AS product_count
            ORDER BY product_count DESC
            """,
            {"name": customer_name},
        )

    async def get_graph_stats(self) -> dict:
        """图谱节点/关系统计。"""
        if not self.available:
            return {
                "enabled": False,
                "reason": "neo4j_unavailable",
                "total_nodes": 0,
                "total_relations": 0,
                "nodes": [],
                "relations": [],
            }
        nodes = await self._run(
            "MATCH (n) RETURN labels(n) AS label, count(n) AS count ORDER BY count DESC"
        )
        rels = await self._run(
            "MATCH ()-[r]->() RETURN type(r) AS rel_type, count(r) AS count ORDER BY count DESC"
        )
        total_nodes = sum(int(n.get("count", 0)) for n in nodes)
        total_rels = sum(int(r.get("count", 0)) for r in rels)
        return {
            "enabled": self.available,
            "reason": None,
            "total_nodes": total_nodes,
            "total_relations": total_rels,
            "nodes": nodes,
            "relations": rels,
        }

    async def get_customer_graph(self, customer_name: str) -> dict:
        """客户图谱可视化数据（节点+边 JSON）。

        支持按客户中文姓名、用户名或 customer_id 查询。图谱数据使用的实际关系类型：
        (Customer)-[:INVESTS_IN]->(Product)
        (Product)-[:HAS_RISK_LEVEL]->(RiskLevel)
        (Product)-[:BELONGS_TO]->(Industry)
        (Product)-[:MANAGED_BY]->(FundManager)
        """
        rows = await self._run(
            """
            MATCH (cust:Customer)
            WHERE toString(cust.customer_id) = $name
               OR toString(cust.id) = $name
               OR toLower(coalesce(cust.name, '')) = toLower($name)
               OR toLower(coalesce(cust.display_name, '')) = toLower($name)
               OR toLower(coalesce(cust.username, '')) = toLower($name)
            OPTIONAL MATCH (cust)-[:INVESTS_IN]->(prod:Product)
            OPTIONAL MATCH (prod)-[:HAS_RISK_LEVEL]->(rl:RiskLevel)
            OPTIONAL MATCH (prod)-[:BELONGS_TO]->(ind:Industry)
            OPTIONAL MATCH (prod)-[:MANAGED_BY]->(fm:FundManager)
            RETURN coalesce(cust.name, cust.display_name, cust.username, toString(cust.customer_id), toString(cust.id)) AS customer,
                   collect(DISTINCT {
                       id: prod.product_id,
                       name: prod.name,
                       risk: prod.risk_level,
                       risk_level: rl.name,
                       industry: ind.name,
                       fund_manager: fm.name
                   }) AS products
            """,
            {"name": customer_name},
        )
        if not rows:
            return {"nodes": [], "edges": []}
        row = rows[0]
        customer = row.get("customer")
        if not customer:
            return {"nodes": [], "edges": []}
        nodes = [{"id": customer, "label": "客户", "name": customer}]
        edges = []
        seen = {customer}
        for prod in row.get("products") or []:
            pid = prod.get("id")
            if not pid:
                continue
            if pid not in seen:
                nodes.append(
                    {
                        "id": pid,
                        "label": "产品",
                        "name": prod.get("name"),
                        "risk": prod.get("risk"),
                    }
                )
                seen.add(pid)
            edges.append(
                {"source": customer, "target": pid, "relation": "HOLDS"}
            )
            for rel, target, label in (
                ("SUITABLE_FOR", prod.get("risk_level"), "风险等级"),
                ("BELONGS_TO", prod.get("industry"), "行业"),
                ("MANAGED_BY", prod.get("fund_manager"), "基金经理"),
            ):
                if not target:
                    continue
                key = f"{label}:{target}"
                if key not in seen:
                    nodes.append({"id": target, "label": label, "name": target})
                    seen.add(key)
                edges.append({"source": pid, "target": target, "relation": rel})
        return {"nodes": nodes, "edges": edges}

    async def list_customers(self) -> list[dict]:
        """图谱中全部客户名单（前端客户选择器）。"""
        return await self._run(
            """
            MATCH (c:Customer)
            RETURN coalesce(c.name, c.display_name, c.username, toString(c.customer_id), toString(c.id)) AS name,
                   c.username AS username,
                   coalesce(c.customer_id, toString(c.id)) AS customer_id
            ORDER BY name
            """
        )

    # -- 动态同步（业务写操作后增量更新，图谱不可用时静默降级）------------
    # 实际 Neo4j schema：
    #   Customer {id, name, username}
    #   Product  {id, name, risk_level, product_type, source}
    #   RiskLevel {name} / Industry {name} / FundManager {name}
    #   关系：HOLDS(Customer→Product) / SUITABLE_FOR(Product→RiskLevel)
    #         BELONGS_TO(Product→Industry) / MANAGED_BY(Product→FundManager)

    @staticmethod
    def _risk_level_r(value: str | None) -> str:
        """C1-C5 → R1-R5（图谱 RiskLevel 统一 R 前缀）。"""
        return str(value or "C1").upper().replace("C", "R")

    async def sync_customer(self, user_id: int, name: str, username: str) -> bool:
        """新增/更新客户节点（注册、恢复、画像变更时调用）。"""
        if not self.available:
            return False
        try:
            async with self._driver.session() as session:  # type: ignore[union-attr]
                await session.run(
                    """
                    MERGE (c:Customer {customer_id: $customer_key})
                    SET c.id = $id,
                        c.customer_id = $customer_key,
                        c.name = $name,
                        c.username = $username,
                        c.display_name = $name
                    """,
                    {
                        "id": int(user_id),
                        "customer_key": str(user_id),
                        "name": name,
                        "username": username,
                    },
                )
            return True
        except Exception:  # noqa: BLE001
            logger.warning("graph.sync_customer failed user=%s", user_id, exc_info=True)
            return False

    async def delete_customer(self, user_id: int) -> bool:
        """删除客户节点及其全部关系（软删除用户时调用）。"""
        if not self.available:
            return False
        try:
            async with self._driver.session() as session:  # type: ignore[union-attr]
                await session.run(
                    "MATCH (c:Customer) WHERE c.id = $id OR c.customer_id = $customer_id DETACH DELETE c",
                    {"id": int(user_id), "customer_id": str(user_id)},
                )
            return True
        except Exception:  # noqa: BLE001
            logger.warning("graph.delete_customer failed user=%s", user_id, exc_info=True)
            return False

    async def sync_product(
        self,
        product_id: str,
        name: str,
        product_type: str = "",
        risk_level: str = "C1",
        fund_manager: str | None = None,
        industry: str | None = None,
    ) -> bool:
        """新增/更新产品节点及其风险等级/行业/基金经理关联。"""
        if not self.available:
            return False
        risk_r = self._risk_level_r(risk_level)
        try:
            async with self._driver.session() as session:  # type: ignore[union-attr]
                await session.run(
                    """
                    MERGE (p:Product {product_id: $pid})
                    SET p.id = $pid,
                        p.product_id = $pid,
                        p.name = $name, p.product_type = $product_type,
                        p.risk_level = $risk, p.source = 'dynamic'
                    MERGE (rl:RiskLevel {name: $risk})
                    MERGE (p)-[:HAS_RISK_LEVEL]->(rl)
                    """,
                    {
                        "pid": str(product_id),
                        "name": name,
                        "product_type": product_type or "",
                        "risk": risk_r,
                    },
                )
                if fund_manager:
                    await session.run(
                        """
                        MATCH (p:Product {id: $pid})
                        MERGE (fm:FundManager {name: $name})
                        MERGE (p)-[:MANAGED_BY]->(fm)
                        """,
                        {"pid": product_id, "name": fund_manager},
                    )
                if industry:
                    await session.run(
                        """
                        MATCH (p:Product {id: $pid})
                        MERGE (ind:Industry {name: $name})
                        MERGE (p)-[:BELONGS_TO]->(ind)
                        """,
                        {"pid": product_id, "name": industry},
                    )
            return True
        except Exception:  # noqa: BLE001
            logger.warning("graph.sync_product failed pid=%s", product_id, exc_info=True)
            return False

    async def delete_product(self, product_id: str) -> bool:
        """删除产品节点及其全部关系（产品删除/停用时调用）。"""
        if not self.available:
            return False
        try:
            async with self._driver.session() as session:  # type: ignore[union-attr]
                await session.run(
                    "MATCH (p:Product) WHERE p.product_id = $pid OR p.id = $pid DETACH DELETE p",
                    {"pid": str(product_id)},
                )
            return True
        except Exception:  # noqa: BLE001
            logger.warning("graph.delete_product failed pid=%s", product_id, exc_info=True)
            return False

    async def sync_holding(self, customer_id: int, product_id: str) -> bool:
        """新增客户-产品持仓关系（申购成交后调用）。"""
        if not self.available:
            return False
        try:
            async with self._driver.session() as session:  # type: ignore[union-attr]
                await session.run(
                    """
                    MATCH (c:Customer)
                    WHERE c.id = $cid OR c.customer_id = $customer_id
                    MATCH (p:Product)
                    WHERE p.product_id = $pid OR p.id = $pid
                    MERGE (c)-[:INVESTS_IN]->(p)
                    """,
                    {
                        "cid": int(customer_id),
                        "customer_id": str(customer_id),
                        "pid": str(product_id),
                    },
                )
            return True
        except Exception:  # noqa: BLE001
            logger.warning(
                "graph.sync_holding failed cid=%s pid=%s", customer_id, product_id, exc_info=True
            )
            return False

    async def delete_holding(self, customer_id: int, product_id: str) -> bool:
        """删除客户-产品持仓关系（赎回清仓后调用）。"""
        if not self.available:
            return False
        try:
            async with self._driver.session() as session:  # type: ignore[union-attr]
                await session.run(
                    """
                    MATCH (c:Customer {id: $cid})-[r:HOLDS]->(p:Product {id: $pid})
                    DELETE r
                    """,
                    {"cid": int(customer_id), "pid": product_id},
                )
            return True
        except Exception:  # noqa: BLE001
            logger.warning(
                "graph.delete_holding failed cid=%s pid=%s", customer_id, product_id, exc_info=True
            )
            return False

    async def sync_customer_holdings(self, customer_id: int, product_ids: list[str]) -> bool:
        """重建某客户全部持仓关系（以 DB 为权威源，先清空再重建）。

        适用于交易完成后不确定持仓集合的场景（赎回、转仓、代理操作等）。
        """
        if not self.available:
            return False
        try:
            async with self._driver.session() as session:  # type: ignore[union-attr]
                await session.run(
                    "MATCH (c:Customer {id: $cid})-[r:HOLDS]->() DELETE r",
                    {"cid": int(customer_id)},
                )
                for pid in product_ids:
                    await session.run(
                        """
                        MATCH (c:Customer {id: $cid})
                        MATCH (p:Product {id: $pid})
                        MERGE (c)-[:HOLDS]->(p)
                        """,
                        {"cid": int(customer_id), "pid": pid},
                    )
            return True
        except Exception:  # noqa: BLE001
            logger.warning(
                "graph.sync_customer_holdings failed cid=%s", customer_id, exc_info=True
            )
            return False

    async def clear_all(self) -> bool:
        """清空整个图谱（全量重建前置）。"""
        if not self.available:
            return False
        try:
            async with self._driver.session() as session:  # type: ignore[union-attr]
                await session.run("MATCH (n) DETACH DELETE n")
            return True
        except Exception:  # noqa: BLE001
            logger.warning("graph.clear_all failed", exc_info=True)
            return False

    async def rebuild_graph_from_db(self, database) -> dict:
        """以数据库为权威源全量重建图谱（节点+持仓关系）。

        database: app.db.session.Database 实例。
        返回统计信息；图谱不可用时返回 enabled=False 并保留原数据。
        """
        if not self.available:
            return {"enabled": False, "reason": "neo4j unavailable"}
        from app.models.auth import Role, User  # noqa: F401
        from app.models.profile import CustomerHolding, CustomerProfile, Product

        async with database.session_factory() as session:
            products = list(
                (await session.execute(select(Product).where(Product.status == "active"))).scalars().all()
            )
            customers = list((await session.execute(select(CustomerProfile))).scalars().all())
            holdings = list(
                (
                    await session.execute(
                        select(CustomerHolding).where(CustomerHolding.status == "active")
                    )
                )
                .scalars()
                .all()
            )
        from sqlalchemy import select as sa_select

        # 客户名称/用户名：从 users 表补齐
        customer_ids = [c.user_id for c in customers]
        async with database.session_factory() as session:
            users = {}
            if customer_ids:
                rows = (
                    await session.execute(
                        sa_select(User.id, User.display_name, User.username).where(
                            User.id.in_(customer_ids)
                        )
                    )
                ).all()
                users = {r[0]: {"name": r[1], "username": r[2]} for r in rows}

        # 产品行业/基金经理：按团队聚合 + 完整行业映射（避免"其他"泛滥）
        product_info = {
            str(p.id): {
                "name": p.name,
                "product_type": p.product_type,
                "risk_level": str(p.risk_level).upper().replace("C", "R"),
                "fund_manager": fund_team_for(p.product_type, p.name),
                "industry": _PRODUCT_INDUSTRY.get(p.name, "其他"),
            }
            for p in products
        }

        await self.clear_all()
        n_customer = n_product = n_holding = 0
        for uid, info in users.items():
            if await self.sync_customer(uid, info["name"], info["username"]):
                n_customer += 1
        for pid, info in product_info.items():
            if await self.sync_product(pid, **info):
                n_product += 1
        for h in holdings:
            pid = str(h.product_id)
            if await self.sync_holding(h.user_id, pid):
                n_holding += 1
        stats = await self.get_graph_stats()
        return {
            "enabled": True,
            "customers": n_customer,
            "products": n_product,
            "holdings": n_holding,
            "total_nodes": stats["total_nodes"],
            "total_relations": stats["total_relations"],
        }


class _noop_session:
    """占位 context manager（图谱不可用时的类型占位）。"""

    async def __aenter__(self):
        raise RuntimeError("neo4j graph is not available")

    async def __aexit__(self, *args):
        return False
