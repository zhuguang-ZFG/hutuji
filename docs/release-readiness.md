# 发布与出货就绪度

> 状态日期：2026-07-28（S3 `05f4563` 已提交并通过 IDF 6.0.1 构建；提交前候选的云端工具、abort 与 fresh-`Idle` 已实机回归；当前 commit 尚未烧录，断连/换纸待实测）。本文是“现在到底做到哪”的唯一状态页；每次通过新门禁后同步更新。

## 结论

**当前不可商业出货。**  
① 基础文档与本仓门禁已落地；京东云 HTTPS 常驻，Grbl §9 已烧录并通过已列实机项。
S3 已打通官方云工具调用、直连逐行任务与安全 abort 回归；源码已固化为 `05f4563`，
页尾自动换纸与完整断连安全重画已编码/构建，但当前 commit 尚未烧录，断连/换纸实测、App 语音与生产签核未闭合。

## 证据矩阵

| 层 | 当前状态 | 已有证据 | 出货前缺口 |
|----|----------|----------|------------|
| 架构/协议 | ✅ 已定案 | `README.md` 决策表；`protocol.md` v0.5 | 实测数字回填后评审 v0.6/或保留 v0.5 实测附录 |
| 基础文档地图 | ✅ 已齐 | `docs/README.md` + `agent-handoff.md` + `docs/templates/` + `deploy/` | 按阶段回填 🔬 与签核，不把文档当证据 |
| **本仓 Agent 门禁** | ✅ 已建立 | `agent-gate.md` + `scripts/agent_gate.py` + Cursor 硬轨 + `foundation-checklist.md` | 交接前重跑 `hub`；报告归档 `results/hutuji_gate_last.md` |
| **防幻觉 / 禁乱改资料** | ✅ 已建立 | `agent-anti-drift.md` + `agent-edit-allowlist.json` + `worktree-inventory.md`（门禁 docs 层校验） | 提交/丢弃脏树后更新 inventory；白名单外改动须用户点名 |
| **三仓 agent 约束闭环** | ✅ 已建立 | `agent-constraint-matrix.md`；Grbl/xiaozhi/fz 根 `AGENTS.md` 入口；门禁校验 Grbl AGENTS 存在 | 外仓 AGENTS 变更须保持指向 hutuji；工程完成仍看下方各层 |
| MCP 本地服务 | ✅ 自动化通过 | pytest；`e2e_local.py` PASS（经本仓门禁 `hub`） | 在发布 commit 上重跑并归档输出 |
| MCP 安全基础 | ✅ 代码/测试/VPS 部署均通过 | CRC32、512KiB、SVG 白名单（256KB 上限+64 层深度限制）、TTL、限流、localhost；stderr/status 脱敏；§5 M-code 黑名单；TOCTOU 竞态修复；孤文件清理；Content-Disposition attachment；**A4 画布限幅（X≤190 Y≤277mm，四周 10mm 边距）**；圆弧包围盒校验；G-code 注释剥离。安全审计 11 项 + code review 7 项全修（86 条测试，门禁 pass） | ✅ 已部署 VPS（2026-07-27）：preflight 8/8 |
| 小智云桥接 | 🟡 工具发现+官方云调用通过 | WSS `tools/list`；本机 `tools/call hutuji_draw`→`/files`+CRC；官方小智消息 API 发现七个 `hutuji.*` 工具，并经云端路径实际触发 `draw/status/abort`（2026-07-28）；智能体配置与知识库召回已通过 | App 真实语音对话；七工具逐项云端行为回归；长期重连 |
| Grbl §9 加固 | ✅ 已烧录+实机验证 | Grbl `e7f373f`（§9 keepalive 改 setsockopt 修编译）；fz `standard` **overall=pass**；**实机验证（2026-07-27）**：COM3 烧录 OK → WiFi STA ChinaNet-jJmz 信号 100% IP=192.168.1.13 → Telnet:23 连通收 banner+`?`状态 → `[ESP901]` 返回 `Paper=No Changing=Off` → §9-A keepalive/§9-B ESP901/§9-C Changing 字段均通过 | bringup 阶段 2（换纸中 `Changing=On` 验证须有纸） |
| S3 M1 | ✅ 已集成并上板 | `feat/hutuji-usb-pipe`；提交前 COM6 候选镜像已连接 Grbl Telnet；`firmware/m1-usb-pipe.md` | 当前源码 `05f4563` 尚未烧录；M2 缺口见下一行 |
| S3 M2 哑管道 | 🟡 关键直连闭环已实测，M2 整体未闭合 | `05f4563`：Telnet 连接优化、32 深应答队列与 PSRAM 零拷贝行索引；候选 IP 先验 Grbl banner；应用层静默探活约 21s；官方云路径触发 `draw/status/abort`；提交前候选的 **abort 实机回归**从 `streaming/Run,Z=5` 恢复到 `aborted/Idle,ready=true,authorized=true,Z=0`，8 秒无 XY 漂移/无 planner 续跑，绘图完成判定未见 `done/Run`；当前 commit 新增“绘图 fresh `Idle` → S3 显式 `M30` → 最终应答 → fresh `Idle` → done”及“重连 banner → fresh `Changing` → Off 才受限 reset/废纸 M30/PSRAM 从头重发，On 停止”路径。IDF 6.0.1 构建通过（`xiaozhi.bin=0x2ccde0`，app 余 `0x123220`/29%，SHA256 `5C79178B...92ABD`），**尚未烧录该 commit** | 先烧录/启动核验 `05f4563`；完整断连复位/废纸换纸/缓存重发实测；`Changing=On`/自动换纸；P2 S3/S4；App 语音出图；repeat 快路与其余工具逐项云端回归 |
| 写字机实机 | 🟡 阶段 1 流控 + S3 任务运动闭环已验证 | MK38 板（COM3）；WiFi STA `192.168.1.13:23`；`[ESP901]`/授权/A4 行程/原点与方向已验证；无限位开关，MCP 限幅为唯一安全防线；M3/M5 Z 宏已修复；S3 abort 明确 Z=0 抬笔和最终 fresh-`Idle` 已在真实运动中验证。一次恢复事故曾清空坐标，已按复位前已知 `(133.960,165.000)` 回物理原点并以 `[ESP444]RESTART` 重建、复核 `MPos=0,0,0`；后续回归最终 `MPos=(100,100,0)` | 纸面笔迹/出墨确认；有纸 `Changing=On` 与自动换纸；断连重放；App 语音出图 |
| 京东云生产环境 | ✅ HTTPS 已通 | MCP+bridge+nginx+**Cloudflare Tunnel**；llm7；`HUTUJI_PUBLIC_BASE_URL=https://hutuji.donglicao.com`；Let's Encrypt 证书（ESP-IDF cert bundle 信任）；preflight 8/8（2026-07-27）；安全审计 11 项全修已部署 | App 对话验收；阶段 6 |
| 生产/合规 | 🔴 未签核 | SOP + `docs/templates/` + `compliance.md` | BOM、标签、法规、开源通知、保修/售后负责人签核 |

## 当前最近一步

1. ~~**坐标原点验证**~~：✅ 已验证（2026-07-27）——左下角死点=(0,0)，X+右 Y+前，方向正确。
2. ~~**Telnet 逐行流控**~~：✅ PC 直连 Telnet 8/8（2026-07-27）；S3 Pipe 下载、逐行转发、
   `ok` 配对与 fresh-`Idle` 完成判定已实机回归（2026-07-28）。
3. ~~**M3/M5 笔控 Z 轴修复**~~：✅ 已修复+验证（2026-07-27）——GCode.cpp `mc_line` 后加 `protocol_buffer_synchronize()`，M3→Z=5mm/M5→Z=0mm 物理运动正常。待确认纸面有笔迹。
4. ~~**S3 云端工具与 Pipe 直连闭环**~~：✅ 官方消息 API 发现七工具；云端路径触发 `draw/status/abort`；实机未出现 `done/Run`，最终 `done/Idle`；abort 后 Z=0 且无续跑。最新烧录口为 **COM6**，打开 CH340K 监视口会复位 S3，非干扰日志使用 COM6。
5. ~~**固化 S3 证据基线**~~：✅ `05f4563` 已提交，IDF 6.0.1 最终构建通过；该 commit 尚未烧录，不能把构建通过当硬件证据。
6. **烧录当前 S3 基线**：烧录 `05f4563`，核对写入哈希、七工具注册、Telnet ready/authorized 与无 panic。
7. **断连复位重发实测**：验证普通画线断连后的 planner 清理、废纸换纸与 PSRAM 从头重发；换纸态仍必须人工分流。
8. **实测自动换纸**：有纸验证页尾显式 `M30` 成功、`error:90` 和换纸中 abort 三支。
9. **换纸 `Changing=On` 验证**：有纸时触发换纸，`[ESP901]` 返回 `Changing=On`。
10. **端到端语音出图**：App 语音 → 小智云 → MCP 生成 → S3 下载 → Pipe 转发 → Grbl 画。
11. 本仓交接：`python scripts/agent_gate.py --profile hub`。

## 状态更新规则

- ✅：命令、commit、设备编号、日志或记录表可复核。
- 🟡：设计/代码存在但缺生产环境、硬件证据或外部门禁 pass。
- 🔴：未实施、未验证或存在硬阻断。
- 状态提升必须在 `docs/templates/release-manifest.md` 或 `docs/templates/device-test-record.md` 有证据路径；本仓软件层可附 `results/hutuji_gate_last.md`。
- 失败记录不得删除；修复后追加复测结果和关联 commit。
- **禁止宣称表**见 `docs/agent-gate.md` §5（可出货 / §9 已完成 / 硬件已验证 等）。
