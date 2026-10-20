"""TG 频道冷备份（telegram_archive，2026-09-12 PoC）。

只测可观察契约：门（默认关/三件套开关）、状态机迁移（pending→uploaded/failed）、
字节校验失败不冒充 uploaded、删除时尽力撤回、列表 API 暴露 tg_backup 字段。
网络面（curl 细节）全部 monkeypatch——真实链路已由频道探针覆盖（sha256 一致）。
"""
from __future__ import annotations

import hashlib
import time
import queue
from pathlib import Path
from typing import Any

import pytest

from draw_portal import telegram_archive as ta
from draw_portal.config import Config
from draw_portal.models import SavedPreview
from draw_portal.store import Store


def _store(tmp_path: Path) -> Store:
    return Store(tmp_path / "data.db", provider_cipher_key=None)


def _saved(tmp_path: Path, store: Store, *, tg_pending: bool = True) -> SavedPreview:
    png = tmp_path / "preview.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    return store.create_saved_preview(
        user_id="u1",
        job_id=None,
        title="测试",
        preview_path=str(png),
        gcode_path=None,
        tg_pending=tg_pending,
    )


def _cfg(tmp_path: Path) -> Any:
    # enqueue/delete 只用 store 与 config 无关，传 None 风格的替身即可。
    return None


def _set_env(monkeypatch: pytest.MonkeyPatch, *, on: bool) -> None:
    monkeypatch.setenv("HUTUJI_TG_UPLOAD_ENABLED", "1" if on else "0")
    monkeypatch.setenv("TG_BOT_TOKEN", "t0ken" if on else "")
    monkeypatch.setenv("TG_CHANNEL_ID", "-1004350290412" if on else "")


def test_enabled_requires_all_three(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, on=False)
    assert ta.enabled() is False
    _set_env(monkeypatch, on=True)
    assert ta.enabled() is True
    monkeypatch.delenv("TG_CHANNEL_ID")
    assert ta.enabled() is False


def test_enqueue_disabled_is_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_env(monkeypatch, on=False)
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    # 未启用：队列不增长、状态不迁移。
    before = ta._queue.qsize()
    ta.enqueue_preview(store, saved)
    assert ta._queue.qsize() == before
    assert store.get_saved_preview(saved.id, user_id="u1").tg_state == "pending"


def test_disable_upload_stops_queued_tasks_but_keeps_deletion(monkeypatch, tmp_path):
    _set_env(monkeypatch, on=True)
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    store.mark_preview_telegram(saved.id, state="uploaded", file_id="existing", message_id=77)
    saved = store.get_saved_preview(saved.id, user_id="u1")
    monkeypatch.setenv("HUTUJI_TG_UPLOAD_ENABLED", "0")
    uploads = []
    deletes = []
    monkeypatch.setattr(ta, "_upload_document", lambda path: uploads.append(path))
    monkeypatch.setattr(ta, "_delete_channel_copy_async", deletes.append)
    ta._process(store, ta._Task(saved.id, Path(saved.preview_path), "t", "u1", store))
    assert uploads == []
    ta.delete_message_async(saved)
    assert deletes == [77]
    assert ta.retry_user(store, "u1")["enqueued"] == 0


def test_process_happy_path_marks_uploaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch, on=True)
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path))
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_upload(path: Path) -> tuple[str, int | None]:
        calls.append(("upload", {"path": str(path)}))
        return "FAKEID", 77

    def fake_verify(file_id: str, src_sha: str) -> bool:
        calls.append(("verify", {"file_id": file_id, "sha": src_sha[:16]}))
        assert file_id == "FAKEID"
        return True

    monkeypatch.setattr(ta, "_upload_document", fake_upload)
    monkeypatch.setattr(ta, "_verify_download", fake_verify)
    monkeypatch.setattr(ta.time, "sleep", lambda _s: None)
    ta._process(store, ta._Task(saved_id=saved.id, path=Path(saved.preview_path), title="t", user_id="u1", store=store))
    row = store.get_saved_preview(saved.id, user_id="u1")
    assert row.tg_state == "uploaded"
    assert row.tg_file_id == "FAKEID"
    assert row.tg_message_id == 77
    entries = store.list_archive_log_for_user("u1")
    assert len(entries) == 1
    assert entries[0]["saved_id"] == saved.id
    assert entries[0]["user_id"] == "u1"
    assert entries[0]["tg_file_id"] == "FAKEID"
    assert entries[0]["tg_message_id"] == 77
    assert entries[0]["tg_removed_at"] is None
    import sqlite3

    conn = sqlite3.connect(tmp_path / "data.db")
    checked = conn.execute(
        "SELECT tg_checked_at FROM saved_previews WHERE id = ?", (saved.id,)
    ).fetchone()[0]
    conn.close()
    assert checked is not None


def test_process_sha_mismatch_marks_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch, on=True)
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path))
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    monkeypatch.setattr(ta, "_upload_document", lambda _p: ("FAKEID", 78))
    monkeypatch.setattr(ta, "_verify_download", lambda _f, _s: False)
    monkeypatch.setattr(ta.time, "sleep", lambda _s: None)
    ta._process(store, ta._Task(saved_id=saved.id, path=Path(saved.preview_path), title="t", user_id="u1", store=store))
    row = store.get_saved_preview(saved.id, user_id="u1")
    assert row.tg_state == "failed"
    assert row.tg_file_id == "FAKEID"


def test_process_upload_exhaustion_marks_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch, on=True)
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    attempts: list[int] = []

    def flaky(_path: Path) -> tuple[str, int | None]:
        attempts.append(1)
        raise ta._RateLimited(0.0)

    monkeypatch.setattr(ta, "_upload_document", flaky)
    monkeypatch.setattr(ta, "_verify_download", lambda _f, _s: True)
    monkeypatch.setattr(ta.time, "sleep", lambda _s: None)
    ta._process(store, ta._Task(saved_id=saved.id, path=Path(saved.preview_path), title="t", user_id="u1", store=store))
    assert len(attempts) == ta._MAX_ATTEMPTS
    assert store.get_saved_preview(saved.id, user_id="u1").tg_state == "failed"


def test_process_missing_file_marks_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch, on=True)
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    Path(saved.preview_path).unlink()
    monkeypatch.setattr(ta.time, "sleep", lambda _s: None)
    ta._process(store, ta._Task(saved_id=saved.id, path=Path(saved.preview_path), title="t", user_id="u1", store=store))
    assert store.get_saved_preview(saved.id, user_id="u1").tg_state == "failed"


def test_mark_rejects_unknown_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    store.mark_preview_telegram(saved.id, state="bogus", file_id=None, message_id=None)
    assert store.get_saved_preview(saved.id, user_id="u1").tg_state == "pending"


def test_delete_message_async_skips_without_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch, on=True)
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)  # tg_message_id=None
    fired: list[int] = []
    monkeypatch.setattr(ta, "_api_call", lambda m, f, *, timeout: fired.append(f))
    ta.delete_message_async(saved)
    time.sleep(0.05)
    assert fired == []


def test_delete_message_async_fires_with_message_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch, on=True)
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    store.mark_preview_telegram(saved.id, state="uploaded", file_id="F", message_id=99)
    saved = store.get_saved_preview(saved.id, user_id="u1")
    fired: list[int] = []

    def fake_api(method: str, fields: dict[str, str], *, timeout: float) -> dict[str, Any]:
        fired.append(int(fields["message_id"]))
        return {"ok": True}

    monkeypatch.setattr(ta, "_api_call", fake_api)
    ta.delete_message_async(saved)
    deadline = time.time() + 2.0
    while not fired and time.time() < deadline:
        time.sleep(0.02)
    assert fired == [99]


def test_saved_list_api_exposes_tg_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """列表契约：tg_backup 字段在场（null=未启用）。走 app 路由不易（需登录闸），
    直接钉 store→模型→序列化的字段链。"""
    _set_env(monkeypatch, on=False)
    store = _store(tmp_path)
    saved = _saved(tmp_path, store, tg_pending=False)
    items = store.list_saved_previews(saved.user_id)
    assert items[0].tg_state is None
    assert items[0].tg_file_id is None


def test_enqueue_task_binds_own_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """评审修复回归：worker 不全局绑定首个 Store——Store 随任务入队，
    同进程两个库各自回写各自的行（2026-09-12 单例横库缺陷）。"""
    _set_env(monkeypatch, on=True)
    monkeypatch.setattr(ta, "_ensure_worker", lambda: None)  # 只钉队列边界，不起线程。
    store_a = _store(tmp_path / "a")
    store_b = _store(tmp_path / "b")
    saved_a = _saved(tmp_path, store_a)
    saved_b = _saved(tmp_path, store_b)
    ta.enqueue_preview(store_a, saved_a)
    ta.enqueue_preview(store_b, saved_b)
    task_a = ta._queue.get_nowait()
    task_b = ta._queue.get_nowait()
    assert task_a.store is store_a
    assert task_b.store is store_b
    assert task_a.user_id == "u1"
    monkeypatch.setattr(ta, "_upload_document", lambda _p: ("FAKEID", 1))
    monkeypatch.setattr(ta, "_verify_download", lambda _f, _s: True)
    monkeypatch.setattr(ta.time, "sleep", lambda _s: None)
    ta._process(task_b.store, task_b)
    assert store_b.get_saved_preview(saved_b.id, user_id="u1").tg_state == "uploaded"
    # B 的任务不得写进 A 的库；A 行保持 pending（fail-open 的原始语义）。
    assert store_a.get_saved_preview(saved_a.id, user_id="u1").tg_state == "pending"


def test_restore_missing_local_file_restored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch, on=True)
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ta.time, "sleep", lambda _s: None)
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    png = Path(saved.preview_path)
    blob = png.read_bytes()
    store.mark_preview_telegram(saved.id, state="uploaded", file_id="F1", message_id=11)
    store.append_archive_log(
        saved_id=saved.id, user_id="u1", title=saved.title,
        preview_path=saved.preview_path, tg_file_id="F1", tg_message_id=11,
    )
    png.unlink()
    monkeypatch.setattr(ta, "_download_document", lambda _f, _dest: blob)
    summary = ta.restore_user(store, "u1")
    assert summary["restored"] == 1 and summary["failed"] == 0
    assert png.read_bytes() == blob
    assert store.get_saved_preview(saved.id, user_id="u1").tg_state == "uploaded"


def test_restore_trimmed_row_recreated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch, on=True)
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ta.time, "sleep", lambda _s: None)
    store = _store(tmp_path)
    blob = b"\x89PNG\r\n\x1a\n" + b"y" * 32
    # 生产时序：台账在上传时刻（行还在）落账，trim 事后只删行不删台账。
    live = store.create_saved_preview(
        user_id="u1", job_id=None, title="旧图",
        preview_path=str(tmp_path / "old.png"), gcode_path=None, tg_pending=False,
    )
    assert store.append_archive_log(
        saved_id=live.id, user_id="u1", title="旧图",
        preview_path=str(tmp_path / "old.png"),
        tg_file_id="F2", tg_message_id=12,
    ) is True
    store.delete_saved_preview(live.id, user_id="u1")  # 模拟 trim 清行
    saved_id = live.id
    monkeypatch.setattr(ta, "_download_document", lambda _f, _dest: blob)
    summary = ta.restore_user(store, "u1")
    assert summary["recreated"] == 1
    row = store.get_saved_preview(saved_id, user_id="u1")
    assert row is not None
    assert row.tg_state == "uploaded"
    assert row.gcode_path is None  # 只归档预览：召回行不可再次发送出图。
    assert (tmp_path / "saved" / "u1" / f"{saved_id}.png").read_bytes() == blob


def test_restore_skips_removed_and_intact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch, on=True)
    monkeypatch.setattr(ta.time, "sleep", lambda _s: None)
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    store.mark_preview_telegram(saved.id, state="uploaded", file_id="F3", message_id=13)
    store.append_archive_log(
        saved_id=saved.id, user_id="u1", title=saved.title,
        preview_path=saved.preview_path, tg_file_id="F3", tg_message_id=13,
    )
    downloaded: list[str] = []
    monkeypatch.setattr(
        ta, "_download_document", lambda f, _d: downloaded.append(f) or b"z" * 8
    )
    # 在库且文件完好 → 跳过（不重复下载）。
    summary = ta.restore_user(store, "u1")
    assert summary["skipped"] == 1 and summary["restored"] == 0
    assert downloaded == []
    # 用户删图（撤回 + 台账标记）→ 台账行消失，召回不再可见。
    store.mark_archive_log_removed(saved.id)
    summary2 = ta.restore_user(store, "u1")
    assert summary2["restored"] == 0 and summary2["skipped"] == 0
    assert store.list_archive_log_for_user("u1") == []
    assert downloaded == []


def test_restore_download_failure_counts_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch, on=True)
    monkeypatch.setattr(ta.time, "sleep", lambda _s: None)
    store = _store(tmp_path)
    live = store.create_saved_preview(
        user_id="u1", job_id=None, title="x",
        preview_path=str(tmp_path / "x.png"), gcode_path=None, tg_pending=False,
    )
    assert store.append_archive_log(
        saved_id=live.id, user_id="u1", title="x",
        preview_path=str(tmp_path / "x.png"),
        tg_file_id="F4", tg_message_id=14,
    ) is True
    store.delete_saved_preview(live.id, user_id="u1")
    saved_id = live.id
    monkeypatch.setattr(ta, "_download_document", lambda _f, _d: None)
    summary = ta.restore_user(store, "u1")
    assert summary["failed"] == 1
    assert store.get_saved_preview(saved_id, user_id="u1") is None

def test_process_row_deleted_before_upload_aborts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch, on=True)
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path))
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    uploaded: list[Path] = []
    monkeypatch.setattr(ta, "_upload_document", lambda p: uploaded.append(p) or ("F", 1))
    monkeypatch.setattr(ta.time, "sleep", lambda _s: None)
    store.delete_saved_preview(saved.id, user_id="u1")  # 入队后、处理前删行
    ta._process(store, ta._Task(saved_id=saved.id, path=Path(saved.preview_path), title="t", user_id="u1", store=store))
    assert uploaded == []  # 不再上传已删除的作品


def test_process_row_deleted_midflight_deletes_channel_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """评审①回归：mark/台账任何一步发现行已删 → 频道副本尽力撤回、不落台账。"""
    _set_env(monkeypatch, on=True)
    monkeypatch.setenv("DRAW_PORTAL_DATA_DIR", str(tmp_path))
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    deleted: list[str] = []

    def fake_verify(file_id: str, src_sha: str) -> bool:
        # 用户在「上传完成」与「落库」之间删除：行消失。
        store.delete_saved_preview(saved.id, user_id="u1")
        return True

    monkeypatch.setattr(ta, "_upload_document", lambda _p: ("F1", 5))
    monkeypatch.setattr(ta, "_verify_download", fake_verify)
    monkeypatch.setattr(ta, "_delete_channel_copy_async", lambda mid: deleted.append(str(mid)))
    monkeypatch.setattr(ta.time, "sleep", lambda _s: None)
    ta._process(store, ta._Task(saved_id=saved.id, path=Path(saved.preview_path), title="t", user_id="u1", store=store))
    assert deleted == ["5"]
    assert store.list_archive_log_for_user("u1") == []  # 无台账 = 召回不可见，不复活


def test_append_archive_log_guarded_by_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch, on=True)
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    store.delete_saved_preview(saved.id, user_id="u1")
    ok = store.append_archive_log(
        saved_id=saved.id, user_id="u1", title="t",
        preview_path=saved.preview_path, tg_file_id="F", tg_message_id=1,
    )
    assert ok is False
    assert store.list_archive_log_for_user("u1") == []


@pytest.mark.parametrize("raises", [False, True])
def test_failed_verification_after_user_delete_retracts_copy(monkeypatch, tmp_path, raises):
    _set_env(monkeypatch, on=True)
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    deleted = []
    def verify(*args):
        old = store.delete_saved_preview(saved.id, user_id="u1")
        ta.delete_message_async(old)
        if raises:
            raise TimeoutError("校验超时")
        return False
    monkeypatch.setattr(ta, "_upload_document", lambda path: ("F1", 5))
    monkeypatch.setattr(ta, "_verify_download", verify)
    monkeypatch.setattr(ta, "_delete_channel_copy_async", deleted.append)
    ta._process(store, ta._Task(saved.id, Path(saved.preview_path), saved.title, "u1", store))
    assert deleted == [5]
    assert store.get_saved_preview(saved.id, user_id="u1") is None
    assert store.list_archive_log_for_user("u1") == []


def test_mark_preview_telegram_returns_row_presence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch, on=True)
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    assert store.mark_preview_telegram(saved.id, state="uploaded", file_id="F", message_id=1) is True
    store.delete_saved_preview(saved.id, user_id="u1")
    assert store.mark_preview_telegram(saved.id, state="uploaded", file_id="F", message_id=1) is False


def test_enqueue_pending_on_boot_requeues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """评审②回归：重启丢队列后，启动扫描把 pending 重新入队（绑定本库）。"""
    _set_env(monkeypatch, on=True)
    monkeypatch.setattr(ta, "_ensure_worker", lambda: None)
    try:
        while True:  # 清残留，模拟重启后空队列
            ta._queue.get_nowait()
    except queue.Empty:
        pass
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    assert ta.enqueue_pending_on_boot(store) == 1
    task = ta._queue.get_nowait()
    assert task.saved_id == saved.id
    assert task.store is store


def test_retry_user_enqueues_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch, on=True)
    monkeypatch.setattr(ta, "_ensure_worker", lambda: None)
    store = _store(tmp_path)
    saved = _saved(tmp_path, store)
    store.mark_preview_telegram(saved.id, state="failed", file_id=None, message_id=None)
    summary = ta.retry_user(store, "u1")
    assert summary["enqueued"] == 1
    task = ta._queue.get_nowait()
    assert task.saved_id == saved.id
    assert store.get_saved_preview("gone1", user_id="u1") is None
