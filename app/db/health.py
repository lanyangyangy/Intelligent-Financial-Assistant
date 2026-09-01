from sqlalchemy import text

from app.db.session import Database


class DatabaseHealth:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def check(self) -> dict[str, str]:
        try:
            async with self.database.session_factory() as session:
                await session.execute(text("SELECT 1"))
                result = await session.execute(text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"))
                pgvector = bool(result.scalar())
                return {"status": "ok", "pgvector": "ok" if pgvector else "missing"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": type(exc).__name__}
