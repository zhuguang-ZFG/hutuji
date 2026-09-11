"""分享令牌：无状态 HMAC 签名（24h TTL），接收者免会话只读预览。

令牌形如 ``b64url("{job_id}:{exp}")`` + "." + ``b64url(HMAC-SHA256)``；
校验失败/过期一律返回 None，调用方统一 404，不回显内部细节。
令牌只授予只读预览，不含 G-code 下载，不入库（重启不失效，密钥轮换即全废）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

_TTL_SECONDS = 24 * 3600


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _from_b64url(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def issue_token(secret: str, job_id: str, *, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + _TTL_SECONDS)
    payload = f"{job_id}:{exp}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return _b64url(payload) + "." + _b64url(digest)


def parse_token(secret: str, token: str, *, now: float | None = None) -> str | None:
    if not token or token.count(".") != 1:
        return None
    try:
        body_b, sig_b = token.split(".", 1)
        payload = _from_b64url(body_b)
        expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    try:
        given = _from_b64url(sig_b)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(given, expected):
        return None
    try:
        text = payload.decode("utf-8")
        job_id, exp_raw = text.rsplit(":", 1)
        exp = int(exp_raw)
    except (ValueError, UnicodeDecodeError):
        return None
    now_ts = now if now is not None else time.time()
    if exp < now_ts:
        return None
    if not job_id:
        return None
    return job_id
