"""Telegram 注册监控通知：未配置禁用、配置后发送、新用户首次绑定触发且重绑不重复推。"""
from __future__ import annotations

from pathlib import Path

import pytest

from draw_portal import tg_notify

from test_app import _announce, _client, _mock_probe_ok, _register_and_login  # noqa: E402


class _SyncThread:
    """把 daemon 线程改同步执行：让 _post 断言确定性。"""

    def __init__(self, target, args=(), daemon: bool = True):
        self._target = target
        self._args = args

    def start(self) -> None:
        self._target(*self._args)


def test_notify_async_disabled_without_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    sent: list[str] = []
    monkeypatch.setattr(tg_notify, "_post", lambda t, c, x: sent.append(x))
    tg_notify.notify_async("不应发出")
    assert sent == []


def test_notify_async_posts_configured_text(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TG_BOT_TOKEN", "tok")
    monkeypatch.setenv("TG_CHAT_ID", "42")
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(tg_notify, "_post", lambda t, c, x: sent.append((t, c, x)))
    monkeypatch.setattr(tg_notify.threading, "Thread", _SyncThread)
    tg_notify.notify_async("新用户绑定设备")
    assert sent == [("tok", "42", "[hutuji] 新用户绑定设备")]


def test_dedupe_key_repeats_skipped(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TG_BOT_TOKEN", "tok")
    monkeypatch.setenv("TG_CHAT_ID", "42")
    monkeypatch.setattr(tg_notify.threading, "Thread", _SyncThread)
    sent: list[str] = []
    monkeypatch.setattr(tg_notify, "_post", lambda t, c, x: sent.append(x))
    tg_notify.notify_async("一次", dedupe_key="u1")
    tg_notify.notify_async("两次", dedupe_key="u1")
    tg_notify.notify_async("另键", dedupe_key="u2")
    tg_notify.notify_async("无键")
    assert [s for s in sent if s.endswith("一次")] and sent[-1].endswith("无键")
    assert sum("两次" in s for s in sent) == 0


def test_new_user_first_binding_notifies_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """注册监控主链：首次绑定触发通知；同用户重绑（解绑再绑）不重复推。"""
    client = _client(monkeypatch, tmp_path)
    _mock_probe_ok(monkeypatch)
    monkeypatch.setenv("TG_BOT_TOKEN", "tok")
    monkeypatch.setenv("TG_CHAT_ID", "42")
    monkeypatch.setattr(tg_notify.threading, "Thread", _SyncThread)
    sent: list[str] = []
    monkeypatch.setattr(tg_notify, "_post", lambda t, c, x: sent.append(x))
    tg_notify._seen.clear()
    _register_and_login(client)
    _announce(client)
    assert client.post(
        "/api/user/bind/complete", json={"bind_code": "ABC123"}
    ).status_code == 200
    assert len(sent) == 1
    assert "mac=aa:bb:cc:dd:ee:ff" in sent[0]

    client.post("/api/user/bind/unbind")
    _announce(client, code="DEF456")
    assert client.post(
        "/api/user/bind/complete", json={"bind_code": "DEF456"}
    ).status_code == 200
    assert len(sent) == 1
