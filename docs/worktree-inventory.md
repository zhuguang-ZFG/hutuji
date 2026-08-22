# 工作树盘点

> 登记所有脏树、未推提交与“生产/板上运行态领先 GitHub”的情况。HEAD 每次用命令现取；本页只保存经证据绑定的基线和必须保留的本地资产。**不要把本页静态快照当作当前 HEAD 或板上镜像身份。**

## 1. hutuji（枢纽仓）

- **路径 / 分支**：`D:/Users/hutuji` / `main`
- **当前提交链**：实时用 `git log --oneline origin/main..HEAD` 与 `git rev-parse --short HEAD` 查询；不以本页静态快照代替。
- **生产运行态**：已切换至 `4787a06` + `67ed684`（2026-08-22）。`server.py` SHA-256 `ae7219b7…7122`、`cloud_bridge.py` `f69e043b…a1739`、`preflight.sh` `e1243451…6cec`、bridge unit `4dc68f4b…824a` 均为 Git blob 精确 LF 内容、CR=0；三服务 active，`/bridge-health` 返回 200，最终 preflight `pass=30 fail=0 expected=30`。备份 `/root/hutuji-bak-20260822-67ed684/`、`/root/hutuji-bak-20260822-4787a06/`、`/root/hutuji-bak-20260822-67ed684-template/`。真实语音 HIL 未执行，设备消息 token 缺失；历史生产轮次保留在 `docs/deployment.md`。
- **本地保留资产**：`results/local-only/2026-08-18/`、`2026-08-19/`、`2026-08-20/` 与 `2026-08-21/` 均保留且被 gitignore；08-21 新增唱歌部署候选与冒烟证据、刷机事故串口原始日志（`2026-08-21-sing-bootloop-raw.log`）、恢复启动日志（`2026-08-21-waveshare-restore-boot.log`）与触摸第三轮开机取证（`2026-08-21-touch-round3-boot.log`，含双门回读 `70 -> 20 peak 60 -> 40`）。**2026-08-22 新增 `results/local-only/2026-08-22/hil-draw-com14.log`**：设备消息 API 触发的五角星全链 HIL 串口证据（生成→预览→确认→下载 CRC32 `586d72ae`→流控出图→归位→M30→error:90 缺纸）。根目录杂散 `COM14` 文件已移入 `results/local-only/2026-08-20/`；`tmp/` 未跟踪保留。不新增忽略规则，避免同类误生成被静默掩盖。
- **工作树（2026-08-22 现取）**：HEAD 以 `git rev-parse --short HEAD` 为准。本轮文档（发现收紧契约/行号、厂商参数快照、售后 KB 厂家信息）随文档提交入库。仍未提交：`mcp-server/hutuji_mcp/cloud_bridge.py`、`mcp-server/tests/test_cloud_bridge.py`（空闲死链探活：过期 id 本地消费、禁止覆盖 pending、回包刷新 `_last_traffic`、顶层 JSON-RPC error 立即探活、stdin 锁）；用户未跟踪资产 `memory_user.json` 与 `tmp/` 不纳入候选。**不要把当前脏树或静态 SHA 当作实时生产/板上身份**；生产仍是 `4787a06` + `67ed684`，本轮桥接未部署。
- **未推判断**：不用静态文字；执行 `git log --oneline origin/main..HEAD`。

## 2. Grbl_Esp32（运动控制）

- **路径 / 分支 / HEAD**：`D:/Users/Grbl_Esp32` / `fix/panel-hold-after-align-116687e` / `847b564`，与远端同名分支对齐；工作树干净。
- **有证据的板上行为基线**：`e3b1cad`（2026-08-15 烧录与启动自证）；源码后续 `847b564` 增加 §9-D′ 蓝牙功率，**没有本轮烧录证据，不能把源码 HEAD 写成板上基线**。
- **2026-08-22 换新写字机（COM13，CH340）**：烧前已完整取证并备份——原厂整片镜像 `results/local-only/2026-08-22/new-plotter-factory-backup.bin`（4MB，esptool read_flash）、串口证据 `new-plotter-serial-evidence.log`（烧前 `$I`/`$$`/`$102`/`$130`/`$131`：厂商默认 `$130/$131=200/200`；烧后 `$$` 快照为 fork 机型默认 `$130=210/$131=297`，`$20=0` 软限关闭，云端 190/190 限幅不受影响；X 长边/短边仍待装纸后单轴点动实测量尺）。烧入 app-only `0x10000`（`e3b1cad` 经 worktree 重建 `release`，PlatformIO SUCCESS，esptool `Hash of data verified`），未整片擦写，授权 NVS 保留（启动日志 `[License] Authorized (restored from NVS)`，ChipID `00002C876AE9BFB4`）。注意：源码 HEAD `847b564` 的 fz `standard` 当前因本机 mingw 16.1 工具链损坏红（syslimits/pthread 头链错误，非产品代码失败），故新机用已验证基线 `e3b1cad` 而非 HEAD。配网走 AP 模式（`GRBL_ESP`，WebUI/命令行），静态 IP 待配网完成后按实读网段写 `.13`。
- **验证边界**：核心 §9、归位、自动换纸有历史证据；后续源码变化须另跑 fz、构建、烧录与启动身份核验。

## 3. xiaozhi-esp32（S3 AI 交互）

- **工作树（2026-08-22 现取）**：HEAD `c4e9c1b`（`feat/hutuji-usb-pipe`，已推 origin）。含 R22-PIPE-02、点动 1/10mm、`$MD`、HUD/断连告警，以及发现收紧：槽位忙 1s 重试缓存、扫网不跳过未验明的缓存 IP、扫描超时 200ms、发现期 PERFORMANCE、`shutdown`+`close`。提交前 host **119/119**。祖先含 π/Grobot `cb82edb` 与 MQTT 加固 `5109c9b`。触摸灵敏度体感、π/Grobot 动效现场目视仍待用户确认。工作树干净。
- **历史批次（工作树此前干净时登记）**：`7af5cbb`/`5109c9b` 打断进监听与 MQTT 会话清理（goodbye 复核、空通道无副作用、迟到 hello 丢弃）。`70f1c58` 批次 4 文件（板 cc、lcd_display.cc、settings.cc、host 测试）：FT6336 TH_GROUP 70→30（FT5x06 官方寄存器文档 + Linux EDT 20–80 区间）、indev 读定时器 33ms→10ms（`LV_DEF_REFR_PERIOD=33` 实测 + LVGL issue #8152）；复审 P1-1 断连看门狗门控早退后原地续表 + 已恢复连接不进配网、P2-1 `Settings::SetInt` 句柄守卫、P2-2 talk/wifi 点按回弹、P2-3 贴边存档回默认。host 99/99。`37be0e9` 批次 6 文件（lcd_display.{h,cc}、板 cc、双语 locale、host 测试）：「说话」大圆钮 + 「控制」「配网」角落钮、三钮按下跟随拖动 + NVS `hutuji_ui` 布局记忆、二维码「关闭」（Schedule+StopConfigAp 事件链）、断连 120s 看门狗；含实机事故修复（拖动回调 user_data 误传 `this` → LoadProhibited 白屏重启，两轮同 Backtrace `0x42026ca6` 取证，改传 state + 防回退测试钉死）。原「分页抽屉 + taskLVGL 栈 12288 + 触摸/点动调参 + WiFi 省电持有 + 测试 + DESIGN」8 文件批次已提交 `2bc04a6`。Y 限幅收紧批次已提交 `75e83d7`（`hutuji_recovery_core.h` + host 测试 + 文档）。
- **板上镜像**：COM14 当前为 `c4e9c1b` 工作树构建的 Waveshare 3.5 app（`xiaozhi.bin` SHA-256 `303B7A77…2D54`，3,005,024B，app-flash `Hash of data verified`）。含点动 1/10mm、全宽 HUD、断连告警、`$MD`、R22、发现收紧。刷后串口见「扫网后缓存 IP 命中: 192.168.0.13」+ `[VER:]`（`discover-fix-com14-boot.log`）。**未证明槽位忙 1s 快路径**。此前镜像 `14A97DCA…9C653` / `A1CA4995…006B3` 已被覆盖。
- **当前源码验证与构建**：全 host suite **119/119**。ESP-IDF v6.0.1 Waveshare `build-waveshare-35` 已刷 COM14；落盘 app SHA-256 `303B7A77…2D54`（分区余 27%）。最近一次 BLE 启用构建仍是 `build-hutuji-ble-enabled/xiaozhi.bin` 3,172,544B、SHA-256 `5a68fafe…80d6`（`CONFIG_HUTUJI_BLE_DIAGNOSTICS=y`，生命周期代码 `f1aa0e7`）。
- **取证脚本已修正**：`results/local-only/2026-08-20/serial_capture.py` 旧版靠 pyserial 默认 open，DTR/RTS 在 open/close 各复位一次板子（S3 原生 USB Serial/JTAG 把它当复位线），曾把工具复位误读成「自发重启」；现改为清 `dtr`/`rts` 后再 `open()`。同目录 `serial_reset_capture.py` 是**有意**用 RTS 发复位脉冲，不要照此修改。
- **BLE 板上窄证据与回滚（生命周期代码 `f1aa0e7`，当前 HEAD 未改该模块）**：启用镜像 `5a68fafe…80d6` 刷入 COM14 后 4 分钟 1,683 行日志无 `BLE_ERR_MEM_CAPACITY`/panic/assert/reboot，free SRAM 稳定约 22.1KiB（`minimal sram: 1647` 低于 24KiB 门槛，缺 BLE-off 基线，不判定回归）；Bleak 30s/15s 均收到目标 UUID、固定 6 字节 payload、seq 10→140、`stale=0`、无 local name/scan response。NVS 回滚读回 byte-identical（`enabled=0`），默认镜像重刷自证，10s 目标 UUID 扫描无匹配。证据在 `results/local-only/2026-08-19/`（本机，不入库）。BLE 仍 🟡：尚未完成共存量化矩阵（含 off 基线）、完整功耗/温升与阶段 B 认证 GATT。

## 4. fz（Grbl 门禁）

- **路径 / 分支 / HEAD**：`D:/Users/zhugu/fz` / `main` / `c2bf1a3`。
- **工作树**：2 个未跟踪实验文件 `native_sim/telnet_server_behavior.cpp`、`native_sim/test_telnet_server_behavior.py`；属于用户资产，保留、不上传到 hutuji。
- **规则**：改 Grbl 代码后必须在 fz 跑 `scripts/agent_gate.py --profile standard`；本仓 `full` 只做 §9 标记检查，不能替代。

## 当前汇总

| 仓库 | 实时 HEAD 命令 | 已知状态 |
|---|---|---|
| hutuji | `git rev-parse --short HEAD` | 本轮文档入库后工作树仅剩桥接探活与用户资产（见 §1）；生产仍是 `4787a06` + `67ed684` |
| xiaozhi-esp32 | `git -C D:/Users/xiaozhi-esp32 rev-parse --short HEAD` | `c4e9c1b` 已推 `feat/hutuji-usb-pipe`；工作树干净；板上 `303B7A77…2D54`，扫网后缓存命中已见串口，槽位忙 1s 快路径未单列取证 |
| fz | `git -C D:/Users/zhugu/fz rev-parse --short HEAD` | `c2bf1a3`；2 个用户实验文件保留 |

> 每次新增/清除工作树后立即更新。本页的静态 SHA 是证据快照，不替代实时命令；源码 HEAD 也不自动等于生产/板上运行态。
