"""绘图队列结构化指标（Phase-3）；周期性 JSON 日志，不阻断热路径。"""
from __future__ import annotations

import asyncio
import json
import logging
import time

logger = logging.getLogger("queue_metrics")


def _settings():
    from . import server

    return server.settings


async def _queue_depth() -> int | None:
    from .draw_queue import QUEUE_KEY, queue_enabled

    if not queue_enabled():
        return None
    url = getattr(_settings(), "draw_queue_url", "").strip()
    if not (url.startswith("redis://") or url.startswith("rediss://")):
        from .draw_queue import get_backend

        backend = get_backend()
        if hasattr(backend, "_queue"):
            return backend._queue.qsize()  # type: ignore[attr-defined]
        return None
    import redis.asyncio as redis

    client = redis.from_url(url, decode_responses=True)
    try:
        depth = await client.llen(QUEUE_KEY)
    finally:
        await client.aclose()
    return int(depth)


async def collect_metrics(*, role: str) -> dict[str, object]:
    from . import breaker

    payload: dict[str, object] = {
        "ts": time.time(),
        "role": role,
    }
    try:
        depth = await _queue_depth()
        if depth is not None:
            payload["draw_queue_depth"] = depth
    except Exception as exc:
        payload["draw_queue_depth_error"] = type(exc).__name__
    try:
        payload["breaker_open_count"] = breaker.open_backend_count()
    except Exception as exc:
        payload["breaker_open_count_error"] = type(exc).__name__
    return payload


async def metrics_loop(stop: asyncio.Event, *, role: str) -> None:
    interval = max(5, getattr(_settings(), "metrics_interval_sec", 15))
    while not stop.is_set():
        try:
            payload = await collect_metrics(role=role)
            logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("queue_metrics 采集失败", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def emit_once(*, role: str) -> None:
    payload = await collect_metrics(role=role)
    logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True))
