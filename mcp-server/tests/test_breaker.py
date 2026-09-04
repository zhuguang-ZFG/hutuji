"""breaker 契约测试（Phase-3）。"""
from __future__ import annotations

import logging
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


def test_redis_breaker_list_open_skips_sample_zsets() -> None:
    """scan_iter 前缀 hutuji:breaker:* 会命中 samples ZSET；GET 会 ResponseError。"""

    class _FakeClient:
        def scan_iter(self, match=None):
            yield "hutuji:breaker:samples:agnes"
            yield "hutuji:breaker:agnes"

        def get(self, key):
            if str(key).startswith("hutuji:breaker:samples:"):
                raise AssertionError("不得 GET samples ZSET")
            return '{"state": "open"}'

    store = breaker.RedisBreakerStore.__new__(breaker.RedisBreakerStore)
    store._client = _FakeClient()
    assert store.list_open_backends() == ["agnes"]


def test_breaker_success_closes_from_half_open(memory_breaker, monkeypatch: pytest.MonkeyPatch) -> None:
    store = breaker.MemoryBreakerStore()
    now = time.time()
    store.set_state("agnes", {"state": "half_open", "opened_at": now - 200})
    monkeypatch.setattr(breaker, "get_store", lambda: store)
    breaker.record_outcome("agnes", "ok")
    state = store.get_state("agnes")
    assert state.get("state") == "closed"


class _DeadStore:
    """全方法抛 OSError，模拟 Redis 掉线。"""

    def record_sample(self, *args, **kwargs):
        raise OSError("redis down")

    def get_state(self, backend_id):
        raise OSError("redis down")

    def set_state(self, backend_id, state):
        raise OSError("redis down")

    def list_open_backends(self):
        raise OSError("redis down")


def test_breaker_corrupt_state_logs_and_closes(caplog) -> None:
    """状态 JSON 损坏：按 closed 处理并告警（损坏可见，不再静默）。"""

    class _FakeClient:
        def get(self, key):
            return "{损坏"

    store = breaker.RedisBreakerStore.__new__(breaker.RedisBreakerStore)
    store._client = _FakeClient()
    with caplog.at_level(logging.WARNING, logger="hutuji_mcp.breaker"):
        state = store.get_state("agnes")
    assert state.get("state") == "closed"
    assert any("损坏" in r.getMessage() for r in caplog.records)


def test_breaker_record_outcome_survives_store_failure(
    memory_breaker, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """熔断存储（Redis）掉线不得砸付费生成主链：记录丢失只告警、按无闸放行。"""
    monkeypatch.setattr(breaker, "get_store", lambda: _DeadStore())
    breaker.reset_breaker_for_tests()
    with caplog.at_level(logging.WARNING, logger="hutuji_mcp.breaker"):
        breaker.record_outcome("agnes", "ok")
    assert any("放行" in r.getMessage() for r in caplog.records)


def test_breaker_is_open_failopen_when_store_down(
    memory_breaker, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """enforce 开启下存储故障：is_open 放行（可用性优先）并告警。"""
    base = load_settings()
    patched = Settings(**{**base.__dict__, "breaker_enforce": True})
    monkeypatch.setattr("hutuji_mcp.config.settings", patched)
    monkeypatch.setattr(breaker, "get_store", lambda: _DeadStore())
    breaker.reset_breaker_for_tests()
    with caplog.at_level(logging.WARNING, logger="hutuji_mcp.breaker"):
        assert breaker.is_open("agnes") is False
    assert any("放行" in r.getMessage() for r in caplog.records)
