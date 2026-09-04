import pytest

from draw_portal.upload_kinds import detect_upload_kind


def test_detect_svg():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'
    assert detect_upload_kind(svg, "image/svg+xml", "a.svg") == "svg"


def test_detect_png():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    assert detect_upload_kind(png, "image/png", "a.png") == "raster"


def test_detect_webp_by_type():
    raw = b"RIFFxxxxWEBP" + b"\x00" * 20
    assert detect_upload_kind(raw, "image/webp", "a.webp") == "raster"


def test_unsupported():
    # 真·不支持：无扩展名 + octet-stream + 非 ZIP 非文本
    with pytest.raises(ValueError):
        detect_upload_kind(b"\x00\x01\x02\x03" + b"\xff" * 32, "application/octet-stream", "a.bin")


def _ooxml(marker: str) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(marker, "<x/>")
    return buf.getvalue()


def test_detect_docx_txt_csv():
    assert detect_upload_kind(_ooxml("word/document.xml"), "application/octet-stream", "a.docx") == "docx"
    assert detect_upload_kind(_ooxml("xl/workbook.xml"), "", "a.xlsx") == "xlsx"
    assert detect_upload_kind(_ooxml("ppt/presentation.xml"), "", "a.pptx") == "pptx"
    assert detect_upload_kind("姓名,分数\r\n张三,95".encode("gbk"), "text/csv", "a.csv") == "csv"
    assert detect_upload_kind("你好世界".encode("utf-8"), "text/plain", "a.txt") == "txt"


def test_doc_rejections_are_specific():
    """伪装/损坏的文档给出具体原因（DocIngestError），不是笼统不支持。"""
    from hutuji_mcp.doc_ingest import DocIngestError

    with pytest.raises(DocIngestError, match="ZIP"):
        detect_upload_kind(b"not a zip at all", "application/octet-stream", "a.docx")
    with pytest.raises(DocIngestError, match="不符"):
        detect_upload_kind(_ooxml("xl/workbook.xml"), "application/octet-stream", "a.docx")
