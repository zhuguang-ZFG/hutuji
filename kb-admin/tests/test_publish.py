from __future__ import annotations

import time
import uuid
from pathlib import Path

from kb_admin.models import Faq
from kb_admin.publish import XiaozhiConsoleError, publish
from kb_admin.store import Store


def _sample_faq(**overrides) -> Faq:
    defaults = {
        "id": str(uuid.uuid4()),
        "title": "没墨怎么办",
        "body": "先试笔。",
        "category": "fault",
        "aliases": ["画不出来"],
        "enabled": True,
        "updated_at": time.time(),
        "updated_by": "tester",
        "sort_key": 0,
        "imported": False,
    }
    defaults.update(overrides)
    return Faq(**defaults)


def test_publish_success(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "data" / "t.db")
    store.upsert_faq(_sample_faq(title="Q1"))
    store.upsert_faq(_sample_faq(title="Q2", enabled=False))

    seen: dict[str, object] = {}

    def fake_update(kb_id, path):
        seen["kb_id"] = kb_id
        seen["path"] = path
        seen["content"] = path.read_text(encoding="utf-8")
        return "deadbeef"

    monkeypatch.setattr("kb_admin.publish.update_kb_from_path", fake_update)

    rec = publish(store, kb_id=71153, actor="u", preface="# P\n", appendix="")

    assert rec.status == "success"
    assert rec.xiaozhi_doc_md5 == "deadbeef"
    assert rec.error_summary is None
    assert rec.faq_count == 1
    assert rec.created_by == "u"
    assert seen["kb_id"] == 71153
    assert seen["path"] == tmp_path / "data" / "publish_tmp.md"
    assert "# P" in seen["content"]
    assert "Q1" in seen["content"]
    assert "Q2" not in seen["content"]

    rows = store.list_publish_records()
    assert len(rows) == 1
    assert rows[0].id == rec.id
    assert rows[0].status == "success"


def test_publish_failure_no_raise(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "t.db")
    store.upsert_faq(_sample_faq())

    def boom(_kb_id, _path):
        raise XiaozhiConsoleError("上传失败: Bearer sk-testsecret123（旧文档保留）")

    monkeypatch.setattr("kb_admin.publish.update_kb_from_path", boom)

    rec = publish(store, kb_id=71153, actor="editor", preface="", appendix="")

    assert rec.status == "failed"
    assert rec.xiaozhi_doc_md5 == ""
    assert rec.error_summary is not None
    assert "sk-testsecret123" not in rec.error_summary
    assert rec.faq_count == 1

    rows = store.list_publish_records()
    assert len(rows) == 1
    assert rows[0].status == "failed"


def test_publish_counts_only_enabled(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "t.db")
    store.upsert_faq(_sample_faq(title="on"))
    store.upsert_faq(_sample_faq(title="off", enabled=False))

    monkeypatch.setattr(
        "kb_admin.publish.update_kb_from_path", lambda _kb, _p: "abc"
    )

    rec = publish(store, kb_id=1, actor="u", preface="", appendix="")
    assert rec.faq_count == 1
