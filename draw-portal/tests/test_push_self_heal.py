"""messaging token 自愈回归：push 401 → 现场铸新 → 回写绑定表 → 重试成功。

2026-09-07 实证：绑定表 device_token_enc 解密出 test-token 占位，全量推送 401；
官方签发端点 POST /agents/generate-messaging-token {macAddress} 让服务端可自愈。
"""

from __future__ import annotations

import draw_portal.push as push_mod
from cryptography.fernet import Fernet

from draw_portal.push import push_draw_self_heal

_FERNET_KEY = Fernet.generate_key()


def _fake_mint(*, api_base, mac, timeout=15):
    return "fresh-jwt"


def test_401_self_heal_mints_updates_and_retries(monkeypatch):
    calls = {"push": [], "mint": 0, "stored": []}

    def fake_push_for_user_binding(**kw):
        calls["push"].append(kw["device_token_enc"])
        return False, "HTTP 401"

    def fake_push_draw(**kw):
        calls["push"].append(kw["device_token"])
        return True, "hutuji.draw ok"

    monkeypatch.setattr(push_mod, "push_for_user_binding", fake_push_for_user_binding)
    monkeypatch.setattr(push_mod, "push_draw_to_device", fake_push_draw)
    monkeypatch.setattr(push_mod, "mint_messaging_token", _fake_mint)

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
    assert len(calls["stored"]) == 1  # 新 token（加密后）已回写
    assert calls["push"][-1] == "fresh-jwt"  # 重试用的是新铸明文 token


def test_non_401_does_not_mint(monkeypatch):
    monkeypatch.setattr(
        push_mod,
        "push_for_user_binding",
        lambda **kw: (False, "网络 TimeoutError"),
    )
    monkeypatch.setattr(
        push_mod,
        "mint_messaging_token",
        lambda **kw: (_ for _ in ()).throw(AssertionError("不该铸 token")),
    )
    ok, summary = push_draw_self_heal(
        api_base="https://example",
        token_cipher_key=_FERNET_KEY,
        device_mac="aa:bb:cc:dd:ee:ff",
        device_token_enc="stale-enc",
        gcode_url="https://x/g",
        preview_url="https://x/p",
    )
    assert not ok
    assert summary == "网络 TimeoutError"


def test_mint_unavailable_reports_heal_failure(monkeypatch):
    monkeypatch.setattr(
        push_mod,
        "push_for_user_binding",
        lambda **kw: (False, "HTTP 401"),
    )
    monkeypatch.setattr(push_mod, "mint_messaging_token", lambda **kw: "")
    ok, summary = push_draw_self_heal(
        api_base="https://example",
        token_cipher_key=_FERNET_KEY,
        device_mac="aa:bb:cc:dd:ee:ff",
        device_token_enc="stale-enc",
        gcode_url="https://x/g",
        preview_url="https://x/p",
    )
    assert not ok
    assert "自愈失败" in summary
