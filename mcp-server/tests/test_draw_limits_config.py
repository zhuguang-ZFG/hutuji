"""课堂级 DrawLimiter / vpype 并发配置。"""
from __future__ import annotations

import asyncio

import pytest

from hutuji_mcp import config, gcode_gen, server


def test_classroom_draw_limiter_defaults_from_settings() -> None:
    limiter = server.DrawLimiter()
    assert limiter.max_concurrent == config.settings.draw_max_concurrent
    assert limiter.per_second == config.settings.draw_per_second
    assert limiter.per_minute == config.settings.draw_per_minute
    assert limiter.max_concurrent >= 8
    assert limiter.per_second >= 4


def test_vpype_max_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUTUJI_VPYPE_MAX_CONCURRENT", "5")
    gcode_gen._vpype_semaphores.clear()
    assert gcode_gen._vpype_max_concurrent() == 5

    async def _probe() -> int:
        sem = gcode_gen._vpype_semaphore()
        return sem._value  # noqa: SLF001 — 测试只读初始容量

    assert asyncio.run(_probe()) == 5


def test_load_settings_draw_limits_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUTUJI_HOST", "127.0.0.1")
    monkeypatch.setenv("HUTUJI_PUBLIC_BASE_URL", "http://127.0.0.1:8300")
    monkeypatch.setenv("HUTUJI_DRAW_MAX_CONCURRENT", "0")
    with pytest.raises(ValueError, match="draw_max_concurrent"):
        config.load_settings()
