from uuid import uuid4

from sqlalchemy import select

from app.db.session import Database
from app.models.profile import Product, ProductSuitabilityRule

PRODUCTS = (
 {"name":"现金管理保本计划","product_type":"cash_management","risk_level":"C1","minimum_amount":1000,"target_customer_type":"all","description":"低风险现金管理产品，未完成正式测评的客户也可购买。"},
 {"name":"稳健增值计划","product_type":"fixed_income","risk_level":"C2","minimum_amount":10000,"target_customer_type":"individual","description":"稳健型固收配置，适合保守和稳健客户。"},
 {"name":"平衡配置组合","product_type":"balanced_fund","risk_level":"C3","minimum_amount":20000,"target_customer_type":"individual","description":"股债均衡配置，适合平衡型客户。"},
 {"name":"成长精选组合","product_type":"equity_fund","risk_level":"C4","minimum_amount":50000,"target_customer_type":"individual","description":"成长型权益配置，适合成长型客户。"},
 {"name":"私行进取策略","product_type":"private_strategy","risk_level":"C5","minimum_amount":500000,"target_customer_type":"individual","description":"高风险进取策略，仅供高风险承受能力客户。"},
 {"name":"企业现金管理计划","product_type":"corporate_cash","risk_level":"C2","minimum_amount":100000,"target_customer_type":"enterprise","description":"企业客户现金管理方案，仅限企业客户。"},
)
async def ensure_demo_products(database: Database) -> None:
 async with database.session_factory() as session:
  for data in PRODUCTS:
   item=(await session.execute(select(Product).where(Product.name==data["name"]))).scalar_one_or_none()
   if item is None:
    product_data=data.copy()
    item=Product(id=str(uuid4()),source_type="synthetic_demo",status="active",**product_data); session.add(item); await session.flush()
   else:
    for key,value in data.items(): setattr(item,key,value)
    item.status="active"; item.deleted_at=None
   rule=(await session.execute(select(ProductSuitabilityRule).where(ProductSuitabilityRule.product_id==item.id))).scalars().first()
   customer_type=data["target_customer_type"]
   if rule is None:
    session.add(ProductSuitabilityRule(id=str(uuid4()),product_id=item.id,minimum_risk_level=item.risk_level,investor_type=customer_type,minimum_investable_asset=item.minimum_amount,rule_text=f"{item.risk_level}及以上且客户类型为{customer_type}"))
   else:
    rule.minimum_risk_level=item.risk_level; rule.investor_type=customer_type; rule.minimum_investable_asset=item.minimum_amount
  await session.commit()
