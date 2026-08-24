"""Quick Draw 数据准备（离线）：官方简化版 ndjson → 运行时笔画 JSONL。

设计稿 `docs/design/quickdraw-local-cache.md` §3。产物每类一个
`<category>.jsonl`，每行 `{"strokes": [[[x...],[y...]], ...]}`（丢掉 t 通道，
写字机不需要时间维），供 `hutuji_mcp/quickdraw.py` 的 `_load_entries` 消费。

- 过滤官方 `recognized=true` 标记后**蓄水池采样**（单遍流式，不整载入内存；
  类目源文件 50~150MB），固定随机种子保证同参数同产出、可复现。
- 类目名即 GCS 路径分量，写错的类目会被 404 直接打出来（自校验），
  不另行维护官方 categories.txt 副本。
- 同步、无第三方依赖（stdlib urllib）；失败类目汇总到结尾，任一失败退 1。

用法：
    python scripts/quickdraw_prep.py                      # 全量（映射表所有类目）
    python scripts/quickdraw_prep.py --only cat,dog --per-category 20   # 试跑
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
from urllib.parse import quote
from pathlib import Path

_GCS_BASE = "https://storage.googleapis.com/quickdraw_dataset/full/simplified"
_CATEGORY_JSON = Path(__file__).resolve().parent.parent / "hutuji_mcp" / "quickdraw_categories.json"
_DEFAULT_OUT = Path(__file__).resolve().parent.parent.parent / "tmp" / "quickdraw-data"
_UA = "hutuji-quickdraw-prep/1.0"
_TIMEOUT_SECONDS = 60
_ATTEMPTS = 2


def _load_categories(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    # 去重保序：同一类目可被多个中文词映射
    return list(dict.fromkeys(v for k, v in data.items() if not k.startswith("_")))


def _iter_ndjson_lines(category: str):
    """流式拉一类目的官方简化 ndjson；404 直接抛（类目名写错的自校验）。类目名含空格
    须经 quote 编码（「sea turtle」→「sea%20turtle」），否则 urllib 抛 InvalidURL。"""
    url = f"{_GCS_BASE}/{quote(category)}.ndjson"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    # 单次尝试、不在本层重试：中途断流的重试必须发生在整类目级别（见
    # _prep_category），否则本生成器从头重 yield 而调用方的蓄水池/计数不重置，
    # 已消费行会重复入池，「同种子同产出」就被网络状况绑架了。
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        for raw in resp:
            yield raw


def _prep_category(category: str, per_category: int, out_dir: Path, seed: int) -> tuple[int, int, int]:
    """单类目：流式过官方 ndjson，蓄水池采样 recognized 条目，写 JSONL。

    返回 (recognized 总数, 采样数, 输出字节数)。
    """
    # 重试在整类目级别：每次尝试都从全新 rng/蓄水池/计数开始，重试后的产物
    # 与无重试的干净单遍逐字节一致（可复现性不被断流点污染）。
    for attempt in range(1, _ATTEMPTS + 1):
        rng = random.Random(f"{seed}:{category}")
        reservoir: list[dict] = []
        recognized = 0
        try:
            for raw in _iter_ndjson_lines(category):
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if not row.get("recognized"):
                    continue
                strokes = []
                for stroke in row.get("drawing", []):
                    # 简化版笔画为 [x[], y[]]（部分带第三路 t[]，忽略）
                    if len(stroke) < 2:
                        continue
                    xs, ys = stroke[0], stroke[1]
                    if len(xs) >= 2 and len(xs) == len(ys):
                        strokes.append([xs, ys])
                if not strokes:
                    continue
                recognized += 1
                # 蓄水池采样：第 i 条以 N/i 概率替换随机槽位，单遍等概
                if len(reservoir) < per_category:
                    reservoir.append({"strokes": strokes})
                else:
                    j = rng.randrange(recognized)
                    if j < per_category:
                        reservoir[j] = {"strokes": strokes}
            break
        except urllib.error.HTTPError:
            raise  # 404/403 不重试：类目名或权限问题，重试无意义
        except (urllib.error.URLError, TimeoutError):
            if attempt == _ATTEMPTS:
                raise
    out_path = out_dir / f"{category}.jsonl"
    payload = "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in reservoir)
    # newline="\n"：Windows text mode 会把 \n 翻成 \r\n，产物须跨平台逐字节一致
    # （部署纪律要求 CR=0；运行时 splitlines 虽兼容 CRLF，但哈希对账不许依赖它）。
    out_path.write_text(payload, encoding="utf-8", newline="\n")
    return recognized, len(reservoir), len(payload.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quick Draw 数据准备（离线）")
    parser.add_argument("--categories-file", type=Path, default=_CATEGORY_JSON)
    parser.add_argument("--per-category", type=int, default=200)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=20260824)
    parser.add_argument("--only", type=str, default="", help="逗号分隔的类目子集（试跑用）")
    args = parser.parse_args(argv)

    categories = _load_categories(args.categories_file)
    if args.only:
        wanted = {c.strip() for c in args.only.split(",") if c.strip()}
        unknown = wanted - set(categories)
        if unknown:
            print(f"ERROR: --only 含映射表外类目: {sorted(unknown)}", file=sys.stderr)
            return 2
        categories = [c for c in categories if c in wanted]
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"类目数={len(categories)} 每类采样={args.per_category} 种子={args.seed} 输出={args.out}")
    failures: list[str] = []
    total_bytes = 0
    for i, category in enumerate(categories, 1):
        try:
            recognized, sampled, nbytes = _prep_category(category, args.per_category, args.out, args.seed)
        except Exception as exc:  # noqa: BLE001 — 单类失败不拖垮整批，结尾汇总
            print(f"[{i}/{len(categories)}] {category}: FAIL {type(exc).__name__}", file=sys.stderr)
            failures.append(category)
            continue
        total_bytes += nbytes
        print(f"[{i}/{len(categories)}] {category}: recognized={recognized} sampled={sampled} bytes={nbytes}")
    print(f"完成 {len(categories) - len(failures)}/{len(categories)} 类，总输出 {total_bytes / 1024 / 1024:.1f}MiB")
    if failures:
        print(f"失败类目: {failures}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
