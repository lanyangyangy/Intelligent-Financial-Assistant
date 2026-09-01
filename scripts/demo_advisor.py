"""Demo 1 · 正常投顾：画像读取 → 适当性校验 → 推荐解释。

前置：docker compose -f docker-compose.p0.yml up -d --wait
      .\\venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8000
运行：.\\venv\\Scripts\\python.exe scripts\\demo_advisor.py

演示剧本（约 60 秒）：
  1. 理财顾问登录，为指定客户发起投顾推荐；
  2. 展示 Supervisor 路由到 investment_advisor；
  3. 展示返回中的适当性校验结果与推荐解释（evidence/confidence）。
"""
from __future__ import annotations

import asyncio

from scripts.demo_common import DemoClient, dump, heading, observation, section


async def main() -> int:
    client = DemoClient()
    try:
        await client.login("advisor")

        heading("Demo 1 · 正常投顾推荐（理财顾问账号）")

        section("步骤 1：登录理财顾问，发起投顾推荐")
        observation("Supervisor 应把消息路由到 investment_advisor（投顾助手）")
        result = await client.chat(
            "advisor",
            "请为李伟推荐合适的理财配置，注意他的风险承受能力",
            customer_id="liwei",
        )
        dump("ChatResponse", result)
        observation(f"路由 Agent = {result.get('agent')}")
        if result.get("agent") != "investment_advisor":
            print("  ⚠️ 未路由到投顾助手，请检查演示账号与消息措辞")

        section("步骤 2：检查适当性硬门槛")
        observation("若客户风险等级低于产品风险等级，系统应给出硬门槛拦截或风险提示")
        obs = result.get("data", {})
        if obs.get("suitability"):
            dump("适当性校验", obs["suitability"])
        else:
            print("  （本次未返回结构化适当性字段，说明推荐在允许范围内或模型未配置）")

        section("步骤 3：推荐解释与证据")
        observation("evidence 列出推荐依据（画像/规则/知识），confidence 给出置信度")
        if result.get("evidence"):
            dump("evidence", result["evidence"][:3])
        print(f"\n  confidence = {result.get('confidence')}")
        print(f"\n  summary = {result.get('summary')}")

        heading("讲解总结")
        observation("投顾推荐 = 画像读取 + 适当性硬门槛 + 推荐解释，完整链路已展示")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
