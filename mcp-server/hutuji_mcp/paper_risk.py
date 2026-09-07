"""纸面风险闸：对 AI 位图链产出的 G-code 做画糊风险统计并拒收重画。

背景（2026-08-27 用户确认「纸面 OK」前的最后一刀）：AI 位图链偶发产出
「排线填色纹理 / 碎段虚线 / 涂鸦团」类线稿，预览缩略图看着像样、上纸即
画糊。本闸在 §5 `validate_gcode` 之后追加五个统计指标，任一越界即
`PaperRiskReject`，由 `_run_draw_job` 清理半成品后整轮重生图（最多 3 次）。

指标与阈值来源（08-26 废/好样本实测中位数，见
docs/superpowers/specs/2026-08-27-ai-bitmap-paper-quality-design.md §2.2）：

- `short_g1_ratio`：相邻落笔点距 <0.5mm 的 G1 段占比；废样本中位 0.67、
  好样本 0.05，阈值 0.35 与 curated gate v3 同口径。
- `pen_lifts`：抬笔次数；好样本中位 15、废样本 55，阈值 40 取中间带。
- `travel_gap_p50`：抬笔空走距离中位数（mm）；虚线/断段形态空走密集，
  废样本中位 4.85、好样本 2.0，阈值 2.0 按「好样本边缘即拒」收紧。
- `scribble_core_hits`：小包围盒内多点/短段密度异常高的小团数量（涂鸦
  团判据）；好样本 0，阈值 0 即任一命中即拒。
- `ink_span_mm`：墨迹跨度 max(ΔX, ΔY)；与 curated `MIN_INK_SPAN_MM` 同
  值 40——整幅缩成小团角标同样上纸不可认。

只读 G-code 文本做统计，不改 §5 契约、不碰 Grbl/xiaozhi、不改 vpype 容差。
QD / curated 命中路径不经本闸（库内样本质量自有入库门槛）。

2026-09-07 结构性复核（评审「行级闸」诉求的取证结论，勿再重推）：
本闸评估的是**塌缩后**文本——`gcode_gen._collapse_short_pen_down_segments`
（地板 1.0mm，2026-08-28 上线）保证每笔除末点 flush 外落笔段恒 ≥1.0mm，
故 <0.5mm 短段每笔至多 1 个：`short_g1_ratio` 封顶 ~1/N、`scribble_core`
的短段中点近乎绝迹，两项对现管线产物结构性惰性（保留无害，属纵深防御）；
活指标是 `pen_lifts` / `travel_gap_p50` / `ink_span_mm`。「局部画糊被全文件
平均稀释」类行级闸**在现管线无可抓信号**，已逐项证伪：①段长信号被塌缩
消灭（0.45mm×40 涂鸦笔塌成 13×1.35mm+0.45mm 尾，逐笔短段比 0.071，结构
上限 1/N，任何 ≥0.8 阈值不可达）；②最坏情形密集涂鸦球经位图链实心色块
转轮廓环 + 排线簇抑制后仅余 9 笔良性碎片（15mm 窗墨长 52.7mm）；③密度类
阈值无分离带——好侧 curated 现管线产物 15mm 窗墨长达 332.7mm（violin）、
逐笔长/跨比达 5.0（test_star 凹形轮廓固有 >1），被中和后的涂鸦残留
（~53mm）远在好侧分布之下，阈带为空；④细密纹理类（千级短笔）由活指标
`pen_lifts≤40` 兜住。证据脚本 `tmp/calib_prisk_0907.py`（不入库）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 阈值调整 = 行为变更：bump 版本号并在回归里用废/好样本重钉。
PAPER_RISK_GATE_VERSION = "2"

MAX_SHORT_G1_RATIO = 0.35  # 同 curated_gate（gate v3 口径）
MIN_INK_SPAN_MM = 40.0  # 同 curated_gate
MAX_PEN_LIFTS = 40  # 好样本中位 15 与废样本 55 之间的中间带
MAX_TRAVEL_GAP_P50_MM = 2.0  # 好样本中位 4.85→收紧到 2.0 拒虚线形态
MAX_SCRIBBLE_CORE_HITS = 0  # 任一涂鸦团即拒

# 涂鸦团判据（2026-08-27 v2 修正）：1mm 窗内 ≥45 个**短段**中点。窗口必须平移
# 不变——固定网格桶会让跨桶密团被劈半逃逸（v1 已证：45 段密团骑缝时两桶各
# ~22 双双低于阈值）。
SCRIBBLE_WIN_MM = 1.0
SCRIBBLE_SEG_MIN = 45
_SHORT_MM = 0.5  # 同 curated_gate 的短段定义

# hutuji_gwrite 产物紧凑无空格（`G1G90 Z5.0F10000`、`G0 X12.3Y4.5F8000`），
# 也兼容带空格形态。Z5 落笔 / Z0 抬笔，中点 2.5 判沿。
_Z_RE = re.compile(r"Z(-?\d+(?:\.\d+)?)")
_XY_RE = re.compile(
    r"X(-?\d+(?:\.\d+)?)[^;\n]*?Y(-?\d+(?:\.\d+)?)"
)


@dataclass(frozen=True)
class PaperRiskReport:
    """五项指标快照；reason 为 None 即 Pass。"""

    pen_lifts: int
    short_g1_ratio: float
    travel_gap_p50_mm: float
    ink_span_mm: float
    scribble_core_hits: int
    g1_xy_segments: int
    reason: str | None = None


class PaperRiskReject(Exception):
    """纸面风险闸拒收；`reason` 为稳定 token，供日志/journal 回收迭代。"""

    def __init__(self, reason: str, report: PaperRiskReport):
        super().__init__(reason)
        self.reason = reason
        self.report = report


def _parse_strokes(gcode: str):
    """按 Z 沿状态机把 G-code 拆成落笔点序列与抬笔空走距离序列。

    返回 (segments, gaps, pen_lifts, xs, ys)：segments = 落笔态相邻点距
    （mm）；gaps = 抬笔空走距离（首笔前不计）；pen_lifts = Z 落→抬沿次数。
    G0 只更新坐标（segment_first 的抬笔空走），G1 带 Z 的行走笔沿、带 XY
    的走画线/空走点。
    """
    pen_down = False
    pos: tuple[float, float] | None = None
    stroke_end: tuple[float, float] | None = None
    seen_first_stroke = False
    segments: list[float] = []
    gaps: list[float] = []
    pen_lifts = 0
    xs: list[float] = []
    ys: list[float] = []
    short_mids: list[tuple[float, float]] = []

    for raw in gcode.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("G0"):
            m = _XY_RE.search(line)
            if m:
                pos = (float(m.group(1)), float(m.group(2)))
            continue
        zm = _Z_RE.search(line)
        if zm:
            z = float(zm.group(1))
            if z > 2.5 and not pen_down:
                # 落笔沿（Z5）：上一笔终点到本笔起点的空走记一次 gap（首笔前不计）。
                if seen_first_stroke and stroke_end is not None and pos is not None:
                    gaps.append(
                        ((pos[0] - stroke_end[0]) ** 2 + (pos[1] - stroke_end[1]) ** 2) ** 0.5
                    )
                pen_down = True
            elif z <= 2.5 and pen_down:
                # 抬笔沿（Z0）。
                pen_down = False
                pen_lifts += 1
                stroke_end = pos
            continue
        m = _XY_RE.search(line)
        if not m:
            continue
        new = (float(m.group(1)), float(m.group(2)))
        if pen_down:
            if pos is not None:
                d = ((new[0] - pos[0]) ** 2 + (new[1] - pos[1]) ** 2) ** 0.5
                segments.append(d)
                if d < _SHORT_MM:
                    short_mids.append(((pos[0] + new[0]) / 2, (pos[1] + new[1]) / 2))
            xs.append(new[0])
            ys.append(new[1])
            seen_first_stroke = True
        pos = new
    return segments, gaps, pen_lifts, xs, ys, short_mids




def _count_scribble_cores(short_mids: list[tuple[float, float]]) -> int:
    """平移不变 1mm 窗内短段中点 ≥45 的涂鸦团计数。

    对每个短段中点，统计以其为中心的 1mm 盒内的短段中点数；任一 ≥45 即判
    涂鸦团。网格桶只做邻域加速（每点只查 3×3 桶），判据与桶对齐无关，跨缝
    密团无法逃逸。返回命中点数（报告面：≥1 即拒）。
    """
    if len(short_mids) < SCRIBBLE_SEG_MIN:
        return 0
    cell = SCRIBBLE_WIN_MM
    half = cell / 2.0
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for x, y in short_mids:
        buckets.setdefault((int(x // cell), int(y // cell)), []).append((x, y))
    hits = 0
    for x, y in short_mids:
        cx, cy = int(x // cell), int(y // cell)
        count = 0
        for nx in range(cx - 1, cx + 2):
            for ny in range(cy - 1, cy + 2):
                for ox, oy in buckets.get((nx, ny), ()):
                    if abs(ox - x) <= half and abs(oy - y) <= half:
                        count += 1
        if count >= SCRIBBLE_SEG_MIN:
            hits += 1
    return hits


def evaluate_gcode(gcode: str, *, pen_lifts: int) -> PaperRiskReport:
    """统计五项指标；`pen_lifts` 用 §5 `validate_gcode` 的权威值覆盖文本沿计数。"""
    segments, gaps, _text_lifts, xs, ys, short_mids = _parse_strokes(gcode)
    total = len(segments)
    short = sum(1 for d in segments if d < _SHORT_MM)
    short_ratio = (short / total) if total else 0.0
    gap_p50 = 0.0
    if gaps:
        ordered = sorted(gaps)
        mid = len(ordered) // 2
        gap_p50 = (
            ordered[mid]
            if len(ordered) % 2
            else (ordered[mid - 1] + ordered[mid]) / 2
        )
    ink_span = max(max(xs) - min(xs), max(ys) - min(ys)) if xs else 0.0
    return PaperRiskReport(
        pen_lifts=pen_lifts,
        short_g1_ratio=short_ratio,
        travel_gap_p50_mm=gap_p50,
        ink_span_mm=ink_span,
        scribble_core_hits=_count_scribble_cores(short_mids),
        g1_xy_segments=total,
    )


def enforce_gcode(gcode: str, *, pen_lifts: int) -> PaperRiskReport:
    """Pass 返回报告；Reject 抛 `PaperRiskReject`（reason 稳定 token）。

    判定顺序固定（reason token 稳定，journal 回收按 token 计数）：
    ink_span → short_g1_ratio → pen_lifts → travel_gap → scribble_core。
    """
    report = evaluate_gcode(gcode, pen_lifts=pen_lifts)
    reason = None
    if report.ink_span_mm < MIN_INK_SPAN_MM:
        reason = "ink_span"
    elif report.short_g1_ratio > MAX_SHORT_G1_RATIO:
        reason = "short_g1_ratio"
    elif report.pen_lifts > MAX_PEN_LIFTS:
        reason = "pen_lifts"
    elif report.travel_gap_p50_mm > MAX_TRAVEL_GAP_P50_MM:
        reason = "travel_gap"
    elif report.scribble_core_hits > MAX_SCRIBBLE_CORE_HITS:
        reason = "scribble_core"
    if reason is not None:
        raise PaperRiskReject(reason, report)
    return report
