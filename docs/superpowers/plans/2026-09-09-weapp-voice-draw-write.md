# 小程序语音画+写（确认卡）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 小程序聊小派能「画/写」：生成并推预览到绑定机，用户在聊天页确认后才 `hutuji.confirm`。

**Architecture:** portal LLM 增 `voice.draw`/`voice.write`（复用 internal draw + write 排版）；`push_draw_self_heal(auto_confirm=False)`；turn 回包带 `preview`；新增 `/api/voice/confirm|cancel`；weapp chat 预览卡。

**Tech Stack:** FastAPI draw-portal、既有 `push.py`/`pipeline`、weapp、pytest

## Global Constraints

- 白名单路径见 `agent-edit-allowlist.json` → `xiaopai_voice_draw_write_authorized`
- 不改固件 / §5 / 换纸；不自动 confirm
- 密钥不进 weapp；不宣称可出货

---

## File map

| 文件 | 职责 |
|------|------|
| `draw_portal/voice_agent.py` | 工具 schema + 执行分派（portal vs device） |
| `draw_portal/voice_jobs.py` | 生成+发布（画/写）纯函数，易测 |
| `draw_portal/app.py` | turn 接线、confirm/cancel 路由 |
| `tests/test_voice_*.py` | 契约 |
| `weapp/pages/chat/*` | 预览卡 UI |
| `docs/protocol.md` §10.4.13 | 契约补丁 |
| readiness / inventory / allowlist | 进度 |

---

### Task 1: voice_jobs + agent 工具

- [ ] RED：`test_voice_jobs.py` / agent allowlist 含 `voice.draw`/`voice.write`；confirm 不在 LLM 白名单
- [ ] GREEN：`voice_jobs.py` + 改 `voice_agent.py`（`run_portal_tool` 回调）
- [ ] pytest 相关 pass

### Task 2: HTTP confirm/cancel + turn preview

- [ ] RED：`test_voice_api.py` turn 回 preview；confirm/cancel 401/未绑机/成功
- [ ] GREEN：`app.py` 接线日限额、push、回包
- [ ] pytest pass

### Task 3: weapp 预览卡

- [ ] chat 消息带 preview；开始画/取消调 API
- [ ] 版本 bump + 上传（若本机 CLI 可用）

### Task 4: 文档门禁

- [ ] protocol §10.4.13、allowlist、readiness、inventory
- [ ] `python scripts/agent_gate.py --profile docs`（或 hub 若触及测）
- [ ] 双机部署 portal 改动文件 + restart（用户可验 HIL）
