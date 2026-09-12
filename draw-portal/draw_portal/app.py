from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
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
from draw_portal.device_identity import DeviceIdentityError, parse_identity, verify_announce
from draw_portal import chat as chat_mod
from draw_portal import xiaopai as xiaopai_mod
from draw_portal import tg_notify
from draw_portal import telegram_archive
from draw_portal.crypto import decrypt_token, encrypt_token
from draw_portal.email_verify import (
    generate_email_code,
    hash_email_code,
    send_verification_email,
    verify_email_code,
)
from draw_portal.models import (
    BindSession,
    DeviceBinding,
    normalize_bind_code,
    normalize_email,
    normalize_mac,
    normalize_registration_email,
)
from draw_portal.passwords import hash_password, verify_password
from draw_portal.pipeline import (
    copy_saved_preview,
    is_photo_raster,
    process_upload,
    process_upload_doc,
    process_teach_text,
    process_write_text,
)
from draw_portal.teach_chars import extract_teach_chars
from draw_portal.probe import probe_device_token
from draw_portal.push import (
    _install_capability_pair,
    publish_doc_pages,
    publish_job_files,
    probe_plotter_sku,
    push_draw_self_heal,
    push_for_user_binding,
    push_tool_call,
)
from draw_portal.upload_kinds import detect_upload_kind
from draw_portal.store import BindStateError, Store
from draw_portal.generate import GenerateError, materialize_generated_job, request_internal_draw
from draw_portal import firmware_ota as firmware_ota_mod

logger = logging.getLogger(__name__)

# asyncio 事件循环对 task 只持弱引用：后台任务若无强引用驻留，可能被 GC
# 静默回收（任务停在 await 上永不完成、无异常可追溯）。模块级集合 + 完成
# 回调自清理是标准做法。
_background_tasks: set[asyncio.Task] = set()

# i2i 精修并发上限：魔搭免费层单任务轮询约 3 分钟，课堂 NAT 突发会在同一事件
# 循环挂出一串轮询协程（每个还握着原始照片字节）。名额之外的精修排队等位，
# 不把突发流量原样堆到上游 API。模块级单例与 _background_tasks 同口径。
_refine_slots = asyncio.Semaphore(4)

# 同一设备的 claim/announce/status 会同时完成；串行铸 token，避免后一次使前一次失效。
# 固定分片避免匿名 MAC 使锁表无限增长；当前 portal 为单进程服务。
_bind_locks = tuple(threading.Lock() for _ in range(64))



def _spawn_background(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


class RegisterRequestBody(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)


class RegisterVerifyBody(BaseModel):
    email: str
    code: str = Field(min_length=4, max_length=8)


class WxLoginBody(BaseModel):
    """微信小程序静默登录：wx.login 的 code 换会话（2026-09-07）。"""

    code: str = Field(min_length=1, max_length=256)


class ChatHistoryItem(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=500)


class ChatBody(BaseModel):
    """小程序文字聊天：单条消息 + 客户端携带的最近若干轮上下文。"""

    message: str = Field(min_length=1, max_length=200)
    history: list[ChatHistoryItem] = Field(default_factory=list, max_length=10)


class LoginBody(BaseModel):
    email: str
    password: str

class XiaopaiSettingBody(BaseModel):
    """小派音色/模型切换：两字段均可选，至少一项（2026-09-12 §10.4.19）。"""

    tts_voice: str | None = Field(default=None, max_length=64)
    llm_model: str | None = Field(default=None, max_length=64)


class XiaopaiBindBody(BaseModel):
    """设备验证码绑定：设备屏幕「设置→验证码」6 位数字。"""

    code: str = Field(min_length=6, max_length=6)


class BindAnnounceBody(BaseModel):
    mac: str
    bind_code: str
    device_token: str = Field(default="", max_length=4096)
    # 机型词汇（2026-09-11，§10.4.15）：S3 1.0.9 起随 announce 上报（$I VER
    # build 段识别）；1.0.8 旧固件缺席 → "paper"。
    plotter_sku: str = Field(default="paper", pattern="^(nopaper|paper)$")
    device_id: str | None = Field(default=None, max_length=128)
    identity_version: int | None = Field(default=None, ge=1, le=10)
    public_key: str | None = Field(default=None, max_length=4096)
    nonce: str | None = Field(default=None, max_length=256)
    signature: str | None = Field(default=None, max_length=4096)
    challenge: str | None = Field(default=None, max_length=64)


class BindChallengeBody(BaseModel):
    mac: str
    bind_code: str
    device_id: str = Field(max_length=32)
    identity_version: int
    public_key: str = Field(max_length=87)
    nonce: str = Field(max_length=22)


class BindCompleteBody(BaseModel):
    bind_code: str
    force: bool = False  # 设备已绑他人时强制换绑（授权证据=屏显 bind_code 在场持有）
    device_id: str | None = Field(default=None, max_length=128)
    public_key: str | None = Field(default=None, max_length=128)
    nonce: str | None = Field(default=None, max_length=256)
    identity_version: int | None = None


class BindClaimBody(BaseModel):
    mac: str
    force: bool = False  # 与 bind/complete 同口径：跨账号换绑须显式确认（在场证据=扫到屏显配网 QR）
    device_id: str | None = Field(default=None, max_length=128)
    nonce: str | None = Field(default=None, max_length=256)
    public_key: str | None = Field(default=None, max_length=128)
    identity_version: int | None = None


class BindUnbindBody(BaseModel):
    device_mac: str | None = None


class DeviceSelectBody(BaseModel):
    device_mac: str


class WxProfileBody(BaseModel):
    nickname: str = Field(min_length=1, max_length=32)


class WxPhoneBody(BaseModel):
    code: str = Field(min_length=1, max_length=128)


class BindPhoneBody(BaseModel):
    """网页/邮箱账号补绑手机号（小程序微信用户须走 getPhoneNumber → wx-phone）。"""

    phone: str = Field(min_length=11, max_length=20)


class DeviceControlBody(BaseModel):
    tool: str = Field(min_length=1, max_length=32)
    action: str | None = None


# 设备控制白名单：与固件 IsVoiceAllowedAction 同源（语音不开放的维护动作这里同样不放）
_MANUAL_ACTION_WHITELIST = frozenset({
    "jog_x+", "jog_x-", "jog_y+", "jog_y-",
    "jog_step_1", "jog_step_10",
    "pen_up", "pen_down", "home",
})
_CONTROL_TOOL_WHITELIST = frozenset({
    "hutuji.pen_test", "hutuji.status", "hutuji.confirm", "hutuji.abort",
    "hutuji.pause", "hutuji.resume", "hutuji.repeat",
})
_WX_ACCESS_TOKEN_CACHE: dict[str, Any] = {"token": None, "expires_at": 0.0}


def _identity_from_body(body):
    return parse_identity(device_id=body.device_id, public_key=body.public_key,
                          nonce=body.nonce, identity_version=body.identity_version)


def _bind_error(exc: BindStateError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)})


def _wx_access_token(config: Any) -> str:
    """小程序 access_token 进程内缓存（官方有效期 7200s，提前 300s 续）。"""
    import json as _json
    import urllib.parse as _parse
    import urllib.request as _req

    now = time.time()
    if _WX_ACCESS_TOKEN_CACHE["token"] and _WX_ACCESS_TOKEN_CACHE["expires_at"] > now + 300:
        return _WX_ACCESS_TOKEN_CACHE["token"]
    qs = _parse.urlencode(
        {"grant_type": "client_credential", "appid": config.wx_appid, "secret": config.wx_secret}
    )
    try:
        with _req.urlopen("https://api.weixin.qq.com/cgi-bin/token?" + qs, timeout=5) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except Exception:
        raise _WxLoginError(502, "微信服务不可用，请稍后再试") from None
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise _WxLoginError(502, "微信 access_token 获取失败")
    _WX_ACCESS_TOKEN_CACHE["token"] = token
    _WX_ACCESS_TOKEN_CACHE["expires_at"] = now + int(data.get("expires_in", 7200))
    return token


def _wx_phone_number(config: Any, code: str) -> str:
    """getPhoneNumber 的 code → 手机号（纯数字，不带区号前缀）。"""
    import json as _json
    import urllib.request as _req

    token = _wx_access_token(config)
    body = _json.dumps({"code": code}).encode("utf-8")
    req = _req.Request(
        f"https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token={token}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _req.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except Exception:
        raise _WxLoginError(502, "微信手机号服务不可用，请稍后再试") from None
    if data.get("errcode"):
        raise _WxLoginError(401, "手机号授权已过期，请重新点击授权")
    info = data.get("phone_info") or {}
    phone = info.get("purePhoneNumber") or info.get("phoneNumber")
    if not isinstance(phone, str) or not phone:
        raise _WxLoginError(502, "微信手机号响应异常")
    return phone


class SavePreviewBody(BaseModel):
    title: str | None = None


class ConversationTurnBody(BaseModel):
    role: str
    content: str = Field(min_length=1, max_length=2000)
    client_ts: float | None = None


class KdrawDoneBody(BaseModel):
    mac: str
    title: str | None = Field(default=None, max_length=60)


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


class WriteBody(BaseModel):
    text: str
    size_mm: float | None = None
    line_spacing: float | None = None
    font: str | None = None  # 字库文件名词（/api/fonts 列表值），空=默认手写体


class WriteAiBody(BaseModel):
    mode: str  # generate | polish
    prompt: str = ""
    text: str = ""


class VoiceTurnBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=2000)


class TeachBody(BaseModel):
    text: str


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


def _notify_subscribe(
    config: Any,
    store: Store,
    *,
    user_id: str,
    status: str,
    title: str,
    phrase: str | None = None,
) -> bool:
    """生成完成/失败 → 一次性订阅消息（fire-and-forget，绝不影响主链）。

    @return True = 已派发后台发送线程（不代表微信侧送达）；False = 未发。
    """
    from draw_portal import subscribe_notify

    if not subscribe_notify.enabled(config):
        return False
    try:
        user = store.get_user(user_id)
    except Exception:
        return False
    openid = getattr(user, "wx_openid", None) if user else None
    if not openid:
        return False
    if status == "success":
        # 粘合性通知（里程碑/连续打卡）：成功事件就是打卡与累计时刻（§10.4.18）
        _engagement_notify(config, store, user_id=user_id, openid=openid)
    subscribe_notify.notify_job_result_async(
        config,
        user_id=user_id,
        openid=openid,
        status=status,
        title=title,
        phrase=phrase,
    )
    return True


def _engagement_notify(
    config: Any,
    store: Store,
    *,
    user_id: str,
    openid: str,
) -> None:
    """里程碑/连续打卡通知（§10.4.18）：只在作品成功时刻补发，绝不影响主链。

    直接走 subscribe_notify 的 async 入口（phrase=里程碑），与常规成功通知
    去重键不同互不吞并；已发登记以 store 主键抢占，天然幂等。
    """
    from datetime import date

    from draw_portal import milestones, subscribe_notify

    if not subscribe_notify.enabled(config):
        return
    try:
        count = store.count_ready_jobs(user_id)
        m = milestones.milestone_for_count(count)
        if m is not None and store.record_engagement_event(
            user_id, "milestone", f"draw:{m}", when=time.time()
        ):
            subscribe_notify.notify_job_result_async(
                config,
                user_id=user_id,
                openid=openid,
                status="success",
                title=milestones.draw_milestone_text(m),
                phrase=milestones.ENGAGEMENT_PHRASE,
            )
        days = store.list_ready_job_days(user_id)
        streak = milestones.streak_for_dates(
            {date.fromisoformat(d) for d in days}, date.today()
        )
        n = milestones.streak_milestone_for(streak)
        if n is not None and store.record_engagement_event(
            user_id, "streak", f"streak:{n}", when=time.time()
        ):
            subscribe_notify.notify_job_result_async(
                config,
                user_id=user_id,
                openid=openid,
                status="success",
                title=milestones.streak_milestone_text(n),
                phrase=milestones.ENGAGEMENT_PHRASE,
            )
    except Exception as exc:
        logger.warning("engagement notify error: %s", type(exc).__name__)


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

    # 启动恢复（评审②）：上次进程重启丢掉内存队列的 pending 行重新入队。
    telegram_archive.enqueue_pending_on_boot(store)

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

    def require_phone(user_id: str = Depends(user_dep)) -> str:
        """产品主路径硬前置：未绑定手机号禁止绑机 / 传图 / 生图 / 推送 / 控制。
        只读 me、拿号、资料、登出仍走 secured。错误码稳定为 phone_required。"""
        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="未登录")
        if not (user.phone and str(user.phone).strip()):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "phone_required",
                    "message": "请先绑定手机号后再使用",
                },
            )
        return user_id

    secured = [Depends(user_dep)]
    phone_secured = [Depends(require_phone)]

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

    @app.get("/api/auth/me", dependencies=secured)
    def me(user_id: str = Depends(user_dep)) -> dict[str, Any]:
        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="未登录")
        binding = store.get_binding_for_user(user_id)
        phone = user.phone
        phone_bound = bool(phone and str(phone).strip())
        return {
            "email": user.email,
            "wx": user.wx_openid is not None,
            "nickname": user.nickname,
            "phone": phone,
            "phone_bound": phone_bound,
            "avatar_url": f"/api/auth/avatar/{user.id}" if user.avatar_path else None,
            "binding": store.binding_status_dict(binding),
            "devices": [
                {**store.binding_status_dict(item), "device_mac": item.device_mac}
                for item in store.list_bindings_for_user(user_id)
            ],
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

    @app.post("/api/device/bind/challenge")
    def bind_challenge(body: BindChallengeBody, request: Request) -> dict[str, Any]:
        try:
            mac = normalize_mac(body.mac)
            code = normalize_bind_code(body.bind_code)
            identity = _identity_from_body(body)
            row = store.create_identity_challenge(
                identity, mac=mac, bind_code=code,
                request_ip=request.client.host if request.client else "unknown",
                now=time.time(), ttl=config.device_identity_nonce_ttl_seconds,
            )
        except BindStateError as exc:
            raise _bind_error(exc) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"challenge": row["challenge"], "expires_at": row["expires_at"]}

    @app.post("/api/device/bind/announce")
    def bind_announce(body: BindAnnounceBody) -> dict[str, Any]:
        try:
            mac = normalize_mac(body.mac)
            code = normalize_bind_code(body.bind_code)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        token = body.device_token.strip()
        now = time.time()
        identity = None
        expires_at = now + config.bind_code_ttl_seconds
        supplied = any(value is not None for value in (
            body.device_id, body.public_key, body.nonce, body.signature, body.challenge, body.identity_version
        ))
        known = store.get_device_identity(mac)
        if config.device_identity_required or supplied or known is not None:
            try:
                identity = _identity_from_body(body)
                verify_announce(identity, challenge=body.challenge, signature=body.signature,
                                bind_code=code, mac=mac, plotter_sku=body.plotter_sku, device_token=token)
            except DeviceIdentityError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from None
            challenge = store.get_identity_challenge(identity)
            if (challenge is None or challenge["device_mac"] != mac or
                challenge["public_key"] != identity.public_key or challenge["bind_code"] != code or
                not secrets.compare_digest(challenge["challenge"], body.challenge)):
                raise HTTPException(status_code=401, detail="设备绑定挑战不匹配，请重新扫码")
            expires_at = min(expires_at, challenge["expires_at"])
            if challenge["consumed_at"] is not None:
                if store.get_completed_identity_binding(identity, mac=mac) is None:
                    raise _bind_error(BindStateError("该绑定码已使用，请重新扫描设备二维码", status=410, code="binding_expired"))
                return {"status": "announced", "bound": True, "expires_at": expires_at, "mac_suffix": mac[-5:]}
            if challenge["expires_at"] <= now:
                raise _bind_error(BindStateError("绑定窗口已过期，请在设备上重新打开配网", status=410, code="binding_expired"))
        elif len(token) < 8:
            raise HTTPException(status_code=400, detail="旧版设备缺少连接凭据")
        enc = encrypt_token(config.token_cipher_key, token)
        try:
            session = store.upsert_bind_session(
                bind_code=code, device_mac=mac, device_token_enc=enc,
                expires_at=expires_at, plotter_sku=body.plotter_sku, identity=identity,
            )
            # 已有所有者也要处理新扫码的换绑认领；不能直接回 bound 截断新会话。
            claim = store.get_bind_claim(mac, now=now)
            matched = _try_auto_bind(mac, claim.user_id, claim.force, now) if claim else None
        except BindStateError as exc:
            raise _bind_error(exc) from None
        bound = matched is not None or (identity is None and store.get_user_id_for_mac(mac) is not None)
        return {
            "status": "announced",
            "expires_at": session.expires_at,
            "mac_suffix": mac[-5:],
            # 固件据此停轮询/打日志；老固件忽略多余字段，向后兼容。
            "bound": bound,
        }

    @app.post("/api/auth/wx-profile", dependencies=secured)
    def wx_profile(body: WxProfileBody, user_id: str = Depends(user_dep)) -> dict[str, str]:
        nickname = body.nickname.strip()[:32]
        if not nickname:
            raise HTTPException(status_code=400, detail="昵称不能为空")
        user = store.get_user(user_id)
        store.set_user_profile(user_id, nickname=nickname, avatar_path=user.avatar_path if user else None)
        return {"status": "ok"}

    @app.post("/api/auth/wx-avatar", dependencies=secured)
    async def wx_avatar(file: UploadFile = File(...), user_id: str = Depends(user_dep)) -> dict[str, str]:
        raw = await file.read()
        if len(raw) > 2 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="头像不能超过 2MB")
        kind = detect_upload_kind(raw, file.content_type or "", file.filename or "")
        if kind != "raster":
            raise HTTPException(status_code=400, detail="头像只支持图片")
        ext = "png" if raw[:4] == b"\x89PNG" else "jpg"
        avatar_dir = config.data_dir / "avatars"
        avatar_dir.mkdir(parents=True, exist_ok=True)
        rel = f"{user_id}.{ext}"
        (avatar_dir / rel).write_bytes(raw)
        user = store.get_user(user_id)
        store.set_user_profile(user_id, nickname=user.nickname if user else None, avatar_path=rel)
        return {"status": "ok"}

    @app.get("/api/auth/avatar/{uid}")
    def auth_avatar(uid: str) -> FileResponse:
        # 头像半公开（uid 不可枚举）：只服务白名单后缀，不落 404 之外的内部细节
        for ext in ("png", "jpg"):
            path = config.data_dir / "avatars" / f"{uid}.{ext}"
            if path.is_file() and path.parent.resolve() == (config.data_dir / "avatars").resolve():
                return FileResponse(path, media_type=f"image/{'jpeg' if ext == 'jpg' else 'png'}")
        raise HTTPException(status_code=404, detail="不存在")

    @app.post("/api/auth/wx-phone", dependencies=secured)
    def wx_phone(body: WxPhoneBody, user_id: str = Depends(user_dep)) -> dict[str, str]:
        if not config.wx_appid or not config.wx_secret:
            raise HTTPException(status_code=503, detail="小程序登录未配置")
        try:
            phone = _wx_phone_number(config, body.code.strip())
        except _WxLoginError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from None
        store.set_user_phone(user_id, phone)
        return {"status": "ok", "phone": phone}

    @app.post("/api/auth/bind-phone", dependencies=secured)
    def bind_phone(body: BindPhoneBody, user_id: str = Depends(user_dep)) -> dict[str, str]:
        """邮箱账号补绑中国大陆 11 位手机号；微信用户须走 wx-phone。"""
        import re as _re

        user = store.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="未登录")
        if user.wx_openid:
            raise HTTPException(status_code=400, detail="微信用户请用手机号授权")
        phone = body.phone.strip().replace(" ", "").replace("-", "")
        if phone.startswith("+86"):
            phone = phone[3:]
        if not _re.fullmatch(r"1\d{10}", phone):
            raise HTTPException(status_code=400, detail="手机号格式不正确")
        store.set_user_phone(user_id, phone)
        return {"status": "ok", "phone": phone}

    @app.post("/api/device/control", dependencies=phone_secured)
    def device_control(body: DeviceControlBody, user_id: str = Depends(require_phone)) -> dict[str, Any]:
        binding = store.get_binding_for_user(user_id)
        if binding is None:
            raise HTTPException(status_code=400, detail="请先绑定设备")
        tool = body.tool.strip()
        action = (body.action or "").strip()
        if tool == "hutuji.manual":
            if action not in _MANUAL_ACTION_WHITELIST:
                raise HTTPException(status_code=400, detail="不支持的手动动作")
            arguments: dict[str, str] = {"action": action}
        elif tool in _CONTROL_TOOL_WHITELIST:
            if action:
                raise HTTPException(status_code=400, detail="该工具不接受 action")
            arguments = {}
        else:
            raise HTTPException(status_code=400, detail="不支持的控制工具")
        ok, result = push_tool_call(
            api_base=config.xiaozhi_api_base,
            device_token=decrypt_token(config.token_cipher_key, binding.device_token_enc),
            name=tool,
            arguments=arguments,
            timeout=15,
        )
        if not ok and result in ("HTTP 401", "HTTP 403", "HTTP 404"):
            # 与 draw_send 同一自愈：铸新 messaging token → 回写绑定表 → 重试。
            # 404「Device not online」也要铸：整片刷机重生 client UUID 后旧
            # messaging token 指向失效 clientId（2026-09-08 实机定性），
            # 重铸取注册表当前 clientId；真离线时重试自然仍败，不误报。
            from draw_portal.push import mint_messaging_token

            fresh = mint_messaging_token(api_base=config.xiaozhi_api_base, mac=binding.device_mac)
            if fresh:
                from draw_portal.crypto import encrypt_token

                store.update_binding_token(
                    user_id, device_token_enc=encrypt_token(config.token_cipher_key, fresh), device_mac=binding.device_mac
                )
                ok, result = push_tool_call(
                    api_base=config.xiaozhi_api_base,
                    device_token=fresh,
                    name=tool,
                    arguments=arguments,
                    timeout=15,
                )
        if not ok:
            return {"status": "error", "detail": result}
        # 设备侧 McpTool::Call 包一层 content/isError；解出 text 再回前端，
        # 避免小程序把 content 数组当 status → connected 读不到假「未连接」。
        from draw_portal.mcp_result import unwrap_tool_text

        return {"status": "ok", "result": unwrap_tool_text(result)}

    def _infer_voice_format(filename: str) -> str:
        lower = filename.lower()
        if lower.endswith(".wav"):
            return "wav"
        if lower.endswith(".m4a"):
            return "m4a"
        return "mp3"

    @app.post("/api/voice/asr", dependencies=phone_secured)
    async def voice_asr(
        file: UploadFile = File(...),
        user_id: str = Depends(require_phone),
    ) -> dict[str, str]:
        from draw_portal.tencent_asr import AsrError, recognize_sentence

        raw = await file.read()
        voice_format = _infer_voice_format(file.filename or "")
        try:
            text = recognize_sentence(audio=raw, voice_format=voice_format)
        except AsrError as exc:
            msg = str(exc)
            if "未配置" in msg:
                raise HTTPException(status_code=503, detail=msg) from None
            raise HTTPException(status_code=400, detail=msg) from None
        _record_portal_usage(
            store,
            event_type="web_voice_asr",
            user_id=user_id,
            detail=f"{voice_format}:{len(raw)}",
            ok=True,
        )
        return {"text": text}

    @app.post("/api/voice/turn", dependencies=phone_secured)
    def voice_turn(
        body: VoiceTurnBody,
        user_id: str = Depends(require_phone),
    ) -> dict[str, Any]:
        from draw_portal import voice_agent
        from draw_portal.mcp_result import unwrap_tool_text
        from draw_portal.push import mint_messaging_token
        from draw_portal.voice_agent import VoiceAgentError
        from draw_portal.voice_jobs import VoiceJobError, run_voice_draw, run_voice_write

        binding = store.get_binding_for_user(user_id)
        device_mac: str | None = binding.device_mac if binding else None
        token_holder: dict[str, str | None] = {
            "token": (
                decrypt_token(config.token_cipher_key, binding.device_token_enc)
                if binding
                else None
            ),
        }
        enc_holder: dict[str, str | None] = {
            "enc": binding.device_token_enc if binding else None,
        }

        def push_tool(name: str, arguments: dict[str, Any]) -> str:
            tok = token_holder["token"]
            if tok is None:
                return "请先绑定丹青"
            ok, result = push_tool_call(
                api_base=config.xiaozhi_api_base,
                device_token=tok,
                name=name,
                arguments=arguments,
                timeout=15,
            )
            if not ok and result in ("HTTP 401", "HTTP 403", "HTTP 404") and binding is not None:
                fresh = mint_messaging_token(
                    api_base=config.xiaozhi_api_base, mac=binding.device_mac
                )
                if fresh:
                    new_enc = encrypt_token(config.token_cipher_key, fresh)
                    store.update_binding_token(user_id, device_token_enc=new_enc, device_mac=binding.device_mac)
                    token_holder["token"] = fresh
                    enc_holder["enc"] = new_enc
                    ok, result = push_tool_call(
                        api_base=config.xiaozhi_api_base,
                        device_token=fresh,
                        name=name,
                        arguments=arguments,
                        timeout=15,
                    )
            if not ok:
                return result if isinstance(result, str) else str(result)
            return unwrap_tool_text(result)

        def run_portal_tool(
            name: str, arguments: dict[str, Any]
        ) -> tuple[str, dict[str, str] | None]:
            if binding is None or enc_holder["enc"] is None or device_mac is None:
                raise VoiceJobError("请先绑定丹青")

            def on_token_minted(new_enc: str) -> None:
                store.update_binding_token(user_id, device_token_enc=new_enc, device_mac=binding.device_mac)
                enc_holder["enc"] = new_enc
                token_holder["token"] = decrypt_token(config.token_cipher_key, new_enc)

            if name == "voice.draw":
                if not config.usage_ingest_secret:
                    raise VoiceJobError("生图通道未配置", status=503)
                desc = str(arguments.get("description") or "").strip()
                since = time.time() - 86400
                used = sum(
                    row["count"]
                    for row in store.usage_counts_for_user_since(user_id, since)
                    if row["event_type"] == "web_generate"
                )
                if used >= config.generate_daily_limit:
                    raise VoiceJobError(
                        f"今日生图次数已达上限（{config.generate_daily_limit} 次），明天再来",
                        status=429,
                    )
                job = store.create_draw_job(
                    user_id=user_id, source_name=("语音画: " + desc)[:120]
                )
                job_path = _job_dir(config.data_dir, job.id)
                try:
                    summary, preview = run_voice_draw(
                        description=desc,
                        user_id=user_id,
                        job_id=job.id,
                        job_dir=job_path,
                        mcp_internal_base_url=config.mcp_internal_base_url,
                        usage_ingest_secret=config.usage_ingest_secret,
                        generate_timeout_seconds=config.generate_timeout_seconds,
                        mcp_output_dir=config.mcp_output_dir,
                        public_base_url=config.public_base_url,
                        mark_ready=lambda jid, base: store.mark_draw_job_ready(
                            jid, basename=base
                        ),
                        mark_failed=lambda jid, err: store.mark_draw_job_failed(
                            jid, error_summary=err
                        ),
                        api_base=config.xiaozhi_api_base,
                        token_cipher_key=config.token_cipher_key,
                        device_mac=device_mac,
                        device_token_enc=enc_holder["enc"],
                        on_token_minted=on_token_minted,
                    )
                except Exception as exc:
                    # 已建任务后的失败才通知（守卫级 429/未绑定不打扰）；忙批全忙 429
                    # 同属瞬态忙——与 /api/draw/generate 同口径不推「生成失败」，否则
                    # 失败推送+稍后重试成功推送只会误导用户。
                    if not (isinstance(exc, VoiceJobError) and exc.status == 429):
                        _notify_subscribe(config, store, user_id=user_id, status="failed", title=desc)
                    raise
                _record_portal_usage(
                    store,
                    event_type="web_generate",
                    user_id=user_id,
                    device_mac=device_mac,
                    detail=desc[:120],
                    ok=True,
                )
                _notify_subscribe(config, store, user_id=user_id, status="success", title=desc)
                return summary, preview.as_dict()

            if name == "voice.write":
                raw_text = str(arguments.get("text") or "")
                job = store.create_draw_job(
                    user_id=user_id, source_name=("语音写: " + raw_text.strip())[:120]
                )
                job_path = _job_dir(config.data_dir, job.id)
                try:
                    summary, preview = run_voice_write(
                        text=raw_text,
                        user_id=user_id,
                        job_id=job.id,
                        job_dir=job_path,
                        gfont_dir=config.gfont_dir or "",
                        stroke_dir=config.stroke_dir or "",
                        mcp_output_dir=config.mcp_output_dir,
                        public_base_url=config.public_base_url,
                        mark_ready=lambda jid, base: store.mark_draw_job_ready(
                            jid, basename=base
                        ),
                        mark_failed=lambda jid, err: store.mark_draw_job_failed(
                            jid, error_summary=err
                        ),
                        api_base=config.xiaozhi_api_base,
                        token_cipher_key=config.token_cipher_key,
                        device_mac=device_mac,
                        device_token_enc=enc_holder["enc"],
                        on_token_minted=on_token_minted,
                    )
                except Exception:
                    _notify_subscribe(config, store, user_id=user_id, status="failed", title=raw_text)
                    raise
                _record_portal_usage(
                    store,
                    event_type="web_write",
                    user_id=user_id,
                    device_mac=device_mac,
                    detail=("语音写: " + raw_text.strip())[:100],
                    ok=True,
                )
                _notify_subscribe(
                    config, store, user_id=user_id, status="success", title=raw_text
                )
                return summary, preview.as_dict()

            raise VoiceJobError("未知画写工具")

        try:
            out = voice_agent.chat(
                session_id=body.session_id,
                user_text=body.text,
                device_mac=device_mac,
                push_tool=push_tool,
                run_portal_tool=run_portal_tool,
            )
        except VoiceAgentError as exc:
            msg = str(exc)
            if "未配置" in msg:
                raise HTTPException(status_code=503, detail=msg) from None
            raise HTTPException(status_code=400, detail=msg) from None
        _record_portal_usage(
            store,
            event_type="web_voice_turn",
            user_id=user_id,
            device_mac=device_mac,
            detail=f"{len(body.text)}:{len(out.get('reply', ''))}",
            ok=True,
        )
        payload: dict[str, Any] = {
            "reply": out["reply"],
            "tool_traces": out["tool_traces"],
        }
        if out.get("preview"):
            payload["preview"] = out["preview"]
        return payload

    def _voice_device_tool(user_id: str, tool_name: str) -> dict[str, str]:
        from draw_portal.mcp_result import unwrap_tool_text
        from draw_portal.push import mint_messaging_token

        binding = store.get_binding_for_user(user_id)
        if binding is None:
            raise HTTPException(status_code=400, detail="请先绑定丹青")
        token = decrypt_token(config.token_cipher_key, binding.device_token_enc)
        ok, result = push_tool_call(
            api_base=config.xiaozhi_api_base,
            device_token=token,
            name=tool_name,
            arguments={},
            timeout=30,
        )
        if not ok and result in ("HTTP 401", "HTTP 403", "HTTP 404"):
            fresh = mint_messaging_token(
                api_base=config.xiaozhi_api_base, mac=binding.device_mac
            )
            if fresh:
                store.update_binding_token(
                    user_id,
                    device_token_enc=encrypt_token(config.token_cipher_key, fresh),
                    device_mac=binding.device_mac,
                )
                ok, result = push_tool_call(
                    api_base=config.xiaozhi_api_base,
                    device_token=fresh,
                    name=tool_name,
                    arguments={},
                    timeout=30,
                )
        if not ok:
            detail = result if isinstance(result, str) else str(result)
            raise HTTPException(status_code=502, detail=detail[:120])
        text = unwrap_tool_text(result)
        _record_portal_usage(
            store,
            event_type=f"web_voice_{tool_name.split('.')[-1]}",
            user_id=user_id,
            device_mac=binding.device_mac,
            detail=tool_name,
            ok=True,
        )
        return {"status": "ok", "result": text}

    @app.post("/api/voice/confirm", dependencies=phone_secured)
    def voice_confirm(user_id: str = Depends(require_phone)) -> dict[str, str]:
        return _voice_device_tool(user_id, "hutuji.confirm")

    @app.post("/api/voice/cancel", dependencies=phone_secured)
    def voice_cancel(user_id: str = Depends(require_phone)) -> dict[str, str]:
        return _voice_device_tool(user_id, "hutuji.abort")

    @app.get("/api/device/firmware/check", dependencies=phone_secured)
    def firmware_check(user_id: str = Depends(require_phone)) -> dict[str, Any]:
        binding = store.get_binding_for_user(user_id)
        if binding is None:
            raise HTTPException(status_code=400, detail="请先绑定设备")
        return firmware_ota_mod.check_firmware(
            store=store,
            binding=binding,
            catalog_path=config.firmware_catalog_path,
            api_base=config.xiaozhi_api_base,
            token_cipher_key=config.token_cipher_key,
        )

    @app.post("/api/device/firmware/upgrade", dependencies=phone_secured)
    def firmware_upgrade(user_id: str = Depends(require_phone)) -> dict[str, Any]:
        binding = store.get_binding_for_user(user_id)
        if binding is None:
            raise HTTPException(status_code=400, detail="请先绑定设备")
        return firmware_ota_mod.start_upgrade(
            store=store,
            binding=binding,
            catalog_path=config.firmware_catalog_path,
            public_base=config.firmware_public_base,
            signing_secret=config.firmware_signing_secret,
            api_base=config.xiaozhi_api_base,
            token_cipher_key=config.token_cipher_key,
        )

    @app.get("/api/device/firmware/ota_status", dependencies=phone_secured)
    def firmware_ota_status(user_id: str = Depends(require_phone)) -> dict[str, Any]:
        binding = store.get_binding_for_user(user_id)
        if binding is None:
            raise HTTPException(status_code=400, detail="请先绑定设备")
        return firmware_ota_mod.get_ota_status(
            store=store,
            binding=binding,
            api_base=config.xiaozhi_api_base,
            token_cipher_key=config.token_cipher_key,
        )

    def _finalize_binding(session: BindSession, user_id: str, now: float, event_type: str,
                          *, force: bool = False, require_claim: bool = False) -> DeviceBinding:
        with _bind_locks[int(session.device_mac.replace(":", ""), 16) % len(_bind_locks)]:
            current = store.get_bind_session(session.bind_code)
            if current and current.consumed_at is not None:
                binding = store.get_binding_for_mac(current.device_mac)
                if binding and binding.user_id == user_id and binding.bound_at == current.consumed_at:
                    return binding
            if current is None or current.consumed_at is not None or current.expires_at <= time.time():
                raise BindStateError("绑定码无效或已过期", status=410, code="binding_expired")
            store.check_binding_owner(current, user_id=user_id, force=force)
            return _finalize_binding_locked(current, user_id, now, event_type,
                                            force=force, require_claim=require_claim)

    def _finalize_binding_locked(session: BindSession, user_id: str, now: float, event_type: str,
                                 *, force: bool, require_claim: bool) -> DeviceBinding:
        """探活 + 旧 token 保留 + 落绑定 + usage 埋点：抽屉码与自动撮合共用一条完成路径。"""
        announced_token = decrypt_token(config.token_cipher_key, session.device_token_enc)
        ok, _summary = probe_device_token(
            api_base=config.xiaozhi_api_base, device_token=announced_token,
        ) if announced_token else (False, "missing")
        token_enc = session.device_token_enc
        existing = store.get_binding_for_user(user_id)
        same_device = store.get_binding_for_mac(session.device_mac)
        if (
            not ok
            and same_device is not None
            and same_device.user_id == user_id
            and same_device.token_probe_ok
        ):
            # 固件 announce 仍可能上报 websocket token（非 messaging JWT）；
            # 旧设备维持兼容行为；签名流须重新探活，历史成功标记不代表当前仍在线。
            old_ok = True
            if session.device_id is not None:
                try:
                    old_token = decrypt_token(config.token_cipher_key, same_device.device_token_enc)
                except ValueError:
                    old_ok = False
                else:
                    old_ok, _summary = probe_device_token(
                        api_base=config.xiaozhi_api_base, device_token=old_token,
                    )
            if old_ok:
                token_enc = same_device.device_token_enc
                ok = True
        if not ok:
            # 绑定期现场铸 messaging token（2026-09-08 量产口径）：announce 上报的常是
            # NVS websocket token（≠ messaging JWT），等推送 401 再自愈则用户第一单必
            # 失败；控制台 JWT 经 draw-portal.env 注入（refresh 脚本已互联），铸新失败
            # 仅旧版按原样落绑定；签名流在下方保持 pending，等实际连通再消费证明。
            from draw_portal.push import mint_messaging_token
            from draw_portal.crypto import encrypt_token


            fresh = mint_messaging_token(
                api_base=config.xiaozhi_api_base, mac=session.device_mac
            )
            if fresh:
                token_enc = encrypt_token(config.token_cipher_key, fresh)
                ok = True
                if session.device_id is not None:
                    ok, _summary = probe_device_token(
                        api_base=config.xiaozhi_api_base, device_token=fresh,
                    )
        if session.device_id is not None and not ok:
            # 身份确认不等于连接就绪；保留短码与 nonce，让切网后的自动重试继续。
            raise BindStateError("设备连接正在准备，请稍候",
                                 status=503, code="binding_pending")
        binding = store.complete_binding(
            session.bind_code, user_id=user_id, force=force, device_token_enc=token_enc,
            token_probe_ok=ok, now=time.time(), require_claim=require_claim,
        )
        if existing is None:
            # 注册监控（TG）：新用户首次绑定 = 注册完成的业务事实；重绑/换机不推。
            tg_notify.notify_async(
                f"新用户绑定设备 mac={binding.device_mac} 事件={event_type}",
                dedupe_key=user_id,
            )
        _record_portal_usage(
            store,
            event_type=event_type,
            user_id=user_id,
            device_mac=binding.device_mac,
            ok=ok,
        )
        return binding

    def _try_auto_bind(mac: str, user_id: str, force: bool, now: float) -> DeviceBinding | None:
        """claim（用户在场证明）× session（设备身份证明）双向撮合。
        跨账号冲突未确认时不越权：claim 保留，等用户 force 重认领或走抽屉码。"""
        other_user = store.get_user_id_for_mac(mac)
        if other_user is not None and other_user != user_id and not force:
            return None
        claim = store.get_bind_claim(mac, now=now)
        if claim is None or claim.user_id != user_id:
            return None
        session = store.get_live_bind_session_for_mac(mac, now=now, claim=claim)
        if session is None:
            return None
        return _finalize_binding(session, user_id, now, "bind_auto_complete",
                                 force=force, require_claim=True)

    @app.post("/api/user/bind/complete", dependencies=phone_secured)
    def bind_complete(body: BindCompleteBody, user_id: str = Depends(user_dep)) -> dict[str, Any]:
        try:
            code = normalize_bind_code(body.bind_code)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        now = time.time()
        # 先偷看不消费：跨账号冲突要 409 且不烧掉会话，用户一键确认后同码强制换绑
        peek = store.get_bind_session(code)
        if peek is None:
            raise _bind_error(BindStateError("设备正在联网，请稍候", status=400, code="binding_pending"))
        if any(value is not None for value in (body.device_id, body.public_key, body.nonce, body.identity_version)):
            try:
                identity = _identity_from_body(body)
            except DeviceIdentityError as exc:
                raise _bind_error(BindStateError(str(exc))) from None
            if (peek.device_id, peek.public_key, peek.nonce) != (identity.device_id, identity.public_key, identity.nonce):
                raise _bind_error(BindStateError("扫码信息与设备不匹配，请重新扫码"))
        if config.device_identity_required and peek.device_id is None:
            raise _bind_error(BindStateError("该设备需要升级身份固件"))
        other_user = store.get_user_id_for_mac(peek.device_mac)
        if other_user is not None and other_user != user_id:
            if not body.force:
                raise _bind_error(BindStateError("该设备已绑定其他账号，确认后可换绑", code="binding_conflict"))
        try:
            binding = _finalize_binding(peek, user_id, now, "bind_complete", force=body.force)
        except BindStateError as exc:
            raise _bind_error(exc) from None
        return {"status": "bound", "binding": store.binding_status_dict(binding),
                "device_mac": binding.device_mac, "mac_suffix": binding.device_mac[-5:]}

    @app.post("/api/user/bind/claim", dependencies=phone_secured)
    def bind_claim(body: BindClaimBody, user_id: str = Depends(user_dep)) -> dict[str, Any]:
        """一次扫码合一流程的认领侧：小程序配网成功后按 QR 里的 MAC 认领。
        设备 announce 到达即自动绑定；announce 先到（设备先连回家网）则此处直接撮合。"""
        try:
            mac = normalize_mac(body.mac)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        now = time.time()
        other_user = store.get_user_id_for_mac(mac)
        known = store.get_device_identity(mac)
        identity = None
        supplied = any(value is not None for value in (body.device_id, body.public_key, body.nonce, body.identity_version))
        if config.device_identity_required or supplied or known is not None:
            try:
                identity = _identity_from_body(body)
            except DeviceIdentityError as exc:
                raise _bind_error(BindStateError(str(exc))) from None
            if known and known["device_id"] != identity.device_id and other_user not in (None, user_id):
                raise _bind_error(BindStateError("设备身份已重置，请由原绑定账号重新扫码恢复"))
            challenge = store.get_identity_challenge(identity)
            if challenge and (challenge["device_mac"] != mac or challenge["public_key"] != identity.public_key):
                raise _bind_error(BindStateError("二维码与设备不匹配，请重新扫码"))
            if challenge and (challenge["expires_at"] <= now or challenge["consumed_at"] is not None):
                existing = store.get_completed_identity_binding(identity, mac=mac, user_id=user_id)
                if existing is None:
                    raise _bind_error(BindStateError("绑定窗口已过期，请重新扫描设备二维码", status=410, code="binding_expired"))
                return {"status": "already_bound", "binding": store.binding_status_dict(existing)}
        if other_user == user_id and identity is None:
            existing_binding = store.get_binding_for_mac(mac)
            if existing_binding is not None:
                return {
                    "status": "already_bound",
                    "binding": store.binding_status_dict(existing_binding),
                }
        if other_user is not None and not body.force:
            if other_user != user_id:
                raise _bind_error(BindStateError("该设备已绑定其他账号，确认后可换绑", code="binding_conflict"))
        expires_at = now + (min(config.bind_claim_ttl_seconds, config.device_identity_nonce_ttl_seconds)
                            if identity else config.bind_claim_ttl_seconds)
        if identity and challenge:
            expires_at = min(expires_at, challenge["expires_at"])
        claim = store.upsert_bind_claim(
            device_mac=mac,
            user_id=user_id,
            force=body.force,
            expires_at=expires_at,
            created_at=now,
            device_id=body.device_id,
            nonce=body.nonce,
            public_key=body.public_key,
            identity_version=body.identity_version,
        )
        try:
            binding = _try_auto_bind(mac, user_id, body.force, now)
        except BindStateError as exc:
            raise _bind_error(exc) from None
        if binding is not None:
            return {"status": "bound", "binding": store.binding_status_dict(binding)}
        return {"status": "claimed", "expires_at": claim.expires_at}

    @app.get("/api/user/bind/claim/status", dependencies=phone_secured)
    def bind_claim_status(mac: str, device_id: str | None = None, public_key: str | None = None,
                          nonce: str | None = None, identity_version: int | None = None,
                          user_id: str = Depends(user_dep)) -> dict[str, Any]:
        """返回当前用户对某台设备的一次扫码认领进度。

        小程序在设备重启、切回家庭 Wi‑Fi 的窗口内轮询此接口；不回显
        认领人的信息，也不把 MAC 当作授权凭据。已绑定时直接返回 bound，
        认领过期时返回 expired，便于客户端给出可恢复的重试按钮。
        """
        try:
            normalized = normalize_mac(mac)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        now = time.time()
        identity = None
        if any(value is not None for value in (device_id, public_key, nonce, identity_version)):
            try:
                identity = parse_identity(device_id=device_id, public_key=public_key,
                                          nonce=nonce, identity_version=identity_version)
            except DeviceIdentityError as exc:
                raise _bind_error(BindStateError(str(exc))) from None
            binding = store.get_completed_identity_binding(identity, mac=normalized, user_id=user_id)
            if binding:
                return {"status": "bound", "binding": store.binding_status_dict(binding)}
        claim = store.get_bind_claim(normalized, now=now)
        if claim is not None and claim.user_id == user_id:
            if identity is None and claim.device_id is not None:
                raise _bind_error(BindStateError("请重新扫描设备二维码"))
            if identity and (claim.device_id, claim.public_key, claim.nonce, claim.identity_version) != (
                identity.device_id, identity.public_key, identity.nonce, identity.identity_version
            ):
                return {"status": "expired"}
            try:
                matched = _try_auto_bind(normalized, user_id, claim.force, now)
            except BindStateError as exc:
                raise _bind_error(exc) from None
            if matched is not None:
                return {"status": "bound", "binding": store.binding_status_dict(matched)}
            return {"status": "claimed", "expires_at": claim.expires_at}
        if claim is None:
            # 认领可能已被 announce 消费；再次检查 MAC 归属以保持幂等。
            if identity is None and store.get_device_identity(normalized) is None and store.get_user_id_for_mac(normalized) == user_id:
                binding = store.get_binding_for_mac(normalized)
                return {"status": "bound", "binding": store.binding_status_dict(binding)}
            return {"status": "expired"}
        if claim.user_id != user_id:
            # 不向其他账号泄露认领窗口是否存在。
            return {"status": "expired"}
        return {"status": "claimed", "expires_at": claim.expires_at}

    @app.post("/api/user/bind/unbind", dependencies=phone_secured)
    def bind_unbind(body: BindUnbindBody | None = None, user_id: str = Depends(user_dep)) -> dict[str, str]:
        binding = store.get_binding_for_user(user_id)
        if body is not None and body.device_mac:
            try:
                target = store.get_binding_for_mac(body.device_mac)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if target is None or target.user_id != user_id:
                raise HTTPException(status_code=404, detail="设备未绑定到当前账号")
            binding = target
        removed = store.delete_binding_for_mac(binding.device_mac, user_id=user_id) if binding else False
        if not removed:
            raise HTTPException(status_code=404, detail="未绑定设备")
        if binding is not None:
            _record_portal_usage(
                store,
                event_type="unbind",
                user_id=user_id,
                device_mac=binding.device_mac,
            )
        return {"status": "unbound"}

    @app.get("/api/user/bind/status", dependencies=phone_secured)
    def bind_status(user_id: str = Depends(user_dep)) -> dict[str, Any]:
        binding = store.get_binding_for_user(user_id)
        return store.binding_status_dict(binding)

    @app.get("/api/user/devices", dependencies=phone_secured)
    def list_user_devices(user_id: str = Depends(user_dep)) -> dict[str, Any]:
        devices = []
        active = store.get_binding_for_user(user_id)
        for item in store.list_bindings_for_user(user_id):
            status = store.binding_status_dict(item)
            status["device_mac"] = item.device_mac
            status["active"] = bool(active and active.device_mac == item.device_mac)
            devices.append(status)
        return {"devices": devices}

    @app.post("/api/user/devices/select", dependencies=phone_secured)
    def select_user_device(body: DeviceSelectBody, user_id: str = Depends(user_dep)) -> dict[str, Any]:
        try:
            binding = store.select_binding_for_user(user_id, body.device_mac)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if binding is None:
            raise HTTPException(status_code=404, detail="设备未绑定到当前账号")
        return {"status": "selected", "binding": store.binding_status_dict(binding)}

    def _require_binding(user_id: str):
        binding = store.get_binding_for_user(user_id)
        if binding is None:
            raise HTTPException(status_code=400, detail="请先绑定小派设备")
        return binding

    def _reject_if_firmware_upgrading(user_id: str) -> None:
        if firmware_ota_mod.is_upgrading(store, user_id):
            raise HTTPException(status_code=400, detail="固件升级中，请稍后再出图")


    def _machine_profile_for_user(user_id: str) -> tuple[object, str]:
        """按绑定机型选 G-code 生成档（protocol §10.4.15，2026-09-11）。

        知识源优先级：设备 status 探测（现值，命中回写库）→ 库值
        （announce 学习 / 默认 paper）；探测失败（离线 / 1.0.8 旧固件无字段）
        回落库值不写库。返回 (profile, machine)：paper → (None, "paper")，
        传 None 保持 MK38 默认档与 G-code 内容缓存键逐字节兼容；nopaper →
        PROFILE_NOPAPER（不转置、限幅 205/290）。
        """
        from hutuji_mcp.gcode_gen import PROFILE_NOPAPER

        binding = store.get_binding_for_user(user_id)
        if binding is None:
            return None, "paper"
        machine = binding.machine_type or "paper"
        try:
            sku = probe_plotter_sku(
                api_base=config.xiaozhi_api_base,
                device_token=decrypt_token(
                    config.token_cipher_key, binding.device_token_enc
                ),
            )
        except Exception:
            sku = None
        if sku is not None:
            machine = sku
            try:
                store.set_binding_machine_type(user_id, sku, device_mac=binding.device_mac)
            except ValueError:
                pass
        if machine == "nopaper":
            return PROFILE_NOPAPER, "nopaper"
        return None, "paper"
    def _publish_from_job(job_id: str) -> tuple[str, str]:
        published = publish_job_files(
            job_dir=_job_dir(config.data_dir, job_id),
            mcp_output_dir=config.mcp_output_dir,
            public_base_url=config.public_base_url,
        )
        return published.gcode_url, published.preview_url

    async def _refine_photo_job(orig_job_id: str, raw: bytes, user_id: str, style: str) -> None:
        """照片两段式第 2 段：程序化风格渲染（排线 hatch / 波纹 squiggle）→ 独立 refined job。

        2026-09-10 拍板替换 AI i2i 链：位图经骨架化必然碎线糊团（提示词调优
        在 TCB 实测撞 run-to-run 噪声地板），程序化渲染直出矢量折线、零模型费、
        零付费通道依赖、确定性可复现；魔搭/TCB i2i 客户端模块保留为备用通道，
        但本链不再调用。原 job 的 Canny 结果已同步回给用户；此处任何失败只把
        refined_status 置 failed 并记类型名，绝不影响原 job（fail-open 口径不变）。
        渲染确定性 + §5 字节硬闸在 process_upload_sketch 内——原 AI 链的抬笔门
        （PREF/HARD 两轮重试）对确定性算法无意义，随链删除。
        """
        from draw_portal.pipeline import process_upload_sketch

        try:
            refined_job = store.create_draw_job(
                user_id=user_id, source_name=f"风格精修:{style}"
            )
            refined_path = _job_dir(config.data_dir, refined_job.id)
            try:
                profile, _machine = await asyncio.to_thread(
                    _machine_profile_for_user, user_id
                )
                result = await asyncio.to_thread(
                    process_upload_sketch,
                    raw,
                    refined_path,
                    style=style,
                    profile=profile,
                )
                published = await asyncio.to_thread(
                    publish_job_files,
                    job_dir=refined_path,
                    mcp_output_dir=config.mcp_output_dir,
                    public_base_url=config.public_base_url,
                )
                store.mark_draw_job_ready(refined_job.id, basename=published.basename)
            except Exception as exc:
                store.mark_draw_job_failed(
                    refined_job.id, error_summary=type(exc).__name__
                )
                raise
            store.set_draw_job_refined(
                orig_job_id, status="ready", refined_job_id=refined_job.id
            )
            logger.info(
                "照片风格精修完成：%s（%s，抬笔 %s）", orig_job_id, style, result.pen_lifts
            )
        except asyncio.CancelledError:
            store.set_draw_job_refined(orig_job_id, status="failed")
            raise
        except Exception as exc:
            logger.info("照片风格精修失败（%s）：%s", type(exc).__name__, orig_job_id)
            store.set_draw_job_refined(orig_job_id, status="failed")

    async def _refine_photo_job_guarded(orig_job_id: str, raw: bytes, user_id: str, style: str) -> None:
        async with _refine_slots:
            await _refine_photo_job(orig_job_id, raw, user_id, style)

    @app.post("/api/draw/upload", dependencies=phone_secured)
    async def draw_upload(
        file: UploadFile = File(...),
        size_mm: str | None = Form(None),
        line_spacing: str | None = Form(None),
        style: str | None = Form(None),
        user_id: str = Depends(user_dep),
    ) -> dict[str, Any]:
        # 照片风格精修档位：hatch 排线素描（默认）/ squiggle 连续波纹；
        # 白名单外的值静默回落默认——风格是体验参数不是安全闸，不值得 400。
        sketch_style = style if style in ("hatch", "squiggle") else "hatch"
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
            from draw_portal.write_layout_params import WriteLayoutParamsError, parse_optional_float

            try:
                doc_size_mm = parse_optional_float(size_mm)
                doc_line_spacing = parse_optional_float(line_spacing)
            except WriteLayoutParamsError as exc:
                store.mark_draw_job_failed(job.id, error_summary="WriteLayoutParamsError")
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            try:
                profile, _machine = await asyncio.to_thread(
                    _machine_profile_for_user, user_id
                )
                result = await asyncio.to_thread(
                    process_upload_doc,
                    raw,
                    kind,
                    job_path,
                    gfont_dir=config.gfont_dir,
                    stroke_dir=config.stroke_dir,
                    size_mm=doc_size_mm,
                    line_spacing=doc_line_spacing,
                    profile=profile,
                )
                published_pages = await asyncio.to_thread(
                    publish_doc_pages,
                    job_dir=job_path,
                    mcp_output_dir=config.mcp_output_dir,
                    public_base_url=config.public_base_url,
                )
                # basename 单值化决策：job 主键 = 第 1 页；整篇页表在 pages.json
                store.mark_draw_job_ready(job.id, basename=published_pages[0].basename)
            except (DocIngestError, WriteTextError, WriteLayoutParamsError) as exc:
                store.mark_draw_job_failed(job.id, error_summary=type(exc).__name__)
                _notify_subscribe(config, store, user_id=user_id, status="failed", title=source_name)
                raise HTTPException(status_code=400, detail=str(exc)[:120]) from None
            except Exception as exc:
                store.mark_draw_job_failed(job.id, error_summary=type(exc).__name__)
                _notify_subscribe(config, store, user_id=user_id, status="failed", title=source_name)
                raise HTTPException(status_code=422, detail="文档转换失败") from exc

            _record_portal_usage(
                store,
                event_type="web_upload",
                user_id=user_id,
                detail=f"doc:{kind}:{source_name[:100]}",
                ok=True,
            )
            _notify_subscribe(config, store, user_id=user_id, status="success", title=source_name)
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
            profile, _machine = await asyncio.to_thread(
                _machine_profile_for_user, user_id
            )
            result = await asyncio.to_thread(
                process_upload,
                raw,
                content_type=content_type,
                filename=filename,
                job_dir=job_path,
                profile=profile,
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
            _notify_subscribe(config, store, user_id=user_id, status="failed", title=source_name)
            raise HTTPException(status_code=422, detail="图片转换失败") from exc

        _record_portal_usage(
            store,
            event_type="web_upload",
            user_id=user_id,
            detail=source_name[:120],
            ok=True,
        )
        _notify_subscribe(config, store, user_id=user_id, status="success", title=source_name)
        # 照片两段式：Canny 结果已就绪，后台起程序化风格渲染（2026-09-10 起
        # 替换 AI i2i——零付费通道依赖，任何环境都可精修）；小程序在结果页
        # 轮询 /api/draw/jobs/{id} 的 refined 字段。非照片（线稿/SVG/文档）
        # refined=null，前端不展示精修入口。
        refined_payload: dict[str, Any] | None = None
        if await asyncio.to_thread(is_photo_raster, raw):
            store.set_draw_job_refined(job.id, status="pending")
            _spawn_background(_refine_photo_job_guarded(job.id, raw, user_id, sketch_style))
            refined_payload = {"status": "pending", "style": sketch_style}
        return {
            "status": "ready",
            "job_id": job.id,
            "preview_url": published.preview_url,
            "gcode_url": published.gcode_url,
            "lines": result.lines,
            "bytes": result.bytes,
            "refined": refined_payload,
        }

    @app.post("/api/draw/write/ai", dependencies=phone_secured)
    async def draw_write_ai(
        body: WriteAiBody,
        user_id: str = Depends(user_dep),
    ) -> dict[str, Any]:
        """主题生成或草稿润色：只回纯文本，不排版、不出纸。"""
        from hutuji_mcp.article_llm import generate_or_polish
        from hutuji_mcp.llm_svg import LlmError

        mode = (body.mode or "").strip().lower()
        try:
            article = await generate_or_polish(
                mode=mode, prompt=body.prompt, text=body.text
            )
        except LlmError as exc:
            raise HTTPException(status_code=400, detail=str(exc)[:120]) from None
        _record_portal_usage(
            store,
            event_type="web_write_ai",
            user_id=user_id,
            detail=f"{mode}:{len(article)}",
            ok=True,
        )
        return {"status": "ok", "mode": mode, "text": article}

    @app.get("/api/fonts", dependencies=phone_secured)
    async def list_write_fonts() -> dict[str, Any]:
        """写字可选字库列表（小程序字体选择器）；未配置字库目录 = 503。"""
        from hutuji_mcp.write_text import list_fonts

        if not config.gfont_dir:
            raise HTTPException(status_code=503, detail="写字字库未配置")
        return {"fonts": list_fonts(config.gfont_dir)}


    @app.get("/api/wx/subscribe-config", dependencies=phone_secured)
    def wx_subscribe_config() -> dict[str, Any]:
        """小程序启动时拉订阅消息模板 ID；空模板 = 通知未启用（前端静默跳过）。"""
        from draw_portal import subscribe_notify

        return {
            "enabled": subscribe_notify.enabled(config),
            "template_id": config.subscribe_template_id or None,
        }
    @app.post("/api/draw/write", dependencies=phone_secured)
    async def draw_write(
        body: WriteBody,
        user_id: str = Depends(user_dep),
    ) -> dict[str, Any]:
        from hutuji_mcp import t2s
        from hutuji_mcp.write_text import WriteTextError

        from draw_portal.write_layout_params import WriteLayoutParamsError, resolve_article_layout

        if not config.gfont_dir:
            raise HTTPException(status_code=503, detail="写字字库未配置")
        safe = t2s.to_simplified(body.text.strip())
        if not safe:
            raise HTTPException(status_code=400, detail="文本为空")
        if len(safe) > 5000:
            raise HTTPException(
                status_code=400,
                detail=f"文本超长：{len(safe)} 字符（上限 5000）",
            )
        try:
            layout = resolve_article_layout(
                size_mm=body.size_mm,
                line_spacing=body.line_spacing,
            )
        except WriteLayoutParamsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        font_name = (body.font or "").strip() or None
        if font_name is not None and (
            len(font_name) > 60 or "/" in font_name or "\\" in font_name or ".." in font_name
        ):
            raise HTTPException(status_code=400, detail="字体名不合法")

        source_name = ("写字: " + safe)[:120]
        job = store.create_draw_job(user_id=user_id, source_name=source_name)
        job_path = _job_dir(config.data_dir, job.id)
        try:
            profile, _machine = await asyncio.to_thread(
                _machine_profile_for_user, user_id
            )
            result = await asyncio.to_thread(
                process_write_text,
                safe,
                job_path,
                gfont_dir=config.gfont_dir,
                layout=layout,
                stroke_dir=config.stroke_dir or "",
                font_name=font_name,
                profile=profile,
                fit_single_page=body.size_mm is None,
            )
            published_pages = await asyncio.to_thread(
                publish_doc_pages,
                job_dir=job_path,
                mcp_output_dir=config.mcp_output_dir,
                public_base_url=config.public_base_url,
            )
            store.mark_draw_job_ready(job.id, basename=published_pages[0].basename)
        except WriteTextError as exc:
            store.mark_draw_job_failed(job.id, error_summary=type(exc).__name__)
            _notify_subscribe(config, store, user_id=user_id, status="failed", title=safe)
            raise HTTPException(status_code=400, detail=str(exc)[:120]) from None
        except Exception as exc:
            store.mark_draw_job_failed(job.id, error_summary=type(exc).__name__)
            _notify_subscribe(config, store, user_id=user_id, status="failed", title=safe)
            raise HTTPException(status_code=422, detail="写字转换失败") from exc

        _record_portal_usage(
            store,
            event_type="web_write",
            user_id=user_id,
            detail=source_name[:100],
            ok=True,
        )
        _notify_subscribe(config, store, user_id=user_id, status="success", title=safe)
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

    @app.post("/api/draw/teach", dependencies=phone_secured)
    async def draw_teach(
        body: TeachBody,
        user_id: str = Depends(user_dep),
    ) -> dict[str, Any]:
        from hutuji_mcp import t2s
        from hutuji_mcp.stroke_order import StrokeOrderError

        if not config.stroke_dir:
            raise HTTPException(status_code=503, detail="笔顺数据未配置")
        safe = t2s.to_simplified(body.text.strip())
        chars, input_truncated = extract_teach_chars(safe)
        if not chars:
            raise HTTPException(status_code=400, detail="没有可教的汉字")

        source_name = ("笔顺: " + chars)[:120]
        job = store.create_draw_job(user_id=user_id, source_name=source_name)
        job_path = _job_dir(config.data_dir, job.id)
        try:
            profile, _machine = await asyncio.to_thread(
                _machine_profile_for_user, user_id
            )
            result = await asyncio.to_thread(
                process_teach_text,
                chars,
                job_path,
                stroke_dir=config.stroke_dir,
                profile=profile,
            )
            published_pages = await asyncio.to_thread(
                publish_doc_pages,
                job_dir=job_path,
                mcp_output_dir=config.mcp_output_dir,
                public_base_url=config.public_base_url,
            )
            store.mark_draw_job_ready(job.id, basename=published_pages[0].basename)
        except StrokeOrderError as exc:
            store.mark_draw_job_failed(job.id, error_summary=type(exc).__name__)
            _notify_subscribe(config, store, user_id=user_id, status="failed", title=safe)
            raise HTTPException(status_code=400, detail=str(exc)[:120]) from None
        except Exception as exc:
            store.mark_draw_job_failed(job.id, error_summary=type(exc).__name__)
            _notify_subscribe(config, store, user_id=user_id, status="failed", title=safe)
            raise HTTPException(status_code=422, detail="笔顺转换失败") from exc

        _record_portal_usage(
            store,
            event_type="web_teach",
            user_id=user_id,
            detail=source_name[:100],
            ok=True,
        )
        _notify_subscribe(config, store, user_id=user_id, status="success", title=chars)
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
            "chars_taught": chars,
            "input_truncated": input_truncated,
        }

    @app.get("/api/draw/jobs/{job_id}", dependencies=phone_secured)
    def draw_job_status(
        job_id: str,
        user_id: str = Depends(user_dep),
    ) -> dict[str, Any]:
        """job 状态查询：两段式精修进度的轮询面（小程序结果页 ~5s 间隔）。

        URL 由 basename 确定性推导（与 push._install_capability_pair 同公式），
        不另存；refined=null 表示无精修（非照片/未启用）。
        """
        job = store.get_draw_job(job_id, user_id=user_id)
        if job is None:
            raise HTTPException(status_code=404, detail="作品不存在")
        prefix = config.public_base_url.rstrip("/")
        payload: dict[str, Any] = {"job_id": job.id, "status": job.status}
        if job.status == "ready" and job.basename:
            payload["gcode_url"] = f"{prefix}/files/{job.basename}.gcode"
            payload["preview_url"] = f"{prefix}/files/{job.basename}.png"
        refined: dict[str, Any] | None = None
        if job.refined_status:
            refined = {"status": job.refined_status}
            if job.refined_status == "ready" and job.refined_job_id:
                refined["job_id"] = job.refined_job_id
                refined_job = store.get_draw_job(job.refined_job_id, user_id=user_id)
                if refined_job and refined_job.status == "ready" and refined_job.basename:
                    refined["gcode_url"] = f"{prefix}/files/{refined_job.basename}.gcode"
                    refined["preview_url"] = f"{prefix}/files/{refined_job.basename}.png"
        payload["refined"] = refined
        return payload

    @app.post("/api/draw/generate", dependencies=phone_secured)
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
            _profile, machine_type = await asyncio.to_thread(
                _machine_profile_for_user, user_id
            )
            result = await asyncio.to_thread(
                request_internal_draw,
                base_url=config.mcp_internal_base_url,
                secret=config.usage_ingest_secret,
                description=description,
                timeout_seconds=config.generate_timeout_seconds,
                machine=machine_type,
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
            # 429（2026-09-12 忙批：MCP 侧全忙瞬态映射 429；含日限/队列满）不推
            # 「生成失败」订阅消息——瞬态忙几秒后重试即成，失败推送+随后的成功
            # 推送只会误导；白话已随 detail 透传给页面，in-app 自会解释。
            if exc.status != 429:
                _notify_subscribe(config, store, user_id=user_id, status="failed", title=description)
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
            _notify_subscribe(config, store, user_id=user_id, status="failed", title=description)
            raise HTTPException(status_code=502, detail="生图服务异常") from exc

        _record_portal_usage(
            store,
            event_type="web_generate",
            user_id=user_id,
            detail=description[:120],
            ok=True,
        )
        _notify_subscribe(config, store, user_id=user_id, status="success", title=description)
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

    @app.post("/api/draw/{job_id}/send", dependencies=phone_secured)
    def draw_send(job_id: str, user_id: str = Depends(user_dep)) -> dict[str, Any]:
        _reject_if_firmware_upgrading(user_id)
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
        ok, summary = push_draw_self_heal(
            api_base=config.xiaozhi_api_base,
            token_cipher_key=config.token_cipher_key,
            device_mac=binding.device_mac,
            device_token_enc=binding.device_token_enc,
            gcode_url=gcode_url,
            preview_url=preview_url,
            pages_arg=pages_arg,
            on_token_minted=lambda enc: store.update_binding_token(user_id, device_token_enc=enc, device_mac=binding.device_mac),
            auto_confirm=True,
        )
        if ok:
            store.update_binding_last_push(user_id, when=time.time(), device_mac=binding.device_mac)
        _record_portal_usage(
            store,
            event_type="web_send",
            user_id=user_id,
            device_mac=binding.device_mac,
            ok=ok,
        )
        return {"status": "sent" if ok else "push_failed", "detail": summary}

    @app.post("/api/draw/{job_id}/save", dependencies=phone_secured)
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
            tg_pending=telegram_archive.enabled(),
        )
        # TG 频道冷备份（PoC）：enabled 才动作（行已带 pending 态），daemon
        # 顺序上传+逐字节校验；任何失败只落日志，绝不影响保存主链。
        telegram_archive.enqueue_preview(store, saved)
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

    @app.get("/api/draw/saved", dependencies=phone_secured)
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
                    # TG 冷备份状态（PoC）：null=未启用；pending/uploaded/failed。
                    "tg_backup": it.tg_state,
                }
                for it in items
            ]
        }

    @app.get("/api/draw/saved/{saved_id}", dependencies=phone_secured)
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
            "tg_backup": item.tg_state,
        }

    @app.get("/api/draw/saved/{saved_id}/preview.png", dependencies=phone_secured)
    def draw_saved_preview_png(saved_id: str, user_id: str = Depends(user_dep)) -> FileResponse:
        item = store.get_saved_preview(saved_id, user_id=user_id)
        if item is None:
            raise HTTPException(status_code=404, detail="不存在")
        path = Path(item.preview_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="预览已丢失")
        return FileResponse(path, media_type="image/png")

    @app.post("/api/draw/saved/{saved_id}/send", dependencies=phone_secured)
    def draw_saved_send(saved_id: str, user_id: str = Depends(user_dep)) -> dict[str, Any]:
        _reject_if_firmware_upgrading(user_id)
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

        ok, summary = push_draw_self_heal(
            api_base=config.xiaozhi_api_base,
            token_cipher_key=config.token_cipher_key,
            device_mac=binding.device_mac,
            device_token_enc=binding.device_token_enc,
            gcode_url=published.gcode_url,
            preview_url=published.preview_url,
            on_token_minted=lambda enc: store.update_binding_token(user_id, device_token_enc=enc, device_mac=binding.device_mac),
            auto_confirm=True,
        )
        if ok:
            store.update_binding_last_push(user_id, when=time.time(), device_mac=binding.device_mac)
        _record_portal_usage(
            store,
            event_type="web_saved_send",
            user_id=user_id,
            device_mac=binding.device_mac,
            ok=ok,
        )
        return {"status": "sent" if ok else "push_failed", "detail": summary}


    # ---- 分享卡片（2026-09-11）：HMAC 无状态令牌，接收者免会话只读预览 ----
    # 令牌只盖 job_id + 过期时间；预览图经本端点代理（/files 直链带不上
    # x-session，接收者会话不属于作者，必须走令牌自证）。失败一律 404。
    @app.post("/api/draw/{job_id}/share", dependencies=phone_secured)
    def draw_share_create(job_id: str, user_id: str = Depends(user_dep)) -> dict[str, Any]:
        from draw_portal import share_token

        job = store.get_draw_job(job_id, user_id=user_id)
        if job is None or job.status != "ready":
            raise HTTPException(status_code=404, detail="任务不存在或未就绪")
        token = share_token.issue_token(config.session_secret, job.id)
        return {
            "share_token": token,
            "title": (job.source_name or "作品").strip(),
        }

    @app.get("/api/draw/share/{share_token}")
    def draw_share_view(share_token: str) -> dict[str, Any]:
        from draw_portal import share_token as st_mod

        job_id = st_mod.parse_token(config.session_secret, share_token)
        if not job_id:
            raise HTTPException(status_code=404, detail="分享已失效")
        job = store.get_draw_job_any(job_id)
        if job is None or job.status != "ready":
            raise HTTPException(status_code=404, detail="分享已失效")
        preview_file = _job_dir(config.data_dir, job.id) / "preview.png"
        if not preview_file.is_file():
            raise HTTPException(status_code=404, detail="预览已丢失")
        return {
            "title": (job.source_name or "作品").strip(),
            "preview_url": f"/api/draw/share/{share_token}/preview.png",
            "created_at": job.created_at,
        }

    @app.get("/api/draw/share/{share_token}/preview.png")
    def draw_share_preview_png(share_token: str) -> FileResponse:
        from draw_portal import share_token as st_mod

        job_id = st_mod.parse_token(config.session_secret, share_token)
        if not job_id:
            raise HTTPException(status_code=404, detail="分享已失效")
        job = store.get_draw_job_any(job_id)
        if job is None or job.status != "ready":
            raise HTTPException(status_code=404, detail="分享已失效")
        preview_file = _job_dir(config.data_dir, job.id) / "preview.png"
        if not preview_file.is_file():
            raise HTTPException(status_code=404, detail="预览已丢失")
        return FileResponse(preview_file, media_type="image/png")
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

    @app.post("/api/device/kdraw/done")
    def kdraw_done(body: KdrawDoneBody) -> dict[str, Any]:
        """奎享完成守护云端通知（设备→portal 反向，2026-09-12）。

        鉴权口径与 conversation/report 同一先例：MAC 必须已绑定呼图账号，
        未绑定一律 403，不区分「未绑定/胡报 MAC」。mac 在此面属弱凭证——
        本端点只触发一次性订阅消息（不落库、不暴露数据），伪造收益趋零，
        故不引 device_token（避免 token 轮换 401 丢通知）。
        """
        try:
            mac = normalize_mac(body.mac)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        user_id = store.get_user_id_for_mac(mac)
        if user_id is None:
            raise HTTPException(status_code=403, detail="设备未绑定呼图账号")
        title = (body.title or "").strip()[:20] or "奎享作品"
        notified = _notify_subscribe(
            config, store, user_id=user_id, status="success",
            title=title, phrase="绘制完成",
        )
        logger.info("kdraw done: mac_suffix=%s user=%s notified=%s",
                    mac[-5:], user_id, bool(notified))
        return {"status": "ok", "notified": bool(notified)}

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

    @app.delete("/api/draw/saved/{saved_id}", dependencies=phone_secured)
    def draw_saved_delete(saved_id: str, user_id: str = Depends(user_dep)) -> dict[str, str]:
        item = store.delete_saved_preview(saved_id, user_id=user_id)
        if item is None:
            raise HTTPException(status_code=404, detail="不存在")
        # 频道副本尽力撤回（用户删图 → 第三方不留）；失败仅日志，不回滚本地删除。
        # 台账同步标记 removed：召回时跳过该行（尊重删除意愿），先于 fire-and-forget。
        store.mark_archive_log_removed(saved_id)
        telegram_archive.delete_message_async(item)
        for path in (item.preview_path, item.gcode_path):
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass
        return {"status": "deleted"}

    @app.post("/api/chat", dependencies=phone_secured)
    async def chat_send(body: ChatBody, user_id: str = Depends(user_dep)) -> dict[str, Any]:
        """小程序文字聊天（2026-09-09）：内容安全 → 本机 MCP /internal/chat。

        配额与 web_generate 同口径：成功才计 24h 窗口（失败不占额）。模型回复
        也过一遍内容安全；被拦时回落固定话术，不把「违规」帽子扣给用户。
        消息安全审核需要真实 openid，故本端点只对微信用户开放（聊天 UI 只在小程序）。
        """
        if not config.usage_ingest_secret or not (config.wx_appid and config.wx_secret):
            raise HTTPException(status_code=503, detail="聊天通道未配置")
        user = store.get_user(user_id)
        openid = (user.wx_openid or "") if user else ""
        if not openid:
            raise HTTPException(status_code=403, detail="聊天功能仅小程序账号可用")
        message = body.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="消息不能为空")

        since = time.time() - 86400
        used = sum(
            row["count"]
            for row in store.usage_counts_for_user_since(user_id, since)
            if row["event_type"] == "wx_chat"
        )
        if used >= config.chat_daily_limit:
            raise HTTPException(
                status_code=429,
                detail=f"今日聊天次数已达上限（{config.chat_daily_limit} 次），明天再来",
            )

        history = [{"role": it.role, "content": it.content} for it in body.history]
        try:
            await asyncio.to_thread(
                chat_mod.msg_sec_check,
                appid=config.wx_appid,
                secret=config.wx_secret,
                openid=openid,
                content=message,
            )
            result = await asyncio.to_thread(
                chat_mod.request_internal_chat,
                base_url=config.mcp_internal_base_url,
                secret=config.usage_ingest_secret,
                message=message,
                history=history,
                timeout_seconds=config.chat_timeout_seconds,
            )
            reply = result["reply"]
            try:
                await asyncio.to_thread(
                    chat_mod.msg_sec_check,
                    appid=config.wx_appid,
                    secret=config.wx_secret,
                    openid=openid,
                    content=reply,
                )
            except chat_mod.ChatContentRejected:
                reply = "这句话小派接不住，换个话题聊聊吧~"
        except chat_mod.ChatContentRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except chat_mod.ChatError as exc:
            status = exc.status if exc.status in (400, 503) else 502
            raise HTTPException(status_code=status, detail=exc.detail) from exc

        _record_portal_usage(
            store,
            event_type="wx_chat",
            user_id=user_id,
            detail=message[:120],
            ok=True,
        )
        return {
            "reply": reply,
            "draw_intent": result.get("draw_intent"),
            "remaining": config.chat_daily_limit - used - 1,
        }

    # ---- 小派设置：音色/模型切换（2026-09-12 §10.4.19）----
    # portal 只持共享密钥转发本机 MCP /internal/xiaozhi/*；控制台 JWT 只在 MCP
    # 侧。用户切换只写自己的克隆 agent（首次个性化时 MCP 从基础 agent 整包
    # 克隆人格/KB/MCP/记忆档），设备经验证码绑定后生效；未个性化设备走共享
    # agent 互不影响（用户拍板「不能改所有人的」）。成功才计 usage 限频。

    @app.get("/api/settings/xiaopai", dependencies=phone_secured)
    async def xiaopai_settings(user_id: str = Depends(user_dep)) -> dict[str, Any]:
        try:
            options = await asyncio.to_thread(
                xiaopai_mod.request_options,
                base_url=config.mcp_internal_base_url,
                secret=config.usage_ingest_secret,
            )
        except xiaopai_mod.XiaopaiError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        row = store.xiaopai_settings_get(user_id) or {}
        return {
            "voices": options["voices"],
            "models": options["models"],
            "tts_voice": row.get("tts_voice", ""),
            "llm_model": row.get("llm_model", ""),
            "device_bound": bool(row.get("device_bound", 0)),
            "agent_ready": bool(row),
        }

    @app.post("/api/settings/xiaopai", dependencies=phone_secured)
    async def xiaopai_setting_apply(
        body: XiaopaiSettingBody, user_id: str = Depends(user_dep)
    ) -> dict[str, Any]:
        updates = {
            k: v.strip()
            for k, v in (("tts_voice", body.tts_voice), ("llm_model", body.llm_model))
            if isinstance(v, str) and v.strip()
        }
        if not updates:
            raise HTTPException(status_code=400, detail="tts_voice / llm_model 至少一项")
        since = time.time() - 86400
        used = sum(
            row["count"]
            for row in store.usage_counts_for_user_since(user_id, since)
            if row["event_type"] == "xiaopai_setting"
        )
        if used >= 20:
            raise HTTPException(status_code=429, detail="今日设置次数已达上限，明天再来")
        row = store.xiaopai_settings_get(user_id)
        if not row:
            try:
                clone = await asyncio.to_thread(
                    xiaopai_mod.request_clone,
                    base_url=config.mcp_internal_base_url,
                    secret=config.usage_ingest_secret,
                )
            except xiaopai_mod.XiaopaiError as exc:
                raise HTTPException(status_code=exc.status, detail=str(exc)) from None
            row = store.xiaopai_settings_insert(
                user_id, clone["agent_id"], when=time.time()
            )
        try:
            result = await asyncio.to_thread(
                xiaopai_mod.request_config,
                base_url=config.mcp_internal_base_url,
                secret=config.usage_ingest_secret,
                agent_id=row["agent_id"],
                updates=updates,
            )
        except xiaopai_mod.XiaopaiError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        row = store.xiaopai_settings_update(user_id, when=time.time(), **updates)
        _record_portal_usage(
            store,
            event_type="xiaopai_setting",
            user_id=user_id,
            detail=",".join(sorted(updates)),
            ok=True,
        )
        return {
            "ok": True,
            "tts_voice": result.get("tts_voice"),
            "llm_model": result.get("llm_model"),
            "device_bound": bool(row.get("device_bound", 0)),
        }

    @app.post("/api/settings/xiaopai/bind", dependencies=phone_secured)
    async def xiaopai_setting_bind(
        body: XiaopaiBindBody, user_id: str = Depends(user_dep)
    ) -> dict[str, Any]:
        since = time.time() - 86400
        used = sum(
            row["count"]
            for row in store.usage_counts_for_user_since(user_id, since)
            if row["event_type"] == "xiaopai_bind"
        )
        if used >= 10:
            raise HTTPException(status_code=429, detail="今日绑定尝试已达上限，明天再来")
        row = store.xiaopai_settings_get(user_id)
        if not row:
            try:
                clone = await asyncio.to_thread(
                    xiaopai_mod.request_clone,
                    base_url=config.mcp_internal_base_url,
                    secret=config.usage_ingest_secret,
                )
            except xiaopai_mod.XiaopaiError as exc:
                raise HTTPException(status_code=exc.status, detail=str(exc)) from None
            row = store.xiaopai_settings_insert(
                user_id, clone["agent_id"], when=time.time()
            )
        try:
            await asyncio.to_thread(
                xiaopai_mod.request_bind,
                base_url=config.mcp_internal_base_url,
                secret=config.usage_ingest_secret,
                agent_id=row["agent_id"],
                code=body.code,
            )
        except xiaopai_mod.XiaopaiError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        store.xiaopai_settings_update(user_id, device_bound=1, when=time.time())
        _record_portal_usage(
            store,
            event_type="xiaopai_bind",
            user_id=user_id,
            detail="bound",
            ok=True,
        )
        return {"ok": True, "device_bound": True}

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
