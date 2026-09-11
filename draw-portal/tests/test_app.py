from __future__ import annotations

import secrets
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from draw_portal.app import create_app
from draw_portal.pipeline import PipelineResult


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DRAW_PORTAL_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DRAW_PORTAL_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("DRAW_PORTAL_TOKEN_CIPHER_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("DRAW_PORTAL_HOST", "127.0.0.1")
    monkeypatch.setenv("DRAW_PORTAL_PORT", "8302")
    monkeypatch.setenv("DRAW_PORTAL_MCP_OUTPUT_DIR", str(tmp_path / "mcp-out"))
    monkeypatch.setenv("DRAW_PORTAL_PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("DRAW_PORTAL_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("DRAW_PORTAL_SMTP_FROM", "noreply@example.test")
    monkeypatch.setenv("DRAW_PORTAL_EMAIL_DEV_EXPOSE_CODE", "1")


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr("draw_portal.app.send_verification_email", lambda **kwargs: None)
    return TestClient(create_app())


def _register_and_login(client: TestClient, email: str = "buyer@qq.com") -> None:
    req = client.post(
        "/api/auth/register/request",
        json={"email": email, "password": "secret123"},
    )
    assert req.status_code == 200
    code = req.json().get("dev_code")
    assert code
    verify = client.post(
        "/api/auth/register/verify",
        json={"email": email, "code": code},
    )
    assert verify.status_code == 200
    # 产品面硬前置手机号：邮箱路径走 bind-phone 补绑
    phone = client.post("/api/auth/bind-phone", json={"phone": "13800138000"})
    assert phone.status_code == 200, phone.text


def test_register_rejects_non_qq_email(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(monkeypatch, tmp_path)
    r = client.post(
        "/api/auth/register/request",
        json={"email": "user@gmail.com", "password": "secret123"},
    )
    assert r.status_code == 400
    assert "QQ" in r.json()["detail"]


def test_register_accepts_foxmail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(monkeypatch, tmp_path)
    r = client.post(
        "/api/auth/register/request",
        json={"email": "buyer@foxmail.com", "password": "secret123"},
    )
    assert r.status_code == 200
    assert r.json().get("dev_code")


def test_health(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(monkeypatch, tmp_path)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["service"] == "draw-portal"


def test_register_login_and_bind_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "draw_portal.app.probe_device_token",
        lambda **kwargs: (True, "mock ok"),
    )
    _register_and_login(client)

    announce = client.post(
        "/api/device/bind/announce",
        json={
            "mac": "aa:bb:cc:dd:ee:ff",
            "bind_code": "ABC123",
            "device_token": "test-device-token-" + secrets.token_hex(8),
        },
    )
    assert announce.status_code == 200

    session = client.get("/api/device/bind/session", params={"bind_code": "ABC123"})
    assert session.json()["status"] == "pending"

    complete = client.post("/api/user/bind/complete", json={"bind_code": "ABC123"})
    assert complete.status_code == 200
    assert complete.json()["binding"]["bound"] is True

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["binding"]["bound"] is True

def _announce(client: TestClient, mac: str = "aa:bb:cc:dd:ee:ff", code: str = "ABC123"):
    return client.post(
        "/api/device/bind/announce",
        json={
            "mac": mac,
            "bind_code": code,
            "device_token": "test-device-token-" + secrets.token_hex(8),
        },
    )


def _mock_probe_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "draw_portal.app.probe_device_token",
        lambda **kwargs: (True, "mock ok"),
    )


def test_bind_claim_then_announce_auto_binds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """一次扫码合一主路径：配网成功后认领在先，设备 announce 到达即自动绑定。"""
    client = _client(monkeypatch, tmp_path)
    _mock_probe_ok(monkeypatch)
    _register_and_login(client)

    claim = client.post("/api/user/bind/claim", json={"mac": "aa:bb:cc:dd:ee:ff"})
    assert claim.status_code == 200
    assert claim.json()["status"] == "claimed"

    announce = _announce(client)
    assert announce.status_code == 200
    assert announce.json()["bound"] is True

    me = client.get("/api/auth/me")
    assert me.json()["binding"]["bound"] is True

    # 认领已消费、绑定已成立：后续 announce 幂等回 bound，不再换绑。
    again = _announce(client, code="DEF456")
    assert again.json()["bound"] is True
    assert client.get("/api/auth/me").json()["binding"]["bound"] is True

def test_bind_mints_messaging_token_when_probe_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """绑定期铸 token（2026-09-08 量产口径）：announce 上报 token 探活失败时现场铸
    messaging JWT 落绑定——用户首次推送不再先吃一轮 401 再等自愈。"""
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "draw_portal.app.probe_device_token",
        lambda **kwargs: (False, "HTTP 401"),
    )
    minted: list[str] = []

    def fake_mint(*, api_base: str, mac: str, timeout: int = 15) -> str:
        minted.append(mac)
        return "minted-messaging-jwt"

    monkeypatch.setattr("draw_portal.push.mint_messaging_token", fake_mint)
    _register_and_login(client)

    announce = _announce(client)
    assert announce.json()["bound"] is False
    claim = client.post("/api/user/bind/claim", json={"mac": "aa:bb:cc:dd:ee:ff"})
    assert claim.status_code == 200
    assert claim.json()["status"] == "bound"
    assert claim.json()["binding"]["token_probe_ok"] is True
    assert minted == ["aa:bb:cc:dd:ee:ff"]


def test_bind_keeps_raw_token_when_mint_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """铸新失败（控制台 JWT 不可用等）不阻断绑定：按 announce 原 token 落、
    probe_ok=False，推送侧 401 自愈仍兜底。"""
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "draw_portal.app.probe_device_token",
        lambda **kwargs: (False, "HTTP 401"),
    )
    monkeypatch.setattr("draw_portal.push.mint_messaging_token", lambda **kwargs: "")
    _register_and_login(client)

    _announce(client)
    claim = client.post("/api/user/bind/claim", json={"mac": "aa:bb:cc:dd:ee:ff"})
    assert claim.status_code == 200
    assert claim.json()["status"] == "bound"
    assert claim.json()["binding"]["token_probe_ok"] is False


def test_bind_announce_first_claim_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """反向时序：设备先连回家网 announce，用户认领时当场撮合完成。"""
    client = _client(monkeypatch, tmp_path)
    _mock_probe_ok(monkeypatch)
    _register_and_login(client)

    announce = _announce(client)
    assert announce.json()["bound"] is False

    claim = client.post("/api/user/bind/claim", json={"mac": "aa:bb:cc:dd:ee:ff"})
    assert claim.status_code == 200
    assert claim.json()["status"] == "bound"
    assert claim.json()["binding"]["bound"] is True


def test_bind_claim_conflict_requires_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """跨账号冲突与抽屉码同口径：未确认认领 409 且不越权换绑，force 后才换。"""
    client = _client(monkeypatch, tmp_path)
    _mock_probe_ok(monkeypatch)
    _register_and_login(client, "first@qq.com")
    _announce(client)
    assert client.post("/api/user/bind/complete", json={"bind_code": "ABC123"}).status_code == 200

    _register_and_login(client, "second@qq.com")
    claim = client.post("/api/user/bind/claim", json={"mac": "aa:bb:cc:dd:ee:ff"})
    assert claim.status_code == 409

    # 未 force 的认领不碰既有绑定：announce 仍归第一用户。
    announce = _announce(client, code="DEF456")
    assert announce.json()["bound"] is True
    assert client.get("/api/auth/me").json()["binding"]["bound"] is False

    # force 认领 + 在场会话 → 即时换绑到第二用户。
    claim2 = client.post(
        "/api/user/bind/claim",
        json={"mac": "aa:bb:cc:dd:ee:ff", "force": True},
    )
    assert claim2.status_code == 200
    assert claim2.json()["status"] == "bound"
    assert client.get("/api/auth/me").json()["binding"]["bound"] is True


def test_bind_claim_expired_does_not_auto_bind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """过期认领不得撮合；抽屉码降级路径不受影响。"""
    monkeypatch.setenv("DRAW_PORTAL_BIND_CLAIM_TTL_SECONDS", "-1")
    client = _client(monkeypatch, tmp_path)
    _mock_probe_ok(monkeypatch)
    _register_and_login(client)

    claim = client.post("/api/user/bind/claim", json={"mac": "aa:bb:cc:dd:ee:ff"})
    assert claim.json()["status"] == "claimed"

    announce = _announce(client)
    assert announce.json()["bound"] is False

    complete = client.post("/api/user/bind/complete", json={"bind_code": "ABC123"})
    assert complete.status_code == 200
    assert complete.json()["binding"]["bound"] is True


def test_bind_claim_rejects_invalid_mac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)
    r = client.post("/api/user/bind/claim", json={"mac": "not-a-mac"})
    assert r.status_code == 400


def test_draw_upload_save_send(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "draw_portal.app.probe_device_token",
        lambda **kwargs: (True, "mock ok"),
    )

    def fake_pipeline(raw, *, content_type, filename, job_dir, profile=None):
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "draw.gcode").write_text("G21\nG90\n", encoding="ascii")
        (job_dir / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        return PipelineResult(2, 10, 1, job_dir / "draw.svg", job_dir / "draw.gcode", job_dir / "preview.png")

    monkeypatch.setattr("draw_portal.app.process_upload", fake_pipeline)
    monkeypatch.setattr(
        "draw_portal.app.publish_job_files",
        lambda **kwargs: type("P", (), {
            "gcode_url": "https://example.test/files/x.gcode",
            "preview_url": "https://example.test/files/x.png",
            "basename": "draw_test",
        })(),
    )
    monkeypatch.setattr(
        "draw_portal.app.push_draw_self_heal",
        lambda **kwargs: (True, "ok"),
    )

    _register_and_login(client)

    client.post(
        "/api/device/bind/announce",
        json={
            "mac": "aa:bb:cc:dd:ee:ff",
            "bind_code": "XYZ789",
            "device_token": "test-device-token-" + secrets.token_hex(8),
        },
    )
    client.post("/api/user/bind/complete", json={"bind_code": "XYZ789"})

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
    up = client.post(
        "/api/draw/upload",
        files={"file": ("line.png", fake_png, "image/png")},
    )
    assert up.status_code == 200
    job_id = up.json()["job_id"]

    save = client.post(f"/api/draw/{job_id}/save", json={})
    assert save.status_code == 200
    saved_id = save.json()["id"]

    send = client.post(f"/api/draw/{job_id}/send")
    assert send.status_code == 200
    assert send.json()["status"] == "sent"

    saved_send = client.post(f"/api/draw/saved/{saved_id}/send")
    assert saved_send.status_code == 200
