"""write_text（奎享单线字库排版链）+ gfont 解析器契约测试。

字体夹具是测试内现场合成的 v2 明文 .gfont（结构同真字库：Java 大端 DataInput
+ ZIP 十进制码位条目），不引入奎享专有字体文件。
"""

from __future__ import annotations

import asyncio
import io
import json
import struct
import time
import types
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
    # 竖版 190 页宽下用右边距 8 锁 174mm 测试行宽（旧横版为右边距 95；点位构造与本测试机制无关）
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0, margin_right_mm=8.0)
    # 页宽 174mm，全宽步进 10mm → 首行缩进后放 15 字，次行 17 字
    result = write_text.layout_text("永" * 40, font, metrics, layout)
    assert result.pages[0].line_count >= 3
    xs = [x for s in result.pages[0].strokes for x, _ in s.points]
    assert max(xs) <= 190.0 - 8.0 + 1e-6  # 不越右边距


def test_layout_cjk_uses_stroke_order_pen_lifts():
    """CJK 有笔顺数据时按 medians 段数逐笔抬笔；未传 stroke_data 仍走 gfont 单路径。"""
    from hutuji_mcp.stroke_order import StrokeData

    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0, paragraph_indent=0)
    # 「中」四笔：竖、横、左竖、右竖（测试夹具，非真库）
    medians = {
        "中": [
            [(512, 900), (512, 100)],
            [(200, 500), (800, 500)],
            [(200, 900), (200, 100)],
            [(800, 900), (800, 100)],
        ]
    }
    stroke = StrokeData(medians=medians)
    with_stroke = write_text.layout_text("中", font, metrics, layout, stroke_data=stroke)
    without = write_text.layout_text("中", font, metrics, layout)
    assert len(with_stroke.pages[0].strokes) == 4
    assert len(without.pages[0].strokes) == 1  # 夹具盒形单路径
    # 缺笔顺字回落 gfont，不整单失败
    mixed = write_text.layout_text("中永", font, metrics, layout, stroke_data=stroke)
    # 中=4 + 永(夹具单路径)=1
    assert len(mixed.pages[0].strokes) == 5


def test_layout_tab_matches_space_width():
    """Tab 与空格同价：测量与落版一致（修复前测量 5.5mm/落版 5.25mm，满行处折行错位）。"""
    font, metrics = _fixture_font(), _fixture_metrics()
    # text_width=36.9：「中中 文」测量 36.75 恰好一行放下；Tab 若按全宽半步+整字距
    # （37.0）测量会把「文」挤到第二行——两种空白必须落出完全相同的版。
    layout = write_text.WriteLayout(
        size_mm=10.0, char_gap_mm=0.5, paragraph_indent=0, margin_right_mm=145.1
    )
    tab = write_text.layout_text("中中\t文", font, metrics, layout)
    space = write_text.layout_text("中中 文", font, metrics, layout)
    tab_points = [[s.points for s in page.strokes] for page in tab.pages]
    space_points = [[s.points for s in page.strokes] for page in space.pages]
    assert tab.pages[0].line_count == 1
    assert tab_points == space_points


def test_layout_latin_cjk_boundary_counts_overhang_in_wrap():
    """拉丁→CJK 边界避让计入行宽测量：漏算时满行尾交界墨迹越出右边距（「1932年」修复的测量侧）。"""
    # 「年」墨区 x∈[-40,60]（左越界 40 单位，探针集命中 → cjk_left_overhang=40）；
    # 「A」三角墨区 x∈[-50,50] → latin_left_overhang=50，逐字槽=max(50, 50+50)=100。
    overhang_box = [-40.0, -100.0, 60.0, -100.0, 60.0, 0.0, -40.0, 0.0]
    font = gfont.GfontFile.parse(
        _build_v2_font(
            {
                "永": (_BOX_POINTS, _BOX_VERBS),
                "国": (_BOX_POINTS, _BOX_VERBS),
                "年": (overhang_box, _BOX_VERBS),
                "A": (_TRIANGLE_POINTS, _TRIANGLE_VERBS),
            }
        )
    )
    metrics = write_text.measure_font(font)
    assert metrics.cjk_left_overhang == pytest.approx(40.0)
    assert metrics.latin_left_overhang == pytest.approx(50.0)
    # size=10/gap=0.5：「A年」一对落版 25mm（A 10.5 + 避让 4 + 年 10.5），5 对末字墨缘 120.5。
    # 页宽 110：测量漏 4mm/对时 5×21=105≤110 不折行、墨迹越界；计入后 5×25=125>110 提前折行。
    layout = write_text.WriteLayout(
        size_mm=10.0, char_gap_mm=0.5, paragraph_indent=0, margin_right_mm=72.0
    )
    result = write_text.layout_text("A年" * 5, font, metrics, layout)
    assert result.pages[0].line_count == 2
    ink_right = max(
        x for page in result.pages for s in page.strokes for x, _ in s.points
    )
    assert ink_right <= 190.0 - 72.0 + 1e-6


def test_layout_punctuation_never_starts_line():
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0, margin_right_mm=8.0)
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


def test_layout_open_punct_never_ends_line():
    """避尾：开标点不得收行尾——恰满行时弹出随下一行行首（与避头对偶）。
    构造「《」恰为行尾最后一元：首行缩进 20mm 后每字 10mm，14 字+《=170≤174 放下，
    次字 180>174 溢出；无避尾时《墨区落在首行尾（右缘 178），修复后《须在次行行首。"""
    font = gfont.GfontFile.parse(
        _build_v2_font({ch: (_BOX_POINTS, _BOX_VERBS) for ch in "永国《"})
    )
    metrics = write_text.measure_font(font)
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0, margin_right_mm=8.0)
    result = write_text.layout_text("永" * 14 + "《" + "国" * 5, font, metrics, layout)
    page = result.pages[0]
    assert page.line_count == 2
    line1 = [s for s in page.strokes if all(y <= 18.0 for _, y in s.points)]
    line2 = [s for s in page.strokes if all(y > 18.0 for _, y in s.points)]
    assert line1 and line2
    # 首行 14 字墨区右沿 = 28+14×10=168；《若留守行尾则右沿 178（旧行为，此处立即红）
    assert max(x for s in line1 for x, _ in s.points) == pytest.approx(168.0, abs=0.01)
    # 次行以《起头：首笔 X=左边距 8.0
    assert min(x for x, _ in line2[0].points) == pytest.approx(8.0, abs=0.01)


def test_layout_kinsoku_merge_never_exceeds_page_limit():
    """避头并入不得越 §5 页限：连续闭标点在满行会把墨迹顶出页宽硬线，被
    validate_gcode 整单拒绝（功能性失败，非外观）。并入前须判 x_end≤_PAGE_WIDTH_MM，
    超限则避头让位、标点照常折行。
    竖版构造（190 页宽、174mm 行宽、10mm 步进，2026-09-06 重推并实测）：首行缩进后
    15 字、次行 17 字、第三行 17 字；。并入第三行末（墨至 188≤190 放行）；」再并
    将墨至 198>190 越限 → 折到第四行行首。"""
    font = gfont.GfontFile.parse(
        _build_v2_font({ch: (_BOX_POINTS, _BOX_VERBS) for ch in "永国。」"})
    )
    metrics = write_text.measure_font(font)
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0)
    result = write_text.layout_text("永" * 49 + "。" + "」", font, metrics, layout)
    page = result.pages[0]
    assert page.line_count == 4
    # 全页墨迹右沿不得越 190（§5 限幅真值 = 竖版撰写页宽；实测最大 188）
    assert max(x for s in page.strokes for x, _ in s.points) <= 190.0
    # 。并入第三行（该行 18 笔 = 17 字+。）；」越限折到第四行行首 X=8
    line3 = [s for s in page.strokes if all(34.0 < y <= 50.0 for _, y in s.points)]
    line4 = [s for s in page.strokes if all(y > 50.0 for _, y in s.points)]
    assert len(line3) == 18
    assert line4 and min(x for x, _ in line4[0].points) == pytest.approx(8.0, abs=0.01)


def test_layout_paginates_and_reports_missing():
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout(
        size_mm=20.0, line_spacing=1.5, margin_top_mm=240.0, margin_bottom_mm=0.0,
        margin_right_mm=8.0,  # 竖版 190 页宽下保持 174mm 测试行宽，保住 6/8 字容量点位（旧横版为 277−8−95）
    )
    # 竖版页高 277−240=37mm、行高 30mm → 一页一行；首行缩进后容量 6 字、续行 8 字，15 字 → 3 页
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


def test_load_font_default_picks_most_complete(tmp_path):
    """空名默认选字数最多的字库（2026-09-04 用户指令钉死：字体库要选最全的）。
    生产对账（本机 9 个真字库）：declared_count 与 ZIP 实际条目数逐一相等；
    如果梦万国库 12806 为最大，且 ASCII 62/62、拼音声调字符 25/25 全覆盖——
    混写/带调拼音都依赖默认命中最全字库，退回「取第一个」此处立即红。"""
    (tmp_path / "aaa_small.gfont").write_bytes(
        _build_v2_font({ch: (_BOX_POINTS, _BOX_VERBS) for ch in "永国"})
    )
    (tmp_path / "zzz_big.gfont").write_bytes(
        _build_v2_font({ch: (_BOX_POINTS, _BOX_VERBS) for ch in "永国中文字"})
    )
    font = write_text.load_font(str(tmp_path), None)
    assert font.meta.declared_count == 5  # 文件名排序小的只有 2 字；按字数选大


def test_load_font_default_prefers_handwriting_pick(tmp_path):
    """2026-09-06 用户拍板默认改「手写感」blood8真迹_xiongzai——即使字数更少也优先。"""
    (tmp_path / "blood8真迹_xiongzai_blood8.gfont").write_bytes(
        _build_v2_font({"永": (_BOX_POINTS, _BOX_VERBS), "国": (_BOX_POINTS, _BOX_VERBS)})
    )
    (tmp_path / "z如果梦万国库.gfont").write_bytes(
        _build_v2_font(
            {
                "永": (_BOX_POINTS, _BOX_VERBS),
                "国": (_BOX_POINTS, _BOX_VERBS),
                "中": (_BOX_POINTS, _BOX_VERBS),
            }
        )
    )
    font = write_text.load_font(str(tmp_path), None)
    assert font.meta.declared_count == 2  # 偏好命中 2 字库而非 3 字库


def test_load_font_preference_falls_back_when_missing_or_broken(tmp_path):
    """偏好文件缺席/损坏时回落「字数最多」——默认链永不因偏好文件问题瘫痪。"""
    (tmp_path / "blood8真迹_xiongzai_blood8.gfont").write_bytes(b"broken")
    (tmp_path / "a可用.gfont").write_bytes(
        _build_v2_font({"永": (_BOX_POINTS, _BOX_VERBS), "国": (_BOX_POINTS, _BOX_VERBS)})
    )
    font = write_text.load_font(str(tmp_path), None)
    assert font.meta.declared_count == 2


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
    # 无版式管线：G-code X 域 = 排版 SVG 域（恒等），不被 layout 缩放。手写抖动
    # （hutuji_write 链默认开）绕实例墨心旋转/缩放/平移，边缘墨最坏出格 ≈0.109em
    # （精确界由 test_layout_handwriting_jitter_bounded_near_original_ink 钉），
    # 故恒等断言带抖动预算；§5 页界 0–190 仍硬。
    import re as _re

    xs = [
        float(m.group(1))
        for m in _re.finditer(r"X(-?[\d.]+)Y(-?[\d.]+)", gcode)
    ]
    jitter_budget = 0.115 * 12.0  # 工具默认字号 12mm
    assert min(xs) >= 8.0 - jitter_budget  # 左边距只允许抖动预算内出格
    assert max(xs) <= 182.0 + jitter_budget
    assert min(xs) >= 0.0 and max(xs) <= 190.0  # §5 页界

    # 预览必须是 PNG：设备侧 IsValidDrawCapabilityUrl 硬校验 .png、LvglAllocatedImage
    # 只解 PNG。首版发 .svg 会在 StartDraw 直接被拒（纸都不动），故此处钉死。
    assert payload["preview_url"].endswith(".png")
    preview_path = out_dir / payload["preview_url"].rsplit("/", 1)[-1]
    assert preview_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert payload["pages"][0]["preview_url"] == payload["preview_url"]


def test_write_pages_arg_matches_chain_push_byte_for_byte(tmp_path, monkeypatch):
    """`pages_arg` 透传件与链式推送实际发送的 pages 字符串逐字节一致。

    设备幂等重入要求「完全相同参数」（protocol §4.2）：两步回落时云端若把
    pages_arg 原样透传给 hutuji.draw，必须与链式推送发出的是同一份字节，
    否则被当成新任务 busy 拒绝。两处共用 `_pages_argument` 序列化单源，
    本测试钉死「返回值 pages_arg == 链式推送 pages」这一可观察契约。
    """
    from hutuji_mcp import server
    from hutuji_mcp.config import Settings
    from httpx import Response

    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    (font_dir / "test.gfont").write_bytes(
        _build_v2_font({ch: (_BOX_POINTS, _BOX_VERBS) for ch in "永国中"})
    )
    out_dir = (tmp_path / "out").resolve()
    out_dir.mkdir()
    patched = Settings(
        llm_base_url="http://127.0.0.1:1", llm_api_key="", llm_model="", output_dir=out_dir,
        public_base_url="http://127.0.0.1:8300", host="127.0.0.1", port=8300,
        output_ttl_seconds=600, gfont_dir=str(font_dir),
        xiaozhi_device_token="tok",
    )
    monkeypatch.setattr(server, "settings", patched)
    server._gfont_cache.clear()
    server._gfont_metrics_cache.clear()

    pushed: dict[str, str] = {}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            if json["name"] == "hutuji.status":
                return Response(200, json={"success": True, "data": {"plotter_sku": "paper"}})
            pushed.update(json["arguments"])
            return Response(200, json={"success": True, "data": "previewing"})

    # 链式推送经 device_messaging（2026-09-08），须补丁该模块 httpx。
    from hutuji_mcp import device_messaging as dm

    monkeypatch.setattr(dm, "httpx", types.SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(
        dm, "load_device_token", lambda env_fallback="": env_fallback or "tok"
    )

    async def _run() -> dict:
        payload = json.loads(
            await server.hutuji_write("永国中" * 6, size_mm=40.0, line_spacing=3.0)
        )
        for _ in range(100):
            if pushed:
                break
            await asyncio.sleep(0.01)  # 等 fire-and-forget 链式推送落桩
        return payload

    payload = asyncio.run(_run())
    assert payload["total_pages"] >= 3  # 多页，pages 语义才有分歧面
    assert pushed["pages"]  # 链式推送确实发出去了
    assert payload["pages_arg"] == pushed["pages"]  # 逐字节一致
    assert pushed["url"] == payload["url"]
    assert pushed["preview_url"] == payload["preview_url"]

    # pages_arg 形态钉死：紧凑无空格、键序 url→preview_url、页数一致、
    # 首页与顶层 url/preview_url 相同（protocol §4.2 硬契约）。
    arg = payload["pages_arg"]
    assert " " not in arg
    assert arg.index('"url"') < arg.index('"preview_url"')
    parsed = json.loads(arg)
    assert len(parsed) == payload["total_pages"]
    assert parsed[0] == {"url": payload["url"], "preview_url": payload["preview_url"]}


def test_write_pages_arg_present_without_chain_push(tmp_path, monkeypatch):
    """未配 device token（无链式推送）时 pages_arg 仍必须在返回值里——
    它服务的正是「推送没发、云端自己两步衔接」的回落场景。"""
    from hutuji_mcp import server
    from hutuji_mcp.config import Settings

    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    (font_dir / "test.gfont").write_bytes(
        _build_v2_font({ch: (_BOX_POINTS, _BOX_VERBS) for ch in "永国中"})
    )
    out_dir = (tmp_path / "out").resolve()
    out_dir.mkdir()
    patched = Settings(
        llm_base_url="http://127.0.0.1:1", llm_api_key="", llm_model="", output_dir=out_dir,
        public_base_url="http://127.0.0.1:8300", host="127.0.0.1", port=8300,
        output_ttl_seconds=600, gfont_dir=str(font_dir),
        xiaozhi_device_token="",  # 空 = 链式推送禁用
    )
    monkeypatch.setattr(server, "settings", patched)
    server._gfont_cache.clear()
    server._gfont_metrics_cache.clear()

    payload = json.loads(asyncio.run(server.hutuji_write("永国中，永国")))
    assert json.loads(payload["pages_arg"]) == [
        {"url": payload["url"], "preview_url": payload["preview_url"]}
    ]

def test_write_multipage_staggers_output_ttl(tmp_path, monkeypatch):
    """多页文章错峰 TTL（protocol §4.2）：设备逐页「下载→灌流→换纸」串行推进，
    页耗时按行数折算（2026-09-01 真机实测 8.8 行/s + 换纸 8s，12mm 满页 10597 行
    ≈ 20 分钟）；另加一次性确认补贴 600s——t0 到人按「确认」开画的等待无上界、
    与页大小无关，稀疏页单靠 2× 余量只有几分钟绝对量（est₁≈248s 时确认延迟
    >12 分钟第 2 页必 404，2026-09-03 advisory 复核实锤）。故第 i 页 mtime 前移到
    t0 + 补贴 + Σ_{j<i}（lines_j/8.8 + 8）× 2（第 1 页同样带补贴：确认延迟卡住
    的是第 1 页首次下载本身）；单页文章不动时间戳（保持既有单页语义），
    且 _serve_file 对未来 mtime 的页必须照常 200（now-mtime 为负 → 未过期）。
    """
    from hutuji_mcp import server
    from hutuji_mcp.config import Settings

    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    (font_dir / "test.gfont").write_bytes(
        _build_v2_font({ch: (_BOX_POINTS, _BOX_VERBS) for ch in "永国中"})
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

    # 字号 40mm × 行距 3.0 → 每页仅 1 行（页高 174 / 行高 120），一行 4 字即逐页
    started = time.time()
    payload = json.loads(
        asyncio.run(
            server.hutuji_write("永国中" * 6, size_mm=40.0, line_spacing=3.0)
        )
    )
    assert payload["total_pages"] >= 3

    mtimes = []
    for page in payload["pages"]:
        stem = page["url"].rsplit("/", 1)[-1][: -len(".gcode")]
        trio = [out_dir / f"{stem}{suffix}" for suffix in (".svg", ".gcode", ".png")]
        per_page = {p.stat().st_mtime for p in trio}
        assert max(per_page) - min(per_page) < 1.0  # 三个后缀同窗口
        mtimes.append(trio[1].stat().st_mtime)

    # 第 1 页也带一次性确认补贴（多页语义）：mtime ≈ 写盘时刻 + 补贴。
    allowance = server.WRITE_PAGE_CONFIRM_ALLOWANCE_SECONDS
    assert started + allowance - 1.0 <= mtimes[0] <= time.time() + allowance + 1.0
    # 逐页公式钉死：第 i 页相对第 1 页的前移量 = Σ_{j<i}（lines_j/率 + 换纸）× 余量，
    # 与每页实际行数挂钩；补贴在差值中抵消——恰好钉死它「只加一次、不按页乘」；
    # 退回统一预算（与行数脱钩）或退回按页乘补贴，此处立即红。
    cumulative = 0.0
    for i in range(1, len(mtimes)):
        prev_lines = payload["pages"][i - 1]["lines"]
        cumulative += (
            prev_lines / server.WRITE_PAGE_LINES_PER_SECOND
            + server.WRITE_PAGE_CHANGE_SECONDS
        ) * server.WRITE_PAGE_TIME_MARGIN
        assert abs((mtimes[i] - mtimes[0]) - cumulative) < 5.0
    # 只允许带新实机计时证据的反向调整（每次调整必须改断言并附证据）。
    assert server.WRITE_PAGE_LINES_PER_SECOND <= 8.8
    assert server.WRITE_PAGE_CHANGE_SECONDS >= 8.0
    assert server.WRITE_PAGE_TIME_MARGIN >= 2.0
    assert allowance >= 600.0

    # 末页 mtime 在未来，仍须可下载
    last = payload["pages"][-1]["url"].rsplit("/", 1)[-1]
    resp = asyncio.run(server._serve_file(last))
    assert resp.status_code == 200
    assert resp.headers["X-Hutuji-CRC32"]


def test_page_preview_png_is_visible_rgb_and_survives_empty_page():
    """预览 PNG 必须 RGB 512² 且真有黑像素——1px 灰度整页细线在设备缩放下白屏
    （2026-09-01 实机实测），空页仍须出图（否则设备预览校验直接拒）。"""
    import imageio.v3 as iio
    import numpy as np

    font, metrics = _fixture_font(), _fixture_metrics()
    result = write_text.layout_text("永国中", font, metrics, write_text.WriteLayout())
    img = iio.imread(io.BytesIO(write_text.page_to_preview_png(result.pages[0])))
    assert img.shape == (512, 512, 3)
    dark = int((img[..., 0] == 0).sum())
    assert dark > 200  # 内容包围盒裁剪后笔画应占可观像素，不是零星几点

    blank = iio.imread(io.BytesIO(write_text.page_to_preview_png(write_text.LaidOutPage())))
    assert blank.shape == (512, 512, 3)
    assert int((blank == 255).all(axis=2).sum()) == 512 * 512  # 纯白，不抛
    assert np.array_equal(blank[0, 0], [255, 255, 255])


def test_write_chain_push_sends_pages_json_string(tmp_path, monkeypatch):
    """多页文章链式推送：pages 以紧凑 JSON 字符串传（MCP Property 无 array 类型），
    键序 url→preview_url 固定（设备侧 ParseArticlePages 按此序扫描），
    且 arguments.url/preview_url 必须等于 pages[0]——否则用户看的预览与写的内容不同。"""
    from hutuji_mcp import server
    from httpx import Response

    captured: dict[str, object] = {}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            captured["json"] = json
            captured["auth"] = headers.get("Authorization", "")
            return Response(200, json={"success": True, "data": "previewing"})

    # 链式推送经 device_messaging（2026-09-08 自愈），须补丁该模块的 httpx。
    from hutuji_mcp import device_messaging as dm

    monkeypatch.setattr(dm, "httpx", types.SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(dm, "load_device_token", lambda env_fallback="": env_fallback or "tok")
    monkeypatch.setattr(
        server, "settings",
        types.SimpleNamespace(
            xiaozhi_device_token="tok", xiaozhi_api_base="http://127.0.0.1:1",
        ),
    )
    pages = [
        {"url": "https://h/files/a.gcode", "preview_url": "https://h/files/a.png"},
        {"url": "https://h/files/b.gcode", "preview_url": "https://h/files/b.png"},
    ]
    asyncio.run(server._chain_device_draw(pages[0]["url"], pages[0]["preview_url"], pages))

    args = captured["json"]["arguments"]
    assert captured["json"]["name"] == "hutuji.draw"
    assert args["url"] == pages[0]["url"]
    assert args["preview_url"] == pages[0]["preview_url"]
    assert isinstance(args["pages"], str)  # Property 只有 string，不能传 list
    assert args["pages"].index('"url"') < args["pages"].index('"preview_url"')
    assert json.loads(args["pages"]) == pages
    assert " " not in args["pages"]  # 紧凑分隔符，省设备侧解析与传输

    # 单页 draw 链不带 pages（既有契约不变）。
    captured.clear()
    asyncio.run(server._chain_device_draw(pages[0]["url"], pages[0]["preview_url"]))
    assert "pages" not in captured["json"]["arguments"]


def test_hutuji_write_rejects_empty_and_overlong(monkeypatch):
    from hutuji_mcp import server

    with pytest.raises(WriteTextError, match="文本为空"):
        asyncio.run(server.hutuji_write("   "))
    with pytest.raises(WriteTextError, match="文本超长"):
        asyncio.run(server.hutuji_write("字" * 5001))

def test_layout_hard_breaks_overlong_latin_token():
    """单 token 超整行宽（长 URL/英文串）逐字硬断折行。旧行为整词独占一行、
    墨迹顶出 §5 页限被 validate_gcode 整单拒绝（2026-09-04 实测 60 个 x 墨至 288mm）。"""
    font = _fixture_font()
    metrics = _fixture_metrics()
    layout = write_text.WriteLayout(paragraph_indent=0, margin_right_mm=8.0)
    result = write_text.layout_text("A" * 60, font, metrics, layout)
    # 夹具「A」三角墨宽满 em（x∈[-50,+50]）：2026-09-06 步进修复后拉丁槽 =
    # 墨右缘+全库拉丁左越界 = 100 units；默认字号 12mm 下槽宽 12.8mm（旧的半 em
    # 口径 6.8mm 会让相邻 A 墨区重叠 5.2mm——正是本批修的叠字 bug）。
    # 174/12.8=13 字/行，60 字 = 13×4+8 = 5 行；旧行为 1 行越界 288mm，
    # guard 误放 overflow 之后曾退化「每字一行」——行数精确钉 5
    assert result.pages[0].line_count == 5
    total_strokes = sum(len(p.strokes) for p in result.pages)
    assert total_strokes == 60  # 拆字不丢字：每个 A 一条三角笔画
    max_x = max(x for p in result.pages for s in p.strokes for x, _ in s.points)
    assert max_x <= write_text._PAGE_WIDTH_MM  # §5 硬线（旧行为约 288mm）
    assert max_x <= layout.margin_left_mm + layout.text_width_mm


def test_layout_skips_blank_and_whitespace_paragraphs():
    """空段与纯空白段（空格/制表）都整段跳过、不产空行——省纸取向。"""
    font = _fixture_font()
    metrics = _fixture_metrics()
    result = write_text.layout_text("中\n \n\t\n\n文", font, metrics, write_text.WriteLayout())
    assert result.pages[0].line_count == 2  # 旧行为纯空白段各占一行
    total_strokes = sum(len(p.strokes) for p in result.pages)
    assert total_strokes == 2


def test_load_font_default_scan_skips_unreadable(tmp_path, monkeypatch):
    """默认（字数最多）扫描遇到读失败的文件跳过而非裸抛 OSError。"""
    small = tmp_path / "a小字库.gfont"
    big = tmp_path / "b大字库.gfont"
    small.write_bytes(
        _build_v2_font({"永": (_BOX_POINTS, _BOX_VERBS), "国": (_BOX_POINTS, _BOX_VERBS)})
    )
    big.write_bytes(
        _build_v2_font({ch: (_BOX_POINTS, _BOX_VERBS) for ch in "永国中文字"})
    )
    real_read = write_text._read_font_bytes

    def flaky(path: str) -> bytes:
        if str(path).endswith("b大字库.gfont"):
            raise OSError("模拟读失败")
        return real_read(path)

    monkeypatch.setattr(write_text, "_read_font_bytes", flaky)
    font = write_text.load_font(str(tmp_path))
    assert font.meta.declared_count == 2


def test_load_font_named_read_failure_maps_to_write_text_error(tmp_path, monkeypatch):
    (tmp_path / "x字库.gfont").write_bytes(
        _build_v2_font({"永": (_BOX_POINTS, _BOX_VERBS)})
    )

    def boom(path: str) -> bytes:
        raise OSError("模拟读失败")

    monkeypatch.setattr(write_text, "_read_font_bytes", boom)
    with pytest.raises(WriteTextError, match="字库"):
        write_text.load_font(str(tmp_path), "x字库")


def _line_x_starts(page):
    """每行首字 x 与行带顶 y：按 y 聚类（box 字形 y∈[baseline-100*scale, baseline]）。"""
    rows = {}
    for stroke in page.strokes:
        for x, y in stroke.points:
            rows.setdefault(round(y), []).append(x)
    bands = []
    for y in sorted(rows):
        if bands and y - bands[-1][0] <= 20:
            bands[-1][1].extend(rows[y])
            continue
        bands.append([y, list(rows[y])])
    return [(band[0], min(band[1]), max(band[1])) for band in bands]


def test_heading_centered_larger_with_gaps():
    """标题段：18mm 放大 + 水平居中 + 免缩进 + 段前后各空一行；正文行不变。"""
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout()
    res = write_text.layout_text(
        "中文字\n永国中文\n中文字\n永国中文",
        font, metrics, layout, headings={0, 2},
    )
    page = res.pages[0]
    bands = _line_x_starts(page)
    assert len(bands) == 4
    center_x = layout.margin_left_mm + layout.text_width_mm / 2  # 95
    # 缩进与 indent_mm 同源（字号+字距）；夹具方框下旧墨宽口径数值巧合相同
    body_first_x = layout.margin_left_mm + 2 * (layout.size_mm + layout.char_gap_mm)
    for i, (top, lo, hi) in enumerate(bands):
        if i in (0, 2):  # 标题行：居中（左右缘关于版心对称）且字身放大
            assert (lo + hi) / 2 == pytest.approx(center_x, abs=1.0)
            assert hi - lo > 3 * layout.size_mm  # 3 字标题墨宽明显超正文字号步进
        else:  # 正文行：段首缩进 2 全宽，顶左缘
            assert lo == pytest.approx(body_first_x, abs=1.0)
    # 行带顶 y：标题行字身 18mm（顶=基线-18），正文 12mm；行距 19.2
    base_top = layout.margin_top_mm + metrics.baseline_to_top * (
        layout.size_mm / metrics.em_height
    )  # 第 0 行基线
    line_h = layout.line_height_mm
    # 标题0 占行带 [0,2) 顶=base_top-18；正文1 在第 3 行带（2+段后空1）顶=base_top+3*lh-12
    assert bands[0][0] == pytest.approx(base_top - layout.heading_size_mm, abs=0.6)
    assert bands[1][0] == pytest.approx(base_top + 3 * line_h - layout.size_mm, abs=0.6)
    # 标题2 前空一行：正文1 占行带 3，标题2 行带 5（3+1+段前空1）
    assert bands[2][0] == pytest.approx(base_top + 5 * line_h - layout.heading_size_mm, abs=0.6)
    assert page.line_count == 9  # 2+1（空）+1+1（空）+2+1（空）+1


def test_heading_at_page_top_no_pre_gap():
    font, metrics = _fixture_font(), _fixture_metrics()
    res = write_text.layout_text(
        "中文字\n永国中文", font, metrics, write_text.WriteLayout(), headings={0}
    )
    page = res.pages[0]
    bands = _line_x_starts(page)
    base_top = 8 + metrics.baseline_to_top * 0.12
    assert bands[0][0] == pytest.approx(base_top - 18.0, abs=0.6)  # 贴页首，无段前空距
    assert page.line_count == 4  # 标题 2 行带 + 段后空 1 + 正文 1


def test_heading_near_page_bottom_moves_to_next_page():
    """标题与其行带须同框：空距放不下时整标题切下页（不栏尾孤标题）。"""
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout()
    max_lines = int(layout.text_height_mm // layout.line_height_mm)
    filler = "\n".join(["永"] * (max_lines - 1))
    res = write_text.layout_text(
        filler + "\n中文字\n永国中文", font, metrics, layout, headings={max_lines - 1}
    )
    assert len(res.pages) == 2
    assert not res.truncated
    bands2 = _line_x_starts(res.pages[1])
    center_x = layout.margin_left_mm + layout.text_width_mm / 2
    assert (bands2[0][1] + bands2[0][2]) / 2 == pytest.approx(center_x, abs=1.0)


def test_heading_long_title_wraps_centered():
    """长标题折行后每个视觉行都各自居中。"""
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout()
    # 竖版行宽 174mm（190−8−8），18mm 全宽步进 18.8mm：174/18.8 = 9 字/行；
    # 20 字标题折 9+9+2 三个视觉行带（2026-09-06 竖版重推并实测）
    title = "中" * 20
    res = write_text.layout_text(title + "\n永国中文", font, metrics, layout, headings={0})
    page = res.pages[0]
    bands = _line_x_starts(page)
    center_x = layout.margin_left_mm + layout.text_width_mm / 2
    # 前三个行带都是标题行（9 字满行 ×2 + 2 字余行），均居中
    assert (bands[0][1] + bands[0][2]) / 2 == pytest.approx(center_x, abs=1.0)
    assert (bands[1][1] + bands[1][2]) / 2 == pytest.approx(center_x, abs=1.0)
    assert (bands[2][1] + bands[2][2]) / 2 == pytest.approx(center_x, abs=1.0)
    # 满行墨宽 ≈ 9*18.8−0.8=168.4 → 左沿≈8+2.8=10.8；余行 2*18.8−0.8=36.8 → 左沿≈76.6
    assert bands[2][1] - bands[0][1] == pytest.approx(76.6 - 10.8, abs=1.0)

def test_heading_size_validation():
    layout = write_text.WriteLayout(heading_size_mm=50.0)
    with pytest.raises(write_text.WriteTextError, match="标题字号"):
        layout.validate()


def test_no_headings_behavior_unchanged():
    """headings=None 与空集行为逐行一致（回归闸：语音写字链不受影响）。"""
    font, metrics = _fixture_font(), _fixture_metrics()
    text = "中文字\n永国中文\n中文字"
    a = write_text.layout_text(text, font, metrics, write_text.WriteLayout())
    b = write_text.layout_text(text, font, metrics, write_text.WriteLayout(), headings=set())
    assert a.pages[0].line_count == b.pages[0].line_count == 3
    assert [[s.points for s in p.strokes] for p in a.pages] == [
        [s.points for s in p.strokes] for p in b.pages
    ]


def test_t2s_maps_traditional_singles_and_keeps_rest():
    """繁→简逐字映射：命中的换字，简体与非汉字原样（云端文章混入繁体的收口）。"""
    from hutuji_mcp import t2s

    assert t2s.to_simplified("劉胡蘭，女，於1932年出生於山西省文水縣。") == (
        "刘胡兰，女，于1932年出生于山西省文水县。"
    )
    assert t2s.to_simplified("已经是简体 abc 123。") == "已经是简体 abc 123。"
    assert t2s.to_simplified("") == ""


# 宽墨字库夹具：高探针「永」100×100 方框，宽探针「国」墨宽只 70（同真行书库：国墨宽
# 0.70em），「中」墨宽满 100em。旧口径拿「国」墨宽当全宽 → 步进 0.7em 撑不住 1.0em 的字。
_NARROW_BOX_POINTS = [15.0, -100.0, 85.0, -100.0, 85.0, 0.0, 15.0, 0.0]


def _wide_ink_font() -> gfont.GfontFile:
    return gfont.GfontFile.parse(
        _build_v2_font(
            {
                "永": (_BOX_POINTS, _BOX_VERBS),
                "国": (_NARROW_BOX_POINTS, _BOX_VERBS),
                "中": (_BOX_POINTS, _BOX_VERBS),
            }
        )
    )


def test_cjk_advance_is_size_plus_gap_not_probe_ink_width():
    """相邻 CJK 字墨区不得交叠：步进按字号（字身 em）+ 字距，不按「国」墨宽。

    旧口径 full_width(国)*scale + gap 在宽探针窄于 em 的字库上系统性短缺
    （2026-09-06 用户纸面实证「字重叠到一起」，如果梦万国库 0.70em 复现）。
    """
    font = _wide_ink_font()
    metrics = write_text.measure_font(font)
    assert metrics.full_width < metrics.em_height  # 夹具前提：宽探针窄于字身
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.8)
    page = write_text.layout_text("中中中", font, metrics, layout).pages[0]

    xs = sorted(px for stroke in page.strokes for px, _ in stroke.points)
    # 每字 4 个不同 x 值中的 2 个 distinct（方框），按字聚类取每字墨区左右缘
    assert len(page.strokes) == 3  # 一字一方框 = 一条折线
    boxes = [
        (
            min(px for px, _ in stroke.points),
            max(px for px, _ in stroke.points),
        )
        for stroke in page.strokes
    ]
    for left, right in boxes:
        assert right - left == pytest.approx(layout.size_mm, abs=0.01)  # 墨宽满 em
    for prev, nxt in zip(boxes, boxes[1:]):
        assert nxt[0] - prev[1] == pytest.approx(layout.char_gap_mm, abs=0.01)


def test_latin_digits_advance_half_em_not_ink_residue():
    """拉丁/数字按半 em 步进：v<9 字库 glyph_advance 是墨宽残值，「1932」会叠成一团。"""
    font = gfont.GfontFile.parse(
        _build_v2_font(
            {
                "永": (_BOX_POINTS, _BOX_VERBS),
                "国": (_BOX_POINTS, _BOX_VERBS),
                # 数字墨宽只 40（窄字），glyph_advance=40 是墨宽残值；
                # 旧口径按残值步进 → 同文本「19」二字只隔 4mm、与墨宽相抵即重叠。
                "1": ([0.0, -80.0, 40.0, 0.0], [0, 1]),
                "9": ([0.0, -80.0, 40.0, 0.0], [0, 1]),
            }
        )
    )
    metrics = write_text.measure_font(font)
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0)
    page = write_text.layout_text("19", font, metrics, layout).pages[0]
    starts = sorted({round(min(px for px, _ in s.points), 3) for s in page.strokes})
    assert len(starts) == 2
    assert starts[1] - starts[0] == pytest.approx(layout.size_mm * 0.5, abs=0.01)


def test_t2s_missing_table_degrades_to_passthrough(monkeypatch, caplog):
    """映射表缺失/读坏（部署漏传 tsv）降级为不归一，不把 hutuji_write 整条炸掉。"""
    from pathlib import Path

    from hutuji_mcp import t2s

    monkeypatch.setattr(t2s, "_TABLE_PATH", Path("不存在的目录/t2s_table.tsv"))
    monkeypatch.setattr(t2s, "_t2s", None)
    with caplog.at_level("WARNING", logger="hutuji_mcp.t2s"):
        assert t2s.to_simplified("劉蘭於個") == "劉蘭於個"  # 原文透传
    assert any("不做繁→简" in r.message for r in caplog.records)


def test_wide_latin_digit_advances_by_ink_edge():
    """墨宽超半 em 的宽拉丁字（如果梦「2」实测 0.69em）按墨右缘步进，不越槽撞下一字。"""
    font = gfont.GfontFile.parse(
        _build_v2_font(
            {
                "永": (_BOX_POINTS, _BOX_VERBS),
                "国": (_BOX_POINTS, _BOX_VERBS),
                "2": ([0.0, -80.0, 69.0, 0.0], [0, 1]),  # 墨右缘 69 > 半 em 50
                "9": ([0.0, -80.0, 40.0, 0.0], [0, 1]),
            }
        )
    )
    metrics = write_text.measure_font(font)
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0)
    page = write_text.layout_text("29", font, metrics, layout).pages[0]
    starts = sorted({round(min(px for px, _ in s.points), 3) for s in page.strokes})
    assert len(starts) == 2
    # 「2」步进 = 墨右缘 69 units × 0.1 = 6.9mm；半 em 口径只有 5mm（越槽 1.9mm）
    assert starts[1] - starts[0] == pytest.approx(6.9, abs=0.01)


def test_t2s_output_is_fully_simplified():
    """表内链式映射（薴→苧→苎）必须一次到底：输出再过一遍不得再变。"""
    from hutuji_mcp import t2s

    table = t2s._load()
    assert t2s.to_simplified("薴") == "苎"
    assert [(k, v) for k, v in table.items() if table.get(v, v) != v] == []


def test_hutuji_write_normalizes_traditional_input(tmp_path, monkeypatch):
    """繁体输入落到只收简体字形的字库仍不缺字：规范化在长度闸与排版之前生效。"""
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
                "文": (_BOX_POINTS, _BOX_VERBS),
                "，": (_COMMA_POINTS, _COMMA_VERBS),
            }
        )
    )
    out_dir = (tmp_path / "out").resolve()
    out_dir.mkdir()
    monkeypatch.setattr(server, "settings", Settings(
        llm_base_url="http://127.0.0.1:1", llm_api_key="", llm_model="", output_dir=out_dir,
        public_base_url="http://127.0.0.1:8300", host="127.0.0.1", port=8300,
        output_ttl_seconds=600, gfont_dir=str(font_dir),
    ))
    server._gfont_cache.clear()
    server._gfont_metrics_cache.clear()

    payload = json.loads(asyncio.run(server.hutuji_write("中國，中文")))
    assert payload["missing_chars"] == []
    gcode = (out_dir / payload["url"].rsplit("/", 1)[-1]).read_text(encoding="utf-8")
    validate_gcode(gcode)  # §5 契约不抛即过


def test_list_fonts_returns_stems_sorted(tmp_path):
    """字体选择器数据源：id=.gfont 文件名词（稳定契约）、name=动力巢系列
    显示名（登记在表用表值，未登记回落「动力巢科技·<词>」）、排序、非 gfont
    排除、坏目录空列表。"""
    (tmp_path / "blood8真迹_xiongzai_blood8.gfont").write_bytes(b"x")
    (tmp_path / "未登记体.gfont").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("nope", encoding="utf-8")
    fonts = write_text.list_fonts(str(tmp_path))
    assert fonts == [
        {"id": "blood8真迹_xiongzai_blood8", "name": "动力巢科技·真迹手写"},
        {"id": "未登记体", "name": "动力巢科技·未登记体"},
    ]
    assert write_text.list_fonts(str(tmp_path / "missing")) == []
    assert write_text.list_fonts("") == []


def test_fit_single_shrinks_multichar_to_one_page():
    """单页适配：12mm 排出 2 页的文本缩号凑成 1 页（2026-09-11 用户拍板）。"""
    font = _fixture_font()
    metrics = _fixture_metrics()
    layout = write_text.WriteLayout(size_mm=12.0)
    text = "中" * 200
    base = write_text.layout_text(text, font, metrics, layout)
    assert len(base.pages) == 2
    fit = write_text.layout_text_fit_single(text, font, metrics, layout)
    assert len(fit.pages) == 1
    assert fit.truncated is False
    assert fit.missing_chars == base.missing_chars


def test_fit_single_floor_falls_back_to_original_pages():
    """4mm 地板仍超页：回落原字号多页——不静默截断、不出不可读小字。"""
    font = _fixture_font()
    metrics = _fixture_metrics()
    layout = write_text.WriteLayout(size_mm=12.0)
    text = "中" * 2000
    base = write_text.layout_text(text, font, metrics, layout)
    assert len(base.pages) == 12
    fit = write_text.layout_text_fit_single(text, font, metrics, layout)
    assert len(fit.pages) == 12
    assert fit.truncated == base.truncated


def test_fit_single_passthrough_single_page_and_floor_size():
    """单页原文直通（不试排）；地板字号（≤4mm）原样返回不试缩。"""
    font = _fixture_font()
    metrics = _fixture_metrics()
    layout = write_text.WriteLayout(size_mm=12.0)
    base = write_text.layout_text("中" * 120, font, metrics, layout)
    assert len(base.pages) == 1
    fit = write_text.layout_text_fit_single("中" * 120, font, metrics, layout)
    assert len(fit.pages) == 1
    assert [p.strokes for p in fit.pages] == [p.strokes for p in base.pages]
    floor_layout = write_text.WriteLayout(size_mm=4.0)
    floor_base = write_text.layout_text("中" * 2000, font, metrics, floor_layout)
    assert len(floor_base.pages) == 2
    assert len(
        write_text.layout_text_fit_single("中" * 2000, font, metrics, floor_layout).pages
    ) == 2


def test_layout_handwriting_jitter_deterministic_and_default_off():
    """抖动契约：默认关=两次默认排版逐点全同（零随机态泄漏）；开=同输入逐点复现且必动墨。"""
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0, paragraph_indent=0)
    text = "永国中文字"
    plain_a = [write_text.page_to_svg(p) for p in write_text.layout_text(text, font, metrics, layout).pages]
    plain_b = [write_text.page_to_svg(p) for p in write_text.layout_text(text, font, metrics, layout).pages]
    assert plain_a == plain_b
    jit_a = [
        write_text.page_to_svg(p)
        for p in write_text.layout_text(text, font, metrics, layout, handwriting=True).pages
    ]
    jit_b = [
        write_text.page_to_svg(p)
        for p in write_text.layout_text(text, font, metrics, layout, handwriting=True).pages
    ]
    assert jit_a == jit_b  # 种子=字体 uuid+全文 → 逐点复现（SVG 内容缓存依赖）
    assert jit_a != plain_a  # 夹具各字形全等仿射才会恒等——幅度>0 必动墨
    assert len(jit_a) == len(plain_a) == 1


def test_layout_handwriting_jitter_bounded_near_original_ink():
    """抖动界：每个抖动点的原像都在默认版点集里，最近距离 ≤ 0.115em。

    夹具盒字最坏位移构成（em=size_mm）：0.5·√2·em×0.05（分轴缩放）
    + 0.5·√2·em×2·sin(2.5°/2)（旋转）+ 0.03·√2·em（平移）≈ 0.109em。
    """
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0, paragraph_indent=0)
    text = "永国中文字" * 30
    plain = write_text.layout_text(text, font, metrics, layout)
    jit = write_text.layout_text(text, font, metrics, layout, handwriting=True)
    em_mm = layout.size_mm
    bound = 0.115 * em_mm
    plain_pts = [p for page in plain.pages for s in page.strokes for p in s.points]
    moved = 0
    for page in jit.pages:
        for s in page.strokes:
            for x, y in s.points:
                nearest = min(abs(x - px) + abs(y - py) for px, py in plain_pts)
                assert nearest <= bound
                if nearest > 1e-9:
                    moved += 1
    assert moved > 0  # 不是恒等变换


def test_layout_handwriting_jitter_keeps_pagination_and_in_bounds():
    """抖动不改版式：分页/行数/missing 与默认版一致；全页墨仍在 190×277 页内（§5 前置自守）。"""
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout(size_mm=12.0)
    text = "永国中文，" * 300
    plain = write_text.layout_text(text, font, metrics, layout)
    jit = write_text.layout_text(text, font, metrics, layout, handwriting=True)
    assert len(jit.pages) == len(plain.pages)
    assert [p.line_count for p in jit.pages] == [p.line_count for p in plain.pages]
    assert jit.truncated == plain.truncated
    assert jit.missing_chars == plain.missing_chars == []
    for page in jit.pages:
        for s in page.strokes:
            for x, y in s.points:
                assert 0.0 <= x <= 190.0
                assert 0.0 <= y <= 277.0


def test_layout_handwriting_jitter_applies_to_stroke_order_path():
    """笔顺逐笔路同样吃抖动：stroke_data 在场时抖动仍生效、笔画数不变。"""
    from hutuji_mcp.stroke_order import StrokeData

    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0, paragraph_indent=0)
    medians = {
        "中": [
            [(512, 900), (512, 100)],
            [(200, 500), (800, 500)],
            [(200, 900), (200, 100)],
            [(800, 900), (800, 100)],
        ]
    }
    stroke = StrokeData(medians=medians)
    plain = write_text.layout_text("中", font, metrics, layout, stroke_data=stroke)
    jit = write_text.layout_text(
        "中", font, metrics, layout, stroke_data=stroke, handwriting=True
    )
    assert len(plain.pages[0].strokes) == len(jit.pages[0].strokes) == 4
    plain_pts = [p for s in plain.pages[0].strokes for p in s.points]
    jit_pts = [p for s in jit.pages[0].strokes for p in s.points]
    assert jit_pts != plain_pts


def test_layout_handwriting_jitter_passthrough_fit_single():
    """fit_single 透传 handwriting：单页直通版抖动生效且同输入逐点复现。"""
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout(size_mm=10.0)
    jit_a = write_text.layout_text_fit_single("永国中", font, metrics, layout, handwriting=True)
    jit_b = write_text.layout_text_fit_single("永国中", font, metrics, layout, handwriting=True)
    svg_a = [write_text.page_to_svg(p) for p in jit_a.pages]
    assert svg_a == [write_text.page_to_svg(p) for p in jit_b.pages]
    plain = write_text.layout_text_fit_single("永国中", font, metrics, layout)
    assert svg_a != [write_text.page_to_svg(p) for p in plain.pages]
