"""LLM SVG 输出闸门回归：恶意内容必须在本地确定性拒绝。"""

import pytest

from hutuji_mcp.llm_svg import LlmError, _sanitize_description, _validate_svg


@pytest.mark.parametrize(
    ("svg", "reason"),
    [
        ("<svg><script>alert(1)</script><path d='M0 0' fill='none'/></svg>", "script"),
        ("<svg><path d='M0 0' fill='none' onload='alert(1)'/></svg>", "事件属性"),
        (
            "<svg xmlns:xlink='http://www.w3.org/1999/xlink'>"
            "<image xlink:href='http://evil/x'/></svg>",
            "image",
        ),
        ("<svg><rect x='0' y='0' width='10' height='10'/></svg>", "rect"),
        (
            "<svg><foreignObject><body xmlns='http://www.w3.org/1999/xhtml'>"
            "x</body></foreignObject></svg>",
            "foreignobject",
        ),
        ("<svg><path d='M0 0' fill='red'/></svg>", "fill"),
        (
            "<svg><path d='M0 0' fill='none' stroke='url(http://evil/x)'/></svg>",
            "URL",
        ),
    ],
)
def test_rejects_malicious_svg(svg: str, reason: str) -> None:
    with pytest.raises(LlmError, match=reason):
        _validate_svg(svg)


def test_accepts_single_line_svg() -> None:
    _validate_svg(
        '<svg viewBox="0 0 190 190">'
        '<path d="M10 10 L100 100" fill="none" stroke="black"/>'
        "</svg>"
    )


def test_description_is_bounded_and_control_chars_are_removed() -> None:
    cleaned = _sanitize_description("a\x00b\x1fc" + "图" * 300)
    assert "\x00" not in cleaned
    assert "\x1f" not in cleaned
    assert len(cleaned) == 200

