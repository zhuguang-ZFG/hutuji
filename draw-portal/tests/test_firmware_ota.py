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
_TOKEN_KEY = Fernet.generate_key()


def _bind_ota_device(store, *, device_mac="aa:bb:cc:dd:ee:ff", user_id="u", bound_at=10):
    return store.set_device_binding(
        user_id=user_id, device_mac=device_mac,
        device_token_enc=Fernet(_TOKEN_KEY).encrypt(b"test-device-token").decode(),
        bound_at=bound_at, token_probe_ok=True,
    )


@pytest.fixture
def ota_case(tmp_path):
    from draw_portal.store import Store
    store = Store(tmp_path / "reconcile.db")
    return store, _bind_ota_device(store), _write_catalog(tmp_path)


def _call_ota(entry, store, binding, catalog):
    from draw_portal import firmware_ota as ota
    kwargs = dict(store=store, binding=binding, api_base="https://example.invalid",
                  token_cipher_key=_TOKEN_KEY)
    if entry == "status":
        return ota.get_ota_status(**kwargs)
    kwargs["catalog_path"] = str(catalog)
    if entry == "check":
        return ota.check_firmware(**kwargs)
    return ota.start_upgrade(**kwargs, public_base="https://fw.example.invalid", signing_secret="")


@pytest.mark.parametrize("entry", ["status", "check", "busy"])
@pytest.mark.parametrize("new_mac,new_target,new_time", [
    ("aa:bb:cc:dd:ee:ff", "hutuji.1.0.1", 100),
    ("aa:bb:cc:dd:ee:ff", "hutuji.1.0.2", 100),
    ("aa:bb:cc:dd:ee:ff", "hutuji.1.0.2", 1000),
    ("11:22:33:44:55:66", "hutuji.1.0.2", 100),
])
def test_late_observation_cannot_touch_replacement_session(
    ota_case, monkeypatch, entry, new_mac, new_target, new_time,
):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from draw_portal import firmware_ota as ota
    from draw_portal import store as store_mod

    store, binding, catalog = ota_case
    old = store.upsert_firmware_ota_session(
        user_id=binding.user_id, device_mac=binding.device_mac, state="upgrading",
        target_version="hutuji.1.0.1", progress=20, updated_at=100,
    )
    monkeypatch.setattr(ota, "time", SimpleNamespace(time=lambda: 1000))
    monkeypatch.setattr(store_mod, "time", SimpleNamespace(time=lambda: new_time))
    entered, release = threading.Event(), threading.Event()
    calls = []

    def push(**kw):
        calls.append(kw["name"])
        assert kw["name"] == "hutuji.status"
        if len(calls) == 1:
            entered.set()
            assert release.wait(10)
        return True, _status_json(ota={"state": "failed", "reason": "download_failed"})

    monkeypatch.setattr(ota, "push_tool_call", push)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(_call_ota, entry, store, binding, catalog)
        try:
            assert entered.wait(10), "入口未核对已有会话"
            assert _call_ota("status", store, binding, catalog)["state"] == "failed"
            if new_mac != binding.device_mac:
                _bind_ota_device(store, device_mac=new_mac)
            new = store.reserve_firmware_ota_session(binding.user_id, new_mac)
            assert new and new.session_id != old.session_id
            assert store.set_firmware_ota_target(new, new_target)
            store.accept_firmware_ota_start(new)
            accepted = store.get_firmware_ota_session(binding.user_id)
            before = store.update_firmware_ota_session(
                accepted, state="upgrading", progress=35, reason=None, updated_at=new_time,
            )
        finally:
            release.set()
        response = pending.result(timeout=10)
    assert store.get_firmware_ota_session(binding.user_id) == before
    assert store.reserve_firmware_ota_session(binding.user_id, new_mac) is None
    assert calls == ["hutuji.status", "hutuji.status"]
    if entry == "busy":
        assert response == {"status": "error", "detail": "busy"}
    elif entry == "status":
        assert response["state"] == ("upgrading" if new_mac == binding.device_mac else "idle")


@pytest.mark.parametrize("entry", ["status", "check", "busy"])
def test_stale_failure_cannot_overwrite_concurrent_progress(ota_case, monkeypatch, entry):
    from types import SimpleNamespace
    from draw_portal import firmware_ota as ota
    store, binding, catalog = ota_case
    old = store.upsert_firmware_ota_session(
        user_id=binding.user_id, device_mac=binding.device_mac, state="upgrading",
        target_version="hutuji.1.0.1", progress=20, updated_at=100,
    )
    monkeypatch.setattr(ota, "time", SimpleNamespace(time=lambda: 1000))
    winner = []

    def push(**kw):
        assert kw["name"] == "hutuji.status"
        winner.append(store.update_firmware_ota_session(
            old, state="upgrading", progress=60, reason=None, updated_at=999,
        ))
        return True, _status_json(ota={"state": "failed", "reason": "write_failed"})

    monkeypatch.setattr(ota, "push_tool_call", push)
    _call_ota(entry, store, binding, catalog)
    assert len(winner) == 1
    assert store.get_firmware_ota_session(binding.user_id) == winner[0]
    assert store.reserve_firmware_ota_session(binding.user_id, binding.device_mac) is None


@pytest.mark.parametrize("entry", ["status", "check", "busy"])
@pytest.mark.parametrize("phase,device_ota,version,want", [
    ("upgrading", {"state": "failed", "reason": "verify_failed"}, "hutuji.1.0.0", ("failed", 20, "verify_failed")),
    ("upgrading", {"state": "rolled_back", "reason": "write_failed"}, "hutuji.1.0.0", ("rolled_back", 20, "write_failed")),
    ("upgrading", {"state": "upgrading", "progress": 60}, "hutuji.1.0.0", ("upgrading", 60, None)),
    ("upgrading", {"state": "upgrading", "progress": 100}, "hutuji.1.0.0", ("upgrading", 100, None)),
    ("upgrading", {"state": "failed", "reason": "download_failed"}, "hutuji.1.0.1", ("ok", 100, None)),
    ("uncertain", {"state": "idle"}, "hutuji.1.0.1", ("ok", 100, None)),
    ("uncertain", {"state": "upgrading", "progress": 50}, "hutuji.1.0.0", ("upgrading", 50, None)),
    ("upgrading", {"state": "idle"}, "hutuji.1.0.0", ("failed", 20, "status_timeout")),
])
def test_recovery_entries_reconcile_without_resending(
    ota_case, monkeypatch, entry, phase, device_ota, version, want,
):
    from types import SimpleNamespace
    from draw_portal import firmware_ota as ota
    store, binding, catalog = ota_case
    store.upsert_firmware_ota_session(
        user_id=binding.user_id, device_mac=binding.device_mac, state=phase,
        target_version="hutuji.1.0.1", progress=20, updated_at=100,
    )
    monkeypatch.setattr(ota, "time", SimpleNamespace(time=lambda: 1000))
    calls = []

    def push(**kw):
        calls.append(kw["name"])
        return True, _status_json(version=version, ota=device_ota)

    monkeypatch.setattr(ota, "push_tool_call", push)
    response = _call_ota(entry, store, binding, catalog)
    after = store.get_firmware_ota_session(binding.user_id)
    assert (after.state, after.progress, after.reason) == want
    assert calls == ["hutuji.status"]
    if entry == "busy":
        assert response == {"status": "error", "detail": "busy"}


@pytest.mark.parametrize("entry", ["status", "check", "busy"])
@pytest.mark.parametrize("phase,raw,when", [
    ("starting", "failed", 999),
    ("starting", "progress", 999),
    ("starting", "matched", 999),
    ("uncertain", "failed", 999),
    ("upgrading", "same_progress", 100),
    ("upgrading", "lower_progress", 100),
    ("uncertain", "offline", 100),
    ("upgrading", "{}", 100),
    ("upgrading", '{"state":"idle","firmware_version":"bad","ota":{"state":"idle"}}', 100),
    ("upgrading", '{"state":"idle","firmware_version":"hutuji.1.0.0","ota":{"state":"unknown"}}', 100),
])
def test_unconfirmed_observation_preserves_reservation(ota_case, monkeypatch, entry, phase, raw, when):
    from types import SimpleNamespace
    from draw_portal import firmware_ota as ota
    store, binding, catalog = ota_case
    before = store.upsert_firmware_ota_session(
        user_id=binding.user_id, device_mac=binding.device_mac, state=phase,
        target_version="hutuji.1.0.1", progress=20, updated_at=when,
    )
    monkeypatch.setattr(ota, "time", SimpleNamespace(time=lambda: 1000))
    responses = {
        "failed": _status_json(ota={"state": "failed", "reason": "download_failed"}),
        "progress": _status_json(ota={"state": "upgrading", "progress": 50}),
        "matched": _status_json(version="hutuji.1.0.1", ota={"state": "idle"}),
        "same_progress": _status_json(ota={"state": "upgrading", "progress": 20}),
        "lower_progress": _status_json(ota={"state": "upgrading", "progress": 10}),
    }
    calls = []

    def push(**kw):
        calls.append(kw["name"])
        return (False, "网络 timeout") if raw == "offline" else (True, responses.get(raw, raw))

    monkeypatch.setattr(ota, "push_tool_call", push)
    _call_ota(entry, store, binding, catalog)
    assert store.get_firmware_ota_session(binding.user_id) == before
    assert store.reserve_firmware_ota_session(binding.user_id, binding.device_mac) is None
    assert calls == ["hutuji.status"]


@pytest.mark.parametrize("entry", ["status", "check", "busy"])
@pytest.mark.parametrize("change", ["unbind", "rebind", "transfer"])
@pytest.mark.parametrize("during_io", [False, True])
def test_observation_requires_original_binding_ownership(ota_case, monkeypatch, entry, change, during_io):
    from draw_portal import firmware_ota as ota
    store, binding, catalog = ota_case
    before = store.upsert_firmware_ota_session(
        user_id=binding.user_id, device_mac=binding.device_mac, state="upgrading",
        target_version="hutuji.1.0.1", updated_at=100,
    )

    def replace_binding():
        assert store.delete_binding_for_mac(binding.device_mac, user_id=binding.user_id)
        if change != "unbind":
            _bind_ota_device(store, user_id="other" if change == "transfer" else binding.user_id,
                             bound_at=binding.bound_at + 1)

    calls = []

    def push(**kw):
        calls.append(kw["name"])
        replace_binding()
        return True, _status_json(version="hutuji.1.0.1", ota={"state": "idle"})

    monkeypatch.setattr(ota, "push_tool_call", push)
    if not during_io:
        replace_binding()
    result = _call_ota(entry, store, binding, catalog)
    assert store.get_firmware_ota_session(binding.user_id) == before
    assert calls == (["hutuji.status"] if during_io else [])
    if entry == "check":
        assert result["reason"] == "check_failed"
    elif entry == "status":
        assert "firmware_version" not in result


@pytest.mark.parametrize("entry", ["status", "check", "busy"])
def test_other_device_recovery_cannot_release_original_ledger(ota_case, monkeypatch, entry):
    from draw_portal import firmware_ota as ota
    store, binding, catalog = ota_case
    before = store.upsert_firmware_ota_session(
        user_id=binding.user_id, device_mac=binding.device_mac, state="upgrading",
        target_version="hutuji.1.0.1", updated_at=100,
    )
    other = _bind_ota_device(store, device_mac="11:22:33:44:55:66")
    calls = []

    def push(**kw):
        calls.append(kw["name"])
        return True, _status_json(version="hutuji.1.0.1", ota={"state": "idle"})

    monkeypatch.setattr(ota, "push_tool_call", push)
    _call_ota(entry, store, other, catalog)
    assert store.get_firmware_ota_session(binding.user_id) == before
    assert store.reserve_firmware_ota_session(binding.user_id, other.device_mac) is None
    assert calls == ([] if entry == "busy" else ["hutuji.status"])


@pytest.mark.parametrize("same_device", [False, True])
def test_reservation_conflict_recovers_but_requires_new_upgrade_request(ota_case, monkeypatch, same_device):
    from draw_portal import firmware_ota as ota
    store, binding, catalog = ota_case
    reserve = store.reserve_firmware_ota_session
    winner = []
    calls = []

    def conflict(user_id, device_mac):
        other_mac = device_mac if same_device else "11:22:33:44:55:66"
        session = reserve(user_id, other_mac)
        assert session and store.set_firmware_ota_target(session, "hutuji.1.0.1")
        store.accept_firmware_ota_start(session)
        winner.append(store.get_firmware_ota_session(user_id))
        return reserve(user_id, device_mac)

    def push(**kw):
        calls.append(kw["name"])
        return True, _status_json(version="hutuji.1.0.1", ota={"state": "idle"})

    monkeypatch.setattr(store, "reserve_firmware_ota_session", conflict)
    monkeypatch.setattr(ota, "push_tool_call", push)
    assert _call_ota("busy", store, binding, catalog) == {"status": "error", "detail": "busy"}
    after = store.get_firmware_ota_session(binding.user_id)
    assert len(winner) == 1
    assert after.session_id == winner[0].session_id
    assert after.state == ("ok" if same_device else "upgrading")
    assert calls == (["hutuji.status"] if same_device else [])


def test_stale_other_device_check_preserves_ota_session(tmp_path):
    import time
    from draw_portal import firmware_ota as ota
    from draw_portal.store import Store
    store = Store(tmp_path / "isolation.db")
    before = store.upsert_firmware_ota_session(
        user_id="u", device_mac="aa:bb:cc:dd:ee:ff", state="uncertain",
        target_version="hutuji.1.0.49", updated_at=time.time() - 700,
    )
    assert not ota.is_upgrading(store, "u", device_mac="11:22:33:44:55:66")
    assert store.get_firmware_ota_session("u") == before
    assert store.reserve_firmware_ota_session("u", "11:22:33:44:55:66") is None


@pytest.mark.parametrize("raw", [
    "{}", "<html>gateway</html>", '{"success":true}',
    '{"firmware_version":"hutuji.1.0.48"}',
    '{"state":"idle","firmware_version":"hutuji.1.0.48","ota":{"state":"unknown"}}',
    '{"state":"idle","firmware_version":"bad","ota":{"state":"idle"}}',
    '{"state":"alien","firmware_version":"hutuji.1.0.48","ota":{"state":"idle"}}',
])
def test_invalid_status_cannot_expire_ota(tmp_path, monkeypatch, raw):
    import time
    from types import SimpleNamespace
    from draw_portal import firmware_ota as ota
    from draw_portal.store import Store
    store = Store(tmp_path / "invalid-status.db")
    binding = _bind_ota_device(store)
    before = store.upsert_firmware_ota_session(
        user_id="u", device_mac=binding.device_mac, state="uncertain",
        target_version="hutuji.1.0.49", updated_at=time.time() - 700,
    )
    assert not ota.tool_result_failed(raw)
    monkeypatch.setattr(ota, "_push_healed", lambda **kw: (True, raw))
    result = ota.get_ota_status(store=store, binding=binding, api_base="", token_cipher_key=b"")
    assert result["state"] == "upgrading"
    assert store.get_firmware_ota_session("u") == before


def test_expiration_cannot_clear_target_added_after_snapshot(tmp_path):
    from draw_portal.store import Store
    store = Store(tmp_path / "target-race.db")
    before = store.reserve_firmware_ota_session("u", "aa:bb:cc:dd:ee:ff")
    assert store.set_firmware_ota_target(before, "hutuji.1.0.49")
    store.expire_firmware_ota_session("u", cutoff=before.updated_at, expected=before)
    after = store.get_firmware_ota_session("u")
    assert after.state == "starting" and after.target_version == "hutuji.1.0.49"


def test_expiration_cannot_clear_replacement_with_same_timestamp(tmp_path):
    from draw_portal.store import Store
    store = Store(tmp_path / "generation-race.db")
    old = store.upsert_firmware_ota_session(user_id="u", device_mac="aa:bb:cc:dd:ee:ff",
                                          state="uncertain", target_version="hutuji.1.0.49", updated_at=100)
    new = store.upsert_firmware_ota_session(user_id="u", device_mac="11:22:33:44:55:66",
                                          state="uncertain", target_version="hutuji.1.0.50", updated_at=100)
    store.expire_firmware_ota_session("u", cutoff=100, expected=old)
    assert store.get_firmware_ota_session("u") == new


def test_concurrent_upgrade_claims_before_device_io(monkeypatch, tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from draw_portal import firmware_ota as ota
    from draw_portal.store import Store
    store = Store(tmp_path / "concurrent.db")
    binding = _bind_ota_device(store)
    entered, release = threading.Event(), threading.Event()
    pushes = []
    def plan(**kw):
        entered.set()
        assert release.wait(5)
        return {"update_available": True, "board": _BOARD, "latest": "hutuji.1.0.1"}
    monkeypatch.setattr(ota, "check_firmware", plan)
    monkeypatch.setattr(ota, "load_catalog", lambda p: {})
    monkeypatch.setattr(ota, "recommended_for_board", lambda *a: {"version": "hutuji.1.0.1", "relpath": _REL})
    monkeypatch.setattr(ota, "_fetch_device_status", lambda **kw: (True, {
        "state": "idle", "board": _BOARD, "firmware_version": "hutuji.1.0.0"}, ""))
    monkeypatch.setattr(ota, "build_bin_url", lambda *a, **kw: "https://example.invalid/x.bin")
    monkeypatch.setattr(ota, "_push_healed", lambda **kw: (pushes.append(1) or True, '{"ok":true}'))
    def start():
        return ota.start_upgrade(store=store, binding=binding, catalog_path="stub",
            public_base="stub", signing_secret="", api_base="stub", token_cipher_key=b"")
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(start)
        try:
            assert entered.wait(5)
            assert start() == {"status": "error", "detail": "busy"}
        finally:
            release.set()
        assert first.result(timeout=5)["status"] == "started"
    assert len(pushes) == 1
    assert store.get_firmware_ota_session("u").state == "upgrading"


def test_old_start_and_status_cannot_overwrite_new_ota_generation(tmp_path):
    from draw_portal.store import Store
    store = Store(tmp_path / "generation.db")
    first = store.reserve_firmware_ota_session("u", "aa:bb:cc:dd:ee:ff")
    assert first is not None
    store.expire_firmware_ota_session("u", cutoff=first.updated_at)
    second = store.reserve_firmware_ota_session("u", "aa:bb:cc:dd:ee:ff")
    assert second and second.session_id != first.session_id
    assert not store.set_firmware_ota_target(first, "old")
    store.fail_firmware_ota_start(first, "busy")
    store.update_firmware_ota_session(first, state="failed", progress=0, reason="busy", updated_at=0)
    assert store.get_firmware_ota_session("u") == second


def test_failed_upgrade_check_releases_reservation(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from draw_portal import firmware_ota as ota
    from draw_portal.store import Store
    store = Store(tmp_path / "failure.db")
    binding = _bind_ota_device(store)
    def fail(**kw):
        raise OSError("模拟目录读取失败")
    monkeypatch.setattr(ota, "check_firmware", fail)
    with pytest.raises(OSError):
        ota.start_upgrade(store=store, binding=binding, catalog_path="stub", public_base="stub",
            signing_secret="", api_base="stub", token_cipher_key=b"")
    assert not ota.is_upgrading(store, "u")


def test_poll_during_start_cannot_apply_previous_device_failure(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from draw_portal import firmware_ota as ota
    from draw_portal.store import Store
    store = Store(tmp_path / "pending.db")
    binding = _bind_ota_device(store)
    old_status = {"state": "idle", "board": _BOARD, "firmware_version": "hutuji.1.0.0",
                  "ota": {"state": "failed", "reason": "download_failed"}}
    monkeypatch.setattr(ota, "_fetch_device_status", lambda **kw: (True, old_status, ""))
    def poll():
        result = ota.get_ota_status(store=store, binding=binding, api_base="", token_cipher_key=b"")
        assert result["state"] == "upgrading"
        assert ota.is_upgrading(store, "u")
    def plan(**kw):
        poll()  # 占位已建立，目标尚未查出。
        return {"update_available": True, "board": _BOARD, "latest": "hutuji.1.0.1"}
    def push(**kw):
        poll()  # 已登记目标，但发送尚未应答，仍不能消费旧状态。
        return True, '{"ok":true}'
    monkeypatch.setattr(ota, "check_firmware", plan)
    monkeypatch.setattr(ota, "load_catalog", lambda p: {})
    monkeypatch.setattr(ota, "recommended_for_board", lambda *a: {"version": "hutuji.1.0.1", "relpath": _REL})
    monkeypatch.setattr(ota, "build_bin_url", lambda *a, **kw: "https://example.invalid/x.bin")
    monkeypatch.setattr(ota, "_push_healed", push)
    assert ota.start_upgrade(store=store, binding=binding, catalog_path="", public_base="",
                            signing_secret="", api_base="", token_cipher_key=b"")["status"] == "started"
    assert store.get_firmware_ota_session("u").state == "upgrading"


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


def test_stalled_idle_ota_expires_when_device_reachable(tmp_path, monkeypatch):
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
    monkeypatch.setattr(firmware_ota, "push_tool_call", lambda **kw: (True, _status_json(ota={"state": "idle", "progress": 90})))
    assert client.get("/api/device/firmware/ota_status").json()["state"] == "upgrading"
    assert store.get_firmware_ota_session(binding.user_id).updated_at == started
    clock[0] = started + 600
    response = client.get("/api/device/firmware/ota_status").json()
    assert response["state"] == "failed"
    assert response["reason"] == "status_timeout"
    assert client.post("/api/draw/nonexistent/send").status_code == 404


def test_stalled_upgrading_ota_keeps_lock(tmp_path, monkeypatch):
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
    clock = [started + 600]
    monkeypatch.setattr(firmware_ota, "time", SimpleNamespace(time=lambda: clock[0]))
    store.upsert_firmware_ota_session(user_id=binding.user_id, device_mac=binding.device_mac,
                                    state="upgrading", target_version="hutuji.1.0.1", updated_at=started)
    monkeypatch.setattr(firmware_ota, "push_tool_call", lambda **kw: (True, _status_json(ota={"state": "upgrading", "progress": 0})))
    response = client.get("/api/device/firmware/ota_status").json()
    assert response["state"] == "upgrading"
    assert firmware_ota.is_upgrading(store, binding.user_id, device_mac=binding.device_mac)
    assert client.post("/api/draw/nonexistent/send").status_code == 400


def test_unreachable_device_does_not_expire_ota_lock(tmp_path, monkeypatch):
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
    clock = [started + 600]
    monkeypatch.setattr(firmware_ota, "time", SimpleNamespace(time=lambda: clock[0]))
    store.upsert_firmware_ota_session(user_id=binding.user_id, device_mac=binding.device_mac,
                                    state="uncertain", target_version="hutuji.1.0.1", updated_at=started)
    monkeypatch.setattr(firmware_ota, "push_tool_call", lambda **kw: (False, "网络 timeout"))
    response = client.get("/api/device/firmware/ota_status").json()
    assert response["state"] == "upgrading"
    assert firmware_ota.is_upgrading(store, binding.user_id, device_mac=binding.device_mac)
    assert client.post("/api/draw/nonexistent/send").status_code == 400


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


@pytest.mark.parametrize("failure", ["timeout", "wrapped_timeout", "disconnect", "truncated"])
def test_upgrade_lost_response_from_real_transport_keeps_reservation(tmp_path, monkeypatch, failure):
    """故障从 urlopen/read 注入，避免模拟摘要掩盖真实 TimeoutError 大小写。"""
    import io
    from http.client import IncompleteRead, RemoteDisconnected
    from urllib.error import URLError

    _, client = _clients(monkeypatch, tmp_path)
    _announce(client, "LOSS01")
    assert client.post("/api/user/bind/complete", json={"bind_code": "LOSS01"}).status_code == 200
    failures = {"timeout": TimeoutError(), "wrapped_timeout": URLError(TimeoutError()),
                "disconnect": RemoteDisconnected(), "truncated": IncompleteRead(b"{", 10)}

    class Response(io.BytesIO):
        status = 200

    def transport(request, **kwargs):
        if json.loads(request.data)["name"] == "hutuji.status":
            return Response(_status_json().encode())
        raise failures[failure]

    monkeypatch.setattr("urllib.request.urlopen", transport)
    result = client.post("/api/device/firmware/upgrade").json()
    assert result["status"] == "unknown"
    assert client.get("/api/device/firmware/ota_status").json()["state"] == "upgrading"
    assert client.post("/api/device/firmware/upgrade").json()["detail"] == "busy"


def test_unknown_delivery_ignores_old_failure_until_upgrade_is_observed(tmp_path, monkeypatch):
    _, client = _clients(monkeypatch, tmp_path)
    _announce(client, "TTTT01")
    assert client.post("/api/user/bind/complete", json={"bind_code": "TTTT01"}).status_code == 200
    device_ota = {"state": "failed", "reason": "download_failed"}
    def push(**kw):
        if kw["name"] == "hutuji.status":
            return True, _status_json(ota=device_ota)
        return False, "HTTP 504"
    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", push)
    assert client.post("/api/device/firmware/upgrade").json()["status"] == "unknown"
    # 504 时尚不知命令是否送达，旧失败不能解除忙闸或允许重复下发。
    assert client.get("/api/device/firmware/ota_status").json()["state"] == "upgrading"
    assert client.post("/api/device/firmware/upgrade").json()["detail"] == "busy"
    device_ota = {"state": "upgrading", "progress": 0}
    assert client.get("/api/device/firmware/ota_status").json()["state"] == "upgrading"
    device_ota = {"state": "failed", "reason": "verify_failed"}
    final = client.get("/api/device/firmware/ota_status").json()
    assert final["state"] == "failed" and final["reason"] == "verify_failed"


@pytest.mark.parametrize("body", [b'{"success":true,"data":', b'', b'<html>unavailable</html>',
                                  b'{}', b'null', b'[]', b'1', b'{"ok":1}', b'{"success":true}',
                                  b'{"reason":{}}', b'{"detail":[]}'])
def test_unconfirmed_ota_body_keeps_reservation(tmp_path, monkeypatch, body):
    """HTTP读取完成不等于拿到设备受理结果，不能消费旧failed或重复下发。"""
    import io

    _, client = _clients(monkeypatch, tmp_path)
    _announce(client, "BODY01")
    assert client.post("/api/user/bind/complete", json={"bind_code": "BODY01"}).status_code == 200
    starts = []
    device_ota = {"state": "failed", "reason": "download_failed"}

    class Response(io.BytesIO):
        status = 200

    def transport(request, **kwargs):
        if json.loads(request.data)["name"] == "hutuji.status":
            return Response(_status_json(ota=device_ota).encode())
        starts.append(1)
        return Response(body)

    monkeypatch.setattr("urllib.request.urlopen", transport)
    assert client.post("/api/device/firmware/upgrade").json()["status"] == "unknown"
    assert client.get("/api/device/firmware/ota_status").json()["state"] == "upgrading"
    assert client.post("/api/device/firmware/upgrade").json()["detail"] == "busy"
    assert len(starts) == 1
    device_ota = {"state": "upgrading", "progress": 20}
    assert client.get("/api/device/firmware/ota_status").json()["state"] == "upgrading"
    device_ota = {"state": "failed", "reason": "verify_failed"}
    final = client.get("/api/device/firmware/ota_status").json()
    assert final["state"] == "failed" and final["reason"] == "verify_failed"


@pytest.mark.parametrize("payload", [{"ok": True}, {"success": True, "data": {"ok": True}},
    {"success": True, "data": {"content": [{"type": "text", "text": '{"ok":true}'}]}},
    '{"ok":true}'])
def test_ota_accepts_explicit_device_acknowledgement(tmp_path, monkeypatch, payload):
    import io

    _, client = _clients(monkeypatch, tmp_path)
    _announce(client, "BODY02")
    assert client.post("/api/user/bind/complete", json={"bind_code": "BODY02"}).status_code == 200

    class Response(io.BytesIO):
        status = 200

    def transport(request, **kwargs):
        return Response(_status_json().encode() if json.loads(request.data)["name"] == "hutuji.status"
                        else json.dumps(payload).encode())

    monkeypatch.setattr("urllib.request.urlopen", transport)
    assert client.post("/api/device/firmware/upgrade").json()["status"] == "started"


@pytest.mark.parametrize("state,reason", [("ok", None), ("failed", "status_timeout")])
def test_late_failure_cannot_regress_terminal_ota_session(tmp_path, monkeypatch, state, reason):
    from draw_portal import firmware_ota as ota
    from draw_portal.store import Store
    from types import SimpleNamespace
    store = Store(tmp_path / "terminal.db")
    binding = _bind_ota_device(store)
    store.upsert_firmware_ota_session(user_id="u", device_mac=binding.device_mac, state=state,
                                    reason=reason, target_version="v2")
    monkeypatch.setattr(ota, "_fetch_device_status", lambda **kw: (True,
        {"firmware_version": "v1", "ota": {"state": "failed", "reason": "download_failed"}}, ""))
    result = ota.get_ota_status(store=store, binding=binding, api_base="", token_cipher_key=b"")
    assert result["state"] == state and result["reason"] == reason


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
