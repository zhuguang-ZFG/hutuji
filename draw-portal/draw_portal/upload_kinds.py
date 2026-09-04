from __future__ import annotations

from pathlib import Path

_RASTER_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/webp",
        "image/gif",
        "image/bmp",
        "image/x-ms-bmp",
    }
)


def detect_upload_kind(raw: bytes, content_type: str, filename: str) -> str:
    """返回 raster | svg | docx | xlsx | pptx | csv | txt。

    文档类（Office/CSV/TXT）的判别与防御在 hutuji_mcp.doc_ingest.sniff_doc_kind
    （ZIP 标志部件双向核对、DOCTYPE 拒收）；此处只分流。DocIngestError 是
    ValueError 子类，消息具体（如「加密文档不支持」），直接放行为 400 文案。
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    ext = Path(filename or "").suffix.lower()
    if ct in {"image/svg+xml"} or ext == ".svg":
        return "svg"
    head = raw[:512].lstrip()
    if ext == ".svg" or (
        head.startswith(b"<")
        and b"<svg" in head[:256].lower()
    ):
        return "svg"
    if ct in _RASTER_TYPES or ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        return "raster"
    from hutuji_mcp.doc_ingest import sniff_doc_kind

    doc_kind = sniff_doc_kind(raw, filename or "", ct)
    if doc_kind:
        return doc_kind
    raise ValueError("不支持的文件格式")
