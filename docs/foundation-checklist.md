# ① 基础建设闭环清单

> **目的**：把「仓库奠基」从口头齐活变成可勾选、可门禁的闭环。  
> **完成定义**：本页全部勾选 **且** `python scripts/agent_gate.py --profile hub` 为 pass。  
> 勾选 ≠ 可出货；出货仍看 `release-readiness.md` + `bringup-sop.md`。

状态日期：2026-07-26。

## A. 真值与防跑偏

- [x] 根 `README.md` 决策表 **7 条**锁定（双芯片 / Telnet / 哑管道 / 官方云 / 京东云 / 换纸口径 / 枢纽仓）
- [x] `docs/protocol.md` **v0.5** 为板间契约真值（含 §9 清单）
- [x] `docs/agent-handoff.md` 防呆页（误判表 + 禁止重开 + 默认下一刀）
- [x] `docs/agent-gate.md` 本仓门禁定义
- [x] `docs/agent-anti-drift.md` 防幻觉 / 禁乱改
- [x] `docs/agent-edit-allowlist.json` 改码白名单（机读）
- [x] `docs/worktree-inventory.md` 跨仓脏树清单
- [x] `.cursor/rules/hutuji-agent-rails.mdc` Cursor 硬轨（alwaysApply）
- [x] `docs/README.md` 真值优先级与文档地图
- [x] `AGENTS.md` / `CLAUDE.md` 与决策编号对齐

## B. 执行顺序与状态

- [x] `docs/bringup-sop.md` 阶段 0–9（未过不得进下一阶段）
- [x] `docs/release-readiness.md` 证据矩阵（唯一状态页）
- [x] `docs/development.md` 开发边界与命令
- [x] `docs/troubleshooting.md` 分层排障入口

## C. 部署 / 生产模板（有模板 ≠ 已上线）

- [x] `docs/deployment.md` + `deploy/`（systemd / nginx / env.example）
- [x] `docs/operations.md` 值守骨架
- [x] `docs/production-sop.md` + `docs/release-checklist.md` + `docs/compliance.md`
- [x] `docs/templates/device-test-record.md` + `release-manifest.md`
- [x] `docs/customer-quickstart.md` 客户资料母版

## D. 可运行软件基线（本仓）

- [x] `mcp-server/` 可安装、可测（pytest + `e2e_local.py`）
- [x] `scripts/agent_gate.py` 枢纽门禁可跑
- [x] 已建立并可跑：`python scripts/agent_gate.py --profile hub` → overall pass（证据：`results/hutuji_gate_last.md`；**每次交接前重跑**）

## E. 明确不属 ① 闭环（禁止勾进本页假装完成）

| 项 | 归属 | 当前 |
|----|------|------|
| Grbl §9 实改 + fz pass | 工程下一刀 / bringup 阶段 2 | 🟡 工作树可能有未提交改动；**未**宣称完成 |
| S3 M2 哑管道 | 路线图 ② | 🔴 |
| 硬件联调证据 | bringup 0–8 | 🔴 |
| 京东云实机 | 路线图 ③ / deployment | 🔴 |
| 出货签核 | 路线图 ④ | 🔴 |

## F. 交接时必做

1. 跑 `python scripts/agent_gate.py --profile hub`，把 `results/hutuji_gate_last.md` 路径写入会话结论。  
2. 若动过 Grbl：另跑 fz 门禁，**分别**报告两个 overall。  
3. 更新 `release-readiness.md` 日期与证据行。  
4. **不覆盖** `git status` 里已有、非本任务的用户改动。
