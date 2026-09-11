"""process_write_text 与文档 article 版式参数转发（假 layout_text 断言）。"""
from __future__ import annotations

import io
import json
import struct
import zipfile
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

    def fake_materialize(laid, job_dir, *, kind, mode, profile=None):
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


@pytest.fixture
def font_and_stroke_dirs(tmp_path):
    """两种现场合成字形 + 第三种标准笔顺；不依赖机器上的专有字库。"""
    def utf(value):
        raw = value.encode("utf-8")
        return struct.pack(">H", len(raw)) + raw

    def font_bytes(points):
        header = (
            struct.pack(">i", 2) + utf("test") + struct.pack(">i", 0)
            + utf("测试字体") + utf("pytest") + utf("")
            + struct.pack(">ii", 100, 3) + utf("test-id") + struct.pack(">i", 0)
        )
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            for char, coords in {
                "永": [0, -100, 100, -100, 100, 0, 0, 0],
                "国": [0, -100, 100, -100, 100, 0, 0, 0],
                "中": points,
            }.items():
                verbs = [0] + [1] * (len(coords) // 2 - 1)
                glyph = (
                    struct.pack(">Hi", ord(char), len(coords))
                    + struct.pack(f">{len(coords)}f", *coords)
                    + struct.pack(">i", len(verbs)) + bytes(verbs)
                )
                zf.writestr(str(ord(char)), glyph)
        return header + archive.getvalue()

    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    (font_dir / "a.gfont").write_bytes(font_bytes([0, -100, 100, -100, 100, 0, 0, 0]))
    (font_dir / "b.gfont").write_bytes(font_bytes([0, -100, 100, 0, 0, 0]))
    stroke_dir = tmp_path / "strokes"
    stroke_dir.mkdir()
    medians = [[[100, 100], [900, 900]], [[100, 900], [900, 100]]]
    (stroke_dir / "strokes.jsonl").write_text(
        "中\t" + json.dumps(medians) + "\n", encoding="utf-8"
    )
    return str(font_dir), str(stroke_dir)


def test_explicit_font_controls_chinese_even_with_stroke_data(
    monkeypatch, tmp_path, font_and_stroke_dirs
):
    """真字库解析和排版输出：选两种字体必须产生两种笔画，不被 medians 吃掉。"""
    from draw_portal import pipeline
    from hutuji_mcp import write_text

    # 只省去已有端到端覆盖的 vpype/写盘，观察真实排版生成的 SVG。
    monkeypatch.setattr(
        pipeline, "_materialize_laid_pages",
        lambda laid, *args, **kwargs: [write_text.page_to_svg(page) for page in laid.pages],
    )
    font_dir, stroke_dir = font_and_stroke_dirs

    def render(name, strokes):
        return pipeline.process_write_text(
            "中", tmp_path / "job", gfont_dir=font_dir, stroke_dir=strokes,
            font_name=name, layout=WriteLayout(),
        )

    a = render("a", stroke_dir)
    b = render("b", stroke_dir)
    assert a != b, "显式字体被标准笔顺覆盖，换字体未改变中文字形"
    assert a == render("a", "")
    assert b == render("b", "")


def test_unspecified_font_preserves_standard_stroke_behavior(
    monkeypatch, tmp_path, font_and_stroke_dirs
):
    from draw_portal import pipeline
    from hutuji_mcp import write_text

    monkeypatch.setattr(
        pipeline, "_materialize_laid_pages",
        lambda laid, *args, **kwargs: [write_text.page_to_svg(page) for page in laid.pages],
    )
    font_dir, stroke_dir = font_and_stroke_dirs
    kwargs = dict(gfont_dir=font_dir, layout=WriteLayout())
    standard = pipeline.process_write_text("中", tmp_path / "default", stroke_dir=stroke_dir, **kwargs)
    font_only = pipeline.process_write_text("中", tmp_path / "font", **kwargs)
    assert standard != font_only, "未选字体时配置的标准笔顺仍须保留"
    assert standard == pipeline.process_write_text(
        "中", tmp_path / "empty", stroke_dir=stroke_dir, font_name="", **kwargs
    )
