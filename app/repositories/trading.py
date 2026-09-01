from sqlalchemy import func, select

from app.models.profile import CustomerAssetSnapshot, CustomerHolding
from app.models.trading import Account, Order


def locked(query, for_update): return query.with_for_update() if for_update else query
class SqlAlchemyAccountRepository:
 async def get_by_user(self,s,user_id,for_update=False): return (await s.execute(locked(select(Account).where(Account.user_id==user_id),for_update))).scalar_one_or_none()
 async def save(self,s,account): s.add(account); await s.flush(); return account
class SqlAlchemyOrderRepository:
 async def get(self,s,order_id,user_id=None,for_update=False):
  q=select(Order).where(Order.id==order_id)
  if user_id is not None:q=q.where(Order.user_id==user_id)
  return (await s.execute(locked(q,for_update))).scalar_one_or_none()
 async def get_by_idempotency(self,s,user_id,key): return (await s.execute(select(Order).where(Order.user_id==user_id,Order.idempotency_key==key).order_by(Order.created_at.desc()).limit(1))).scalars().first()
 async def save(self,s,order): s.add(order); await s.flush(); return order
class SqlAlchemyHoldingRepository:
 async def get_active(self,s,user_id,product_id,for_update=False): return (await s.execute(locked(select(CustomerHolding).where(CustomerHolding.user_id==user_id,CustomerHolding.product_id==product_id,CustomerHolding.status=="active"),for_update))).scalar_one_or_none()
 async def sum_market_value(self,s,user_id): return (await s.execute(select(func.coalesce(func.sum(CustomerHolding.market_value),0)).where(CustomerHolding.user_id==user_id,CustomerHolding.status=="active"))).scalar_one()
 async def save(self,s,holding): s.add(holding); await s.flush(); return holding
class SqlAlchemyAssetRepository:
 async def latest(self,s,user_id): return (await s.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==user_id).order_by(CustomerAssetSnapshot.snapshot_time.desc().nullslast(),CustomerAssetSnapshot.created_at.desc().nullslast(),CustomerAssetSnapshot.id.desc()).limit(1))).scalars().first()
 async def save(self,s,snapshot): s.add(snapshot); await s.flush(); return snapshot
