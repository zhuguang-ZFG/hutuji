"""奎享单线字库 → 分页排版 → SVG 文本流。

设计要点（坐标系已实测钉死，勿改）：
- 字形 Y 轴向上为负（baseline=0，字身主体在负半区），与 SVG「Y 向下」天然同向：
  svg_y = baseline_y + glyph_y * scale，无需翻转（2026-08-31 如果梦万国库实测：
  「A」Y[-324,+7]、「g」Y[-224,+145]）。
- 本地字体全是 v<9，meta 无 top/bottom/advance——em 高与全宽字宽用探针字形实测
  （默认探针「永」（高）与「国」（宽）），不引 meta.size（方正兰亭=300 与字身 522 不符）。
- CJK 步进 = 字号（字身 em）+ 字距，不用「国」墨宽当全宽（2026-09-06 实证：行书库
  国墨宽仅 0.70em、印刷体 0.88em，按墨宽走步进系统性短缺 → 相邻字墨区相撞）。
  行书出锋起笔仍可为负 X，装饰笔画的轻微越格属字库设计，不做 per-glyph ink-box 变宽
  （变宽会毁掉汉字方格对齐；越格严重的字库靠调大 char_gap_mm 兜）。
- 文本入口的繁→简规范化在 server.hutuji_write 做（t2s 模块），本模块只管排版。

排版规则（中文文章）：
- 页区 = (190-左右边距) × (277-上下边距) mm（竖版 B 案：宽 190mm 阅读方向、高 277mm 行推进；
  转置由 gcode_gen 在 G-code 层做机器 X:=SVG y、Y:=SVG x，墙上站直横读）
- \n 分段，段首缩进 2 个全宽字；CJK 任意折行，拉丁按空格折行、长词硬断
- 避头：行首禁「，。！？；：、）】》」…—·」等闭标点——并入上一行末，但以页宽（190mm）为上界，超限让位折行
- 避尾：行尾禁「（【《「『“‘」等开标点——弹出随下一行行首
- 空段：连续 \n 不产出空行（空白段被跳过）；普通段间距只有缩进一种表达——省纸取向的显式取舍
- 标题段（headings 参数，文档导入链检出）：18mm 放大落墨（占 heading_rows 个正文行带）、
  逐视觉行水平居中、免段首缩进、段前后各空一行（页首/页底/切页后不加）；行网格恒按正文字号
- 分页：行写满页高即切页；每页一份独立 SVG（多页 G-code 下发属 S3 侧后续）
"""

import math
import os
import re
from dataclasses import dataclass, field

from .gfont import GfontError, GfontFile, GlyphData, glyph_advance, glyph_to_polylines

# §5 硬限幅分轴（gcode_gen.MAX_X_MM/MAX_Y_MM 同源；此处自守不 import 防循环）
# 竖版 B 案（2026-09-06 用户拍板「不要横着写，要竖着写」）：机器墙上竖装，撰写页
# 宽=190mm（SVG x 阅读方向）、高=277mm（SVG y 行推进方向）。gcode_gen 在 G-code 层
# 做转置（机器 X:=SVG y、Y:=SVG x），所以本文件只管竖版撰写页几何。
_PAGE_WIDTH_MM = 190.0   # 竖版页宽：210 − 2×10 边距（SVG x 阅读方向向右）
_PAGE_HEIGHT_MM = 277.0  # 竖版页高：297 − 2×10 边距（SVG y 行推进向下）

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
    heading_size_mm: float = 18.0  # 标题字号（1.5×正文，2026-09-04 用户拍板标题须放大）；行高按行距倍数折算成整数行带

    @property
    def heading_rows(self) -> int:
        """标题行占用的正文行数（标题行高向上取整，最少 1 行）。"""
        return max(1, math.ceil(self.heading_size_mm * self.line_spacing / self.line_height_mm))

    def validate(self) -> None:
        if not 2.0 <= self.size_mm <= 40.0:
            raise WriteTextError(f"字号越界：{self.size_mm}（允许 2–40mm）")
        if not 1.0 <= self.line_spacing <= 3.0:
            raise WriteTextError(f"行距越界：{self.line_spacing}（允许 1.0–3.0）")
        if not 0.0 <= self.char_gap_mm <= self.size_mm:
            raise WriteTextError(f"字距越界：{self.char_gap_mm}")
        if not 2.0 <= self.heading_size_mm <= 40.0:
            raise WriteTextError(f"标题字号越界：{self.heading_size_mm}（允许 2–40mm）")
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
        return _PAGE_WIDTH_MM - self.margin_left_mm - self.margin_right_mm

    @property
    def text_height_mm(self) -> float:
        return _PAGE_HEIGHT_MM - self.margin_top_mm - self.margin_bottom_mm

    @property
    def line_height_mm(self) -> float:
        return self.size_mm * self.line_spacing


@dataclass
class FontMetrics:
    """探针实测的字库度量（字体单位制）。"""

    em_height: float  # CJK 字身全高（永 实测 ymax-ymin）
    full_width: float  # 全宽字墨宽（国 实测 xmax-xmin）
    baseline_to_top: float  # 基线到字身顶（永 实测 -ymin，正数）
    # 分组左越界（负 min-x 的绝对值上限，0=无越界）：拉丁槽位与拉丁→CJK 边界补偿用。
    # 2026-09-06 审计实证：如果梦「年」左越界 227 units（12mm 字号下 5.2mm），
    # 「1932年」的数字槽（半 em）根本盖不住，2→年 纸面相撞 1.94mm。
    cjk_left_overhang: float = 0.0
    latin_left_overhang: float = 0.0
    # 逐字槽位公式见 layout_text.cjk_slot_units / latin_advance_units。


# 左越界探针集：高频 CJK 抽样 + 全 ASCII 字母数字；只量在库字形
_CJK_OVERHANG_PROBES = "的一是不了人我在有他这中大来上国个年到说们为子和" + "永国中文字"
_LATIN_OVERHANG_PROBES = tuple(
    chr(c) for c in range(ord("0"), ord("9") + 1)
) + tuple(chr(c) for c in range(ord("a"), ord("z") + 1)) + tuple(
    chr(c) for c in range(ord("A"), ord("Z") + 1)
)


def _probe_extents_units(font: GfontFile, probes) -> tuple[float, float]:
    """扫探针字形，返回（左越界上限, 墨右缘上限）；无命中字形则双 0。"""
    overhang = 0.0
    right_edge = 0.0
    for ch in probes:
        glyph = font.load_glyph(ch)
        if glyph is None:
            continue
        xs = [x for chunk in glyph.chunks for x in chunk.points[0::2]]
        if xs:
            overhang = max(overhang, -min(xs))
            right_edge = max(right_edge, max(xs))
    return overhang, right_edge


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
    cjk_overhang, _ = _probe_extents_units(font, _CJK_OVERHANG_PROBES)
    latin_overhang, _ = _probe_extents_units(font, _LATIN_OVERHANG_PROBES)
    return FontMetrics(
        em_height=em_height,
        full_width=full_width,
        baseline_to_top=baseline_to_top,
        cjk_left_overhang=cjk_overhang,
        latin_left_overhang=latin_overhang,
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
    headings: set[int] | None = None,
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

    # 行网格（y 基线/行计数）恒按正文字号走；标题段只在落墨与横向步进上切大字号，
    # 竖向占 heading_rows 个正文行带——行计数语义不被标题搞混，分页/容量闸全部照旧
    base_scale = layout.size_mm / metrics.em_height
    scale = base_scale
    # CJK 网格 = 字身 em（字号本身）+ 字距；逐字槽位见 cjk_slot_units。
    # 2026-09-06 前用「国」墨宽×scale 作全宽——行书库该比 0.70em、印刷体 0.88em，
    # 步进系统性短缺 → 相邻字墨区相撞（用户纸面实证「有字重叠到一起」，两字体
    # 本地复现）。full_width 探针仍留给拉丁缺字回退/空格折算用。
    full_advance_mm = layout.size_mm + layout.char_gap_mm
    indent_mm = layout.paragraph_indent * full_advance_mm

    cache = _GlyphCache(font)

    def cjk_slot_units(ch: str) -> float:
        """CJK 逐字槽位（字库单位）：em 与（本字墨右缘+全库 CJK 左越界上限）取大。

        字库字形不以 [0,em] 为界（如果梦「来」墨跨 [-180,398]，宽字墨宽达
        1.11em）；em 网格只保普通字对，宽右缘字后随大左越界字必撞（探针实测
        是→是 需 571>522、来→和 需 661>522）。槽位按本字右缘预留全库最大
        左越界——窄字留在 em 网格，宽字局部放宽；逐字无关上下文，测量/落墨
        同式，折行不错位。缺字形回落 em。
        """
        glyph = cache.get(ch)
        if glyph is None:
            return metrics.em_height
        xs = [x for chunk in glyph.chunks for x in chunk.points[0::2]]
        if not xs:
            return metrics.em_height
        return max(metrics.em_height, max(xs) + metrics.cjk_left_overhang)

    def token_advance_mm(token: str) -> float:
        # Tab 与空格同价：flush_line 把 "\t" 折成 " " 落版（全宽半步、无字距），
        # 测量必须同口径，否则 Tab 行折行点与墨迹错位（旧口径 5.0mm 低压 6.4mm）
        if token in (" ", "\t"):
            return full_advance_mm * 0.5
        if len(token) == 1 and _is_cjk(token):
            return cjk_slot_units(token) * scale + layout.char_gap_mm
        width_units = 0.0
        for ch in token:
            if ch.isspace():
                width_units += metrics.full_width * 0.5
                continue
            if ch.isascii() and ch.isalnum():
                # v<9 字库拉丁/数字无可靠 advance（glyph_advance 是墨宽残值），
                # 半 em 地板 + 墨右缘/拉丁左越界保底，见 latin_advance_units
                width_units += latin_advance_units(ch)
                continue
            glyph = cache.get(ch)
            width_units += (
                glyph_advance(glyph) if glyph is not None else metrics.full_width * 0.5
            )
        return width_units * scale + layout.char_gap_mm * max(1, len(token))

    def latin_advance_units(ch: str) -> float:
        """拉丁/数字步进（字库单位）：半 em 与（墨右缘+拉丁左越界）取大。

        v<9 字库无拉丁 advance，glyph_advance 是墨宽残值（近 0 或偏小）；
        半 em 对窄字符够，但宽墨字符会越槽——注意如果梦万国库「2」撞「年」
        的真因不是「2」太宽（墨右缘仅 153 units），而是「年」左越界 227
        units 把墨伸进数字槽（2026-09-06 样本审计实证，纸面相撞 1.94mm）。
        槽内预留全字库拉丁左越界覆盖拉丁内部相邻；后随 CJK 大左越界的边界
        补偿在 flush_line 前瞻另加（折行测量同口径，见下方 token 交界补偿）。
        """
        floor = metrics.em_height * 0.5
        glyph = cache.get(ch)
        if glyph is None:
            return floor
        xs = [x for chunk in glyph.chunks for x in chunk.points[0::2]]
        if not xs:
            return floor
        return max(floor, max(xs) + metrics.latin_left_overhang)

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
    # 逐段处理；段内折行，段首缩进（标题段：放大居中、免缩进、段前后各加一行空距）
    heading_set = headings or set()
    paragraphs = text.split("\n")
    truncated = False
    for para_idx, paragraph in enumerate(paragraphs):
        # 空白段（零字符或纯空格/制表）整段跳过不产空行——省纸取向的显式取舍
        if not paragraph.strip():
            continue
        is_heading = para_idx in heading_set
        # 标题段切大字号落墨/步进（竖向行网格不变，见上方 base_scale 注释）
        scale = layout.heading_size_mm / metrics.em_height if is_heading else base_scale
        full_advance_mm = (layout.heading_size_mm if is_heading else layout.size_mm) + layout.char_gap_mm
        heading_rows = layout.heading_rows if is_heading else 1
        if is_heading and page.line_count > 0:
            # 段前空一行；页首不加。空距 + 标题整行带要同框，否则先切页（不栏尾孤标题）
            if page.line_count + 1 + heading_rows > max_lines_per_page:
                if len(pages) + 1 >= max_pages:
                    truncated = True
                    break
                new_page()
            else:
                page.line_count += 1
        tokens = _tokenize(paragraph)
        line_no_on_page = page.line_count
        x_cursor = layout.margin_left_mm + (0.0 if is_heading else indent_mm)
        y_baseline = (
            layout.margin_top_mm
            + metrics.baseline_to_top * base_scale
            + line_no_on_page * layout.line_height_mm
        )
        # 行内暂存：避头标点需要整行回退重组，简化为「行尾溢出时把行首禁排标点并入」
        line_tokens: list[str] = []
        line_width = x_cursor - layout.margin_left_mm
        line_start_x = x_cursor

        def flush_line() -> bool:
            """把暂存行落版；返回 False 表示页满需切页（调用方判 max_pages）。"""
            nonlocal x_cursor, y_baseline, line_tokens, line_width, line_start_x
            stroke_base = len(page.strokes)
            x = line_start_x
            flat = [ch for tok in line_tokens for ch in (" " if tok == "\t" else tok)]
            for idx, ch in enumerate(flat):
                if ch.isspace():
                    x += full_advance_mm * 0.5
                    continue
                emit_glyph(page.strokes, ch, x, y_baseline)
                glyph = cache.get(ch)
                nxt = flat[idx + 1] if idx + 1 < len(flat) else None
                if _is_cjk(ch):
                    x += cjk_slot_units(ch) * scale + layout.char_gap_mm
                elif ch.isascii() and ch.isalnum():
                    # 与 token_advance_mm 同口径：半 em 地板 + 墨右缘/拉丁左越界保底；
                    # 后随 CJK 时加其左越界补偿（「1932年」数字槽盖不住「年」的出锋，
                    # 2026-09-06 审计 2→年 撞 1.94mm）。测量侧在折行循环按 token
                    # 交界同口径计入同一笔补偿——两侧不等宽折行点就会与墨迹错位。
                    boundary = (
                        metrics.cjk_left_overhang if nxt is not None and _is_cjk(nxt) else 0.0
                    )
                    x += (latin_advance_units(ch) + boundary) * scale + layout.char_gap_mm
                else:
                    adv_units = (
                        glyph_advance(glyph)
                        if glyph is not None
                        else metrics.full_width * 0.5
                    )
                    x += adv_units * scale + layout.char_gap_mm
            if is_heading:
                # 标题逐视觉行水平居中：按落墨 bbox 对版心平移（advance 盒含侧轴承/字距，
                # 真实字体墨比盒窄，按盒居中视觉偏左——2026-09-04 生产冒烟实测偏 6.4mm）。
                # 墨宽 ≥ 行宽（长词硬断撑满）不平移——居中不得把墨推出 §5 限幅
                ink = [pt for s in page.strokes[stroke_base:] for pt in s.points]
                if ink:
                    xs = [p[0] for p in ink]
                    ink_lo, ink_hi = min(xs), max(xs)
                    if ink_hi - ink_lo < layout.text_width_mm:
                        offset = (layout.margin_left_mm + layout.text_width_mm / 2.0) - (ink_lo + ink_hi) / 2.0
                        offset = min(offset, layout.margin_left_mm + layout.text_width_mm - ink_hi)
                        offset = max(offset, layout.margin_left_mm - ink_lo)
                        if offset:
                            for s in page.strokes[stroke_base:]:
                                s.points = [(px + offset, py) for px, py in s.points]
            page.line_count += heading_rows
            line_tokens = []
            line_width = 0.0
            line_start_x = layout.margin_left_mm
            if page.line_count >= max_lines_per_page:
                return False
            y_baseline = (
                layout.margin_top_mm
                + metrics.baseline_to_top * base_scale
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
            # 长词硬断：单 token（长 URL/英文串）宽度超整行时逐字切分——旧行为
            # 整词独占一行把墨迹顶出 §5 页限被 validate_gcode 整单拒绝
            # （2026-09-04 实测 60 个 x 墨至 288mm）。必须先切再算 overflow：
            # 否则未断的整条长尾先把单字行头冲成「每字一行」的病态排版
            if len(tok) > 1 and adv > layout.text_width_mm:
                tokens.insert(i + 1, tok[1:])
                tok = tok[0]
                adv = token_advance_mm(tok)
            # 拉丁→CJK 边界补偿与 flush_line 同口径计入测量：行末字符是拉丁/数字
            # 且本 token 以 CJK 起笔时，落墨会在拉丁槽后追加 cjk_left_overhang 避让
            # （「1932年」2→年 纸面相撞 1.94mm 的修复）。测量若不算这笔，满行尾
            # 交界的折行点比真实墨位窄 cjk_left_overhang×scale，行宽判决与墨迹错位。
            # tokenize 保证拉丁连字/CJK 单字各自成元，边界只出现在 token 交界。
            prev_ch = line_tokens[-1][-1] if line_tokens else ""
            if prev_ch.isascii() and prev_ch.isalnum() and _is_cjk(tok[0]):
                adv += metrics.cjk_left_overhang * scale
            overflow = line_width + adv > layout.text_width_mm and line_tokens
            # 避头：闭标点不置行首、并入当前行末——但并入后墨迹不得越 §5 页限
            # （190mm 硬线，竖版撰写页宽）：连续闭标点（。」/！」在中文常见）在满行会把右缘顶出限幅，
            # 被 validate_gcode 整单拒绝（2026-09-04 测试实锤：无上限时墨至 288mm）。
            # 超限则避头让位、照常折行——标点行首是外观妥协，越界是整篇写不出。
            merge_into_line = (
                tok in _NO_LINE_START
                and layout.margin_left_mm + line_width + adv <= _PAGE_WIDTH_MM
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
                            # 截断边界上 carried 开标点随未排文本一起丢弃——
                            # truncated=True 已如实上报，不为半个标点单独续页
                            truncated = True
                            break
                        new_page()
                        y_baseline = layout.margin_top_mm + metrics.baseline_to_top * base_scale
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
        # 标题段后空一行：仅当标题行仍在当前页（新页 line_count=0 不空——否则页首白送一行）；
        # 页底不加（下段反正切页）；+2 须给下段首行留位，否则幻象行把 line_count 顶到上限，
        # 下段首行 y 会算出页外
        if is_heading and 0 < page.line_count and page.line_count + 2 <= max_lines_per_page:
            page.line_count += 1
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
        f'<svg xmlns="http://www.w3.org/2000/svg" width="190mm" height="277mm" '
        f'viewBox="0 0 190 277">'
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


def _read_font_bytes(path: str) -> bytes:
    """读字库文件字节；独立成函数让 OSError 归一收窄、测试可注入故障。"""
    with open(path, "rb") as fh:
        return fh.read()


# 默认字库偏好（2026-09-06 用户拍板「手写感」）：优先 blood8 真迹 xiongzai 变体
# （8625 字、工整行楷）；文件缺席/损坏时回落「目录内字数最多」（2026-09-04 指令）。
_DEFAULT_FONT_PREFERENCE = ("blood8真迹_xiongzai",)


def load_font(fonts_dir: str, name: str | None = None) -> GfontFile:
    """按显示名或文件名词解析字库；name 为空时按 _DEFAULT_FONT_PREFERENCE 优先、
    否则取目录中字数最多的字库。"""
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
    # 选中即持有所解析对象直接返回：偏好/字数两路此前在扫描时解析一次、
    # 返回前又整份重解析（多 MB 字库每请求白付 1 次完整解密+ZIP 遍历）
    chosen_font: GfontFile | None = None
    if name:
        for path in candidates:
            if name in os.path.basename(path):
                chosen = path
                break
        if chosen is None:
            raise WriteTextError(f"找不到字库「{name}」（目录内 {len(candidates)} 个）")
    else:
        # 先按偏好名单命中（偏好文件须能正常解析，坏文件不进）
        for preferred in _DEFAULT_FONT_PREFERENCE:
            for path in candidates:
                if preferred in os.path.basename(path):
                    try:
                        chosen_font = GfontFile.parse(_read_font_bytes(path))
                    except (GfontError, OSError):
                        continue
                    chosen = path
                    break
            if chosen is not None:
                break
    if chosen is None and not name:
        best_count = -1
        for path in candidates:
            try:
                font = GfontFile.parse(_read_font_bytes(path))
            except (GfontError, OSError):
                # 坏文件/读失败跳过——目录扫描要尽力找出可用字库
                continue
            if font.meta.declared_count > best_count:
                best_count = font.meta.declared_count
                chosen = path
                chosen_font = font
        if chosen is None:
            raise WriteTextError("目录内无可用字库")

    if chosen_font is not None:
        return chosen_font
    try:
        return GfontFile.parse(_read_font_bytes(chosen))
    except (GfontError, OSError) as exc:
        raise WriteTextError(f"字库读取或解析失败：{os.path.basename(chosen)}：{exc}") from exc
