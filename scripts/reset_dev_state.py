"""Reset local Redis and application/demo PostgreSQL state, then reseed demo data.

This script is intentionally limited to development/local environments.
"""
import argparse
import asyncio

from sqlalchemy import text

from app.core.settings import get_settings
from app.db.schema import ensure_schema
from app.db.session import Database
from app.infrastructure.redis_client import RedisClient
from app.services.auth_seed import ensure_auth_seed
from app.services.profile_seed import ensure_profile_seed
from app.services.trading_seed import ensure_trading_seed


async def reset_postgres(database: Database, include_knowledge: bool) -> None:
    tables = [
        "order_status_history", "trades", "orders", "account",
        "customer_holding", "customer_asset_snapshot",
        "customer_subjective_profile", "customer_profile",
        "product_suitability_rule", "risk_rule", "product",
        "refresh_sessions", "user_roles", "role_permissions",
        "users", "roles", "permissions",
        "outbox_event", "async_task",
    ]
    if include_knowledge:
        tables += [
            "knowledge_chunk", "knowledge_document_version",
            "knowledge_document", "knowledge_base",
        ]
    async with database.engine.begin() as connection:
        existing = (await connection.execute(text("""
            SELECT tablename FROM pg_tables WHERE schemaname = 'public'
        """))).scalars().all()
        selected = [table for table in tables if table in existing]
        if selected:
            quoted = ", ".join(f'"{table}"' for table in selected)
            await connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


async def reset_redis(redis_client: RedisClient) -> None:
    await redis_client.client.flushdb()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-knowledge", action="store_true", help="also delete imported knowledge documents/chunks")
    parser.add_argument("--no-redis", action="store_true", help="do not flush Redis")
    args = parser.parse_args()

    settings = get_settings()
    if settings.app_env not in {"development", "dev", "local"}:
        raise RuntimeError("reset_dev_state.py only runs in development/local environments")

    database = Database(settings)
    redis_client = RedisClient(settings)
    try:
        await ensure_schema(database.engine)
        await reset_postgres(database, args.include_knowledge)
        if not args.no_redis:
            await reset_redis(redis_client)
        await ensure_auth_seed(database, settings)
        await ensure_profile_seed(database)
        await ensure_trading_seed(database)
        print("dev state reset and demo seed ok")
        print(f"redis_flushed={not args.no_redis}")
        print(f"knowledge_cleared={args.include_knowledge}")
    finally:
        await redis_client.close()
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
