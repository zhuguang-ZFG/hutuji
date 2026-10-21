"""召回提交与主动删除的先后关系，使用真实 SQLite 和文件。"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from draw_portal import telegram_archive as ta
from draw_portal.store import Store


def _archive(tmp_path):
    store = Store(tmp_path / "data.db")
    saved = store.create_saved_preview(
        user_id="u1", job_id=None, title="原图", preview_path=str(tmp_path / "gone.png"),
        gcode_path=None, tg_pending=False,
    )
    store.append_archive_log(saved_id=saved.id, user_id="u1", title=saved.title,
                             preview_path=saved.preview_path, tg_file_id="F1", tg_message_id=11)
    return store, saved


@pytest.mark.parametrize("when", ["snapshot", "download", "commit"])
def test_delete_during_restore_never_recreates_row_or_file(monkeypatch, tmp_path, when):
    store, saved = _archive(tmp_path)
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ta.time, "sleep", lambda _: None)
    monkeypatch.setattr(ta, "_download_with_retry", lambda _: b"image")
    method = {"snapshot": "list_archive_log_for_user", "commit": "restore_saved_preview"}.get(when)
    if when == "download":
        def download(_):
            store.delete_saved_preview(saved.id, user_id="u1")
            return b"image"
        monkeypatch.setattr(ta, "_download_with_retry", download)
    else:
        original = getattr(store, method)
        def interleave(*args, **kwargs):
            if when == "snapshot":
                result = original(*args, **kwargs)
                store.delete_saved_preview(saved.id, user_id="u1")
                return result
            store.delete_saved_preview(saved.id, user_id="u1")
            return original(*args, **kwargs)
        monkeypatch.setattr(store, method, interleave)
    summary = ta.restore_user(store, "u1")
    assert summary == {"user_id": "u1", "restored": 0, "recreated": 0, "skipped": 1, "failed": 0}
    assert store.get_saved_preview(saved.id, user_id="u1") is None
    assert store.list_archive_log_for_user("u1") == []
    assert not Path(saved.preview_path).exists()
    assert not (tmp_path / "saved" / "u1" / f"{saved.id}.png").exists()


def test_restore_rechecks_ledger_identity_at_commit(monkeypatch, tmp_path):
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path))
    store, saved = _archive(tmp_path)
    monkeypatch.setattr(ta.time, "sleep", lambda _: None)
    def download(_):
        store.append_archive_log(saved_id=saved.id, user_id="u1", title=saved.title,
                                 preview_path=saved.preview_path, tg_file_id="F2", tg_message_id=12)
        return b"stale image"
    monkeypatch.setattr(ta, "_download_with_retry", download)
    summary = ta.restore_user(store, "u1")
    assert summary["skipped"] == 1 and summary["restored"] == 0
    assert not Path(saved.preview_path).exists()


def test_delete_after_restore_removes_row_and_prevents_next_restore(monkeypatch, tmp_path):
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path))
    store, saved = _archive(tmp_path)
    monkeypatch.setattr(ta.time, "sleep", lambda _: None)
    monkeypatch.setattr(ta, "_download_with_retry", lambda _: b"image")
    assert ta.restore_user(store, "u1")["restored"] == 1
    removed = store.delete_saved_preview(saved.id, user_id="u1")
    Path(removed.preview_path).unlink()
    assert ta.restore_user(store, "u1")["restored"] == 0
    assert store.get_saved_preview(saved.id, user_id="u1") is None
    assert store.list_archive_log_for_user("u1") == []


def test_delete_waits_for_restore_write_transaction(monkeypatch, tmp_path):
    store, saved = _archive(tmp_path)
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ta.time, "sleep", lambda _: None)
    monkeypatch.setattr(ta, "_download_with_retry", lambda _: b"image")
    writing, deleting = Event(), Event()
    original_write = Path.write_bytes
    def write(path, data):
        if path.suffix == ".tmp":
            writing.set()
            assert deleting.wait(5)
        return original_write(path, data)
    monkeypatch.setattr(Path, "write_bytes", write)
    def delete():
        assert writing.wait(5)
        deleting.set()
        item = Store(tmp_path / "data.db").delete_saved_preview(saved.id, user_id="u1")
        Path(item.preview_path).unlink(missing_ok=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        restore = pool.submit(ta.restore_user, store, "u1")
        removed = pool.submit(delete)
        assert restore.result(timeout=10)["restored"] == 1
        removed.result(timeout=10)
    assert store.get_saved_preview(saved.id, user_id="u1") is None
    assert store.list_archive_log_for_user("u1") == []
    assert not Path(saved.preview_path).exists()
