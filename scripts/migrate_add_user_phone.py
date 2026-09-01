"""幂等迁移：users 表新增 phone 列（目标项目 sys_user.phone 等价）。

用法：.venv\\Scripts\\python.exe scripts/migrate_add_user_phone.py
PostgreSQL 的 ADD COLUMN IF NOT EXISTS 幂等，可重复执行。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.settings import Settings  # noqa: E402
from app.db.session import Database  # noqa: E402


async def main() -> None:
    settings = Settings()
    db = Database(settings)
    async with db.session_factory() as session:
        await session.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(32)")
        )
        await session.commit()
        print("OK: users.phone 列已就绪")


if __name__ == "__main__":
    asyncio.run(main())
