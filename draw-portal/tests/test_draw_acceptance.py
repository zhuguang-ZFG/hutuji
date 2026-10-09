"""真实推送与自动确认链：只有明确受理的新作品可以进入确认。"""
import io
import json

import pytest
from cryptography.fernet import Fernet

from draw_portal.crypto import encrypt_token
from draw_portal.push import push_draw_self_heal
from draw_portal.push import auto_confirm_after_preview, push_draw_to_device
from test_firmware_ota import _clients, _announce


@pytest.mark.parametrize("raw", ["", "<html>bad</html>", '{"data":', 'null', '1', '{}',
    '{"success":true}', '{"ok":true}', '"started"',
    json.dumps({"data": json.dumps(json.dumps({"error": "正在试笔，请稍候"}))})])
def test_unknown_draw_never_confirms_or_retries(monkeypatch, raw):
    calls = []
    class Response(io.BytesIO):
        status = 200
    def transport(request, **kw):
        name = json.loads(request.data)["name"]
        calls.append(name)
        reply = raw if name == "hutuji.draw" else json.dumps({"state": "awaiting_confirmation"})
        return Response(reply.encode())
    monkeypatch.setattr("urllib.request.urlopen", transport)
    key = Fernet.generate_key()
    ok, detail = push_draw_self_heal(
        api_base="https://example.test", token_cipher_key=key,
        device_mac="aa:bb:cc:dd:ee:ff", device_token_enc=encrypt_token(key, "test-token"),
        gcode_url="https://example.test/new.gcode", preview_url="https://example.test/new.png",
        auto_confirm=True,
    )
    assert not ok
    assert detail
    assert calls == ["hutuji.draw"]


@pytest.mark.parametrize("supports_identity", [True, False])
def test_auto_confirm_binds_exact_artifact_and_requires_capability(monkeypatch, supports_identity):
    calls = []
    class Response(io.BytesIO):
        status = 200
    def transport(request, **kw):
        call = json.loads(request.data); calls.append(call)
        if call["name"] == "hutuji.draw":
            data = "previewing"
        elif call["name"] == "hutuji.status":
            data = {"state": "awaiting_confirmation", "confirm_by_url": supports_identity}
        else:
            assert call["arguments"] == {"expected_url": "https://example.test/new.gcode",
                                          "expected_preview_url": "https://example.test/new.png"}
            data = "started"
        return Response(json.dumps({"success": True, "data": data}).encode())
    monkeypatch.setattr("urllib.request.urlopen", transport)
    key = Fernet.generate_key()
    ok, detail = push_draw_self_heal(api_base="https://example.test", token_cipher_key=key,
        device_mac="aa:bb:cc:dd:ee:ff", device_token_enc=encrypt_token(key, "test-token"),
        gcode_url="https://example.test/new.gcode", preview_url="https://example.test/new.png", auto_confirm=True)
    assert ok  # 旧固件仍可接收预览，detail明确要求人工确认。
    assert sum(c["name"] == "hutuji.confirm" for c in calls) == int(supports_identity)
    assert ("已自动开始画" in detail) is supports_identity


def test_control_double_encoded_rejection_is_not_success(monkeypatch, tmp_path):
    client, _ = _clients(monkeypatch, tmp_path)
    _announce(client, "ACCEPT")
    assert client.post("/api/user/bind/complete", json={"bind_code": "ACCEPT"}).status_code == 200
    class Response(io.BytesIO):
        status = 200
    raw = json.dumps({"success": True, "data": json.dumps(json.dumps({"error": "正在试笔，请稍候"}))})
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Response(raw.encode()))
    assert client.post("/api/device/control", json={"tool": "hutuji.abort"}).json() == {
        "status": "error", "detail": "正在试笔，请稍候"}


@pytest.mark.parametrize("raw", ['previewing', '"previewing"',
    '{"success":true,"data":"previewing"}',
    '{"isError":false,"content":[{"type":"text","text":"\\\"previewing\\\""}]}'])
def test_known_draw_acceptance_envelopes(monkeypatch, raw):
    monkeypatch.setattr("draw_portal.push.push_tool_call", lambda **kw: (True, raw))
    assert push_draw_to_device(api_base="https://example.test", device_token="test-token",
        gcode_url="https://example.test/new.gcode", preview_url="https://example.test/new.png")[0]


@pytest.mark.parametrize("raw", ['<html>awaiting_confirmation</html>',
    '{"state":"awaiting_confirmation"', '{"awaiting_confirmation":"false"}', 'null'])
def test_malformed_status_never_authorizes_confirmation(monkeypatch, raw):
    calls = []
    clock = {"now": 0}
    def call(**kw):
        calls.append(kw["name"])
        return True, raw
    monkeypatch.setattr("draw_portal.push.push_tool_call", call)
    monkeypatch.setattr("draw_portal.push.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("draw_portal.push.time.sleep", lambda s: clock.update(now=clock["now"]+s))
    ok, _ = auto_confirm_after_preview(api_base="https://example.test", device_token="test-token",
        timeout_s=3, poll_s=1, expected_url="new", expected_preview_url="png")
    assert not ok
    assert calls == ["hutuji.status"] * 3


def test_replaced_preview_is_not_retried(monkeypatch):
    calls = []
    def call(**kw):
        calls.append(kw["name"])
        if kw["name"] == "hutuji.status":
            return True, '{"state":"awaiting_confirmation","confirm_by_url":true}'
        return False, "预览已变化，请重新核对作品"
    monkeypatch.setattr("draw_portal.push.push_tool_call", call)
    ok, _ = auto_confirm_after_preview(api_base="https://example.test", device_token="test-token",
        expected_url="new", expected_preview_url="png")
    assert not ok
    assert calls == ["hutuji.status", "hutuji.confirm"]


def test_unknown_confirm_does_not_report_success_or_expose_body(monkeypatch):
    clock = {"now": 0}
    def call(**kw):
        if kw["name"] == "hutuji.status":
            return True, '{"state":"awaiting_confirmation","confirm_by_url":true}'
        return True, "<html>private-upstream</html>"
    monkeypatch.setattr("draw_portal.push.push_tool_call", call)
    monkeypatch.setattr("draw_portal.push.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("draw_portal.push.time.sleep", lambda s: clock.update(now=clock["now"]+s))
    ok, detail = auto_confirm_after_preview(api_base="https://example.test", device_token="test-token",
        expected_url="new", expected_preview_url="png", timeout_s=2, poll_s=1)
    assert not ok and "private-upstream" not in detail
