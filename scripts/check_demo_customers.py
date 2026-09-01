import asyncio

from sqlalchemy import select

from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.services.demo_customer_seed import ensure_demo_customer_profiles


async def main():
 db=Database(get_settings())
 await ensure_demo_customer_profiles(db)
 async with db.session_factory() as s:
  rows=(await s.execute(select(User).where(User.username.in_(["retail_investor_demo","high_net_worth_demo"])))).scalars().all()
  print([(r.username,r.display_name) for r in rows])
 await db.dispose()

if __name__ == "__main__": asyncio.run(main())
