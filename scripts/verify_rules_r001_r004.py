"""验证 F4.1 规则引擎对 R001-R004 的匹配能力。

用户规则：
- R001：单笔交易金额≥5万元 → 大额现金交易
- R002：同日累计交易金额≥20万元
- R003：7天内交易次数≥10次 → 频繁交易
- R004：资金快进快出（24小时内申购并赎回同一产品）

对应实现：
- R001 → RW-001 _rule_rw001（单笔≥5万）
- R002 → RW-021 _rule_rw021（单日累计≥20万，不限时段）
- R003 → RW-022 _rule_rw022（7天内交易≥10次）
- R004 → RW-003 _rule_rw003（24h 同产品买后卖）
"""

import sys
from datetime import UTC, datetime, timedelta
from uuid import uuid4

sys.path.insert(0, ".")

from app.agents.risk_agent import RiskMonitorAgent  # noqa: E402
from app.models.trading import Order, Trade  # noqa: E402


def _trade(amount: float, ts: datetime, uid: str = "u1") -> Trade:
    t = Trade(
        id=str(uuid4()),
        trade_no=f"T{str(uuid4().hex[:8])}",
        order_id=str(uuid4()),
        user_id=uid,
        product_id="p1",
        amount=amount,
        quantity=1,
        executed_at=ts,
    )
    return t


def _order(
    side: str, amount: float, ts: datetime, pid: str = "p1", status: str = "executed"
) -> Order:
    return Order(
        id=str(uuid4()),
        order_no=f"O{str(uuid4().hex[:8])}",
        user_id="u1",
        account_id="a1",
        product_id=pid,
        amount=amount,
        quantity=1,
        status=status,
        side=side,
        created_at=ts,
        updated_at=ts,
    )


now = datetime.now(UTC)

# 绕过 AgentBase.__init__（需要 settings/LLM），直接创建实例调用纯规则方法
agent = RiskMonitorAgent.__new__(RiskMonitorAgent)

print("=" * 60)
print("R001 单笔≥5万 → RW-001")
trades = [_trade(60_000, now)]
print("  单笔6万:", "✅ 命中" if agent._rule_rw001(trades, []) else "❌ 未命中")
trades = [_trade(30_000, now)]
print("  单笔3万:", "✅ 命中" if agent._rule_rw001(trades, []) else "❌ 未命中(正确)")

print("=" * 60)
print("R002 同日累计≥20万（普通时段）→ RW-021")
# 同一天 3 笔各 8 万 = 24 万（普通时段 10/11/14 点）
trades = [
    _trade(80_000, now.replace(hour=10)),
    _trade(80_000, now.replace(hour=11)),
    _trade(80_000, now.replace(hour=14)),
]
daily = [{"date": now.date().isoformat(), "count": 3, "amount": 240_000.0}]
hit_rw021 = agent._rule_rw021(trades, [])
print(f"  同日24万(普通时段): rw021={hit_rw021}")
print("  → 普通时段同日累计≥20万", "✅ 命中" if hit_rw021 else "❌ 未命中")

# 同日累计 15 万（<20万）不应命中
trades_15 = [
    _trade(50_000, now.replace(hour=10)),
    _trade(50_000, now.replace(hour=11)),
    _trade(50_000, now.replace(hour=14)),
]
print(
    "  同日15万:", "✅ 命中" if agent._rule_rw021(trades_15, []) else "❌ 未命中(正确)"
)

# 待审核订单同样计入：2 笔订单各 11 万同日 = 22 万
orders_22 = [
    _order("buy", 110_000, now.replace(hour=10)),
    _order("buy", 110_000, now.replace(hour=15)),
]
print(
    "  待审核订单同日22万:",
    "✅ 命中" if agent._rule_rw021([], orders_22) else "❌ 未命中",
)

print("=" * 60)
print("R003 7天≥10次频繁交易 → RW-022")
# 10 笔小额（各 5000，累计5万）
small = [_trade(5_000, now - timedelta(days=i % 6, hours=i)) for i in range(10)]
hit_rw022 = agent._rule_rw022(small, [])
print(f"  7天10笔×5000: rw022={hit_rw022}")
print("  → 10次频繁交易", "✅ 命中" if hit_rw022 else "❌ 未命中")

# 9 笔不应命中
nine = [_trade(5_000, now - timedelta(days=i % 6, hours=i)) for i in range(9)]
print("  7天9笔:", "✅ 命中" if agent._rule_rw022(nine, []) else "❌ 未命中(正确)")

# 待审核订单同样计入：10 笔订单
orders_10 = [
    _order("buy", 5_000, now - timedelta(days=i % 6, hours=i)) for i in range(10)
]
print(
    "  7天10笔订单:",
    "✅ 命中" if agent._rule_rw022([], orders_10) else "❌ 未命中",
)

print("=" * 60)
print("R004 24h 快进快出 → RW-003")
orders = [
    _order("buy", 50_000, now - timedelta(hours=6)),
    _order("sell", 50_000, now - timedelta(hours=2)),
]
print("  24h内买后卖:", "✅ 命中" if agent._rule_rw003(orders) else "❌ 未命中")
# 间隔明确 >24h：3 天前买、1.5 天前卖
orders2 = [
    _order("buy", 50_000, now - timedelta(days=3)),
    _order("sell", 50_000, now - timedelta(hours=36)),
]
print("  超24h(36h):", "✅ 命中" if agent._rule_rw003(orders2) else "❌ 未命中(正确)")

print("=" * 60)
print("完整 evaluate 全量规则（含 RW-021/RW-022）")
merged = (
    [_trade(80_000, now.replace(hour=10))]
    + [_trade(80_000, now.replace(hour=11))]
    + [_trade(80_000, now.replace(hour=14))]
    + [_trade(5_000, now - timedelta(days=i % 6, hours=i)) for i in range(10)]
)
merged_daily = [{"date": now.date().isoformat(), "count": 13, "amount": 290_000.0}]
hits = agent.evaluate(merged, orders, merged_daily, 500_000)
print("  命中:", hits if hits else "（无）")
print("  R002 覆盖:", any("RW-021" in h for h in hits))
print("  R003 覆盖:", any("RW-022" in h for h in hits))
print("  R004 覆盖:", any("RW-003" in h for h in hits))
print("=" * 60)
