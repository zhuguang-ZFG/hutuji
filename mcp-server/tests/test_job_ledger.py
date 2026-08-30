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
