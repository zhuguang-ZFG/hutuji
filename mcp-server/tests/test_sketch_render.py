"""sketch_render 契约测试：排线/波纹渲染的几何不变量、fail-closed 与 §5 端到端。

钉的是消费方观察到的契约（折线形状、界内、风格语义、错误类型），不是实现细节；
参数常量（层阈/间距/行距）属调参面，除已实测钉死者外不在这里锁数值。
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET

import imageio.v3 as iio
import numpy as np
import pytest

from hutuji_mcp import sketch_render
from hutuji_mcp.sketch_render import (
    SketchRenderError,
    render_hatch,
    render_sketch_polylines,
    render_squiggle,
)


def _png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    iio.imwrite(buf, arr, extension=".png")
    return buf.getvalue()


def _gradient_png(w: int = 240, h: int = 160) -> bytes:
    """左黑右白水平渐变灰度图：两风格都必产出的最小确定性输入。"""
    ramp = np.linspace(0, 255, w, dtype=np.uint8)
    return _png(np.tile(ramp, (h, 1)))


def _half_black_png(w: int = 200, h: int = 120) -> bytes:
    arr = np.full((h, w), 255, dtype=np.uint8)
    arr[:, : w // 2] = 0
    return _png(arr)


@pytest.mark.parametrize("level", [1, 64, 128, 224])
def test_decode_gray_preserves_absolute_exposure(level):
    """暗照不能因没有白像素而被按本张最大值拉成纯白。"""
    gray = sketch_render._load_gray(_png(np.full((40, 60), level, dtype=np.uint8)))
    np.testing.assert_allclose(gray, level / 255.0, atol=1e-6)


def test_transparent_black_is_white_paper_not_ink():
    raw = _png(np.zeros((40, 60, 4), dtype=np.uint8))
    np.testing.assert_allclose(sketch_render._load_gray(raw), 1.0)


def test_equal_channel_average_does_not_hide_colored_features():
    """红绿亮度不同，不能因 RGB 均值相同而丢掉轮廓。"""
    arr = np.zeros((40, 60, 3), dtype=np.uint8)
    arr[:, :30, 0] = 255
    arr[:, 30:, 1] = 255
    gray = sketch_render._load_gray(_png(arr))
    assert gray[:, 30:].mean() > gray[:, :30].mean() + 0.4


def test_portrait_work_raster_has_bounded_height():
    gray = sketch_render._load_gray(_png(np.zeros((4000, 100), dtype=np.uint8)))
    assert gray.shape[0] <= 1280
    assert abs(gray.shape[1] / gray.shape[0] - 100 / 4000) < 0.002


def test_dark_hatching_has_no_nearly_overlapping_parallel_lines(monkeypatch):
    """实际暗区轨迹的同向线须留间距，避免不相关网格叠加造成局部黑块。"""
    monkeypatch.setattr(sketch_render, "_chain_hatch_segments", lambda segs, mask: segs)
    polys, w, h = render_hatch(_png(np.zeros((760, 500), dtype=np.uint8)))
    intercepts = []
    for p in polys:
        delta = p[-1] - p[0]
        if len(p) == 2 and np.linalg.norm(delta) > 100 and abs(delta[0] - delta[1]) < 1e-6:
            intercepts.append((p[0, 1] - p[0, 0]) / np.sqrt(2))
    gaps = np.diff(sorted(intercepts))
    assert len(gaps) > 30
    # 760px 长边对应 277mm 纸面时，3px≈1.1mm；旧网格可仅隔0.5px。
    assert gaps.min() >= 3.0 - 1e-6


def test_portrait_and_landscape_have_comparable_hatching_density(monkeypatch):
    monkeypatch.setattr(sketch_render, "_chain_hatch_segments", lambda segs, mask: segs)
    counts = []
    for shape in ((1280, 720), (720, 1280)):
        polys, _, _ = render_hatch(_png(np.zeros(shape, dtype=np.uint8)))
        counts.append(len(polys))
    assert abs(counts[0] - counts[1]) / max(counts) < 0.06


def test_hatch_segments_respect_tone_mask():
    """左黑右白图：排线（含串链连线）不得画进右半白区。
    2026-09-10 串链落地后改为栅格化口径（链式折线不再是 2 点段，
    段中点断言会悄悄变空话）：白区墨覆盖 <1%（边界容差含轮廓过渡）。"""
    polys, w, h = render_hatch(_half_black_png())
    assert polys, "排线产出不能为空"
    white = _raster_ink_fraction_region(polys, w, h, x0=w // 2 + 3)
    assert white < 0.01, f"排线越界进白区 ink={white:.1%}"


def test_hatch_respects_min_run_length(monkeypatch):
    # 串链后段被并成长链，min_run 是层生成期约束——以恒等串链观测段原文
    monkeypatch.setattr(sketch_render, "_chain_hatch_segments", lambda segs, mask: segs)
    polys, _, _ = render_hatch(_gradient_png())
    segs = [p for p in polys if len(p) == 2]
    lengths = [float(np.hypot(*(p[1] - p[0]))) for p in segs]
    assert min(lengths) >= sketch_render._HATCH_MIN_RUN_PX - sketch_render._HATCH_SAMPLE_STEP_PX * 2


def test_hatch_layers_stacked_by_darkness():
    """黑区墨覆盖必须显著高于深灰区：累积层语义（越暗排线越密）。

    中灰取值须落在归一化后的阈值之下：40/255≈0.157 经 p2/p98 拉伸（≈恒等）
    + gamma 0.7 → ≈0.36，吃第 1+2 层；黑色吃全部 4 层。普通中灰（如 128）
    归一化后 ≈0.60 在首层阈值之上、不排线——这是 2026-09-10 黑卡事故修复的
    核心语义，不许把阈值抬回中间调。串链落地后改栅格化口径（链式折线不再
    是 2 点段）。
    """
    arr = np.full((160, 240), 255, dtype=np.uint8)
    arr[:, : 80] = 0      # 黑
    arr[:, 80:160] = 40   # 深灰（归一化后 ≈0.36，第 1+2 层）
    polys, w, h = render_hatch(_png(arr))
    dark = _raster_ink_fraction_region(polys, w, h, x0=0, x1=78)
    mid = _raster_ink_fraction_region(polys, w, h, x0=82, x1=158)
    assert dark > mid * 1.2, f"累积层语义破坏 dark={dark:.1%} mid={mid:.1%}"
    assert mid > 0.005, f"深灰区排线消失 mid={mid:.1%}"


def _raster_ink_fraction_region(
    polys: list[np.ndarray], w: int, h: int, *, x0: int = 0, x1: int | None = None
) -> float:
    """指定列区间的墨覆盖率（1px 栅格化）。"""
    x1 = w if x1 is None else x1
    img = np.zeros((h, x1 - x0), dtype=bool)
    for p in polys:
        xy = np.round(p).astype(int)
        for (xa, ya), (xb, yb) in zip(xy[:-1], xy[1:]):
            n = int(max(abs(xb - xa), abs(yb - ya))) + 1
            xs = np.round(np.linspace(xa, xb, n)).astype(int)
            ys = np.round(np.linspace(ya, yb, n)).astype(int)
            ok = (xs >= x0) & (xs < x1) & (ys >= 0) & (ys < h)
            img[ys[ok], xs[ok] - x0] = True
    return float(img.mean())
def _raster_ink_fraction(polys: list[np.ndarray], w: int, h: int) -> float:
    """1px 全分辨率栅格化（无 AA）→ 真墨覆盖率；预览器粗线会污染判读。"""
    img = np.zeros((h, w), dtype=bool)
    for p in polys:
        xy = np.round(p).astype(int)
        for (x0, y0), (x1, y1) in zip(xy[:-1], xy[1:]):
            n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
            xs = np.round(np.linspace(x0, x1, n)).astype(int)
            ys = np.round(np.linspace(y0, y1, n)).astype(int)
            ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
            img[ys[ok], xs[ok]] = True
    return float(img.mean())


def test_mid_tone_photo_does_not_flood_page():
    """2026-09-10 生产黑卡事故回归：室内中间调照片（背景 217/主体 140/暗斑 30）
    不得整页铺排线。修复前裸亮度阈值 0.78 首层 ~90% 页面积叠层糊成实心；
    修复后 p2/p98 拉伸 + gamma 提亮，墨覆盖率必须 <25%（实测 ≈12%）。
    """
    arr = np.full((428, 760), 217, dtype=np.uint8)
    arr[80:360, 180:560] = 140   # 主体中间调块（逆光人脸量级）
    arr[120:200, 260:400] = 30   # 暗斑（头发/投影量级）
    polys, w, h = render_hatch(_png(arr))
    ink = _raster_ink_fraction(polys, w, h)
    assert ink < 0.25, f"中间调照片铺墨失控 ink={ink:.1%}"


def test_tone_normalization_keeps_subject_order_against_background():
    """全局明暗关系不被局部均衡打平，保留主体、阴影和背景层次。"""
    arr = np.full((180, 240), 220, dtype=np.uint8)
    arr[40:145, 70:170] = 125
    arr[75:115, 100:140] = 45
    normalized = sketch_render._normalize_tones(arr.astype(np.float32) / 255.0)
    background = float(normalized[10:30, 10:50].mean())
    subject = float(normalized[50:70, 80:95].mean())
    shadow = float(normalized[85:105, 110:130].mean())
    assert background > subject > shadow
    assert background - subject > 0.08


def test_structure_contours_ignore_smooth_lighting_gradient():
    """同一平面缓慢受光不能被切成几条等高线。"""
    gray = np.tile(np.linspace(0.2, 0.95, 240, dtype=np.float32), (160, 1))
    assert sketch_render._structure_contours(gray) == []


def test_structure_contours_keep_bright_thin_features():
    """亮调上的细镜框即使不跨固定亮度阈值，也必须保留两侧边缘。"""
    gray = np.full((160, 240), 0.8, dtype=np.float32)
    gray[30:130, 110:116] = 0.95
    polys = sketch_render._structure_contours(gray)
    length = sum(float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum()) for p in polys)
    assert length > 160, "亮调细边缘被固定亮度档漏掉"
    assert all(p[:, 0].min() > 100 and p[:, 0].max() < 126 for p in polys)



def test_structure_contours_hysteresis_bridges_weak_section():
    """同一垂直边缘中段对比度平滑变弱（0.008≤g<0.014）时不得断成两截——
    五官/下颌轮廓正是这类连续弱段；旧单阈会把中段齐根切掉。"""
    gray = np.full((120, 160), 0.9, dtype=np.float32)
    tent = 0.04 + 0.06 * np.abs(np.arange(120, dtype=np.float32) - 60) / 60
    gray[:, 80:] = 0.9 - tent[:, None]  # 两端 g≈0.027 强、中段 g≈0.011 弱
    polys = sketch_render._structure_contours(gray)
    vertical = [p for p in polys if p[:, 0].min() > 75 and p[:, 0].max() < 85]
    assert vertical, "垂直边缘整体丢失"
    span = max(float(p[:, 1].max() - p[:, 1].min()) for p in vertical)
    assert span > 100, f"弱段被单阈切断，最长连续段仅 {span:.1f}px"


def test_structure_contours_hysteresis_rejects_isolated_weak_edge():
    """全程低于强阈且无强锚点的边缘照旧拒收——缓慢光照/弱噪声不能搭滞回便车。"""
    gray = np.full((120, 160), 0.9, dtype=np.float32)
    gray[:, 80:] = 0.86  # 全程弱段 g≈0.011，无强锚点
    assert sketch_render._structure_contours(gray) == []

def test_structure_contours_reject_texture_without_erasing_object():
    """弱细纹背景不出碎线，实物的强轮廓仍须存在。"""
    y, x = np.mgrid[:180, :260]
    gray = (0.75 + 0.025 * np.sin(x * 2 * np.pi / 3) * np.sin(y * 2 * np.pi / 3)).astype(np.float32)
    gray[40:140, 80:180] = 0.25
    polys = sketch_render._structure_contours(gray)
    assert polys, "背景降噪时误删了主体轮廓"
    length = sum(float(np.linalg.norm(np.diff(p, axis=0), axis=1).sum()) for p in polys)
    assert length > 300
    for poly in polys:
        assert poly[:, 0].min() > 70 and poly[:, 0].max() < 190
        assert poly[:, 1].min() > 30 and poly[:, 1].max() < 150


def test_hatch_segment_budget_governor(monkeypatch):
    """段数预算闸：病态密集输入（半幅暗噪点，CLAHE 后必大面积触发排线）必须
    被层距放大压回预算内——2026-09-10 事故原图 CLAHE 后 5508 段 → G-code
    575KiB 超 §5 512KiB 闸整单 422。预算约束的是串链前的段生成期（闸在串链
    上游），故以恒等串链观测段原文：排线段数 ≤ 预算。"""
    monkeypatch.setattr(sketch_render, "_chain_hatch_segments", lambda segs, mask: segs)
    rng = np.random.default_rng(7)
    arr = np.full((428, 760), 230, dtype=np.uint8)
    arr[:, : 380] = (rng.random((428, 380)) * 120).astype(np.uint8)
    polys, _, _ = render_hatch(_png(arr))
    n_seg = sum(1 for p in polys if len(p) == 2)
    assert n_seg <= sketch_render._HATCH_SEGMENT_BUDGET, f"预算闸失效 n_seg={n_seg}"


def test_hatch_chaining_collapses_pen_lifts():
    """串链提速的用户可观察契约：大片暗区的排线应并成少数长链而非每段一笔。
    左半黑 200×120：串链前 ~百级独立段，串链后总折线数必须量级收敛。"""
    polys, w, h = render_hatch(_half_black_png())
    assert len(polys) < 40, f"串链失效，折线数={len(polys)}"


def test_hatch_reversed_segment_does_not_draw_across_white_notch():
    """反向接入后须从真实尾端续接，不能从上一段另一端校验白区。"""
    mask = np.ones((12, 18), dtype=bool)
    mask[4:7, 10:13] = False
    segs = [
        np.array([[2, 2], [2, 8]], dtype=np.float64),
        np.array([[8, 2], [8, 8]], dtype=np.float64),
        np.array([[14, 8], [14, 2]], dtype=np.float64),
    ]
    polys = sketch_render._chain_hatch_segments(segs, mask)
    from skimage.draw import line

    for poly in polys:
        for a, b in zip(poly[:-1], poly[1:]):
            (xa, ya), (xb, yb) = np.rint([a, b]).astype(int)
            rr, cc = line(ya, xa, yb, xb)
            assert mask[rr, cc].all(), "反向接入使连接线穿过白色缺口"


def test_hatch_link_checks_white_pixels_between_quarter_samples():
    """仅验连接线三个分点会漏掉窄白带；8px 的桥第 1px 即为反例。"""
    mask = np.ones((12, 14), dtype=bool)
    mask[8, 3] = False
    segs = [
        np.array([[2, 2], [2, 8]], dtype=np.float64),
        np.array([[10, 2], [10, 8]], dtype=np.float64),
    ]
    polys = sketch_render._chain_hatch_segments(segs, mask)
    assert len(polys) == 2, "排线跨过未检查到的白像素连成了一笔"


def test_hatch_final_paths_do_not_simplify_across_white_notch(monkeypatch):
    """串链已绕开 1px 留白，再对稀疏拐点抽稀会把它压成穿白区的直线。"""
    gray = np.zeros((32, 32), dtype=np.uint8)
    gray[2, 14] = 255
    segs = [
        np.array([[2, 2], [10, 2]], dtype=np.float64),
        np.array([[10, 3], [18, 3]], dtype=np.float64),
        np.array([[18, 2], [26, 2]], dtype=np.float64),
    ]
    monkeypatch.setattr(sketch_render, "_structure_contours", lambda gray: [])
    monkeypatch.setattr(sketch_render, "_normalize_tones", lambda gray, **kwargs: gray)
    monkeypatch.setattr(sketch_render, "_gaussian_for_mask", lambda gray: gray)
    monkeypatch.setattr(sketch_render, "_hatch_layer", lambda *args: segs)
    polys, _, _ = render_hatch(_png(gray))
    from skimage.draw import line

    for poly in polys:
        for a, b in zip(poly[:-1], poly[1:]):
            (xa, ya), (xb, yb) = np.rint([a, b]).astype(int)
            rr, cc = line(ya, xa, yb, xb)
            assert not np.any((rr == 2) & (cc == 14)), "最终抽稀重新切入白色缺口"


def _serpentine_polyline() -> np.ndarray:
    return np.array(
        [[20, 20], [180, 20], [180, 40], [20, 40], [20, 60], [180, 60], [180, 80], [20, 80]],
        dtype=np.float64,
    )


def test_programmatic_svg_preserves_turns_and_trace_default_still_smooths():
    """稀疏回折逐点透传；未选择程序化模式的既有描摹调用继续平滑。"""
    from hutuji_mcp.bitmap_svg import polylines_to_svg

    poly = _serpentine_polyline()
    def points(svg):
        node = next(n for n in ET.fromstring(svg).iter() if n.tag.endswith("polyline"))
        return np.array([list(map(float, xy.split(","))) for xy in node.attrib["points"].split()])
    np.testing.assert_array_equal(points(polylines_to_svg([poly], 200, 100, smooth=False)), poly)
    assert not np.array_equal(points(polylines_to_svg([poly], 200, 100)), poly)


def test_programmatic_preview_preserves_serpentine_white_gaps(monkeypatch):
    """把预览设成 1:1 栅格，四行蛇形应保持四行，不能被均值压成中间斜线。"""
    from hutuji_mcp import bitmap_svg

    padding = bitmap_svg._PREVIEW_PADDING
    monkeypatch.setattr(bitmap_svg, "_PREVIEW_MAX_WIDTH", 200 + 2 * padding)
    monkeypatch.setattr(bitmap_svg, "_PREVIEW_MAX_HEIGHT", 100 + 2 * padding)
    raw = sketch_render.render_sketch_preview([_serpentine_polyline()], 200, 100)
    actual = iio.imread(raw)[padding:-padding, padding:-padding]
    expected = np.full((100, 200), 255, dtype=np.uint8)
    expected[[20, 40, 60, 80], 20:181] = 0
    expected[20:41, 180] = 0
    expected[40:61, 20] = 0
    expected[60:81, 180] = 0
    np.testing.assert_array_equal(actual, expected)


def test_squiggle_amplitude_never_crosses_rows():
    """振幅 < 行距一半：任一波形点离其行基线不超过 max_amp，相邻行永不相撞。"""
    polys, w, h = render_squiggle(_gradient_png())
    waves = [p for p in polys if len(p) > 2]
    assert waves, "渐变图必须产出波形行"
    spacing = sketch_render._SQUIGGLE_ROW_SPACING_PX
    max_amp = sketch_render._SQUIGGLE_MAX_AMP_PX
    for p in waves:
        y_base = np.round(p[:, 1] / spacing) * spacing
        dev = np.abs(p[:, 1] - y_base).max()
        assert dev <= max_amp + spacing * 0.5 + 1.0


def test_squiggle_dark_image_few_long_polys():
    """全黑图：蛇形连接应把波形并成少数长折线（抬笔数远小于行数）。"""
    polys, w, h = render_squiggle(_png(np.zeros((200, 200), dtype=np.uint8)))
    rows = int(h / sketch_render._SQUIGGLE_ROW_SPACING_PX)
    waves = [p for p in polys if len(p) > 2]
    assert waves and len(waves) < rows / 2, f"蛇形连接失效：{len(waves)} 条 vs {rows} 行"


def test_squiggle_does_not_bridge_white_gaps_in_same_row():
    """两块黑区之间的 40px 白带必须抬笔；旧串链会在每行横穿白带。"""
    arr = np.zeros((120, 240), dtype=np.uint8)
    arr[:, 100:140] = 255
    polys, w, h = render_squiggle(_png(arr))
    white = _raster_ink_fraction_region(polys, w, h, x0=105, x1=135)
    assert white == 0, f"波纹把白区连成了横线：{white:.1%}"


def test_squiggle_adjacent_rows_do_not_join_through_white_notch(monkeypatch):
    """行尾与下行行首都暗，不代表两端之间也暗；回折线须检查整段。"""
    # 单测波纹连接，排除色调重映射和等值线描边对白色缺口边缘的影响。
    monkeypatch.setattr(sketch_render, "_normalize_tones", lambda gray: gray)
    monkeypatch.setattr(sketch_render, "_iso_contours", lambda gray: [])
    arr = np.zeros((12, 240), dtype=np.uint8)
    arr[4:6, -4:] = 255
    polys, w, h = render_squiggle(_png(arr))
    from skimage.draw import line

    ink = np.zeros((h, w), dtype=bool)
    for poly in polys:
        xy = np.rint(poly).astype(int)
        for (x0, y0), (x1, y1) in zip(xy[:-1], xy[1:]):
            rr, cc = line(y0, x0, y1, x1)
            inside = (rr >= 0) & (rr < h) & (cc >= 0) & (cc < w)
            ink[rr[inside], cc[inside]] = True
    assert not ink[4:6, -1].any(), "相邻行回折线跨过了白色缺口"


def test_pure_white_both_styles_fail_closed():
    white = _png(np.full((120, 160), 255, dtype=np.uint8))
    with pytest.raises(SketchRenderError):
        render_hatch(white)
    with pytest.raises(SketchRenderError):
        render_squiggle(white)


def test_undecodable_bytes_fail_closed():
    with pytest.raises(SketchRenderError, match="解码失败"):
        render_sketch_polylines(b"\x89PNG-not-really")


def test_unknown_style_fail_closed():
    with pytest.raises(SketchRenderError, match="未知风格"):
        render_sketch_polylines(_gradient_png(), style="crayon")


def test_deterministic_same_input_same_output():
    """程序化渲染的核心卖点：同输入必须逐点同输出（可复现、无噪声地板）。"""
    raw = _gradient_png()
    p1, _, _ = render_hatch(raw)
    p2, _, _ = render_hatch(raw)
    assert len(p1) == len(p2)
    for a, b in zip(p1, p2):
        np.testing.assert_array_equal(a, b)


def test_output_contract_matches_trace_shape():
    """与 trace_bitmap_to_polylines 同形状：(N,2) float64、y 向下、界内。"""
    polys, w, h = render_sketch_polylines(_gradient_png(), style="hatch")
    assert (w, h) == (240, 160)
    for p in polys:
        assert p.ndim == 2 and p.shape[1] == 2
        assert p.dtype == np.float64
        assert p[:, 0].min() >= -1 and p[:, 0].max() <= w
        assert p[:, 1].min() >= -1 and p[:, 1].max() <= h


def test_end_to_end_svg_gcode_passes_section5(tmp_path):
    """全真 vpype 端到端：渲染 → SVG 白名单 → G-code §5 校验。"""
    from hutuji_mcp.bitmap_svg import polylines_to_svg
    from hutuji_mcp.gcode_gen import svg_to_gcode, validate_gcode
    from hutuji_mcp.llm_svg import _validate_svg

    for style in ("hatch", "squiggle"):
        polys, w, h = render_sketch_polylines(_gradient_png(), style=style)
        svg = polylines_to_svg(polys, w, h, smooth=False)
        _validate_svg(svg)
        svg_path = tmp_path / f"{style}.svg"
        gcode_path = tmp_path / f"{style}.gcode"
        svg_path.write_text(svg, encoding="utf-8")
        svg_to_gcode(svg_path, gcode_path)
        validate_gcode(gcode_path.read_text(encoding="utf-8"), total_bytes=gcode_path.stat().st_size)
