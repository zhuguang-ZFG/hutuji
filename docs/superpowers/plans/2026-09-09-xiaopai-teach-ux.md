# 小程序笔顺体验 P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创作页「笔顺」模式：随便输入 → 抽出汉字（最多 20）→ 田字格渐显预览 → 收藏/发送；与 `hutuji_teach` / `stroke_order` 同源。

**Architecture:** Portal `POST /api/draw/teach` + `process_teach_text` 复用 `_materialize_laid_pages`；weapp 三段切换扩展写字页模式。

**Tech Stack:** Python draw-portal + pytest；微信小程序；`hutuji_mcp.stroke_order` / `write_text.page_to_*`。

**Spec:** `docs/superpowers/specs/2026-09-09-xiaopai-teach-ux-design.md`（已批准）

**Worktree:** `D:/Users/hutuji/.worktrees/xiaopai-ota` / `feat/xiaopai-ota-mass-prod`

## Global Constraints

- 抽出汉字最多 **20**；设备页上限 **20**（不抬 `kArticleMaxPages`）。
- 输入随便写；无汉字 → 400；缺 `stroke_dir` → 503。
- 不改 §5/竖版/MCP `hutuji_teach` 签名；不宣称可出货/纸面已验。
- PYTHONPATH=`worktree/mcp-server`；venv=`D:\Users\hutuji\mcp-server\.venv\Scripts\python.exe`
- Conventional commits；中文文案。

## File map

| File | Role |
|------|------|
| `draw-portal/draw_portal/teach_chars.py` | 抽 CJK + 20 字帽 |
| `draw-portal/draw_portal/pipeline.py` | `process_teach_text` |
| `draw-portal/draw_portal/app.py` | `POST /api/draw/teach` |
| `draw-portal/tests/test_teach_chars.py` | 抽字 |
| `draw-portal/tests/test_teach_pipeline.py` | 管线假测 |
| `draw-portal/tests/test_teach_api.py` | API |
| `draw-portal/weapp/pages/index/*` | 三段 UI |
| `draw-portal/weapp/pages/{profile,control,firmware}/*.wxml` | v0.2.14 |
| `docs/protocol.md` / readiness / inventory / allowlist | 收口 |

---

### Task 1: 抽字纯函数

**Files:** Create `teach_chars.py` + `tests/test_teach_chars.py`

**Interfaces:**
- `TEACH_MAX_CHARS = 20`
- `def extract_teach_chars(text: str) -> tuple[str, bool]`  
  — 按出现顺序抽出 CJK（与 `write_text._CJK_RE` 同族：`[㐀-鿿豈-﫿＀-￯]`）；返回 `(chars, input_truncated)`；`chars` 最长 20；空串合法（调用方判 400）

- [ ] **Step 1: 测试**

```python
from draw_portal.teach_chars import extract_teach_chars, TEACH_MAX_CHARS

def test_extract_mixed():
    chars, trunc = extract_teach_chars("春眠不觉晓，hello 123")
    assert chars == "春眠不觉晓"
    assert trunc is False

def test_extract_truncate():
    s = "一二三四五六七八九十" + "甲乙丙丁戊己庚辛"
    chars, trunc = extract_teach_chars(s)
    assert len(chars) == TEACH_MAX_CHARS == 20
    assert trunc is True
    assert chars.startswith("一二三四五六七八九十")

def test_extract_empty():
    assert extract_teach_chars("hello!") == ("", False)
```

- [ ] **Step 2–4:** RED → 实现 → GREEN → commit `feat(portal): 笔顺抽字与 20 字帽`

---

### Task 2: `process_teach_text`

**Files:** Modify `pipeline.py`；Create `tests/test_teach_pipeline.py`

**Interfaces:**
- `def process_teach_text(chars: str, job_dir: Path, *, stroke_dir: str) -> DocPipelineResult`  
  — `kind="teach"`, `mode="article"`；空 → `StrokeOrderError("没有可教的汉字")`；内部 `layout_teach(chars, data, ARTICLE_MAX_PAGES)` + `_materialize_laid_pages`  
  — 返回的 `missing_chars`/`truncated` 来自 `LayoutResult`（页上限）

Monkeypatch `layout_teach` / materialize 或 `StrokeData.load` 测转发。

- [ ] Commit `feat(portal): process_teach_text 田字格管线`

---

### Task 3: `POST /api/draw/teach`

**Files:** Modify `app.py`；Create `tests/test_teach_api.py`

Mirror `/api/draw/write`：`TeachBody(text)` → extract → 无字 400 → 无 stroke_dir 503 → `process_teach_text` → `publish_doc_pages(mcp_output_dir=config.mcp_output_dir)` → 响应含 `chars_taught`, `input_truncated`, `missing_chars`, `truncated`, `pages`。

`source_name=("笔顺: "+chars)[:120]`；usage `web_teach`。

- [ ] Commit `feat(portal): POST /api/draw/teach`

---

### Task 4: 小程序笔顺段

**Files:** `weapp/pages/index/*`；脚注 **v0.2.14**；复用 `_writeToken`（或改名 `_modeToken`）

- `mode: draw|write|teach`
- teach：textarea、生成笔顺 → `/api/draw/teach`、翻页、藏三格入口
- truncated / missing_chars / input_truncated 提示

- [ ] Commit `feat(weapp): 创作页笔顺模式（v0.2.14）`

---

### Task 5: 文档收口

protocol §10 增 teach；readiness 🟡；inventory；allowlist `xiaopai_teach_ux_authorized`；docs gate；commit `docs(teach-ux): 契约与 readiness 收口`

---

## Spec coverage

| Spec | Task |
|------|------|
| 三段入口 / 随便写 / 20 截断 | 1,4 |
| process + API | 2,3 |
| 翻页/send/save | 4（复用） |
| 文档 | 5 |

## Placeholder scan

无 TBD。实现时 publish 参数逐字抄 write 路由。
