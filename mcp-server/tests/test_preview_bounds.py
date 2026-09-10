"""预览资源边界：SVG 坐标可很大，栅格与逐段画线的分配必须有上限。"""
from __future__ import annotations

import imageio.v3 as iio
import numpy as np
import pytest
from skimage import draw

from hutuji_mcp import bitmap_svg, curated
from hutuji_mcp.llm_svg import _validate_svg


@pytest.mark.parametrize("width,height", [(20000, 20000), (1000000, 3), (3, 1000000)])
def test_large_svg_preview_has_bounded_raster(monkeypatch, width, height):
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">'
        f'<polyline points="0,0 {width - 1},{height - 1}" fill="none" stroke="black"/>'
        '</svg>'
    )
    _validate_svg(svg)
    real_full = np.full
    allocations = []

    def bounded_full(shape, *args, **kwargs):
        # 分配前断言：旧代码在这里红，不能为了复现真的申请 1.6 GB。
        assert max(shape) <= 2048, f"预览单边无界：{shape}"
        assert np.prod(shape) <= 2048**2, f"预览像素无界：{shape}"
        allocations.append(shape)
        return real_full(shape, *args, **kwargs)

    monkeypatch.setattr(np, "full", bounded_full)
    png = curated._preview_from_svg(svg)
    image = iio.imread(png)
    assert allocations
    assert image.shape[0] <= 220 and image.shape[1] <= 320
    # 一根 1px 线从 2048 降到 208 会呈浅灰；检查几何跨度，不要求强制加粗成黑线。
    rows, cols = np.nonzero(image < 250)
    assert len(rows), "缩放后仍须保留横跨画面的线"
    if height > 3:
        assert rows.max() - rows.min() > image.shape[0] / 2
    if width > 3:
        assert cols.max() - cols.min() > image.shape[1] / 2


@pytest.mark.parametrize(
    "points,visible",
    [
        ([[-1e9, 48], [1e9, 48]], True),
        ([[-1e9, -1e9], [1e9, 1e9]], True),
        ([[1e9, 48], [2e9, 48]], False),
        ([[-1e9, -10], [1e9, -10]], False),
    ],
)
def test_preview_clips_segments_before_line_allocation(monkeypatch, points, visible):
    real_line = draw.line

    def bounded_line(r0, c0, r1, c1):
        assert all(0 <= value < 96 for value in (r0, c0, r1, c1)), (
            "须先裁线再栅格化，事后过滤像素挡不住坐标数组巨额分配"
        )
        return real_line(r0, c0, r1, c1)

    monkeypatch.setattr(draw, "line", bounded_line)
    png = bitmap_svg.polylines_to_preview_png([np.array(points)], 96, 96)
    image = iio.imread(png)
    assert bool((image < 128).any()) is visible


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_preview_rejects_nonfinite_coordinates(monkeypatch, value):
    def forbidden_line(*args):
        pytest.fail("非有限坐标不得传入分配坐标数组的 line()")

    monkeypatch.setattr(draw, "line", forbidden_line)
    with pytest.raises(bitmap_svg.BitmapError):
        bitmap_svg.polylines_to_preview_png([np.array([[0, 0], [value, 10]])], 96, 96)


@pytest.mark.parametrize("width,height", [(0, 100), (100, -1), (float("inf"), 100)])
def test_preview_rejects_invalid_dimensions(width, height):
    with pytest.raises(bitmap_svg.BitmapError):
        bitmap_svg.polylines_to_preview_png([], width, height)


def test_normal_resolution_preview_preserves_ink_density():
    # 1024 像素源每 8 行一根 1px 线；缩略图应呈灰，不能恢复成密排线黑块。
    polys = [np.array([[0, y], [1023, y]], dtype=float) for y in range(0, 1024, 8)]
    image = iio.imread(bitmap_svg.polylines_to_preview_png(polys, 1024, 1024))
    assert 170 < image[20:-20, 20:-20].mean() < 220
