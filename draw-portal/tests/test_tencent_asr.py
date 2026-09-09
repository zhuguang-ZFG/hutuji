"""腾讯云一句话识别 helper 契约。"""
from __future__ import annotations

import pytest

from draw_portal import tencent_asr as m


def test_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DRAW_PORTAL_TENCENT_SECRET_ID", raising=False)
    monkeypatch.delenv("DRAW_PORTAL_TENCENT_SECRET_KEY", raising=False)
    monkeypatch.delenv("DRAW_PORTAL_TENCENT_ASR_APP_ID", raising=False)
    assert m.asr_configured() is False
    with pytest.raises(m.AsrError, match="未配置"):
        m.recognize_sentence(audio=b"abc")


def test_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_SECRET_ID", "id")
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_SECRET_KEY", "key")
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_ASR_APP_ID", "123")
    assert m.asr_configured() is True
    with pytest.raises(m.AsrError, match="过长"):
        m.recognize_sentence(audio=b"x" * (m.MAX_AUDIO_BYTES + 1))


def test_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_SECRET_ID", "id")
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_SECRET_KEY", "key")
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_ASR_APP_ID", "123")
    with pytest.raises(m.AsrError, match="为空"):
        m.recognize_sentence(audio=b"")


def test_httpx_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_SECRET_ID", "id")
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_SECRET_KEY", "key")
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_ASR_APP_ID", "123")

    seen: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"Response": {"Result": " 你好 "}}

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, *, content: bytes, headers: dict) -> _FakeResponse:
            import json

            seen["payload"] = json.loads(content.decode("utf-8"))
            return _FakeResponse()

    monkeypatch.setattr(m.httpx, "Client", _FakeClient)
    assert m.recognize_sentence(audio=b"abc") == "你好"
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert "AppId" not in payload
    assert payload.get("VoiceFormat") == "mp3"
    assert payload.get("DataLen") == 3


def test_api_error_includes_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_SECRET_ID", "id")
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_SECRET_KEY", "key")
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_ASR_APP_ID", "123")

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {
                "Response": {
                    "Error": {
                        "Code": "UnknownParameter",
                        "Message": "The parameter `AppId` is not recognized.",
                    }
                }
            }

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, *, content: bytes, headers: dict) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr(m.httpx, "Client", _FakeClient)
    with pytest.raises(m.AsrError, match="UnknownParameter"):
        m.recognize_sentence(audio=b"abc")
