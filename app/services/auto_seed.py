"""自动种子数据加载：项目启动时检测空库并灌入全部演示数据。

幂等设计：
  - 仅当数据库为空（users 表无记录）时自动灌入，避免覆盖已有数据
  - 各 seed 步骤本身幂等（按 username / document_key 查重，可重复执行）
  - 环境变量 AUTO_SEED 控制：
      1   = 强制灌入（跳过空库检测）
      0   = 禁用自动灌入
      缺省 = 空库自动灌入（推荐，别人首次启动即自动加载演示数据库）

调用方：
  - app/main.py lifespan 启动时自动调用
  - scripts/seed_all.py 手动调用（--force 强制）
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import func, select

from app.db.schema import ensure_schema
from app.models.auth import User
from app.services.auth_seed import ensure_auth_seed
from app.services.demo_customer_seed import ensure_demo_customer_profiles
from app.services.demo_product_seed import ensure_demo_products
from app.services.profile_seed import ensure_profile_seed
from app.services.trading_seed import ensure_trading_seed

logger = logging.getLogger(__name__)


async def _database_empty(database) -> bool:
    """users 表无记录视为空库（首次部署）。"""
    async with database.session_factory() as session:
        count = (
            await session.execute(select(func.count()).select_from(User))
        ).scalar_one()
    return count == 0


async def run_auto_seed(database, settings, *, force: bool = False) -> bool:
    """自动灌入种子数据，返回是否实际执行了 seed。

    步骤（按依赖顺序，全部幂等）：
      1. ensure_schema                建表
      2. ensure_auth_seed             角色/权限/员工演示账号
      3. ensure_profile_seed          基础画像
      4. ensure_trading_seed          账户/交易
      5. ensure_demo_products         演示产品目录
      6. ensure_demo_customer_profiles 演示客户画像
      7. seed_rich_data               30 客户/30 产品/持仓/300+ 交易/图谱
      8. seed_knowledge_docs          知识库文档（document_key 幂等）
      9. seed_knowledge_graph         Neo4j 图谱（不可用自动跳过）
    """
    # AUTO_SEED 环境变量覆盖：1 强制 / 0 禁用
    flag = os.getenv("AUTO_SEED", "").strip().lower()
    if flag == "0":
        logger.info("auto_seed disabled by AUTO_SEED=0")
        return False
    if flag in {"1", "true", "yes", "on"}:
        force = True

    if not force:
        # 先建表再检测空库（首次部署时 users 表可能还不存在）
        await ensure_schema(database.engine)
        if not await _database_empty(database):
            logger.info("auto_seed skipped: database already has users")
            return False
        logger.info("auto_seed: empty database detected, seeding demo data...")
    else:
        logger.info("auto_seed: forced by AUTO_SEED/--force")

    # 1-6：基础 seed（幂等）
    await ensure_schema(database.engine)
    await ensure_auth_seed(database, settings)
    await ensure_profile_seed(database)
    await ensure_trading_seed(database)
    await ensure_demo_products(database)
    await ensure_demo_customer_profiles(database)
    logger.info(
        "auto_seed: base seed done (schema/auth/profile/trading/products/customers)"
    )

    # 7：富数据（30 客户/30 产品/持仓/300+ 交易/风控异常样本/图谱）
    try:
        from scripts.seed_rich_data import main as seed_rich_data_main

        await seed_rich_data_main()
        logger.info(
            "auto_seed: rich data done (30 customers / 30 products / holdings / trades)"
        )
    except Exception as exc:  # noqa: BLE001 - 富数据失败不阻断启动
        logger.warning("auto_seed: rich data skipped (%s: %s)", type(exc).__name__, exc)

    # 8：知识库文档（document_key 幂等，可重复执行）
    try:
        from scripts.seed_knowledge_docs import main as seed_knowledge_docs_main

        await seed_knowledge_docs_main()
        logger.info("auto_seed: knowledge docs done")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "auto_seed: knowledge docs skipped (%s: %s)", type(exc).__name__, exc
        )

    # 9：Neo4j 图谱（不可用自动跳过）
    try:
        from scripts.seed_knowledge_graph import main as seed_knowledge_graph_main

        await seed_knowledge_graph_main()
        logger.info("auto_seed: knowledge graph done")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "auto_seed: knowledge graph skipped (%s: %s)", type(exc).__name__, exc
        )

    logger.info("auto_seed completed")
    return True
