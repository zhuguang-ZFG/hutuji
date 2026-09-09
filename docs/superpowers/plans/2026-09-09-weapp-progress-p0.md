# 小程序控制页机台进度 P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 控制页用现有 `hutuji.status` 展示人话时间线、多页进度条、出图中 1.5s 轮询，以及有则显示的已画/预计时长。

**Architecture:** 抽出纯函数 `weapp/utils/job_progress.js`（时间线步、轮询间隔、时长格式、页进度）；`control` 页在 `_deriveControls` 中消费；仍经 `/api/device/control` → `hutuji.status`。不改固件、不加 portal 推送。

**Tech Stack:** 微信小程序（CommonJS）、Node 冒烟断言、可选手测清单；无新 Python 依赖。

**Spec:** `docs/superpowers/specs/2026-09-09-weapp-progress-voice-design.md` §2（P0）；§3 语音另开计划。

**Worktree:** `D:/Users/hutuji/.worktrees/xiaopai-ota`（分支 `feat/xiaopai-ota-mass-prod`）；勿覆盖 xiaozhi inventory 脏文件。

## Global Constraints

- 只读现有 `hutuji.status` 字段；**禁止**宣称行级完成百分比（无 `lines_sent`）。
- `eta_ms` UI 文案必须是「预计」总时长，**禁止**写「约剩」。
- 轮询：空闲 **5000** ms；忙碌（见下）**1500** ms。
- 版本脚注 bump 到 **v0.2.16**（control/profile/firmware 三处）。
- 不改双芯片 / Telnet / 换纸 / 固件 StatusJson；不宣称可出货。
- Conventional commits；中文 UI；改动前补 allowlist。

## File map

| File | Responsibility |
|------|----------------|
| `draw-portal/weapp/utils/job_progress.js` | 纯函数：时间线步、poll ms、时长、页条 |
| `draw-portal/weapp/pages/control/control.js` | 自适应 interval + setData 时间线/页条/ETA |
| `draw-portal/weapp/pages/control/control.wxml` | 时间线 + 页条 + ETA 行 |
| `draw-portal/weapp/pages/control/control.wxss` | 时间线/进度条样式（贴合现有深色卡） |
| `draw-portal/weapp/pages/{control,profile,firmware}/*.wxml` | 脚注 v0.2.16 |
| `docs/agent-edit-allowlist.json` | `xiaopai_progress_p0_authorized` |
| `docs/templates/weapp-control-smoke.md` | 进度手测行 + 版本号 |
| `docs/release-readiness.md` / `docs/worktree-inventory.md` | 进度与脏树 |
| `docs/superpowers/specs/2026-09-09-weapp-progress-voice-design.md` | 文首链到本计划 |

**Out of this plan:** ASR / 智能体聊天（spec §3）→ 后续 `docs/superpowers/plans/2026-09-09-weapp-voice-asr.md`。

---

### Task 1: 白名单 + spec 文首

**Files:**
- Modify: `docs/agent-edit-allowlist.json`
- Modify: `docs/superpowers/specs/2026-09-09-weapp-progress-voice-design.md`（文首状态）

**Interfaces:**
- Produces: allowlist 键 `xiaopai_progress_p0_authorized`，paths 含 control 三件套 + `job_progress.js` + smoke 模板 + 两份状态文档

- [ ] **Step 1: 在 `agent-edit-allowlist.json` 根对象追加**

```json
"xiaopai_progress_p0_authorized": {
  "note": "2026-09-09 用户批准 weapp 进度 P0（时间线+1.5s 轮询+页条）；仅 draw-portal/weapp + 文档；不改固件 status。",
  "plan": "docs/superpowers/plans/2026-09-09-weapp-progress-p0.md",
  "paths": [
    "draw-portal/weapp/utils/job_progress.js",
    "draw-portal/weapp/pages/control/control.js",
    "draw-portal/weapp/pages/control/control.wxml",
    "draw-portal/weapp/pages/control/control.wxss",
    "draw-portal/weapp/pages/profile/profile.wxml",
    "draw-portal/weapp/pages/firmware/firmware.wxml",
    "docs/templates/weapp-control-smoke.md",
    "docs/release-readiness.md",
    "docs/worktree-inventory.md",
    "docs/superpowers/specs/2026-09-09-weapp-progress-voice-design.md",
    "docs/superpowers/plans/2026-09-09-weapp-progress-p0.md"
  ]
}
```

- [ ] **Step 2: 改 spec 文首状态行为**

```markdown
> 状态：**已批准**（2026-09-09）→ P0 计划 `docs/superpowers/plans/2026-09-09-weapp-progress-p0.md`；P1 语音另开计划
```

- [ ] **Step 3: Commit**

```powershell
cd D:\Users\hutuji\.worktrees\xiaopai-ota
git add docs/agent-edit-allowlist.json docs/superpowers/specs/2026-09-09-weapp-progress-voice-design.md docs/superpowers/plans/2026-09-09-weapp-progress-p0.md
git commit -m "$(cat <<'EOF'
docs: authorize weapp progress P0 and link plan

EOF
)"
```

（Windows 无 bash HEREDOC 时用：`git commit -m "docs: authorize weapp progress P0 and link plan"`）

---

### Task 2: `job_progress.js` 纯函数 + Node 断言

**Files:**
- Create: `draw-portal/weapp/utils/job_progress.js`

**Interfaces:**
- Produces（全部纯函数，无 wx 依赖）:
  - `POLL_MS_IDLE = 5000`, `POLL_MS_BUSY = 1500`
  - `TIMELINE_LABELS = ["空闲","预览","待确认","准备中","出图中","换纸","结束"]`（长度 7）
  - `function timelineIndex(state, paperChanging) -> number`  // 0..6
  - `function pollIntervalMs(state, otaState) -> number`
  - `function formatDurationMs(ms) -> string`  // `"m:ss"` 或 `""`；非有限/负 → `""`
  - `function pageProgress(page, pages) -> { show:boolean, label:string, percent:number }`
  - `function deriveProgressView(status) -> { timelineIndex, timelineLabels, timelineHint, pollMs, pageShow, pageLabel, pagePercent, elapsedLabel, etaLabel, otaLine }`

映射表（钉死）：

| 输入 | `timelineIndex` |
|------|-----------------|
| `paper_changing === "on"` 或 `state === "paper_change"` | 5 |
| `idle` / `ready` / `""` / 缺省 | 0 |
| `previewing` | 1 |
| `awaiting_confirmation` | 2 |
| `downloading` / `verifying` | 3 |
| `streaming` | 4 |
| `paused` | 4（hint=`已暂停`） |
| `done` / `error` / `aborted` | 6 |
| 其它未知 state | 0 |

`pollIntervalMs`：若 `state ∈ {downloading,verifying,streaming,paused,paper_change}` 或 `otaState` 非空且非 `"idle"` → 1500，否则 5000。

`pageProgress`：`pages` 为数字且 `> 0` 时 `show=true`，`label="${page||1} / ${pages}"`，`percent = min(100, max(0, round(100*(page||1)/pages)))`；否则 `show=false`。

- [ ] **Step 1: 写入完整模块**

```javascript
// draw-portal/weapp/utils/job_progress.js
const POLL_MS_IDLE = 5000;
const POLL_MS_BUSY = 1500;
const TIMELINE_LABELS = ["空闲", "预览", "待确认", "准备中", "出图中", "换纸", "结束"];

const BUSY_STATES = {
  downloading: 1,
  verifying: 1,
  streaming: 1,
  paused: 1,
  paper_change: 1,
};

function timelineIndex(state, paperChanging) {
  const s = state == null ? "" : String(state);
  if (paperChanging === "on" || s === "paper_change") return 5;
  if (s === "previewing") return 1;
  if (s === "awaiting_confirmation") return 2;
  if (s === "downloading" || s === "verifying") return 3;
  if (s === "streaming" || s === "paused") return 4;
  if (s === "done" || s === "error" || s === "aborted") return 6;
  return 0;
}

function pollIntervalMs(state, otaState) {
  const s = state == null ? "" : String(state);
  if (BUSY_STATES[s]) return POLL_MS_BUSY;
  const o = otaState == null ? "idle" : String(otaState);
  if (o && o !== "idle") return POLL_MS_BUSY;
  return POLL_MS_IDLE;
}

function formatDurationMs(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n < 0) return "";
  const sec = Math.floor(n / 1000);
  const m = Math.floor(sec / 60);
  const r = sec % 60;
  return m + ":" + (r < 10 ? "0" : "") + r;
}

function pageProgress(page, pages) {
  const total = Number(pages);
  if (!Number.isFinite(total) || total <= 0) {
    return { show: false, label: "", percent: 0 };
  }
  const cur = Number(page);
  const p = Number.isFinite(cur) && cur > 0 ? cur : 1;
  const percent = Math.min(100, Math.max(0, Math.round((100 * p) / total)));
  return { show: true, label: p + " / " + total, percent: percent };
}

function deriveProgressView(status) {
  const st = status || {};
  const state = st.state || st.job_state || "";
  const paperChanging = st.paper_changing || "";
  const idx = timelineIndex(state, paperChanging);
  const ota = st.ota || {};
  const otaState = ota.state || "idle";
  let timelineHint = "";
  if (state === "paused") timelineHint = "已暂停";
  if (state === "error" || state === "aborted") timelineHint = state === "error" ? "出错" : "已停止";
  if (state === "done") timelineHint = "完成";
  const page = pageProgress(st.page, st.pages);
  let otaLine = "";
  if (otaState && otaState !== "idle") {
    const prog = typeof ota.progress === "number" ? ota.progress : 0;
    otaLine = "OTA " + otaState + " " + prog + "%";
  }
  const elapsedLabel = formatDurationMs(st.elapsed_ms);
  const etaLabel = formatDurationMs(st.eta_ms);
  return {
    timelineIndex: idx,
    timelineLabels: TIMELINE_LABELS.slice(),
    timelineHint: timelineHint,
    pollMs: pollIntervalMs(state, otaState),
    pageShow: page.show,
    pageLabel: page.label,
    pagePercent: page.percent,
    elapsedLabel: elapsedLabel,
    etaLabel: etaLabel,
    otaLine: otaLine,
  };
}

module.exports = {
  POLL_MS_IDLE,
  POLL_MS_BUSY,
  TIMELINE_LABELS,
  timelineIndex,
  pollIntervalMs,
  formatDurationMs,
  pageProgress,
  deriveProgressView,
};
```

- [ ] **Step 2: Node 断言（期望全部 PASS）**

```powershell
cd D:\Users\hutuji\.worktrees\xiaopai-ota
node -e "const j=require('./draw-portal/weapp/utils/job_progress'); const a=(c,m)=>{if(!c){console.error('FAIL',m);process.exit(1)}}; a(j.timelineIndex('streaming')===4,'stream'); a(j.timelineIndex('paused')===4,'paused'); a(j.timelineIndex('idle','on')===5,'paper'); a(j.timelineIndex('downloading')===3,'dl'); a(j.pollIntervalMs('streaming','idle')===1500,'busy'); a(j.pollIntervalMs('idle','idle')===5000,'idle'); a(j.pollIntervalMs('idle','downloading')===1500,'ota'); a(j.formatDurationMs(65000)==='1:05','fmt'); a(j.pageProgress(2,5).percent===40,'page'); a(j.pageProgress(1,0).show===false,'nosh'); const v=j.deriveProgressView({state:'streaming',elapsed_ms:1000,eta_ms:5000,page:1,pages:3}); a(v.pollMs===1500&&v.pageShow&&v.elapsedLabel==='0:01'&&v.etaLabel==='0:05','derive'); console.log('PASS job_progress')"
```

Expected stdout: `PASS job_progress`，exit 0。

- [ ] **Step 3: Commit**

```powershell
git add draw-portal/weapp/utils/job_progress.js
git commit -m "feat(weapp): add job_progress helpers for control timeline"
```

---

### Task 3: 控制页接线（JS + 自适应轮询）

**Files:**
- Modify: `draw-portal/weapp/pages/control/control.js`

**Interfaces:**
- Consumes: `require("../../utils/job_progress")` → `deriveProgressView`
- Produces: `data` 增字段 `timelineIndex`, `timelineLabels`, `timelineHint`, `pageShow`, `pageLabel`, `pagePercent`, `elapsedLabel`, `etaLabel`, `otaLine`, `timeLine`（可选拼接文案）；方法 `_reschedulePoll(pollMs)`、`_clearPoll()`

- [ ] **Step 1: 文件顶部增加 require；`data` 初始字段**

在现有 `const mcpResult = ...` 后：

```javascript
const jobProgress = require("../../utils/job_progress");
```

`data` 追加：

```javascript
timelineIndex: 0,
timelineLabels: ["空闲", "预览", "待确认", "准备中", "出图中", "换纸", "结束"],
timelineHint: "",
pageShow: false,
pageLabel: "",
pagePercent: 0,
elapsedLabel: "",
etaLabel: "",
otaLine: "",
```

- [ ] **Step 2: 替换 timer 逻辑**

删除 `onShow` 里写死 `setInterval(..., 5000)`。改为：

```javascript
onShow() {
  phoneGate.ensurePhoneOrRedirect(api).then((ok) => {
    if (!ok) return;
    this.refresh();
  });
},

onHide() {
  this._clearPoll();
},

onUnload() {
  this._clearPoll();
},

_clearPoll() {
  if (this._timer) {
    clearInterval(this._timer);
    this._timer = null;
  }
  this._pollMs = 0;
},

_reschedulePoll(pollMs) {
  const ms = pollMs || jobProgress.POLL_MS_IDLE;
  if (this._timer && this._pollMs === ms) return;
  this._clearPoll();
  this._pollMs = ms;
  this._timer = setInterval(() => this.refresh(true), ms);
},
```

- [ ] **Step 3: 在 `_deriveControls` 末尾合并 progress**

在 `return { canConfirm, ... manualLocked }` 之前计算：

```javascript
const prog = jobProgress.deriveProgressView(status);
```

并在 return 对象中展开：

```javascript
timelineIndex: prog.timelineIndex,
timelineLabels: prog.timelineLabels,
timelineHint: prog.timelineHint,
pageShow: prog.pageShow,
pageLabel: prog.pageLabel,
pagePercent: prog.pagePercent,
elapsedLabel: prog.elapsedLabel,
etaLabel: prog.etaLabel,
otaLine: prog.otaLine,
```

- [ ] **Step 4: `refresh` 成功/清空路径都 `_reschedulePoll`**

在 `setData(Object.assign({ status }, this._deriveControls(status)))` 之后：

```javascript
this._reschedulePoll(jobProgress.deriveProgressView(status).pollMs);
```

在 unbound / 解析失败清空分支末尾：

```javascript
this._reschedulePoll(jobProgress.POLL_MS_IDLE);
```

未绑定（`!bound`）时 `_clearPoll()` 即可。

- [ ] **Step 5: Node 再跑 Task 2 断言 + 人工读 `control.js` 确认无残留 `5000` 写死 interval**

```powershell
Select-String -Path "D:\Users\hutuji\.worktrees\xiaopai-ota\draw-portal\weapp\pages\control\control.js" -Pattern "setInterval"
```

Expected: 仅 `_reschedulePoll` 内一处，间隔变量为 `ms`。

- [ ] **Step 6: Commit**

```powershell
git add draw-portal/weapp/pages/control/control.js
git commit -m "feat(weapp): adaptive status poll and progress fields on control page"
```

---

### Task 4: 控制页 WXML/WXSS + 版本脚注

**Files:**
- Modify: `draw-portal/weapp/pages/control/control.wxml`
- Modify: `draw-portal/weapp/pages/control/control.wxss`
- Modify: `draw-portal/weapp/pages/profile/profile.wxml`（脚注）
- Modify: `draw-portal/weapp/pages/firmware/firmware.wxml`（脚注）

**Interfaces:**
- Consumes: Task 3 `data` 字段名一字不差

- [ ] **Step 1: 在 `status-card` 内、`status-bottom` 之后插入**

```xml
<view class="timeline" wx:if="{{status}}">
  <block wx:for="{{timelineLabels}}" wx:key="*this">
    <view class="timeline-step {{index === timelineIndex ? 'active' : ''}} {{index < timelineIndex ? 'done' : ''}}">
      <view class="timeline-dot"></view>
      <text class="timeline-lab">{{item}}</text>
    </view>
  </block>
</view>
<view class="timeline-hint" wx:if="{{timelineHint}}">{{timelineHint}}</view>
<view class="eta-row" wx:if="{{elapsedLabel || etaLabel}}">
  <text wx:if="{{elapsedLabel}}">已画 {{elapsedLabel}}</text>
  <text wx:if="{{elapsedLabel && etaLabel}}"> · </text>
  <text wx:if="{{etaLabel}}">预计 {{etaLabel}}</text>
</view>
<view class="page-bar-wrap" wx:if="{{pageShow}}">
  <view class="page-bar-meta">页 {{pageLabel}}</view>
  <view class="page-bar-track">
    <view class="page-bar-fill" style="width: {{pagePercent}}%;"></view>
  </view>
</view>
<view class="ota-line" wx:if="{{otaLine}}">{{otaLine}}</view>
```

页脚改为 `动力巢 · v0.2.16`。profile / firmware 脚注同步。

- [ ] **Step 2: WXSS 追加（贴合现有 `#00d1e5` / `#14151f`）**

```css
.timeline {
  display: flex;
  flex-wrap: wrap;
  gap: 8rpx 10rpx;
  margin-top: 16rpx;
}
.timeline-step {
  display: flex;
  align-items: center;
  gap: 6rpx;
  opacity: 0.35;
}
.timeline-step.done { opacity: 0.55; }
.timeline-step.active { opacity: 1; }
.timeline-dot {
  width: 12rpx;
  height: 12rpx;
  border-radius: 50%;
  background: #6b7385;
}
.timeline-step.active .timeline-dot { background: #00d1e5; }
.timeline-step.done .timeline-dot { background: #3d9aa8; }
.timeline-lab { font-size: 20rpx; color: #c5cad6; }
.timeline-hint,
.eta-row,
.ota-line,
.page-bar-meta {
  margin-top: 10rpx;
  font-size: 22rpx;
  color: #9aa3b5;
}
.page-bar-wrap { margin-top: 12rpx; }
.page-bar-track {
  margin-top: 6rpx;
  height: 10rpx;
  border-radius: 6rpx;
  background: rgba(255, 255, 255, 0.08);
  overflow: hidden;
}
.page-bar-fill {
  height: 100%;
  background: #00d1e5;
  border-radius: 6rpx;
}
```

- [ ] **Step 3: Commit**

```powershell
git add draw-portal/weapp/pages/control/control.wxml draw-portal/weapp/pages/control/control.wxss draw-portal/weapp/pages/profile/profile.wxml draw-portal/weapp/pages/firmware/firmware.wxml
git commit -m "feat(weapp): control timeline, page bar, and v0.2.16 footers"
```

---

### Task 5: 手测清单 + readiness / inventory

**Files:**
- Modify: `docs/templates/weapp-control-smoke.md`
- Modify: `docs/release-readiness.md`（短条，🟡 待手测或 ✅ 有证据）
- Modify: `docs/worktree-inventory.md`（登记本批路径）

- [ ] **Step 1: smoke 模板**

- 文首体验版改为 ≥ **0.2.16**
- 行 6 页脚改为 v0.2.16
- 追加行：

| # | 步骤 | 期望 | 结果 |
|---|------|------|------|
| 9 | 控制页：空闲观察 | 时间线在「空闲」；约 5s 刷新 | ☐ |
| 10 | 发送一页出图 | 时间线经准备中→出图中；已画/预计有则显示 | ☐ |
| 11 | 多页文章发送 | 页条显示 `page/pages` 并前进 | ☐ |
| 12 | 出图中停在控制页 | 状态更新明显快于空闲（约 1.5s） | ☐ |

- [ ] **Step 2: readiness 追加一条（勿宣称可出货）**

说明：weapp 控制页进度 P0 已合入源码（时间线/页条/1.5s）；真机手测 ☐ 见 `weapp-control-smoke` 9–12。

- [ ] **Step 3: inventory 登记 weapp 改动与计划/spec 路径**

- [ ] **Step 4: 文档门禁**

```powershell
cd D:\Users\hutuji\.worktrees\xiaopai-ota
python scripts\agent_gate.py --profile docs
```

Expected: overall pass（exit 0）。若 `line_ref` 因本批新引用变红：人工核对后 `--accept-line-refs` 并提交基线。

- [ ] **Step 5: Commit**

```powershell
git add docs/templates/weapp-control-smoke.md docs/release-readiness.md docs/worktree-inventory.md docs/agent-line-refs-baseline.json
git commit -m "docs: weapp progress P0 smoke rows and readiness note"
```

---

### Task 6: 体验版上传提示（人工）

**Files:** 无代码

- [ ] **Step 1:** 用现有 CLI/开发者工具上传体验版 **0.2.16**（与 AI 写文批同流程）。
- [ ] **Step 2:** 按 smoke 9–12 勾选；结果写入 readiness（有证据才 ✅）。
- [ ] **Step 3:** 若用户要求再 commit 证据文档。

---

## Spec coverage (self-review)

| Spec § | Task |
|--------|------|
| §2.1 字段映射 | Task 2–4 |
| §2.2 时间线 | Task 2–4 |
| §2.3 轮询 5s/1.5s | Task 2–3 |
| §2.4 改动面 | File map + Tasks |
| §2.5 验收 | Task 5–6 |
| §3 语音 | **本计划明确排除** → 另开 `weapp-voice-asr` |
| §4 非目标 | Global Constraints |

无 TBD；`eta` 文案与固件语义一致（预计总时长）。
