from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


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

    return Config(
        password=password,
        db_path=Path(db_raw),
        kb_id=int(kb_id_raw),
        preface_path=Path(preface_raw) if preface_raw else Path("data/preface.md"),
        appendix_path=Path(appendix_raw) if appendix_raw else Path("data/appendix.md"),
        import_md_path=Path(import_raw) if import_raw else None,
        session_secret=session_secret,
        xiaozhi_token=xiaozhi_token,
        host=os.environ.get("KB_ADMIN_HOST", "127.0.0.1"),
        port=int(os.environ.get("KB_ADMIN_PORT", "8301")),
    )
