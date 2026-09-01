import pytest

from app.agents.graph import route_message

pytestmark = pytest.mark.unit


def test_real_chinese_customer_is_forced_to_customer_service():
    agents = route_message("帮我推荐一个适合C2客户的固收产品", is_customer=True)

    assert agents == ["customer_service"]


def test_real_chinese_advisor_recommendation_routes_to_investment_advisor():
    agents = route_message(
        "帮零售投资者推荐一个稳健理财产品",
        employee_role="financial_advisor",
    )

    assert agents == ["investment_advisor"]


def test_real_chinese_risk_scan_routes_to_risk_monitor():
    agents = route_message(
        "风控扫描一下零售投资者的交易记录",
        employee_role="risk_specialist",
    )

    assert agents == ["risk_monitor"]


def test_real_chinese_analytics_query_routes_to_data_analyst():
    agents = route_message("统计一下在售产品数量", employee_role="auditor")

    assert agents == ["data_analyst"]


def test_real_chinese_purchase_command_routes_to_business_operator():
    agents = route_message(
        "帮零售投资者申购5000元的固收产品",
        employee_role="financial_advisor",
    )

    assert agents == ["business_operator"]


def test_real_chinese_confirmation_routes_to_business_operator():
    agents = route_message("确认", employee_role="customer_manager")

    assert agents == ["business_operator"]
