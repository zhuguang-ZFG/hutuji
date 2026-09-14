"""整页按机器范围校验，动态纸张必须贯穿真实 vpype 与排版。"""
from dataclasses import replace

import pytest

from hutuji_mcp.paper_config import PaperConfig, PaperConfigError


def test_defaults_preserve_both_machine_envelopes():
    paper = PaperConfig.default_for("paper")
    plain = PaperConfig.default_for("nopaper")
    assert (paper.page_width_mm, paper.page_height_mm) == (190, 277)
    assert (paper.swap_xy, paper.max_x_mm, paper.max_y_mm) == (True, 277, 190)
    assert (plain.swap_xy, plain.max_x_mm, plain.max_y_mm) == (False, 205, 290)
    assert PaperConfig.from_dict(plain.to_dict()) == plain
    assert plain.marker == "p1:A4:2100:2970:0:100:0:2050:2900"


@pytest.mark.parametrize("changes", [
    {"paper": "A3", "width_mm": 297, "height_mm": 420},
    {"swap_xy": False}, {"landscape": True}, {"max_x_mm": 276.9},
    {"max_y_mm": float("nan")}, {"max_x_mm": float("inf")},
    {"width_mm": True}, {"margin_mm": 10.01}, {"landscape": 1},
    {"paper": "A2"}, {"paper": "a4"}, {"margin_mm": -1},
])
def test_invalid_or_unfitted_paper_is_rejected(changes):
    with pytest.raises(PaperConfigError):
        replace(PaperConfig(), **changes)


def test_large_paper_requires_actual_large_range_and_keeps_orientation_order():
    value = PaperConfig("A2", 420, 594, True, 10, True, 400, 574)
    assert (value.page_width_mm, value.page_height_mm) == (574, 400)
    assert value.requires_range_confirmation(PaperConfig())
    assert not PaperConfig().requires_range_confirmation(value)
    custom = PaperConfig("custom", 333.3, 444.4, False, 10.1, False, 313.1, 424.2)
    assert (custom.page_width_mm, custom.page_height_mm) == (313.1, 424.2)


def test_dynamic_page_reaches_real_vpype_and_final_bounds(tmp_path):
    from hutuji_mcp.gcode_gen import GcodeError, svg_to_gcode, validate_gcode

    config = PaperConfig("A2", 420, 594, False, 10, False, 400, 574)
    svg = tmp_path / "source.svg"
    gcode = tmp_path / "out.gcode"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><path d="M10 10 L90 90" fill="none" stroke="black"/></svg>', encoding="ascii")
    profile = config.to_profile()
    stats = svg_to_gcode(svg, gcode, profile=profile)
    text = gcode.read_text(encoding="ascii")
    assert stats.lines > 0
    validate_gcode(text, total_bytes=gcode.stat().st_size, profile=profile)
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(text)
    assert "X395.000" in text or "Y482.000" in text
