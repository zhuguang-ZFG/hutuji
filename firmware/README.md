# firmware/：S3 哑管道固件补丁资料

> 补丁本体不在本仓库；位于 `D:/Users/xiaozhi-esp32` fork。本文只记录边界、构建入口和验证状态。  
> 全项目接手防呆：`../docs/agent-handoff.md`。状态：`../docs/release-readiness.md`。

## 当前链路与契约

- 板型：`lichuang-dev`（立创实战派 ESP32-S3）。
- 板级原理图/脚位对照：`../docs/hardware/lichuang-szpi-esp32s3-board.md`（**以板级 `config.h` 为准，禁止轻易改脚**）。
- 正式板间链路：**WiFi Telnet :23**，S3 做 TCP 客户端；不使用 USB Host/CH9350。
- 设备侧工具：`hutuji.status/draw/abort/pause/resume/repeat/pen_test` 共七项。
- S3 是哑管道：全量下载 → 长度/CRC32 校验 → 授权门 → 逐行发送/等 `ok`。
- 权威契约：`../docs/protocol.md` **v0.5**；实施清单：`m1-usb-pipe.md` 的 M2 章节。

## 三仓边界

| 内容 | 修改位置 | 本仓留什么证据 |
|------|----------|----------------|
| S3 工具、下载、Telnet 转发 | `D:/Users/xiaozhi-esp32` | commit、构建日志、固件 SHA256、烧录/设备记录 |
| Grbl keepalive/状态路由 | `D:/Users/Grbl_Esp32` | commit、`agent_gate.py` overall pass、实测记录 |
| MCP 服务、协议、部署、SOP | `D:/Users/hutuji` | pytest/e2e/云桥接/发布清单 |

禁止把两个固件 fork 合并进本仓，禁止合并双芯片固件。

## S3 实施状态

| 里程碑 | 状态 | 说明 |
|--------|------|------|
| M1 Telnet 连接、发现、探活 | 🟡 已烧录实测，发现策略 2026-08-22 再收紧 | NVS IP 缓存 + `/24` 扫描，候选须通过 Grbl banner 校验；槽位忙改为 1s 重试缓存、不扫网跳过真 IP（host 119 项钉死）。刷后 COM14 见「扫网后缓存 IP 命中」，槽位忙 1s 快路径未单列取证；不把旧 `05f4563` 链路核验写成已覆盖本条 |
| M2 下载/CRC/授权/逐行流控 | 🟡 关键闭环已实测 | 全量下载、长度/CRC、授权门、逐行 `ok` 配对和 fresh-`Idle` 完成判定已回归 |
| abort/单写者 | 🟡 关键闭环已实测 | 非换纸 abort 从 `streaming/Run,Z=5` 恢复至 `aborted/Idle,Z=0`；普通画线断连后的废纸处理与 PSRAM 从头重发主路径已实测，最终页尾无纸 `error:90`，8.3 尚未完整通过 |
| 硬件冒烟与多页换纸 | 🟡 关键有纸路径已实测 | `13057e8`/`0x2ce890` 已有 S3 自动路径出墨、页尾 `[PaperStatus] 0` + `state=done`；2026-07-30 B-13 已捕获有纸 `Changing=On`。仍缺换纸中 abort、App 真实语音、P2 S3/S4 |

当前源码 HEAD 是 `211d75a`（`feat/hutuji-usb-pipe`，已提交并推送）。该版在既有下载/CRC/授权/窗口流控上叠加 Waveshare Grobot 工作台、预览/控制抽屉、BLE Phase A 与触摸层修复；当前默认镜像已构建并刷 COM14，入口恢复可见且前一版可拖动已有现场反馈，绝对锚定版跟手性和抽屉动作仍待 HIL。2026-08-08 的 `16f2f83` 标准 release/COM12 记录只保留为历史链路证据；当前 HEAD 的下载/CRC/窗口应答配对和纸面回归仍待真实设备 token，2026-08-03 S4 证据使用较早的 `ae4b524`。以下为历史提交链：`c1267ec` WiFi 三修 →
`ed5b132` 净作画时长 + ETA → `15f73c9` `StreamToGrbl` 窗口化流控（window=512 + 换纸行排空）→
`13057e8` 落笔前旁路 `G92 Z0` 校准 + `ok` 静默丢失兜底 → `f1cf066` 默认关闭的 §8.5 abort
取证脚手架（`HUTUJI_AUTO_TEST_ABORT_ON_PAPER`，产品行为不变）。
`15f73c9` 的 8.3 自动触发镜像 `xiaozhi.bin=0x2cdcb0`、SHA256 `090D65AB...8D0E` 已烧录 COM6，
并完成有纸 `state=done` 复测（`results/s3-ed5b132-windowed-flow-eta-paper-draw-evidence-20260729.md`）。
**该镜像属历史**——已被 2026-07-29 20:41 写入的 `0x2ce890`（`13057e8`）覆盖；`0x2ce890` 本身又已被 07-31 的 `0x2cec90` 覆盖，勿当现状。

**`13057e8` 固化了此前未提交的 3 个文件**（`main/boards/lichuang-dev/hutuji_job.cc`、`hutuji_job.h`、
`hutuji_pipe.cc`，+407/−83）：`PreparePenOrigin` 在 fresh `Idle` + 弹簧回位 100ms
（`kPenSpringReturnMs`）后旁路 `G92 Z0`，以及 `ok` 超时按 MPos 状态兜底（`kMaxOkFallback=3`、
`kOkFallbackPosTolMm=0.05`）。**S3-LC-01 在 2026-07-29 烧录的镜像内容即这批改动**（已被 07-31 的 `0x2cec90` 覆盖）——2026-07-29
20:39 构建 `xiaozhi.bin=0x2ce890`（2943120 字节），SHA256
`853179049C21028E62DB6784E20040756DB2866A73ED93F20842EFD7A0A24C3E`，20:41 由 app-flash 脚本
`results/.tmp/flash_autodraw_com6.ps1` 写入 COM6 并通过 esptool 自校验（`Wrote 2943120 bytes`、
`Hash of data verified`，ESP32-S3 MAC `9c:13:9e:8b:ae:f0`）。证据
`results/s3-13057e8-com6-flash-evidence-20260729.md`（原始日志由 IDF 自留于
`build-hutuji-f1000-autodraw/log/idf_py_stdout_output_36852`）。该镜像 autodraw URL 硬编码为
`http://192.168.1.3:8300/files/f1000-line.gcode`，上电即自动下载并画。
**烧录本身已可宣称**；此前落笔只在手动 Telnet 序列（`G92 Z0` →
`G1 Z5.0 F10000`）下纸面证实，S3 自动路径出墨已于 2026-07-30 B-12 归档：同一 `0x2ce890` 镜像下载 409 行/9782 字节/CRC32 `733095ec` 测试件，8 条线全部完整连续，页尾 `M30` 后 `[PaperStatus] 0`、`state=done`。

历史基线（勿当现状）：`3ca9d31` 在 `27eff73` 的受控自动测试入口上修复 `M3/M5` planner-sync `ok`
的 60s 误超时，IDF 6.0.1 默认构建 `xiaozhi.bin=0x2cce30`、app 分区剩余 `0x1231d0`/29%、SHA256
`5D5FE23CE0DF6EE364EBACC2503E8374174781AEAE5E9BFC9BACDF227987BBA6`；其 8.3 自动触发镜像
`xiaozhi.bin=0x2cd070`、剩余 `0x122f90`/29%、SHA256
`98C3B6AF0CB136AF79A0256B5FB8A5279FE0715FE087322419A791D3BD3C122C` 曾烧录 COM6。
该轮复测已看到断连、重连、`Changing=Off`、废纸 `M30`、`[PaperStatus] 0`、PSRAM 从第 1 行重画、
55/55 行完成与 fresh `Idle`；最终页尾自动换纸因无纸返回 `[PaperStatus] 2` + `error:90`，
任务 `state=error`。证据见 `results/bringup-8.3-fault-capture-20260729-010436.md` 与
`results/s3-3ca9d31-8.3-pen-timeout-evidence-20260729.md`；不得扩大为 8.3 完整通过或有纸换纸验收。

## 开发与构建

1. 在 `D:/Users/xiaozhi-esp32` 确认目标分支和基线，保存 `git status --short`。
2. 开工前读该仓自身 `AGENTS.md`，使用已验证的 ESP-IDF **v6.0.1**。
3. 构建入口：`python3 scripts/release.py lichuang-dev`；若上游脚本要求名称参数，以该仓 `--help` 为准。
   8.3 自动触发绘图调试镜像使用：`idf.py -DBOARD_NAME=lichuang-dev -DBOARD_TYPE=lichuang-dev -DHUTUJI_AUTO_TEST_DRAW=ON -DHUTUJI_AUTO_TEST_DRAW_URL='<短时效 G-code URL>' build`；真实 URL/token 不入库。
   bringup §8.5（换纸中调 abort）取证镜像再加 `-DHUTUJI_AUTO_TEST_ABORT_ON_PAPER=ON`；**PowerShell 下 URL 必须整体加单引号**，否则 `http://…` 会被切成新参数（`idf.py` 报 `No such option: -D`）。取证后回刷不带宏的镜像。
4. 产物必须记录：fork commit、是否 dirty、IDF 版本、板型、sdkconfig、构建命令、二进制 SHA256。
5. 烧录前确认 S3 与写字机在同一网络；连接器会先试 NVS 缓存，再扫描本机 `/24`，候选只有通过 Grbl banner 校验后才写回缓存。不得绕过 banner 校验固定错误主机。

## 固件完成判据

- clean commit 可复现构建；编译和烧录日志归档。
- `hutuji.status` 返回 `connected/ready/authorized`，状态查询不破坏单写者。
- 下载长度或 CRC 错误时零字节下发。
- 普通绘图、换纸、断连恢复、abort、缺纸/卡纸均按协议实测。
- 对应逐台记录和发布清单已填写；否则只能称“开发中”，不能称“可出货”。

历史 USB Host/CH9350 否决依据见 `../docs/hardware.md`，不要重新尝试已否决路线。
