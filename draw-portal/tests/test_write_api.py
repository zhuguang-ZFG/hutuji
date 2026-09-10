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

    def fake_process(text, job_dir, *, gfont_dir, layout, stroke_dir="", font_name=None):
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

def _fake_write_ok(captured: dict):
    """可观测 font_name 透传的 fake 排版链。"""
    from draw_portal.pipeline import DocPipelineResult
    from draw_portal.push import PublishResult

    def fake_process(text, job_dir, *, gfont_dir, layout, stroke_dir="", font_name=None):
        captured["font_name"] = font_name
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
            kind="write", mode="article", page_count=1, truncated=False,
            missing_chars=[], lines=10, bytes=100, page_lines=[10],
            svg_path=job_dir / "draw.svg",
            gcode_path=job_dir / "draw.gcode",
            preview_path=job_dir / "preview.png",
        )

    def fake_publish(*, job_dir, mcp_output_dir, public_base_url):
        return [PublishResult(
            gcode_url="https://example.test/files/w.gcode",
            preview_url="https://example.test/files/w.png",
            basename="draw_write_1",
        )]

    return fake_process, fake_publish


def test_write_font_passthrough(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """font 字段透传到排版链；缺省/空白 = None 走默认字库。"""
    client = _auth(monkeypatch, tmp_path)
    captured: dict = {}
    fake_process, fake_publish = _fake_write_ok(captured)
    monkeypatch.setattr("draw_portal.app.process_write_text", fake_process)
    monkeypatch.setattr("draw_portal.app.publish_doc_pages", fake_publish)
    r = client.post("/api/draw/write", json={"text": "你好", "font": "blood8真迹_xiongzai"})
    assert r.status_code == 200
    assert captured["font_name"] == "blood8真迹_xiongzai"
    r = client.post("/api/draw/write", json={"text": "你好"})
    assert r.status_code == 200
    assert captured["font_name"] is None


def test_write_font_invalid_400(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)
    for bad in ("../etc", "a/b", "x\\y", "字" * 61):
        r = client.post("/api/draw/write", json={"text": "你好", "font": bad})
        assert r.status_code == 400, bad


def test_write_font_unknown_400(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """具名字库不存在：load_font 的 WriteTextError 映 400（不 422、不 500）。"""
    from hutuji_mcp.write_text import WriteTextError

    client = _auth(monkeypatch, tmp_path)

    def fake_process(text, job_dir, *, gfont_dir, layout, stroke_dir="", font_name=None):
        raise WriteTextError(f"找不到字库「{font_name}」（目录内 9 个）")

    monkeypatch.setattr("draw_portal.app.process_write_text", fake_process)
    r = client.post("/api/draw/write", json={"text": "你好", "font": "不存在库"})
    assert r.status_code == 400
    assert "找不到字库" in r.json()["detail"]


def test_fonts_endpoint_lists_stems(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / "blood8真迹_xiongzai.gfont").write_bytes(b"x")
    (fonts / "如果梦万国.gfont").write_bytes(b"x")
    (fonts / "README.txt").write_text("nope", encoding="utf-8")
    monkeypatch.setenv("DRAW_PORTAL_GFONT_DIR", str(fonts))
    c = _client(monkeypatch, tmp_path)
    _register_and_login(c)
    r = c.get("/api/fonts")
    assert r.status_code == 200
    assert r.json()["fonts"] == ["blood8真迹_xiongzai", "如果梦万国"]


def test_fonts_endpoint_unconfigured_503(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DRAW_PORTAL_GFONT_DIR", "")
    c = _client(monkeypatch, tmp_path)
    _register_and_login(c)
    assert c.get("/api/fonts").status_code == 503
