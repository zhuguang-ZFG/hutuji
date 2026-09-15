"""Seed the curated library from Twemoji vectors (graphics CC-BY 4.0) — free channel.

与付费生图灌库（curated_prep.py）并行：对映射表覆盖的类目，直接下载 Twemoji
SVG、按 vpype 展平成单线折线、过同一入库闸（gate v3）后写入同一库布局。
矢量→矢量、无栅格化/骨架化，天然避开 AI 纹理、排线与实心块病灶。
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np
import vpype

from hutuji_mcp.bitmap_svg import polylines_to_svg
from hutuji_mcp.curated_gate import GateReject, gate_svg
from curated_prep import next_index, recount_and_write_manifest

MAPPING_JSON = ROOT / "hutuji_mcp" / "twemoji_categories.json"
BASE_URL = "https://raw.githubusercontent.com/jdecked/twemoji/master/assets/svg"

# 36 单位画布 → 190mm 纸面 ~0.63mm 段：过 gate v3 short_g1 闸（<0.5mm 短段比）
# 又保住曲线细节；勿按「更粗=更快」上调，短段比闸会拒。
QUANTIZATION = 0.12
# 行数闸对齐：gate v3 拒 >800 G1 行；贝塞尔展平在 0.12 量化下复杂件可到
# ~2900 行。转换后接生产同款 RDP（首档 0.05 单位 ≈ 产线 0.28mm 质量口径），
# 逐级放宽直到 ≤780 点（留 20 行余量给 Z/抬笔指令）。
_RDP_TOL_START = 0.05
_RDP_TOL_FACTOR = 1.6
_MAX_PLOTTER_POINTS = 780

def load_mapping() -> dict:
    data = json.loads(MAPPING_JSON.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def convert_svg(svg_text: str, code: str, *, smooth: bool = True) -> str:
    """Twemoji SVG → 单线 plotter SVG（区域边界即笔画；填充/描边皆可）。

    行数自适应：首档 RDP 0.05 单位 ≈ 产线 0.28mm；超 780 点逐级 1.6× 放宽。
    """
    from hutuji_mcp.bitmap_svg import _rdp_polylines

    lc, w, h = vpype.read_svg(io.StringIO(svg_text), quantization=QUANTIZATION)
    polys = [
        np.array([[float(z.real), float(z.imag)] for z in line], dtype=float)
        for line in lc
        if len(line) >= 2
    ]
    if not polys:
        raise ValueError(f"{code}: 无可画路径")
    tol = _RDP_TOL_START
    for _ in range(6):
        polys = _rdp_polylines(polys, tol)
        if sum(len(p) for p in polys) <= _MAX_PLOTTER_POINTS:
            break
        tol *= _RDP_TOL_FACTOR
    # 市场矢量导出不向下截断物理尺寸，否则贴边坐标会落到新视口外，
    # 再次由 vpype 读取时整条右/下边界会被裁掉。
    width, height = (math.ceil(w) + 1, math.ceil(h) + 1) if not smooth else (int(w), int(h))
    return polylines_to_svg(polys, width, height, smooth=smooth)


def fetch_code(code: str, cache_dir: Path) -> str:
    cache = cache_dir / f"{code}.svg"
    if not cache.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        url = f"{BASE_URL}/{code}.svg"
        with urllib.request.urlopen(url, timeout=20) as resp:
            cache.write_bytes(resp.read())
    return cache.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed curated library from Twemoji vectors")
    parser.add_argument("--categories", default="", help="逗号分隔类目子集；默认映射表全量")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "tmp" / "curated-vector-data")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "tmp" / "twemoji-cache")
    parser.add_argument("--n-target", type=int, default=50)
    parser.add_argument("--n-min", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true", help="只转换过闸不写盘")
    args = parser.parse_args(argv)

    mapping = load_mapping()
    wanted = set(args.categories.split(",")) if args.categories else set(mapping)
    failures = 0
    accepted = 0
    for cat in sorted(mapping):
        if cat not in wanted:
            continue
        cat_dir = args.out_dir / cat
        for code in mapping[cat]:
            try:
                svg_text = fetch_code(code, args.cache_dir)
            except Exception as exc:  # noqa: BLE001 -- 404/网络瞬态
                print(f"{cat}/{code}: 下载失败（{type(exc).__name__}）", file=sys.stderr)
                failures += 1
                continue
            try:
                plotter = convert_svg(svg_text, code)
            except ValueError as exc:
                print(f"{cat}/{code}: 转换失败：{exc}", file=sys.stderr)
                failures += 1
                continue
            if args.dry_run:
                print(f"{cat}/{code}: dry-run 转换 OK（{len(plotter)} 字节）")
                accepted += 1
                continue
            try:
                with tempfile.TemporaryDirectory(prefix="twemoji-gate-") as work:
                    gate_svg(plotter, work_dir=Path(work))
            except GateReject as exc:
                print(f"{cat}/{code}: reject {exc.reason}", file=sys.stderr)
                failures += 1
                continue
            cat_dir.mkdir(parents=True, exist_ok=True)
            index = next_index(cat_dir)
            (cat_dir / f"{index:03d}.svg").write_text(plotter, encoding="utf-8", newline="\n")
            print(f"{cat}/{code}: accept {index:03d}.svg", flush=True)
            accepted += 1
    if not args.dry_run:
        manifest = recount_and_write_manifest(args.out_dir, args.n_target, args.n_min)
        ready_cats = sum(1 for row in manifest["categories"].values() if row["count"] >= args.n_min)
        print(f"manifest: ready={manifest['ready']} 达标类目={ready_cats}/{len(manifest['categories'])}")
    print(f"done: accept={accepted} failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
