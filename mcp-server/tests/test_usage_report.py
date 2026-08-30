"""usage_report 异步上报契约。"""
from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from hutuji_mcp.usage_report import record_tool_usage


def test_usage_report_noop_without_secret():
    record_tool_usage(
        ingest_url="http://127.0.0.1:8302/api/internal/usage",
        ingest_secret="",
        event_type="tool_draw",
    )


def test_usage_report_posts_in_background(monkeypatch: pytest.MonkeyPatch):
    posted: list[dict] = []
    done = threading.Event()

    def fake_urlopen(req: urllib.request.Request, timeout: float = 3):
        posted.append(json.loads(req.data.decode("utf-8")))
        done.set()

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    record_tool_usage(
        ingest_url="http://127.0.0.1:8302/api/internal/usage",
        ingest_secret="secret",
        event_type="tool_status",
        ok=True,
        detail="ok",
    )
    assert done.wait(2.0)
    assert posted == [
        {
            "source": "mcp",
            "event_type": "tool_status",
            "ok": True,
            "detail": "ok",
            "user_id": None,
            "device_mac": None,
        }
    ]
