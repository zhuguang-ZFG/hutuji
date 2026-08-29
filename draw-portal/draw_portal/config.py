from __future__ import annotations

import ipaddress
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

_LOOPBACK_HOST_NAMES = frozenset({"localhost"})


def _is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in _LOOPBACK_HOST_NAMES


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
    saved_preview_limit: int
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    email_code_ttl_seconds: int
    email_dev_expose_code: bool


@dataclass(frozen=True)
class AdminConfig:
    db_path: Path
    session_secret: str
    admin_password: str
    host: str
    port: int


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
    )


def load_admin_config() -> AdminConfig:
    db_raw = os.environ.get("DRAW_PORTAL_DB", "/var/lib/hutuji/draw-portal/data.db")
    admin_password = os.environ.get("DRAW_ADMIN_PASSWORD", "").strip()
    if not admin_password:
        raise RuntimeError("DRAW_ADMIN_PASSWORD 未配置")
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
    )
