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


def test_draw_upload_save_send(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "draw_portal.app.probe_device_token",
        lambda **kwargs: (True, "mock ok"),
    )

    def fake_pipeline(raw, *, content_type, filename, job_dir):
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
