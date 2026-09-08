"""从 draw-portal internal API 拉取运营后台覆盖项（30s 缓存）。"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("HUTUJI_MCP")

_CACHE_TTL_SECONDS = 30.0
_cache_lock = threading.Lock()
_cache_payload: dict[str, Any] | None = None
_cache_at: float = 0.0
_cache_revision: int = -1

_DEFAULT_IMAGE_BACKENDS = {
    "sensenova": True,
    "agnes": True,
    "siliconflow": True,
    "fallback": True,  # 仅当 HUTUJI_IMAGE_FALLBACK_URL 非空才入池；URL 空则无噪音
    # 2026-09-08 新增：魔搭（t2i 对冲 + 照片 i2i 精修共用 token）与腾讯云开发
    # 混元（仅 t2i）；运营后台可同口径开关。
    "modelscope": True,
    "tcb": True,
}


def resolve_mcp_settings_url(settings: Any) -> str:
    explicit = getattr(settings, "mcp_settings_url", "") or ""
    if explicit.strip():
        return explicit.strip()
    base = getattr(settings, "usage_ingest_url", "") or ""
    if base.endswith("/api/internal/usage"):
        return base.replace("/api/internal/usage", "/api/internal/mcp-settings")
    return "http://127.0.0.1:8302/api/internal/mcp-settings"


def _fetch_remote(settings: Any) -> dict[str, Any]:
    url = resolve_mcp_settings_url(settings)
    secret = getattr(settings, "usage_ingest_secret", "") or ""
    if not url or not secret.strip():
        return {}
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"X-Hutuji-Usage-Secret": secret.strip()},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        logger.warning("mcp-settings fetch rejected http=%s", exc.code)
        return {}
    except Exception as exc:
        logger.warning("mcp-settings fetch failed type=%s", type(exc).__name__)
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("mcp-settings fetch invalid json")
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def get_cached_mcp_settings(settings: Any | None = None) -> dict[str, Any]:
    if settings is None:
        from .config import settings as live_settings

        settings = live_settings
    now = time.monotonic()
    global _cache_payload, _cache_at, _cache_revision
    with _cache_lock:
        if _cache_payload is not None and now - _cache_at < _CACHE_TTL_SECONDS:
            return _cache_payload
    payload = _fetch_remote(settings)
    revision = 0
    if isinstance(payload, dict):
        try:
            revision = int(payload.get("revision") or 0)
        except (TypeError, ValueError):
            revision = 0
    with _cache_lock:
        _cache_payload = payload
        _cache_at = now
        _cache_revision = revision
    return payload


def portal_image_racers(settings: Any | None = None) -> list[dict[str, Any]]:
    data = get_cached_mcp_settings(settings)
    racers = data.get("image_racers") if isinstance(data, dict) else None
    if not isinstance(racers, list) or not racers:
        return []
    out: list[dict[str, Any]] = []
    for item in racers:
        if isinstance(item, dict) and item.get("provider_type"):
            out.append(item)
    return out


def effective_draw_limit(settings: Any, name: str, env_default: int) -> int:
    limits = get_cached_mcp_settings(settings).get("draw_limits") or {}
    if not isinstance(limits, dict):
        return env_default
    raw = limits.get(name)
    if raw is None:
        return env_default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return env_default


def image_backend_enabled(settings: Any, name: str) -> bool:
    backends = get_cached_mcp_settings(settings).get("image_backends") or {}
    if not isinstance(backends, dict):
        return _DEFAULT_IMAGE_BACKENDS.get(name, True)
    raw = backends.get(name)
    if raw is None:
        return _DEFAULT_IMAGE_BACKENDS.get(name, True)
    return bool(raw)


def effective_vpype_max_concurrent(settings: Any, env_default: int) -> int:
    return effective_draw_limit(settings, "vpype_max_concurrent", env_default)
