from __future__ import annotations

import json
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from draw_portal.mcp_result import tool_result_failed, unwrap_tool_text


def probe_device_token(*, api_base: str, device_token: str, timeout: float = 20.0,
                       expected_device_mac: str | None = None) -> tuple[bool, str]:
    """调小智设备消息 API 探活；返回 (ok, 摘要)。禁止把 token 写进摘要。"""
    url = f"{api_base.rstrip('/')}/messaging/device/tools/call"
    body = json.dumps(
        {"name": "hutuji.status", "arguments": {}},
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {device_token}",
        "Content-Type": "application/json",
    }
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read(65537)
            if len(body) > 65536:
                return False, "状态响应过大"
            raw = body.decode("utf-8", errors="replace")
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            if tool_result_failed(raw):
                return False, "设备工具返回错误"
            for _ in range(8):
                unwrapped = unwrap_tool_text(raw)
                if unwrapped == raw:
                    break
                raw = unwrapped
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return False, "响应非 JSON"
            if (not isinstance(data, dict) or not isinstance(data.get("state"), str)
                    or not data["state"] or type(data.get("connected")) is not bool
                    or type(data.get("ready")) is not bool):
                return False, "设备状态形状无效"
            # S3可在线而写字机离线；绑定仅要求S3身份正确，不要求电机就绪。
            if expected_device_mac is not None:
                actual = data.get("device_id")
                if not isinstance(actual, str) or actual.lower() != expected_device_mac.lower():
                    return False, "连接凭据对应的设备不匹配"
            return True, "hutuji.status ok"
    except HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except URLError as exc:
        return False, f"网络 {type(exc.reason).__name__}"
    except (OSError, HTTPException) as exc:
        return False, f"网络 {type(exc).__name__}"
