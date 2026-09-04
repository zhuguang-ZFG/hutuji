"""奎享单线字库 → 分页排版 → SVG 文本流。

设计要点（坐标系已实测钉死，勿改）：
- 字形 Y 轴向上为负（baseline=0，字身主体在负半区），与 SVG「Y 向下」天然同向：
  svg_y = baseline_y + glyph_y * scale，无需翻转（2026-08-31 如果梦万国库实测：
  「A」Y[-324,+7]、「g」Y[-224,+145]）。
- 本地字体全是 v<9，meta 无 top/bottom/advance——em 高与全宽字宽用探针字形实测
  （默认探针「永」（高）与「国」（宽）），不引 meta.size（方正兰亭=300 与字身 522 不符）。
- 笔画起笔可为负 X（行书出锋），相邻字墨区轻微交叠属字库设计，advance 用全宽+字距，
  不做 ink-box 紧贴。

排版规则（中文文章）：
- 页区 = (190-左右边距) × (190-上下边距) mm（§5 限幅 X/Y≤190 是硬线）
- \n 分段，段首缩进 2 个全宽字；CJK 任意折行，拉丁按空格折行、长词硬断
- 避头：行首禁「，。！？；：、）】》」…—·」等闭标点——并入上一行末，但以 §5 页限（190mm）为上界，超限让位折行
- 避尾：行尾禁「（【《「『“‘」等开标点——弹出随下一行行首
- 空段：连续 \n 不产出空行（空白段被跳过），段间距只有缩进一种表达——省纸取向的显式取舍
- 分页：行写满页高即切页；每页一份独立 SVG（多页 G-code 下发属 S3 侧后续）
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .gfont import GfontError, GfontFile, GlyphData, glyph_advance, glyph_to_polylines

# §5 硬限幅（gcode_gen.MAX_X_MM/MAX_Y_MM 同源；此处自守不 import 防循环）
_PAGE_LIMIT_MM = 190.0

# 行首禁排标点（避头）；行尾禁排开标点（避尾）
_NO_LINE_START = set("，。！？；：、）】》」』”’…—·～％")
_NO_LINE_END = set("（【《「『“‘")
_CJK_RE = re.compile(r"[㐀-鿿豈-﫿＀-￯]")

_PROBE_HEIGHT_CHAR = "永"
_PROBE_WIDTH_CHAR = "国"


class WriteTextError(Exception):
    """写字排版失败（字体缺失/参数越界/页面溢出前置校验）。"""


@dataclass
class WriteLayout:
    """排版参数；默认值按 190×190 页区手写字体验定。"""

    size_mm: float = 12.0  # CJK 字高目标（mm）；2026-09-02 用户反馈 8mm 在 190mm 页面上过小，默认上调
    line_spacing: float = 1.6  # 行距倍数（相对 size_mm）
    char_gap_mm: float = 0.8  # 字距（mm，加在步进后）
    margin_left_mm: float = 8.0
    margin_right_mm: float = 8.0
    margin_top_mm: float = 8.0
    margin_bottom_mm: float = 8.0
    paragraph_indent: int = 2  # 段首缩进全宽字数

    def validate(self) -> None:
        if not 2.0 <= self.size_mm <= 40.0:
            raise WriteTextError(f"字号越界：{self.size_mm}（允许 2–40mm）")
        if not 1.0 <= self.line_spacing <= 3.0:
            raise WriteTextError(f"行距越界：{self.line_spacing}（允许 1.0–3.0）")
        if not 0.0 <= self.char_gap_mm <= self.size_mm:
            raise WriteTextError(f"字距越界：{self.char_gap_mm}")
        for name in ("margin_left_mm", "margin_right_mm", "margin_top_mm", "margin_bottom_mm"):
            if getattr(self, name) < 0.0:
                raise WriteTextError(f"页边距非法：{name}={getattr(self, name)}")
        if self.text_width_mm <= self.size_mm:
            raise WriteTextError("页宽不足：边距后放不下一个整字")
        if self.text_height_mm <= self.size_mm * self.line_spacing:
            raise WriteTextError("页高不足：边距后放不下一行")
        if not 0 <= self.paragraph_indent <= 8:
            raise WriteTextError(f"段首缩进越界：{self.paragraph_indent}")

    @property
    def text_width_mm(self) -> float:
        return _PAGE_LIMIT_MM - self.margin_left_mm - self.margin_right_mm

    @property
    def text_height_mm(self) -> float:
        return _PAGE_LIMIT_MM - self.margin_top_mm - self.margin_bottom_mm

    @property
    def line_height_mm(self) -> float:
        return self.size_mm * self.line_spacing


@dataclass
class FontMetrics:
    """探针实测的字库度量（字体单位制）。"""

    em_height: float  # CJK 字身全高（永 实测 ymax-ymin）
    full_width: float  # 全宽字墨宽（国 实测 xmax-xmin）
    baseline_to_top: float  # 基线到字身顶（永 实测 -ymin，正数）


def measure_font(font: GfontFile) -> FontMetrics:
    """v<9 字库无 meta 度量，用探针字形实测；探针缺失即字库不适合排文章。"""
    height_glyph = font.load_glyph(_PROBE_HEIGHT_CHAR)
    width_glyph = font.load_glyph(_PROBE_WIDTH_CHAR)
    if height_glyph is None or width_glyph is None:
        raise WriteTextError(
            f"字库缺探针字（{_PROBE_HEIGHT_CHAR}/{_PROBE_WIDTH_CHAR}），不适合文章排版"
        )
    ys = [y for chunk in height_glyph.chunks for y in chunk.points[1::2]]
    xs = [x for chunk in width_glyph.chunks for x in chunk.points[0::2]]
    if not ys or not xs:
        raise WriteTextError("探针字形无笔画数据")
    em_height = max(ys) - min(ys)
    full_width = max(xs) - min(xs)
    baseline_to_top = -min(ys)
    if em_height <= 0 or full_width <= 0 or baseline_to_top <= 0:
        raise WriteTextError("探针字形度量异常")
    return FontMetrics(
        em_height=em_height, full_width=full_width, baseline_to_top=baseline_to_top
    )


@dataclass
class _PenStroke:
    """一条落笔路径（页面 SVG 坐标，单位 mm）。"""

    points: list[tuple[float, float]]


@dataclass
class LaidOutPage:
    strokes: list[_PenStroke] = field(default_factory=list)
    line_count: int = 0


@dataclass
class LayoutResult:
    pages: list[LaidOutPage]
    missing_chars: list[str]
    truncated: bool  # True = 输入文本未排完（页数已达上限）


def _is_cjk(ch: str) -> bool:
    return bool(_CJK_RE.match(ch))


def _tokenize(text: str) -> list[str]:
    """折行单元：CJK 单字、拉丁连字（字母数字串）、空白、标点各自成元。"""
    tokens: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            tokens.append(ch)
            i += 1
        elif _is_cjk(ch):
            tokens.append(ch)
            i += 1
        elif ch.isascii() and ch.isalnum():
            j = i
            while j < len(text) and text[j].isascii() and text[j].isalnum():
                j += 1
            tokens.append(text[i:j])
            i = j
        else:
            tokens.append(ch)
            i += 1
    return tokens


class _GlyphCache:
    """单字取形缓存：一字多页复用；缺字返回 None 并只记一次。"""

    def __init__(self, font: GfontFile):
        self._font = font
        self._cache: dict[str, GlyphData | None] = {}
        self.missing: list[str] = []

    def get(self, ch: str) -> GlyphData | None:
        if ch not in self._cache:
            try:
                self._cache[ch] = self._font.load_glyph(ch)
            except GfontError:
                self._cache[ch] = None
            if self._cache[ch] is None and ch not in self.missing:
                self.missing.append(ch)
        return self._cache[ch]


def layout_text(
    text: str,
    font: GfontFile,
    metrics: FontMetrics,
    layout: WriteLayout,
    max_pages: int = 20,
) -> LayoutResult:
    """核心排版：文本 → 分页落笔路径（页面 SVG 坐标，mm）。

    max_pages 是防呆上界（与设备侧 kArticleMaxPages 同值）；超出截断并置 truncated。
    实际容量随字号走：默认 12mm 字高约 117 字/页 → 20 页 ≈2300 字先到页数闸，
    `server._WRITE_TEXT_MAX_CHARS=5000` 只在更小字号下才可能先触发。
    """
    layout.validate()
    if not text.strip():
        raise WriteTextError("文本为空")
    if max_pages < 1:
        raise WriteTextError("max_pages 非法")

    scale = layout.size_mm / metrics.em_height
    full_advance_mm = metrics.full_width * scale + layout.char_gap_mm
    indent_mm = layout.paragraph_indent * full_advance_mm

    cache = _GlyphCache(font)

    def token_advance_mm(token: str) -> float:
        if token == " ":
            return full_advance_mm * 0.5
        if len(token) == 1 and _is_cjk(token):
            return full_advance_mm
        width_units = 0.0
        for ch in token:
            if ch.isspace():
                width_units += metrics.full_width * 0.5
                continue
            glyph = cache.get(ch)
            width_units += (
                glyph_advance(glyph) if glyph is not None else metrics.full_width * 0.5
            )
        return width_units * scale + layout.char_gap_mm * max(1, len(token))

    def emit_glyph(strokes: list[_PenStroke], ch: str, x_origin: float, y_baseline: float) -> None:
        glyph = cache.get(ch)
        if glyph is None:
            return
        for path in glyph_to_polylines(glyph):
            pts = [
                (x_origin + gx * scale, y_baseline + gy * scale) for gx, gy in path
            ]
            if len(pts) >= 2:
                strokes.append(_PenStroke(points=pts))

    pages: list[LaidOutPage] = []
    page = LaidOutPage()
    max_lines_per_page = int(layout.text_height_mm // layout.line_height_mm)

    def new_page() -> None:
        nonlocal page
        pages.append(page)
        page = LaidOutPage()

    # 逐段处理；段内折行，段首缩进
    paragraphs = text.split("\n")
    truncated = False
    for para_index, paragraph in enumerate(paragraphs):
        tokens = _tokenize(paragraph)
        line_no_on_page = page.line_count
        x_cursor = layout.margin_left_mm + (indent_mm if paragraph.strip() else 0.0)
        y_baseline = (
            layout.margin_top_mm
            + metrics.baseline_to_top * scale
            + line_no_on_page * layout.line_height_mm
        )
        # 行内暂存：避头标点需要整行回退重组，简化为「行尾溢出时把行首禁排标点并入」
        line_tokens: list[str] = []
        line_width = x_cursor - layout.margin_left_mm
        line_start_x = x_cursor

        def flush_line() -> bool:
            """把暂存行落版；返回 False 表示页满需切页（调用方判 max_pages）。"""
            nonlocal x_cursor, y_baseline, line_tokens, line_width, line_start_x
            x = line_start_x
            for tok in line_tokens:
                if tok == "\t":
                    tok = " "
                for ch in tok:
                    if ch.isspace():
                        x += full_advance_mm * 0.5
                        continue
                    emit_glyph(page.strokes, ch, x, y_baseline)
                    glyph = cache.get(ch)
                    if len(tok) == 1 and _is_cjk(ch):
                        x += full_advance_mm
                    else:
                        adv_units = (
                            glyph_advance(glyph)
                            if glyph is not None
                            else metrics.full_width * 0.5
                        )
                        x += adv_units * scale + layout.char_gap_mm
            page.line_count += 1
            line_tokens = []
            line_width = 0.0
            line_start_x = layout.margin_left_mm
            if page.line_count >= max_lines_per_page:
                return False
            y_baseline = (
                layout.margin_top_mm
                + metrics.baseline_to_top * scale
                + page.line_count * layout.line_height_mm
            )
            x_cursor = layout.margin_left_mm
            return True

        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok in ("\r",):
                i += 1
                continue
            adv = token_advance_mm(tok)
            overflow = line_width + adv > layout.text_width_mm and line_tokens
            # 避头：闭标点不置行首、并入当前行末——但并入后墨迹不得越 §5 页限
            # （190mm 硬线）：连续闭标点（。」/！」在中文常见）在满行会把右缘顶出限幅，
            # 被 validate_gcode 整单拒绝（2026-09-04 测试实锤：无上限时墨至 198mm）。
            # 超限则避头让位、照常折行——标点行首是外观妥协，越界是整篇写不出。
            merge_into_line = (
                tok in _NO_LINE_START
                and layout.margin_left_mm + line_width + adv <= _PAGE_LIMIT_MM
            )
            if overflow and not merge_into_line and tok.strip():
                # 避尾：开标点不得收行尾——弹出暂存行尾的开标点，随新行同行首
                # （tokenize 把开标点独立成元，无此回退它会留守行尾；旧注释声称
                # 「自然折行已覆盖」不实，2026-09-04 测试实锤旧行为墨至行尾 178）。
                carried: list[str] = []
                carried_adv = 0.0
                while line_tokens and line_tokens[-1] in _NO_LINE_END:
                    carried.insert(0, line_tokens.pop())
                    carried_adv += token_advance_mm(carried[0])
                line_width -= carried_adv
                if line_tokens:
                    if not flush_line():
                        if len(pages) + 1 >= max_pages:
                            truncated = True
                            break
                        new_page()
                        y_baseline = layout.margin_top_mm + metrics.baseline_to_top * scale
                    line_start_x = layout.margin_left_mm
                # 病态输入（整行全是开标点）不 flush，carried 落回本行继续
                if carried:
                    line_tokens.extend(carried)
                    line_width += carried_adv
            line_tokens.append(tok)
            line_width += adv
            i += 1
        if truncated:
            break
        if line_tokens and not flush_line():
            if len(pages) + 1 >= max_pages:
                truncated = True
                break
            new_page()
        if truncated:
            break
    else:
        # 全部段落排完
        pass

    if not truncated and (page.strokes or page.line_count > 0 or not pages):
        pages.append(page)
    if truncated and page.strokes:
        pages.append(page)

    return LayoutResult(
        pages=pages,
        missing_chars=cache.missing,
        truncated=truncated,
    )


def page_to_svg(page: LaidOutPage) -> str:
    """落笔路径 → 单线 SVG（元素白名单内：仅 <svg>/<path>，属性最简）。"""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="190mm" height="190mm" '
        f'viewBox="0 0 190 190">'
    ]
    for stroke in page.strokes:
        d = "M" + "L".join(f"{x:.3f},{y:.3f}" for x, y in stroke.points)
        parts.append(f'<path d="{d}" fill="none" stroke="black" stroke-width="0.3"/>')
    parts.append("</svg>")
    return "".join(parts)

# 预览 PNG 几何（2026-09-01 实机实测钉死，勿改小）：整页 190mm 幅面里 1px 灰度细线
# 在设备预览缩放下会被采样吞没（首版 760² 灰度白屏），故按内容包围盒裁剪放大成
# RGB 512² 方图 + 2px 方形笔刷。设备侧 LvglAllocatedImage 只解 PNG。
_PREVIEW_PX = 512
_PREVIEW_PAD_RATIO = 0.10
_PREVIEW_STROKE_PX = 2


def page_to_preview_png(page: LaidOutPage) -> bytes:
    """落笔路径 → 预览 PNG 字节（设备 hutuji.draw 的 `.png` 能力地址）。

    纯 numpy 画线 + imageio 落盘，不引描摹链（skimage/sknw）：写字页已是 mm 折线，
    无需骨架化。空页返回纯白图——预览必须存在，否则设备侧 StartDraw 校验即拒。
    """
    try:
        import imageio.v3 as iio
        import numpy as np
    except Exception as exc:  # noqa: BLE001
        raise WriteTextError(f"预览图依赖不可用（{type(exc).__name__}）") from None

    canvas = np.full((_PREVIEW_PX, _PREVIEW_PX, 3), 255, dtype=np.uint8)
    xs = [x for stroke in page.strokes for x, _ in stroke.points]
    ys = [y for stroke in page.strokes for _, y in stroke.points]
    if not xs:
        return iio.imwrite("<bytes>", canvas, extension=".png")

    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    span = max(x1 - x0, y1 - y0, 1.0)
    pad = span * _PREVIEW_PAD_RATIO
    scale = _PREVIEW_PX / (span + 2 * pad)
    half = _PREVIEW_STROKE_PX // 2
    last = _PREVIEW_PX - 1

    for stroke in page.strokes:
        pts = stroke.points
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            px0, py0 = (ax - x0 + pad) * scale, (ay - y0 + pad) * scale
            px1, py1 = (bx - x0 + pad) * scale, (by - y0 + pad) * scale
            # DDA：步数取两轴像素跨度上界，保证不留断点（笔画多为短段，成本可忽略）
            steps = int(max(abs(px1 - px0), abs(py1 - py0), 1.0))
            xi = np.clip(np.linspace(px0, px1, steps + 1).astype(int), 0, last)
            yi = np.clip(np.linspace(py0, py1, steps + 1).astype(int), 0, last)
            for dy in range(-half, half + 1):
                for dx in range(-half, half + 1):
                    canvas[np.clip(yi + dy, 0, last), np.clip(xi + dx, 0, last)] = 0
    return iio.imwrite("<bytes>", canvas, extension=".png")


def load_font(fonts_dir: str, name: str | None = None) -> GfontFile:
    """按显示名或文件名词解析字库；name 为空时取目录中字数最多的字库。"""
    if not fonts_dir or not os.path.isdir(fonts_dir):
        raise WriteTextError(f"字库目录不可用：{fonts_dir or '(未配置)'}")
    candidates = [
        os.path.join(fonts_dir, f)
        for f in sorted(os.listdir(fonts_dir))
        if f.lower().endswith(".gfont")
    ]
    if not candidates:
        raise WriteTextError(f"字库目录无 .gfont 文件：{fonts_dir}")

    chosen: str | None = None
    if name:
        for path in candidates:
            if name in os.path.basename(path):
                chosen = path
                break
        if chosen is None:
            raise WriteTextError(f"找不到字库「{name}」（目录内 {len(candidates)} 个）")
    else:
        best_count = -1
        for path in candidates:
            try:
                font = GfontFile.parse(open(path, "rb").read())
            except GfontError:
                continue
            if font.meta.declared_count > best_count:
                best_count = font.meta.declared_count
                chosen = path
        if chosen is None:
            raise WriteTextError("目录内无可用字库")

    try:
        return GfontFile.parse(open(chosen, "rb").read())
    except GfontError as exc:
        raise WriteTextError(f"字库解析失败：{os.path.basename(chosen)}：{exc}") from exc
