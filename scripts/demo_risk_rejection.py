"""Demo 2 · 危险操作拒绝：越权拦截 + 二次确认 + 幂等。

前置：同 demo_advisor.py。
运行：.\\venv\\Scripts\\python.exe scripts\\demo_risk_rejection.py

演示剧本（约 90 秒）：
  1. 风控专员尝试申购 → 403 越权拒绝（角色权限边界）；
  2. 理财顾问发起超阈值申购 → 返回 requires_confirmation 二次确认；
  3. 同一 request_id 重复提交 → 幂等，不重复执行。
"""
from __future__ import annotations

import asyncio
import uuid

import httpx

from scripts.demo_common import DemoClient, dump, heading, observation, section


async def main() -> int:
    client = DemoClient()
    try:
        await client.login("risk_specialist")
        await client.login("advisor")

        heading("Demo 2 · 危险操作拒绝与安全护栏")

        section("场景 1：越权申购（风控专员）")
        observation("风控专员无 purchase 意图权限，chat 层应返回 403，绝不静默执行")
        try:
            await client.chat("risk_specialist", "帮客户申购10万元稳健债券")
            print("  ⚠️ 未拦截！越权申购被放行")
        except httpx.HTTPStatusError as exc:
            print(f"  ✅ 已拦截：HTTP {exc.response.status_code}")
            print(f"     响应体：{exc.response.text[:200]}")

        section("场景 2：超阈值申购触发二次确认（理财顾问）")
        observation("零售客户申购超过 1 万阈值 → requires_confirmation=true")
        request_id = str(uuid.uuid4())
        result = await client.chat(
            "advisor",
            "帮零售投资者申购20万元稳健债券",
            customer_id="retail_investor_demo",
            request_id=request_id,
        )
        dump("ChatResponse", result)
        observation(
            f"requires_confirmation = {result.get('requires_confirmation')}，"
            f"next_action = {result.get('next_action')}"
        )

        section("场景 3：幂等防重（同一 request_id 重复提交）")
        observation("同一操作人同一 request_id 24h 内重复提交返回首次结果，不重复执行")
        repeat = await client.chat(
            "advisor",
            "帮零售投资者申购20万元稳健债券",
            customer_id="retail_investor_demo",
            request_id=request_id,
        )
        dump("重复提交响应", repeat)
        observation("两次请求共享同一幂等语义，金融操作不因重试而双份执行")

        heading("讲解总结")
        observation("权限矩阵 + 分层确认阈值 + 幂等键，三道护栏保证高风险操作安全")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
