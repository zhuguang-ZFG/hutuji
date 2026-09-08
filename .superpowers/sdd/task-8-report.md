# Task 8 report — 联调 HIL + 文档收口（docs-first）

**Date:** 2026-09-09  
**Worktree:** `D:\Users\hutuji\.worktrees\xiaopai-ota` / `feat/xiaopai-ota-mass-prod`  
**Scope this session:** 文档收口 only；**未跑**真机 HIL 1–6

## Status

**complete（docs）** / HIL **未验证**

## Docs changes

| 文件 | 动作 |
|------|------|
| `docs/protocol.md` | 已有 `hutuji.ota_*` + status OTA 字段；**补** `reason` 稳定 token 表（ok/up_to_date/check_failed/download_failed/verify_failed/write_failed/busy/board_mismatch/rolled_back） |
| `docs/release-readiness.md` | 文首 🟡 OTA 条 + 状态总览 **#18**；HIL ①–⑥ 分列 🔴「未验证」；钉 xiaozhi `73257fa`..`942fcfb`；**不得宣称**升级绝对安全 / 出货签核仍 🔴 |
| `docs/worktree-inventory.md` | §0.1 登记 `feat/xiaopai-ota-mass-prod` worktree；§3 登记 OTA 提交链 + 脏 `hutuji_draw_bind.cc` 禁覆盖 |
| `docs/agent-edit-allowlist.json` | 增键 `xiaozhi_ota_mass_prod_authorized`（ota/job/三板 tools/CMake PROJECT_VER） |
| Spec | 已批准，已链 plan（无改） |
| 行号指纹 | `--fix-line-refs` 45 处纯移动；人工改 6 处空锚/多命中后 `--accept-line-refs`（261 指纹） |

## Gate

```
python scripts/agent_gate.py --profile docs
→ overall=pass exit=0
```

（venv: `D:\Users\hutuji\mcp-server\.venv\Scripts\python.exe`）

## HIL（本会话未跑＝未验证）

| # | 项 | 状态 | 可跑时钉 commit |
|---|-----|------|-----------------|
| 1 | idle 一键升 → 重启版本变新 → status + 语音冒烟 | 🔴 未验证 | xiaozhi ≥`942fcfb`；hub 含 weapp/portal OTA |
| 2 | 升中断网/停 nginx → 旧版仍跑 | 🔴 未验证 | 同上 |
| 3 | 出图中升 → `busy`、任务不中断 | 🔴 未验证 | 同上 |
| 4 | 错 board → `board_mismatch` | 🔴 未验证 | 同上 |
| 5 | 每天自检仅提示 | 🔴 未验证 | `fcaca02`+`942fcfb` |
| 6 | 坏包回滚（发版门槛） | 🔴 未验证 | **未做不得写升级绝对安全** |

xiaozhi：freenove `idf.py build` RC=0（Task 6）；**仍无 flash/HIL**。

## Commits

- Hub docs commit：见本批 `git log -1`（UTF-8，未 push）
- Xiaozhi OTA 范围（外仓，本任务不提交）：`73257fa` → `9194e60` → `fcaca02` → `942fcfb`

## Explicit non-claims

未宣称：HIL 通过、可出货、联调完成、升级绝对安全。
