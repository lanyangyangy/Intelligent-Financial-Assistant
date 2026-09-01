"""P3 HTTP 端到端验证：/api/operation/* 结构化端点。

登录理财顾问 → POST /api/operation/purchase（结构化）→ 幂等 + 审计落库。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

BASE = "http://127.0.0.1:8000"


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        # 1. 登录理财顾问
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "username": "financial_advisor_demo",
                "password": "Demo@2026FinancialAdvisor",
            },
        )
        print("login:", login.status_code)
        assert login.status_code == 200, login.text
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. 结构化申购端点
        payload = {
            "customer_name": "零售投资者",
            "product_name": "国债逆回购优选",
            "amount": 6000,
            "request_id": "HTTP-P3-001",
        }
        r = await client.post("/api/operation/purchase", json=payload, headers=headers)
        print("purchase:", r.status_code, r.text[:220])
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        print(
            "  status=",
            data["status"],
            "order_no=",
            (data.get("data") or {}).get("order_no"),
        )
        assert data["status"] == "success"

        # 3. 相同 request_id 幂等重放
        r2 = await client.post("/api/operation/purchase", json=payload, headers=headers)
        data2 = r2.json()["data"]
        order1 = (data.get("data") or {}).get("order_no")
        order2 = (data2.get("data") or {}).get("order_no")
        print("  idempotent order match:", order1 == order2)
        assert order1 == order2, "幂等重放应返回同一订单"

        print("P3 HTTP VERIFY OK")


if __name__ == "__main__":
    asyncio.run(main())
