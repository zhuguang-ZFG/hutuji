from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import HTTPException, Response

SESSION_COOKIE = "draw_portal_session"
_SESSION_TTL_SECONDS = 7 * 24 * 3600


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _encode_session(secret: str, user_id: str) -> str:
    body = json.dumps({"uid": user_id, "exp": int(time.time()) + _SESSION_TTL_SECONDS})
    sig = _sign(secret, body)
    return f"{body}.{sig}"


def _decode_session(secret: str, cookie: str | None) -> str | None:
    if not cookie or "." not in cookie:
        return None
    body, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(_sign(secret, body), sig):
        return None
    try:
        data: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        return None
    if int(data.get("exp", 0)) < int(time.time()):
        return None
    uid = data.get("uid")
    return uid if isinstance(uid, str) and uid else None


def set_session_cookie(response: Response, *, secret: str, user_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=_encode_session(secret, user_id),
        httponly=True,
        samesite="lax",
        path="/",
        max_age=_SESSION_TTL_SECONDS,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE, path="/")


def verify_session(secret: str, cookie: str | None) -> str | None:
    return _decode_session(secret, cookie)


def require_user_id(secret: str, cookie: str | None) -> str:
    user_id = verify_session(secret, cookie)
    if user_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    return user_id
