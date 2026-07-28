# 发布与出货就绪度

> 状态日期：2026-07-27（M3/M5 笔控 Z 轴修复验证 + 坐标原点验证 + Telnet 流控 8/8 + 串口画星）。本文是”现在到底做到哪”的唯一状态页；每次通过新门禁后同步更新。

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
| MCP 安全基础 | ✅ 代码/测试/VPS 部署均通过 | CRC32、512KiB、SVG 白名单（256KB 上限+64 层深度限制）、TTL、限流、localhost；stderr/status 脱敏；§5 M-code 黑名单；TOCTOU 竞态修复；孤文件清理；Content-Disposition attachment；**A4 画布限幅（X≤190 Y≤277mm，四周 10mm 边距）**；圆弧包围盒校验；G-code 注释剥离。安全审计 11 项 + code review 7 项全修（85 条测试，门禁 pass） | ✅ 已部署 VPS（2026-07-27）：preflight 8/8 |
| 小智云桥接 | 🟡 工具发现+本机 call 通过 | WSS `tools/list`；本机 `tools/call hutuji_draw`→`/files`+CRC；**智能体已配**：DeepSeek V4 + 角色提示(few-shot) + 知识库(召回OK) + 助手名=呼图机（2026-07-26） | App/控制台真实对话 `tools/call`；长期重连 |
| Grbl §9 加固 | ✅ 已烧录+实机验证 | Grbl `e7f373f`（§9 keepalive 改 setsockopt 修编译）；fz `standard` **overall=pass**；**实机验证（2026-07-27）**：COM3 烧录 OK → WiFi STA ChinaNet-jJmz 信号 100% IP=192.168.1.13 → Telnet:23 连通收 banner+`?`状态 → `[ESP901]` 返回 `Paper=No Changing=Off` → §9-A keepalive/§9-B ESP901/§9-C Changing 字段均通过 | bringup 阶段 2（换纸中 `Changing=On` 验证须有纸）；S3 Telnet 联调 |
| S3 M1 | 🟡 已在分支 | `feat/hutuji-usb-pipe`；`firmware/m1-usb-pipe.md` | 构建/烧录 |
| S3 M2 哑管道 | ✅ Telnet 联调通过 + 连接优化 + **断连检测/笔控加固** | xiaozhi `414bb04`（IDF 6.0.1）：Telnet 联调首次通过（2026-07-27）；**连接优化**：去掉 mDNS + NVS IP 缓存 + 非阻塞 connect → 冷启 ~10s；双端 TCP keepalive；**体验控制工具（2026-07-27）**：pause/resume/repeat/pen_test 已 code review 加固；**P2 S1+S2（2026-07-28）**：Pipe 应答从 EventGroup 单 bit 改 32 深队列（窗口化前置），Job 预解析成 PSRAM 零拷贝行索引；**断连盲区修复（2026-07-28）**：EAGAIN 分支加 `silent_polls` 计数器，连续 7 次（≈21s）探活无应答即判死重连，补 TCP keepalive 漏掉的应用层假死（对齐奎享 grbl/a.java:382-404）；**笔控加固（2026-07-28）**：pause/resume/abort 注入 `G1G90 Z0/5` 抬落笔（对齐奎享 f.java:63-68/74），长暂停不渗墨、中止笔离纸；七工具注册 + COM9 烧录启动自证通过 | 四工具云端语音调用未验；抬笔落笔物理动作未实测；窗口化 S3/S4 阶段；端到端语音出图 |
| 写字机实机 | 🟡 阶段 1 流控已验证 + **笔控已对齐奎享** | MK38 板；COM3 CH340；WiFi STA IP=192.168.1.13；Telnet:23 连通；`[ESP901]` OK；`[License] Authorized`；`$130=210 $131=297`（A4 行程）；`$1=25`（电机正常失能，奎享不依赖常使能）；无限位开关（MCP 端限幅兜底）；坐标原点已验证（左下死点=(0,0)，X+右 Y+前）；Z 轴方向已确认（Z=5 触纸、Z=0 抬起，带弹簧失能回位）；**M3/M5 宏实测失效** → 已改 `G1G90 Z5.0/Z0.0` 直 Z 运动；**hutuji_gwrite.toml 已逐字对齐奎享真实产物**（9 份 cache G-code 证据）：F8000 画线/F10000 抬落笔、开头两次 `G1G90 Z0.0` 机械自校准、无 `G92`、无 M 码、收尾禁 `G0 X0Y0`（换纸触发）；**linesimplify 容差 0.2→0.1mm**（279 行，串口 0 阻塞），`linemerge` 0.05→0.3 对齐奎享 `nearDst`；**$1 恢复默认 25ms**（奎享不设 $1，弹簧回位即抬笔方向） | 抬笔物理出墨确认；窗口化 S3/S4；端到端语音出图 |
| 京东云生产环境 | ✅ HTTPS 已通 | MCP+bridge+nginx+**Cloudflare Tunnel**；llm7；`HUTUJI_PUBLIC_BASE_URL=https://hutuji.donglicao.com`；Let's Encrypt 证书（ESP-IDF cert bundle 信任）；preflight 8/8（2026-07-27）；安全审计 11 项全修已部署 | App 对话验收；阶段 6 |
| 生产/合规 | 🔴 未签核 | SOP + `docs/templates/` + `compliance.md` | BOM、标签、法规、开源通知、保修/售后负责人签核 |

## 当前最近一步

1. ~~**坐标原点验证**~~：✅ 已验证（2026-07-27）——左下角死点=(0,0)，X+右 Y+前，方向正确。
2. ~~**Telnet 逐行流控**~~：✅ PC 直连 Telnet 8/8 ok（2026-07-27）。S3 Pipe 端到端待验。
3. ~~**M3/M5 笔控 Z 轴修复**~~：✅ 已修复+验证（2026-07-27）——GCode.cpp `mc_line` 后加 `protocol_buffer_synchronize()`，M3→Z=5mm/M5→Z=0mm 物理运动正常。待确认纸面有笔迹。
4. ~~**S3 新增体验工具烧录 + 注册验证**~~：✅ 已烧 **COM8**（S3 的 COM 口曾由 COM7 变为 COM8，每次须重查），串口日志自证七个 `hutuji.*` 全部注册；底层动作经 COM3 对 Grbl 实测通过（`pen_test` 的 M5→M3→M5 全 ok 且 Z=0→5→0；`!` 进入 `Hold:0` 坐标冻结、`~` 续跑不丢行程）。**仍缺**：经小智云真实调用这四个工具、`repeat` 的 PSRAM 快路、暂停 10 分钟自动取消。
5. **S3 Pipe 端到端**：S3 下载 G-code → Telnet 转发 → ok 计数。
6. **换纸 `Changing=On` 验证**：有纸时触发换纸，`[ESP901]` 返回 `Changing=On`。
7. **端到端语音出图**：App 语音 → 小智云 → MCP 生成 → S3 下载 → Pipe 转发 → Grbl 画。
8. 本仓交接：`python scripts/agent_gate.py --profile hub`。

## 状态更新规则

- ✅：命令、commit、设备编号、日志或记录表可复核。
- 🟡：设计/代码存在但缺生产环境、硬件证据或外部门禁 pass。
- 🔴：未实施、未验证或存在硬阻断。
- 状态提升必须在 `docs/templates/release-manifest.md` 或 `docs/templates/device-test-record.md` 有证据路径；本仓软件层可附 `results/hutuji_gate_last.md`。
- 失败记录不得删除；修复后追加复测结果和关联 commit。
- **禁止宣称表**见 `docs/agent-gate.md` §5（可出货 / §9 已完成 / 硬件已验证 等）。
