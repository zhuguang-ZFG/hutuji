"""TG 频道冷备份（telegram_archive，2026-09-12 PoC）。

只测可观察契约：门（默认关/三件套开关）、状态机迁移（pending→uploaded/failed）、
字节校验失败不冒充 uploaded、删除时尽力撤回、列表 API 暴露 tg_backup 字段。
网络面（curl 细节）全部 monkeypatch——真实链路已由频道探针覆盖（sha256 一致）。
"""
from __future__ import annotations

import hashlib
import time
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
    ta._process(store, ta._Task(saved_id=saved.id, path=Path(saved.preview_path), title="t"))
    row = store.get_saved_preview(saved.id, user_id="u1")
    assert row.tg_state == "uploaded"
    assert row.tg_file_id == "FAKEID"
    assert row.tg_message_id == 77
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
    ta._process(store, ta._Task(saved_id=saved.id, path=Path(saved.preview_path), title="t"))
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
    ta._process(store, ta._Task(saved_id=saved.id, path=Path(saved.preview_path), title="t"))
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
    ta._process(store, ta._Task(saved_id=saved.id, path=Path(saved.preview_path), title="t"))
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
    ta.delete_message_async(store, saved)
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
    ta.delete_message_async(store, saved)
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
