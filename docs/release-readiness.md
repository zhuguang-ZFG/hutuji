# 发布与出货就绪度

> 状态日期：2026-07-26（S3 M2 构建烧录+配网实测更新）。本文是”现在到底做到哪”的唯一状态页；每次通过新门禁后同步更新。

## 结论

**当前不可商业出货。**  
① 基础文档 + **本仓可跑门禁/Cursor 硬轨**已落地；MCP 本地与小智云工具发现已通过。  
Grbl §9（须 fz pass）、S3 M2、真实硬件联调、VPS 常驻、法规/客户资料签核均未闭合。

## 证据矩阵

| 层 | 当前状态 | 已有证据 | 出货前缺口 |
|----|----------|----------|------------|
| 架构/协议 | ✅ 已定案 | `README.md` 决策表；`protocol.md` v0.5 | 实测数字回填后评审 v0.6/或保留 v0.5 实测附录 |
| 基础文档地图 | ✅ 已齐 | `docs/README.md` + `agent-handoff.md` + `docs/templates/` + `deploy/` | 按阶段回填 🔬 与签核，不把文档当证据 |
| **本仓 Agent 门禁** | ✅ 已建立 | `agent-gate.md` + `scripts/agent_gate.py` + Cursor 硬轨 + `foundation-checklist.md` | 交接前重跑 `hub`；报告归档 `results/hutuji_gate_last.md` |
| **防幻觉 / 禁乱改资料** | ✅ 已建立 | `agent-anti-drift.md` + `agent-edit-allowlist.json` + `worktree-inventory.md`（门禁 docs 层校验） | 提交/丢弃脏树后更新 inventory；白名单外改动须用户点名 |
| **三仓 agent 约束闭环** | ✅ 已建立 | `agent-constraint-matrix.md`；Grbl/xiaozhi/fz 根 `AGENTS.md` 入口；门禁校验 Grbl AGENTS 存在 | 外仓 AGENTS 变更须保持指向 hutuji；工程完成仍看下方各层 |
| MCP 本地服务 | ✅ 自动化通过 | pytest；`e2e_local.py` PASS（经本仓门禁 `hub`） | 在发布 commit 上重跑并归档输出 |
| MCP 安全基础 | ✅ 代码/测试通过 | CRC32、512KiB、SVG 白名单、TTL、限流、localhost；stderr/status 脱敏 | 京东云 nginx/TLS/防火墙实机验证 |
| 小智云桥接 | 🟡 工具发现+本机 call 通过 | WSS `tools/list`；本机 `tools/call hutuji_draw`→`/files`+CRC；**智能体已配**：DeepSeek V4 + 角色提示(few-shot) + 知识库(召回OK) + 助手名=呼图机（2026-07-26） | App/控制台真实对话 `tools/call`；长期重连 |
| Grbl §9 加固 | 🟡 已提交+fz 绿、未烧录实机 | Grbl `cdf923f`（§9+`*Core.h`+AGENTS）；fz `4c26bbc` 契约对齐；fz `standard` 曾 **overall=pass**（2026-07-26） | **烧录** + bringup 阶段 2 实机（`Changing=` / Telnet `[PaperStatus]`）；**禁止**口头「§9 已完成」直至阶段 2 |
| S3 M1 | 🟡 已在分支 | `feat/hutuji-usb-pipe`；`firmware/m1-usb-pipe.md` | 构建/烧录 |
| S3 M2 哑管道 | 🟡 已构建烧录+配网验证、未联调 Grbl | xiaozhi `673be32`→正在构建新版（UI 定制+出图进度显示）：IDF 6.0.1；屏幕文案「呼图机」品牌化；出图时显示「画画中 35% (120/340)」进度；WiFi 配网实测 OK（ChinaNet-jJmz IP=192.168.1.12）；MCP 工具注册 OK；Pipe 指数退避正常 | Grbl 写字机联调（Telnet 连通+逐行流控）；断连复位重发仍缺 |
| 写字机实机 | 🔴 零本批次证据 | 仅成熟现役固件事实与源码推导 | 阶段 0–2、7–8 真实记录 |
| 京东云生产环境 | 🟡 桥接已连、待对话验收 | MCP+bridge+nginx；llm7；`tools/call` 出图 OK；80/443 `/files`；知识库已上传 | App 对话验收；正式 TLS；阶段 6 |
| 生产/合规 | 🔴 未签核 | SOP + `docs/templates/` + `compliance.md` | BOM、标签、法规、开源通知、保修/售后负责人签核 |

## 当前最近一步

1. 有 Grbl 写字机后：烧录 Grbl §9 → bringup 阶段 2；S3 改 `HUTUJI_PIPE_HOST` 为写字机实际 IP → 重新构建烧录 → 阶段 3+ Telnet 联调。  
2. 无 Grbl 时：S3 已烧录+配网验证通过（`673be32`，WiFi=ChinaNet-jJmz，MQTT 连通，MCP 工具注册 OK，Pipe 指数退避正常）；补断连复位重发（M2 #5）。  
3. 本仓交接：`python scripts/agent_gate.py --profile hub`。

## 状态更新规则

- ✅：命令、commit、设备编号、日志或记录表可复核。
- 🟡：设计/代码存在但缺生产环境、硬件证据或外部门禁 pass。
- 🔴：未实施、未验证或存在硬阻断。
- 状态提升必须在 `docs/templates/release-manifest.md` 或 `docs/templates/device-test-record.md` 有证据路径；本仓软件层可附 `results/hutuji_gate_last.md`。
- 失败记录不得删除；修复后追加复测结果和关联 commit。
- **禁止宣称表**见 `docs/agent-gate.md` §5（可出货 / §9 已完成 / 硬件已验证 等）。
