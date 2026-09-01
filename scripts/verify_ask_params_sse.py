"""SSE 流式路径验证追问续补（理财顾问申购：缺产品→追问→补产品）。

SSE 流式与普通 POST 都走 _run_chat 的路由 + business_operator.run，
这里验证 chat/stream 端点在续补场景下同样工作。
"""

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def login(username: str, password: str) -> str:
    data = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        BASE + "/api/v1/auth/login",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["data"]["access_token"]


def stream_chat(token: str, body: dict) -> str:
    """调用 SSE 端点，返回拼接的完整响应文本。"""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + "/api/v1/chat/stream",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    chunks = []
    with urllib.request.urlopen(req, timeout=90) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if line.startswith("data: "):
                chunks.append(line[6:])
    # 返回最后的 assistant 事件文本（含 status/next_action）
    return "\n".join(chunks[-20:])


def main() -> None:
    advisor = login("financial_advisor_demo", "Demo@2026FinancialAdvisor")
    s = f"e2e-sse-ask-{int(time.time())}"

    out1 = stream_chat(advisor, {"message": "给李伟申购50000元的", "session_id": s})
    print("SSE-1 输出片段:")
    print(out1[:600])
    print("---")

    out2 = stream_chat(advisor, {"message": "现金管理保本计划", "session_id": s})
    print("SSE-2 输出片段:")
    print(out2[:800])
    print("---")


if __name__ == "__main__":
    main()
