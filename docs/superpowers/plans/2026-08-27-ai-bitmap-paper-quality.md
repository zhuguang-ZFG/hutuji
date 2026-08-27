# AI 位图链纸面风险闸 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AI 位图热路径在「预览还行、纸上废」时拒交废 G-code，最多整轮重生图 3 次，并堵住抽稀过狠/排线乱核，使常见主题纸面可认。

**Architecture:** 新增 `paper_risk.py` 在 §5 通过后评估 G-code（短段比、抬笔、空走中位、墨迹跨度、涂鸦核）；`_run_draw_job` 仅对 AI 路径循环 `generate_bitmap(…, attempt=n)`→落盘→闸门，拒则换 attempt 破缓存；`bitmap_svg` 加抽稀地板与排线簇抑制。QD/curated 命中不经重试环。默认 enforce，可用 env 关闭。

**Tech Stack:** Python 3.12+、现有 `hutuji_mcp`、pytest、vpype/`validate_gcode`、无新依赖。

**Spec:** `docs/superpowers/specs/2026-08-27-ai-bitmap-paper-quality-design.md`（已批准：立刻 enforce、整轮新生图、五类纸面目视由用户签字）。

## Global Constraints

- 正式契约仍为 protocol **v0.5**；本批不改 §5 限幅字面量、不改 Grbl/xiaozhi 固件。
- curated 入库闸 **v3** 保持「不单凭抬笔硬拒」；热路径纸面闸可拒抬笔过高（政策分立）。
- 重试：同题最多 **3** 次尝试（attempt 0..2）；耗尽不得返回废 G-code URL。
- 每 attempt = **完整新生图**（禁止同 PNG 只重描摹）。
- 凭据禁入库；日志只记 reason/attempt/数字，不落描述全文以外已有脱敏口径。
- 改完跑相关 pytest；宣称纸面改善须用户五类目视签字，未验写「纸面未验」。
- 动手前对账 `docs/worktree-inventory.md`，不覆盖无关脏树；提交须用户明确要求才做（若执行模式要求每任务 commit，用本计划文末信息性 commit 文案，仍服从用户「未要求不 commit」规则时改为暂存说明）。

---

## File Structure

| 文件 | 职责 |
|---|---|
| `mcp-server/hutuji_mcp/paper_risk.py` | 纸面风险评估：解析 G1、算指标、Pass/Reject；版本常量 |
| `mcp-server/hutuji_mcp/bitmap_svg.py` | 抽稀地板；排线簇抑制；`generate_bitmap(..., attempt=0)` 缓存键含 attempt |
| `mcp-server/hutuji_mcp/server.py` | `_run_draw_job` AI 分支：闸门+重试环；失败话术 |
| `mcp-server/hutuji_mcp/config.py` | `paper_risk_gate: bool`（默认 True） |
| `deploy/env.mcp.example` | `HUTUJI_PAPER_RISK_GATE` 注释 |
| `mcp-server/tests/test_paper_risk.py` | 闸门契约单测 |
| `mcp-server/tests/test_bitmap_decimate_floor.py` 或并入既有 bitmap 测 | 抽稀地板 |
| `mcp-server/tests/test_contract.py`（追加用例） | draw 重试环行为 |
| `docs/deployment.md` / `docs/release-readiness.md` / `docs/worktree-inventory.md` | 登记启用与未验纸面 |

---

### Task 1: `paper_risk` 模块 + 契约测试

**Files:**
- Create: `mcp-server/hutuji_mcp/paper_risk.py`
- Create: `mcp-server/tests/test_paper_risk.py`

**Interfaces:**
- Produces:
  - `PAPER_RISK_GATE_VERSION = "1"`
  - `class PaperRiskReject(Exception)` with `.reason: str`
  - `@dataclass(frozen=True) class PaperRiskReport` with fields: `pen_lifts: int`, `short_g1_ratio: float`, `travel_gap_p50_mm: float`, `ink_span_mm: float`, `scribble_core_hits: int`, `g1_xy_segments: int`
  - `def evaluate_gcode(gcode: str, *, pen_lifts: int) -> PaperRiskReport` — 只算分，不抛
  - `def enforce_gcode(gcode: str, *, pen_lifts: int) -> PaperRiskReport` — 不过则 `raise PaperRiskReject`
- Consumes: 无（可复用 `curated_gate._G1_RE` 逻辑：本模块**复制**同形正则与 `_xy_moves`，避免改 curated 公共 API；注释写清同源）

**钉死阈值（证据：08-26 废狗抬笔 55/69、修后 15；空走中位 4.85mm；`PEN_LIFT_WARN=50`）：**

```python
PAPER_RISK_GATE_VERSION = "1"
MAX_SHORT_G1_RATIO = 0.35          # 同 curated
MIN_INK_SPAN_MM = 40.0             # 同 curated
MAX_PEN_LIFTS = 40                 # 热路径；介于好样本(~15)与废样本(~55)
MAX_TRAVEL_GAP_P50_MM = 2.0        # 废样本中位 4.85；好样本应远低于此
MAX_SCRIBBLE_CORE_HITS = 0         # 任一涂鸦核窗即拒
# 涂鸦核窗：边长 SCRIBBLE_WIN_MM 的轴对齐窗内，长度 < SCRIBBLE_SEG_MM 的段数 ≥ SCRIBBLE_SEG_MIN
SCRIBBLE_WIN_MM = 25.0
SCRIBBLE_SEG_MM = 1.0
SCRIBBLE_SEG_MIN = 45
_SHORT_MM = 0.5
```

- [ ] **Step 1: 写失败测试**

```python
# mcp-server/tests/test_paper_risk.py
from __future__ import annotations

import pytest
from hutuji_mcp import paper_risk


def _header() -> str:
    return "G21\nG90\nF8000\nG1G90 Z0.0F10000\nG1G90 Z0.0F10000\n"


def test_pass_long_continuous_stroke():
    # 一条长折线：少抬笔、跨度够、无碎段核
    body = ["G1G90 X10Y10F8000"]
    for i in range(20):
        body.append(f"G1G90 X{10 + i * 5}Y{10 + (i % 2) * 5}F8000")
    body.append("G1G90 Z0.0F10000")
    g = _header() + "\n".join(body) + "\n"
    # pen_lifts 由调用方传入（与 validate_gcode 统计一致）；此处模拟 1
    report = paper_risk.enforce_gcode(g, pen_lifts=1)
    assert report.ink_span_mm >= paper_risk.MIN_INK_SPAN_MM
    assert report.pen_lifts == 1


def test_reject_high_pen_lifts():
    g = _header() + "G1G90 X10Y10F8000\nG1G90 X100Y10F8000\nG1G90 Z0.0F10000\n"
    with pytest.raises(paper_risk.PaperRiskReject) as ei:
        paper_risk.enforce_gcode(g, pen_lifts=paper_risk.MAX_PEN_LIFTS + 1)
    assert "pen_lifts" in ei.value.reason


def test_reject_large_travel_gap_p50():
    # 多段短墨 + 大空走：抬笔次数合法但中位空走 > 2mm
    parts = [_header()]
    x = 10.0
    for i in range(12):
        parts.append(f"G1G90 Z5.0F10000\nG1G90 X{x}Y10F8000\nG1G90 X{x + 0.8}Y10F8000\nG1G90 Z0.0F10000\n")
        x += 10.0  # 空走 ~9.2mm
    g = "".join(parts)
    with pytest.raises(paper_risk.PaperRiskReject) as ei:
        paper_risk.enforce_gcode(g, pen_lifts=12)
    assert "travel_gap" in ei.value.reason


def test_version_constant():
    assert paper_risk.PAPER_RISK_GATE_VERSION == "1"
```

- [ ] **Step 2: 跑测确认失败**

Run:

```powershell
cd D:\Users\hutuji\mcp-server
.\.venv\Scripts\python.exe -m pytest tests/test_paper_risk.py -q
```

Expected: FAIL（`ModuleNotFoundError` 或 import 失败）

- [ ] **Step 3: 实现 `paper_risk.py`**

实现要点：

1. 用与 `curated_gate` 同形的 `_G1_RE` 抽 XY 点序列（仅含 XY 的 G1）。
2. `short_g1_ratio`：相邻点距 `_SHORT_MM` 占比。
3. `ink_span_mm`：max(Δx, Δy)。
4. `travel_gap_p50_mm`：需要抬笔空走——实现时扫描行：在 `Z0`（抬）到下一 `Z5`（落）之间若有 XY 移动，记空走长度；或更简单：**用调用方 `pen_lifts` + 从 G-code 解析「抬笔后到下一落笔前的 XY 位移」**。推荐实现：逐行状态机 `pen_down`，`Z5`→down，`Z0`→up；up 期间累积 XY 位移，再次 down 时记录一段 gap；对 gaps 取中位数；无 gap 则 `0.0`。
5. `scribble_core_hits`：对所有「段长 &lt; SCRIBBLE_SEG_MM」的段中点，用网格桶（边长 `SCRIBBLE_WIN_MM`）计数，任一桶 ≥ `SCRIBBLE_SEG_MIN` 则 hits+=1（可计桶数）。
6. `enforce_gcode` 顺序检查：ink_span → short_ratio → pen_lifts → travel_gap_p50 → scribble_core；reason 前缀用稳定 token：`ink:` / `short_g1_ratio:` / `pen_lifts:` / `travel_gap:` / `scribble_core:`。

- [ ] **Step 4: 跑测确认通过**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_paper_risk.py -q
```

Expected: PASS

- [ ] **Step 5: Commit（仅当用户要求提交时）**

```text
feat(paper_risk): 纸面风险闸 v1 指标与 enforce
```

---

### Task 2: 抽稀地板（防 `1133→6`）

**Files:**
- Modify: `mcp-server/hutuji_mcp/bitmap_svg.py`（`_decimate_polylines` 调用处与常量）
- Create or extend: `mcp-server/tests/test_bitmap_decimate.py`

**Interfaces:**
- Consumes: `_decimate_polylines(polys, tol_px)`
- Produces: 抽稀后若 `after < max(MIN_POINTS_AFTER, int(before * MIN_KEEP_RATIO))` 则 **回退用抽稀前 polys**（或二分降低 tol 直至满足；优先实现「一次 tol 不过则跳过抽稀并 `logger.warning`」——简单可测）

**钉死常量：**

```python
_DECIMATE_MIN_POINTS_AFTER = 80
_DECIMATE_MIN_KEEP_RATIO = 0.08  # after/before；1133→6 ≈0.005 必触发回退
```

- [ ] **Step 1: 写失败测试**

```python
import numpy as np
from hutuji_mcp import bitmap_svg as b

def test_decimate_floor_skips_when_collapse_too_hard(monkeypatch):
    # 构造长阶梯折线：默认 tol 会大幅压缩
    pts = np.array([[float(i), float(i % 2)] for i in range(400)], dtype=float)
    polys = [pts]
    # 强制很大 tol
    out = b._decimate_polylines_with_floor(polys, tol_px=50.0)
    after = sum(len(p) for p in out)
    assert after >= b._DECIMATE_MIN_POINTS_AFTER
```

将现有调用改为 `_decimate_polylines_with_floor`（内部调 `_decimate_polylines`，检查地板）。

- [ ] **Step 2: 跑测确认失败**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_bitmap_decimate.py -q
```

Expected: FAIL（函数不存在）

- [ ] **Step 3: 实现地板包装并接到 `trace_bitmap_to_polylines`**

```python
def _decimate_polylines_with_floor(polys, tol_px: float):
    before = sum(len(p) for p in polys)
    out = _decimate_polylines(polys, tol_px)
    after = sum(len(p) for p in out)
    min_after = max(_DECIMATE_MIN_POINTS_AFTER, int(before * _DECIMATE_MIN_KEEP_RATIO))
    if before > 0 and after < min_after:
        logger.warning(
            "位图描摹：抽稀触地板 %d→%d（min_after=%d），保留未抽稀折线",
            before, after, min_after,
        )
        return polys
    return out
```

`trace_bitmap_to_polylines` 内把 `_decimate_polylines(...)` 换成 `_decimate_polylines_with_floor(...)`。

- [ ] **Step 4: 跑测确认通过** + 既有 bitmap 相关测不破

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_bitmap_decimate.py tests/test_contract.py -k "bitmap or stitch or blob or decimat" -q
```

Expected: PASS（收集到的相关项全绿）

- [ ] **Step 5: Commit（用户要求时）**

```text
fix(bitmap): 抽稀地板防止主题被压没
```

---

### Task 3: 排线簇抑制（描摹域）

**Files:**
- Modify: `mcp-server/hutuji_mcp/bitmap_svg.py`（stitch 之后、decimate 之前）
- Extend: `mcp-server/tests/test_bitmap_decimate.py` 或 `tests/test_bitmap_hatch.py`

**Interfaces:**
- Produces: `def _suppress_hatch_clusters(polys: list[np.ndarray], *, angle_tol_deg: float = 8.0, max_len_px: float = 40.0, min_cluster: int = 8) -> list[np.ndarray]`
- 规则：长度 ≤ `max_len_px` 的折线按首尾角分桶（度）；同桶内数量 ≥ `min_cluster` 且两两中点距离中位 &lt; 6px → **整簇丢弃**（典型毛发排线）；其余保留。

- [ ] **Step 1: 写失败测试** — 合成 12 条近平行短段应被删光或删到 &lt;3；另 1 条长轮廓保留。

- [ ] **Step 2: 跑测确认失败**

- [ ] **Step 3: 实现并接入 `trace_bitmap_to_polylines`**（顺序：sknw→stitch→**hatch suppress**→decimate floor）

- [ ] **Step 4: 跑测确认通过**

- [ ] **Step 5: Commit（用户要求时）**

```text
fix(bitmap): 抑制近平行短段排线簇
```

---

### Task 4: `generate_bitmap` 支持 `attempt` 破缓存

**Files:**
- Modify: `mcp-server/hutuji_mcp/bitmap_svg.py` — `generate_bitmap` / `_generate_bitmap_uncached` 缓存键
- Modify: 相关测试若写死缓存键

**Interfaces:**
- Produces: `async def generate_bitmap(description: str, *, attempt: int = 0) -> BitmapResult`
- 缓存键：`f"{safe_desc.strip()}::a{attempt}"`（attempt=0 也带后缀，避免与旧键语义纠缠；或 attempt=0 保持旧键 `safe_desc`、attempt≥1 带后缀——**采用后者**：`cache_key = safe_desc.strip() if attempt == 0 else f"{safe_desc.strip()}::a{attempt}"`，兼容现网缓存命中测试）

- [ ] **Step 1: 写测试** — monkeypatch `_fetch_image_png` 计数；同 description `attempt=0` 两次只 fetch 1 次；`attempt=1` 再 fetch 1 次。

- [ ] **Step 2: RED → 实现 → GREEN**

- [ ] **Step 3: Commit（用户要求时）**

```text
feat(bitmap): generate_bitmap attempt 破结果缓存
```

---

### Task 5: `_run_draw_job` 纸面闸 + 重试环

**Files:**
- Modify: `mcp-server/hutuji_mcp/server.py`
- Modify: `mcp-server/hutuji_mcp/config.py` — `paper_risk_gate: bool = True`
- Modify: `deploy/env.mcp.example`
- Extend: `mcp-server/tests/test_contract.py`（或 `test_paper_risk_draw.py`）

**Interfaces:**
- Consumes: `paper_risk.enforce_gcode`, `generate_bitmap(..., attempt=)`, `settings.paper_risk_gate`
- Produces: AI 分支最多 3 次；耗尽抛可映射到工具错误的异常（新建 `PaperRiskExhaustedError(RuntimeError)` 在 `paper_risk.py` 或 `server.py`，消息固定中文）

**失败话术（钉死）：**

```text
这张线稿太碎，纸上会画糊，请换个说法再试
```

**伪代码（写入实现）：**

```python
MAX_PAPER_RISK_ATTEMPTS = 3
...
if bitmap is not None:  # QD / curated
    stats = await _persist_draw_outputs(bitmap)
else:
    async with _draw_limiter.slot():
        last_reject = None
        for attempt in range(MAX_PAPER_RISK_ATTEMPTS):
            if time.monotonic() >= deadline:
                raise DrawTimeoutError(...)
            bitmap = await generate_bitmap(description, attempt=attempt)
            stats = await _persist_draw_outputs(bitmap)
            if not settings.paper_risk_gate:
                break
            gcode_text = gcode_path.read_text(encoding="utf-8")
            try:
                paper_risk.enforce_gcode(gcode_text, pen_lifts=stats.pen_lifts)
                logger.info(
                    "paper_risk pass attempt=%d pen_lifts=%d",
                    attempt, stats.pen_lifts,
                )
                break
            except paper_risk.PaperRiskReject as exc:
                last_reject = exc
                logger.warning(
                    "paper_risk reject reason=%s attempt=%d",
                    exc.reason, attempt,
                )
                for path in (svg_path, gcode_path, preview_path):
                    if path is not None:
                        path.unlink(missing_ok=True)
                svg_path = gcode_path = preview_path = None
                if attempt + 1 >= MAX_PAPER_RISK_ATTEMPTS:
                    raise PaperRiskExhaustedError(
                        "这张线稿太碎，纸上会画糊，请换个说法再试"
                    ) from last_reject
        else:
            raise PaperRiskExhaustedError(...)  # 理论上不可达
```

确保 `hutuji_draw` / JSON-RPC 层把 `PaperRiskExhaustedError` 变成工具错误字符串（看现有 `BitmapError`/`DrawTimeoutError` 处理方式，原样对齐）。

- [ ] **Step 1: 测试** — monkeypatch `generate_bitmap` 返回固定 BitmapResult；monkeypatch `_svg_to_gcode_cached` / 写假 gcode；monkeypatch `enforce_gcode` 前两次 Reject、第三次 Pass → 最终有 URL 且生成调用 3 次。另一测：三次全 Reject → 异常且无残留文件。

- [ ] **Step 2: RED → 实现 config + server → GREEN**

- [ ] **Step 3: Commit（用户要求时）**

```text
feat(draw): AI 路径纸面风险闸与最多三轮重生图
```

---

### Task 6: 文档登记 + 门禁

**Files:**
- Modify: `docs/deployment.md`（文首一条：闸版本、env、立刻 enforce、未纸面目视）
- Modify: `docs/release-readiness.md`（文首：已合代码状态；**纸面未验**）
- Modify: `docs/worktree-inventory.md`（本批文件）
- 若新增 `path:line`：跑 `--fix-line-refs` / `--accept-line-refs` 按需

- [ ] **Step 1: 写文档条目**（禁止宣称可出货/联调完成；写明五类纸面目视待用户）

- [ ] **Step 2: 跑门禁**

```powershell
cd D:\Users\hutuji
.\mcp-server\.venv\Scripts\python.exe scripts\agent_gate.py --profile hub
```

Expected: `overall=pass exit=0`

- [ ] **Step 3: Commit（用户要求时）**

```text
docs: 登记 AI 纸面风险闸启用与纸面未验
```

---

### Task 7: 生产部署（代码合入后，独立窗口）

**非本计划自动执行**；合入并用户批准部署后：

1. 从 Git blob 导出 LF：`paper_risk.py`、`bitmap_svg.py`、`server.py`、`config.py`；核 SHA-256 / CR=0  
2. scp 阿里云；京东云热备同步同文件  
3. `systemctl restart hutuji-mcp hutuji-cloud-bridge`（阿里云）；京东云仅 mcp  
4. journal 冒烟：一次 AI 链出图见 `paper_risk pass` 或 `reject`  
5. 用户执行五类纸面目视（猫/狗/树/房/鱼）→ 回填 readiness  

回滚：`HUTUJI_PAPER_RISK_GATE=0` + restart，或还原四文件。

---

## Spec coverage（自审）

| 规格项 | 任务 |
|---|---|
| 纸面风险闸指标 + enforce | Task 1 |
| 抽稀地板 | Task 2 |
| 排线簇抑制 | Task 3 |
| attempt 破缓存 / 整轮新生图 | Task 4–5 |
| 最多 3 次、耗尽无废 URL | Task 5 |
| env 关闭闸 | Task 5–6 |
| QD/curated 不经重试 | Task 5 |
| 提示词默认不动 | （无任务，刻意） |
| 文档 / 纸面未验 | Task 6–7 |
| 立刻 enforce | Task 5 默认 True |

## Placeholder scan

无 TBD；阈值已钉死并可在回归中用废/好样本微调（改则 bump `PAPER_RISK_GATE_VERSION`）。

## Type consistency

- `enforce_gcode(gcode, *, pen_lifts: int) -> PaperRiskReport`
- `PaperRiskReject.reason: str`
- `generate_bitmap(description, *, attempt: int = 0)`
- `settings.paper_risk_gate: bool`
