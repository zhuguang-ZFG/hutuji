"""voice_jobs / voice_agent 画写工具契约。"""
from __future__ import annotations

import pytest

from draw_portal import voice_agent as va
from draw_portal.voice_jobs import VoicePreview, portal_tool_name


def test_portal_tool_names() -> None:
    assert portal_tool_name("voice.draw")
    assert portal_tool_name("voice.write")
    assert not portal_tool_name("hutuji.confirm")


def test_preview_as_dict() -> None:
    p = VoicePreview(
        kind="draw",
        job_id="j1",
        preview_url="https://example.test/a.png",
        gcode_url="https://example.test/a.gcode",
    )
    d = p.as_dict()
    assert d["kind"] == "draw"
    assert "pages_arg" not in d


def _configure_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUTUJI_LLM_API_KEY", "test-key")
    monkeypatch.setenv("HUTUJI_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("HUTUJI_LLM_MODEL", "test-model")


def test_voice_draw_tool_returns_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_llm(monkeypatch)
    portal_calls: list[tuple[str, dict]] = []

    def run_portal(name: str, args: dict):
        portal_calls.append((name, args))
        assert name == "voice.draw"
        return (
            "已推预览",
            {
                "kind": "draw",
                "job_id": "jid",
                "preview_url": "https://example.test/p.png",
                "gcode_url": "https://example.test/p.gcode",
            },
        )

    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "voice.draw",
                                    "arguments": '{"description":"一只猫"}',
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
                        "content": "画好了，请在小程序点开始画。",
                    }
                }
            ]
        },
    ]

    def fake_post(*, url: str, headers: dict, payload: dict) -> dict:
        return responses.pop(0)

    monkeypatch.setattr(va, "_post_chat", fake_post)
    out = va.chat(
        session_id="draw-sess",
        user_text="画一只猫",
        device_mac="aa:bb:cc",
        push_tool=lambda *_a: "",
        run_portal_tool=run_portal,
    )
    assert portal_calls and portal_calls[0][0] == "voice.draw"
    assert out["preview"]["preview_url"].endswith(".png")
    assert "开始画" in out["reply"] or out["preview"]


def test_confirm_still_not_in_llm_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_llm(monkeypatch)
    push_calls: list[str] = []

    def _push(name: str, args: dict) -> str:
        push_calls.append(name)
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
                                "id": "c1",
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
                {"message": {"role": "assistant", "content": "好的"}}
            ]
        },
    ]
    monkeypatch.setattr(va, "_post_chat", lambda **kw: responses.pop(0))
    out = va.chat(
        session_id="no-confirm",
        user_text="开始画",
        device_mac="aa:bb",
        push_tool=_push,
    )
    assert not push_calls
    assert any("不允许" in t.get("summary", "") for t in out["tool_traces"])
