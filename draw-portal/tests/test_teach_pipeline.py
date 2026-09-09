"""process_teach_text 笔顺管线转发（假 layout_teach / materialize 断言）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from hutuji_mcp.stroke_order import StrokeOrderError


def test_process_teach_text_forwards_layout(monkeypatch, tmp_path):
    from draw_portal import pipeline

    seen: dict = {}

    class FakeData:
        pass

    class FakeLaid:
        pages = [object(), object()]
        truncated = True
        missing_chars = ["龘"]

    def fake_load(stroke_dir):
        seen["stroke_dir"] = stroke_dir
        return FakeData()

    def fake_layout_teach(chars, data, max_pages):
        seen["chars"] = chars
        seen["data"] = data
        seen["max_pages"] = max_pages
        return FakeLaid()

    def fake_materialize(laid, job_dir, *, kind, mode):
        seen["kind"] = kind
        seen["mode"] = mode
        seen["truncated"] = laid.truncated
        seen["missing_chars"] = laid.missing_chars
        return pipeline.DocPipelineResult(
            kind=kind,
            mode=mode,
            page_count=len(laid.pages),
            truncated=laid.truncated,
            missing_chars=laid.missing_chars,
            lines=2,
            bytes=8,
            page_lines=[1, 1],
            svg_path=job_dir / "draw.svg",
            gcode_path=job_dir / "draw.gcode",
            preview_path=job_dir / "preview.png",
        )

    monkeypatch.setattr("hutuji_mcp.stroke_order.StrokeData.load", fake_load)
    monkeypatch.setattr("hutuji_mcp.stroke_order.layout_teach", fake_layout_teach)
    monkeypatch.setattr("draw_portal.pipeline._materialize_laid_pages", fake_materialize)

    result = pipeline.process_teach_text(
        "你好",
        tmp_path / "job",
        stroke_dir="/stroke",
    )
    assert seen["stroke_dir"] == "/stroke"
    assert seen["chars"] == "你好"
    assert seen["data"] is not None
    assert seen["max_pages"] == 20
    assert seen["kind"] == "teach"
    assert seen["mode"] == "article"
    assert result.kind == "teach"
    assert result.mode == "article"
    assert result.page_count == 2
    assert result.truncated is True
    assert result.missing_chars == ["龘"]


def test_process_teach_text_empty_raises(tmp_path):
    from draw_portal.pipeline import process_teach_text

    with pytest.raises(StrokeOrderError, match="没有可教的汉字"):
        process_teach_text("   ", tmp_path / "job", stroke_dir="/stroke")


def test_process_teach_text_empty_skips_load(monkeypatch, tmp_path):
    from draw_portal.pipeline import process_teach_text

    def fail_load(_stroke_dir):
        raise AssertionError("StrokeData.load should not run for empty chars")

    monkeypatch.setattr("hutuji_mcp.stroke_order.StrokeData.load", fail_load)

    with pytest.raises(StrokeOrderError, match="没有可教的汉字"):
        process_teach_text("", tmp_path / "job", stroke_dir="/stroke")
