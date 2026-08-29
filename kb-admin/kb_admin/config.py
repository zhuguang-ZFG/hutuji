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
    password: str
    db_path: Path
    kb_id: int
    preface_path: Path
    appendix_path: Path
    import_md_path: Path | None
    session_secret: str
    xiaozhi_token: str | None
    host: str
    port: int


def load_config() -> Config:
    password = os.environ.get("KB_ADMIN_PASSWORD", "")
    if not password:
        raise RuntimeError("KB_ADMIN_PASSWORD 未配置")

    db_raw = os.environ.get("KB_ADMIN_DB", "data/kb_admin.db")
    kb_id_raw = os.environ.get("KB_ADMIN_KB_ID", "")
    if not kb_id_raw:
        raise RuntimeError("KB_ADMIN_KB_ID 未配置")

    preface_raw = os.environ.get("KB_ADMIN_PREFACE_PATH", "")
    appendix_raw = os.environ.get("KB_ADMIN_APPENDIX_PATH", "")
    import_raw = os.environ.get("KB_ADMIN_IMPORT_MD", "").strip()

    session_secret = os.environ.get("KB_ADMIN_SESSION_SECRET") or secrets.token_hex(32)
    xiaozhi_token = os.environ.get("XIAOZHI_TOKEN") or None

    host = os.environ.get("KB_ADMIN_HOST", "127.0.0.1")
    if not _is_loopback_host(host.lower()):
        raise RuntimeError(
            f"KB_ADMIN_HOST 只允许 loopback，当前 {host!r}；公网只经 nginx 反代 /kb-admin/"
        )

    port_raw = os.environ.get("KB_ADMIN_PORT", "8301")
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise RuntimeError(f"KB_ADMIN_PORT 必须是整数，当前 {port_raw!r}") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError(f"KB_ADMIN_PORT 必须在 1..65535，当前 {port}")

    return Config(
        password=password,
        db_path=Path(db_raw),
        kb_id=int(kb_id_raw),
        preface_path=Path(preface_raw) if preface_raw else Path("data/preface.md"),
        appendix_path=Path(appendix_raw) if appendix_raw else Path("data/appendix.md"),
        import_md_path=Path(import_raw) if import_raw else None,
        session_secret=session_secret,
        xiaozhi_token=xiaozhi_token,
        host=host,
        port=port,
    )
