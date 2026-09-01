import asyncio

from sqlalchemy import select

from app.core.settings import get_settings
from app.db.session import Database
from app.models.profile import CustomerEnterpriseVerification, CustomerProfile


async def main():
    db = Database(get_settings())
    async with db.session_factory() as session:
        rows = list((await session.execute(select(CustomerEnterpriseVerification).where(CustomerEnterpriseVerification.status == "approved"))).scalars().all())
        repaired = []
        for item in rows:
            profile = (await session.execute(select(CustomerProfile).where(CustomerProfile.user_id == item.user_id))).scalar_one_or_none()
            if profile is None:
                profile = CustomerProfile(id=str(__import__("uuid").uuid4()), user_id=item.user_id)
                session.add(profile)
            profile.customer_type = "enterprise"
            profile.customer_tier = "enterprise_standard"
            profile.source_type = "enterprise_verified"
            repaired.append({"verification_id": item.id, "user_id": item.user_id})
        await session.commit()
        print({"approved_count": len(rows), "repaired": repaired})
    await db.dispose()

if __name__ == "__main__": asyncio.run(main())
