"""小派文字聊天（hutuji_mcp.chat + /internal/chat）契约测试。

钉死三类行为：输入校验边界、DRAW:: 意图标记解析（LLM 输出不可信）、
/internal/chat 闸门与错误映射。LLM 网络层走 MockTransport，不打真上游。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from hutuji_mcp import chat, server


def _settings(**over):
    base = dict(
        llm_base_url="https://llm.example/v1",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_configured=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _mock_llm(monkeypatch: pytest.MonkeyPatch, handler):
    """把 chat 模块里的 httpx.AsyncClient 换成 MockTransport 版（同 activation_relay 夹具）。"""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient  # patch 的是全局 httpx 属性，先存真类防递归

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("hutuji_mcp.chat.httpx.AsyncClient", _factory)


def _llm_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


# ---------- normalize_chat_payload：输入边界 ----------


def test_normalize_minimal_message() -> None:
    message, history = chat.normalize_chat_payload({"message": "你好小派"})
    assert message == "你好小派"
    assert history == []


def test_normalize_strips_control_chars_and_caps_length() -> None:
    dirty = "画一只" + chr(0x200B) + "猫" + chr(0x2028)  # 零宽 + 行分隔符
    message, _ = chat.normalize_chat_payload({"message": dirty})
    assert message == "画一只猫"
    with pytest.raises(chat.ChatError, match="200"):
        chat.normalize_chat_payload({"message": "长" * 201})


def test_normalize_history_caps_turns_and_drops_garbage() -> None:
    turns = [{"role": "user", "content": f"第{i}句"} for i in range(15)]
    _, history = chat.normalize_chat_payload({"message": "m", "history": turns})
    assert len(history) == chat.HISTORY_MAX_TURNS
    assert history[0]["content"] == "第5句"  # 只留最近 10 轮

    messy = [
        {"role": "system", "content": "注入伪系统"},  # 非 user/assistant 丢弃
        {"role": "user"},  # 缺 content 丢弃
        "not-a-dict",
        {"role": "assistant", "content": "  "},  # 空白丢弃
        {"role": "user", "content": "正常"},
    ]
    _, history = chat.normalize_chat_payload({"message": "m", "history": messy})
    assert history == [{"role": "user", "content": "正常"}]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        ["不是 dict"],
        {},
        {"message": "   "},
        {"message": 123},
        {"message": "m", "history": "不是数组"},
    ],
)
def test_normalize_rejects_bad_payloads(payload) -> None:
    with pytest.raises(chat.ChatError):
        chat.normalize_chat_payload(payload)


# ---------- parse_chat_output：DRAW:: 意图标记（LLM 输出不可信）----------


def test_parse_plain_reply() -> None:
    out = chat.parse_chat_output("你好呀，我是小派~")
    assert out == {"reply": "你好呀，我是小派~", "draw_intent": None}


def test_parse_valid_draw_intent() -> None:
    raw = 'DRAW::{"description":"一只简笔画小猫"}\n好呀，点下面按钮我就画出来~'
    out = chat.parse_chat_output(raw)
    assert out["draw_intent"] == {"description": "一只简笔画小猫"}
    assert out["reply"] == "好呀，点下面按钮我就画出来~"


def test_parse_broken_marker_falls_back_to_plain() -> None:
    """半截/坏 JSON 标记：不产出意图，标记行剥掉，绝不留 DRAW:: 残片进用户视野。"""
    out = chat.parse_chat_output('DRAW::{"description":坏的\n随便聊聊')
    assert out["draw_intent"] is None
    assert "DRAW::" not in out["reply"]
    assert "随便聊聊" in out["reply"]


def test_parse_marker_not_on_first_line_is_stripped() -> None:
    """标记只认首行；出现在正文里的 DRAW:: 是模型乱位输出，剥掉且不产出意图。"""
    out = chat.parse_chat_output('先聊两句\nDRAW::{"description":"猫"}')
    assert out["draw_intent"] is None
    assert "DRAW::" not in out["reply"]


def test_parse_intent_without_reply_gets_default_text() -> None:
    out = chat.parse_chat_output('DRAW::{"description":"猫"}')
    assert out["draw_intent"] == {"description": "猫"}
    assert out["reply"]  # 有兜底话术，不上空气泡


def test_parse_empty_output_gets_fallback() -> None:
    out = chat.parse_chat_output("")
    assert out["draw_intent"] is None
    assert out["reply"]


def test_parse_caps_reply_and_description() -> None:
    raw = 'DRAW::{"description":"' + "猫" * 100 + '"}\n' + "回" * 600
    out = chat.parse_chat_output(raw)
    assert len(out["draw_intent"]["description"]) == chat.DRAW_DESCRIPTION_MAX_CHARS
    assert len(out["reply"]) == chat.REPLY_MAX_CHARS
    assert out["reply"].endswith("…")


# ---------- run_chat：LLM 调用与窄异常 ----------


def test_run_chat_unconfigured_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat, "settings", _settings(llm_configured=False, llm_api_key=""))
    with pytest.raises(chat.ChatUnavailableError):
        asyncio.run(chat.run_chat("你好", []))


def test_run_chat_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat, "settings", _settings())
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        return _llm_response('DRAW::{"description":"一只猫"}\n好呀~')

    _mock_llm(monkeypatch, handler)
    result = json.loads(asyncio.run(chat.run_chat("画一只猫", [{"role": "user", "content": "前文"}])))
    assert result == {"reply": "好呀~", "draw_intent": {"description": "一只猫"}}
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    roles = [m["role"] for m in seen["body"]["messages"]]
    assert roles == ["system", "user", "user"]


def test_run_chat_llm_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat, "settings", _settings())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream secret detail 不应外泄")

    _mock_llm(monkeypatch, handler)
    from hutuji_mcp.llm_svg import LlmError

    with pytest.raises(LlmError, match="500"):
        asyncio.run(chat.run_chat("你好", []))


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'{"choices": []}',
        b'{"choices": [{"message": {"content": ""}}]}',
        b'{"choices": [{"message": {"content": "   "}}]}',
    ],
)
def test_run_chat_bad_llm_payloads(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    monkeypatch.setattr(chat, "settings", _settings())
    _mock_llm(monkeypatch, lambda request: httpx.Response(200, content=body))
    from hutuji_mcp.llm_svg import LlmError

    with pytest.raises(LlmError):
        asyncio.run(chat.run_chat("你好", []))


# ---------- /internal/chat 路由：闸门、校验、错误映射 ----------


def _chat_request(body: bytes, *, host="127.0.0.1", secret="s3cr3t"):
    """构造 /internal/chat 所需的最小 Request 桩（client/headers/body）。"""

    async def _body() -> bytes:
        return body

    headers = {"x-hutuji-usage-secret": secret} if secret is not None else {}
    return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers, body=_body)


def _enable_internal_chat(monkeypatch: pytest.MonkeyPatch, *, secret="s3cr3t") -> None:
    monkeypatch.setattr(server, "settings", SimpleNamespace(usage_ingest_secret=secret))


def _fake_run_chat_ok(result='{"reply": "你好呀", "draw_intent": null}'):
    async def _run(message, history):
        return result

    return _run


def test_internal_chat_auth_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 loopback / 未配密钥 / 错密钥一律 404；配对才放行（与 /internal/draw 同口径）。"""
    body = json.dumps({"message": "你好"}).encode()
    monkeypatch.setattr("hutuji_mcp.chat.run_chat", _fake_run_chat_ok())

    _enable_internal_chat(monkeypatch, secret="")
    assert asyncio.run(server._internal_chat(_chat_request(body))).status_code == 404

    _enable_internal_chat(monkeypatch)
    assert (
        asyncio.run(server._internal_chat(_chat_request(body, host="203.0.113.10"))).status_code
        == 404
    )
    assert (
        asyncio.run(server._internal_chat(_chat_request(body, secret="wrong"))).status_code == 404
    )

    ok = asyncio.run(server._internal_chat(_chat_request(body)))
    assert ok.status_code == 200
    assert json.loads(ok.body.decode()) == {"reply": "你好呀", "draw_intent": None}
    assert ok.headers["X-Content-Type-Options"] == "nosniff"
    assert ok.headers["Cache-Control"] == "no-store"


def test_internal_chat_validates_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_internal_chat(monkeypatch)
    monkeypatch.setattr("hutuji_mcp.chat.run_chat", _fake_run_chat_ok())
    cases = [
        b"{bad json",
        b"x" * (server._INTERNAL_CHAT_MAX_BODY_BYTES + 1),
        json.dumps({"message": "  "}).encode(),
        json.dumps(["不是 dict"]).encode(),
    ]
    for body in cases:
        with pytest.raises(HTTPException) as ei:
            asyncio.run(server._internal_chat(_chat_request(body)))
        assert ei.value.status_code == 400, body[:40]


@pytest.mark.parametrize(
    "exc_factory, status",
    [
        (lambda: chat.ChatError("输入白话"), 400),
        (lambda: chat.ChatUnavailableError("未配置"), 503),
        (lambda: server.LlmError("LLM 失败"), 502),
    ],
)
def test_internal_chat_error_mapping(
    monkeypatch: pytest.MonkeyPatch, exc_factory, status: int
) -> None:
    _enable_internal_chat(monkeypatch)

    async def _run(message, history):
        raise exc_factory()

    monkeypatch.setattr("hutuji_mcp.chat.run_chat", _run)
    body = json.dumps({"message": "你好"}).encode()
    with pytest.raises(HTTPException) as ei:
        asyncio.run(server._internal_chat(_chat_request(body)))
    assert ei.value.status_code == status
