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
