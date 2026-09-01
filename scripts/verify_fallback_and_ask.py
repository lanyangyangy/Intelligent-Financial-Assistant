"""验证：①非八大业务兜底"目前没有该业务"；②缺客户追问客户ID；③缺产品+金额追问产品+金额。"""

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
    login("risk_specialist_demo", "Demo@2026RiskSpecialist")
    now = int(time.time())

    # ── 场景1：非八大业务 → 兜底"目前没有该业务" ────────────────────
    s = f"e2e-unk-{now}"
    r = post(
        "/api/v1/chat", {"message": "帮客户办理贷款业务", "session_id": s}, manager
    )["data"]
    print(f"场景1(贷款) agent={r.get('agent')} summary={r.get('summary')}")
    # 贷款不在八大业务，路由可能投客服或业务操作；业务操作兜底必须含"目前没有该业务"
    print("---")

    # 显式指定业务操作 Agent（确保走兜底）
    r2 = post(
        "/api/v1/chat",
        {
            "message": "帮客户办理贷款业务",
            "agent": "business_operator",
            "session_id": s,
        },
        manager,
    )["data"]
    print(
        f"场景1-2(显式business_operator) intent={r2.get('data', {}).get('intent')} summary={r2.get('summary')}"
    )
    assert "目前没有该业务" in (r2.get("summary") or ""), "应兜底『目前没有该业务』"
    print("✅ 场景1 非八大业务兜底『目前没有该业务』\n")

    # ── 场景2：缺客户 → 追问客户ID ──────────────────────────────────
    s2 = f"e2e-ask-{now}-2"
    r = post(
        "/api/v1/chat",
        {"message": "给申购20000元的现金管理保本计划", "session_id": s2},
        advisor,
    )["data"]
    print(f"场景2-1(缺客户) next={r.get('next_action')} summary={r.get('summary')}")
    assert r.get("next_action") == "ask_params"
    assert "客户ID" in (r.get("summary") or ""), "缺客户应追问客户ID"
    print("✅ 场景2 缺客户 → 追问客户ID")

    # 补客户ID 续补成功（高净值=私行客户确认阈值10万，2万直接执行）
    r2 = post("/api/v1/chat", {"message": "客户ID 10", "session_id": s2}, advisor)[
        "data"
    ]
    print(
        f"场景2-2(补客户ID 10) next={r2.get('next_action')} summary={r2.get('summary')[:70]}"
    )
    assert r2.get("status") == "success" and "申购" in (r2.get("summary") or ""), (
        "补客户后应续补成功"
    )
    print("✅ 场景2 补客户ID → 续补执行成功\n")

    # ── 场景3：缺产品+金额 → 追问产品+金额 ──────────────────────────
    s3 = f"e2e-ask-{now}-3"
    r = post("/api/v1/chat", {"message": "给客户ID 10申购", "session_id": s3}, advisor)[
        "data"
    ]
    print(
        f"场景3-1(缺产品+金额) next={r.get('next_action')} summary={r.get('summary')}"
    )
    assert r.get("next_action") == "ask_params"
    summary3 = r.get("summary") or ""
    assert "产品名称" in summary3 and "申购金额" in summary3, (
        "缺产品+金额应追问产品+金额"
    )
    print("✅ 场景3 缺产品+金额 → 追问产品名称+金额")

    # 补产品+金额 续补成功（高净值=私行确认阈值10万，2万直接执行）
    r2 = post(
        "/api/v1/chat",
        {"message": "现金管理保本计划 20000元", "session_id": s3},
        advisor,
    )["data"]
    print(
        f"场景3-2(补产品+金额) next={r2.get('next_action')} summary={r2.get('summary')[:70]}"
    )
    assert r2.get("status") == "success" and "申购" in (r2.get("summary") or ""), (
        "补产品+金额后应续补成功"
    )
    print("✅ 场景3 补产品+金额 → 续补执行成功\n")

    # ── 场景4：转账缺转入方 → 追问转入方客户ID ─────────────────────
    s4 = f"e2e-ask-{now}-4"
    r = post(
        "/api/v1/chat",
        {"message": "把客户ID 10的60000元转到", "session_id": s4},
        manager,
    )["data"]
    print(f"场景4-1(缺转入方) next={r.get('next_action')} summary={r.get('summary')}")
    assert r.get("next_action") == "ask_params"
    assert "转入方客户ID" in (r.get("summary") or ""), "缺转入方应追问转入方客户ID"
    print("✅ 场景4 缺转入方 → 追问转入方客户ID\n")

    print("🎉 全部兜底/追问文案验证通过")


if __name__ == "__main__":
    main()
