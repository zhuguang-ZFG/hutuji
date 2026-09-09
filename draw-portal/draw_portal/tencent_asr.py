"""腾讯云一句话识别（SentenceRecognition）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone

import httpx

MAX_AUDIO_BYTES = 1_572_864

_ASR_HOST = "asr.tencentcloudapi.com"
_ASR_SERVICE = "asr"
_ASR_ACTION = "SentenceRecognition"
_ASR_VERSION = "2019-06-14"
_ASR_REGION = "ap-shanghai"
_HTTP_TIMEOUT = 20.0


class AsrError(Exception):
    """语音识别失败。"""


def asr_configured() -> bool:
    return all(
        os.environ.get(name, "").strip()
        for name in (
            "DRAW_PORTAL_TENCENT_SECRET_ID",
            "DRAW_PORTAL_TENCENT_SECRET_KEY",
            "DRAW_PORTAL_TENCENT_ASR_APP_ID",
        )
    )


def _load_credentials() -> tuple[str, str, int]:
    secret_id = os.environ.get("DRAW_PORTAL_TENCENT_SECRET_ID", "").strip()
    secret_key = os.environ.get("DRAW_PORTAL_TENCENT_SECRET_KEY", "").strip()
    app_id_raw = os.environ.get("DRAW_PORTAL_TENCENT_ASR_APP_ID", "").strip()
    if not secret_id or not secret_key or not app_id_raw:
        raise AsrError("语音识别未配置")
    try:
        app_id = int(app_id_raw)
    except ValueError as exc:
        raise AsrError("语音识别未配置") from exc
    return secret_id, secret_key, app_id


def _tc3_authorization(
    *,
    secret_id: str,
    secret_key: str,
    host: str,
    service: str,
    payload: str,
    timestamp: int,
) -> str:
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    content_type = "application/json; charset=utf-8"
    canonical_headers = f"content-type:{content_type}\nhost:{host}\n"
    signed_headers = "content-type;host"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = "\n".join(
        (
            "POST",
            "/",
            "",
            canonical_headers,
            signed_headers,
            hashed_payload,
        )
    )
    credential_scope = f"{date}/{service}/tc3_request"
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = "\n".join(
        (
            "TC3-HMAC-SHA256",
            str(timestamp),
            credential_scope,
            hashed_canonical,
        )
    )

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    secret_date = _sign(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _sign(secret_date, service)
    secret_signing = _sign(secret_service, "tc3_request")
    signature = hmac.new(
        secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return (
        "TC3-HMAC-SHA256 "
        f"Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )


def _build_request_payload(*, audio: bytes, voice_format: str) -> dict[str, object]:
    # SentenceRecognition（2019-06-14）请求体不含 AppId；账号由 TC3 签名的
    # SecretId 标识。带 AppId 会回 UnknownParameter（生产 2026-09-09 实测）。
    return {
        "ProjectId": 0,
        "SubServiceType": 2,
        "EngSerViceType": "16k_zh",
        "SourceType": 1,
        "VoiceFormat": voice_format,
        "UsrAudioKey": secrets.token_hex(8),
        "Data": base64.b64encode(audio).decode("ascii"),
        "DataLen": len(audio),
    }


def _call_sentence_recognition(
    *,
    secret_id: str,
    secret_key: str,
    payload: dict[str, object],
) -> dict:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    timestamp = int(time.time())
    headers = {
        "Authorization": _tc3_authorization(
            secret_id=secret_id,
            secret_key=secret_key,
            host=_ASR_HOST,
            service=_ASR_SERVICE,
            payload=body,
            timestamp=timestamp,
        ),
        "Content-Type": "application/json; charset=utf-8",
        "Host": _ASR_HOST,
        "X-TC-Action": _ASR_ACTION,
        "X-TC-Version": _ASR_VERSION,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Region": _ASR_REGION,
    }
    url = f"https://{_ASR_HOST}/"
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        response = client.post(url, content=body.encode("utf-8"), headers=headers)
    if response.status_code != 200:
        raise AsrError(f"语音识别 HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise AsrError("语音识别响应无效") from exc
    if not isinstance(data, dict):
        raise AsrError("语音识别响应无效")
    return data


def recognize_sentence(*, audio: bytes, voice_format: str = "mp3") -> str:
    if not asr_configured():
        raise AsrError("语音识别未配置")
    if not audio:
        raise AsrError("录音为空")
    if len(audio) > MAX_AUDIO_BYTES:
        raise AsrError("录音过长")

    secret_id, secret_key, _app_id = _load_credentials()
    payload = _build_request_payload(audio=audio, voice_format=voice_format)
    data = _call_sentence_recognition(
        secret_id=secret_id, secret_key=secret_key, payload=payload
    )

    response = data.get("Response")
    if not isinstance(response, dict):
        raise AsrError("语音识别响应无效")

    error = response.get("Error")
    if isinstance(error, dict):
        code = error.get("Code")
        message = error.get("Message")
        parts: list[str] = []
        if isinstance(code, str) and code:
            parts.append(code)
        if isinstance(message, str) and message.strip():
            parts.append(message.strip()[:120])
        if parts:
            raise AsrError("语音识别错误 " + " ".join(parts))
        raise AsrError("语音识别错误")

    result = response.get("Result")
    if not isinstance(result, str):
        raise AsrError("未识别到语音")
    text = result.strip()
    if not text:
        raise AsrError("未识别到语音")
    return text
