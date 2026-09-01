"""Demo 3 · 依赖故障降级：健康矩阵 + 可选依赖回退。

前置：服务已启动（未配置 DASHSCOPE_API_KEY、未启用 Neo4j 时效果最佳）。
运行：.\\venv\\Scripts\\python.exe scripts\\demo_degradation.py

演示剧本（约 60 秒）：
  1. GET /health 展示五组件健康矩阵；
  2. 未配置模型 Key → qwen/embedding 为 skipped（不发起计费请求）；
  3. Neo4j 未启用 → neo4j 为 skipped（知识图谱回退纯 RAG，主流程不阻塞）。
"""
from __future__ import annotations

import asyncio

from scripts.demo_common import DemoClient, dump, heading, observation, section

COMPONENT_LABELS = {
    "postgresql": "PostgreSQL（业务库）",
    "redis": "Redis（记忆/事件/确认凭据）",
    "qwen": "Qwen Chat（模型对话）",
    "embedding": "Embedding（向量化）",
    "neo4j": "Neo4j（知识图谱，可选）",
}


async def main() -> int:
    client = DemoClient()
    try:
        heading("Demo 3 · 依赖故障降级与健康可观测")

        section("步骤 1：健康检查五组件矩阵")
        health = await client.health()
        dump("GET /health", health)
        checks = health.get("checks", {})

        section("步骤 2：解读各组件状态")
        for component, label in COMPONENT_LABELS.items():
            status = checks.get(component, {}).get("status", "unknown")
            icon = {"ok": "✅", "configured": "🟡", "skipped": "⚪", "error": "❌"}.get(
                status, "❓"
            )
            print(f"  {icon} {label} → {status}")

        qwen = checks.get("qwen", {}).get("status")
        embedding = checks.get("embedding", {}).get("status")
        neo4j = checks.get("neo4j", {}).get("status")

        observation(
            "qwen/embedding 为 skipped/configured：说明未配置 Key 或未开启计费冒烟，"
            "系统如实上报而不误报在线健康"
        )
        if qwen == "skipped":
            print("  · 模型未配置 → skipped（不发起计费请求）")
        elif qwen == "configured":
            print("  · 模型已配置 → configured（仅校验配置，不真实调用）")
        if embedding in {"skipped", "configured"}:
            print(f"  · Embedding → {embedding}（维度 {checks.get('embedding', {}).get('dimension', '-')}）")

        observation(
            "neo4j 为 skipped：图数据库可选依赖未启用时，知识图谱回退纯 RAG，"
            "投顾/客服主流程不受影响（降级不阻塞）"
        )
        if neo4j == "skipped":
            print("  · Neo4j 未启用 → skipped（GraphRAG 回退纯 RAG）")

        section("步骤 3：Trace ID 可观测")
        observation("每个请求响应头携带 X-Trace-Id，日志按 trace 关联 Agent/工具/模型耗时")
        print("  · 演示：查看后端日志中 trace_id 与 http_request_completed 行")

        heading("讲解总结")
        observation("可选依赖禁用不误报 error、模型未配置不误报在线、健康矩阵如实呈现")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
