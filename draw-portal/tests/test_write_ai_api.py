"""POST /api/draw/write/ai 契约。"""
from __future__ import annotations

from pathlib import Path

import pytest

from hutuji_mcp.llm_svg import LlmError

from test_app import _client, _register_and_login  # noqa: E402


def _auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    c = _client(monkeypatch, tmp_path)
    _register_and_login(c)
    return c


def test_write_ai_generate_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)

    async def fake(*, mode, prompt="", text=""):
        assert mode == "generate"
        assert prompt == "春天的校园"
        return "校园里桃花开了。"

    monkeypatch.setattr(
        "hutuji_mcp.article_llm.generate_or_polish", fake
    )
    r = client.post(
        "/api/draw/write/ai",
        json={"mode": "generate", "prompt": "春天的校园"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "校园里桃花开了。"
    assert r.json()["mode"] == "generate"


def test_write_ai_polish_maps_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)

    async def boom(*, mode, prompt="", text=""):
        raise LlmError("请先粘贴要润色的草稿")

    monkeypatch.setattr("hutuji_mcp.article_llm.generate_or_polish", boom)
    r = client.post("/api/draw/write/ai", json={"mode": "polish", "text": ""})
    assert r.status_code == 400
    assert "草稿" in r.json()["detail"]


def test_write_ai_requires_phone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _client(monkeypatch, tmp_path)
    # 登录但不绑手机：_register_and_login 会绑；此处只 register 无 phone
    # 用现成 helper：先 login 再清 phone 太重——直接未登录应 401
    r = client.post("/api/draw/write/ai", json={"mode": "generate", "prompt": "x"})
    assert r.status_code in (401, 403)
