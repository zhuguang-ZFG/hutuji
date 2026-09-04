"""doc_ingest：Office/CSV/TXT 解析与防滥用闸门（stdlib ZIP/XML，无第三方依赖）。

合成夹具全部内联构造（zipfile 写字节），不依赖机器上的真实文档。
"""
from __future__ import annotations

import io
import zipfile

import pytest

from hutuji_mcp import doc_ingest
from hutuji_mcp.doc_ingest import DocIngestError, parse_document, sniff_doc_kind

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PR_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _zip(parts: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _docx(paragraphs_xml: str) -> bytes:
    return _zip(
        {
            "word/document.xml": (
                f'<w:document xmlns:w="{_W_NS}"><w:body>{paragraphs_xml}</w:body></w:document>'
            )
        }
    )


def _xlsx(
    sheet_xml: str,
    *,
    sheet_name: str = "成绩",
    sheet_state: str = "",
    extra_sheets: dict[str, str] | None = None,
) -> bytes:
    sheets = [
        f'<sheet name="{sheet_name}" sheetId="1" r:id="rId1"{sheet_state}/>',
    ]
    parts: dict[str, str] = {}
    rels = ['<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>']
    for i, (name, xml) in enumerate((extra_sheets or {}).items(), start=2):
        sheets.append(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>')
        rels.append(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>')
        parts[f"xl/worksheets/sheet{i}.xml"] = xml
    parts.update(
        {
            "xl/workbook.xml": (
                f'<workbook xmlns="{_S_NS}" xmlns:r="{_R_NS}">'
                f"<sheets>{''.join(sheets)}</sheets></workbook>"
            ),
            "xl/_rels/workbook.xml.rels": (
                f'<Relationships xmlns="{_PR_NS}">{"".join(rels)}</Relationships>'
            ),
            "xl/worksheets/sheet1.xml": sheet_xml,
        }
    )
    return _zip(parts)


def _sheet_xml(rows_xml: str, *, cols_xml: str = "", merge_xml: str = "") -> str:
    return (
        f'<worksheet xmlns="{_S_NS}">{cols_xml}'
        f"<sheetData>{rows_xml}</sheetData>{merge_xml}</worksheet>"
    )


def _pptx(slides: list[str]) -> bytes:
    sld_ids = "".join(
        f'<p:sldId id="{255 + i}" r:id="rId{i}"/>' for i in range(1, len(slides) + 1)
    )
    rels = "".join(
        f'<Relationship Id="rId{i}" Target="slides/slide{i}.xml"/>'
        for i in range(1, len(slides) + 1)
    )
    parts = {
        "ppt/presentation.xml": (
            f'<p:presentation xmlns:p="{_P_NS}" xmlns:r="{_R_NS}">'
            f"<p:sldIdLst>{sld_ids}</p:sldIdLst></p:presentation>"
        ),
        "ppt/_rels/presentation.xml.rels": (
            f'<Relationships xmlns="{_PR_NS}">{rels}</Relationships>'
        ),
    }
    for i, text in enumerate(slides, start=1):
        parts[f"ppt/slides/slide{i}.xml"] = (
            f'<p:sld xmlns:p="{_P_NS}" xmlns:a="{_A_NS}"><p:cSld><p:spTree>'
            f'<p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody>'
            f"</p:sp></p:spTree></p:cSld></p:sld>"
        )
    return _zip(parts)


# ---------------------------------------------------------------- 嗅探


def test_sniff_office_by_ext_and_marker():
    assert sniff_doc_kind(_docx("<w:p/>"), "a.docx", "") == "docx"
    assert sniff_doc_kind(_xlsx(_sheet_xml("")), "a.xlsx", "") == "xlsx"
    assert sniff_doc_kind(_pptx(["标题"]), "a.pptx", "") == "pptx"


def test_sniff_office_by_content_type_without_ext():
    ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert sniff_doc_kind(_xlsx(_sheet_xml("")), "blob", ct) == "xlsx"


def test_sniff_rejects_marker_mismatch():
    with pytest.raises(DocIngestError, match="不符"):
        sniff_doc_kind(_xlsx(_sheet_xml("")), "a.docx", "")


def test_sniff_txt_and_csv():
    assert sniff_doc_kind("你好".encode(), "a.txt", "") == "txt"
    assert sniff_doc_kind("a,b\n1,2".encode(), "a.csv", "text/csv") == "csv"


def test_sniff_unrelated_returns_none():
    assert sniff_doc_kind(b"\x00\x01\x02\x03" * 8, "a.bin", "application/octet-stream") is None
    # ZIP 但不是 Office（扩展名也没声明是）：不接盘、不报错，交回上层判不支持
    assert sniff_doc_kind(_zip({"random.txt": "hi"}), "a.zip", "") is None


def test_sniff_binary_masquerading_as_txt():
    with pytest.raises(DocIngestError, match="不是文本文件"):
        sniff_doc_kind(b"\x00\xff\x00\xff" * 16, "a.txt", "")


# ---------------------------------------------------------------- 防御矩阵


def test_reject_non_zip():
    with pytest.raises(DocIngestError, match="ZIP"):
        parse_document(b"definitely not a zip file", "docx")


def test_reject_doctype_entity():
    evil = _zip(
        {
            "word/document.xml": (
                '<!DOCTYPE x [<!ENTITY e "boom">]>'
                f'<w:document xmlns:w="{_W_NS}"><w:body><w:p><w:r><w:t>&e;</w:t></w:r></w:p>'
                "</w:body></w:document>"
            )
        }
    )
    with pytest.raises(DocIngestError, match="实体"):
        parse_document(evil, "docx")


def test_reject_too_many_entries(monkeypatch):
    monkeypatch.setattr(doc_ingest, "MAX_ZIP_ENTRIES", 3)
    big = _zip({f"f{i}.txt": "x" for i in range(5)} | {"word/document.xml": "<w/>"})
    with pytest.raises(DocIngestError, match="过多"):
        parse_document(big, "docx")


def test_reject_encrypted_zip():
    # writestr 会重写 flag_bits，手工把本地头+中央目录的加密位（bit 0）置 1
    raw = bytearray(_zip({"word/document.xml": "<w/>"}))
    loc = raw.find(b"PK\x03\x04")
    cen = raw.find(b"PK\x01\x02")
    assert loc >= 0 and cen >= 0
    raw[loc + 6] |= 0x1
    raw[cen + 8] |= 0x1
    with pytest.raises(DocIngestError, match="加密"):
        parse_document(bytes(raw), "docx")

def test_reject_text_over_limit():
    long_doc = _docx("".join(f"<w:p><w:r><w:t>第{i}段 {'字' * 50}</w:t></w:r></w:p>" for i in range(200)))
    with pytest.raises(DocIngestError, match="过多"):
        parse_document(long_doc, "docx")


def test_reject_broken_xml():
    bad = _zip({"word/document.xml": "<w:document><unclosed>"})
    with pytest.raises(DocIngestError, match="损坏"):
        parse_document(bad, "docx")


# ---------------------------------------------------------------- docx / txt


def test_docx_paragraphs_and_breaks():
    raw = _docx(
        "<w:p><w:r><w:t>第一段</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>带</w:t></w:r><w:r><w:tab/><w:t>制表</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>上半</w:t></w:r><w:r><w:br/></w:r><w:r><w:t>下半</w:t></w:r></w:p>"
        "<w:p/>"
    )
    doc = parse_document(raw, "docx")
    assert doc.mode == "article"
    assert doc.paragraphs == ["第一段", "带 制表", "上半", "下半"]


def test_docx_empty_rejected():
    with pytest.raises(DocIngestError, match="没有可写出"):
        parse_document(_docx("<w:p/><w:p><w:r><w:t>   </w:t></w:r></w:p>"), "docx")


def test_txt_utf8_gbk_and_empty():
    assert parse_document("第一行\n\n第二行".encode("utf-8"), "txt").paragraphs == ["第一行", "第二行"]
    assert parse_document("你好".encode("gbk"), "txt").paragraphs == ["你好"]
    with pytest.raises(DocIngestError, match="没有可写出"):
        parse_document("\n\n".encode(), "txt")
    with pytest.raises(DocIngestError, match="编码"):
        parse_document(b"\xff\xfe\x00\x01bad", "txt")


# ---------------------------------------------------------------- pptx


def test_pptx_slide_order_and_markers():
    raw = _pptx(["封面标题", "第二页要点"])
    doc = parse_document(raw, "pptx")
    assert doc.paragraphs == ["—— 第 1 页 ——", "封面标题", "—— 第 2 页 ——", "第二页要点"]


def test_pptx_single_slide_no_marker():
    doc = parse_document(_pptx(["唯一页"]), "pptx")
    assert doc.paragraphs == ["唯一页"]


def test_pptx_empty_rejected():
    raw = _zip(
        {
            "ppt/presentation.xml": (
                f'<p:presentation xmlns:p="{_P_NS}" xmlns:r="{_R_NS}"><p:sldIdLst/></p:presentation>'
            ),
            "ppt/_rels/presentation.xml.rels": f'<Relationships xmlns="{_PR_NS}"/>',
        }
    )
    with pytest.raises(DocIngestError, match="没有可写出"):
        parse_document(raw, "pptx")


# ---------------------------------------------------------------- xlsx / csv


def test_xlsx_shared_strings_and_numbers():
    raw = _xlsx(
        _sheet_xml(
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>95.5</v></c></row>'
        )
    )
    # sharedStrings 单独补进包
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as zin, zipfile.ZipFile(buf, "w") as zout:
        for name in zin.namelist():
            zout.writestr(name, zin.read(name))
        zout.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="{_S_NS}"><si><t>姓名</t></si><si><t>分数</t></si><si><t>张三</t></si></sst>',
        )
    doc = parse_document(buf.getvalue(), "xlsx")
    assert doc.mode == "tables"
    assert doc.tables[0].name == "成绩"
    assert doc.tables[0].rows == [["姓名", "分数"], ["张三", "95.5"]]


def test_xlsx_inline_strings_and_sparse_cells():
    raw = _xlsx(
        _sheet_xml(
            '<row r="1"><c r="A1" t="inlineStr"><is><t>品名</t></is></c>'
            '<c r="C1" t="inlineStr"><is><t>数量</t></is></c></row>'
        )
    )
    doc = parse_document(raw, "xlsx")
    # B 列空洞补空串，行尾空洞裁剪
    assert doc.tables[0].rows == [["品名", "", "数量"]]

def test_xlsx_col_widths_and_merges():
    raw = _xlsx(
        _sheet_xml(
            '<row r="1"><c r="A1" t="inlineStr"><is><t>汇总标题</t></is></c></row>'
            '<row r="2"><c r="A2" t="inlineStr"><is><t>甲</t></is></c>'
            '<c r="B2" t="inlineStr"><is><t>乙</t></is></c></row>',
            cols_xml='<cols><col min="1" max="1" width="20" customWidth="1"/></cols>',
            merge_xml='<mergeCells count="1"><mergeCell ref="A1:B1"/></mergeCells>',
        )
    )
    doc = parse_document(raw, "xlsx")
    table = doc.tables[0]
    assert table.col_widths == [20.0, doc_ingest._XLSX_DEFAULT_COL_CHARS]
    assert table.merges == [(0, 0, 1)]


def test_xlsx_hidden_sheet_skipped():
    visible = _sheet_xml('<row r="1"><c r="A1" t="inlineStr"><is><t>可见</t></is></c></row>')
    hidden = _sheet_xml('<row r="1"><c r="A1" t="inlineStr"><is><t>隐藏</t></is></c></row>')
    raw = _xlsx(visible, extra_sheets={"秘密": hidden.replace("<sheetData>", "<sheetData>")})
    # extra_sheets 的 state 默认 visible；单独构 hidden 场景：
    raw_hidden_only = _xlsx(hidden, sheet_state=' state="hidden"')
    with pytest.raises(DocIngestError, match="没有可写出"):
        parse_document(raw_hidden_only, "xlsx")
    doc = parse_document(raw, "xlsx")
    assert len(doc.tables) == 2


def test_xlsx_limits(monkeypatch):
    monkeypatch.setattr(doc_ingest, "MAX_TABLE_ROWS", 2)
    three_rows = _sheet_xml(
        "".join(
            f'<row r="{i}"><c r="A{i}" t="inlineStr"><is><t>r{i}</t></is></c></row>'
            for i in range(1, 4)
        )
    )
    with pytest.raises(DocIngestError, match="行数超限"):
        parse_document(_xlsx(three_rows), "xlsx")


def test_xlsx_too_many_sheets(monkeypatch):
    monkeypatch.setattr(doc_ingest, "MAX_TABLES", 1)
    sheet = _sheet_xml('<row r="1"><c r="A1"><v>1</v></c></row>')
    raw = _xlsx(sheet, extra_sheets={"S2": sheet})
    with pytest.raises(DocIngestError, match="工作表过多"):
        parse_document(raw, "xlsx")


def test_csv_dialects_and_encodings():
    doc = parse_document("姓名,分数\r\n张三,95\r\n李四,88".encode("gbk"), "csv")
    assert doc.tables[0].rows == [["姓名", "分数"], ["张三", "95"], ["李四", "88"]]
    semi = parse_document("a;b\n1;2".encode("utf-8-sig"), "csv")
    assert semi.tables[0].rows == [["a", "b"], ["1", "2"]]
    with pytest.raises(DocIngestError, match="没有可写出"):
        parse_document("\n,\n".encode(), "csv")


def test_csv_col_cap():
    wide = ",".join(["c"] * 30).encode()
    with pytest.raises(DocIngestError, match="列数超限"):
        parse_document(wide, "csv")


# ---------------------------------------------------------------- 编号与标题


def _numbering_xml(fmt: str = "decimal", lvl_text: str = "%1.") -> str:
    return (
        f'<w:numbering xmlns:w="{_W_NS}">'
        f'<w:abstractNum w:abstractNumId="2"><w:lvl w:ilvl="0">'
        f'<w:numFmt w:val="{fmt}"/><w:lvlText w:val="{lvl_text}"/></w:lvl>'
        f'<w:lvl w:ilvl="1"><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%2)"/></w:lvl>'
        f"</w:abstractNum>"
        f'<w:num w:numId="3"><w:abstractNumId w:val="2"/></w:num></w:numbering>'
    )


def _list_para(text: str, num_id: int = 3, ilvl: int = 0) -> str:
    return (
        f'<w:p><w:pPr><w:numPr><w:ilvl w:val="{ilvl}"/><w:numId w:val="{num_id}"/>'
        f"</w:numPr></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>"
    )


def test_docx_list_numbering_decimal():
    raw = _zip(
        {
            "word/numbering.xml": _numbering_xml(),
            "word/document.xml": (
                f'<w:document xmlns:w="{_W_NS}"><w:body>'
                + _list_para("甲") + _list_para("乙") + _list_para("丙")
                + "</w:body></w:document>"
            ),
        }
    )
    doc = parse_document(raw, "docx")
    assert doc.paragraphs == ["1. 甲", "2. 乙", "3. 丙"]


def test_docx_list_bullet_and_nested_reset():
    raw = _zip(
        {
            "word/numbering.xml": _numbering_xml(fmt="bullet", lvl_text="•"),
            "word/document.xml": (
                f'<w:document xmlns:w="{_W_NS}"><w:body>'
                + _list_para("甲") + _list_para("子项", ilvl=1) + _list_para("乙")
                + _list_para("又一子项", ilvl=1)
                + "</w:body></w:document>"
            ),
        }
    )
    doc = parse_document(raw, "docx")
    # bullet 用 lvlText 原样；子项用 lvl1 的 lowerLetter；浅级出现重置深级计数
    assert doc.paragraphs == ["• 甲", "a) 子项", "• 乙", "a) 又一子项"]


def test_docx_list_chinese_counting():
    raw = _zip(
        {
            "word/numbering.xml": _numbering_xml(fmt="chineseCountingThousand", lvl_text="%1、"),
            "word/document.xml": (
                f'<w:document xmlns:w="{_W_NS}"><w:body>'
                + _list_para("甲") + _list_para("乙")
                + "</w:body></w:document>"
            ),
        }
    )
    assert parse_document(raw, "docx").paragraphs == ["一、甲", "二、乙"]


def test_docx_list_without_numbering_part():
    # 无 numbering.xml：编号引用静默失效（不报错不瞎编）
    raw = _docx(_list_para("甲"))
    assert parse_document(raw, "docx").paragraphs == ["甲"]


def test_docx_heading_styles_detected():
    raw = _docx(
        '<w:p><w:pPr><w:pStyle w:val="1"/></w:pPr><w:r><w:t>标题一</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>正文</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>二级</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>题名</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="自定义蓝色"/></w:pPr><w:r><w:t>非标题</w:t></w:r></w:p>'
    )
    doc = parse_document(raw, "docx")
    assert doc.headings == {0, 2, 3}


def test_pptx_title_placeholder_marked_heading():
    P, A = _P_NS, _A_NS
    slide = (
        f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>'
        f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
        f'<p:txBody><a:p><a:r><a:t>页标题</a:t></a:r></a:p></p:txBody></p:sp>'
        f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>'
        f'<p:txBody><a:p><a:r><a:t>正文要点</a:t></a:r></a:p></p:txBody></p:sp>'
        f"</p:spTree></p:cSld></p:sld>"
    )
    raw = _zip(
        {
            "ppt/presentation.xml": (
                f'<p:presentation xmlns:p="{P}" xmlns:r="{_R_NS}">'
                f'<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>'
            ),
            "ppt/_rels/presentation.xml.rels": (
                f'<Relationships xmlns="{_PR_NS}">'
                f'<Relationship Id="rId1" Target="slides/slide1.xml"/></Relationships>'
            ),
            "ppt/slides/slide1.xml": slide,
        }
    )
    doc = parse_document(raw, "pptx")
    assert doc.paragraphs == ["页标题", "正文要点"]
    assert doc.headings == {0}


def test_txt_csv_have_no_headings():
    assert parse_document("标题\n正文".encode(), "txt").headings == set()
