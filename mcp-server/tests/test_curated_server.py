"""Curated preference routing at the draw-job boundary (QD 退役后)。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from hutuji_mcp import curated, quickdraw, server
from hutuji_mcp.bitmap_svg import BitmapResult

_BASE_URL = "http://127.0.0.1:8300"
_AI_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M3 3 L4 4"/></svg>'
_CURATED_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1 L2 2" fill="none" stroke="black"/></svg>'

class _SlotSpy:
    def __init__(self):
        self.entries = 0

    async def __aenter__(self):
        self.entries += 1

    async def __aexit__(self, *exc):
        return False


def _settings(output_dir, curated_dir=""):
    return SimpleNamespace(
        output_dir=output_dir,
        output_ttl_seconds=600,
        xiaozhi_device_token="",
        paper_risk_gate=False,  # 路由测试不验纸面闸；闸行为见 test_paper_risk
        curated_dir=curated_dir,
        public_base_url=_BASE_URL,
    )


def _patch_settings(monkeypatch, server_dir, curated_dir):
    """server.settings 与 curated 模块自持 settings 必须分别 patch。"""
    monkeypatch.setattr(server, "settings", _settings(server_dir, curated_dir))
    monkeypatch.setattr(curated, "settings", SimpleNamespace(curated_dir=curated_dir))


def _stub_persist(monkeypatch):
    async def _fake_svg_to_gcode(_svg, _svg_path, _gcode_path, on_raw_text=None, *, profile=None):
        # shadow 分支会读 gcode 文件做指标评测：stub 必须写实文件（对齐生产）。
        _gcode_path.write_text("G21\nG90\nF24000\n", encoding="utf-8")
        return SimpleNamespace(lines=1, bytes=2, pen_lifts=0), "G21\nG90\nF24000\n"

    monkeypatch.setattr(server, "_svg_to_gcode_cached", _fake_svg_to_gcode)


def _seed_library(root, category="cat", count=52):
    """写一个达标类目库：manifest.json + 若干 svg。"""
    cat_dir = root / category
    cat_dir.mkdir(parents=True)
    (cat_dir / "000.svg").write_text(_CURATED_SVG, encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({"n_min": 30, "categories": {category: {"count": count}}}),
        encoding="utf-8",
    )


def _stub_qd_forbidden(monkeypatch):
    qd_lookup = Mock(side_effect=AssertionError("QD 已退役，运行时不得再查询"))
    monkeypatch.setattr(quickdraw, "lookup", qd_lookup)
    return qd_lookup


def _stub_ai(monkeypatch, fail=False):
    if fail:
        generate = AsyncMock(side_effect=AssertionError("curated 命中不得调用 AI 生图"))
    else:
        generate = AsyncMock(return_value=BitmapResult(svg=_AI_SVG, preview_png=b"ai"))
    monkeypatch.setattr(server, "generate_bitmap", generate)
    return generate


def _stub_slot(monkeypatch):
    slot_spy = _SlotSpy()
    monkeypatch.setattr(server, "_draw_limiter", SimpleNamespace(slot=lambda: slot_spy))
    return slot_spy


def test_draw_job_prefer_hit_serves_curated(monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    _seed_library(lib, count=52)
    out = tmp_path / "out"
    out.mkdir()
    _patch_settings(monkeypatch, out, str(lib))
    qd_lookup = _stub_qd_forbidden(monkeypatch)
    generate = _stub_ai(monkeypatch, fail=True)
    _stub_persist(monkeypatch)
    slot_spy = _stub_slot(monkeypatch)

    # 须显式简笔风才吃 curated；裸「画一只猫」走 AI（2026-09-08 风格门）。
    payload = json.loads(asyncio.run(server._run_draw_job("简笔画一只猫", _BASE_URL)))

    assert qd_lookup.call_count == 0
    generate.assert_not_awaited()
    assert slot_spy.entries == 0, "curated 命中与 QD 命中同口径：不占付费限流名额"
    assert payload["url"].endswith(".gcode")


def test_draw_job_plain_noun_skips_curated_for_ai(monkeypatch, tmp_path):
    """裸「画老虎」不得命中 curated（儿童简笔库），须落 AI 位图链。"""
    lib = tmp_path / "lib"
    _seed_library(lib, category="tiger", count=52)
    out = tmp_path / "out"
    out.mkdir()
    _patch_settings(monkeypatch, out, str(lib))
    _stub_qd_forbidden(monkeypatch)
    generate = _stub_ai(monkeypatch)
    _stub_persist(monkeypatch)
    slot_spy = _stub_slot(monkeypatch)

    asyncio.run(server._run_draw_job("画一只老虎", _BASE_URL))

    generate.assert_awaited_once_with("画一只老虎", attempt=0)
    assert slot_spy.entries == 1


def test_draw_job_prefer_miss_under_n_min_falls_to_ai(monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    _seed_library(lib, count=5)
    out = tmp_path / "out"
    out.mkdir()
    _patch_settings(monkeypatch, out, str(lib))
    qd_lookup = _stub_qd_forbidden(monkeypatch)
    generate = _stub_ai(monkeypatch)
    _stub_persist(monkeypatch)
    slot_spy = _stub_slot(monkeypatch)

    asyncio.run(server._run_draw_job("画一只猫", _BASE_URL))

    assert qd_lookup.call_count == 0
    generate.assert_awaited_once_with("画一只猫", attempt=0)
    assert slot_spy.entries == 1


def test_draw_job_prefer_unmapped_falls_to_ai(monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    _seed_library(lib, count=52)
    out = tmp_path / "out"
    out.mkdir()
    _patch_settings(monkeypatch, out, str(lib))
    _stub_qd_forbidden(monkeypatch)
    generate = _stub_ai(monkeypatch)
    _stub_persist(monkeypatch)
    slot_spy = _stub_slot(monkeypatch)

    asyncio.run(server._run_draw_job("未收录主题", _BASE_URL))

    generate.assert_awaited_once_with("未收录主题", attempt=0)
    assert slot_spy.entries == 1


def test_draw_job_prefer_disabled_without_dir(monkeypatch, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    _patch_settings(monkeypatch, out, "")
    _stub_qd_forbidden(monkeypatch)
    generate = _stub_ai(monkeypatch)
    _stub_persist(monkeypatch)
    slot_spy = _stub_slot(monkeypatch)

    asyncio.run(server._run_draw_job("画一只猫", _BASE_URL))

    generate.assert_awaited_once_with("画一只猫", attempt=0)
    assert slot_spy.entries == 1


def test_draw_job_prefer_hit_concurrent_share_one_job(monkeypatch, tmp_path):
    """命中路径同题并发只跑一次事务（机制与位图路径共用 _draw_inflight）。"""
    lib = tmp_path / "lib"
    _seed_library(lib, count=52)
    out = tmp_path / "out"
    out.mkdir()
    _patch_settings(monkeypatch, out, str(lib))
    _stub_qd_forbidden(monkeypatch)
    _stub_persist(monkeypatch)
    slot_spy = _stub_slot(monkeypatch)
    getattr(server, "_draw_inflight", {}).clear()
    server._gcode_cache.clear()

    real_pick = curated._pick_and_load
    calls: list[str] = []

    def _counting_pick(root_s, category):
        calls.append(category)
        return real_pick(root_s, category)

    monkeypatch.setattr(curated, "_pick_and_load", _counting_pick)

    async def exercise():
        tasks = [
            asyncio.create_task(server.hutuji_draw("简笔画一只猫")) for _ in range(4)
        ]
        try:
            payloads = [json.loads(r) for r in await asyncio.gather(*tasks)]
            assert len({p["url"] for p in payloads}) == 1, "同题并发必须共享同一事务产物"
            assert len(calls) == 1, f"同题并发只查一次库：{calls}"
            assert slot_spy.entries == 0
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(exercise())
    getattr(server, "_draw_inflight", {}).clear()
    server._gcode_cache.clear()
