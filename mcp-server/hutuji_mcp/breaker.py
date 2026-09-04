"""生图通道熔断（Phase-3）；3a 默认 shadow（记事件不过滤），3b enforce。"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

BREAKER_PREFIX = "hutuji:breaker:"
SAMPLE_PREFIX = "hutuji:breaker:samples:"


def _settings():
    from . import server

    return server.settings


def breaker_enforce_enabled() -> bool:
    return bool(getattr(_settings(), "breaker_enforce", False))


class BreakerStore(Protocol):
    def record_sample(self, backend_id: str, outcome: str, *, now: float) -> None: ...

    def get_state(self, backend_id: str) -> dict[str, Any]: ...

    def set_state(self, backend_id: str, state: dict[str, Any]) -> None: ...

    def list_open_backends(self) -> list[str]: ...


@dataclass
class _CacheEntry:
    state: dict[str, Any]
    at: float


class MemoryBreakerStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, dict[str, Any]] = {}
        self._samples: dict[str, list[tuple[float, str]]] = {}

    def record_sample(self, backend_id: str, outcome: str, *, now: float) -> None:
        with self._lock:
            samples = self._samples.setdefault(backend_id, [])
            samples.append((now, outcome))
            window = getattr(_settings(), "breaker_window_sec", 60)
            cutoff = now - window
            self._samples[backend_id] = [(t, o) for t, o in samples if t >= cutoff]

    def get_state(self, backend_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._states.get(backend_id, {"state": "closed"}))

    def set_state(self, backend_id: str, state: dict[str, Any]) -> None:
        with self._lock:
            self._states[backend_id] = dict(state)

    def list_open_backends(self) -> list[str]:
        with self._lock:
            return [
                bid
                for bid, st in self._states.items()
                if st.get("state") in ("open", "half_open")
            ]


class RedisBreakerStore:
    def __init__(self, url: str) -> None:
        import redis

        self._client = redis.from_url(url, decode_responses=True)

    def record_sample(self, backend_id: str, outcome: str, *, now: float) -> None:
        key = f"{SAMPLE_PREFIX}{backend_id}"
        window = getattr(_settings(), "breaker_window_sec", 60)
        member = f"{now}:{outcome}"
        pipe = self._client.pipeline()
        pipe.zadd(key, {member: now})
        pipe.zremrangebyscore(key, 0, now - window)
        pipe.expire(key, window + 30)
        pipe.execute()

    def get_state(self, backend_id: str) -> dict[str, Any]:
        raw = self._client.get(f"{BREAKER_PREFIX}{backend_id}")
        if not raw:
            return {"state": "closed"}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("breaker 状态 JSON 损坏按 closed 处理 backend=%s", backend_id)
            return {"state": "closed"}
        return data if isinstance(data, dict) else {"state": "closed"}

    def set_state(self, backend_id: str, state: dict[str, Any]) -> None:
        ttl = max(300, getattr(_settings(), "breaker_open_sec", 120) * 3)
        self._client.set(
            f"{BREAKER_PREFIX}{backend_id}",
            json.dumps(state, ensure_ascii=False),
            ex=ttl,
        )

    def list_open_backends(self) -> list[str]:
        open_ids: list[str] = []
        for key in self._client.scan_iter(match=f"{BREAKER_PREFIX}*"):
            if str(key).startswith(SAMPLE_PREFIX):
                continue
            raw = self._client.get(key)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("breaker 状态 JSON 损坏跳过 key=%s", str(key))
                continue
            if data.get("state") in ("open", "half_open"):
                open_ids.append(str(key).removeprefix(BREAKER_PREFIX))
        return open_ids

    async def _recent_samples_async(self, backend_id: str, *, now: float) -> list[str]:
        key = f"{SAMPLE_PREFIX}{backend_id}"
        window = getattr(_settings(), "breaker_window_sec", 60)
        rows = await asyncio.to_thread(
            self._client.zrangebyscore, key, now - window, now
        )
        outcomes: list[str] = []
        for row in rows:
            text = str(row)
            if ":" in text:
                outcomes.append(text.split(":", 1)[1])
        return outcomes


_store: BreakerStore | None = None
_cache: dict[str, _CacheEntry] = {}
_cache_lock = threading.Lock()


def get_store() -> BreakerStore:
    global _store
    if _store is not None:
        return _store
    url = getattr(_settings(), "draw_queue_url", "").strip()
    if url.startswith("redis://") or url.startswith("rediss://"):
        _store = RedisBreakerStore(url)
    else:
        _store = MemoryBreakerStore()
    return _store


def reset_breaker_for_tests() -> None:
    global _store
    _store = None
    with _cache_lock:
        _cache.clear()


def _recent_samples(store: BreakerStore, backend_id: str, *, now: float) -> list[str]:
    if isinstance(store, MemoryBreakerStore):
        with store._lock:
            samples = store._samples.get(backend_id, [])
            window = getattr(_settings(), "breaker_window_sec", 60)
            cutoff = now - window
            return [o for t, o in samples if t >= cutoff]
    if isinstance(store, RedisBreakerStore):
        key = f"{SAMPLE_PREFIX}{backend_id}"
        window = getattr(_settings(), "breaker_window_sec", 60)
        rows = store._client.zrangebyscore(key, now - window, now)
        outcomes: list[str] = []
        for row in rows:
            text = str(row)
            if ":" in text:
                outcomes.append(text.split(":", 1)[1])
        return outcomes
    return []


def _is_failure(outcome: str) -> bool:
    return outcome not in ("ok",)


def _evaluate_state(backend_id: str, store: BreakerStore, *, now: float) -> dict[str, Any]:
    state = store.get_state(backend_id)
    current = str(state.get("state") or "closed")
    open_sec = getattr(_settings(), "breaker_open_sec", 120)
    fail_ratio = getattr(_settings(), "breaker_fail_ratio", 0.5)
    consecutive_limit = getattr(_settings(), "breaker_consecutive_fail", 3)

    if current == "open":
        opened_at = float(state.get("opened_at") or 0)
        if now - opened_at >= open_sec:
            return {"state": "half_open", "opened_at": opened_at}
        return state

    samples = _recent_samples(store, backend_id, now=now)
    if not samples:
        return {"state": "closed"}

    failures = sum(1 for s in samples if _is_failure(s))
    ratio = failures / len(samples)
    consecutive = 0
    for outcome in reversed(samples):
        if _is_failure(outcome):
            consecutive += 1
        else:
            break

    would_open = len(samples) >= 5 and ratio >= fail_ratio
    would_open = would_open or consecutive >= consecutive_limit

    if current == "half_open":
        last = samples[-1] if samples else "ok"
        if _is_failure(last):
            return {"state": "open", "opened_at": now, "reason": "half_open_fail"}
        return {"state": "closed"}

    if would_open:
        return {"state": "open", "opened_at": now, "reason": "threshold"}
    return {"state": "closed"}


def _cached_is_open(backend_id: str) -> bool:
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(backend_id)
        if entry and now - entry.at < 0.1:
            return entry.state.get("state") in ("open", "half_open")
    try:
        store = get_store()
        evaluated = _evaluate_state(backend_id, store, now=time.time())
        store.set_state(backend_id, evaluated)
    except Exception as exc:
        # 熔断存储（Redis）故障不得砸付费生成主链：按 closed 放行（可用性
        # 优先），告警日志让运维发现掉线——enforce 期丢的是保护不是出图
        logger.warning(
            "breaker 状态读取失败按 closed 放行 backend=%s（%s）",
            backend_id,
            type(exc).__name__,
        )
        return False
    with _cache_lock:
        _cache[backend_id] = _CacheEntry(evaluated, now)
    if evaluated.get("state") == "open" and not breaker_enforce_enabled():
        logger.info(
            "breaker shadow backend=%s would_open reason=%s",
            backend_id,
            evaluated.get("reason", "threshold"),
        )
    return evaluated.get("state") in ("open", "half_open")


def is_open(backend_id: str) -> bool:
    if not _cached_is_open(backend_id):
        return False
    return breaker_enforce_enabled()


def record_outcome(
    backend_id: str,
    outcome: str,
    *,
    latency_ms: int | None = None,
    job_id: str | None = None,
) -> None:
    from . import job_ledger

    now = time.time()
    try:
        store = get_store()
        store.record_sample(backend_id, outcome, now=now)
        evaluated = _evaluate_state(backend_id, store, now=now)
        store.set_state(backend_id, evaluated)
    except Exception as exc:
        # 熔断存储（Redis）故障不得砸付费生成主链：样本/状态丢失只告警，
        # 按无闸放行——账本事件独立于熔断存储，照常入队
        logger.warning(
            "breaker 记录失败按放行处理 backend=%s（%s）",
            backend_id,
            type(exc).__name__,
        )
        evaluated = None
    if evaluated is not None:
        with _cache_lock:
            _cache[backend_id] = _CacheEntry(evaluated, time.monotonic())
    job_ledger.schedule_backend_event(
        backend_id=backend_id,
        outcome=outcome,
        latency_ms=latency_ms,
        job_id=job_id,
    )
    if evaluated is None:
        return
    if evaluated.get("state") == "open" and not breaker_enforce_enabled():
        logger.info(
            "breaker shadow backend=%s outcome=%s state=open enforce=0",
            backend_id,
            outcome,
        )


def filter_backend_ids(backend_ids: list[str]) -> list[str]:
    if not breaker_enforce_enabled():
        return backend_ids
    return [bid for bid in backend_ids if not is_open(bid)]


def should_skip_backend(backend_id: str) -> bool:
    return breaker_enforce_enabled() and is_open(backend_id)


def open_backend_count() -> int:
    store = get_store()
    return len(store.list_open_backends())
