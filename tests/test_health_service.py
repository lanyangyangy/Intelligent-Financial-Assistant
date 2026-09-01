from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.infrastructure.qwen import QwenProvider
from app.services.health_service import HealthService

pytestmark = pytest.mark.unit


def test_qwen_config_reports_chat_model_without_claiming_live_health():
    provider = object.__new__(QwenProvider)
    provider._client = object()
    provider.settings = SimpleNamespace(qwen_chat_model="qwen-plus")

    result = asyncio.run(provider.check_config())

    assert result == {
        "status": "configured",
        "model": "qwen-plus",
        "verified": "false",
    }


def test_health_service_preserves_optional_component_semantics():
    database = SimpleNamespace(check=AsyncMock(return_value={"status": "ok"}))
    redis = SimpleNamespace(check=AsyncMock(return_value={"status": "ok"}))
    qwen = SimpleNamespace(
        check_config=AsyncMock(return_value={"status": "configured"}),
        check_embedding=AsyncMock(return_value={"status": "configured"}),
    )
    graph = SimpleNamespace(check=AsyncMock(return_value={"status": "skipped"}))
    service = HealthService(database, redis, qwen, graph, SimpleNamespace())

    checks = asyncio.run(service.check_all())

    assert checks == {
        "postgresql": {"status": "ok"},
        "redis": {"status": "ok"},
        "qwen": {"status": "configured"},
        "embedding": {"status": "configured"},
        "neo4j": {"status": "skipped"},
    }
