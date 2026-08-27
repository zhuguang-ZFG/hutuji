"""Curated single-stroke SVG library runtime (design 2026-08-26)."""
from __future__ import annotations

import json
import logging
import random
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import quickdraw
from .bitmap_svg import BitmapResult, polylines_to_preview_png
from .config import settings
from .llm_svg import _validate_svg

logger = logging.getLogger(__name__)
_rng = random.SystemRandom()
_warned: set[tuple[str, str]] = set()
_POINTS_RE = re.compile(r"<polyline\b[^>]*\bpoints=\"([^\"]+)\"", re.I)


def _warn_once(reason: str, category: str, detail: str = "") -> None:
    key = (reason, category)
    if key in _warned:
        return
    _warned.add(key)
    logger.warning("curated %s（category=%s）%s", reason, category, detail)


def _curated_dir() -> Path | None:
    raw = getattr(settings, "curated_dir", "")
    if not raw:
        return None
    return Path(raw)


def coverage_ready(manifest: dict, required: list[str]) -> bool:
    n_min = int(manifest.get("n_min", 30))
    cats = manifest.get("categories") or {}
    if not required:
        return False
    for name in required:
        row = cats.get(name) or {}
        if int(row.get("count", 0)) < n_min:
            return False
    return True


def required_categories() -> list[str]:
    return sorted(set(quickdraw._category_map().values()))


@lru_cache(maxsize=1)
def _manifest_cache(root: str) -> dict:
    return json.loads((Path(root) / "manifest.json").read_text(encoding="utf-8"))


def is_replace_active() -> bool:
    if not getattr(settings, "curated_replace_qd", False):
        return False
    root = _curated_dir()
    if root is None:
        return False
    try:
        manifest = _manifest_cache(str(root.resolve()))
    except OSError:
        _warn_once("manifest unreadable", "-")
        return False
    ready_flag = bool(manifest.get("ready"))
    ready = ready_flag and coverage_ready(manifest, required_categories())
    if not ready:
        _warn_once("replace blocked", "-", "coverage not ready")
        return False
    return True


@lru_cache(maxsize=64)
def _list_svgs(root: str, category: str) -> tuple[str, ...]:
    d = Path(root) / category
    if not d.is_dir():
        return ()
    return tuple(sorted(p.name for p in d.glob("*.svg")))


def _preview_from_svg(svg: str) -> bytes:
    polys: list[np.ndarray] = []
    for m in _POINTS_RE.finditer(svg):
        nums = [float(x) for x in m.group(1).replace(",", " ").split()]
        if len(nums) >= 4 and len(nums) % 2 == 0:
            polys.append(np.array(nums, dtype=float).reshape(-1, 2))
    if not polys:
        polys = [np.array([[0.0, 0.0], [1.0, 1.0]], dtype=float)]
    return polylines_to_preview_png(polys, 256, 256)


def lookup(description: str) -> BitmapResult | None:
    if not is_replace_active():
        return None
    root = _curated_dir()
    if root is None:
        return None
    root_s = str(root.resolve())
    category = quickdraw._match_category(description)
    if category is None:
        return None
    names = _list_svgs(root_s, category)
    if not names:
        _warn_once("miss under replace", category, "→ AI")
        return None
    name = _rng.choice(names)
    try:
        svg = (Path(root_s) / category / name).read_text(encoding="utf-8")
        _validate_svg(svg)
    except Exception:
        _warn_once("bad svg", category, name)
        return None
    logger.info("curated hit category=%s idx=%s", category, name)
    return BitmapResult(svg=svg, preview_png=_preview_from_svg(svg))


def _pick_and_load(root_s: str, category: str) -> BitmapResult | None:
    """类目内随机抽一条并加载校验；lookup 与 lookup_preferred 共用。"""
    names = _list_svgs(root_s, category)
    if not names:
        return None
    name = _rng.choice(names)
    try:
        svg = (Path(root_s) / category / name).read_text(encoding="utf-8")
        _validate_svg(svg)
    except Exception:
        _warn_once("bad svg", category, name)
        return None
    logger.info("curated hit category=%s idx=%s", category, name)
    return BitmapResult(svg=svg, preview_png=_preview_from_svg(svg))


def lookup_preferred(description: str) -> BitmapResult | None:
    """达标类目优先出 curated（2026-08-27 用户拍板 QD 退役路由）。

    判据：类目在 manifest 的 count >= n_min（单类目达标，不要求全集
    ready——142/142 运行时抽检已过，见 deployment 文首）。manifest 每次
    现读（灌库进行中计数要新鲜，不能 lru_cache 冻结）；未达标/未映射/
    目录缺失一律 None 落 AI 链。
    """
    root = _curated_dir()
    if root is None:
        return None
    category = quickdraw._match_category(description)
    if category is None:
        return None
    root_s = str(root.resolve())
    try:
        manifest = json.loads((Path(root_s) / "manifest.json").read_text(encoding="utf-8"))
    except OSError:
        _warn_once("manifest unreadable", "-")
        return None
    n_min = int(manifest.get("n_min", 30))
    count = int(manifest.get("categories", {}).get(category, {}).get("count", 0))
    if count < n_min:
        logger.info(
            "curated prefer miss（count=%d < %d）category=%s → AI", count, n_min, category
        )
        return None
    result = _pick_and_load(root_s, category)
    if result is None:
        _warn_once("miss under prefer", category, "→ AI")
    return result
