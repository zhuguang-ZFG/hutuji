"""Quick Draw 本地缓存：命中即免 AI 生图的单线笔画来源。

设计稿 `docs/design/quickdraw-local-cache.md`（2026-08-24 用户批准）。要点：

- 命中路径与位图路线**共用同一产物面**：`polylines_to_svg` /
  `polylines_to_preview_png` / `_validate_svg` / vpype / `validate_gcode`，
  不新增 G-code 攻击面；§5 全部约束由原管线原样生效。
- 一切失败（禁用/目录缺失/类目未收录/数据损坏/校验不过）都返回 None，
  由调用方回落既有 AI 对冲——缓存是可用性特性，坏了不能让「画不了画」。
- 数据集 Quick, Draw! by Google, Inc.，CC BY 4.0（署名见
  `QUICKDRAW-ATTRIBUTION.md`）。数据文件由 `scripts/quickdraw_prep.py`
  离线准备，运行时不碰外网。
"""
from __future__ import annotations

import json
import logging
import random
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

from .bitmap_svg import BitmapResult, polylines_to_preview_png, polylines_to_svg
from .config import settings
from .llm_svg import _validate_svg

logger = logging.getLogger(__name__)

_CATEGORY_PATH = Path(__file__).with_name("quickdraw_categories.json")
# Quick Draw 简化版数据已归一化到 256×256；vpype layout 负责缩放到毫米画布。
_CANVAS_SIZE = 256
# 类目数据常驻上限：与位图中心线缓存同量级（16 项），超出 LRU 逐出。
_STROKE_CACHE_SIZE = 16

# 命中失败时剥一层画动词/量词前缀再查一次（「画一只猫」→「猫」、「画猫」→「猫」）。
# 只剥开头、只剥一次；备择最长在前（Python 正则按序取首个命中）。
# 裸「画」保留：「画小猫」类说法覆盖面大；代价是「画」作名词语素的词
# （画家/画板/画册…）会被误剥，由 _NO_STRIP 显式拦截——**往映射表新增
# 单字键时必须复查这类冲突**（2026-08-24 review 发现「画家」→「家」错配）。
_PREFIX_RE = re.compile(
    r"^(?:画一[只个幅张条朵棵辆架艘匹头尾群颗粒]|来一[只个幅张条]|"
    r"画[只个幅张条朵棵辆架艘匹头尾群颗粒]|一[只个幅张条朵棵辆架艘匹头尾群颗粒]|画)"
)
# 「画」作名词语素的整词不剥离（精确整词命中仍优先，如「画笔」在映射表内）。
_NO_STRIP = frozenset({"画家", "画板", "画册", "画纸", "画室", "画像", "画展", "画风", "画布"})

# 进程内同类失败只 warning 一次（防日志刷屏）：key = (原因, 类目)。
_warned: set[tuple[str, str]] = set()
_rng = random.SystemRandom()


def _warn_once(reason: str, category: str, detail: str = "") -> None:
    key = (reason, category)
    if key in _warned:
        return
    _warned.add(key)
    logger.warning("Quick Draw %s（category=%s）%s", reason, category, detail)


def _quickdraw_dir() -> Path | None:
    """HUTUJI_QUICKDRAW_DIR；空值 = 禁用。getattr 兼容测试注入的窄 settings。"""
    raw = getattr(settings, "quickdraw_dir", "")
    if not raw:
        return None
    return Path(raw)


@lru_cache(maxsize=1)
def _category_map() -> dict[str, str]:
    """加载中文词 → 官方类目映射（不含 _meta）。文件随包入 git，必定在场；
    损坏属部署事故，让它抛——服务 import 期不做磁盘探测是另一回事，
    本函数只在首次 lookup 时被调。"""
    data = json.loads(_CATEGORY_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _match_category(description: str) -> str | None:
    mapping = _category_map()
    hit = mapping.get(description)
    if hit is not None:
        return hit
    if description in _NO_STRIP:
        return None
    stripped = _PREFIX_RE.sub("", description, count=1)
    if stripped and stripped != description:
        return mapping.get(stripped)
    return None


@lru_cache(maxsize=_STROKE_CACHE_SIZE)
def _load_entries(data_dir: str, category: str) -> tuple | None:
    """读一类目的笔画 JSONL（prep 脚本产物），返回 entries 元组（LRU 需可哈希）。

    每行 {"strokes": [[[x...],[y...]], ...]}；装入即转 np 折线，随机取用。
    文件缺失/坏行 → None（调用方按 miss 处理）；坏行不污染整类：跳过该行。
    """
    path = Path(data_dir) / f"{category}.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        _warn_once("类目数据缺失", category)
        return None
    entries = []
    bad = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            strokes = json.loads(line)["strokes"]
            polys = tuple(
                np.column_stack((np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)))
                for xs, ys in strokes
                if len(xs) >= 2 and len(xs) == len(ys)
            )
            # 非有限坐标（NaN/Inf，json.loads 会收）在数据边界拒收：进入 SVG
            # 会格式化成 "nan" 字符串，虽下游 vpype/validate_gcode 仍 fail-closed，
            # 但边界拦截让坏数据在装载期就现形并按坏行计数。
            if polys and all(np.isfinite(p).all() for p in polys):
                entries.append(polys)
            elif polys:
                bad += 1
        except (ValueError, KeyError, TypeError):
            bad += 1
    if bad:
        _warn_once("坏行", category, f"跳过 {bad} 行")
    if not entries:
        _warn_once("类目数据为空", category)
        return None
    return tuple(entries)


def lookup(description: str) -> BitmapResult | None:
    """本地缓存查找：命中返回与位图路线同形态的 BitmapResult；未命中返回 None。

    同步纯本地（读已缓存的笔画文件 + 拼 SVG/PNG），供 `asyncio.to_thread` 调用。
    description 须已过 `_sanitize_description`（server 侧保证）。
    """
    data_dir = _quickdraw_dir()
    if data_dir is None:
        return None
    try:
        category = _match_category(description)
    except Exception:  # noqa: BLE001 — 映射表损坏属部署事故；按 miss 回落，不拖垮出图
        _warn_once("映射表加载失败", "-")
        return None
    if category is None:
        return None
    entries = _load_entries(str(data_dir), category)
    if not entries:
        return None
    polys = list(_rng.choice(entries))
    svg = polylines_to_svg(polys, _CANVAS_SIZE, _CANVAS_SIZE)
    try:
        _validate_svg(svg)
    except Exception:  # noqa: BLE001 — 数据被篡改也止于白名单；按 miss 回落 AI
        _warn_once("SVG 白名单校验失败", category)
        return None
    preview = polylines_to_preview_png(polys, _CANVAS_SIZE, _CANVAS_SIZE)
    logger.info("Quick Draw 命中 category=%s entries=%d", category, len(entries))
    return BitmapResult(svg=svg, preview_png=preview)
