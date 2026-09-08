"""MCP / messaging 回包解包：防小程序假「未连接」。"""
from __future__ import annotations

import json

from draw_portal.mcp_result import unwrap_tool_text


def test_unwrap_xiaozhi_messaging_envelope():
    """现网真值：success + data 对象。"""
    payload = {
        "connected": True,
        "ready": True,
        "state": "idle",
        "grbl_state": "Idle",
    }
    raw = json.dumps(
        {"success": True, "message": "Tools called successfully", "data": payload},
        separators=(",", ":"),
    )
    out = unwrap_tool_text(raw)
    assert json.loads(out) == payload


def test_unwrap_mcp_envelope_extracts_status_json():
    payload = {"connected": True, "ready": True, "state": "idle"}
    raw = json.dumps(
        {
            "content": [{"type": "text", "text": json.dumps(payload, separators=(",", ":"))}],
            "isError": False,
        },
        separators=(",", ":"),
    )
    out = unwrap_tool_text(raw)
    assert json.loads(out) == payload


def test_unwrap_plain_payload_passthrough():
    plain = '{"connected":false,"state":"idle"}'
    assert unwrap_tool_text(plain) == plain
    assert unwrap_tool_text("started") == "started"
    assert unwrap_tool_text("not-json") == "not-json"


def test_unwrap_content_string_variant():
    raw = json.dumps({"content": '{"connected":true}', "isError": False}, separators=(",", ":"))
    assert unwrap_tool_text(raw) == '{"connected":true}'
