"""小智设备 tools/call 回包解包。

线上实测（2026-09-08 lima）：messaging API 回的是
  {"success":true,"message":"...","data":{connected,ready,state,...}}
不是设备侧 MCP CallToolResult 的 content[].text。
若原样当 status 用 → status.connected 恒 undefined → 小程序假「未连接」，
但 hutuji.manual 不依赖解析仍能控机。

兼容两形态：
1) Xiaozhi messaging：取 data（dict 序列化回 JSON 字符串；str 原样）
2) MCP CallToolResult：取 content[0].text
"""
from __future__ import annotations

import json
from typing import Any
from hutuji_mcp.tool_result import tool_result_failed as tool_result_failed


def unwrap_tool_text(raw: str) -> str:
    """抽出工具正文；非已知信封或解析失败则原样返回。"""
    if not isinstance(raw, str) or not raw:
        return raw if isinstance(raw, str) else ""
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(data, dict):
        return raw

    # 1) 小智官方 messaging /tools/call 信封（现网真值）
    if "data" in data and (
        data.get("success") is True or isinstance(data.get("data"), (dict, str))
    ):
        payload = data["data"]
        if isinstance(payload, dict):
            # 状态类工具：data 即 hutuji.status 对象
            if any(k in payload for k in ("connected", "ready", "state", "grbl_state")):
                return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            # 少数工具 data 再包一层 text
            if isinstance(payload.get("text"), str):
                return payload["text"]
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if isinstance(payload, str):
            return payload

    # 2) 设备侧 MCP CallToolResult（直连/旧路径）
    content = data.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and isinstance(first.get("text"), str):
            return first["text"]
    if isinstance(content, str):
        return content
    return raw
