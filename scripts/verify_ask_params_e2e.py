"""HTTP 端到端验证业务操作追问续补（三个场景）。

场景1 理财顾问申购：缺产品+金额 → 追问 → 补参 → 大额确认 → 成功
场景2 客户经理转账：缺金额 → 追问 → 补参 → 直接成功
场景3 风控专员可疑上报：缺客户 → 追问 → 补参 → 成功
"""

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
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode())


def login(username: str, password: str) -> str:
    r = post("/api/v1/auth/login", {"username": username, "password": password})
    return r["data"]["access_token"]


def main() -> None:
    advisor = login("financial_advisor_demo", "Demo@2026FinancialAdvisor")
    manager = login("customer_manager_demo", "Demo@2026CustomerManager")
    risk = login("risk_specialist_demo", "Demo@2026RiskSpecialist")

    # ── 场景1：申购 缺产品+金额（高净值客户，余额充足；私行确认阈值10万）──
    s1 = f"e2e-ask-{int(time.time())}-1"
    r1 = post(
        "/api/v1/chat",
        {"message": "给高净值客户演示申购120000元", "session_id": s1},
        advisor,
    )["data"]
    print(f"场景1-1 next={r1.get('next_action')} summary={r1.get('summary')[:50]}")
    assert r1.get("next_action") == "ask_params", "应追问"

    r2 = post(
        "/api/v1/chat",
        {"message": "现金管理保本计划", "session_id": s1},
        advisor,
    )["data"]
    print(
        f"场景1-2 next={r2.get('next_action')} confirm={r2.get('requires_confirmation')} summary={r2.get('summary')[:60]}"
    )
    assert r2.get("requires_confirmation"), "12万申购应触发二次确认"
    cid = r2.get("data", {}).get("confirmation_id")

    r3 = post(
        "/api/v1/chat",
        {
            "message": "确认",
            "session_id": s1,
            "decision": "confirm",
            "confirmation_id": cid,
        },
        advisor,
    )["data"]
    print(f"场景1-3 next={r3.get('next_action')} summary={r3.get('summary')[:70]}")
    assert r3.get("status") == "success" and "申购" in (r3.get("summary") or ""), (
        "申购应成功"
    )
    print("✅ 场景1 申购续补（缺产品+金额→补参→确认→成功）\n")

    # ── 场景2：转账 缺金额（高净值转出方，余额充足）────────────────
    s2 = f"e2e-ask-{int(time.time())}-2"
    r1 = post(
        "/api/v1/chat",
        {"message": "把高净值客户演示账号的转到王芳账户", "session_id": s2},
        manager,
    )["data"]
    print(f"场景2-1 next={r1.get('next_action')} summary={r1.get('summary')[:50]}")
    assert r1.get("next_action") == "ask_params", "应追问"

    r2 = post(
        "/api/v1/chat",
        {"message": "5000元", "session_id": s2},
        manager,
    )["data"]
    print(f"场景2-2 next={r2.get('next_action')} summary={r2.get('summary')[:60]}")
    assert r2.get("status") == "success" and "转账成功" in (r2.get("summary") or ""), (
        "转账应成功"
    )
    print("✅ 场景2 转账续补（缺金额→补参→直接成功）\n")

    # ── 场景3：可疑上报 缺客户 ─────────────────────────────────────
    s3 = f"e2e-ask-{int(time.time())}-3"
    r1 = post(
        "/api/v1/chat",
        {"message": "上报可疑交易", "session_id": s3},
        risk,
    )["data"]
    print(f"场景3-1 next={r1.get('next_action')} summary={r1.get('summary')[:50]}")
    assert r1.get("next_action") == "ask_params", "应追问"

    r2 = post(
        "/api/v1/chat",
        {"message": "李伟", "session_id": s3},
        risk,
    )["data"]
    print(f"场景3-2 next={r2.get('next_action')} summary={r2.get('summary')[:70]}")
    assert r2.get("status") == "success" and "可疑交易上报完成" in (
        r2.get("summary") or ""
    ), "上报应成功"
    print("✅ 场景3 可疑上报续补（缺客户→补参→成功）\n")

    print("🎉 全部追问续补场景通过")


if __name__ == "__main__":
    main()
