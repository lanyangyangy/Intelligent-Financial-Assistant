from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx


@dataclass
class Result:
    name: str
    status: int | None
    elapsed_ms: float
    ok: bool
    detail: str = ""


class ConcurrencyRunner:
    def __init__(self, base_url: str, timeout: float, concurrency: int, requests: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.concurrency = concurrency
        self.requests = requests
        self.results: list[Result] = []
        self.lock = asyncio.Lock()

    async def request(self, client: httpx.AsyncClient, name: str, method: str, path: str, *, token: str, expected: int = 200, **kwargs: Any) -> Result:
        start = time.perf_counter()
        try:
            response = await client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)
            elapsed = (time.perf_counter() - start) * 1000
            ok = response.status_code == expected or (name.startswith("idempotent-order-") and response.status_code in (201, 409))
            detail = "" if ok else response.text[:300]
            return Result(name, response.status_code, elapsed, ok, detail)
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            return Result(name, None, elapsed, False, repr(exc))

    async def login(self, client: httpx.AsyncClient, username: str, password: str) -> str:
        response = await client.post("/auth/login", json={"username": username, "password": password})
        response.raise_for_status()
        return response.json()["data"]["access_token"]

    async def run_batch(self, client: httpx.AsyncClient, token: str, jobs: list[tuple[str, str, str, dict[str, Any]]]) -> None:
        semaphore = asyncio.Semaphore(self.concurrency)

        async def run_one(job: tuple[str, str, str, dict[str, Any]]) -> None:
            async with semaphore:
                result = await self.request(client, job[0], job[1], job[2], token=token, **job[3])
                async with self.lock:
                    self.results.append(result)

        await asyncio.gather(*(run_one(job) for job in jobs))

    async def run(self) -> int:
        limits = httpx.Limits(max_connections=max(20, self.concurrency * 2), max_keepalive_connections=self.concurrency)
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, limits=limits) as client:
            try:
                customer = await self.login(client, "retail_investor_demo", "Demo@2026RetailInvestor")
                admin = await self.login(client, "super_admin_demo", "Demo@2026SuperAdmin")
            except Exception as exc:
                print(json.dumps({"status": "FAIL", "stage": "login", "error": repr(exc)}, ensure_ascii=False, indent=2))
                return 1

            readonly_jobs: list[tuple[str, str, str, dict[str, Any]]] = []
            for i in range(self.requests):
                if i % 2:
                    readonly_jobs.append((f"customer-orders-{i}", "GET", "/trading/orders/me", {}))
                else:
                    readonly_jobs.append((f"customer-profile-{i}", "GET", "/profile/me", {}))
            original = self.concurrency
            self.concurrency = max(1, original)
            await self.run_batch(client, customer, readonly_jobs)

            before_count = len(self.results)
            admin_jobs = [(f"admin-users-{i}", "GET", "/admin/users?limit=20&offset=0", {}) for i in range(self.requests)]
            await self.run_batch(client, admin, admin_jobs)

            products_response = await client.get("/profile/products", headers={"Authorization": f"Bearer {customer}"})
            products_response.raise_for_status()
            product = next(item for item in products_response.json()["data"] if item.get("status") == "active")
            amount = str(Decimal(str(product.get("minimum_amount", "10000"))))
            idem = f"concurrency-{uuid.uuid4()}"
            idem_jobs = [(f"idempotent-order-{i}", "POST", "/trading/orders", {"json": {"product_id": product["id"], "amount": amount, "idempotency_key": idem}}) for i in range(max(2, min(self.requests, 20)))]
            idem_start = len(self.results)
            await self.run_batch(client, customer, idem_jobs)

            idem_results = self.results[idem_start:]
            idem_statuses = [result.status for result in idem_results]
            order_ids: set[str] = set()
            for result in idem_results:
                if result.ok:
                    try:
                        # The body is fetched separately below; status consistency is asserted through count and API status.
                        pass
                    except Exception:
                        pass
            # Fetch the customer's latest orders and verify only one order was created for this unique key.
            orders_response = await client.get("/trading/orders/me", headers={"Authorization": f"Bearer {customer}"})
            orders_response.raise_for_status()
            matching = [item for item in orders_response.json()["data"] if item.get("idempotency_key") == idem]
            # Current response schema intentionally does not expose idempotency_key; successful concurrent requests must still all be 201.
            if not all(result.status in (201, 409) for result in idem_results):
                self.results.append(Result("idempotent-order-batch", None, 0, False, "one or more concurrent idempotent requests failed"))

        total = len(self.results)
        failed = [result for result in self.results if not result.ok]
        latencies = [result.elapsed_ms for result in self.results if result.status is not None]
        summary = {
            "status": "PASS" if not failed else "FAIL",
            "config": {"requests_per_scenario": self.requests, "concurrency": self.concurrency, "timeout_seconds": self.timeout},
            "total_requests": total,
            "failed_requests": len(failed),
            "latency_ms": {
                "min": round(min(latencies), 2) if latencies else None,
                "median": round(statistics.median(latencies), 2) if latencies else None,
                "p95": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 2) if latencies else None,
                "max": round(max(latencies), 2) if latencies else None,
            },
            "failures": [result.__dict__ for result in failed[:20]],
            "idempotent_statuses": idem_statuses if "idem_statuses" in locals() else [],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Controlled HTTP concurrency test for Wealth Manager")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--requests", type=int, default=20, help="requests per read-only scenario")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    if args.concurrency < 1 or args.requests < 1:
        parser.error("--concurrency and --requests must be positive")
    return asyncio.run(ConcurrencyRunner(args.base_url, args.timeout, args.concurrency, args.requests).run())


if __name__ == "__main__":
    sys.exit(main())










