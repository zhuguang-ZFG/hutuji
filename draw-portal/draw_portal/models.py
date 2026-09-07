from __future__ import annotations

import re
from dataclasses import dataclass

_BIND_CODE_RE = re.compile(r"^[A-Z0-9]{6,8}$")
_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
_USER_STATUS = frozenset({"active", "pending", "banned"})
_QQ_REGISTRATION_DOMAINS = frozenset({"qq.com", "foxmail.com", "vip.qq.com"})


@dataclass(frozen=True)
class User:
    id: str
    email: str
    password_hash: str
    created_at: float
    status: str = "active"
    email_verified_at: float | None = None
    last_login_at: float | None = None
    banned_at: float | None = None
    ban_reason: str | None = None
    admin_note: str | None = None
    # 微信小程序登录（2026-09-07）：wx_openid 非空即微信用户，email 为占位符
    wx_openid: str | None = None


@dataclass(frozen=True)
class EmailVerification:
    email: str
    password_hash: str
    code_hash: str
    expires_at: float
    attempts: int
    created_at: float


@dataclass(frozen=True)
class BindSession:
    bind_code: str
    device_mac: str
    device_token_enc: str
    expires_at: float
    consumed_at: float | None


@dataclass(frozen=True)
class DeviceBinding:
    user_id: str
    device_mac: str
    device_token_enc: str
    bound_at: float
    last_push_at: float | None
    token_probe_ok: bool | None


@dataclass(frozen=True)
class DrawJob:
    id: str
    user_id: str
    status: str
    source_name: str | None
    basename: str | None
    error_summary: str | None
    created_at: float


@dataclass(frozen=True)
class SavedPreview:
    id: str
    user_id: str
    job_id: str | None
    title: str
    preview_path: str
    gcode_path: str | None
    saved_at: float


@dataclass(frozen=True)
class UserSummary:
    user: User
    job_count: int
    saved_count: int
    bound: bool
    device_mac: str | None


def normalize_mac(mac: str) -> str:
    m = mac.strip().lower()
    if not _MAC_RE.match(m):
        raise ValueError("无效 MAC 地址")
    return m


def normalize_bind_code(code: str) -> str:
    c = code.strip().upper()
    if not _BIND_CODE_RE.match(c):
        raise ValueError("无效 bind_code")
    return c


def normalize_email(email: str) -> str:
    e = email.strip().lower()
    if not e or "@" not in e or len(e) > 254:
        raise ValueError("无效邮箱")
    return e


def normalize_registration_email(email: str) -> str:
    e = normalize_email(email)
    domain = e.rsplit("@", 1)[1]
    if domain not in _QQ_REGISTRATION_DOMAINS:
        raise ValueError("仅支持 QQ 邮箱注册（@qq.com / @foxmail.com）")
    return e


def normalize_user_status(status: str) -> str:
    s = status.strip().lower()
    if s not in _USER_STATUS:
        raise ValueError("无效用户状态")
    return s
