"""固件 OTA HTTP：check / upgrade / ota_status + 升级中拒出图。"""
from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import draw_portal.app as app_mod
from draw_portal.app import create_app

_BOARD = "freenove-esp32s3-display-2.8-lcd"
_REL = f"s3/{_BOARD}/hutuji.1.0.1/xiaozhi.bin"


def _write_catalog(tmp_path: Path) -> Path:
    catalog = {
        "device": "s3",
        "boards": {
            _BOARD: {
                "version": "hutuji.1.0.1",
                "notes": "fix",
                "sha256": "aa" * 32,
                "size": 1,
                "relpath": _REL,
            }
        },
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


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
    catalog = _write_catalog(tmp_path)
    monkeypatch.setenv("DRAW_PORTAL_FIRMWARE_CATALOG_PATH", str(catalog))
    monkeypatch.setenv("DRAW_PORTAL_FIRMWARE_PUBLIC_BASE", "https://fw.example.test")
    monkeypatch.setattr("draw_portal.app.send_verification_email", lambda **kwargs: None)


def _clients(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, TestClient]:
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


def _status_json(
    *,
    state: str = "idle",
    version: str = "hutuji.1.0.0",
    board: str = _BOARD,
    ota: dict | None = None,
) -> str:
    payload: dict = {
        "connected": True,
        "ready": True,
        "state": state,
        "firmware_version": version,
        "board": board,
    }
    if ota is not None:
        payload["ota"] = ota
    return json.dumps(
        {"success": True, "message": "ok", "data": payload},
        separators=(",", ":"),
    )


def test_firmware_check_requires_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, c2 = _clients(monkeypatch, tmp_path)
    r = c2.get("/api/device/firmware/check")
    assert r.status_code == 400
    assert "绑定" in r.json()["detail"]


def test_firmware_upgrade_pushes_ota_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, c2 = _clients(monkeypatch, tmp_path)
    _announce(c2, "EEEEEE")
    assert c2.post("/api/user/bind/complete", json={"bind_code": "EEEEEE"}).status_code == 200

    calls: list[dict] = []

    def fake_push(**kw):
        calls.append(kw)
        if kw.get("name") == "hutuji.status":
            return True, _status_json()
        return True, json.dumps({"ok": True})

    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", fake_push)
    r = c2.post("/api/device/firmware/upgrade")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "started"
    ota_calls = [c for c in calls if c.get("name") == "hutuji.ota_start"]
    assert len(ota_calls) == 1
    assert "url" in ota_calls[0]["arguments"]
    assert ota_calls[0]["arguments"]["version"] == "hutuji.1.0.1"
    assert ota_calls[0]["arguments"]["board"] == _BOARD


def test_firmware_upgrade_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, c2 = _clients(monkeypatch, tmp_path)
    _announce(c2, "FFFF01")
    assert c2.post("/api/user/bind/complete", json={"bind_code": "FFFF01"}).status_code == 200

    def fake_push(**kw):
        if kw.get("name") == "hutuji.status":
            return True, _status_json(state="streaming")
        return True, json.dumps({"ok": True})

    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", fake_push)
    r = c2.post("/api/device/firmware/upgrade")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert body["detail"] == "busy"


def test_draw_blocked_while_upgrading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, c2 = _clients(monkeypatch, tmp_path)
    _announce(c2, "GGGG01")
    assert c2.post("/api/user/bind/complete", json={"bind_code": "GGGG01"}).status_code == 200

    def fake_push(**kw):
        if kw.get("name") == "hutuji.status":
            return True, _status_json()
        return True, json.dumps({"ok": True})

    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", fake_push)
    assert c2.post("/api/device/firmware/upgrade").json()["status"] == "started"

    blocked = c2.post("/api/draw/nonexistent/send")
    assert blocked.status_code == 400
    assert "升级" in blocked.json()["detail"]

    blocked_saved = c2.post("/api/draw/saved/nonexistent/send")
    assert blocked_saved.status_code == 400
    assert "升级" in blocked_saved.json()["detail"]


def test_firmware_check_and_ota_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, c2 = _clients(monkeypatch, tmp_path)
    _announce(c2, "HHHH01")
    assert c2.post("/api/user/bind/complete", json={"bind_code": "HHHH01"}).status_code == 200

    def fake_push(**kw):
        if kw.get("name") == "hutuji.status":
            return True, _status_json(ota={"state": "idle", "progress": 0})
        return True, json.dumps({"ok": True})

    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", fake_push)
    chk = c2.get("/api/device/firmware/check")
    assert chk.status_code == 200
    data = chk.json()
    assert data["update_available"] is True
    assert data["latest"] == "hutuji.1.0.1"
    assert data["current"] == "hutuji.1.0.0"

    assert c2.post("/api/device/firmware/upgrade").json()["status"] == "started"
    st = c2.get("/api/device/firmware/ota_status")
    assert st.status_code == 200
    body = st.json()
    assert body["state"] == "upgrading"
    assert body["target_version"] == "hutuji.1.0.1"
