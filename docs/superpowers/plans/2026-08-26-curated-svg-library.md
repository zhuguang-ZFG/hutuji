# Curated SVG Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an offline curated single-stroke SVG library that, once 298 QD categories meet coverage, fully replaces Quick Draw for those themes (no doodle style), with hard ingest gates and dark deploy until flip.

**Architecture:** Independent `/opt/hutuji/curated/<category>/*.svg` + `manifest.json`; `hutuji_mcp/curated.py` lookup reuses QD category matching; `curated_gate.py` hard-rejects bad SVGs offline; `_run_draw_job` prefers curated only when `REPLACE_QD=1` and `manifest.ready`; otherwise behavior stays QD-style-gated → AI.

**Tech Stack:** Python 3 / existing hutuji MCP (`BitmapResult`, `_validate_svg`, `svg_to_gcode`/`validate_gcode`, `generate_bitmap`), pytest, vpype, env config via `Settings`.

**Spec:** `docs/superpowers/specs/2026-08-26-curated-svg-library-design.md`

## Global Constraints

- Curated English category names == QD values from `quickdraw_categories.json` (298 unique; exclude `_meta`).
- `n_target=50`, `n_min=30`; `ready` iff every mapped English category has `count >= n_min`.
- Ingest hard gate: **抬笔不作硬拒**（2026-08-26 用户口径 supersede；初值 ≤25）。硬拒仅：SVG/vpype/§5、短段比、墨迹跨度、G1 行数上限。
- While `ready=false` or `HUTUJI_CURATED_REPLACE_QD!=1`, runtime must match today’s QD quality routing (`_QD_STYLE_RE` → QD → else AI); curated lookup short-circuits to `None`.
- When replace active: curated hit skips QD; curated miss → AI only (never QD). Curated path is **not** style-gated (it is the quality replacement).
- SVG bodies do not enter git; code/tests/scripts/docs do.
- Do not modify S3 firmware or vpype hutuji profile in this plan.
- Conventional commits; Chinese user-facing logs stay consistent with existing style.

## File map

| File | Responsibility |
|---|---|
| `mcp-server/hutuji_mcp/curated_gate.py` | Pure ingest gate: SVG → pass/fail + reason |
| `mcp-server/hutuji_mcp/curated.py` | Runtime lookup, manifest ready, replace active |
| `mcp-server/hutuji_mcp/config.py` | `curated_dir`, `curated_replace_qd` |
| `mcp-server/hutuji_mcp/server.py` | Draw-job branch order |
| `mcp-server/scripts/curated_prep.py` | Offline generate / gate / write / manifest / audit list |
| `mcp-server/tests/test_curated_gate.py` | Gate unit tests (fixtures, no network) |
| `mcp-server/tests/test_curated.py` | Lookup + replace semantics |
| `mcp-server/tests/test_curated_server.py` | `_run_draw_job` routing with mocks |
| `docs/deployment.md` (short note) | Env vars + flip/rollback |
| `mcp-server/hutuji_mcp/CURATED-ATTRIBUTION.md` | Not Google QD; pipeline ToS note |

---

### Task 1: Ingest gate module

**Files:**
- Create: `mcp-server/hutuji_mcp/curated_gate.py`
- Create: `mcp-server/tests/test_curated_gate.py`
- Test: `mcp-server/tests/test_curated_gate.py`

**Interfaces:**
- Consumes: `_validate_svg`, `svg_to_gcode`, `validate_gcode`, `GcodeStats`
- Produces:
  - `GATE_VERSION: str = "1"`
  - `MAX_PEN_LIFTS: int = 25`
  - `MAX_G1_LINES: int = 800`
  - `MAX_SVG_BYTES: int = 256 * 1024`
  - `MAX_SHORT_G1_RATIO: float = 0.35` (G1 segment length `< 0.5` mm)
  - `MIN_INK_SPAN_MM: float = 40.0` (max(dx,dy) of drawn XY must be ≥ this after layout)
  - `class GateReject(Exception)` with `.reason: str`
  - `def gate_svg(svg: str, *, work_dir: Path) -> GcodeStats` — writes temp svg/gcode under `work_dir`, runs full pipeline; returns stats on pass; raises `GateReject` on fail

- [ ] **Step 1: Write failing tests**

Create `mcp-server/tests/test_curated_gate.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from hutuji_mcp import curated_gate


def _poly_svg(points: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="190mm" height="190mm" '
        'viewBox="0 0 100 100" version="1.1">'
        f'<polyline points="{points}" fill="none" stroke="black" stroke-width="1"/>'
        "</svg>"
    )


def test_gate_accepts_simple_stroke(tmp_path: Path):
    svg = _poly_svg("10,10 90,10 90,90 10,90 10,10")
    stats = curated_gate.gate_svg(svg, work_dir=tmp_path)
    assert stats.pen_lifts <= curated_gate.MAX_PEN_LIFTS
    assert stats.lines <= curated_gate.MAX_G1_LINES


def test_gate_rejects_invalid_svg_tag(tmp_path: Path):
    bad = '<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>'
    with pytest.raises(curated_gate.GateReject) as ei:
        curated_gate.gate_svg(bad, work_dir=tmp_path)
    assert ei.value.reason


def test_gate_rejects_too_many_pen_lifts(tmp_path: Path, monkeypatch):
    svg = _poly_svg("0,0 50,0 50,50")

    def fake_svg_to_gcode(svg_path, gcode_path):
        from hutuji_mcp.gcode_gen import GcodeStats, svg_to_gcode as real

        stats = real(svg_path, gcode_path)
        return GcodeStats(lines=stats.lines, bytes=stats.bytes, pen_lifts=26)

    monkeypatch.setattr(curated_gate, "svg_to_gcode", fake_svg_to_gcode)
    with pytest.raises(curated_gate.GateReject) as ei:
        curated_gate.gate_svg(svg, work_dir=tmp_path)
    assert "pen_lifts" in ei.value.reason


def test_gate_version_constant():
    assert curated_gate.GATE_VERSION == "1"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd D:/Users/hutuji/mcp-server && py -m pytest tests/test_curated_gate.py -v
```

Expected: import/collection failure (`curated_gate` missing).

- [ ] **Step 3: Write minimal implementation**

Create `mcp-server/hutuji_mcp/curated_gate.py`:

```python
"""Curated SVG ingest hard gate (design 2026-08-26)."""
from __future__ import annotations

import math
import re
from pathlib import Path

from .gcode_gen import GcodeStats, svg_to_gcode, validate_gcode
from .llm_svg import _validate_svg

GATE_VERSION = "1"
MAX_PEN_LIFTS = 25
MAX_G1_LINES = 800
MAX_SVG_BYTES = 256 * 1024
MAX_SHORT_G1_RATIO = 0.35
MIN_INK_SPAN_MM = 40.0
_SHORT_MM = 0.5

# hutuji gwrite often emits compact tokens; allow optional spaces.
_G1_RE = re.compile(
    r"G1(?![0-9])[^;\n]*?"
    r"(?:X(-?\d+(?:\.\d+)?)[^;\n]*?Y(-?\d+(?:\.\d+)?)"
    r"|Y(-?\d+(?:\.\d+)?)[^;\n]*?X(-?\d+(?:\.\d+)?))",
    re.I,
)


class GateReject(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _xy_moves(gcode: str) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for m in _G1_RE.finditer(gcode):
        if m.group(1) is not None:
            pts.append((float(m.group(1)), float(m.group(2))))
        else:
            pts.append((float(m.group(4)), float(m.group(3))))
    return pts


def _ink_checks(gcode: str) -> None:
    pts = _xy_moves(gcode)
    if len(pts) < 2:
        raise GateReject("ink: too few G1 XY points")
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    if span < MIN_INK_SPAN_MM:
        raise GateReject(f"ink: span {span:.1f}mm < {MIN_INK_SPAN_MM}")
    short = 0
    total = 0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        d = math.hypot(x1 - x0, y1 - y0)
        if d == 0:
            continue
        total += 1
        if d < _SHORT_MM:
            short += 1
    if total and (short / total) > MAX_SHORT_G1_RATIO:
        raise GateReject(f"short_g1_ratio: {short}/{total}")


def gate_svg(svg: str, *, work_dir: Path) -> GcodeStats:
    raw = svg.encode("utf-8")
    if len(raw) > MAX_SVG_BYTES:
        raise GateReject(f"svg_bytes: {len(raw)} > {MAX_SVG_BYTES}")
    try:
        _validate_svg(svg)
    except Exception as exc:  # noqa: BLE001 — gate reason surface
        raise GateReject(f"validate_svg: {exc}") from exc
    work_dir.mkdir(parents=True, exist_ok=True)
    svg_path = work_dir / "gate.svg"
    gcode_path = work_dir / "gate.gcode"
    svg_path.write_text(svg, encoding="utf-8")
    try:
        stats = svg_to_gcode(svg_path, gcode_path)
    except Exception as exc:  # noqa: BLE001
        raise GateReject(f"vpype: {exc}") from exc
    text = gcode_path.read_text(encoding="utf-8")
    try:
        stats = validate_gcode(text, total_bytes=len(text.encode("utf-8")))
    except Exception as exc:  # noqa: BLE001
        raise GateReject(f"validate_gcode: {exc}") from exc
    if stats.pen_lifts > MAX_PEN_LIFTS:
        raise GateReject(f"pen_lifts: {stats.pen_lifts} > {MAX_PEN_LIFTS}")
    if stats.lines > MAX_G1_LINES:
        raise GateReject(f"lines: {stats.lines} > {MAX_G1_LINES}")
    _ink_checks(text)
    return stats
```

If `_G1_RE` fails against real hutuji profile output from the square fixture, adjust the regex against one captured `gate.gcode` before loosening ink checks.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd D:/Users/hutuji/mcp-server && py -m pytest tests/test_curated_gate.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd D:/Users/hutuji
git add mcp-server/hutuji_mcp/curated_gate.py mcp-server/tests/test_curated_gate.py
git commit -m "feat(curated): ingest hard gate (pen_lifts<=25, vpype, ink span)"
```

---

### Task 2: Runtime `curated.py` + config

**Files:**
- Create: `mcp-server/hutuji_mcp/curated.py`
- Modify: `mcp-server/hutuji_mcp/config.py`
- Create: `mcp-server/tests/test_curated.py`

**Interfaces:**
- Consumes: `quickdraw._match_category`, `BitmapResult`, `_validate_svg`, `polylines_to_preview_png`
- Produces:
  - `def coverage_ready(manifest: dict, required_categories: list[str]) -> bool`
  - `def required_categories() -> list[str]`
  - `def is_replace_active() -> bool`
  - `def lookup(description: str) -> BitmapResult | None`

- [ ] **Step 1: Write failing tests**

Create `mcp-server/tests/test_curated.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd D:/Users/hutuji/mcp-server && py -m pytest tests/test_curated.py -v
```

Expected: import failure.

- [ ] **Step 3: Add config fields**

In `Settings` (`mcp-server/hutuji_mcp/config.py`), after `quickdraw_dir`:

```python
    # Curated SVG library (design docs/superpowers/specs/2026-08-26-curated-svg-library-design.md).
    # Empty curated_dir = disabled. curated_replace_qd only takes effect when manifest.ready.
    curated_dir: str = ""
    curated_replace_qd: bool = False
```

In `load_settings()`:

```python
        curated_dir=os.environ.get("HUTUJI_CURATED_DIR", ""),
        curated_replace_qd=os.environ.get("HUTUJI_CURATED_REPLACE_QD", "0").strip()
        in ("1", "true", "TRUE", "yes"),
```

- [ ] **Step 4: Implement `curated.py`**

```python
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
```

- [ ] **Step 5: Run tests — expect pass**

```bash
cd D:/Users/hutuji/mcp-server && py -m pytest tests/test_curated.py -v
```

- [ ] **Step 6: Commit**

```bash
git add mcp-server/hutuji_mcp/curated.py mcp-server/hutuji_mcp/config.py mcp-server/tests/test_curated.py
git commit -m "feat(curated): runtime lookup + replace gate behind manifest.ready"
```

---

### Task 3: Wire `_run_draw_job`

**Files:**
- Modify: `mcp-server/hutuji_mcp/server.py`
- Create: `mcp-server/tests/test_curated_server.py`

**Interfaces:**
- Consumes: `curated.lookup`, `curated.is_replace_active`, `quickdraw.lookup`, `_QD_STYLE_RE`
- Produces: draw branch order per spec §4.2

- [ ] **Step 1: Write failing routing tests**

Read how `tests/test_quickdraw.py` invokes the draw job (search ` _run_draw_job` / `hutuji_draw` / `_persist_draw_outputs`). Copy that harness exactly into `tests/test_curated_server.py`.

Required cases:

1. `is_replace_active=True`, `curated.lookup` returns hit for `"画一只老虎"` → `quickdraw.lookup` call count `0`; persist called with curated SVG.
2. `is_replace_active=True`, `curated.lookup` returns `None` → `generate_bitmap` called; `quickdraw.lookup` still `0`.
3. `is_replace_active=False`, description without `_QD_STYLE_RE` → AI path (existing); curated not consulted for output (lookup may short-circuit internally).

Use `AsyncMock` for `_persist_draw_outputs` / `generate_bitmap` as in existing tests.

- [ ] **Step 2: Run — expect fail** (old branch always style-gates QD)

- [ ] **Step 3: Patch `server.py` draw branch**

Replace the block that currently does style-gated `quickdraw.lookup` with:

```python
        from . import curated as curated_mod

        if curated_mod.is_replace_active():
            # 精选库已就绪并开启替换：同主题不再走 QD 涂鸦；miss 只回落 AI。
            bitmap = await asyncio.to_thread(curated_mod.lookup, description)
        else:
            # 现网质量路由：仅明示简笔/速写等画风时查 QD。
            bitmap = (
                await asyncio.to_thread(quickdraw.lookup, description)
                if _QD_STYLE_RE.search(description)
                else None
            )
```

- [ ] **Step 4: Run curated + quickdraw server tests — expect pass**

```bash
cd D:/Users/hutuji/mcp-server && py -m pytest tests/test_curated_server.py tests/test_quickdraw.py -v
```

- [ ] **Step 5: Commit**

```bash
git add mcp-server/hutuji_mcp/server.py mcp-server/tests/test_curated_server.py
git commit -m "feat(curated): draw-job prefers curated when replace active"
```

---

### Task 4: Offline `curated_prep.py`

**Files:**
- Create: `mcp-server/scripts/curated_prep.py`
- Create: `mcp-server/tests/fixtures/curated_ok.svg`
- Create: `mcp-server/tests/test_curated_prep.py`

**Interfaces:**
- CLI: `--out`, `--only`, `--per-category`, `--n-min`, `--fixture-svg`, `--audit-dir`, `--max-attempts-per-slot`
- Uses `curated_gate.gate_svg`, `curated.required_categories` / recount logic
- AI path: `asyncio.run(generate_bitmap(...))` only when no `--fixture-svg`

- [ ] **Step 1: Fixture SVG** — same square polyline as Task 1 accept case; save as `tests/fixtures/curated_ok.svg`.

- [ ] **Step 2: Failing test for fixture fill**

```python
def test_prep_fixture_fills_category_and_manifest(tmp_path):
    from scripts import curated_prep  # or run via subprocess / importlib by path

    # Prefer importing functions: fill_category + write_manifest
    ...
    assert (tmp_path / "cat" / "000.svg").exists()
    man = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert man["gate_version"] == "1"
    assert man["categories"]["cat"]["count"] >= 3
    assert man["ready"] is False
```

Because `scripts/` may not be a package, load via:

```python
import importlib.util
spec = importlib.util.spec_from_file_location(
    "curated_prep",
    Path(__file__).resolve().parents[1] / "scripts" / "curated_prep.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
```

- [ ] **Step 3: Implement `curated_prep.py`**

Essential functions:

```python
def unique_categories() -> list[str]:
    data = json.loads(CATEGORY_JSON.read_text(encoding="utf-8"))
    return list(dict.fromkeys(v for k, v in data.items() if not k.startswith("_")))


def recount_and_write_manifest(out_dir: Path, n_target: int, n_min: int) -> dict:
    cats = {}
    for cat in unique_categories():
        d = out_dir / cat
        n = len(list(d.glob("*.svg"))) if d.is_dir() else 0
        cats[cat] = {"count": n}
    ready = all(cats[c]["count"] >= n_min for c in cats)
    man = {
        "gate_version": GATE_VERSION,
        "n_target": n_target,
        "n_min": n_min,
        "categories": cats,
        "ready": ready,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(man, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return man


def next_index(cat_dir: Path) -> int:
    existing = sorted(cat_dir.glob("*.svg"))
    if not existing:
        return 0
    return int(existing[-1].stem) + 1


def fill_category(cat: str, *, per_category: int, out_dir: Path, fixture: Path | None, max_attempts: int, audit_dir: Path) -> None:
    cat_dir = out_dir / cat
    cat_dir.mkdir(parents=True, exist_ok=True)
    while len(list(cat_dir.glob("*.svg"))) < per_category:
        # generate or read fixture; gate_svg; write; on GateReject continue until max_attempts budget
        ...
    # write 2-3 random paths to audit_dir / f"{cat}.txt"
```

CLI `main()` parses args, loops categories (filtered by `--only`), calls `fill_category`, then `recount_and_write_manifest`.

For AI generation:

```python
async def _gen(prompt: str) -> str:
    from hutuji_mcp.bitmap_svg import generate_bitmap
    return (await generate_bitmap(prompt)).svg
```

Prompt: use category English name as `generate_bitmap` description (existing `_BITMAP_STYLE` prefix applies).

- [ ] **Step 4: Run unit test + fixture smoke**

```bash
cd D:/Users/hutuji/mcp-server
py -m pytest tests/test_curated_prep.py -v
py scripts/curated_prep.py --only cat --per-category 3 --fixture-svg tests/fixtures/curated_ok.svg --out tmp/curated-data
```

Expected: three SVGs + `manifest.json` with `ready: false`.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/scripts/curated_prep.py mcp-server/tests/test_curated_prep.py mcp-server/tests/fixtures/curated_ok.svg
git commit -m "feat(curated): offline prep script with gate + manifest + audit list"
```

---

### Task 5: Docs + attribution

**Files:**
- Create: `mcp-server/hutuji_mcp/CURATED-ATTRIBUTION.md`
- Modify: `docs/deployment.md` (append a dated short section only)

- [ ] **Step 1: Attribution file**

State: curated assets are hutuji pipeline outputs; not Google Quick, Draw!; respect image-backend ToS; when replace is active those themes no longer distribute QD strokes.

- [ ] **Step 2: Deployment note**

Document: `HUTUJI_CURATED_DIR`, `HUTUJI_CURATED_REPLACE_QD`, sync to `/opt/hutuji/curated/`, dark deploy (`REPLACE=0`), flip only when `manifest.ready`, probe journal `curated hit`, rollback `REPLACE=0`.

- [ ] **Step 3: Commit**

```bash
git commit -m "docs(curated): attribution + deploy flip/rollback notes"
```

---

### Task 6: M3 full fill + M4 production flip (ops)

Run only after Tasks 1–5 are merged. No code commits required unless prep bugs appear.

- [ ] Fill library (resume-safe):

```bash
cd D:/Users/hutuji/mcp-server
py scripts/curated_prep.py --out tmp/curated-data --per-category 50 --n-min 30
```

- [ ] Wait until `manifest.json` has `"ready": true`.
- [ ] Review `tmp/curated-audit/`; remove rejects; `py scripts/curated_prep.py --only <cat> ...` to refill.
- [ ] Sync to lima + jdcloud `/opt/hutuji/curated/`.
- [ ] Deploy code with `HUTUJI_CURATED_REPLACE_QD=0`; confirm style-gated QD still works.
- [ ] Set `HUTUJI_CURATED_DIR=/opt/hutuji/curated` and `REPLACE_QD=1`; restart MCP.
- [ ] Probe 「画一只老虎」→ `curated hit`; must not log `Quick Draw 命中`.
- [ ] Rollback drill: `REPLACE_QD=0`.

---

## Self-review (plan vs spec)

| Spec requirement | Covered by |
|---|---|
| Independent `curated/` + manifest | Task 2, 4 |
| Reuse `quickdraw_categories.json` | Task 2 |
| Hard gates / pen_lifts≤25 / vpype | Task 1 |
| Prep + audit 2–3 / class | Task 4 |
| Replace only if ready | Task 2, 3 |
| No QD under replace; miss→AI | Task 3 |
| Dark deploy then flip | Task 5, 6 |
| Full coverage before flip | Task 6 |
| No firmware / vpype profile edits | Global Constraints |

Note locked in plan: current production QD is **style-gated**; curated replace path is **not** style-gated.

---

Plan complete and saved to `docs/superpowers/plans/2026-08-26-curated-svg-library.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — execute in this session with executing-plans checkpoints  

Which approach?
