"""用户主键/外键类型：整数自增 + 宽容绑定。

users.id 从 UUID 字符串改为整数自增主键后，历史/外部传入的数字字符串
（如 JWT sub、路径参数 "123"、解析器提取的客户 ID）在比较与写入时
自动转换为 int，避免 asyncpg 严格类型导致的 `integer = text` 报错。

注意：非数字字符串（如用户名 "liwei"）不会转换，由调用方保证先按
用户名查询，不能直接与 User.id 比较。
"""

from __future__ import annotations

from sqlalchemy.types import Integer, TypeDecorator


class UserId(TypeDecorator):
    """宽容绑定的整数用户 ID 类型（比较/写入时数字字符串自动转 int）。"""

    impl = Integer
    cache_ok = True

    def coerce_compared_value(self, op, value):  # noqa: D401
        """比较时保持 UserId 自身类型，使 bind_processor 生效（数字字符串→int）。

        不能返回 Integer()：那样会跳过本类型的 bind_processor，
        数字字符串（如路径参数 '1'）会原样传给 asyncpg 导致
        `invalid input for query argument $1: '1'` DataError。
        """
        return self

    def bind_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int):
                return value
            try:
                return int(str(value))
            except (TypeError, ValueError):
                # 非数字字符串（如用户名）：交还给数据库，由调用方保证不会出现
                return value

        return process
