"""article_llm：主题生成 / 润色契约。

LLM 网络层走 httpx.MockTransport（与 chat 同夹具），覆盖流式读取 + 回包字节上限。
"""
from __future__ import annotations

import asyncio
import types

import httpx
import pytest

from hutuji_mcp import article_llm
from hutuji_mcp.llm_svg import LlmError


def _patch_llm(monkeypatch: pytest.MonkeyPatch, *, key: str = "k") -> None:
    monkeypatch.setattr(
        article_llm,
        "settings",
        types.SimpleNamespace(
            llm_api_key=key,
            llm_base_url="https://example.test/v1",
            llm_model="m",
        ),
    )


def _mock_llm(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """把 article_llm 的 httpx.AsyncClient 换成 MockTransport 版（支持 stream）。"""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient  # 先存真类防递归

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(article_llm.httpx, "AsyncClient", _factory)


def _llm_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_generate_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "chat/completions" in str(request.url)
        return _llm_response("第一段。\n\n第二段。")

    _mock_llm(monkeypatch, handler)
    text = asyncio.run(article_llm.generate_or_polish(mode="generate", prompt="春天"))
    assert "第一段" in text


def test_polish_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch)
    with pytest.raises(LlmError, match="草稿"):
        asyncio.run(article_llm.generate_or_polish(mode="polish", text="  "))


def test_generate_rejects_svg_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch)
    _mock_llm(monkeypatch, lambda request: _llm_response("<svg><path/></svg>"))
    with pytest.raises(LlmError, match="不可用"):
        asyncio.run(article_llm.generate_or_polish(mode="generate", prompt="猫"))


def test_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, key="")
    with pytest.raises(LlmError, match="未就绪"):
        asyncio.run(article_llm.generate_or_polish(mode="generate", prompt="猫"))


def test_oversized_response_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2-02 回归：故障/被接管上游超大回包必须在读取阶段中断，不整段缓冲。

    正文膨胀到远超 ARTICLE_LLM_MAX_RESPONSE_BYTES，断言收敛为 LlmError 大小上限，
    而非 OOM 或正常返回。旧的非流式 resp.json() 实现无字节闸、此断言必红。
    """
    _patch_llm(monkeypatch)
    huge = "好" * (article_llm.ARTICLE_LLM_MAX_RESPONSE_BYTES // 2 + 10000)
    _mock_llm(monkeypatch, lambda request: _llm_response(huge))
    with pytest.raises(LlmError, match="大小上限"):
        asyncio.run(article_llm.generate_or_polish(mode="generate", prompt="猫"))
