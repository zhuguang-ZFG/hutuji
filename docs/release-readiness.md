# 发布与出货就绪度

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
| 4 | Waveshare 预览与屏幕控制 | 🟡 `211d75a` 默认镜像已构建、刷写并通过窄启动检查；入口可见且前一版可拖动已有现场反馈，当前绝对锚定版跟手性、预览确认/取消与抽屉机器动作 HIL 待验 |
| 5 | MCP / 京东云运行态 | ✅ SPEED-01/设备 PNG 预览窄部署，preflight 26/26；仓库实现已提交 |
| 6 | 断连恢复 `paper_active` abort | 🟡 待复测；不同于已通过的正常换纸中 abort 分流 |
| 7 | 出货签核 | 🔴 待剩余 HIL、纸面画质、生产与合规签核 |
| 8 | BLE 只读诊断辅助面 | 🟡 Phase A 当前加固源码（`f1aa0e7`）已完成 COM14 刷机复验：4 分钟/1,683 行稳定性、两轮 6 字节空中包、NVS byte-identical 回滚与默认镜像自证，BLE 专测 18/18、全 host 93/93、默认/启用构建通过。尚未完成 WiFi/Telnet 共存量化门槛（含 BLE-off 基线对照）、功耗/温升与阶段 B 认证 GATT，故不得改为 ✅ |

> **BLE 修复实现边界**：只改 `hutuji_ble_diag.cc` 的 NimBLE 广播刷新生命周期：停止广播后把数据设置与重启排到独立 host event；快照刷新与地址轮换共用 pending event，轮换优先；过期 event 无动作返回；定时器重挂失败继续 fail-closed；`Shutdown()` 幂等并移除排队事件。WiFi/Telnet/job 路径未改。

> **BLE 分阶段边界（2026-08-19）**：默认关闭；公开/认证字段分级、UUID/线序/枚举、快照 stale/unknown、NVS 双槽记录、运行时 fail-closed、量化共存门槛和外部资料证据表均见 `docs/protocol.md` §1.4。BLE 不替代 WiFi Telnet、不承载 G-code、不直接控制电机/笔/换纸/授权/abort/job，也不宣称 UWB/AoA/Apple Precision Finding。阶段 A 的窄范围板上证据不替代当前源码刷机复验、完整共存矩阵或阶段 B/C。

> **默认下一刀**：① 当前 Waveshare 预览确认/取消与五按钮屏幕 HIL；② 纸面实物画质人工目视；③ 断连恢复 `paper_active` 窗口 abort 复测；④ 生产与合规签核。

> **禁止宣称**（除非对应行已 ✅）：可出货 / 商业放行 / §9 已完成 / 硬件已验证 / 联调完成 / 京东云已上线 / M2 已完成。门禁报告里的 `claims_forbidden` 清单不得为了绿而删。

---
## 门禁状态
- 本仓 `hub` 最近通过：**2026-08-19**，pytest **861 passed**、e2e `PASS`；docs 指纹 151 处全对，外仓文档引用 152 处全在范围；本轮治理与外仓约束均通过
- Grbl HEAD：`D:/Users/Grbl_Esp32`（见 `docs/worktree-inventory.md` §2）
- xiaozhi HEAD：`D:/Users/xiaozhi-esp32`（见 `docs/worktree-inventory.md` §3）
- fz HEAD：`D:/Users/zhugu/fz`（见 `docs/worktree-inventory.md` §4）

---
## 禁止宣称表

见 `docs/agent-gate.md` §5（可出货 / §9 已完成 / 硬件已验证 等）。
