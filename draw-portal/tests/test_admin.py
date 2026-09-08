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

    bindings = client.get("/api/bindings")
    assert bindings.status_code == 200
    assert bindings.json()["total"] == 1
    assert bindings.json()["items"][0]["device_mac"] == "aa:bb:cc:dd:ee:ff"

    assert client.post(f"/api/users/{user.id}/unbind").status_code == 200
    assert store.get_binding_for_user(user.id) is None

    stats = client.get("/api/stats")
    assert stats.status_code == 200
    assert stats.json()["users_total"] == 1

    notes = client.put(
        "/api/ops/notes",
        json={"weapp_version": "0.2.2", "firmware_note": "freenove test"},
    )
    assert notes.status_code == 200
    assert notes.json()["notes"]["weapp_version"] == "0.2.2"
    assert client.get("/api/ops/notes").json()["weapp_version"] == "0.2.2"

    checklist = client.get("/api/ops/checklist")
    assert checklist.status_code == 200
    assert checklist.json()["source"] == "docs/release-checklist.md"
    assert len(checklist.json()["sections"]) >= 5


def test_admin_conversation_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
    store.insert_conversation_event(
        dedupe_key="k1",
        device_mac="aa:bb:cc:dd:ee:ff",
        user_id=user.id,
        session_id="sess1",
        role="user",
        content="你好",
        client_ts=1.0,
        created_at=1.0,
    )
    client = TestClient(create_app())
    client.post("/api/auth/login", json={"password": "admin-secret"})

    listed = client.get(f"/api/users/{user.id}/conversations")
    assert listed.status_code == 200
    assert listed.json()["items"]

    detail = client.get(f"/api/users/{user.id}/conversations/sess1")
    assert detail.status_code == 200

    with store._connect() as conn:
        rows = conn.execute(
            "SELECT action FROM admin_audit_log ORDER BY id"
        ).fetchall()
    actions = [r[0] for r in rows]
    assert "view_conversations" in actions
    assert "view_conversation_detail" in actions


def test_admin_conversation_export_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch, tmp_path)
    store = Store(tmp_path / "t.db")
    user = store.create_user(
        email="u@example.com",
        password_hash=hash_password("secret123"),
        email_verified_at=1.0,
    )
    store.insert_conversation_event(
        dedupe_key="k1",
        device_mac="aa:bb:cc:dd:ee:ff",
        user_id=user.id,
        session_id="sess1",
        role="user",
        content="你好",
        client_ts=1.0,
        created_at=1.0,
    )
    client = TestClient(create_app())
    client.post("/api/auth/login", json={"password": "admin-secret"})
    res = client.get(f"/api/users/{user.id}/conversations/export")
    assert res.status_code == 200
    assert "text/csv" in res.headers.get("content-type", "")
    body = res.content.decode("utf-8-sig")
    assert "session_id,role,content" in body
    assert "你好" in body
    with store._connect() as conn:
        row = conn.execute(
            "SELECT action FROM admin_audit_log WHERE action = 'export_conversations'"
        ).fetchone()
    assert row is not None


def test_admin_draw_limits_and_image_backends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch, tmp_path)
    client = TestClient(create_app())
    client.post("/api/auth/login", json={"password": "admin-secret"})

    limits = client.get("/api/settings/draw-limits")
    assert limits.status_code == 200
    assert "draw_max_concurrent" in limits.json()["keys"]

    put_limits = client.put(
        "/api/settings/draw-limits",
        json={"draw_max_concurrent": 6, "draw_per_second": None},
    )
    assert put_limits.status_code == 200
    assert put_limits.json()["overrides"]["draw_max_concurrent"] == 6

    backends = client.get("/api/settings/image-backends")
    assert backends.status_code == 200
    assert backends.json()["toggles"]["agnes"] is True

    put_back = client.put(
        "/api/settings/image-backends",
        json={"sensenova": False, "agnes": True},
    )
    assert put_back.status_code == 200
    assert put_back.json()["toggles"]["sensenova"] is False
