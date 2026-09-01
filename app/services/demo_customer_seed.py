import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import Database
from app.models.auth import Role, User
from app.models.profile import (
    CustomerAssetSnapshot,
    CustomerHolding,
    CustomerProfile,
    CustomerRiskAssessment,
    Product,
)
from app.models.trading import Account
from app.services.auth_seed import DEMO_ACCOUNT_PASSWORDS
from app.services.auth_service import hash_password
from app.services.profile_calculation_service import ProfileCalculationService

DEMO_CUSTOMERS = (
    {
        "username": "high_net_worth_demo",
        "display_name": "高净值客户演示",
        "role_code": "high_net_worth_customer",
        "customer_type": "individual",
        "customer_tier": "private_bank",
        "asset": Decimal("12000000"),
        "cash": Decimal("4000000"),
        "investable": Decimal("12000000"),
        "goal": "资产保值与传承",
    },
    {
        "username": "retail_investor_demo",
        "display_name": "零售投资者演示",
        "role_code": "retail_investor",
        "customer_type": "individual",
        "customer_tier": "ordinary",
        "asset": Decimal("800000"),
        "cash": Decimal("200000"),
        "investable": Decimal("600000"),
        "goal": "稳健增值",
    },
    {
        "username": "minor_investor_demo",
        "display_name": "未成年投资者熔断测试",
        "role_code": "retail_investor",
        "customer_type": "individual",
        "customer_tier": "ordinary",
        "age": 17,
        "occupation": "student",
        "education_level": "HIGH_SCHOOL_OR_BELOW",
        "annual_income": Decimal("120000"),
        "investment_experience_years": 1,
        "region": "上海",
        "investment_horizon_years": 3,
        "asset": Decimal("150000"),
        "cash": Decimal("40000"),
        "investable": Decimal("110000"),
        "goal": "稳健增值",
        "risk_level": "C3",
        "risk_score": 55,
        "risk_status": "active",
        "test_scenario": "UNDER_AGE",
    },
    {
        "username": "senior_investor_demo",
        "display_name": "高龄投资者熔断测试",
        "role_code": "retail_investor",
        "customer_type": "individual",
        "customer_tier": "gold",
        "age": 81,
        "occupation": "retired",
        "education_level": "BACHELOR",
        "annual_income": Decimal("300000"),
        "investment_experience_years": 12,
        "region": "北京",
        "investment_horizon_years": 5,
        "asset": Decimal("2000000"),
        "cash": Decimal("500000"),
        "investable": Decimal("1500000"),
        "goal": "资产保值与传承",
        "risk_level": "C4",
        "risk_score": 70,
        "risk_status": "active",
        "test_scenario": "AGE_OVER_80",
    },
    {
        "username": "expired_assessment_demo",
        "display_name": "风评过期熔断测试",
        "role_code": "retail_investor",
        "customer_type": "individual",
        "customer_tier": "ordinary",
        "age": 45,
        "occupation": "engineer",
        "education_level": "BACHELOR",
        "annual_income": Decimal("360000"),
        "investment_experience_years": 8,
        "region": "深圳",
        "investment_horizon_years": 5,
        "asset": Decimal("800000"),
        "cash": Decimal("200000"),
        "investable": Decimal("600000"),
        "goal": "稳健增值",
        "risk_level": "C4",
        "risk_score": 68,
        "risk_status": "active",
        "test_scenario": "ASSESSMENT_EXPIRED",
    },
)

# 为数据分析演示提供多个产品维度的可解释收益样本；仅补齐缺失持仓，
# 不覆盖用户已有的真实或演示交易数据。
DEMO_RETURN_HOLDINGS = (
    ("现金管理保本计划", "100000", "101500", "1500"),
    ("平衡配置组合", "150000", "159000", "9000"),
    ("成长精选组合", "150000", "165000", "15000"),
    ("私行进取策略", "100000", "112000", "12000"),
)


async def ensure_demo_customer_profiles(database: Database) -> None:
    async with database.session_factory() as session:
        product = (
            await session.execute(select(Product).where(Product.name == "稳健增值计划"))
        ).scalar_one_or_none()
        if product is None:
            return
        for item in DEMO_CUSTOMERS:
            role = (
                await session.execute(
                    select(Role).where(Role.code == item["role_code"])
                )
            ).scalar_one_or_none()
            if role is None:
                continue
            user = (
                await session.execute(
                    select(User)
                    .options(selectinload(User.roles))
                    .where(User.username == item["username"])
                )
            ).scalar_one_or_none()
            if user is None:
                user = User(
                    username=item["username"],
                    password_hash=hash_password(
                        DEMO_ACCOUNT_PASSWORDS.get(
                            item["username"], "Demo@2026RetailInvestor"
                        )
                    ),
                    display_name=item["display_name"],
                    status="active",
                    is_super_admin=False,
                )
                session.add(user)
                await session.flush()
            await session.refresh(user, attribute_names=["roles"])
            if role not in user.roles:
                user.roles.append(role)
            profile = (
                await session.execute(
                    select(CustomerProfile).where(CustomerProfile.user_id == user.id)
                )
            ).scalar_one_or_none()
            if profile is None:
                profile = CustomerProfile(id=str(uuid4()), user_id=user.id)
                session.add(profile)
            profile.customer_type = item["customer_type"]
            profile.customer_tier = item["customer_tier"]
            profile.investment_goal = item["goal"]
            if item.get("test_scenario"):
                profile.age = item["age"]
                profile.occupation = item["occupation"]
                profile.education_level = item["education_level"]
                profile.annual_income = item["annual_income"]
                profile.investment_experience_years = item[
                    "investment_experience_years"
                ]
                profile.region = item["region"]
                profile.investment_horizon_years = item["investment_horizon_years"]
                profile.liquidity_preference = "medium"
            else:
                profile.investment_horizon = "5年以上"
                profile.liquidity_preference = "中等"
            profile.source_type = "synthetic_demo"
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
            account.available_balance = item["cash"]
            account.frozen_balance = Decimal("0")
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
            asset.total_asset = item["asset"]
            asset.cash_balance = item["cash"]
            asset.investable_asset = item["investable"]
            asset.liability = Decimal("0")
            asset.net_asset = item["asset"]
            asset.source_type = "synthetic_demo"
            holding = (
                (
                    await session.execute(
                        select(CustomerHolding)
                        .where(
                            CustomerHolding.user_id == user.id,
                            CustomerHolding.product_id == product.id,
                            CustomerHolding.status == "active",
                        )
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if holding is None:
                holding = CustomerHolding(
                    id=str(uuid4()),
                    user_id=user.id,
                    product_id=product.id,
                    quantity=item["asset"] - item["cash"],
                    cost_amount=item["asset"] - item["cash"],
                    market_value=item["asset"] - item["cash"],
                    profit_loss=Decimal("0"),
                    holding_days=90,
                    status="active",
                )
                session.add(holding)
        retail_user = (
            await session.execute(
                select(User).where(User.username == "retail_investor_demo")
            )
        ).scalar_one_or_none()
        if retail_user is not None:
            for (
                product_name,
                cost_amount,
                market_value,
                profit_loss,
            ) in DEMO_RETURN_HOLDINGS:
                return_product = (
                    await session.execute(
                        select(Product).where(Product.name == product_name)
                    )
                ).scalar_one_or_none()
                if return_product is None:
                    continue
                existing = (
                    (
                        await session.execute(
                            select(CustomerHolding)
                            .where(
                                CustomerHolding.user_id == retail_user.id,
                                CustomerHolding.product_id == return_product.id,
                                CustomerHolding.status == "active",
                            )
                            .limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
                if existing is None:
                    amount = Decimal(cost_amount)
                    session.add(
                        CustomerHolding(
                            id=str(uuid4()),
                            user_id=retail_user.id,
                            product_id=return_product.id,
                            quantity=amount,
                            cost_amount=amount,
                            market_value=Decimal(market_value),
                            profit_loss=Decimal(profit_loss),
                            holding_days=90,
                            status="active",
                        )
                    )
        demo_risks = {
            "high_net_worth_demo": {"level": "C3", "score": 50},
            "retail_investor_demo": {"level": "C2", "score": 35},
            **{
                item["username"]: {
                    "level": item["risk_level"],
                    "score": item["risk_score"],
                    "status": item["risk_status"],
                    "scenario": item["test_scenario"],
                }
                for item in DEMO_CUSTOMERS
                if item.get("test_scenario")
            },
        }
        calculate_test_profiles: list[str] = []
        test_customer_by_username = {
            item["username"]: item
            for item in DEMO_CUSTOMERS
            if item.get("test_scenario")
        }
        expired_user_id: str | None = None
        now = datetime.now(UTC)
        for username, risk_config in demo_risks.items():
            demo_user = (
                await session.execute(select(User).where(User.username == username))
            ).scalar_one_or_none()
            if demo_user is None:
                continue
            risk = (
                (
                    await session.execute(
                        select(CustomerRiskAssessment)
                        .where(CustomerRiskAssessment.user_id == demo_user.id)
                        .order_by(CustomerRiskAssessment.assessed_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            scenario = risk_config.get("scenario")
            scenario_customer = test_customer_by_username.get(username, {})
            expires_at = (
                now - timedelta(days=1)
                if scenario == "ASSESSMENT_EXPIRED"
                else now + timedelta(days=365)
            )
            answers = {f"q{i}": "B" for i in range(1, 17)}
            answers.update(
                {
                    "investment_experience_years": scenario_customer.get(
                        "investment_experience_years", 8
                    ),
                    "max_loss_tolerance": 20,
                    "investment_horizon_years": 5,
                    "liquidity_need": "medium",
                    "investment_goal": "balanced",
                    "risk_willingness": 50,
                }
            )
            if risk is None:
                risk = CustomerRiskAssessment(id=str(uuid4()), user_id=demo_user.id)
                session.add(risk)
            risk.risk_level = risk_config["level"]
            risk.score = risk_config["score"]
            risk.answers_json = json.dumps(answers, ensure_ascii=False)
            risk.status = risk_config.get("status", "active")
            risk.source_type = "questionnaire" if scenario else "synthetic_demo"
            risk.assessed_at = now
            risk.expires_at = expires_at
            if scenario:
                calculate_test_profiles.append(demo_user.id)
                if scenario == "ASSESSMENT_EXPIRED":
                    expired_user_id = demo_user.id
        await session.flush()
        for user_id in calculate_test_profiles:
            profile = (
                await session.execute(
                    select(CustomerProfile).where(CustomerProfile.user_id == user_id)
                )
            ).scalar_one()
            expected_status = (
                "EXPIRED" if profile.user_id == expired_user_id else "NEEDS_REVIEW"
            )
            if profile.profile_status != expected_status:
                await ProfileCalculationService().calculate(session, user_id)
        await session.commit()
