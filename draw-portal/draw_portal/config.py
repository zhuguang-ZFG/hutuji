from __future__ import annotations

import ipaddress
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from draw_portal.internal_client import parse_internal_allow_cidrs

_LOOPBACK_HOST_NAMES = frozenset({"localhost"})


def load_provider_cipher_key() -> bytes | None:
    key_raw = os.environ.get("DRAW_PORTAL_PROVIDER_CIPHER_KEY", "").strip()
    if not key_raw:
        return None
    try:
        from cryptography.fernet import Fernet

        key_bytes = key_raw.encode("ascii")
        Fernet(key_bytes)
        return key_bytes
    except Exception as exc:
        raise RuntimeError("DRAW_PORTAL_PROVIDER_CIPHER_KEY 无效") from exc


def _parse_firmware_allowed_hosts(raw: str) -> tuple[str, ...]:
    out: list[str] = []
    for part in raw.split(","):
        text = part.strip().lower()
        if text:
            out.append(text)
    return tuple(out)


def _is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in _LOOPBACK_HOST_NAMES


def _load_mcp_internal_base_url() -> str:
    """网页文字生图调本机 MCP /internal/draw 的基址；只允许 loopback
    （密钥头绝不能出本机网卡）。"""
    raw = os.environ.get(
        "DRAW_PORTAL_MCP_INTERNAL_BASE_URL", "http://127.0.0.1:8300"
    ).rstrip("/")
    from urllib.parse import urlsplit

    host = (urlsplit(raw).hostname or "").lower()
    if not _is_loopback_host(host):
        raise RuntimeError(
            f"DRAW_PORTAL_MCP_INTERNAL_BASE_URL 只允许 loopback，当前 {raw!r}"
        )
    return raw

@dataclass(frozen=True)
class Config:
    db_path: Path
    data_dir: Path
    session_secret: str
    token_cipher_key: bytes
    xiaozhi_api_base: str
    public_base_url: str
    mcp_output_dir: Path
    host: str
    port: int
    bind_code_ttl_seconds: int
    bind_claim_ttl_seconds: int
    saved_preview_limit: int
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    email_code_ttl_seconds: int
    email_dev_expose_code: bool
    usage_ingest_secret: str
    conversation_retention_days: int
    conversation_max_content: int
    provider_cipher_key: bytes | None
    internal_allow_cidrs: tuple[object, ...]
    mcp_internal_base_url: str
    generate_daily_limit: int
    generate_timeout_seconds: int
    gfont_dir: str  # 奎享 .gfont 字库目录（文档导入排版用）；空 = 文档导入禁用
    # 标准笔顺目录（与 HUTUJI_STROKE_DIR 同源）；空 = 文档写字回落 gfont 连笔
    stroke_dir: str = ""
    # 照片精修已于 2026-09-10 切换程序化风格渲染（sketch_render），零付费凭据
    # 依赖；魔搭/TCB i2i 客户端模块在 hutuji_mcp 保留为备用通道，portal 不再注入。
    # 微信小程序（2026-09-07）：两项同配才启用 /api/auth/wx-login，空=禁用
    wx_appid: str = ""
    wx_secret: str = ""
    # 小程序文字聊天（2026-09-09）：/api/chat 日配额与 MCP /internal/chat 转发超时
    chat_daily_limit: int = 50
    chat_timeout_seconds: int = 30
    # 小派 OTA（2026-09-09）：catalog 路径空=禁用 check/upgrade；签名密钥空=不签下载 URL
    firmware_catalog_path: str = ""
    firmware_public_base: str = ""
    firmware_allowed_hosts: tuple[str, ...] = ()
    firmware_signing_secret: str = ""
    # 一次性订阅消息（2026-09-11）：模板 ID 空 = 通知禁用；字段键位与 MP
    # 后台建模板的取号顺序对齐（描述/状态/时间 → thing1/phrase2/time3）
    subscribe_template_id: str = ""
    subscribe_fields: dict[str, str] | None = None


@dataclass(frozen=True)
class AdminConfig:
    db_path: Path
    session_secret: str
    admin_password: str
    host: str
    port: int
    provider_cipher_key: bytes | None


def load_config() -> Config:
    db_raw = os.environ.get("DRAW_PORTAL_DB", "/var/lib/hutuji/draw-portal/data.db")
    data_raw = os.environ.get("DRAW_PORTAL_DATA_DIR", "/var/lib/hutuji/draw-portal")
    session_secret = os.environ.get("DRAW_PORTAL_SESSION_SECRET") or secrets.token_hex(32)

    key_raw = os.environ.get("DRAW_PORTAL_TOKEN_CIPHER_KEY", "").strip()
    if not key_raw:
        raise RuntimeError("DRAW_PORTAL_TOKEN_CIPHER_KEY 未配置（32 字节 urlsafe base64）")
    try:
        from cryptography.fernet import Fernet

        token_cipher_key = key_raw.encode("ascii")
        Fernet(token_cipher_key)
    except Exception as exc:
        raise RuntimeError("DRAW_PORTAL_TOKEN_CIPHER_KEY 无效") from exc

    host = os.environ.get("DRAW_PORTAL_HOST", "127.0.0.1")
    if not _is_loopback_host(host.lower()):
        raise RuntimeError(
            f"DRAW_PORTAL_HOST 只允许 loopback，当前 {host!r}；公网只经 nginx 反代 /draw-upload/"
        )

    port_raw = os.environ.get("DRAW_PORTAL_PORT", "8302")
    port = int(port_raw)
    if not 1 <= port <= 65535:
        raise RuntimeError(f"DRAW_PORTAL_PORT 必须在 1..65535，当前 {port}")

    mcp_out = os.environ.get(
        "DRAW_PORTAL_MCP_OUTPUT_DIR", "/opt/hutuji/mcp-server/output"
    )
    public_base = os.environ.get(
        "DRAW_PORTAL_PUBLIC_BASE_URL",
        os.environ.get("HUTUJI_PUBLIC_BASE_URL", "https://hutuji.donglicao.com"),
    ).rstrip("/")

    return Config(
        db_path=Path(db_raw),
        data_dir=Path(data_raw),
        session_secret=session_secret,
        token_cipher_key=token_cipher_key,
        xiaozhi_api_base=os.environ.get(
            "DRAW_PORTAL_XIAOZHI_API_BASE", "https://xiaozhi.me/api"
        ).rstrip("/"),
        public_base_url=public_base,
        mcp_output_dir=Path(mcp_out),
        host=host,
        port=port,
        bind_code_ttl_seconds=int(os.environ.get("DRAW_PORTAL_BIND_TTL_SECONDS", "600")),
        saved_preview_limit=int(os.environ.get("DRAW_PORTAL_SAVED_PREVIEW_LIMIT", "50")),
        # 认领窗口放宽到 30 分钟：claim 在配网成功后发出，设备还要重启+连家网+激活
        # （relay 重试环最长 ~10 分钟）才 announce，600s 会话 TTL 盖不住最坏路径。
        bind_claim_ttl_seconds=int(os.environ.get("DRAW_PORTAL_BIND_CLAIM_TTL_SECONDS", "1800")),
        smtp_host=os.environ.get("DRAW_PORTAL_SMTP_HOST", "").strip(),
        smtp_port=int(os.environ.get("DRAW_PORTAL_SMTP_PORT", "587")),
        smtp_user=os.environ.get("DRAW_PORTAL_SMTP_USER", "").strip(),
        smtp_password=os.environ.get("DRAW_PORTAL_SMTP_PASSWORD", "").strip(),
        smtp_from=os.environ.get("DRAW_PORTAL_SMTP_FROM", "").strip(),
        email_code_ttl_seconds=int(os.environ.get("DRAW_PORTAL_EMAIL_CODE_TTL_SECONDS", "600")),
        email_dev_expose_code=os.environ.get("DRAW_PORTAL_EMAIL_DEV_EXPOSE_CODE", "")
        .strip()
        .lower()
        in {"1", "true", "yes"},
        usage_ingest_secret=os.environ.get("DRAW_PORTAL_USAGE_INGEST_SECRET", "").strip(),
        wx_appid=os.environ.get("DRAW_PORTAL_WX_APPID", "").strip(),
        wx_secret=os.environ.get("DRAW_PORTAL_WX_SECRET", "").strip(),
        conversation_retention_days=int(
            os.environ.get("DRAW_PORTAL_CONVERSATION_RETENTION_DAYS", "90")
        ),
        conversation_max_content=int(
            os.environ.get("DRAW_PORTAL_CONVERSATION_MAX_CONTENT", "500")
        ),
        provider_cipher_key=load_provider_cipher_key(),
        internal_allow_cidrs=parse_internal_allow_cidrs(
            os.environ.get("DRAW_PORTAL_INTERNAL_ALLOW_CIDRS", "")
        ),
        mcp_internal_base_url=_load_mcp_internal_base_url(),
        # 默认 20（2026-09-10 用户拍板「文生图每天用户 20 次」；旧默认 10 过紧）
        generate_daily_limit=int(os.environ.get("DRAW_PORTAL_GENERATE_DAILY_LIMIT", "20")),
        generate_timeout_seconds=int(
            os.environ.get("DRAW_PORTAL_GENERATE_TIMEOUT_SECONDS", "300")
        ),
        gfont_dir=(
            os.environ.get("DRAW_PORTAL_GFONT_DIR")
            or os.environ.get("HUTUJI_GFONT_DIR", "")
        ).strip(),
        stroke_dir=(
            os.environ.get("DRAW_PORTAL_STROKE_DIR")
            or os.environ.get("HUTUJI_STROKE_DIR", "")
        ).strip(),
        chat_daily_limit=int(os.environ.get("DRAW_PORTAL_CHAT_DAILY_LIMIT", "50")),
        chat_timeout_seconds=int(os.environ.get("DRAW_PORTAL_CHAT_TIMEOUT_SECONDS", "30")),
        firmware_catalog_path=os.environ.get("DRAW_PORTAL_FIRMWARE_CATALOG_PATH", "").strip(),
        firmware_public_base=os.environ.get("DRAW_PORTAL_FIRMWARE_PUBLIC_BASE", "").strip()
        or public_base,
        firmware_allowed_hosts=_parse_firmware_allowed_hosts(
            os.environ.get(
                "DRAW_PORTAL_FIRMWARE_ALLOWED_HOSTS",
                "hutuji.donglicao.com",
            )
        ),
        firmware_signing_secret=os.environ.get(
            "DRAW_PORTAL_FIRMWARE_SIGNING_SECRET", ""
        ).strip(),
        subscribe_template_id=os.environ.get(
            "DRAW_PORTAL_WX_SUBSCRIBE_TEMPLATE_ID", ""
        ).strip(),
        subscribe_fields=_parse_subscribe_fields(
            os.environ.get("DRAW_PORTAL_WX_SUBSCRIBE_FIELDS", "")
        ),
    )


def _parse_subscribe_fields(raw: str) -> dict[str, str]:
    from draw_portal.subscribe_notify import parse_fields

    return parse_fields(raw)

def load_admin_config() -> AdminConfig:
    db_raw = os.environ.get("DRAW_PORTAL_DB", "/var/lib/hutuji/draw-portal/data.db")
    admin_password = os.environ.get("DRAW_ADMIN_PASSWORD", "").strip()
    session_secret = os.environ.get("DRAW_ADMIN_SESSION_SECRET") or secrets.token_hex(32)
    host = os.environ.get("DRAW_ADMIN_HOST", "127.0.0.1")
    if not _is_loopback_host(host.lower()):
        raise RuntimeError("DRAW_ADMIN_HOST 只允许 loopback")
    port = int(os.environ.get("DRAW_ADMIN_PORT", "8303"))
    return AdminConfig(
        db_path=Path(db_raw),
        session_secret=session_secret,
        admin_password=admin_password,
        host=host,
        port=port,
        provider_cipher_key=load_provider_cipher_key(),
    )
