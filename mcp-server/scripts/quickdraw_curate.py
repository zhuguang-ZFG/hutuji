#!/usr/bin/env python
"""Quick Draw 样本精选：按质量分把每类样本筛成头部子集（2026-08-25 用户裁决 C 组合）。

Quick, Draw! 每类样本质量方差极大，「灵魂涂鸦」与「像样简笔画」混在一起，
运行时随机抽取导致出图质量不稳定（当日实证：AI 链鱼像样、QD 链鱼为涂鸦）。
本脚本按可解释三因子打分，把每类裁到头部 N 个（默认 50），运行时随机抽样即保底。

三因子（权重和为 1）：
- 平滑度 0.4：相邻线段转角均值越大越「抖」，灵魂涂鸦的典型特征；
- 构图 0.3：包围盒面积占画布 10%~85% 得满分，过小/过大线性衰减；
- 笔画数 0.3：2~8 笔得满分，单笔过简与多笔糊线性衰减。

确定性：分数相同按原文件行序稳定排序，不引入随机性。
用法：
    python scripts/quickdraw_curate.py --data-dir tmp/quickdraw-data --keep 50
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

CANVAS = 256.0


def _bbox(strokes: list[list[list[float]]]) -> tuple[float, float, float, float]:
    xs = [x for s in strokes for x in s[0]]
    ys = [y for s in strokes for y in s[1]]
    return min(xs), min(ys), max(xs), max(ys)


def smoothness_score(strokes: list[list[list[float]]]) -> float:
    """相邻线段转角均值映射：0（乱抖）~1（顺滑）。"""
    turns: list[float] = []
    for s in strokes:
        xs, ys = s[0], s[1]
        for i in range(1, len(xs) - 1):
            ax, ay = xs[i] - xs[i - 1], ys[i] - ys[i - 1]
            bx, by = xs[i + 1] - xs[i], ys[i + 1] - ys[i]
            na = math.hypot(ax, ay)
            nb = math.hypot(bx, by)
            if na < 1e-6 or nb < 1e-6:
                continue
            cos = max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))
            turns.append(math.acos(cos))
    if not turns:
        return 0.5
    return 1.0 / (1.0 + sum(turns) / len(turns))


def fill_score(strokes: list[list[list[float]]]) -> float:
    x0, y0, x1, y1 = _bbox(strokes)
    fill = (x1 - x0) * (y1 - y0) / (CANVAS * CANVAS)
    if fill <= 0:
        return 0.0
    if 0.10 <= fill <= 0.85:
        return 1.0
    if fill < 0.10:
        return max(0.0, fill / 0.10)
    return max(0.0, (1.0 - fill) / (1.0 - 0.85))


def stroke_score(strokes: list[list[list[float]]]) -> float:
    n = len(strokes)
    if 2 <= n <= 8:
        return 1.0
    if n < 2:
        return max(0.0, n / 2.0)
    return max(0.0, 1.0 - (n - 8) / 16.0)


def quality(strokes: list[list[list[float]]]) -> float:
    return (
        0.4 * smoothness_score(strokes)
        + 0.3 * fill_score(strokes)
        + 0.3 * stroke_score(strokes)
    )


def curate_file(path: Path, keep: int) -> tuple[int, int]:
    entries = []
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            strokes = json.loads(line)["strokes"]
            if not strokes:
                continue
        except Exception:
            continue
        entries.append((quality(strokes), idx, line))
    before = len(entries)
    entries.sort(key=lambda e: (-e[0], e[1]))
    kept = entries[:keep]
    path.write_text("\n".join(e[2] for e in kept) + "\n", encoding="utf-8")
    return before, len(kept)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--keep", type=int, default=50)
    args = ap.parse_args()
    data = Path(args.data_dir)
    files = sorted(data.glob("*.jsonl"))
    if not files:
        print(f"no jsonl under {data}", file=sys.stderr)
        return 1
    total_before = total_after = 0
    for f in files:
        b, a = curate_file(f, args.keep)
        total_before += b
        total_after += a
        print(f"  {f.name}: {b} -> {a}")
    print(f"curated {len(files)} categories: {total_before} -> {total_after} samples")
    return 0


if __name__ == "__main__":
    sys.exit(main())
