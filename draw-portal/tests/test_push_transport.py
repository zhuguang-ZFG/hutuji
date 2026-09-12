"""设备消息 HTTP 成功与工具成功分开判断，网络异常保留窄类型摘要。"""
import json
from http.client import IncompleteRead

import pytest

from draw_portal.push import push_tool_call


class _Reply:
    status = 200

    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


@pytest.mark.parametrize("payload", [
    {"success": False, "message": "private-upstream"},
    {"isError": True, "content": [{"type": "text", "text": "private-upstream"}]},
    {"success": True, "data": {"error": "private-upstream"}},
    {"success": True, "data": {"isError": True, "content": [{"text": "private-upstream"}]}},
    {"content": [{"text": '{"error":"private-upstream"}'}], "isError": False},
])
def test_http_200_tool_failure_is_rejected_without_upstream_echo(monkeypatch, payload):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: _Reply(payload))
    ok, detail = push_tool_call(api_base="https://example.test", device_token="fake", name="hutuji.speed", arguments={"rate": 8000})
    assert not ok
    assert "private-upstream" not in detail


@pytest.mark.parametrize("error", [TimeoutError("private-upstream"), ConnectionResetError("private-upstream"), IncompleteRead(b"private-upstream")])
def test_transport_failure_does_not_escape_as_500(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr("urllib.request.urlopen", fail)
    ok, detail = push_tool_call(api_base="https://example.test", device_token="fake", name="hutuji.status", arguments={})
    assert not ok
    assert type(error).__name__ in detail
    assert "private-upstream" not in detail


def test_speed_integer_is_preserved_and_success_body_returned(monkeypatch):
    def call(request, **kwargs):
        assert json.loads(request.data)["arguments"] == {"rate": 8000}
        return _Reply({"success": True, "data": {"rate": 8000}})
    monkeypatch.setattr("urllib.request.urlopen", call)
    ok, result = push_tool_call(api_base="https://example.test", device_token="fake", name="hutuji.speed", arguments={"rate": 8000})
    assert ok and json.loads(result)["data"]["rate"] == 8000
