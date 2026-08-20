# 工作树盘点

> 登记所有脏树、未推提交与“生产/板上运行态领先 GitHub”的情况。HEAD 每次用命令现取；本页只保存经证据绑定的基线和必须保留的本地资产。**不要把本页静态快照当作当前 HEAD 或板上镜像身份。**

## 1. hutuji（枢纽仓）

- **路径 / 分支**：`D:/Users/hutuji` / `main`
- **当前提交链**：实时用 `git log --oneline origin/main..HEAD` 与 `git rev-parse --short HEAD` 查询；不以本页静态快照代替。
- **生产运行态**：应用基线 `7eac656`。2026-08-20 LF blob `bitmap_svg.py` / `server.py` / `requirements.txt` 生产 SHA-256 `765cc091…1fa` / `ddf2b5c0…f26` / `4200aed6…deb`，双服务 MainPID `2966745/2966746`，两条 ExecStartPre 成功，preflight 26/26。标准 MCP cancel 冷题探针前两次各 2 秒取消、第三次 8.504 秒接管成功；同窗 3 次 `CallToolRequest`、2 次取消、仅 1 轮生图对冲/1 次 Agnes POST。现行链仍含 2026-08-19 三路对冲、商汤快速重试、中心线/G-code 双缓存；硅流 key 按用户决定保持停用。bridge/nginx/cloudflared/env 结构未改。
- **本地保留资产**：`results/local-only/2026-08-18/`、`2026-08-19/` 与 `2026-08-20/` 均保留且被 gitignore；08-20 新增生产 draw/cancel 探针、精确 LF 部署候选与初次 CRLF 候选失败证据。根目录误生成的 `memory_remove` / `memory_replace` JSON 参数文件已于 2026-08-20 核对内容后删除；其中过时的 clang-format 持久记忆同步移除，无效的同值 replace 未执行。不新增忽略规则，避免同类误生成被静默掩盖。
- **未推判断**：不用静态文字；执行 `git log --oneline origin/main..HEAD`。

## 2. Grbl_Esp32（运动控制）

- **路径 / 分支 / HEAD**：`D:/Users/Grbl_Esp32` / `fix/panel-hold-after-align-116687e` / `847b564`，与远端同名分支对齐；工作树干净。
- **有证据的板上行为基线**：`e3b1cad`（2026-08-15 烧录与启动自证）；源码后续 `847b564` 增加 §9-D′ 蓝牙功率，**没有本轮烧录证据，不能把源码 HEAD 写成板上基线**。
- **验证边界**：核心 §9、归位、自动换纸有历史证据；后续源码变化须另跑 fz、构建、烧录与启动身份核验。

## 3. xiaozhi-esp32（S3 AI 交互）

- **路径 / 分支 / HEAD**：`D:/Users/xiaozhi-esp32` / `feat/hutuji-usb-pipe` / `1fba8a6`，已推 `origin/feat/hutuji-usb-pipe`；激活码中转固件钩子 `1fba8a6`，唤醒词自定义「小派」(multinet mn7_cn) 提交 `616fd74`，测试/待机文案修正 `46a6cb4`，DESIGN.md 契约同步提交 `1040c0a`（仅文档，固件内容与 `abc50d8` 相同），手动区加固提交 `abc50d8`，touch 拖动修复提交 `211d75a`，UI/设计系统提交 `2dc2769`，BLE 生命周期提交 `f1aa0e7`。
- **工作树：干净**。原「分页抽屉 + taskLVGL 栈 12288 + 触摸/点动调参 + WiFi 省电持有 + 测试 + DESIGN」8 文件批次已提交 `2bc04a6`（含 `JobHoldsPerformance`/`PerformanceReassertPeriodMs` 决策核、`StartPerformanceHold`/`StopPerformanceHold`/`ReassertPerformance`/`PerformanceTimerThunk` 接线，host 96/96）。手动页展开 + 点动 + 省电持有连点 HIL 均已过；预览确认/取消与其余四键 HIL 未过。
- **板上默认 UI 镜像**：COM14 当前运行 `1fba8a6` 的构建（app `d1638a63` 0x2d70a0B，含激活码中转钩子 + 省电持有 + 唤醒词「小派」+ 新待机文案），写入哈希校验通过；无感激活 E2E 已过（解绑→重启→7.6s 自动回绑，控制台新记录 id=2569191）。旧基线：`46a6cb4` 的构建（app `508035db` 0x2d6b30B 级 + assets `ae7d1211` 4,054,864B，含 WiFi 省电持有 + 自定义唤醒词「小派」mn7_cn + 新待机文案），写入哈希校验通过；启动实测 MultiNet7 加载、`Command 1: xiao pai`、0 error、minimal sram 24,379B，host 96/96。唤醒词收音灵敏度现场复测待做。
- **当前源码验证与构建**：全 host suite 94/94（`scripts/tests`，含分页布局、标题抗溢出、`active` 强制回主页、栈 12288 与 HWM 日志的钉死断言；新增断言并入既有 drawer 契约测试，未新增测试项）。ESP-IDF v6.0.1 构建 `build/xiaozhi.bin` 2,976,352B、SHA-256 `40778e1c…`，已刷 COM14。最近一次 BLE 启用构建仍是 `build-hutuji-ble-enabled/xiaozhi.bin` 3,172,544B、SHA-256 `5a68fafe…80d6`（`CONFIG_HUTUJI_BLE_DIAGNOSTICS=y`，生命周期代码 `f1aa0e7`）。
- **取证脚本已修正**：`results/local-only/2026-08-20/serial_capture.py` 旧版靠 pyserial 默认 open，DTR/RTS 在 open/close 各复位一次板子（S3 原生 USB Serial/JTAG 把它当复位线），曾把工具复位误读成「自发重启」；现改为清 `dtr`/`rts` 后再 `open()`。同目录 `serial_reset_capture.py` 是**有意**用 RTS 发复位脉冲，不要照此修改。
- **BLE 板上窄证据与回滚（生命周期代码 `f1aa0e7`，当前 HEAD 未改该模块）**：启用镜像 `5a68fafe…80d6` 刷入 COM14 后 4 分钟 1,683 行日志无 `BLE_ERR_MEM_CAPACITY`/panic/assert/reboot，free SRAM 稳定约 22.1KiB（`minimal sram: 1647` 低于 24KiB 门槛，缺 BLE-off 基线，不判定回归）；Bleak 30s/15s 均收到目标 UUID、固定 6 字节 payload、seq 10→140、`stale=0`、无 local name/scan response。NVS 回滚读回 byte-identical（`enabled=0`），默认镜像重刷自证，10s 目标 UUID 扫描无匹配。证据在 `results/local-only/2026-08-19/`（本机，不入库）。BLE 仍 🟡：尚未完成共存量化矩阵（含 off 基线）、完整功耗/温升与阶段 B 认证 GATT。

## 4. fz（Grbl 门禁）

- **路径 / 分支 / HEAD**：`D:/Users/zhugu/fz` / `main` / `c2bf1a3`。
- **工作树**：2 个未跟踪实验文件 `native_sim/telnet_server_behavior.cpp`、`native_sim/test_telnet_server_behavior.py`；属于用户资产，保留、不上传到 hutuji。
- **规则**：改 Grbl 代码后必须在 fz 跑 `scripts/agent_gate.py --profile standard`；本仓 `full` 只做 §9 标记检查，不能替代。

## 当前汇总

| 仓库 | 实时 HEAD 命令 | 已知状态 |
|---|---|---|
| hutuji | `git rev-parse --short HEAD` | `f93b3d4`：服务修复 `7eac656` 已部署，R17-XREPO-01 交叉钉已提交。工作树脏：`docs/release-readiness.md` + 本页（分页/爆栈/取证工具三条证据）待提交 |
| Grbl_Esp32 | `git -C D:/Users/Grbl_Esp32 rev-parse --short HEAD` | `847b564`，干净；板上证据止于 `e3b1cad` |
| xiaozhi-esp32 | `git -C D:/Users/xiaozhi-esp32 rev-parse --short HEAD` | `1fba8a6` 已推且**干净**；手动页展开 + 点动 + 省电持有连点 HIL 均已过，唤醒词「小派」启动证据齐、收音待复测，无感激活 E2E 已过（7.6s 自动回绑）；预览确认/取消与其余四键 HIL 未过；板上镜像即 `1fba8a6` 构建 |
| fz | `git -C D:/Users/zhugu/fz rev-parse --short HEAD` | `c2bf1a3`；2 个用户实验文件保留 |

> 每次新增/清除工作树后立即更新。本页的静态 SHA 是证据快照，不替代实时命令；源码 HEAD 也不自动等于生产/板上运行态。
