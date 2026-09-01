"""客户 ID 优先参数提取测试（数据库 UUID 主键）。"""
import pytest

from app.services.operator_parser import parse_operation

pytestmark = pytest.mark.unit

UUID = "860418e7-9de7-4ac1-b4e1-7aa1293ec2b1"
UUID2 = "cf65c718-dbe1-4f51-9efb-47de386a96c6"


def test_purchase_db_uuid():
    """用数据库 UUID 客户 ID 申购。"""
    p = parse_operation(f"帮客户ID {UUID} 申购10000元的国债逆回购优选")
    assert p.params["customer_id"] == UUID
    assert "customer_name" not in p.params


def test_transfer_db_uuid_source_target():
    p = parse_operation(f"把客户ID {UUID} 的50000元转到客户ID {UUID2} 账户")
    assert p.params["source_customer_id"] == UUID
    assert p.params["target_customer_id"] == UUID2


def test_redeem_db_uuid():
    p = parse_operation(f"赎回客户ID {UUID} 持有的国债逆回购优选全部份额")
    assert p.params["customer_id"] == UUID


def test_reassess_db_uuid():
    p = parse_operation(f"客户ID {UUID} 需要重新风险评估")
    assert p.params["customer_id"] == UUID


def test_info_update_db_uuid():
    p = parse_operation(f"更新客户ID {UUID} 手机号13812345678")
    assert p.params["customer_id"] == UUID


def test_workorder_db_uuid():
    p = parse_operation(f"为客户ID {UUID} 创建工单内容是投诉")
    assert p.params["customer_id"] == UUID


def test_suspicious_db_uuid():
    p = parse_operation(f"上报客户ID {UUID} 的可疑交易")
    assert p.params["customer_id"] == UUID
