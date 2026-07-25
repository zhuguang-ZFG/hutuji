# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 本仓库文档与注释一律用**中文**（项目约定）。

## 这是什么

**hutuji** = AI 绘图自动换纸写字机：语音 → 云端 AI 生成 G-code → 写字机执行并自动换纸。

本仓库是**枢纽仓库**，只放 MCP 生成服务、协议文档、硬件说明（`docs/hardware.md`）、部署脚本、联调 SOP。**固件本体不在这里**——分散在两个独立 fork，且不向本仓库合并：

| 代码去哪找 | 本地路径 | 角色 |
|-----------|----------|------|
| 运动控制 / 换纸固件 | `D:/Users/Grbl_Esp32` | Grbl_Esp32（成熟商业现役固件，分支 `fix/panel-hold-after-align-116687e`） |
| AI 交互 / 语音固件 | `D:/Users/xiaozhi-esp32` | xiaozhi-esp32（板型 `lichuang-dev` 立创实战派 ESP32-S3） |

不要假设任何固件源码在本仓库——运动控制去 Grbl_Esp32 查，AI 交互去 xiaozhi-esp32 查。

## 动手前必读（权威来源）

按顺序读这几份，它们是决策与契约的唯一真值，其它文件都是它们的展开：

1. `README.md` — 决策记录表（2026-07-24 逐项锁定，**7 条**；编号以此为准）
2. `AGENTS.md` — 三仓布局、硬规则、开发约定
3. `docs/protocol.md` — 板间链路协议 + G-code 契约（**v0.5**，②③ 共同契约，改动需两侧同步评审）。§9 是待实施的 Grbl 侧改动清单
4. `docs/hardware.md` — 关键硬件事实 + 备用方案 B（拆机 TTL）与已否决方案（CH9350 / USB Host）的留档依据
5. `docs/release-readiness.md` — 当前真实进度；不得把“文档/代码已齐”说成“可出货”

## 架构要点（读多个文件才能拼出的大图）

**双芯片，禁止合并固件**：ESP32-S3 跑 xiaozhi（AI/语音/WiFi），独立 ESP32 跑 Grbl（运动+换纸）。

**板间通讯 = 方案 E：WiFi Telnet**（不是 USB Host，不是 CH9350，不是蓝牙数据链路）：
写字机 Grbl_Esp32 内置 ESP3D WebUI 开 **Telnet:23**，STA 连家用路由器拿**固定 IP**；S3 由 xiaozhi WifiBoard 连同一路由器，做 **TCP 客户端**直连。链路内即原串口字节流——115200 语义、行协议 `ok`/`error:NN`、banner、实时字符全部同构。
> 三次推翻的方案，别再往回走：① S3 原生 USB Host——实战派 C 口经板载 CH334F HUB 占为设备口，GPIO19/20 无排针引出；② CH9350 桥接——仅枚举 HID 类设备，不认写字机的 CH340（vendor-specific 串口）。备用链路仅方案 B（拆机 TTL，IO10/IO11），WiFi 实测抖动不可接受时才启用。

**哑管道原则**：G-code 全部在云端（京东云 VPS 的 MCP 服务）生成+转换完毕。S3 板只做**下载 + 校验 + 流控转发**，绝不生成/编辑/预览。

**Telnet 收不到换纸消息（v0.5 勘察纠错，别再照旧假设）**：`PaperSystem.cpp` 63 处 `grbl_msg_sendf` 路由**全为 `CLIENT_SERIAL`，`CLIENT_ALL` 出现 0 次**——`[PaperAuto-N]` 进度、`[PaperStatus] N` 结果码**S3 一个字节也收不到**。Telnet 只收 `ok`/`error:NN`、`?` 的 `<...>` 状态行、banner。故 S3 判换纸靠三层：自发 `M30` 自知 + `[ESP901]` 查 `Changing`（需 §9 小改）+ `?` 探活（已验证换纸期可响应）。CH340 串口仍是最全诊断口。

**Grbl 侧可小改（口径已变更）**：换纸机械/时序、运动、授权、U1-Grbl 协议**一律不改**（现役换纸相关：`M701`/`M704`/`M711`/`M712`/`M713`/`M721`/`M30` 等）；但**消息路由、状态字段、WebUI 网络层**等不影响商业出图行为的小加固**可接受**——当前待实施清单见 `docs/protocol.md` §9（keepalive + `[PaperStatus]` 路由 + `Changing` 字段，约 12 行）。**改后必跑门禁**。

## 转发状态机的关键坑（protocol.md 详列）

- **就绪探活**：Telnet 不保证连上就有 banner，故 TCP 建连后 S3 **主动发 `$I`，收到 `[VER:…]`/`[OPT:…]` 才算就绪**，此前不得发任何 G-code。banner `Grbl 1.3a ['$' for help]` 语义改为**对端复位**（欠压等）：收到就复位状态机、当前任务置 error。
- **严格逐行流控**：发一行、等 `ok\r\n` 才发下一行，无 character-counting，禁止预发抢跑。换纸行的 ok 可能阻塞 ≥60s，期间禁发 G0–G3（回 `error:8`）。
- **单写者铁律**：`is_realtime_command` 只认 `0x18`/`?`/`~`/`!`（走旁路不吃 ok）；`M704`/`[ESP901]` 是**普通命令、进 client_buffer 吃一个 `ok`**——出图中发它们会**打乱 ok 配对**。故出图中 status **只发 `?`**；且 S3 侧任何时刻**只允许一个逻辑往 Telnet 写字节**（draw 转发与 status 查询必须串行化，否则字节在 TCP 流里交错拼成乱码行）。
- **Telnet 独占**：`MAX_TLNT_CLIENTS=1` 是固件硬上限。PC telnet 与 S3 **互斥**，PC 观测一律走 CH340 串口。
- **先下完再落笔**：全量下载 + 长度/CRC32 校验 + 授权门**全部通过后**才发第一个字节（防「静默半成品纸」）。重发用 PSRAM 缓存，**不重新下载**（URL 可短时效）。
- **授权前置**：未授权芯片 G0–G3 全回 `error:110`（只拦 G0–G3，`$I`/`M...` 照常）；`draw` 开画前查授权门。S3 不自动授权、不自动 `$X` 解锁、不在换纸开始 8s 窗口内发 `0x18`。
- **abort 不是急停**：换纸一旦启动**物理上打不断**（`0x18` 被吞且 ignored 回执只发 SERIAL）。abort 分状态：换纸中诚实回报「无法即停、完成后即停」，不硬发 `0x18`。

## 硬规则

- **改了 `D:/Users/Grbl_Esp32` 的任何代码后（含 §9 的消息路由/状态字段/keepalive 小改），必须自己跑门禁**：
  `python D:/Users/zhugu/fz/scripts/agent_gate.py`（先设好 `FZ_ROOT`/`GRBL_ROOT`）。overall 非 pass **不得声称修好**。细则见 `D:/Users/zhugu/fz/docs/AGENT_VIBE_CODING.md`。
- **改 Grbl 的边界**：换纸机械/时序、运动、授权、U1-Grbl 协议不改；**禁止**碰 `report_realtime_status()`（固定 `char[200]` + 无边界 `strcat`，有栈溢出风险）与 `report_state_text()`（状态字被现役商业上位机依赖）。
- **凭据禁入库**：`D:/Downloads/VPS.txt` 含 VPS 明文密码与 API key，绝不复制、引用、提交到任何仓库。

## 构建与测试

本仓库已有可构建的 `mcp-server/`（③）。权威状态见 `docs/release-readiness.md`。

- **③ MCP（本仓）**：在 `mcp-server/` 执行
  - `.venv/Scripts/pip install -r requirements-dev.txt`
  - `.venv/Scripts/python -m pytest -q`（契约/注入/限流等）
  - `.venv/Scripts/python scripts/e2e_local.py`（须输出 `PASS`）
  - 小智云工具发现：仅当前终端设 `MCP_ENDPOINT` 后跑 `scripts/cloud_bridge_smoke.py`（接入点禁入库）
- **② S3 固件补丁**：在 `D:/Users/xiaozhi-esp32` 用 ESP-IDF 构建（IDF ≥5.5.2，仓要求 v6.0.2）：
  `python3 scripts/release.py lichuang-dev`。挂点是板级 `InitializeTools()`，参照 `press_to_talk_mcp_tool.cc` 做独立工具类，**板级行为不进核心模块**。说明见 `firmware/`。
- **京东云部署模板**：`deploy/` + `docs/deployment.md`（尚未实机验收）。

## 路线图（当前进度）

1. **① 仓库奠基**（协议、文档地图、SOP、京东云 deploy 模板）— ✅ 已齐；实时状态见 `docs/release-readiness.md`
2. **② 哑管道**（S3 设备侧 MCP 工具 `hutuji.draw/status/abort` + HTTP 全量下载校验 + Telnet 流控转发）— 补丁在 xiaozhi fork 分支 `feat/hutuji-usb-pipe`（分支名沿用历史，链路已改方案 E），说明见 `firmware/m1-usb-pipe.md`。**开工前先实施 `docs/protocol.md` §9 的 Grbl 侧小改**（否则 S3 收不到换纸状态）；协议 v0.5 的自动化动作均标 🔬 待冒烟实测。
3. **③ 智能端**（MCP 生成服务：LLM → SVG → G-code，官方云联调，**部署京东云 VPS** `117.72.118.95`）— 本地基础加固与小智云 `initialize/tools/list` 已通过，京东云 TLS/实际工具调用待联调，权威验收项见 `mcp-server/README.md`「实施验收清单」

## 约定

- 修改项目结构、约定或工作流后，同步更新本文件、`README.md`、`AGENTS.md`。
- 项目记忆/决策日志：`C:/Users/zhugu/.kimi-code/memory/projects/hutuji.md`。
