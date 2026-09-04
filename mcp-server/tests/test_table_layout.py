"""table_layout：表格排版引擎契约（190 界 / 分页 / 表头重复 / 合并格 / 缩字号）。

字库用合成 v2 明文 gfont（与 test_write_text 同法）：探针字「永」「国」是
100×100 方框（full_width=em=100，scale = size_mm/100），内容字同形。
"""
from __future__ import annotations

import io
import struct
import zipfile

import pytest

from hutuji_mcp import doc_ingest, table_layout, write_text
from hutuji_mcp.gcode_gen import svg_to_gcode, validate_gcode
from hutuji_mcp.llm_svg import _validate_svg
from hutuji_mcp.write_text import WriteLayout, WriteTextError

# 探针/内容字全部 100×100 方框（基线 y=0，字身 [-100,0]）
_BOX_POINTS = [0.0, -100.0, 100.0, -100.0, 100.0, 0.0, 0.0, 0.0]
_BOX_VERBS = [0, 1, 1, 1]

_CHARS = "永国0123456789姓名分数张三李四甲乙学生"


def _utf(text: str) -> bytes:
    data = text.encode("utf-8")
    return struct.pack(">H", len(data)) + data


def _glyph_bytes(key: str) -> bytes:
    out = bytearray()
    out += struct.pack(">H", ord(key))  # v<=7：key 是 u16 char
    out += struct.pack(">i", len(_BOX_POINTS))
    for v in _BOX_POINTS:
        out += struct.pack(">f", v)
    out += struct.pack(">i", len(_BOX_VERBS))
    out += bytes(_BOX_VERBS)
    return bytes(out)


def _fixture_font():
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as archive:
        for ch in _CHARS:
            archive.writestr(str(ord(ch)), _glyph_bytes(ch))
    out = bytearray()
    out += struct.pack(">i", 2)  # v2
    out += _utf("testfamily")
    out += struct.pack(">i", 0)
    out += _utf("测试字库")
    out += _utf("pytest")
    out += _utf("")
    out += struct.pack(">i", 100)
    out += struct.pack(">i", len(_CHARS))
    out += _utf("uuid-test")
    out += struct.pack(">i", 0)
    out += zip_buf.getvalue()
    from hutuji_mcp import gfont

    return gfont.GfontFile.parse(bytes(out))


@pytest.fixture(scope="module")
def font_and_metrics():
    font = _fixture_font()
    return font, write_text.measure_font(font)


def _layout(**over):
    base = dict(size_mm=8.0, line_spacing=1.25, char_gap_mm=0.5)
    base.update(over)
    return WriteLayout(**base)


def _bbox(page):
    xs = [x for s in page.strokes for x, _ in s.points]
    ys = [y for s in page.strokes for _, y in s.points]
    return min(xs), max(xs), min(ys), max(ys)


def _verticals(page):
    return [
        s for s in page.strokes
        if len(s.points) == 2 and abs(s.points[0][0] - s.points[1][0]) < 1e-6
    ]


def _horizontals(page):
    return [
        s for s in page.strokes
        if len(s.points) == 2 and abs(s.points[0][1] - s.points[1][1]) < 1e-6
    ]


def test_single_page_bounds_and_shared_borders(font_and_metrics):
    font, metrics = font_and_metrics
    table = doc_ingest.ParsedTable(
        name="成绩", rows=[["姓名", "分数"], ["张三", "95"], ["李四", "88"]]
    )
    res = table_layout.layout_tables([table], font, metrics, _layout())
    assert len(res.pages) == 1
    page = res.pages[0]
    x0, x1, y0, y1 = _bbox(page)
    assert x0 >= 0 and x1 <= 190.0 and y0 >= 0 and y1 <= 190.0
    # 3 行共享边框：横线 = 顶 1 + 每行底 3 = 4（不是 2×3+1=7）
    assert len(_horizontals(page)) == 4
    # 2 列 → 每行 3 条竖线，3 行共 9
    assert len(_verticals(page)) == 9


def test_pagination_repeats_header(font_and_metrics):
    font, metrics = font_and_metrics
    rows = [["姓名", "分数"]] + [[f"学生{i % 10}", "9{i % 10}"] for i in range(60)]
    res = table_layout.layout_tables(
        [doc_ingest.ParsedTable(name="成绩", rows=rows)], font, metrics, _layout()
    )
    assert len(res.pages) >= 2
    assert not res.truncated
    for page in res.pages:
        x0, x1, y0, y1 = _bbox(page)
        assert x1 <= 277.0 and y1 <= 190.0  # §5 页限：X 长边 277 / Y 宽边 190
    # 续页首行是表头（重复表头决策）：第 2 页 line_count 含表头 1 行
    assert res.pages[1].line_count >= 2


def test_horizontal_merge_suppresses_inner_verticals(font_and_metrics):
    font, metrics = font_and_metrics
    table = doc_ingest.ParsedTable(
        name="",
        rows=[["汇总标题", "", ""], ["甲", "乙", "01"]],
        merges=[(0, 0, 2)],
    )
    res = table_layout.layout_tables([table], font, metrics, _layout())
    page = res.pages[0]
    verts = _verticals(page)
    row0_top = 8.0
    row0 = [s for s in verts if abs(s.points[0][1] - row0_top) < 0.01]
    # 合并行只有左右外框 2 条竖线；数据行 3 列 → 4 条
    assert len(row0) == 2
    assert len(verts) == 2 + 4


def test_file_col_widths_honored(font_and_metrics):
    font, metrics = font_and_metrics
    narrow = doc_ingest.ParsedTable(name="", rows=[["甲", "乙"]], col_widths=[6.0, 6.0])
    wide = doc_ingest.ParsedTable(name="", rows=[["甲", "乙"]], col_widths=[24.0, 6.0])
    _, mx1, _, _ = _bbox(table_layout.layout_tables([narrow], font, metrics, _layout()).pages[0])
    _, mx2, _, _ = _bbox(table_layout.layout_tables([wide], font, metrics, _layout()).pages[0])
    assert mx2 > mx1 + 20  # 自报宽列明显更宽


def test_shrink_fits_wide_table(font_and_metrics):
    font, metrics = font_and_metrics
    rows = [["姓名", "分数", "姓名", "分数", "姓名", "分数", "姓名", "分数"]]
    rows += [["张三", "95", "李四", "88", "张三", "95", "李四", "88"]]
    res = table_layout.layout_tables(
        [doc_ingest.ParsedTable(name="", rows=rows)], font, metrics, _layout()
    )
    _, x1, _, _ = _bbox(res.pages[0])
    assert x1 <= 190.0


def test_too_wide_raises(font_and_metrics):
    font, metrics = font_and_metrics
    rows = [["字"] * 24 for _ in range(2)]
    with pytest.raises(WriteTextError, match="过宽"):
        table_layout.layout_tables(
            [doc_ingest.ParsedTable(name="", rows=rows)],
            font, metrics, _layout(char_gap_mm=6.0),
        )


def test_missing_chars_reported(font_and_metrics):
    font, metrics = font_and_metrics
    table = doc_ingest.ParsedTable(name="", rows=[["姓名", "鲲"]])
    res = table_layout.layout_tables([table], font, metrics, _layout())
    assert res.missing_chars == ["鲲"]


def test_truncated_at_max_pages(font_and_metrics):
    font, metrics = font_and_metrics
    rows = [["姓名"]] + [[f"学生{i % 10}"] for i in range(60)]
    res = table_layout.layout_tables(
        [doc_ingest.ParsedTable(name="", rows=rows)], font, metrics, _layout(), max_pages=1
    )
    assert res.truncated
    assert len(res.pages) == 1


def test_empty_tables_rejected(font_and_metrics):
    font, metrics = font_and_metrics
    with pytest.raises(WriteTextError, match="没有可排版"):
        table_layout.layout_tables([], font, metrics, _layout())


def test_pages_pass_svg_gate(font_and_metrics):
    font, metrics = font_and_metrics
    table = doc_ingest.ParsedTable(name="成绩", rows=[["姓名", "分数"], ["张三", "95"]])
    res = table_layout.layout_tables([table], font, metrics, _layout())
    for page in res.pages:
        _validate_svg(write_text.page_to_svg(page))


def test_gcode_end_to_end(font_and_metrics, tmp_path):
    """合成字库表格 → SVG → vpype(normalize=False) → §5 闸（真 vpype 子进程）。"""
    font, metrics = font_and_metrics
    table = doc_ingest.ParsedTable(name="成绩", rows=[["姓名", "分数"], ["张三", "95"]])
    res = table_layout.layout_tables([table], font, metrics, _layout())
    svg = write_text.page_to_svg(res.pages[0])
    svg_path = tmp_path / "t.svg"
    gcode_path = tmp_path / "t.gcode"
    svg_path.write_text(svg, encoding="utf-8")
    stats = svg_to_gcode(svg_path, gcode_path, normalize=False)
    assert stats.lines > 0
    raw = gcode_path.read_bytes()
    validate_gcode(raw.decode("ascii"), total_bytes=len(raw))
    # normalize=False 保版式：表格外框右缘仍在 mm 原坐标（不被 vpype layout 重排）
    text = raw.decode("ascii")
    assert "G90" in text and "G21" in text
