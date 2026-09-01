"""pytest 公共 fixture：集成测试依赖探测与自动跳过。

测试分层约定：
- unit        纯单元测试，不访问网络、数据库和 Redis；
- integration 依赖真实 PostgreSQL/Redis（docker compose 启动）；
- e2e         依赖完整 HTTP 服务（tests/test_http_*.py 以脚本运行）。

默认执行 `pytest` 时，integration 测试在依赖服务不可用时会自动 skip，
保证无 Docker 环境也能稳定通过，不会因连接失败而报错。
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse

import pytest


def _tcp_available(url: str, timeout: float = 1.5) -> bool:
    """用 TCP 端口探测判断依赖服务是否可达（比真实连接更快更可靠）。"""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port
        if port is None:
            return False
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _redis_available() -> bool:
    try:
        from app.core.settings import get_settings

        return _tcp_available(get_settings().redis_url)
    except Exception:
        return False


def _postgres_available() -> bool:
    try:
        from app.core.settings import get_settings

        return _tcp_available(get_settings().database_url)
    except Exception:
        return False


@pytest.fixture
def requires_redis() -> bool:
    """Redis 可用性守卫：不可用时跳过依赖它的集成测试。"""
    if not _redis_available():
        pytest.skip("Redis 不可用，跳过集成测试（先执行 docker compose -f docker-compose.p0.yml up -d --wait）")
    return True


@pytest.fixture
def requires_postgres() -> bool:
    """PostgreSQL 可用性守卫：不可用时跳过依赖它的集成测试。"""
    if not _postgres_available():
        pytest.skip("PostgreSQL 不可用，跳过集成测试（先执行 docker compose -f docker-compose.p0.yml up -d --wait）")
    return True
