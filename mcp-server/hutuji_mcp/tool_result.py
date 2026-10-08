"""门户和云端推送共用的设备业务错误判据，逐层保留信封错误旗标。"""
from __future__ import annotations

import json
from typing import Any


# 旧 S3 把拒绝作为成功字符串返回；精确匹配以保留「已经暂停」等幂等成功。
_LEGACY_REJECTIONS = frozenset({
    "写字机正忙，请稍候再试",
    "正在试笔，请稍候",
    "换纸中无法暂停，换纸完成后可再试",
})


def tool_result_failed(raw: str) -> bool:
    """只认明确业务失败；每轮最多拆一层，不能越过中间层的 isError。"""
    value: Any = raw
    for _ in range(8):
        if isinstance(value, str):
            if value in _LEGACY_REJECTIONS:
                return True
            try:
                value = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                return False
            except RecursionError:
                return True
            if isinstance(value, str):
                return value in _LEGACY_REJECTIONS
        if not isinstance(value, dict):
            return False
        if (value.get("isError") is True or value.get("success") is False
                or value.get("ok") is False or bool(value.get("error"))):
            return True
        if "data" in value and (
            value.get("success") is True or isinstance(value["data"], (dict, str))
        ):
            value = value["data"]
            continue
        content = value.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            value = content[0]
            continue
        if isinstance(content, str):
            value = content
            continue
        if isinstance(value.get("text"), str) and not any(
            key in value for key in ("connected", "ready", "state", "grbl_state")
        ):
            value = value["text"]
            continue
        return False
    return True  # 正常信封远少于八层；超深不能记为受理成功。
