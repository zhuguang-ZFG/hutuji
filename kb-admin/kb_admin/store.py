from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from kb_admin.models import Faq, PublishRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS faqs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    category TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL,
    updated_by TEXT NOT NULL,
    sort_key INTEGER NOT NULL DEFAULT 0,
    imported INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS publish_records (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    created_by TEXT NOT NULL,
    status TEXT NOT NULL,
    xiaozhi_doc_md5 TEXT NOT NULL,
    error_summary TEXT,
    faq_count INTEGER NOT NULL
);
"""


class Store:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _row_to_faq(row: sqlite3.Row) -> Faq:
        return Faq(
            id=row["id"],
            title=row["title"],
            body=row["body"],
            category=row["category"],
            aliases=json.loads(row["aliases"]),
            enabled=bool(row["enabled"]),
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
            sort_key=row["sort_key"],
            imported=bool(row["imported"]),
        )

    @staticmethod
    def _row_to_publish_record(row: sqlite3.Row) -> PublishRecord:
        return PublishRecord(
            id=row["id"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            status=row["status"],
            xiaozhi_doc_md5=row["xiaozhi_doc_md5"],
            error_summary=row["error_summary"],
            faq_count=row["faq_count"],
        )

    def upsert_faq(self, faq: Faq) -> Faq:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO faqs (
                    id, title, body, category, aliases, enabled,
                    updated_at, updated_by, sort_key, imported
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    body=excluded.body,
                    category=excluded.category,
                    aliases=excluded.aliases,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by,
                    sort_key=excluded.sort_key,
                    imported=excluded.imported
                """,
                (
                    faq.id,
                    faq.title,
                    faq.body,
                    faq.category,
                    json.dumps(faq.aliases, ensure_ascii=False),
                    int(faq.enabled),
                    faq.updated_at,
                    faq.updated_by,
                    faq.sort_key,
                    int(faq.imported),
                ),
            )
            conn.commit()
        return faq

    def get_faq(self, id: str) -> Faq | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM faqs WHERE id = ?", (id,)).fetchone()
        if row is None:
            return None
        return self._row_to_faq(row)

    def list_faqs(self, *, category: str | None = None) -> list[Faq]:
        with self._connect() as conn:
            if category is None:
                rows = conn.execute(
                    "SELECT * FROM faqs ORDER BY sort_key, updated_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM faqs WHERE category = ? ORDER BY sort_key, updated_at DESC",
                    (category,),
                ).fetchall()
        return [self._row_to_faq(r) for r in rows]

    def delete_faq(self, id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM faqs WHERE id = ?", (id,))
            conn.commit()
            return cur.rowcount > 0

    def add_publish_record(self, rec: PublishRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO publish_records (
                    id, created_at, created_by, status,
                    xiaozhi_doc_md5, error_summary, faq_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.id,
                    rec.created_at,
                    rec.created_by,
                    rec.status,
                    rec.xiaozhi_doc_md5,
                    rec.error_summary,
                    rec.faq_count,
                ),
            )
            conn.commit()

    def list_publish_records(self, limit: int = 50) -> list[PublishRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM publish_records ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_publish_record(r) for r in rows]
