from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from draw_portal.auth import (
    SESSION_COOKIE,
    clear_session_cookie,
    require_user_id,
    set_session_cookie,
)
from draw_portal.config import load_config
from draw_portal.crypto import decrypt_token, encrypt_token
from draw_portal.email_verify import (
    generate_email_code,
    hash_email_code,
    send_verification_email,
    verify_email_code,
)
from draw_portal.models import normalize_bind_code, normalize_email, normalize_mac, normalize_registration_email
from draw_portal.passwords import hash_password, verify_password
from draw_portal.pipeline import copy_saved_preview, process_upload
from draw_portal.probe import probe_device_token
from draw_portal.push import publish_job_files, push_for_user_binding
from draw_portal.store import Store


class RegisterRequestBody(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)


class RegisterVerifyBody(BaseModel):
    email: str
    code: str = Field(min_length=4, max_length=8)


class LoginBody(BaseModel):
    email: str
    password: str


class BindAnnounceBody(BaseModel):
    mac: str
    bind_code: str
    device_token: str = Field(min_length=8)


class BindCompleteBody(BaseModel):
    bind_code: str


class SavePreviewBody(BaseModel):
    title: str | None = None


_MAX_UPLOAD_BYTES = 4 * 1024 * 1024
_MAX_VERIFY_ATTEMPTS = 8


def _job_dir(data_dir: Path, job_id: str) -> Path:
    return data_dir / "jobs" / job_id


def _saved_dir(data_dir: Path, user_id: str) -> Path:
    return data_dir / "saved" / user_id


def _default_saved_title() -> str:
    return datetime.now().strftime("上传 %Y-%m-%d %H:%M")


def create_app() -> FastAPI:
    config = load_config()
    store = Store(config.db_path)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def user_dep(
        draw_portal_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> str:
        user_id = require_user_id(config.session_secret, draw_portal_session)
        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="未登录")
        if user.status == "banned":
            raise HTTPException(status_code=403, detail="账号已封禁")
        if user.status != "active" or user.email_verified_at is None:
            raise HTTPException(status_code=403, detail="邮箱未验证")
        return user_id

    secured = [Depends(user_dep)]

    def _send_register_code(email: str, password: str) -> dict[str, str]:
        if not config.smtp_host or not config.smtp_from:
            raise HTTPException(status_code=503, detail="邮件服务未配置")
        code = generate_email_code()
        store.upsert_email_verification(
            email=email,
            password_hash=hash_password(password),
            code_hash=hash_email_code(code),
            expires_at=time.time() + config.email_code_ttl_seconds,
        )
        try:
            send_verification_email(
                smtp_host=config.smtp_host,
                smtp_port=config.smtp_port,
                smtp_user=config.smtp_user,
                smtp_password=config.smtp_password,
                smtp_from=config.smtp_from,
                to_email=email,
                code=code,
                public_base_url=config.public_base_url,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail="验证码发送失败") from exc
        out: dict[str, str] = {"status": "code_sent"}
        if config.email_dev_expose_code:
            out["dev_code"] = code
        return out

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "draw-portal"}

    @app.post("/api/auth/register/request")
    def register_request(body: RegisterRequestBody) -> dict[str, str]:
        try:
            email = normalize_registration_email(body.email)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if store.get_user_by_email(email) is not None:
            raise HTTPException(status_code=409, detail="邮箱已注册")
        return _send_register_code(email, body.password)

    @app.post("/api/auth/register/verify")
    def register_verify(body: RegisterVerifyBody, response: Response) -> dict[str, str]:
        try:
            email = normalize_registration_email(body.email)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if store.get_user_by_email(email) is not None:
            raise HTTPException(status_code=409, detail="邮箱已注册")
        pending = store.get_email_verification(email)
        if pending is None:
            raise HTTPException(status_code=400, detail="请先申请验证码")
        now = time.time()
        if pending.expires_at < now:
            raise HTTPException(status_code=400, detail="验证码已过期")
        if pending.attempts >= _MAX_VERIFY_ATTEMPTS:
            raise HTTPException(status_code=429, detail="验证次数过多，请重新申请")
        if not verify_email_code(body.code, pending.code_hash):
            store.increment_verification_attempts(email)
            raise HTTPException(status_code=400, detail="验证码错误")
        user = store.create_user(
            email=email,
            password_hash=pending.password_hash,
            email_verified_at=now,
            status="active",
        )
        store.delete_email_verification(email)
        set_session_cookie(response, secret=config.session_secret, user_id=user.id)
        return {"status": "registered"}

    @app.post("/api/auth/register/resend")
    def register_resend(body: RegisterRequestBody) -> dict[str, str]:
        try:
            email = normalize_registration_email(body.email)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if store.get_user_by_email(email) is not None:
            raise HTTPException(status_code=409, detail="邮箱已注册")
        return _send_register_code(email, body.password)

    @app.post("/api/auth/register")
    def register_legacy() -> dict[str, str]:
        raise HTTPException(
            status_code=410,
            detail="请使用 /auth/register/request 与 /auth/register/verify",
        )

    @app.post("/api/auth/login")
    def login(body: LoginBody, response: Response) -> dict[str, str]:
        try:
            email = normalize_email(body.email)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        user = store.get_user_by_email(email)
        if user is None or not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="邮箱或密码错误")
        if user.status == "banned":
            raise HTTPException(status_code=403, detail="账号已封禁")
        if user.email_verified_at is None:
            raise HTTPException(status_code=403, detail="邮箱未验证")
        now = time.time()
        store.touch_last_login(user.id, when=now)
        set_session_cookie(response, secret=config.session_secret, user_id=user.id)
        return {"status": "ok"}

    @app.post("/api/auth/logout", dependencies=secured)
    def logout(response: Response) -> dict[str, str]:
        clear_session_cookie(response)
        return {"status": "ok"}

    @app.get("/api/auth/me", dependencies=secured)
    def me(user_id: str = Depends(user_dep)) -> dict[str, Any]:
        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="未登录")
        binding = store.get_binding_for_user(user_id)
        return {
            "email": user.email,
            "binding": store.binding_status_dict(binding),
        }

    @app.get("/api/device/bind/session")
    def bind_session(bind_code: str) -> dict[str, Any]:
        try:
            code = normalize_bind_code(bind_code)
        except ValueError:
            return {"status": "missing"}
        session = store.get_bind_session(code)
        if session is None:
            return {"status": "missing"}
        now = time.time()
        if session.consumed_at is not None:
            return {"status": "consumed"}
        if session.expires_at < now:
            return {"status": "expired"}
        return {"status": "pending", "expires_at": session.expires_at}

    @app.post("/api/device/bind/announce")
    def bind_announce(body: BindAnnounceBody) -> dict[str, Any]:
        try:
            mac = normalize_mac(body.mac)
            code = normalize_bind_code(body.bind_code)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        token = body.device_token.strip()
        if not token:
            raise HTTPException(status_code=400, detail="device_token 为空")
        now = time.time()
        enc = encrypt_token(config.token_cipher_key, token)
        session = store.upsert_bind_session(
            bind_code=code,
            device_mac=mac,
            device_token_enc=enc,
            expires_at=now + config.bind_code_ttl_seconds,
        )
        return {
            "status": "announced",
            "expires_at": session.expires_at,
            "mac_suffix": mac[-5:],
        }

    @app.post("/api/user/bind/complete", dependencies=secured)
    def bind_complete(body: BindCompleteBody, user_id: str = Depends(user_dep)) -> dict[str, Any]:
        try:
            code = normalize_bind_code(body.bind_code)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        now = time.time()
        session = store.consume_bind_session(code, now=now)
        if session is None:
            raise HTTPException(status_code=400, detail="bind_code 无效或已过期")
        ok, _summary = probe_device_token(
            api_base=config.xiaozhi_api_base,
            device_token=decrypt_token(config.token_cipher_key, session.device_token_enc),
        )
        token_enc = session.device_token_enc
        existing = store.get_binding_for_user(user_id)
        if (
            not ok
            and existing is not None
            and existing.token_probe_ok
            and existing.device_mac == session.device_mac
        ):
            # 固件 announce 仍可能上报 websocket token（非 messaging JWT）；
            # 探活失败时保留已验证可用的旧 token，避免重绑把推送打坏。
            token_enc = existing.device_token_enc
            ok = True
        binding = store.set_device_binding(
            user_id=user_id,
            device_mac=session.device_mac,
            device_token_enc=token_enc,
            bound_at=now,
            token_probe_ok=ok,
        )
        return {"status": "bound", "binding": store.binding_status_dict(binding)}

    @app.post("/api/user/bind/unbind", dependencies=secured)
    def bind_unbind(user_id: str = Depends(user_dep)) -> dict[str, str]:
        if not store.delete_binding_for_user(user_id):
            raise HTTPException(status_code=404, detail="未绑定设备")
        return {"status": "unbound"}

    @app.get("/api/user/bind/status", dependencies=secured)
    def bind_status(user_id: str = Depends(user_dep)) -> dict[str, Any]:
        binding = store.get_binding_for_user(user_id)
        return store.binding_status_dict(binding)

    def _require_binding(user_id: str):
        binding = store.get_binding_for_user(user_id)
        if binding is None:
            raise HTTPException(status_code=400, detail="请先绑定小派设备")
        return binding

    def _publish_from_job(job_id: str) -> tuple[str, str]:
        published = publish_job_files(
            job_dir=_job_dir(config.data_dir, job_id),
            mcp_output_dir=config.mcp_output_dir,
            public_base_url=config.public_base_url,
        )
        return published.gcode_url, published.preview_url

    @app.post("/api/draw/upload", dependencies=secured)
    async def draw_upload(
        file: UploadFile = File(...),
        user_id: str = Depends(user_dep),
    ) -> dict[str, Any]:
        content_type = (file.content_type or "").split(";")[0].strip().lower()
        filename = file.filename or "upload.bin"
        raw = await file.read()
        if len(raw) > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="文件过大（上限 4MB）")
        if len(raw) < 16:
            raise HTTPException(status_code=400, detail="文件无效")

        source_name = Path(filename).name[:120]
        job = store.create_draw_job(user_id=user_id, source_name=source_name)
        job_path = _job_dir(config.data_dir, job.id)

        try:
            result = await asyncio.to_thread(
                process_upload,
                raw,
                content_type=content_type,
                filename=filename,
                job_dir=job_path,
            )
            published = await asyncio.to_thread(
                publish_job_files,
                job_dir=job_path,
                mcp_output_dir=config.mcp_output_dir,
                public_base_url=config.public_base_url,
            )
            store.mark_draw_job_ready(job.id, basename=published.basename)
        except ValueError:
            store.mark_draw_job_failed(job.id, error_summary="UnsupportedFormat")
            raise HTTPException(status_code=400, detail="不支持的文件格式")
        except Exception as exc:
            store.mark_draw_job_failed(job.id, error_summary=type(exc).__name__)
            raise HTTPException(status_code=422, detail="图片转换失败") from exc

        return {
            "status": "ready",
            "job_id": job.id,
            "preview_url": published.preview_url,
            "gcode_url": published.gcode_url,
            "lines": result.lines,
            "bytes": result.bytes,
        }

    @app.post("/api/draw/{job_id}/send", dependencies=secured)
    def draw_send(job_id: str, user_id: str = Depends(user_dep)) -> dict[str, Any]:
        job = store.get_draw_job(job_id, user_id=user_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if job.status != "ready":
            raise HTTPException(status_code=400, detail="任务未就绪")
        binding = _require_binding(user_id)
        gcode_url, preview_url = _publish_from_job(job_id)
        ok, summary = push_for_user_binding(
            api_base=config.xiaozhi_api_base,
            token_cipher_key=config.token_cipher_key,
            device_token_enc=binding.device_token_enc,
            gcode_url=gcode_url,
            preview_url=preview_url,
        )
        if ok:
            store.update_binding_last_push(user_id, when=time.time())
        return {"status": "sent" if ok else "push_failed", "detail": summary}

    @app.post("/api/draw/{job_id}/save", dependencies=secured)
    def draw_save(
        job_id: str,
        body: SavePreviewBody | None = None,
        user_id: str = Depends(user_dep),
    ) -> dict[str, Any]:
        job = store.get_draw_job(job_id, user_id=user_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if job.status != "ready":
            raise HTTPException(status_code=400, detail="任务未就绪")

        job_path = _job_dir(config.data_dir, job_id)
        if not (job_path / "preview.png").is_file():
            raise HTTPException(status_code=400, detail="预览不存在")

        saved_id = uuid.uuid4().hex
        saved_root = _saved_dir(config.data_dir, user_id)
        preview_rel = str(saved_root / f"{saved_id}.png")
        gcode_rel = str(saved_root / f"{saved_id}.gcode")
        copy_saved_preview(job_dir=job_path, saved_dir=saved_root, saved_id=saved_id)
        gcode_path: str | None = None
        if (job_path / "draw.gcode").is_file():
            shutil.copy2(job_path / "draw.gcode", saved_root / f"{saved_id}.gcode")
            gcode_path = gcode_rel

        title = (body.title if body and body.title else None) or _default_saved_title()
        saved = store.create_saved_preview(
            user_id=user_id,
            job_id=job_id,
            title=title[:120],
            preview_path=preview_rel,
            gcode_path=gcode_path,
        )
        for path in store.trim_saved_previews(
            user_id, limit=config.saved_preview_limit
        ):
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        return {
            "status": "saved",
            "id": saved.id,
            "title": saved.title,
            "preview_url": f"/api/draw/saved/{saved.id}/preview.png",
        }

    @app.get("/api/draw/saved", dependencies=secured)
    def draw_saved_list(user_id: str = Depends(user_dep)) -> dict[str, Any]:
        items = store.list_saved_previews(user_id)
        return {
            "items": [
                {
                    "id": it.id,
                    "title": it.title,
                    "saved_at": it.saved_at,
                    "has_gcode": bool(it.gcode_path),
                    "preview_url": f"/api/draw/saved/{it.id}/preview.png",
                }
                for it in items
            ]
        }

    @app.get("/api/draw/saved/{saved_id}", dependencies=secured)
    def draw_saved_detail(saved_id: str, user_id: str = Depends(user_dep)) -> dict[str, Any]:
        item = store.get_saved_preview(saved_id, user_id=user_id)
        if item is None:
            raise HTTPException(status_code=404, detail="不存在")
        return {
            "id": item.id,
            "title": item.title,
            "saved_at": item.saved_at,
            "has_gcode": bool(item.gcode_path),
            "preview_url": f"/api/draw/saved/{item.id}/preview.png",
        }

    @app.get("/api/draw/saved/{saved_id}/preview.png", dependencies=secured)
    def draw_saved_preview_png(saved_id: str, user_id: str = Depends(user_dep)) -> FileResponse:
        item = store.get_saved_preview(saved_id, user_id=user_id)
        if item is None:
            raise HTTPException(status_code=404, detail="不存在")
        path = Path(item.preview_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="预览已丢失")
        return FileResponse(path, media_type="image/png")

    @app.post("/api/draw/saved/{saved_id}/send", dependencies=secured)
    def draw_saved_send(saved_id: str, user_id: str = Depends(user_dep)) -> dict[str, Any]:
        item = store.get_saved_preview(saved_id, user_id=user_id)
        if item is None:
            raise HTTPException(status_code=404, detail="不存在")
        if not item.gcode_path:
            raise HTTPException(status_code=400, detail="仅保存了预览，需重新上传出图")
        gcode_file = Path(item.gcode_path)
        preview_file = Path(item.preview_path)
        if not gcode_file.is_file() or not preview_file.is_file():
            raise HTTPException(status_code=404, detail="文件已丢失")
        binding = _require_binding(user_id)

        import secrets as _secrets

        token = _secrets.token_urlsafe(16).replace("-", "_")[:22]
        base = f"draw_{time.strftime('%Y%m%d_%H%M%S')}_{token}"
        config.mcp_output_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(gcode_file, config.mcp_output_dir / f"{base}.gcode")
        shutil.copy2(preview_file, config.mcp_output_dir / f"{base}.png")
        prefix = config.public_base_url.rstrip("/")
        gcode_url = f"{prefix}/files/{base}.gcode"
        preview_url = f"{prefix}/files/{base}.png"

        ok, summary = push_for_user_binding(
            api_base=config.xiaozhi_api_base,
            token_cipher_key=config.token_cipher_key,
            device_token_enc=binding.device_token_enc,
            gcode_url=gcode_url,
            preview_url=preview_url,
        )
        if ok:
            store.update_binding_last_push(user_id, when=time.time())
        return {"status": "sent" if ok else "push_failed", "detail": summary}

    @app.delete("/api/draw/saved/{saved_id}", dependencies=secured)
    def draw_saved_delete(saved_id: str, user_id: str = Depends(user_dep)) -> dict[str, str]:
        item = store.delete_saved_preview(saved_id, user_id=user_id)
        if item is None:
            raise HTTPException(status_code=404, detail="不存在")
        for path in (item.preview_path, item.gcode_path):
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass
        return {"status": "deleted"}

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


if __name__ == "__main__":
    import uvicorn

    cfg = load_config()
    uvicorn.run(
        "draw_portal.app:create_app",
        factory=True,
        host=cfg.host,
        port=cfg.port,
    )
