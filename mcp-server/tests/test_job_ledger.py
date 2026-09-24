"""job_ledger 契约测试（Phase-3）。"""
from __future__ import annotations

from hutuji_mcp import job_ledger
from hutuji_mcp.bitmap_svg import BitmapError
from hutuji_mcp.config import Settings, load_settings


def test_redact_description_hashes_and_truncates() -> None:
    digest, redacted = job_ledger.redact_description("画一只小猫")
    assert len(digest) == 64
    assert "小猫" in redacted


def test_backend_id_from_label_and_racer() -> None:
    assert job_ledger.backend_id_from_label("Agnes") == "agnes"
    assert job_ledger.backend_id_from_label(
        "自定义",
        racer={"id": "abc-123", "name": "自定义"},
    ) == "racer:abc-123"


def test_classify_bitmap_error() -> None:
    assert job_ledger.classify_bitmap_error(BitmapError("upstream timeout")) == "timeout"
    assert job_ledger.classify_bitmap_error(BitmapError("HTTP 429")) == "http_429"
    assert job_ledger.classify_bitmap_error(BitmapError("HTTP 503")) == "http_5xx"


def test_schedule_enqueue_noop_without_dsn(monkeypatch) -> None:
    base = load_settings()
    patched = Settings(**{**base.__dict__, "pg_dsn": ""})
    monkeypatch.setattr("hutuji_mcp.server.settings", patched)
    job_ledger.schedule_enqueue(
        job_id="job1",
        description="测试",
        public_base_url="https://hutuji.example",
    )


def test_parse_result_summary_strips_query() -> None:
    summary = job_ledger._parse_result_summary(
        '{"url":"https://hutuji.example/files/draw_x.gcode?sig=secret","bytes":12,"pen_lifts":1}'
    )
    assert summary["result_url"] == "https://hutuji.example/files/draw_x.gcode"
    assert summary["bytes_out"] == 12


def test_ledger_writer_bounds_backlog_and_preserves_order():
    import threading

    entered, release = threading.Event(), threading.Event()
    writer = job_ledger._LedgerWriter(capacity=2)
    seen = []

    def slow():
        entered.set()
        assert release.wait(3)
        seen.append(0)

    try:
        assert writer.submit(slow)
        assert entered.wait(2)
        assert writer.submit(lambda: seen.append(1))
        assert writer.submit(lambda: seen.append(2))
        assert writer.submit(lambda: seen.append(3)) is False
        assert writer.pending.qsize() == 2
    finally:
        release.set()
        writer.pending.put(None, timeout=3)
        writer.thread.join(timeout=3)
    assert not writer.thread.is_alive()
    assert seen == [0, 1, 2]


def test_ledger_writer_continues_after_error_and_keeps_context():
    writer = job_ledger._LedgerWriter(capacity=3)
    seen = []

    def broken():
        raise OSError("模拟数据库失败")

    token = job_ledger.bind_job_id("job-context")
    try:
        assert writer.submit(broken)
        assert writer.submit(lambda: seen.append(job_ledger.get_current_job_id()))
    finally:
        job_ledger.reset_job_id(token)
        writer.pending.put(None, timeout=3)
        writer.thread.join(timeout=3)
    assert seen == ["job-context"]
    assert not writer.thread.is_alive()


def test_postgres_operations_have_timeouts(monkeypatch):
    import sys
    from types import SimpleNamespace

    seen = {}
    monkeypatch.setattr(job_ledger, "_settings", lambda: SimpleNamespace(pg_dsn="postgresql://test"))
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=lambda dsn, **kw: seen.update(kw)))
    job_ledger._connect()
    assert seen["connect_timeout"] == 5
    assert "statement_timeout=5000" in seen["options"]
    assert "lock_timeout=2000" in seen["options"]
