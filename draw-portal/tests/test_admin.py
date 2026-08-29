from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from draw_admin.app import create_app
from draw_portal.passwords import hash_password
from draw_portal.store import Store


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DRAW_PORTAL_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DRAW_ADMIN_PASSWORD", "admin-secret")
    monkeypatch.setenv("DRAW_ADMIN_SESSION_SECRET", "admin-session")
    monkeypatch.setenv("DRAW_ADMIN_HOST", "127.0.0.1")
    monkeypatch.setenv("DRAW_ADMIN_PORT", "8303")


def test_admin_users_ban_unbind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch, tmp_path)
    store = Store(tmp_path / "t.db")
    user = store.create_user(
        email="u@example.com",
        password_hash=hash_password("secret123"),
        email_verified_at=1.0,
    )
    store.set_device_binding(
        user_id=user.id,
        device_mac="aa:bb:cc:dd:ee:ff",
        device_token_enc="enc",
        bound_at=1.0,
        token_probe_ok=True,
    )
    client = TestClient(create_app())
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "admin-secret"}).status_code == 200

    users = client.get("/api/users")
    assert users.status_code == 200
    assert users.json()["total"] == 1

    assert client.post(f"/api/users/{user.id}/ban", json={"reason": "test"}).status_code == 200
    assert store.get_user(user.id).status == "banned"

    assert client.post(f"/api/users/{user.id}/unban").status_code == 200
    assert store.get_user(user.id).status == "active"

    assert client.post(f"/api/users/{user.id}/unbind").status_code == 200
    assert store.get_binding_for_user(user.id) is None

    stats = client.get("/api/stats")
    assert stats.status_code == 200
    assert stats.json()["users_total"] == 1
