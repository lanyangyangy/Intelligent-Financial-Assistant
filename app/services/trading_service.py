from decimal import Decimal
from uuid import uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.security.roles import CUSTOMER_ROLE_CODES
from app.models.auth import User
from app.models.profile import (
    CustomerHolding,
    CustomerProfile,
    CustomerRiskAssessment,
    Product,
)
from app.models.trading import Account, Order, OrderStatusHistory, Trade
from app.services.asset_summary_service import AssetSummaryService


class TradingError(ValueError):
    pass


class TradingService:
    @staticmethod
    def _customer_role_filter():
        return or_(*(User.roles.any(code=code) for code in CUSTOMER_ROLE_CODES))

    async def get_or_create_account(
        self, session: AsyncSession, user_id: str
    ) -> Account:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        customer = (
            await session.execute(
                select(
                    exists().where(
                        User.id == user_id,
                        self._customer_role_filter(),
                    )
                )
            )
        ).scalar()
        if user is None or not customer:
            raise TradingError("only customer accounts can trade")
        account = (
            await session.execute(select(Account).where(Account.user_id == user_id))
        ).scalar_one_or_none()
        if account is None:
            account = Account(
                id=str(uuid4()),
                user_id=user_id,
                account_no=f"AC{user_id}",
                available_balance=Decimal("200000.00"),
            )
            session.add(account)
            await session.flush()
        return account

    async def _order_response(self, session, order):
        product = (
            await session.execute(select(Product).where(Product.id == order.product_id))
        ).scalar_one_or_none()
        return order, product.name if product else None

    async def create_order(
        self,
        session: AsyncSession,
        user: User,
        product_id: str,
        amount: Decimal,
        idempotency_key: str | None = None,
        operator: User | None = None,
    ):
        if idempotency_key:
            existing = (
                (
                    await session.execute(
                        select(Order)
                        .where(
                            Order.user_id == user.id,
                            Order.idempotency_key == idempotency_key,
                        )
                        .order_by(Order.created_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if existing is not None:
                product = (
                    await session.execute(
                        select(Product).where(Product.id == existing.product_id)
                    )
                ).scalar_one_or_none()
                return existing, product.name if product else None
        customer = (
            await session.execute(
                select(exists().where(User.id == user.id, self._customer_role_filter()))
            )
        ).scalar()
        if not customer:
            raise TradingError("only customer accounts can trade")
        product = (
            await session.execute(
                select(Product).where(
                    Product.id == product_id, Product.status == "active"
                )
            )
        ).scalar_one_or_none()
        if product is None:
            raise TradingError("product unavailable")
        if amount < Decimal(str(product.minimum_amount)):
            raise TradingError(f"minimum amount is {product.minimum_amount}")

        # F2.1 硬性门槛熔断（客户需求 2026-08-04）：
        #   - 年龄 < 18：不允许购买任何产品
        #   - 年龄 > 80：可购 R1/R2；R3 需人工复核（不可直接下单）；
        #     R4 及以上不允许购买
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user.id)
            )
        ).scalar_one_or_none()
        if profile is not None and profile.age is not None:
            product_raw = str(product.risk_level).upper().replace("C", "R")
            product_order = {"R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5}.get(
                product_raw, 1
            )
            if profile.age < 18:
                raise TradingError(
                    f"未满 18 周岁（当前 {profile.age} 岁），依法不允许购买理财产品"
                )
            if profile.age > 80:
                if product_order > 3:
                    raise TradingError("年龄超过 80 岁，不允许购买 R4 及以上产品")
                if product_order == 3:
                    raise TradingError(
                        "年龄超过 80 岁，R3 产品需人工复核后方可购买，当前不可直接下单"
                    )
        account = await self.get_or_create_account(session, user.id)
        if account.status != "active":
            raise TradingError("account inactive")
        if account.available_balance < amount:
            raise TradingError("insufficient available balance")
        order = Order(
            id=str(uuid4()),
            order_no=f"O{uuid4().hex[:16].upper()}",
            user_id=user.id,
            account_id=account.id,
            product_id=product.id,
            amount=amount,
            quantity=amount,
            status="pending_confirmation",
            idempotency_key=idempotency_key,
        )
        session.add(order)
        await session.flush()
        operator_id = operator.id if operator is not None else user.id
        note = (
            "staff created order for customer"
            if operator is not None
            else "customer created order"
        )
        session.add(
            OrderStatusHistory(
                id=str(uuid4()),
                order_id=order.id,
                from_status=None,
                to_status=order.status,
                operator_user_id=operator_id,
                note=note,
            )
        )
        return order, product.name

    async def transition(self, session, order, to_status, operator_id, note=""):
        old = order.status
        order.status = to_status
        if note:
            order.review_note = note
        session.add(
            OrderStatusHistory(
                id=str(uuid4()),
                order_id=order.id,
                from_status=old,
                to_status=to_status,
                operator_user_id=operator_id,
                note=note,
            )
        )
        await session.flush()

    async def confirm_order(
        self, session, user: User, order_id: str, operator: User | None = None
    ):
        order = (
            await session.execute(
                select(Order)
                .where(Order.id == order_id, Order.user_id == user.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if order is None:
            raise TradingError("order not found")
        if order.status != "pending_confirmation":
            raise TradingError("order cannot be confirmed")
        account = (
            await session.execute(
                select(Account).where(Account.id == order.account_id).with_for_update()
            )
        ).scalar_one()
        if account.available_balance < order.amount:
            raise TradingError("insufficient available balance")
        from app.services.risk_assessment_service import RiskAssessmentService

        risk = (
            (
                await session.execute(
                    select(CustomerRiskAssessment)
                    .where(
                        CustomerRiskAssessment.user_id == user.id,
                        CustomerRiskAssessment.status.in_(["active", "provisional"]),
                    )
                    .order_by(CustomerRiskAssessment.assessed_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if risk is None:
            risk = await RiskAssessmentService().ensure_default(session, user.id)
        product = (
            await session.execute(select(Product).where(Product.id == order.product_id))
        ).scalar_one()
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user.id)
            )
        ).scalar_one_or_none()
        target_type = getattr(product, "target_customer_type", "individual")
        if target_type != "all" and (
            profile is None or profile.customer_type != target_type
        ):
            raise TradingError(f"product is only available to {target_type} customers")
        required = {"C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5}.get(
            str(product.risk_level).upper().replace("R", "C"), 1
        )
        actual = {"C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5}.get(risk.risk_level, 1)
        if actual < required:
            raise TradingError(
                f"risk level {risk.risk_level} is not suitable for product {product.risk_level}; complete a formal risk assessment to unlock higher-risk products"
            )
        account.available_balance -= order.amount
        account.frozen_balance += order.amount
        operator_id = operator.id if operator is not None else user.id
        note = (
            "staff confirmed order for customer"
            if operator is not None
            else "customer confirmed order"
        )
        await self.transition(session, order, "pending_review", operator_id, note)
        return order

    async def cancel_order(self, session, user: User, order_id: str):
        order = (
            await session.execute(
                select(Order)
                .where(Order.id == order_id, Order.user_id == user.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if order is None:
            raise TradingError("order not found")
        if order.status not in {"pending_confirmation", "pending_review"}:
            raise TradingError("order cannot be cancelled")
        if order.status == "pending_review":
            account = (
                await session.execute(
                    select(Account)
                    .where(Account.id == order.account_id)
                    .with_for_update()
                )
            ).scalar_one()
            account.available_balance += order.amount
            account.frozen_balance -= order.amount
        await self.transition(
            session, order, "cancelled", user.id, "customer cancelled order"
        )
        return order

    async def review_order(
        self, session, operator: User, order_id: str, approve: bool, note: str = ""
    ):
        order = (
            await session.execute(
                select(Order).where(Order.id == order_id).with_for_update()
            )
        ).scalar_one_or_none()
        if order is None:
            raise TradingError("order not found")
        if order.status != "pending_review":
            raise TradingError("order is not awaiting review")
        if not approve:
            account = (
                await session.execute(
                    select(Account)
                    .where(Account.id == order.account_id)
                    .with_for_update()
                )
            ).scalar_one()
            account.available_balance += order.amount
            account.frozen_balance -= order.amount
            await self.transition(
                session, order, "rejected", operator.id, note or "rejected by staff"
            )
            order.failure_reason = note or "rejected by staff"
            return order
        await self.transition(
            session, order, "executing", operator.id, note or "approved by staff"
        )
        account = (
            await session.execute(
                select(Account).where(Account.id == order.account_id).with_for_update()
            )
        ).scalar_one()
        account.frozen_balance -= order.amount
        holding = (
            await session.execute(
                select(CustomerHolding)
                .where(
                    CustomerHolding.user_id == order.user_id,
                    CustomerHolding.product_id == order.product_id,
                    CustomerHolding.status == "active",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if holding is None:
            holding = CustomerHolding(
                id=str(uuid4()),
                user_id=order.user_id,
                product_id=order.product_id,
                quantity=order.quantity,
                cost_amount=order.amount,
                market_value=order.amount,
                profit_loss=0,
                holding_days=0,
                status="active",
            )
            session.add(holding)
        else:
            holding.quantity += order.quantity
            holding.cost_amount += order.amount
            holding.market_value += order.amount
        session.add(
            Trade(
                id=str(uuid4()),
                trade_no=f"T{uuid4().hex[:16].upper()}",
                order_id=order.id,
                user_id=order.user_id,
                product_id=order.product_id,
                amount=order.amount,
                quantity=order.quantity,
            )
        )
        await self.transition(
            session, order, "executed", operator.id, note or "mock trade executed"
        )
        await AssetSummaryService().snapshot(
            session, order.user_id, source_type="trade_derived"
        )
        return order

    # ---- redeem（赎回，F3.4 真实落库）---------------------------------
    async def redeem(
        self,
        session: AsyncSession,
        user: User,
        product_id: str,
        shares: Decimal,
        operator: User | None = None,
    ) -> dict:
        """赎回：校验持仓 → 创建卖出订单 → 扣减持仓 → 资金回账 → 记录 Trade。"""
        product = (
            await session.execute(
                select(Product).where(
                    Product.id == product_id, Product.status == "active"
                )
            )
        ).scalar_one_or_none()
        if product is None:
            raise TradingError("product unavailable")
        holding = (
            await session.execute(
                select(CustomerHolding)
                .where(
                    CustomerHolding.user_id == user.id,
                    CustomerHolding.product_id == product_id,
                    CustomerHolding.status == "active",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if holding is None:
            raise TradingError(f"客户未持有产品「{product.name}」，无法赎回")
        if holding.quantity < shares:
            raise TradingError(
                f"持仓不足：持有 {holding.quantity} 份，请求赎回 {shares} 份"
            )
        account = await self.get_or_create_account(session, user.id)
        unit_price = (
            (holding.market_value / holding.quantity)
            if holding.quantity
            else Decimal("1")
        )
        amount = (shares * unit_price).quantize(Decimal("0.01"))

        order = Order(
            id=str(uuid4()),
            order_no=f"O{uuid4().hex[:16].upper()}",
            user_id=user.id,
            account_id=account.id,
            product_id=product_id,
            amount=amount,
            quantity=shares,
            status="executed",
            side="sell",
        )
        session.add(order)
        await session.flush()
        holding.quantity -= shares
        holding.market_value -= amount
        holding.cost_amount = max(Decimal("0"), holding.cost_amount - amount)
        account.available_balance += amount
        session.add(
            Trade(
                id=str(uuid4()),
                trade_no=f"T{uuid4().hex[:16].upper()}",
                order_id=order.id,
                user_id=user.id,
                product_id=product_id,
                amount=amount,
                quantity=shares,
            )
        )
        operator_id = operator.id if operator is not None else user.id
        session.add(
            OrderStatusHistory(
                id=str(uuid4()),
                order_id=order.id,
                from_status=None,
                to_status="executed",
                operator_user_id=operator_id,
                note="staff redeem executed"
                if operator
                else "customer redeem executed",
            )
        )
        if holding.quantity <= 0:
            holding.status = "closed"
        await AssetSummaryService().snapshot(
            session, user.id, source_type="trade_derived"
        )
        return {
            "order_id": str(order.id),
            "order_no": order.order_no,
            "product": product.name,
            "shares": float(shares),
            "amount": float(amount),
            "status": order.status,
        }

    # ---- transfer（转账，F3.4 真实落库）--------------------------------
    async def transfer(
        self,
        session: AsyncSession,
        from_user: User,
        to_user: User,
        amount: Decimal,
        operator: User | None = None,
    ) -> dict:
        """转账：账户余额 A → B 划转。"""
        if amount <= 0:
            raise TradingError("转账金额必须大于 0")
        from_account = await self.get_or_create_account(session, from_user.id)
        to_account = (
            await session.execute(
                select(Account).where(Account.user_id == to_user.id).with_for_update()
            )
        ).scalar_one_or_none()
        if to_account is None:
            raise TradingError(f"目标账户「{to_user.display_name}」不存在")
        if from_account.available_balance < amount:
            raise TradingError(
                f"转出账户余额不足（可用 {from_account.available_balance:,.2f} 元）"
            )
        from_account.available_balance -= amount
        to_account.available_balance += amount
        return {
            "from_user_id": from_user.id,
            "to_user_id": to_user.id,
            "amount": float(amount),
            "status": "executed",
        }

    # ---- info update（信息更新，F3.4 真实落库）--------------------------
    @staticmethod
    async def update_profile_field(
        session: AsyncSession, user_id: str, field: str, value: str
    ) -> dict:
        """更新客户基础资料字段（白名单校验，支持中文字段别名）。"""
        from app.models.profile import CustomerProfile

        whitelist = {
            "occupation": "occupation",
            "region": "region",
            "investment_goal": "investment_goal",
            "liquidity_preference": "liquidity_preference",
            "education_level": "education_level",
        }
        # 中文别名 → 标准字段
        aliases = {
            "职业": "occupation",
            "所在地区": "region",
            "地区": "region",
            "投资目标": "investment_goal",
            "流动性偏好": "liquidity_preference",
            "流动性": "liquidity_preference",
            "学历": "education_level",
        }
        norm = field.strip().lower()
        norm = aliases.get(field.strip(), norm)
        if norm not in whitelist:
            raise TradingError(
                f"暂不支持更新字段「{field}」，支持：职业、所在地区、投资目标、流动性偏好、学历"
            )
        profile = (
            await session.execute(
                select(CustomerProfile).where(CustomerProfile.user_id == user_id)
            )
        ).scalar_one_or_none()
        if profile is None:
            raise TradingError("客户画像不存在，请先创建画像")
        setattr(profile, whitelist[norm], value)
        return {"field": whitelist[norm], "value": value, "user_id": user_id}
