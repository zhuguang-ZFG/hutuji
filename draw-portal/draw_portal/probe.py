from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def probe_device_token(*, api_base: str, device_token: str, timeout: float = 20.0) -> tuple[bool, str]:
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
            raw = resp.read().decode("utf-8", errors="replace")
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return False, "响应非 JSON"
            if isinstance(data, dict) and data.get("error"):
                return False, "JSON-RPC error"
            return True, "hutuji.status ok"
    except HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except URLError as exc:
        return False, f"网络 {type(exc.reason).__name__}"
