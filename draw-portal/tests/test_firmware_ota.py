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


def test_firmware_upgrade_rejects_device_changed_since_check(tmp_path, monkeypatch):
    _, client = _clients(monkeypatch, tmp_path)
    for code, mac in (("AAAAAA", "aa:bb:cc:dd:ee:ff"), ("BBBBBB", "11:22:33:44:55:66")):
        _announce(client, code, mac)
        assert client.post("/api/user/bind/complete", json={"bind_code": code}).status_code == 200
    calls = []
    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call",
                        lambda **kw: (calls.append(kw), (True, _status_json()))[1])
    response = client.post("/api/device/firmware/upgrade", json={"device_mac": "aa:bb:cc:dd:ee:ff"})
    assert response.status_code == 409
    assert calls == []


def test_firmware_upgrade_rechecks_owner_after_status(tmp_path, monkeypatch):
    first, second = _clients(monkeypatch, tmp_path)
    _announce(first, "AAAAAA")
    assert first.post("/api/user/bind/complete", json={"bind_code": "AAAAAA"}).status_code == 200
    calls = []

    def push(**kw):
        calls.append(kw)
        if len(calls) == 1:
            _announce(second, "BBBBBB")
            assert second.post("/api/user/bind/complete", json={"bind_code": "BBBBBB", "force": True}).status_code == 200
        return True, _status_json()

    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", push)
    response = first.post("/api/device/firmware/upgrade")
    assert not any(call["name"] == "hutuji.ota_start" for call in calls)
    assert response.json()["status"] == "error"


def test_firmware_upgrade_device_rejection_is_not_started(tmp_path, monkeypatch):
    _, client = _clients(monkeypatch, tmp_path)
    _announce(client, "AAAAAA")
    assert client.post("/api/user/bind/complete", json={"bind_code": "AAAAAA"}).status_code == 200

    def push(**kw):
        if kw["name"] == "hutuji.ota_start":
            return True, '{"isError":true,"content":[{"type":"text","text":"device rejected"}]}'
        return True, _status_json()

    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", push)
    response = client.post("/api/device/firmware/upgrade")
    assert response.json()["status"] == "error"


def test_ota_status_keeps_original_device_after_active_switch(tmp_path, monkeypatch):
    _, client = _clients(monkeypatch, tmp_path)
    _announce(client, "AAAAAA", "aa:bb:cc:dd:ee:ff")
    assert client.post("/api/user/bind/complete", json={"bind_code": "AAAAAA"}).status_code == 200
    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", lambda **kw: (
        True, _status_json() if kw["name"] == "hutuji.status" else '{"ok":true}'
    ))
    assert client.post("/api/device/firmware/upgrade").json()["status"] == "started"
    _announce(client, "BBBBBB", "11:22:33:44:55:66")
    assert client.post("/api/user/bind/complete", json={"bind_code": "BBBBBB"}).status_code == 200

    from draw_portal.crypto import decrypt_token
    from draw_portal.store import Store
    import os
    store = Store(tmp_path / "t.db")
    original = store.get_binding_for_mac("aa:bb:cc:dd:ee:ff")
    original_token = decrypt_token(os.environ["DRAW_PORTAL_TOKEN_CIPHER_KEY"].encode(), original.device_token_enc)
    calls = []

    def status(**kw):
        calls.append(kw)
        return True, _status_json(version="hutuji.1.0.1", ota={"state": "idle", "progress": 0})

    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", status)
    # 新活动设备即使版本碰巧相同，也不能把原设备的升级会话判为成功。
    other = client.get("/api/device/firmware/ota_status?device_mac=11:22:33:44:55:66")
    assert other.json()["state"] == "idle"
    assert other.json()["target_version"] is None
    assert store.get_firmware_ota_session(original.user_id).state == "upgrading"
    response = client.get("/api/device/firmware/ota_status?device_mac=aa:bb:cc:dd:ee:ff")
    assert calls[-1]["device_token"] == original_token
    assert response.json()["state"] == "ok"


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


def test_duplicate_upgrade_returns_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, c2 = _clients(monkeypatch, tmp_path)
    _announce(c2, "IIII01")
    assert c2.post("/api/user/bind/complete", json={"bind_code": "IIII01"}).status_code == 200

    calls: list[dict] = []

    def fake_push(**kw):
        calls.append(kw)
        if kw.get("name") == "hutuji.status":
            return True, _status_json()
        return True, json.dumps({"ok": True})

    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", fake_push)
    first = c2.post("/api/device/firmware/upgrade")
    assert first.status_code == 200
    assert first.json()["status"] == "started"
    ota_before = sum(1 for c in calls if c.get("name") == "hutuji.ota_start")
    assert ota_before == 1

    second = c2.post("/api/device/firmware/upgrade")
    assert second.status_code == 200
    assert second.json() == {"status": "error", "detail": "busy"}
    ota_after = sum(1 for c in calls if c.get("name") == "hutuji.ota_start")
    assert ota_after == 1


def test_other_device_upgrade_does_not_block_active_device_send(tmp_path, monkeypatch):
    from draw_portal.store import Store

    _, client = _clients(monkeypatch, tmp_path)
    _announce(client, "AAAAAA")
    assert client.post("/api/user/bind/complete", json={"bind_code": "AAAAAA"}).status_code == 200
    store = Store(tmp_path / "t.db")
    binding = store.get_binding_for_mac("aa:bb:cc:dd:ee:ff")
    store.upsert_firmware_ota_session(user_id=binding.user_id, device_mac=binding.device_mac, state="upgrading")
    _announce(client, "BBBBBB", "11:22:33:44:55:66")
    assert client.post("/api/user/bind/complete", json={"bind_code": "BBBBBB"}).status_code == 200
    # 到达任务查找的 404，证明没有被另一设备的升级拦成 400。
    assert client.post("/api/draw/nonexistent/send").status_code == 404
    assert client.post("/api/draw/saved/nonexistent/send").status_code == 404
    assert store.get_firmware_ota_session(binding.user_id).state == "upgrading"


@pytest.mark.parametrize("device_state,progress", [("idle", 90), ("upgrading", 0)])
def test_stalled_ota_polling_does_not_renew_expiry(tmp_path, monkeypatch, device_state, progress):
    import time
    from types import SimpleNamespace
    from draw_portal import firmware_ota
    from draw_portal.store import Store

    _, client = _clients(monkeypatch, tmp_path)
    _announce(client, "AAAAAA")
    assert client.post("/api/user/bind/complete", json={"bind_code": "AAAAAA"}).status_code == 200
    store = Store(tmp_path / "t.db")
    binding = store.get_binding_for_mac("aa:bb:cc:dd:ee:ff")
    started = time.time()
    clock = [started + 599]
    monkeypatch.setattr(firmware_ota, "time", SimpleNamespace(time=lambda: clock[0]))
    store.upsert_firmware_ota_session(user_id=binding.user_id, device_mac=binding.device_mac,
                                    state="upgrading", target_version="hutuji.1.0.1", updated_at=started)
    monkeypatch.setattr(firmware_ota, "push_tool_call", lambda **kw: (True, _status_json(ota={"state": device_state, "progress": progress})))
    assert client.get("/api/device/firmware/ota_status").json()["state"] == "upgrading"
    assert store.get_firmware_ota_session(binding.user_id).updated_at == started
    clock[0] = started + 600
    response = client.get("/api/device/firmware/ota_status").json()
    assert response["state"] == "failed"
    assert response["reason"] == "status_timeout"
    assert client.post("/api/draw/nonexistent/send").status_code == 404


def test_ota_expiry_does_not_overwrite_new_progress(tmp_path):
    from draw_portal.store import Store

    store = Store(tmp_path / "t.db")
    store.upsert_firmware_ota_session(user_id="u", device_mac="aa:bb:cc:dd:ee:ff",
                                    state="upgrading", progress=50, updated_at=1200)
    store.expire_firmware_ota_session("u", cutoff=1199)
    assert store.get_firmware_ota_session("u").state == "upgrading"
    store.expire_firmware_ota_session("u", cutoff=1200)
    assert store.get_firmware_ota_session("u").reason == "status_timeout"


def test_late_ota_response_cannot_overwrite_new_device_session(tmp_path):
    from draw_portal.store import Store

    store = Store(tmp_path / "t.db")
    old = store.upsert_firmware_ota_session(user_id="u", device_mac="aa:bb:cc:dd:ee:ff",
                                          state="upgrading", target_version="v1", updated_at=100)
    store.expire_firmware_ota_session("u", cutoff=100)
    store.upsert_firmware_ota_session(user_id="u", device_mac="11:22:33:44:55:66",
                                    state="upgrading", target_version="v2", updated_at=800)
    current = store.update_firmware_ota_session(old, state="ok", progress=100, reason=None, updated_at=801)
    assert current.device_mac == "11:22:33:44:55:66"
    assert current.target_version == "v2" and current.state == "upgrading"


def test_expired_ota_can_reconcile_only_matching_version(tmp_path, monkeypatch):
    import time
    from draw_portal.store import Store

    _, client = _clients(monkeypatch, tmp_path)
    _announce(client, "AAAAAA")
    assert client.post("/api/user/bind/complete", json={"bind_code": "AAAAAA"}).status_code == 200
    store = Store(tmp_path / "t.db")
    binding = store.get_binding_for_mac("aa:bb:cc:dd:ee:ff")
    store.upsert_firmware_ota_session(user_id=binding.user_id, device_mac=binding.device_mac,
                                    state="upgrading", target_version="hutuji.1.0.1", updated_at=time.time()-601)
    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", lambda **kw: (True, _status_json(ota={"state": "ok"})))
    assert client.get("/api/device/firmware/ota_status").json()["reason"] == "status_timeout"
    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", lambda **kw: (True, _status_json(version="hutuji.1.0.1")))
    assert client.get("/api/device/firmware/ota_status").json()["state"] == "ok"


def test_push_failure_session_reason_not_check_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _, c2 = _clients(monkeypatch, tmp_path)
    _announce(c2, "JJJJ01")
    assert c2.post("/api/user/bind/complete", json={"bind_code": "JJJJ01"}).status_code == 200

    def fake_push(**kw):
        if kw.get("name") == "hutuji.status":
            return True, _status_json()
        return False, "HTTP 503"

    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", fake_push)
    r = c2.post("/api/device/firmware/upgrade")
    assert r.status_code == 200
    assert r.json() == {"status": "error", "detail": "HTTP 503"}

    st = c2.get("/api/device/firmware/ota_status")
    assert st.status_code == 200
    body = st.json()
    assert body["state"] == "failed"
    assert body["reason"] != "check_failed"
    assert body["reason"] is None


def test_upgrade_gateway_504_keeps_session_upgrading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """504 = 命令可能已送达（2026-09-11 实证）：会话保持 upgrading，前端等对账。"""
    _, c2 = _clients(monkeypatch, tmp_path)
    _announce(c2, "KKKK01")
    assert c2.post("/api/user/bind/complete", json={"bind_code": "KKKK01"}).status_code == 200

    def fake_push(**kw):
        if kw.get("name") == "hutuji.status":
            return True, _status_json()
        return False, "HTTP 504"

    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", fake_push)
    r = c2.post("/api/device/firmware/upgrade")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unknown"
    assert body["target_version"] == "hutuji.1.0.1"

    st = c2.get("/api/device/firmware/ota_status")
    assert st.status_code == 200
    assert st.json()["state"] == "upgrading"


def test_ota_status_reconciles_on_version_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """升级重启后设备 ota 态复位 idle，靠 firmware_version==target 收口为 ok。"""
    _, c2 = _clients(monkeypatch, tmp_path)
    _announce(c2, "LLLL01")
    assert c2.post("/api/user/bind/complete", json={"bind_code": "LLLL01"}).status_code == 200

    def idle_push(**kw):
        if kw.get("name") == "hutuji.status":
            return True, _status_json(ota={"state": "idle", "progress": 0})
        return True, json.dumps({"ok": True})

    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", idle_push)
    assert c2.post("/api/device/firmware/upgrade").json()["status"] == "started"

    def upgraded_push(**kw):
        if kw.get("name") == "hutuji.status":
            return True, _status_json(version="hutuji.1.0.1", ota={"state": "idle", "progress": 0})
        return True, json.dumps({"ok": True})

    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", upgraded_push)
    st = c2.get("/api/device/firmware/ota_status")
    assert st.status_code == 200
    body = st.json()
    assert body["state"] == "ok"
    assert body["progress"] == 100
    assert body["reason"] is None
    assert body["firmware_version"] == "hutuji.1.0.1"
