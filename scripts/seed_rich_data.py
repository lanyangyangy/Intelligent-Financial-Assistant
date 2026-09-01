"""生成中等规模真实感演示数据（30 客户 / 30 产品 / 持仓 / 交易 / 图谱）。

设计目标（贴合需求文档演示场景）：
  - 客户：30 位，覆盖 C1-C5 全风险等级、普通/黄金/白金/钻石/私行全层级、
    个人 + 企业客户，年龄 24-75 岁、不同职业/收入
  - 产品：30 款，覆盖 R1-R5 全风险、货基/债基/混合/股票/QDII/私募/结构性存款/保险，
    不同期限/起投/流动性
  - 资产：总资产 5 万 - 5000 万，符合客户层级分布
  - 持仓：每客户 4-8 只，与风险等级匹配（C1 客户主要持 R1-R2 等）
  - 交易：300+ 笔，含正常交易 + 触发风控规则的异常样本
    （大额≥5万 / 频繁≥10次7天 / 快进快出 / 夜间 / 整数金额规避 / 拆分）
  - 图谱：先灌 DB 再导入 Neo4j，保证一致性

用法（repo root，project venv）：
    .venv\\Scripts\\python.exe scripts\\seed_rich_data.py
可重复执行（按 username 幂等，不重复插入已有客户/产品）。
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.knowledge_graph import (
    KnowledgeGraphService,
    fund_team_for,
)
from app.models.auth import Role, User
from app.models.profile import (
    CustomerAssetSnapshot,
    CustomerHolding,
    CustomerProfile,
    CustomerRiskAssessment,
    Product,
)
from app.models.trading import Account, Order, Trade
from app.services.auth_service import hash_password

# ---------------------------------------------------------------------------
# 客户模板（30 位）：名字 / 角色 / 层级 / 风险等级 / 资产(万) / 现金比例 / 目标
# ---------------------------------------------------------------------------
TIER_LABEL = {
    "ordinary": "普通客户",
    "gold": "黄金客户",
    "platinum": "白金客户",
    "diamond": "钻石客户",
    "private_bank": "私行客户",
}

CUSTOMERS = [
    # (username, display_name, tier, risk_level, total_asset_wan, cash_pct, goal)
    ("liwei", "李伟", "ordinary", "C1", 8, 0.7, "保值"),
    ("wangfang", "王芳", "ordinary", "C2", 15, 0.5, "稳健增值"),
    ("zhangqiang", "张强", "ordinary", "C3", 25, 0.4, "稳健增值"),
    ("liujing", "刘静", "gold", "C2", 45, 0.45, "稳健增值"),
    ("chenlei", "陈磊", "gold", "C3", 60, 0.35, "平衡增长"),
    ("yangfan", "杨帆", "gold", "C4", 80, 0.3, "成长"),
    ("zhoumin", "周敏", "platinum", "C3", 120, 0.35, "稳健增值"),
    ("wuxin", "吴鑫", "platinum", "C4", 180, 0.25, "成长"),
    ("xuxi", "徐熙", "platinum", "C5", 220, 0.2, "高收益"),
    ("suntao", "孙涛", "diamond", "C4", 350, 0.3, "成长"),
    ("mawei", "马伟", "diamond", "C5", 500, 0.15, "高收益"),
    ("zhuyu", "朱雨", "diamond", "C3", 280, 0.35, "稳健增值"),
    ("heshuang", "何爽", "private_bank", "C4", 800, 0.25, "资产增值"),
    ("gaolei", "高磊", "private_bank", "C5", 1500, 0.15, "高收益"),
    ("linfen", "林芬", "private_bank", "C3", 950, 0.3, "资产保值与传承"),
    ("luoyun", "罗云", "private_bank", "C4", 1200, 0.2, "资产增值"),
    ("caomin", "曹敏", "ordinary", "C2", 12, 0.55, "保值"),
    ("xiehao", "谢浩", "gold", "C3", 55, 0.4, "稳健增值"),
    ("tangyan", "唐艳", "gold", "C2", 40, 0.5, "保值"),
    ("dengchao", "邓超", "platinum", "C4", 150, 0.25, "成长"),
    ("fanrong", "范蓉", "diamond", "C5", 420, 0.2, "高收益"),
    ("penghui", "彭辉", "private_bank", "C5", 2000, 0.12, "高收益"),
    ("xiaoqiang", "肖强", "ordinary", "C1", 6, 0.75, "保值"),
    ("songna", "宋娜", "gold", "C2", 30, 0.5, "稳健增值"),
    ("jiangtao", "姜涛", "platinum", "C4", 200, 0.2, "成长"),
    ("liangyan", "梁燕", "diamond", "C3", 320, 0.35, "稳健增值"),
    ("guohao", "郭浩", "private_bank", "C5", 1800, 0.1, "高收益"),
    ("yinyin", "尹欣", "gold", "C3", 65, 0.4, "平衡增长"),
    ("fangui", "范贵", "platinum", "C5", 260, 0.15, "高收益"),
    ("qinlan", "秦兰", "private_bank", "C4", 1100, 0.22, "资产增值"),
]

# 企业客户 2 位
ENTERPRISE_CUSTOMERS = [
    (
        "huaxin_group",
        "华鑫集团",
        "enterprise_standard",
        "C3",
        3000,
        0.3,
        "企业现金管理",
    ),
    (
        "yuanfang_tech",
        "远芳科技",
        "enterprise_standard",
        "C4",
        5000,
        0.2,
        "企业资产配置",
    ),
]

# ---------------------------------------------------------------------------
# 产品模板（30 款）：名称 / 类型 / 风险R / 期限 / 起投(万) / 流动性 / 目标客户
# ---------------------------------------------------------------------------
PRODUCTS = [
    # R1 低风险
    ("天利货币基金A", "货币基金", "R1", 0, 0.01, "high", "all"),
    ("安盈现金管理", "现金管理", "R1", 0, 0.01, "high", "all"),
    ("稳利结构性存款1号", "结构性存款", "R1", 180, 1, "low", "all"),
    # R2 中低风险
    ("鑫达纯债债券A", "债券基金", "R2", 365, 0.1, "medium", "all"),
    ("安鑫短期理财", "银行理财", "R2", 90, 1, "low", "all"),
    ("增利债券精选", "债券基金", "R2", 730, 0.1, "medium", "all"),
    ("稳健增值计划", "固定收益", "R2", 365, 1, "medium", "all"),
    ("安享季季鑫", "银行理财", "R2", 90, 5, "low", "all"),
    # R3 中风险
    ("平衡配置组合", "平衡基金", "R3", 0, 2, "medium", "all"),
    ("优选固收+", "混合基金", "R3", 365, 1, "medium", "all"),
    ("红利指数增强", "指数基金", "R3", 0, 0.1, "medium", "all"),
    ("安联稳健平衡", "混合基金", "R3", 730, 2, "medium", "all"),
    ("沪深300指数增强", "指数基金", "R3", 0, 0.1, "medium", "all"),
    # R4 中高风险
    ("成长精选组合", "股票基金", "R4", 0, 5, "medium", "all"),
    ("科技先锋混合", "混合基金", "R4", 0, 1, "medium", "all"),
    ("医疗健康主题基金", "股票基金", "R4", 0, 0.1, "medium", "all"),
    ("新能源产业基金", "股票基金", "R4", 0, 0.1, "medium", "all"),
    ("港股通精选", "QDII基金", "R4", 0, 5, "low", "all"),
    # R5 高风险
    ("私行进取策略", "私募股权", "R5", 0, 50, "low", "private_bank"),
    ("全球配置私募", "私募基金", "R5", 0, 100, "low", "private_bank"),
    ("量化对冲私募", "私募基金", "R5", 0, 50, "low", "private_bank"),
    # 补充各类
    ("安泰养老目标", "养老基金", "R3", 3650, 0.1, "low", "all"),
    ("教育金储蓄计划", "储蓄保险", "R2", 3650, 1, "low", "all"),
    ("增额终身寿险", "保险产品", "R2", 7300, 2, "low", "all"),
    ("年金保险计划", "保险产品", "R3", 7300, 5, "low", "all"),
    ("美元债精选", "QDII债券", "R3", 365, 1, "low", "all"),
    ("国债逆回购优选", "现金管理", "R1", 0, 0.01, "high", "all"),
    ("黄金ETF联接", "商品基金", "R4", 0, 0.1, "medium", "all"),
    ("大宗商品CTA", "私募基金", "R5", 0, 100, "low", "private_bank"),
    ("企业现金管理计划", "现金管理", "R2", 0, 10, "high", "enterprise"),
]

# 产品 → 行业（图谱）
PRODUCT_INDUSTRY = {
    "天利货币基金A": "货币市场",
    "安盈现金管理": "货币市场",
    "稳利结构性存款1号": "固定收益",
    "鑫达纯债债券A": "债券市场",
    "安鑫短期理财": "固定收益",
    "增利债券精选": "债券市场",
    "稳健增值计划": "债券市场",
    "安享季季鑫": "固定收益",
    "平衡配置组合": "公募基金",
    "优选固收+": "债券市场",
    "红利指数增强": "权益市场",
    "安联稳健平衡": "公募基金",
    "沪深300指数增强": "权益市场",
    "成长精选组合": "权益市场",
    "科技先锋混合": "权益市场",
    "医疗健康主题基金": "权益市场",
    "新能源产业基金": "权益市场",
    "港股通精选": "权益市场",
    "私行进取策略": "私募股权",
    "全球配置私募": "私募股权",
    "量化对冲私募": "私募股权",
    "安泰养老目标": "公募基金",
    "教育金储蓄计划": "保险",
    "增额终身寿险": "保险",
    "年金保险计划": "保险",
    "美元债精选": "债券市场",
    "国债逆回购优选": "货币市场",
    "黄金ETF联接": "商品",
    "大宗商品CTA": "私募股权",
    "企业现金管理计划": "货币市场",
}

# 客户风险等级 → 允许的最高产品风险（R 档）
RISK_ALLOW = {"C1": 2, "C2": 3, "C3": 4, "C4": 5, "C5": 5}

# 交易异常样本模板
# (类型, 金额区间(万), 说明)
ABNORMAL_TRADES = [
    ("large", (5, 50), "单笔大额"),
    ("frequent", (0.1, 3), "频繁交易"),
    ("quick_inout", (1, 20), "快进快出"),
    ("night", (1, 10), "夜间交易"),
    ("integer", (3, 30), "整数金额规避"),
    ("split", (4, 8), "拆分交易"),
]


def utcnow() -> datetime:
    return datetime.now(UTC)


def rand_amount(low_wan: float, high_wan: float) -> Decimal:
    return Decimal(str(round(random.uniform(low_wan, high_wan) * 10000, 2)))


def risk_label(risk: str) -> str:
    return risk.upper().replace("C", "R")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
async def main() -> None:
    settings = get_settings()
    db = Database(settings)
    random.seed(20260804)  # 可复现

    async with db.session_factory() as session:
        # 角色
        retail_role = (
            await session.execute(select(Role).where(Role.code == "retail_investor"))
        ).scalar_one_or_none()
        high_role = (
            await session.execute(
                select(Role).where(Role.code == "high_net_worth_customer")
            )
        ).scalar_one_or_none()
        enter_role = (
            await session.execute(select(Role).where(Role.code == "retail_investor"))
        ).scalar_one_or_none()

        # ---- 产品 ----
        product_map: dict[str, Product] = {}
        for p in PRODUCTS:
            name, ptype, rlevel, term, min_amt, liquidity, target = p
            existing = (
                await session.execute(select(Product).where(Product.name == name))
            ).scalar_one_or_none()
            if existing is None:
                existing = Product(
                    id=str(uuid4()),
                    name=name,
                    product_type=ptype,
                    risk_level=rlevel,
                    term_days=term,
                    minimum_amount=float(min_amt * 10000),
                    liquidity=liquidity,
                    description=f"{ptype}（{rlevel}级）",
                    target_customer_type=target,
                    status="active",
                    source_type="rich_mock",
                )
                session.add(existing)
                await session.flush()
            product_map[name] = existing
        print(f"products: {len(product_map)}")

        # ---- 客户 ----
        all_customers = CUSTOMERS + ENTERPRISE_CUSTOMERS
        for item in all_customers:
            username, display_name, tier, risk_level, asset_wan, cash_pct, goal = item
            role = (
                high_role
                if tier in {"private_bank", "diamond"}
                else (
                    enter_role
                    if username in {"huaxin_group", "yuanfang_tech"}
                    else retail_role
                )
            )
            user = (
                await session.execute(
                    select(User)
                    .options(selectinload(User.roles))
                    .where(User.username == username)
                )
            ).scalar_one_or_none()
            if user is None:
                user = User(
                    username=username,
                    password_hash=hash_password("Demo@2026Customer"),
                    display_name=display_name,
                    status="active",
                    is_super_admin=False,
                )
                session.add(user)
                await session.flush()
            await session.refresh(user, attribute_names=["roles"])
            if role is not None and role not in user.roles:
                user.roles.append(role)

            total_asset = Decimal(str(asset_wan * 10000))
            cash = (total_asset * Decimal(str(cash_pct))).quantize(Decimal("0.01"))
            investable = (total_asset - cash).quantize(Decimal("0.01"))
            customer_type = (
                "enterprise"
                if username in {"huaxin_group", "yuanfang_tech"}
                else "individual"
            )

            # Profile
            profile = (
                await session.execute(
                    select(CustomerProfile).where(CustomerProfile.user_id == user.id)
                )
            ).scalar_one_or_none()
            if profile is None:
                profile = CustomerProfile(id=str(uuid4()), user_id=user.id)
                session.add(profile)
            profile.age = random.randint(24, 75)
            profile.occupation = random.choice(
                [
                    "公务员",
                    "事业单位",
                    "国企员工",
                    "医生",
                    "律师",
                    "工程师",
                    "企业员工",
                    "个体经营",
                    "退休",
                ]
            )
            profile.education_level = random.choice(
                ["HIGH_SCHOOL_OR_BELOW", "COLLEGE", "BACHELOR", "MASTER_OR_ABOVE"]
            )
            profile.annual_income = random.uniform(8, 200) * 10000
            profile.customer_tier = tier
            profile.customer_type = customer_type
            profile.investment_goal = goal
            profile.liquidity_preference = random.choice(["high", "medium", "low"])
            profile.investment_experience_years = random.randint(1, 20)
            profile.investment_horizon_years = random.randint(1, 10)
            profile.risk_level = risk_level
            profile.risk_score = random.randint(20, 100)

            # Account
            account = (
                await session.execute(select(Account).where(Account.user_id == user.id))
            ).scalar_one_or_none()
            if account is None:
                account = Account(
                    id=str(uuid4()),
                    user_id=user.id,
                    account_no=f"AC{user.id:08d}",
                    currency="CNY",
                    status="active",
                )
                session.add(account)
            account.available_balance = cash
            account.frozen_balance = Decimal("0")

            # Asset snapshot
            asset = (
                (
                    await session.execute(
                        select(CustomerAssetSnapshot)
                        .where(CustomerAssetSnapshot.user_id == user.id)
                        .order_by(
                            CustomerAssetSnapshot.snapshot_time.desc().nullslast(),
                            CustomerAssetSnapshot.id.desc(),
                        )
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if asset is None:
                asset = CustomerAssetSnapshot(id=str(uuid4()), user_id=user.id)
                session.add(asset)
            asset.total_asset = total_asset
            asset.cash_balance = cash
            asset.investable_asset = investable
            asset.liability = Decimal("0")
            asset.net_asset = total_asset
            asset.source_type = "rich_mock"

            # Risk assessment（正式风评）
            assessment = (
                (
                    await session.execute(
                        select(CustomerRiskAssessment)
                        .where(
                            CustomerRiskAssessment.user_id == user.id,
                            CustomerRiskAssessment.status == "active",
                        )
                        .order_by(CustomerRiskAssessment.assessed_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if assessment is None:
                assessment = CustomerRiskAssessment(
                    id=str(uuid4()),
                    user_id=user.id,
                    risk_level=risk_level,
                    score=random.randint(20, 100),
                    answers_json=json.dumps({"q1": "A", "q2": "B"}),
                    status="active",
                    source_type="questionnaire",
                    assessed_at=utcnow(),
                    expires_at=utcnow() + timedelta(days=365),
                )
                session.add(assessment)
            else:
                assessment.risk_level = risk_level

            # ---- 持仓（与风险等级匹配）----
            max_r = RISK_ALLOW[risk_level]
            eligible = [
                p
                for p in product_map.values()
                if risk_label(p.risk_level) in [f"R{i}" for i in range(1, max_r + 1)]
                and p.target_customer_type in {"all", customer_type}
            ]
            if not eligible:
                eligible = list(product_map.values())
            n_holdings = random.randint(4, 8)
            random.shuffle(eligible)
            chosen = eligible[:n_holdings]
            holding_budget = investable
            for pi, p in enumerate(chosen):
                share = (1 / n_holdings) * random.uniform(0.7, 1.3)
                amount = (holding_budget * Decimal(str(share))).quantize(
                    Decimal("0.01")
                )
                holding = (
                    (
                        await session.execute(
                            select(CustomerHolding).where(
                                CustomerHolding.user_id == user.id,
                                CustomerHolding.product_id == p.id,
                                CustomerHolding.status == "active",
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                pnl_pct = random.uniform(-8, 15) / 100
                cost = amount
                market = (amount * (1 + Decimal(str(pnl_pct)))).quantize(
                    Decimal("0.01")
                )
                if holding is None:
                    holding = CustomerHolding(
                        id=str(uuid4()),
                        user_id=user.id,
                        product_id=p.id,
                        quantity=amount,
                        cost_amount=cost,
                        market_value=market,
                        profit_loss=(market - cost),
                        holding_days=random.randint(10, 400),
                        status="active",
                    )
                    session.add(holding)
                else:
                    holding.market_value = market
                    holding.profit_loss = market - cost
            print(
                f"customer: {username} ({display_name}) tier={tier} risk={risk_level} asset={total_asset}"
            )

        await session.commit()
        print("=== 客户/产品/持仓/资产已生成 ===")

        # ---- 交易（含风控异常样本）----
        # 幂等保护：已生成过 mock 交易（order_no 前缀 MO）则整段跳过，
        # 避免 seed_all --force / AUTO_SEED=1 重复执行时交易无限堆积。
        existing_mock_order = (
            await session.execute(
                select(Order.id).where(Order.order_no.like("MO%")).limit(1)
            )
        ).scalar_one_or_none()
        if existing_mock_order is not None:
            print("=== 交易已存在（MO* mock 订单），跳过交易生成 ===")
            existing_mock_orders = True
        else:
            existing_mock_orders = False

        customers_db = list(
            (
                await session.execute(
                    select(User).where(User.username.in_([c[0] for c in all_customers]))
                )
            )
            .scalars()
            .all()
        )
        trades_created = 0
        for idx, user in enumerate(customers_db):
            if existing_mock_orders:
                break
            account = (
                await session.execute(select(Account).where(Account.user_id == user.id))
            ).scalar_one_or_none()
            if account is None:
                continue
            holdings = list(
                (
                    await session.execute(
                        select(CustomerHolding).where(
                            CustomerHolding.user_id == user.id,
                            CustomerHolding.status == "active",
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not holdings:
                continue
            # 每客户 8-15 笔正常交易
            n_trades = random.randint(8, 15)
            for t in range(n_trades):
                holding = random.choice(holdings)
                amount = rand_amount(0.5, 15)
                order = Order(
                    id=str(uuid4()),
                    order_no=f"MO{uuid4().hex[:14].upper()}",
                    user_id=user.id,
                    account_id=account.id,
                    product_id=holding.product_id,
                    amount=amount,
                    quantity=amount,
                    status="executed",
                    side="buy",
                    created_at=utcnow() - timedelta(days=random.randint(1, 180)),
                )
                session.add(order)
                await session.flush()
                trade = Trade(
                    id=str(uuid4()),
                    trade_no=f"MT{uuid4().hex[:14].upper()}",
                    order_id=order.id,
                    user_id=user.id,
                    product_id=holding.product_id,
                    amount=amount,
                    quantity=amount,
                    executed_at=utcnow() - timedelta(days=random.randint(1, 180)),
                )
                session.add(trade)
                trades_created += 1
            # 风控异常样本：每客户 0-3 笔异常交易（大额/频繁/快进快出/夜间等）
            n_abnormal = random.randint(0, 3)
            for a in range(n_abnormal):
                atype, range_wan, _ = random.choice(ABNORMAL_TRADES)
                holding = random.choice(holdings)
                amount = rand_amount(*range_wan)
                executed_at = utcnow() - timedelta(days=random.randint(1, 90))
                order = Order(
                    id=str(uuid4()),
                    order_no=f"MO{uuid4().hex[:14].upper()}",
                    user_id=user.id,
                    account_id=account.id,
                    product_id=holding.product_id,
                    amount=amount,
                    quantity=amount,
                    status="executed",
                    side="buy",
                    created_at=executed_at,
                    review_note=f"mock:{atype}",
                )
                session.add(order)
                await session.flush()
                trade = Trade(
                    id=str(uuid4()),
                    trade_no=f"MT{uuid4().hex[:14].upper()}",
                    order_id=order.id,
                    user_id=user.id,
                    product_id=holding.product_id,
                    amount=amount,
                    quantity=amount,
                    executed_at=executed_at,
                )
                session.add(trade)
                trades_created += 1
        await session.commit()
        print(f"=== 交易已生成: {trades_created} 笔（含风控异常样本）===")

    # ---- 图谱同步（从 DB 导入 Neo4j）----
    graph = KnowledgeGraphService(settings)
    await graph.connect()
    if graph.available:
        async with db.session_factory() as session:
            products = list(
                (
                    await session.execute(
                        select(Product).where(Product.status == "active")
                    )
                )
                .scalars()
                .all()
            )
            product_rows = [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "product_type": p.product_type,
                    "risk_level": str(p.risk_level).upper().replace("C", "R"),
                    "fund_manager": fund_team_for(p.product_type, p.name),
                }
                for p in products
            ]
            customers = list(
                (await session.execute(select(CustomerProfile))).scalars().all()
            )
            customer_rows = [
                {"id": c.user_id, "risk_level": (c.risk_level or "C1").upper()}
                for c in customers
            ]
            holdings = list(
                (
                    await session.execute(
                        select(CustomerHolding).where(
                            CustomerHolding.status == "active"
                        )
                    )
                )
                .scalars()
                .all()
            )
            holding_rows = [
                {"customer_id": h.user_id, "product_id": str(h.product_id)}
                for h in holdings
            ]
            industries = [
                {
                    "product_id": str(p.id),
                    "industry": PRODUCT_INDUSTRY.get(p.name, "其他"),
                }
                for p in products
            ]
        await graph.import_products(product_rows)
        await graph.import_customers(customer_rows)
        await graph.import_holdings(holding_rows)
        await graph.import_industries(industries)
        stats = await graph.get_graph_stats()
        print(
            f"Neo4j 同步完成: 节点={stats['total_nodes']} 关系={stats['total_relations']}"
        )
    else:
        print("NEO4J UNAVAILABLE - 图谱导入跳过")
    await graph.close()
    await db.dispose()
    print("done. rich mock data seeded.")


if __name__ == "__main__":
    asyncio.run(main())
