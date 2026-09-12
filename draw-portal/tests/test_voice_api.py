"""POST /api/voice/asr 与 /api/voice/turn 契约。"""
from __future__ import annotations
import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from draw_portal.store import Store
from draw_portal.voice_jobs import VoiceJobError
from test_app import _client, _register_and_login  # noqa: E402


def _auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _client(monkeypatch, tmp_path)
    _register_and_login(client)
    return client


def test_voice_asr_requires_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _client(monkeypatch, tmp_path)
    r = client.post(
        "/api/voice/asr",
        files={"file": ("clip.mp3", b"audio-bytes", "audio/mpeg")},
    )
    assert r.status_code in (401, 403)


def test_voice_turn_requires_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _client(monkeypatch, tmp_path)
    r = client.post(
        "/api/voice/turn",
        json={"session_id": "s1", "text": "你好"},
    )
    assert r.status_code in (401, 403)


def test_voice_asr_not_configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)
    monkeypatch.setattr("draw_portal.tencent_asr.asr_configured", lambda: False)
    r = client.post(
        "/api/voice/asr",
        files={"file": ("clip.mp3", b"audio-bytes", "audio/mpeg")},
    )
    assert r.status_code == 503
    assert "未配置" in r.json()["detail"]


def test_voice_turn_empty_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)
    r = client.post(
        "/api/voice/turn",
        json={"session_id": "s1", "text": ""},
    )
    assert r.status_code == 422


def test_voice_turn_missing_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)
    r = client.post("/api/voice/turn", json={"session_id": "s1"})
    assert r.status_code == 422


def test_voice_turn_llm_not_configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)
    monkeypatch.delenv("HUTUJI_LLM_API_KEY", raising=False)
    monkeypatch.delenv("HUTUJI_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("HUTUJI_LLM_MODEL", raising=False)
    r = client.post(
        "/api/voice/turn",
        json={"session_id": "s1", "text": "你好"},
    )
    assert r.status_code == 503
    assert "未配置" in r.json()["detail"]


def test_voice_asr_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)

    def fake_recognize(*, audio: bytes, voice_format: str = "mp3") -> str:
        assert audio == b"audio-bytes"
        assert voice_format == "wav"
        return "识别到了"

    monkeypatch.setattr("draw_portal.tencent_asr.recognize_sentence", fake_recognize)
    r = client.post(
        "/api/voice/asr",
        files={"file": ("clip.wav", b"audio-bytes", "audio/wav")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "识别到了"


def test_voice_confirm_requires_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/voice/confirm")
    assert r.status_code in (401, 403)


def test_voice_cancel_requires_binding(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)
    r = client.post("/api/voice/cancel")
    assert r.status_code == 400
    assert "绑定" in r.json()["detail"]


def test_voice_turn_returns_preview(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    client = _auth(monkeypatch, tmp_path)

    def fake_chat(**kwargs):
        return {
            "reply": "请点开始画",
            "tool_traces": [{"name": "voice.draw", "summary": "ok"}],
            "preview": {
                "kind": "draw",
                "job_id": "j1",
                "preview_url": "https://example.test/p.png",
                "gcode_url": "https://example.test/p.gcode",
            },
        }

    monkeypatch.setenv("HUTUJI_LLM_API_KEY", "k")
    monkeypatch.setenv("HUTUJI_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("HUTUJI_LLM_MODEL", "m")
    monkeypatch.setattr("draw_portal.voice_agent.chat", fake_chat)
    r = client.post(
        "/api/voice/turn",
        json={"session_id": "s-preview", "text": "画猫"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preview"]["preview_url"].endswith(".png")
    assert body["reply"] == "请点开始画"


@pytest.mark.parametrize(
    "status,expect_failed_notify",
    [(429, False), (502, True)],
)
def test_voice_draw_busy_429_suppresses_failed_notification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: int,
    expect_failed_notify: bool,
):
    """2026-09-12 忙批：语音画遇 MCP 全忙 429 不推「生成失败」订阅消息——与
    /api/draw/generate 同口径（瞬态忙几秒后重试即成，失败推送+随后的成功推送
    只会误导用户）；502 等真失败照常推。"""
    monkeypatch.setenv("DRAW_PORTAL_USAGE_INGEST_SECRET", "s")
    client = _auth(monkeypatch, tmp_path)
    store = Store(tmp_path / "t.db")
    user = store.get_user_by_email("buyer@qq.com")
    assert user is not None
    key = os.environ["DRAW_PORTAL_TOKEN_CIPHER_KEY"].encode("ascii")
    store.set_device_binding(
        user_id=user.id,
        device_mac="aa:bb:cc:dd:ee:ff",
        device_token_enc=Fernet(key).encrypt(b"tok").decode("ascii"),
        bound_at=1.0,
        token_probe_ok=True,
    )
    notifies: list[str] = []
    monkeypatch.setattr(
        "draw_portal.app._notify_subscribe",
        lambda *args, **kwargs: notifies.append(kwargs["status"]) or True,
    )

    def boom(**kwargs):
        raise VoiceJobError("生图通道都在忙，请稍等几秒再试", status=status)

    monkeypatch.setattr("draw_portal.voice_jobs.run_voice_draw", boom)

    def fake_chat(**kwargs):
        try:
            kwargs["run_portal_tool"]("voice.draw", {"description": "一只猫"})
        except VoiceJobError as exc:
            return {"reply": str(exc), "tool_traces": [], "preview": None}
        raise AssertionError("run_portal_tool 未抛 VoiceJobError")

    monkeypatch.setenv("HUTUJI_LLM_API_KEY", "k")
    monkeypatch.setenv("HUTUJI_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("HUTUJI_LLM_MODEL", "m")
    monkeypatch.setattr("draw_portal.voice_agent.chat", fake_chat)
    r = client.post("/api/voice/turn", json={"session_id": "s-busy", "text": "画猫"})
    assert r.status_code == 200, r.text
    expected = ["failed"] if expect_failed_notify else []
    assert notifies == expected