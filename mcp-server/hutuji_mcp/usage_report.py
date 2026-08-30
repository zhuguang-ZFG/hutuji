"""MCP 侧 usage 事件上报（loopback → draw-portal internal API）。"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("HUTUJI_MCP")


def record_tool_usage(
    *,
    ingest_url: str,
    ingest_secret: str,
    event_type: str,
    ok: bool | None = None,
    detail: str | None = None,
    user_id: str | None = None,
    device_mac: str | None = None,
    source: str = "mcp",
) -> None:
    url = (ingest_url or "").strip()
    secret = (ingest_secret or "").strip()
    if not url or not secret:
        return
    payload: dict[str, Any] = {
        "source": source,
        "event_type": event_type,
        "ok": ok,
        "detail": (detail or "")[:200] or None,
        "user_id": user_id,
        "device_mac": device_mac,
    }

    def _post() -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Hutuji-Usage-Secret": secret,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status >= 300:
                    logger.warning("usage ingest http=%s", resp.status)
        except urllib.error.HTTPError as exc:
            logger.warning("usage ingest rejected http=%s", exc.code)
        except Exception as exc:
            logger.warning("usage ingest failed type=%s", type(exc).__name__)

    threading.Thread(target=_post, name="hutuji-usage-ingest", daemon=True).start()
