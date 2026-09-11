from __future__ import annotations

import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from draw_portal.draw_limits import DRAW_LIMIT_KEYS, validate_draw_limit_overrides
from draw_portal.image_backends import (
    IMAGE_BACKEND_DB_KEYS,
    IMAGE_BACKEND_NAMES,
    default_image_backend_toggles,
)
from draw_portal.image_providers import (
    extra_json_from_db,
    extra_json_to_db,
    sanitize_key_for_admin,
    sanitize_provider_for_admin,
    validate_extra_json,
    validate_provider_base_url,
    validate_provider_model,
    validate_provider_name,
    validate_provider_type,
)
from draw_portal.mcp_settings import build_mcp_settings_payload
from draw_portal.provider_crypto import decrypt_api_key, encrypt_api_key
from draw_portal.models import (
    BindClaim,
    BindSession,
    DeviceBinding,
    DrawJob,
    EmailVerification,
    FirmwareOtaSession,
    SavedPreview,
    User,
    UserSummary,
    normalize_bind_code,
    normalize_email,
    normalize_mac,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    email_verified_at REAL,
    last_login_at REAL,
    banned_at REAL,
    ban_reason TEXT,
    admin_note TEXT
);

CREATE TABLE IF NOT EXISTS email_verifications (
    email TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    expires_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS bind_sessions (
    bind_code TEXT PRIMARY KEY,
    device_mac TEXT NOT NULL,
    device_token_enc TEXT NOT NULL,
    expires_at REAL NOT NULL,
    consumed_at REAL,
    plotter_sku TEXT NOT NULL DEFAULT 'paper'
);

CREATE TABLE IF NOT EXISTS bind_claims (
    device_mac TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    force INTEGER NOT NULL DEFAULT 0,
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS device_bindings (
    user_id TEXT PRIMARY KEY,
    device_mac TEXT NOT NULL UNIQUE,
    device_token_enc TEXT NOT NULL,
    bound_at REAL NOT NULL,
    last_push_at REAL,
    token_probe_ok INTEGER,
    machine_type TEXT NOT NULL DEFAULT 'paper'
);

CREATE TABLE IF NOT EXISTS firmware_ota_sessions (
    user_id TEXT PRIMARY KEY,
    device_mac TEXT NOT NULL,
    state TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    target_version TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS draw_jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL,
    source_name TEXT,
    basename TEXT,
    error_summary TEXT,
    created_at REAL NOT NULL,
    refined_status TEXT,
    refined_job_id TEXT
);

CREATE TABLE IF NOT EXISTS saved_previews (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    job_id TEXT,
    title TEXT NOT NULL,
    preview_path TEXT NOT NULL,
    gcode_path TEXT,
    saved_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_archive_log (
    saved_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    preview_path TEXT NOT NULL,
    tg_file_id TEXT NOT NULL,
    tg_message_id INTEGER NOT NULL,
    archived_at REAL NOT NULL,
    tg_removed_at REAL
);

CREATE INDEX IF NOT EXISTS idx_tg_archive_user
    ON telegram_archive_log(user_id, archived_at DESC);

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    target_user_id TEXT,
    detail TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    device_mac TEXT NOT NULL,
    user_id TEXT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    client_ts REAL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_mac_time
    ON conversation_events(device_mac, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_conversation_user_time
    ON conversation_events(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    user_id TEXT,
    device_mac TEXT,
    detail TEXT,
    ok INTEGER,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_type_time
    ON usage_events(event_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_usage_user_time
    ON usage_events(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS platform_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS image_providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider_type TEXT NOT NULL,
    base_url TEXT NOT NULL,
    model TEXT NOT NULL,
    extra_json TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    race_weight INTEGER NOT NULL DEFAULT 100,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS image_provider_keys (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES image_providers(id) ON DELETE CASCADE,
    label TEXT,
    api_key_enc TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    disabled_until REAL,
    last_used_at REAL,
    last_error_code INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_image_providers_enabled
    ON image_providers(enabled, sort_order);

CREATE INDEX IF NOT EXISTS idx_image_provider_keys_provider
    ON image_provider_keys(provider_id, enabled);

CREATE TABLE IF NOT EXISTS platform_config_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS engagement_events (
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (user_id, kind, key)
);

"""

_USER_MIGRATION_COLUMNS = (
    ("status", "TEXT NOT NULL DEFAULT 'active'"),
    ("email_verified_at", "REAL"),
    ("last_login_at", "REAL"),
    ("banned_at", "REAL"),
    ("ban_reason", "TEXT"),
    ("admin_note", "TEXT"),
    ("wx_openid", "TEXT"),
    ("nickname", "TEXT"),
    ("phone", "TEXT"),
    ("avatar_path", "TEXT"),
)
_DRAW_JOB_MIGRATION_COLUMNS = (
    ("refined_status", "TEXT"),
    ("refined_job_id", "TEXT"),
)
_SAVED_PREVIEW_TELEGRAM_COLUMNS = (
    # 2026-09-12 图库 TG 频道冷备份（PoC）：状态 NULL=未启用 / pending /
    # uploaded / failed；file_id 只属于签发 bot，删 bot 即失效（无恢复 SLA）。
    ("tg_state", "TEXT"),
    ("tg_file_id", "TEXT"),
    ("tg_message_id", "INTEGER"),
    ("tg_checked_at", "REAL"),
)



class Store:
    def __init__(self, db_path: Path, *, provider_cipher_key: bytes | None = None) -> None:
        self._db_path = db_path
        self._provider_cipher_key = provider_cipher_key
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        for name, ddl in _USER_MIGRATION_COLUMNS:
            if name not in cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
        conn.execute(
            """
            UPDATE users SET status = 'active'
            WHERE status IS NULL OR status = ''
            """
        )
        conn.execute(
            """
            UPDATE users SET email_verified_at = created_at
            WHERE email_verified_at IS NULL AND status = 'active'
            """
        )
        job_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(draw_jobs)").fetchall()
        }
        for name, ddl in _DRAW_JOB_MIGRATION_COLUMNS:
            if name not in job_cols:
                conn.execute(f"ALTER TABLE draw_jobs ADD COLUMN {name} {ddl}")
        session_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(bind_sessions)").fetchall()
        }
        if "plotter_sku" not in session_cols:
            conn.execute(
                "ALTER TABLE bind_sessions ADD COLUMN plotter_sku TEXT NOT NULL DEFAULT 'paper'"
            )
        binding_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(device_bindings)").fetchall()
        }
        # 2026-09-11 机型列：旧库缺省 'paper'（现役换纸机行为），由 announce/
        # status 探测按真值刷新。
        if "machine_type" not in binding_cols:
            conn.execute(
                "ALTER TABLE device_bindings ADD COLUMN machine_type TEXT NOT NULL DEFAULT 'paper'"
            )
        # 2026-09-12 TG 冷备份列（PoC，默认 NULL=未启用，不回填旧图）。
        preview_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(saved_previews)").fetchall()
        }
        for name, ddl in _SAVED_PREVIEW_TELEGRAM_COLUMNS:
            if name not in preview_cols:
                conn.execute(f"ALTER TABLE saved_previews ADD COLUMN {name} {ddl}")
        conn.commit()

    @staticmethod
    def _row_user(row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            email=row["email"],
            password_hash=row["password_hash"],
            created_at=row["created_at"],
            status=row["status"] or "active",
            email_verified_at=row["email_verified_at"],
            last_login_at=row["last_login_at"],
            banned_at=row["banned_at"],
            ban_reason=row["ban_reason"],
            admin_note=row["admin_note"],
            wx_openid=row["wx_openid"] if "wx_openid" in row.keys() else None,
            nickname=row["nickname"] if "nickname" in row.keys() else None,
            phone=row["phone"] if "phone" in row.keys() else None,
            avatar_path=row["avatar_path"] if "avatar_path" in row.keys() else None,
        )

    @staticmethod
    def _row_email_verification(row: sqlite3.Row) -> EmailVerification:
        return EmailVerification(
            email=row["email"],
            password_hash=row["password_hash"],
            code_hash=row["code_hash"],
            expires_at=row["expires_at"],
            attempts=row["attempts"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_bind_session(row: sqlite3.Row) -> BindSession:
        consumed = row["consumed_at"]
        return BindSession(
            bind_code=row["bind_code"],
            device_mac=row["device_mac"],
            device_token_enc=row["device_token_enc"],
            expires_at=row["expires_at"],
            consumed_at=consumed,
            plotter_sku=(
                row["plotter_sku"]
                if "plotter_sku" in row.keys() and (row["plotter_sku"] or "")
                else "paper"
            ),
        )

    @staticmethod
    def _row_bind_claim(row: sqlite3.Row) -> BindClaim:
        return BindClaim(
            device_mac=row["device_mac"],
            user_id=row["user_id"],
            force=bool(row["force"]),
            expires_at=row["expires_at"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_binding(row: sqlite3.Row) -> DeviceBinding:
        probe = row["token_probe_ok"]
        return DeviceBinding(
            user_id=row["user_id"],
            device_mac=row["device_mac"],
            device_token_enc=row["device_token_enc"],
            bound_at=row["bound_at"],
            last_push_at=row["last_push_at"],
            token_probe_ok=None if probe is None else bool(probe),
            machine_type=(
                row["machine_type"]
                if "machine_type" in row.keys() and (row["machine_type"] or "")
                else "paper"
            ),
        )

    @staticmethod
    def _row_firmware_ota_session(row: sqlite3.Row) -> FirmwareOtaSession:
        return FirmwareOtaSession(
            user_id=row["user_id"],
            device_mac=row["device_mac"],
            state=row["state"],
            progress=int(row["progress"] or 0),
            reason=row["reason"],
            target_version=row["target_version"],
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _row_draw_job(row: sqlite3.Row) -> DrawJob:
        return DrawJob(
            id=row["id"],
            user_id=row["user_id"],
            status=row["status"],
            source_name=row["source_name"],
            basename=row["basename"],
            error_summary=row["error_summary"],
            created_at=row["created_at"],
            refined_status=row["refined_status"],
            refined_job_id=row["refined_job_id"],
        )

    @staticmethod
    def _row_saved_preview(row: sqlite3.Row) -> SavedPreview:
        return SavedPreview(
            id=row["id"],
            user_id=row["user_id"],
            job_id=row["job_id"],
            title=row["title"],
            preview_path=row["preview_path"],
            gcode_path=row["gcode_path"],
            saved_at=row["saved_at"],
            tg_state=row["tg_state"],
            tg_file_id=row["tg_file_id"],
            tg_message_id=row["tg_message_id"],
        )

    def create_user(
        self,
        *,
        email: str,
        password_hash: str,
        email_verified_at: float | None = None,
        status: str = "active",
    ) -> User:
        now = time.time()
        user = User(
            id=str(uuid.uuid4()),
            email=email,
            password_hash=password_hash,
            created_at=now,
            status=status,
            email_verified_at=email_verified_at,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    id, email, password_hash, created_at, status, email_verified_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user.id,
                    user.email,
                    user.password_hash,
                    user.created_at,
                    user.status,
                    user.email_verified_at,
                ),
            )
            conn.commit()
        return user

    def upsert_email_verification(
        self,
        *,
        email: str,
        password_hash: str,
        code_hash: str,
        expires_at: float,
    ) -> EmailVerification:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO email_verifications (
                    email, password_hash, code_hash, expires_at, attempts, created_at
                ) VALUES (?, ?, ?, ?, 0, ?)
                ON CONFLICT(email) DO UPDATE SET
                    password_hash=excluded.password_hash,
                    code_hash=excluded.code_hash,
                    expires_at=excluded.expires_at,
                    attempts=0,
                    created_at=excluded.created_at
                """,
                (email, password_hash, code_hash, expires_at, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM email_verifications WHERE email = ?", (email,)
            ).fetchone()
        assert row is not None
        return self._row_email_verification(row)

    def get_email_verification(self, email: str) -> EmailVerification | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM email_verifications WHERE email = ?", (email,)
            ).fetchone()
        return self._row_email_verification(row) if row else None

    def increment_verification_attempts(self, email: str) -> int:
        with self._connect() as conn:
            conn.execute(
                "UPDATE email_verifications SET attempts = attempts + 1 WHERE email = ?",
                (email,),
            )
            conn.commit()
            row = conn.execute(
                "SELECT attempts FROM email_verifications WHERE email = ?", (email,)
            ).fetchone()
        return int(row["attempts"]) if row else 0

    def delete_email_verification(self, email: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM email_verifications WHERE email = ?", (email,))
            conn.commit()

    def get_user_by_openid(self, openid: str) -> User | None:
        """微信小程序登录：openid 唯一定位用户（2026-09-07）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE wx_openid = ?", (openid,)
            ).fetchone()
        return self._row_user(row) if row else None

    def create_wx_user(self, openid: str) -> User:
        """微信用户首登建档：email 用不可登录占位符（UNIQUE 约束需要值），
        密码哈希置为随机不可用值——微信用户不走邮箱密码路径。"""
        now = time.time()
        user = User(
            id=str(uuid.uuid4()),
            email=f"wx_{openid[:16]}@wx.local",
            password_hash=secrets.token_hex(32),
            created_at=now,
            status="active",
            email_verified_at=now,
            wx_openid=openid,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    id, email, password_hash, created_at, status,
                    email_verified_at, wx_openid
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user.id,
                    user.email,
                    user.password_hash,
                    user.created_at,
                    user.status,
                    user.email_verified_at,
                    user.wx_openid,
                ),
            )
            conn.commit()
        return user

    def set_user_profile(self, user_id: str, *, nickname: str | None, avatar_path: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET nickname = ?, avatar_path = ? WHERE id = ?",
                (nickname, avatar_path, user_id),
            )
            conn.commit()

    def set_user_phone(self, user_id: str, phone: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET phone = ? WHERE id = ?", (phone, user_id))
            conn.commit()

    def get_user_by_email(self, email: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return self._row_user(row) if row else None

    def get_user(self, user_id: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_user(row) if row else None

    def touch_last_login(self, user_id: str, *, when: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (when, user_id),
            )
            conn.commit()

    def set_user_banned(
        self, user_id: str, *, reason: str, when: float
    ) -> User | None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE users SET status = 'banned', banned_at = ?, ban_reason = ?
                WHERE id = ?
                """,
                (when, reason[:500], user_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_user(row) if row else None

    def set_user_active(self, user_id: str) -> User | None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE users SET status = 'active', banned_at = NULL, ban_reason = NULL
                WHERE id = ?
                """,
                (user_id,),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row_user(row) if row else None

    def set_admin_note(self, user_id: str, note: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET admin_note = ? WHERE id = ?",
                (note[:1000] if note else None, user_id),
            )
            conn.commit()

    def list_users(self, *, limit: int = 100, offset: int = 0) -> list[UserSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT u.*,
                    (SELECT COUNT(*) FROM draw_jobs j WHERE j.user_id = u.id) AS job_count,
                    (SELECT COUNT(*) FROM saved_previews s WHERE s.user_id = u.id) AS saved_count,
                    b.device_mac AS bound_mac
                FROM users u
                LEFT JOIN device_bindings b ON b.user_id = u.id
                ORDER BY u.created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        out: list[UserSummary] = []
        for row in rows:
            user = self._row_user(row)
            mac = row["bound_mac"]
            out.append(
                UserSummary(
                    user=user,
                    job_count=int(row["job_count"]),
                    saved_count=int(row["saved_count"]),
                    bound=bool(mac),
                    device_mac=mac,
                )
            )
        return out

    def count_users(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        return int(row["c"]) if row else 0

    # ---- 粘合性通知（§10.4.18）：ready 作品计数 / 连续打卡日期 / 已发登记 ----

    def count_ready_jobs(self, user_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM draw_jobs WHERE user_id = ? AND status = 'ready'",
                (user_id,),
            ).fetchone()
        return int(row["c"]) if row else 0

    def list_ready_job_days(self, user_id: str) -> set[str]:
        """有 ready 作品的本地日历日（ISO 日期串，去重）。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT date(created_at, 'unixepoch', 'localtime') AS day
                FROM draw_jobs
                WHERE user_id = ? AND status = 'ready'
                ORDER BY day
                """,
                (user_id,),
            ).fetchall()
        return {row["day"] for row in rows if row["day"] is not None}

    def record_engagement_event(self, user_id: str, kind: str, key: str, *, when: float) -> bool:
        """登记粘合性通知已发；首次登记返回 True，重复（或并发撞主键）返回 False。"""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO engagement_events (user_id, kind, key, created_at)"
                " VALUES (?, ?, ?, ?)",
                (user_id, kind, key, when),
            )
            conn.commit()
        return cur.rowcount == 1

    def platform_stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM users WHERE status = 'active'"
            ).fetchone()[0]
            banned = conn.execute(
                "SELECT COUNT(*) FROM users WHERE status = 'banned'"
            ).fetchone()[0]
            bindings = conn.execute("SELECT COUNT(*) FROM device_bindings").fetchone()[0]
            jobs = conn.execute("SELECT COUNT(*) FROM draw_jobs").fetchone()[0]
            saved = conn.execute("SELECT COUNT(*) FROM saved_previews").fetchone()[0]
            jobs_ready = conn.execute(
                "SELECT COUNT(*) FROM draw_jobs WHERE status = 'ready'"
            ).fetchone()[0]
            conversations = conn.execute(
                "SELECT COUNT(*) FROM conversation_events"
            ).fetchone()[0]
            usage = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
        return {
            "users_total": int(users),
            "users_active": int(active),
            "users_banned": int(banned),
            "bindings_total": int(bindings),
            "jobs_total": int(jobs),
            "jobs_ready": int(jobs_ready),
            "saved_total": int(saved),
            "conversation_events_total": int(conversations),
            "usage_events_total": int(usage),
        }

    def user_detail(self, user_id: str) -> dict[str, Any] | None:
        user = self.get_user(user_id)
        if user is None:
            return None
        binding = self.get_binding_for_user(user_id)
        with self._connect() as conn:
            jobs = conn.execute(
                """
                SELECT id, status, source_name, created_at, error_summary
                FROM draw_jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT 20
                """,
                (user_id,),
            ).fetchall()
            saved = conn.execute(
                """
                SELECT id, title, saved_at FROM saved_previews
                WHERE user_id = ? ORDER BY saved_at DESC LIMIT 20
                """,
                (user_id,),
            ).fetchall()
        return {
            "user": user,
            "binding": binding,
            "recent_jobs": [dict(r) for r in jobs],
            "recent_saved": [dict(r) for r in saved],
        }

    def log_admin_action(
        self, *, action: str, target_user_id: str | None, detail: str | None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO admin_audit_log (action, target_user_id, detail, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (action, target_user_id, detail, time.time()),
            )
            conn.commit()
    def upsert_bind_session(
        self,
        *,
        bind_code: str,
        device_mac: str,
        device_token_enc: str,
        expires_at: float,
        plotter_sku: str = "paper",
    ) -> BindSession:
        code = normalize_bind_code(bind_code)
        mac = normalize_mac(device_mac)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bind_sessions (
                    bind_code, device_mac, device_token_enc, expires_at, consumed_at, plotter_sku
                ) VALUES (?, ?, ?, ?, NULL, ?)
                ON CONFLICT(bind_code) DO UPDATE SET
                    device_mac=excluded.device_mac,
                    device_token_enc=excluded.device_token_enc,
                    expires_at=excluded.expires_at,
                    consumed_at=NULL,
                    plotter_sku=excluded.plotter_sku
                """,
                (code, mac, device_token_enc, expires_at, plotter_sku),
            )
            conn.commit()
        return self.get_bind_session(code)

    def get_bind_session(self, bind_code: str) -> BindSession | None:
        code = normalize_bind_code(bind_code)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM bind_sessions WHERE bind_code = ?", (code,)
            ).fetchone()
        return self._row_bind_session(row) if row else None

    def consume_bind_session(self, bind_code: str, *, now: float) -> BindSession | None:
        session = self.get_bind_session(bind_code)
        if session is None:
            return None
        if session.consumed_at is not None:
            return None
        if session.expires_at < now:
            return None
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE bind_sessions SET consumed_at = ?
                WHERE bind_code = ? AND consumed_at IS NULL AND expires_at >= ?
                """,
                (now, session.bind_code, now),
            )
            conn.commit()
            if cur.rowcount != 1:
                return None
        return BindSession(
            bind_code=session.bind_code,
            device_mac=session.device_mac,
            device_token_enc=session.device_token_enc,
            expires_at=session.expires_at,
            consumed_at=now,
        )

    def get_live_bind_session_for_mac(self, device_mac: str, *, now: float) -> BindSession | None:
        """claim 侧撮合：找该 MAC 最新一条未消费、未过期的 announce 会话。"""
        mac = normalize_mac(device_mac)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM bind_sessions
                WHERE device_mac = ? AND consumed_at IS NULL AND expires_at >= ?
                ORDER BY expires_at DESC LIMIT 1
                """,
                (mac, now),
            ).fetchone()
        return self._row_bind_session(row) if row else None

    def upsert_bind_claim(
        self,
        *,
        device_mac: str,
        user_id: str,
        force: bool,
        expires_at: float,
        created_at: float,
    ) -> BindClaim:
        mac = normalize_mac(device_mac)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bind_claims (device_mac, user_id, force, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(device_mac) DO UPDATE SET
                    user_id=excluded.user_id,
                    force=excluded.force,
                    expires_at=excluded.expires_at,
                    created_at=excluded.created_at
                """,
                (mac, user_id, int(force), expires_at, created_at),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM bind_claims WHERE device_mac = ?", (mac,)
            ).fetchone()
        assert row is not None
        return self._row_bind_claim(row)

    def get_bind_claim(self, device_mac: str, *, now: float) -> BindClaim | None:
        """读取未过期认领；过期行顺手删除（与产物 TTL 同口径：访问时清理，无后台线程）。"""
        mac = normalize_mac(device_mac)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM bind_claims WHERE device_mac = ?", (mac,)
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] < now:
                conn.execute("DELETE FROM bind_claims WHERE device_mac = ?", (mac,))
                conn.commit()
                return None
        return self._row_bind_claim(row)

    def delete_bind_claim(self, device_mac: str) -> bool:
        mac = normalize_mac(device_mac)
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM bind_claims WHERE device_mac = ?", (mac,))
            conn.commit()
            return cur.rowcount > 0

    def set_device_binding(
        self,
        *,
        user_id: str,
        device_mac: str,
        device_token_enc: str,
        bound_at: float,
        token_probe_ok: bool | None,
        machine_type: str = "paper",
    ) -> DeviceBinding:
        mac = normalize_mac(device_mac)
        probe_val = None if token_probe_ok is None else int(token_probe_ok)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO device_bindings (
                    user_id, device_mac, device_token_enc, bound_at, last_push_at,
                    token_probe_ok, machine_type
                ) VALUES (?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    device_mac=excluded.device_mac,
                    device_token_enc=excluded.device_token_enc,
                    bound_at=excluded.bound_at,
                    last_push_at=NULL,
                    token_probe_ok=excluded.token_probe_ok,
                    machine_type=excluded.machine_type
                """,
                (user_id, mac, device_token_enc, bound_at, probe_val, machine_type),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM device_bindings WHERE user_id = ?", (user_id,)
            ).fetchone()
        assert row is not None
        return self._row_binding(row)

    def set_binding_machine_type(self, user_id: str, machine_type: str) -> None:
        """按设备上报刷新机型词汇；非法值一律 ValueError（白名单纪律）。"""
        if machine_type not in ("nopaper", "paper"):
            raise ValueError(f"未知机型：{machine_type!r}")
        with self._connect() as conn:
            conn.execute(
                "UPDATE device_bindings SET machine_type = ? WHERE user_id = ?",
                (machine_type, user_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM device_bindings WHERE user_id = ?", (user_id,)
            ).fetchone()
        assert row is not None
        return self._row_binding(row)

    def get_binding_for_user(self, user_id: str) -> DeviceBinding | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM device_bindings WHERE user_id = ?", (user_id,)
            ).fetchone()
        return self._row_binding(row) if row else None

    def delete_binding_for_user(self, user_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM device_bindings WHERE user_id = ?", (user_id,))
            conn.commit()
            return cur.rowcount > 0

    def delete_binding_for_mac(self, device_mac: str) -> bool:
        """按设备 MAC 删绑定（不限用户）——仅 bind_complete 的 force 换绑路径调用，
        前置授权证据 = 调用方持有未消费、未过期的设备屏显 bind_code（持有即在场证明）。"""
        mac = normalize_mac(device_mac)
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM device_bindings WHERE device_mac = ?", (mac,))
            conn.commit()
            return cur.rowcount > 0

    def update_binding_probe(self, user_id: str, *, ok: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE device_bindings SET token_probe_ok = ? WHERE user_id = ?",
                (int(ok), user_id),
            )
            conn.commit()

    def binding_status_dict(self, binding: DeviceBinding | None) -> dict:
        if binding is None:
            return {"bound": False}
        mac = binding.device_mac
        return {
            "bound": True,
            "device_mac": f"{mac[:8]}…{mac[-2:]}",
            "bound_at": binding.bound_at,
            "token_probe_ok": binding.token_probe_ok,
        }

    def get_firmware_ota_session(self, user_id: str) -> FirmwareOtaSession | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM firmware_ota_sessions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return self._row_firmware_ota_session(row) if row else None

    def upsert_firmware_ota_session(
        self,
        *,
        user_id: str,
        device_mac: str,
        state: str,
        progress: int = 0,
        reason: str | None = None,
        target_version: str | None = None,
        updated_at: float | None = None,
    ) -> FirmwareOtaSession:
        when = time.time() if updated_at is None else updated_at
        mac = normalize_mac(device_mac)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO firmware_ota_sessions (
                    user_id, device_mac, state, progress, reason, target_version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    device_mac = excluded.device_mac,
                    state = excluded.state,
                    progress = excluded.progress,
                    reason = excluded.reason,
                    target_version = excluded.target_version,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    mac,
                    state,
                    int(progress),
                    reason,
                    target_version,
                    when,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM firmware_ota_sessions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        assert row is not None
        return self._row_firmware_ota_session(row)

    def list_device_bindings(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """运营一览：MAC ↔ user ↔ phone ↔ last_push / token_probe（不含 token 本体）。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT b.device_mac, b.user_id, b.bound_at, b.last_push_at, b.token_probe_ok,
                       u.email, u.phone, u.nickname, u.status AS user_status
                FROM device_bindings b
                JOIN users u ON u.id = b.user_id
                ORDER BY COALESCE(b.last_push_at, b.bound_at) DESC
                LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "device_mac": row["device_mac"],
                    "user_id": row["user_id"],
                    "email": row["email"],
                    "phone": row["phone"],
                    "nickname": row["nickname"],
                    "user_status": row["user_status"],
                    "bound_at": row["bound_at"],
                    "last_push_at": row["last_push_at"],
                    "token_probe_ok": (
                        None
                        if row["token_probe_ok"] is None
                        else bool(row["token_probe_ok"])
                    ),
                }
            )
        return out

    def get_ops_notes(self) -> dict[str, str]:
        raw = self._read_platform_settings(
            ("ops_weapp_version", "ops_firmware_note", "ops_shipping_note")
        )
        return {
            "weapp_version": raw.get("ops_weapp_version", ""),
            "firmware_note": raw.get("ops_firmware_note", ""),
            "shipping_note": raw.get("ops_shipping_note", ""),
        }

    def set_ops_notes(
        self,
        *,
        weapp_version: str | None = None,
        firmware_note: str | None = None,
        shipping_note: str | None = None,
    ) -> dict[str, str]:
        values: dict[str, str | None] = {}
        if weapp_version is not None:
            values["ops_weapp_version"] = (weapp_version or "").strip()[:80] or None
        if firmware_note is not None:
            values["ops_firmware_note"] = (firmware_note or "").strip()[:500] or None
        if shipping_note is not None:
            values["ops_shipping_note"] = (shipping_note or "").strip()[:500] or None
        if values:
            self._write_platform_settings(
                allowed_keys=frozenset(values.keys()),
                values=values,
            )
        return self.get_ops_notes()

    def create_draw_job(self, *, user_id: str, source_name: str | None) -> DrawJob:
        job = DrawJob(
            id=str(uuid.uuid4()),
            user_id=user_id,
            status="processing",
            source_name=source_name,
            basename=None,
            error_summary=None,
            created_at=time.time(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO draw_jobs (
                    id, user_id, status, source_name, basename, error_summary, created_at
                ) VALUES (?, ?, ?, ?, NULL, NULL, ?)
                """,
                (job.id, job.user_id, job.status, job.source_name, job.created_at),
            )
            conn.commit()
        return job

    def mark_draw_job_ready(self, job_id: str, *, basename: str) -> DrawJob | None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE draw_jobs SET status = 'ready', basename = ?, error_summary = NULL
                WHERE id = ?
                """,
                (basename, job_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM draw_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_draw_job(row) if row else None

    def mark_draw_job_failed(self, job_id: str, *, error_summary: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE draw_jobs SET status = 'failed', error_summary = ?
                WHERE id = ?
                """,
                (error_summary[:500], job_id),
            )
            conn.commit()

    def set_draw_job_refined(
        self, job_id: str, *, status: str, refined_job_id: str | None = None
    ) -> None:
        """照片两段式：pending → ready（回填精修 job id）/ failed。只写 refined_* 两列。"""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE draw_jobs
                SET refined_status = ?,
                    refined_job_id = COALESCE(?, refined_job_id)
                WHERE id = ?
                """,
                (status, refined_job_id, job_id),
            )
            conn.commit()

    def get_draw_job(self, job_id: str, *, user_id: str) -> DrawJob | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM draw_jobs WHERE id = ? AND user_id = ?",
                (job_id, user_id),
            ).fetchone()
        return self._row_draw_job(row) if row else None

    def get_draw_job_any(self, job_id: str) -> DrawJob | None:
        """分享令牌校验后的无属主读取（share 端点专用；令牌已盖到 job_id）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM draw_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._row_draw_job(row) if row else None

    def update_binding_last_push(self, user_id: str, *, when: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE device_bindings SET last_push_at = ? WHERE user_id = ?",
                (when, user_id),
            )
            conn.commit()

    def update_binding_token(self, user_id: str, *, device_token_enc: str) -> None:
        """push 401 自愈重铸 messaging token 后回写（announce 上报值不可信，
        2026-09-07 绑定表惊现 test-token 占位导致全量推送 401）。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE device_bindings SET device_token_enc = ? WHERE user_id = ?",
                (device_token_enc, user_id),
            )
            conn.commit()

    def create_saved_preview(
        self,
        *,
        user_id: str,
        job_id: str | None,
        title: str,
        preview_path: str,
        gcode_path: str | None,
        tg_pending: bool = False,
    ) -> SavedPreview:
        saved = SavedPreview(
            id=str(uuid.uuid4()),
            user_id=user_id,
            job_id=job_id,
            title=title,
            preview_path=preview_path,
            gcode_path=gcode_path,
            saved_at=time.time(),
            tg_state="pending" if tg_pending else None,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO saved_previews (
                    id, user_id, job_id, title, preview_path, gcode_path, saved_at,
                    tg_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    saved.id,
                    saved.user_id,
                    saved.job_id,
                    saved.title,
                    saved.preview_path,
                    saved.gcode_path,
                    saved.saved_at,
                    saved.tg_state,
                ),
            )
            conn.commit()
        return saved

    def mark_preview_telegram(
        self,
        saved_id: str,
        *,
        state: str,
        file_id: str | None,
        message_id: int | None,
    ) -> bool:
        """归档 worker 回写：只认 in ('pending','uploaded','failed')，防脏值。
        返回 False = 行已被删除（回写落空），worker 据此撤回频道副本。"""
        if state not in {"pending", "uploaded", "failed"}:
            return False
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE saved_previews
                SET tg_state = ?, tg_file_id = ?, tg_message_id = ?, tg_checked_at = ?
                WHERE id = ?
                """,
                (state, file_id, message_id, time.time(), saved_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def append_archive_log(
        self,
        *,
        saved_id: str,
        user_id: str,
        title: str,
        preview_path: str,
        tg_file_id: str,
        tg_message_id: int,
    ) -> bool:
        """上传成功即记台账（分用户召回的唯一凭据）；行删除不删台账。
        INSERT 以 saved_previews 行仍存在为守卫（单语句原子）——用户在上传
        期间删行时这里落空，worker 据此撤回频道副本，不留下可复活的台账。"""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO telegram_archive_log (
                    saved_id, user_id, title, preview_path,
                    tg_file_id, tg_message_id, archived_at, tg_removed_at
                )
                SELECT ?, ?, ?, ?, ?, ?, ?, NULL
                WHERE EXISTS (SELECT 1 FROM saved_previews WHERE id = ?)
                ON CONFLICT(saved_id) DO UPDATE SET
                    tg_file_id = excluded.tg_file_id,
                    tg_message_id = excluded.tg_message_id,
                    tg_removed_at = NULL
                """,
                (
                    saved_id, user_id, title, preview_path,
                    tg_file_id, tg_message_id, time.time(), saved_id,
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    def list_pending_previews(self) -> list[SavedPreview]:
        """启动恢复用：全库 tg_state='pending'（进程重启丢队列的行）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM saved_previews WHERE tg_state = 'pending' ORDER BY saved_at ASC"
            ).fetchall()
        return [self._row_saved_preview(r) for r in rows]

    def list_retryable_previews(self, user_id: str) -> list[SavedPreview]:
        """人工重试用：该用户 pending/failed 且仍须归档的行。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM saved_previews
                WHERE user_id = ? AND tg_state IN ('pending', 'failed')
                ORDER BY saved_at ASC
                """,
                (user_id,),
            ).fetchall()
        return [self._row_saved_preview(r) for r in rows]

    def mark_archive_log_removed(self, saved_id: str) -> None:
        """用户主动删除 → 频道副本撤回 + 台账标记；召回时跳过该行。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE telegram_archive_log SET tg_removed_at = ? WHERE saved_id = ?",
                (time.time(), saved_id),
            )
            conn.commit()

    def list_archive_log_for_user(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM telegram_archive_log
                WHERE user_id = ? AND tg_removed_at IS NULL
                ORDER BY archived_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def restore_saved_preview(
        self,
        *,
        user_id: str,
        saved_id: str,
        title: str,
        preview_path: str,
        saved_at: float,
        tg_file_id: str,
        tg_message_id: int,
    ) -> None:
        """召回重建行：沿用原 saved_id（台账主键连贯），无 gcode 副本不可再发送。"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO saved_previews (
                    id, user_id, job_id, title, preview_path, gcode_path, saved_at,
                    tg_state, tg_file_id, tg_message_id, tg_checked_at
                ) VALUES (?, ?, NULL, ?, ?, NULL, ?, 'uploaded', ?, ?, ?)
                """,
                (saved_id, user_id, title, preview_path, saved_at, tg_file_id, tg_message_id, time.time()),
            )
            conn.commit()

    def list_saved_previews(self, user_id: str) -> list[SavedPreview]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM saved_previews WHERE user_id = ?
                ORDER BY saved_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [self._row_saved_preview(r) for r in rows]

    def get_saved_preview(self, saved_id: str, *, user_id: str) -> SavedPreview | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM saved_previews WHERE id = ? AND user_id = ?",
                (saved_id, user_id),
            ).fetchone()
        return self._row_saved_preview(row) if row else None

    def delete_saved_preview(self, saved_id: str, *, user_id: str) -> SavedPreview | None:
        item = self.get_saved_preview(saved_id, user_id=user_id)
        if item is None:
            return None
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM saved_previews WHERE id = ? AND user_id = ?",
                (saved_id, user_id),
            )
            conn.commit()
        return item

    def trim_saved_previews(self, user_id: str, *, limit: int) -> list[str]:
        if limit < 1:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, preview_path, gcode_path FROM saved_previews
                WHERE user_id = ? ORDER BY saved_at DESC
                """,
                (user_id,),
            ).fetchall()
            if len(rows) <= limit:
                return []
            doomed = rows[limit:]
            paths: list[str] = []
            for row in doomed:
                conn.execute(
                    "DELETE FROM saved_previews WHERE id = ? AND user_id = ?",
                    (row["id"], user_id),
                )
                paths.append(row["preview_path"])
                if row["gcode_path"]:
                    paths.append(row["gcode_path"])
            conn.commit()
        return paths

    def get_user_id_for_mac(self, device_mac: str) -> str | None:
        mac = normalize_mac(device_mac)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id FROM device_bindings WHERE device_mac = ?",
                (mac,),
            ).fetchone()
        return str(row["user_id"]) if row else None

    def purge_conversation_before(self, cutoff_ts: float) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM conversation_events WHERE created_at < ?",
                (cutoff_ts,),
            )
            conn.commit()
            return int(cur.rowcount)

    def insert_conversation_event(
        self,
        *,
        dedupe_key: str,
        device_mac: str,
        user_id: str | None,
        session_id: str,
        role: str,
        content: str,
        client_ts: float | None,
        created_at: float,
    ) -> bool:
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO conversation_events (
                        dedupe_key, device_mac, user_id, session_id, role,
                        content, client_ts, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dedupe_key,
                        device_mac,
                        user_id,
                        session_id,
                        role,
                        content,
                        client_ts,
                        created_at,
                    ),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def insert_usage_event(
        self,
        *,
        source: str,
        event_type: str,
        user_id: str | None = None,
        device_mac: str | None = None,
        detail: str | None = None,
        ok: bool | None = None,
        created_at: float | None = None,
    ) -> None:
        when = created_at if created_at is not None else time.time()
        mac = normalize_mac(device_mac) if device_mac else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_events (
                    source, event_type, user_id, device_mac, detail, ok, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    event_type,
                    user_id,
                    mac,
                    (detail[:200] if detail else None),
                    None if ok is None else int(ok),
                    when,
                ),
            )
            conn.commit()

    def list_conversation_sessions(
        self,
        *,
        user_id: str | None = None,
        device_mac: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        clauses: list[str] = []
        params: list[Any] = []
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if device_mac:
            clauses.append("device_mac = ?")
            params.append(normalize_mac(device_mac))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT session_id,
                       MAX(created_at) AS last_at,
                       MIN(created_at) AS first_at,
                       COUNT(*) AS message_count,
                       device_mac
                FROM conversation_events
                {where}
                GROUP BY session_id, device_mac
                ORDER BY last_at DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_conversation_messages(
        self,
        *,
        session_id: str,
        user_id: str | None = None,
        device_mac: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        clauses = ["session_id = ?"]
        params: list[Any] = [session_id]
        if user_id:
            clauses.append("user_id = ?")
            params.append(user_id)
        if device_mac:
            clauses.append("device_mac = ?")
            params.append(normalize_mac(device_mac))
        where = " AND ".join(clauses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT role, content, client_ts, created_at, device_mac
                FROM conversation_events
                WHERE {where}
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_conversation_export_rows(
        self,
        *,
        user_id: str,
        session_id: str | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 20000))
        clauses = ["user_id = ?"]
        params: list[Any] = [user_id]
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        where = " AND ".join(clauses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT session_id, role, content, client_ts, created_at, device_mac
                FROM conversation_events
                WHERE {where}
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_draw_limit_overrides(self) -> dict[str, int]:
        return self._parse_int_platform_settings(DRAW_LIMIT_KEYS)

    def get_image_backend_toggles(self) -> dict[str, bool]:
        toggles = default_image_backend_toggles()
        raw = self._read_platform_settings(tuple(IMAGE_BACKEND_DB_KEYS.values()))
        for name in IMAGE_BACKEND_NAMES:
            db_key = IMAGE_BACKEND_DB_KEYS[name]
            if db_key in raw:
                toggles[name] = raw[db_key].strip() not in ("0", "false", "False")
        return toggles

    def get_mcp_settings_payload(self) -> dict[str, object]:
        raw = self._read_platform_settings(
            tuple(DRAW_LIMIT_KEYS) + tuple(IMAGE_BACKEND_DB_KEYS.values())
        )
        limits: dict[str, int] = {}
        for key in DRAW_LIMIT_KEYS:
            if key not in raw:
                continue
            try:
                limits[key] = int(raw[key])
            except ValueError:
                continue
        image_raw = {
            db_key: raw[db_key]
            for name, db_key in IMAGE_BACKEND_DB_KEYS.items()
            if db_key in raw
        }
        return build_mcp_settings_payload(
            draw_limit_overrides=limits,
            image_backend_raw=image_raw,
            image_racers=self.list_image_racers_for_mcp(),
            revision=self.get_image_provider_revision(),
        )

    def _require_provider_cipher(self) -> bytes:
        if not self._provider_cipher_key:
            raise RuntimeError("DRAW_PORTAL_PROVIDER_CIPHER_KEY 未配置")
        return self._provider_cipher_key

    def get_image_provider_revision(self) -> int:
        raw = self._read_platform_settings(("image_providers_revision",))
        if "image_providers_revision" not in raw:
            return 0
        try:
            return int(raw["image_providers_revision"])
        except ValueError:
            return 0

    def _bump_image_provider_revision(self, conn: sqlite3.Connection) -> int:
        rev = self.get_image_provider_revision() + 1
        now = time.time()
        conn.execute(
            "DELETE FROM platform_settings WHERE key = ?",
            ("image_providers_revision",),
        )
        conn.execute(
            """
            INSERT INTO platform_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            ("image_providers_revision", str(rev), now),
        )
        conn.execute(
            """
            INSERT INTO platform_config_revisions (kind, revision, created_at)
            VALUES (?, ?, ?)
            """,
            ("image_providers", rev, now),
        )
        return rev

    def list_image_providers_admin(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM image_providers
                ORDER BY sort_order ASC, created_at ASC
                """
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            keys = self.list_image_provider_keys_admin(str(row["id"]))
            out.append(sanitize_provider_for_admin(dict(row), keys=keys))
        return out

    def get_image_provider_admin(self, provider_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM image_providers WHERE id = ?",
                (provider_id,),
            ).fetchone()
        if row is None:
            return None
        keys = self.list_image_provider_keys_admin(provider_id)
        return sanitize_provider_for_admin(dict(row), keys=keys)

    def list_image_provider_keys_admin(self, provider_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM image_provider_keys
                WHERE provider_id = ?
                ORDER BY created_at ASC
                """,
                (provider_id,),
            ).fetchall()
        return [sanitize_key_for_admin(dict(r)) for r in rows]

    def create_image_provider(
        self,
        *,
        name: str,
        provider_type: str,
        base_url: str,
        model: str,
        extra: dict[str, Any] | None = None,
        enabled: bool = True,
        race_weight: int = 100,
        sort_order: int = 0,
    ) -> dict[str, Any]:
        self._require_provider_cipher()
        ptype = validate_provider_type(provider_type)
        now = time.time()
        provider_id = uuid.uuid4().hex
        row = {
            "name": validate_provider_name(name),
            "provider_type": ptype,
            "base_url": validate_provider_base_url(base_url, provider_type=ptype),
            "model": validate_provider_model(model),
            "extra_json": extra_json_to_db(validate_extra_json(extra)),
            "enabled": int(enabled),
            "race_weight": max(1, min(int(race_weight), 1000)),
            "sort_order": int(sort_order),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO image_providers (
                    id, name, provider_type, base_url, model, extra_json,
                    enabled, race_weight, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider_id,
                    row["name"],
                    row["provider_type"],
                    row["base_url"],
                    row["model"],
                    row["extra_json"],
                    row["enabled"],
                    row["race_weight"],
                    row["sort_order"],
                    now,
                    now,
                ),
            )
            self._bump_image_provider_revision(conn)
            conn.commit()
        created = self.get_image_provider_admin(provider_id)
        assert created is not None
        return created

    def update_image_provider(
        self,
        provider_id: str,
        *,
        patch: dict[str, Any],
    ) -> dict[str, Any] | None:
        current = self.get_image_provider_admin(provider_id)
        if current is None:
            return None
        ptype = validate_provider_type(
            str(patch.get("provider_type", current["provider_type"]))
        )
        now = time.time()
        name = validate_provider_name(str(patch.get("name", current["name"])))
        base_url = validate_provider_base_url(
            str(patch.get("base_url", current["base_url"])),
            provider_type=ptype,
        )
        model = validate_provider_model(str(patch.get("model", current["model"])))
        extra = validate_extra_json(patch.get("extra", current.get("extra")))
        enabled = bool(patch.get("enabled", current["enabled"]))
        race_weight = max(1, min(int(patch.get("race_weight", current["race_weight"])), 1000))
        sort_order = int(patch.get("sort_order", current["sort_order"]))
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE image_providers SET
                    name = ?, provider_type = ?, base_url = ?, model = ?,
                    extra_json = ?, enabled = ?, race_weight = ?, sort_order = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    name,
                    ptype,
                    base_url,
                    model,
                    extra_json_to_db(extra),
                    int(enabled),
                    race_weight,
                    sort_order,
                    now,
                    provider_id,
                ),
            )
            self._bump_image_provider_revision(conn)
            conn.commit()
        return self.get_image_provider_admin(provider_id)

    def delete_image_provider(self, provider_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM image_providers WHERE id = ?",
                (provider_id,),
            )
            if cur.rowcount == 0:
                return False
            self._bump_image_provider_revision(conn)
            conn.commit()
        return True

    def add_image_provider_key(
        self,
        provider_id: str,
        *,
        api_key: str,
        label: str | None = None,
    ) -> dict[str, Any] | None:
        if self.get_image_provider_admin(provider_id) is None:
            return None
        cipher = self._require_provider_cipher()
        plain = (api_key or "").strip()
        if not plain:
            raise ValueError("api_key 不能为空")
        now = time.time()
        key_id = uuid.uuid4().hex
        enc = encrypt_api_key(cipher, plain)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO image_provider_keys (
                    id, provider_id, label, api_key_enc, enabled,
                    disabled_until, last_used_at, last_error_code,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, NULL, NULL, NULL, ?, ?)
                """,
                (
                    key_id,
                    provider_id,
                    (label or "")[:80] or None,
                    enc,
                    now,
                    now,
                ),
            )
            self._bump_image_provider_revision(conn)
            conn.commit()
        rows = self.list_image_provider_keys_admin(provider_id)
        for row in rows:
            if row["id"] == key_id:
                return row
        return None

    def update_image_provider_key(
        self,
        key_id: str,
        *,
        enabled: bool | None = None,
        api_key: str | None = None,
        label: str | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM image_provider_keys WHERE id = ?",
                (key_id,),
            ).fetchone()
        if row is None:
            return None
        now = time.time()
        enc = str(row["api_key_enc"])
        if api_key is not None:
            plain = api_key.strip()
            if not plain:
                raise ValueError("api_key 不能为空")
            enc = encrypt_api_key(self._require_provider_cipher(), plain)
        new_enabled = int(row["enabled"]) if enabled is None else int(enabled)
        new_label = row["label"] if label is None else (label[:80] or None)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE image_provider_keys SET
                    label = ?,
                    api_key_enc = ?,
                    enabled = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (new_label, enc, new_enabled, now, key_id),
            )
            self._bump_image_provider_revision(conn)
            conn.commit()
        provider_id = str(row["provider_id"])
        for item in self.list_image_provider_keys_admin(provider_id):
            if item["id"] == key_id:
                return item
        return None

    def delete_image_provider_key(self, key_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM image_provider_keys WHERE id = ?",
                (key_id,),
            )
            if cur.rowcount == 0:
                return False
            self._bump_image_provider_revision(conn)
            conn.commit()
        return True

    def list_image_racers_for_mcp(self) -> list[dict[str, Any]]:
        if not self._provider_cipher_key:
            return []
        now = time.time()
        cipher = self._provider_cipher_key
        with self._connect() as conn:
            providers = conn.execute(
                """
                SELECT * FROM image_providers
                WHERE enabled = 1
                ORDER BY sort_order ASC, created_at ASC
                """
            ).fetchall()
            out: list[dict[str, Any]] = []
            for prow in providers:
                keys = conn.execute(
                    """
                    SELECT * FROM image_provider_keys
                    WHERE provider_id = ? AND enabled = 1
                    ORDER BY created_at ASC
                    """,
                    (str(prow["id"]),),
                ).fetchall()
                api_keys: list[str] = []
                for krow in keys:
                    if krow["disabled_until"] is not None and float(krow["disabled_until"]) > now:
                        continue
                    try:
                        api_keys.append(
                            decrypt_api_key(cipher, str(krow["api_key_enc"]))
                        )
                    except ValueError:
                        continue
                if not api_keys and str(prow["provider_type"]) != "custom_post":
                    continue
                extra = extra_json_from_db(prow["extra_json"])
                out.append(
                    {
                        "id": str(prow["id"]),
                        "name": str(prow["name"]),
                        "provider_type": str(prow["provider_type"]),
                        "base_url": str(prow["base_url"]),
                        "model": str(prow["model"]),
                        "extra": extra,
                        "race_weight": int(prow["race_weight"] or 100),
                        "api_keys": api_keys,
                    }
                )
        return out

    def _read_platform_settings(self, keys: tuple[str, ...]) -> dict[str, str]:
        if not keys:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT key, value FROM platform_settings
                WHERE key IN ({})
                """.format(",".join("?" * len(keys))),
                keys,
            ).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def _parse_int_platform_settings(self, keys: tuple[str, ...]) -> dict[str, int]:
        raw = self._read_platform_settings(keys)
        out: dict[str, int] = {}
        for key in keys:
            if key not in raw:
                continue
            try:
                out[key] = int(raw[key])
            except ValueError:
                continue
        return out

    def _write_platform_settings(
        self,
        *,
        allowed_keys: frozenset[str],
        values: dict[str, str | None],
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            for key, value in values.items():
                if key not in allowed_keys:
                    continue
                conn.execute("DELETE FROM platform_settings WHERE key = ?", (key,))
                if value is not None:
                    conn.execute(
                        """
                        INSERT INTO platform_settings (key, value, updated_at)
                        VALUES (?, ?, ?)
                        """,
                        (key, value, now),
                    )
            conn.commit()

    def apply_draw_limit_patch(self, patch: dict[str, int | None]) -> dict[str, int]:
        current = self.get_draw_limit_overrides()
        for key in DRAW_LIMIT_KEYS:
            if key not in patch:
                continue
            value = patch[key]
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        validated = validate_draw_limit_overrides(current)
        writes: dict[str, str | None] = {
            key: (str(validated[key]) if key in validated else None)
            for key in DRAW_LIMIT_KEYS
        }
        self._write_platform_settings(
            allowed_keys=frozenset(DRAW_LIMIT_KEYS),
            values=writes,
        )
        return validated

    def apply_image_backend_patch(self, patch: dict[str, bool | None]) -> dict[str, bool]:
        toggles = self.get_image_backend_toggles()
        writes: dict[str, str | None] = {}
        for name in IMAGE_BACKEND_NAMES:
            if name not in patch:
                continue
            value = patch[name]
            db_key = IMAGE_BACKEND_DB_KEYS[name]
            if value is None:
                toggles[name] = True
                writes[db_key] = None
            else:
                toggles[name] = bool(value)
                writes[db_key] = "1" if toggles[name] else "0"
        if writes:
            self._write_platform_settings(
                allowed_keys=frozenset(IMAGE_BACKEND_DB_KEYS.values()),
                values=writes,
            )
        return toggles

    def usage_counts_since(self, since_ts: float) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_type, source, COUNT(*) AS count
                FROM usage_events
                WHERE created_at >= ?
                GROUP BY event_type, source
                ORDER BY count DESC
                """,
                (since_ts,),
            ).fetchall()
        return [dict(r) for r in rows]

    def usage_counts_for_user_since(
        self, user_id: str, since_ts: float
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_type, source, COUNT(*) AS count
                FROM usage_events
                WHERE user_id = ? AND created_at >= ?
                GROUP BY event_type, source
                ORDER BY count DESC
                """,
                (user_id, since_ts),
            ).fetchall()
        return [dict(r) for r in rows]

