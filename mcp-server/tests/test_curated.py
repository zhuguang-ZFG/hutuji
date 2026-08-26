from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hutuji_mcp import curated, quickdraw

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" version="1.1">'
    '<polyline points="10,10 90,10 90,90" fill="none" stroke="black" stroke-width="1"/>'
    "</svg>"
)


@pytest.fixture(autouse=True)
def _reset():
    curated._manifest_cache.cache_clear()
    curated._list_svgs.cache_clear()
    curated._warned.clear()
    quickdraw._category_map.cache_clear()
    quickdraw._substring_keys.cache_clear()
    yield
    curated._manifest_cache.cache_clear()
    curated._list_svgs.cache_clear()
    curated._warned.clear()
    quickdraw._category_map.cache_clear()
    quickdraw._substring_keys.cache_clear()


def _settings(monkeypatch, root: Path | None, replace: bool) -> None:
    monkeypatch.setattr(
        curated,
        "settings",
        SimpleNamespace(
            curated_dir=str(root) if root else "",
            curated_replace_qd=replace,
        ),
    )


def _lib(root: Path, cat: str = "cat", n: int = 3) -> None:
    d = root / cat
    d.mkdir(parents=True)
    for i in range(n):
        (d / f"{i:03d}.svg").write_text(_SVG, encoding="utf-8")
    manifest = {
        "gate_version": "1",
        "n_target": 50,
        "n_min": 30,
        "categories": {cat: {"count": n}},
        "ready": False,
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_coverage_ready_requires_all_categories():
    required = ["cat", "dog", "tiger"]
    manifest = {
        "n_min": 30,
        "categories": {
            "cat": {"count": 30},
            "dog": {"count": 30},
            "tiger": {"count": 29},
        },
    }
    assert curated.coverage_ready(manifest, required) is False
    manifest["categories"]["tiger"]["count"] = 30
    assert curated.coverage_ready(manifest, required) is True


def test_replace_blocked_when_not_ready(tmp_path, monkeypatch):
    _lib(tmp_path)
    _settings(monkeypatch, tmp_path, replace=True)
    assert curated.is_replace_active() is False


def test_lookup_none_when_replace_inactive(tmp_path, monkeypatch):
    _lib(tmp_path)
    _settings(monkeypatch, tmp_path, replace=False)
    assert curated.lookup("猫") is None


def test_lookup_hit_when_replace_forced(tmp_path, monkeypatch):
    _lib(tmp_path)
    _settings(monkeypatch, tmp_path, replace=True)
    monkeypatch.setattr(curated, "is_replace_active", lambda: True)
    hit = curated.lookup("猫")
    assert hit is not None
    assert "<polyline" in hit.svg
    assert isinstance(hit.preview_png, (bytes, bytearray))
    assert len(hit.preview_png) > 32
