"""八大业务全链路测试（HTTP 端到端，覆盖 6 类场景）。

八种业务：申购 purchase / 赎回 redeem / 转账 transfer / 产品查询 product_query /
信息更新 info_update / 风评重做 risk_reassess / 可疑上报 suspicious_report /
工单创建 workorder_create

覆盖：
1. 权限校验测试  —— 各角色 × 各业务（PERMISSION_MATRIX + 路由 + HTTP 403/拒绝）
2. 兜底测试      —— 非八大业务（业务相关→"目前没有该业务"；闲聊→客服）
3. 失败性测试    —— 参数错误/余额不足/客户不存在/年龄熔断/适当性拦截
4. 追问测试      —— 缺客户/缺产品/缺金额 → 追问 → 补参 → 成功
5. 二次确认测试  —— 大额申购/转账 → 确认执行 / 取消不执行
6. 风险测试      —— 大额事件发布 event:large_transaction + 风控预警联动
"""

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"

PASS = []
FAIL = []


def check(name: str, cond: bool, detail: str = ""):
    if cond:
        PASS.append(name)
        print(f"  ✅ {name}")
    else:
        FAIL.append(name)
        print(f"  ❌ {name} {detail}")


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
            return json.loads(resp.read().decode()), resp.status
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode()), exc.code
        except Exception:  # noqa: BLE001
            return {}, exc.code


def login(username: str, password: str) -> str:
    r, _ = post("/api/v1/auth/login", {"username": username, "password": password})
    return r["data"]["access_token"]


def get(path: str, token: str | None = None):
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": f"Bearer {token}"} if token else {},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode()), resp.status
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode()), exc.code
        except Exception:  # noqa: BLE001
            return {}, exc.code


def chat(token: str, message: str, session: str, agent: str | None = None, **kw):
    body = {"message": message, "session_id": session, **kw}
    if agent:
        body["agent"] = agent
    r, code = post("/api/v1/chat", body, token)
    return r.get("data", {}), code


# ── 登录各角色 ────────────────────────────────────────────────────────
ADVISOR = None
MANAGER = None
RISK = None
AUDITOR = None
SUPER = None


def setup():
    global ADVISOR, MANAGER, RISK, AUDITOR, SUPER
    ADVISOR = login("financial_advisor_demo", "Demo@2026FinancialAdvisor")
    MANAGER = login("customer_manager_demo", "Demo@2026CustomerManager")
    RISK = login("risk_specialist_demo", "Demo@2026RiskSpecialist")
    AUDITOR = login("auditor_demo", "Demo@2026Auditor")
    SUPER = login("super_admin_demo", "Demo@2026SuperAdmin")


# ══════════════════════════════════════════════════════════════════════
# 1. 权限校验测试（8 业务 × 角色矩阵）
# ══════════════════════════════════════════════════════════════════════
def test_permission():
    print("\n══ 1. 权限校验测试 ══")
    now = int(time.time())

    # 申购 → 理财顾问；客户经理越权（高净值=私行，确认阈值10万）
    r, code = chat(
        ADVISOR,
        "给客户ID 10申购30000元的现金管理保本计划",
        f"p1-{now}",
        "business_operator",
    )
    check(
        "申购:理财顾问=可执行", r.get("status") == "success", str(r.get("summary"))[:60]
    )
    r, code = chat(
        MANAGER,
        "给客户ID 10申购30000元的现金管理保本计划",
        f"p2-{now}",
        "business_operator",
    )
    check(
        "申购:客户经理=拒绝",
        "无权" in (r.get("summary") or "") or code == 403,
        str(r.get("summary"))[:60],
    )

    # 转账 → 客户经理；理财顾问越权（高净值=私行，转账确认阈值50万）
    r, _ = chat(
        MANAGER, "把客户ID 10的30000元转到客户ID 31", f"p3-{now}", "business_operator"
    )
    check(
        "转账:客户经理=可执行",
        r.get("status") == "success" or r.get("next_action") == "confirm_transfer",
        str(r.get("summary"))[:60],
    )
    r, _ = chat(
        ADVISOR, "把客户ID 10的30000元转到客户ID 31", f"p4-{now}", "business_operator"
    )
    check(
        "转账:理财顾问=拒绝",
        "无权" in (r.get("summary") or ""),
        str(r.get("summary"))[:60],
    )

    # 可疑上报 → 风控专员；客户经理越权
    r, _ = chat(RISK, "上报客户ID 31的可疑交易", f"p5-{now}", "business_operator")
    check(
        "可疑上报:风控=可执行",
        r.get("status") == "success" or "可疑交易上报完成" in (r.get("summary") or ""),
        str(r.get("summary"))[:60],
    )
    r, _ = chat(MANAGER, "上报客户ID 31的可疑交易", f"p6-{now}", "business_operator")
    check(
        "可疑上报:客户经理=拒绝",
        "无权" in (r.get("summary") or ""),
        str(r.get("summary"))[:60],
    )

    # 工单创建 → 客户经理；风控专员越权
    r, _ = chat(MANAGER, "给客户ID 31创建投诉工单", f"p7-{now}", "business_operator")
    check(
        "工单创建:客户经理=可执行",
        r.get("status") == "success" or "工单创建成功" in (r.get("summary") or ""),
        str(r.get("summary"))[:60],
    )
    r, _ = chat(RISK, "给客户ID 31创建投诉工单", f"p8-{now}", "business_operator")
    check(
        "工单创建:风控=拒绝",
        "无权" in (r.get("summary") or ""),
        str(r.get("summary"))[:60],
    )

    # 风评重做 → 理财顾问；客户经理越权
    r, _ = chat(ADVISOR, "给客户ID 10重新做风险评估", f"p9-{now}", "business_operator")
    check(
        "风评重做:理财顾问=可执行",
        r.get("status") == "success",
        str(r.get("summary"))[:60],
    )
    r, _ = chat(MANAGER, "给客户ID 10重新做风险评估", f"p10-{now}", "business_operator")
    check(
        "风评重做:客户经理=拒绝",
        "无权" in (r.get("summary") or ""),
        str(r.get("summary"))[:60],
    )

    # 信息更新 → 客户经理；理财顾问越权
    r, _ = chat(
        MANAGER, "把客户ID 31的手机号改成13800138000", f"p11-{now}", "business_operator"
    )
    check(
        "信息更新:客户经理=可执行",
        r.get("status") == "success",
        str(r.get("summary"))[:60],
    )
    r, _ = chat(
        ADVISOR, "把客户ID 31的手机号改成13800138000", f"p12-{now}", "business_operator"
    )
    check(
        "信息更新:理财顾问=拒绝",
        "无权" in (r.get("summary") or ""),
        str(r.get("summary"))[:60],
    )

    # 产品查询 → 全部内部员工
    for name, tk in (("理财顾问", ADVISOR), ("客户经理", MANAGER), ("风控", RISK)):
        r, _ = chat(
            tk, "查询安盈现金管理的净值", f"pq-{name}-{now}", "business_operator"
        )
        check(
            f"产品查询:{name}=可查询",
            r.get("status") == "success",
            str(r.get("summary"))[:60],
        )
    # 审计角色：无 business_operator Agent 权限（AGENT_REQUIRED_ROLES 不含 auditor），
    # 显式指定时被 403 拒绝（Agent 角色边界），产品只读查询走数据分析回退。
    r, code = chat(
        AUDITOR, "查询安盈现金管理的净值", f"pq-audit-{now}", "business_operator"
    )
    check(
        "产品查询:审计=Agent边界拒绝",
        code == 403 or "无权" in (r.get("summary") or ""),
        f"code={code}",
    )


# ══════════════════════════════════════════════════════════════════════
# 2. 兜底测试（业务相关→"目前没有该业务"；闲聊→客服）
# ══════════════════════════════════════════════════════════════════════
def test_fallback():
    print("\n══ 2. 兜底测试 ══")
    now = int(time.time())

    # 业务相关但非八大业务 → 路由到 operator → "目前没有该业务"
    for biz, msg in (
        ("贷款", "帮客户办理贷款业务"),
        ("存款", "给客户办理定期存款"),
        ("开户", "给客户开户"),
        ("保险", "帮客户购买保险"),
        ("外汇", "帮客户办理购汇业务"),
    ):
        r, _ = chat(MANAGER, msg, f"fb-{biz}-{now}")
        if r.get("agent") == "business_operator":
            check(
                f"兜底:{biz}=『目前没有该业务』",
                "目前没有该业务" in (r.get("summary") or ""),
                str(r.get("summary"))[:60],
            )
        else:
            check(
                f"兜底:{biz}=路由到operator",
                r.get("agent") == "business_operator",
                f"实际={r.get('agent')}",
            )

    # 纯闲聊 → 客服，不触发八大业务兜底
    r, _ = chat(MANAGER, "今天天气怎么样", f"fb-chat-{now}")
    check("闲聊:路由到客服", r.get("agent") == "customer_service", str(r.get("agent")))
    r, _ = chat(MANAGER, "你好", f"fb-hello-{now}")
    check("问候:路由到客服", r.get("agent") == "customer_service", str(r.get("agent")))

    # 咨询类业务问题 → 客服，不触发兜底
    r, _ = chat(MANAGER, "贷款怎么办理", f"fb-q-{now}")
    check(
        "业务咨询:路由到客服", r.get("agent") == "customer_service", str(r.get("agent"))
    )


# ══════════════════════════════════════════════════════════════════════
# 3. 失败性测试
# ══════════════════════════════════════════════════════════════════════
def test_failure():
    print("\n══ 3. 失败性测试 ══")
    now = int(time.time())

    # 客户不存在
    r, _ = chat(
        ADVISOR,
        "给不存在的客户张三申购5000元的现金管理保本计划",
        f"f1-{now}",
        "business_operator",
    )
    check(
        "失败:客户不存在",
        "未找到客户" in (r.get("summary") or ""),
        str(r.get("summary"))[:60],
    )

    # 产品不存在
    r, _ = chat(
        ADVISOR,
        "给客户ID 10申购5000元的不存在的产品X",
        f"f2-{now}",
        "business_operator",
    )
    check(
        "失败:产品不存在",
        "未找到在售产品" in (r.get("summary") or ""),
        str(r.get("summary"))[:60],
    )

    # 余额不足（李伟余额低，5万>1万阈值需确认→确认后执行发现余额不足）
    s = f"f3-{now}"
    r, _ = chat(ADVISOR, "给李伟申购50000元的现金管理保本计划", s, "business_operator")
    cid = (
        r.get("data", {}).get("confirmation_id")
        if r.get("requires_confirmation")
        else None
    )
    if cid:
        body = {
            "message": "确认",
            "session_id": s,
            "decision": "confirm",
            "confirmation_id": cid,
        }
        r2, _ = post("/api/v1/chat", body, ADVISOR)
        d2 = r2.get("data", {})
        summary2 = d2.get("summary") or ""
        check(
            "失败:余额不足",
            "余额不足" in summary2 or "insufficient" in summary2.lower(),
            summary2[:80],
        )
    else:
        summary = r.get("summary") or ""
        check(
            "失败:余额不足",
            "余额不足" in summary or "insufficient" in summary.lower(),
            summary[:80],
        )

    # 年龄熔断：未成年不可购
    r, _ = chat(
        ADVISOR,
        "给未成年投资者演示申购5000元的现金管理保本计划",
        f"f4-{now}",
        "business_operator",
    )
    check(
        "失败:未成年熔断",
        "未成年" in (r.get("summary") or "") or "年龄" in (r.get("summary") or ""),
        str(r.get("summary"))[:80],
    )

    # 适当性拦截：C1 客户（零售投资者）买 C4 产品（成长精选组合，起投5万）。
    # 6万>1万阈值先触发二次确认 → 确认后执行时被适当性拦截。
    s = f"f5-{now}"
    r, _ = chat(
        ADVISOR, "给零售投资者演示申购60000元的成长精选组合", s, "business_operator"
    )
    cid = (
        r.get("data", {}).get("confirmation_id")
        if r.get("requires_confirmation")
        else None
    )
    if cid:
        body = {
            "message": "确认",
            "session_id": s,
            "decision": "confirm",
            "confirmation_id": cid,
        }
        r2, _ = post("/api/v1/chat", body, ADVISOR)
        d2 = r2.get("data", {})
        summary2 = d2.get("summary") or ""
        check(
            "失败:适当性拦截",
            "suitable" in summary2.lower() or "风险" in summary2,
            summary2[:80],
        )
    else:
        summary = r.get("summary") or ""
        check(
            "失败:适当性拦截",
            "suitable" in summary.lower() or "风险" in summary,
            summary[:80],
        )

    # 转账转出=转入同一客户
    r, _ = chat(
        MANAGER, "把客户ID 31的5000元转到客户ID 31", f"f6-{now}", "business_operator"
    )
    check(
        "失败:同一客户转账",
        "同一客户" in (r.get("summary") or ""),
        str(r.get("summary"))[:60],
    )


# ══════════════════════════════════════════════════════════════════════
# 4. 追问测试（缺参数→追问→补参→成功）
# ══════════════════════════════════════════════════════════════════════
def test_ask_params():
    print("\n══ 4. 追问测试 ══")
    now = int(time.time())

    # 缺客户
    s = f"ask1-{now}"
    r, _ = chat(ADVISOR, "给申购5000元的现金管理保本计划", s)
    check(
        "追问:缺客户→ask_params",
        r.get("next_action") == "ask_params",
        str(r.get("summary"))[:60],
    )
    check(
        "追问:缺客户→提示客户ID",
        "客户ID" in (r.get("summary") or ""),
        str(r.get("summary"))[:60],
    )
    r, _ = chat(ADVISOR, "客户ID 10", s)
    check(
        "追问:补客户→执行成功", r.get("status") == "success", str(r.get("summary"))[:60]
    )

    # 缺产品+金额
    s = f"ask2-{now}"
    r, _ = chat(ADVISOR, "给客户ID 10申购", s)
    check(
        "追问:缺产品+金额→ask_params",
        r.get("next_action") == "ask_params",
        str(r.get("summary"))[:60],
    )
    check(
        "追问:缺产品+金额→提示产品+金额",
        "产品名称" in (r.get("summary") or "")
        and "申购金额" in (r.get("summary") or ""),
        str(r.get("summary"))[:80],
    )
    r, _ = chat(ADVISOR, "现金管理保本计划 30000元", s)
    check(
        "追问:补产品+金额→执行成功",
        r.get("status") == "success",
        str(r.get("summary"))[:60],
    )

    # 缺转入方（转账）
    s = f"ask3-{now}"
    r, _ = chat(MANAGER, "把客户ID 31的5000元转到", s)
    check(
        "追问:缺转入方→ask_params",
        r.get("next_action") == "ask_params",
        str(r.get("summary"))[:60],
    )
    check(
        "追问:缺转入方→提示转入方ID",
        "转入方客户ID" in (r.get("summary") or ""),
        str(r.get("summary"))[:60],
    )
    r, _ = chat(MANAGER, "客户ID 10", s)
    check(
        "追问:补转入方→执行成功",
        r.get("status") == "success",
        str(r.get("summary"))[:60],
    )


# ══════════════════════════════════════════════════════════════════════
# 5. 二次确认测试（确认执行 / 取消不执行）
# ══════════════════════════════════════════════════════════════════════
def test_confirmation():
    print("\n══ 5. 二次确认测试 ══")
    now = int(time.time())

    # 大额申购（高净值 12 万 > 私行 10 万阈值）
    s = f"cf1-{now}"
    r, _ = chat(ADVISOR, "给客户ID 10申购120000元的现金管理保本计划", s)
    check(
        "确认:大额申购→requires_confirmation",
        r.get("requires_confirmation") is True,
        str(r.get("summary"))[:60],
    )
    cid = r.get("data", {}).get("confirmation_id")
    check("确认:返回confirmation_id", bool(cid), str(cid))

    # 确认执行
    if cid:
        body = {
            "message": "确认",
            "session_id": s,
            "decision": "confirm",
            "confirmation_id": cid,
        }
        r, code = post("/api/v1/chat", body, ADVISOR)
        d = r.get("data", {})
        check(
            "确认:确认后执行成功",
            d.get("status") == "success",
            str(d.get("summary"))[:60],
        )

    # 大额转账取消（高净值转出=私行，确认阈值50万，用60万触发）
    s = f"cf2-{now}"
    r, _ = chat(MANAGER, "把客户ID 10的600000元转到客户ID 31", s)
    check(
        "确认:大额转账→requires_confirmation",
        r.get("requires_confirmation") is True,
        str(r.get("summary"))[:60],
    )
    cid = r.get("data", {}).get("confirmation_id")
    if cid:
        body = {
            "message": "取消",
            "session_id": s,
            "decision": "cancel",
            "confirmation_id": cid,
        }
        r, code = post("/api/v1/chat", body, MANAGER)
        d = r.get("data", {})
        check(
            "确认:取消→不执行",
            d.get("cancelled") is True or "已取消" in (d.get("summary") or ""),
            str(d.get("summary"))[:60],
        )


# ══════════════════════════════════════════════════════════════════════
# 6. 风险测试（大额事件 + 风控预警联动）
# ══════════════════════════════════════════════════════════════════════
def test_risk():
    print("\n══ 6. 风险测试 ══")
    now = int(time.time())

    # 大额申购 → 触发二次确认（私行大额事件阈值50万，用60万验证风控联动）
    s = f"risk1-{now}"
    r, _ = chat(ADVISOR, "给客户ID 10申购600000元的现金管理保本计划", s)
    check(
        "风险:大额申购→触发确认",
        r.get("requires_confirmation") is True,
        str(r.get("summary"))[:60],
    )
    cid = r.get("data", {}).get("confirmation_id")
    if cid:
        body = {
            "message": "确认",
            "session_id": s,
            "decision": "confirm",
            "confirmation_id": cid,
        }
        r2, _ = post("/api/v1/chat", body, ADVISOR)
        d2 = r2.get("data", {})
        summary2 = d2.get("summary") or ""
        check(
            "风险:大额申购→风控联动提示",
            "风控" in summary2 or "已同步" in summary2,
            summary2[:80],
        )

    # 风控扫描 → risk_monitor（返回预警/规则命中）
    s = f"risk2-{now}"
    r, _ = chat(RISK, "风控扫描零售投资者交易记录", s)
    check(
        "风险:风控扫描→risk_monitor",
        r.get("agent") == "risk_monitor",
        str(r.get("agent")),
    )

    # 风控专员可疑上报 → 生成红色预警 + 工单
    r, _ = chat(RISK, "上报客户ID 31的可疑交易", f"risk3-{now}", "business_operator")
    summary = r.get("summary") or ""
    check("风险:可疑上报→预警+工单", "可疑交易上报完成" in summary, summary[:60])

    # 超管查看风控预警/工单
    r, code = get("/api/risk/alerts", SUPER)
    check("风险:预警列表可查", r.get("success") is True, f"code={code}")
    r, code = get("/api/risk/work-orders", SUPER)
    check("风险:工单列表可查", r.get("success") is True, f"code={code}")


# ══════════════════════════════════════════════════════════════════════
def main():
    setup()
    test_permission()
    test_fallback()
    test_failure()
    test_ask_params()
    test_confirmation()
    test_risk()
    print(f"\n{'=' * 50}")
    print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败项:")
        for name in FAIL:
            print(f"  ❌ {name}")
        sys.exit(1)
    print("🎉 全部通过")


if __name__ == "__main__":
    main()
