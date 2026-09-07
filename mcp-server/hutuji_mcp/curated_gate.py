"""Curated SVG ingest hard gate (design 2026-08-26)."""
from __future__ import annotations

import math
import re
from pathlib import Path

from .gcode_gen import GcodeStats, svg_to_gcode, validate_gcode
from .llm_svg import _validate_svg

# 2026-08-26 supersede（用户口径）：抬笔次数不是「画不成」判据，硬拒无产品意义。
# 入库硬闸只拦画不出/会卡死的产物：SVG 注入、vpype/§5、过碎短段、无墨迹、行数爆炸。
# gate_version=3：相对 v1(≤25)/v2(≤35) 取消抬笔硬上限。
# gate_version=4：_ink_checks 输入面改回塌缩前 vpype 原文（与 paper_risk
# 2.1 同根因——1.0mm 塌缩地板让终稿短段比结构封顶 ~1/N，0.35 硬闸停火）。
GATE_VERSION = "4"
MAX_G1_LINES = 800
MAX_SVG_BYTES = 256 * 1024
MAX_SHORT_G1_RATIO = 0.35
MIN_INK_SPAN_MM = 40.0
_SHORT_MM = 0.5

# hutuji gwrite often emits compact tokens; allow optional spaces.
_G1_RE = re.compile(
    r"G1(?![0-9])[^;\n]*?"
    r"(?:X(-?\d+(?:\.\d+)?)[^;\n]*?Y(-?\d+(?:\.\d+)?)"
    r"|Y(-?\d+(?:\.\d+)?)[^;\n]*?X(-?\d+(?:\.\d+)?))",
    re.I,
)


class GateReject(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _xy_moves(gcode: str) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for m in _G1_RE.finditer(gcode):
        if m.group(1) is not None:
            pts.append((float(m.group(1)), float(m.group(2))))
        else:
            pts.append((float(m.group(4)), float(m.group(3))))
    return pts


def _ink_checks(gcode: str) -> None:
    pts = _xy_moves(gcode)
    if len(pts) < 2:
        raise GateReject("ink: too few G1 XY points")
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    if span < MIN_INK_SPAN_MM:
        raise GateReject(f"ink: span {span:.1f}mm < {MIN_INK_SPAN_MM}")
    short = 0
    total = 0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        d = math.hypot(x1 - x0, y1 - y0)
        if d == 0:
            continue
        total += 1
        if d < _SHORT_MM:
            short += 1
    if total and (short / total) > MAX_SHORT_G1_RATIO:
        raise GateReject(f"short_g1_ratio: {short}/{total}")


def gate_svg(svg: str, *, work_dir: Path) -> GcodeStats:
    raw = svg.encode("utf-8")
    if len(raw) > MAX_SVG_BYTES:
        raise GateReject(f"svg_bytes: {len(raw)} > {MAX_SVG_BYTES}")
    try:
        _validate_svg(svg)
    except Exception as exc:  # noqa: BLE001 — gate reason surface
        raise GateReject(f"validate_svg: {exc}") from exc
    work_dir.mkdir(parents=True, exist_ok=True)
    svg_path = work_dir / "gate.svg"
    gcode_path = work_dir / "gate.gcode"
    svg_path.write_text(svg, encoding="utf-8")
    holder: list[str] = []
    try:
        svg_to_gcode(svg_path, gcode_path, on_raw_text=holder.append)
    except Exception as exc:  # noqa: BLE001
        raise GateReject(f"vpype: {exc}") from exc
    text = gcode_path.read_text(encoding="utf-8")
    try:
        stats = validate_gcode(text, total_bytes=len(text.encode("utf-8")))
    except Exception as exc:  # noqa: BLE001
        raise GateReject(f"validate_gcode: {exc}") from exc
    if stats.lines > MAX_G1_LINES:
        raise GateReject(f"lines: {stats.lines} > {MAX_G1_LINES}")
    # 过碎判定吃塌缩前原文（0.35 阈值的 08-26 标定面）；行数/§5 仍按终稿
    _ink_checks(holder[0] if holder else text)
    return stats
