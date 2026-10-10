from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

from draw_portal.firmware_version import is_newer


def load_catalog(path: str | Path) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("catalog root must be object")
    return data


def recommended_for_board(catalog: dict[str, Any], board: str, device_mac: str = "") -> dict[str, Any] | None:
    boards = catalog.get("boards")
    if not isinstance(boards, dict):
        return None
    entry = boards.get(board)
    if not isinstance(entry, dict):
        return None
    rollout = entry.get("rollout")
    if rollout is None:
        return entry
    # stable条目保留给未升级的设备直查latest；门户仅为明确MAC选择灰度candidate。
    if not isinstance(rollout, dict):
        raise ValueError("invalid rollout")
    candidate = rollout.get("candidate")
    percent = rollout.get("percent", 0)
    salt = rollout.get("id")
    allow = rollout.get("devices", [])
    if (not isinstance(candidate, dict) or "rollout" in candidate
            or type(percent) is not int or not 0 <= percent <= 100
            or not isinstance(salt, str) or not salt
            or not isinstance(allow, list)):
        raise ValueError("invalid rollout policy")
    import re
    from .firmware_version import parse_hutuji_version
    if (not parse_hutuji_version(str(candidate.get('version','')))
            or not isinstance(candidate.get('relpath'),str)
            or not candidate['relpath'].startswith('s3/'+board+'/')
            or any(part in ('','.', '..') for part in candidate['relpath'].split('/'))
            or not candidate['relpath'].endswith('/xiaozhi.bin')
            or not re.fullmatch('[0-9a-f]{64}',str(candidate.get('sha256','')))
            or type(candidate.get('size')) is not int or not 0<candidate['size']<=8*1024*1024):
        raise ValueError('invalid rollout firmware')
    def normalized(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", value):
            raise ValueError("invalid rollout MAC")
        return value.lower()
    allow = {normalized(value) for value in allow}
    if not device_mac:
        return entry
    target = normalized(device_mac)
    bucket = int(hashlib.sha256((salt + ":" + target).encode()).hexdigest()[:8], 16) % 100
    return candidate if target in allow or bucket < percent else entry


def build_bin_url(
    public_base: str,
    relpath: str,
    *,
    secret: str | None = None,
    ttl_sec: int = 900,
) -> str:
    base = public_base.rstrip("/")
    rel = relpath.lstrip("/")
    url = f"{base}/firmware/{rel}"
    if not secret:
        return url
    exp = int(time.time()) + ttl_sec
    # 签名必须与 URL 路径段一致：用 lstrip 后的 rel，避免 leading slash 导致验签失败
    msg = f"{rel}:{exp}"
    sig = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{url}?exp={exp}&sig={sig}"


def plan_check(
    *,
    board: str,
    current_version: str,
    catalog: dict[str, Any],
    device_mac: str = "",
) -> dict[str, Any]:
    entry = recommended_for_board(catalog, board, device_mac)
    if entry is None:
        return {
            "update_available": False,
            "current": current_version,
            "latest": None,
            "notes": "",
            "reason": "board_mismatch",
        }
    latest = str(entry.get("version") or "")
    notes = str(entry.get("notes") or "")
    if is_newer(latest, current_version):
        return {
            "update_available": True,
            "current": current_version,
            "latest": latest,
            "notes": notes,
        }
    return {
        "update_available": False,
        "current": current_version,
        "latest": latest,
        "notes": notes,
        "reason": "up_to_date",
    }
