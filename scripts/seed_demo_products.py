import asyncio

from app.core.settings import get_settings
from app.db.session import Database
from app.services.demo_product_seed import ensure_demo_products


async def main():
    db=Database(get_settings())
    await ensure_demo_products(db)
    await db.dispose()
    print('demo products seeded')
if __name__=='__main__': asyncio.run(main())
