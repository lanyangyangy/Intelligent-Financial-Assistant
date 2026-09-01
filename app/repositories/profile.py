from sqlalchemy import select

from app.models.auth import User
from app.models.profile import CustomerProfile, CustomerRiskAssessment, Product


class SqlAlchemyCustomerRepository:
    async def get_user(self, session, user_id): return (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    async def get_profile(self, session, user_id): return (await session.execute(select(CustomerProfile).where(CustomerProfile.user_id == user_id))).scalar_one_or_none()
    async def save_profile(self, session, profile): session.add(profile); await session.flush(); return profile

class SqlAlchemyProductRepository:
    async def get_active(self, session, product_id): return (await session.execute(select(Product).where(Product.id == product_id, Product.status == "active", Product.deleted_at.is_(None)))).scalar_one_or_none()
    async def list_active(self, session): return list((await session.execute(select(Product).where(Product.status == "active", Product.deleted_at.is_(None)).order_by(Product.name))).scalars().all())

class SqlAlchemyRiskAssessmentRepository:
    async def get_active(self, session, user_id): return (await session.execute(select(CustomerRiskAssessment).where(CustomerRiskAssessment.user_id == user_id, CustomerRiskAssessment.status.in_(["active", "provisional"])).order_by(CustomerRiskAssessment.assessed_at.desc()).limit(1))).scalars().first()
    async def supersede_active(self, session, user_id):
        rows=list((await session.execute(select(CustomerRiskAssessment).where(CustomerRiskAssessment.user_id == user_id, CustomerRiskAssessment.status == "active"))).scalars().all())
        for row in rows: row.status="superseded"
        await session.flush()
    async def save(self, session, assessment): session.add(assessment); await session.flush(); return assessment
