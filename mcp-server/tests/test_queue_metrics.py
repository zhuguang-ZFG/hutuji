"""queue_metrics 契约测试。"""
from __future__ import annotations

import asyncio

import pytest

from hutuji_mcp import queue_metrics
from hutuji_mcp.config import Settings, load_settings


@pytest.fixture
def memory_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    base = load_settings()
    patched = Settings(
        **{
            **base.__dict__,
            "draw_queue_url": "memory://",
            "draw_queue_max_depth": 10,
            "draw_enqueue_per_second": 100,
        }
    )
    monkeypatch.setattr("hutuji_mcp.config.settings", patched)
    monkeypatch.setattr("hutuji_mcp.server.settings", patched)
    monkeypatch.setattr("hutuji_mcp.draw_queue.settings", patched)
    from hutuji_mcp import draw_queue

    draw_queue.reset_backend_for_tests()


def test_collect_metrics_memory_queue(memory_queue) -> None:
    payload = asyncio.run(queue_metrics.collect_metrics(role="test"))
    assert payload["role"] == "test"
    assert payload.get("draw_queue_depth") == 0
