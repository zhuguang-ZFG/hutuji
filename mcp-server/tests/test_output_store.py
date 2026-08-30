"""output_store 本地后端与 device 限流契约。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hutuji_mcp import draw_queue, server
from hutuji_mcp.config import Settings, load_settings
from hutuji_mcp.gcode_gen import validate_gcode
from hutuji_mcp.output_store import LocalOutputStore, get_output_store, reset_output_store_for_tests


@pytest.fixture
def memory_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    base = load_settings()
    patched = Settings(
        **{
            **base.__dict__,
            "draw_queue_url": "memory://",
            "draw_queue_max_depth": 50,
            "draw_enqueue_per_second": 100,
        }
    )
    monkeypatch.setattr("hutuji_mcp.config.settings", patched)
    monkeypatch.setattr("hutuji_mcp.draw_queue.settings", patched)
    draw_queue.reset_backend_for_tests()


def _minimal_gcode() -> bytes:
    text = "\n".join(
        [
            "G21",
            "G90",
            "F8000",
            "G1G90 Z0.0F10000",
            "G1G90 Z0.0F10000",
            "G0 X10 Y10 F8000",
            "G1G90 Z5.0F10000",
            "G1G90 X20 Y20 F8000",
            "G1G90 Z0.0F10000",
        ]
    )
    validate_gcode(text, total_bytes=len(text.encode("ascii")))
    return text.encode("ascii")


def test_local_store_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    base = load_settings()
    patched = Settings(
        **{
            **base.__dict__,
            "output_dir": tmp_path,
            "output_backend": "local",
        }
    )
    monkeypatch.setattr(server, "settings", patched)
    reset_output_store_for_tests()
    store = get_output_store()
    assert isinstance(store, LocalOutputStore)
    token = "A" * 22
    stem = f"draw_20260830_120000_{token}"
    with store.job_workspace(stem) as paths:
        paths.gcode_path.write_bytes(_minimal_gcode())
        paths.svg_path.write_text("<svg></svg>", encoding="utf-8")
        paths.preview_path.write_bytes(b"png")
        store.finalize_job(paths)
        name = paths.gcode_path.name
    data, _mtime = store.read_verified(name)
    assert data.startswith(b"G21")


def test_device_rate_limit(memory_queue, monkeypatch):
    base = load_settings()
    patched = Settings(
        **{
            **base.__dict__,
            "draw_queue_url": "memory://",
            "draw_queue_max_depth": 50,
            "draw_enqueue_per_second": 100,
            "draw_per_device_minute": 2,
        }
    )
    monkeypatch.setattr("hutuji_mcp.config.settings", patched)
    monkeypatch.setattr("hutuji_mcp.draw_queue.settings", patched)
    draw_queue.reset_backend_for_tests()

    async def run_case() -> None:
        await draw_queue.attach_or_enqueue("a", "https://hutuji.example", device_id="dev1")
        await draw_queue.attach_or_enqueue("b", "https://hutuji.example", device_id="dev1")
        with pytest.raises(draw_queue.DrawQueueBusyError):
            await draw_queue.attach_or_enqueue("c", "https://hutuji.example", device_id="dev1")

    asyncio.run(run_case())
