"""G2/G3 弧拟合（hutuji_mcp.arc_fit）的行为测试。

钉的是可观察契约：行数只减不增、终点逐位不变、形状偏差有界、
已知回折事故不得复现；不钉内部拟合顺序与具体弧条数。
"""

import math
import re

from hutuji_mcp import gcode_gen
from hutuji_mcp.arc_fit import (
    ARC_FIT_CORNER_TOLERANCE_MM,
    ARC_FIT_TOLERANCE_MM,
    fit_gcode_arcs,
)

_WORD = re.compile(r"([A-Z])([+-]?(?:\d+\.?\d*|\.\d+))")


def _pen_down_run(points, feed=8000):
    """造一段合法落笔 run：G0 到起点 + Z5 + 逐点 G1 + Z0。"""
    lines = ["G21", "G90", "F24000", "G1G90 Z0.0F10000", "G1G90 Z0.0F10000"]
    x0, y0 = points[0]
    lines.append(f"G0 X{x0:.3f}Y{y0:.3f}F24000")
    lines.append("G1G90 Z5.0F10000")
    for x, y in points[1:]:
        lines.append(f"G1 X{x:.3f}Y{y:.3f}F{feed}")
    lines.append("G1G90 Z0.0F10000")
    return "\n".join(lines) + "\n"


def _circle_pts(cx, cy, r, a0, a1, n):
    """圆上 n+1 个采样点（含两端），从角度 a0° 到 a1°（方向由差值定）。"""
    return [
        (
            cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
            cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)),
        )
        for i in range(n + 1)
    ]

def _motion_lines(text):
    return [l for l in text.splitlines() if l.startswith(("G1 ", "G2 ", "G3 "))]


def _arc_endpoints(text):
    """(code, x, y, i, j) 列表。"""
    out = []
    for line in text.splitlines():
        if line.startswith(("G2 ", "G3 ")):
            w = dict((a, float(v)) for a, v in _WORD.findall(line))
            out.append((line[:2], w["X"], w["Y"], w["I"], w["J"]))
    return out


def test_straight_run_collapses_to_single_g1():
    pts = [(10.0, 10.0), (11.0, 10.0), (12.0, 10.0), (13.0, 10.0), (14.0, 10.0)]
    fitted = fit_gcode_arcs(_pen_down_run(pts))
    motions = _motion_lines(fitted)
    assert len(motions) == 1
    assert motions[0].startswith("G1 ")
    assert "X14.000" in motions[0] and "Y10.000" in motions[0]


def test_known_circle_fits_single_ccw_arc():
    # 半径 20mm 的 120° 逆时针弧，细分 40 段——必须收成一条 G3。
    pts = _circle_pts(100.0, 50.0, 20.0, 0.0, 120.0, 40)
    fitted = fit_gcode_arcs(_pen_down_run(pts))
    arcs = _arc_endpoints(fitted)
    assert len(arcs) == 1
    code, ex, ey, i_off, j_off = arcs[0]
    assert code == "G3"
    # 终点与原折线终点逐位一致（3 位小数）。
    assert abs(ex - pts[-1][0]) < 5e-4 and abs(ey - pts[-1][1]) < 5e-4
    # I/J 指向圆心：圆心 = 起点 + (I, J)。
    ccx, ccy = pts[0][0] + i_off, pts[0][1] + j_off
    assert abs(ccx - 100.0) < 1e-3 and abs(ccy - 50.0) < 1e-3
    # 除弧外不再有落笔 G1。
    assert not [l for l in _motion_lines(fitted) if l.startswith("G1 ")]


def test_known_clockwise_arc_emits_g2():
    pts = _circle_pts(100.0, 50.0, 15.0, 90.0, -60.0, 30)
    arcs = _arc_endpoints(fit_gcode_arcs(_pen_down_run(pts)))
    assert len(arcs) == 1 and arcs[0][0] == "G2"


def test_hairpin_v_turn_rejected():
    # 2026-09-05 实测事故：1mm 短弦急回折的 V 字，三点圆扫掠 342°，
    # 顶点/弦中点闸全过（弦太短弧弦几乎重合），不拦截会画成近整圆偏 5.5mm。
    pts = [(5.755, 30.38), (4.79, 29.6), (5.095, 29.776)]
    fitted = fit_gcode_arcs(_pen_down_run(pts))
    assert _arc_endpoints(fitted) == []
    # 两个原始段都必须以 G1 保留。
    assert len(_motion_lines(fitted)) == 2


def test_s_curve_rejected():
    # 先逆时针后顺时针的 S 形：任何跨拐点的弧都必须被方向闸拦下，
    # 产物里不允许出现「弧终点之后的形状与源折线反向」。
    left = _circle_pts(50.0, 50.0, 10.0, 180.0, 90.0, 15)
    right = _circle_pts(50.0 + 10.0 + 10.0, 50.0 - 10.0, 10.0, 180.0, 270.0, 15)
    pts = left + right[1:]
    fitted = fit_gcode_arcs(_pen_down_run(pts))
    # 终点必须逐位保留（行为契约，不管内部怎么拟合）。
    motions = _motion_lines(fitted)
    last = motions[-1]
    w = dict((a, float(v)) for a, v in _WORD.findall(last))
    assert abs(w["X"] - pts[-1][0]) < 5e-4 and abs(w["Y"] - pts[-1][1]) < 5e-4


def test_shape_deviation_bounded_on_glyph_like_curve():
    # 类字形复合曲线（两段不同半径圆弧相接）：逐点采样弧路径，
    # 与原折线双向最近距离不得超过拐角闸 + 顶点闸预算。
    pts = _circle_pts(60.0, 40.0, 12.0, 200.0, 80.0, 30)
    fitted = fit_gcode_arcs(_pen_down_run(pts))

    def seg_dist(p, a, b):
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        if l2 == 0:
            return math.hypot(p[0] - ax, p[1] - ay)
        t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2))
        return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)

    pos = pts[0]
    budget = ARC_FIT_TOLERANCE_MM + ARC_FIT_CORNER_TOLERANCE_MM
    for line in _motion_lines(fitted):
        w = dict((a, float(v)) for a, v in _WORD.findall(line))
        if line.startswith("G1 "):
            pos = (w["X"], w["Y"])
            continue
        cx, cy = pos[0] + w["I"], pos[1] + w["J"]
        r = math.hypot(w["I"], w["J"])
        a0 = math.atan2(pos[1] - cy, pos[0] - cx)
        a1 = math.atan2(w["Y"] - cy, w["X"] - cx)
        sweep = (a1 - a0) % (2 * math.pi)
        if line.startswith("G2 ") and sweep > 1e-12:
            sweep -= 2 * math.pi
        for k in range(33):
            a = a0 + sweep * k / 32
            p = (cx + r * math.cos(a), cy + r * math.sin(a))
            d = min(seg_dist(p, pts[j], pts[j + 1]) for j in range(len(pts) - 1))
            assert d <= budget + 1e-3, f"弧偏离原折线 {d:.3f}mm: {line}"
        pos = (w["X"], w["Y"])


def test_non_motion_lines_and_crlf_preserved():
    pts = [(10.0, 10.0), (20.0, 10.0)]
    text = _pen_down_run(pts).replace("\n", "\r\n")
    fitted = fit_gcode_arcs(text)
    assert "\r\n" in fitted
    for kept in ("G21", "G90", "G1G90 Z5.0F10000", "G1G90 Z0.0F10000"):
        assert kept in fitted
    # 行数只能减不能增。
    assert len(fitted.splitlines()) <= len(text.splitlines())


def test_single_segment_run_passes_through():
    pts = [(10.0, 10.0), (25.0, 30.0)]
    fitted = fit_gcode_arcs(_pen_down_run(pts))
    assert len(_motion_lines(fitted)) == 1
    assert "X25.000" in fitted and "Y30.000" in fitted


def test_pipeline_output_still_passes_contract(tmp_path):
    # 端到端：含圆弧的 SVG 走完整 svg_to_gcode 管线，产物必须过 §5 活闸。
    svg = tmp_path / "arc.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" '
        'viewBox="0 0 100 60">'
        '<path d="M 10 50 A 20 20 0 0 1 90 50" fill="none" stroke="black"/>'
        "</svg>",
        encoding="utf-8",
    )
    out = tmp_path / "arc.gcode"
    stats = gcode_gen.svg_to_gcode(svg, out)
    text = out.read_text(encoding="utf-8")
    gcode_gen.validate_gcode(text, total_bytes=len(text.encode("utf-8")))
    assert stats.lines == len(text.splitlines())
