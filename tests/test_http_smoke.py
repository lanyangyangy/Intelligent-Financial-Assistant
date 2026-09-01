from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx


@dataclass
class SmokeFailure:
    name: str
    detail: str


class SmokeRunner:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self.failures: list[SmokeFailure] = []
        self.tokens: dict[str, str] = {}
        self.users: dict[str, dict[str, Any]] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def request(self, name: str, method: str, path: str, *, token: str | None = None, expected: int = 200, **kwargs: Any) -> httpx.Response | None:
        headers = dict(kwargs.pop("headers", {}) or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = await self.client.request(method, path, headers=headers, **kwargs)
        except Exception as exc:
            self.failures.append(SmokeFailure(name, f"request error: {exc}"))
            return None
        if response.status_code != expected:
            self.failures.append(SmokeFailure(name, f"expected {expected}, got {response.status_code}: {response.text[:500]}"))
            return None
        return response

    @staticmethod
    def data(response: httpx.Response) -> Any:
        payload = response.json()
        if not payload.get("success", False):
            raise AssertionError(f"API returned failure: {payload}")
        return payload.get("data")

    async def login(self, key: str, username: str, password: str) -> str | None:
        response = await self.request(
            f"login:{key}",
            "POST",
            "/auth/login",
            expected=200,
            json={"username": username, "password": password},
        )
        if response is None:
            return None
        try:
            token = self.data(response)["access_token"]
            self.tokens[key] = token
            return token
        except (AssertionError, KeyError, TypeError) as exc:
            self.failures.append(SmokeFailure(f"login:{key}", str(exc)))
            return None

    async def run(self, accounts: dict[str, tuple[str, str]]) -> int:
        health = await self.request("health", "GET", "/health")
        if health is not None:
            try:
                checks = self.data(health)["checks"]
                for component in ("postgresql", "redis"):
                    if checks.get(component, {}).get("status") != "ok":
                        self.failures.append(SmokeFailure("health", f"{component} is not ok: {checks.get(component)}"))
                if checks.get("embedding", {}).get("status") not in {"ok", "configured", "skipped"}:
                    self.failures.append(SmokeFailure("health", f"embedding is not ready: {checks.get('embedding')}"))
            except (AssertionError, KeyError, TypeError) as exc:
                self.failures.append(SmokeFailure("health", str(exc)))

        for key, (username, password) in accounts.items():
            token = await self.login(key, username, password)
            if token:
                response = await self.request(f"me:{key}", "GET", "/auth/me", token=token)
                if response is not None:
                    try:
                        self.users[key] = self.data(response)
                    except (AssertionError, TypeError) as exc:
                        self.failures.append(SmokeFailure(f"me:{key}", str(exc)))

        admin = self.tokens.get("super_admin")
        customer = self.tokens.get("retail_investor")
        if admin:
            users_response = await self.request("admin users page", "GET", "/admin/users?limit=2&offset=0", token=admin)
            if users_response is not None:
                try:
                    users = self.data(users_response)
                    if not isinstance(users.get("items"), list) or not isinstance(users.get("total"), int):
                        raise AssertionError(f"invalid pagination shape: {users}")
                except (AssertionError, TypeError, AttributeError) as exc:
                    self.failures.append(SmokeFailure("admin users page", str(exc)))
            await self.request("admin roles", "GET", "/admin/roles", token=admin)
            await self.request("pending orders page", "GET", "/trading/orders/pending?limit=2&offset=0", token=admin)
            await self.request("invalid pending pagination", "GET", "/trading/orders/pending?limit=0&offset=-1", token=admin, expected=422)
            await self.request("admin access without token", "GET", "/admin/users", expected=401)
        if customer:
            for name, path in (
                ("customer account", "/trading/account/me"),
                ("customer orders", "/trading/orders/me"),
                ("customer trades", "/trading/trades/me"),
                ("customer profile", "/profile/me"),
            ):
                await self.request(name, "GET", path, token=customer)
            await self.request("customer cannot access admin", "GET", "/admin/users", token=customer, expected=403)
            await self.request("customer cannot access pending", "GET", "/trading/orders/pending", token=customer, expected=403)

            products_response = await self.request("customer products", "GET", "/profile/products", token=customer)
            if products_response is not None:
                try:
                    products = self.data(products_response)
                    product = next((item for item in products if item.get("status") == "active" and item.get("target_customer_type", "individual") in {"individual", "all"} and str(item.get("risk_level", "C1")).replace("R", "C") == "C1"), None)
                    if product is None:
                        raise AssertionError("no active product available")
                    amount = Decimal(str(product.get("minimum_amount", "10000")))
                    idempotency_key = str(uuid.uuid4())
                    order_payload = {"product_id": product["id"], "amount": str(amount), "idempotency_key": idempotency_key}
                    first = await self.request("create order", "POST", "/trading/orders", token=customer, expected=201, json=order_payload)
                    second = await self.request("repeat idempotent order", "POST", "/trading/orders", token=customer, expected=201, json=order_payload)
                    if first is not None and second is not None:
                        first_order = self.data(first)
                        second_order = self.data(second)
                        if first_order["id"] != second_order["id"]:
                            self.failures.append(SmokeFailure("order idempotency", "repeat request created a different order"))
                        else:
                            order_id = first_order["id"]
                            confirmed = await self.request("confirm order", "POST", f"/trading/orders/{order_id}/confirm", token=customer)
                            if confirmed is not None and admin:
                                pending = await self.request("pending order after confirm", "GET", "/trading/orders/pending?limit=100&offset=0", token=admin)
                                if pending is not None:
                                    pending_items = self.data(pending).get("items", [])
                                    if not any(item.get("id") == order_id for item in pending_items):
                                        self.failures.append(SmokeFailure("pending order after confirm", "confirmed order not found in pending list"))
                                reviewed = await self.request("review order", "POST", f"/trading/orders/{order_id}/review", token=admin, json={"note": "smoke test"})
                                if reviewed is not None and self.data(reviewed).get("status") != "executed":
                                    self.failures.append(SmokeFailure("review order", "order did not reach executed status"))
                except (AssertionError, KeyError, TypeError, ValueError) as exc:
                    self.failures.append(SmokeFailure("order flow", str(exc)))

        await self.request("unknown order for customer", "GET", "/trading/orders/00000000-0000-0000-0000-000000000000", token=customer, expected=404) if customer else None
        return 1 if self.failures else 0


def load_accounts(path: str | None) -> dict[str, tuple[str, str]]:
    defaults = {
        "retail_investor": ("retail_investor_demo", "Demo@2026RetailInvestor"),
        "high_net_worth_customer": ("high_net_worth_demo", "Demo@2026HighNetWorth"),
        "financial_advisor": ("financial_advisor_demo", "Demo@2026FinancialAdvisor"),
        "risk_specialist": ("risk_specialist_demo", "Demo@2026RiskSpecialist"),
        "customer_manager": ("customer_manager_demo", "Demo@2026CustomerManager"),
        "auditor": ("auditor_demo", "Demo@2026Auditor"),
        "super_admin": ("super_admin_demo", "Demo@2026SuperAdmin"),
    }
    if not path:
        return defaults
    loaded: dict[str, tuple[str, str]] = {}
    section: str | None = None
    for raw_line in open(path, encoding="utf-8"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section and "=" in line:
            key, value = line.split("=", 1)
            loaded.setdefault(section, ["", ""])
            if key == "username":
                loaded[section][0] = value
            elif key == "password":
                loaded[section][1] = value
    return {key: tuple(value) for key, value in loaded.items() if value[0] and value[1]} or defaults


async def async_main(args: argparse.Namespace) -> int:
    runner = SmokeRunner(args.base_url, args.timeout)
    try:
        code = await runner.run(load_accounts(args.accounts))
        result = {"status": "PASS" if code == 0 else "FAIL", "failures": [failure.__dict__ for failure in runner.failures]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    finally:
        await runner.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Wealth Manager HTTP smoke regression test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--accounts", default=None, help="optional path to local DEMO_ACCOUNTS.txt")
    return asyncio.run(async_main(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
