from __future__ import annotations

import asyncio

import pytest

from app.api.chat_stream import (
    STREAM_CHUNK_SIZE,
    _can_fallback_after_stream_failure,
    _stream_text,
)

pytestmark = pytest.mark.unit


def test_non_customer_summary_is_split_into_multiple_stream_chunks() -> None:
    summary = "当前在售金融产品共6款。"

    async def collect() -> list[str]:
        return [chunk async for chunk in _stream_text(summary)]

    chunks = asyncio.run(collect())

    assert len(chunks) > 1
    assert all(0 < len(chunk) <= STREAM_CHUNK_SIZE for chunk in chunks)
    assert "".join(chunks) == summary


def test_empty_summary_does_not_emit_a_fake_delta() -> None:
    async def collect() -> list[str]:
        return [chunk async for chunk in _stream_text("")]

    assert asyncio.run(collect()) == []


def test_stream_failure_only_falls_back_before_any_delta_is_sent() -> None:
    assert _can_fallback_after_stream_failure([]) is True
    assert _can_fallback_after_stream_failure(["partial response"]) is False
