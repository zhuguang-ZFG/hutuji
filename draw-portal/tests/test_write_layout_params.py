import pytest
from draw_portal.write_layout_params import (
    WriteLayoutParamsError,
    parse_optional_float,
    resolve_article_layout,
)


def test_defaults():
    lay = resolve_article_layout()
    assert lay.size_mm == 12.0
    assert lay.line_spacing == 1.6


def test_custom_ok():
    lay = resolve_article_layout(size_mm=18.0, line_spacing=2.0)
    assert lay.size_mm == 18.0
    assert lay.line_spacing == 2.0
    assert lay.char_gap_mm == 0.8  # 不暴露、不改默认


@pytest.mark.parametrize("size", [1.9, 40.1])
def test_size_oob(size):
    with pytest.raises(WriteLayoutParamsError, match="字号越界"):
        resolve_article_layout(size_mm=size)


@pytest.mark.parametrize("ls", [0.9, 3.1])
def test_line_spacing_oob(ls):
    with pytest.raises(WriteLayoutParamsError, match="行距越界"):
        resolve_article_layout(line_spacing=ls)


def test_parse_optional_float():
    assert parse_optional_float(None) is None
    assert parse_optional_float("") is None
    assert parse_optional_float("12") == 12.0
    with pytest.raises(WriteLayoutParamsError, match="版式参数无效"):
        parse_optional_float("x")
