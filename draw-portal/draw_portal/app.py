from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from draw_portal.analytics import (
    conversation_dedupe_key,
    normalize_role,
    retention_cutoff,
    truncate_conversation_content,
    usage_event_type,
)
from draw_portal.auth import (
    SESSION_HEADER,
    SESSION_COOKIE,
    _encode_session as _encode_session_value,
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
from draw_portal.pipeline import copy_saved_preview, process_upload, process_upload_doc
from draw_portal.probe import probe_device_token
from draw_portal.push import (
    _install_capability_pair,
    publish_doc_pages,
    publish_job_files,
    push_for_user_binding,
)
from draw_portal.upload_kinds import detect_upload_kind
from draw_portal.store import Store
from draw_portal.generate import GenerateError, materialize_generated_job, request_internal_draw

class RegisterRequestBody(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)


class RegisterVerifyBody(BaseModel):
    email: str
    code: str = Field(min_length=4, max_length=8)


class WxLoginBody(BaseModel):
    """微信小程序静默登录：wx.login 的 code 换会话（2026-09-07）。"""

    code: str = Field(min_length=1, max_length=256)


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


class ConversationTurnBody(BaseModel):
    role: str
    content: str = Field(min_length=1, max_length=2000)
    client_ts: float | None = None


class ConversationReportBody(BaseModel):
    mac: str
    session_id: str = Field(min_length=1, max_length=64)
    events: list[ConversationTurnBody] = Field(min_length=1, max_length=50)


class UsageIngestBody(BaseModel):
    source: str = Field(default="mcp", max_length=32)
    event_type: str = Field(min_length=1, max_length=64)
    user_id: str | None = None
    device_mac: str | None = None
    detail: str | None = Field(default=None, max_length=200)
    ok: bool | None = None


class GenerateDrawBody(BaseModel):
    description: str = Field(min_length=1, max_length=200)


_MAX_UPLOAD_BYTES = 4 * 1024 * 1024
# 文档导入种类（detect_upload_kind 可能返回）；走 doc_ingest→排版→多页链
_DOC_UPLOAD_KINDS = frozenset({"docx", "xlsx", "pptx", "csv", "txt"})
_MAX_VERIFY_ATTEMPTS = 8


def _job_dir(data_dir: Path, job_id: str) -> Path:
    return data_dir / "jobs" / job_id


def _saved_dir(data_dir: Path, user_id: str) -> Path:
    return data_dir / "saved" / user_id


def _default_saved_title() -> str:
    return datetime.now().strftime("上传 %Y-%m-%d %H:%M")


def _record_portal_usage(
    store: Store,
    *,
    event_type: str,
    user_id: str | None = None,
    device_mac: str | None = None,
    detail: str | None = None,
    ok: bool | None = None,
) -> None:
    store.insert_usage_event(
        source="draw_portal",
        event_type=usage_event_type(event_type),
        user_id=user_id,
        device_mac=device_mac,
        detail=detail,
        ok=ok,
    )


def _is_loopback_client(host: str) -> bool:
    from draw_portal.internal_client import is_loopback_host

    return is_loopback_host(host)


def _is_trusted_mcp_settings_client(host: str, config: object) -> bool:
    from draw_portal.internal_client import is_trusted_internal_client

    allow = getattr(config, "internal_allow_cidrs", ())
    return is_trusted_internal_client(host, allow_cidrs=allow)


class _WxLoginError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _wx_code2session(config: Any, code: str) -> str:
    """微信 code2Session（stdlib，5s 超时）。只回 openid；errcode 全量映射 502/401。"""
    import json as _json
    import urllib.parse as _parse
    import urllib.request as _request

    url = (
        "https://api.weixin.qq.com/sns/jscode2session?"
        + _parse.urlencode(
            {
                "appid": config.wx_appid,
                "secret": config.wx_secret,
                "js_code": code,
                "grant_type": "authorization_code",
            }
        )
    )
    try:
        with _request.urlopen(_request.Request(url), timeout=5) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except Exception:
        raise _WxLoginError(502, "微信登录服务不可用，请稍后再试") from None
    if data.get("errcode"):
        # 40029 code 无效 / 40226 高风险用户 / 45011 频率限制：统一 401 让前端重走 wx.login
        raise _WxLoginError(401, "微信登录态失效，请重试") from None
    openid = data.get("openid")
    if not isinstance(openid, str) or not openid:
        raise _WxLoginError(502, "微信登录响应异常") from None
    return openid


def create_app() -> FastAPI:
    config = load_config()
    store = Store(config.db_path, provider_cipher_key=config.provider_cipher_key)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def user_dep(
        draw_portal_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
        x_session: str | None = Header(default=None),
    ) -> str:
        # 网页端走 cookie；小程序真机 cookie 罐不可靠（iOS/Android 常丢 Set-Cookie、
        # 重启不持久），会话值经 x-session 头发送（auth.SESSION_HEADER，2026-09-07）。
        user_id = require_user_id(
            config.session_secret, draw_portal_session or x_session
        )
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

    @app.post("/api/auth/wx-login")
    def wx_login(body: WxLoginBody, response: Response) -> dict[str, Any]:
        """微信小程序登录：code → code2Session → openid → upsert 用户 → 发同款会话。

        复用网页端全部会话/配额/绑定逻辑（同一 cookie）；两项 env 未配即 503。
        """
        if not config.wx_appid or not config.wx_secret:
            raise HTTPException(status_code=503, detail="小程序登录未配置")
        try:
            openid = _wx_code2session(config, body.code.strip())
        except _WxLoginError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from None
        user = store.get_user_by_openid(openid)
        created = user is None
        if created:
            user = store.create_wx_user(openid)
        if user.status == "banned":
            raise HTTPException(status_code=403, detail="账号已封禁")
        store.touch_last_login(user.id, when=time.time())
        session_value = _encode_session_value(config.session_secret, user.id)
        set_session_cookie(response, secret=config.session_secret, user_id=user.id)
        return {
            "status": "ok",
            "user": {"wx": True, "created": created},
            "session": session_value,  # 小程序无 cookie 罐，存 storage 经 x-session 头回带
        }

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
        _record_portal_usage(
            store,
            event_type="bind_complete",
            user_id=user_id,
            device_mac=binding.device_mac,
            ok=ok,
        )
        return {"status": "bound", "binding": store.binding_status_dict(binding)}

    @app.post("/api/user/bind/unbind", dependencies=secured)
    def bind_unbind(user_id: str = Depends(user_dep)) -> dict[str, str]:
        binding = store.get_binding_for_user(user_id)
        if not store.delete_binding_for_user(user_id):
            raise HTTPException(status_code=404, detail="未绑定设备")
        if binding is not None:
            _record_portal_usage(
                store,
                event_type="unbind",
                user_id=user_id,
                device_mac=binding.device_mac,
            )
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
            kind = detect_upload_kind(raw, content_type, filename)
        except ValueError as exc:
            # DocIngestError（ValueError 子类）的消息是用户可读的具体原因；
            # 用类名判别避免此处 import hutuji_mcp——位图/SVG 路径不该被
            # hutuji_mcp 缺失（本地 dev 无 PYTHONPATH）波及。
            store.mark_draw_job_failed(job.id, error_summary=type(exc).__name__)
            detail = str(exc)[:120] if type(exc).__name__ == "DocIngestError" else "不支持的文件格式"
            raise HTTPException(status_code=400, detail=detail) from None

        if kind in _DOC_UPLOAD_KINDS:
            from hutuji_mcp.doc_ingest import DocIngestError
            from hutuji_mcp.write_text import WriteTextError
            if not config.gfont_dir:
                store.mark_draw_job_failed(job.id, error_summary="DocImportNotConfigured")
                raise HTTPException(status_code=503, detail="文档导入未配置（服务端缺字库目录）")
            try:
                result = await asyncio.to_thread(
                    process_upload_doc, raw, kind, job_path, gfont_dir=config.gfont_dir
                )
                published_pages = await asyncio.to_thread(
                    publish_doc_pages,
                    job_dir=job_path,
                    mcp_output_dir=config.mcp_output_dir,
                    public_base_url=config.public_base_url,
                )
                # basename 单值化决策：job 主键 = 第 1 页；整篇页表在 pages.json
                store.mark_draw_job_ready(job.id, basename=published_pages[0].basename)
            except (DocIngestError, WriteTextError) as exc:
                store.mark_draw_job_failed(job.id, error_summary=type(exc).__name__)
                raise HTTPException(status_code=400, detail=str(exc)[:120]) from None
            except Exception as exc:
                store.mark_draw_job_failed(job.id, error_summary=type(exc).__name__)
                raise HTTPException(status_code=422, detail="文档转换失败") from exc

            _record_portal_usage(
                store,
                event_type="web_upload",
                user_id=user_id,
                detail=f"doc:{kind}:{source_name[:100]}",
                ok=True,
            )
            first = published_pages[0]
            return {
                "status": "ready",
                "job_id": job.id,
                "preview_url": first.preview_url,
                "gcode_url": first.gcode_url,
                "lines": result.lines,
                "bytes": result.bytes,
                "pages": [
                    {"url": p.gcode_url, "preview_url": p.preview_url}
                    for p in published_pages
                ],
                "page_count": result.page_count,
                "doc_kind": result.kind,
                "doc_mode": result.mode,
                "truncated": result.truncated,
                "missing_chars": result.missing_chars,
            }

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
            raise HTTPException(status_code=400, detail="不支持的文件格式") from None
        except Exception as exc:
            store.mark_draw_job_failed(job.id, error_summary=type(exc).__name__)
            raise HTTPException(status_code=422, detail="图片转换失败") from exc

        _record_portal_usage(
            store,
            event_type="web_upload",
            user_id=user_id,
            detail=source_name[:120],
            ok=True,
        )
        return {
            "status": "ready",
            "job_id": job.id,
            "preview_url": published.preview_url,
            "gcode_url": published.gcode_url,
            "lines": result.lines,
            "bytes": result.bytes,
        }

    @app.post("/api/draw/generate", dependencies=secured)
    async def draw_generate(
        body: GenerateDrawBody,
        user_id: str = Depends(user_dep),
    ) -> dict[str, Any]:
        """网页文字生图（2026-08-31）：转发本机 MCP /internal/draw。

        配额按「成功次数」计 24h 窗口（失败不占额——付费失败不该罚买家）；
        先查后记存在并发超限一单的窄窗，按可接受残余风险处理（日上限是
        防滥用闸，不是精确计费）。
        """
        if not config.usage_ingest_secret:
            raise HTTPException(status_code=503, detail="生图通道未配置")
        description = body.description.strip()
        if not description:
            raise HTTPException(status_code=400, detail="描述不能为空")

        since = time.time() - 86400
        used = sum(
            row["count"]
            for row in store.usage_counts_for_user_since(user_id, since)
            if row["event_type"] == "web_generate"
        )
        if used >= config.generate_daily_limit:
            raise HTTPException(
                status_code=429,
                detail=f"今日生图次数已达上限（{config.generate_daily_limit} 次），明天再来",
            )

        job = store.create_draw_job(
            user_id=user_id, source_name=("AI: " + description)[:120]
        )
        job_path = _job_dir(config.data_dir, job.id)
        try:
            result = await asyncio.to_thread(
                request_internal_draw,
                base_url=config.mcp_internal_base_url,
                secret=config.usage_ingest_secret,
                description=description,
                timeout_seconds=config.generate_timeout_seconds,
            )
            generated = await asyncio.to_thread(
                materialize_generated_job,
                base_url=config.mcp_internal_base_url,
                result=result,
                job_dir=job_path,
            )
            published = await asyncio.to_thread(
                publish_job_files,
                job_dir=job_path,
                mcp_output_dir=config.mcp_output_dir,
                public_base_url=config.public_base_url,
            )
            store.mark_draw_job_ready(job.id, basename=published.basename)
        except GenerateError as exc:
            store.mark_draw_job_failed(job.id, error_summary="GenerateError")
            # 只透传 400/429 的输入白话；504/502 一律固定文案，防上游细节外泄（2026-08-31 评审裁决）
            if exc.status in (400, 429):
                detail = str(exc)
            elif exc.status == 504:
                detail = "生图超时，请稍后再试"
            else:
                detail = "生图服务暂不可用，请稍后再试"
            raise HTTPException(status_code=exc.status, detail=detail) from exc
        except Exception as exc:
            store.mark_draw_job_failed(job.id, error_summary=type(exc).__name__)
            raise HTTPException(status_code=502, detail="生图服务异常") from exc

        _record_portal_usage(
            store,
            event_type="web_generate",
            user_id=user_id,
            detail=description[:120],
            ok=True,
        )
        return {
            "status": "ready",
            "job_id": job.id,
            "preview_url": published.preview_url,
            "gcode_url": published.gcode_url,
            "lines": generated.lines,
            "bytes": generated.bytes,
            "pen_lifts": generated.pen_lifts,
            "description": description,
        }

    @app.post("/api/draw/{job_id}/send", dependencies=secured)
    def draw_send(job_id: str, user_id: str = Depends(user_dep)) -> dict[str, Any]:
        job = store.get_draw_job(job_id, user_id=user_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if job.status != "ready":
            raise HTTPException(status_code=400, detail="任务未就绪")
        binding = _require_binding(user_id)
        job_path = _job_dir(config.data_dir, job_id)
        pages_arg: str | None = None
        if (job_path / "pages.json").is_file():
            # 多页文档整篇重发：每页重新发布刷 TTL（含错峰），pages 序列化
            # 单源 hutuji_mcp.article_pages.pages_argument（设备纯扫描器，
            # 幂等重入要求字节级一致，protocol §4.2）。
            from hutuji_mcp.article_pages import pages_argument

            published_pages = publish_doc_pages(
                job_dir=job_path,
                mcp_output_dir=config.mcp_output_dir,
                public_base_url=config.public_base_url,
            )
            urls = [
                {"url": p.gcode_url, "preview_url": p.preview_url}
                for p in published_pages
            ]
            gcode_url, preview_url = urls[0]["url"], urls[0]["preview_url"]
            if len(urls) > 1:
                pages_arg = pages_argument(urls)
        else:
            gcode_url, preview_url = _publish_from_job(job_id)
        ok, summary = push_for_user_binding(
            api_base=config.xiaozhi_api_base,
            token_cipher_key=config.token_cipher_key,
            device_token_enc=binding.device_token_enc,
            gcode_url=gcode_url,
            preview_url=preview_url,
            pages_arg=pages_arg,
        )
        if ok:
            store.update_binding_last_push(user_id, when=time.time())
        _record_portal_usage(
            store,
            event_type="web_send",
            user_id=user_id,
            device_mac=binding.device_mac,
            ok=ok,
        )
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
        _record_portal_usage(
            store,
            event_type="web_save",
            user_id=user_id,
            detail=saved.title[:120],
            ok=True,
        )
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

        published = _install_capability_pair(
            gcode_src=gcode_file,
            png_src=preview_file,
            mcp_output_dir=config.mcp_output_dir,
            public_base_url=config.public_base_url,
        )

        ok, summary = push_for_user_binding(
            api_base=config.xiaozhi_api_base,
            token_cipher_key=config.token_cipher_key,
            device_token_enc=binding.device_token_enc,
            gcode_url=published.gcode_url,
            preview_url=published.preview_url,
        )
        if ok:
            store.update_binding_last_push(user_id, when=time.time())
        _record_portal_usage(
            store,
            event_type="web_saved_send",
            user_id=user_id,
            device_mac=binding.device_mac,
            ok=ok,
        )
        return {"status": "sent" if ok else "push_failed", "detail": summary}

    @app.post("/api/device/conversation/report")
    def conversation_report(body: ConversationReportBody) -> dict[str, Any]:
        try:
            mac = normalize_mac(body.mac)
            session_id = body.session_id.strip()[:64]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        user_id = store.get_user_id_for_mac(mac)
        if user_id is None:
            raise HTTPException(status_code=403, detail="设备未绑定呼图账号")
        now = time.time()
        store.purge_conversation_before(
            retention_cutoff(retention_days=config.conversation_retention_days)
        )
        accepted = 0
        for turn in body.events:
            try:
                role = normalize_role(turn.role)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            content = truncate_conversation_content(
                turn.content, max_len=config.conversation_max_content
            )
            if not content:
                continue
            client_ts = turn.client_ts if turn.client_ts is not None else now
            key = conversation_dedupe_key(
                device_mac=mac,
                session_id=session_id,
                role=role,
                content=content,
                client_ts=client_ts,
            )
            if store.insert_conversation_event(
                dedupe_key=key,
                device_mac=mac,
                user_id=user_id,
                session_id=session_id,
                role=role,
                content=content,
                client_ts=client_ts,
                created_at=now,
            ):
                accepted += 1
        return {"status": "ok", "accepted": accepted, "total": len(body.events)}

    @app.post("/api/internal/usage")
    def usage_ingest(
        body: UsageIngestBody,
        request: Request,
        x_hutuji_usage_secret: str | None = Header(default=None, alias="X-Hutuji-Usage-Secret"),
    ) -> dict[str, str]:
        if not config.usage_ingest_secret:
            raise HTTPException(status_code=503, detail="usage ingest 未配置")
        if x_hutuji_usage_secret != config.usage_ingest_secret:
            raise HTTPException(status_code=401, detail="unauthorized")
        client_host = request.client.host if request.client else ""
        if not _is_loopback_client(client_host):
            raise HTTPException(status_code=403, detail="仅允许 loopback 写入")
        try:
            event_type = usage_event_type(body.event_type)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        mac = None
        if body.device_mac:
            try:
                mac = normalize_mac(body.device_mac)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.insert_usage_event(
            source=(body.source or "mcp")[:32],
            event_type=event_type,
            user_id=body.user_id,
            device_mac=mac,
            detail=body.detail,
            ok=body.ok,
        )
        return {"status": "ok"}

    @app.get("/api/internal/mcp-settings")
    def mcp_settings_internal(
        request: Request,
        x_hutuji_usage_secret: str | None = Header(default=None, alias="X-Hutuji-Usage-Secret"),
    ) -> dict[str, Any]:
        if not config.usage_ingest_secret:
            raise HTTPException(status_code=503, detail="mcp settings 未配置")
        if x_hutuji_usage_secret != config.usage_ingest_secret:
            raise HTTPException(status_code=401, detail="unauthorized")
        client_host = request.client.host if request.client else ""
        if not _is_trusted_mcp_settings_client(client_host, config):
            raise HTTPException(status_code=403, detail="仅允许可信内网或 loopback 读取")
        return store.get_mcp_settings_payload()

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
