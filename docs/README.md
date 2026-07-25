# hutuji 文档总入口

> 面向第一次接手的开发者、测试人员、生产人员和 AI agent。先看本页，再按角色进入对应文档。

## 先读：真值优先级

文档冲突时按以下顺序处理，**不要自行折中**：

1. 根目录 `README.md` 的架构决策记录：决定做什么、不做什么。
2. `protocol.md` v0.5：S3、Grbl、MCP 服务之间的可执行契约。
3. `bringup-sop.md`：从底层到端到端的验证顺序和阶段通过判据。
4. `release-readiness.md`：当前哪些已验证、哪些仍阻断出货。
5. 各子系统 README/运行手册：只解释本层如何实现和操作，不得改写上层契约。

发现冲突时：停止相关实施 → 记录文件与段落 → 以高优先级文档修正低优先级文档 → 补回归证据。

## 新 agent 的前 30 分钟

0. **必读防呆**：`agent-handoff.md`（常见误判、禁止重开的决策、默认下一刀）。不明所以时先回到该页。
1. **读门禁定义**：`agent-gate.md`；结束前跑 `python ../scripts/agent_gate.py`（在仓库根）。
2. 读根目录 `AGENTS.md`、`README.md`（决策 **7 条**，编号以此为准）。
3. 读 `protocol.md`，重点看 §1.1、§1.2、§2.1、§4.1、§5.1、§9。
4. 读 `release-readiness.md`，不要把“代码存在”误报成“硬件已验证”。
5. 按任务选择：
   - MCP/云端：`development.md` → `../mcp-server/README.md`（组 0–3 **多已落地**，勿当待办重做）。
   - Grbl/S3/硬件联调：`bringup-sop.md` → `../firmware/README.md`；改 Grbl 另跑 fz 门禁。
   - 京东云：`deployment.md` → `operations.md` → `../deploy/README.md`。
   - 小批量出货：`production-sop.md` → `release-checklist.md` → `compliance.md`。
6. 运行 `git status --short`，保留所有已有修改；凭据只进进程环境，不进仓库。

## 文档地图

| 文档 | 读者 | 用途 | 是否权威 |
|------|------|------|----------|
| `agent-handoff.md` | **AI agent（首选）** | 防呆、误判表、默认下一刀 | 接手导向 |
| `agent-gate.md` | AI agent / 评审 | 本仓完成定义、profile、禁止宣称 | **本仓门禁真值** |
| `foundation-checklist.md` | AI agent / 负责人 | ① 基础建设闭环勾选 | ① 完成定义 |
| `protocol.md` | 固件/MCP 开发 | 板间协议、状态机、G-code、错误语义 | **契约真值** |
| `bringup-sop.md` | 联调/测试 | 阶段 0–9，从写字机到出货门禁 | **执行顺序** |
| `release-readiness.md` | 全员 | 当前证据、阻断项、下一步 | **状态真值** |
| `development.md` | 软件开发/agent | 环境、调试、测试、代码评审、证据 | 开发规范 |
| `troubleshooting.md` | 联调/售后 | 按现象和层级定位故障 | 诊断手册 |
| `deployment.md` | 运维 | 京东云部署、TLS、服务、回滚 | 部署规范 |
| `operations.md` | 运维/值守 | 监控、日志、告警、备份、事件响应 | 运维规范 |
| `production-sop.md` | 生产/QA | 首件、逐台检验、老化、包装、放行 | **出货流程** |
| `release-checklist.md` | 发布负责人 | 三仓冻结、构建、哈希、审批、回滚 | **版本放行** |
| `compliance.md` | 负责人/法务/QA | 安全、隐私、开源许可证、法规阻断 | 合规清单 |
| `customer-quickstart.md` | 客户/售后 | 安装、联网、使用、异常处理 | 客户资料母版 |
| `research.md` | 研发 | 外部来源与待复核事实 | 来源索引 |
| `reference-projects.md` | 研发/合规 | 参考仓库、许可证、采用边界 | 选型与许可边界 |
| `hardware.md` | 硬件研发 | 当前链路与备用接线 | 硬件留档 |

记录模板位于 `docs/templates/`（`device-test-record.md`、`release-manifest.md`）；部署模板位于仓库根 `deploy/`（systemd / nginx / `env.example`）。  
生产落点**仅京东云** `117.72.118.95`。模板中的占位字段必须在批次放行前填写，禁止删掉未通过项目来“做绿”。  
Cursor 硬轨：`.cursor/rules/hutuji-agent-rails.mdc`。可跑门禁：仓库根 `python scripts/agent_gate.py`。

## 完成定义

“开发完成”“联调完成”“可出货”是三个不同状态：

- 本仓开发完成：`scripts/agent_gate.py --profile hub` overall=pass，且文档已同步。
- Grbl 相关完成：另加 fz `agent_gate.py` overall=pass（本仓门禁不替代）。
- 联调完成：`bringup-sop.md` 对应阶段有真实设备证据。
- 可出货：阶段 0–9 全过，逐台记录、版本清单、合规审批和客户资料齐全。

任一 agent 只有在相应证据存在时才能使用上述表述；禁止宣称表见 `agent-gate.md` §5。
