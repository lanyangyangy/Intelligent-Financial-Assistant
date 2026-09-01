import asyncio

from app.core.settings import get_settings
from app.db.schema import ensure_schema
from app.db.session import Database
from app.services.auth_seed import ensure_auth_seed
from app.services.profile_seed import ensure_profile_seed
from app.services.trading_seed import ensure_trading_seed


async def main():
    settings = get_settings()
    database = Database(settings)
    try:
        await ensure_schema(database.engine)
        await ensure_auth_seed(database, settings)
        await ensure_profile_seed(database)
        await ensure_trading_seed(database)
        print("demo seed ok")
    finally:
        await database.dispose()

if __name__ == "__main__":
    asyncio.run(main())
