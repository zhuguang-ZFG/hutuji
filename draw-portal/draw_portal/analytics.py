"""对话与使用事件采集（链 1 固件上报 + 链 2 服务端埋点）。"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any

# 与 protocol §10.3 记忆 value 上限对齐
DEFAULT_MAX_CONTENT = 500
DEFAULT_RETENTION_DAYS = 90

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def truncate_conversation_content(text: str, *, max_len: int = DEFAULT_MAX_CONTENT) -> str:
    cleaned = _CONTROL_CHARS.sub("", (text or "").strip())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1] + "…"


def conversation_dedupe_key(
    *,
    device_mac: str,
    session_id: str,
    role: str,
    content: str,
    client_ts: float | None,
) -> str:
    ts_bucket = int(client_ts) if client_ts is not None else 0
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"{device_mac}:{session_id}:{role}:{ts_bucket}:{digest}"


def normalize_role(role: str) -> str:
    r = (role or "").strip().lower()
    if r in {"user", "assistant", "system"}:
        return r
    raise ValueError("role 无效")


def usage_event_type(name: str) -> str:
    """统一事件名：web_upload、tool_draw、mcp_tool_hutuji_draw 等。"""
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", (name or "").strip())[:64]
    if not cleaned:
        raise ValueError("event_type 为空")
    return cleaned


def retention_cutoff(*, retention_days: int = DEFAULT_RETENTION_DAYS) -> float:
    days = max(1, min(retention_days, 3650))
    return time.time() - days * 86400


def summarize_usage_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        key = str(row.get("event_type") or "unknown")
        out[key] = out.get(key, 0) + int(row.get("count") or 0)
    return out
