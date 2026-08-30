"""运营后台生图 provider 契约（类型、校验、脱敏）。"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

PROVIDER_TYPES: tuple[str, ...] = (
    "agnes",
    "sensenova",
    "siliconflow",
    "custom_post",
)

_PROVIDER_TYPE_SET = frozenset(PROVIDER_TYPES)

_EXTRA_ALLOWED_KEYS = frozenset(
    {
        "image_size",
        "batch_size",
        "num_inference_steps",
        "guidance_scale",
        "response_format",
    }
)

_PRIVATE_HOST_RE = re.compile(
    r"^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|::1|\[::1\])",
    re.I,
)


def validate_provider_type(value: str) -> str:
    text = (value or "").strip().lower()
    if text not in _PROVIDER_TYPE_SET:
        raise ValueError(f"provider_type 必须是 {', '.join(PROVIDER_TYPES)} 之一")
    return text


def validate_provider_base_url(value: str, *, provider_type: str) -> str:
    text = (value or "").strip()
    if provider_type == "custom_post":
        parsed = urlsplit(text)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("custom_post 的 base_url 必须是 https 且带主机名")
        host = parsed.hostname.lower()
        if _PRIVATE_HOST_RE.match(host):
            raise ValueError("base_url 不得指向内网或 loopback")
        return text.rstrip("/")
    parsed = urlsplit(text)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("base_url 必须是 https 且带主机名")
    host = parsed.hostname.lower()
    if _PRIVATE_HOST_RE.match(host):
        raise ValueError("base_url 不得指向内网或 loopback")
    return text.rstrip("/")


def validate_provider_model(value: str) -> str:
    text = (value or "").strip()
    if not text or len(text) > 128:
        raise ValueError("model 长度须在 1..128")
    return text


def validate_provider_name(value: str) -> str:
    text = (value or "").strip()
    if not text or len(text) > 80:
        raise ValueError("name 长度须在 1..80")
    return text


def validate_extra_json(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in _EXTRA_ALLOWED_KEYS:
            raise ValueError(f"extra 不允许字段 {key!r}")
        out[key] = value
    return out


def extra_json_from_db(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("extra_json 必须是 object")
    return validate_extra_json(data)


def extra_json_to_db(extra: dict[str, Any] | None) -> str | None:
    if not extra:
        return None
    validated = validate_extra_json(extra)
    return json.dumps(validated, ensure_ascii=False, separators=(",", ":"))


def sanitize_provider_for_admin(row: dict[str, Any], *, keys: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "provider_type": row["provider_type"],
        "base_url": row["base_url"],
        "model": row["model"],
        "extra": extra_json_from_db(row.get("extra_json")),
        "enabled": bool(row.get("enabled")),
        "race_weight": int(row.get("race_weight") or 100),
        "sort_order": int(row.get("sort_order") or 0),
        "updated_at": row.get("updated_at"),
        "keys": keys,
    }


def sanitize_key_for_admin(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "label": row.get("label"),
        "enabled": bool(row.get("enabled")),
        "has_key": bool(row.get("api_key_enc")),
        "disabled_until": row.get("disabled_until"),
        "last_used_at": row.get("last_used_at"),
        "last_error_code": row.get("last_error_code"),
        "updated_at": row.get("updated_at"),
    }
