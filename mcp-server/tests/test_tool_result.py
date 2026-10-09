"""拒绝原因必须保留设备已知文案，不回显任意网关错误正文。"""
import json

import pytest

from hutuji_mcp import tool_result


@pytest.mark.parametrize("message", ["正在调整速度，请稍候查询结果", "正在升级固件，请等待设备重启", "当前没有待确认的预览", "纸张设置已变化，请刷新后重试"])
@pytest.mark.parametrize("kind", ["data", "content", "nested"])
def test_device_rejection_detail_survives_envelopes(message, kind):
    payload = {"error": message}
    if kind == "data":
        payload = {"success": True, "data": payload}
    else:
        payload = {"isError": False, "content": [{"type": "text", "text": json.dumps(payload)}]}
        if kind == "nested":
            payload = {"success": True, "data": payload}
    raw = json.dumps(payload)
    assert tool_result.tool_result_failed(raw)
    assert tool_result.tool_result_detail(raw, "fallback") == message


@pytest.mark.parametrize("message", ["写字机正忙，请稍候再试", "正在试笔，请稍候", "换纸中无法暂停，换纸完成后可再试"])
def test_legacy_rejection_detail(message):
    assert tool_result.tool_result_detail(json.dumps({"success": True, "data": json.dumps(message)}), "fallback") == message


@pytest.mark.parametrize("payload", [
    {"success": False, "message": "数据库内部凭据 secret"},
    {"isError": True, "content": [{"text": "private-upstream"}]},
    {"error": "<html>网关故障</html>"},
    {"error": "正在调整速度，请稍候查询结果 token=private-upstream"},
    {"error": "秘密" * 1000},
])
def test_unrecognized_upstream_body_stays_private(payload):
    raw = json.dumps(payload)
    assert tool_result.tool_result_failed(raw)
    assert tool_result.tool_result_detail(raw, "fallback") == "fallback"


@pytest.mark.parametrize("flag,value", [("isError", True), ("success", False), ("ok", False)])
def test_outer_failure_cannot_be_hidden_by_success_text(flag, value):
    raw = json.dumps({"success": True, "data": {flag: value, "text": '"started"'}})
    assert tool_result.tool_result_failed(raw)
    assert tool_result.tool_result_detail(raw, "fallback") == "fallback"


@pytest.mark.parametrize("payload", ["已经是暂停状态", {"state": "idle", "text": "正在调整速度，请稍候查询结果"}, {"success": True, "data": "started"}])
def test_success_does_not_gain_failure_detail(payload):
    raw = json.dumps(payload)
    assert not tool_result.tool_result_failed(raw)
    assert tool_result.tool_result_detail(raw, "fallback") == "fallback"


def test_detail_controls_and_reason_token():
    raw = json.dumps({"error": "\u202e正在调整速度，请稍候查询结果\n"})
    assert tool_result.tool_result_detail(raw, "fallback") == "正在调整速度，请稍候查询结果"
    assert tool_result.tool_result_detail('{"error":"grbl_settings_mismatch"}', "fallback") == "写字机参数校验未通过，请检查设备设置"


@pytest.mark.parametrize("layers", [1, 2, 3])
def test_encoded_json_string_does_not_hide_rejection(layers):
    raw = json.dumps({"error": "正在试笔，请稍候"})
    for _ in range(layers):
        raw = json.dumps(raw)
    raw = json.dumps({"success": True, "data": raw})
    assert tool_result.tool_result_failed(raw)
    assert tool_result.tool_result_detail(raw) == "正在试笔，请稍候"
