# 发布与出货就绪度

> **2026-08-20 唤醒词改「小派」+ 售后知识库上云（xiaozhi `616fd74`+`46a6cb4` 已推已刷，KB 71153 已替换并召回验证）**：唤醒词从「你好小智」(wn9_nihaoxiaozhi) 改为自定义「小派」：`CONFIG_USE_CUSTOM_WAKE_WORD` + 拼音 `xiao pai` + `CONFIG_SR_MN_CN_MULTINET7_QUANT`（mn7_cn 2.6MB + fst 打进 srmodels.bin，assets 4.05MB/8MB 分区）；板上启动实测 MultiNet7 加载、`1 active speech commands: Command 1: xiao pai`、AFE detector=MultiNet、0 error、minimal sram 24,379B（模型数据在 PSRAM，SRAM 无可见回退）；阈值默认 20，收音灵敏度待现场复测。配套修正（`46a6cb4`）：host 契约测试改写为新事实（USE_CUSTOM_WAKE_WORD/xiao pai/小派/mn7 钉死，仍禁「你好小派」伪称——multinet 命令只有两音节），zh-CN STANDBY 改「说\"小派\"开始画画」、en-US 同步，host 96/96（改写 1 项、总数不变），app 镜像 `508035db` 已刷 COM14。售后 KB：微信版 `售后知识点.xlsx`（44 条 Q&A，61 张图）逐图转写后按内容归位为 38 条问答，并入 `docs/xiaozhi-agent/knowledge-base.md`（308→559 行），`update-kb 71153` 替换成功（旧文档 id=67723 已删），召回实测「字体大小最大就是120吗」0.831、「怎么用蓝牙连接写字机」0.802 均列首位。**缺口（原文件图片缺失，未收录，勿编造）**：序号3 调行间隔、序号20 内容不显示/字体很小、序号39 字体录入上传电脑、序号44 国产系统找不到图标——需原始手册补图后补录。

> **2026-08-20 MCP 取消重试去重修复（生产验证，提交 `7eac656`）**：根因是完整 draw 与 `DrawLimiter` 名额原属 JSON-RPC waiter；小智云取消后名额提前释放，约 35 秒重试会另起付费生图。现由 `_draw_inflight` 独立任务持有完整事务，`asyncio.shield()` 只分离 waiter；`_bitmap_inflight` 同步扩到 PNG→描摹→缓存。LF blob `bitmap_svg.py` / `server.py` / `requirements.txt` 的生产 SHA-256 为 `765cc091…1fa` / `ddf2b5c0…f26` / `4200aed6…deb`，0 个 CR；备份 `/root/hutuji-bak-20260820-draw-retry-7eac656/`。双服务 04:24:32 CST 重启为 MainPID `2966745/2966746`，两条 ExecStartPre `status=0`，8300 监听者身份相符，preflight **26/26 PASS**。冷题生产探针：前两次各 2 秒后标准 MCP cancel，均返回 `Request cancelled`；第三次接管成功 8.504 秒，总计 13.5 秒，1035 行/24,707B/19 次抬笔。journald 同窗为 3 次 `CallToolRequest`、2 次取消、**仅 1 轮**生图对冲（Agnes POST 一次并胜出），证明重试未重复付费生成。单轮内既定多后端对冲不变。仓内 hub：863 passed + e2e `PASS`。无固件/硬件面变化。

> **2026-08-20 Waveshare 手动页 taskLVGL 爆栈定位与修复（源码待提交，已刷 `152a8cbf`，HIL 已过）**：用户报「点动按钮一点就重启」，实测崩溃点不在点动而在**切到手动页那一下**。串口取证（`results/local-only/2026-08-20/waveshare-935dfbcf-jogcrash4-20260820.raw.log:366-383`）：`machine controls opened` → `machine manual section expanded` → `***ERROR*** A stack overflow in task taskLVGL has been detected.` → `rst:0xc (RTC_SW_CPU_RST)`；`xtensa-esp32s3-elf-addr2line` 对 `build/xiaozhi.elf`（SHA-256 `61f8ae22e…`，与日志 `ELF file SHA256: 61f8ae22e` 一致）解出 `panic_abort` ← `esp_system_abort` ← `vApplicationStackOverflowHook`（`port.c:573`）← `vTaskSwitchContext`，即 FreeRTOS 在切上下文时查出 taskLVGL 越界，故 backtrace 无应用帧（`|<-CORRUPTED`）。成因：分页改造（round 8）让手动页比主页深两级、四级 `LV_SIZE_CONTENT` 叠在 flex 里（panel→section→body→col→row→button→label），展开首帧的布局+绘制递归压穿 `esp_lvgl_port` 默认 7168B 栈；主页只两级，故历轮从未崩，round 5/7 点动能过也因当时布局浅两级。修法：`SpiLcdDisplay` 显式 `port_cfg.task_stack = 12288`，并在切页日志带 `uxTaskGetStackHighWaterMark` 作长期证据（贴底即可见，不再静默复活）。不消嵌套层：那会推翻已过 HIL 的分页两列布局。host 94/94（新增断言并入既有 drawer 契约测试，未新增测试项），ESP-IDF v6.0.1 构建 `build/xiaozhi.bin` 0x2d6870B、SHA-256 `152a8cbf…` 已写入 COM14 并通过哈希校验；随后 75s 无 error/爆栈/复位，最低 SRAM 25,143B（抬栈 5KB 的代价约 2.3KB）。**HIL 已过**（`results/local-only/2026-08-20/waveshare-152a8cbf-hil-stackfix-20260820.raw.log`，约 27 分钟窗口含约 3 分钟真实交互）：手动页展开→渲染存活（首次展开 HWM 6,060B，折叠后 4,844B 稳定，即旧 7,168B 栈必然压穿、新 12,288B 余量约一半），折叠→再展开存活；点动 X/Y 3 次触发、4 次 `state=manual`，全程 0 error、0 爆栈、0 复位；末态最低 SRAM 23,587B、板持续报 `Idle`。点动链路（`kJogFreshStateTimeoutMs` 6s 覆盖、越界 fail-closed）与栈修复均已实测。


> **2026-08-20 点动/灌流期 WiFi 省电持有（提交 `2bc04a6`，已刷 `40778e1c`，HIL 已过）**：症状根因：音频通道关闭后 `application.cc:535` 把 WiFi 省电踩回 `LOW_POWER(MAX_MODEM, listen_interval=10)`，叠加 block-ack 拆链（`RX DELBA reason:39`）时 Telnet 往返从 20~70ms 退化到 1440ms（`?`）/3200ms（`ok`），点动新鲜坐标与流内 ok 兜底假超时。对策（落点 `waveshare/esp32-s3-touch-lcd-3.5`，非 lichuang-dev；application.cc 零改动）：共享头新增 `hutuji::JobHoldsPerformance`（9 个 active 态，与显示层 active 谓词同源，manual 不在其中）与 `PerformanceReassertPeriodMs`（min(点动新鲜预算,4000)=4s）；`Job::SetState` 进 active 开持有、出 active 按 app 设备态（listening/speaking）回落；持有期 esp_timer 每 4s 幂等重申 `PERFORMANCE(WIFI_PS_NONE)`（对抗 `OnAudioChannelClosed` 单向覆盖），进持有立即重申一次；点动新鲜坐标窗口（`QueryAndWaitFreshMachineState`→判界→`$J`→等 Idle）短时持有，背光被拉亮属可接受代价。host 96/96（新增决策核编译跑测 + 接线钉死），ESP-IDF v6.0.1 构建 `build/xiaozhi.bin` 2,976,352B、SHA-256 `40778e1c…` 已写入 COM14 并通过哈希校验。**HIL 已过**（`results/local-only/2026-08-20/waveshare-40778e1c-perfhold-20260820.raw.log`）：连点 X/Y 13 次全触发（10×X、3×Y）、15 次 `state=manual`，**0 次**「点动前未取到新鲜坐标」假失败；日志逐条对上 `action=jog_x+`(42753) → `PERFORMANCE (NONE)`(43053) → `LOW_POWER (MAX_MODEM)`(43333)，即 PERFORMANCE 完整包住每个点动窗口、结束即回落；0 爆栈/0 复位，最低 SRAM 23,975B。唯一 E 是开机 MQTT 会话未就绪（T≈9.5s）的既有 notify 失败噪音，与本次改动无关。

> **2026-08-20 取证工具自身污染修正**：`serial_capture.py` 旧版用 `serial.Serial(...)` 直接 open，pyserial 在 open/close 置 DTR/RTS，而 ESP32-S3 原生 USB Serial/JTAG 把它当复位线——抓取起止各复位一次板子。实测 capture3 末行墙钟 15:45:40.050 与 capture4 首行 uptime 反推开机时刻 15:45:40.438 相差 **0.388s**，即此前判定的「抓取间隙自发重启」是工具假象。已改为先建对象、清 `dtr`/`rts` 再 `open()`。此前基于「uptime 归零」推出的重启结论一律以此为准重读。

> **2026-08-20 固件点动包线交叉钉（仓内 R17-XREPO-01，864 项全绿）**：`test_contract.py` 新增对外仓 `hutuji_recovery_core.h` 的 `kJogEnvelopeMax{X,Y}Mm` / `kJogStepMm` 字面量交叉钉，与 `gcode_gen.MAX_X_MM/MAX_Y_MM` 互为事实源——只改任一侧限幅即红（RED 实测：外仓副本改 190→191 触发 AssertionError；外仓缺失按门禁外仓层同口径跳过，hub profile 在本机必跑到）。纯测试加固，无生产/固件行为变化。

> **2026-08-20 Waveshare 手动区交互加固与点动安全防线（源码 `abc50d8` 已推，未刷机）**：「高级调试」更名「点动·手动」，展开态跨抽屉开合保持（用户决策；2026-08-18 开机默认折叠的「不做主界面噪音」不变）。抽屉面板内全部按钮（含 reset/set_origin 高危键）从 `PRESSED` 改判 `CLICKED`：从按钮起手拖成滚动时 LVGL 只发 `PRESS_LOST`、释放不发 `CLICKED`（LVGL 9.5 `lv_indev.c` release 分支 `scroll_obj == NULL` 才发 CLICKED，本地源码核验），消除滚屏误触发。点动越界判定改 fail-closed：先发 `?` 取「新状态报告 + 新有限 MPos」双序号（新抽 `QueryAndWaitFreshMachineState`，丢 ok 兜底同用），再交 `DecideJog` 判 X≤190/Y≤277 包线（与 §5 同源），取不到新鲜坐标或越界即拒；此前直读缓存坐标，`$10=WPos` 或断连期旧值可冒充当前位置。host 94/94（新增 `DecideJog` 编译跑测与 UI 事件契约钉死），ESP-IDF v6.0.1 构建 `build/xiaozhi.bin` 2,975,056B、SHA-256 `899e79a0…dc66`。未刷机、HIL 未验。

> **2026-08-20 Waveshare touch 拖动修复（源码 `211d75a`）**：按下记录绝对触点与按钮坐标，按住按绝对差跟随，6px 仅作松手时点按/拖动分界；按钮加 `PRESS_LOCK` 并关闭自身滚动/滚动链，`screen`、纸感舞台、表情容器与 canvas 也关闭滚动及滚动链；LVGL tick 保持 5ms、FT5x06 主动采样周期保持 12ms。host 测试 93/93，ESP-IDF v6.0.1 默认构建 `build-waveshare-35/xiaozhi.bin` 2,974,896B、SHA-256 `966bdf3d…a1adf` 已写入 COM14并通过哈希校验；随后约 60 秒持续 `Idle`，最低 SRAM 27,803B，无 panic/assert/watchdog。用户已确认入口恢复可见且前一版能够拖动；当前绝对锚定版的慢拖/快拖跟手性待现场复核。**未验证**：预览确认/取消、抽屉机器动作与纸面实物画质目视。

> **2026-08-19 生图低延迟对冲、合并、双缓存与硅流接入（生产验证）**：生产运行商汤 `sensenova-u1-fast`、Agnes、imagefree 三路并发对冲 + 同题 single-flight 合并 + 600s/16 项中心线缓存 + 同 SVG G-code 内容缓存；商汤瞬态 500 快速重试一次。SiliconFlow schnell + 可图 Kolors（已备案 Beijing-KeTu-202404280003，合规候选）双 racer 代码已实现并部署：真实冷调用证实请求形状/鉴权/失败降级正确；当日账户可图 402（余额不足）、schnell 403（模型未开放），**用户当日拍板不充值**，生产 `mcp.env` 的 key 已注释停用（备份 `mcp.env.bak-20260819-sf`；停用后真实出图与日志复核均无硅流请求）。将来若要启用：取消注释 → 充值/实名 → 重启 → 重跑质量闸（冷延迟 + 真实描摹 + 预览目视）。当前 `bitmap_svg.py` / `config.py` LF scp 后本地/生产 md5 同为 `d6f064ccbc1d51474a2c93e4d595e9b3` / `183aa425da1c603bf80e4e00386254f2`，双服务 active，preflight **26/26 PASS**。真实同题小松鼠：首轮 imagefree 胜出 22.278s，第二轮双缓存命中 **0.002s**，两次均 941 行/22,159B/50 次抬笔。此前分段实测：FETCH 13.168s / TRACE 0.131s / SVG+PREVIEW 0.173s / G-code 0.708s，生成下载占 93%；商汤同 payload 实测 200/12.37s，先前 500 属间歇性故障。preview-first 协议拆分仅省 0.71s 且引入确认过快时 G-code 404 风险，不采用；高频主题预热属主动付费新行为，未经用户明确批准不启用。Pollinations 候选否决：6 个冷 seed 仅 1 次 3.1s、5 次 44–46s，真实图含照片/3D 阴影纹理，描摹闭环 300s 未收敛；imagefree 3:4 画幅近 50 条 gallery 统计与 1:1 的 p50 相当（8.7s/7.9s），不构成切换依据。AI Horde 官方异步契约允许无 worker 等待 10 分钟，不满足设备预算。新增 racer 必须先过真实图片→描摹→G-code→目视可写闭环。**凭据处置**：2026-08-19 用户将硅流 key 粘贴进聊天并明确拍板「直接用，不轮换」，破例写入生产后同日决定不充值并停用；该 key 已按泄露面管理，废弃即可。

> **2026-08-17 SPEED-01 + 中心线预览云端生产部署（运行态窄覆盖）**：LF scp `bitmap_svg.py` / `server.py`，生产 SHA-256 `949d2da7…fff8` / `ac06ee82…e9e` 与本机 staged 文件一致；备份 `/root/hutuji-bak-20260817-speed01/`。仅重启 `hutuji-mcp`，新 MainPID `2559546`，ExecStartPre、应用启动与 fresh MCP 200/202 均通过；进程 maps 实查 `numpy/scipy/skimage` 均已加载，确认重依赖预热落在真实服务进程；生产 preflight **26/26 PASS**。**未验证**：未追加付费首图计时，不能把约 3 秒冷 import 的移出直接写成实测提速；设备预览确认/取消仍缺最终人机验收。bridge/nginx/cloudflared/env/unit 未改，生产提交基线仍为 `1082400`，两文件运行态按上述 SHA 绑定。

> **2026-08-17 Waveshare 控制面与真实语音历史证据（superseded by `dcbec6f` current source）**：`178f75f` 阶段完成儿童友好 UI、FT6336 映射、二维码触摸隔离、5 分钟待机、真实语音五角星出图、页尾归位与自动换纸；生产 bridge stale session 经重启恢复并通过 preflight 26/26。该证据证明真实语音主链与历史硬件行为，不替代当前 `dcbec6f` 新增预览/五按钮的人机验收。详细日志路径与观察边界保留在 git 历史及 `worktree-inventory.md`。

> **2026-08-16 全脸字幕遮挡已修复（S3 `df5c440` 已推并刷 COM12）**：根因是 `LcdDisplay::SetChatMessage()` 两套编译分支都会显示底部聊天栏，而全脸画布已扩展到 `280x190`，字幕栏从底部向上覆盖嘴部/下半脸。修复在 WeChat 与普通样式两条实现的统一入口增加 `CONFIG_BOARD_TYPE_LICHUANG_DEV_S3 + grobot_eyes_` 守卫：清空 chat label、隐藏 `bottom_bar_`、立即返回；状态栏、情绪、说话/聆听动画不受影响，其它板型继续显示聊天正文。验证：受影响模块 **32/32**、全 host **40/40**；ESP-IDF v6.0.1 build 通过（bin `0x2d4bf0`，app 余 28%）；COM12 app-flash `Hash of data verified`；启动 `unknown→starting→activating→idle`，无 panic/assert，最低 SRAM `41111B`，证据 `results/bringup-grobot-subtitle-suppression-20260816.md`，ELF `e5544a990…`。**边界**：当前固件已刷入，但未在本次串口取证中自动触发完整对话；行为由两分支 host 契约钉死，需用户正常说话目视确认「文字不再覆盖面部」。

> 状态日期：2026-08-20

---
## 状态总览

> 以 `docs/release-readiness.md` 当前行首状态段落为真值；禁止另起宣称。每条须有命令/commit/设备/日志证据才能标 ✅。

| # | 条目 | 状态 |
|---|------|------|
| 1 | S4 有纸出图与自动换纸 | ✅ 已有历史取证；纸面画质目视仍单列待补 |
| 2 | §9 Grbl 小改清单 | ✅ 核心 §9 已有烧录/fz/实机证据；源码后续 `847b564` 不自动等于板上基线 |
| 3 | 真实语音出图主链 | ✅ 2026-08-17 语音→云端→下载→出图→归位→换纸已有证据 |
| 4 | Waveshare 预览与屏幕控制 | 🟡 `152a8cbf` 镜像已刷 COM14 并哈希校验，75s 窄启动检查无 error/爆栈/复位（最低 SRAM 25,143B）。抽屉已由滚动改**分页**（主页 ↔ 手动页互斥），并修掉展开手动页必崩的 taskLVGL 爆栈（栈 7168→12288，切页日志带 HWM）。**待验**：手动页展开与点动 X/Y 现场复测、HWM 实测余量、预览确认/取消与暂停/继续/停止/重画/试笔 HIL、纸面画质目视。源码待提交，host 94/94 + 构建 + 刷机证据齐；手动页展开与点动 X/Y HIL 已过（展开 HWM 6,060B，0 崩/0 复位）；`40778e1c` 再加 WiFi 省电持有（active 态/点动窗口钉 PERFORMANCE），连点 X/Y 13 次 0 假失败、PERFORMANCE 包住点动段已实测；预览确认/取消与其余四键 HIL 仍待验 |
| 5 | MCP / 京东云运行态 | ✅ `7eac656` 取消重试去重已部署并以标准 MCP cancel 探针验真；preflight 26/26，仓内 863 项 + e2e PASS |
| 6 | 断连恢复 `paper_active` abort | 🟡 待复测；不同于已通过的正常换纸中 abort 分流 |
| 7 | 出货签核 | 🔴 待剩余 HIL、纸面画质、生产与合规签核 |
| 8 | BLE 只读诊断辅助面 | 🟡 Phase A 当前加固源码（`f1aa0e7`）已完成 COM14 刷机复验：4 分钟/1,683 行稳定性、两轮 6 字节空中包、NVS byte-identical 回滚与默认镜像自证，BLE 专测 18/18、全 host 93/93、默认/启用构建通过。尚未完成 WiFi/Telnet 共存量化门槛（含 BLE-off 基线对照）、功耗/温升与阶段 B 认证 GATT，故不得改为 ✅ |

> **BLE 修复实现边界**：只改 `hutuji_ble_diag.cc` 的 NimBLE 广播刷新生命周期：停止广播后把数据设置与重启排到独立 host event；快照刷新与地址轮换共用 pending event，轮换优先；过期 event 无动作返回；定时器重挂失败继续 fail-closed；`Shutdown()` 幂等并移除排队事件。WiFi/Telnet/job 路径未改。

> **BLE 分阶段边界（2026-08-19）**：默认关闭；公开/认证字段分级、UUID/线序/枚举、快照 stale/unknown、NVS 双槽记录、运行时 fail-closed、量化共存门槛和外部资料证据表均见 `docs/protocol.md` §1.4。BLE 不替代 WiFi Telnet、不承载 G-code、不直接控制电机/笔/换纸/授权/abort/job，也不宣称 UWB/AoA/Apple Precision Finding。阶段 A 的窄范围板上证据不替代当前源码刷机复验、完整共存矩阵或阶段 B/C。

> **默认下一刀**：① 当前 Waveshare 预览确认/取消与五按钮屏幕 HIL；② 纸面实物画质人工目视；③ 断连恢复 `paper_active` 窗口 abort 复测；④ 生产与合规签核。

> **禁止宣称**（除非对应行已 ✅）：可出货 / 商业放行 / §9 已完成 / 硬件已验证 / 联调完成 / 京东云已上线 / M2 已完成。门禁报告里的 `claims_forbidden` 清单不得为了绿而删。

---
## 门禁状态
- 本仓 `hub` 最近通过：**2026-08-20**，pytest **863 passed**、e2e `PASS`；docs 指纹 151 处全对，外仓文档引用 152 处全在范围；本轮治理与外仓约束均通过
- Grbl HEAD：`D:/Users/Grbl_Esp32`（见 `docs/worktree-inventory.md` §2）
- xiaozhi HEAD：`D:/Users/xiaozhi-esp32`（见 `docs/worktree-inventory.md` §3）
- fz HEAD：`D:/Users/zhugu/fz`（见 `docs/worktree-inventory.md` §4）

---
## 禁止宣称表

见 `docs/agent-gate.md` §5（可出货 / §9 已完成 / 硬件已验证 等）。
