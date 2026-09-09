"""POST /api/voice/asr 与 /api/voice/turn 契约。"""
from __future__ import annotations

from pathlib import Path

import pytest

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
