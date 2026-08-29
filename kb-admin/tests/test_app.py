from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kb_admin.app import create_app
from kb_admin.models import PublishRecord


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KB_ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("KB_ADMIN_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("KB_ADMIN_KB_ID", "71153")
    monkeypatch.setenv("KB_ADMIN_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("KB_ADMIN_PREFACE_PATH", str(tmp_path / "preface.md"))
    monkeypatch.setenv("KB_ADMIN_APPENDIX_PATH", str(tmp_path / "appendix.md"))
    (tmp_path / "preface.md").write_text("# 前言\n", encoding="utf-8")
    (tmp_path / "appendix.md").write_text("", encoding="utf-8")


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    _env(monkeypatch, tmp_path)
    return TestClient(create_app())


def _login(client: TestClient) -> None:
    r = client.post("/api/login", json={"password": "secret"})
    assert r.status_code == 200


def test_rejects_anonymous(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/faqs").status_code == 401
    assert client.post("/api/faqs", json={"title": "x", "body": "y"}).status_code == 401
    assert client.post("/api/import").status_code == 401
    assert client.post("/api/publish").status_code == 401
    assert client.get("/api/publish-records").status_code == 401


def test_login_wrong_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(monkeypatch, tmp_path)
    r = client.post("/api/login", json={"password": "wrong"})
    assert r.status_code == 401


def test_login_and_create_faq(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(monkeypatch, tmp_path)
    _login(client)

    r = client.post(
        "/api/faqs",
        json={
            "title": "没墨怎么办",
            "body": "先试笔。",
            "category": "fault",
            "aliases": ["画不出来"],
            "enabled": True,
            "sort_key": 1,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "没墨怎么办"
    assert data["body"] == "先试笔。"
    assert data["category"] == "fault"
    assert data["aliases"] == ["画不出来"]
    assert data["enabled"] is True
    assert data["sort_key"] == 1
    assert data["imported"] is False
    assert data["updated_by"] == "admin"

    listed = client.get("/api/faqs").json()
    assert len(listed) == 1
    assert listed[0]["id"] == data["id"]


def test_update_and_delete_faq(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(monkeypatch, tmp_path)
    _login(client)

    created = client.post(
        "/api/faqs",
        json={"title": "Q", "body": "A", "category": "other"},
    ).json()

    updated = client.put(
        f"/api/faqs/{created['id']}",
        json={"title": "Q2", "body": "A2", "category": "install", "enabled": False},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["title"] == "Q2"
    assert body["category"] == "install"
    assert body["enabled"] is False

    deleted = client.delete(f"/api/faqs/{created['id']}")
    assert deleted.status_code == 200
    assert client.get("/api/faqs").json() == []


def test_import_from_md(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    md_path = tmp_path / "kb.md"
    md_path.write_text(
        "### 问题一\n\n答案一。\n\n### 问题二\n\n答案二。\n",
        encoding="utf-8",
    )
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("KB_ADMIN_IMPORT_MD", str(md_path))
    client = TestClient(create_app())
    _login(client)

    r = client.post("/api/import")
    assert r.status_code == 200
    data = r.json()
    assert data["imported"] == 2
    assert len(data["faqs"]) == 2
    assert all(f["enabled"] is False for f in data["faqs"])
    assert all(f["imported"] is True for f in data["faqs"])

    titles = {f["title"] for f in client.get("/api/faqs").json()}
    assert titles == {"问题一", "问题二"}


def test_import_preserves_enabled_on_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    md_path = tmp_path / "kb.md"
    md_path.write_text("### 重复题\n\n旧答案。\n", encoding="utf-8")
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("KB_ADMIN_IMPORT_MD", str(md_path))
    client = TestClient(create_app())
    _login(client)

    first = client.post("/api/import").json()
    faq_id = first["faqs"][0]["id"]
    client.put(f"/api/faqs/{faq_id}", json={"enabled": True})

    md_path.write_text("### 重复题\n\n新答案。\n", encoding="utf-8")
    second = client.post("/api/import").json()
    assert second["faqs"][0]["enabled"] is True
    assert second["faqs"][0]["body"] == "新答案。"


def test_publish_and_list_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = _client(monkeypatch, tmp_path)
    _login(client)

    client.post(
        "/api/faqs",
        json={"title": "Q1", "body": "A1", "category": "fault", "enabled": True},
    )

    fake_rec = PublishRecord(
        id=str(uuid.uuid4()),
        created_at=time.time(),
        created_by="admin",
        status="success",
        xiaozhi_doc_md5="deadbeef",
        error_summary=None,
        faq_count=1,
    )

    def fake_publish(store, *, kb_id, actor, preface, appendix):
        store.add_publish_record(fake_rec)
        return fake_rec

    monkeypatch.setattr("kb_admin.app.publish", fake_publish)

    r = client.post("/api/publish")
    assert r.status_code == 200
    pub = r.json()
    assert pub["status"] == "success"
    assert pub["xiaozhi_doc_md5"] == "deadbeef"
    assert pub["faq_count"] == 1

    records = client.get("/api/publish-records").json()
    assert len(records) == 1
    assert records[0]["id"] == fake_rec.id
