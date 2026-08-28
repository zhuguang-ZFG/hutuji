# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 本仓库文档与注释一律用**中文**（项目约定）。

## 这是什么

**hutuji** = AI 绘图自动换纸写字机：语音 → 云端 AI 生成 G-code → 写字机执行并自动换纸。

本仓库是**枢纽仓库**，只放 MCP 生成服务、协议文档、硬件说明（`docs/hardware.md`）、部署脚本、联调 SOP。**固件本体不在这里**——分散在两个独立 fork，且不向本仓库合并：

| 代码去哪找 | 本地路径 | 角色 |
|-----------|----------|------|
| 运动控制 / 换纸固件 | `D:/Users/Grbl_Esp32` | Grbl_Esp32（成熟商业现役固件；实时分支/HEAD 与板上证据看 `docs/worktree-inventory.md`，不得复制静态 SHA 当当前真值） |
| AI 交互 / 语音固件 | `D:/Users/xiaozhi-esp32` | xiaozhi-esp32（板型 `lichuang-dev` 立创实战派 ESP32-S3） |

不要假设任何固件源码在本仓库——运动控制去 Grbl_Esp32 查，AI 交互去 xiaozhi-esp32 查。

## 动手前必读（权威来源）

按顺序读这几份，它们是决策与契约的唯一真值，其它文件都是它们的展开：

0. `docs/agent-handoff.md` — **防呆页**（误判表、禁止重开项、默认下一刀）；不明所以先回这里
1. `docs/agent-anti-drift.md` — **防幻觉 / 禁乱改**；白名单 `docs/agent-edit-allowlist.json`；三仓矩阵 `docs/agent-constraint-matrix.md`；脏树 `docs/worktree-inventory.md`
2. `docs/agent-gate.md` — **本仓门禁定义**；结束前跑 `python scripts/agent_gate.py`
3. `docs/foundation-checklist.md` — ① 基础建设闭环勾选
4. `README.md` — 决策记录表（2026-07-24 逐项锁定，**7 条**；编号以此为准）
5. `AGENTS.md` — 三仓布局、硬规则、开发约定
6. `docs/protocol.md` — 板间链路协议 + G-code 契约（**v0.5**，②③ 共同契约，改动需两侧同步评审）。§9 是 Grbl 侧小改清单（完成须 fz pass + 烧录实机，禁止口头宣称）
7. `docs/hardware.md` — 链路/备用接线；**MK38**见 `docs/hardware/mk38-board.md`；**实战派 S3**见 `docs/hardware/lichuang-szpi-esp32s3-board.md`
8. `docs/release-readiness.md` — 当前真实进度；不得把“文档/代码已齐”说成“可出货”

## 架构要点（读多个文件才能拼出的大图）

**双芯片，禁止合并固件**：ESP32-S3 跑 xiaozhi（AI/语音/WiFi），独立 ESP32 跑 Grbl（运动+换纸）。

**板间通讯 = 方案 E：WiFi Telnet**（不是 USB Host，不是 CH9350，不是蓝牙数据链路）：
写字机 Grbl_Esp32 内置 ESP3D WebUI 开 **Telnet:23**，STA 连家用路由器（DHCP；静态租约仍是运维首选）；S3 由 xiaozhi WifiBoard 连同一路由器，做 **TCP 客户端**直连，按 NVS 缓存 IP → 子网扫描发现写字机，出厂态写字机由 S3 零接触代配户网（见 `docs/protocol.md` §1.5）。链路内即原串口字节流——115200 语义、行协议 `ok`/`error:NN`、banner、实时字符全部同构。
> 三次推翻的方案，别再往回走：① S3 原生 USB Host——实战派 C 口经板载 CH334F HUB 占为设备口，GPIO19/20 无排针引出；② CH9350 桥接——仅枚举 HID 类设备，不认写字机的 CH340（vendor-specific 串口）。备用链路仅方案 B（拆机 TTL，IO10/IO11），WiFi 实测抖动不可接受时才启用。

**哑管道原则**：G-code 全部在云端（京东云 VPS 的 MCP 服务）生成+转换完毕。S3 板只做**下载 + 校验 + 流控转发**，绝不生成/编辑/预览。

**行长上限是 254 字节 payload，不是 255**：`Protocol.h:34` 是 `LINE_BUFFER_SIZE 256`，但 `Protocol.cpp:68` 的溢出判定排在换行判定**之前**——第 255 个 payload 字节能进缓冲，紧随的换行符先撞上 `len == 255` 回 `error:11`，`:218` 的 `empty_line()` 把整行丢掉，**该行从未进 `execute_line()`**。上游 grblHAL 用 257 正是为绕开这个 off-by-one。校验器口径 `MAX_LINE_PAYLOAD_BYTES = 254`，**禁止改回 255**（旧口径会放行一种设备静默吃行的产物）。同理 **CRLF 行尾会让设备每行回两个 `ok`**（`\r`、`\n` 各触发一次 EOL，空行也回 ok），故 **S3 必须重切行、禁止原样透传下载字节**——透传会让在途窗口计数正向漂移。两条都在 `docs/protocol.md` §3。

**Grbl 错误码有数字与文本两种线上形态**：`$Errors/Verbose=1` 时 `Report.cpp:236` 回 `error: Line too long` 而非 `error:11`。只认数字不是降级而是**行为反转**（`error:8` 换纸推迟会变成整单失败）。该 `$`-设置存在 NVS、可被任何上位机改写。S3 侧解析走 `hutuji_recovery_core.h` 的 `ParseGrblErrorCode()`，两种形态由 `scripts/tests/test_hutuji_recovery_core.py` 的 host 编译单测钉住。

**奎享工作在负 X 坐标系**（唯一缓存产物 763 个 X 值里 762 个为负），我方硬拒负坐标（无限位开关，限幅是唯一安全防线）。故**奎享产物不能直接当回归基线**，需先平移到正象限；**不得为「对齐奎享」放宽负坐标限幅**。

**§5 大小/长度一律按「设备看到的字节」计，不按 Python 字符**：512KiB 上限是 S3 侧 PSRAM 缓冲的硬约束，S3 下载到的、`Content-Length` 与 CRC32 算的都是**磁盘原始字节**。故 `_read_generated_gcode` 必须 `read_bytes()` 并把 `len(raw)` 显式传给 `validate_gcode(text, total_bytes=…)`——**禁止改回 `read_text()`**：后者走 universal newlines 把 CRLF 塌成 LF，每行少算 1 字节。实测某 20846 行 CRLF 产物磁盘 524369 字节（超上限 81 字节）被判为未超并放行，回给 S3 的 `bytes` 少报 20846。单行 **254** 字节 payload 上限（Grbl `LINE_BUFFER_SIZE` 256 的换行 off-by-one，protocol §3）同理已按 `encode("ascii")` 计。

**换纸消息只做定点路由（别扩大修改面）**：原固件的换纸消息均走 `CLIENT_SERIAL`；§9 `e7f373f` 只把四处 `[PaperStatus] N` 结果码改为 `CLIENT_ALL`，其余 `[PaperAuto-N]` 进度仍只走 CH340。`[ESP901]` 的 `Changing=Off/On` 均已 Telnet 实测，真实 `[PaperStatus] 0` 到达亦已有纸验证（B-13 2026-07-31 / S4 2026-08-03）。S3 判换纸靠三层：自发 `M30` 自知 + `[ESP901]` 查 `Changing` + `?` 探活。CH340 串口仍是最全诊断口。

**Grbl 侧可小改（口径已变更）**：换纸机械/时序、运动、授权、U1-Grbl 协议**一律不改**（现役换纸相关：`M701`/`M704`/`M711`/`M712`/`M713`/`M721`/`M30` 等）；但**消息路由、状态字段、WebUI 网络层**等不影响商业出图行为的小加固**可接受**。`docs/protocol.md` §9 清单已以 `e7f373f` 提交、fz `standard` pass 并烧录；keepalive、`[ESP901]` Paper/`Changing=Off` 已实机验证，`Changing=On` 与有纸自动换纸亦已验证（B-13 2026-07-31 / S4 2026-08-03）。后续任何 Grbl 改动仍**必跑 fz 门禁**。

**画布与行程安全**：A4 纸 210×297mm。**2026-08-21 用户口径：X 为长边（297 方向）、Y 为宽边（210 方向），与旧文「X 短边 Y 长边」相反。** MCP 生成端限幅 X≤190 / Y≤190mm（四周 10mm 边距；Y 已由 277 收紧到物理宽边内，X 抬 277 须先有实机 `$$` 证明 $130=297），Grbl 行程记录为 `$130=210 $131=297`（待实机复核，若用户口径属实需对调）。**无限位开关**（引脚注释掉了），软限位/归零不可用，MCP 端校验是唯一安全防线。**坐标原点已验证**：左下角机械死点 = (0,0)，X+ 向右，Y+ 向前（远离操作者）。归零方式 = 手动推死点后上电。

## 转发状态机的关键坑

详见 `docs/protocol.md`（v0.5）。摘要：窗口化流控（512B character-counting 在途窗口，一行一应答配对，§3）、单写者（出图中只发 `?`，不发 M 命令）、Telnet 独占（`MAX_TLNT_CLIENTS=1`）、先下完再落笔（全量下载 + CRC32 + 授权门 → 才发第一字节）、abort ≠ 急停（换纸物理上打不断）。改转发逻辑前**必读原文**。

## 硬规则

- **本仓改动后**：跑 `python scripts/agent_gate.py`（默认 `hub`）。overall 非 pass 不得宣称本仓通过。定义见 `docs/agent-gate.md`。
- **改了 `D:/Users/Grbl_Esp32` 的任何代码后（含 §9 的消息路由/状态字段/keepalive 小改），必须自己跑 fz 门禁**：
  `python D:/Users/zhugu/fz/scripts/agent_gate.py`（先设好 `FZ_ROOT`/`GRBL_ROOT`）。overall 非 pass **不得声称修好**。细则见 `D:/Users/zhugu/fz/docs/AGENT_VIBE_CODING.md`。本仓 `--profile full` 的源码标记检查**不替代** fz。
- **改 Grbl 的边界**：换纸机械/时序、运动、授权、U1-Grbl 协议不改；**禁止**碰 `report_realtime_status()`（固定 `char[200]` + 无边界 `strcat`，有栈溢出风险）与 `report_state_text()`（状态字被现役商业上位机依赖）。
- **凭据禁入库且日志必须先脱敏**：`D:/Downloads/VPS.txt` 含 VPS 明文密码与 API key，绝不复制、引用、提交到任何仓库。MCP、bridge 与 journal 拉取脚本统一复用 `hutuji_mcp/redact.py`；不得在调用侧复制正则。第三方 root/uvicorn/httpx/httpcore handler 必须装共享 filter，先格式化再脱敏，并覆盖异常与 stack 文本；取证脚本必须在落盘前脱敏完整 URL、签名查询参数和凭据。
- **禁止自由发挥 / 幻觉 / 乱改**：见 `docs/agent-anti-drift.md`。不重开 USB Host/CH9350（「阿里云部署」2026-08-23 已由用户反转，见根 README 决策 #5）；不跳 `bringup-sop`；不把 MCP 组 0–4 当待办重做；不覆盖 `worktree-inventory.md` 记载的脏树；不为门禁绿改换纸常量；用户未指定时按 handoff §6。

## 构建与测试

Python ≥3.12（当前 3.13）。无 linter/formatter 配置（无 ruff/black/mypy）。无 `pyproject.toml`，纯 `requirements.txt` + 手动 venv。权威状态见 `docs/release-readiness.md`。

### 从零搭建（新克隆）

```bash
cd mcp-server
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt
```

`.venv` 已 gitignore；**未建 venv 则门禁 pytest/e2e 层直接 fail**。

### 常用命令（均在 `mcp-server/` 下执行）

| 场景 | 命令 |
|------|------|
| 全量测试 | `.venv/Scripts/python -m pytest -q` |
| 单文件 | `.venv/Scripts/python -m pytest tests/test_injection.py -q` |
| 单用例 | `.venv/Scripts/python -m pytest -k <名字> -q` |
| e2e 闭环 | `.venv/Scripts/python scripts/e2e_local.py`（须输出 `PASS`） |
| 起开发服务 | `.venv/Scripts/python -m uvicorn hutuji_mcp.server:app --host 127.0.0.1 --port 8300` |
| 起假 Grbl | `.venv/Scripts/python -m hutuji_mcp.grbl_sim` |

> **`-m uvicorn` 不读 `HUTUJI_HOST`**，只有 `python -m hutuji_mcp.server` 才走 `settings.host`；`--host` 漏写即监听 `0.0.0.0`。`HUTUJI_HOST` 本身已收紧为**只允许 loopback**（`config.py` import 期校验），写成 `0.0.0.0` 不再被静默忽略，而是让服务起不来。监听面裸开时 `/mcp` 另有应用层兜底 `server.LoopbackOnlyMcpGuard`（按 ASGI 对端地址拒非 loopback、回 404），但 `/files` 会绕开 nginx 限速/ACL 直曝，故 `--host` 仍不能漏。

### 本仓门禁

仓库根执行 `python scripts/agent_gate.py` → `results/hutuji_gate_last.md`。**退出码是唯一真值**：`results/.hutuji_gate_running` 在场 = 上次没跑完、其间报告不可信；报告头 `git_head` 与当前 HEAD 不符即过期。

| Profile | 内容 |
|---------|------|
| `docs` | 仅文档锚点/一致性检查 + 文档 `path:line` 越界与行内容指纹（基线 `docs/agent-line-refs-baseline.json`，改动引用后须 `--accept-line-refs` 重登） |
| `hub`（默认） | `docs` + 外仓文档 `path:line` 校验 + pytest + e2e（缺 fork 时该层 pass 但 detail 自陈「无可校验」） |
| `full` | `hub` + 外仓引用要求三仓至少一个在位（全缺即 fail） + Grbl §9 源码标记（**必须设 `GRBL_ROOT`，未设时该层直接 fail、不再 skip**）；且仍须自己另跑 fz |

## 外部参考证据纪律

涉及社区、GitHub、上游文档、SDK 或硬件资料时，先查官方文档/官方仓库，再查高质量社区；对影响设计或结论的依据记录 URL、版本或 commit、访问日期、适用板型与范围、本地复核命令/结果、限制和未验证项，落在相关设计段落或明确链接的 findings。区分上游与本地 fork、源码与板上/生产运行态；外部资料仅作证据或参照，不能覆盖本仓 `README.md`、`docs/protocol.md`、`docs/release-readiness.md` 真值。网络不可用时使用本地镜像或仓内 findings 必须标来源层级和快照日期，不得把社区共识写成实机证据。

## BLE 只读诊断辅助面

正式 Telnet/G-code 契约仍为 v0.5，BLE 独立使用 `BLE-DIAG schema v1`。阶段 A 已实现为仅公开 6 字节粗粒度广播且不注册 GATT；阶段 B 才启用本地维护确认、LE Secure Connections + MITM bonding、22 字节 `READ` 快照；阶段 C 由手机/PC 消费 RSSI 显示 `near/unknown`，S3 不持续扫描。默认镜像仍关闭，不替代 Telnet、不承载 G-code/控制命令；字段、NVS 双槽、回滚和共存门槛见 `docs/protocol.md` §1.4。阶段 A 当前源码（`f1aa0e7`，含 pending event 合并/轮换优先级加固）已过 BLE 专测 18/18、全 host 93/93、默认/启用构建，且启用镜像 `5a68fafe…80d6` 已刷 COM14 完成窄范围复验（4 分钟稳定、两轮 6 字节广播、NVS 逐字节回滚）；期间 `minimal sram: 1647` 低于 24KiB 门槛但缺 BLE-off 对照，未判回归。共存量化门槛、功耗/温升和阶段 B/C 仍未完成。

### 环境变量

关键变量（全表见 `mcp-server/README.md`）：`HUTUJI_LLM_API_KEY`（未配置时 `hutuji_draw` 报错）、`HUTUJI_LLM_BASE_URL`/`_MODEL`、`HUTUJI_PUBLIC_BASE_URL`（返回给 S3 的下载前缀）、`HUTUJI_OUTPUT_TTL_SECONDS`（默认 600）。配置全走环境变量，**无 `.env` 文件**。

### 网络安全

公网只经 nginx 暴露 `/files/`；`/mcp` 在 `deploy/nginx-hutuji.conf` 里显式 404，安全组不放行 8300。小智云工具发现冒烟：仅当前终端设 `MCP_ENDPOINT` 后跑 `scripts/cloud_bridge_smoke.py`（接入点禁入库）。

设备侧 MCP 可经官方消息 API 直调：仓库根运行 `python scripts/xiaozhi_console.py device-tools` 或 `device-call hutuji.status`。它使用独立的 `XIAOZHI_DEVICE_TOKEN`（设备自定义主题 URL 的 token 参数），不是控制台 JWT；脚本只允许七个 `hutuji.*` 工具并脱敏能力 URL/凭据。

### ② S3 固件补丁（不在本仓构建）

在 `D:/Users/xiaozhi-esp32` 用 ESP-IDF 构建 `python3 scripts/release.py lichuang-dev`。挂点是板级 `InitializeTools()`，参照 `press_to_talk_mcp_tool.cc` 做独立工具类，**板级行为不进核心模块**。说明见 `firmware/`。

- **版本用 v6.0.1，不是 6.0.2/5.5.2**：`lichuang-dev` 在 xiaozhi `docs/esp-idf-6-migration.md`（板级矩阵行）记为 **IDF 6.0.1 full build passed**——那才是这块板实测过的版本。
- **IDF 已在本机，别重装（省 2–4GB）**：源码 `C:\Users\zhugu\.espressif\v6.0.1\v6.0.1\esp-idf`，工具链 `C:\Espressif`。`C:\Users\zhugu\.espressif` 是指向 `D:\zhugu-home\.espressif` 的**符号链接**，`du` 显示 0 字节不代表"没装"。
- **缺的只有 Python venv，且 Git Bash 装不了**：须在 **PowerShell** 跑 `$env:IDF_PATH="…\esp-idf"; .\install.ps1 esp32s3`。Git Bash / MSys 下 IDF 直接拒绝（`ERROR: MSys/Mingw is not supported`）。

### 串口识别

实战派 S3 板载 CH334F HUB + CH340K 做 UART 桥，故 **S3 会以 `USB-SERIAL CH340K` 枚举**——「CH340 是写字机最全诊断口」不能反推「CH340 就是写字机」。判定一律看**被动串口日志自证**（S3 打 `Project name: xiaozhi`），不要用 `esptool chip_id`（会打断运行中固件）。

### 京东云部署

`deploy/` + `docs/deployment.md`。**2026-07-27 HTTPS 已通**：`hutuji-mcp` + `hutuji-cloud-bridge` + nginx + **Cloudflare Tunnel**（`hutuji.donglicao.com`，Let's Encrypt 证书，绕过京东云 ICP 拦截）。`HUTUJI_PUBLIC_BASE_URL=https://hutuji.donglicao.com`。权威进度见 `docs/release-readiness.md`。

- **更新代码用 scp，不能 `git pull`**：`/opt/hutuji` 无 `.git`。改完推文件到 `/opt/hutuji/mcp-server/hutuji_mcp/`，属主保持 `hutuji`，再 `systemctl restart hutuji-mcp`。
- **生图后端**：正式出图只允许图片路线。同题完整 `hutuji_draw` 事务由独立任务持有；MCP waiter 取消不得杀掉任务或释放限流名额，后续同题重试复用生图、描摹、vpype、产物和结果。Quick Draw 命中跳过付费限流名额，但服务异步 G-code 转换仍受全进程最多 2 个 vpype 子进程的资源闸保护。位图层另有 10 分钟/16 项缓存与覆盖生图+描摹的 single-flight。未命中时商汤、Agnes、imagefree 与可选硅流 racer 并发对冲；去重的是客户端重试，不取消同一逻辑生成轮内既定的多后端对冲。商汤瞬态 500 快速重试一次；同 SVG G-code 缓存跳过 vpype；CDN 下载失败不得重新生成。禁止模型直写 SVG；Pollinations 已实测否决；聊天中粘贴过的 key 一律视为泄露。
- **生产 venv 精简装，没有 pytest**：只能跑 `scripts/e2e_local.py` 与直接 import 校验函数实测。
- 改动前先备份；候选从 Git blob 导出为 LF，并逐文件核对哈希与 CR=0（2026-08-20 本机实测 `git archive` 会受 CRLF 转换影响，不能直接当生产候选）。代码/依赖变更后显式重启 `hutuji-mcp` 与共用包/venv 的 `hutuji-cloud-bridge`；unit 变更先 `daemon-reload`，`enable --now` 不会重启已 active 服务。当前生产应用代码基线为 `c2cdbd4`（2026-08-23：共享日志脱敏 + 第三方 handler 过滤 + health pull 落盘前脱敏），三文件远端 SHA-256 与 Git blob 相符、CR=0；两服务 active，`/bridge-health` 200，当前 30 项 preflight `pass=30 fail=0 expected=30`。真实语音 HIL 尚未执行；完整部署记录与 nginx reload、mtime、监听身份、bridge restart recency 约束见 `docs/deployment.md` 与 `deploy/README.md`。
- **`limit_req` 的 key/zone 变更必须 `restart nginx`，`reload` 会静默丢弃**（2026-08-08 生产实测并已隔离复现全过程）：三道常规检查全部看不见这个失败——`nginx -t` 是新进程、无继承共享内存区，必过；`systemctl reload` 只发信号不等受理，恒 `status=0/SUCCESS`；master 拒绝后只写 `error.log` 的 `[emerg] limit_req ... uses the "$hutuji_client_ip" key while previously it used the "$binary_remote_addr" key`，并**继续用旧配置服务**，worker 不换、`is-active` 仍 active。生产实际跑了 37 分钟旧配置，而当时 19 项 preflight **全绿**——其余 19 项在旧配置下同样成立，唯一变化的限速身份没有任何判据覆盖。隔离复现（独立 nginx 实例，`/tmp/nginx-repro`，已清理）四项全部再现：`-t` successful、`-s reload` exit 0、`[emerg]` 同文言、reload 前后 **PID 不变**，且探针**不进**自定义日志而落进 http 级默认 combined 日志；restart 后探针立刻进自定义日志。可靠判据只有两个：worker 启动时间（比配置 mtime 新）与专用日志真收到带标记的探针；`stat` 取配置 mtime 必须 `-L`，否则读到 `ln -sfn` 的时刻（实测差一天：符号链接 08-07 17:31:15 vs 配置本体 08-08 20:54:01）。

- **FastAPI 自动文档端点必须在应用层关掉**：`FastAPI(...)` 须显式传 `docs_url=None`/`redoc_url=None`/`openapi_url=None`。不传时默认自动挂 `/docs`、`/redoc`、`/openapi.json`、`/docs/oauth2-redirect`，实测四条全回 200。公网当前访问不到，但那份安全**完全来自 nginx 兜底**（443 的 `location / { return 404; }`），应用层零防御——`location /` 一旦放开、或给 `:8300` 开直连调试，攻击面即静默打开。对比 `/mcp` 有三层保护，这四条原本只有第一层。preflight 第 18 项直连 `:8300` 验它们必须 404（**不能经 nginx 探**，那证不到应用层）。
- **公网兜底路径曾是自指 301 死循环，不是 404**（2026-08-07 实测并修复）：Cloudflare Tunnel 的 `service: http://localhost:80` 使**全部**公网流量落进 nginx `:80` 块，`:443` 块的 `location / { return 404; }` 隧道永远走不到；旧 `:80` 兜底无条件 `return 301 https://hutuji.donglicao.com$request_uri`，边缘拿到 301 后再回源 `:80`，于是公网 `/docs`、`/redoc`、`/openapi.json`、任意随机路径全是 `301 Location: 自身`，`curl -L` 也停在 301。危害不是暴露而是**判据造假**：文档长期写的「公网非白名单路径须 404」从未成立，却因 `301 ∉ 2xx` 骗过一切「须失败」式复验（第 18 项之所以看起来「安全」也靠这层假象）。修法是 `:80` 兜底按 `$http_x_forwarded_proto` 分流——隧道回源（实测确带 `X-Forwarded-Proto: https`）`return 404`，真明文直连仍 `301` 强制 HTTPS。preflight 第 19 项必须**同时**验两个方向：只验 404 会静默丢掉明文强制 HTTPS，只验 301 就是回到死循环。
- **preflight 曾有三处「文件对了但跑的不对」的盲区，现已各补一项**：
  - **生成链（`check_generate_chain`）**：旧版 15 项没有一条执行过部署的 `hutuji_mcp` 代码——下载探针用 `printf "G21\nG90\nF8000\n"` 人工拼产物，那份内容连 `validate_gcode` 自己都不接受，所以只证明了「nginx 能按字节发文件」。已实测复现：把 `hutuji_gwrite.toml` 移走后 `import hutuji_mcp.server` 仍成功、服务保持 active、`/mcp` 与 `/files` 照常响应，而真实 `svg_to_gcode()` 报 `vpype 转换失败（exit 2）`——即 `pass=15 fail=0` 与「一画就整单失败」可以同时成立。根因是 `_PROFILE_PATH` 只在请求期被 `_vpype_command()` 用到，import 期不加载不校验，`systemctl active` 抓不到；`vpype` 更是 subprocess、从不 import，装坏同样只在请求期暴露。现在 `check_generate_chain` 用生产 venv 真跑 `svg_to_gcode()`，`check_download` 拿这份真产物验下载契约。**探针必须用 `/opt/hutuji/mcp-server/.venv/bin/python`，不能用系统 `python3`**：`_vpype_cmd()` 按 `sys.executable` 同目录找 vpype，换解释器即换掉被验对象。
  - **位图描摹链（`check_bitmap_chain`）**：与生成链同款的 fail-open——skimage/sknw/imageio 是 `trace_bitmap_to_polylines` 里的延迟 import，缺失时 `import hutuji_mcp.server` 成功、服务 active、`gcode_gen --self-test` 绿、preflight 全绿，只有真实描摹炸（2026-08-09 本机拦掉 imageio 实测复现）。现在 unit 有第二条 `ExecStartPre`（沙箱内 `bitmap_svg --self-test`，离线合成探针 PNG 不碰计费生图 API），preflight 用同一入口真跑当前落盘代码，堵「scp 了坏依赖但没 restart」时 ExecStartPre 记录仍绿的洞。
  - **unit 漂移（`check_units_match_templates`）**：`systemctl is-active` 只证明「在跑」，不证明跑的是哪一版 unit——`--host 127.0.0.1`、`ProtectSystem=strict`、`SystemCallFilter` 等加固项可全部缺失而照样 active（监听面另有第 5/6 项覆盖，加固项原本无人验）。两个 unit 与 nginx 的安装方式完全同构（都从 `/opt/hutuji/deploy/` 复制到系统目录），却只有 nginx 有一致性判据。现在 `check_units_match_templates` 对两个 unit 各查 LF 归一化 md5 与模板一致、`NeedDaemonReload=no`、`FragmentPath` 指向 `/etc/systemd/system/`、**无 drop-in**；对 MCP 还查 `ExecStartPre` 的实际退出结果为成功，并要求主进程启动时间不早于已部署 unit、包内 Python 代码与 profile。这样既抓漏 `daemon-reload`，也抓 reload 后未 restart 的旧进程。
- **preflight 无 skip 状态**：输出 `pass=N fail=N expected=N`，放行判据 `PASS==EXPECTED_CHECKS && FAIL==0`。历史 `OPTIONAL_SKIP` 与 pass 等价放行，使「明文 `:80 /files` 须 403」可从未被验证却报全绿，已删除；**禁止重新引入 skip**，取不到非 loopback IPv4 一律判 fail。**探 8300 用 `curl`，别用 bash 的 `/dev/tcp`**（Git Bash 下会假阳性）。

## 路线图（当前进度）

1. **① 仓库奠基** — ✅ 文档地图、门禁、deploy 模板；见 `foundation-checklist.md`
2. **② 哑管道** — 🟡 实时分支/HEAD 与板上镜像证据以 `docs/worktree-inventory.md` 为准（不钉静态 SHA，防腐）；触摸、七工具、Telnet、授权、Idle 与控制抽屉低层动作有部分 HIL，2026-08-28 唤醒/Grbl 主屏指示（聆听扫光/胶囊变色/说话钮变绿/顶栏三级圆点）已刷 COM14，用户照片确认 Grbl 绿点与顶栏上屏。缺预览确认/取消、暂停/继续/停止/重画/试笔五个屏幕动作、纸面画质目视与断连恢复 `paper_active` 窗口 abort 复测。
3. **③ 智能端** — 🟡 HTTPS、京东云 MCP+桥接常驻和真实语音出图已有证据；SPEED-01/设备 PNG 预览运行态已部署，仓库实现见当前 HEAD。剩余设备预览/控制人机验收与生产签核。
4. **④ 出货** — 🔴 剩余 HIL、生产与合规签核未闭合。

> **不可出货**。默认下一刀见 `docs/agent-handoff.md` §6：当前 Waveshare UI HIL → 断连恢复 `paper_active` 窗口 abort 复测 → 生产与合规签核。AI 虚实交替与异步生成+完成推送均已于 2026-08-28 闭环（后者当日生产启用+屏幕目视确认），不作默认待办重做。

## 约定

- 修改项目结构、约定或工作流后，同步更新本文件、`README.md`、`AGENTS.md`、`docs/agent-gate.md`，以及 `.cursor/rules/hutuji-agent-rails.mdc`（`alwaysApply` 硬轨，与本文件的「禁止重开 / 禁乱改 / 门禁 / 默认下一刀」同源，改口径时勿漏）。
- 项目记忆/决策日志：`C:/Users/zhugu/.kimi-code/memory/projects/hutuji.md`。
