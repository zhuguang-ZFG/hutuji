"""表格排版引擎：ParsedTable → LaidOutPage（格线 + 格内文字同页笔画）。

与 write_text 同一套字形度量数学（scale = size_mm/em_height、glyph_advance、
字形 Y 负半区向上与 SVG Y 向下同向），但**不 import write_text 的内部闭包**——
token_advance_mm 是 layout_text 的函数内定义，本模块按同一公式做字符级测量。
单元格是短文本：不需要文章级的避头/避尾/英文词黏结，按字贪心折行 + 长硬断。

边框不走 SVG `<line>` 元素：`page_to_svg` 只序列化 strokes，格线用闭合/直线
折线笔画表达，§5 闸 / vpype / 预览 / 设备对它们与文字笔画一视同仁（全是 path）。

字号收紧策略：列宽按内容测量，总和超页宽先按列等比压缩；压缩地板是
「最硬断单元（单字）+ 内边距」，地板之和仍超页宽才逐级缩字号（≥3.5mm，
再低 0.3mm 笔尖写出来不可读），全失败报错——绝不静默丢列。
"""
from __future__ import annotations

from dataclasses import dataclass

from .article_pages import ARTICLE_MAX_PAGES
from .doc_ingest import ParsedTable
from .gfont import GlyphData, glyph_advance, glyph_to_polylines
from .write_text import (
    FontMetrics,
    LaidOutPage,
    LayoutResult,
    WriteLayout,
    WriteTextError,
    _is_cjk,
    _PenStroke,
)

# 单元格内边距（mm）：0.3 笔尖 + 手写抖动，<1mm 字墨会糊到格线上
_CELL_PAD_X_MM = 1.2
_CELL_PAD_Y_MM = 1.0
# 表名（xlsx 工作表名）与表格体的间距
_TABLE_TITLE_GAP_MM = 2.0
# 单列表述自然宽上限：超页宽一半的列按折行处理，不让一列吃掉整页
_NATURAL_COL_CAP_RATIO = 0.5
# 缩字号下限：再低笔尖（0.3mm 线宽）写出的字不可读，报错比出糊字负责
_MIN_TABLE_FONT_MM = 3.5
_FONT_SHRINK_STEP_MM = 0.5
# 字号收敛迭代上界（正常 1-2 次收敛；防守性上限防浮点震荡死循环）
_LAYOUT_ATTEMPTS = 12
# 文件给了 <cols> 但个别列无 spec 时的回落宽（Excel 默认 8.43 字符）
_XLSX_FALLBACK_CHARS = 8.43

def _span_map(table: ParsedTable) -> dict[int, dict[int, int]]:
    """横向合并 span 查询表：行号 → {起始列: 结束列}（单格合并无意义，滤掉）。"""
    out: dict[int, dict[int, int]] = {}
    for r, c1, c2 in table.merges:
        if c2 > c1:
            out.setdefault(r, {})[c1] = c2
    return out



class _CellFont:
    """格内文字测量/落笔：公式与 write_text.layout_text 内部一致（字符级）。

    CJK 一律按全宽步进（metrics.full_width×scale+gap）——与 write_text 的
    token_advance_mm 同口径；v<9 字库 glyph_advance 是墨宽不是步进宽，逐字量
    会把同列汉字量得忽宽忽窄（2026-09-04 实测：张 5.84 / 语 3.47mm）。
    """

    def __init__(self, font, metrics: FontMetrics, size_mm: float, char_gap_mm: float):
        self._font = font
        self._metrics = metrics
        self.scale = size_mm / metrics.em_height
        self.char_gap_mm = char_gap_mm
        self.full_advance_mm = metrics.full_width * self.scale + char_gap_mm
        self._cache: dict[str, GlyphData | None] = {}
        self.missing: set[str] = set()

    def _glyph(self, ch: str) -> GlyphData | None:
        if ch not in self._cache:
            try:
                self._cache[ch] = self._font.load_glyph(ch)
            except Exception:  # noqa: BLE001 — 字形 ZIP 损坏按缺字处理，与 write_text 同口径
                self._cache[ch] = None
            if self._cache[ch] is None and not ch.isspace():
                self.missing.add(ch)
        return self._cache[ch]

    def char_width_mm(self, ch: str) -> float:
        if ch.isspace():
            return self._metrics.full_width * 0.5 * self.scale + self.char_gap_mm
        if _is_cjk(ch):
            return self.full_advance_mm
        glyph = self._glyph(ch)
        units = glyph_advance(glyph) if glyph is not None else self._metrics.full_width * 0.5
        return units * self.scale + self.char_gap_mm

    def text_width_mm(self, text: str) -> float:
        return sum(self.char_width_mm(ch) for ch in text)

    def wrap_cell(self, text: str, avail_mm: float) -> list[str]:
        """按字贪心折行；单字都比 avail 宽时硬放（字号地板已保证不发生）。"""
        if not text:
            return [""]
        lines: list[str] = []
        buf = ""
        width = 0.0
        for ch in text:
            w = self.char_width_mm(ch)
            if buf and width + w > avail_mm + 1e-6:
                lines.append(buf)
                buf, width = "", 0.0
            buf += ch
            width += w
        if buf or not lines:
            lines.append(buf)
        return lines

    def emit_text(self, page: LaidOutPage, text: str, x_origin: float, y_baseline: float) -> None:
        x = x_origin
        for ch in text:
            glyph = self._glyph(ch)
            if glyph is not None:
                for path in glyph_to_polylines(glyph):
                    pts = [(x + gx * self.scale, y_baseline + gy * self.scale) for gx, gy in path]
                    if len(pts) >= 2:
                        page.strokes.append(_PenStroke(points=pts))
            x += self.char_width_mm(ch)


def _measure_col_widths(
    tables: list[ParsedTable], font_mm: _CellFont, layout: WriteLayout
) -> list[list[float]]:
    """自然列宽：文件自报列宽（xlsx <col>）优先，否则内容测量；均封顶页宽一半。

    自报列宽单位是「字符数」（Excel 以默认字体 0 字宽为 1 单位），乘本字库
    0 字步进转 mm——与奎享 getColumnWidthInPixels 同源（POI 同样按此换算）。
    合并格起始格的内容不参与单列驱动（它按 span 总宽折行，不该撑胖单列）。
    """
    natural_cap = layout.text_width_mm * _NATURAL_COL_CAP_RATIO
    digit_w = font_mm.char_width_mm("0")
    out: list[list[float]] = []
    for table in tables:
        cols = max(len(r) for r in table.rows)
        if table.col_widths is not None:
            widths = [
                min(
                    (table.col_widths[c] if c < len(table.col_widths) else _XLSX_FALLBACK_CHARS) * digit_w + 2 * _CELL_PAD_X_MM,
                    natural_cap,
                )
                for c in range(cols)
            ]
            out.append(widths)
            continue
        spans = _span_map(table)
        widths = [2 * _CELL_PAD_X_MM + digit_w] * cols
        for r, row in enumerate(table.rows):
            row_spans = spans.get(r, {})
            for i, cell in enumerate(row):
                if not cell or row_spans.get(i, i) != i:
                    continue
                w = min(font_mm.text_width_mm(cell), natural_cap) + 2 * _CELL_PAD_X_MM
                if w > widths[i]:
                    widths[i] = w
        out.append(widths)
    return out


def layout_tables(
    tables: list[ParsedTable],
    font,
    metrics: FontMetrics,
    layout: WriteLayout,
    max_pages: int = ARTICLE_MAX_PAGES,
) -> LayoutResult:
    """表格列表 → 分页笔画。页数触 max_pages 截断并置 truncated=True。"""
    layout.validate()
    if not tables:
        raise WriteTextError("没有可排版的表格")

    size_mm = layout.size_mm
    font_mm: _CellFont | None = None
    plans: list[list[float]] = []
    for _ in range(_LAYOUT_ATTEMPTS):
        font_mm = _CellFont(font, metrics, size_mm, layout.char_gap_mm)
        plans = _measure_col_widths(tables, font_mm, _with_size(layout, size_mm))
        widths_ok = True
        fitted: list[list[float]] = []
        for table, natural in zip(tables, plans):
            total = sum(natural)
            avail = layout.text_width_mm
            if total <= avail:
                fitted.append(natural)
                continue
            floors = _col_floors(table, font_mm)
            if sum(floors) > avail:
                widths_ok = False
                break
            fitted.append(_shrink_cols(natural, floors, avail))
        if widths_ok:
            plans = fitted
            break
        size_mm -= _FONT_SHRINK_STEP_MM
        if size_mm < _MIN_TABLE_FONT_MM:
            raise WriteTextError(
                f"表格过宽：字号缩到 {_MIN_TABLE_FONT_MM}mm 仍排不下（请拆分表格或减列）"
            )
    assert font_mm is not None

    span_maps = [_span_map(t) for t in tables]
    return _paginate(tables, plans, span_maps, font_mm, _with_size(layout, size_mm), max_pages)


def _with_size(layout: WriteLayout, size_mm: float) -> WriteLayout:
    return WriteLayout(
        size_mm=size_mm,
        line_spacing=layout.line_spacing,
        char_gap_mm=layout.char_gap_mm,
        margin_left_mm=layout.margin_left_mm,
        margin_right_mm=layout.margin_right_mm,
        margin_top_mm=layout.margin_top_mm,
        margin_bottom_mm=layout.margin_bottom_mm,
        paragraph_indent=0,
    )


def _col_floors(table: ParsedTable, font_mm: _CellFont) -> list[float]:
    cols = max(len(r) for r in table.rows)
    floors = [2 * _CELL_PAD_X_MM] * cols
    for row in table.rows:
        for i, cell in enumerate(row):
            for ch in cell:
                need = 2 * _CELL_PAD_X_MM + font_mm.char_width_mm(ch)
                if need > floors[i]:
                    floors[i] = need
    return floors


def _shrink_cols(natural: list[float], floors: list[float], avail: float) -> list[float]:
    """等比压缩自然宽到 avail；跌破地板的列钉在地板，剩余列继续分摊（≤3 轮收敛）。"""
    widths = list(natural)
    for _ in range(3):
        total = sum(widths)
        if total <= avail:
            return widths
        flex = [i for i, w in enumerate(widths) if w > floors[i] + 1e-9]
        if not flex:
            return floors
        flex_total = sum(widths[i] - floors[i] for i in flex)
        budget = avail - sum(floors)
        if budget <= 0:
            return floors
        ratio = budget / flex_total
        for i in flex:
            widths[i] = max(floors[i], floors[i] + (widths[i] - floors[i]) * ratio)
    return widths


def _line(x0: float, y0: float, x1: float, y1: float) -> _PenStroke:
    return _PenStroke(points=[(x0, y0), (x1, y1)])


def _paginate(
    tables: list[ParsedTable],
    plans: list[list[float]],
    span_maps: list[dict[int, dict[int, int]]],
    font_mm: _CellFont,
    layout: WriteLayout,
    max_pages: int,
) -> LayoutResult:
    pages: list[LaidOutPage] = []
    page = LaidOutPage()
    truncated = False
    line_h = layout.line_height_mm
    x_left = layout.margin_left_mm
    y_top = layout.margin_top_mm
    y_bottom = layout.margin_top_mm + layout.text_height_mm
    y = y_top
    # 相邻行共享边框：上行底线即下行顶线，只画一次（重画 = 同一段描两遍，
    # G-code 行数与书写时间白白翻倍）。标题/表间距/翻页会打断连续性。
    prev_row_bottom: float | None = None

    def close_page() -> None:
        nonlocal page, y, prev_row_bottom
        # 空页也计数入 pages：页预算(len)是真值，病态「单行比整页高」靠它终止；
        # 空页在 _finish 统一滤除，不进产物。
        pages.append(page)
        page = LaidOutPage()
        y = y_top
        prev_row_bottom = None

    for table, col_w, spans in zip(tables, plans, span_maps):
        header = table.rows[0]
        title_h = line_h + _TABLE_TITLE_GAP_MM if table.name else 0.0
        start = 0
        while start < len(table.rows):
            rows_done = 0
            first_on_page = True
            while True:
                idx = start + rows_done
                if idx >= len(table.rows):
                    break
                row = table.rows[idx]
                row_h = _row_height(row, col_w, font_mm, line_h, spans.get(idx, {}))
                extra = row_h + (title_h if first_on_page and start == 0 else 0.0)
                # 跨页续表在新页重复表头（start==0 时首行本身就在，不重复）
                header_extra = 0.0
                if start > 0 and first_on_page:
                    header_extra = _row_height(header, col_w, font_mm, line_h, spans.get(0, {}))
                if y + extra + header_extra > y_bottom + 1e-9:
                    if first_on_page:
                        if len(pages) + 1 >= max_pages:
                            truncated = True
                            return _finish(pages, page, font_mm, truncated)
                        close_page()
                        continue
                    break
                if first_on_page and start == 0 and table.name:
                    _emit_title(page, table.name, x_left, y, font_mm, layout)
                    y += title_h
                if start > 0 and first_on_page:
                    _emit_row(page, header, col_w, y, x_left, font_mm, layout,
                              draw_top=prev_row_bottom is None, spans=spans.get(0, {}))
                    y += _row_height(header, col_w, font_mm, line_h, spans.get(0, {}))
                    prev_row_bottom = y
                    page.line_count += 1
                _emit_row(page, row, col_w, y, x_left, font_mm, layout,
                          draw_top=prev_row_bottom is None, spans=spans.get(idx, {}))
                y += row_h
                prev_row_bottom = y
                page.line_count += max(1, _cell_lines(row, col_w, font_mm, spans.get(idx, {})))
                rows_done += 1
                first_on_page = False
            if rows_done == 0 and start < len(table.rows):
                # 空页也放不下的病态行（折行后比整页还高）：页预算耗尽即截断，
                # 否则翻页再试（close_page 对空页也计数，保证此循环必然终止）。
                if len(pages) + 1 >= max_pages:
                    truncated = True
                    return _finish(pages, page, font_mm, truncated)
                close_page()
            start += rows_done
        prev_row_bottom = None
        if y > y_top:
            y += _TABLE_TITLE_GAP_MM  # 表间距

    return _finish(pages, page, font_mm, truncated)


def _row_height(
    row: list[str], col_w: list[float], font_mm: _CellFont, line_h: float, spans: dict[int, int]
) -> float:
    lines = _cell_lines(row, col_w, font_mm, spans)
    return max(1, lines) * line_h + 2 * _CELL_PAD_Y_MM


def _cell_lines(row: list[str], col_w: list[float], font_mm: _CellFont, spans: dict[int, int]) -> int:
    """该行最大折行数；合并格起始格按 span 总宽折，被覆盖格不算（内容恒空）。"""
    best = 1
    i = 0
    while i < len(col_w):
        end = spans.get(i, i)
        if i < len(row) and row[i]:
            avail = sum(col_w[i : end + 1]) - 2 * _CELL_PAD_X_MM
            best = max(best, len(font_mm.wrap_cell(row[i], avail)))
        i = end + 1
    return best

def _emit_title(page: LaidOutPage, title: str, x: float, y: float, font_mm: _CellFont, layout: WriteLayout) -> None:
    font_mm.emit_text(page, title, x, y + font_mm._metrics.baseline_to_top * font_mm.scale)
    page.line_count += 1


def _emit_row(
    page: LaidOutPage,
    row: list[str],
    col_w: list[float],
    y_top: float,
    x_left: float,
    font_mm: _CellFont,
    layout: WriteLayout,
    draw_top: bool,
    spans: dict[int, int],
) -> None:
    """一格线行：文字 + 顶/底横线 + 竖线；span 内部竖线不画（合并格语义）。"""
    line_h = layout.line_height_mm
    row_h = _row_height(row, col_w, font_mm, line_h, spans)
    x = x_left
    boundaries: list[float] = []
    i = 0
    while i < len(col_w):
        end = spans.get(i, i)
        w_span = sum(col_w[i : end + 1])
        if i < len(row) and row[i]:
            cy = y_top + _CELL_PAD_Y_MM + font_mm._metrics.baseline_to_top * font_mm.scale
            for line in font_mm.wrap_cell(row[i], w_span - 2 * _CELL_PAD_X_MM):
                font_mm.emit_text(page, line, x + _CELL_PAD_X_MM, cy)
                cy += line_h
        x += w_span
        boundaries.append(x)
        i = end + 1
    x_right = x
    if draw_top:
        page.strokes.append(_line(x_left, y_top, x_right, y_top))
    page.strokes.append(_line(x_left, y_top + row_h, x_right, y_top + row_h))
    page.strokes.append(_line(x_left, y_top, x_left, y_top + row_h))
    for bx in boundaries:
        page.strokes.append(_line(bx, y_top, bx, y_top + row_h))


def _finish(pages: list[LaidOutPage], page: LaidOutPage, font_mm: _CellFont, truncated: bool) -> LayoutResult:
    # 空页只存在于「单行比整页高」的病态截断路径，滤掉不进产物
    kept = [p for p in pages if p.strokes]
    if page.strokes:
        kept.append(page)
    if not kept:
        kept.append(page)
    return LayoutResult(
        pages=kept,
        missing_chars=sorted(font_mm.missing),
        truncated=truncated,
    )
