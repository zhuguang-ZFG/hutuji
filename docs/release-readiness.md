# 发布与出货就绪度

> 状态日期：2026-07-27（S3↔Grbl WiFi Telnet 哑管道首次联通）。本文是”现在到底做到哪”的唯一状态页；每次通过新门禁后同步更新。

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
| MCP 安全基础 | ✅ 代码/测试/VPS 部署均通过 | CRC32、512KiB、SVG 白名单（256KB 上限+64 层深度限制）、TTL、限流、localhost；stderr/status 脱敏（不泄露绝对路径）；§5 M-code 黑名单；TOCTOU 竞态修复（Response 内存发送+status glob _safe_mtime）；SVG+gcode 孤文件清理（svg_to_gcode 和 validate_gcode 失败均清理）；Content-Disposition attachment；坐标容差对称（±0.01mm）；圆弧整圆浮点精度修复；grbl_sim 绑 127.0.0.1；**G-code 注释剥离（`;`后 X/Y 不再误拒）**。**安全审计 11 项 + code review 7 项全修**（2026-07-26，84 条测试，门禁 pass） | ✅ 已部署 VPS（2026-07-26）：preflight 8/8；HTTPS 301 跳转生效；`/mcp` 404；8300 不可达 |
| 小智云桥接 | 🟡 工具发现+本机 call 通过 | WSS `tools/list`；本机 `tools/call hutuji_draw`→`/files`+CRC；**智能体已配**：DeepSeek V4 + 角色提示(few-shot) + 知识库(召回OK) + 助手名=呼图机（2026-07-26） | App/控制台真实对话 `tools/call`；长期重连 |
| Grbl §9 加固 | ✅ 已烧录+实机验证 | Grbl `e7f373f`（§9 keepalive 改 setsockopt 修编译）；fz `standard` **overall=pass**；**实机验证（2026-07-27）**：COM3 烧录 OK → WiFi STA ChinaNet-jJmz 信号 100% IP=192.168.1.13 → Telnet:23 连通收 banner+`?`状态 → `[ESP901]` 返回 `Paper=No Changing=Off` → §9-A keepalive/§9-B ESP901/§9-C Changing 字段均通过 | bringup 阶段 2（换纸中 `Changing=On` 验证须有纸）；S3 Telnet 联调 |
| S3 M1 | 🟡 已在分支 | `feat/hutuji-usb-pipe`；`firmware/m1-usb-pipe.md` | 构建/烧录 |
| S3 M2 哑管道 | ✅ Telnet 联调通过 | xiaozhi `4485d3a`（IDF 6.0.1）：**Telnet 联调首次通过（2026-07-27）**：S3(192.168.1.12)→Grbl(192.168.1.13:23) Telnet 连通，收到 banner `Grbl 1.3a`+版本 `[VER:1.3a.20211103:]`+机型 `Custom 3-Axis HR4988`；指数退避重连正常（WiFi 未就绪时 1→2→4s）；MQTT 连通 `mqtt.xiaozhi.me`；MCP 工具 hutuji.status/draw/abort 注册 OK | 逐行流控验证（发 G-code 看 ok 计数）；断连复位重发；端到端语音出图 |
| 写字机实机 | 🟡 阶段 0 通电+WiFi+Telnet 验证 | MK38 板 ChipID=0000FC7742C63C94；COM3 CH340（VID:1A86 PID:7523）；WiFi STA IP=192.168.1.13；Telnet:23 连通；`[ESP901]` OK；`[License] Authorized`（2026-07-27） | 阶段 1–2（归零/换纸/出图）、7–8 真实记录 |
| 京东云生产环境 | 🟡 安全补丁已部署、待对话验收 | MCP+bridge+nginx；llm7；`tools/call` 出图 OK；80→301 HTTPS；443 `/files`；知识库已上传；**安全审计 11 项全修已部署**（2026-07-26，preflight 8/8，公网复验通过） | App 对话验收；正式 TLS；阶段 6 |
| 生产/合规 | 🔴 未签核 | SOP + `docs/templates/` + `compliance.md` | BOM、标签、法规、开源通知、保修/售后负责人签核 |

## 当前最近一步

1. **逐行流控验证**：通过 Pipe 发几行 G-code，确认 ok 逐行返回、S3 计数正确。
2. **阶段 1–2 bringup**：归零（`$H`）→ 手动 G-code 出图 → 换纸触发 `Changing=On` 验证。
3. **端到端语音出图**：App 语音 → 小智云 → MCP 生成 → S3 下载 → Pipe 转发 → Grbl 画。
4. 本仓交接：`python scripts/agent_gate.py --profile hub`。

## 状态更新规则

- ✅：命令、commit、设备编号、日志或记录表可复核。
- 🟡：设计/代码存在但缺生产环境、硬件证据或外部门禁 pass。
- 🔴：未实施、未验证或存在硬阻断。
- 状态提升必须在 `docs/templates/release-manifest.md` 或 `docs/templates/device-test-record.md` 有证据路径；本仓软件层可附 `results/hutuji_gate_last.md`。
- 失败记录不得删除；修复后追加复测结果和关联 commit。
- **禁止宣称表**见 `docs/agent-gate.md` §5（可出货 / §9 已完成 / 硬件已验证 等）。
