from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from hutuji_mcp import bitmap_svg
from hutuji_mcp import portal_settings
from hutuji_mcp.server import DrawLimiter


def test_portal_settings_effective_draw_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        portal_settings,
        "get_cached_mcp_settings",
        lambda _settings: {"draw_limits": {"draw_max_concurrent": 3}},
    )
    settings = SimpleNamespace(draw_max_concurrent=8)
    assert portal_settings.effective_draw_limit(settings, "draw_max_concurrent", 8) == 3


def test_draw_limiter_loads_portal_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        portal_settings,
        "get_cached_mcp_settings",
        lambda _settings: {
            "draw_limits": {
                "draw_max_concurrent": 2,
                "draw_per_second": 2,
                "draw_per_minute": 10,
            }
        },
    )
    limiter = DrawLimiter(max_concurrent=8, per_second=4, per_minute=120)
    assert limiter._load_portal_limits() == (2, 2, 10)
    assert limiter.max_concurrent == 8  # 工作线程只返回快照，不修改事件循环状态。


def test_race_skips_admin_disabled_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bitmap_svg, "settings", _sensenova_fallback_settings())
    monkeypatch.setattr(
        portal_settings,
        "get_cached_mcp_settings",
        lambda _settings: {"image_backends": {"sensenova": False, "agnes": True}},
    )
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host or "")
        if request.url.host == "x.invalid":
            return httpx.Response(
                200,
                json={"data": [{"url": "https://cdn.example.invalid/a.png"}]},
            )
        raise AssertionError(f"unexpected host {request.url.host}")

    transport = httpx.MockTransport(handler)

    async def run() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            url = await bitmap_svg._race_image_backends(client, "星")
        assert url == "https://cdn.example.invalid/a.png"

    asyncio.run(run())
    assert "token.sensenova.invalid" not in seen_hosts
    assert "x.invalid" in seen_hosts


def test_portal_image_racers_dynamic_race(monkeypatch: pytest.MonkeyPatch) -> None:
    from hutuji_mcp import bitmap_svg
    from hutuji_mcp import portal_settings

    monkeypatch.setattr(
        portal_settings,
        "portal_image_racers",
        lambda _settings=None: [
            {
                "id": "p1",
                "name": "DynAgnes",
                "provider_type": "agnes",
                "base_url": "https://x.invalid/v1",
                "model": "m",
                "extra": {},
                "api_keys": ["sk-dyn"],
            }
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "x.invalid"
        return httpx.Response(
            200,
            json={"data": [{"url": "https://cdn.example.invalid/dyn.png"}]},
        )

    transport = httpx.MockTransport(handler)

    async def run() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            url = await bitmap_svg._race_image_backends(client, "星")
        assert url == "https://cdn.example.invalid/dyn.png"

    asyncio.run(run())


def _sensenova_fallback_settings() -> SimpleNamespace:
    return SimpleNamespace(
        llm_api_key="k",
        llm_base_url="https://x.invalid/v1",
        image_model="m",
        sensenova_api_key="sense-key",
        sensenova_base_url="https://token.sensenova.invalid/v1",
        sensenova_image_model="sensenova-u1-fast",
        siliconflow_api_key="",
        image_fallback_url="",
    )


def test_draw_limiter_fetch_runs_off_loop_and_cancel_leaves_no_slot(monkeypatch):
    import threading
    started, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    def fetch(_):
        assert threading.get_ident() != loop_thread
        started.set()
        assert release.wait(5)
        return {"draw_limits": {"draw_max_concurrent": 1}}
    monkeypatch.setattr(portal_settings, "_cache_payload", None)
    monkeypatch.setattr(portal_settings, "_fetch_remote", fetch)
    limiter = DrawLimiter(max_concurrent=8)
    async def run():
        task = asyncio.create_task(limiter.enter())
        try:
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert limiter._active == 0
        finally:
            release.set()
    asyncio.run(run())
    assert limiter.max_concurrent == 8


def test_concurrent_settings_refresh_fetches_once(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    barrier = threading.Barrier(4)
    calls = []
    monkeypatch.setattr(portal_settings, "_cache_payload", None)
    monkeypatch.setattr(portal_settings, "_cache_at", 0)
    def fetch(_):
        calls.append(1)
        return {"revision": 7}
    monkeypatch.setattr(portal_settings, "_fetch_remote", fetch)
    def read():
        barrier.wait(timeout=5)
        return portal_settings.get_cached_mcp_settings(SimpleNamespace())
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: read(), range(4)))
    assert len(calls) == 1
    assert results == [{"revision": 7}] * 4


def test_bitmap_and_vpype_config_fetch_runs_off_loop(monkeypatch):
    import threading
    from hutuji_mcp import gcode_gen
    loop_thread = threading.get_ident()
    calls = []
    def fetch(_):
        assert threading.get_ident() != loop_thread
        calls.append(1)
        return {"image_backends": {name: False for name in
                ("agnes", "sensenova", "siliconflow", "modelscope", "tcb", "fallback")}}
    monkeypatch.setattr(portal_settings, "get_cached_mcp_settings", fetch)
    monkeypatch.setattr(bitmap_svg, "settings", _sensenova_fallback_settings())
    async def run():
        async with httpx.AsyncClient() as client:
            with pytest.raises(bitmap_svg.BitmapError, match="未启用"):
                await bitmap_svg._race_image_backends(client, "星")
        # 真正入口必须在创建子进程之前完成异步配置读取。
        async def convert(*args, **kwargs):
            return "converted"
        monkeypatch.setattr(gcode_gen, "_svg_to_gcode_async_unbounded", convert)
        from pathlib import Path
        assert await gcode_gen.svg_to_gcode_async(Path("x.svg"), Path("x.gcode")) == "converted"
    asyncio.run(run())
    assert len(calls) >= 2


def test_vpype_limit_change_keeps_running_and_waiting_jobs_on_one_gate():
    from hutuji_mcp import gcode_gen
    async def run():
        gate = gcode_gen._vpype_semaphore(2)
        await gate.__aenter__()
        await gate.__aenter__()
        changed = gcode_gen._vpype_semaphore(1)
        entered = asyncio.Event()
        async def wait():
            async with changed:
                entered.set()
        waiter = asyncio.create_task(wait())
        await asyncio.sleep(0)
        assert not entered.is_set()
        await gate.__aexit__(None, None, None)
        await asyncio.sleep(0)
        assert not entered.is_set()  # 缩容后已有一份工作，不能额外放行。
        await gate.__aexit__(None, None, None)
        await asyncio.wait_for(waiter, 1)
        assert entered.is_set()
        await changed.__aenter__()
        cancelled = asyncio.create_task(wait())
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        # 扩容唤醒现有等待者，取消没有占用名额。
        entered.clear()
        waiter = asyncio.create_task(wait())
        await asyncio.sleep(0)
        gcode_gen._vpype_semaphore(2)
        await asyncio.wait_for(waiter, 1)
        await changed.__aexit__(None, None, None)
    asyncio.run(run())
