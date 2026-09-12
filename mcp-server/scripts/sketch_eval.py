"""离线照片精修对比：保留 1px 原尺寸栅格、SVG、真实 G-code 与可浏览报告。

样张只落 results/local-only，不联网、不发设备；基线取显式 Git ref，避免把
中途调参版本误当成生产。墨覆盖只能量负载，画质仍需看原图和细节对照。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import types
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageOps

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "mcp-server"))

from hutuji_mcp import gcode_gen, sketch_render
from hutuji_mcp.bitmap_svg import polylines_to_svg
from hutuji_mcp.gcode_gen import svg_to_gcode, validate_gcode
from hutuji_mcp.llm_svg import _validate_svg


def _baseline(ref: str) -> types.ModuleType:
    source = subprocess.run(
        ["git", "show", f"{ref}:mcp-server/hutuji_mcp/sketch_render.py"],
        cwd=_ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    module = types.ModuleType("sketch_baseline")
    exec(compile(source, f"{ref}/sketch_render.py", "exec"), module.__dict__)
    return module


def rasterize(polys: list[np.ndarray], w: int, h: int) -> Image.Image:
    """按工作栅格逐线画 1px，不把设备缩略图放大当纸面。"""
    raster = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(raster)
    for poly in polys:
        draw.line([tuple(xy) for xy in np.rint(poly).astype(int)], fill=0, width=1)
    return raster


def _gcode_raster(text: str, *, profile) -> Image.Image:
    """把已过 §5 的最终文件画回撰写页，包含短段塌缩与 G2/G3 弧拟合结果。"""
    pen_down = False
    motion = 0
    position = None
    paths: list[np.ndarray] = []
    transpose = gcode_gen._transpose_xy_enabled() if profile is None else profile.transpose_xy
    for raw in text.splitlines():
        words = [(key, float(value)) for key, value in gcode_gen._WORD_RE.findall(raw.split(";", 1)[0])]
        values = dict(words)
        for key, value in words:
            if key == "G" and value in (0, 1, 2, 3):
                motion = int(value)
        if "Z" in values:
            pen_down = values["Z"] == 5
        if "X" not in values and "Y" not in values:
            continue
        target = np.array([values.get("X", position[0] if position is not None else 0), values.get("Y", position[1] if position is not None else 0)])
        if position is not None and pen_down and motion in (1, 2, 3):
            points = np.array([position, target])
            if motion in (2, 3):
                clockwise = motion == 2
                cx, cy = gcode_gen._arc_center(
                    *position, *target, values.get("I"), values.get("J"), values.get("R"), clockwise,
                )
                radius = math.hypot(position[0] - cx, position[1] - cy)
                start = math.atan2(position[1] - cy, position[0] - cx)
                end = math.atan2(target[1] - cy, target[0] - cx)
                sweep = -((start - end) % math.tau) if clockwise else (end - start) % math.tau
                angles = np.linspace(start, start + sweep, max(2, math.ceil(abs(sweep) * radius * 8)))
                points = np.column_stack((cx + radius * np.cos(angles), cy + radius * np.sin(angles)))
                points[0], points[-1] = position, target
            paths.append(points[:, ::-1] if transpose else points)
        position = target
    # 与 vpype 撰写页使用同一毫米边界；4px/mm 只影响这份诊断图的显示。
    return rasterize([path * 4 for path in paths], round(gcode_gen.PAGE_AUTHOR_WIDTH_MM * 4), round(gcode_gen.PAGE_AUTHOR_HEIGHT_MM * 4))


def _render(
    module: types.ModuleType, raw: bytes, style: str, dest: Path, *, gcode: bool, profile,
) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    polys, w, h = module.render_sketch_polylines(raw, style=style)
    elapsed = time.perf_counter() - started
    smooth = module is not sketch_render
    svg = polylines_to_svg(polys, w, h, smooth=smooth)
    _validate_svg(svg)
    (dest / "draw.svg").write_text(svg, encoding="utf-8")
    # 对比导出后实际消费的 SVG，不能把平滑前的中间折线当作旧版交付效果。
    exported = [
        np.array([list(map(float, xy.split(","))) for xy in node.attrib["points"].split()])
        for node in ET.fromstring(svg).iter() if node.tag.endswith("polyline")
    ]
    raster = rasterize(exported, w, h)
    raster.save(dest / "fullres.png")
    metrics = {
        "width": w, "height": h, "polylines": len(polys),
        "points": sum(len(poly) for poly in polys),
        "ink_fraction": float((np.asarray(raster) == 0).mean()),
        "render_seconds": round(elapsed, 3), "section5": "未运行", "export_smoothing": smooth,
    }
    if gcode:
        svg_to_gcode(dest / "draw.svg", dest / "draw.gcode", profile=profile)
        data = (dest / "draw.gcode").read_bytes()
        text = data.decode("ascii")
        stats = validate_gcode(text, total_bytes=len(data), profile=profile)
        _gcode_raster(text, profile=profile).save(dest / "gcode.png")
        metrics.update(
            section5="通过", gcode_bytes=len(data),
            gcode_lines=stats.lines, pen_lifts=stats.pen_lifts,
        )
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-ref")
    parser.add_argument("--profile", choices=("mk38", "nopaper"), default="mk38")
    parser.add_argument("--styles", choices=("hatch", "squiggle"), nargs="+", default=["hatch", "squiggle"])
    parser.add_argument("--skip-gcode", action="store_true", help="仅调参预览；最终复验必须跑 G-code")
    args = parser.parse_args()
    dest = args.output.resolve()
    local_only = (_ROOT / "results/local-only").resolve()
    if not dest.is_relative_to(local_only):
        parser.error("样张输出必须位于 results/local-only 下")
    dest.mkdir(parents=True, exist_ok=True)
    variants = {"current": sketch_render}
    if args.baseline_ref:
        variants = {"baseline": _baseline(args.baseline_ref), **variants}
    paths: list[Path] = []
    for path in args.inputs:
        paths.extend(sorted(p for p in path.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg")) if path.is_dir() else [path])
    profile = gcode_gen.PROFILE_NOPAPER if args.profile == "nopaper" else None
    report: dict = {
        "baseline_ref": args.baseline_ref, "profile": args.profile,
        "renderer_sha256": hashlib.sha256(Path(sketch_render.__file__).read_bytes()).hexdigest(),
        "samples": [],
    }
    sections: list[str] = []
    failed = False
    for idx, path in enumerate(paths, 1):
        sample_id = f"sample-{idx:02d}"
        sample_dir = dest / sample_id
        sample_dir.mkdir(exist_ok=True)
        raw = path.read_bytes()
        with Image.open(path) as original:
            ImageOps.exif_transpose(original).convert("RGB").save(sample_dir / "source.png")
        sample = {"id": sample_id, "source": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "variants": {}}
        for style in args.styles:
            cards = [f'<figure><figcaption>原图</figcaption><a href="{sample_id}/source.png"><img src="{sample_id}/source.png"></a></figure>']
            for label, module in variants.items():
                key = f"{label}-{style}"
                rel = f"{sample_id}/{key}"
                try:
                    metrics = _render(module, raw, style, dest / rel, gcode=not args.skip_gcode, profile=profile)
                    summary = f'{metrics["polylines"]} 条折线 · 墨覆盖 {metrics["ink_fraction"]:.1%}'
                    if "gcode_bytes" in metrics:
                        summary += f' · {metrics["gcode_bytes"] / 1024:.1f} KiB · {metrics["pen_lifts"]} 次抬笔'
                    preview = "fullres.png" if args.skip_gcode else "gcode.png"
                    cards.append(f'<figure><figcaption>{html.escape(key)}<br>{summary}<br><a href="{rel}/fullres.png">SVG 1px 栅格</a> · <a href="{rel}/draw.svg">SVG</a></figcaption><a href="{rel}/{preview}"><img src="{rel}/{preview}"></a></figure>')
                except Exception as exc:
                    # 本机诊断只记异常类型；输入路径/上游内容不拼进网页错误正文。
                    metrics = {"error": type(exc).__name__}
                    cards.append(f'<figure><figcaption>{html.escape(key)}：{type(exc).__name__}</figcaption></figure>')
                    failed = True
                sample["variants"][key] = metrics
                print(json.dumps({"sample": sample_id, "variant": key, **metrics}, ensure_ascii=False), flush=True)
            sections.append(f'<h2>{sample_id} / {style}</h2><div class="row">{"".join(cards)}</div>')
        report["samples"].append(sample)
    (dest / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (dest / "index.html").write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>照片精修对比</title>'
        '<style>body{font:16px system-ui;margin:24px;background:#eee;color:#222}.row{display:flex;gap:16px;align-items:start}figure{margin:0;flex:1;min-width:0;background:white}figcaption{padding:12px;min-height:48px}img{display:block;width:100%;height:auto}h2{margin-top:40px}</style>'
        '<h1>照片精修对比</h1><p>正常评估展示最终 G-code 笔迹（4px/mm）；跳过转换时仅展示 SVG。点击查看原尺寸，另附 SVG 1px 栅格。纸面效果待实机确认。样张仅存本机。</p>'
        + "".join(sections) + '</html>', encoding="utf-8",
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
