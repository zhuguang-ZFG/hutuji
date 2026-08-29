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
    with pytest.raises(ValueError):
        detect_upload_kind(b"hello world", "text/plain", "a.txt")
