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
    # 页宽放宽到 261 后，用右边距 95 锁回 174mm 测试行宽（点位构造与本测试机制无关）
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0, margin_right_mm=95.0)
    # 页宽 174mm，全宽步进 10mm → 首行缩进后放 15 字，次行 17 字
    result = write_text.layout_text("永" * 40, font, metrics, layout)
    assert result.pages[0].line_count >= 3
    xs = [x for s in result.pages[0].strokes for x, _ in s.points]
    assert max(xs) <= 190.0 - 8.0 + 1e-6  # 不越右边距


def test_layout_punctuation_never_starts_line():
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0, margin_right_mm=95.0)
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
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0, margin_right_mm=95.0)
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
    """避头并入不得越 §5 页限：连续闭标点在满行会把墨迹顶出 277mm 硬线（2026-09-04
    页宽放宽后），被 validate_gcode 整单拒绝（功能性失败，非外观）。并入前须判
    x_end≤_PAGE_WIDTH_MM，超限则避头让位、标点照常折行。
    构造（261mm 行宽、10mm 步进）：首行缩进后 24 字（260mm）满，次行 25 字（250mm）；
    。并入次行末（墨至 278>277）与 」并入（墨至 288>277）都越限 → 两字折到第三行。"""
    font = gfont.GfontFile.parse(
        _build_v2_font({ch: (_BOX_POINTS, _BOX_VERBS) for ch in "永国。」"})
    )
    metrics = write_text.measure_font(font)
    layout = write_text.WriteLayout(size_mm=10.0, char_gap_mm=0.0)
    result = write_text.layout_text("永" * 49 + "。" + "」", font, metrics, layout)
    page = result.pages[0]
    assert page.line_count == 3
    # 全页墨迹右沿不得越 277（§5 限幅真值）
    assert max(x for s in page.strokes for x, _ in s.points) <= 277.0
    # 。随避头折到第三行行首 X=8（并入会墨至 278 越限）
    line3 = [s for s in page.strokes if all(y > 34.0 for _, y in s.points)]
    assert line3
    assert min(x for x, _ in line3[0].points) == pytest.approx(8.0, abs=0.01)


def test_layout_paginates_and_reports_missing():
    font, metrics = _fixture_font(), _fixture_metrics()
    layout = write_text.WriteLayout(
        size_mm=20.0, line_spacing=1.5, margin_top_mm=150.0, margin_bottom_mm=0.0,
        margin_right_mm=95.0,  # 锁回 174mm 测试行宽，保住 6/8 字容量点位
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

    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            pushed.update(json["arguments"])
            return _Resp()

    monkeypatch.setattr(server.httpx, "AsyncClient", _Client)

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

    captured: dict[str, object] = {}

    class _Resp:
        status_code = 200

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
            return _Resp()

    monkeypatch.setattr(server.httpx, "AsyncClient", _Client)
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
    layout = write_text.WriteLayout(paragraph_indent=0, margin_right_mm=95.0)
    result = write_text.layout_text("A" * 60, font, metrics, layout)
    # 每行恰 25 字（夹具 A 步进 6.8mm，174/6.8=25）：旧行为 1 行越界 288mm；
    # guard 误放 overflow 之后曾退化成「每字一行」——行数必须精确钉 3
    assert result.pages[0].line_count == 3
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
    body_first_x = layout.margin_left_mm + 2 * (
        metrics.full_width * (layout.size_mm / metrics.em_height) + layout.char_gap_mm
    )
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
    # 18mm 全宽步进 18.8mm：261/18.8 = 13 字/行；20 字标题折 13+7 两行
    title = "中" * 20
    res = write_text.layout_text(title + "\n永国中文", font, metrics, layout, headings={0})
    page = res.pages[0]
    bands = _line_x_starts(page)
    center_x = layout.margin_left_mm + layout.text_width_mm / 2
    # 前两个行带都是标题行（13 字满行 + 7 字余行），均居中
    assert (bands[0][1] + bands[0][2]) / 2 == pytest.approx(center_x, abs=1.0)
    assert (bands[1][1] + bands[1][2]) / 2 == pytest.approx(center_x, abs=1.0)
    # 满行墨宽 ≈ 13*18.8-0.8=243.6 → 左沿≈8+8.7；余行 7*18.8-0.8=130.8 → 左沿≈8+65.1
    assert bands[1][1] - bands[0][1] == pytest.approx(65.1 - 8.7, abs=1.0)


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
