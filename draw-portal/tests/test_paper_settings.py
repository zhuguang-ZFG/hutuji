"""纸张快照、精确绑定与真实产物传递回归。"""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from draw_portal import paper_settings as ps
from draw_portal.crypto import decrypt_token, encrypt_token
from draw_portal.store import Store
from hutuji_mcp.paper_config import PaperConfig
from hutuji_mcp.paper_artifacts import read_paper_metadata, write_paper_metadata


def _setup(tmp_path):
    store = Store(tmp_path / "paper.db")
    user = store.create_wx_user("paper-test-user")
    config = SimpleNamespace(token_cipher_key=Fernet.generate_key(), xiaozhi_api_base="https://invalid.test")
    binding = store.set_device_binding(user_id=user.id, device_mac="aa:bb:cc:dd:ee:ff",
        device_token_enc=encrypt_token(config.token_cipher_key, "old-token"), bound_at=10,
        token_probe_ok=True, machine_type="nopaper")
    return store, config, binding


def _status(paper):
    return json.dumps({"plotter_sku": "nopaper", "connected": True, "ready": True,
                      "paper": "unknown", "paper_config": {
        "schema": 1, "config": paper.to_dict(), "marker": paper.marker,
    }})


def _code(x=20, y=20):
    return ("G21\nG90\nF24000\nG1G90 Z0.0F10000\nG1G90 Z0.0F10000\n"
            "G0 X10 Y10 F24000\nG1G90 Z5.0F10000\n"
            f"G1G90 X{x} Y{y} F24000\nG1G90 Z0.0F10000\n").encode()


def test_read_cache_keeps_only_current_binding(tmp_path):
    store, config, binding = _setup(tmp_path)
    paper = PaperConfig("A2", 420, 594, False, 10, False, 400, 574)
    snapshot = ps.read_settings(store, config, binding, call_tool=lambda **kw: (True, _status(paper)))
    assert snapshot.paper == paper and snapshot.online
    cached = ps.read_settings(store, config, binding, call_tool=lambda **kw: (False, "offline"))
    assert cached.paper == paper and not cached.online
    newer = store.set_device_binding(user_id=binding.user_id, device_mac=binding.device_mac,
        device_token_enc=binding.device_token_enc, bound_at=11, token_probe_ok=True, machine_type="nopaper")
    assert store.get_device_paper(newer) is None
    assert not store.update_device_paper(binding, json.dumps(paper.to_dict()), capable=True)
    assert not store.update_binding_token_if_current(binding, "stale")
    with pytest.raises(ps.PaperSettingsError, match="绑定已变化"):
        ps.read_settings(store, config, binding, call_tool=lambda **kw: (False, "offline"))


def test_owner_changes_during_status_cannot_write_cache(tmp_path):
    store, config, binding = _setup(tmp_path)
    def reply(**kwargs):
        store.delete_binding_for_mac(binding.device_mac, user_id=binding.user_id)
        return True, _status(PaperConfig.default_for("nopaper"))
    with pytest.raises(ps.PaperSettingsError, match="绑定已变化"):
        ps.read_settings(store, config, binding, call_tool=reply)
    assert store.get_device_paper(binding) is None


@pytest.mark.parametrize("minted", ["renewed-token", None])
def test_invalid_credential_heals_once_or_returns_connection_error(tmp_path, monkeypatch, minted):
    store, config, binding = _setup(tmp_path)
    store.update_binding_token_if_current(binding, "invalid-ciphertext")
    calls = []

    def mint(**kwargs):
        assert kwargs["mac"] == binding.device_mac
        calls.append("mint")
        return minted

    def status(**kwargs):
        assert kwargs["device_token"] == minted
        calls.append("status")
        return True, _status(PaperConfig.default_for("nopaper"))

    monkeypatch.setattr("draw_portal.push.mint_messaging_token", mint)
    if minted:
        result = ps.read_settings(store, config, binding, require_live=True, call_tool=status)
        assert result.online and calls == ["mint", "status"]
        current = store.get_binding_for_mac(binding.device_mac)
        assert decrypt_token(config.token_cipher_key, current.device_token_enc) == minted
    else:
        with pytest.raises(ps.PaperSettingsError) as error:
            ps.read_settings(store, config, binding, require_live=True, call_tool=status)
        assert error.value.status == 503 and calls == ["mint"]
        assert store.get_device_paper(binding) is None


@pytest.mark.parametrize("bad", [None, {"schema": True}, {"schema": 2}, {"schema": 1, "config": {}}])
def test_present_invalid_status_cannot_fall_back_to_a4(bad):
    # null 是损坏的新增字段，不能把它误当旧固件缺字段。
    with pytest.raises(ps.PaperSettingsError):
        ps.parse_status(json.dumps({"plotter_sku": "nopaper", "paper": "unknown", "paper_config": bad}), "paper")


@pytest.mark.parametrize("present", ["on", "off", "unknown"])
def test_legacy_paper_telemetry_is_not_a_configuration(present):
    snapshot = ps.parse_status(json.dumps({"plotter_sku": "nopaper", "paper": present,
                                           "connected": True, "ready": True}), "paper")
    assert snapshot.paper == PaperConfig.default_for("nopaper")
    assert snapshot.online and not snapshot.capable


def test_live_configuration_requires_the_plotter_to_be_ready(tmp_path):
    store, config, binding = _setup(tmp_path)
    value = json.loads(_status(PaperConfig.default_for("nopaper")))
    value["ready"] = False
    with pytest.raises(ps.PaperSettingsError) as caught:
        ps.read_settings(store, config, binding, require_live=True, call_tool=lambda **kw: (True, json.dumps(value)))
    assert caught.value.status == 503
    assert store.get_device_paper(binding) is None


@pytest.mark.parametrize("live", [False, True])
def test_paper_save_in_progress_cannot_authorize_a_new_snapshot(tmp_path, live):
    store, config, binding = _setup(tmp_path)
    value = json.loads(_status(PaperConfig.default_for("nopaper")))
    value["paper_config"]["updating"] = True
    with pytest.raises(ps.PaperSettingsError, match="正在保存") as caught:
        ps.read_settings(store, config, binding, require_live=live,
                         call_tool=lambda **kw: (True, json.dumps(value)))
    assert caught.value.status == 409
    assert store.get_device_paper(binding) is None


@pytest.mark.parametrize("updating", [0, 1, "false", None])
def test_invalid_paper_update_state_fails_closed(updating):
    value = json.loads(_status(PaperConfig.default_for("nopaper")))
    value["paper_config"]["updating"] = updating
    with pytest.raises(ps.PaperSettingsError):
        ps.parse_status(json.dumps(value), "nopaper")


@pytest.mark.parametrize("cached", [False, True])
def test_boot_status_cannot_replace_confirmed_machine_or_paper(tmp_path, cached):
    store, config, binding = _setup(tmp_path)
    confirmed = PaperConfig("A2", 420, 594, False, 10, False, 400, 574)
    if cached:
        ps.read_settings(store, config, binding, call_tool=lambda **kw: (True, _status(confirmed)))
    value = json.loads(_status(PaperConfig()))
    value.update(plotter_sku="paper", connected=False, ready=False)
    snapshot = ps.read_settings(store, config, binding, call_tool=lambda **kw: (True, json.dumps(value)))
    assert snapshot.paper == (confirmed if cached else PaperConfig.default_for("nopaper"))
    assert not snapshot.online and snapshot.machine == "nopaper"
    assert store.get_binding_for_mac(binding.device_mac).machine_type == "nopaper"
    row = store.get_device_paper(binding)
    assert (json.loads(row["config_json"]) == confirmed.to_dict()) if cached else row is None


@pytest.mark.parametrize("flags", [{}, {"connected": True}, {"ready": True},
                                    {"connected": 1, "ready": True}])
def test_missing_or_non_boolean_ready_flags_cannot_authorize_send(flags):
    snapshot = ps.parse_status(json.dumps({"plotter_sku": "nopaper", **flags}), "paper")
    assert not snapshot.online


def test_update_checks_expected_confirmation_and_readback(tmp_path):
    store, config, binding = _setup(tmp_path)
    before = PaperConfig.default_for("nopaper")
    desired = PaperConfig("A3", 297, 420, False, 10, False, 277, 400)
    calls = []
    current = [before]
    def call(**kwargs):
        calls.append(kwargs)
        if kwargs["name"] == "hutuji.paper":
            args = kwargs["arguments"]
            assert args["expected"] == before.marker and args["range_confirmed"] is True
            current[0] = PaperConfig.from_dict(json.loads(args["config"]))
        return True, _status(current[0])
    with pytest.raises(ps.PaperSettingsError, match="确认"):
        ps.update_settings(store, config, binding, desired, expected_marker=before.marker,
                           range_confirmed=False, call_tool=call)
    assert all(x["name"] == "hutuji.status" for x in calls)
    result = ps.update_settings(store, config, binding, desired, expected_marker=before.marker,
                               range_confirmed=True, call_tool=call)
    assert result.paper == desired
    assert calls[-1]["name"] == "hutuji.status"
    with pytest.raises(ps.PaperSettingsError, match="已变化"):
        ps.update_settings(store, config, binding, before, expected_marker=before.marker,
                           range_confirmed=True, call_tool=call)


def test_publish_save_and_resend_preserve_large_paper(tmp_path):
    from draw_portal.push import publish_job_files

    paper = PaperConfig("A2", 420, 594, False, 10, False, 400, 574)
    snapshot = ps.PaperSnapshot(paper, "aa:bb:cc:dd:ee:ff", "nopaper")
    job = tmp_path / "job"
    ps.write_job_context(job, snapshot)
    code = job / "draw.gcode"
    code.write_bytes(_code(350, 550))
    write_paper_metadata(code, paper)
    (job / "preview.png").write_bytes(b"png")
    ps.verify_job(job, snapshot)
    published = publish_job_files(job_dir=job, mcp_output_dir=tmp_path / "public", public_base_url="https://example.test")
    copied = tmp_path / "public" / (published.basename + ".gcode")
    assert read_paper_metadata(copied) == paper
    saved = tmp_path / "saved.gcode"
    saved.write_bytes(code.read_bytes())
    ps.copy_saved_context(job, saved)
    context = ps.read_context(saved.with_suffix(".context.json"))
    ps.verify_for_device(saved, snapshot, context=context)
    with pytest.raises(ps.PaperSettingsError, match="另一台"):
        ps.verify_for_device(saved, replace(snapshot, device_mac="11:22:33:44:55:66"), context=context)
    with pytest.raises(ps.PaperSettingsError, match="设置已变化"):
        ps.verify_for_device(saved, replace(snapshot, paper=PaperConfig.default_for("nopaper")), context=context)
    ps.remove_saved_file(saved)
    assert not saved.exists() and not saved.with_suffix(".paper.json").exists()
    assert not saved.with_suffix(".context.json").exists()


def test_old_artifact_cannot_enter_non_default_paper(tmp_path):
    code = tmp_path / "old.gcode"
    code.write_bytes(_code())
    default = PaperConfig.default_for("nopaper")
    ps.verify_for_device(code, ps.PaperSnapshot(default, machine="nopaper"))
    changed = replace(default, margin_mm=11)
    with pytest.raises(ps.PaperSettingsError, match="旧作品"):
        ps.verify_for_device(code, ps.PaperSnapshot(changed, machine="nopaper"))


@pytest.mark.parametrize("changed", ["paper", "device"])
def test_drawpad_changed_paper_or_device_is_rejected_before_conversion(tmp_path, monkeypatch, changed):
    from test_app import _client, _register_and_login

    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)
    calls = []
    monkeypatch.setattr("draw_portal.app.process_upload", lambda *args, **kw: calls.append(kw))
    config = PaperConfig()
    response = client.post("/api/draw/upload", data={
        "paper_marker": "stale" if changed == "paper" else config.marker,
        "device_mac": "aa:bb:cc:dd:ee:ff" if changed == "device" else "",
    }, files={"file": ("draft.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M10 10L20 20"/></svg>', "image/svg+xml")})
    assert response.status_code == 409, response.text
    assert not calls


def test_internal_paper_auth_happens_before_body_validation(tmp_path, monkeypatch):
    from test_app import _client

    monkeypatch.setenv("DRAW_PORTAL_USAGE_INGEST_SECRET", "paper-internal-test")
    client = _client(monkeypatch, tmp_path)
    for body in (b"invalid json", b"x" * 40000):
        reply = client.post("/api/internal/paper/push", content=body)
        assert reply.status_code == 404
    monkeypatch.setattr("draw_portal.app._is_loopback_client", lambda _: True)
    reply = client.post("/api/internal/paper/push", content=b"invalid json",
                        headers={"x-hutuji-usage-secret": "paper-internal-test"})
    assert reply.status_code == 400


def test_internal_paper_resolves_exact_device_and_validates_push(tmp_path, monkeypatch):
    from test_app import _client, _register_and_login, _mock_probe_ok, _announce

    monkeypatch.setenv("DRAW_PORTAL_USAGE_INGEST_SECRET", "paper-internal-test")
    _mock_probe_ok(monkeypatch)
    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)
    monkeypatch.setattr("draw_portal.app._is_loopback_client", lambda _: True)
    monkeypatch.setattr("draw_portal.app.push_tool_call",
                        lambda **kwargs: (True, _status(PaperConfig.default_for("nopaper"))))
    assert client.post("/api/user/bind/claim", json={"mac": "aa:bb:cc:dd:ee:ff"}).status_code == 200
    assert _announce(client).json()["bound"]
    headers = {"x-hutuji-usage-secret": "paper-internal-test"}
    response = client.get("/api/internal/paper", params={"device_id": "aa:bb:cc:dd:ee:ff"}, headers=headers)
    assert response.status_code == 200, response.text
    target = response.json()
    assert target["device_id"] == "aa:bb:cc:dd:ee:ff" and len(target["owner_stamp"]) == 64
    assert client.post("/api/internal/paper/push", json={"target": target, "pages": []}, headers=headers).status_code == 409
    target["owner_stamp"] = "0" * 64
    assert client.post("/api/internal/paper/push", json={"target": target, "pages": []}, headers=headers).status_code == 409
