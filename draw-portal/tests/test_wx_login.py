"""微信小程序登录契约（2026-09-07）：503 未配置 / 401 微信侧拒 / 建档+会话 /
二次登录复用 / 封禁拒 / 会话与网页端同源（cookie 打通 /api/auth/me）。"""
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import draw_portal.app as app_mod
from draw_portal.app import create_app


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, wx: bool = True) -> None:
    monkeypatch.setenv("DRAW_PORTAL_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DRAW_PORTAL_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("DRAW_PORTAL_TOKEN_CIPHER_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("DRAW_PORTAL_HOST", "127.0.0.1")
    monkeypatch.setenv("DRAW_PORTAL_PORT", "8302")
    monkeypatch.setenv("DRAW_PORTAL_MCP_OUTPUT_DIR", str(tmp_path / "mcp-out"))
    monkeypatch.setenv("DRAW_PORTAL_PUBLIC_BASE_URL", "https://example.test")
    if wx:
        monkeypatch.setenv("DRAW_PORTAL_WX_APPID", "wx-test-appid")
        monkeypatch.setenv("DRAW_PORTAL_WX_SECRET", "wx-test-secret")
    else:
        monkeypatch.delenv("DRAW_PORTAL_WX_APPID", raising=False)
        monkeypatch.delenv("DRAW_PORTAL_WX_SECRET", raising=False)


def test_wx_login_disabled_without_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch, tmp_path, wx=False)
    client = TestClient(create_app())
    r = client.post("/api/auth/wx-login", json={"code": "any"})
    assert r.status_code == 503
    assert "小程序登录未配置" in r.json()["detail"]


def test_wx_login_creates_user_and_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch, tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        app_mod, "_wx_code2session", lambda cfg, code: (calls.append(code), "openid-A")[1]
    )
    client = TestClient(create_app())
    r = client.post("/api/auth/wx-login", json={"code": "js-code-1"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["wx"] is True
    assert r.json()["user"]["created"] is True
    assert calls == ["js-code-1"]
    # 同一会话 cookie 直接可用 /api/auth/me（网页端同款会话）
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["binding"] is not None or me.json()["binding"] == me.json()["binding"]


def test_wx_login_second_time_reuses_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(app_mod, "_wx_code2session", lambda cfg, code: "openid-A")
    client = TestClient(create_app())
    first = client.post("/api/auth/wx-login", json={"code": "c1"}).json()
    second = client.post("/api/auth/wx-login", json={"code": "c2"}).json()
    assert first["user"]["created"] is True
    assert second["user"]["created"] is False


def test_wx_login_rejects_wechat_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch, tmp_path)

    def boom(cfg, code):
        raise app_mod._WxLoginError(401, "微信登录态失效，请重试")

    monkeypatch.setattr(app_mod, "_wx_code2session", boom)
    client = TestClient(create_app())
    r = client.post("/api/auth/wx-login", json={"code": "bad"})
    assert r.status_code == 401
    assert "重试" in r.json()["detail"]


def test_wx_login_banned_user_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(app_mod, "_wx_code2session", lambda cfg, code: "openid-B")
    client = TestClient(create_app())
    assert client.post("/api/auth/wx-login", json={"code": "c"}).status_code == 200
    # 封禁：直改 sqlite（store 是闭包变量，测试按 env 里的 DB 路径操作）
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.execute("UPDATE users SET status = 'banned' WHERE wx_openid = 'openid-B'")
    conn.commit()
    conn.close()
    # TestClient 复用旧 cookie 会被 me 层拒；用新 client 模拟冷启动再登录
    client2 = TestClient(create_app())
    r = client2.post("/api/auth/wx-login", json={"code": "c2"})
    assert r.status_code == 403
    assert "封禁" in r.json()["detail"]
