# 小派售后 KB 编辑后台（一期）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a loopback-authenticated FAQ admin on Alibaba Cloud that edits a local SQLite store and publishes assembled Markdown to the existing Xiaozhi knowledge base via `update-kb` semantics—so Xiaopai voice recall improves without changing firmware or the draw MCP path.

**Architecture:** New package `kb_admin/` under hub repo (sibling to `mcp-server/`, not inside `hutuji_mcp` runtime). FastAPI serves JSON API + static admin UI; SQLite is the edit source of truth; publish writes a temp `.md`, calls a extracted `update_kb_from_path(kb_id, path)` from `scripts/xiaozhi_console.py`, records success/failure. Xiaozhi cloud remains the runtime recall source.

**Tech Stack:** Python ≥3.12, FastAPI, uvicorn, SQLite (`sqlite3` stdlib), pytest, existing `xiaozhi_console.py` HTTP helpers + `hutuji_mcp.redact`.

**Spec:** `docs/superpowers/specs/2026-08-29-xiaopai-support-kb-admin-design.md`

## Global Constraints

- Channel: Xiaopai voice only (install / fault / after-sales); no WeChat/web CS in this plan.
- Runtime recall truth = Xiaozhi KB; edit truth = SQLite; never dual-write `knowledge-base.md` as live edit source.
- Do not modify xiaozhi / Grbl firmware or `hutuji_draw` generation path.
- Credentials (`XIAOZHI_TOKEN`) only in server `EnvironmentFile`; never to browser; logs via `hutuji_mcp.redact` / `_redact_text`.
- Phase-1: FAQ CRUD + aliases + import + publish + publish history. No troubleshooting-flow editor.
- Content volume: shell OK with zero FAQs; no minimum count gate.
- Defaults for open items (§8): path `/kb-admin` behind auth; SQLite + daily copy backup; keep configurable read-only appendix block in published markdown.
- Conventional commits; Chinese user-facing strings; docs gate after doc edits.

## File map

| File | Responsibility |
|---|---|
| `kb-admin/kb_admin/models.py` | Dataclasses / category enums |
| `kb-admin/kb_admin/store.py` | SQLite CRUD for FAQ + publish records |
| `kb-admin/kb_admin/assemble.py` | FAQ rows → publish Markdown |
| `kb-admin/kb_admin/import_md.py` | Parse `knowledge-base.md` → candidate FAQs |
| `kb-admin/kb_admin/publish.py` | Assemble → temp file → `update_kb_from_path` → record |
| `kb-admin/kb_admin/auth.py` | Session cookie login (single password from env) |
| `kb-admin/kb_admin/app.py` | FastAPI routes + static mount |
| `kb-admin/kb_admin/static/index.html` | Minimal admin UI |
| `kb-admin/requirements.txt` | fastapi, uvicorn, pytest (dev noted in README) |
| `kb-admin/tests/test_store.py` | Store unit tests |
| `kb-admin/tests/test_assemble.py` | Assemble / import tests |
| `kb-admin/tests/test_publish.py` | Publish with mocked console |
| `kb-admin/tests/test_app.py` | Auth + CRUD HTTP |
| `scripts/xiaozhi_console.py` | Extract `update_kb_from_path(kb_id, path: Path)` |
| `deploy/hutuji-kb-admin.service` | systemd unit template |
| `deploy/nginx-kb-admin-snippet.conf` | nginx path snippet (commented deploy notes) |
| `deploy/env.kb-admin.example` | env template |
| `kb-admin/README.md` | local run / deploy / rollback |
| `docs/deployment.md` | short pointer to kb-admin |
| `docs/release-readiness.md` | one evidence line when first deployed (later task) |

---

### Task 1: Extract `update_kb_from_path` in console script

**Files:**
- Modify: `scripts/xiaozhi_console.py` (cmd_update_kb body)
- Modify: `mcp-server/tests/test_field_tools.py` (add/adjust coverage if console helpers are already tested)
- Test: existing field_tools tests + a focused unit if present; else add `kb-admin/tests` later that mocks this function—for this task, add a pure extract and a thin pytest under `mcp-server/tests/test_field_tools.py` that monkeypatches `api`/`api_upload`.

**Interfaces:**
- Produces: `def update_kb_from_path(kb_id: str | int, path: Path) -> str` — returns `file_md5` on success; on failure raises `SystemExit` today OR change to raise `RuntimeError` with message (prefer **raise `XiaozhiConsoleError(msg)`** and map CLI to exit codes, to make library use sane). Minimal acceptable extract: keep `sys.exit` in CLI wrapper, library function returns md5 or raises `XiaozhiConsoleError`.

- [ ] **Step 1: Write failing test**

In `mcp-server/tests/test_field_tools.py` (or new `tests/test_xiaozhi_console_kb.py` imported via path):

```python
from pathlib import Path
import scripts.xiaozhi_console as xz  # adjust import style to match existing tests

def test_update_kb_from_path_uses_given_file(tmp_path, monkeypatch):
    md = tmp_path / "out.md"
    md.write_text("# hi\n", encoding="utf-8")
    calls = []

    def fake_api(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET" and "document?" in path:
            return {"data": {"list": []}}
        if method == "POST" and path == "/knowledge-bases/document":
            return {"success": True}
        if method == "DELETE":
            return {"success": True}
        return {"success": True, "data": {}}

    def fake_upload(path, file_path):
        assert Path(file_path) == md
        return {"success": True, "data": {"file_name": "out.md", "file_md5": "abc123"}}

    monkeypatch.setattr(xz, "api", fake_api)
    monkeypatch.setattr(xz, "api_upload", fake_upload)
    md5 = xz.update_kb_from_path(71153, md)
    assert md5 == "abc123"
```

- [ ] **Step 2: Run test — expect FAIL** (`update_kb_from_path` missing)

```powershell
cd D:\Users\hutuji\mcp-server
.\.venv\Scripts\python.exe -m pytest tests/test_field_tools.py -k update_kb_from_path -q
```

- [ ] **Step 3: Implement extract**

Refactor `cmd_update_kb` to:

```python
class XiaozhiConsoleError(Exception):
    pass

def update_kb_from_path(kb_id, path: Path) -> str:
    # same pagination / upload / create / delete loop as today,
    # but read `path` instead of KNOWLEDGE_BASE_PATH;
    # raise XiaozhiConsoleError instead of sys.exit(1) for library callers
    ...
    return file_md5

def cmd_update_kb(kb_id):
    try:
        update_kb_from_path(kb_id, KNOWLEDGE_BASE_PATH)
    except XiaozhiConsoleError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)
```

Keep exit code 2 for bad `kb_id` in CLI.

- [ ] **Step 4: Re-run test — PASS**

- [ ] **Step 5: Commit**

```bash
git add scripts/xiaozhi_console.py mcp-server/tests/test_field_tools.py
git commit -m "refactor(console): extract update_kb_from_path for KB admin publish"
```

---

### Task 2: SQLite store + models

**Files:**
- Create: `kb-admin/kb_admin/__init__.py` (empty)
- Create: `kb-admin/kb_admin/models.py`
- Create: `kb-admin/kb_admin/store.py`
- Create: `kb-admin/tests/test_store.py`
- Create: `kb-admin/requirements.txt` with `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `pytest>=8.3,<9`

**Interfaces:**
- Produces:
  - `CATEGORIES = ("install", "fault", "consumable", "safety", "kxnx", "other")`
  - `class Faq` dataclass: `id, title, body, category, aliases: list[str], enabled: bool, updated_at, updated_by, sort_key, imported: bool`
  - `class PublishRecord` dataclass: `id, created_at, created_by, status, xiaozhi_doc_md5, error_summary, faq_count`
  - `class Store`:
    - `Store(db_path: Path)`
    - `upsert_faq(faq: Faq) -> Faq`
    - `get_faq(id: str) -> Faq | None`
    - `list_faqs(*, category: str | None = None) -> list[Faq]`
    - `delete_faq(id: str) -> bool`
    - `add_publish_record(rec: PublishRecord) -> None`
    - `list_publish_records(limit: int = 50) -> list[PublishRecord]`

- [ ] **Step 1: Failing tests** in `kb-admin/tests/test_store.py`

```python
from pathlib import Path
from kb_admin.models import Faq
from kb_admin.store import Store
import time, uuid

def test_upsert_and_list(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    f = Faq(
        id=str(uuid.uuid4()),
        title="没墨怎么办",
        body="先试笔。",
        category="fault",
        aliases=["画不出来", "没墨"],
        enabled=True,
        updated_at=time.time(),
        updated_by="tester",
        sort_key=0,
        imported=False,
    )
    s.upsert_faq(f)
    rows = s.list_faqs(category="fault")
    assert len(rows) == 1
    assert rows[0].aliases == ["画不出来", "没墨"]
```

- [ ] **Step 2: Run — FAIL**

```powershell
cd D:\Users\hutuji\kb-admin
..\mcp-server\.venv\Scripts\pip.exe install -r requirements.txt
..\mcp-server\.venv\Scripts\python.exe -m pytest tests/test_store.py -q
```

(Or create `kb-admin/.venv` — plan default: reuse mcp-server venv with `PYTHONPATH=kb-admin`.)

- [ ] **Step 3: Implement models + store** (schema `faqs`, `publish_records`; aliases as JSON text)

- [ ] **Step 4: PASS**

- [ ] **Step 5: Commit**

```bash
git add kb-admin/
git commit -m "feat(kb-admin): SQLite store for FAQ and publish records"
```

---

### Task 3: Assemble Markdown + import from knowledge-base.md

**Files:**
- Create: `kb-admin/kb_admin/assemble.py`
- Create: `kb-admin/kb_admin/import_md.py`
- Create: `kb-admin/tests/test_assemble.py`

**Interfaces:**
- Produces:
  - `def assemble_markdown(faqs: list[Faq], *, preface: str, appendix: str) -> str`
  - `def import_candidates(md_text: str) -> list[Faq]` — splits on `##` / `###`; sets `imported=True`, `enabled=False`, `category="other"` default; `id` new UUIDs; `aliases=[]`

Assemble rules (spec §3.3):
1. preface
2. per category section heading in fixed order of `CATEGORIES`
3. each enabled FAQ: `### {title}\n` + if aliases: `同义问：a；b\n\n` + body + `\n\n`
4. appendix

- [ ] **Step 1: Failing tests**

```python
from kb_admin.assemble import assemble_markdown
from kb_admin.import_md import import_candidates
from kb_admin.models import Faq
import time, uuid

def test_assemble_skips_disabled():
    faqs = [
        Faq(id="1", title="A", body="a", category="fault", aliases=[], enabled=True,
            updated_at=0, updated_by="u", sort_key=0, imported=False),
        Faq(id="2", title="B", body="b", category="fault", aliases=["x"], enabled=False,
            updated_at=0, updated_by="u", sort_key=1, imported=False),
    ]
    out = assemble_markdown(faqs, preface="# P\n", appendix="## App\n")
    assert "### A" in out and "### B" not in out
    assert out.startswith("# P\n") and out.rstrip().endswith("## App")

def test_import_splits_headings():
    md = "# Root\n\n## 故障排查\n\nbody1\n\n### 子题\n\nbody2\n"
    rows = import_candidates(md)
    assert len(rows) >= 2
    assert all(r.imported and not r.enabled for r in rows)
```

- [ ] **Step 2–4: TDD implement until PASS**

- [ ] **Step 5: Commit**

```bash
git add kb-admin/kb_admin/assemble.py kb-admin/kb_admin/import_md.py kb-admin/tests/test_assemble.py
git commit -m "feat(kb-admin): assemble publish markdown and import md candidates"
```

---

### Task 4: Publish service

**Files:**
- Create: `kb-admin/kb_admin/publish.py`
- Create: `kb-admin/tests/test_publish.py`

**Interfaces:**
- Consumes: `Store`, `assemble_markdown`, `scripts.xiaozhi_console.update_kb_from_path`
- Produces: `def publish(store: Store, *, kb_id: int, actor: str, preface: str, appendix: str) -> PublishRecord`

Behavior:
1. `faqs = [f for f in store.list_faqs() if f.enabled]`
2. `body = assemble_markdown(...)`
3. write temp file under store data dir
4. call `update_kb_from_path(kb_id, path)`
5. on success: record `status=success`, md5, `faq_count=len(faqs)`
6. on `XiaozhiConsoleError`: record `status=failed`, redacted error; re-raise or return failed record (API will return 502 with record id)—**return failed PublishRecord without raising** so UI can show history

- [ ] **Step 1: Failing test with monkeypatch**

```python
def test_publish_success(tmp_path, monkeypatch):
    ...
    monkeypatch.setattr("kb_admin.publish.update_kb_from_path", lambda kb, p: "deadbeef")
    rec = publish(store, kb_id=71153, actor="u", preface="#", appendix="")
    assert rec.status == "success"
    assert rec.xiaozhi_doc_md5 == "deadbeef"
```

- [ ] **Step 2–4: Implement + PASS**

- [ ] **Step 5: Commit**

```bash
git add kb-admin/kb_admin/publish.py kb-admin/tests/test_publish.py
git commit -m "feat(kb-admin): publish assembled FAQ markdown to Xiaozhi KB"
```

---

### Task 5: FastAPI auth + CRUD + publish routes

**Files:**
- Create: `kb-admin/kb_admin/auth.py`
- Create: `kb-admin/kb_admin/app.py`
- Create: `kb-admin/kb_admin/config.py` (env: `KB_ADMIN_PASSWORD`, `KB_ADMIN_DB`, `KB_ADMIN_KB_ID`, `KB_ADMIN_PREFACE_PATH`, `KB_ADMIN_APPENDIX_PATH`, `XIAOZHI_TOKEN` passthrough)
- Create: `kb-admin/tests/test_app.py`

**Interfaces:**
- Cookie session: after `POST /api/login {"password":"..."}` set `HttpOnly` cookie `kb_admin_session` = HMAC or signed token of `ok`.
- Routes (all except login require auth → 401):
  - `GET /api/faqs`
  - `POST /api/faqs`
  - `PUT /api/faqs/{id}`
  - `DELETE /api/faqs/{id}`
  - `POST /api/import` — body optional; reads default repo `docs/xiaozhi-agent/knowledge-base.md` path from env `KB_ADMIN_IMPORT_MD`
  - `POST /api/publish`
  - `GET /api/publish-records`
- Bind default `127.0.0.1` only in config (mirror MCP host discipline).

- [ ] **Step 1: Failing HTTP tests** using `httpx.ASGITransport` + `AsyncClient` (or Starlette TestClient if already in deps—prefer httpx async as MCP does, or sync TestClient for speed):

```python
from fastapi.testclient import TestClient
from kb_admin.app import create_app

def test_rejects_anonymous(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("KB_ADMIN_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("KB_ADMIN_KB_ID", "71153")
    client = TestClient(create_app())
    assert client.get("/api/faqs").status_code == 401

def test_login_and_create_faq(tmp_path, monkeypatch):
    ...
    r = client.post("/api/login", json={"password": "secret"})
    assert r.status_code == 200
    r2 = client.post("/api/faqs", json={...})
    assert r2.status_code == 200
```

- [ ] **Step 2–4: Implement until PASS**

- [ ] **Step 5: Commit**

```bash
git add kb-admin/kb_admin/auth.py kb-admin/kb_admin/app.py kb-admin/kb_admin/config.py kb-admin/tests/test_app.py
git commit -m "feat(kb-admin): authenticated FAQ CRUD and publish API"
```

---

### Task 6: Minimal admin UI

**Files:**
- Create: `kb-admin/kb_admin/static/index.html` (single file: login form, FAQ table, edit modal, import button, publish button, publish history)
- Modify: `kb-admin/kb_admin/app.py` — `StaticFiles` mount at `/` and API under `/api`

UI rules (keep dumb):
- No SPA framework.
- Fetch with `credentials: 'same-origin'`.
- Publish shows confirm `confirm('将推送到小智知识库，确认？')`.
- Chinese labels.

- [ ] **Step 1: Manual checklist doc in README** (automated UI test optional smoke: GET `/` returns 200 HTML containing `登录`)

```python
def test_index_served(tmp_path, monkeypatch):
    ...
    assert "登录" in client.get("/").text
```

- [ ] **Step 2–4: Implement UI + smoke PASS**

- [ ] **Step 5: Commit**

```bash
git add kb-admin/kb_admin/static/index.html kb-admin/kb_admin/app.py kb-admin/tests/test_app.py
git commit -m "feat(kb-admin): minimal browser UI for FAQ edit and publish"
```

---

### Task 7: Deploy templates + docs

**Files:**
- Create: `deploy/hutuji-kb-admin.service`
- Create: `deploy/env.kb-admin.example`
- Create: `deploy/nginx-kb-admin-snippet.conf` (location `/kb-admin/` → proxy to `127.0.0.1:8301`, strip prefix or mount app at root behind path—**prefer app `root_path=/kb-admin`** documented)
- Create: `kb-admin/README.md`
- Modify: `docs/deployment.md` (short section at top or ops section)
- Modify: `docs/superpowers/specs/2026-08-29-xiaopai-support-kb-admin-design.md` status line if needed

Unit sketch:

```
[Service]
User=hutuji
WorkingDirectory=/opt/hutuji/kb-admin
EnvironmentFile=/etc/hutuji/kb-admin.env
ExecStart=/opt/hutuji/kb-admin/.venv/bin/python -m uvicorn kb_admin.app:app --host 127.0.0.1 --port 8301
```

- [ ] **Step 1: Write files**
- [ ] **Step 2: Run docs gate**

```powershell
cd D:\Users\hutuji
.\mcp-server\.venv\Scripts\python.exe scripts\agent_gate.py --profile docs
```

Expected: `overall=pass` (fix line refs if inventory/deployment touch fingerprints).

- [ ] **Step 3: Commit**

```bash
git add deploy/hutuji-kb-admin.service deploy/env.kb-admin.example deploy/nginx-kb-admin-snippet.conf kb-admin/README.md docs/deployment.md
git commit -m "docs(deploy): kb-admin unit, env example, and ops notes"
```

- [ ] **Step 4: Stop — do not scp/production-enable without explicit user deploy authorization**

Production first boot (manual, out of code tasks): scp package → venv → env → nginx snippet → `daemon-reload` → `restart` → login UI → import dry-run → enable 1 FAQ → publish → `recall` 3 queries.

---

## Spec coverage self-check

| Spec requirement | Task |
|---|---|
| FAQ CRUD + aliases + enabled | 2, 5, 6 |
| Draft in SQLite / publish to Xiaozhi | 4, 5 |
| Publish records | 2, 4, 5 |
| Import from knowledge-base.md | 3, 5 |
| Auth, no anonymous | 5 |
| Reuse console update-kb, no second HTTP client | 1, 4 |
| Separate unit, loopback, redact | 7 |
| No firmware / draw changes | Global |
| Shell OK with empty content | Global / no min count |
| Flow editor deferred | Not in tasks |

## Placeholder scan

None intentional; open deploy hostname left as `/kb-admin` default per spec §8.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-29-xiaopai-support-kb-admin.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — this session executes tasks with checkpoints  

Which approach?
