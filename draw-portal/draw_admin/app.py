from __future__ import annotations

from typing import Any

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pathlib import Path

from draw_admin.auth import (
    ADMIN_SESSION_COOKIE,
    clear_admin_session_cookie,
    require_admin,
    set_admin_session_cookie,
    verify_admin_password,
)
from draw_portal.config import load_admin_config
from draw_portal.store import Store


class AdminLoginBody(BaseModel):
    password: str


class BanBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class NoteBody(BaseModel):
    note: str = Field(max_length=1000)


def create_app() -> FastAPI:
    config = load_admin_config()
    store = Store(config.db_path)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def admin_dep(
        draw_admin_session: str | None = Cookie(default=None, alias=ADMIN_SESSION_COOKIE),
    ) -> None:
        require_admin(config.session_secret, draw_admin_session)

    secured = [Depends(admin_dep)]

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "draw-admin"}

    @app.post("/api/auth/login")
    def login(body: AdminLoginBody, response: Response) -> dict[str, str]:
        if not verify_admin_password(body.password, config.admin_password):
            raise HTTPException(status_code=401, detail="密码错误")
        set_admin_session_cookie(response, secret=config.session_secret)
        return {"status": "ok"}

    @app.post("/api/auth/logout", dependencies=secured)
    def logout(response: Response) -> dict[str, str]:
        clear_admin_session_cookie(response)
        return {"status": "ok"}

    @app.get("/api/stats", dependencies=secured)
    def stats() -> dict[str, Any]:
        return store.platform_stats()

    @app.get("/api/users", dependencies=secured)
    def users(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        items = store.list_users(limit=min(limit, 200), offset=max(offset, 0))
        return {
            "total": store.count_users(),
            "items": [
                {
                    "id": it.user.id,
                    "email": it.user.email,
                    "status": it.user.status,
                    "created_at": it.user.created_at,
                    "email_verified_at": it.user.email_verified_at,
                    "last_login_at": it.user.last_login_at,
                    "banned_at": it.user.banned_at,
                    "ban_reason": it.user.ban_reason,
                    "admin_note": it.user.admin_note,
                    "job_count": it.job_count,
                    "saved_count": it.saved_count,
                    "bound": it.bound,
                    "device_mac": it.device_mac,
                }
                for it in items
            ],
        }

    @app.get("/api/users/{user_id}", dependencies=secured)
    def user_detail(user_id: str) -> dict[str, Any]:
        detail = store.user_detail(user_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        user = detail["user"]
        binding = detail["binding"]
        return {
            "user": {
                "id": user.id,
                "email": user.email,
                "status": user.status,
                "created_at": user.created_at,
                "email_verified_at": user.email_verified_at,
                "last_login_at": user.last_login_at,
                "banned_at": user.banned_at,
                "ban_reason": user.ban_reason,
                "admin_note": user.admin_note,
            },
            "binding": store.binding_status_dict(binding) if binding else {"bound": False},
            "device_mac_full": binding.device_mac if binding else None,
            "recent_jobs": detail["recent_jobs"],
            "recent_saved": detail["recent_saved"],
        }

    @app.post("/api/users/{user_id}/ban", dependencies=secured)
    def ban_user(user_id: str, body: BanBody) -> dict[str, str]:
        import time as _time

        user = store.set_user_banned(user_id, reason=body.reason, when=_time.time())
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        store.log_admin_action(
            action="ban",
            target_user_id=user_id,
            detail=body.reason[:200],
        )
        return {"status": "banned"}

    @app.post("/api/users/{user_id}/unban", dependencies=secured)
    def unban_user(user_id: str) -> dict[str, str]:
        user = store.set_user_active(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        store.log_admin_action(action="unban", target_user_id=user_id, detail=None)
        return {"status": "active"}

    @app.post("/api/users/{user_id}/unbind", dependencies=secured)
    def admin_unbind(user_id: str) -> dict[str, str]:
        if not store.delete_binding_for_user(user_id):
            raise HTTPException(status_code=404, detail="未绑定设备")
        store.log_admin_action(action="unbind", target_user_id=user_id, detail=None)
        return {"status": "unbound"}

    @app.post("/api/users/{user_id}/note", dependencies=secured)
    def set_note(user_id: str, body: NoteBody) -> dict[str, str]:
        if store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        store.set_admin_note(user_id, body.note or None)
        store.log_admin_action(action="note", target_user_id=user_id, detail=None)
        return {"status": "ok"}

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


if __name__ == "__main__":
    import uvicorn

    cfg = load_admin_config()
    uvicorn.run(
        "draw_admin.app:create_app",
        factory=True,
        host=cfg.host,
        port=cfg.port,
    )
