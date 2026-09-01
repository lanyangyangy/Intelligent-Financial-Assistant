"""HTTP 端到端：缺转出方→补"客户1"→确认→转账成功。"""

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def post(path: str, body: dict, token: str | None = None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    login = post(
        "/api/v1/auth/login",
        {"username": "customer_manager_demo", "password": "Demo@2026CustomerManager"},
    )
    tk = login["data"]["access_token"]
    s = f"e2e-c1-{int(time.time())}"

    # 1. 缺转出方 → 追问
    r1 = post(
        "/api/v1/chat",
        {
            "message": "把客户的60000元转到客户ID 2账户",
            "session_id": s,
            "request_id": "e1",
        },
        tk,
    )["data"]
    print("1 追问:", r1["next_action"], "|", r1["summary"][:50])

    # 2. 补"客户1" → 确认请求
    r2 = post(
        "/api/v1/chat", {"message": "客户1", "session_id": s, "request_id": "e2"}, tk
    )["data"]
    print(
        "2 续补:",
        r2["next_action"],
        "| requires_confirmation:",
        r2["requires_confirmation"],
    )
    print("  summary:", r2["summary"][:80])
    assert r2["requires_confirmation"], "应触发确认"
    assert "1" in str(r2["data"]["params"].get("customer_identifier")), (
        "转出方应为客户1"
    )
    assert "60000" in str(r2["data"]["params"].get("amount")), "金额应保持60000"
    print("  ✅ 转出方=1 金额=60000")
    cid = r2["data"]["confirmation_id"]

    # 3. 确认执行
    r3 = post(
        "/api/v1/chat",
        {
            "message": "把客户的60000元转到客户ID 2账户",
            "session_id": s,
            "request_id": "e3",
            "decision": "confirm",
            "confirmation_id": cid,
        },
        tk,
    )["data"]
    print("3 确认:", r3["status"], "|", r3["summary"][:80])
    assert r3["status"] == "success" and "转账成功" in r3["summary"], "应转账成功"
    print("  ✅ 转账成功")

    # 4. 取消路径（重新发起→补客户1→取消）
    s2 = f"e2e-c1b-{int(time.time())}"
    post(
        "/api/v1/chat",
        {
            "message": "把客户的60000元转到客户ID 2账户",
            "session_id": s2,
            "request_id": "e4",
        },
        tk,
    )
    r5 = post(
        "/api/v1/chat", {"message": "客户1", "session_id": s2, "request_id": "e5"}, tk
    )["data"]
    cid5 = r5["data"]["confirmation_id"]
    r6 = post(
        "/api/v1/chat",
        {
            "message": "把客户的60000元转到客户ID 2账户",
            "session_id": s2,
            "request_id": "e6",
            "decision": "cancel",
            "confirmation_id": cid5,
        },
        tk,
    )["data"]
    print(
        "4 取消:",
        r6["status"],
        "| cancelled:",
        r6["data"].get("cancelled"),
        "|",
        r6["summary"][:30],
    )
    assert r6["data"].get("cancelled") is True, "应取消成功"
    print("  ✅ 取消成功，未执行转账")
    print("\n🎉 全链路通过")


if __name__ == "__main__":
    main()
