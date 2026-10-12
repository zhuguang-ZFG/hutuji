"""Office/文本文档解析 → 排版链输入（portal 文档导入的解析半层）。

OOXML 三件套（docx/xlsx/pptx）就是 ZIP+XML，全部 stdlib（zipfile + ElementTree +
csv）解析，**不引第三方依赖**（本仓无 pyproject，依赖收紧是显式纪律）。

安全口径与 llm_svg 的 SVG 闸门同族——先限量/prefilter 再解析：
- ZIP：条目数 / 单条目 / 加密三项闸；只按白名单部件名读取（从不遍历落盘，
  目录穿越天然不可达）；流式读 + 上限截断，不信用中央目录的声明大小。
- XML：解析前字节级拒 `<!DOCTYPE` / `<!ENTITY`——ElementTree 不取外部实体，
  但内部子集照常展开（billion laughs），prefilter 一刀切两种都死。
- 内容量：总字符 / 行 / 列 / 单元格长全有上界，超限报错而非静默截断
  （截断会悄丢用户内容；write_text 的 max_pages 是最后一道）。

显式取舍（v1 不做，勿当 bug 修）：
- Word 内嵌表格只按段落顺序抽文字，不画框线（画表格走 Excel/CSV 路径）。
- xlsx 数字/日期按原始字符串写出，不解析样式表（无 number format）。
- pptx 只抽文字（每页若干段落），不还原图形/图表/图片。
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# ---- 上限（全是防资源耗尽的结构闸，不是业务限制）----
MAX_ZIP_ENTRIES = 256
MAX_MEMBER_BYTES = 32 * 1024 * 1024  # 单部件解压后上限
MAX_TEXT_CHARS = 5000  # 与 server._WRITE_TEXT_MAX_CHARS 同口径
MAX_TABLES = 8  # xlsx 工作表数上限
MAX_TABLE_ROWS = 500
MAX_TABLE_COLS = 24
MAX_CELL_CHARS = 40
MAX_MERGE_CELLS = 2048  # mergeCell 条数上限：封堵 O(merges×rows) 展开 DoS（32MiB 部件可塞 ~1.2M 条）
MAX_COL_SPECS = 2048  # <col> 条数上限：与 MAX_MERGE_CELLS 同族，封堵 col_widths 的 O(ncols×specs) 空转

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_DOC_KINDS = ("docx", "xlsx", "pptx", "csv", "txt")

# 扩展名 / Content-Type → (kind, ZIP 内标志部件)。标志部件在场才算该格式
# 成立——只信扩展名等于把「改名伪装」的判别权交给上传者。
_OFFICE_MARKERS = {
    "docx": "word/document.xml",
    "xlsx": "xl/workbook.xml",
    "pptx": "ppt/presentation.xml",
}
_EXT_TO_KIND = {
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
    ".csv": "csv",
    ".txt": "txt",
}
_CT_TO_KIND = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/csv": "csv",
}


class DocIngestError(ValueError):
    """文档解析/结构拒绝——继承 ValueError 保持 portal 上传链的 400 语义。"""


@dataclass
class ParsedTable:
    name: str
    rows: list[list[str]]
    # 文件自报列宽（Excel 字符宽单位，<col width=…>；无 customWidth 的列取默认宽）。
    # None = 文件没给（CSV 等），排版层回落内容测量。行高不采用文件值：
    # 手写单线字有硬字号地板，行高一律按内容折行自排（与奎享 PageInfo 适配同理）。
    col_widths: list[float] | None = None
    # 横向合并格（row, col1, col2 闭区间，0 基）。纵向合并 v1 只保留左上内容、
    # 框线按未合并画（奎享全量支持，见 docs/kxnx/findings/25；纵向要跨行等高，
    # 与内容自排行高耦合，代价不值）。
    merges: list[tuple[int, int, int]] = field(default_factory=list)


@dataclass
class ParsedDoc:
    kind: str  # docx | xlsx | pptx | csv | txt
    mode: str  # "article" | "tables"
    paragraphs: list[str] = field(default_factory=list)  # article 模式
    # 标题段下标（paragraphs 的 0 基索引）：docx 的 w:pStyle（Heading*/标题*/Title）
    # 与 pptx 的 title/ctrTitle 占位符检出；排版层对标题段免段首缩进 + 段前后加距。
    headings: set[int] = field(default_factory=set)
    tables: list[ParsedTable] = field(default_factory=list)  # tables 模式
    total_chars: int = 0


# ---------------------------------------------------------------- 低层防线


def _open_ooxml(raw: bytes) -> zipfile.ZipFile:
    if not raw[:4].startswith(b"PK"):
        raise DocIngestError("不是有效的 Office 文档（缺少 ZIP 头）")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise DocIngestError("Office 文档 ZIP 结构损坏") from None
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        zf.close()
        raise DocIngestError(f"文档内部文件过多（上限 {MAX_ZIP_ENTRIES}）")
    for info in infos:
        if info.flag_bits & 0x1:
            zf.close()
            raise DocIngestError("加密文档不支持")
    return zf

def _read_part(zf: zipfile.ZipFile, name: str) -> bytes:
    """流式读部件并截上限——不信用中央目录声明的 file_size（zip64 可伪造）。"""
    try:
        with zf.open(name) as fp:
            data = fp.read(MAX_MEMBER_BYTES + 1)
    except KeyError:
        raise DocIngestError("文档缺少必要部件") from None
    except zipfile.BadZipFile:
        raise DocIngestError("文档部件损坏") from None
    if len(data) > MAX_MEMBER_BYTES:
        raise DocIngestError("文档部件过大")
    return data


def _parse_xml_part(zf: zipfile.ZipFile, name: str) -> ET.Element:
    data = _read_part(zf, name)
    # billion laughs / 外部实体一刀切：ElementTree 本身不取外部实体，但会展开
    # 内部子集；两种声明一起拒，解析面只剩纯元素树。
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise DocIngestError("文档含 XML 实体声明，拒绝解析")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise DocIngestError("文档 XML 结构损坏") from None


def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise DocIngestError("无法识别文本编码（仅支持 UTF-8 / GBK）")


def _check_total_chars(total: int) -> None:
    if total > MAX_TEXT_CHARS:
        raise DocIngestError(f"文档文字过多（上限 {MAX_TEXT_CHARS} 字符）")


def _clip_cell(text: str) -> str:
    text = text.strip()
    if len(text) > MAX_CELL_CHARS:
        return text[: MAX_CELL_CHARS - 1] + "…"
    return text


# ---------------------------------------------------------------- 格式嗅探


def sniff_doc_kind(raw: bytes, filename: str, content_type: str) -> str | None:
    """返回 docx|xlsx|pptx|csv|txt；不像文档返回 None，像但损坏抛 DocIngestError。

    Office 类型必须「扩展名/CT 声明」与「ZIP 内标志部件」双向相符——防改名伪装。
    """
    ext = ""
    if filename:
        m = re.search(r"\.[A-Za-z0-9]{1,8}$", filename)
        ext = m.group(0).lower() if m else ""
    ct = (content_type or "").split(";")[0].strip().lower()

    kind = _EXT_TO_KIND.get(ext) or _CT_TO_KIND.get(ct)
    if kind in _OFFICE_MARKERS:
        if ext and ext in _EXT_TO_KIND and _EXT_TO_KIND[ext] != kind:
            # 扩展名与 CT 指向不同 Office 类型：以扩展名为准再验部件
            kind = _EXT_TO_KIND[ext]
        zf = _open_ooxml(raw)
        try:
            marker = _OFFICE_MARKERS[kind]
            if marker not in zf.namelist():
                raise DocIngestError("文件内容与格式声明不符")
        finally:
            zf.close()
        return kind
    if kind in ("csv", "txt"):
        if raw[:4].startswith(b"PK"):
            raise DocIngestError("文件内容与格式声明不符")
        if b"\x00" in raw[:512]:
            raise DocIngestError("不是文本文件")
        _decode_text(raw[:4096] if len(raw) > 4096 else raw)  # 抽样验码
        return kind
    return None


# ---------------------------------------------------------------- docx / txt


# 内置标题样式 styleId：英文 "Heading1"…/"Title"/"Subtitle"，中文 Word 是
# "1"…"9"/"标题"…（styleId 无空格）。宽匹配只认已知形态，防自定义样式误中。
_HEADING_STYLE_RE = re.compile(
    r"^(heading[1-9]|title|subtitle|[1-9]|标题[1-9]?|标题.*)$", re.IGNORECASE
)


def _is_heading_style(style_val: str) -> bool:
    return bool(_HEADING_STYLE_RE.match((style_val or "").strip()))


# ---------------------------------------------------------------- docx 编号


_CJK_DIGITS = "零一二三四五六七八九"


def _cjk_counter(n: int) -> str:
    """chineseCounting 系：1..99 中文计数，超出回退十进制串。"""
    if n <= 0 or n > 99:
        return str(n)
    if n < 10:
        return _CJK_DIGITS[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + _CJK_DIGITS[n % 10]
    return _CJK_DIGITS[n // 10] + "十" + (_CJK_DIGITS[n % 10] if n % 10 else "")


def _roman(n: int) -> str:
    out = ""
    for val, sym in ((10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= val:
            out += sym
            n -= val
    return out or "i"


def _fmt_counter(fmt: str, n: int) -> str:
    if fmt in ("chineseCountingThousand", "chineseCounting", "chineseLegalSimplified"):
        return _cjk_counter(n)
    if fmt == "lowerLetter":
        return chr(ord("a") + (n - 1) % 26)
    if fmt == "upperLetter":
        return chr(ord("A") + (n - 1) % 26)
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    return str(n)  # decimal 与未知格式同退十进制


def _docx_numbering(zf: zipfile.ZipFile) -> dict[int, dict[int, tuple[str, str]]]:
    """numbering.xml → numId → {ilvl: (numFmt, lvlText)}；部件缺失/损坏 → 空（不编号）。"""
    if "word/numbering.xml" not in zf.namelist():
        return {}
    root = _parse_xml_part(zf, "word/numbering.xml")
    abstracts: dict[str, dict[int, tuple[str, str]]] = {}
    for an in root.iter(f"{_W}abstractNum"):
        aid = an.get(f"{_W}abstractNumId")
        lvls: dict[int, tuple[str, str]] = {}
        for lvl in an.iter(f"{_W}lvl"):
            try:
                ilvl = int(lvl.get(f"{_W}ilvl") or 0)
            except ValueError:
                continue
            fmt_el = lvl.find(f"{_W}numFmt")
            txt_el = lvl.find(f"{_W}lvlText")
            lvls[ilvl] = (
                fmt_el.get(f"{_W}val") if fmt_el is not None else "decimal",
                txt_el.get(f"{_W}val") if txt_el is not None else "%1.",
            )
        if aid is not None:
            abstracts[aid] = lvls
    out: dict[int, dict[int, tuple[str, str]]] = {}
    for num in root.iter(f"{_W}num"):
        nid = num.get(f"{_W}numId")
        ref = num.find(f"{_W}abstractNumId")
        if nid is not None and ref is not None:
            lvls = abstracts.get(ref.get(f"{_W}val") or "")
            if lvls:
                try:
                    out[int(nid)] = lvls
                except ValueError:
                    continue
    return out


class _ListCounters:
    """逐段推进的编号状态：numId → 各级计数；浅级出现即重置更深级（Word 语义）。"""

    def __init__(self, numbering: dict[int, dict[int, tuple[str, str]]]):
        self._numbering = numbering
        self._counts: dict[int, dict[int, int]] = {}

    def prefix_for(self, p: ET.Element) -> str:
        ppr = p.find(f"{_W}pPr")
        if ppr is None:
            return ""
        numpr = ppr.find(f"{_W}numPr")
        if numpr is None:
            return ""
        nid_el = numpr.find(f"{_W}numId")
        try:
            nid = int(nid_el.get(f"{_W}val") or "") if nid_el is not None else 0
        except ValueError:
            return ""
        ilvl_el = numpr.find(f"{_W}ilvl")
        try:
            ilvl = int(ilvl_el.get(f"{_W}val") or 0) if ilvl_el is not None else 0
        except ValueError:
            ilvl = 0
        lvls = self._numbering.get(nid)
        if not lvls:
            return ""
        level = min(ilvl, 2)  # 更深层级按第 3 级格式处理（排版链无层级表达）
        fmt, lvl_text = lvls.get(level, lvls.get(0, ("decimal", "%1.")))
        counts = self._counts.setdefault(nid, {})
        counts[level] = counts.get(level, 0) + 1
        for deeper in [k for k in counts if k > level]:
            del counts[deeper]
        if fmt == "bullet":
            return (lvl_text or "•") + " "
        text = lvl_text
        for k in range(1, 10):
            token = f"%{k}"
            if token in text:
                sub_fmt = lvls.get(k - 1, (fmt,))[0]
                text = text.replace(token, _fmt_counter(sub_fmt, counts.get(k - 1, 0) or 1))
        # 编号与正文的间隔按书写惯例：ASCII 收尾（"1."）补空格，CJK 顿号等不补
        return text + (" " if text and text[-1].isascii() else "")


def _docx_paragraphs(root: ET.Element, zf: zipfile.ZipFile) -> tuple[list[str], set[int]]:
    """document.xml 全量 w:p 按文档序抽文字；w:br/w:cr 切段，w:tab 归一为空格。

    内嵌表格（w:tbl）的单元格段落会被 iter 按文档序一并抽出——网格结构丢失
    是 v1 显式取舍（见模块 docstring）。列表段前缀编号（numbering.xml 还原），
    标题段（pStyle 命中内置标题样式）记入 headings 供排版层免缩进加距。
    """
    counters = _ListCounters(_docx_numbering(zf))
    paras: list[str] = []
    headings: set[int] = set()
    acc_chars = 0  # 在途累计：列表编号前缀（lowerRoman/upperRoman 为 O(k)/项）累积可达
    # O(N²) 字符，第 391 行的 post-hoc 校验要等整个 w:p 循环跑完才触发——攻击者用
    # 32MiB document.xml 塞满 roman 编号列表即可在校验前耗尽 CPU/内存。逐段落地即查，
    # 越过 MAX_TEXT_CHARS 立刻抛（与 pptx R4-01 同族），把放大收敛为 O(bail²)。

    def _emit(text: str, is_heading: bool) -> None:
        nonlocal acc_chars
        if text.strip():
            if is_heading:
                headings.add(len(paras))
            paras.append(text)
            acc_chars += len(text)
            _check_total_chars(acc_chars)

    for p in root.iter(f"{_W}p"):
        ppr = p.find(f"{_W}pPr")
        style = ""
        if ppr is not None:
            style_el = ppr.find(f"{_W}pStyle")
            style = style_el.get(f"{_W}val") if style_el is not None else ""
        is_heading = _is_heading_style(style or "")
        prefix = counters.prefix_for(p)
        buf: list[str] = []
        for node in p.iter():
            if node.tag == f"{_W}t":
                buf.append(node.text or "")
            elif node.tag == f"{_W}tab":
                buf.append(" ")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                _emit(prefix + "".join(buf) if prefix else "".join(buf), is_heading)
                prefix = ""  # 编号只加在段的首个物理行
                buf = []
        text = "".join(buf)
        _emit(prefix + text if prefix else text, is_heading)
    return paras, headings


def _parse_docx(raw: bytes) -> ParsedDoc:
    zf = _open_ooxml(raw)
    try:
        root = _parse_xml_part(zf, "word/document.xml")
        paras, headings = _docx_paragraphs(root, zf)
    finally:
        zf.close()
    if not paras:
        raise DocIngestError("文档没有可写出的文字")
    total = sum(len(p) for p in paras)
    _check_total_chars(total)
    return ParsedDoc(
        kind="docx", mode="article", paragraphs=paras, headings=headings, total_chars=total
    )


def _parse_txt(raw: bytes) -> ParsedDoc:
    text = _decode_text(raw)
    paras = [line.rstrip("\r") for line in text.split("\n") if line.strip()]
    if not paras:
        raise DocIngestError("文档没有可写出的文字")
    total = sum(len(p) for p in paras)
    _check_total_chars(total)
    return ParsedDoc(kind="txt", mode="article", paragraphs=paras, total_chars=total)


# ---------------------------------------------------------------- pptx


def _rels_map(zf: zipfile.ZipFile, rels_name: str) -> dict[str, str]:
    root = _parse_xml_part(zf, rels_name)
    out: dict[str, str] = {}
    for rel in root.iter(f"{_PR}Relationship"):
        rid = rel.get("Id")
        target = rel.get("Target") or ""
        if rid and target:
            out[rid] = target
    return out


def _join_part(base_dir: str, target: str) -> str:
    """OOXML rels target 规范化：绝对（/xl/...）按包根，相对按 base_dir。"""
    target = target.lstrip("/")
    if target.startswith(("xl/", "ppt/", "word/")):
        return target
    return f"{base_dir}/{target}" if base_dir else target


def _parse_pptx(raw: bytes) -> ParsedDoc:
    zf = _open_ooxml(raw)
    try:
        pres = _parse_xml_part(zf, "ppt/presentation.xml")
        rels = _rels_map(zf, "ppt/_rels/presentation.xml.rels")
        slides: list[list[tuple[str, bool]]] = []
        seen_targets: set[str] = set()
        acc_chars = 0  # 累计抽出字符数：边抽边卡 MAX_TEXT_CHARS，封堵 256 部件 × 32MiB
                       # 的聚合放大（post-hoc 卡太晚，R4，R2-01 同族）
        # 放映顺序以 presentation.xml 的 sldIdLst 为准（不是文件名数字序）
        for sld_id in pres.iter(f"{_P}sldId"):
            target = rels.get(sld_id.get(f"{_R}id") or "", "")
            if not target:
                continue
            # 同一 slide 部件被重复引用只解析一次：sldIdLst 可塞 ~数十万条
            # 全指向同一 rId，逐条 _parse_xml_part 会放大为 CPU DoS（R3，R2-01 同族）；
            # 去重后展开上界 = 不同部件数 ≤ MAX_ZIP_ENTRIES
            if target in seen_targets:
                continue
            seen_targets.add(target)
            slide = _parse_xml_part(zf, _join_part("ppt", target))
            paras: list[tuple[str, bool]] = []
            # 按形状遍历：占位符类型（p:ph@type=title/ctrTitle）在 sp 级，
            # 标题形状里的段落即标题——PPT 标题/正文层级由此保留
            for sp in slide.iter(f"{_P}sp"):
                ph = sp.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
                is_title = ph is not None and (ph.get("type") or "body") in ("title", "ctrTitle")
                for ap in sp.iter(f"{_A}p"):
                    text = "".join(t.text or "" for t in ap.iter(f"{_A}t")).strip()
                    if text:
                        paras.append((text, is_title))
                        acc_chars += len(text)
                        _check_total_chars(acc_chars)
            if paras:
                slides.append(paras)
    finally:
        zf.close()
    if not slides:
        raise DocIngestError("幻灯片没有可写出的文字")
    paragraphs: list[str] = []
    headings: set[int] = set()
    for i, paras in enumerate(slides):
        if len(slides) > 1:
            headings.add(len(paragraphs))
            paragraphs.append(f"—— 第 {i + 1} 页 ——")
        for text, is_title in paras:
            if is_title:
                headings.add(len(paragraphs))
            paragraphs.append(text)
    total = sum(len(p) for p in paragraphs)
    _check_total_chars(total)
    return ParsedDoc(
        kind="pptx", mode="article", paragraphs=paragraphs, headings=headings, total_chars=total
    )


# ---------------------------------------------------------------- xlsx / csv


def _col_index(ref: str) -> int:
    m = re.match(r"^([A-Za-z]+)", ref or "")
    if not m:
        return -1
    idx = 0
    for ch in m.group(1).upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = _parse_xml_part(zf, "xl/sharedStrings.xml")
    out: list[str] = []
    for si in root.iter(f"{_S}si"):
        out.append("".join(t.text or "" for t in si.iter(f"{_S}t")))
    return out


# Excel 默认列宽（字符单位）：<cols> 缺省时 Excel UI 的实际值
_XLSX_DEFAULT_COL_CHARS = 8.43
# <col min/max> 是 1 基闭区间且可指整列（min=1 max=16384），展开必须有界
_XLSX_COL_SPEC_SPAN = 64


def _merge_ref(ref: str) -> tuple[int, int, int, int] | None:
    """mergeCell ref（如 B2:D4）→ (r1, c1, r2, c2) 0 基闭区间。"""
    m = re.match(r"^([A-Za-z]+)(\d+)(?::([A-Za-z]+)(\d+))?$", (ref or "").strip())
    if not m:
        return None
    c1 = _col_index(m.group(1))
    r1 = int(m.group(2)) - 1
    c2 = _col_index(m.group(3)) if m.group(3) else c1
    r2 = int(m.group(4)) - 1 if m.group(4) else r1
    if c1 < 0 or c2 < 0 or r1 < 0 or r2 < 0 or c2 < c1 or r2 < r1:
        return None
    return (r1, c1, r2, c2)


def _xlsx_sheet(
    zf: zipfile.ZipFile, part: str, shared: list[str]
) -> tuple[list[list[str]], list[float] | None, list[tuple[int, int, int]]]:
    """工作表 → (行, 自报列宽|None, 横向合并 span 列表)。

    列宽取 <cols><col width=…>（字符单位，与奎享 getColumnWidthInPixels 同源——
    POI 也是用这个值换算像素）；文件没给任何 <cols> 时返回 None 交排版层内容测量。
    纵向合并按行拆成每行一条横向 span：左上内容只存一次，续行只省内部竖线。
    """
    root = _parse_xml_part(zf, part)
    default_w = _XLSX_DEFAULT_COL_CHARS
    sfmt = root.find(f"{_S}sheetFormatPr")
    if sfmt is not None:
        try:
            default_w = float(sfmt.get("defaultColWidth") or default_w)
        except ValueError:
            pass
    col_specs: list[tuple[int, int, float]] = []
    for col in root.iter(f"{_S}col"):
        try:
            lo = int(col.get("min") or "") - 1
            hi = int(col.get("max") or "") - 1
            w = float(col.get("width") or default_w)
        except ValueError:
            continue
        if lo < 0 or hi < lo:
            continue
        col_specs.append((lo, min(hi, lo + _XLSX_COL_SPEC_SPAN), w))
        if len(col_specs) > MAX_COL_SPECS:
            raise DocIngestError(f"列定义过多（上限 {MAX_COL_SPECS} 个）")

    raw_merges: list[tuple[int, int, int, int]] = []
    for mc in root.iter(f"{_S}mergeCell"):
        parsed = _merge_ref(mc.get("ref") or "")
        if parsed is not None:
            raw_merges.append(parsed)
            if len(raw_merges) > MAX_MERGE_CELLS:
                raise DocIngestError(f"合并单元格过多（上限 {MAX_MERGE_CELLS} 个）")

    rows: list[list[str]] = []
    orig_to_out: dict[int, int] = {}
    for row in root.iter(f"{_S}row"):
        try:
            orig_idx = int(row.get("r") or "") - 1
        except ValueError:
            orig_idx = len(orig_to_out)  # 无 r 属性时按出现序（规范文件恒有）
        cells: dict[int, str] = {}
        for c in row.iter(f"{_S}c"):
            col = _col_index(c.get("r") or "")
            if col < 0:
                col = len(cells)
            if col >= MAX_TABLE_COLS:
                raise DocIngestError(f"表格列数超限（上限 {MAX_TABLE_COLS} 列）")
            ctype = c.get("t") or ""
            v = c.find(f"{_S}v")
            if ctype == "s":
                try:
                    text = shared[int(v.text)] if v is not None and v.text else ""
                except (ValueError, IndexError):
                    text = ""
            elif ctype == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(f"{_S}t"))
            else:
                # 数字/日期按原始字符串（无样式表，v1 显式取舍）
                text = v.text if v is not None and v.text else ""
            cells[col] = _clip_cell(text)
        if cells and any(cells.values()):
            width = max(cells) + 1
            row_out = [cells.get(i, "") for i in range(width)]
            while row_out and not row_out[-1]:
                row_out.pop()
            orig_to_out[orig_idx] = len(rows)
            rows.append(row_out)
            if len(rows) > MAX_TABLE_ROWS:
                raise DocIngestError(f"表格行数超限（上限 {MAX_TABLE_ROWS} 行）")

    if not rows:
        return [], None, []
    ncols = max(len(r) for r in rows)
    col_widths: list[float] | None = None
    if col_specs:
        col_widths = []
        for c in range(ncols):
            w = default_w
            for lo, hi, spec_w in col_specs:
                if lo <= c <= hi:
                    w = spec_w
                    break
            col_widths.append(w)
    merges: list[tuple[int, int, int]] = []
    for r1, c1, r2, c2 in raw_merges:
        if c1 >= ncols:
            continue
        # mergeCell ref 行范围无上界（可指整列 A1:A1048576 甚至更大），绝不能对
        # 原始行号区间 range()——那是 O(ref 跨度) 空转、可被单个合并元素打成 DoS。
        # 改为与实际落盘行号（≤MAX_TABLE_ROWS）求交，迭代次数有界于真实行数。
        for orig_r, out_r in orig_to_out.items():
            if r1 <= orig_r <= r2:
                merges.append((out_r, c1, min(c2, ncols - 1)))
    return rows, col_widths, merges


def _parse_xlsx(raw: bytes) -> ParsedDoc:
    zf = _open_ooxml(raw)
    try:
        shared = _xlsx_shared_strings(zf)
        wb = _parse_xml_part(zf, "xl/workbook.xml")
        rels = _rels_map(zf, "xl/_rels/workbook.xml.rels")
        sheets = list(wb.iter(f"{_S}sheet"))
        if len(sheets) > MAX_TABLES:
            raise DocIngestError(f"工作表过多（上限 {MAX_TABLES} 个）")
        tables: list[ParsedTable] = []
        for i, sheet in enumerate(sheets):
            # 隐藏表跳过（与奎享 isSheetHidden 同口径：用户看不到的表不出货）
            if (sheet.get("state") or "visible") != "visible":
                continue
            name = (sheet.get("name") or f"Sheet{i + 1}").strip() or f"Sheet{i + 1}"
            target = rels.get(sheet.get(f"{_R}id") or "", "")
            if not target:
                continue
            rows, col_widths, merges = _xlsx_sheet(zf, _join_part("xl", target), shared)
            if rows:
                tables.append(ParsedTable(name=name, rows=rows, col_widths=col_widths, merges=merges))
    finally:
        zf.close()
    if not tables:
        raise DocIngestError("表格没有可写出的内容")
    total = sum(len(cell) for t in tables for r in t.rows for cell in r)
    _check_total_chars(total)
    return ParsedDoc(kind="xlsx", mode="tables", tables=tables, total_chars=total)


def _parse_csv(raw: bytes) -> ParsedDoc:
    text = _decode_text(raw)
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows: list[list[str]] = []
    for record in csv.reader(io.StringIO(text), dialect):
        cells = [_clip_cell(c) for c in record]
        if len(cells) > MAX_TABLE_COLS:
            raise DocIngestError(f"表格列数超限（上限 {MAX_TABLE_COLS} 列）")
        if any(cells):
            while cells and not cells[-1]:
                cells.pop()
            if cells:
                rows.append(cells)
                if len(rows) > MAX_TABLE_ROWS:
                    raise DocIngestError(f"表格行数超限（上限 {MAX_TABLE_ROWS} 行）")
    if not rows:
        raise DocIngestError("表格没有可写出的内容")
    total = sum(len(cell) for r in rows for cell in r)
    _check_total_chars(total)
    return ParsedDoc(kind="csv", mode="tables", tables=[ParsedTable(name="", rows=rows)], total_chars=total)


# ---------------------------------------------------------------- 调度


def parse_document(raw: bytes, kind: str) -> ParsedDoc:
    """按 sniff_doc_kind 的种类分发解析；未知种类抛 DocIngestError。"""
    if kind == "docx":
        return _parse_docx(raw)
    if kind == "xlsx":
        return _parse_xlsx(raw)
    if kind == "pptx":
        return _parse_pptx(raw)
    if kind == "csv":
        return _parse_csv(raw)
    if kind == "txt":
        return _parse_txt(raw)
    raise DocIngestError("不支持的文档格式")
