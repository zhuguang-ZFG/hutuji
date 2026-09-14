"""上传/手绘 SVG 的真实预览必须与路径一致，收藏前已经落盘。"""
from io import BytesIO
import re

from PIL import Image, ImageChops
import pytest

from draw_portal.pipeline import process_upload_svg
from hutuji_mcp.paper_config import PaperConfig


def _page_ink_box(result, config):
    image = Image.open(BytesIO(result.preview_path.read_bytes())).convert("L")
    ink = image.point(lambda value: 255 if value < 230 else 0)
    box = ink.getbbox()
    assert box is not None
    from hutuji_mcp.bitmap_svg import _PREVIEW_PADDING

    pad = _PREVIEW_PADDING
    return tuple((value - pad) / (image.size[i % 2] - 2 * pad) for i, value in enumerate(box))


def _gcode_page_box(result, config):
    from hutuji_mcp.gcode_gen import validate_gcode

    raw = result.gcode_path.read_bytes()
    text = raw.decode("ascii")
    validate_gcode(text, total_bytes=len(raw), profile=config.to_profile())
    points = [(float(x), float(y)) for x, y in re.findall(r"X(-?[\d.]+)\s*Y(-?[\d.]+)", text)]
    if config.swap_xy:
        points = [(y, x) for x, y in points]
    xs, ys = zip(*points)
    return (min(xs) / config.page_width_mm, min(ys) / config.page_height_mm,
            max(xs) / config.page_width_mm, max(ys) / config.page_height_mm)


def test_svg_upload_materializes_path_preview(tmp_path):
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">
    <g transform="translate(15,20)"><path d="M0 0 L50 0 L50 40 L0 40 Z" fill="none" stroke="black"/></g></svg>'''
    result = process_upload_svg(svg, tmp_path)
    image = Image.open(BytesIO(result.preview_path.read_bytes())).convert("RGB")
    assert max(image.size) <= 2048
    ink = ImageChops.difference(image, Image.new("RGB", image.size, "white"))
    box = ink.getbbox()
    assert box is not None
    config = PaperConfig.default_for("paper")
    assert _page_ink_box(result, config) == pytest.approx(_gcode_page_box(result, config), abs=0.025)


@pytest.mark.parametrize("config", [
    PaperConfig.default_for("paper"), PaperConfig.default_for("nopaper"),
    PaperConfig("A2", 420, 594, True, 10, True, 400, 574),
])
@pytest.mark.parametrize("preserve_canvas", [False, True])
def test_upload_preview_and_real_gcode_share_page_layout(tmp_path, config, preserve_canvas):
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="380" height="554" viewBox="0 0 380 554">
    <path d="M20 30 L70 30 L70 60 L20 60 Z" fill="none" stroke="black"/></svg>'''
    result = process_upload_svg(svg, tmp_path, profile=config.to_profile(),
                                **({"preserve_canvas": True} if preserve_canvas else {}))
    preview_box = _page_ink_box(result, config)
    motion_box = _gcode_page_box(result, config)
    assert preview_box == pytest.approx(motion_box, abs=0.025)
    if preserve_canvas:
        # 留白属于构图：角落的小画不能被放成满页，也不能跳到纸张中心。
        assert motion_box[2] - motion_box[0] < 0.20
        assert motion_box[3] < 0.25
    else:
        assert motion_box[2] - motion_box[0] > 0.50
    assert not (tmp_path / "layout-preview.svg").exists()
