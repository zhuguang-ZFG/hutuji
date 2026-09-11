"""笔顺端点契约：POST /api/draw/teach 路由、错误映射与多页发布。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from hutuji_mcp.stroke_order import StrokeOrderError

from draw_portal.pipeline import DocPipelineResult
from draw_portal.push import PublishResult

from test_app import _client, _register_and_login  # noqa: E402


def _auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DRAW_PORTAL_STROKE_DIR", "/x/strokes")
    c = _client(monkeypatch, tmp_path)
    _register_and_login(c)
    return c


def test_teach_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)
    captured: dict = {}

    def fake_process(chars, job_dir, *, stroke_dir, profile=None):
        captured["chars"] = chars
        captured["stroke_dir"] = stroke_dir
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "pages").mkdir(exist_ok=True)
        (job_dir / "pages" / "page_001.gcode").write_text("G21\n", encoding="ascii")
        (job_dir / "pages" / "page_001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (job_dir / "draw.gcode").write_text("G21\n", encoding="ascii")
        (job_dir / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (job_dir / "pages.json").write_text(
            json.dumps({"kind": "teach", "mode": "article", "page_count": 1, "page_lines": [1]}),
            encoding="utf-8",
        )
        return DocPipelineResult(
            kind="teach",
            mode="article",
            page_count=1,
            truncated=False,
            missing_chars=[],
            lines=10,
            bytes=100,
            page_lines=[10],
            svg_path=job_dir / "draw.svg",
            gcode_path=job_dir / "draw.gcode",
            preview_path=job_dir / "preview.png",
        )

    def fake_publish(*, job_dir, mcp_output_dir, public_base_url):
        return [
            PublishResult(
                gcode_url="https://example.test/files/t.gcode",
                preview_url="https://example.test/files/t.png",
                basename="draw_teach_1",
            )
        ]

    monkeypatch.setattr("draw_portal.app.process_teach_text", fake_process)
    monkeypatch.setattr("draw_portal.app.publish_doc_pages", fake_publish)
    r = client.post(
        "/api/draw/teach",
        json={"text": "春眠不觉晓 hello"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["page_count"] == 1
    assert body["chars_taught"] == "春眠不觉晓"
    assert body["input_truncated"] is False
    assert body["preview_url"].endswith(".png")
    assert captured["chars"] == "春眠不觉晓"
    assert captured["stroke_dir"] == "/x/strokes"


def test_teach_empty_400(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)
    r = client.post("/api/draw/teach", json={"text": "hello 123"})
    assert r.status_code == 400
    assert r.json()["detail"] == "没有可教的汉字"


def test_teach_no_stroke_dir_503(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DRAW_PORTAL_STROKE_DIR", "")
    c = _client(monkeypatch, tmp_path)
    _register_and_login(c)
    r = c.post("/api/draw/teach", json={"text": "你好"})
    assert r.status_code == 503


def test_teach_stroke_order_error_400(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)

    def fail_process(chars, job_dir, *, stroke_dir, profile=None):
        raise StrokeOrderError("全部缺字：龘")

    monkeypatch.setattr("draw_portal.app.process_teach_text", fail_process)
    r = client.post("/api/draw/teach", json={"text": "龘"})
    assert r.status_code == 400
    assert "全部缺字" in r.json()["detail"]
