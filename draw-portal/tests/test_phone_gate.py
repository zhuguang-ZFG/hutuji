"""手机号硬前置（2026-09-08）：wx.login 可建会话，无 phone 禁产品面。"""
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import draw_portal.app as app_mod
from draw_portal.app import create_app


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DRAW_PORTAL_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DRAW_PORTAL_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("DRAW_PORTAL_TOKEN_CIPHER_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("DRAW_PORTAL_HOST", "127.0.0.1")
    monkeypatch.setenv("DRAW_PORTAL_PORT", "8302")
    monkeypatch.setenv("DRAW_PORTAL_MCP_OUTPUT_DIR", str(tmp_path / "mcp-out"))
    monkeypatch.setenv("DRAW_PORTAL_PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("DRAW_PORTAL_WX_APPID", "wx-test-appid")
    monkeypatch.setenv("DRAW_PORTAL_WX_SECRET", "wx-test-secret")
    monkeypatch.setenv("DRAW_PORTAL_EMAIL_DEV_EXPOSE_CODE", "1")
    monkeypatch.setenv("DRAW_PORTAL_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("DRAW_PORTAL_SMTP_FROM", "noreply@example.test")
    monkeypatch.setattr("draw_portal.app.send_verification_email", lambda **kwargs: None)


def test_wx_user_without_phone_blocked_then_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(app_mod, "_wx_code2session", lambda cfg, code: "openid-phone")
    monkeypatch.setattr(app_mod, "_wx_phone_number", lambda cfg, code: "13700137000")
    client = TestClient(create_app())
    assert client.post("/api/auth/wx-login", json={"code": "c"}).status_code == 200

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["phone_bound"] is False
    assert me.json()["phone"] is None

    blocked = client.post("/api/user/bind/complete", json={"bind_code": "ABCDEF"})
    assert blocked.status_code == 403
    detail = blocked.json()["detail"]
    assert detail["code"] == "phone_required"

    send_blocked = client.post("/api/draw/generate", json={"description": "一只猫"})
    assert send_blocked.status_code == 403
    assert send_blocked.json()["detail"]["code"] == "phone_required"

    ctrl = client.post("/api/device/control", json={"tool": "hutuji.status"})
    assert ctrl.status_code == 403
    assert ctrl.json()["detail"]["code"] == "phone_required"

    # 拿号后产品面放行（bind 仍可能因无会话 400，但不得再是 phone_required）
    assert client.post("/api/auth/wx-phone", json={"code": "p"}).status_code == 200
    me2 = client.get("/api/auth/me").json()
    assert me2["phone_bound"] is True
    assert me2["phone"] == "13700137000"

    after = client.post("/api/user/bind/complete", json={"bind_code": "ABCDEF"})
    assert after.status_code != 403 or (
        isinstance(after.json().get("detail"), dict)
        and after.json()["detail"].get("code") != "phone_required"
    )
    assert after.status_code in (400, 404) or after.status_code == 200


def test_email_user_bind_phone_then_product(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _env(monkeypatch, tmp_path)
    client = TestClient(create_app())
    req = client.post(
        "/api/auth/register/request",
        json={"email": "buyer@qq.com", "password": "secret123"},
    )
    code = req.json()["dev_code"]
    assert client.post(
        "/api/auth/register/verify", json={"email": "buyer@qq.com", "code": code}
    ).status_code == 200

    me = client.get("/api/auth/me").json()
    assert me["phone_bound"] is False
    assert client.post("/api/draw/generate", json={"description": "猫"}).status_code == 403

    bad = client.post("/api/auth/bind-phone", json={"phone": "123"})
    assert bad.status_code in (400, 422)

    ok = client.post("/api/auth/bind-phone", json={"phone": "13800138000"})
    assert ok.status_code == 200
    assert client.get("/api/auth/me").json()["phone_bound"] is True


def test_wx_user_rejects_manual_bind_phone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(app_mod, "_wx_code2session", lambda cfg, code: "openid-X")
    client = TestClient(create_app())
    assert client.post("/api/auth/wx-login", json={"code": "c"}).status_code == 200
    r = client.post("/api/auth/bind-phone", json={"phone": "13800138000"})
    assert r.status_code == 400
    assert "微信" in r.json()["detail"]
