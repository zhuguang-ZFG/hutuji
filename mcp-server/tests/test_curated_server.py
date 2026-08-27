"""Curated replacement routing at the draw-job boundary."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from hutuji_mcp import curated, quickdraw, server
from hutuji_mcp.bitmap_svg import BitmapResult

_BASE_URL = "http://127.0.0.1:8300"
_CURATED_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1 L2 2"/></svg>'
_AI_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M3 3 L4 4"/></svg>'


class _SlotSpy:
    def __init__(self):
        self.entries = 0

    async def __aenter__(self):
        self.entries += 1

    async def __aexit__(self, *exc):
        return False


def _settings(output_dir):
    return SimpleNamespace(
        output_dir=output_dir,
        output_ttl_seconds=600,
        xiaozhi_device_token="",
        paper_risk_gate=False,  # 路由测试不验纸面闸；闸行为见 test_paper_risk
    )


def _stub_persist(monkeypatch):
    async def _fake_svg_to_gcode(_svg, _svg_path, _gcode_path):
        return SimpleNamespace(lines=1, bytes=2, pen_lifts=0)

    monkeypatch.setattr(server, "_svg_to_gcode_cached", _fake_svg_to_gcode)


def test_draw_job_replace_hit_uses_curated_without_quickdraw(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    monkeypatch.setattr(curated, "is_replace_active", lambda: True)
    monkeypatch.setattr(
        curated,
        "lookup",
        lambda description: BitmapResult(svg=_CURATED_SVG, preview_png=b"curated"),
    )
    quickdraw_lookup = Mock(side_effect=AssertionError("replace 模式不得查询 Quick Draw"))
    monkeypatch.setattr(quickdraw, "lookup", quickdraw_lookup)
    generate = AsyncMock(side_effect=AssertionError("精选库命中不得调用 AI 生图"))
    monkeypatch.setattr(server, "generate_bitmap", generate)
    _stub_persist(monkeypatch)
    slot_spy = _SlotSpy()
    monkeypatch.setattr(server, "_draw_limiter", SimpleNamespace(slot=lambda: slot_spy))

    payload = json.loads(asyncio.run(server._run_draw_job("画一只老虎", _BASE_URL)))

    assert quickdraw_lookup.call_count == 0
    generate.assert_not_awaited()
    assert slot_spy.entries == 0
    assert (tmp_path / payload["url"].rsplit("/", 1)[-1].replace(".gcode", ".svg")).read_text(
        encoding="utf-8"
    ) == _CURATED_SVG


def test_draw_job_replace_miss_falls_back_to_ai_without_quickdraw(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    monkeypatch.setattr(curated, "is_replace_active", lambda: True)
    monkeypatch.setattr(curated, "lookup", lambda description: None)
    quickdraw_lookup = Mock(side_effect=AssertionError("replace 模式不得查询 Quick Draw"))
    monkeypatch.setattr(quickdraw, "lookup", quickdraw_lookup)
    generate = AsyncMock(return_value=BitmapResult(svg=_AI_SVG, preview_png=b"ai"))
    monkeypatch.setattr(server, "generate_bitmap", generate)
    _stub_persist(monkeypatch)
    slot_spy = _SlotSpy()
    monkeypatch.setattr(server, "_draw_limiter", SimpleNamespace(slot=lambda: slot_spy))

    asyncio.run(server._run_draw_job("画一只老虎", _BASE_URL))

    assert quickdraw_lookup.call_count == 0
    generate.assert_awaited_once_with("画一只老虎", attempt=0)
    assert slot_spy.entries == 1


def test_draw_job_without_replace_routes_plain_description_to_ai(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    monkeypatch.setattr(curated, "is_replace_active", lambda: False)
    curated_lookup = AsyncMock()
    monkeypatch.setattr(curated, "lookup", curated_lookup)
    quickdraw_lookup = Mock(side_effect=AssertionError("普通描述不得查询 Quick Draw"))
    monkeypatch.setattr(quickdraw, "lookup", quickdraw_lookup)
    generate = AsyncMock(return_value=BitmapResult(svg=_AI_SVG, preview_png=b"ai"))
    monkeypatch.setattr(server, "generate_bitmap", generate)
    _stub_persist(monkeypatch)
    slot_spy = _SlotSpy()
    monkeypatch.setattr(server, "_draw_limiter", SimpleNamespace(slot=lambda: slot_spy))

    asyncio.run(server._run_draw_job("画一只老虎", _BASE_URL))

    curated_lookup.assert_not_called()
    assert quickdraw_lookup.call_count == 0
    generate.assert_awaited_once_with("画一只老虎", attempt=0)
    assert slot_spy.entries == 1
