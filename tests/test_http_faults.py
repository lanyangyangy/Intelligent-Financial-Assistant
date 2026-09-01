from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import httpx


class FaultRunner:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.results: list[dict[str, Any]] = []

    async def check(self, name: str, method: str, path: str, expected: set[int], *, token: str | None = None, **kwargs: Any) -> None:
        headers = dict(kwargs.pop("headers", {}) or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
                response = await client.request(method, path, headers=headers, **kwargs)
            ok = response.status_code in expected
            self.results.append({"name": name, "status": response.status_code, "expected": sorted(expected), "ok": ok, "body": response.text[:300]})
        except Exception as exc:
            self.results.append({"name": name, "status": None, "expected": sorted(expected), "ok": False, "body": repr(exc)})

    async def login(self, username: str, password: str) -> str:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            response = await client.post("/auth/login", json={"username": username, "password": password})
        response.raise_for_status()
        return response.json()["data"]["access_token"]

    async def run(self) -> int:
        await self.check("health baseline", "GET", "/health", {200})
        try:
            customer = await self.login("retail_investor_demo", "Demo@2026RetailInvestor")
            admin = await self.login("super_admin_demo", "Demo@2026SuperAdmin")
        except Exception as exc:
            print(json.dumps({"status": "FAIL", "stage": "baseline login", "error": repr(exc)}, ensure_ascii=False, indent=2))
            return 1

        # Request validation / malformed input: these should be controlled 4xx, never 5xx.
        await self.check("invalid pagination", "GET", "/trading/orders/pending?limit=0&offset=-1", {422}, token=admin)
        await self.check("malformed login", "POST", "/auth/login", {401, 422}, json={"username": "not-found", "password": "wrong-password"})
        await self.check("unknown product order", "POST", "/trading/orders", {400, 404, 422}, token=customer, json={"product_id": "00000000-0000-0000-0000-000000000000", "amount": "10000.00"})
        await self.check("invalid amount order", "POST", "/trading/orders", {422}, token=customer, json={"product_id": "00000000-0000-0000-0000-000000000000", "amount": "-1"})
        await self.check("unknown order confirm", "POST", "/trading/orders/00000000-0000-0000-0000-000000000000/confirm", {400, 404}, token=customer)
        await self.check("unknown order review", "POST", "/trading/orders/00000000-0000-0000-0000-000000000000/review", {400, 404}, token=admin, json={"note": "fault test"})

        # Invalid credentials and authorization boundary.
        await self.check("invalid bearer token", "GET", "/auth/me", {401}, token="invalid-token")
        await self.check("customer admin boundary", "GET", "/admin/users", {403}, token=customer)

        # Recovery check: the app must remain healthy after all negative cases.
        await self.check("health recovery", "GET", "/health", {200})
        await self.check("customer recovery", "GET", "/trading/orders/me", {200}, token=customer)
        await self.check("admin recovery", "GET", "/admin/users?limit=1&offset=0", {200}, token=admin)

        failures = [item for item in self.results if not item["ok"]]
        summary = {"status": "PASS" if not failures else "FAIL", "total": len(self.results), "failed": len(failures), "results": self.results}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Non-destructive development HTTP fault and recovery tests")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    return asyncio.run(FaultRunner(args.base_url, args.timeout).run())


if __name__ == "__main__":
    raise SystemExit(main())
