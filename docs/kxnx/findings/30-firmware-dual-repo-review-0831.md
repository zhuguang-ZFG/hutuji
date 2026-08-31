# 30 — 固件双仓评审（2026-08-31）：xiaozhi `bf0b295^..0d041c9`（27 提交）+ Grbl `e3b1cad^..40616de`（6 提交）

评审方式：两个评审子代理各自在 30 分钟运行上限被杀（未 yield），收割其 transcript 分析块（`tmp/review_harvest_{grbl,xiaozhi}review_0831.md`，不入库）后派两个收尾代理复核出报告；**全部高严重度论断由主代理亲自打开 file:line 复核裁决**，与代理报告不一致处以本节裁决为准。评审为只读；本文只登记，不含任何固件改动。

## 裁决后的 Findings

| # | 仓 | 严重度 | 发现 | 证据（主代理复核） | 处置建议 |
|---|----|--------|------|--------------------|----------|
| R1 | Grbl | **P2 真实缺陷** | **§9-G′ 写保护存在长名绕过面**：`ProcessSettings.cpp:513-528` 长名循环以 `getName()`（如 `X/MaxTravel`）匹配后调 `is_hutuji_locked_setting_key(key)`，而锁定清单（`ProtocolDecisionCore.h:364-367`）只有数字名 + `Errors/Verbose`——`$X/MaxTravel=200` 可绕过守卫改写 `$130`。数字名路径（`:532-547`，key=`130`）拦截正常 | 主代理亲读 `ProcessSettings.cpp:490-560` 与 `ProtocolDecisionCore.h:359-374` 确认 | 扩展 `kLockedKeys` 补长名（`Stepper/IdleLockTime`、`X|Y/StepsPerMm`、`X|Y/MaxRate`、`X|Y|Z/MaxTravel`、`Direction/Invert` 等）或改按 `Setting*` 对象判定。缓解：S3 指纹门禁重连时 fail-closed 拒画，会话内静默。属 §9-G′ 后续补丁，须走 fz 门禁 |
| R2 | Grbl | P3 治理台账 | 机读白名单 `docs/agent-edit-allowlist.json` 的 `grbl_section9_only` 未含三个已授权触面：`GCode.cpp`（§9-F′ 行 protocol.md:514 契约点名 `:1732/:1789`）、`WebUI/WifiConfig.cpp`（0e1b7ed，commit 内记「2026-08-24 用户点名批准」+ `docs/design/s3-stream-pacing-investigation.md` §3）、`Machines/custom_3axis_hr4988.h`（7f48217，release-readiness.md:53 记「用户原话要求进固件」+ fz pass + COM13 已刷） | 主代理查 allowlist JSON 全文 + 三处授权记录 | 把三个文件补入白名单（附授权依据注释）。注：门禁目前只校验该 JSON 存在与关键键，**不**对 Grbl 提交机器执行白名单，故无静默违规，属台账同步 |
| R4 | Grbl | P3 | 0e1b7ed `esp_wifi_set_ps(WIFI_PS_NONE)` 实际在 `WiFi.begin()` 返回后、`ConnectSTA2AP()` 前调用（`WifiConfig.cpp:306-314`），commit message 称「STA 连上后」有精度偏差；异步先连不保证关联完成 | 主代理亲读 `WifiConfig.cpp:304-316` 确认调用点与授权注释 | 功能正确（esp_wifi_set_ps 可关联前调用）；仅注释措辞问题 |
| R5 | xiaozhi | P3 | 指纹 golden 末项查询用 `[Errors/Verbose]` 括号语法（`hutuji_recovery_core.h:889`），解析器要求 `$` 前缀应答（`ParseGrblSettingLine` `:896`）；表面矛盾，但 COM14 实机抓包「指纹通过 13 项」（release-readiness.md:18）证明应答即 `$Errors/Verbose=Off` 形态且 `Off→0` 映射正确 | 主代理亲读 recovery_core 876-934 + readiness 证据 | 补一句注释说明「查询括号语法、应答 `$…=Off/On`」消除自读矛盾 |
| R6 | xiaozhi | P3 隐私面 | 对话上报（0d041c9）：`role+content+session_id+MAC` 经 HTTPS POST 到 `/draw-upload/api/device/conversation/report`；`kMaxContent=500` 截断、环形队列 cap 8 丢最旧、空闲冲刷、失败即丢（`hutuji_conversation_report.cc:28-38,216-238`） | 主代理亲读该文件确认常量与行为 | 有意功能（运营面）；建议在文档明示上报内容=整段会话文本、服务端按绑定 MAC 白名单接受，并确认留存/删除策略 |
| R7 | xiaozhi | P3 | 指纹不符时管道仍 `ready_=true` 仅 `settings_line_ok_=false`（`hutuji_pipe.cc:1027-1032`：置 ready + LOGE + NotifyCloud「参数被改动」）——设计上 fail-closed（所有运动入口判 `IsSettingsVerified()`），仅状态语义需读者留意 | 主代理亲读 pipe 块 + grep 证实 job 判点 376/870/1014/1667/2402 均拦 | 无改动 |

## 裁决推翻/修正的代理论断

- Grbl 代理把白名单未同步判 **P1**——主代理降 P3：三个触面全部有授权记录（commit 内/契约行/readiness），且该机读文件当前不对 Grbl 提交机器执行，无实际违规。
- Grbl 代理 O4「6 提交均无 fz 宣称」不准确：`7f48217` 有 fz standard pass（release-readiness.md:53）；`2cecd2d`/`40616de` 各有 COM13/COM14 刷机+实机证据（readiness.md:36/18）；`fb2b3b5` 有 08-23 COM13 烧录启动自证（inventory:233）。
- xiaozhi 代理 F1（abort 兜底移交 reset）判 P2——主代理降 P3：两条兜底路径经验证存在且行为正确，属设计确认非缺陷。

## 复核通过的重点面（无缺陷结论，附证据强度）

- **abort/断流归位状态机（fe23b2c…cb7156c 五提交）**：三条路径闭环（纯流式 drain-home / Hold 态 3s 兜底移交 reset / 流错误停止）；`paused_` 冻结闸在任何 quiescence 等待前清除（测试 `test_abort_homes_with_hold_snapshot` 源码序钉死）；G92 快照先于 `SendAbortReset` 且 `HomeAfterAbort` 不重读 MPos。**未发现悬挂路径**。
- **指纹门禁 fail-closed**：draw/confirm/repeat/manual/pen_test/Run 内重检/断连恢复七处入口全判 `IsSettingsVerified()`；golden 13 项与 protocol §6.1 逐项一致（主代理亲比 Grbl `kLockedKeys` 13 键 = S3 `kGrblSettingGoldens` 13 项 = §6.1 表）。
- **§9-E′ CAS**：`std::atomic<bool>` + `compare_exchange_strong` test-and-set，四调用者（[ESP910]/M30/M721/BT）全收口，败者回 `MessageFailed(90)` 不碰 GPIO——与 protocol.md:515 描述一致。
- **AdoptPrefetch（67507c6）**：旧 PSRAM buffer 先释放再接管，测试 `test_prefetch_adopt_frees_old_psram_buffer` 钉死。
- **host 测试**：行为型（编译运行真 C++ 断言）+ 源码序钉型互补；抽查未发现钉在注释/死代码上的假断言。

## 未覆盖面（明示）

- xiaozhi：`draw_bind.cc` 轮询时序逐行走查；LVGL/UI 视觉断言；HUTUJI_AUTO_TEST_* 脚手架；PSRAM/TLS 内存低谷实测。
- Grbl：E′ 物理并发触发（protocol §9 自注「未实机复现」）；keepalive 回收常数实测。
- 跨仓：S3 指纹门禁与 Grbl 写保护在**同一会话内**的绕过→检测时延（R1 缓解路径）未实机测。

## 流程记录

- 两轮评审代理均撞 30 分钟运行上限（`task.maxRuntimeMs=1800000`）被杀；OMP watchdog 本会话对活跃 agent 持续误报 stale（已报 xd://report_issue）；收割-收尾两段式为应对措施，效果验证可行（收尾代理 3m14s/17m46s 完成）。
