"""聊天卡确认必须按用户、设备、作品三者定位，旧卡不能确认新预览。"""
import json
from pathlib import Path

import pytest
from draw_portal.paper_settings import PaperSnapshot, write_job_context
from hutuji_mcp.paper_config import PaperConfig
from test_voice_api import _bound_voice_client

def ready_card(store, user, tmp_path, *, mac="aa:bb:cc:dd:ee:ff"):
    job = store.create_draw_job(user_id=user.id, source_name="语音画")
    store.mark_draw_job_ready(job.id, basename="portal_" + job.id.replace("-", ""))
    write_job_context(tmp_path / "data/jobs" / job.id, PaperSnapshot(PaperConfig(), mac))
    return {"job_id": job.id, "device_mac": mac}

@pytest.mark.parametrize("missing", ["job_id", "device_mac"])
def test_voice_confirm_missing_card_identity_sends_nothing(monkeypatch, tmp_path, missing):
    client, store, user, _ = _bound_voice_client(monkeypatch, tmp_path)
    body = ready_card(store, user, tmp_path)
    del body[missing]
    calls = []
    monkeypatch.setattr("draw_portal.app.push_tool_call", lambda **kw: calls.append(kw) or (True, '"started"'))
    response = client.post("/api/voice/confirm", json=body)
    assert response.status_code == 409
    assert calls == []

@pytest.mark.parametrize("invalid", ["unknown", "foreign", "other-device", "missing-context"])
def test_voice_confirm_rejects_wrong_job_or_snapshot(monkeypatch, tmp_path, invalid):
    client, store, user, _ = _bound_voice_client(monkeypatch, tmp_path)
    body = ready_card(store, user, tmp_path, mac="11:22:33:44:55:66" if invalid == "other-device" else "aa:bb:cc:dd:ee:ff")
    body["device_mac"] = "aa:bb:cc:dd:ee:ff"
    if invalid == "unknown":
        body["job_id"] = "../outside"
    elif invalid == "foreign":
        with store._connect() as conn:
            conn.execute("UPDATE draw_jobs SET user_id='other' WHERE id=?", (body["job_id"],))
    elif invalid == "missing-context":
        (tmp_path / "data/jobs" / body["job_id"] / "job-context.json").unlink()
    calls = []
    monkeypatch.setattr("draw_portal.app.push_tool_call", lambda **kw: calls.append(kw) or (True, '"started"'))
    response = client.post("/api/voice/confirm", json=body)
    assert response.status_code in (404, 409)
    assert calls == []

@pytest.mark.parametrize("capability", [False, True])
@pytest.mark.parametrize("current_matches", [False, True])
def test_voice_confirm_compares_card_urls_at_device(monkeypatch, tmp_path, capability, current_matches):
    client, store, user, _ = _bound_voice_client(monkeypatch, tmp_path)
    body = ready_card(store, user, tmp_path)
    job = store.get_draw_job(body["job_id"], user_id=user.id)
    expected = {"expected_url": f"https://example.test/files/{job.basename}.gcode",
                "expected_preview_url": f"https://example.test/files/{job.basename}.png"}
    calls = []
    def device(**kw):
        calls.append(kw)
        if kw["name"] == "hutuji.status":
            return True, json.dumps({"confirm_by_url": capability})
        assert kw["arguments"] == expected
        return True, '"started"' if current_matches else '{"error":"预览已变化，请重新核对作品"}'
    monkeypatch.setattr("draw_portal.app.push_tool_call", device)
    response = client.post("/api/voice/confirm", json=body)
    assert response.status_code == (409 if not capability else 200 if current_matches else 502)
    assert [c["name"] for c in calls] == ["hutuji.status"] + (["hutuji.confirm"] if capability else [])
