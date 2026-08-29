from __future__ import annotations

import time
import uuid
from pathlib import Path

from kb_admin.models import Faq, PublishRecord
from kb_admin.store import Store


def _sample_faq(**overrides) -> Faq:
    defaults = {
        "id": str(uuid.uuid4()),
        "title": "没墨怎么办",
        "body": "先试笔。",
        "category": "fault",
        "aliases": ["画不出来", "没墨"],
        "enabled": True,
        "updated_at": time.time(),
        "updated_by": "tester",
        "sort_key": 0,
        "imported": False,
    }
    defaults.update(overrides)
    return Faq(**defaults)


def test_upsert_and_list(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    f = _sample_faq()
    s.upsert_faq(f)
    rows = s.list_faqs(category="fault")
    assert len(rows) == 1
    assert rows[0].aliases == ["画不出来", "没墨"]


def test_get_faq(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    f = _sample_faq()
    s.upsert_faq(f)
    got = s.get_faq(f.id)
    assert got is not None
    assert got.title == f.title
    assert got.aliases == f.aliases


def test_get_faq_missing(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    assert s.get_faq("nonexistent") is None


def test_delete_faq(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    f = _sample_faq()
    s.upsert_faq(f)
    assert s.delete_faq(f.id) is True
    assert s.get_faq(f.id) is None
    assert s.delete_faq(f.id) is False


def test_list_faqs_no_filter(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    s.upsert_faq(_sample_faq(category="fault"))
    s.upsert_faq(_sample_faq(category="install"))
    assert len(s.list_faqs()) == 2


def test_publish_records(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    rec = PublishRecord(
        id=str(uuid.uuid4()),
        created_at=time.time(),
        created_by="tester",
        status="ok",
        xiaozhi_doc_md5="abc123",
        error_summary=None,
        faq_count=3,
    )
    s.add_publish_record(rec)
    rows = s.list_publish_records(limit=10)
    assert len(rows) == 1
    assert rows[0].xiaozhi_doc_md5 == "abc123"
    assert rows[0].faq_count == 3
