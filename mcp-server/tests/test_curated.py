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


_GEN_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="190mm" height="190mm" '
    'viewBox="0 0 1024 1024" fill="none" stroke="black" stroke-width="1">'
    '<polyline points="'
    + " ".join(
        f"{300 + (i % 6) * 100},{125 + (i // 6) * 80}" for i in range(60)
    )
    + '"/></svg>'
)


def _ink_span(png: bytes) -> float:
    """预览墨跨度（行覆盖比 × 列覆盖比）：坐标框正确则整图铺开（实测 ~0.38），
    框取错（回退 256）则被裁近空（实测 ~0.009）——比字节数稳健（字节随内容密度漂）。"""
    import imageio.v3 as iio
    import numpy as np

    img = iio.imread(png)
    ink = img < 128
    if not ink.any():
        return 0.0
    rows = np.where(ink.any(axis=1))[0]
    cols = np.where(ink.any(axis=0))[0]
    return ((rows[-1] - rows[0]) / img.shape[0]) * ((cols[-1] - cols[0]) / img.shape[1])


def test_preview_uses_viewbox_frame_for_generation_entries():
    """生成条目（viewBox 1024）预览按 viewBox 取框——回退 256 会把 >256 的点
    全裁出画布（2026-08-27 小派预览空白实锤 321B 近空）。跨度阈值 0.25：
    正确框实测 ~0.38、错框实测 ~0.009。"""
    png = curated._preview_from_svg(_GEN_SVG)
    assert _ink_span(png) > 0.25, "1024 空间预览被裁近空（坐标框取错）"


def test_preview_uses_viewbox_frame_for_vector_entries():
    """矢量条目（viewBox 36）预览同样按 viewBox 取框——回退 256 会把内容
    挤进左上 ~14% 角落（跨度 ~0.009）。阈值 0.25：正确框实测 ~0.40。"""
    vec_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36">'
        '<polyline points="5,5 18,30 31,5 5,5 8,20 28,20" fill="none"/></svg>'
    )
    png = curated._preview_from_svg(vec_svg)
    assert _ink_span(png) > 0.25, "36 空间预览被挤角落（坐标框取错）"
