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
