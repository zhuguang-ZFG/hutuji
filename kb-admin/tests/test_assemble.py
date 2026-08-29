from __future__ import annotations

import time
import uuid

from kb_admin.assemble import assemble_markdown
from kb_admin.import_md import import_candidates
from kb_admin.models import Faq


def _faq(**overrides) -> Faq:
    defaults = {
        "id": str(uuid.uuid4()),
        "title": "标题",
        "body": "正文",
        "category": "fault",
        "aliases": [],
        "enabled": True,
        "updated_at": 0.0,
        "updated_by": "u",
        "sort_key": 0,
        "imported": False,
    }
    defaults.update(overrides)
    return Faq(**defaults)


def test_assemble_skips_disabled():
    faqs = [
        _faq(id="1", title="A", body="a", category="fault", enabled=True),
        _faq(
            id="2",
            title="B",
            body="b",
            category="fault",
            aliases=["x"],
            enabled=False,
            sort_key=1,
        ),
    ]
    out = assemble_markdown(faqs, preface="# P\n", appendix="## App\n")
    assert "### A" in out and "### B" not in out
    assert out.startswith("# P\n") and out.rstrip().endswith("## App")


def test_import_splits_headings():
    md = "# Root\n\n## 故障排查\n\nbody1\n\n### 子题\n\nbody2\n"
    rows = import_candidates(md)
    assert len(rows) >= 2
    assert all(r.imported and not r.enabled for r in rows)


def test_assemble_aliases_line():
    faqs = [_faq(title="没墨", body="先试笔。", aliases=["画不出来", "没墨"])]
    out = assemble_markdown(faqs, preface="", appendix="")
    assert "同义问：画不出来；没墨" in out
    assert "### 没墨\n" in out
    assert "先试笔。" in out


def test_assemble_category_order_and_labels():
    faqs = [
        _faq(title="安全条", body="s", category="safety", sort_key=0),
        _faq(title="装机条", body="i", category="install", sort_key=0),
        _faq(title="故障条", body="f", category="fault", sort_key=0),
    ]
    out = assemble_markdown(faqs, preface="", appendix="")
    assert out.index("## 装机") < out.index("## 故障") < out.index("## 安全")
    assert "### 装机条" in out and "### 故障条" in out and "### 安全条" in out


def test_assemble_omits_empty_categories():
    faqs = [_faq(title="Only", body="x", category="other")]
    out = assemble_markdown(faqs, preface="", appendix="")
    assert "## 其他" in out
    assert "## 装机" not in out
    assert "## 故障" not in out


def test_assemble_sort_key_within_category():
    faqs = [
        _faq(title="Second", body="b", category="fault", sort_key=2),
        _faq(title="First", body="a", category="fault", sort_key=1),
    ]
    out = assemble_markdown(faqs, preface="", appendix="")
    assert out.index("### First") < out.index("### Second")


def test_import_candidate_fields():
    md = "## 某节\n\n内容\n"
    rows = import_candidates(md)
    assert len(rows) == 1
    row = rows[0]
    assert row.title == "某节"
    assert row.body.strip() == "内容"
    assert row.category == "other"
    assert row.aliases == []
    assert row.imported is True
    assert row.enabled is False
    assert row.updated_by == "import"
    uuid.UUID(row.id)


def test_import_skips_h1():
    md = "# 文档标题\n\n## 节一\n\na\n"
    rows = import_candidates(md)
    titles = [r.title for r in rows]
    assert "文档标题" not in titles
    assert "节一" in titles


def test_import_strips_heading_marker_from_title():
    md = "### 子标题\n\n正文\n"
    rows = import_candidates(md)
    assert rows[0].title == "子标题"
