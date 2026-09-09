"""写字端点契约：POST /api/draw/write 路由、错误映射与多页发布。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from draw_portal.pipeline import DocPipelineResult
from draw_portal.push import PublishResult

from test_app import _client, _register_and_login  # noqa: E402


def _auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DRAW_PORTAL_GFONT_DIR", "/x/fonts")
    c = _client(monkeypatch, tmp_path)
    _register_and_login(c)
    return c


def test_write_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)
    captured: dict = {}

    def fake_process(text, job_dir, *, gfont_dir, layout, stroke_dir=""):
        captured["text"] = text
        captured["size"] = layout.size_mm
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "pages").mkdir(exist_ok=True)
        (job_dir / "pages" / "page_001.gcode").write_text("G21\n", encoding="ascii")
        (job_dir / "pages" / "page_001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (job_dir / "draw.gcode").write_text("G21\n", encoding="ascii")
        (job_dir / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (job_dir / "pages.json").write_text(
            json.dumps({"kind": "write", "mode": "article", "page_count": 1, "page_lines": [1]}),
            encoding="utf-8",
        )
        return DocPipelineResult(
            kind="write",
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
                gcode_url="https://example.test/files/w.gcode",
                preview_url="https://example.test/files/w.png",
                basename="draw_write_1",
            )
        ]

    monkeypatch.setattr("draw_portal.app.process_write_text", fake_process)
    monkeypatch.setattr("draw_portal.app.publish_doc_pages", fake_publish)
    r = client.post(
        "/api/draw/write",
        json={"text": "春眠不觉晓", "size_mm": 14, "line_spacing": 1.8},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["page_count"] == 1
    assert body["preview_url"].endswith(".png")
    assert captured["size"] == 14.0


def test_write_empty_400(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)
    r = client.post("/api/draw/write", json={"text": "  "})
    assert r.status_code == 400


def test_write_oob_400(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)
    r = client.post("/api/draw/write", json={"text": "你好", "size_mm": 99})
    assert r.status_code == 400
    assert "字号" in r.json()["detail"]


def test_write_no_gfont_503(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DRAW_PORTAL_GFONT_DIR", "")
    c = _client(monkeypatch, tmp_path)
    _register_and_login(c)
    r = c.post("/api/draw/write", json={"text": "你好"})
    assert r.status_code == 503
