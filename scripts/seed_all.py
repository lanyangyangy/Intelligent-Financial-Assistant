"""一键灌入全部种子数据（幂等，可重复执行）。

用法（repo root，project venv）：
    .venv\\Scripts\\python.exe scripts\\seed_all.py
    .venv\\Scripts\\python.exe scripts\\seed_all.py --force   # 强制灌入（跳过空库检测）

也可在项目启动时自动加载：app/main.py lifespan 会调用 app/services/auto_seed.py，
数据库为空（users 表无记录）时自动灌入全部演示数据（AUTO_SEED=1 强制 / =0 禁用）。

覆盖内容（按依赖顺序）：
    1. 建表 ensure_schema
    2. 认证/权限/角色 + 员工演示账号 ensure_auth_seed
    3. 基础画像 ensure_profile_seed
    4. 账户/交易 ensure_trading_seed
    5. 演示产品 ensure_demo_products
    6. 演示客户画像 ensure_demo_customer_profiles
    7. 富数据 seed_rich_data（30 客户 / 30 产品 / 持仓 / 300+ 交易 / 风控异常样本 / 图谱）
    8. 知识库文档 seed_knowledge_docs（document_key 幂等）
    9. Neo4j 图谱 seed_knowledge_graph（Neo4j 不可用时自动跳过）
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 保证从任意 cwd 启动都能 import 项目包
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.settings import get_settings  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.services.auto_seed import run_auto_seed  # noqa: E402


async def main(force: bool) -> None:
    settings = get_settings()
    database = Database(settings)
    try:
        executed = await run_auto_seed(database, settings, force=force)
        if executed:
            print("\n✅ seed_all 完成：全部演示数据已灌入。")
            print("   启动 outbox/knowledge worker 可完成知识库向量化：")
            print("   .venv\\Scripts\\python.exe -m workers.runner")
        else:
            print("\n⏭️ 数据库已有数据，跳过灌入（--force 可强制重灌）。")
    finally:
        await database.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="灌入全部种子数据（幂等）")
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制灌入（跳过空库检测，重复执行会覆盖/补充演示数据）",
    )
    args = parser.parse_args()
    asyncio.run(main(args.force))
