"""业务操作确定性解析器测试（移植自 Financial System-业务操作agent）。

差异适配：当前项目 customer_id 为字符串（UUID/编号），目标为 int。
"""

import pytest

from app.services.operator_parser import ParserError, parse_operation

pytestmark = pytest.mark.unit

CASES = [
    ("帮客户张三申购10万元稳健债券A", "purchase"),
    ("为客户李四购买2万的货币基金", "purchase"),
    ("赎回客户张三持有的稳健债券A全部份额", "redeem"),
    ("客户李四卖出货币基金5000份", "redeem"),
    ("把客户张三的50万转到客户李四账户", "transfer"),
    ("从客户A向客户B转账6万元", "transfer"),
    ("给客户张三重新做风险评估", "reassessment"),
    ("客户李四需要重做风评", "reassessment"),
    ("把客户张三的手机号改成13812345678", "information_update"),
    ("更新客户李四电话为13912345678", "information_update"),
    ("查询稳健债券A的最新净值", "product_query"),
    ("查一下货币基金产品详情", "product_query"),
    ("上报客户张三的可疑交易", "suspicious_report"),
    ("将客户李四标记为可疑", "suspicious_report"),
    ("为客户张三创建投诉工单，内容是扣费争议", "work_order_create"),
    ("给李四建一个服务工单", "work_order_create"),
]


@pytest.mark.parametrize(("message", "expected"), CASES)
def test_eight_operator_intents(message: str, expected: str) -> None:
    assert parse_operation(message).action == expected


def test_required_parameters_are_normalized() -> None:
    purchase = parse_operation("帮客户张三申购10万元稳健债券A")
    transfer = parse_operation("把客户张三的50万转到客户李四账户")
    update = parse_operation("把客户张三的手机号改成13812345678")

    assert purchase.params == {
        "customer_name": "张三",
        "amount": "100000.00",
        "product_name": "稳健债券A",
    }
    assert transfer.params["amount"] == "500000.00"
    assert transfer.params["source_customer_name"] == "张三"
    assert transfer.params["target_customer_name"] == "李四"
    assert update.params["phone"] == "13812345678"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "为客户李四购买2万的货币基金",
            {"customer_name": "李四", "amount": "20000.00"},
        ),
        (
            "赎回客户张三持有的稳健债券A全部份额",
            {
                "customer_name": "张三",
                "product_name": "稳健债券A",
                "shares": None,
                "redeem_all": True,
            },
        ),
        (
            "客户李四卖出货币基金5000份",
            {"customer_name": "李四", "product_name": "货币基金", "shares": 5000.0},
        ),
        (
            "从客户A向客户B转账6万元",
            {
                "source_customer_name": "A",
                "target_customer_name": "B",
                "amount": "60000.00",
            },
        ),
        ("给客户张三重新做风险评估", {"customer_name": "张三"}),
        ("更新客户李四电话为13912345678", {"customer_name": "李四"}),
        ("将客户李四标记为可疑", {"customer_name": "李四"}),
        ("为客户张三创建投诉工单，内容是扣费争议", {"customer_name": "张三"}),
        ("给客户李四建一个服务工单", {"customer_name": "李四"}),
    ],
)
def test_parameter_accuracy_matrix(message: str, expected: dict) -> None:
    params = parse_operation(message).params
    for key, value in expected.items():
        assert params.get(key) == value, (
            f"{key}: expected {value!r}, got {params.get(key)!r}"
        )


def test_purchase_parser_prefers_explicit_customer_id() -> None:
    operation = parse_operation("帮客户编号1001申购2万元稳健债券A")

    assert operation.action == "purchase"
    assert operation.params == {
        "customer_id": "1001",  # 当前项目 UUID 字符串兼容
        "amount": "20000.00",
        "product_name": "稳健债券A",
    }


def test_amount_unit_conversion() -> None:
    """万元/万/元 单位换算。"""
    assert parse_operation("帮客户张三申购1万元产品").params["amount"] == "10000.00"
    assert parse_operation("帮客户张三申购2万产品").params["amount"] == "20000.00"
    assert parse_operation("帮客户张三申购3000元产品").params["amount"] == "3000.00"


def test_unknown_instruction_raises() -> None:
    with pytest.raises(ParserError):
        parse_operation("今天天气怎么样")
