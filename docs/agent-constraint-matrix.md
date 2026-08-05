# 三仓 Agent 开发约束矩阵（闭环定义）

> **目的**：把「agent 开发约束」从口头提醒变成**跨仓可核对**的闭环。  
> **完成定义**：本页勾选全部完成 **且** 枢纽 `python scripts/agent_gate.py --profile docs` pass。  
> 契约真值仍是 `protocol.md`；本页只钉「谁读什么、谁跑什么门禁、谁不能改什么」。

状态日期：2026-07-28。

## 1. 约束入口必须存在

| 仓 | 路径 | 本地路径 | 最低要求 |
|----|------|----------|----------|
| hutuji（枢纽） | `AGENTS.md` + `docs/agent-anti-drift.md` + `.cursor/rules/hutuji-agent-rails.mdc` | `D:/Users/hutuji` | 防呆 + 门禁 + 白名单 |
| Grbl_Esp32 | `AGENTS.md`（指向 hutuji §9 / fz 门禁） | `D:/Users/Grbl_Esp32` | 商业基线分支 + 禁止改换纸机械 |
| xiaozhi-esp32 | `AGENTS.md` 含 **hutuji 专节** | `D:/Users/xiaozhi-esp32` | 哑管道 / 板级不进核心 |
| fz（仿真） | `AGENTS.md` 含 **hutuji 商业基线**说明 | `D:/Users/zhugu/fz` | 改契约对齐固件，不改固件迁就契约 |

## 2. 动手前强制读序（各仓相同骨架）

1. hutuji `docs/agent-handoff.md`
2. hutuji `docs/agent-anti-drift.md`
3. hutuji `docs/agent-constraint-matrix.md`
4. hutuji `docs/worktree-inventory.md` + hutuji / Grbl / xiaozhi / fz 的 `git status --short`
5. hutuji `docs/release-readiness.md`
6. hutuji `docs/agent-gate.md` + 任务契约：`protocol.md` / bringup / mcp-server README
7. 外仓任务再读该仓根 `AGENTS.md`

## 3. 门禁矩阵（缺一不得宣称）

| 你改了什么 | 必跑 | 通过判据 |
|------------|------|----------|
| 仅 hutuji 文档/规则 | `hutuji`: `scripts/agent_gate.py --profile docs` | overall=pass |
| hutuji MCP / 脚本 | `hutuji`: `--profile hub` | overall=pass |
| Grbl 任何代码（含 §9、`*Core.h`） | `hutuji --profile full`（源码标记）**且** `fz agent_gate.py --profile standard` | 两扇都 pass；仍≠已烧录 |
| fz 仿真/契约 | `fz agent_gate.py`（改 harness 用 standard） | overall=pass；且不得要求商业固件为绿而改机械 |
| xiaozhi hutuji 补丁 | IDF 构建（板型 `lichuang-dev`）+ 更新 hutuji `firmware/*.md` | 构建日志；实机另走 bringup |

## 4. 禁止改动矩阵（摘要）

| 仓 | 禁止 |
|----|------|
| 全仓 | 重开 USB Host / CH9350 / 阿里云；合并双芯片固件；凭据入库；覆盖他人脏树；编造证据 |
| Grbl | 换纸机械/时序/运动/授权/U1；`report_realtime_status` / `report_state_text`；全量换纸消息 `CLIENT_ALL` |
| xiaozhi | 把 hutuji 板级行为塞进核心模块；接受未通过 Grbl banner 验证的 :23 候选或污染 NVS 缓存；出图中用 `[ESP901]`/`M704` 做 status |
| fz | 用改商业固件常量代替更新 `paper_firmware_contract.json`；host SIL 绿宣称 HIL/出货 |
| hutuji | 把固件源码合并进本仓；把 MCP 组 0–4 验收当待办重做 |

## 5. 闭环勾选

- [x] 枢纽：`agent-anti-drift` + `agent-edit-allowlist.json` + Cursor 硬轨 + 本仓门禁  
- [x] Grbl：根目录 `AGENTS.md`（指向 hutuji / fz）  
- [x] xiaozhi：`AGENTS.md` hutuji 专节  
- [x] fz：`AGENTS.md` hutuji 商业基线与契约对齐口径  
- [x] 本矩阵纳入枢纽文档地图与 `docs` 门禁必备文件  

## 6. 与工程进度的边界

本矩阵闭环 **只证明「约束资料齐、入口在、门禁定义在」**。  
不证明：§9 已烧录、M2 完成、硬件联调、京东云上线、可出货。  
那些仍看 `release-readiness.md`。
