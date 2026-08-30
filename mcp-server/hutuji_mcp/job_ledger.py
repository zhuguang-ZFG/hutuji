"""Postgres 绘图任务账本（Phase-3）；空 DSN = 禁用，写入失败不阻断出图。"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

from .redact import _redact_secret_text

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = "phase3_v1"
_current_job_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "draw_job_id", default=None
)
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  id TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS draw_jobs (
  job_id TEXT PRIMARY KEY,
  description_hash TEXT NOT NULL,
  description_redacted TEXT NOT NULL,
  device_id TEXT NOT NULL DEFAULT '',
  public_base_url TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','processing','done','failed','timed_out')),
  route TEXT,
  enqueued_at TIMESTAMPTZ NOT NULL,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  latency_ms INTEGER,
  result_url TEXT,
  error_type TEXT,
  error_detail TEXT,
  worker_instance TEXT,
  bytes_out INTEGER,
  pen_lifts INTEGER
);
CREATE INDEX IF NOT EXISTS draw_jobs_enqueued_at ON draw_jobs (enqueued_at DESC);
CREATE INDEX IF NOT EXISTS draw_jobs_device_id ON draw_jobs (device_id, enqueued_at DESC);
CREATE TABLE IF NOT EXISTS draw_backend_events (
  id BIGSERIAL PRIMARY KEY,
  job_id TEXT REFERENCES draw_jobs(job_id),
  backend_id TEXT NOT NULL,
  outcome TEXT NOT NULL,
  latency_ms INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS draw_backend_events_backend ON draw_backend_events (backend_id, created_at DESC);
"""

_schema_ready = False
_schema_lock = threading.Lock()


def _settings():
    from . import server

    return server.settings


def ledger_enabled() -> bool:
    return bool(getattr(_settings(), "pg_dsn", "").strip())


def get_current_job_id() -> str | None:
    return _current_job_id.get()


def bind_job_id(job_id: str | None):
    return _current_job_id.set(job_id)


def reset_job_id(token: contextvars.Token) -> None:
    _current_job_id.reset(token)


def redact_description(description: str) -> tuple[str, str]:
    digest = hashlib.sha256(description.encode("utf-8")).hexdigest()
    text = _redact_secret_text(description.replace("\n", " ").strip())
    if len(text) > 80:
        text = text[:77] + "..."
    return digest, text


def _sanitize_error_detail(exc: BaseException) -> tuple[str, str]:
    detail = _redact_secret_text(str(exc).replace("\n", " ")[:200])
    return type(exc).__name__, detail


def _parse_result_summary(result_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(result_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    url = str(payload.get("url") or "")
    if "?" in url:
        url = url.split("?", 1)[0]
    return {
        "result_url": url[:512] if url else None,
        "bytes_out": payload.get("bytes"),
        "pen_lifts": payload.get("pen_lifts"),
    }


def _connect():
    import psycopg

    dsn = getattr(_settings(), "pg_dsn", "").strip()
    return psycopg.connect(dsn, connect_timeout=5)


def _ensure_schema(conn) -> None:
    global _schema_ready
    with _schema_lock:
        if _schema_ready:
            return
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
            cur.execute(
                "INSERT INTO schema_migrations (id) VALUES (%s) ON CONFLICT DO NOTHING",
                (_SCHEMA_VERSION,),
            )
        conn.commit()
        _schema_ready = True


def _run_sync(fn) -> None:
    if not ledger_enabled():
        return

    def wrapper() -> None:
        try:
            fn()
        except Exception:
            logger.warning("job_ledger 写入失败", exc_info=True)

    threading.Thread(target=wrapper, daemon=True, name="job-ledger").start()


def schedule_enqueue(
    *,
    job_id: str,
    description: str,
    public_base_url: str,
    device_id: str = "",
) -> None:
    digest, redacted = redact_description(description)
    now = datetime.now(timezone.utc)

    def write() -> None:
        with _connect() as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO draw_jobs (
                      job_id, description_hash, description_redacted, device_id,
                      public_base_url, status, enqueued_at
                    ) VALUES (%s,%s,%s,%s,%s,'pending',%s)
                    ON CONFLICT (job_id) DO NOTHING
                    """,
                    (job_id, digest, redacted, device_id, public_base_url, now),
                )
            conn.commit()

    _run_sync(write)


def schedule_processing(*, job_id: str, worker_instance: str = "") -> None:
    now = datetime.now(timezone.utc)

    def write() -> None:
        with _connect() as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE draw_jobs
                    SET status='processing', started_at=%s, worker_instance=%s
                    WHERE job_id=%s
                    """,
                    (now, worker_instance[:64], job_id),
                )
            conn.commit()

    _run_sync(write)


def schedule_done(*, job_id: str, result_json: str, route: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    summary = _parse_result_summary(result_json)

    def write() -> None:
        with _connect() as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE draw_jobs
                    SET status='done', finished_at=%s,
                        latency_ms=CASE
                          WHEN started_at IS NULL THEN NULL
                          ELSE GREATEST(0, (EXTRACT(EPOCH FROM (%s - started_at)) * 1000)::int)
                        END,
                        result_url=%s, bytes_out=%s, pen_lifts=%s, route=COALESCE(%s, route)
                    WHERE job_id=%s
                    """,
                    (
                        now,
                        now,
                        summary.get("result_url"),
                        summary.get("bytes_out"),
                        summary.get("pen_lifts"),
                        route,
                        job_id,
                    ),
                )
            conn.commit()

    _run_sync(write)


def schedule_failed(*, job_id: str, exc: BaseException) -> None:
    now = datetime.now(timezone.utc)
    error_type, error_detail = _sanitize_error_detail(exc)

    def write() -> None:
        with _connect() as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE draw_jobs
                    SET status='failed', finished_at=%s,
                        latency_ms=CASE
                          WHEN started_at IS NULL THEN NULL
                          ELSE GREATEST(0, (EXTRACT(EPOCH FROM (%s - started_at)) * 1000)::int)
                        END,
                        error_type=%s, error_detail=%s
                    WHERE job_id=%s
                    """,
                    (now, now, error_type, error_detail, job_id),
                )
            conn.commit()

    _run_sync(write)


def schedule_timed_out(*, job_id: str) -> None:
    now = datetime.now(timezone.utc)

    def write() -> None:
        with _connect() as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE draw_jobs SET status='timed_out', finished_at=%s WHERE job_id=%s
                    """,
                    (now, job_id),
                )
            conn.commit()

    _run_sync(write)


def schedule_backend_event(
    *,
    backend_id: str,
    outcome: str,
    latency_ms: int | None = None,
    job_id: str | None = None,
) -> None:
    job_id = job_id or get_current_job_id()
    backend_id = backend_id[:128]
    outcome = outcome[:64]

    def write() -> None:
        with _connect() as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO draw_backend_events (job_id, backend_id, outcome, latency_ms)
                    VALUES (%s,%s,%s,%s)
                    """,
                    (job_id, backend_id, outcome, latency_ms),
                )
            conn.commit()

    _run_sync(write)


def schedule_route(*, job_id: str, route: str) -> None:
    route = route[:32]

    def write() -> None:
        with _connect() as conn:
            _ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE draw_jobs SET route=%s WHERE job_id=%s",
                    (route, job_id),
                )
            conn.commit()

    _run_sync(write)


def reset_ledger_for_tests() -> None:
    global _schema_ready
    with _schema_lock:
        _schema_ready = False


_BACKEND_LABEL_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"商汤"), "sensenova"),
    (re.compile(r"Agnes", re.I), "agnes"),
    (re.compile(r"硅流 Schnell"), "siliconflow:schnell"),
    (re.compile(r"硅流可图"), "siliconflow:kolors"),
    (re.compile(r"备用"), "imagefree"),
]


def backend_id_from_label(label: str, racer: dict[str, Any] | None = None) -> str:
    if racer is not None:
        rid = str(racer.get("id") or racer.get("name") or "").strip()
        if rid:
            return f"racer:{rid[:64]}"
    for pattern, backend_id in _BACKEND_LABEL_MAP:
        if pattern.search(label):
            return backend_id
    safe = re.sub(r"[^a-zA-Z0-9:_-]+", "_", label.strip())[:64]
    return safe or "unknown"


def classify_bitmap_error(exc: BaseException) -> str:
    text = str(exc).lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "429" in text:
        return "http_429"
    if "500" in text or "502" in text or "503" in text or "504" in text:
        return "http_5xx"
    return "error"
