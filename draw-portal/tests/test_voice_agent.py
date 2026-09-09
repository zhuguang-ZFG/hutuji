"""voice_agent 契约。"""
from __future__ import annotations

import pytest

from draw_portal import voice_agent as va


def _configure_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUTUJI_LLM_API_KEY", "test-key")
    monkeypatch.setenv("HUTUJI_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("HUTUJI_LLM_MODEL", "test-model")


def test_not_configured_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HUTUJI_LLM_API_KEY", raising=False)
    monkeypatch.delenv("HUTUJI_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("HUTUJI_LLM_MODEL", raising=False)
    assert va.llm_configured() is False
    with pytest.raises(va.VoiceAgentError, match="对话未配置"):
        va.chat(
            session_id="s1",
            user_text="你好",
            device_mac="aa:bb",
            push_tool=lambda _n, _a: "",
        )


def test_empty_text_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_llm(monkeypatch)
    with pytest.raises(va.VoiceAgentError, match="内容为空"):
        va.chat(
            session_id="s1",
            user_text="   ",
            device_mac="aa:bb",
            push_tool=lambda _n, _a: "",
        )


def test_unbound_status_mentions_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_llm(monkeypatch)
    calls: list[tuple[str, dict]] = []

    def _push(name: str, args: dict) -> str:
        calls.append((name, args))
        return "{}"

    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "hutuji.status",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "请先绑定写字机后再查状态哦。",
                    }
                }
            ]
        },
    ]

    def fake_post_chat(*, url: str, headers: dict, payload: dict) -> dict:
        assert responses, "unexpected extra LLM call"
        return responses.pop(0)

    monkeypatch.setattr(va, "_post_chat", fake_post_chat)
    result = va.chat(
        session_id="unbound-session",
        user_text="写字机什么状态",
        device_mac=None,
        push_tool=_push,
    )
    assert not calls
    assert "绑定" in result["reply"] or any(
        "绑定" in trace.get("summary", "") for trace in result["tool_traces"]
    )


def test_allowlist_rejects_confirm_without_push(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_llm(monkeypatch)
    calls: list[tuple[str, dict]] = []

    def _push(name: str, args: dict) -> str:
        calls.append((name, args))
        return "ok"

    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_bad",
                                "type": "function",
                                "function": {
                                    "name": "hutuji.confirm",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "这个操作我这边不能直接确认哦。",
                    }
                }
            ]
        },
    ]

    def fake_post_chat(*, url: str, headers: dict, payload: dict) -> dict:
        return responses.pop(0)

    monkeypatch.setattr(va, "_post_chat", fake_post_chat)
    result = va.chat(
        session_id="allowlist-session",
        user_text="确认开始画",
        device_mac="aa:bb:cc:dd:ee:ff",
        push_tool=_push,
    )
    assert not calls
    assert any(
        trace["name"] == "hutuji.confirm" and "不允许" in trace["summary"]
        for trace in result["tool_traces"]
    )


def test_happy_path_plain_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_llm(monkeypatch)

    def fake_post_chat(*, url: str, headers: dict, payload: dict) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "你好",
                    }
                }
            ]
        }

    monkeypatch.setattr(va, "_post_chat", fake_post_chat)
    result = va.chat(
        session_id="happy-session",
        user_text="你好",
        device_mac="aa:bb:cc:dd:ee:ff",
        push_tool=lambda _n, _a: "",
    )
    assert result["reply"] == "你好"
    assert result["tool_traces"] == []
