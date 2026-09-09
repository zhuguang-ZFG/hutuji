# 小程序写字体验 P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户在小程序创作页「写字」模式输入文本（或上传文章类文档）、调节字号/行距、翻页预览后，收藏或发送到写字机；排版与现有 `write_text` / 文档链同源。

**Architecture:** Portal 新增 `POST /api/draw/write`，扩展 `process_upload_doc` 接受可选版式；产物复用 `pages.json` + 现有 send/save。小程序创作页加模式切换与翻页预览。不改 MCP `hutuji_write` 签名、不改 §5/竖版/转置。

**Tech Stack:** Python 3.12+（draw-portal + pytest）、微信小程序、复用 `hutuji_mcp.write_text` / `t2s` / `article_pages`。

**Spec:** `docs/superpowers/specs/2026-09-09-xiaopai-write-ux-design.md`（已批准）

**Worktree:** `D:/Users/hutuji/.worktrees/xiaopai-ota`（分支 `feat/xiaopai-ota-mass-prod` 或实现时另开 `feat/xiaopai-write-ux`；勿覆盖 `hutuji_draw_bind.cc` 等 inventory 脏文件）

## Global Constraints

- 文本上限 **5000** 字符（与 `_WRITE_TEXT_MAX_CHARS` 同）；页上限 **20**（`ARTICLE_MAX_PAGES`）。
- 字号 **2–40** mm（默认 12）；行距 **1.0–3.0**（默认 1.6）；P0 **不暴露** `char_gap` / 字库名。
- 文章模式吃用户版式；**table**（xlsx/csv）忽略版式，保持表格默认 `WriteLayout(size_mm=8, line_spacing=1.25, char_gap_mm=0.5)`。
- 写字模式 UI：藏拍照/相册，留文档；send **自动 confirm**（现网口径）。
- 缺 `gfont_dir` → **503**；空文/超长/版式越界 → **4xx** 中文 detail。
- 不改竖版/转置/§5/换纸；不宣称可出货/纸面已验。
- Conventional commits；中文用户文案；门禁/测试用 worktree 内路径 + 本机 portal/mcp venv。

## File map

| File | Responsibility |
|------|----------------|
| `draw-portal/draw_portal/write_layout_params.py` | 解析/校验 `size_mm`/`line_spacing` → 构造文章用 `WriteLayout` |
| `draw-portal/draw_portal/pipeline.py` | `process_write_text`；`process_upload_doc` 增加可选版式 |
| `draw-portal/draw_portal/app.py` | `WriteBody` + `POST /api/draw/write`；upload 读 form 版式 |
| `draw-portal/tests/test_write_layout_params.py` | 版式校验 |
| `draw-portal/tests/test_write_api.py` | write 端点契约（假管线） |
| `draw-portal/tests/test_doc_upload.py` | upload 传版式 / table 忽略 |
| `draw-portal/weapp/pages/index/*` | 画图\|写字 UI、排版预览、翻页 |
| `draw-portal/weapp/pages/{profile,control,firmware}/*.wxml` | 脚注 bump **v0.2.13** |
| `docs/protocol.md` | portal 写字 API 短记（若 §10 门户面有位） |
| `docs/release-readiness.md` / `docs/worktree-inventory.md` | 进度与脏树 |
| `docs/agent-edit-allowlist.json` | 本批 portal/weapp 授权意图（若缺键则补） |

---

### Task 1: 版式参数纯函数

**Files:**
- Create: `draw-portal/draw_portal/write_layout_params.py`
- Create: `draw-portal/tests/test_write_layout_params.py`

**Interfaces:**
- Produces:
  - `class WriteLayoutParamsError(ValueError)` — `args[0]` 为中文 detail
  - `def resolve_article_layout(*, size_mm: float | None = None, line_spacing: float | None = None) -> WriteLayout`
    - `None` → 用 `WriteLayout` 默认字段（12 / 1.6 / char_gap 保持默认 0.8）
    - 越界 → `WriteLayoutParamsError`（文案含「字号越界」或「行距越界」）
  - `def parse_optional_float(raw: str | None) -> float | None` — 空串/`None` → `None`；非法 → `WriteLayoutParamsError("版式参数无效")`

- [ ] **Step 1: 写失败测试**

```python
# draw-portal/tests/test_write_layout_params.py
import pytest
from draw_portal.write_layout_params import (
    WriteLayoutParamsError,
    parse_optional_float,
    resolve_article_layout,
)

def test_defaults():
    lay = resolve_article_layout()
    assert lay.size_mm == 12.0
    assert lay.line_spacing == 1.6

def test_custom_ok():
    lay = resolve_article_layout(size_mm=18.0, line_spacing=2.0)
    assert lay.size_mm == 18.0
    assert lay.line_spacing == 2.0
    assert lay.char_gap_mm == 0.8  # 不暴露、不改默认

@pytest.mark.parametrize("size", [1.9, 40.1])
def test_size_oob(size):
    with pytest.raises(WriteLayoutParamsError, match="字号越界"):
        resolve_article_layout(size_mm=size)

@pytest.mark.parametrize("ls", [0.9, 3.1])
def test_line_spacing_oob(ls):
    with pytest.raises(WriteLayoutParamsError, match="行距越界"):
        resolve_article_layout(line_spacing=ls)

def test_parse_optional_float():
    assert parse_optional_float(None) is None
    assert parse_optional_float("") is None
    assert parse_optional_float("12") == 12.0
    with pytest.raises(WriteLayoutParamsError, match="版式参数无效"):
        parse_optional_float("x")
```

- [ ] **Step 2: 跑测期望 FAIL**

```powershell
cd D:\Users\hutuji\.worktrees\xiaopai-ota\draw-portal
# 若无本 worktree venv，用主仓：D:\Users\hutuji\draw-portal\.venv\Scripts\python.exe
D:\Users\hutuji\draw-portal\.venv\Scripts\python.exe -m pytest tests/test_write_layout_params.py -q
```

Expected: import 失败。

- [ ] **Step 3: 最小实现**

```python
# draw-portal/draw_portal/write_layout_params.py
from __future__ import annotations

from hutuji_mcp.write_text import WriteLayout, WriteTextError


class WriteLayoutParamsError(ValueError):
    pass


def parse_optional_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError as exc:
        raise WriteLayoutParamsError("版式参数无效") from exc


def resolve_article_layout(
    *, size_mm: float | None = None, line_spacing: float | None = None
) -> WriteLayout:
    kwargs: dict = {}
    if size_mm is not None:
        kwargs["size_mm"] = float(size_mm)
    if line_spacing is not None:
        kwargs["line_spacing"] = float(line_spacing)
    try:
        return WriteLayout(**kwargs)
    except WriteTextError as exc:
        msg = str(exc)
        if "字号" in msg:
            raise WriteLayoutParamsError(msg) from exc
        if "行距" in msg:
            raise WriteLayoutParamsError(msg) from exc
        raise WriteLayoutParamsError(msg) from exc
```

注意：跑测时 `PYTHONPATH` 须含 `mcp-server`（与现有 portal 测试相同：`test_app` / conftest 惯例）。若本地靠 env，保持与 `tests/test_doc_upload.py` 一致。

- [ ] **Step 4: 跑测期望 PASS**

```powershell
D:\Users\hutuji\draw-portal\.venv\Scripts\python.exe -m pytest tests/test_write_layout_params.py -q
```

Expected: all passed.

- [ ] **Step 5: Commit**

```powershell
cd D:\Users\hutuji\.worktrees\xiaopai-ota
git add draw-portal/draw_portal/write_layout_params.py draw-portal/tests/test_write_layout_params.py
git commit -m "feat(portal): 写字版式 size_mm/line_spacing 校验"
```

---

### Task 2: `process_write_text` + 文档管线吃版式

**Files:**
- Modify: `draw-portal/draw_portal/pipeline.py`
- Modify: `draw-portal/tests/test_doc_upload.py`（假管线签名兼容；可先改 fake 接受 `**kwargs`）
- Create: `draw-portal/tests/test_write_pipeline.py`（可选：用假字库则 skip；优先测「调用了 layout」用 monkeypatch）

**Interfaces:**
- Consumes: `resolve_article_layout`；`write_text.layout_text` / `page_to_svg` / `page_to_preview_png`
- Produces:
  - `def process_write_text(text: str, job_dir: Path, *, gfont_dir: str, layout: WriteLayout, stroke_dir: str = "") -> DocPipelineResult`
    - `kind="write"`, `mode="article"`
    - 文本调用方已规范化；本函数若空串 raise `WriteTextError("文本为空")`
  - `process_upload_doc(..., size_mm: float | None = None, line_spacing: float | None = None)`  
    - article：`layout = resolve_article_layout(size_mm=..., line_spacing=...)`  
    - table：忽略两参数，仍用表格默认 layout

- [ ] **Step 1: 写失败测试（fake 断言 layout 传入）**

在 `tests/test_write_pipeline.py`：

```python
from pathlib import Path
import pytest
from hutuji_mcp.write_text import WriteLayout

def test_process_write_text_uses_layout(monkeypatch, tmp_path):
    from draw_portal import pipeline
    seen = {}

    class FakeLaid:
        pages = [object()]
        truncated = False
        missing_chars = []

    def fake_layout_text(text, font, metrics, layout, **kwargs):
        seen["layout"] = layout
        seen["text"] = text
        return FakeLaid()

    def fake_materialize(laid, job_dir):
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "draw.gcode").write_text("G21\n", encoding="ascii")
        (job_dir / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (job_dir / "pages.json").write_text("{}", encoding="utf-8")
        return pipeline.DocPipelineResult(
            kind="write", mode="article", page_count=1, truncated=False,
            missing_chars=[], lines=1, bytes=4, page_lines=[1],
            svg_path=job_dir / "draw.svg", gcode_path=job_dir / "draw.gcode",
            preview_path=job_dir / "preview.png",
        )

    monkeypatch.setattr("hutuji_mcp.write_text.load_font", lambda *a, **k: object())
    monkeypatch.setattr("hutuji_mcp.write_text.measure_font", lambda *a, **k: object())
    monkeypatch.setattr("hutuji_mcp.write_text.layout_text", fake_layout_text)
    # 若实现内联 materialize 而非提取函数，则改为 monkeypatch page_to_svg/svg_to_gcode 等；
    # 最小要求：seen["layout"].size_mm == 18
    ...
```

实现时可选择：先提取内部 `_materialize_article_pages(laid, job_dir, *, kind, mode)` 供 `process_write_text` 与 `process_upload_doc` 共用（推荐，避免复制 svg/gcode 循环）。测试可改为只断言 `process_upload_doc` 在 article 下把自定义 layout 传给 `layout_text`（monkeypatch `layout_text`）。

更稳的最小 RED 测试（推荐写进仓库）：

```python
def test_upload_doc_article_forwards_layout(monkeypatch, tmp_path):
    from draw_portal.pipeline import process_upload_doc
    seen = {}

    class FakeDoc:
        kind = "txt"
        mode = "article"
        paragraphs = ["你好"]
        headings = None
        tables = []

    class FakeLaid:
        pages = []
        truncated = False
        missing_chars = []

    def fake_layout_text(text, font, metrics, layout, **kwargs):
        seen["layout"] = layout
        # 返回 0 页会让 materialize 崩；补一个最小 page stub 或整体 monkeypatch materialize
        raise AssertionError(f"layout={layout.size_mm}")

    monkeypatch.setattr("hutuji_mcp.doc_ingest.parse_document", lambda *a, **k: FakeDoc())
    monkeypatch.setattr("hutuji_mcp.write_text.load_font", lambda *a, **k: object())
    monkeypatch.setattr("hutuji_mcp.write_text.measure_font", lambda *a, **k: type("M", (), {"em_height": 1.0})())
    monkeypatch.setattr("hutuji_mcp.write_text.layout_text", fake_layout_text)

    with pytest.raises(AssertionError, match="layout=18"):
        process_upload_doc(
            b"hi", "txt", tmp_path / "j", gfont_dir="/x",
            size_mm=18.0, line_spacing=2.0,
        )
```

- [ ] **Step 2: 跑测期望 FAIL**（尚无 `size_mm` 参数 → TypeError）

```powershell
D:\Users\hutuji\draw-portal\.venv\Scripts\python.exe -m pytest tests/test_write_pipeline.py -q
```

- [ ] **Step 3: 实现**

1. 将 `process_upload_doc` 中「for page in laid.pages … pages.json … return DocPipelineResult」抽成 `_materialize_laid_pages(laid, job_dir, *, kind: str, mode: str) -> DocPipelineResult`。
2. article 分支：

```python
layout = resolve_article_layout(size_mm=size_mm, line_spacing=line_spacing)
laid = write_text.layout_text(
    "\n".join(doc.paragraphs), font, metrics, layout,
    max_pages=ARTICLE_MAX_PAGES,
    headings=doc.headings or None,
    stroke_data=stroke_data,
)
```

3. 新增：

```python
def process_write_text(
    text: str,
    job_dir: Path,
    *,
    gfont_dir: str,
    layout: WriteLayout,
    stroke_dir: str = "",
) -> DocPipelineResult:
    from hutuji_mcp import write_text
    from hutuji_mcp.article_pages import ARTICLE_MAX_PAGES
    assert gfont_dir
    if not text.strip():
        raise write_text.WriteTextError("文本为空")
    font = write_text.load_font(gfont_dir, None)
    metrics = write_text.measure_font(font)
    stroke_data = None
    if stroke_dir:
        from hutuji_mcp import stroke_order
        try:
            stroke_data = stroke_order.StrokeData.load(stroke_dir)
        except stroke_order.StrokeOrderError:
            stroke_data = None
    laid = write_text.layout_text(
        text, font, metrics, layout,
        max_pages=ARTICLE_MAX_PAGES,
        stroke_data=stroke_data,
    )
    return _materialize_laid_pages(laid, job_dir, kind="write", mode="article")
```

4. 更新 `test_doc_upload.py` 里 `fake_process` 签名为 `*, gfont_dir, size_mm=None, line_spacing=None, stroke_dir=""`（或 `**kwargs`），避免回归炸。

- [ ] **Step 4: 跑测 PASS**

```powershell
D:\Users\hutuji\draw-portal\.venv\Scripts\python.exe -m pytest tests/test_write_pipeline.py tests/test_doc_upload.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add draw-portal/draw_portal/pipeline.py draw-portal/tests/test_write_pipeline.py draw-portal/tests/test_doc_upload.py
git commit -m "feat(portal): process_write_text 与文档版式参数"
```

---

### Task 3: `POST /api/draw/write`

**Files:**
- Modify: `draw-portal/draw_portal/app.py`
- Create: `draw-portal/tests/test_write_api.py`

**Interfaces:**
- Consumes: `process_write_text`、`resolve_article_layout`、`publish_doc_pages`、`store.create_draw_job` / `mark_draw_job_ready`
- Produces: HTTP JSON  
  `{ job_id, preview_url, pages: [{url, preview_url}], page_count, truncated, lines, bytes, missing_chars? }`  
  `source_name` 建议 `("写字: " + text)[:120]`

- [ ] **Step 1: 写失败测试**

```python
# draw-portal/tests/test_write_api.py
from pathlib import Path
import json
from draw_portal.pipeline import DocPipelineResult
from draw_portal.push import PublishResult
from test_app import _client, _register_and_login

def _auth(monkeypatch, tmp_path):
    monkeypatch.setenv("DRAW_PORTAL_GFONT_DIR", "/x/fonts")
    c = _client(monkeypatch, tmp_path)
    _register_and_login(c)
    return c

def test_write_ok(monkeypatch, tmp_path):
    client = _auth(monkeypatch, tmp_path)
    captured = {}

    def fake_process(text, job_dir, *, gfont_dir, layout, stroke_dir=""):
        captured["text"] = text
        captured["size"] = layout.size_mm
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "pages").mkdir(exist_ok=True)
        (job_dir / "pages" / "page_001.gcode").write_text("G21\n", encoding="ascii")
        (job_dir / "pages" / "page_001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (job_dir / "draw.gcode").write_text("G21\n", encoding="ascii")
        (job_dir / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (job_dir / "pages.json").write_text(
            json.dumps({"kind": "write", "mode": "article", "page_count": 1, "page_lines": [1]}),
            encoding="utf-8",
        )
        return DocPipelineResult(
            kind="write", mode="article", page_count=1, truncated=False,
            missing_chars=[], lines=10, bytes=100, page_lines=[10],
            svg_path=job_dir / "draw.svg", gcode_path=job_dir / "draw.gcode",
            preview_path=job_dir / "preview.png",
        )

    def fake_publish(*, job_dir, mcp_output_dir, public_base_url):
        return [PublishResult(
            gcode_url="https://example.test/files/w.gcode",
            preview_url="https://example.test/files/w.png",
            basename="draw_write_1",
        )]

    monkeypatch.setattr("draw_portal.app.process_write_text", fake_process)
    monkeypatch.setattr("draw_portal.app.publish_doc_pages", fake_publish)
    r = client.post("/api/draw/write", json={"text": "春眠不觉晓", "size_mm": 14, "line_spacing": 1.8})
    assert r.status_code == 200
    body = r.json()
    assert body["page_count"] == 1
    assert body["preview_url"].endswith(".png")
    assert captured["size"] == 14.0

def test_write_empty_400(monkeypatch, tmp_path):
    client = _auth(monkeypatch, tmp_path)
    r = client.post("/api/draw/write", json={"text": "  "})
    assert r.status_code == 400

def test_write_oob_400(monkeypatch, tmp_path):
    client = _auth(monkeypatch, tmp_path)
    r = client.post("/api/draw/write", json={"text": "你好", "size_mm": 99})
    assert r.status_code == 400
    assert "字号" in r.json()["detail"]

def test_write_no_gfont_503(monkeypatch, tmp_path):
    monkeypatch.setenv("DRAW_PORTAL_GFONT_DIR", "")
    c = _client(monkeypatch, tmp_path)
    _register_and_login(c)
    r = c.post("/api/draw/write", json={"text": "你好"})
    assert r.status_code == 503
```

- [ ] **Step 2: 跑测 FAIL**（404）

- [ ] **Step 3: 实现路由**

```python
class WriteBody(BaseModel):
    text: str
    size_mm: float | None = None
    line_spacing: float | None = None

@app.post("/api/draw/write", dependencies=phone_secured)
async def draw_write(body: WriteBody, user_id: str = Depends(user_dep)):
    from hutuji_mcp import t2s
    from hutuji_mcp.write_text import WriteTextError
    from draw_portal.write_layout_params import WriteLayoutParamsError, resolve_article_layout

    if not config.gfont_dir:
        raise HTTPException(status_code=503, detail="写字字库未配置")
    safe = t2s.to_simplified(body.text.strip())
    if not safe:
        raise HTTPException(status_code=400, detail="文本为空")
    if len(safe) > 5000:
        raise HTTPException(status_code=400, detail=f"文本超长：{len(safe)} 字符（上限 5000）")
    try:
        layout = resolve_article_layout(size_mm=body.size_mm, line_spacing=body.line_spacing)
    except WriteLayoutParamsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job = store.create_draw_job(user_id=user_id, source_name=("写字: " + safe)[:120])
    job_path = _job_dir(config.data_dir, job.id)
    try:
        result = await asyncio.to_thread(
            process_write_text,
            safe,
            job_path,
            gfont_dir=config.gfont_dir,
            layout=layout,
            stroke_dir=config.stroke_dir or "",
        )
        published_pages = await asyncio.to_thread(
            publish_doc_pages,
            job_dir=job_path,
            mcp_output_dir=...,  # 与 upload doc 分支同一参数来源
            public_base_url=config.public_base_url,
        )
        store.mark_draw_job_ready(job.id, basename=published_pages[0].basename)
    except WriteTextError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 成功响应形状对齐 upload doc 分支（page_count/pages/truncated/lines/bytes）
```

对照 `app.py` 现有 doc upload 成功返回块逐字段对齐；`event_type` 可用 `web_write`（若 usage 表无强制枚举则随意；无则跳过或复用 `web_upload`）。

- [ ] **Step 4: pytest PASS** `tests/test_write_api.py`

- [ ] **Step 5: Commit** `feat(portal): POST /api/draw/write`

---

### Task 4: 文档 upload 透传版式

**Files:**
- Modify: `draw-portal/draw_portal/app.py`（`draw_upload` form）
- Modify: `draw-portal/tests/test_doc_upload.py`

**Interfaces:**
- Form 可选：`size_mm`、`line_spacing`（字符串 → `parse_optional_float`）
- 传入 `process_upload_doc(..., size_mm=..., line_spacing=...)`

- [ ] **Step 1: 测试** — upload 带 `size_mm=18` 时 `captured["size_mm"] == 18.0`；xlsx/table 路径 fake 可断言未要求改 table layout（article-only 断言即可）。

```python
def test_doc_upload_passes_layout(monkeypatch, tmp_path):
    client = _doc_client(monkeypatch, tmp_path)
    captured = {}
    # fake_process 记录 size_mm/line_spacing
    ...
    r = client.post(
        "/api/draw/upload",
        files={"file": ("a.txt", b"hello", "text/plain")},
        data={"size_mm": "18", "line_spacing": "2"},
    )
    assert r.status_code == 200
    assert captured["size_mm"] == 18.0
    assert captured["line_spacing"] == 2.0
```

- [ ] **Step 2–4:** RED → 改 `draw_upload` 读 Form → GREEN → commit  
  `feat(portal): 文档上传支持字号行距`

非法 form 浮点 → 400「版式参数无效」。

---

### Task 5: 小程序创作页写字模式

**Files:**
- Modify: `draw-portal/weapp/pages/index/index.{js,wxml,wxss}`
- Modify: 脚注 `v0.2.12` → `v0.2.13`（profile/control/firmware 等同口径处）

**Interfaces:**
- `data.mode`: `"draw" | "write"`
- `data.writeText`, `sizeMm`（默认 12）, `lineSpacing`（默认 1.6）
- `data.job.pages`（可选数组）, `data.pageIndex`（0-based）
- `onLayoutPreview` → `POST /api/draw/write`
- `onPickFile` 在 write 模式 `formData` 附带 `size_mm`/`line_spacing`（检查 `api`/`wx.uploadFile` 现有封装）

- [ ] **Step 1: UI 结构（wxml）**

- 顶部分段：画图 | 写字  
- `wx:if="{{mode==='draw'}}"` 保留现有生成区 + 三格入口  
- `wx:elif` 写字：textarea + 字号/行距步进 +「排版预览」+ 仅文档按钮  
- 预览共用：若 `job.pages && job.pages.length>1` 显示「第 i/N 页」与左右键；`truncated` 提示  

- [ ] **Step 2: JS 行为**

```javascript
onModeTap(e) {
  const mode = e.currentTarget.dataset.mode;
  if (mode === this.data.mode) return;
  this.stopRefinePolling();
  this.setData({ mode, job: null, pageIndex: 0 });
},
bumpSize(delta) {
  const v = Math.min(40, Math.max(2, Math.round((this.data.sizeMm + delta) * 10) / 10));
  this.setData({ sizeMm: v });
},
bumpLine(delta) {
  const v = Math.min(3, Math.max(1, Math.round((this.data.lineSpacing + delta) * 10) / 10));
  this.setData({ lineSpacing: v });
},
onLayoutPreview() {
  // POST /api/draw/write；adoptJob 时写入 pages/page_count/truncated；pageIndex=0
},
```

`adoptJob`：若 `res.pages` 存在则挂到 `job.pages`；预览图优先 `job.pages[pageIndex].preview_url`。

发送/收藏逻辑复用现有 `onSend`/`onSave`（仍用 `job.id`）。

- [ ] **Step 3: 本地静态检查** — 无语法错误；手测清单写入 commit message（真机开发者工具由用户侧验）。

- [ ] **Step 4: Commit** `feat(weapp): 创作页写字模式与翻页预览（v0.2.13）`

---

### Task 6: 文档收口

**Files:**
- Modify: `docs/protocol.md`（门户写字短节或 §10 表格一行：`POST /api/draw/write`）
- Modify: `docs/release-readiness.md`（🟡 写字 P0 代码；HIL/纸面未验）
- Modify: `docs/worktree-inventory.md`
- Modify: `docs/agent-edit-allowlist.json`（`xiaopai_write_ux_authorized` 键）
- Spec 状态已批准；链到本 plan

- [ ] **Step 1: 文档 diff**（禁止勾可出货；写字纸面未验证）

- [ ] **Step 2: docs gate**

```powershell
D:\Users\hutuji\mcp-server\.venv\Scripts\python.exe D:\Users\hutuji\.worktrees\xiaopai-ota\scripts\agent_gate.py --profile docs
```

行号指纹按仓规 `--fix-line-refs` / 人工后 `--accept-line-refs`。

- [ ] **Step 3: Commit** `docs(write-ux): 契约与 readiness 收口`

---

## Spec coverage（自检）

| Spec 节 | Task |
|---------|------|
| 画图\|写字入口 | 5 |
| 字号/行距步进与默认 | 1, 5 |
| 排版预览不自动重排 | 5 |
| 多页翻页 + truncated 提示 | 3 响应, 5 UI |
| 藏拍照/相册留文档 | 5 |
| `POST /api/draw/write` | 3 |
| upload 版式 / table 忽略 | 2, 4 |
| send/save 复用 + 自动 confirm | 3（不改 send）, 5 |
| 5000 / 20 页 / 503 gfont | 3 |
| 不做笔顺/选字库/§5 | Global + 6 |
| readiness 未宣称出货 | 6 |

## Placeholder scan

无 TBD；Task 2/3 中 `publish_doc_pages` 参数须实现时对照 `app.py` doc 分支逐字复制，禁止臆造路径。

---

请选择执行方式后开始 Task 1。
