# 工作树盘点

> 登记所有脏树、未推提交与“生产/板上运行态领先 GitHub”的情况。HEAD 每次用命令现取；本页只保存经证据绑定的基线和必须保留的本地资产。**不要把本页静态快照当作当前 HEAD 或板上镜像身份。**

## 1. hutuji（枢纽仓）

- **路径 / 分支**：`D:/Users/hutuji` / `main`
- **当前提交链**：实时用 `git log --oneline origin/main..HEAD` 与 `git rev-parse --short HEAD` 查询；不以本页静态快照代替。
- **生产运行态**：已切换至 `4787a06` + `67ed684`（2026-08-22）。`server.py` SHA-256 `ae7219b7…7122`、`cloud_bridge.py` `f69e043b…a1739`、`preflight.sh` `e1243451…6cec`、bridge unit `4dc68f4b…824a` 均为 Git blob 精确 LF 内容、CR=0；三服务 active，`/bridge-health` 返回 200，最终 preflight `pass=30 fail=0 expected=30`。备份 `/root/hutuji-bak-20260822-67ed684/`、`/root/hutuji-bak-20260822-4787a06/`、`/root/hutuji-bak-20260822-67ed684-template/`。真实语音 HIL 未执行，设备消息 token 缺失；历史生产轮次保留在 `docs/deployment.md`。
- **本地保留资产**：`results/local-only/2026-08-18/`、`2026-08-19/`、`2026-08-20/` 与 `2026-08-21/` 均保留且被 gitignore；08-21 新增唱歌部署候选与冒烟证据、刷机事故串口原始日志（`2026-08-21-sing-bootloop-raw.log`）、恢复启动日志（`2026-08-21-waveshare-restore-boot.log`）与触摸第三轮开机取证（`2026-08-21-touch-round3-boot.log`，含双门回读 `70 -> 20 peak 60 -> 40`）。根目录杂散 `COM14` 文件已移入 `results/local-only/2026-08-20/`；`tmp/` 未跟踪保留。不新增忽略规则，避免同类误生成被静默掩盖。
- **工作树**：有用户未跟踪资产 `memory_user.json` 与 `tmp/`，本轮未修改、未纳入候选；本轮生产证据已写入 `docs/release-readiness.md` 与 `docs/deployment.md`。不要把当前脏树或静态 SHA 当作实时生产/板上身份。
- **未推判断**：不用静态文字；执行 `git log --oneline origin/main..HEAD`。

## 2. Grbl_Esp32（运动控制）

- **路径 / 分支 / HEAD**：`D:/Users/Grbl_Esp32` / `fix/panel-hold-after-align-116687e` / `847b564`，与远端同名分支对齐；工作树干净。
- **有证据的板上行为基线**：`e3b1cad`（2026-08-15 烧录与启动自证）；源码后续 `847b564` 增加 §9-D′ 蓝牙功率，**没有本轮烧录证据，不能把源码 HEAD 写成板上基线**。
- **验证边界**：核心 §9、归位、自动换纸有历史证据；后续源码变化须另跑 fz、构建、烧录与启动身份核验。

## 3. xiaozhi-esp32（S3 AI 交互）

- **工作树**：干净。2026-08-22 π 启动画面与 Grobot 渐变批次已提交并推送 `cb82edb`：按 `can1357/oh-my-pi` `welcome.ts` 同源复刻 5×12 π 字形、5 色渐变、3000ms/2.5 圈/ease-out cubic/高光参数；3s 动画后 500ms 交接 Grobot。常驻脸色、情绪色、配件色与 Grobot 模式主题按钮均从同一 5 色渐变派生，空闲/说话高光、5s 色潮与主按钮呼吸已接线；Python 预览直接解析 C++ core。host **115/115**、clang-format dry-run 通过，ESP-IDF v6.0.1 Waveshare 构建通过；对应镜像已刷 COM14 且分区写入均 `Hash of data verified`。此前触摸第三轮批次 `4d5d4e0` 的双门回读、点动 Y≤190 与唱歌工具均为 `cb82edb` 祖先；触摸灵敏度体感、π/Grobot 动效现场目视与既有屏幕 HIL 仍待用户确认。
- **历史批次（工作树此前干净时登记）**：`70f1c58` 批次 4 文件（板 cc、lcd_display.cc、settings.cc、host 测试）：FT6336 TH_GROUP 70→30（FT5x06 官方寄存器文档 + Linux EDT 20–80 区间）、indev 读定时器 33ms→10ms（`LV_DEF_REFR_PERIOD=33` 实测 + LVGL issue #8152）；复审 P1-1 断连看门狗门控早退后原地续表 + 已恢复连接不进配网、P2-1 `Settings::SetInt` 句柄守卫、P2-2 talk/wifi 点按回弹、P2-3 贴边存档回默认。host 99/99。`37be0e9` 批次 6 文件（lcd_display.{h,cc}、板 cc、双语 locale、host 测试）：「说话」大圆钮 + 「控制」「配网」角落钮、三钮按下跟随拖动 + NVS `hutuji_ui` 布局记忆、二维码「关闭」（Schedule+StopConfigAp 事件链）、断连 120s 看门狗；含实机事故修复（拖动回调 user_data 误传 `this` → LoadProhibited 白屏重启，两轮同 Backtrace `0x42026ca6` 取证，改传 state + 防回退测试钉死）。原「分页抽屉 + taskLVGL 栈 12288 + 触摸/点动调参 + WiFi 省电持有 + 测试 + DESIGN」8 文件批次已提交 `2bc04a6`。Y 限幅收紧批次已提交 `75e83d7`（`hutuji_recovery_core.h` + host 测试 + 文档）。
- **板上镜像**：COM14 当前运行 `cb82edb` 源码构建的 Waveshare 3.5 镜像；esptool 全分区哈希校验通过。该轮构建/刷写进程已完成，但后续多次 boot 采集因 COM14 重枚举不稳定而只有部分轮次留下日志；最后一次 120s 设备采集在未复位的当前镜像上持续收到写字机 `Idle`，无语音工具调用。唱歌语音全链仍沿用 `5e1a91b` 历史取证。未验证：本轮 π/Grobot 动效现场目视、完整冷启动日志、触摸灵敏度体感与误触回归、说话/配网/关闭/断连自动显码/布局记忆重启恢复的现场 HIL。
- **当前源码验证与构建**：全 host suite **115/115**，含 π 字形/渐变/3000ms 动画/500ms 交接、脸部情绪分离、空闲/说话高光与按钮呼吸契约；触摸双门、管道与唱歌既有测试同跑。ESP-IDF v6.0.1 最新 Waveshare 构建已刷 COM14；构建目录当前 app SHA-256 `4f88a8d8…b119`，但未保存刷写当刻镜像哈希对照，故该哈希只标识当前落盘产物，不反推板上内容。最近一次 BLE 启用构建仍是 `build-hutuji-ble-enabled/xiaozhi.bin` 3,172,544B、SHA-256 `5a68fafe…80d6`（`CONFIG_HUTUJI_BLE_DIAGNOSTICS=y`，生命周期代码 `f1aa0e7`）。
- **取证脚本已修正**：`results/local-only/2026-08-20/serial_capture.py` 旧版靠 pyserial 默认 open，DTR/RTS 在 open/close 各复位一次板子（S3 原生 USB Serial/JTAG 把它当复位线），曾把工具复位误读成「自发重启」；现改为清 `dtr`/`rts` 后再 `open()`。同目录 `serial_reset_capture.py` 是**有意**用 RTS 发复位脉冲，不要照此修改。
- **BLE 板上窄证据与回滚（生命周期代码 `f1aa0e7`，当前 HEAD 未改该模块）**：启用镜像 `5a68fafe…80d6` 刷入 COM14 后 4 分钟 1,683 行日志无 `BLE_ERR_MEM_CAPACITY`/panic/assert/reboot，free SRAM 稳定约 22.1KiB（`minimal sram: 1647` 低于 24KiB 门槛，缺 BLE-off 基线，不判定回归）；Bleak 30s/15s 均收到目标 UUID、固定 6 字节 payload、seq 10→140、`stale=0`、无 local name/scan response。NVS 回滚读回 byte-identical（`enabled=0`），默认镜像重刷自证，10s 目标 UUID 扫描无匹配。证据在 `results/local-only/2026-08-19/`（本机，不入库）。BLE 仍 🟡：尚未完成共存量化矩阵（含 off 基线）、完整功耗/温升与阶段 B 认证 GATT。

## 4. fz（Grbl 门禁）

- **路径 / 分支 / HEAD**：`D:/Users/zhugu/fz` / `main` / `c2bf1a3`。
- **工作树**：2 个未跟踪实验文件 `native_sim/telnet_server_behavior.cpp`、`native_sim/test_telnet_server_behavior.py`；属于用户资产，保留、不上传到 hutuji。
- **规则**：改 Grbl 代码后必须在 fz 跑 `scripts/agent_gate.py --profile standard`；本仓 `full` 只做 §9 标记检查，不能替代。

## 当前汇总

| 仓库 | 实时 HEAD 命令 | 已知状态 |
|---|---|---|
| hutuji | `git rev-parse --short HEAD` | `4d9e49a`（服务时 §5 复核）；工作树干净 |
| xiaozhi-esp32 | `git -C D:/Users/xiaozhi-esp32 rev-parse --short HEAD` | `cb82edb`（π 启动画面 + Grobot/主题统一渐变）已推；工作树干净；对应 Waveshare 镜像已刷 COM14、全分区写入哈希校验通过，完整冷启动日志与现场动效目视待验 |
| fz | `git -C D:/Users/zhugu/fz rev-parse --short HEAD` | `c2bf1a3`；2 个用户实验文件保留 |

> 每次新增/清除工作树后立即更新。本页的静态 SHA 是证据快照，不替代实时命令；源码 HEAD 也不自动等于生产/板上运行态。
