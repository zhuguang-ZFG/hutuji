# 小派量产远程 OTA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 绑定用户可经小程序远程升级小派固件（自建 HTTPS 仓 + manifest）；设备每天 idle 自检只提示不自动升；下载/校验失败不切槽，切槽后靠 PENDING_VERIFY 回滚；Grbl 与写字体验本批不做。

**Architecture:** Portal 持板型→推荐版本目录与 check/upgrade API；nginx 静态服务 `/firmware/`；小程序调 portal，portal 经 messaging 调设备 `hutuji.ota_start`；设备复用 `Application::UpgradeFirmware` / `Ota::Upgrade`，并扩展 `hutuji.status` 的 `firmware_version`/`board`/`ota`；每天自检只 GET 各板 `latest.json`。

**Tech Stack:** Python 3.12+（draw-portal + pytest）、微信小程序、ESP-IDF 6.0.1（xiaozhi-esp32）、nginx 静态文件、HMAC 可选。

**Spec:** `docs/superpowers/specs/2026-09-09-xiaopai-ota-mass-prod-design.md`（已批准）

## Global Constraints

- 仅小派（`device=s3`）；Grbl 按钮禁用，预留 `device=grbl` 字段但不实现升级。
- 版本串：`hutuji.<major>.<minor>.<patch>`；比较只吃三段整数；**禁止**对 `hutuji` 段直接 `stoi`（上游 `Ota::ParseVersion` 会炸，必须修或绕开）。
- 每天 idle 自检：只查不升；升前小程序二次确认。
- 非 idle / 错 board / 非 HTTPS 允许主机 → 拒升，明确 reason token。
- 下载失败不 `esp_ota_set_boot`；失败后必须 `audio_service_.Start()` 恢复（现有路径回归）。
- 升级会话中 portal 不得推画/写任务。
- 不改 Grbl；不合并固件；不写字 UX；凭据不入库。
- 外仓改动路径：`D:/Users/xiaozhi-esp32`；动手前读该仓根 `AGENTS.md`；本仓 `agent-edit-allowlist.json` 无 xiaozhi 条目——**授权依据 = 本批准设计稿**，实现时在 allowlist 补 `xiaozhi_ota_mass_prod` 键（文档任务）。
- 不得宣称：可出货、Grbl 已远程升、未做坏包 HIL 前「升级绝对安全」。
- Conventional commits；中文用户文案；改契约同步 `protocol.md` / `release-readiness.md`。

## File map

| File | Responsibility |
|------|----------------|
| `draw-portal/draw_portal/firmware_version.py` | 解析/比较 `hutuji.x.y.z` |
| `draw-portal/draw_portal/firmware_catalog.py` | 读 catalog + 拼公开 URL / 签发短时下载 URL |
| `draw-portal/draw_portal/firmware_ota.py` | check/upgrade 业务：读绑定、调设备、记会话、busy 闸 |
| `draw-portal/draw_portal/store.py` | OTA 会话表（state/progress/reason） |
| `draw-portal/draw_portal/app.py` | `GET /api/device/firmware/check`、`POST .../upgrade`、`GET .../ota_status` |
| `draw-portal/draw_portal/push.py` | 已有 `push_tool_call`；upgrade 复用 |
| `draw-portal/tests/test_firmware_version.py` | 版本比较 |
| `draw-portal/tests/test_firmware_ota.py` | API + 拒升 + busy |
| `deploy/nginx-hutuji.conf`（及阿里云 gray 注释） | `location /firmware/` 静态 GET |
| `deploy/scripts/publish_s3_firmware.py`（或 `scripts/`） | 算 sha256、写 manifest/latest、上传说明 |
| `draw-portal/weapp/pages/firmware/*` | 升级页 |
| `draw-portal/weapp/pages/profile/*` + `app.json` | 入口 |
| `xiaozhi-esp32/CMakeLists.txt` | `PROJECT_VER "hutuji.1.0.0"`（首发） |
| `xiaozhi-esp32/main/ota.cc` | `ParseVersion` 跳过非数字段 |
| `xiaozhi-esp32/main/boards/lichuang-dev/hutuji_ota.{h,cc}` | check/start/daily + URL 闸 |
| `xiaozhi-esp32/main/boards/lichuang-dev/hutuji_job.cc` | `StatusJson` 增字段 |
| 三板 `InitializeTools` | 注册 `hutuji.ota_check` / `hutuji.ota_start` |
| `docs/protocol.md` §10.x | 工具与 status 字段 |
| `docs/release-readiness.md` / `worktree-inventory.md` | 进度与脏树 |

---

### Task 1: 版本比较纯函数（portal）

**Files:**
- Create: `draw-portal/draw_portal/firmware_version.py`
- Create: `draw-portal/tests/test_firmware_version.py`

**Interfaces:**
- Produces:
  - `def parse_hutuji_version(s: str) -> tuple[int, int, int] | None`
  - `def is_newer(candidate: str, current: str) -> bool` — candidate 严格大于 current 才 True；任一方非法 → False
  - `VERSION_RE` 钉 `^hutuji\.(\d+)\.(\d+)\.(\d+)$`

- [ ] **Step 1: 写失败测试**

```python
# draw-portal/tests/test_firmware_version.py
from draw_portal.firmware_version import is_newer, parse_hutuji_version

def test_parse_ok():
    assert parse_hutuji_version("hutuji.1.2.3") == (1, 2, 3)

def test_parse_rejects_upstream_and_garbage():
    assert parse_hutuji_version("2.4.0") is None
    assert parse_hutuji_version("hutuji.1.0") is None
    assert parse_hutuji_version("hutuji.01.0.0") == (1, 0, 0)  # 允许前导零经 int

def test_is_newer_matrix():
    assert is_newer("hutuji.1.0.1", "hutuji.1.0.0") is True
    assert is_newer("hutuji.1.0.0", "hutuji.1.0.0") is False
    assert is_newer("hutuji.1.0.0", "hutuji.1.0.1") is False
    assert is_newer("hutuji.2.0.0", "hutuji.1.9.9") is True
    assert is_newer("nope", "hutuji.1.0.0") is False
```

- [ ] **Step 2: 跑测期望 FAIL**

```powershell
cd D:\Users\hutuji\draw-portal
.\.venv\Scripts\python.exe -m pytest tests/test_firmware_version.py -q
```

Expected: import/属性错误。

- [ ] **Step 3: 最小实现**

```python
# draw-portal/draw_portal/firmware_version.py
from __future__ import annotations
import re

VERSION_RE = re.compile(r"^hutuji\.(\d+)\.(\d+)\.(\d+)$")

def parse_hutuji_version(s: str) -> tuple[int, int, int] | None:
    m = VERSION_RE.match((s or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))

def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_hutuji_version(candidate), parse_hutuji_version(current)
    if a is None or b is None:
        return False
    return a > b
```

- [ ] **Step 4: 跑测期望 PASS**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_firmware_version.py -q
```

- [ ] **Step 5: Commit（仅在用户要求提交时；否则停在工作树）**

```
feat(portal): hutuji 固件版本比较纯函数
```

---

### Task 2: Catalog + check/upgrade 核心（无 HTTP）

**Files:**
- Create: `draw-portal/draw_portal/firmware_catalog.py`
- Create: `draw-portal/draw_portal/firmware_ota.py`
- Create: `draw-portal/tests/test_firmware_ota_core.py`
- Modify: `draw-portal/draw_portal/config.py` — 增加 `firmware_catalog_path`、`firmware_public_base`、`firmware_allowed_hosts`、`firmware_signing_secret`（可空=不签）

**Interfaces:**
- Catalog JSON 形状（文件，非 DB）：

```json
{
  "device": "s3",
  "boards": {
    "freenove-esp32s3-display-2.8-lcd": {
      "version": "hutuji.1.0.0",
      "notes": "首发量产 OTA",
      "sha256": "abc...",
      "size": 3000000,
      "relpath": "s3/freenove-esp32s3-display-2.8-lcd/hutuji.1.0.0/xiaozhi.bin"
    }
  }
}
```

- Produces:
  - `load_catalog(path) -> dict`
  - `recommended_for_board(catalog, board) -> dict | None`
  - `build_bin_url(public_base, relpath, *, secret=None, ttl_sec=900) -> str`
  - `plan_check(*, board, current_version, catalog) -> dict` 返回 `{update_available, current, latest, notes, reason?}`
  - `assert_upgrade_allowed(*, job_state: str, board: str, manifest_board: str) -> str | None` 返回拒绝 reason 或 None

- [ ] **Step 1: 失败测试**

```python
from draw_portal.firmware_catalog import plan_check, recommended_for_board, load_catalog
from draw_portal.firmware_ota import assert_upgrade_allowed

def test_plan_check_newer(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(
        '{"device":"s3","boards":{"freenove-esp32s3-display-2.8-lcd":'
        '{"version":"hutuji.1.0.1","notes":"fix","sha256":"aa","size":1,'
        '"relpath":"s3/freenove-esp32s3-display-2.8-lcd/hutuji.1.0.1/xiaozhi.bin"}}}',
        encoding="utf-8",
    )
    cat = load_catalog(p)
    out = plan_check(
        board="freenove-esp32s3-display-2.8-lcd",
        current_version="hutuji.1.0.0",
        catalog=cat,
    )
    assert out["update_available"] is True
    assert out["latest"] == "hutuji.1.0.1"

def test_busy_and_wrong_board_reject():
    assert assert_upgrade_allowed(job_state="streaming", board="a", manifest_board="a") == "busy"
    assert assert_upgrade_allowed(job_state="idle", board="a", manifest_board="b") == "board_mismatch"
    assert assert_upgrade_allowed(job_state="idle", board="a", manifest_board="a") is None
```

- [ ] **Step 2: 跑测 FAIL → Step 3 实现 → Step 4 PASS**

`assert_upgrade_allowed` 的 busy 集合至少含：`streaming`、`previewing`、`awaiting_confirmation`、`paper_change`、`paused`、`upgrading`（与 Job state 对齐；未知非 idle 一律 `busy`）。

`build_bin_url`：无 secret 时返回 `f"{base.rstrip('/')}/firmware/{relpath.lstrip('/')}"`；有 secret 时追加 `?exp=&sig=`（HMAC-SHA256 hex of `f"{relpath}:{exp}"`）。

- [ ] **Step 5: 工作树保留；用户要 commit 再提**

---

### Task 3: Store 会话 + HTTP API

**Files:**
- Modify: `draw-portal/draw_portal/store.py` — 表 `firmware_ota_sessions(user_id PK, device_mac, state, progress, reason, target_version, updated_at)`
- Modify: `draw-portal/draw_portal/app.py` — 三路由；draw/write send 路径若 session.state=`upgrading` → 400
- Modify: `draw-portal/draw_portal/push.py` 调用点不变，由 `firmware_ota.start_upgrade` 调 `push_tool_call(..., name="hutuji.ota_start", arguments={url, version, board, sha256})`
- Test: `draw-portal/tests/test_firmware_ota.py`（仿 `test_wx_control.py` 的 `_clients` + monkeypatch `push_tool_call`）

**Interfaces:**
- `GET /api/device/firmware/check` → 先 `hutuji.status` 取 `firmware_version`/`board`（缺 board 时用绑定表缓存或 status 新字段），再 `plan_check`
- `POST /api/device/firmware/upgrade` body `{}` → 二次确认已在前端；服务端：check 仍有更新 → 写 session `upgrading` → push `hutuji.ota_start` → 返回 `{status:"started"}`；设备 busy 则 `status:error, detail:busy`
- `GET /api/device/firmware/ota_status` → 读 session；并可再 poll `hutuji.status` 合并 `ota` 字段

Reason tokens（与设计 F 表对齐，字符串稳定）：`check_failed`、`download_failed`、`verify_failed`、`write_failed`、`busy`、`board_mismatch`、`rolled_back`、`ok`、`up_to_date`

- [ ] **Step 1: HTTP 测试（失败先行）**

```python
def test_firmware_check_requires_binding(...):
    # unbound → 400 请先绑定设备

def test_firmware_upgrade_pushes_ota_start(monkeypatch, ...):
    calls = []
    def fake_push(**kw):
        calls.append(kw)
        return True, {"ok": True}
    monkeypatch.setattr("draw_portal.firmware_ota.push_tool_call", fake_push)
    # bind + mock status 返回 idle + hutuji.1.0.0 + board
    # catalog 指向 1.0.1
    r = c2.post("/api/device/firmware/upgrade")
    assert r.status_code == 200
    assert calls[0]["name"] == "hutuji.ota_start"
    assert "url" in calls[0]["arguments"]

def test_draw_blocked_while_upgrading(...):
    # session upgrading 后 POST 画图 send → 400 detail 含 升级
```

- [ ] **Step 2–4: RED → 实现路由与 store → GREEN**

实现时复用 `device_control` 的 token 自愈（401/403/404 铸新重试）；`push_tool_call` timeout 对 ota_start 可 20s（只受理，不下载）。

- [ ] **Step 5: 本地** `pytest tests/test_firmware_*.py -q`

---

### Task 4: nginx `/firmware/` + 发版脚本骨架

**Files:**
- Modify: `deploy/nginx-hutuji.conf`（京东云模板）— 在 `/songs/` 旁增加：

```nginx
location /firmware/ {
    limit_except GET HEAD { deny all; }
    limit_req zone=hutuji_files burst=10 nodelay;
    alias /var/hutuji/firmware/;
    # 大文件
    proxy_max_temp_file_size 0;
    sendfile on;
    default_type application/octet-stream;
}
```

- Create: `scripts/publish_s3_firmware.py` — 参数 `--board --version --bin --catalog --outdir`：校验 version 正则、算 sha256、写 `manifest.json` + 更新 catalog 的 board 项 + 写 `s3/{board}/latest.json`（设备自检用，字段同设计 §2.1 子集：`device,board,version,url,sha256,size,notes`）
- Create: `docs/templates/firmware-ota-release-checklist.md` — 含故意坏包 HIL、升后联网冒烟、错板拒升
- Docs note：阿里云 gray conf **禁止整份覆盖**；手工插入同口径 `location /firmware/`（计划执行时按 `deployment.md` 双机差异操作）

- [ ] **Step 1: 脚本自测**

```powershell
cd D:\Users\hutuji
python scripts\publish_s3_firmware.py --self-test
```

`--self-test`：用 tempfile 假 bin，断言 sha256 与 latest.json 字段。

- [ ] **Step 2: 实现脚本与 nginx 片段**
- [ ] **Step 3: 文档 checklist**
- [ ] **Step 4: 不在本任务刷生产 nginx**（部署另步；本任务只改仓内模板）

---

### Task 5: 设备 `ParseVersion` + `StatusJson` 字段

**Files（xiaozhi）：**
- Modify: `D:/Users/xiaozhi-esp32/main/ota.cc` — `ParseVersion` 只保留纯数字段：

```cpp
std::vector<int> Ota::ParseVersion(const std::string& version) {
    std::vector<int> versionNumbers;
    std::stringstream ss(version);
    std::string segment;
    while (std::getline(ss, segment, '.')) {
        if (segment.empty()) continue;
        bool all_digit = !segment.empty() &&
            std::all_of(segment.begin(), segment.end(), [](unsigned char c) {
                return std::isdigit(c);
            });
        if (!all_digit) continue;  // 跳过 "hutuji"
        try {
            versionNumbers.push_back(std::stoi(segment));
        } catch (...) {
            continue;
        }
    }
    return versionNumbers;
}
```

- Modify: `hutuji_job.cc` `StatusJson` 增加：
  - `firmware_version` = `esp_app_get_description()->version`
  - `board` = 板型字符串（与 catalog key 一致；用编译期 `BOARD_NAME` / 现有 GetBoardType 同类 API——实现前在 board 头文件确认符号，三板同一字段名）
  - `ota` object：`{state, progress, reason, update_available}`（缺省 `state=idle`）

- Host 测：若仓内已有 `scripts/tests` 对 ota 版本比较，补 `hutuji.1.0.0` vs `hutuji.1.0.1`；否则本任务以编译 + 手工串测清单记入 checklist。

- [ ] **Step 1: 改 ParseVersion + 加 StatusJson 字段**
- [ ] **Step 2: 确认 `PROJECT_VER` 计划在 Task 6 改为 `hutuji.1.0.0`（本任务可先不改，避免半截板）**
- [ ] **Step 3: 文档：`protocol.md` 给 `hutuji.status` 增字段说明（与实现同 PR）**

---

### Task 6: `hutuji_ota` 工具 + 每天自检 + PROJECT_VER

**Files（xiaozhi）：**
- Create: `main/boards/lichuang-dev/hutuji_ota.h` / `hutuji_ota.cc`
- Modify: freenove / waveshare-3.5 / lichuang-dev 的 `InitializeTools()` — `hutuji::ota::RegisterTools(mcp_server);`
- Modify: `CMakeLists.txt` 根 `PROJECT_VER "hutuji.1.0.0"`
- CMake/board 源列表：把 `hutuji_ota.cc` 编进三板（按现有 hutuji_job 同款 INCLUDE）

**Interfaces:**
- `hutuji.ota_check`：无参；GET `{public}/firmware/s3/{board}/latest.json`；比较版本；设置内存 `update_available`；**不下载**；返回 JSON `{update_available,current,latest,notes,reason}`
- `hutuji.ota_start`：参数 `url,version,board,sha256?`；校验：
  1. Job state 必须可升（idle 且非 DeviceState upgrading）
  2. `board` == 本机
  3. URL 以 `https://` 开头且 host ∈ 允许列表（编译默认 `hutuji.donglicao.com`，可 NVS 覆盖）
  4. 然后 `Application::UpgradeFirmware(url, version)`；开始前 `ota.state=upgrading`
- 每天自检：在现有主循环/idle 调度处（`Application` 或 Job 空闲 tick）调用 `MaybeDailyCheck()`：读 NVS `hutuji_ota/last_day`（UTC 或本地日字符串）；跨日且 idle 且 WiFi 连上 → `ota_check`；有更新则 `display` 轻提示「有固件更新」；**禁止**自动 `ota_start`

URL 允许列表硬闸示例：

```cpp
static bool HostAllowed(const std::string& url) {
    // 解析 host；允许 hutuji.donglicao.com；拒绝其它（含 IP 字面量除非显式配置）
}
```

- [ ] **Step 1: 实现 RegisterTools + MaybeDailyCheck**
- [ ] **Step 2: 三板注册**
- [ ] **Step 3: 改 PROJECT_VER**
- [ ] **Step 4: 构建量产板（当前 HIL 板，如 freenove 或 waveshare）** — 遵守本机 IDF 坑：`IDF_TOOLS_PATH`、先归档旧 zip 再 `release.py`，认 `Building CXX` 而非假绿
- [ ] **Step 5: 刷机后串口确认 status 含 `firmware_version":"hutuji.1.0.0"`**

---

### Task 7: 小程序固件页

**Files:**
- Create: `draw-portal/weapp/pages/firmware/firmware.{js,wxml,wxss,json}`
- Modify: `app.json` pages 列表加 `pages/firmware/firmware`
- Modify: `profile.wxml/js` —「固件升级」入口（需已绑机+手机号）

**UX（钉设计 §2.3）：**
- 显示小派当前/最新版本、notes
- 写字机版本行：只读「后续支持」，按钮 `disabled`
- 「检查更新」→ GET check
- 「立即升级」→ `wx.showModal` 二次确认 → POST upgrade → 轮询 ota_status（2s）直到 `ok`/`failed`/`rolled_back` 或超时提示「请查看设备屏幕」
- 错误文案映射 reason token → 中文（busy→出图结束后再试，等）

- [ ] **Step 1: 页面与入口**
- [ ] **Step 2: 本地开发者工具点通 mock / 真 portal**
- [ ] **Step 3: 版本号 bump weapp（如 0.2.11）并 CLI 上传（用户设体验版）**

---

### Task 8: 联调 HIL + 文档收口

**Files:**
- `docs/protocol.md` — §10 增 `hutuji.ota_*`、status 字段、失败 reason
- `docs/release-readiness.md` — 🟡 小派远程 OTA：代码/portal 已有；HIL 条目分列（成功升、断网、忙拒、错板；坏包回滚未做不得 ✅）
- `docs/worktree-inventory.md` — 登记脏树
- `docs/agent-edit-allowlist.json` — 增键记录本批授权的 xiaozhi 路径意图（ota/job/三板 tools/CMake PROJECT_VER）
- `docs/superpowers/specs/2026-09-09-xiaopai-ota-mass-prod-design.md` — 状态保持已批准；可链到本 plan

**HIL 最小集（真机，记设备/commit/日志路径）：**

1. idle 一键升到 catalog 新版本 → 重启后版本变新 → `hutuji.status` + 语音冒烟  
2. 升级中拔网或停 nginx：失败后旧版本仍跑、可语音  
3. 出图中点升级：busy，任务不中断  
4. catalog 故意错 board：board_mismatch  
5. 每天自检：改 NVS 日戳或跨日模拟 → 仅提示  
6. （发版门槛，可稍后）故意坏包 → 回滚 — **未做不得写「升级绝对安全」**

- [ ] **Step 1: 文档 diff**
- [ ] **Step 2: hub `python scripts\agent_gate.py --profile docs`（若只文档）或 portal pytest；xiaozhi 侧按该仓习惯测**
- [ ] **Step 3: 更新 readiness；不勾可出货**

---

## Spec coverage（自检）

| Spec 项 | Task |
|---------|------|
| 仅小派 / Grbl 禁用 | 3 UI 文案、7 写字机禁用、catalog device=s3 |
| 自定义版本 | 1、5 ParseVersion、6 PROJECT_VER |
| 每天自检只查不升 | 6 MaybeDailyCheck |
| 小程序一键 + 二次确认 | 7 |
| 自建 HTTPS + manifest | 2、4 |
| F1–F8 失败模型 / 不切槽 | 5–6 复用 UpgradeFirmware；8 HIL |
| busy 拒升 / 升级中不推画 | 2 assert、3 draw 闸 |
| 写字 UX 不做 | 全局约束 |
| 坏包 HIL 门槛 | 4 checklist、8 |

## Placeholder scan

无 TBD/「类似 Task N」占位；阿里云 gray 插入须按 deployment 手工（已写明禁止整份覆盖）。

---

## 执行方式

Plan complete and saved to `docs/superpowers/plans/2026-09-09-xiaopai-ota-mass-prod.md`.

**两种执行选项：**

1. **Subagent-Driven（推荐）** — 每任务新开子代理，任务间审查  
2. **Inline Execution** — 本会话按 executing-plans 连续做，设检查点  

要哪一种？
