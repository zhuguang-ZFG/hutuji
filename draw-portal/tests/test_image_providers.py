from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from draw_admin.app import create_app
from draw_portal.store import Store


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> bytes:
    key = Fernet.generate_key()
    monkeypatch.setenv("DRAW_PORTAL_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DRAW_ADMIN_PASSWORD", "admin-secret")
    monkeypatch.setenv("DRAW_ADMIN_SESSION_SECRET", "admin-session")
    monkeypatch.setenv("DRAW_ADMIN_HOST", "127.0.0.1")
    monkeypatch.setenv("DRAW_ADMIN_PORT", "8303")
    monkeypatch.setenv("DRAW_PORTAL_PROVIDER_CIPHER_KEY", key.decode("ascii"))
    return key


def test_image_provider_crud_and_mcp_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    key = _env(monkeypatch, tmp_path)
    client = TestClient(create_app())
    client.post("/api/auth/login", json={"password": "admin-secret"})

    created = client.post(
        "/api/image-providers",
        json={
            "name": "Agnes 测试",
            "provider_type": "agnes",
            "base_url": "https://api.example.invalid/v1",
            "model": "agnes-image-2.1-flash",
            "enabled": True,
        },
    )
    assert created.status_code == 200
    provider_id = created.json()["item"]["id"]

    add_key = client.post(
        f"/api/image-providers/{provider_id}/keys",
        json={"api_key": "sk-test-secret", "label": "主 key"},
    )
    assert add_key.status_code == 200
    assert add_key.json()["item"]["has_key"] is True
    assert "sk-test" not in add_key.text

    store = Store(tmp_path / "t.db", provider_cipher_key=key)
    payload = store.get_mcp_settings_payload()
    assert payload["revision"] >= 1
    racers = payload["image_racers"]
    assert len(racers) == 1
    assert racers[0]["api_keys"] == ["sk-test-secret"]

    listed = client.get("/api/image-providers")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["keys"][0]["has_key"] is True
    assert "sk-test" not in listed.text

    with store._connect() as conn:
        row = conn.execute(
            "SELECT action FROM admin_audit_log WHERE action = 'create_image_provider'"
        ).fetchone()
    assert row is not None


def test_image_provider_rejects_private_base_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch, tmp_path)
    client = TestClient(create_app())
    client.post("/api/auth/login", json={"password": "admin-secret"})
    res = client.post(
        "/api/image-providers",
        json={
            "name": "bad",
            "provider_type": "agnes",
            "base_url": "https://127.0.0.1/v1",
            "model": "m",
        },
    )
    assert res.status_code == 400
