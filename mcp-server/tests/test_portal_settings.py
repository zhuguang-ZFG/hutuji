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


def test_draw_limiter_refresh_from_portal(monkeypatch: pytest.MonkeyPatch) -> None:
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
    limiter._refresh_from_portal()
    assert limiter.max_concurrent == 2
    assert limiter.per_second == 2
    assert limiter.per_minute == 10


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
