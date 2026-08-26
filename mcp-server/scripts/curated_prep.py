"""Prepare an offline curated SVG library without enabling it at runtime."""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hutuji_mcp.curated_gate import GATE_VERSION, GateReject, gate_svg

CATEGORY_JSON = ROOT / "hutuji_mcp" / "quickdraw_categories.json"
DEFAULT_OUT = ROOT / "tmp" / "curated-data"
DEFAULT_AUDIT_DIR = ROOT / "tmp" / "curated-audit"


def unique_categories() -> list[str]:
    """Return the deduplicated English Quick Draw category names."""
    data = json.loads(CATEGORY_JSON.read_text(encoding="utf-8"))
    return list(dict.fromkeys(value for key, value in data.items() if not key.startswith("_")))


def recount_and_write_manifest(out_dir: Path, n_target: int, n_min: int) -> dict:
    """Recount all required categories and write the runtime manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    categories = {}
    for category in unique_categories():
        category_dir = out_dir / category
        count = len(list(category_dir.glob("*.svg"))) if category_dir.is_dir() else 0
        categories[category] = {"count": count}
    manifest = {
        "gate_version": GATE_VERSION,
        "n_target": n_target,
        "n_min": n_min,
        "categories": categories,
        "ready": all(row["count"] >= n_min for row in categories.values()),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def next_index(cat_dir: Path) -> int:
    """Return the first index after the highest existing numeric SVG name."""
    indices = [int(path.stem) for path in cat_dir.glob("*.svg") if path.stem.isdigit()]
    return max(indices, default=-1) + 1


async def _gen(prompt: str) -> str:
    from hutuji_mcp.bitmap_svg import generate_bitmap

    return (await generate_bitmap(prompt)).svg


def _candidate_svg(category: str, fixture: Path | None) -> str:
    if fixture is not None:
        return fixture.read_text(encoding="utf-8")
    return asyncio.run(_gen(category))


def _write_audit_list(category: str, cat_dir: Path, audit_dir: Path) -> None:
    paths = sorted(cat_dir.glob("*.svg"))
    audit_dir.mkdir(parents=True, exist_ok=True)
    sample_size = min(3, len(paths))
    selected = random.sample(paths, sample_size) if sample_size else []
    (audit_dir / f"{category}.txt").write_text(
        "".join(f"{path}\n" for path in selected),
        encoding="utf-8",
    )


def fill_category(
    cat: str,
    *,
    per_category: int,
    out_dir: Path,
    fixture: Path | None,
    max_attempts: int,
    audit_dir: Path,
) -> None:
    """Fill one category with gated SVGs, resuming from its current count."""
    if per_category < 1:
        raise ValueError("per_category must be at least 1")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    cat_dir = out_dir / cat
    cat_dir.mkdir(parents=True, exist_ok=True)
    while len(list(cat_dir.glob("*.svg"))) < per_category:
        for attempt in range(1, max_attempts + 1):
            svg = _candidate_svg(cat, fixture)
            try:
                with tempfile.TemporaryDirectory(prefix="curated-gate-") as work:
                    gate_svg(svg, work_dir=Path(work))
            except GateReject as exc:
                if attempt == max_attempts:
                    raise RuntimeError(f"{cat}: gate rejected after {attempt} attempts: {exc.reason}") from exc
                continue
            index = next_index(cat_dir)
            (cat_dir / f"{index:03d}.svg").write_text(svg, encoding="utf-8", newline="\n")
            break
    _write_audit_list(cat, cat_dir, audit_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a curated offline SVG library")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--only", default="", help="comma-separated category subset")
    parser.add_argument("--per-category", type=int, default=50)
    parser.add_argument("--n-min", type=int, default=30)
    parser.add_argument("--fixture-svg", type=Path)
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR)
    parser.add_argument("--max-attempts-per-slot", type=int, default=3)
    args = parser.parse_args(argv)

    categories = unique_categories()
    if args.only:
        wanted = {value.strip() for value in args.only.split(",") if value.strip()}
        unknown = wanted - set(categories)
        if unknown:
            parser.error(f"--only contains unknown categories: {sorted(unknown)}")
        categories = [category for category in categories if category in wanted]
    if args.fixture_svg is not None and not args.fixture_svg.is_file():
        parser.error(f"--fixture-svg is not a file: {args.fixture_svg}")

    failures: list[str] = []
    for category in categories:
        try:
            fill_category(
                category,
                per_category=args.per_category,
                out_dir=args.out,
                fixture=args.fixture_svg,
                max_attempts=args.max_attempts_per_slot,
                audit_dir=args.audit_dir,
            )
        except Exception as exc:  # noqa: BLE001 - retain other categories and write manifest
            print(f"{category}: FAIL {exc}", file=sys.stderr)
            failures.append(category)
    manifest = recount_and_write_manifest(args.out, args.per_category, args.n_min)
    print(f"prepared {len(categories) - len(failures)}/{len(categories)} categories; ready={manifest['ready']}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
