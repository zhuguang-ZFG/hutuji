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
| M1 Telnet 连接、发现、探活 | 🟡 已烧录实测 | NVS IP 缓存 + `/24` 扫描，候选须通过 Grbl banner 校验；双端 keepalive 已验证 |
| M2 下载/CRC/授权/逐行流控 | 🟡 关键闭环已实测 | 全量下载、长度/CRC、授权门、逐行 `ok` 配对和 fresh-`Idle` 完成判定已回归 |
| abort/单写者 | 🟡 关键闭环已实测 | 非换纸 abort 从 `streaming/Run,Z=5` 恢复至 `aborted/Idle,Z=0`；完整断连重发已编码/构建，仍未实测 |
| 硬件冒烟与多页换纸 | 🔴 未闭合 | S3 绘图后显式 `M30` 与断连废纸重画已编码；`Changing=On`、自动换纸、断连重画与 App 语音仍未实测 |

当前源码基线是 `05f4563`，5 个 S3 源文件已提交；IDF 6.0.1 构建产物 `0x2ccde0`、SHA256 `5C79178B...92ABD`，但尚未烧录该 commit。COM6/COM9 证据来自提交前候选镜像，不得扩大为断连或换纸验收。

## 开发与构建

1. 在 `D:/Users/xiaozhi-esp32` 确认目标分支和基线，保存 `git status --short`。
2. 开工前读该仓自身 `AGENTS.md`，使用已验证的 ESP-IDF **v6.0.1**。
3. 构建入口：`python3 scripts/release.py lichuang-dev`；若上游脚本要求名称参数，以该仓 `--help` 为准。
4. 产物必须记录：fork commit、是否 dirty、IDF 版本、板型、sdkconfig、构建命令、二进制 SHA256。
5. 烧录前确认 S3 与写字机在同一网络；连接器会先试 NVS 缓存，再扫描本机 `/24`，候选只有通过 Grbl banner 校验后才写回缓存。不得绕过 banner 校验固定错误主机。

## 固件完成判据

- clean commit 可复现构建；编译和烧录日志归档。
- `hutuji.status` 返回 `connected/ready/authorized`，状态查询不破坏单写者。
- 下载长度或 CRC 错误时零字节下发。
- 普通绘图、换纸、断连恢复、abort、缺纸/卡纸均按协议实测。
- 对应逐台记录和发布清单已填写；否则只能称“开发中”，不能称“可出货”。

历史 USB Host/CH9350 否决依据见 `../docs/hardware.md`，不要重新尝试已否决路线。
