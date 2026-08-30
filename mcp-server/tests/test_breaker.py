"""breaker 契约测试（Phase-3）。"""
from __future__ import annotations

import time

import pytest

from hutuji_mcp import breaker
from hutuji_mcp.config import Settings, load_settings


@pytest.fixture
def memory_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    base = load_settings()
    patched = Settings(
        **{
            **base.__dict__,
            "draw_queue_url": "memory://",
            "breaker_enforce": False,
            "breaker_window_sec": 60,
            "breaker_fail_ratio": 0.5,
            "breaker_open_sec": 120,
            "breaker_consecutive_fail": 3,
        }
    )
    monkeypatch.setattr("hutuji_mcp.config.settings", patched)
    monkeypatch.setattr("hutuji_mcp.server.settings", patched)
    breaker.reset_breaker_for_tests()


def test_breaker_shadow_records_without_blocking(memory_breaker) -> None:
    breaker.record_outcome("agnes", "error")
    breaker.record_outcome("agnes", "error")
    breaker.record_outcome("agnes", "error")
    assert breaker.is_open("agnes") is False
    assert breaker.should_skip_backend("agnes") is False


def test_breaker_enforce_opens_after_consecutive_failures(
    memory_breaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = load_settings()
    patched = Settings(
        **{
            **base.__dict__,
            "draw_queue_url": "memory://",
            "breaker_enforce": True,
            "breaker_consecutive_fail": 3,
        }
    )
    monkeypatch.setattr("hutuji_mcp.config.settings", patched)
    monkeypatch.setattr("hutuji_mcp.server.settings", patched)
    breaker.reset_breaker_for_tests()

    for _ in range(3):
        breaker.record_outcome("agnes", "http_5xx")
    assert breaker.is_open("agnes") is True
    assert breaker.should_skip_backend("agnes") is True


def test_breaker_success_closes_from_half_open(memory_breaker, monkeypatch: pytest.MonkeyPatch) -> None:
    store = breaker.MemoryBreakerStore()
    now = time.time()
    store.set_state("agnes", {"state": "half_open", "opened_at": now - 200})
    monkeypatch.setattr(breaker, "get_store", lambda: store)
    breaker.record_outcome("agnes", "ok")
    state = store.get_state("agnes")
    assert state.get("state") == "closed"
