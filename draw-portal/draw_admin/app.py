from __future__ import annotations

import csv
import io
import time
from typing import Any

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pathlib import Path

from draw_portal.analytics import retention_cutoff, summarize_usage_rows
from draw_portal.draw_limits import DRAW_LIMIT_BOUNDS, DRAW_LIMIT_KEYS
from draw_portal.image_backends import IMAGE_BACKEND_LABELS, IMAGE_BACKEND_NAMES
from draw_portal.image_providers import PROVIDER_TYPES
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


class DrawLimitsPatchBody(BaseModel):
    draw_max_concurrent: int | None = None
    draw_per_second: int | None = None
    draw_per_minute: int | None = None
    vpype_max_concurrent: int | None = None


class ImageBackendsPatchBody(BaseModel):
    sensenova: bool | None = None
    agnes: bool | None = None
    siliconflow: bool | None = None
    fallback: bool | None = None


class ImageProviderCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider_type: str
    base_url: str
    model: str = Field(min_length=1, max_length=128)
    extra: dict[str, Any] | None = None
    enabled: bool = True
    race_weight: int = Field(default=100, ge=1, le=1000)
    sort_order: int = 0


class ImageProviderUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    provider_type: str | None = None
    base_url: str | None = None
    model: str | None = Field(default=None, min_length=1, max_length=128)
    extra: dict[str, Any] | None = None
    enabled: bool | None = None
    race_weight: int | None = Field(default=None, ge=1, le=1000)
    sort_order: int | None = None


class ImageProviderKeyBody(BaseModel):
    api_key: str = Field(min_length=1, max_length=512)
    label: str | None = Field(default=None, max_length=80)


class ImageProviderKeyUpdateBody(BaseModel):
    api_key: str | None = Field(default=None, min_length=1, max_length=512)
    label: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None


class OpsNotesBody(BaseModel):
    weapp_version: str | None = Field(default=None, max_length=80)
    firmware_note: str | None = Field(default=None, max_length=500)
    shipping_note: str | None = Field(default=None, max_length=500)


def _conversation_csv(rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["session_id", "role", "content", "client_ts", "created_at", "device_mac"]
    )
    for row in rows:
        writer.writerow(
            [
                row.get("session_id", ""),
                row.get("role", ""),
                row.get("content", ""),
                row.get("client_ts", ""),
                row.get("created_at", ""),
                row.get("device_mac", ""),
            ]
        )
    # Excel 中文：UTF-8 BOM
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def create_app() -> FastAPI:
    config = load_admin_config()
    store = Store(config.db_path, provider_cipher_key=config.provider_cipher_key)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def _require_provider_cipher() -> None:
        if not config.provider_cipher_key:
            raise HTTPException(
                status_code=503,
                detail="生图通道加密未配置（DRAW_PORTAL_PROVIDER_CIPHER_KEY）",
            )

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
        base = store.platform_stats()
        since = retention_cutoff(retention_days=7)
        usage_rows = store.usage_counts_since(since)
        base["usage_7d"] = summarize_usage_rows(usage_rows)
        return base

    @app.get("/api/analytics/usage", dependencies=secured)
    def analytics_usage(days: int = 7) -> dict[str, Any]:
        days = max(1, min(days, 90))
        since = time.time() - days * 86400
        rows = store.usage_counts_since(since)
        return {"days": days, "items": rows, "totals": summarize_usage_rows(rows)}

    @app.get("/api/users/{user_id}/conversations", dependencies=secured)
    def user_conversations(user_id: str, limit: int = 30) -> dict[str, Any]:
        if store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        limit = min(limit, 100)
        sessions = store.list_conversation_sessions(user_id=user_id, limit=limit)
        store.log_admin_action(
            action="view_conversations",
            target_user_id=user_id,
            detail=f"limit={limit} count={len(sessions)}",
        )
        return {"items": sessions}

    @app.get("/api/users/{user_id}/conversations/export", dependencies=secured)
    def user_conversations_export(
        user_id: str,
        session_id: str | None = None,
        limit: int = 5000,
    ) -> StreamingResponse:
        if store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        limit = min(limit, 20000)
        rows = store.list_conversation_export_rows(
            user_id=user_id,
            session_id=session_id,
            limit=limit,
        )
        if not rows:
            raise HTTPException(status_code=404, detail="无对话记录")
        store.log_admin_action(
            action="export_conversations",
            target_user_id=user_id,
            detail=(
                f"session={session_id[:32] if session_id else 'all'} "
                f"rows={len(rows)} limit={limit}"
            ),
        )
        suffix = session_id[:8] if session_id else "all"
        filename = f"conversations_{user_id[:8]}_{suffix}.csv"
        return StreamingResponse(
            iter([_conversation_csv(rows)]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get(
        "/api/users/{user_id}/conversations/{session_id}", dependencies=secured
    )
    def user_conversation_detail(user_id: str, session_id: str) -> dict[str, Any]:
        if store.get_user(user_id) is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        messages = store.list_conversation_messages(
            session_id=session_id,
            user_id=user_id,
            limit=300,
        )
        if not messages:
            raise HTTPException(status_code=404, detail="会话不存在")
        store.log_admin_action(
            action="view_conversation_detail",
            target_user_id=user_id,
            detail=f"session={session_id[:32]} messages={len(messages)}",
        )
        return {"session_id": session_id, "messages": messages}

    @app.get("/api/settings/draw-limits", dependencies=secured)
    def get_draw_limits() -> dict[str, Any]:
        overrides = store.get_draw_limit_overrides()
        return {
            "overrides": overrides,
            "bounds": {k: {"min": lo, "max": hi} for k, (lo, hi) in DRAW_LIMIT_BOUNDS.items()},
            "keys": list(DRAW_LIMIT_KEYS),
            "hint": "留空表示沿用 MCP 环境变量；保存后约 30 秒内生效，无需重启服务",
        }

    @app.put("/api/settings/draw-limits", dependencies=secured)
    def put_draw_limits(body: DrawLimitsPatchBody) -> dict[str, Any]:
        patch = body.model_dump(exclude_unset=True)
        try:
            overrides = store.apply_draw_limit_patch(patch)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.log_admin_action(
            action="update_draw_limits",
            target_user_id=None,
            detail=",".join(f"{k}={v}" for k, v in overrides.items())[:200] or "cleared",
        )
        return {"status": "ok", "overrides": overrides}

    @app.get("/api/settings/image-backends", dependencies=secured)
    def get_image_backends() -> dict[str, Any]:
        toggles = store.get_image_backend_toggles()
        return {
            "toggles": toggles,
            "labels": IMAGE_BACKEND_LABELS,
            "keys": list(IMAGE_BACKEND_NAMES),
            "hint": "仅控制是否参与生图对冲；API key 仍在 MCP 环境变量中配置",
        }

    @app.put("/api/settings/image-backends", dependencies=secured)
    def put_image_backends(body: ImageBackendsPatchBody) -> dict[str, Any]:
        patch = body.model_dump(exclude_unset=True)
        toggles = store.apply_image_backend_patch(patch)
        store.log_admin_action(
            action="update_image_backends",
            target_user_id=None,
            detail=",".join(f"{k}={'on' if v else 'off'}" for k, v in toggles.items())[:200],
        )
        return {"status": "ok", "toggles": toggles}

    @app.get("/api/image-providers", dependencies=secured)
    def list_image_providers() -> dict[str, Any]:
        _require_provider_cipher()
        return {
            "revision": store.get_image_provider_revision(),
            "provider_types": list(PROVIDER_TYPES),
            "items": store.list_image_providers_admin(),
        }

    @app.post("/api/image-providers", dependencies=secured)
    def create_image_provider(body: ImageProviderCreateBody) -> dict[str, Any]:
        _require_provider_cipher()
        try:
            item = store.create_image_provider(
                name=body.name,
                provider_type=body.provider_type,
                base_url=body.base_url,
                model=body.model,
                extra=body.extra,
                enabled=body.enabled,
                race_weight=body.race_weight,
                sort_order=body.sort_order,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.log_admin_action(
            action="create_image_provider",
            target_user_id=None,
            detail=f"id={item['id'][:16]} type={item['provider_type']}",
        )
        return {"status": "ok", "item": item, "revision": store.get_image_provider_revision()}

    @app.put("/api/image-providers/{provider_id}", dependencies=secured)
    def update_image_provider(provider_id: str, body: ImageProviderUpdateBody) -> dict[str, Any]:
        _require_provider_cipher()
        patch = body.model_dump(exclude_unset=True)
        try:
            item = store.update_image_provider(provider_id, patch=patch)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="通道不存在")
        store.log_admin_action(
            action="update_image_provider",
            target_user_id=None,
            detail=f"id={provider_id[:16]}",
        )
        return {"status": "ok", "item": item, "revision": store.get_image_provider_revision()}

    @app.delete("/api/image-providers/{provider_id}", dependencies=secured)
    def delete_image_provider(provider_id: str) -> dict[str, str]:
        _require_provider_cipher()
        if not store.delete_image_provider(provider_id):
            raise HTTPException(status_code=404, detail="通道不存在")
        store.log_admin_action(
            action="delete_image_provider",
            target_user_id=None,
            detail=f"id={provider_id[:16]}",
        )
        return {"status": "deleted"}

    @app.post("/api/image-providers/{provider_id}/keys", dependencies=secured)
    def add_image_provider_key(provider_id: str, body: ImageProviderKeyBody) -> dict[str, Any]:
        _require_provider_cipher()
        try:
            item = store.add_image_provider_key(
                provider_id,
                api_key=body.api_key,
                label=body.label,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="通道不存在")
        store.log_admin_action(
            action="add_provider_key",
            target_user_id=None,
            detail=f"provider={provider_id[:16]} key={item['id'][:16]}",
        )
        return {"status": "ok", "item": item, "revision": store.get_image_provider_revision()}

    @app.put("/api/image-providers/keys/{key_id}", dependencies=secured)
    def update_image_provider_key(key_id: str, body: ImageProviderKeyUpdateBody) -> dict[str, Any]:
        _require_provider_cipher()
        try:
            item = store.update_image_provider_key(
                key_id,
                enabled=body.enabled,
                api_key=body.api_key,
                label=body.label,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(status_code=404, detail="key 不存在")
        store.log_admin_action(
            action="update_provider_key",
            target_user_id=None,
            detail=f"key={key_id[:16]}",
        )
        return {"status": "ok", "item": item, "revision": store.get_image_provider_revision()}

    @app.delete("/api/image-providers/keys/{key_id}", dependencies=secured)
    def delete_image_provider_key(key_id: str) -> dict[str, str]:
        _require_provider_cipher()
        if not store.delete_image_provider_key(key_id):
            raise HTTPException(status_code=404, detail="key 不存在")
        store.log_admin_action(
            action="delete_provider_key",
            target_user_id=None,
            detail=f"key={key_id[:16]}",
        )
        return {"status": "deleted"}

    @app.get("/api/users", dependencies=secured)
    def users(limit: int = 100, offset: int = 0) -> dict[str, Any]:
        items = store.list_users(limit=min(limit, 200), offset=max(offset, 0))
        return {
            "total": store.count_users(),
            "items": [
                {
                    "id": it.user.id,
                    "email": it.user.email,
                    "phone": it.user.phone,
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

    @app.get("/api/bindings", dependencies=secured)
    def bindings(limit: int = 200) -> dict[str, Any]:
        items = store.list_device_bindings(limit=limit)
        return {"total": len(items), "items": items}

    @app.get("/api/ops/notes", dependencies=secured)
    def get_ops_notes() -> dict[str, str]:
        return store.get_ops_notes()

    @app.put("/api/ops/notes", dependencies=secured)
    def put_ops_notes(body: OpsNotesBody) -> dict[str, Any]:
        notes = store.set_ops_notes(
            weapp_version=body.weapp_version,
            firmware_note=body.firmware_note,
            shipping_note=body.shipping_note,
        )
        store.log_admin_action(
            action="update_ops_notes",
            target_user_id=None,
            detail=f"weapp={notes.get('weapp_version','')[:32]}",
        )
        return {"status": "ok", "notes": notes}

    @app.get("/api/ops/checklist", dependencies=secured)
    def ops_checklist() -> dict[str, Any]:
        """只读签核清单：与 docs/release-checklist.md 对齐，不伪造勾选状态。"""
        return {
            "source": "docs/release-checklist.md",
            "note": "本页只列项；是否勾实以仓内清单与 release-readiness 为准，不得据此宣称可出货。",
            "sections": [
                {
                    "id": "A",
                    "title": "冻结",
                    "items": [
                        "hutuji / Grbl / xiaozhi commit 与依赖锁",
                        "固件二进制 SHA256（若本批烧录）",
                    ],
                },
                {
                    "id": "B",
                    "title": "自动化",
                    "items": ["mcp pytest 全绿", "e2e_local PASS", "桥接烟测（若改桥接）"],
                },
                {
                    "id": "C",
                    "title": "双机",
                    "items": ["unit/nginx 一致或已记 diff", "TLS 与公网面", "回滚包可恢复"],
                },
                {
                    "id": "D",
                    "title": "硬件联调",
                    "items": ["bringup 阶段记录", "协议数字回填或标注仍为推导"],
                },
                {
                    "id": "E",
                    "title": "生产与合规",
                    "items": [
                        "production-sop 首件",
                        "device-test-record",
                        "compliance 签核",
                    ],
                },
                {
                    "id": "F",
                    "title": "审批",
                    "items": ["研发/QA/运维/生产签字栏"],
                },
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
