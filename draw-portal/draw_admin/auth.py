from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import HTTPException, Response

ADMIN_SESSION_COOKIE = "draw_admin_session"
_SESSION_TTL_SECONDS = 12 * 3600


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _encode_session(secret: str) -> str:
    body = json.dumps({"adm": 1, "exp": int(time.time()) + _SESSION_TTL_SECONDS})
    sig = _sign(secret, body)
    return f"{body}.{sig}"


def _decode_session(secret: str, cookie: str | None) -> bool:
    if not cookie or "." not in cookie:
        return False
    body, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(_sign(secret, body), sig):
        return False
    try:
        data: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        return False
    if int(data.get("exp", 0)) < int(time.time()):
        return False
    return data.get("adm") == 1


def verify_admin_password(password: str, expected: str) -> bool:
    return hmac.compare_digest(password, expected)


def set_admin_session_cookie(response: Response, *, secret: str) -> None:
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=_encode_session(secret),
        httponly=True,
        samesite="lax",
        path="/",
        max_age=_SESSION_TTL_SECONDS,
    )


def clear_admin_session_cookie(response: Response) -> None:
    response.delete_cookie(key=ADMIN_SESSION_COOKIE, path="/")


def require_admin(secret: str, cookie: str | None) -> None:
    if not _decode_session(secret, cookie):
        raise HTTPException(status_code=401, detail="未登录运营后台")
