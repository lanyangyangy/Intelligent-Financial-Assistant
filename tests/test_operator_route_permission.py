"""业务操作 Agent 权限边界测试（功能设计文档 6.2 意图分类体系）。

6.2 权限要求：
  产品申购 purchase   → 理财顾问
  产品赎回 redeem     → 理财顾问（份额>1万份 二次确认）
  转账 transfer       → 客户经理（金额>5万 二次确认）
  风评重做 risk_reassess → 理财顾问
  信息更新 info_update → 客户经理
  产品查询 product_query → 员工/顾问（全部内部员工只读）
  可疑上报 suspicious_report → 风控专员
  工单创建 workorder_create → 客户经理
"""
import pytest

from app.agents.graph import AGENT_REQUIRED_ROLES, route_message, staff_agent_allowed
from app.agents.operations_agent import PERMISSION_MATRIX

pytestmark = pytest.mark.unit


# ── 权限矩阵（6.2 意图分类） ──────────────────────────────────────────

def test_permission_matrix_follows_62():
    """PERMISSION_MATRIX 严格对齐功能设计文档 6.2。"""
    assert PERMISSION_MATRIX["purchase"] == {"financial_advisor"}
    assert PERMISSION_MATRIX["redeem"] == {"financial_advisor"}
    assert PERMISSION_MATRIX["risk_reassess"] == {"financial_advisor"}
    assert PERMISSION_MATRIX["transfer"] == {"customer_manager"}
    assert PERMISSION_MATRIX["info_update"] == {"customer_manager"}
    assert PERMISSION_MATRIX["workorder_create"] == {"customer_manager"}
    assert PERMISSION_MATRIX["suspicious_report"] == {"risk_specialist"}
    # 产品查询：员工/顾问（全部内部员工）
    assert PERMISSION_MATRIX["product_query"] == {
        "financial_advisor",
        "customer_manager",
        "risk_specialist",
        "auditor",
    }


def test_advisor_can_purchase_redeem_reassess():
    """理财顾问：申购/赎回/风评重做。"""
    assert "financial_advisor" in PERMISSION_MATRIX["purchase"]
    assert "financial_advisor" in PERMISSION_MATRIX["redeem"]
    assert "financial_advisor" in PERMISSION_MATRIX["risk_reassess"]
    assert "financial_advisor" not in PERMISSION_MATRIX["transfer"]
    assert "financial_advisor" not in PERMISSION_MATRIX["info_update"]
    assert "financial_advisor" not in PERMISSION_MATRIX["workorder_create"]
    assert "financial_advisor" not in PERMISSION_MATRIX["suspicious_report"]


def test_customer_manager_can_transfer_update_workorder():
    """客户经理：转账/信息更新/工单创建；不可申购。"""
    assert "customer_manager" in PERMISSION_MATRIX["transfer"]
    assert "customer_manager" in PERMISSION_MATRIX["info_update"]
    assert "customer_manager" in PERMISSION_MATRIX["workorder_create"]
    assert "customer_manager" not in PERMISSION_MATRIX["purchase"]
    assert "customer_manager" not in PERMISSION_MATRIX["redeem"]
    assert "customer_manager" not in PERMISSION_MATRIX["risk_reassess"]
    assert "customer_manager" not in PERMISSION_MATRIX["suspicious_report"]


def test_risk_specialist_only_suspicious_report():
    """风控专员：仅可疑上报（+产品查询只读）。"""
    assert "risk_specialist" in PERMISSION_MATRIX["suspicious_report"]
    assert "risk_specialist" not in PERMISSION_MATRIX["purchase"]
    assert "risk_specialist" not in PERMISSION_MATRIX["redeem"]
    assert "risk_specialist" not in PERMISSION_MATRIX["transfer"]
    assert "risk_specialist" not in PERMISSION_MATRIX["workorder_create"]


def test_auditor_read_only_product_query():
    """审计：仅产品查询（只读）。"""
    assert "auditor" in PERMISSION_MATRIX["product_query"]
    assert "auditor" not in PERMISSION_MATRIX["purchase"]
    assert "auditor" not in PERMISSION_MATRIX["transfer"]
    assert "auditor" not in PERMISSION_MATRIX["suspicious_report"]


# ── Agent 角色集合（6.2 三组操作人） ─────────────────────────────────

def test_business_operator_allows_three_roles():
    """业务操作 Agent：理财顾问 + 客户经理 + 风控专员（6.2 三组操作人）。"""
    assert AGENT_REQUIRED_ROLES["business_operator"] == {
        "customer_manager",
        "financial_advisor",
        "risk_specialist",
    }
    assert staff_agent_allowed("business_operator", employee_role="financial_advisor")
    assert staff_agent_allowed("business_operator", employee_role="customer_manager")
    assert staff_agent_allowed("business_operator", employee_role="risk_specialist")
    assert not staff_agent_allowed("business_operator", employee_role="auditor")


# ── 路由行为 ─────────────────────────────────────────────────────────

def test_advisor_purchase_routes_to_operator_allowed():
    """理财顾问申购：路由到 business_operator 且权限允许（6.2 purchase→理财顾问）。"""
    agents = route_message(
        "帮零售投资者申购3000元的国债逆回购优选",
        employee_role="financial_advisor",
    )
    assert agents == ["business_operator"]
    assert "financial_advisor" in PERMISSION_MATRIX["purchase"]


def test_customer_manager_purchase_routes_to_operator_denied_by_matrix():
    """客户经理申购：路由到 business_operator，但权限矩阵拒绝（6.2 purchase→理财顾问）。"""
    agents = route_message(
        "帮零售投资者申购3000元的国债逆回购优选",
        employee_role="customer_manager",
    )
    assert agents == ["business_operator"]
    assert "customer_manager" not in PERMISSION_MATRIX["purchase"]


def test_advisor_transfer_denied_by_matrix():
    """理财顾问转账：路由到 operator，权限矩阵拒绝（6.2 transfer→客户经理）。"""
    agents = route_message(
        "把李伟的50000元转到张明账户",
        employee_role="financial_advisor",
    )
    assert agents == ["business_operator"]
    assert "financial_advisor" not in PERMISSION_MATRIX["transfer"]


def test_customer_manager_transfer_allowed():
    """客户经理转账：路由到 operator 且权限允许。"""
    agents = route_message(
        "把李伟的50000元转到张明账户",
        employee_role="customer_manager",
    )
    assert agents == ["business_operator"]
    assert "customer_manager" in PERMISSION_MATRIX["transfer"]


def test_auditor_purchase_explicitly_denied_not_fallback():
    """审计申购（不在 business_operator 角色集）：明确拒绝，不回退数据分析。"""
    agents = route_message(
        "帮零售投资者申购3000元的国债逆回购优选",
        employee_role="auditor",
    )
    assert agents == ["business_operator"]
    assert not staff_agent_allowed("business_operator", employee_role="auditor")


def test_auditor_view_work_orders_falls_back_to_analyst():
    """审计查工单（查询类）不回退到 business_operator，走数据分析。"""
    agents = route_message(
        "查看待处理工单",
        employee_role="auditor",
    )
    assert agents[0] == "data_analyst"


def test_risk_specialist_suspicious_report_routes_to_operator():
    agents = route_message(
        "上报零售投资者的可疑交易",
        employee_role="risk_specialist",
    )
    assert agents == ["business_operator"]
    assert "risk_specialist" in PERMISSION_MATRIX["suspicious_report"]


def test_risk_specialist_monitor_scenario_routes_to_risk_monitor():
    """需求 2.2 风控监测典型场景：风控专员描述"监测到50万大额转账触发规则"
    应路由到风控监测（不被"转账"业务操作词抢占）。"""
    agents = route_message(
        "监测到50万大额转账触发规则",
        employee_role="risk_specialist",
    )
    assert agents == ["risk_monitor"]


def test_risk_specialist_monitor_scan_routes_to_risk_monitor():
    agents = route_message(
        "风控扫描一下零售投资者的交易记录",
        employee_role="risk_specialist",
    )
    assert agents == ["risk_monitor"]


def test_advisor_monitor_scenario_falls_back():
    """非风控角色说"监测到..."：路由 risk_monitor 后按角色回退（数据分析/客服）。"""
    agents = route_message(
        "监测到50万大额转账触发规则",
        employee_role="financial_advisor",
    )
    assert agents[0] == "data_analyst"


def test_super_admin_has_all():
    """系统管理员：全部专用 Agent 可用。"""
    for agent in ("business_operator", "investment_advisor", "risk_monitor"):
        assert staff_agent_allowed(agent, employee_role="auditor", is_super_admin=True)
