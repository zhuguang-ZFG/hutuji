"""小派设置（音色/模型切换，§10.4.19）端点契约测试。

portal 只转发本机 MCP /internal/xiaozhi/*；本文件 monkeypatch
draw_portal.xiaopai 的转发函数，不打真 MCP/控制台。钉死四类行为：
清单读取与未配置 503、首次个性化懒克隆 + 设置写回 + 限频、验证码绑定
（无效码 400 / 成功置位）、以及归属信息不落前端（agent_id 不出 portal）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from draw_portal import xiaopai as xiaopai_mod
from draw_portal.app import create_app
from draw_portal.store import Store


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DRAW_PORTAL_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DRAW_PORTAL_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("DRAW_PORTAL_TOKEN_CIPHER_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("DRAW_PORTAL_HOST", "127.0.0.1")
    monkeypatch.setenv("DRAW_PORTAL_PORT", "8302")
    monkeypatch.setenv("DRAW_PORTAL_MCP_OUTPUT_DIR", str(tmp_path / "mcp-out"))
    monkeypatch.setenv("DRAW_PORTAL_PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("DRAW_PORTAL_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("DRAW_PORTAL_SMTP_FROM", "noreply@example.test")
    monkeypatch.setenv("DRAW_PORTAL_EMAIL_DEV_EXPOSE_CODE", "1")


def _client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, ingest_secret="sec"
) -> TestClient:
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("DRAW_PORTAL_USAGE_INGEST_SECRET", ingest_secret)
    monkeypatch.setattr("draw_portal.app.send_verification_email", lambda **kwargs: None)
    return TestClient(create_app())


def _login_phone(client: TestClient) -> None:
    req = client.post(
        "/api/auth/register/request", json={"email": "buyer@qq.com", "password": "secret123"}
    )
    code = req.json()["dev_code"]
    client.post("/api/auth/register/verify", json={"email": "buyer@qq.com", "code": code})
    assert client.post("/api/auth/bind-phone", json={"phone": "13800138000"}).status_code == 200


_VOICES = [
    {"voice_id": "zh_female_a_moon_bigtts", "voice_name": "甲", "language": "zh", "is_beta": False},
    {"voice_id": "zh_female_b_uranus_bigtts", "voice_name": "乙", "language": "zh", "is_beta": True},
]
_MODELS = [{"name": "xz-lite", "description": "默认"}, {"name": "deepseek", "description": "现役"}]


def _fake_forward(monkeypatch: pytest.MonkeyPatch, *, clone_agent_id="5550001") -> dict:
    """把 xiaopai 转发函数换成内存桩，记录调用并返回契约形状。"""
    calls: dict[str, list] = {"clone": [], "config": [], "bind": [], "options": 0}

    def fake_options(*, base_url: str, secret: str) -> dict:
        calls["options"] += 1
        if not secret:
            raise xiaopai_mod.XiaopaiError("设置通道未配置", status=503)
        return {"voices": _VOICES, "models": _MODELS}

    def fake_clone(*, base_url: str, secret: str) -> dict:
        calls["clone"].append(base_url)
        return {"agent_id": clone_agent_id, "tts_voice": "", "llm_model": ""}

    def fake_config(*, base_url: str, secret: str, agent_id: str, updates: dict) -> dict:
        calls["config"].append((agent_id, dict(updates)))
        out = {"tts_voice": agent_id and updates.get("tts_voice", "v"), "llm_model": "qwen"}
        return {"ok": True, "agent_id": agent_id, **out}

    def fake_bind(*, base_url: str, secret: str, agent_id: str, code: str) -> dict:
        calls["bind"].append((agent_id, code))
        if code != "123456":
            raise xiaopai_mod.XiaopaiError("验证码无效，请重新输入", status=400)
        return {"ok": True, "agent_id": agent_id}

    monkeypatch.setattr(xiaopai_mod, "request_options", fake_options)
    monkeypatch.setattr(xiaopai_mod, "request_clone", fake_clone)
    monkeypatch.setattr(xiaopai_mod, "request_config", fake_config)
    monkeypatch.setattr(xiaopai_mod, "request_bind", fake_bind)
    return calls


def test_settings_options_lists_current_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(monkeypatch, tmp_path)
    _login_phone(client)
    _fake_forward(monkeypatch)
    r = client.get("/api/settings/xiaopai")
    assert r.status_code == 200
    body = r.json()
    assert [v["voice_id"] for v in body["voices"]] == [
        "zh_female_a_moon_bigtts",
        "zh_female_b_uranus_bigtts",
    ]
    assert [m["name"] for m in body["models"]] == ["xz-lite", "deepseek"]
    assert body["tts_voice"] == "" and body["llm_model"] == ""
    assert body["device_bound"] is False and body["agent_ready"] is False


def test_settings_options_unconfigured_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(monkeypatch, tmp_path, ingest_secret="")
    _login_phone(client)
    _fake_forward(monkeypatch)
    r = client.get("/api/settings/xiaopai")
    assert r.status_code == 503


def test_apply_creates_agent_then_reuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(monkeypatch, tmp_path)
    _login_phone(client)
    calls = _fake_forward(monkeypatch)
    r1 = client.post(
        "/api/settings/xiaopai",
        json={"tts_voice": "zh_female_a_moon_bigtts", "llm_model": "qwen"},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["ok"] is True
    assert calls["clone"] and calls["config"] == [
        ("5550001", {"tts_voice": "zh_female_a_moon_bigtts", "llm_model": "qwen"})
    ]
    # 第二次切换不再克隆，agent 复用
    r2 = client.post("/api/settings/xiaopai", json={"llm_model": "xz-lite"})
    assert r2.status_code == 200
    assert len(calls["clone"]) == 1
    assert calls["config"][1] == ("5550001", {"llm_model": "xz-lite"})


def test_apply_requires_at_least_one_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(monkeypatch, tmp_path)
    _login_phone(client)
    _fake_forward(monkeypatch)
    r = client.post("/api/settings/xiaopai", json={})
    assert r.status_code == 400
    assert r.json()["detail"] == "tts_voice / llm_model 至少一项"


def test_apply_daily_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, tmp_path)
    _login_phone(client)
    _fake_forward(monkeypatch)
    for _ in range(20):
        resp = client.post("/api/settings/xiaopai", json={"llm_model": "qwen"})
        assert resp.status_code == 200
    resp = client.post("/api/settings/xiaopai", json={"llm_model": "qwen"})
    assert resp.status_code == 429


def test_bind_sets_device_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, tmp_path)
    _login_phone(client)
    calls = _fake_forward(monkeypatch)
    bad = client.post("/api/settings/xiaopai/bind", json={"code": "999999"})
    assert bad.status_code == 400
    assert bad.json()["detail"] == "验证码无效，请重新输入"
    good = client.post("/api/settings/xiaopai/bind", json={"code": "123456"})
    assert good.status_code == 200
    assert good.json()["device_bound"] is True
    state = client.get("/api/settings/xiaopai")
    assert state.json()["device_bound"] is True


def test_bind_daily_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, tmp_path)
    _login_phone(client)
    _fake_forward(monkeypatch)
    for _ in range(10):
        assert client.post("/api/settings/xiaopai/bind", json={"code": "123456"}).status_code == 200
    assert client.post("/api/settings/xiaopai/bind", json={"code": "123456"}).status_code == 429


def test_agent_id_never_leaks_to_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(monkeypatch, tmp_path)
    _login_phone(client)
    _fake_forward(monkeypatch)
    assert client.post("/api/settings/xiaopai", json={"llm_model": "qwen"}).status_code == 200
    assert client.post("/api/settings/xiaopai/bind", json={"code": "123456"}).status_code == 200
    for resp in (
        client.get("/api/settings/xiaopai"),
        client.post("/api/settings/xiaopai", json={"llm_model": "xz-lite"}),
    ):
        assert "agent_id" not in resp.json()


def test_store_rows_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path)
    store = Store(tmp_path / "t.db")
    assert store.xiaopai_settings_get("u1") is None
    store.xiaopai_settings_insert("u1", "555", when=1.0)
    row = store.xiaopai_settings_get("u1")
    assert row["agent_id"] == "555" and row["tts_voice"] == "" and row["device_bound"] == 0
    store.xiaopai_settings_update("u1", tts_voice="v", device_bound=1, when=2.0)
    row = store.xiaopai_settings_get("u1")
    assert row["tts_voice"] == "v" and row["device_bound"] == 1 and row["updated_at"] == 2.0


def test_failed_bind_attempts_exhaust_daily_limit(tmp_path, monkeypatch):
    client = _client(monkeypatch, tmp_path)
    _login_phone(client)
    calls = _fake_forward(monkeypatch)
    for _ in range(10):
        assert client.post("/api/settings/xiaopai/bind", json={"code": "999999"}).status_code == 400
    assert client.post("/api/settings/xiaopai/bind", json={"code": "123456"}).status_code == 429
    assert len(calls["bind"]) == 10
    store = Store(tmp_path / "t.db")
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM usage_events WHERE event_type='xiaopai_bind' AND ok=0").fetchone()[0] == 10


def test_bind_attempt_limit_is_atomic_and_expires(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = Store(tmp_path / "quota.db")
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: store.reserve_xiaopai_bind_attempt("u", when=100000), range(24)))
    assert len([r for r in results if r is not None]) == 10
    assert store.reserve_xiaopai_bind_attempt("other", when=100000) is not None
    assert store.reserve_xiaopai_bind_attempt("u", when=186401) is not None
