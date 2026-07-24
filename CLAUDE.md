# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 本仓库文档与注释一律用**中文**（项目约定）。

## 这是什么

**hutuji** = AI 绘图自动换纸写字机：语音 → 云端 AI 生成 G-code → 写字机执行并自动换纸。

本仓库是**枢纽仓库**，只放 MCP 生成服务、协议文档、接线图、部署脚本、联调 SOP。**固件本体不在这里**——分散在两个独立 fork，且不向本仓库合并：

| 代码去哪找 | 本地路径 | 角色 |
|-----------|----------|------|
| 运动控制 / 换纸固件 | `D:/Users/Grbl_Esp32` | Grbl_Esp32（成熟商业现役固件，分支 `fix/panel-hold-after-align-116687e`） |
| AI 交互 / 语音固件 | `D:/Users/xiaozhi-esp32` | xiaozhi-esp32（板型 `lichuang-dev` 立创实战派 ESP32-S3） |

不要假设任何固件源码在本仓库——运动控制去 Grbl_Esp32 查，AI 交互去 xiaozhi-esp32 查。

## 动手前必读（权威来源）

按顺序读这几份，它们是决策与契约的唯一真值，其它文件都是它们的展开：

1. `README.md` — 决策记录表（2026-07-24 逐项锁定，7 条）
2. `AGENTS.md` — 三仓布局、硬规则、开发约定
3. `docs/protocol.md` — 板间链路协议 + G-code 契约（**v0.4**，②③ 共同契约，改动需两侧同步评审）
4. `docs/hardware.md` — 关键硬件事实 + 备用方案 B（拆机 TTL）与已否决方案（CH9350 / USB Host）的留档依据

## 架构要点（读多个文件才能拼出的大图）

**双芯片，禁止合并固件**：ESP32-S3 跑 xiaozhi（AI/语音/WiFi），独立 ESP32 跑 Grbl（运动+换纸）。

**板间通讯 = 方案 E：WiFi Telnet**（不是 USB Host，不是 CH9350，不是蓝牙数据链路）：
写字机 Grbl_Esp32 内置 ESP3D WebUI 开 **Telnet:23**，STA 连家用路由器拿**固定 IP**；S3 由 xiaozhi WifiBoard 连同一路由器，做 **TCP 客户端**直连。链路内即原串口字节流——115200 语义、行协议 `ok`/`error:NN`、banner、实时字符全部同构。
> 三次推翻的方案，别再往回走：① S3 原生 USB Host——实战派 C 口经板载 CH334F HUB 占为设备口，GPIO19/20 无排针引出；② CH9350 桥接——仅枚举 HID 类设备，不认写字机的 CH340（vendor-specific 串口）。备用链路仅方案 B（拆机 TTL，IO10/IO11），WiFi 实测抖动不可接受时才启用。

**哑管道原则**：G-code 全部在云端（阿里云 VPS 的 MCP 服务）生成+转换完毕。S3 板只做**下载 + 校验 + 流控转发**，绝不生成/编辑/预览。

**这条 Telnet 链路同样承载全部异步输出**——`[MSG:]`、`[PaperAuto-*]` 换纸进度、`?` 状态报告都混在这条流上（`grbl_msg_sendf` 按 client 路由，`CLIENT_ALL` 消息 Telnet 也收）。S3 解析器必须容忍全部异步输出类型（见 protocol §2）。写字机 CH340 串口仍是最全诊断口，但联调数据链路走 Telnet。

**Grbl 侧一律不改**：换纸机械与逻辑（`PaperSystem.cpp` 的 M701/M711/M712/M713/M30）是成熟商业方案，已满足全部对接需求。

## 转发状态机的三个关键坑（protocol.md 详列）

- **就绪探活**：Telnet 不保证连上就有 banner，故 TCP 建连后 S3 **主动发 `$I`，收到 `[VER:…]`/`[OPT:…]` 才算就绪**，此前不得发任何 G-code。banner `Grbl 1.3a ['$' for help]` 语义改为**对端复位**（欠压等）：收到就复位状态机、当前任务置 error。
- **严格逐行流控**：发一行、等 `ok\r\n` 才发下一行，无 character-counting，禁止预发抢跑。换纸行的 ok 可能阻塞 ≥60s，期间禁发 G0–G3（回 `error:8`）。
- **授权前置**：未授权芯片 G0–G3 全回 `error:110`；banner 须含 `[License] Authorized`。S3 不自动授权、不自动 `$X` 解锁、不在换纸开始 8s 窗口内发 `0x18`。

## 硬规则

- **改了 `D:/Users/Grbl_Esp32` 的 G-code/运动/U1-Grbl 协议代码后，必须自己跑门禁**：
  `python D:/Users/zhugu/fz/scripts/agent_gate.py`（先设好 `FZ_ROOT`/`GRBL_ROOT`）。overall 非 pass **不得声称修好**。细则见 `D:/Users/zhugu/fz/docs/AGENT_VIBE_CODING.md`。
- **凭据禁入库**：`D:/Downloads/VPS.txt` 含 VPS 明文密码与 API key，绝不复制、引用、提交到任何仓库。

## 构建与测试

本仓库目前**只有文档，无可构建代码**。落地后补记于此：

- `mcp-server/`（③，Python 服务，待建）：构建/测试命令待补。
- S3 固件补丁（②）：在 `D:/Users/xiaozhi-esp32` 用 ESP-IDF 构建（IDF ≥5.5.2，仓要求 v6.0.2）：
  `python3 scripts/release.py lichuang-dev`。挂点是板级 `InitializeTools()`，参照 `press_to_talk_mcp_tool.cc` 做独立工具类，**板级行为不进核心模块**。本机无 IDF/ESP 环境，固件为评审级自查。

## 路线图（当前进度）

1. **① 仓库奠基**（协议文档、目录结构）— 已完成首个提交
2. **② 哑管道**（S3 固件补丁：设备侧 MCP 工具 `hutuji.draw/status/abort` + HTTP 下载 + 串口流控转发）— 补丁在 xiaozhi fork 分支 `feat/hutuji-usb-pipe`，说明见 `firmware/m1-usb-pipe.md`
3. **③ 智能端**（MCP 生成服务：LLM → SVG/字体笔画 → G-code，官方云联调，部署阿里云）

## 约定

- 修改项目结构、约定或工作流后，同步更新本文件、`README.md`、`AGENTS.md`。
- 项目记忆/决策日志：`C:/Users/zhugu/.kimi-code/memory/projects/hutuji.md`。
