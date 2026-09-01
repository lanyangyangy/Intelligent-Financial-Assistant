import asyncio
from decimal import Decimal

from sqlalchemy import select

from app.core.settings import get_settings
from app.db.schema import ensure_schema
from app.db.session import Database
from app.models.auth import User
from app.models.profile import CustomerHolding, Product
from app.models.trading import Account, Order, Trade
from app.services.auth_service import AuthService
from app.services.trading_service import TradingService


async def main():
    settings=get_settings(); db=Database(settings); service=TradingService(); AuthService(db, settings)
    try:
        await ensure_schema(db.engine)
        async with db.session_factory() as s:
            customer=(await s.execute(select(User).where(User.username=="retail_investor_demo"))).scalar_one()
            operations=(await s.execute(select(User).where(User.username=="customer_manager_demo"))).scalar_one()
            product=(await s.execute(select(Product).where(Product.status=="active").order_by(Product.name))).scalars().first()
            account=(await s.execute(select(Account).where(Account.user_id==customer.id))).scalar_one()
            before=Decimal(str(account.available_balance))
            order,_=await service.create_order(s, customer, product.id, Decimal(str(max(float(product.minimum_amount), 10000))))
            await s.commit(); order_id=order.id
        async with db.session_factory() as s:
            customer=(await s.execute(select(User).where(User.username=="retail_investor_demo"))).scalar_one()
            await service.confirm_order(s, customer, order_id); await s.commit()
        async with db.session_factory() as s:
            operations=(await s.execute(select(User).where(User.username=="customer_manager_demo"))).scalar_one()
            await service.review_order(s, operations, order_id, True, "automated demo approval"); await s.commit()
        async with db.session_factory() as s:
            order=(await s.execute(select(Order).where(Order.id==order_id))).scalar_one()
            account=(await s.execute(select(Account).where(Account.user_id==order.user_id))).scalar_one()
            trade=(await s.execute(select(Trade).where(Trade.order_id==order_id))).scalar_one()
            holding=(await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==order.user_id, CustomerHolding.product_id==order.product_id))).scalar_one()
            print({"order_status":order.status,"trade":trade.trade_no,"available_before":str(before),"available_after":str(account.available_balance),"holding_quantity":str(holding.quantity)})
            assert order.status=="executed"
            assert account.available_balance==before-Decimal(str(order.amount))
            assert trade.order_id==order.id
    finally:
        await db.dispose()

if __name__=="__main__": asyncio.run(main())
