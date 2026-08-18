# 发布与出货就绪度

> **2026-08-18 Waveshare 手动控制固件部分 HIL（S3 `dcbec6f`，COM14；预览/五动作仍未验）**：当前 `build-waveshare-35/xiaozhi.bin` 2,971,264B、SHA-256 `39157935…8362`，ELF SHA-256 `031cafd5…aada`；app 分区写入 `0x20000` 的工具报告完成且 hash 校验通过。首段串口在 `Touch threshold tuned: 70 -> 40` 后暂时掉线；同一取证脚本 76.586s 重新打开 COM14，随后启动横幅 `ELF file SHA256: 031cafd51...` 与当前 ELF 对齐，并完成触摸初始化、七个 hutuji 工具注册、`HutujiPipe` 启动、WiFi、Grbl banner/授权探针与 `idle`。控制抽屉实机链已覆盖 `jog_y+`×2、`jog_x-`、`pen_down`，均收到 `ok`、坐标变化并回 Idle；末尾无 fatal marker，连续 fresh `Idle`，min SRAM `28923B`。**历史边界**：真实语音出图、归位、自动换纸已有 2026-08-17 证据，不替代当前新增 UI HIL。证据 `results/waveshare-manual-controls-v2-20260818.log`、`results/waveshare-passive-reenumeration-20260818.log`（本机，不入库）。**未验证**：预览确认/取消与暂停/继续/停止/重画/试笔五个屏幕动作。

> **2026-08-17 SPEED-01 + 中心线预览云端生产部署（运行态窄覆盖）**：LF scp `bitmap_svg.py` / `server.py`，生产 SHA-256 `949d2da7…fff8` / `ac06ee82…e9e` 与本机 staged 文件一致；备份 `/root/hutuji-bak-20260817-speed01/`。仅重启 `hutuji-mcp`，新 MainPID `2559546`，ExecStartPre、应用启动与 fresh MCP 200/202 均通过；进程 maps 实查 `numpy/scipy/skimage` 均已加载，确认重依赖预热落在真实服务进程；生产 preflight **26/26 PASS**。**未验证**：未追加付费首图计时，不能把约 3 秒冷 import 的移出直接写成实测提速；设备预览确认/取消仍缺最终人机验收。bridge/nginx/cloudflared/env/unit 未改，生产提交基线仍为 `1082400`，两文件运行态按上述 SHA 绑定。

> **2026-08-17 Waveshare 控制面与真实语音历史证据（superseded by `dcbec6f` current source）**：`178f75f` 阶段完成儿童友好 UI、FT6336 映射、二维码触摸隔离、5 分钟待机、真实语音五角星出图、页尾归位与自动换纸；生产 bridge stale session 经重启恢复并通过 preflight 26/26。该证据证明真实语音主链与历史硬件行为，不替代当前 `dcbec6f` 新增预览/五按钮的人机验收。详细日志路径与观察边界保留在 git 历史及 `worktree-inventory.md`。

> **2026-08-16 全脸字幕遮挡已修复（S3 `df5c440` 已推并刷 COM12）**：根因是 `LcdDisplay::SetChatMessage()` 两套编译分支都会显示底部聊天栏，而全脸画布已扩展到 `280x190`，字幕栏从底部向上覆盖嘴部/下半脸。修复在 WeChat 与普通样式两条实现的统一入口增加 `CONFIG_BOARD_TYPE_LICHUANG_DEV_S3 + grobot_eyes_` 守卫：清空 chat label、隐藏 `bottom_bar_`、立即返回；状态栏、情绪、说话/聆听动画不受影响，其它板型继续显示聊天正文。验证：受影响模块 **32/32**、全 host **40/40**；ESP-IDF v6.0.1 build 通过（bin `0x2d4bf0`，app 余 28%）；COM12 app-flash `Hash of data verified`；启动 `unknown→starting→activating→idle`，无 panic/assert，最低 SRAM `41111B`，证据 `results/bringup-grobot-subtitle-suppression-20260816.md`，ELF `e5544a990…`。**边界**：当前固件已刷入，但未在本次串口取证中自动触发完整对话；行为由两分支 host 契约钉死，需用户正常说话目视确认「文字不再覆盖面部」。

> 状态日期：2026-08-18

---
## 状态总览

> 以 `docs/release-readiness.md` 当前行首状态段落为真值；禁止另起宣称。每条须有命令/commit/设备/日志证据才能标 ✅。

| # | 条目 | 状态 |
|---|------|------|
| 1 | S4 有纸出图与自动换纸 | ✅ 已有历史取证；纸面画质目视仍单列待补 |
| 2 | §9 Grbl 小改清单 | ✅ 核心 §9 已有烧录/fz/实机证据；源码后续 `847b564` 不自动等于板上基线 |
| 3 | 真实语音出图主链 | ✅ 2026-08-17 语音→云端→下载→出图→归位→换纸已有证据 |
| 4 | Waveshare 预览与屏幕控制 | 🟡 当前 `dcbec6f` 仅完成启动身份、控制抽屉低层动作部分 HIL；预览确认/取消与五按钮未验 |
| 5 | MCP / 京东云运行态 | ✅ SPEED-01/设备 PNG 预览窄部署，preflight 26/26；仓库实现已提交 |
| 6 | 断连恢复 `paper_active` abort | 🟡 待复测；不同于已通过的正常换纸中 abort 分流 |
| 7 | 出货签核 | 🔴 待剩余 HIL、纸面画质、生产与合规签核 |

> **默认下一刀**：① 当前 Waveshare 预览确认/取消与五按钮屏幕 HIL；② 纸面实物画质人工目视；③ 断连恢复 `paper_active` 窗口 abort 复测；④ 生产与合规签核。

> **禁止宣称**（除非对应行已 ✅）：可出货 / 商业放行 / §9 已完成 / 硬件已验证 / 联调完成 / 京东云已上线 / M2 已完成。门禁报告里的 `claims_forbidden` 清单不得为了绿而删。

---
## 门禁状态

> 本节只记「最近一次通过」的真值；中间过程不得重复记录。

- 本仓 `hub` 最近通过：**2026-08-18**，pytest **845 passed**、e2e `PASS`；本轮文档治理完成后须再次运行并以退出码为准
- Grbl HEAD：`D:/Users/Grbl_Esp32`（见 `docs/worktree-inventory.md` §2）
- xiaozhi HEAD：`D:/Users/xiaozhi-esp32`（见 `docs/worktree-inventory.md` §3）
- fz HEAD：`D:/Users/zhugu/fz`（见 `docs/worktree-inventory.md` §4）

---
## 禁止宣称表

见 `docs/agent-gate.md` §5（可出货 / §9 已完成 / 硬件已验证 等）。
