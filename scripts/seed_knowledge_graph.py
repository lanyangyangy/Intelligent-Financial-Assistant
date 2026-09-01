"""Import products / customers / holdings / industries from the app DB into Neo4j.

Usage (repo root, project venv):
    .venv\\Scripts\\python.exe scripts\\seed_knowledge_graph.py
"""

import asyncio

from sqlalchemy import select

from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.knowledge_graph import (
    KnowledgeGraphService,
    fund_team_for,
)
from app.models.auth import User
from app.models.profile import CustomerHolding, CustomerProfile, Product

# 产品 → 行业 映射（Mock，符合金融业务逻辑）
PRODUCT_INDUSTRY = {
    "稳健增值计划": "债券市场",
    "现金管理保本计划": "货币市场",
    "平衡配置组合": "公募基金",
    "成长精选组合": "权益市场",
    "私行进取策略": "私募股权",
    "企业现金管理计划": "货币市场",
}


async def main() -> None:
    settings = get_settings()
    db = Database(settings)
    graph = KnowledgeGraphService(settings)
    await graph.connect()
    if not graph.available:
        print("NEO4J UNAVAILABLE - graph import skipped")
        await db.dispose()
        return

    async with db.session_factory() as session:
        products = list(
            (await session.execute(select(Product).where(Product.status == "active")))
            .scalars()
            .all()
        )
        product_rows = [
            {
                "id": str(p.id),
                "name": p.name,
                "product_type": p.product_type,
                "risk_level": str(p.risk_level).upper().replace("C", "R"),
                "fund_manager": fund_team_for(p.product_type, p.name),
            }
            for p in products
        ]
        customers = list(
            (await session.execute(select(CustomerProfile))).scalars().all()
        )
        customer_rows = []
        for c in customers:
            user = await session.get(User, c.user_id)
            customer_rows.append(
                {
                    "id": c.user_id,
                    "risk_level": (c.risk_level or "C1").upper(),
                    "name": user.display_name if user else None,
                    "username": user.username if user else None,
                }
            )
        holdings = list(
            (
                await session.execute(
                    select(CustomerHolding).where(CustomerHolding.status == "active")
                )
            )
            .scalars()
            .all()
        )
        holding_rows = [
            {"customer_id": h.user_id, "product_id": str(h.product_id)}
            for h in holdings
        ]
        industries = [
            {"product_id": str(p.id), "industry": PRODUCT_INDUSTRY.get(p.name, "其他")}
            for p in products
        ]

    await graph.clear_all()
    n_products = await graph.import_products(product_rows)
    n_customers = await graph.import_customers(customer_rows)
    n_holdings = await graph.import_holdings(holding_rows)
    n_industries = await graph.import_industries(industries)
    stats = await graph.get_graph_stats()
    print(
        f"imported: products={n_products} customers={n_customers} holdings={n_holdings} industries={n_industries}"
    )
    print(
        f"graph stats: nodes={stats['total_nodes']} relations={stats['total_relations']}"
    )

    await graph.close()
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
