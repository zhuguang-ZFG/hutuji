"""LLM SVG 输出闸门回归：恶意内容必须在本地确定性拒绝。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from hutuji_mcp import llm_svg
from hutuji_mcp.llm_svg import (
    LlmError,
    _extract_svg,
    _sanitize_description,
    _validate_svg,
)


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


def test_generate_svg_rejects_http_200_non_json(monkeypatch) -> None:  # noqa: ANN001
    """上游偶尔返回 200 HTML/纯文本时，应转成 LlmError，不得漏出 JSONDecodeError 500。"""
    class FakeResponse:
        status_code = 200
        text = "not json"

        def json(self):
            raise json.JSONDecodeError("Expecting value", self.text, 0)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        async def post(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return FakeResponse()

    monkeypatch.setattr(llm_svg, "settings", SimpleNamespace(
        llm_api_key="test-key",
        llm_base_url="https://example.invalid/v1",
        llm_model="test-model",
    ))
    monkeypatch.setattr(llm_svg.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    with pytest.raises(LlmError, match="不是合法 JSON"):
        asyncio.run(llm_svg.generate_svg("测试"))


# ---- DTD / 实体声明（2026-07-26 深度评审）----
# xml.etree 默认拒外部实体（file:// / http:// 都抛 ParseError），内部实体则会
# 递归展开。真实路径 _extract_svg 的正则从 <svg 起切片，DOCTYPE 落在根元素之前
# 会被切掉从而变成 undefined entity——即当前不可利用。这里把「拒 DTD」固化成
# 显式契约，不再依赖切片这一隐式行为。


def test_rejects_doctype_and_entity_declarations() -> None:
    svg = (
        "<!DOCTYPE svg [<!ENTITY a 'M10 10 L20 20'>]>"
        "<svg viewBox='0 0 190 190'>"
        "<path d='&a;' fill='none' stroke='black'/>"
        "</svg>"
    )
    with pytest.raises(LlmError, match="DTD"):
        _validate_svg(svg)


def test_external_entity_file_read_is_rejected() -> None:
    svg = (
        "<!DOCTYPE svg [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>"
        "<svg viewBox='0 0 190 190'>"
        "<path d='&xxe;' fill='none' stroke='black'/>"
        "</svg>"
    )
    with pytest.raises(LlmError):
        _validate_svg(svg)


def test_entity_expansion_is_not_reachable_via_extract() -> None:
    # 端到端形状：模型回复里带 DOCTYPE。_extract_svg 必须拒（无论靠切片还是靠 DTD 闸）。
    reply = (
        "这是你要的图：\n"
        "<!DOCTYPE svg [<!ENTITY a 'M0 0'><!ENTITY b '&a;&a;&a;'>]>"
        "<svg viewBox='0 0 190 190'>"
        "<path d='&b;' fill='none' stroke='black'/>"
        "</svg>"
    )
    with pytest.raises(LlmError):
        _extract_svg(reply)
