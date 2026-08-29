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
_SVG_TYPES = frozenset({"image/svg+xml", "text/plain", "application/octet-stream"})


def detect_upload_kind(raw: bytes, content_type: str, filename: str) -> str:
    """返回 raster | svg。"""
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
    raise ValueError("不支持的文件格式")
