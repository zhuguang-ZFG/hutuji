from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from draw_portal.models import (
    BindSession,
    DeviceBinding,
    DrawJob,
    EmailVerification,
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
    consumed_at REAL
);

CREATE TABLE IF NOT EXISTS device_bindings (
    user_id TEXT PRIMARY KEY,
    device_mac TEXT NOT NULL UNIQUE,
    device_token_enc TEXT NOT NULL,
    bound_at REAL NOT NULL,
    last_push_at REAL,
    token_probe_ok INTEGER
);

CREATE TABLE IF NOT EXISTS draw_jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL,
    source_name TEXT,
    basename TEXT,
    error_summary TEXT,
    created_at REAL NOT NULL
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

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    target_user_id TEXT,
    detail TEXT,
    created_at REAL NOT NULL
);
"""

_USER_MIGRATION_COLUMNS = (
    ("status", "TEXT NOT NULL DEFAULT 'active'"),
    ("email_verified_at", "REAL"),
    ("last_login_at", "REAL"),
    ("banned_at", "REAL"),
    ("ban_reason", "TEXT"),
    ("admin_note", "TEXT"),
)


class Store:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
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
        return {
            "users_total": int(users),
            "users_active": int(active),
            "users_banned": int(banned),
            "bindings_total": int(bindings),
            "jobs_total": int(jobs),
            "jobs_ready": int(jobs_ready),
            "saved_total": int(saved),
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
    ) -> BindSession:
        code = normalize_bind_code(bind_code)
        mac = normalize_mac(device_mac)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bind_sessions (
                    bind_code, device_mac, device_token_enc, expires_at, consumed_at
                ) VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(bind_code) DO UPDATE SET
                    device_mac=excluded.device_mac,
                    device_token_enc=excluded.device_token_enc,
                    expires_at=excluded.expires_at,
                    consumed_at=NULL
                """,
                (code, mac, device_token_enc, expires_at),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM bind_sessions WHERE bind_code = ?", (code,)
            ).fetchone()
        assert row is not None
        return self._row_bind_session(row)

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

    def set_device_binding(
        self,
        *,
        user_id: str,
        device_mac: str,
        device_token_enc: str,
        bound_at: float,
        token_probe_ok: bool | None,
    ) -> DeviceBinding:
        mac = normalize_mac(device_mac)
        probe_val = None if token_probe_ok is None else int(token_probe_ok)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO device_bindings (
                    user_id, device_mac, device_token_enc, bound_at, last_push_at, token_probe_ok
                ) VALUES (?, ?, ?, ?, NULL, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    device_mac=excluded.device_mac,
                    device_token_enc=excluded.device_token_enc,
                    bound_at=excluded.bound_at,
                    last_push_at=NULL,
                    token_probe_ok=excluded.token_probe_ok
                """,
                (user_id, mac, device_token_enc, bound_at, probe_val),
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

    def get_draw_job(self, job_id: str, *, user_id: str) -> DrawJob | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM draw_jobs WHERE id = ? AND user_id = ?",
                (job_id, user_id),
            ).fetchone()
        return self._row_draw_job(row) if row else None

    def update_binding_last_push(self, user_id: str, *, when: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE device_bindings SET last_push_at = ? WHERE user_id = ?",
                (when, user_id),
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
    ) -> SavedPreview:
        saved = SavedPreview(
            id=str(uuid.uuid4()),
            user_id=user_id,
            job_id=job_id,
            title=title,
            preview_path=preview_path,
            gcode_path=gcode_path,
            saved_at=time.time(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO saved_previews (
                    id, user_id, job_id, title, preview_path, gcode_path, saved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    saved.id,
                    saved.user_id,
                    saved.job_id,
                    saved.title,
                    saved.preview_path,
                    saved.gcode_path,
                    saved.saved_at,
                ),
            )
            conn.commit()
        return saved

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
