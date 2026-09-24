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
        user_id="test-user",
            session_id="s1",
            user_text="你好",
            device_mac="aa:bb",
            push_tool=lambda _n, _a: "",
        )


def test_empty_text_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_llm(monkeypatch)
    with pytest.raises(va.VoiceAgentError, match="内容为空"):
        va.chat(
        user_id="test-user",
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
        user_id="test-user",
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
        user_id="test-user",
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
        user_id="test-user",
        session_id="happy-session",
        user_text="你好",
        device_mac="aa:bb:cc:dd:ee:ff",
        push_tool=lambda _n, _a: "",
    )
    assert result["reply"] == "你好"
    assert result["tool_traces"] == []

def test_history_retains_tool_call_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """多轮漂移回归（2026-09-23 生产复现）：工具调用链必须进历史，否则模型学成不调 voice.draw。"""
    _configure_llm(monkeypatch)
    payloads: list[dict] = []
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_draw_1",
                                "type": "function",
                                "function": {"name": "voice.draw", "arguments": '{"description":"小猫"}'},
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"role": "assistant", "content": "已推送预览，请点开始画"}}]},
        {"choices": [{"message": {"role": "assistant", "content": "好的"}}]},
    ]

    def fake_post_chat(*, url: str, headers: dict, payload: dict) -> dict:
        payloads.append(payload)
        return responses.pop(0)

    def run_portal(name: str, args: dict) -> tuple[str, dict]:
        return "已生成并推送预览", {"kind": "draw", "job_id": "j1", "preview_url": "/p.png", "gcode_url": "/g.gcode"}

    monkeypatch.setattr(va, "_post_chat", fake_post_chat)
    first = va.chat(
        user_id="test-user",
        session_id="hist-chain",
        user_text="画一只小猫",
        device_mac="aa:bb:cc:dd:ee:ff",
        push_tool=lambda _n, _a: "ok",
        run_portal_tool=run_portal,
    )
    assert first["preview"]["job_id"] == "j1"

    va.chat(
        user_id="test-user",
        session_id="hist-chain",
        user_text="再画一只小狗",
        device_mac="aa:bb:cc:dd:ee:ff",
        push_tool=lambda _n, _a: "ok",
        run_portal_tool=run_portal,
    )
    messages = payloads[-1]["messages"]
    tc_msgs = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
    assert tc_msgs, "旧轮 assistant tool_calls 须进入下一轮请求，防模型学成不调工具"
    tool_entries = [m for m in messages if m.get("role") == "tool"]
    assert tool_entries, "旧轮 tool 回包须保留"
    tc_ids = {tc["id"] for m in tc_msgs for tc in m["tool_calls"]}
    assert all(t["tool_call_id"] in tc_ids for t in tool_entries)


def test_cap_history_drops_whole_turns_without_orphaning_tools() -> None:
    """裁剪只按整轮丢：tool 回包不得与 assistant tool_calls 拆对（否则 API 400）。"""
    history: list[dict] = []
    for i in range(8):
        history.extend(
            [
                {"role": "user", "content": f"t{i}"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": f"c{i}", "type": "function", "function": {"name": "hutuji.status", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": f"c{i}", "content": "ok"},
                {"role": "assistant", "content": f"r{i}"},
            ]
        )
    va._cap_history(history)
    users = [m for m in history if m["role"] == "user"]
    assert len(users) == va.MAX_HISTORY_TURNS
    assert history[0]["role"] == "user"
    tc_ids = {tc["id"] for m in history for tc in m.get("tool_calls", [])}
    assert all(m["tool_call_id"] in tc_ids for m in history if m["role"] == "tool")


def test_session_table_evicts_lru_beyond_cap() -> None:
    va._sessions.clear()
    try:
        first = va._get_session(("u", "s0"))
        first.history.append({"role": "user", "content": "hi"})
        for i in range(1, va._MAX_SESSIONS):
            va._get_session(("u", f"s{i}"))
        assert len(va._sessions) == va._MAX_SESSIONS
        va._get_session(("u", "s0"))  # 提热：活跃会话不应被逐出
        va._get_session(("u", "s-new"))
        assert len(va._sessions) == va._MAX_SESSIONS
        assert ("u", "s0") in va._sessions
        assert ("u", "s1") not in va._sessions  # 最冷者被逐
        assert va._get_session(("u", "s0")) is first  # 会话历史对象不被置换
    finally:
        va._sessions.clear()


def test_concurrent_turn_is_rejected_until_whole_history_commits(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    _configure_llm(monkeypatch)
    monkeypatch.setattr(va, "_sessions", va.OrderedDict())
    entered, release = threading.Event(), threading.Event()
    requests = []

    def model(**kw):
        messages = kw["payload"]["messages"]
        requests.append(messages[:])
        entered.set()
        assert release.wait(5)
        return {"choices": [{"message": {"content": "完成"}}]}

    monkeypatch.setattr(va, "_post_chat", model)
    def turn(text):
        return va.chat(user_id="u", session_id="s", user_text=text,
                       device_mac=None, push_tool=lambda *a: "")
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(turn, "第一轮")
        try:
            assert entered.wait(5)
            with pytest.raises(va.VoiceAgentBusyError):
                turn("并发轮次")
            assert len(requests) == 1
        finally:
            release.set()
        first.result(timeout=5)
    turn("第二轮")
    assert [m["content"] for m in requests[-1] if m["role"] == "user"] == ["第一轮", "第二轮"]


def test_active_session_survives_eviction_and_failure_releases_lock(monkeypatch):
    monkeypatch.setattr(va, "_sessions", va.OrderedDict())
    monkeypatch.setattr(va, "_MAX_SESSIONS", 1)
    with pytest.raises(RuntimeError):
        with va._session_history("u", "s"):
            with pytest.raises(va.VoiceAgentBusyError):
                with va._session_history("other", "s"):
                    pytest.fail("活跃会话不能被逐出")
            raise RuntimeError("模拟上游失败")
    with va._session_history("other", "s") as history:
        assert history == []
