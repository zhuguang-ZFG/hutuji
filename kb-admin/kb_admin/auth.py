from __future__ import annotations

import hmac
import hashlib
from typing import Annotated

from fastapi import Cookie, HTTPException, Response

SESSION_COOKIE = "kb_admin_session"
_TOKEN = "ok"


def _sign(secret: str) -> str:
    return hmac.new(secret.encode(), _TOKEN.encode(), hashlib.sha256).hexdigest()


def set_session_cookie(response: Response, *, secret: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=_sign(secret),
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE, path="/")


def verify_session(secret: str, cookie: str | None) -> bool:
    if not cookie:
        return False
    expected = _sign(secret)
    return hmac.compare_digest(cookie, expected)


def require_session(
    secret: str,
    kb_admin_session: Annotated[str | None, Cookie()] = None,
) -> None:
    if not verify_session(secret, kb_admin_session):
        raise HTTPException(status_code=401, detail="未登录")
