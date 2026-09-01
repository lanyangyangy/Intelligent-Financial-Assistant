"""二次确认响应词路由测试：纯"确认/取消"文本路由到业务操作 Agent（防客服闲聊抢占）。"""
import pytest

from app.agents.graph import route_message

pytestmark = pytest.mark.unit


def test_customer_manager_confirm_routes_to_operator():
    agents = route_message("确认", employee_role="customer_manager")
    assert agents == ["business_operator"]


def test_customer_manager_confirm_execute_routes_to_operator():
    agents = route_message("确认执行", employee_role="customer_manager")
    assert agents == ["business_operator"]


def test_customer_manager_cancel_routes_to_operator():
    agents = route_message("取消", employee_role="customer_manager")
    assert agents == ["business_operator"]


def test_advisor_confirm_routes_to_operator():
    agents = route_message("确认", employee_role="financial_advisor")
    assert agents == ["business_operator"]


def test_risk_specialist_confirm_routes_to_operator():
    agents = route_message("确认", employee_role="risk_specialist")
    assert agents == ["business_operator"]


def test_auditor_confirm_not_routed_to_operator():
    """审计不在 business_operator 角色集：确认文本不路由 operator。"""
    agents = route_message("确认", employee_role="auditor")
    assert agents != ["business_operator"]


def test_long_confirm_phrase_not_misrouted():
    """长句含"确认"（如"确认收货"）不应误路由到 operator。"""
    agents = route_message("如何取消订单并退款", employee_role="customer_manager")
    assert agents != ["business_operator"]


def test_customer_confirm_forced_to_service():
    """客户回复"确认"：强制客服（客户无业务操作权限）。"""
    agents = route_message("确认", is_customer=True)
    assert agents == ["customer_service"]
