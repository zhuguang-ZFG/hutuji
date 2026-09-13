"""速度必须由设备确认完成；HTTP 200、受理和错误正文不等于已生效。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import draw_portal.app as app_mod
from test_wx_control import _announce, _clients


def _bound(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _, client = _clients(monkeypatch, tmp_path)
    _announce(client, "SPEED1")
    assert client.post("/api/user/bind/complete", json={"bind_code": "SPEED1"}).status_code == 200
    return client


def test_current_control_target_is_separate_from_masked_display(monkeypatch, tmp_path):
    client = _bound(monkeypatch, tmp_path)
    me = client.get("/api/auth/me").json()
    assert "…" in me["binding"]["device_mac"]
    assert me["active_device_mac"] == me["devices"][0]["device_mac"]
    assert "…" not in me["active_device_mac"]
    assert me["devices"][0]["active"] is True


@pytest.mark.parametrize("rate", [0, 2999, 16001, True, 8000.5, "8000", None])
def test_speed_rejects_invalid_input_without_device_call(monkeypatch, tmp_path, rate):
    client = _bound(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(app_mod, "push_tool_call", lambda **kw: calls.append(kw))
    assert client.post("/api/settings/machine-speed", json={"rate": rate}).status_code == 422
    assert calls == []


@pytest.mark.parametrize("payload", [
    {"error": "写入 Y 轴速度失败"},
    "写字机正忙，请稍候再试",
    {"rate": 9000},
    {"rate": "8000"},
    {"isError": True, "content": [{"type": "text", "text": '{"rate":8000}'}]},
    {"success": False, "data": {"rate": 8000}},
])
def test_speed_never_reports_rejection_as_applied(monkeypatch, tmp_path, payload):
    client = _bound(monkeypatch, tmp_path)
    monkeypatch.setattr(app_mod, "push_tool_call", lambda **kw: (True, json.dumps(payload)))
    result = client.post("/api/settings/machine-speed", json={"rate": 8000})
    assert result.status_code == 502
    assert "rate" not in result.json()


@pytest.mark.parametrize("envelope", [
    {"rate": 8000},
    {"success": True, "data": {"rate": 8000}},
    {"isError": False, "content": [{"type": "text", "text": '{"rate":8000}'}]},
])
def test_speed_returns_confirmed_rate(monkeypatch, tmp_path, envelope):
    client = _bound(monkeypatch, tmp_path)
    monkeypatch.setattr(app_mod, "push_tool_call", lambda **kw: (True, json.dumps(envelope)))
    result = client.post("/api/settings/machine-speed", json={"rate": 8000})
    assert result.status_code == 200
    assert result.json()["rate"] == 8000


def test_speed_waits_for_matching_async_completion(monkeypatch, tmp_path):
    client = _bound(monkeypatch, tmp_path)
    calls = []
    replies = iter([
        {"status": "accepted", "request_id": 123, "rate": 8000},
        {"speed": {"state": "pending", "request_id": 123, "requested_rate": 8000}},
        {"speed": {"state": "done", "request_id": 123, "rate": 8000}},
    ])
    def invoke(**kwargs):
        calls.append(kwargs)
        return True, json.dumps({"success": True, "data": next(replies)})
    monkeypatch.setattr(app_mod, "push_tool_call", invoke)
    monkeypatch.setattr("time.sleep", lambda _: None)
    result = client.post("/api/settings/machine-speed", json={"rate": 8000})
    assert result.status_code == 200
    assert result.json()["rate"] == 8000
    assert [c["name"] for c in calls] == ["hutuji.speed", "hutuji.status", "hutuji.status"]


@pytest.mark.parametrize("outcome", [
    {"state": "done", "request_id": 124, "rate": 8000},
    {"state": "error", "request_id": 123, "reason": "写入 Y 轴速度失败"},
    {"state": "done", "request_id": 123, "rate": 16000},
])
def test_speed_async_error_or_other_request_is_not_success(monkeypatch, tmp_path, outcome):
    client = _bound(monkeypatch, tmp_path)
    replies = iter([
        {"status": "accepted", "request_id": 123, "rate": 8000},
        {"speed": outcome},
    ])
    monkeypatch.setattr(app_mod, "push_tool_call", lambda **kw: (True, json.dumps(next(replies))))
    monkeypatch.setattr("time.sleep", lambda _: None)
    result = client.post("/api/settings/machine-speed", json={"rate": 8000})
    assert result.status_code == 502


def test_speed_transport_failure_does_not_echo_upstream_text(monkeypatch, tmp_path):
    client = _bound(monkeypatch, tmp_path)
    monkeypatch.setattr(app_mod, "push_tool_call", lambda **kw: (False, "secret-upstream-token"))
    result = client.post("/api/settings/machine-speed", json={"rate": 8000})
    assert result.status_code == 502
    assert "secret-upstream-token" not in result.text


@pytest.mark.parametrize("axis,rate", [
    ("x", 2999), ("y", 16001), ("z", 99), ("z", 3001), ("z", True),
    ("z", 1200.5), ("z", "1200"), ("X", 10000), ("xyz", 10000), (None, 10000),
])
def test_axis_range_checked_before_any_device_call(monkeypatch, tmp_path, axis, rate):
    client = _bound(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(app_mod, "push_tool_call", lambda **kw: calls.append(kw))
    result = client.post("/api/settings/machine-speed", json={"axis": axis, "rate": rate})
    assert result.status_code == 422
    assert calls == []


def _axis_replies(monkeypatch, *, axis="x", rate=10000, rates=None, outcome=None):
    calls = []
    confirmed = rates or {"x": 10000, "y": 9000, "z": 1200}
    replies = iter([
        {"speed": {"axis_control": True}},
        {"status": "accepted", "request_id": 456, "axis": axis, "rate": rate},
        {"speed": {"state": "pending", "request_id": 456, "axis": axis, "requested_rate": rate}},
        {"speed": outcome if outcome is not None else {
            "state": "done", "request_id": 456, "axis": axis, "requested_rate": rate,
            **({"rate": rate} if rate else {}), "rates": confirmed,
        }},
    ])
    def invoke(**kwargs):
        calls.append(kwargs)
        return True, json.dumps(next(replies))
    monkeypatch.setattr(app_mod, "push_tool_call", invoke)
    monkeypatch.setattr("time.sleep", lambda _: None)
    return calls


@pytest.mark.parametrize("axis,rate", [("x", 10000), ("y", 9000), ("z", 1200)])
def test_single_axis_only_sent_as_selected(monkeypatch, tmp_path, axis, rate):
    client = _bound(monkeypatch, tmp_path)
    calls = _axis_replies(monkeypatch, axis=axis, rate=rate)
    result = client.post("/api/settings/machine-speed", json={"axis": axis, "rate": rate})
    assert result.status_code == 200, result.text
    assert result.json()["axis"] == axis
    assert result.json()["rate"] == rate
    assert result.json()["rates"] == {"x": 10000, "y": 9000, "z": 1200}
    assert [c["name"] for c in calls] == ["hutuji.status", "hutuji.speed", "hutuji.status", "hutuji.status"]
    assert calls[1]["arguments"] == {"rate": rate, "axis": axis}


def test_read_speed_uses_query_without_writes(monkeypatch, tmp_path):
    client = _bound(monkeypatch, tmp_path)
    calls = _axis_replies(monkeypatch, axis="xy", rate=0)
    result = client.get("/api/settings/machine-speed")
    assert result.status_code == 200, result.text
    assert result.json()["rates"] == {"x": 10000, "y": 9000, "z": 1200}
    assert "rate" not in result.json()
    assert calls[1]["arguments"] == {"rate": 0, "axis": "xy"}


@pytest.mark.parametrize("speed", [{}, {"axis_control": False}, {"axis_control": "true"}])
def test_old_firmware_never_receives_single_axis_write(monkeypatch, tmp_path, speed):
    client = _bound(monkeypatch, tmp_path)
    calls = []
    def invoke(**kwargs):
        calls.append(kwargs)
        return True, json.dumps({"speed": speed})
    monkeypatch.setattr(app_mod, "push_tool_call", invoke)
    result = client.post("/api/settings/machine-speed", json={"axis": "x", "rate": 10000})
    assert result.status_code == 409
    assert [c["name"] for c in calls] == ["hutuji.status"]


@pytest.mark.parametrize("updates", [
    {"axis": "y"}, {"axis": None}, {"request_id": 457}, {"requested_rate": 8000},
    {"state": "error"}, {"rates": None}, {"rates": {"x": 10000, "y": 9000}},
    {"rates": {"x": 10000, "y": 9000, "z": True}},
    {"rates": {"x": 9999, "y": 9000, "z": 1200}},
    {"rates": {"x": 10000, "y": float("nan"), "z": 1200}},
    {"rates": {"x": 10000, "y": 9000, "z": 0}},
])
def test_axis_completion_requires_matching_full_readback(monkeypatch, tmp_path, updates):
    client = _bound(monkeypatch, tmp_path)
    outcome = {"state": "done", "request_id": 456, "axis": "x", "requested_rate": 10000,
               "rate": 10000, "rates": {"x": 10000, "y": 9000, "z": 1200}, **updates}
    _axis_replies(monkeypatch, outcome=outcome)
    result = client.post("/api/settings/machine-speed", json={"axis": "x", "rate": 10000})
    assert result.status_code == 502


@pytest.mark.parametrize("method", ["get", "post"])
def test_axis_request_rejects_other_selected_device(monkeypatch, tmp_path, method):
    client = _bound(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(app_mod, "push_tool_call", lambda **kw: calls.append(kw))
    if method == "get":
        result = client.get("/api/settings/machine-speed?device_mac=00:00:00:00:00:00")
    else:
        result = client.post("/api/settings/machine-speed", json={
            "axis": "x", "rate": 10000, "device_mac": "00:00:00:00:00:00",
        })
    assert result.status_code == 409
    assert calls == []
