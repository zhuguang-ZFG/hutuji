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
- 多字（用户 2026-09-11 拍板「集中到单张纸」）：字帖模式——单页 2 列田字格，
  每格成形全字 + 逐笔起点旁中文序号（第 i 笔标「一/二/三…」，按序描红）；
  格尺寸按字数自适应（56mm 起、24mm 地板），超单页容量（18 字）才续页。
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
_COPYBOOK_COLS = 2  # 字帖模式列数：2 列大格比 5 列小格可读
_COPYBOOK_CELL_MAX_MM = 56.0  # 字帖格上限：再大单行两格也放不下教学价值
_COPYBOOK_CELL_MIN_MM = 24.0  # 字帖格地板：再小逐笔序号挤成一团
_STROKE_NUM_MM = 4.5  # 字帖逐笔序号字号（贴笔画起点，不能盖过笔画）

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
            page.strokes.append(_PenStroke(points=pts))


def _emit_grid(page: LaidOutPage, cell_x: float, cell_y: float, cell_mm: float) -> None:
    """田字格：外框实线 + 中十字虚线（4mm 线 / 2mm 空）。"""
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


def _emit_stroke_numbers(
    page: LaidOutPage,
    data: StrokeData,
    strokes: list[list[tuple[int, int]]],
    bbox: tuple[int, int, int, int],
    cell_x: float,
    cell_y: float,
    cell_mm: float,
) -> None:
    """字帖逐笔序号：第 i 笔起点旁标中文数字（与 _emit_char 同变换定位、钳制格内）。"""
    x0, y0, x1, y1 = bbox
    span = max(x1 - x0, y1 - y0, 1)
    scale = (cell_mm - 4.0) / span
    ox = cell_x + 2.0 + (cell_mm - 4.0 - (x1 - x0) * scale) / 2
    oy = cell_y + 2.0 + (cell_mm - 4.0 - (y1 - y0) * scale) / 2
    edge = 0.6  # 序号离格边最小距离（不压外框线）
    for i, stroke in enumerate(strokes, 1):
        if not stroke:
            continue
        fx, fy = stroke[0]
        px = ox + (fx - x0) * scale
        py = oy + (y1 - fy) * scale
        label = _cjk_step_label(i)
        glyphs = [data.medians[d] for d in label if d in data.medians]
        if not glyphs:
            continue
        lx = min(max(px + 1.2, cell_x + edge), cell_x + cell_mm - edge - _STROKE_NUM_MM * len(glyphs))
        ly = min(max(py + 1.2, cell_y + edge), cell_y + cell_mm - edge - _STROKE_NUM_MM)
        for j, g in enumerate(glyphs):
            _emit_char(
                page, g, lx + j * _STROKE_NUM_MM, ly, _STROKE_NUM_MM,
                _char_bbox(g), pad_mm=0.3,
            )


def _center_page_vertically(page: LaidOutPage) -> None:
    """整页内容竖向居中于页区（教学页内容常只占上半页，贴顶难看）。"""
    ys = [y for s in page.strokes for _, y in s.points]
    if not ys:
        return
    lo, hi = min(ys), max(ys)
    shift = (_MARGIN_MM + (_PAGE_H_MM - _MARGIN_MM) - (hi - lo)) / 2 - lo
    shift = max(0.0, shift)  # 内容超高页区时不动作（防下移出界）
    if shift:
        for s in page.strokes:
            s.points = [(x, y + shift) for x, y in s.points]


def _layout_copybook(
    targets: list[str],
    data: StrokeData,
    missing: list[str],
    max_pages: int,
) -> LayoutResult:
    """多字字帖（2026-09-11 用户拍板「集中到单张纸」）：单页 2 列成形格，逐笔中文序号。

    格尺寸按字数自适应（56 上限 / 24 地板）；地板仍放不下整页（>18 字）才续页，
    续页版式同首页（无大字头——字帖模式没有大字头）。max_pages 与单字路径同口径。
    """
    gap = _PANEL_GAP_MM
    usable_w = _PAGE_W_MM - 2 * _MARGIN_MM
    usable_h = _PAGE_H_MM - 2 * _MARGIN_MM
    pages: list[LaidOutPage] = []
    truncated = False
    idx = 0
    while idx < len(targets):
        if len(pages) >= max_pages:
            truncated = True
            break
        rows_all = math.ceil((len(targets) - idx) / _COPYBOOK_COLS)
        cell = min(
            (usable_w - (_COPYBOOK_COLS - 1) * gap) / _COPYBOOK_COLS,
            (usable_h - (rows_all - 1) * gap) / rows_all,
            _COPYBOOK_CELL_MAX_MM,
        )
        if cell < _COPYBOOK_CELL_MIN_MM:
            cell = _COPYBOOK_CELL_MIN_MM
            rows_here = max(1, int((usable_h + gap) // (cell + gap)))
        else:
            rows_here = rows_all
        take = targets[idx:idx + rows_here * _COPYBOOK_COLS]
        page = LaidOutPage()
        row_count = math.ceil(len(take) / _COPYBOOK_COLS)
        y = _MARGIN_MM
        for r in range(row_count):
            row = take[r * _COPYBOOK_COLS:(r + 1) * _COPYBOOK_COLS]
            row_w = len(row) * cell + (len(row) - 1) * gap
            x = (_PAGE_W_MM - row_w) / 2
            for ch in row:
                strokes = data.medians[ch]
                bbox = _char_bbox(strokes)
                _emit_grid(page, x, y, cell)
                _emit_char(page, strokes, x, y, cell, bbox)
                _emit_stroke_numbers(page, data, strokes, bbox, x, y, cell)
                x += cell + gap
            y += cell + gap
            page.line_count += 1
        _center_page_vertically(page)
        pages.append(page)
        idx += len(take)
    return LayoutResult(pages=pages, missing_chars=missing, truncated=truncated)


def layout_teach(chars: str, data: StrokeData, max_pages: int = 20) -> LayoutResult:
    """单字=渐显面板页（每字一页，放不下续页）；多字=单页字帖（逐笔序号，超容续页）。"""
    targets = [c for c in chars if not c.isspace()]
    if not targets:
        raise StrokeOrderError("没有可教的汉字")
    missing = [c for c in targets if c not in data.medians]
    targets = [c for c in targets if c in data.medians]
    if not targets:
        raise StrokeOrderError("全部缺字：" + "".join(missing))
    if len(targets) >= 2:
        return _layout_copybook(targets, data, missing, max_pages)


    first_panel_h = _PAGE_H_MM - _MARGIN_MM - (_MARGIN_MM + _HEADER_STRIP_MM)  # 138
    cont_panel_h = _PAGE_H_MM - 2 * _MARGIN_MM  # 174
    rows_first = max(1, int(first_panel_h // (_PANEL_CELL_MM + _PANEL_GAP_MM)))
    rows_cont = max(1, int(cont_panel_h // (_PANEL_CELL_MM + _PANEL_GAP_MM)))

    pages: list[LaidOutPage] = []
    truncated = False

    def start_page() -> LaidOutPage | None:
        nonlocal truncated
        if len(pages) >= max_pages:
            truncated = True
            return None
        return LaidOutPage()

    for ch in targets:
        strokes = data.medians[ch]
        n = len(strokes)
        bbox = _char_bbox(strokes)
        cols = min(n, _PANEL_COLS_MAX)

        page = start_page()
        if page is None:
            break
        # 首行大字：成形全字居中
        hx = (_PAGE_W_MM - _HEADER_CELL_MM) / 2
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
                x = (_PAGE_W_MM - total_w) / 2
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
