"""breaker 契约测试（Phase-3）。"""
from __future__ import annotations

import logging
import time
import threading

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
    assert breaker.acquire_backend("agnes") is not None


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
    assert breaker.acquire_backend("agnes") is None


def test_redis_breaker_list_open_skips_auxiliary_keys() -> None:
    """状态前缀会命中 samples ZSET 与 probe lock；两者都不得按 JSON 状态 GET。"""

    class _FakeClient:
        def scan_iter(self, match=None):
            yield "hutuji:breaker:samples:agnes"
            yield "hutuji:breaker:probe:agnes"
            yield "hutuji:breaker:agnes"

        def get(self, key):
            if str(key).startswith((
                "hutuji:breaker:samples:", "hutuji:breaker:probe:"
            )):
                raise AssertionError("不得 GET samples ZSET 或 probe lock")
            return '{"state": "open"}'

    store = breaker.RedisBreakerStore.__new__(breaker.RedisBreakerStore)
    store._client = _FakeClient()
    assert store.list_open_backends() == ["agnes"]


def test_redis_breaker_has_bounded_network_wait(monkeypatch):
    import redis

    seen = {}
    monkeypatch.setattr(redis, "from_url", lambda url, **kw: seen.update(kw))
    breaker.RedisBreakerStore("redis://localhost")
    assert 0 < seen["socket_connect_timeout"] <= 1
    assert 0 < seen["socket_timeout"] <= 1


def test_image_race_runs_breaker_io_off_event_loop(monkeypatch):
    import asyncio
    import threading
    from hutuji_mcp import bitmap_svg, portal_settings

    loop_thread = threading.get_ident()
    observed = []

    def acquire(backend_id):
        assert threading.get_ident() != loop_thread
        observed.append("acquire")
        return breaker.BackendPermit()

    def record(*args, **kwargs):
        assert threading.get_ident() != loop_thread
        observed.append("record")

    def release(*args):
        assert threading.get_ident() != loop_thread
        observed.append("release")

    async def image(*args):
        return "https://example.test/image.png"

    monkeypatch.setattr(portal_settings, "portal_image_racers", lambda _: [])
    monkeypatch.setattr(bitmap_svg, "_image_backend_enabled", lambda name: name == "agnes")
    monkeypatch.setattr(bitmap_svg, "_request_agnes_image_url", image)
    monkeypatch.setattr(breaker, "acquire_backend", acquire)
    monkeypatch.setattr(breaker, "record_outcome", record)
    monkeypatch.setattr(breaker, "release_backend", release)
    assert asyncio.run(bitmap_svg._race_image_backends(None, "测试")) == "https://example.test/image.png"
    assert observed == ["acquire", "record", "release"]


def test_breaker_success_closes_from_half_open(memory_breaker, monkeypatch: pytest.MonkeyPatch) -> None:
    store = breaker.MemoryBreakerStore()
    now = time.time()
    store.set_state("agnes", {"state": "half_open", "opened_at": now - 200})
    monkeypatch.setattr(breaker, "get_store", lambda: store)
    breaker.record_outcome("agnes", "ok")
    state = store.get_state("agnes")
    assert state.get("state") == "closed"


def test_breaker_half_open_allows_exactly_one_probe(
    memory_breaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = load_settings()
    patched = Settings(
        **{
            **base.__dict__,
            "draw_queue_url": "memory://",
            "breaker_enforce": True,
            "breaker_open_sec": 120,
        }
    )
    monkeypatch.setattr("hutuji_mcp.config.settings", patched)
    monkeypatch.setattr("hutuji_mcp.server.settings", patched)
    store = breaker.MemoryBreakerStore()
    store.set_state(
        "agnes", {"state": "open", "opened_at": time.time() - 121}
    )
    monkeypatch.setattr(breaker, "get_store", lambda: store)
    breaker.reset_breaker_for_tests()

    permit = breaker.acquire_backend("agnes")
    assert permit is not None and permit.probe_token
    assert breaker.acquire_backend("agnes") is None
    with breaker._cache_lock:
        breaker._cache.clear()
    assert breaker.acquire_backend("agnes") is None
    breaker.record_outcome("agnes", "ok", permit=permit)
    regular = breaker.acquire_backend("agnes")
    assert regular is not None and regular.probe_token is None

    store.set_state(
        "agnes", {"state": "open", "opened_at": time.time() - 121}
    )
    breaker.reset_breaker_for_tests()
    failed_probe = breaker.acquire_backend("agnes")
    assert failed_probe is not None and failed_probe.probe_token
    breaker.record_outcome("agnes", "http_5xx", permit=failed_probe)
    assert breaker.acquire_backend("agnes") is None


def test_regular_outcome_cannot_undo_fresh_probe_failure(
    memory_breaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = load_settings()
    patched = Settings(
        **{
            **base.__dict__,
            "draw_queue_url": "memory://",
            "breaker_enforce": True,
            "breaker_open_sec": 120,
        }
    )
    monkeypatch.setattr("hutuji_mcp.config.settings", patched)
    monkeypatch.setattr("hutuji_mcp.server.settings", patched)
    regular_read = threading.Event()
    probe_written = threading.Event()

    class _InterleavingStore(breaker.MemoryBreakerStore):
        def get_state(self, backend_id: str) -> dict[str, object]:
            state = super().get_state(backend_id)
            if threading.current_thread().name == "regular-outcome":
                regular_read.set()
                if not probe_written.wait(timeout=2):
                    raise AssertionError("探针失败未在时限内写回")
            return state

    store = _InterleavingStore()
    store.set_state(
        "agnes", {"state": "open", "opened_at": time.time() - 121}
    )
    monkeypatch.setattr(breaker, "get_store", lambda: store)
    breaker.reset_breaker_for_tests()
    permit = breaker.acquire_backend("agnes")
    assert permit is not None and permit.probe_token
    assert store.get_state("agnes").get("state") == "half_open"
    errors: list[BaseException] = []

    def _record_regular_outcome() -> None:
        try:
            breaker.record_outcome("agnes", "ok")
        except BaseException as exc:  # pragma: no cover - 只为跨线程回传断言
            errors.append(exc)

    worker = threading.Thread(
        target=_record_regular_outcome, name="regular-outcome"
    )
    worker.start()
    try:
        assert regular_read.wait(timeout=2)
        breaker.record_outcome(
            "agnes", "http_5xx", permit=permit
        )
        fresh_open = store.get_state("agnes")
        assert fresh_open.get("state") == "open"
        assert fresh_open.get("reason") == "half_open_fail"
    finally:
        probe_written.set()
        worker.join(timeout=2)

    assert worker.is_alive() is False
    assert errors == []
    assert store.get_state("agnes") == fresh_open
    assert breaker.acquire_backend("agnes") is None


def test_real_redis_stale_transition_cannot_undo_probe_failure(
    memory_breaker, live_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = load_settings()
    patched = Settings(
        **{
            **base.__dict__,
            "breaker_enforce": True,
            "breaker_window_sec": 600,
            "breaker_open_sec": 120,
            "breaker_consecutive_fail": 3,
        }
    )
    monkeypatch.setattr("hutuji_mcp.config.settings", patched)
    monkeypatch.setattr("hutuji_mcp.server.settings", patched)
    url, client = live_redis
    regular_read = threading.Event()
    probe_written = threading.Event()
    regular_now = 1_000.0
    fresh_now = regular_now + 121.0
    real_monotonic = time.monotonic

    class _ThreadClock:
        @staticmethod
        def time() -> float:
            if threading.current_thread().name == "redis-regular-outcome":
                return regular_now
            return fresh_now

        monotonic = staticmethod(real_monotonic)

    class _InterleavingRedisStore(breaker.RedisBreakerStore):
        def get_state(self, backend_id: str) -> dict[str, object]:
            state = super().get_state(backend_id)
            if threading.current_thread().name == "redis-regular-outcome":
                regular_read.set()
                if not probe_written.wait(timeout=2):
                    raise AssertionError("Redis 探针失败未在时限内写回")
            return state

    monkeypatch.setattr(breaker, "time", _ThreadClock)
    store = _InterleavingRedisStore(url)
    store.record_sample("agnes", "http_5xx", now=regular_now - 2)
    store.record_sample("agnes", "http_5xx", now=regular_now - 1)
    monkeypatch.setattr(breaker, "get_store", lambda: store)
    breaker.reset_breaker_for_tests()
    errors: list[BaseException] = []

    def _record_regular_outcome() -> None:
        try:
            breaker.record_outcome("agnes", "http_5xx")
        except BaseException as exc:  # pragma: no cover - 只为跨线程回传断言
            errors.append(exc)

    worker = threading.Thread(
        target=_record_regular_outcome, name="redis-regular-outcome"
    )
    worker.start()
    try:
        assert regular_read.wait(timeout=2)
        store.set_state(
            "agnes",
            {"state": "half_open", "opened_at": fresh_now - 121},
        )
        permit = breaker.acquire_backend("agnes")
        assert permit is not None and permit.probe_token
        assert client.get("hutuji:breaker:probe:agnes") == permit.probe_token
        breaker.record_outcome(
            "agnes", "http_5xx", permit=permit
        )
        fresh_open = store.get_state("agnes")
        assert fresh_open == {
            "state": "open",
            "opened_at": fresh_now,
            "reason": "half_open_fail",
        }
    finally:
        probe_written.set()
        worker.join(timeout=2)

    assert worker.is_alive() is False
    assert errors == []
    with breaker._cache_lock:
        assert breaker._cache["agnes"].state == fresh_open
    assert store.get_state("agnes") == fresh_open
    assert client.get("hutuji:breaker:probe:agnes") is None
    assert breaker.acquire_backend("agnes") is None


def test_real_redis_open_cooldown_claim_is_atomic(live_redis) -> None:
    url, client = live_redis
    first = breaker.RedisBreakerStore(url)
    second = breaker.RedisBreakerStore(url)
    now = time.time()
    first.set_state("agnes", {"state": "open", "opened_at": now - 121})
    client.pexpire("hutuji:breaker:agnes", 239_000)
    barrier = threading.Barrier(3)
    claims: dict[str, bool] = {}

    def _claim(store: breaker.RedisBreakerStore, token: str) -> None:
        barrier.wait()
        claims[token] = store.claim_half_open_probe(
            "agnes", token, now=now, ttl=300
        )

    workers = [
        threading.Thread(target=_claim, args=(first, "first")),
        threading.Thread(target=_claim, args=(second, "second")),
    ]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join()

    assert sorted(claims.values()) == [False, True]
    winner = next(token for token, claimed in claims.items() if claimed)
    assert first.get_state("agnes")["state"] == "half_open"
    assert client.get("hutuji:breaker:probe:agnes") == winner
    assert client.pttl("hutuji:breaker:agnes") > client.pttl(
        "hutuji:breaker:probe:agnes"
    )
    first.release_half_open_probe("agnes", winner)
    client.pexpire("hutuji:breaker:agnes", 1_000)
    assert second.claim_half_open_probe(
        "agnes", "retry", now=now + 1, ttl=300
    )
    assert client.pttl("hutuji:breaker:agnes") > client.pttl(
        "hutuji:breaker:probe:agnes"
    )
    second.release_half_open_probe("agnes", "retry")
    assert client.get("hutuji:breaker:probe:agnes") is None


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
