"""精品图库入库管线（设计 docs/design/drawing-market-20260914.md，契约 §10.4.23）。

离线工具，非运行时。链路：

    源 SVG（Twemoji 下载 / 外部文件）
      → license 闸（源注册表白名单；manual 源必须显式 --license/--attribution）
      → 通用转换：vpype 展平 + RDP 行数自适应（复用 curated_vector_prep.convert_svg）
      → curated_gate.gate_svg 过闸（注入白名单/§5/过碎/无墨/行数）
      → 产物落盘 svg/ + gcode/ + preview/（预览 = polylines_to_preview_png）
      → manifest.json 按 id 幂等替换

用法：
    # Twemoji 批量（内置注册表，自动许可与署名）
    python market_ingest.py --source twemoji --market-dir ../market --limit 5

    # 外部/手工 SVG 单件
    python market_ingest.py --svg-file dog.svg --id my-dog --title 小狗 \
        --category 动物 --license CC-BY-4.0 --attribution "Twemoji, CC-BY 4.0" \
        --source manual --market-dir ../market

    所有模式都须提供 --license-file、--source-revision；单件还须 --source-url。
    默认 reviewed=false，不进入公开目录；人工核对画质、署名与展示文案后才加 --reviewed。
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "scripts"), str(ROOT.parent / "draw-portal")):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np  # noqa: E402

from hutuji_mcp.market_schema import CATEGORIES, ID_RE, LICENSE_WHITELIST, validate_entry  # noqa: E402
from hutuji_mcp.bitmap_svg import polylines_to_preview_png  # noqa: E402
from hutuji_mcp.curated_gate import GATE_VERSION, GateReject, gate_svg  # noqa: E402
from curated_vector_prep import convert_svg  # noqa: E402  # 复用 Twemoji 转换链

# 源注册表（2026-09-14 用户拍板许可红线）：新增源必须补 license 全文到
# market/licenses/<source>.txt 并在此登记。SA/NC 一律不收。
SOURCES: dict[str, dict[str, str]] = {
    "twemoji": {
        "license": "CC-BY-4.0",
        "attribution": "Twemoji (https://twemoji.twitter.com/), CC-BY 4.0",
        "base_url": "https://raw.githubusercontent.com/jdecked/twemoji/master/assets/svg",
    },
    "manual": {
        # 手工/外部单件：license 与 attribution 必须命令行显式给出，
        # 且 license ∈ market.LICENSE_WHITELIST 才会被接受。
        "license": "",
        "attribution": "",
    },
}

_POINTS_RE = re.compile(r"<polyline\b[^>]*\bpoints=\"([^\"]+)\"", re.I)
_VIEWBOX_DIM_RE = re.compile(r"viewBox=\"\s*-?[\d.]+\s+[ ,]\s*-?[\d.]+\s+[ ,]\s*([\d.]+)\s+[ ,]\s*([\d.]+)\"", re.I)


def _extract_polylines(svg_text: str) -> tuple[list[np.ndarray], int, int]:
    import vpype

    lines, w, h = vpype.read_svg(io.StringIO(svg_text), quantization=1.0)
    polys = [np.column_stack((line.real, line.imag)) for line in lines if len(line) >= 2]
    if not polys:
        raise ValueError("转换产物无可画 polyline")
    return polys, w, h


def _quality_score(polys: list[np.ndarray]) -> float:
    """长笔比例与有效折点的排序分；不能据此宣称画质通过人工复核。"""
    total_pts = sum(len(p) for p in polys)
    if total_pts < 2:
        return 0.0
    segs = [np.diff(p, axis=0) for p in polys]
    seg_lens = [float(np.hypot(s[:, 0], s[:, 1]).sum()) for s in segs]
    total_len = sum(seg_lens) or 1e-9
    # 长笔占比高（>0.6）说明是干净的整笔曲线而非碎段拼接
    long_ratio = sum(1 for l in seg_lens if l > 0.15 * total_len) / len(seg_lens)
    pts_score = min(1.0, total_pts / 300.0)  # 300 点左右信息量足
    return round(0.5 * long_ratio + 0.5 * pts_score, 4)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        stream.write(data)
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def _market_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".ingest.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError("已有入库进程，请等待完成；异常退出需核实进程后清理入库锁") from None
    try:
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def _validate_source(svg: str) -> None:
    from xml.etree import ElementTree
    from hutuji_mcp.llm_svg import _DOCTYPE_RE

    if len(svg.encode("utf-8")) > 1024 * 1024:
        raise ValueError("源 SVG 超过输入预算")
    if _DOCTYPE_RE.search(svg):
        raise ValueError("源 SVG 不得包含 DTD 或实体声明")
    root = ElementTree.fromstring(svg)
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] not in {"svg", "g", "path", "line", "polyline", "polygon", "rect", "circle", "ellipse", "title", "desc"}:
            raise ValueError("源 SVG 含不支持的元素")
        for name, value in node.attrib.items():
            if (name.rsplit("}", 1)[-1].lower().startswith("on") or "href" in name.lower() or
                    "url(" in value.lower() or name.lower() == "style"):
                raise ValueError("源 SVG 不得包含脚本或外部引用")


def ingest_one(
    *,
    market_dir: Path,
    svg_text: str,
    item_id: str,
    title: str,
    category: str,
    license_: str,
    attribution: str,
    source: str,
    source_url: str = "",
    source_revision: str = "",
    license_file: Path | None = None,
    reviewed: bool = False,
) -> dict:
    """单件入库：转换 → 闸 → 产物 → manifest。同 id 幂等覆盖。"""
    if license_ not in LICENSE_WHITELIST:
        raise SystemExit(f"拒收 id={item_id}: license {license_!r} 不在白名单")
    if license_ == "CC-BY-4.0" and not attribution:
        raise SystemExit(f"拒收 id={item_id}: CC-BY 缺署名")
    if (not isinstance(item_id, str) or not ID_RE.fullmatch(item_id) or category not in CATEGORIES or
            not isinstance(source, str) or not ID_RE.fullmatch(source) or not source_revision):
        raise ValueError("须提供合法标识、中文类目和固定来源版本")
    if license_file is None:
        raise ValueError("须提供来源许可全文文件")
    with license_file.open("rb") as stream:
        license_bytes = stream.read(256 * 1024 + 1)
    if not license_bytes.strip() or len(license_bytes) > 256 * 1024:
        raise ValueError("来源许可文件为空或超过预算")

    _validate_source(svg_text)
    plotter_svg = convert_svg(svg_text, item_id)

    with tempfile.TemporaryDirectory() as td:
        try:
            stats = gate_svg(plotter_svg, work_dir=Path(td))
        except GateReject as exc:
            raise SystemExit(f"闸拒收 id={item_id}: {exc.reason}") from exc
        gcode_bytes = (Path(td) / "gate.gcode").read_bytes()

    polys, w, h = _extract_polylines(plotter_svg)
    preview_png = polylines_to_preview_png(polys, w, h, smooth=False)
    svg_raw = plotter_svg.encode("utf-8")
    svg_hash = hashlib.sha256(svg_raw).hexdigest()
    preview_hash = hashlib.sha256(preview_png).hexdigest()
    gcode_hash = hashlib.sha256(gcode_bytes).hexdigest()
    license_hash = hashlib.sha256(license_bytes).hexdigest()

    entry = {
        "id": item_id,
        "title": title,
        "category": category,
        "license": license_,
        "attribution": attribution,
        "source": source,
        "source_url": source_url,
        "source_revision": source_revision,
        "path": f"svg/{category}/{item_id}-{svg_hash[:16]}.svg",
        "gcode_path": f"gcode/{category}/{item_id}-{gcode_hash[:16]}.gcode",
        "preview_path": f"preview/{category}/{item_id}-{preview_hash[:16]}.png",
        "license_path": f"licenses/{source}-{license_hash[:16]}.txt",
        "svg_sha256": svg_hash,
        "gcode_sha256": gcode_hash,
        "preview_sha256": preview_hash,
        "license_sha256": license_hash,
        "changes": "原素材转换为单线绘图，调整路径与构图",
        "reviewed": reviewed,
        "viewBox": f"0 0 {w} {h}",
        "bytes": len(plotter_svg.encode("utf-8")),
        "quality_score": _quality_score(polys),
        "ingested_at": date.today().isoformat(),
        "gate_version": GATE_VERSION,
    }
    validate_entry(entry)
    with _market_lock(market_dir):
        manifest_path = market_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"version": 1, "items": []}
        if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("items"), list):
            raise ValueError("现有清单格式无效，不能覆盖")
        for existing in manifest["items"]:
            validate_entry(existing)
        for field, raw in (("path", svg_raw), ("gcode_path", gcode_bytes), ("preview_path", preview_png), ("license_path", license_bytes)):
            _atomic_write(market_dir / entry[field], raw)
        items = [e for e in manifest["items"] if e["id"] != item_id] + [entry]
        manifest["items"] = sorted(items, key=lambda e: (e["category"], e["id"]))
        _atomic_write(manifest_path, (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return entry


def cmd_twemoji(args: argparse.Namespace) -> None:
    src = SOURCES["twemoji"]
    if not re.fullmatch(r"[a-f0-9]{40}", args.source_revision):
        raise SystemExit("批量下载须使用已核对的 40 位来源 commit，不能使用滚动分支")
    # mapping 结构：{英文别名: [hex codepoint, ...]}（如 airplane -> ["2708"]）。
    # URL 用 hex 码小写（curated_vector_prep 同款口径，GitHub raw 大小写不敏感）；
    # 类目中文化走 --category-map（别名→中文类目 JSON），未覆盖时沿用别名。
    mapping = json.loads((ROOT / "hutuji_mcp" / "twemoji_categories.json").read_text(encoding="utf-8"))
    mapping = {k: v for k, v in mapping.items() if not k.startswith("_")}
    category_map: dict[str, str] = {}
    if getattr(args, "category_map", None):
        category_map = json.loads(Path(args.category_map).read_text(encoding="utf-8"))
    market_dir: Path = args.market_dir
    done = 0
    for alias in sorted(mapping):
        if args.limit and done >= args.limit:
            break
        category = category_map.get(alias, alias)
        for code in mapping[alias]:
            if args.limit and done >= args.limit:
                break
            item_id = f"twemoji-{code}"
            url = f"{src['base_url'].replace('/master/', '/' + args.source_revision + '/')}/{code}.svg"
            try:
                # GitHub raw 对 Python-urllib 默认 UA 回 403；必须带浏览器 UA。
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0 (compatible; hutuji-market-ingest)"}
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read(1024 * 1024 + 1)
                    if len(raw) > 1024 * 1024:
                        raise ValueError("源 SVG 超过预算")
                    svg_text = raw.decode("utf-8")
            except Exception as exc:  # noqa: BLE001 — 单件失败不断批
                print(f"跳过 {alias}/{code}: 下载失败 {type(exc).__name__}", file=sys.stderr)
                continue
            try:
                entry = ingest_one(
                    market_dir=market_dir,
                    svg_text=svg_text,
                    item_id=item_id,
                    title=alias,
                    category=category,
                    license_=src["license"],
                    attribution=src["attribution"],
                    source="twemoji",
                    source_url=url,
                    source_revision=args.source_revision, license_file=args.license_file, reviewed=args.reviewed,
                )
            except (SystemExit, ValueError) as exc:
                print(f"跳过 {alias}/{code}: {exc}", file=sys.stderr)
                continue
            done += 1
            print(
                f"入库 {entry['id']} → {entry['category']} "
                f"quality={entry['quality_score']} bytes={entry['bytes']}"
            )
    print(f"Twemoji 批量完成: {done} 件")
    if not done:
        raise SystemExit("没有素材通过入库，请检查分类映射、来源和许可记录")


def cmd_file(args: argparse.Namespace) -> None:
    license_ = args.license or SOURCES["manual"]["license"]
    attribution = args.attribution or SOURCES["manual"]["attribution"]
    if args.source != "manual" and args.source in SOURCES:
        license_ = license_ or SOURCES[args.source]["license"]
        attribution = attribution or SOURCES[args.source]["attribution"]
    svg_text = Path(args.svg_file).read_text(encoding="utf-8")
    entry = ingest_one(
        market_dir=args.market_dir,
        svg_text=svg_text,
        item_id=args.id,
        title=args.title,
        category=args.category,
        license_=license_,
        attribution=attribution,
        source=args.source,
        source_url=getattr(args, "source_url", ""),
        source_revision=args.source_revision, license_file=args.license_file, reviewed=args.reviewed,
    )
    print(f"入库 {entry['id']} → {entry['category']} quality={entry['quality_score']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="精品图库入库管线")
    ap.add_argument("--market-dir", type=Path, required=True, help="市场数据根（含 manifest.json）")
    ap.add_argument("--source", default="manual", choices=sorted(SOURCES))
    ap.add_argument("--limit", type=int, default=0, help="批量源最多入库件数（0=不限）")
    ap.add_argument("--svg-file", help="外部/手工 SVG 路径")
    ap.add_argument("--id", help="条目 id（幂等键，如 twemoji-1f415）")
    ap.add_argument("--title", help="条目展示名")
    ap.add_argument("--category", help="类目（须与设计文档类目一致）")
    ap.add_argument("--license", dest="license", help="SPDX 许可标识（manual 源必填）")
    ap.add_argument("--attribution", help="署名（CC-BY 必填）")
    ap.add_argument("--source-url", default="", help="溯源 URL")
    ap.add_argument("--source-revision", required=True, help="已核对的来源 commit 或版本")
    ap.add_argument("--license-file", type=Path, required=True, help="来源许可全文，随产物保存")
    ap.add_argument("--reviewed", action="store_true", help="已逐件复核画质、许可、署名和小程序展示文案")
    ap.add_argument("--category-map", help="别名→中文类目 JSON（Twemoji 批量用）")
    args = ap.parse_args()

    if args.svg_file:
        for field in ("id", "title", "category"):
            if not getattr(args, field):
                ap.error(f"--svg-file 模式必须提供 --{field}")
        cmd_file(args)
    elif args.source == "twemoji":
        cmd_twemoji(args)
    else:
        ap.error("当前源不支持批量模式；请用 --svg-file 单件入库，或选 --source twemoji")


if __name__ == "__main__":
    main()
