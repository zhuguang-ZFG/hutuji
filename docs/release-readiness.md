# 发布与出货就绪度

> 状态日期：2026-07-31（**审查修复批次**：P1-3 httpx→LlmError、P1-4 websockets 脱敏 filter、P1-5 nginx :80 /files 反代、P1-1 Grbl §9-C′ underflow 恢复置 Cycle、P1-2 S3 暂停超时补 soft reset；pytest 118 passed、hub+**fz standard** 均 pass。**第二轮审查修复**（三仓已分别提交并推送：Grbl `4ddc945` / fz `784fccf` / xiaozhi `6545ec9`）：§9-C′ 收窄为 `ProtocolDecisionCore::should_resume_segment_underflow()`（不再吞 `endMotion`/`executeHold`/`motionCancel`/`soft_limit`；fz 新增 7 组判定用例）、S3 reset 单 owner 事务（`abort_reset_started_` CAS + `AbortResetToken` 会话代号一次性令牌 + 窗口在途静默 + `SetTaskSessionActive(false)` 清 `ready_`）、`cloud_bridge` 脱敏补 `record.args` Mapping 分支、`protocol.md` §2.1.1/§9-C′ 契约同步；pytest **119 passed**、S3 host `unittest` **9 passed**、fz `standard` overall=pass；**Grbl 改动未烧录（COM3 `SetCommState` err=31，需物理重插 USB）、S3 已烧录并运行自证；第三轮传输安全加固 2026-07-31 21:20–21:22 **已部署生产并复验**（preflight **14/14 PASS**、公网面全绿）**）
>
> **构建面（2026-07-31 补取）**：Grbl PlatformIO `release` **SUCCESS**（`Protocol.cpp.o` 05:23:54 晚于源码 05:19:53，`firmware.bin` 1857024B，Flash 94.4%）；S3 **IDF v6.0.1 构建 pass**（`idf.py build` exit=0；`hutuji_pipe.cc.obj`/`hutuji_job.cc.obj` 均重编，即证新增 `hutuji_recovery_core.h` 参与编译；`xiaozhi.bin=0x2cec90`，app 分区剩 29%；与上次烧录基线镜像 `0x2ce890` 不同）。**S3 已于 07-31 烧录 COM6 并运行自证（回读 `ota_0` + 启动横幅 ELF SHA `d01c895d5…`，证据 `results/s3-06b962c-dirty-com6-flash-evidence-20260731.md`、逐台记录 `results/device-test-record-s3-lc-01-20260731.md`）；Grbl 仍未烧录（COM3 打开报 Windows error 31）** —— 构建通过不等于实机验证。本轮 nginx/preflight 加固**已于 21:20–21:22 部署生产并复验**（见下行）。
> **第三轮传输安全加固（当前真值）**：本仓 `deploy/preflight.sh`、nginx 模板与 EnvironmentFile 规则已于 **2026-07-31 21:20–21:22 部署生产并复验**；nginx 站点链接精确收敛、`:80 /files/` loopback-only ACL、14 项 preflight **全 PASS**、公网面全绿（HTTPS `/mcp` 404、直连 `:80 /files` 403、HTTPS `/files` 合法产物 200）。证据见 `docs/deployment.md` §第三轮传输安全加固。

## 结论

**当前不可商业出货。**  
① 基础文档与本仓门禁已落地；京东云 HTTPS 常驻，Grbl §9 已烧录并通过已列实机项。
S3 已打通官方云工具调用、直连逐行任务与安全 abort 回归；源码 HEAD `f1cf066`（`c1267ec` WiFi 三修
→ `ed5b132` 净作画时长+ETA → `15f73c9` 窗口化流控 S3 → `13057e8` 落笔旁路 + `ok` 兜底 → `f1cf066`
默认关闭的 §8.5 abort 取证脚手架）已提交/推送，
默认构建与 8.3 自动触发镜像均构建通过并烧录 COM6。**`13057e8` 固化了此前未提交的 3 个文件**
（`hutuji_job.{cc,h}`、`hutuji_pipe.cc`，+407/−83：`PreparePenOrigin` 旁路 `G92 Z0` + `ok` 超时状态
兜底），**2026-07-29 那次烧录 COM6 的镜像内容即该 commit**（已被 07-31 的 `0x2cec90` 覆盖）（2026-07-29 20:39 构建 `0x2ce890`，SHA256
`85317904...4C3E`，20:41 app-flash 通过 esptool `Hash of data verified`，证据
`results/s3-13057e8-com6-flash-evidence-20260729.md`）——**烧录已可宣称**；此前落笔只在手动 Telnet 序列下
纸面证实，2026-07-30 B-12 已补齐 S3 自动路径出墨证据。
**2026-07-29 有纸实测首次达成完整任务**：
画线 → 页尾 `M30` 自动换纸 `[PaperStatus] 0` → fresh `Idle` → `state=done`，全程无 `error:90`
（证据 `results/s3-c1267ec-paper-draw-done-evidence-20260729.md`；`15f73c9` 窗口化复测无回归见
`results/s3-ed5b132-windowed-flow-eta-paper-draw-evidence-20260729.md`）。8.3 普通画线断连（`3ca9d31`）
已实测到：重连后 `Changing=Off` → 受限 reset/恢复 → 废纸 `M30` `[PaperStatus] 0` → PSRAM 从第 1 行重画
→ 55/55 行完成并 fresh `Idle`。App 语音、断连路径在 `13057e8` 上的回归
与生产签核未闭合。

## 证据矩阵

| 层 | 当前状态 | 已有证据 | 出货前缺口 |
|----|----------|----------|------------|
| 架构/协议 | ✅ 已定案 | `README.md` 决策表；`protocol.md` v0.5 | 实测数字回填后评审 v0.6/或保留 v0.5 实测附录 |
| 基础文档地图 | ✅ 已齐 | `docs/README.md` + `agent-handoff.md` + `docs/templates/` + `deploy/` | 按阶段回填 🔬 与签核，不把文档当证据 |
| **本仓 Agent 门禁** | ✅ 已建立 | `agent-gate.md` + `scripts/agent_gate.py` + Cursor 硬轨 + `foundation-checklist.md` | 交接前重跑 `hub`；报告归档 `results/hutuji_gate_last.md` |
| **防幻觉 / 禁乱改资料** | ✅ 已建立 | `agent-anti-drift.md` + `agent-edit-allowlist.json` + `worktree-inventory.md`（门禁 docs 层校验） | 提交/丢弃脏树后更新 inventory；白名单外改动须用户点名 |
| **三仓 agent 约束闭环** | ✅ 已建立 | `agent-constraint-matrix.md`；Grbl/xiaozhi/fz 根 `AGENTS.md` 入口；门禁校验 Grbl AGENTS 存在 | 外仓 AGENTS 变更须保持指向 hutuji；工程完成仍看下方各层 |
| MCP 本地服务 | ✅ 自动化通过 | pytest；`e2e_local.py` PASS（经本仓门禁 `hub`） | 在发布 commit 上重跑并归档输出 |
| MCP 安全基础 | ✅ 历史基线+第三轮加固已部署复验 | 已有 CRC32、大小/格式/TTL/限流、loopback、脱敏、G-code 白名单、TOCTOU 与公网隔离历史证据；`b50a10a` 曾部署并通过当时检查；**2026-07-31 21:22 第三轮加固已上线**：nginx `:80 /files/` loopback-only ACL、EnvironmentFile 规则加固、preflight 14 项全 PASS、公网面全绿 | 发布后重跑对应回归 |
| 小智云桥接 | 🟡 工具发现+官方云调用通过 | WSS `tools/list`；本机 `tools/call hutuji_draw`→`/files`+CRC；官方小智消息 API 发现七个 `hutuji.*` 工具，并经云端路径实际触发 `draw/status/abort`（2026-07-28）；智能体配置与知识库召回已通过 | App 真实语音对话；七工具逐项云端行为回归；长期重连 |
| Grbl §9 加固 | ✅ 已烧录+实机验证；**C′ 未烧录** | Grbl `e7f373f`（§9 keepalive 改 setsockopt 修编译）；fz `standard` **overall=pass**；**实机验证（2026-07-27）**：COM3 烧录 OK → WiFi STA ChinaNet-jJmz 信号 100% IP=192.168.1.13 → Telnet:23 连通收 banner+`?`状态 → `[ESP901]` 返回 `Paper=No Changing=Off` → §9-A keepalive/§9-B ESP901/§9-C Changing 字段均通过。Grbl 当前源码 `8d13b5c`（2026-07-28）已提交/推送，固化 A4 默认行程、M3/M5 笔控同步与 segment underflow 收敛；本轮 fz `standard` **overall=pass**（duration 208.91s）+ PlatformIO `release` 构建成功；COM3 烧录成功（bootloader/partition/app 均 `Hash of data verified`）并完成空闲最小核验：授权保留、WiFi `192.168.1.13`、Telnet:23、`?` 回 `Idle`、`[ESP901]` 回 `Paper=No ... Changing=Off`、`$130=210 $131=297`。**2026-07-31 审查修复**：§9-C′ underflow 恢复先提交 `12f26ac`；**第二轮已提交并推送 `4ddc945`**，由 `ProtocolDecisionCore::should_resume_segment_underflow()` 收窄——仅「`Cycle` 中 + planner 仍有 block + 无 `endMotion`/`executeHold`/`motionCancel`/`soft_limit`」的真实断粮才置 `State::Cycle` 续跑，jog/探针取消与 soft limit 照原路进 `Idle`/`Alarm`（fz `standard` overall=pass + 7 组判定用例）；PlatformIO `release` **SUCCESS**（`Protocol.cpp.o` 05:23:54 晚于源码 05:19:53，`firmware.bin` 1857024B，Flash 94.4%）；**未烧录** | bringup 阶段 2 已有 B-13 证据；`8d13b5c` 尚未完成换纸中 abort/断连重画验收；C′ 需烧录+有纸复测 |
| S3 M1 | ✅ 已集成并上板 | `feat/hutuji-usb-pipe`；源码 HEAD `f1cf066` 已提交/推送（链：`3ca9d31`→`c1267ec`→`ed5b132`→`15f73c9`→`13057e8`→`f1cf066`；**实机烧录基线（07-31 更新）= HEAD `06b962c` + 当时未提交的第二轮审查修复（现为 `6545ec9`；未重新构建回读比对），镜像 `0x2cec90`，证据 `results/s3-06b962c-dirty-com6-flash-evidence-20260731.md`**）；`15f73c9` 的 8.3 自动触发镜像 `xiaozhi.bin=0x2cdcb0` 已烧录 COM6 并完成有纸 `state=done` 复测；**2026-07-29 那次烧录的镜像 `0x2ce890` 内容即 `13057e8`（已被 07-31 的 `0x2cec90` 覆盖）**（落笔旁路 + `ok` 兜底；2026-07-29 20:41 app-flash 成功，esptool `Hash of data verified`，证据 `results/s3-13057e8-com6-flash-evidence-20260729.md`，详见 `firmware/README.md`）；`firmware/m1-usb-pipe.md` | M2 缺口见下一行 |
| S3 M2 哑管道 | 🟡 关键直连闭环已实测，M2 整体未闭合 | `05f4563`：Telnet 连接优化、32 深应答队列与 PSRAM 零拷贝行索引；候选 IP 先验 Grbl banner；应用层静默探活约 21s；官方云路径触发 `draw/status/abort`；提交前候选的 **abort 实机回归**从 `streaming/Run,Z=5` 恢复到 `aborted/Idle,ready=true,authorized=true,Z=0`，8 秒无 XY 漂移/无 planner 续跑，绘图完成判定未见 `done/Run`；该行为基线新增“绘图 fresh `Idle` → S3 显式 `M30` → 最终应答 → fresh `Idle` → done”及“重连 banner → fresh `Changing` → Off 才受限 reset/废纸 M30/PSRAM 从头重发，On 停止”路径。`3ca9d31` 修复 Grbl 笔控 `M3/M5` 同步 planner 后才回 `ok` 导致的 60s 误超时；IDF 6.0.1 默认构建通过（`xiaozhi.bin=0x2cce30`，余 `0x1231d0`/29%，SHA256 `5D5FE23CE0DF6EE364EBACC2503E8374174781AEAE5E9BFC9BACDF227987BBA6`）；8.3 自动触发镜像构建通过并烧录 COM6（`xiaozhi.bin=0x2cd070`，余 `0x122f90`/29%，SHA256 `98C3B6AF0CB136AF79A0256B5FB8A5279FE0715FE087322419A791D3BD3C122C`）。8.3 复测报告 `results/bringup-8.3-fault-capture-20260729-010436.md`：S3 从 `192.168.1.12` 下载 658B/CRC32 `2290095f`，WiFi OFF/ON 注入后出现断连、重连、`Changing=Off`、废纸 `M30`、`[PaperStatus] 0`、PSRAM 从第 1 行重画，第二轮 55/55 行完成并 `Run -> Idle`；最终页尾 `M30` 因无纸返回 `[PaperStatus] 2` + `error:90`，任务落 `state=error`。2026-07-30 B-12：同一 `13057e8`/`0x2ce890` 自动路径从 `192.168.1.12` 下载 409 行/9782 字节/CRC32 `733095ec` 测试件，自动旁路 `G92 Z0` + `G1G90 Z5.0F10000` 落笔，8 条线全部完整连续；页尾 `M30` 后 `[PaperStatus] 0`、`state=done`、`replayable=1`；旁路 RTT 断粮压力模型被纸面实测证伪，`WiFi.setSleep(false)` 降级为卡顿优化。 B-13：用户装纸后，HTTP `/command?plain=M30` 最小触发换纸，并每约 250ms 轮询 `[ESP901]`；`M30` 后 0.14s–10.11s 捕获多次 `Changing=On`（先 `Paper=No`，后 `Paper=OK`，完成前再回 `Paper=No`），10.11s 起 `Changing=Off`；串口 `17:30:52.963–52.967` 收到 `[MSG:[PaperStatus] 0]`，证明 Telnet/S3 侧可见换纸成功状态。 | 换纸中 abort；P2 S3/S4；App 语音出图；repeat 快路与其余工具逐项云端回归 |
| 写字机实机 | 🟡 阶段 1 流控 + S3 任务运动闭环已验证 | MK38 板（COM3）；WiFi STA `192.168.1.13:23`；`[ESP901]`/授权/A4 行程/原点与方向已验证；无限位开关，MCP 限幅为唯一安全防线；M3/M5 Z 宏已修复；S3 abort 明确 Z=0 抬笔和最终 fresh-`Idle` 已在真实运动中验证。8.3 复测中普通画线断连后，废纸换纸 `[PaperStatus] 0`、PSRAM 重画 55/55 行和 fresh `Idle` 已在 S3-LC-01/MK38 上出现；最终 COM3 查询 `<Idle|MPos:20.000,140.000,0.000|...>`。一次恢复事故曾清空坐标，已按复位前已知 `(133.960,165.000)` 回物理原点并以 `[ESP444]RESTART` 重建、复核 `MPos=0,0,0`；后续回归最终 `MPos=(100,100,0)`。2026-07-29 同一测试文件中，`Z5.0F10000` + 画线 `F1500` 已纸面出墨且整条均匀，`Z5.0F300` + `F1500` 无墨；2026-07-30 B-12 用 S3 自动路径验证生产 `Z5.0F10000` + `F8000` 组合出墨，409 行测试件 8 条线全部完整连续，任务最终 `state=done` | 换纸中 abort；App 语音出图 |
| 京东云生产环境 | ✅ HTTPS 常驻+第三轮加固已部署 | 2026-07-31 完成 nginx 站点链接收敛，并取得旧配置下 `/files` 200、正文回读；**21:20–21:22 第三轮加固已上线**：nginx `:80 /files/` loopback-only ACL、preflight **14/14 PASS**、公网 HTTPS `/mcp` 404、直连 `:80 /files` 403、HTTPS `/files` 合法产物 200+完整性头，证据 `docs/deployment.md` §第三轮传输安全加固 | 发布时再跑一遍 14 项；长期监控 WSS 重连与工具响应延迟 |
| **京东云公网面** | ✅ 第三轮复验通过 | **2026-07-31 21:22** 复验通过：HTTPS `/mcp` GET/POST 均 404、直连 `:80 /files/*` 403、HTTPS `/files/__nonexistent__` 404；桥接重连后云端 `initialize`+`tools/list`+`ping` 已进日志，新脱敏 filter 生效 | 发布后公网明文 `:80` 与 HTTPS 合法产物端到端回归 |
| **P2 窗口化流控** | 🟡 S1–S3 已烧录，S4 只差证据 | `hutuji_job.cc:58` `kWindow=512` + 换纸行排空 `paper_pending`（首次落地 `13057e8`；07-31 起板上镜像 `0x2cec90` = HEAD `06b962c` + 当时未提交的第二轮审查修复（现为 `6545ec9`））；生产容差已回降 `LINESIMPLIFY_TOLERANCE_MM = 0.05`（`gcode_gen.py:38`）；B-12 409 行测试件全线连续 | 0.05mm **真实图样**有纸出图证据（曲线细节改善且无停顿）；自适应 `Bf` 反压未做（`hutuji_job.cc:1319-1320` TODO）。清单见 `docs/design/p2-windowed-flow-control.md` §5 |
| 生产/合规 | 🔴 未签核 | SOP + `docs/templates/` + `compliance.md` | BOM、标签、法规、开源通知、保修/售后负责人签核 |

## 当前最近一步

1. ~~**坐标原点验证**~~：✅ 已验证（2026-07-27）——左下角死点=(0,0)，X+右 Y+前，方向正确。
2. ~~**Telnet 逐行流控**~~：✅ PC 直连 Telnet 8/8（2026-07-27）；S3 Pipe 下载、逐行转发、
   `ok` 配对与 fresh-`Idle` 完成判定已实机回归（2026-07-28）。
3. ~~**M3/M5 笔控 Z 轴修复**~~：✅ 已修复+验证（2026-07-27）——GCode.cpp `mc_line` 后加 `protocol_buffer_synchronize()`，M3→Z=5mm/M5→Z=0mm 物理运动正常。2026-07-29 已取得直接 `G1 Z` 笔控纸面证据：`Z5.0F10000` + 画线 `F1500` 出墨且整条均匀；2026-07-30 B-12 已取得 S3 自动路径生产组合 `Z5.0F10000` + `F8000` 出墨证据。
4. ~~**S3 云端工具与 Pipe 直连闭环**~~：✅ 官方消息 API 发现七工具；云端路径触发 `draw/status/abort`；实机未出现 `done/Run`，最终 `done/Idle`；abort 后 Z=0 且无续跑。当前烧录口为 **COM6**，日志口为 **COM9**（CH340K）。
5. ~~**固化 S3 证据基线**~~：✅ `05f4563` 已提交，IDF 6.0.1 最终构建通过；`xiaozhi.bin` SHA256 `A77C8635405B7FB18B18CE2486621A7635374D75FEE37E0078CD1423F29F80CC`，`merged-binary.bin` SHA256 `9328F2B71CAC67603E9B9BF063980AE11D575A749CE7F124A4F13C0CFA690E1D`，`releases/v2.4.0_lichuang-dev.zip` SHA256 `F11E51853E0D6E7CD223344F748330CCB583E7CC913E02E43DCD4E08D52941C2`。
6. ~~**烧录当前 S3 基线**~~：✅ S3-LC-01 已烧录 `05f4563`（COM6）；esptool 对 bootloader/partition/OTA/assets/app 均报告 `Hash of data verified`；两轮 COM9 启动日志确认七工具注册、Telnet ready/authorized、持续 fresh `Idle`，无 panic/assert/backtrace。
6a. ~~**烧录当前 Grbl 基线**~~：✅ MK38 已烧录 `8d13b5c`（COM3）；esptool 对 bootloader/partition/app 均报告 `Hash of data verified`；串口核验授权保留、Telnet:23 启动、`[ESP901] Changing=Off`、`$130=210 $131=297`。
6b. ~~**补齐 S3 8.3 自动测试调试入口**~~：✅ xiaozhi `27eff73` 已提交/推送；过期硬编码 G-code URL 已移除，改为 `HUTUJI_AUTO_TEST_DRAW` + `HUTUJI_AUTO_TEST_DRAW_URL` CMake 选项注入。`3ca9d31` 已在该入口基础上修复 `M3/M5` 长等待，默认构建与 8.3 自动测试构建均通过，8.3 自动测试镜像已烧录 COM6。
7. **断连复位重发实测**：🟡 `3ca9d31` 普通画线断连主路径已实测到废纸 `M30`、`[PaperStatus] 0`、PSRAM 从第 1 行重画、55/55 行完成与 fresh `Idle`；最终页尾自动换纸因无纸 `error:90`（当时无纸）。证据：`results/bringup-8.3-fault-capture-20260729-010436.md`、`results/s3-3ca9d31-8.3-pen-timeout-evidence-20260729.md`。**仍缺**该路径在 `c1267ec` 上的回归。
8. **实测自动换纸**：✅ 2026-07-29 有纸实测页尾显式 `M30` 成功——`[PaperStatus] 0`、fresh `Idle`、`state=done`，全程无 `error:90`（`results/s3-c1267ec-paper-draw-done-evidence-20260729.md`）。2026-07-30 B-12 在 `13057e8`/`0x2ce890` 自动路径复测：页尾 `M30` 后 `[PaperStatus] 0`、`state=done`、`replayable=1`，且生产 `Z5.0F10000` + `F8000` 纸面出墨全连续。**仍缺**换纸中 abort；`Changing=On` 分流已于 2026-07-30 B-13 由 `[ESP901]` 轮询捕获。
8a. **换纸中 abort（bringup §8.5）取证手段**：🟡 手段就位、结论未取。查明设备侧 abort 唯一入口是云端 `hutuji.abort`（板上无按键/串口/HTTP 触发），而保护窗仅约 10s（B-13 实测 0.14s→10.11s），在 token 401 期间无法从云端稳定命中；B-15 因此在 xiaozhi fork 加**默认关闭**编译宏 `HUTUJI_AUTO_TEST_ABORT_ON_PAPER`（已提交/推送为 `f1cf066`，`13057e8..f1cf066`）（窗口开启即本地调 `RequestAbort()` 并打印前后 `status`），取证镜像 `xiaozhi.bin=0x2ceb10`（SHA256 `F7FF3525…4BF2`）已构建，默认对照构建 `0x2ce680` 经字符串搜证**不含**脚手架。剧本与四条判据见 `bringup-sop.md` §8.5，构建证据 `results/b15-8_5-abort-scaffold-build-evidence-20260730.md`。**未烧录、未有纸实测；取证后必须回刷不带宏镜像。**
9. ~~**换纸 `Changing=On` 验证**~~：✅ 2026-07-30 B-13 已验证——HTTP `[ESP901]` 轮询在 `M30` 后 0.14s–10.11s 多次返回 `Changing=On`，串口随后收到 `[MSG:[PaperStatus] 0]`。
10. **端到端语音出图**：App 语音 → 小智云 → MCP 生成 → S3 下载 → Pipe 转发 → Grbl 画。
    **当前阻塞**：仓外 token 文件里的控制台 JWT 服务端已失效——`GET https://xiaozhi.me/api/agents`
    带 `Authorization: Bearer …` 返回 401 `{"success":false,"message":"Invalid token"}`（JWT 自带
    `exp` 仍是 2026-10-02，即服务端已作废会话，不是过期）；不带/换头返回 `No token provided`。
    需用户在控制台重新 `localStorage.getItem('token')` 后注入 `XIAOZHI_TOKEN`，才能从 PC 侧
    走云端消息 API 触发 `hutuji.draw`。
11. 本仓交接：`python scripts/agent_gate.py --profile hub`。

## 状态更新规则

- ✅：命令、commit、设备编号、日志或记录表可复核。
- 🟡：设计/代码存在但缺生产环境、硬件证据或外部门禁 pass。
- 🔴：未实施、未验证或存在硬阻断。
- 状态提升必须在 `docs/templates/release-manifest.md` 或 `docs/templates/device-test-record.md` 有证据路径；本仓软件层可附 `results/hutuji_gate_last.md`。
- 失败记录不得删除；修复后追加复测结果和关联 commit。
- **禁止宣称表**见 `docs/agent-gate.md` §5（可出货 / §9 已完成 / 硬件已验证 等）。
