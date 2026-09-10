"""sketch_render 契约测试：排线/波纹渲染的几何不变量、fail-closed 与 §5 端到端。

钉的是消费方观察到的契约（折线形状、界内、风格语义、错误类型），不是实现细节；
参数常量（层阈/间距/行距）属调参面，除已实测钉死者外不在这里锁数值。
"""
from __future__ import annotations

import io

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


def test_hatch_segments_respect_tone_mask():
    """左黑右白图：45° 排线段中点必须落在左半（界差容差 2px）。"""
    polys, w, h = render_hatch(_half_black_png())
    segs = [p for p in polys if len(p) == 2]
    assert segs, "排线段不能为空"
    for seg in segs:
        mid_x = (seg[0, 0] + seg[1, 0]) / 2
        assert mid_x <= w // 2 + 2, f"排线越界进白区 mid_x={mid_x}"


def test_hatch_respects_min_run_length():
    polys, _, _ = render_hatch(_gradient_png())
    segs = [p for p in polys if len(p) == 2]
    lengths = [float(np.hypot(*(p[1] - p[0]))) for p in segs]
    assert min(lengths) >= sketch_render._HATCH_MIN_RUN_PX - sketch_render._HATCH_SAMPLE_STEP_PX * 2


def test_hatch_layers_stacked_by_darkness():
    """黑区段数必须严格多于深灰区：累积层语义（越暗排线越密）。

    中灰取值须落在归一化后的阈值之下：40/255≈0.157 经 p2/p98 拉伸（≈恒等）
    + gamma 0.6 → ≈0.33，吃第 1+2 层；黑色吃全部 4 层。普通中灰（如 128）
    归一化后 ≈0.66 在首层阈值之上、不排线——这是 2026-09-10 黑卡事故修复的
    核心语义，不许把阈值抬回中间调。
    """
    arr = np.full((160, 240), 255, dtype=np.uint8)
    arr[:, : 80] = 0      # 黑
    arr[:, 80:160] = 40   # 深灰（归一化后 ≈0.33，第 1+2 层）
    polys, _, _ = render_hatch(_png(arr))
    segs = [p for p in polys if len(p) == 2]
    dark = sum(1 for p in segs if (p[0, 0] + p[1, 0]) / 2 < 78)
    mid = sum(1 for p in segs if 82 < (p[0, 0] + p[1, 0]) / 2 < 158)
    assert dark > mid > 0

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
        svg = polylines_to_svg(polys, w, h)
        _validate_svg(svg)
        svg_path = tmp_path / f"{style}.svg"
        gcode_path = tmp_path / f"{style}.gcode"
        svg_path.write_text(svg, encoding="utf-8")
        svg_to_gcode(svg_path, gcode_path)
        validate_gcode(gcode_path.read_text(encoding="utf-8"), total_bytes=gcode_path.stat().st_size)
