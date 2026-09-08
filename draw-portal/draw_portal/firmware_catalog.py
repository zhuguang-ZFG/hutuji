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


def recommended_for_board(catalog: dict[str, Any], board: str) -> dict[str, Any] | None:
    boards = catalog.get("boards")
    if not isinstance(boards, dict):
        return None
    entry = boards.get(board)
    if not isinstance(entry, dict):
        return None
    return entry


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
    msg = f"{relpath}:{exp}"
    sig = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{url}?exp={exp}&sig={sig}"


def plan_check(
    *,
    board: str,
    current_version: str,
    catalog: dict[str, Any],
) -> dict[str, Any]:
    entry = recommended_for_board(catalog, board)
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
