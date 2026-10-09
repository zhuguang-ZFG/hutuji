# 30 — 固件双仓评审（2026-08-31）：xiaozhi `bf0b295^..0d041c9`（27 提交）+ Grbl `e3b1cad^..40616de`（6 提交）

评审方式：两个评审子代理各自在 30 分钟运行上限被杀（未 yield），收割其 transcript 分析块（`tmp/review_harvest_{grbl,xiaozhi}review_0831.md`，不入库）后派两个收尾代理复核出报告；**全部高严重度论断由主代理亲自打开 file:line 复核裁决**，与代理报告不一致处以本节裁决为准。评审为只读；本文只登记，不含任何固件改动。

## 裁决后的 Findings

| # | 仓 | 严重度 | 发现 | 证据（主代理复核） | 处置建议 |
|---|----|--------|------|--------------------|----------|
| R1 | Grbl | **P2 真实缺陷（2026-08-31 已修复并已刷 COM13，Grbl `8f3e7d6`）** | **§9-G′ 写保护存在长名绕过面**：`ProcessSettings.cpp:515-530` 长名循环以 `getName()`（如 `X/MaxTravel`）匹配后调 `is_hutuji_locked_setting_key(key)`，而修复前锁定清单只有数字名 + `Errors/Verbose`——`$X/MaxTravel=200` 可绕过守卫改写 `$130`。数字名路径（`:532-547`，key=`130`）拦截正常 | 主代理亲读 `ProcessSettings.cpp:492-562` 与谓词（修复后 `ProtocolDecisionCore.h:389-406`）确认 | **已修复并上板**：`kLockedKeys` 补 12 个长名（`ProtocolDecisionCore.h:393-399`），`strcasecmp`/`strings.h` 换本地 ASCII 折叠（`:367-387`，附带修 40616de 引入的宿主 clang 编译断）；g++ 行为测试 40 断言全过，fz standard overall=pass；COM13 HIL：`$X/MaxTravel=200`→`error:5`、`$130=200`→`error:5`、长名读正常、`$102` 同值写 `ok`、`$130` 复核 210.000 未被绕过（证据 `results/local-only/2026-08-31/grbl-g-prime-longname-guard-com13.log`） |
| R2 | Grbl | P3 治理台账（2026-08-31 已补录） | 机读白名单 `docs/agent-edit-allowlist.json` 的 `grbl_section9_only` 未含三个已授权触面：`GCode.cpp`（§9-F′ 行 protocol.md:611 契约点名 `:1732/:1789`）、`WebUI/WifiConfig.cpp`（0e1b7ed，commit 内记「2026-08-24 用户点名批准」+ `docs/design/s3-stream-pacing-investigation.md` §3）、`Machines/custom_3axis_hr4988.h`（7f48217，release-readiness.md:271 记「用户原话要求进固件」+ fz pass + COM13 已刷） | 主代理查 allowlist JSON 全文 + 三处授权记录 | **已补录**：`GCode.cpp` 并入 `grbl_section9_only`，另两项入新键 `grbl_user_authorized_extras`，`agent_gate.py` 精确契约同步钉死（缺失/篡改判红）；AGENTS.md 硬轨枚举同步 |
| R4 | Grbl | P3（2026-08-31 已修注释） | 0e1b7ed `esp_wifi_set_ps(WIFI_PS_NONE)` 实际在 `WiFi.begin()` 返回后、`ConnectSTA2AP()` 前调用（`WifiConfig.cpp:308-317`），commit message 称「STA 连上后」有精度偏差；异步先连不保证关联完成 | 主代理亲读 `WifiConfig.cpp:308-320` 确认调用点与授权注释 | **已修**：功能本就正确（esp_wifi_set_ps 可关联前调用），`WifiConfig.cpp:314-316` 补调用点澄清注释，明示 commit message 措辞偏差 |
| R6 | xiaozhi | P3 隐私面（2026-08-31 已文档明示） | 对话上报（0d041c9）：`role+content+session_id+MAC` 经 HTTPS POST 到 `/draw-upload/api/device/conversation/report`；`kMaxContent=500` 截断、环形队列 cap 8 丢最旧、空闲冲刷、失败即丢（`hutuji_conversation_report.cc:28-38,216-238`） | 主代理亲读固件文件 + `draw-portal/draw_portal/app.py:2723-2767` 服务端端点核实 | **已明示**：新增 protocol.md §10.5——内容=整段会话文本、受理=绑定 MAC 白名单（未绑定 403）、留存默认 90 天访问时清理、运营面可见 |
| R7 | xiaozhi | P3 | 指纹不符时管道仍 `ready_=true` 仅 `settings_line_ok_=false`（`hutuji_pipe.cc:1074-1079`：置 ready + LOGE + NotifyCloud「参数被改动」）——设计上 fail-closed（所有运动入口判 `IsSettingsVerified()`），仅状态语义需读者留意 | 主代理亲读 pipe 块 + grep 证实 job 判点 376/870/1014/1667/2402 均拦 | 无改动 |

## 裁决推翻/修正的代理论断

- Grbl 代理把白名单未同步判 **P1**——主代理降 P3：三个触面全部有授权记录（commit 内/契约行/readiness），且该机读文件当前不对 Grbl 提交机器执行，无实际违规。
- Grbl 代理 O4「6 提交均无 fz 宣称」不准确：`7f48217` 有 fz standard pass（见上表 R2 的 readiness `$1=255` 条）；`2cecd2d`/`40616de` 各有 COM13/COM14 刷机+实机证据（见上表 R5 指纹条与 readiness:57）；`fb2b3b5` 有 08-23 COM13 烧录启动自证（worktree-inventory.md:1024）。
- xiaozhi 代理 F1（abort 兜底移交 reset）判 P2——主代理降 P3：两条兜底路径经验证存在且行为正确，属设计确认非缺陷。

## 复核通过的重点面（无缺陷结论，附证据强度）

- **abort/断流归位状态机（fe23b2c…cb7156c 五提交）**：三条路径闭环（纯流式 drain-home / Hold 态 3s 兜底移交 reset / 流错误停止）；`paused_` 冻结闸在任何 quiescence 等待前清除（测试 `test_abort_homes_with_hold_snapshot` 源码序钉死）；G92 快照先于 `SendAbortReset` 且 `HomeAfterAbort` 不重读 MPos。**未发现悬挂路径**。
- **指纹门禁 fail-closed**：draw/confirm/repeat/manual/pen_test/Run 内重检/断连恢复七处入口全判 `IsSettingsVerified()`；golden 13 项与 protocol §6.1 逐项一致（主代理亲比：评审时 Grbl `kLockedKeys` 13 键 = S3 `kGrblSettingGoldens` 13 项 = §6.1 表；R1 修复后 `kLockedKeys` 扩为 25 键 = 13 数字名 + 12 长名别名，锁定的**逻辑设置集合不变**，S3 指纹侧 13 项不动）。
- **§9-E′ CAS**：`std::atomic<bool>` + `compare_exchange_strong` test-and-set，四调用者（[ESP910]/M30/M721/BT）全收口，败者回 `MessageFailed(90)` 不碰 GPIO——与 protocol.md:612 描述一致（2026-09-24 按 E′ 原子认领行复核；普通有换纸分支的历史契约，不适用于量产无换纸分支）。
- **AdoptPrefetch（67507c6）**：旧 PSRAM buffer 先释放再接管，测试 `test_prefetch_adopt_frees_old_psram_buffer` 钉死。
- **host 测试**：行为型（编译运行真 C++ 断言）+ 源码序钉型互补；抽查未发现钉在注释/死代码上的假断言。

## 未覆盖面（明示）

- xiaozhi：`draw_bind.cc` 轮询时序逐行走查；LVGL/UI 视觉断言；HUTUJI_AUTO_TEST_* 脚手架；PSRAM/TLS 内存低谷实测。
- Grbl：E′ 物理并发触发（protocol §9 自注「未实机复现」）；keepalive 回收常数实测。
- 跨仓：S3 指纹门禁与 Grbl 写保护在**同一会话内**的绕过→检测时延（R1 缓解路径）未实机测。

## 流程记录

- 两轮评审代理均撞 30 分钟运行上限（`task.maxRuntimeMs=1800000`）被杀；OMP watchdog 本会话对活跃 agent 持续误报 stale（已报 xd://report_issue）；收割-收尾两段式为应对措施，效果验证可行（收尾代理 3m14s/17m46s 完成）。

## 修复批记录（2026-08-31 下午，R1/R2 两项用户批准后执行）

- **R1 修复**：Grbl `8f3e7d6`（`fix/panel-hold-after-align-116687e`）。`kLockedKeys` 由 13 键扩至 25 键（补 12 长名，行号见上表）；`strcasecmp`/`strings.h` 换为本地 ASCII 折叠 `hutuji_key_equals_ignore_case()`——附带修复 `40616de` 引入的宿主编译断（Windows clang/MSVC 目标无 `<strings.h>`，fz protocol_scenarios/native_model/native_units 三层曾全断，2026-08-31 复现于 R1 验证期）。验证：g++ 16.1 行为测试 40 断言全过（长名/数字名/大小写/前后缀/空键）；clang++ 22.1.8 `-fsyntax-only` 过；**fz standard overall=pass**（PATH 前置 LLVM 22.1.8，exit 0；唯一 fail 层 `wokwi_startup` 属 `_soft_fail_ids` 不阻断 overall——云仿真 30s 超时，用的还是修复前 pio 旧镜像）。**当日已刷 COM13**（pio release，固件 1858432B，SHA-256 `270d8c84…311b60`，Hash of data verified）并经串口 HIL 验证长名/数字名双拒、读与非锁定写不受影响（证据 `results/local-only/2026-08-31/grbl-g-prime-longname-guard-com13.log`）。
- **R2 补录**：hub 侧提交见 git log。`grbl_section9_only` 并入 `GCode.cpp`；新增 `grbl_user_authorized_extras`（`WebUI/WifiConfig.cpp` + `Machines/custom_3axis_hr4988.h`）；`agent_gate.py` 精确契约（必含键 + 精确等值列表）同步，`test_contract.py:7720` 的 `test_gate_rejects_weakened_edit_allowlist` 钉住弱化判红（2026-09-14 按实际签名重定位；旧引用已漂到空白行，不沿旧指纹盲接）。AGENTS.md 硬轨枚举补齐 §9-F′/§9-G′ 归属。
- **遗留**：R4/R5/R6 已于 2026-08-31 下午全部处置（用户「全部批准」）；R7 裁决即「无改动」。findings #30 全部条目闭环。
