"""大幅产物不能借别的机器范围放行，配置与实际下载字节同寿命。"""
import asyncio
from dataclasses import replace
import json
import os

import pytest

from hutuji_mcp import server
from hutuji_mcp.output_store import LocalOutputStore, OutputNotFoundError, StaleOutputStoreError
from hutuji_mcp.paper_artifacts import PAPER_SUFFIX, encode_paper_metadata, paper_path, write_paper_metadata
from hutuji_mcp.paper_config import PaperConfig


BODY = b"G21\nG90\nF24000\nG1G90 Z0.0F10000\nG1G90 Z0.0F10000\nG0 X10 Y10 F24000\nG1G90 Z5.0F10000\nG1 X390 Y560 F24000\nG1G90 Z0.0F10000\n"
LARGE = PaperConfig("A2", 420, 594, False, 10, False, 400, 574)
STEM = "draw_20260913_120000_" + "Z" * 22


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "settings", replace(server.settings, output_dir=tmp_path))
    path = tmp_path / (STEM + ".gcode")
    path.write_bytes(BODY)
    return LocalOutputStore(), path


def test_large_output_requires_its_own_metadata(tmp_path, monkeypatch):
    store, path = _store(tmp_path, monkeypatch)
    with pytest.raises(StaleOutputStoreError):
        store.read_verified(path.name)
    write_paper_metadata(path, LARGE)
    assert store.read_verified(path.name)[0] == BODY
    assert store.paper_marker(path.name, BODY) == LARGE.marker
    path.write_bytes(BODY.replace(b"X390", b"X391"))
    with pytest.raises(StaleOutputStoreError, match="不匹配"):
        store.read_verified(path.name)


@pytest.mark.parametrize("variant", ["bad-json", "wrong-config", "oversize", "hardlink"])
def test_metadata_error_cannot_fall_back(tmp_path, monkeypatch, variant):
    store, path = _store(tmp_path, monkeypatch)
    # 内容故意落在旧默认档内；存在但损坏的元数据仍不能当无元数据文件放行。
    data = BODY.replace(b"X390 Y560", b"X20 Y20")
    path.write_bytes(data)
    meta = paper_path(path)
    if variant == "bad-json":
        meta.write_bytes(b"{")
    elif variant == "wrong-config":
        value = json.loads(encode_paper_metadata(LARGE, data))
        value["paper"]["max_x_mm"] = 10
        meta.write_text(json.dumps(value), encoding="utf-8")
    elif variant == "oversize":
        meta.write_bytes(b" " * 2049)
    else:
        source = tmp_path / "other"
        source.write_bytes(encode_paper_metadata(LARGE, data))
        os.link(source, meta)
    with pytest.raises((OutputNotFoundError, StaleOutputStoreError)):
        store.read_verified(path.name)


def test_metadata_removed_before_response_does_not_emit_unmarked_large_output(tmp_path, monkeypatch):
    store, path = _store(tmp_path, monkeypatch)
    write_paper_metadata(path, LARGE)
    data, _ = store.read_verified(path.name)
    paper_path(path).unlink()
    with pytest.raises(StaleOutputStoreError):
        store.paper_marker(path.name, data)


def test_metadata_ttl_is_shifted_and_orphan_is_cleaned(tmp_path, monkeypatch):
    store, path = _store(tmp_path, monkeypatch)
    with store.job_workspace(STEM) as paths:
        paths.svg_path.write_bytes(b"svg")
        paths.preview_path.write_bytes(b"png")
        write_paper_metadata(path, LARGE)
        store.finalize_job(paths, created_at=1000)
        assert paper_path(path).stat().st_mtime == 1000
    path.unlink()
    store.cleanup_expired(now=2000 + server.settings.output_ttl_seconds)
    assert not any(tmp_path.iterdir())


def test_metadata_never_becomes_public_download(tmp_path, monkeypatch):
    from fastapi import HTTPException

    _store(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as error:
        asyncio.run(server._serve_file(STEM + PAPER_SUFFIX))
    assert error.value.status_code == 404
