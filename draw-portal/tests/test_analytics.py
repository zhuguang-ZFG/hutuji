"""对话与 usage 采集 API 测试。"""
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from draw_portal.app import create_app
from draw_portal.passwords import hash_password
from draw_portal.store import Store


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DRAW_PORTAL_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DRAW_PORTAL_SESSION_SECRET", "portal-secret")
    monkeypatch.setenv("DRAW_PORTAL_TOKEN_CIPHER_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("DRAW_PORTAL_USAGE_INGEST_SECRET", "ingest-secret")
    monkeypatch.setenv("DRAW_PORTAL_SMTP_HOST", "")


def test_conversation_report_and_usage_ingest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _env(monkeypatch, tmp_path)
    store = Store(tmp_path / "t.db")
    user = store.create_user(
        email="u@qq.com",
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
    monkeypatch.setattr("draw_portal.app._is_loopback_client", lambda _host: True)

    bad = client.post(
        "/api/device/conversation/report",
        json={
            "mac": "aa:bb:cc:dd:ee:ff",
            "session_id": "sess-1",
            "events": [{"role": "user", "content": "画一只猫", "client_ts": 100.0}],
        },
    )
    assert bad.status_code == 200
    assert bad.json()["accepted"] == 1

    dup = client.post(
        "/api/device/conversation/report",
        json={
            "mac": "aa:bb:cc:dd:ee:ff",
            "session_id": "sess-1",
            "events": [{"role": "user", "content": "画一只猫", "client_ts": 100.0}],
        },
    )
    assert dup.json()["accepted"] == 0

    unbound = client.post(
        "/api/device/conversation/report",
        json={
            "mac": "11:22:33:44:55:66",
            "session_id": "sess-x",
            "events": [{"role": "user", "content": "hi"}],
        },
    )
    assert unbound.status_code == 403

    usage = client.post(
        "/api/internal/usage",
        json={"source": "mcp", "event_type": "tool_draw", "ok": True},
        headers={"X-Hutuji-Usage-Secret": "ingest-secret"},
    )
    assert usage.status_code == 200

    usage_bad = client.post(
        "/api/internal/usage",
        json={"source": "mcp", "event_type": "tool_draw"},
        headers={"X-Hutuji-Usage-Secret": "wrong"},
    )
    assert usage_bad.status_code == 401

    sessions = store.list_conversation_sessions(user_id=user.id)
    assert len(sessions) == 1
    assert sessions[0]["message_count"] == 1

    rows = store.usage_counts_since(0)
    assert any(r["event_type"] == "tool_draw" for r in rows)
