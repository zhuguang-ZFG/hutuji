"""设备 messaging 链式推送 + 401/403/404 自愈。

2026-09-08：语音异步 draw 链式 404 只记日志 → 屏永无预览；小程序 portal 已自愈。
"""
from __future__ import annotations

import asyncio
import types

import pytest

from hutuji_mcp import device_messaging as dm


class _Resp:
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_load_device_token_prefers_file(tmp_path, monkeypatch):
    path = tmp_path / "device_messaging.token"
    path.write_text("file-tok\n", encoding="utf-8")
    monkeypatch.setenv("HUTUJI_DEVICE_TOKEN_FILE", str(path))
    monkeypatch.setenv("HUTUJI_XIAOZHI_DEVICE_TOKEN", "env-tok")
    assert dm.load_device_token(env_fallback="fb") == "file-tok"


def test_load_device_token_falls_back_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HUTUJI_DEVICE_TOKEN_FILE", str(tmp_path / "missing.token"))
    assert dm.load_device_token(env_fallback="env-only") == "env-only"


def test_save_device_token_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "device_messaging.token"
    monkeypatch.setenv("HUTUJI_DEVICE_TOKEN_FILE", str(path))
    dm.save_device_token(" new-tok ")
    assert path.read_text(encoding="utf-8").strip() == "new-tok"
    assert dm.load_device_token() == "new-tok"


def test_chain_404_heals_and_retries(tmp_path, monkeypatch):
    path = tmp_path / "device_messaging.token"
    monkeypatch.setenv("HUTUJI_DEVICE_TOKEN_FILE", str(path))
    monkeypatch.setenv("HUTUJI_DEVICE_MAC", "aa:bb:cc:dd:ee:ff")
    path.write_text("stale\n", encoding="utf-8")

    posts: list[str] = []

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            tok = headers.get("Authorization", "").removeprefix("Bearer ")
            posts.append(tok)
            return _Resp(404 if tok == "stale" else 200)

    monkeypatch.setattr(dm, "httpx", types.SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(dm, "mint_messaging_token", lambda **kw: "fresh")

    ok, detail = asyncio.run(
        dm.chain_hutuji_draw(
            api_base="https://example",
            env_token="stale",
            url="https://h/a.gcode",
            preview_url="https://h/a.png",
        )
    )
    assert ok
    assert detail == "ok_healed"
    assert posts == ["stale", "fresh"]
    assert path.read_text(encoding="utf-8").strip() == "fresh"


@pytest.mark.parametrize("status", [401, 403])
def test_chain_auth_errors_heal(tmp_path, monkeypatch, status):
    path = tmp_path / "t.token"
    monkeypatch.setenv("HUTUJI_DEVICE_TOKEN_FILE", str(path))
    monkeypatch.setenv("HUTUJI_DEVICE_MAC", "aa:bb:cc:dd:ee:ff")
    path.write_text("bad\n", encoding="utf-8")
    seen: list[int] = []

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            tok = headers.get("Authorization", "")
            code = status if "bad" in tok else 200
            seen.append(code)
            return _Resp(code)

    monkeypatch.setattr(dm, "httpx", types.SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(dm, "mint_messaging_token", lambda **kw: "good")
    ok, detail = asyncio.run(
        dm.chain_hutuji_draw(
            api_base="https://example",
            env_token="bad",
            url="https://h/a.gcode",
            preview_url="https://h/a.png",
        )
    )
    assert ok and detail == "ok_healed"
    assert seen == [status, 200]


def test_chain_mint_fail_keeps_original_detail(tmp_path, monkeypatch):
    path = tmp_path / "t.token"
    monkeypatch.setenv("HUTUJI_DEVICE_TOKEN_FILE", str(path))
    monkeypatch.setenv("HUTUJI_DEVICE_MAC", "aa:bb:cc:dd:ee:ff")
    path.write_text("stale\n", encoding="utf-8")

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            return _Resp(404)

    monkeypatch.setattr(dm, "httpx", types.SimpleNamespace(AsyncClient=_Client))
    monkeypatch.setattr(dm, "mint_messaging_token", lambda **kw: "")
    ok, detail = asyncio.run(
        dm.chain_hutuji_draw(
            api_base="https://example",
            env_token="stale",
            url="https://h/a.gcode",
            preview_url="https://h/a.png",
        )
    )
    assert not ok
    assert detail == "http_404+heal_mint_fail"


def test_server_chain_uses_device_messaging(monkeypatch):
    from hutuji_mcp import server

    called: dict[str, object] = {}

    async def fake_chain(**kw):
        called.update(kw)
        return True, "ok_healed"

    monkeypatch.setattr(
        server,
        "settings",
        types.SimpleNamespace(
            xiaozhi_device_token="env-tok",
            xiaozhi_api_base="https://api.example",
        ),
    )
    monkeypatch.setattr(
        "hutuji_mcp.device_messaging.chain_hutuji_draw", fake_chain
    )
    monkeypatch.setattr(
        "hutuji_mcp.device_messaging.load_device_token", lambda: ""
    )
    asyncio.run(
        server._chain_device_draw(
            "https://h/a.gcode",
            "https://h/a.png",
            [{"url": "https://h/a.gcode", "preview_url": "https://h/a.png"}],
        )
    )
    assert called["env_token"] == "env-tok"
    assert called["url"] == "https://h/a.gcode"
    assert isinstance(called["pages_arg"], str)
