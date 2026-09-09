"""article_llm：主题生成 / 润色契约。"""
from __future__ import annotations

import asyncio
import types

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


class _Resp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_generate_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            assert "chat/completions" in url
            assert json["messages"][0]["role"] == "system"
            return _Resp(
                200,
                {"choices": [{"message": {"content": "第一段。\n\n第二段。"}}]},
            )

    monkeypatch.setattr(article_llm.httpx, "AsyncClient", _Client)
    text = asyncio.run(article_llm.generate_or_polish(mode="generate", prompt="春天"))
    assert "第一段" in text


def test_polish_rejects_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch)
    with pytest.raises(LlmError, match="草稿"):
        asyncio.run(article_llm.generate_or_polish(mode="polish", text="  "))


def test_generate_rejects_svg_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            return _Resp(
                200,
                {"choices": [{"message": {"content": "<svg><path/></svg>"}}]},
            )

    monkeypatch.setattr(article_llm.httpx, "AsyncClient", _Client)
    with pytest.raises(LlmError, match="不可用"):
        asyncio.run(article_llm.generate_or_polish(mode="generate", prompt="猫"))


def test_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, key="")
    with pytest.raises(LlmError, match="未就绪"):
        asyncio.run(article_llm.generate_or_polish(mode="generate", prompt="猫"))
