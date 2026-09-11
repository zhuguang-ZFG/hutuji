"""小智控制台客户端（xiaozhi_console）+ /internal/xiaozhi/* 路由契约测试。

钉死四类行为：清单解析与缓存、agent 克隆整包语义、/internal 路由闸门与
归属防线（共享 agent 拒改）、绑定码形状。网络层不打真上游（monkeypatch
模块函数），形状断言对齐 2026-09-12 生产探针实证。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from hutuji_mcp import server, xiaozhi_console as xcon


def _voices_payload() -> dict:
    return {
        "success": True,
        "data": {
            "languages": ["zh", "en"],
            "tts_voices": {
                "zh": [
                    {"voice_id": "zh_female_a_moon_bigtts", "voice_name": "甲", "status": 1},
                    {"voice_id": "zh_female_b_uranus_bigtts", "voice_name": "乙", "is_beta": True},
                    {"bad": "row"},
                ],
                "en": [{"voice_id": "en_male_c", "voice_name": "C"}],
                7: [{"voice_id": "should_skip", "voice_name": "skip"}],
            },
        },
    }


def _models_payload() -> dict:
    return {
        "success": True,
        "data": {
            "modelList": [
                {"name": "xz-lite", "description": "默认"},
                {"name": "deepseek", "description": "深度思考"},
                {"no_name": True},
            ]
        },
    }


def _agent_payload() -> dict:
    return {
        "success": True,
        "data": {
            "agent": {
                "id": 2094251,
                "language": "zh",
                "tts_voice": "zh_female_linjianvhai_moon_bigtts",
                "llm_model": "deepseek",
                "character": "小派人格",
                "asr_speed": 0,
                "tts_speech_speed": 0,
                "tts_pitch": 0,
                "memory_type": "SHORT_TERM",
                "teen_mode": 0,
                "mcp_endpoints": ["wss://x"],
                "knowledge_base_ids": [71153],
            }
        },
    }


# ---------- 清单解析 ----------


def test_voices_parse_groups_and_skips_bad_rows() -> None:
    voices = xcon._voices_from_payload(_voices_payload()["data"])
    ids = [v["voice_id"] for v in voices]
    assert ids == ["zh_female_a_moon_bigtts", "zh_female_b_uranus_bigtts", "en_male_c"]
    assert voices[0]["language"] == "zh"
    assert voices[1]["is_beta"] is True


def test_models_parse_skips_unnamed() -> None:
    models = xcon._models_from_payload(_models_payload()["data"])
    assert [m["name"] for m in models] == ["xz-lite", "deepseek"]


def test_malformed_payloads_raise_narrow() -> None:
    with pytest.raises(xcon.XiaozhiConsoleError, match="音色清单"):
        xcon._voices_from_payload({"tts_voices": "no"})
    with pytest.raises(xcon.XiaozhiConsoleError, match="模型清单"):
        xcon._models_from_payload({"modelList": "no"})


def test_validate_base_rejects_non_https_and_credentials() -> None:
    with pytest.raises(xcon.XiaozhiConsoleError, match="https"):
        xcon._validate_base("http://xiaozhi.me/api")
    with pytest.raises(xcon.XiaozhiConsoleError, match="凭据"):
        xcon._validate_base("https://user:pw@xiaozhi.me/api")
    assert xcon._validate_base("https://xiaozhi.me/api/") == "https://xiaozhi.me/api"


# ---------- 缓存与克隆 ----------


def test_fetch_options_caches_by_token(monkeypatch: pytest.MonkeyPatch) -> None:
    xcon._CACHE.clear()
    calls = []

    def fake_api(method, url, token, body, timeout):
        calls.append(url)
        if "tts-list" in url:
            return _voices_payload()
        return _models_payload()

    monkeypatch.setattr(xcon, "_api_json", fake_api)
    first = xcon.fetch_options("tok", "https://console.example/api")
    assert len(first["voices"]) == 3 and len(first["models"]) == 2
    xcon.fetch_options("tok", "https://console.example/api")
    assert len(calls) == 2  # 第二次全走缓存
    xcon.fetch_options("tok2", "https://console.example/api")
    assert len(calls) == 4  # token 变化重新拉取
    xcon._CACHE.clear()


def test_update_agent_config_merges_whitelist_only(monkeypatch: pytest.MonkeyPatch) -> None:
    posted = {}

    def fake_api(method, url, token, body, timeout):
        if method == "POST":
            posted["url"], posted["body"] = url, body
        return _agent_payload()

    monkeypatch.setattr(xcon, "_api_json", fake_api)
    applied = xcon.update_agent_config(
        "tok", "https://console.example/api", "123", {"tts_voice": "zh_female_a_moon_bigtts"}
    )
    assert applied == {"tts_voice": "zh_female_a_moon_bigtts", "llm_model": "deepseek"}
    assert posted["url"].endswith("/agents/123/config")
    assert posted["body"]["character"] == "小派人格"
    assert posted["body"]["knowledge_base_ids"] == [71153]
    assert posted["body"]["tts_voice"] == "zh_female_a_moon_bigtts"


def test_update_agent_config_rejects_unknown_field() -> None:
    with pytest.raises(xcon.XiaozhiConsoleError, match="character"):
        xcon.update_agent_config("tok", "https://console.example/api", "1", {"character": "x"})


def test_clone_agent_creates_and_copies(monkeypatch: pytest.MonkeyPatch) -> None:
    posts = []

    def fake_api(method, url, token, body, timeout):
        if method == "POST":
            posts.append((url, body))
            if url.endswith("/agents"):
                return {"success": True, "data": {"id": 5550001}}
        return _agent_payload()

    monkeypatch.setattr(xcon, "_api_json", fake_api)
    result = xcon.clone_agent("tok", "https://console.example/api", "2094251", "小派-克隆-1")
    assert result == {
        "agent_id": "5550001",
        "tts_voice": "zh_female_linjianvhai_moon_bigtts",
        "llm_model": "deepseek",
    }
    config_posts = [b for u, b in posts if u.endswith("/agents/5550001/config")]
    assert len(config_posts) == 1
    assert config_posts[0]["character"] == "小派人格"
    assert config_posts[0]["knowledge_base_ids"] == [71153]


def test_clone_agent_rejects_non_int_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        xcon, "_api_json", lambda *a, **k: {"success": True, "data": {"id": "x"}}
    )
    with pytest.raises(xcon.XiaozhiConsoleError, match="形状异常"):
        xcon.clone_agent("tok", "https://console.example/api", "1", "n")


# ---------- /internal/xiaozhi/* 路由 ----------


def _req(body: bytes, *, host="127.0.0.1", secret="s3cr3t", method="POST"):
    async def _body() -> bytes:
        return body

    headers = {"x-hutuji-usage-secret": secret} if secret is not None else {}
    return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers, body=_body)


def _enable(monkeypatch: pytest.MonkeyPatch, *, secret="s3cr3t") -> None:
    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            usage_ingest_secret=secret,
            xiaozhi_console_token="tok",
            xiaozhi_agent_id="2094251",
            xiaozhi_api_base="https://console.example/api",
        ),
    )


def test_routes_auth_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, secret="")
    body = json.dumps({"agent_id": "555"}).encode()
    assert asyncio.run(server._internal_xiaozhi_options(_req(b"", method="GET"))).status_code == 404
    assert asyncio.run(server._internal_xiaozhi_clone(_req(b""))).status_code == 404
    assert asyncio.run(server._internal_xiaozhi_config(_req(body))).status_code == 404
    assert asyncio.run(server._internal_xiaozhi_bind(_req(body))).status_code == 404

    _enable(monkeypatch)
    assert (
        asyncio.run(server._internal_xiaozhi_clone(_req(b"", host="203.0.113.9"))).status_code
        == 404
    )
    assert (
        asyncio.run(server._internal_xiaozhi_config(_req(body, secret="bad"))).status_code == 404
    )


def test_config_route_rejects_base_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    body = json.dumps({"agent_id": "2094251", "llm_model": "qwen"}).encode()
    with pytest.raises(HTTPException) as ei:
        asyncio.run(server._internal_xiaozhi_config(_req(body)))
    assert ei.value.status_code == 400
    assert "共享" in ei.value.detail


def test_config_route_validates_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(server._internal_xiaozhi_config(_req(b"{}")))
    assert ei.value.status_code == 400

    good_options = {
        "voices": [{"voice_id": "zh_female_a_moon_bigtts"}],
        "models": [{"name": "qwen"}],
    }
    monkeypatch.setattr(
        "hutuji_mcp.xiaozhi_console.fetch_options", lambda *a, **k: good_options
    )
    with pytest.raises(HTTPException) as ei2:
        asyncio.run(
            server._internal_xiaozhi_config(
                _req(json.dumps({"agent_id": "555", "tts_voice": "nope"}).encode())
            )
        )
    assert ei2.value.status_code == 400
    with pytest.raises(HTTPException) as ei3:
        asyncio.run(
            server._internal_xiaozhi_config(
                _req(json.dumps({"agent_id": "555", "llm_model": "nope"}).encode())
            )
        )
    assert ei3.value.status_code == 400


def test_config_route_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    good_options = {
        "voices": [{"voice_id": "zh_female_a_moon_bigtts"}],
        "models": [{"name": "qwen"}],
    }
    monkeypatch.setattr(
        "hutuji_mcp.xiaozhi_console.fetch_options", lambda *a, **k: good_options
    )
    monkeypatch.setattr(
        "hutuji_mcp.xiaozhi_console.update_agent_config",
        lambda *a, **k: {"tts_voice": "zh_female_a_moon_bigtts", "llm_model": "qwen"},
    )
    resp = asyncio.run(
        server._internal_xiaozhi_config(
            _req(json.dumps({"agent_id": "555", "tts_voice": "zh_female_a_moon_bigtts"}).encode())
        )
    )
    payload = json.loads(resp.body.decode())
    assert resp.status_code == 200
    assert payload == {
        "ok": True,
        "agent_id": "555",
        "tts_voice": "zh_female_a_moon_bigtts",
        "llm_model": "qwen",
    }


def test_bind_route_validates_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    for body, detail_part in [
        (json.dumps({"agent_id": "555", "verification_code": "12345"}).encode(), "6 位"),
        (json.dumps({"agent_id": "555", "verification_code": "12a456"}).encode(), "6 位"),
        (json.dumps({"verification_code": "123456"}).encode(), "agent_id"),
        (json.dumps({"agent_id": "2094251", "verification_code": "123456"}).encode(), "共享"),
    ]:
        with pytest.raises(HTTPException) as ei:
            asyncio.run(server._internal_xiaozhi_bind(_req(body)))
        assert ei.value.status_code == 400, body[:40]


def test_bind_route_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr(
        "hutuji_mcp.xiaozhi_console.bind_device_by_code", lambda *a, **k: {"agent_id": "555"}
    )
    resp = asyncio.run(
        server._internal_xiaozhi_bind(
            _req(json.dumps({"agent_id": "555", "verification_code": "123456"}).encode())
        )
    )
    assert resp.status_code == 200
    assert json.loads(resp.body.decode()) == {"ok": True, "agent_id": "555"}


def test_clone_route_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr(
        "hutuji_mcp.xiaozhi_console.clone_agent",
        lambda *a, **k: {"agent_id": "555", "tts_voice": "v", "llm_model": "m"},
    )
    resp = asyncio.run(server._internal_xiaozhi_clone(_req(b"")))
    assert resp.status_code == 200
    assert json.loads(resp.body.decode())["agent_id"] == "555"
