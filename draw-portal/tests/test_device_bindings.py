"""多设备迁移、回包隔离与绑定事务回滚；全部使用临时库和本地替身。"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from draw_portal.store import Store
from test_app import _client, _register_and_login
from test_device_identity import (
    MAC, _bind, _claim, _identity, _second_user, _start, portal,
)


OTHER_MAC = "aa:bb:cc:dd:ee:02"


def _binding(store, user_id, mac=MAC, *, when=100, token="encrypted-first"):
    return store.set_device_binding(
        user_id=user_id, device_mac=mac, device_token_enc=token,
        bound_at=when, token_probe_ok=True,
    )


@pytest.mark.parametrize("has_machine_type", [False, True])
def test_legacy_database_migrates_by_column_name_and_is_repeatable(tmp_path, has_machine_type):
    path = tmp_path / "old.db"
    machine_column = ", machine_type TEXT NOT NULL DEFAULT 'paper'" if has_machine_type else ""
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT, password_hash TEXT, created_at REAL)")
        conn.execute("INSERT INTO users VALUES ('owner', 'buyer@qq.com', 'not-a-real-hash', 10)")
        # 故意改变旧表列顺序并加未知列，防止 SELECT * 对位抄错密文/机型。
        conn.execute(
            "CREATE TABLE device_bindings (device_mac TEXT UNIQUE, user_id TEXT PRIMARY KEY, "
            "last_push_at REAL, device_token_enc TEXT, token_probe_ok INTEGER, bound_at REAL, "
            "legacy_extra TEXT" + machine_column + ")"
        )
        conn.execute(
            "INSERT INTO device_bindings (user_id, device_mac, device_token_enc, bound_at, "
            "last_push_at, token_probe_ok, legacy_extra) VALUES (?, ?, ?, 20, 30, 1, 'ignored')",
            ("owner", MAC, "old-encrypted-token"),
        )
        if has_machine_type:
            conn.execute("UPDATE device_bindings SET machine_type='nopaper'")
        conn.execute(
            "CREATE TABLE bind_sessions (bind_code TEXT PRIMARY KEY, device_mac TEXT, "
            "device_token_enc TEXT, expires_at REAL, consumed_at REAL)"
        )
    store = Store(path)
    first = store.get_binding_for_user("owner")
    assert first.device_mac == MAC
    assert first.device_token_enc == "old-encrypted-token"
    assert (first.bound_at, first.last_push_at, first.token_probe_ok) == (20, 30, True)
    assert first.machine_type == ("nopaper" if has_machine_type else "paper")
    store.update_binding_token("owner", device_mac=MAC, device_token_enc="fresh-encrypted-token")
    # 历史镜像不能在下次进程启动时反向覆盖已迁移主表。
    with store._connect() as conn:
        conn.execute("UPDATE device_bindings SET device_token_enc='stale-token'")
    for _ in range(2):
        store = Store(path)
        assert len(store.list_bindings_for_user("owner")) == 1
        assert store.get_binding_for_user("owner").device_token_enc == "fresh-encrypted-token"
    session = store.upsert_bind_session(
        bind_code="ABC123", device_mac=OTHER_MAC, device_token_enc="placeholder",
        expires_at=9999999999,
    )
    assert session.device_id is None
    assert session.plotter_sku == "paper"


def test_select_promotes_legacy_only_binding_before_sync(tmp_path):
    store = Store(tmp_path / "binding.db")
    user = store.create_user(email="buyer@qq.com", password_hash="test")
    expected = _binding(store, user.id)
    with store._connect() as conn:
        conn.execute("DELETE FROM user_device_bindings WHERE user_id=?", (user.id,))
    assert store.select_binding_for_user(user.id, MAC) == expected
    assert store.get_binding_for_user(user.id) == expected
    assert store.get_binding_for_mac(MAC) == expected
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM device_bindings WHERE user_id=?", (user.id,)).fetchone()[0] == 1


def test_stale_legacy_mirror_cannot_steal_device_from_primary_owner(tmp_path):
    store = Store(tmp_path / "binding.db")
    old = store.create_user(email="old@qq.com", password_hash="test")
    owner = store.create_user(email="owner@qq.com", password_hash="test")
    expected = _binding(store, owner.id)
    # 模拟旧进程留下过时镜像；主表归属是新完成事务的真值。
    with store._connect() as conn:
        conn.execute("UPDATE device_bindings SET user_id=? WHERE device_mac=?", (old.id, MAC))
    assert store.select_binding_for_user(old.id, MAC) is None
    assert store.get_binding_for_mac(MAC) == expected
    assert store.get_binding_for_user(owner.id) == expected


@pytest.mark.parametrize("field,value", [
    ("device_token_enc", "refreshed-token"), ("token_probe_ok", False),
    ("machine_type", "nopaper"), ("last_push_at", 789),
])
def test_delayed_update_targets_captured_device_not_current_selection(tmp_path, field, value):
    store = Store(tmp_path / "binding.db")
    user = store.create_user(email="buyer@qq.com", password_hash="test")
    first = _binding(store, user.id)
    second = _binding(store, user.id, OTHER_MAC, when=200, token="encrypted-second")
    assert store.get_binding_for_user(user.id) == second
    updates = {
        "device_token_enc": lambda: store.update_binding_token(user.id, device_mac=first.device_mac, device_token_enc=value),
        "token_probe_ok": lambda: store.update_binding_probe(user.id, device_mac=first.device_mac, ok=value),
        "machine_type": lambda: store.set_binding_machine_type(user.id, value, device_mac=first.device_mac),
        "last_push_at": lambda: store.update_binding_last_push(user.id, device_mac=first.device_mac, when=value),
    }
    updates[field]()
    assert getattr(store.get_binding_for_mac(MAC), field) == value
    assert store.get_binding_for_user(user.id) == second
    store.select_binding_for_user(user.id, MAC)
    assert getattr(store.get_binding_for_user(user.id), field) == value
    with store._connect() as conn:
        mirror = conn.execute("SELECT * FROM device_bindings WHERE user_id=?", (user.id,)).fetchone()
    assert mirror[field] == value


def test_send_callbacks_remain_on_device_selected_at_request_start(monkeypatch, tmp_path):
    import os

    from draw_portal.crypto import encrypt_token
    from test_paper_settings import _code

    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)
    store = Store(tmp_path / "t.db")
    user_id = store.get_user_by_email("buyer@qq.com").id
    second = _binding(store, user_id, OTHER_MAC, when=100, token="second-token")
    _binding(store, user_id, when=200, token=encrypt_token(
        os.environ["DRAW_PORTAL_TOKEN_CIPHER_KEY"].encode(), "first-device-token",
    ))
    job = store.create_draw_job(user_id=user_id, source_name="test.png")
    store.mark_draw_job_ready(job.id, basename="test")
    job_dir = tmp_path / "data" / "jobs" / job.id
    job_dir.mkdir(parents=True)
    (job_dir / "draw.gcode").write_bytes(_code())
    monkeypatch.setattr("draw_portal.app.push_tool_call", lambda **kwargs: (
        True, '{"plotter_sku":"paper","state":"idle","connected":true,"ready":true}',
    ))
    monkeypatch.setattr("draw_portal.app.publish_job_files", lambda **kw: SimpleNamespace(
        gcode_url="https://example.test/test.gcode", preview_url="https://example.test/test.png",
        basename="test",
    ))

    def push(**kwargs):
        assert kwargs["device_mac"] == MAC
        kwargs["before_action"]()
        store.select_binding_for_user(user_id, OTHER_MAC)
        kwargs["on_token_minted"]("first-device-new-token")
        # 已发出的请求回写原设备；切换后不得再向任何设备续发控制。
        from draw_portal.paper_settings import PaperSettingsError
        with pytest.raises(PaperSettingsError, match="已切换"):
            kwargs["before_action"]()
        return True, "ok"

    monkeypatch.setattr("draw_portal.app.push_draw_self_heal", push)
    response = client.post(f"/api/draw/{job.id}/send")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "sent"
    assert store.get_binding_for_user(user_id) == second
    updated = store.get_binding_for_mac(MAC)
    assert updated.device_token_enc == "first-device-new-token"
    assert updated.last_push_at is not None
    assert store.get_binding_for_mac(OTHER_MAC).last_push_at is None


def test_transfer_repairs_old_owner_selection_and_stale_unbind_cannot_delete_new_owner(portal):
    key, identity = _identity()
    _bind(portal, key, identity)
    old_user_id = portal.store.get_binding_for_mac(MAC).user_id
    remaining = _binding(portal.store, old_user_id, OTHER_MAC)
    portal.store.select_binding_for_user(old_user_id, MAC)
    other = _second_user(portal.client)
    _, fresh_identity = _identity(nonce=b"t" * 16)
    assert _claim(other, fresh_identity, force=True).status_code == 200
    _, response = _start(portal.client, key, fresh_identity, code="DEF456")
    assert response.json()["bound"] is True
    new_binding = portal.store.get_binding_for_mac(MAC)
    assert new_binding.user_id != old_user_id
    assert portal.store.get_binding_for_user(old_user_id) == remaining
    assert portal.store.delete_binding_for_mac(MAC, user_id=old_user_id) is False
    portal.store.update_binding_token(old_user_id, device_mac=MAC, device_token_enc="late-old-owner-token")
    portal.store.update_binding_probe(old_user_id, device_mac=MAC, ok=False)
    assert portal.store.get_binding_for_mac(MAC) == new_binding
    assert portal.store.get_binding_for_user(old_user_id) == remaining


@pytest.mark.parametrize("reset_key", [False, True])
def test_failed_completion_rolls_back_identity_ownership_and_consumption(portal, monkeypatch, reset_key):
    key, identity = _identity()
    _bind(portal, key, identity)
    previous = portal.store.get_binding_for_mac(MAC)
    actor = portal.client if reset_key else _second_user(portal.client)
    new_key, new_identity = _identity(number=2 if reset_key else 1, nonce=b"r" * 16)
    _start(portal.client, new_key, new_identity, code="DEF456")
    write = Store._write_binding

    def failed_write(*args, **kwargs):
        write(*args, **kwargs)
        raise RuntimeError("injected-transaction-failure")

    with monkeypatch.context() as patch:
        patch.setattr(Store, "_write_binding", staticmethod(failed_write))
        with pytest.raises(RuntimeError, match="injected-transaction-failure"):
            _claim(actor, new_identity, force=True)
    assert portal.store.get_binding_for_mac(MAC) == previous
    assert portal.store.get_device_identity(MAC)["device_id"] == identity.device_id
    assert portal.store.get_identity_challenge(new_identity)["consumed_at"] is None
    assert portal.store.get_bind_session("DEF456").consumed_at is None
    assert _claim(actor, new_identity, force=True).json()["status"] == "bound"
    assert portal.store.get_device_identity(MAC)["device_id"] == new_identity.device_id


def test_device_selection_and_unbind_reject_other_account(portal):
    key, identity = _identity()
    _bind(portal, key, identity)
    original = portal.store.get_binding_for_mac(MAC)
    other = _second_user(portal.client)
    assert other.post("/api/user/devices/select", json={"device_mac": MAC}).status_code == 404
    assert other.post("/api/user/bind/unbind", json={"device_mac": MAC}).status_code == 404
    assert portal.store.get_binding_for_mac(MAC) == original
    devices = portal.client.get("/api/user/devices").json()["devices"]
    assert devices[0]["active"] is True
    assert devices[0]["device_mac"] == MAC
    assert "device_token_enc" not in devices[0]
