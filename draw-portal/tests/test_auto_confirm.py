"""小程序 send 后自动 hutuji.confirm：已在小程序预览过，不必再在小派点确认。"""

from __future__ import annotations

import draw_portal.push as push_mod
from cryptography.fernet import Fernet

from draw_portal.crypto import encrypt_token
from draw_portal.push import auto_confirm_after_preview, push_draw_self_heal

_FERNET_KEY = Fernet.generate_key()


def test_auto_confirm_waits_for_awaiting_then_confirms(monkeypatch):
    calls: list[str] = []
    statuses = iter(
        [
            (True, '{"success":true,"data":{"state":"previewing"}}'),
            (
                True,
                '{"success":true,"data":{"state":"awaiting_confirmation","awaiting_confirmation":true}}',
            ),
        ]
    )

    def fake_tool(**kw):
        calls.append(kw["name"])
        if kw["name"] == "hutuji.status":
            return next(statuses)
        assert kw["name"] == "hutuji.confirm"
        return True, '{"success":true,"data":"started"}'

    monkeypatch.setattr(push_mod, "push_tool_call", fake_tool)
    monkeypatch.setattr(push_mod.time, "sleep", lambda _s: None)

    ok, summary = auto_confirm_after_preview(
        api_base="https://example",
        device_token="tok",
        timeout_s=10,
        poll_s=0,
    )
    assert ok
    assert summary == "hutuji.confirm ok"
    assert calls == ["hutuji.status", "hutuji.status", "hutuji.confirm"]


def test_confirm_transient_failure_retries_within_window(monkeypatch):
    """2026-09-25 生产事故根治：WiFi 抖动期单次 confirm 瞬断不再判死，
    窗口内下一轮 status 仍 awaiting 则重试，第二次成功即开画。"""
    calls: list[str] = []
    awaiting = (
        True,
        '{"success":true,"data":{"state":"awaiting_confirmation","awaiting_confirmation":true}}',
    )
    statuses = iter([awaiting, awaiting])
    confirm_results = iter([(False, "HTTP 504"), (True, '{"success":true,"data":"started"}')])

    def fake_tool(**kw):
        calls.append(kw["name"])
        if kw["name"] == "hutuji.status":
            return next(statuses)
        return next(confirm_results)

    monkeypatch.setattr(push_mod, "push_tool_call", fake_tool)
    monkeypatch.setattr(push_mod.time, "sleep", lambda _s: None)

    ok, summary = auto_confirm_after_preview(
        api_base="https://example", device_token="tok", timeout_s=10, poll_s=0,
    )
    assert ok
    assert summary == "hutuji.confirm ok"
    assert calls == ["hutuji.status", "hutuji.confirm", "hutuji.status", "hutuji.confirm"]


def test_confirm_persistent_failure_exhausts_window(monkeypatch):
    """confirm 恒瞬态失败：窗口内持续重试（区别于旧单次即弃），耗尽后报超时。"""
    import itertools

    calls: list[str] = []
    statuses = itertools.repeat(
        (True, '{"success":true,"data":{"state":"awaiting_confirmation","awaiting_confirmation":true}}')
    )
    clock = {"t": 0.0}

    def fake_tool(**kw):
        calls.append(kw["name"])
        if kw["name"] == "hutuji.status":
            return next(statuses)
        return False, "HTTP 504"

    monkeypatch.setattr(push_mod, "push_tool_call", fake_tool)
    monkeypatch.setattr(push_mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(push_mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))

    ok, summary = auto_confirm_after_preview(
        api_base="https://example", device_token="tok", timeout_s=5, poll_s=1.5,
    )
    assert not ok
    assert summary.startswith("confirm timeout")
    assert calls.count("hutuji.confirm") >= 2


def test_preview_terminal_state_fails_fast(monkeypatch):
    """预览下载失败落到 error：立即出局，不再白耗 90s 窗口才报失败。"""
    calls: list[str] = []

    def fake_tool(**kw):
        calls.append(kw["name"])
        return True, '{"success":true,"data":{"state":"error"}}'

    monkeypatch.setattr(push_mod, "push_tool_call", fake_tool)
    monkeypatch.setattr(push_mod.time, "sleep", lambda _s: None)

    ok, summary = auto_confirm_after_preview(
        api_base="https://example", device_token="tok", timeout_s=90, poll_s=0,
    )
    assert not ok
    assert "state=error" in summary
    assert calls == ["hutuji.status"]


def test_confirm_response_lost_but_job_started(monkeypatch):
    """confirm 应答丢失但设备已开画：下一轮 status 见 streaming，按成功收，
    不得重发 confirm（重发会被设备判「没有待确认」而误报失败）。"""
    calls: list[str] = []
    statuses = iter(
        [
            (True, '{"success":true,"data":{"state":"awaiting_confirmation"}}'),
            (True, '{"success":true,"data":{"state":"streaming"}}'),
        ]
    )

    def fake_tool(**kw):
        calls.append(kw["name"])
        if kw["name"] == "hutuji.status":
            return next(statuses)
        return False, "timeout"

    monkeypatch.setattr(push_mod, "push_tool_call", fake_tool)
    monkeypatch.setattr(push_mod.time, "sleep", lambda _s: None)

    ok, summary = auto_confirm_after_preview(
        api_base="https://example", device_token="tok", timeout_s=10, poll_s=0,
    )
    assert ok
    assert summary == "already streaming"
    assert calls == ["hutuji.status", "hutuji.confirm", "hutuji.status"]


def test_push_draw_self_heal_auto_confirm_appends_started(monkeypatch):
    enc = encrypt_token(_FERNET_KEY, "plain-jwt")

    monkeypatch.setattr(
        push_mod,
        "push_draw_to_device",
        lambda **kw: (True, "hutuji.draw ok"),
    )
    monkeypatch.setattr(
        push_mod,
        "auto_confirm_after_preview",
        lambda **kw: (True, "hutuji.confirm ok"),
    )

    ok, summary = push_draw_self_heal(
        api_base="https://example",
        token_cipher_key=_FERNET_KEY,
        device_mac="aa:bb:cc:dd:ee:ff",
        device_token_enc=enc,
        gcode_url="https://x/g",
        preview_url="https://x/p",
        auto_confirm=True,
    )
    assert ok
    assert "已自动开始画" in summary


def test_401_self_heal_still_works_without_auto_confirm(monkeypatch):
    """回归：自愈路径改走 push_draw_to_device，不再依赖 push_for_user_binding。"""
    calls = {"push": [], "stored": []}

    def fake_push_draw(**kw):
        calls["push"].append(kw["device_token"])
        if kw["device_token"] == "stale":
            return False, "HTTP 401"
        return True, "hutuji.draw ok"

    monkeypatch.setattr(push_mod, "push_draw_to_device", fake_push_draw)
    monkeypatch.setattr(push_mod, "mint_messaging_token", lambda **kw: "fresh-jwt")
    monkeypatch.setattr(
        push_mod,
        "decrypt_token",
        lambda _k, enc: "stale" if enc == "stale-enc" else enc,
    )

    ok, summary = push_draw_self_heal(
        api_base="https://example",
        token_cipher_key=_FERNET_KEY,
        device_mac="aa:bb:cc:dd:ee:ff",
        device_token_enc="stale-enc",
        gcode_url="https://x/g",
        preview_url="https://x/p",
        on_token_minted=lambda enc: calls["stored"].append(enc),
    )
    assert ok
    assert "自愈" in summary
    assert calls["push"][-1] == "fresh-jwt"
    assert len(calls["stored"]) == 1
