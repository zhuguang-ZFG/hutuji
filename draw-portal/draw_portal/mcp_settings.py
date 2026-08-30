"""MCP 进程从 portal internal API 拉取的运营配置快照。"""
from __future__ import annotations

from typing import Any

from draw_portal.draw_limits import DRAW_LIMIT_KEYS
from draw_portal.image_backends import (
    IMAGE_BACKEND_DB_KEYS,
    IMAGE_BACKEND_NAMES,
    default_image_backend_toggles,
)


def build_mcp_settings_payload(
    *,
    draw_limit_overrides: dict[str, int],
    image_backend_raw: dict[str, str],
) -> dict[str, Any]:
    toggles = default_image_backend_toggles()
    for name in IMAGE_BACKEND_NAMES:
        db_key = IMAGE_BACKEND_DB_KEYS[name]
        raw = image_backend_raw.get(db_key)
        if raw is None:
            continue
        toggles[name] = raw.strip() not in ("0", "false", "False")
    return {
        "draw_limits": {k: draw_limit_overrides[k] for k in DRAW_LIMIT_KEYS if k in draw_limit_overrides},
        "image_backends": toggles,
    }
