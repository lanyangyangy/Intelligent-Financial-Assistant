"""Demo 脚本公共工具：登录 + Chat + 健康检查（HTTP 直连真实服务）。

用法：先启动服务（scripts/start_dev.ps1），再运行 demo 脚本。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

BASE_URL = "http://127.0.0.1:8000/api/v1"

DEMO_ACCOUNTS = {
    "customer": ("retail_investor_demo", "Demo@2026RetailInvestor"),
    "advisor": ("financial_advisor_demo", "Demo@2026FinancialAdvisor"),
    "risk_specialist": ("risk_specialist_demo", "Demo@2026RiskSpecialist"),
    "customer_manager": ("customer_manager_demo", "Demo@2026CustomerManager"),
    "super_admin": ("super_admin_demo", "Demo@2026SuperAdmin"),
}


class DemoClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 60.0) -> None:
        self.client = httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self.tokens: dict[str, str] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def login(self, key: str) -> None:
        username, password = DEMO_ACCOUNTS[key]
        response = await self.client.post(
            "/auth/login", json={"username": username, "password": password}
        )
        response.raise_for_status()
        self.tokens[key] = response.json()["data"]["access_token"]

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[key]}"}

    async def chat(self, key: str, message: str, **extra: Any) -> dict[str, Any]:
        response = await self.client.post(
            "/chat",
            json={"message": message, **extra},
            headers=self._headers(key),
        )
        response.raise_for_status()
        return response.json()["data"]

    async def health(self) -> dict[str, Any]:
        response = await self.client.get("/health")
        response.raise_for_status()
        return response.json()["data"]


def heading(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def observation(text: str) -> None:
    print(f"  🎯 讲解点：{text}")


def section(text: str) -> None:
    print(f"\n  ── {text}")


def dump(name: str, value: Any) -> None:
    print(f"\n  {name}:")
    print(json.dumps(value, ensure_ascii=False, indent=2))
