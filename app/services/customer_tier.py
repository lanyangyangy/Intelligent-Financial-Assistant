"""Customer tier classification and tier-aware policy configuration (standalone copy)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CustomerTier:
    """Immutable tier configuration."""
    tier_id: int                    # 1-5
    display_name: str
    level_key: str
    min_assets: float = 0.0
    large_transaction_threshold: float = 50_000.0
    confirmation_threshold_purchase: float = 10_000.0
    confirmation_threshold_transfer: float = 50_000.0
    max_products_in_recommendation: int = 3
    priority_high_risk_products: bool = False
    show_vip_theme: bool = False
    vip_service_message: str = ""


# ── Tier definitions ───────────────────────────────────────────────────

TIER_5_PRIVATE_BANKING = CustomerTier(
    tier_id=5, display_name="私行客户", level_key="私行",
    min_assets=10_000_000.0,
    large_transaction_threshold=500_000.0,
    confirmation_threshold_purchase=100_000.0,
    confirmation_threshold_transfer=500_000.0,
    max_products_in_recommendation=5,
    priority_high_risk_products=True,
    show_vip_theme=True,
    vip_service_message="您的专属私行顾问将全程跟进资产配置方案。",
)

TIER_4_DIAMOND = CustomerTier(
    tier_id=4, display_name="钻石客户", level_key="钻石",
    min_assets=5_000_000.0,
    large_transaction_threshold=300_000.0,
    confirmation_threshold_purchase=50_000.0,
    confirmation_threshold_transfer=200_000.0,
    max_products_in_recommendation=4,
    show_vip_theme=True,
    vip_service_message="您的专属理财顾问团队将为您提供个性化资产规划。",
)

TIER_3_PLATINUM = CustomerTier(
    tier_id=3, display_name="白金客户", level_key="白金",
    min_assets=1_000_000.0,
    large_transaction_threshold=200_000.0,
    confirmation_threshold_purchase=30_000.0,
    confirmation_threshold_transfer=100_000.0,
)

TIER_2_GOLD = CustomerTier(
    tier_id=2, display_name="金卡客户", level_key="金卡",
    min_assets=500_000.0,
    large_transaction_threshold=100_000.0,
    confirmation_threshold_purchase=20_000.0,
    confirmation_threshold_transfer=80_000.0,
)

TIER_1_RETAIL = CustomerTier(
    tier_id=1, display_name="零售投资者", level_key="普通",
    min_assets=0.0,
    large_transaction_threshold=50_000.0,
    confirmation_threshold_purchase=10_000.0,
    confirmation_threshold_transfer=50_000.0,
)

ALL_TIERS: tuple[CustomerTier, ...] = (
    TIER_5_PRIVATE_BANKING, TIER_4_DIAMOND, TIER_3_PLATINUM,
    TIER_2_GOLD, TIER_1_RETAIL,
)

TIER_BY_LEVEL: dict[str, CustomerTier] = {tier.level_key: tier for tier in ALL_TIERS}


def classify_tier(
    customer_level: str | None,
    total_assets: float = 0.0,
) -> CustomerTier:
    """Classify a customer into their effective tier."""
    if customer_level and customer_level in TIER_BY_LEVEL:
        tier = TIER_BY_LEVEL[customer_level]
        if total_assets > 0:
            for candidate in ALL_TIERS:
                if candidate.tier_id > tier.tier_id and total_assets >= candidate.min_assets:
                    return candidate
        return tier

    for candidate in ALL_TIERS:
        if total_assets >= candidate.min_assets:
            return candidate
    return TIER_1_RETAIL


def tier_confirmation_thresholds(tier: CustomerTier) -> tuple[float, float]:
    """Return (purchase_threshold, transfer_threshold) for secondary confirm."""
    return (tier.confirmation_threshold_purchase, tier.confirmation_threshold_transfer)


def tier_risk_threshold(tier: CustomerTier, base_threshold: float = 50_000.0) -> float:
    """Return the effective large-transaction threshold for a given tier."""
    return tier.large_transaction_threshold
