"""以数据库为权威源，将图谱全量重建到 Neo4j（动态同步的兜底工具）。

与业务运行时的增量同步（注册/删除/产品增删改/交易成交时调用
KnowledgeGraphService 的 sync_* / delete_* 原子操作）配合使用。
适合：首次接入、历史数据不一致、或想强制对齐 DB 与图谱的场景。

用法（repo root，project venv）：
    .venv\\Scripts\\python.exe scripts\\sync_graph_dynamic.py          # 增量合并（MERGE，不清理存量）
    .venv\\Scripts\\python.exe scripts\\sync_graph_dynamic.py --clear  # 先清空图谱再重建
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.settings import get_settings  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.infrastructure.knowledge_graph import KnowledgeGraphService  # noqa: E402


async def main(clear: bool) -> None:
    settings = get_settings()
    db = Database(settings)
    graph = KnowledgeGraphService(settings)
    await graph.connect()
    if not graph.available:
        print("NEO4J UNAVAILABLE - 图谱重建跳过（业务运行时增量同步将自动降级）")
        await db.dispose()
        return
    if clear:
        await graph.clear_all()
        print("已清空现有图谱")
    result = await graph.rebuild_graph_from_db(db)
    print(
        f"图谱重建完成: 客户={result.get('customers', 0)} "
        f"产品={result.get('products', 0)} 持仓关系={result.get('holdings', 0)}"
    )
    print(
        f"当前图谱统计: 节点={result.get('total_nodes', 0)} "
        f"关系={result.get('total_relations', 0)}"
    )
    await graph.close()
    await db.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="以 DB 为权威源重建 Neo4j 图谱")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="先清空图谱再重建（默认 MERGE 增量合并）",
    )
    args = parser.parse_args()
    asyncio.run(main(args.clear))
