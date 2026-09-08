"""小程序配套后端契约（2026-09-07）：force 换绑（409 不烧会话/确认后同码换绑/同人免 force）、
设备控制白名单与派发（hutuji.manual/action 白名单 + tools/call 透传）、
微信资料（昵称/头像/手机号 getPhoneNumber）。"""
from __future__ import annotations

import secrets
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


def _clients(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, TestClient]:
    """同一 app 两个客户端：c1=邮箱用户，c2=微信用户（共享 store）。"""
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(app_mod, "_wx_code2session", lambda cfg, code: "openid-B")
    monkeypatch.setattr("draw_portal.app.probe_device_token", lambda **kwargs: (True, "mock"))
    monkeypatch.setattr(app_mod, "_wx_phone_number", lambda cfg, code: "13900139000")
    app = create_app()
    c1, c2 = TestClient(app), TestClient(app)
    req = c1.post("/api/auth/register/request", json={"email": "buyer@qq.com", "password": "secret123"})
    code = req.json()["dev_code"]
    assert c1.post("/api/auth/register/verify", json={"email": "buyer@qq.com", "code": code}).status_code == 200
    assert c1.post("/api/auth/bind-phone", json={"phone": "13800138000"}).status_code == 200
    assert c2.post("/api/auth/wx-login", json={"code": "js"}).status_code == 200
    assert c2.post("/api/auth/wx-phone", json={"code": "phone"}).status_code == 200
    return c1, c2


def _announce(client: TestClient, code: str, mac: str = "aa:bb:cc:dd:ee:ff") -> None:
    r = client.post(
        "/api/device/bind/announce",
        json={"mac": mac, "bind_code": code, "device_token": "tok-" + secrets.token_hex(8)},
    )
    assert r.status_code == 200


def test_bind_conflict_409_then_force_rebind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    c1, c2 = _clients(monkeypatch, tmp_path)
    _announce(c1, "AAAAAA")
    assert c1.post("/api/user/bind/complete", json={"bind_code": "AAAAAA"}).status_code == 200

    _announce(c1, "BBBBBB")
    no_force = c2.post("/api/user/bind/complete", json={"bind_code": "BBBBBB"})
    assert no_force.status_code == 409
    assert "强制换绑" in no_force.json()["detail"]
    # 409 不得消费会话：同码仍可查询为 pending
    assert c2.get("/api/device/bind/session", params={"bind_code": "BBBBBB"}).json()["status"] == "pending"

    forced = c2.post("/api/user/bind/complete", json={"bind_code": "BBBBBB", "force": True})
    assert forced.status_code == 200
    assert c2.get("/api/auth/me").json()["binding"]["bound"] is True
    assert c1.get("/api/auth/me").json()["binding"]["bound"] is False


def test_bind_same_user_rebind_needs_no_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    c1, _ = _clients(monkeypatch, tmp_path)
    _announce(c1, "AAAAAA")
    assert c1.post("/api/user/bind/complete", json={"bind_code": "AAAAAA"}).status_code == 200
    _announce(c1, "CCCCCC")
    again = c1.post("/api/user/bind/complete", json={"bind_code": "CCCCCC"})
    assert again.status_code == 200


def test_device_control_requires_binding_and_whitelist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, c2 = _clients(monkeypatch, tmp_path)
    unbound = c2.post("/api/device/control", json={"tool": "hutuji.manual", "action": "jog_x+"})
    assert unbound.status_code == 400
    assert "绑定" in unbound.json()["detail"]

    _announce(c2, "DDDDDD")
    assert c2.post("/api/user/bind/complete", json={"bind_code": "DDDDDD"}).status_code == 200
    bad_tool = c2.post("/api/device/control", json={"tool": "hutuji.reset"})
    assert bad_tool.status_code == 400
    bad_action = c2.post("/api/device/control", json={"tool": "hutuji.manual", "action": "set_origin"})
    assert bad_action.status_code == 400
    stray_action = c2.post("/api/device/control", json={"tool": "hutuji.pen_test", "action": "x"})
    assert stray_action.status_code == 400


def test_device_control_dispatch_and_result_relay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, c2 = _clients(monkeypatch, tmp_path)
    _announce(c2, "EEEEEE")
    assert c2.post("/api/user/bind/complete", json={"bind_code": "EEEEEE"}).status_code == 200

    calls: list[dict] = []
    monkeypatch.setattr(
        app_mod,
        "push_tool_call",
        lambda **kw: (calls.append(kw), (True, '{"state":"idle"}'))[1],
    )
    r = c2.post("/api/device/control", json={"tool": "hutuji.manual", "action": "jog_x+"})
    assert r.status_code == 200
    assert calls[0]["name"] == "hutuji.manual"
    assert calls[0]["arguments"] == {"action": "jog_x+"}
    assert r.json()["result"] == '{"state":"idle"}'

    # 现网 messaging 信封：success + data（不是 MCP content）
    import json as _json

    payload = {"connected": True, "ready": True, "state": "idle"}
    envelope = _json.dumps(
        {"success": True, "message": "Tools called successfully", "data": payload},
        separators=(",", ":"),
    )
    monkeypatch.setattr(app_mod, "push_tool_call", lambda **kw: (True, envelope))
    st = c2.post("/api/device/control", json={"tool": "hutuji.status"})
    assert st.status_code == 200
    assert _json.loads(st.json()["result"]) == payload

    monkeypatch.setattr(app_mod, "push_tool_call", lambda **kw: (False, "HTTP 503"))
    r2 = c2.post("/api/device/control", json={"tool": "hutuji.pen_test"})
    assert r2.json() == {"status": "error", "detail": "HTTP 503"}

def test_wx_profile_phone_avatar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, c2 = _clients(monkeypatch, tmp_path)
    monkeypatch.setattr(app_mod, "_wx_phone_number", lambda cfg, code: "13800138000")

    p = c2.post("/api/auth/wx-profile", json={"nickname": "小派用户"})
    assert p.status_code == 200
    ph = c2.post("/api/auth/wx-phone", json={"code": "phone-code"})
    assert ph.status_code == 200 and ph.json()["phone"] == "13800138000"

    fake_png = __import__("base64").b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    av = c2.post(
        "/api/auth/wx-avatar",
        files={"file": ("a.png", fake_png, "image/png")},
    )
    assert av.status_code == 200, av.text

    me = c2.get("/api/auth/me").json()
    assert me["nickname"] == "小派用户"
    assert me["phone"] == "13800138000"
    assert me["avatar_url"]
    got = c2.get(me["avatar_url"].replace("/api", "/api", 1))
    assert got.status_code == 200

    monkeypatch.setattr(
        app_mod,
        "_wx_phone_number",
        lambda cfg, code: (_ for _ in ()).throw(app_mod._WxLoginError(401, "手机号授权已过期，请重新点击授权")),
    )
    assert c2.post("/api/auth/wx-phone", json={"code": "stale"}).status_code == 401
