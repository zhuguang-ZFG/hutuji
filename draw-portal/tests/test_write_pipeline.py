"""process_write_text 与文档 article 版式参数转发（假 layout_text 断言）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from hutuji_mcp.write_text import WriteLayout, WriteTextError


def test_upload_doc_article_forwards_layout(monkeypatch, tmp_path):
    from draw_portal.pipeline import process_upload_doc

    seen: dict = {}

    class FakeDoc:
        kind = "txt"
        mode = "article"
        paragraphs = ["你好"]
        headings = None
        tables = []

    def fake_layout_text(text, font, metrics, layout, **kwargs):
        seen["layout"] = layout
        raise AssertionError(f"layout={layout.size_mm}")

    monkeypatch.setattr("hutuji_mcp.doc_ingest.parse_document", lambda *a, **k: FakeDoc())
    monkeypatch.setattr("hutuji_mcp.write_text.load_font", lambda *a, **k: object())
    monkeypatch.setattr(
        "hutuji_mcp.write_text.measure_font",
        lambda *a, **k: type("M", (), {"em_height": 1.0})(),
    )
    monkeypatch.setattr("hutuji_mcp.write_text.layout_text", fake_layout_text)

    with pytest.raises(AssertionError, match="layout=18"):
        process_upload_doc(
            b"hi",
            "txt",
            tmp_path / "j",
            gfont_dir="/x",
            size_mm=18.0,
            line_spacing=2.0,
        )
    assert seen["layout"].line_spacing == 2.0


def test_upload_doc_table_ignores_layout_params(monkeypatch, tmp_path):
    from draw_portal.pipeline import DocPipelineResult, process_upload_doc

    seen: dict = {}

    class FakeDoc:
        kind = "xlsx"
        mode = "tables"
        paragraphs = []
        headings = None
        tables = [object()]

    class FakeLaid:
        pages = [object()]
        truncated = False
        missing_chars = []

    def fake_layout_tables(tables, font, metrics, layout, **kwargs):
        seen["layout"] = layout
        return FakeLaid()

    def fake_materialize(laid, job_dir, *, kind, mode):
        return DocPipelineResult(
            kind=kind,
            mode=mode,
            page_count=1,
            truncated=False,
            missing_chars=[],
            lines=1,
            bytes=4,
            page_lines=[1],
            svg_path=job_dir / "draw.svg",
            gcode_path=job_dir / "draw.gcode",
            preview_path=job_dir / "preview.png",
        )

    monkeypatch.setattr("hutuji_mcp.doc_ingest.parse_document", lambda *a, **k: FakeDoc())
    monkeypatch.setattr("hutuji_mcp.write_text.load_font", lambda *a, **k: object())
    monkeypatch.setattr(
        "hutuji_mcp.write_text.measure_font",
        lambda *a, **k: type("M", (), {"em_height": 1.0})(),
    )
    monkeypatch.setattr("hutuji_mcp.table_layout.layout_tables", fake_layout_tables)
    monkeypatch.setattr("draw_portal.pipeline._materialize_laid_pages", fake_materialize)

    process_upload_doc(
        b"hi",
        "xlsx",
        tmp_path / "j",
        gfont_dir="/x",
        size_mm=18.0,
        line_spacing=2.0,
    )
    assert seen["layout"].size_mm == 8.0
    assert seen["layout"].line_spacing == 1.25


def test_process_write_text_uses_layout(monkeypatch, tmp_path):
    from draw_portal import pipeline

    seen: dict = {}

    class FakeLaid:
        pages = [object()]
        truncated = False
        missing_chars = []

    def fake_layout_text(text, font, metrics, layout, **kwargs):
        seen["layout"] = layout
        seen["text"] = text
        return FakeLaid()

    monkeypatch.setattr("hutuji_mcp.write_text.load_font", lambda *a, **k: object())
    monkeypatch.setattr(
        "hutuji_mcp.write_text.measure_font",
        lambda *a, **k: type("M", (), {"em_height": 1.0})(),
    )
    monkeypatch.setattr("hutuji_mcp.write_text.layout_text", fake_layout_text)
    monkeypatch.setattr("hutuji_mcp.write_text.page_to_svg", lambda page: "<svg/>")
    monkeypatch.setattr("hutuji_mcp.write_text.page_to_preview_png", lambda page: b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr("hutuji_mcp.llm_svg._validate_svg", lambda svg: None)
    def fake_svg_to_gcode(svg_path, gcode_path, **kwargs):
        gcode_path.write_text("G21\nG90\nF8000\n", encoding="ascii")
        return type("S", (), {"lines": 1, "bytes": 4, "pen_lifts": 0})()

    monkeypatch.setattr("hutuji_mcp.gcode_gen.svg_to_gcode", fake_svg_to_gcode)
    monkeypatch.setattr("hutuji_mcp.gcode_gen.validate_gcode", lambda *a, **k: None)

    layout = WriteLayout(size_mm=18.0, line_spacing=2.0)
    result = pipeline.process_write_text(
        "你好",
        tmp_path / "job",
        gfont_dir="/x",
        layout=layout,
    )
    assert seen["layout"].size_mm == 18.0
    assert seen["text"] == "你好"
    assert result.kind == "write"
    assert result.mode == "article"
    assert result.page_count == 1


def test_process_write_text_empty_raises(tmp_path):
    from draw_portal.pipeline import process_write_text

    with pytest.raises(WriteTextError, match="文本为空"):
        process_write_text(
            "   ",
            tmp_path / "job",
            gfont_dir="/x",
            layout=WriteLayout(),
        )
