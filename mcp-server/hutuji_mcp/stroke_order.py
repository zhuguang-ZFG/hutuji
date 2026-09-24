"""标准笔顺教学排版：hanzi-writer-data medians（PRC 规范笔顺）→ 田字格渐显面板页。

数据源：`HUTUJI_STROKE_DIR/strokes.jsonl`（`scripts/fetch_stroke_data.py` 抓取
hanzi-writer-data@2.0.1 / Make Me A Hanzi 生成；Arphic Public License，商用可，
ARPHICPL.TXT 随数据目录分发）。与奎享 .gfont 字库笔画的关键差别：gfont 是写字机
连笔口径（中=2 笔），medians 是教学标准口径（中=4 笔、永=5 笔，逐笔有序）。

坐标系：源数据 1024×1024、**y 向上为正**（永字第 1 笔「点」y≈824 在顶部实证），
本模块翻转成页面 y 向下。每个字按全部笔画的联合包围盒等比缩放居中进格子，
全字所有面板共用同一变换（渐显不跳动）。

页面版式（190×277 竖版，与 §5 限幅同源）：
- 单字：首行大字（成形 30mm 田字格）+ 渐显面板（第 k 格 = 前 k 笔 + 格右下角
  中文步序数字）；放不下的面板续页（续页无大字头）。教学可读性优先，单字保持渐显。
- 多字（用户 2026-09-12 拍板「分解行+成形格」，supersede 09-11「集中到单张纸」成形字帖）：
  每字一行——第 k 格 = 前 k 笔渐显（右下角步序数字），行尾成形格 = 全字（无序号，临摹用）；
  整组统一格宽（24mm 上限 / 16mm 地板，按最宽字需要），一行装不下折行，页高放不下才续页。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .write_text import LaidOutPage, LayoutResult, _PenStroke

_GRID_UNITS = 1024.0  # 源数据坐标幅面

# 版式常量（mm，190×277 竖版）
_MARGIN_MM = 8.0
_HEADER_CELL_MM = 30.0  # 首行大字格
_HEADER_STRIP_MM = 36.0  # 大字格 + 下方余白
_PANEL_GAP_MM = 3.0
_PANEL_CELL_MM = 34.0  # 渐显格子固定尺寸（再大纸张利用率低，再小笔画挤）
_PANEL_COLS_MAX = 5  # 竖版页宽 190：5 格×34 + 4 间隙×3 = 182 ≤ 190
_LABEL_SIZE_MM = 6.0  # 渐显面板步序数字尺寸
_ROW_CELL_MAX_MM = 24.0  # 混合行格上限：与字帖上限同口径的教学可读性上限
_ROW_CELL_MIN_MM = 16.0  # 混合行格地板：再小逐笔渐显挤成一团（2026-09-12 混合版式）

_PAGE_W_MM = 190.0
_PAGE_H_MM = 277.0


class StrokeOrderError(Exception):
    """笔顺教学失败（数据未配置/字缺/页面上限）。"""


@dataclass
class StrokeData:
    """字 → 逐笔中心线（1024 网格、y 向上）。"""

    medians: dict[str, list[list[tuple[int, int]]]]

    @classmethod
    def load(cls, stroke_dir: str) -> "StrokeData":
        path = Path(stroke_dir) / "strokes.jsonl"
        if not path.is_file():
            raise StrokeOrderError(f"笔顺数据缺失：{path}")
        out: dict[str, list[list[tuple[int, int]]]] = {}
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\r\n")  # 兼容 Windows 本机手改/CRLF 数据
                if not line:
                    continue
                ch, payload = line.split("\t", 1)
                out[ch] = [
                    [(int(x), int(y)) for x, y in stroke]
                    for stroke in json.loads(payload)
                ]
        if not out:
            raise StrokeOrderError("笔顺数据为空")
        return cls(medians=out)


def _cjk_step_label(n: int) -> str:
    """1..99 中文小写（与 doc_ingest._cjk_counter 同口径的简化版：只到 99）。"""
    digits = "零一二三四五六七八九"
    if n <= 10:
        return "十" if n == 10 else digits[n]
    if n < 20:
        return "十" + digits[n % 10]
    return digits[n // 10] + "十" + (digits[n % 10] if n % 10 else "")


def _char_bbox(strokes: list[list[tuple[int, int]]]) -> tuple[int, int, int, int]:
    xs = [x for s in strokes for x, _ in s]
    ys = [y for s in strokes for _, y in s]
    return min(xs), min(ys), max(xs), max(ys)

# 笔画平滑（2026-09-24 美观批）：medians 是稀疏控制点（横仅 2 点），直连是机械折线；
# centripetal Catmull-Rom 过原控制点补间出流畅曲线，两段夹角 >45° 的折点（横折类复合笔）
# 锚定不圆滑。新增细分点近似共线，下游 vpype linesimplify(0.05mm) 会再抽稀，不破 §5 上限。
_SMOOTH_SUBDIV = 6  # 每跨细分段数
_CORNER_COS = math.cos(math.radians(45))  # 折点判据：邻段夹角余弦 < cos45°


def _cr_centripetal(
    p0: tuple[float, float], p1: tuple[float, float],
    p2: tuple[float, float], p3: tuple[float, float], t: float,
) -> tuple[float, float]:
    """centripetal Catmull-Rom 单点补间（alpha=0.5，不等距点串不打环不尖点）。"""
    def _tj(ti: float, pa: tuple[float, float], pb: tuple[float, float]) -> float:
        return ti + math.hypot(pb[0] - pa[0], pb[1] - pa[1]) ** 0.5
    t0 = 0.0
    t1 = _tj(t0, p0, p1)
    t2 = _tj(t1, p1, p2)
    t3 = _tj(t2, p2, p3)
    if t1 == t0 or t2 == t1 or t3 == t2:
        return p1  # 重复点退化：原样返回锚点
    tt = t1 + (t2 - t1) * t

    def _lerp(pa, pb, ta, tb):
        if tb == ta:
            return pa
        w = (tt - ta) / (tb - ta)
        return (pa[0] + (pb[0] - pa[0]) * w, pa[1] + (pb[1] - pa[1]) * w)

    a1 = _lerp(p0, p1, t0, t1)
    a2 = _lerp(p1, p2, t1, t2)
    a3 = _lerp(p2, p3, t2, t3)
    b1 = _lerp(a1, a2, t0, t2)
    b2 = _lerp(a2, a3, t1, t3)
    return _lerp(b1, b2, t1, t2)


def _smooth_median(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """单笔 median 折线 → 平滑折线；端点与折点原位保留（渐显面板逐帧对齐不破）。"""
    if len(pts) <= 2:
        return pts
    corners: list[int] = []
    for i in range(1, len(pts) - 1):
        ax, ay = pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]
        bx, by = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if la == 0 or lb == 0:
            continue
        if (ax * bx + ay * by) / (la * lb) < _CORNER_COS:
            corners.append(i)
    runs: list[list[tuple[float, float]]] = []
    prev = 0
    for c in corners:
        runs.append(pts[prev:c + 1])
        prev = c
    runs.append(pts[prev:])
    out: list[tuple[float, float]] = []
    for run in runs:
        if len(run) >= 3:
            ext = [run[0]] + run + [run[-1]]
            sm = [
                _cr_centripetal(ext[i], ext[i + 1], ext[i + 2], ext[i + 3], j / _SMOOTH_SUBDIV)
                for i in range(len(ext) - 3)
                for j in range(_SMOOTH_SUBDIV)
            ]
            sm.append(run[-1])
        else:
            sm = run
        out.extend(sm if not out else sm[1:])  # 相邻段共享折点只收一次
    return out


def _emit_char(
    page: LaidOutPage,
    strokes: list[list[tuple[int, int]]],
    cell_x: float,
    cell_y: float,
    cell_mm: float,
    bbox: tuple[int, int, int, int],
    pad_mm: float = 2.0,
) -> None:
    """把（部分）笔画按统一 bbox 变换画进格子；源 y 向上 → 页 y 向下翻转。"""
    x0, y0, x1, y1 = bbox
    span = max(x1 - x0, y1 - y0, 1)
    scale = (cell_mm - 2 * pad_mm) / span
    ox = cell_x + pad_mm + (cell_mm - 2 * pad_mm - (x1 - x0) * scale) / 2
    oy = cell_y + pad_mm + (cell_mm - 2 * pad_mm - (y1 - y0) * scale) / 2
    for stroke in strokes:
        pts = [(ox + (x - x0) * scale, oy + (y1 - y) * scale) for x, y in stroke]
        if len(pts) >= 2:
            page.strokes.append(_PenStroke(points=_smooth_median(pts)))


def _emit_grid(page: LaidOutPage, cell_x: float, cell_y: float, cell_mm: float) -> None:
    """田字格：外框实线 + 中十字虚线（4mm 线 / 2mm 空）。

    2026-09-24 美观批曾加双对角虚线升级米字格，当日实机预览即被用户判「看不清楚」
    回退：512px 页预览下单格约 40px，对角+十字 8 组虚线糊成星爆黑团淹没字形；
    且对角使每格虚线段翻倍、抬笔次数近翻倍（「永」209 次），实画时长显著变长。
    美观收益全部来自笔画平滑，格线保持田字格，对角勿再加回。"""
    x2, y2 = cell_x + cell_mm, cell_y + cell_mm
    page.strokes.append(_PenStroke(points=[
        (cell_x, cell_y), (x2, cell_y), (x2, y2), (cell_x, y2), (cell_x, cell_y),
    ]))
    mid_x, mid_y = cell_x + cell_mm / 2, cell_y + cell_mm / 2

    def dashed(a: tuple[float, float], b: tuple[float, float]) -> None:
        (ax, ay), (bx, by) = a, b
        length = math.hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / length, (by - ay) / length
        t = 0.0
        while t < length:
            seg = min(t + 4.0, length)
            page.strokes.append(_PenStroke(points=[
                (ax + ux * t, ay + uy * t), (ax + ux * seg, ay + uy * seg),
            ]))
            t = seg + 2.0

    dashed((cell_x, mid_y), (x2, mid_y))
    dashed((mid_x, cell_y), (mid_x, y2))


def _emit_step_label(
    page: LaidOutPage,
    data: StrokeData,
    k: int,
    cell_x: float,
    cell_y: float,
    cell_mm: float,
) -> None:
    """格右下角中文步序数字（同数据源 median，缺数字字形则静默略过）。"""
    label = _cjk_step_label(k)
    digit_strokes = [data.medians[d] for d in label if d in data.medians]
    if not digit_strokes:
        return
    total_w = _LABEL_SIZE_MM * len(digit_strokes)
    lx = cell_x + cell_mm - total_w - 1.0
    ly = cell_y + cell_mm - _LABEL_SIZE_MM - 1.0
    for i, ds in enumerate(digit_strokes):
        _emit_char(
            page, ds, lx + i * _LABEL_SIZE_MM, ly, _LABEL_SIZE_MM,
            _char_bbox(ds), pad_mm=0.4,
        )


def _center_page_vertically(page: LaidOutPage) -> None:
    """整页内容竖向居中于页区（教学页内容常只占上半页，贴顶难看）。"""
    ys = [y for s in page.strokes for _, y in s.points]
    if not ys:
        return
    lo, hi = min(ys), max(ys)
    shift = (page.page_height_mm - (hi - lo)) / 2 - lo
    shift = max(0.0, shift)  # 内容超高页区时不动作（防下移出界）
    if shift:
        for s in page.strokes:
            s.points = [(x, y + shift) for x, y in s.points]


def _layout_mixed_rows(
    targets: list[str],
    data: StrokeData,
    missing: list[str],
    max_pages: int,
    page_width_mm: float = _PAGE_W_MM,
    page_height_mm: float = _PAGE_H_MM,
) -> LayoutResult:
    """多字混合版式（2026-09-12 用户拍板「分解行+成形格」，supersede 09-11 成形字帖）：
    每字一行——第 k 格 = 前 k 笔渐显（右下角步序数字），行尾成形格 = 全字（无序号，
    临摹用）。整组统一格宽 = min(各字 (页宽-间隙×n)/(n+1), 24 上限)，16mm 地板；
    一行装不下（n+1 > per_row）的字折行续排；页高放不下才续页（续页无大字头）。
    max_pages 与单字路径同口径。
    """
    gap = _PANEL_GAP_MM
    usable_w = page_width_mm - 2 * _MARGIN_MM
    usable_h = page_height_mm - 2 * _MARGIN_MM
    # 字帖感要求各字格子同大：整组取最宽字需要的格宽，钳到上下限
    cell = min(
        min((usable_w - len(data.medians[ch]) * gap) / (len(data.medians[ch]) + 1)
            for ch in targets),
        _ROW_CELL_MAX_MM,
    )
    if cell < _ROW_CELL_MIN_MM:
        cell = _ROW_CELL_MIN_MM
    per_row = max(1, int((usable_w + gap) // (cell + gap)))

    pages: list[LaidOutPage] = []
    truncated = False
    page = LaidOutPage(page_width_mm=page_width_mm, page_height_mm=page_height_mm)
    y = _MARGIN_MM

    def flush() -> None:
        nonlocal page, y
        if page.strokes:
            _center_page_vertically(page)
            pages.append(page)
            page = LaidOutPage(page_width_mm=page_width_mm, page_height_mm=page_height_mm)
            y = _MARGIN_MM

    for ch in targets:
        strokes = data.medians[ch]
        n = len(strokes)
        bbox = _char_bbox(strokes)
        seq = list(range(1, n + 1)) + [0]  # 0 = 成形格（全字、无步序数字）
        for start in range(0, len(seq), per_row):
            row = seq[start:start + per_row]
            if y + cell > page_height_mm - _MARGIN_MM:
                flush()
                if len(pages) >= max_pages:
                    truncated = True
                    return LayoutResult(pages=pages, missing_chars=missing, truncated=truncated)
            row_w = len(row) * cell + (len(row) - 1) * gap
            x = (page_width_mm - row_w) / 2
            for k in row:
                _emit_grid(page, x, y, cell)
                if k:
                    _emit_char(page, strokes[:k], x, y, cell, bbox)
                    _emit_step_label(page, data, k, x, y, cell)
                else:
                    _emit_char(page, strokes, x, y, cell, bbox)
                x += cell + gap
            y += cell + gap
            page.line_count += 1
    flush()
    return LayoutResult(pages=pages, missing_chars=missing, truncated=truncated)


def layout_teach(chars: str, data: StrokeData, max_pages: int = 20, *,
                 page_width_mm: float = _PAGE_W_MM, page_height_mm: float = _PAGE_H_MM) -> LayoutResult:
    """单字=渐显面板页（每字一页，放不下续页）；多字=混合行（逐字分解行+成形格，超容续页）。"""
    targets = [c for c in chars if not c.isspace()]
    if not targets:
        raise StrokeOrderError("没有可教的汉字")
    missing = [c for c in targets if c not in data.medians]
    targets = [c for c in targets if c in data.medians]
    if not targets:
        raise StrokeOrderError("全部缺字：" + "".join(missing))
    if (not all(math.isfinite(value) and value <= 2000 for value in (page_width_mm, page_height_mm)) or
            page_width_mm < 2 * _MARGIN_MM + _PANEL_CELL_MM or
            page_height_mm < 2 * _MARGIN_MM + _HEADER_STRIP_MM + _PANEL_CELL_MM + _PANEL_GAP_MM):
        raise StrokeOrderError("当前纸张放不下教学格，请增大纸张或减小留边")
    if len(targets) >= 2:
        return _layout_mixed_rows(targets, data, missing, max_pages, page_width_mm, page_height_mm)


    first_panel_h = page_height_mm - 2 * _MARGIN_MM - _HEADER_STRIP_MM
    cont_panel_h = page_height_mm - 2 * _MARGIN_MM
    rows_first = max(1, int(first_panel_h // (_PANEL_CELL_MM + _PANEL_GAP_MM)))
    rows_cont = max(1, int(cont_panel_h // (_PANEL_CELL_MM + _PANEL_GAP_MM)))

    pages: list[LaidOutPage] = []
    truncated = False

    def start_page() -> LaidOutPage | None:
        nonlocal truncated
        if len(pages) >= max_pages:
            truncated = True
            return None
        return LaidOutPage(page_width_mm=page_width_mm, page_height_mm=page_height_mm)

    for ch in targets:
        strokes = data.medians[ch]
        n = len(strokes)
        bbox = _char_bbox(strokes)
        cols = min(n, max(1, int((page_width_mm + _PANEL_GAP_MM) // (_PANEL_CELL_MM + _PANEL_GAP_MM))))

        page = start_page()
        if page is None:
            break
        # 首行大字：成形全字居中
        hx = (page_width_mm - _HEADER_CELL_MM) / 2
        _emit_grid(page, hx, _MARGIN_MM, _HEADER_CELL_MM)
        _emit_char(page, strokes, hx, _MARGIN_MM, _HEADER_CELL_MM, bbox)
        page.line_count = 1

        step = 1  # 下一个待画面板序号（第 k 格 = 前 k 笔）
        has_header = True
        while step <= n:
            rows = rows_first if has_header else rows_cont
            stop = min(step + cols * rows, n + 1)
            y = (_MARGIN_MM + _HEADER_STRIP_MM) if has_header else _MARGIN_MM
            row_start = step
            while row_start < stop:
                row = range(row_start, min(row_start + cols, stop))
                total_w = len(row) * _PANEL_CELL_MM + (len(row) - 1) * _PANEL_GAP_MM
                x = (page_width_mm - total_w) / 2
                for k in row:
                    _emit_grid(page, x, y, _PANEL_CELL_MM)
                    _emit_char(page, strokes[:k], x, y, _PANEL_CELL_MM, bbox)
                    _emit_step_label(page, data, k, x, y, _PANEL_CELL_MM)
                    x += _PANEL_CELL_MM + _PANEL_GAP_MM
                y += _PANEL_CELL_MM + _PANEL_GAP_MM
                page.line_count += 1
                row_start += cols
            step = stop
            if step > n:
                break
            _center_page_vertically(page)
            pages.append(page)
            page = start_page()
            if page is None:
                break
            has_header = False
        if truncated:
            # 截断边界：本字已排的半页不丢（与 layout_text 同口径——如实上报）
            if page.strokes:
                _center_page_vertically(page)
                pages.append(page)
            break
        _center_page_vertically(page)
        pages.append(page)

    return LayoutResult(pages=pages, missing_chars=missing, truncated=truncated)
