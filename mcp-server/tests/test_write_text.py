"""write_text（奎享单线字库排版链）+ gfont 解析器契约测试。

字体夹具是测试内现场合成的 v2 明文 .gfont（结构同真字库：Java 大端 DataInput
+ ZIP 十进制码位条目），不引入奎享专有字体文件。
"""

from __future__ import annotations

import asyncio
import io
import json
import struct
import zipfile

import pytest

from hutuji_mcp import gfont, write_text
from hutuji_mcp.gcode_gen import validate_gcode
from hutuji_mcp.gfont import GfontError
from hutuji_mcp.llm_svg import _validate_svg
from hutuji_mcp.write_text import WriteTextError


def _utf(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def _glyph_bytes(key: str, points: list[float], verbs: list[int]) -> bytes:
    out = bytearray()
    out += struct.pack(">H", ord(key))  # v<=7：key 是 u16 char
    out += struct.pack(">i", len(points))
    for value in points:
        out += struct.pack(">f", value)
    out += struct.pack(">i", len(verbs))
    out += bytes(verbs)
    return bytes(out)


def _build_v2_font(glyphs: dict[str, tuple[list[float], list[int]]]) -> bytes:
    """合成 v2 明文 gfont：meta + i32 preview_count=0 + ZIP（十进制码位条目）。"""
    out = bytearray()
    out += struct.pack(">i", 2)  # version
    out += _utf("testfamily")
    out += struct.pack(">i", 0)  # language kind
    out += _utf("测试字库")
    out += _utf("pytest")
    out += _utf("")
    out += struct.pack(">i", 100)  # size
    out += struct.pack(">i", len(glyphs))  # declared count
    out += _utf("uuid-test")  # v>=2 uuid
    out += struct.pack(">i", 0)  # preview count
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as archive:
        for key, (points, verbs) in glyphs.items():
            archive.writestr(str(ord(key)), _glyph_bytes(key, points, verbs))
    out += zip_buf.getvalue()
    return bytes(out)


# 探针字「永」「国」都是 100x100 方框（基线 y=0，字身 [-100,0]，向上为负——同真字库）
_BOX_POINTS = [0.0, -100.0, 100.0, -100.0, 100.0, 0.0, 0.0, 0.0]
_BOX_VERBS = [0, 1, 1, 1]
_TRIANGLE_POINTS = [0.0, -80.0, 50.0, 0.0, -50.0, 0.0]
_TRIANGLE_VERBS = [0, 1, 1]
_COMMA_POINTS = [50.0, -10.0, 55.0, 5.0]
_COMMA_VERBS = [0, 1]
_CUBIC_POINTS = [0.0, 0.0, 30.0, -90.0, 70.0, -90.0, 100.0, 0.0]
_CUBIC_VERBS = [0, 2]


def _fixture_font() -> gfont.GfontFile:
    return gfont.GfontFile.parse(
        _build_v2_font(
            {
                "永": (_BOX_POINTS, _BOX_VERBS),
                "国": (_BOX_POINTS, _BOX_VERBS),
                "中": (_BOX_POINTS, _BOX_VERBS),
                "文": (_BOX_POINTS, _BOX_VERBS),
                "字": (_BOX_POINTS, _BOX_VERBS),
                "A": (_TRIANGLE_POINTS, _TRIANGLE_VERBS),
                "B": (_CUBIC_POINTS, _CUBIC_VERBS),
                "，": (_COMMA_POINTS, _COMMA_VERBS),
                "。": (_COMMA_POINTS, _COMMA_VERBS),
            }
        )
    )


def _fixture_metrics() -> write_text.FontMetrics:
    return write_text.measure_font(_fixture_font())


def test_gfont_parse_v2_meta_roundtrip():
    font = _fixture_font()
    assert font.meta.version == 2
    assert font.meta.display_name == "测试字库"
    assert font.meta.declared_count == 9
    assert font.has_glyph("永")
    assert not font.has_glyph("不存在")


def test_gfont_load_glyph_polylines_and_cubic_flatten():
    font = _fixture_font()
    box = font.load_glyph("永")
    paths = gfont.glyph_to_polylines(box)
    assert len(paths) == 1
    assert paths[0][0] == (0.0, -100.0)  # moveTo 起点
    assert paths[0][-1] == (0.0, 0.0)
    cubic = font.load_glyph("B")
    cubic_paths = gfont.glyph_to_polylines(cubic)
    # cubic 24 步细分：moveTo 起点 + 24 个采样点
    assert len(cubic_paths) == 1
    assert len(cubic_paths[0]) == 25
    assert cubic_paths[0][-1] == (100.0, 0.0)
    # 中点应低于两端连线（曲线拱起，向上为负）
    assert cubic_paths[0][12][1] < -40.0


def test_gfont_reject_bad_version_and_truncated():
    with pytest.raises(GfontError, match="不支持的 gfont 版本"):
        gfont.GfontFile.parse(struct.pack(">i", 10) + b"\x00" * 32)
    with pytest.raises(GfontError):
        gfont.GfontFile.parse(b"\x00\x00")


def test_gfont_reject_bad_zip():
    data = bytearray(_build_v2_font({"永": (_BOX_POINTS, _BOX_VERBS)}))
    data = bytes(data[:-10]) + b"not-a-zip!"
    font = gfont.GfontFile.parse(data)
    with pytest.raises(GfontError, match="ZIP"):
        font.load_glyph("永")


def test_gfont_plaintext_v6_header_fallback():
    """方正兰亭变体：version=6 但 meta 明文（无加密头）——按明文回退解析。"""
    out = bytearray()
    out += struct.pack(">i", 6)
    out += _utf("kvenjoy")
    out += struct.pack(">i", 4)
    out += _utf("明文头字库")
    out += _utf("src")
    out += _utf("mark")
    out += struct.pack(">i", 300)
    out += struct.pack(">i", 1)
    out += _utf("")  # v>=2 uuid
    out += _utf("")  # v>=4 skip
    out += struct.pack(">i", 0)  # preview count
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as archive:
        archive.writestr(str(ord("永")), _glyph_bytes("永", _BOX_POINTS, _BOX_VERBS))
    out += zip_buf.getvalue()
    font = gfont.GfontFile.parse(bytes(out))
    assert font.meta.display_name == "明文头字库"
    assert font.has_glyph("永")


def test_measure_font_probe_metrics():
    metrics = _fixture_metrics()
    assert metrics.em_height == pytest.approx(100.0)
    assert metrics.full_width == pytest.approx(100.0)
    assert metrics.baseline_to_top == pytest.approx(100.0)


def test_measure_font_rejects_missing_probe():
    font = gfont.GfontFile.parse(_build_v2_font({"A": (_TRIANGLE_POINTS, _TRIANGLE_VERBS)}))
    with pytest.raises(WriteTextError, match="探针"):
        write_text.measure_font(font)


def test_layout_single_line_positions():
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0)
    result = write_text.layout_text("永国", font, metrics, layout)
    assert len(result.pages) == 1
    assert result.pages[0].line_count == 1
    xs = [x for s in result.pages[0].strokes for x, _ in s.points]
    # scale=0.1，全宽步进=10mm；段首缩进 2 字 → 首字 origin=8+20=28，末字 origin=38
    # 墨区 X ∈ [28, 48]
    assert min(xs) == pytest.approx(28.0, abs=0.01)
    assert max(xs) == pytest.approx(48.0, abs=0.01)
    # 基线 y = 8 + 10（基线到顶）=18；字身 y ∈ [8, 18]
    ys = [y for s in result.pages[0].strokes for _, y in s.points]
    assert min(ys) == pytest.approx(8.0, abs=0.01)
    assert max(ys) == pytest.approx(18.0, abs=0.01)


def test_layout_wraps_at_text_width():
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0)
    # 页宽 174mm，全宽步进 10mm → 首行缩进后放 15 字，次行 17 字
    result = write_text.layout_text("永" * 40, font, metrics, layout)
    assert result.pages[0].line_count >= 3
    xs = [x for s in result.pages[0].strokes for x, _ in s.points]
    assert max(xs) <= 190.0 - 8.0 + 1e-6  # 不越右边距


def test_layout_punctuation_never_starts_line():
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0)
    # 构造「。」恰落在折行点：首行缩进 20mm 后每字 10mm，页宽 174 → 第 16 字溢出
    text = "永" * 15 + "。" + "国" * 10
    result = write_text.layout_text(text, font, metrics, layout)
    page = result.pages[0]
    # 「。」未被挤到第二行行首：第二行首字 origin 应 = 左边距（8mm）且字形是方框（国），
    # 若「。」在行首，第二行第一笔 X 会落在 8+5*0.1=8.5（逗号形）——直接断言第二行
    # 起始笔画的最低 X 是方框左沿 8.0
    assert page.line_count >= 2
    second_line_strokes = [
        s for s in page.strokes if all(y > 18.0 for _, y in s.points)
    ]
    assert second_line_strokes
    first_stroke_min_x = min(x for x, _ in second_line_strokes[0].points)
    assert first_stroke_min_x == pytest.approx(8.0, abs=0.01)


def test_layout_paginates_and_reports_missing():
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout(
        size_mm=20.0, line_spacing=1.5, margin_top_mm=150.0, margin_bottom_mm=0.0
    )
    # 页高 40mm、行高 30mm → 一页一行；首行缩进后容量 6 字、续行 8 字，15 字 → 3 页
    result = write_text.layout_text("永国中" * 4 + "永国龘", font, metrics, layout)
    assert len(result.pages) == 3
    assert all(p.line_count == 1 for p in result.pages)
    assert result.missing_chars == ["龘"]


def test_layout_rejects_invalid_params_and_empty():
    font, metrics = _fixture_font(), _fixture_metrics()
    with pytest.raises(WriteTextError, match="字号越界"):
        write_text.WriteLayout(size_mm=1.0).validate()
    with pytest.raises(WriteTextError, match="文本为空"):
        write_text.layout_text("   ", font, metrics, write_text.WriteLayout())


def test_page_svg_passes_injection_gate():
    font, metrics = _fixture_font(), _fixture_metrics()
    result = write_text.layout_text("永国中", font, metrics, write_text.WriteLayout())
    svg = write_text.page_to_svg(result.pages[0])
    _validate_svg(svg)  # 不抛即过白名单（元素仅 svg/path、fill=none、无事件/href）


def test_load_font_dir_errors(tmp_path):
    with pytest.raises(WriteTextError, match="字库目录不可用"):
        write_text.load_font(str(tmp_path / "nope"), None)
    with pytest.raises(WriteTextError, match="无 .gfont"):
        write_text.load_font(str(tmp_path), None)
    (tmp_path / "only.gfont").write_bytes(_build_v2_font({"永": (_BOX_POINTS, _BOX_VERBS)}))
    with pytest.raises(WriteTextError, match="找不到字库"):
        write_text.load_font(str(tmp_path), "不存在")


def test_hutuji_write_end_to_end(tmp_path, monkeypatch):
    """工具全链：合成字库 → 排版 → 无版式 vpype → §5 校验 → 产物 URL。"""
    from hutuji_mcp import server
    from hutuji_mcp.config import Settings

    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    (font_dir / "test.gfont").write_bytes(
        _build_v2_font(
            {
                "永": (_BOX_POINTS, _BOX_VERBS),
                "国": (_BOX_POINTS, _BOX_VERBS),
                "中": (_BOX_POINTS, _BOX_VERBS),
                "，": (_COMMA_POINTS, _COMMA_VERBS),
            }
        )
    )
    out_dir = (tmp_path / "out").resolve()
    out_dir.mkdir()
    patched = Settings(
        llm_base_url="http://127.0.0.1:1", llm_api_key="", llm_model="", output_dir=out_dir,
        public_base_url="http://127.0.0.1:8300", host="127.0.0.1", port=8300,
        output_ttl_seconds=600, gfont_dir=str(font_dir),
    )
    monkeypatch.setattr(server, "settings", patched)
    server._gfont_cache.clear()
    server._gfont_metrics_cache.clear()

    payload = json.loads(asyncio.run(server.hutuji_write("永国中，永国")))
    assert payload["total_pages"] == 1
    assert payload["missing_chars"] == []
    name = payload["url"].rsplit("/", 1)[-1]
    gcode_path = out_dir / name
    assert gcode_path.exists()
    gcode = gcode_path.read_text(encoding="utf-8")
    stats = validate_gcode(gcode)  # §5 契约不抛即过
    assert stats.pen_lifts >= 6  # 三个方框+逗号×1 以上
    # 无版式管线：G-code X 域 = 排版 SVG 域（恒等），不被 layout 缩放
    import re as _re

    xs = [
        float(m.group(1))
        for m in _re.finditer(r"X(-?[\d.]+)Y(-?[\d.]+)", gcode)
    ]
    assert min(xs) >= 8.0  # 左边距不被压缩
    assert max(xs) <= 182.0


def test_hutuji_write_rejects_empty_and_overlong(monkeypatch):
    from hutuji_mcp import server

    with pytest.raises(WriteTextError, match="文本为空"):
        asyncio.run(server.hutuji_write("   "))
    with pytest.raises(WriteTextError, match="文本超长"):
        asyncio.run(server.hutuji_write("字" * 5001))
