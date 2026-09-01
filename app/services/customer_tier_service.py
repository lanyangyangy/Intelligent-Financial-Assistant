from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import CustomerProfile
from app.services.asset_summary_service import AssetSummaryService

TIER_ORDER = {"ordinary": 0, "gold": 1, "platinum": 2, "diamond": 3, "private_bank": 4, "enterprise_standard": 0}

def calculate_customer_tier(customer_type: str, investable_asset: Decimal | float | None) -> tuple[str, list[str]]:
    if customer_type == "enterprise":
        return "enterprise_standard", ["企业客户使用企业客户层级"]
    asset = Decimal(str(investable_asset or 0))
    if asset >= Decimal("10000000"):
        return "private_bank", ["可投资资产达到1000万元"]
    if asset >= Decimal("6000000"):
        return "diamond", ["可投资资产达到600万元"]
    if asset >= Decimal("2000000"):
        return "platinum", ["可投资资产达到200万元"]
    if asset >= Decimal("500000"):
        return "gold", ["可投资资产达到50万元"]
    return "ordinary", ["可投资资产未达到高净值客户层级门槛"]

class CustomerTierService:
    async def calculate(self, session: AsyncSession, user_id: str, persist: bool = True) -> dict:
        profile = (await session.execute(select(CustomerProfile).where(CustomerProfile.user_id == user_id))).scalar_one_or_none()
        if profile is None:
            return {"user_id": user_id, "customer_type": "individual", "customer_tier": "ordinary", "investable_asset": 0, "reasons": ["客户画像不存在，按普通个人客户保守处理"]}
        asset = await AssetSummaryService().latest_or_derived(session, user_id)
        investable_asset = asset.investable_asset if asset else Decimal("0")
        tier, reasons = calculate_customer_tier(profile.customer_type, investable_asset)
        if persist and profile.customer_tier != tier:
            profile.customer_tier = tier
            await session.flush()
            # flush 可能使带 server_default/onupdate 的字段（尤其 updated_at）
            # 进入过期状态。列表接口随后会把 profile 序列化为 Pydantic，
            # 此时不能再触发 AsyncSession 的隐式懒加载，否则会 MissingGreenlet。
            await session.refresh(profile)
        return {"user_id": user_id, "customer_type": profile.customer_type, "customer_tier": tier, "investable_asset": investable_asset, "reasons": reasons}
