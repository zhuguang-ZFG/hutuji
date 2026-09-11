"""process_write_text 单页适配转发（假 layout 断言路由与参数）。"""
from __future__ import annotations

from pathlib import Path

from draw_portal import pipeline


def _patch_pipeline(monkeypatch, seen: dict) -> None:
    """夹桩：load_font/measure_font 回假对象，两条 layout 路由都记 seen['via']。"""

    class FakeLaid:
        pages = [object()]
        truncated = False
        missing_chars: list[str] = []

    def fake_load_font(gfont_dir, font_name):
        seen["gfont_dir"] = gfont_dir
        seen["font_name"] = font_name
        return object()

    def fake_via(name):
        def route(*args, **kwargs):
            seen["via"] = name
            return FakeLaid()

        return route

    def fake_materialize(laid, job_dir, *, kind, mode, profile=None):
        seen["kind"] = kind
        return pipeline.DocPipelineResult(
            kind=kind,
            mode=mode,
            page_count=len(laid.pages),
            truncated=laid.truncated,
            missing_chars=laid.missing_chars,
            lines=1,
            bytes=4,
            page_lines=[1],
            svg_path=job_dir / "draw.svg",
            gcode_path=job_dir / "draw.gcode",
            preview_path=job_dir / "preview.png",
        )

    monkeypatch.setattr("hutuji_mcp.write_text.load_font", fake_load_font)
    monkeypatch.setattr("hutuji_mcp.write_text.measure_font", lambda font: object())
    monkeypatch.setattr("hutuji_mcp.write_text.layout_text", fake_via("layout_text"))
    monkeypatch.setattr(
        "hutuji_mcp.write_text.layout_text_fit_single", fake_via("fit")
    )
    monkeypatch.setattr(pipeline, "_materialize_laid_pages", fake_materialize)


def test_process_write_text_default_uses_plain_layout(monkeypatch, tmp_path: Path):
    """默认（fit_single_page=False）走 layout_text——显式字号的调用方不被缩号。"""
    seen: dict = {}
    _patch_pipeline(monkeypatch, seen)

    result = pipeline.process_write_text(
        "你好",
        tmp_path / "job",
        gfont_dir="/gfont",
        layout=object(),
    )
    assert seen["via"] == "layout_text"
    assert seen["font_name"] is None
    assert result.kind == "write"


def test_process_write_text_fit_flag_routes_to_fit_single(monkeypatch, tmp_path: Path):
    """未显式传字号（app 层 body.size_mm=None）→ fit_single_page=True 走适配。"""
    seen: dict = {}
    _patch_pipeline(monkeypatch, seen)

    pipeline.process_write_text(
        "你好",
        tmp_path / "job",
        gfont_dir="/gfont",
        layout=object(),
        fit_single_page=True,
    )
    assert seen["via"] == "fit"
