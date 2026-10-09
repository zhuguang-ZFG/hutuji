"""真实控制路由接真实推送判据，设备HTTP为替身，不发送硬件命令。"""
import io
import json

import pytest

from test_firmware_ota import _clients, _announce


@pytest.mark.parametrize("message", ["正在调整速度，请稍候查询结果", "正在升级固件，请等待设备重启", "当前没有待确认的预览", "纸张设置已变化，请刷新后重试"])
@pytest.mark.parametrize("transport", ["http", "route_guard"])
def test_control_preserves_device_rejection_reason(monkeypatch, tmp_path, message, transport):
    client, _ = _clients(monkeypatch, tmp_path)
    _announce(client, "AAAAAA")
    assert client.post("/api/user/bind/complete", json={"bind_code": "AAAAAA"}).status_code == 200
    raw = json.dumps({"success": True, "data": {"isError": False, "content": [
        {"type": "text", "text": json.dumps({"error": message})}
    ]}})
    calls = []

    class Reply(io.BytesIO):
        status = 200

    def urlopen(request, **kwargs):
        calls.append(json.loads(request.data)["name"])
        return Reply(raw.encode("utf-8"))

    def push(**kwargs):
        calls.append(kwargs["name"])
        return True, raw

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    if transport == "route_guard":
        monkeypatch.setattr("draw_portal.app.push_tool_call", push)
    response = client.post("/api/device/control", json={"tool": "hutuji.abort", "device_mac": "aa:bb:cc:dd:ee:ff"})
    assert response.status_code == 200
    assert response.json() == {"status": "error", "detail": message}
    assert calls == ["hutuji.abort"]
