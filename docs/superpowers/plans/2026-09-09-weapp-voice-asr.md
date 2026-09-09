# 小程序语音聊小派 P1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 小程序按住说话 → portal ASR 出字 → 独立会话文本聊小派（同角色提示 + `hutuji.*` 经 messaging）→ 聊天气泡显示回复（无 TTS）。

**Architecture:** 微信 `RecorderManager` 上传音频到 portal；腾讯云**一句话识别** HTTP（密钥只在 portal env）；对话由 portal 编排：系统提示取自 `docs/xiaozhi-agent/role-prompt.md` 设备工具口径 + `HUTUJI_LLM_*` OpenAI 兼容补全 + 工具调用走绑定设备 messaging（与 `/api/device/control` 同源）。**不是**设备麦那条官方 WSS 同会话（定案：独立 session_id）。日后若官方开放多端 chat API，只替换 `voice_agent.chat()`。

**Tech Stack:** draw-portal FastAPI + pytest、微信小程序、腾讯云 ASR REST、httpx、既有 `push_tool_call` / 绑机 token。

**Spec:** `docs/superpowers/specs/2026-09-09-weapp-progress-voice-design.md` §3  
**前置:** 进度 P0 已合；weapp **0.2.16** 已 CLI 上传（体验版须用户在 MP 选中）。

**Worktree:** `D:/Users/hutuji/.worktrees/xiaopai-ota`（`feat/xiaopai-ota-mass-prod`）

## Global Constraints

- 密钥：`DRAW_PORTAL_TENCENT_SECRET_ID` / `DRAW_PORTAL_TENCENT_SECRET_KEY` / `DRAW_PORTAL_TENCENT_ASR_APP_ID`（或等价三键）**只**落 portal env；**禁止**进 weapp / 日志明文。
- ASR 或 LLM 未配置 → **503** 中文 detail，不假成功。
- 会话：小程序生成 `session_id`（≤64）；与设备麦会话无关。
- 工具：仅白名单 `hutuji.status` / `hutuji.remember` / `hutuji.recall` / `hutuji.forget` / `hutuji.abort`（与控制页同类只读+记忆+停止；**不含**点动/confirm，避免误触）。
- 音频上限：**1.5 MiB**；时长建议 ≤60s；格式 `mp3`（RecorderManager）。
- 手机号门禁：`phone_secured`；须已绑机才允许工具（未绑可纯聊，工具调用回中文说明）。
- 版本脚注 bump **v0.2.17**；不宣称可出货。
- Conventional commits；脏树只 stage 本任务文件。

## File map

| File | Responsibility |
|------|----------------|
| `draw-portal/draw_portal/tencent_asr.py` | 腾讯云一句话识别：签名 + POST + 出 `text` |
| `draw-portal/draw_portal/voice_agent.py` | session 短历史 + LLM + tool loop + messaging |
| `draw-portal/draw_portal/app.py` | `POST /api/voice/asr`、`POST /api/voice/turn` |
| `draw-portal/tests/test_tencent_asr.py` | ASR 假 httpx / 缺密钥 503 / 超限 |
| `draw-portal/tests/test_voice_agent.py` | 工具白名单、未绑机、假 LLM+假 push |
| `draw-portal/tests/test_voice_api.py` | 路由鉴权负向 |
| `draw-portal/weapp/pages/chat/*` | 按住说话 + 气泡列表 |
| `draw-portal/weapp/app.json` | 注册 chat 页；从「我的」或控制入口进 |
| `deploy/env.draw-portal.example` | 三键说明（无真值） |
| `docs/protocol.md` | §10.4.x 短记两 API |
| `docs/agent-edit-allowlist.json` | `xiaopai_voice_p1_authorized` |
| `docs/templates/weapp-control-smoke.md` | 语音手测行 |
| `docs/release-readiness.md` / `worktree-inventory.md` | 证据 |

---

### Task 1: 白名单 + protocol 短位 + env 例

**Files:**
- Modify: `docs/agent-edit-allowlist.json`
- Modify: `docs/protocol.md`（门户语音两 API 短记，放在 §10.4 邻近）
- Modify: `deploy/env.draw-portal.example`
- Modify: `docs/superpowers/specs/2026-09-09-weapp-progress-voice-design.md`（文首链本计划）

**Interfaces:**
- Produces allowlist key `xiaopai_voice_p1_authorized` covering all File map paths above + this plan.

- [ ] **Step 1:** 追加 allowlist JSON 键（paths 列全上表）。
- [ ] **Step 2:** protocol 增加约 15 行：`POST /api/voice/asr`（multipart `file`）→ `{text}`；`POST /api/voice/turn` JSON `{session_id, text}` → `{reply, tool_traces?}`；phone_secured；独立 session。
- [ ] **Step 3:** env.example 三键注释「空=语音关闭」。
- [ ] **Step 4:** Commit `docs: authorize weapp voice P1 and document APIs`

---

### Task 2: `tencent_asr.py` + 测

**Files:**
- Create: `draw-portal/draw_portal/tencent_asr.py`
- Create: `draw-portal/tests/test_tencent_asr.py`

**Interfaces:**
- `class AsrError(Exception)` — `args[0]` 中文
- `def asr_configured() -> bool`
- `def recognize_sentence(*, audio: bytes, voice_format: str = "mp3") -> str`
  - 未配置 → `AsrError("语音识别未配置")`
  - `len(audio) > 1_572_864` → `AsrError("录音过长")`
  - 空音频 → `AsrError("录音为空")`
  - 调用腾讯云一句话识别（`asr.tencentcloudapi.com` SentenceRecognition）；成功返回 strip 后文本；上游错误只含状态/码，不回显密钥

实现口径（钉死，便于 TDD）：
- 用现成 `hmac`/`hashlib` 签 TC3-HMAC-SHA256（可抄腾讯文档最小实现，**禁止**把 SecretKey 写入测试）。
- httpx 超时 20s。

- [ ] **Step 1: 失败测**

```python
# draw-portal/tests/test_tencent_asr.py
import pytest
from draw_portal import tencent_asr as m

def test_not_configured(monkeypatch):
    monkeypatch.delenv("DRAW_PORTAL_TENCENT_SECRET_ID", raising=False)
    monkeypatch.delenv("DRAW_PORTAL_TENCENT_SECRET_KEY", raising=False)
    monkeypatch.delenv("DRAW_PORTAL_TENCENT_ASR_APP_ID", raising=False)
    assert m.asr_configured() is False
    with pytest.raises(m.AsrError, match="未配置"):
        m.recognize_sentence(audio=b"abc")

def test_too_large(monkeypatch):
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_SECRET_ID", "id")
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_SECRET_KEY", "key")
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_ASR_APP_ID", "123")
    assert m.asr_configured() is True
    with pytest.raises(m.AsrError, match="过长"):
        m.recognize_sentence(audio=b"x" * (1_572_864 + 1))

def test_empty(monkeypatch):
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_SECRET_ID", "id")
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_SECRET_KEY", "key")
    monkeypatch.setenv("DRAW_PORTAL_TENCENT_ASR_APP_ID", "123")
    with pytest.raises(m.AsrError, match="为空"):
        m.recognize_sentence(audio=b"")
```

- [ ] **Step 2:** RED → 实现 → GREEN（另加 1 测：monkeypatch httpx 回假 JSON `Result` 字段出字）。
- [ ] **Step 3:** Commit `feat(portal): tencent sentence ASR helper`

---

### Task 3: `voice_agent.py` + 测

**Files:**
- Create: `draw-portal/draw_portal/voice_agent.py`
- Create: `draw-portal/tests/test_voice_agent.py`

**Interfaces:**
- `VOICE_TOOL_ALLOWLIST = frozenset({"hutuji.status","hutuji.remember","hutuji.recall","hutuji.forget","hutuji.abort"})`
- `class VoiceAgentError(Exception)`
- `def chat(*, session_id: str, user_text: str, device_mac: str | None, push_tool) -> dict`
  - 返回 `{"reply": str, "tool_traces": list[dict]}`
  - `push_tool(name, arguments) -> str` 由 app 注入（绑机 messaging）
  - 未配置 `HUTUJI_LLM_*`（读 portal 已有 config 或 env `HUTUJI_LLM_API_KEY`/`BASE_URL`/`MODEL`，与 article_llm 同源）→ `VoiceAgentError("对话未配置")`
  - 工具名不在白名单 → 不调用，reply 说明
  - 无 `device_mac` 时若模型要调工具 → reply 含「请先绑定写字机」
  - session 历史进程内 dict，cap **12** 条消息 / session；超则丢最旧

System prompt 固定开头（中文，截断角色文件前 1200 字或内嵌精简版）：只允许用白名单工具；记住/召回走 hutuji.remember/recall；不要编造设备状态。

Tool loop：最多 **3** 轮 tool calls。

- [ ] **Step 1–4:** 假 LLM（返回 tool_calls / 纯文本）+ 假 push；测白名单拒绝、未绑机、正常 status。
- [ ] **Step 5:** Commit `feat(portal): voice agent session and tool loop`

---

### Task 4: HTTP 路由

**Files:**
- Modify: `draw-portal/draw_portal/app.py`
- Create: `draw-portal/tests/test_voice_api.py`

**Interfaces:**
- `POST /api/voice/asr` phone_secured, multipart file → `{ "text": "..." }`；AsrError → 400/503
- `POST /api/voice/turn` phone_secured, body `VoiceTurnBody(session_id, text)` → chat 结果；空 text 400
- 组合可选：weapp 可先 asr 再 turn（两步），不做第三端点

- [ ] **Step 1:** 测未登录 401；缺密钥 asr 503；turn 空文 400（TestClient 或现有 portal 测风格）。
- [ ] **Step 2:** 接线实现。
- [ ] **Step 3:** Commit `feat(portal): /api/voice/asr and /api/voice/turn`

---

### Task 5: weapp 聊天页

**Files:**
- Create: `draw-portal/weapp/pages/chat/chat.{js,wxml,wxss,json}`
- Modify: `draw-portal/weapp/app.json`（pages 注册；**不加** tabBar，从「我的」入口）
- Modify: `draw-portal/weapp/pages/profile/profile.wxml` + `.js`（「语音聊小派」入口）
- Modify: footers → **v0.2.17**（control/profile/firmware/chat）

**UI：**
- 气泡列表：`messages[{role,content}]`
- 按住说话：`touchstart` → `RecorderManager.start({format:'mp3'})`；`touchend` → stop → upload `/api/voice/asr` → 展示用户字 → `/api/voice/turn`
- 失败 toast 中文；录音中禁用连点

- [ ] **Step 1–3:** 实现 + 本地无真机时至少保证无语法错（开发者工具编译由上传验证）。
- [ ] **Step 4:** Commit `feat(weapp): hold-to-talk chat page v0.2.17`

---

### Task 6: 文档 + 双机 env 提示 + CLI 上传

**Files:**
- Modify: smoke / readiness / inventory
- 人工：生产注入三键（用户提供或已有腾讯云账号）后 restart portal；CLI upload 0.2.17

- [ ] **Step 1:** smoke 追加行：按住说话出字；说「写字机状态」见工具痕迹或状态摘要。
- [ ] **Step 2:** readiness 🟡（代码+上传；密钥/HIL ☐）。
- [ ] **Step 3:** `python3 scripts/agent_gate.py --profile docs`
- [ ] **Step 4:** CLI upload 0.2.17（同 0.2.16 流程）。
- [ ] **Step 5:** Commit docs。

---

## Spec coverage

| Spec §3 | Task |
|---------|------|
| Recorder → portal ASR | 2,4,5 |
| 腾讯云密钥仅服务端 | 1,2,Global |
| 独立 session + hutuji.* | 3 |
| 气泡文字、无 TTS | 5 |
| 非目标（直连设备音频等） | Global |

**诚实边界：** 文本补全走 `HUTUJI_LLM_*` portal 编排，工具走官方 messaging；与设备麦 WSS **不同会话**（已定案）。非「绕过官方云自建全栈」——工具面仍官方。
