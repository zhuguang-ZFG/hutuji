"""落笔态最短 G1 地板：压住 $1=25 空闲失能窗口。

根因（2026-08-28）：本机 $1=25 + 弹簧笔架。planner 段缓冲抽空后 st_go_idle →
超时失能 → 笔弹起，逻辑 Z 仍可为 5。AI 描摹易出「Z5 → 亚毫米 G1 链 → 很快停」；
缓存简笔画段长撑得住 planner，故同 Z 字面量后者正常。不改 $1/Z5 契约，只把
落笔态连续短 G1 塌到 ≥ MIN_PEN_DOWN_G1_MM。
"""
from __future__ import annotations

import math

from hutuji_mcp import gcode_gen
from hutuji_mcp.gcode_gen import validate_gcode


def _stroke_with_micro_g1() -> str:
    """Z5 后一串 0.1mm 级 G1（F8000 下每段 ~0.75ms），模拟阶梯/碎描摹。"""
    parts = [
        "G21",
        "G90",
        "F8000",
        "G1G90 Z0.0F10000",
        "G1G90 Z0.0F10000",
        "G0 X10.000Y10.000F8000",
        "G1G90 Z5.0F10000",
    ]
    x = 10.0
    for _ in range(20):
        x += 0.1
        parts.append(f"G1 X{x:.3f}Y10.000F8000")
    parts.append("G1G90 Z0.0F10000")
    return "\n".join(parts) + "\n"


def _pen_down_xy_lengths(text: str) -> list[float]:
    """提取落笔态（Z≈5 之后、Z0 之前）相邻 G1 XY 段长。"""
    cur_x = cur_y = None
    pen_down = False
    lengths: list[float] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        upper = line.upper().replace(" ", "")
        if upper.startswith("G1G90Z5") or upper.startswith("G1Z5"):
            pen_down = True
            continue
        if upper.startswith("G1G90Z0") or upper.startswith("G1Z0"):
            pen_down = False
            continue
        if not pen_down or not upper.startswith("G1"):
            if upper.startswith("G0"):
                words = gcode_gen._WORD_RE.findall(upper)
                for letter, number in words:
                    if letter == "X":
                        cur_x = float(number)
                    elif letter == "Y":
                        cur_y = float(number)
            continue
        words = gcode_gen._WORD_RE.findall(upper)
        nx, ny = cur_x, cur_y
        for letter, number in words:
            if letter == "X":
                nx = float(number)
            elif letter == "Y":
                ny = float(number)
        if cur_x is not None and cur_y is not None and nx is not None and ny is not None:
            lengths.append(math.hypot(nx - cur_x, ny - cur_y))
        cur_x, cur_y = nx, ny
    return lengths


def test_collapse_short_pen_down_g1_raises_floor() -> None:
    """亚毫米落笔 G1 必须塌成 ≥ MIN_PEN_DOWN_G1_MM（末点保留）。"""
    src = _stroke_with_micro_g1()
    before = _pen_down_xy_lengths(src)
    assert before, "夹具必须含落笔 G1"
    assert max(before) < gcode_gen.MIN_PEN_DOWN_G1_MM

    out = gcode_gen._collapse_short_pen_down_segments(src)
    after = _pen_down_xy_lengths(out)
    assert after, "塌缩后仍须有落笔运动"
    assert all(d + 1e-9 >= gcode_gen.MIN_PEN_DOWN_G1_MM for d in after[:-1])
    # 末段可短于地板（冲到笔画终点），但整笔画总长不变
    assert abs(sum(after) - sum(before)) < 1e-6
    validate_gcode(out)


def test_collapse_preserves_long_segments() -> None:
    """已够长的落笔段不得被无故拆短或改坐标。"""
    text = (
        "G21\nG90\nF8000\n"
        "G1G90 Z0.0F10000\nG1G90 Z0.0F10000\n"
        "G0 X10.000Y10.000F8000\n"
        "G1G90 Z5.0F10000\n"
        "G1 X50.000Y10.000F8000\n"
        "G1 X50.000Y50.000F8000\n"
        "G1G90 Z0.0F10000\n"
    )
    out = gcode_gen._collapse_short_pen_down_segments(text)
    assert out == text


def test_collapse_does_not_bridge_pen_up() -> None:
    """抬笔态 G0 / 两笔之间不得被合并成一条落笔线。"""
    text = (
        "G21\nG90\nF8000\n"
        "G1G90 Z0.0F10000\nG1G90 Z0.0F10000\n"
        "G0 X10.000Y10.000F8000\n"
        "G1G90 Z5.0F10000\n"
        "G1 X10.100Y10.000F8000\n"
        "G1G90 Z0.0F10000\n"
        "G0 X20.000Y10.000F8000\n"
        "G1G90 Z5.0F10000\n"
        "G1 X20.100Y10.000F8000\n"
        "G1G90 Z0.0F10000\n"
    )
    out = gcode_gen._collapse_short_pen_down_segments(text)
    assert out.count("G1G90 Z5.0F10000") == 2
    assert "G0 X20.000Y10.000F8000" in out
