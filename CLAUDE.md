# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 本仓库文档与注释一律用**中文**（项目约定）。

## 这是什么

**hutuji** = AI 绘图自动换纸写字机：语音 → 云端 AI 生成 G-code → 写字机执行并自动换纸。

本仓库是**枢纽仓库**，只放 MCP 生成服务、协议文档、硬件说明（`docs/hardware.md`）、部署脚本、联调 SOP。**固件本体不在这里**——分散在两个独立 fork，且不向本仓库合并：

| 代码去哪找 | 本地路径 | 角色 |
|-----------|----------|------|
| 运动控制 / 换纸固件 | `D:/Users/Grbl_Esp32` | Grbl_Esp32（成熟商业现役固件，分支 `fix/panel-hold-after-align-116687e`，源码 HEAD `966723b`；板上行为基线 `f5c8985`，后续只改注释、未重烧） |
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
写字机 Grbl_Esp32 内置 ESP3D WebUI 开 **Telnet:23**，STA 连家用路由器拿**固定 IP**；S3 由 xiaozhi WifiBoard 连同一路由器，做 **TCP 客户端**直连。链路内即原串口字节流——115200 语义、行协议 `ok`/`error:NN`、banner、实时字符全部同构。
> 三次推翻的方案，别再往回走：① S3 原生 USB Host——实战派 C 口经板载 CH334F HUB 占为设备口，GPIO19/20 无排针引出；② CH9350 桥接——仅枚举 HID 类设备，不认写字机的 CH340（vendor-specific 串口）。备用链路仅方案 B（拆机 TTL，IO10/IO11），WiFi 实测抖动不可接受时才启用。

**哑管道原则**：G-code 全部在云端（京东云 VPS 的 MCP 服务）生成+转换完毕。S3 板只做**下载 + 校验 + 流控转发**，绝不生成/编辑/预览。

**§5 大小/长度一律按「设备看到的字节」计，不按 Python 字符**：512KiB 上限是 S3 侧 PSRAM 缓冲的硬约束，S3 下载到的、`Content-Length` 与 CRC32 算的都是**磁盘原始字节**。故 `_read_generated_gcode` 必须 `read_bytes()` 并把 `len(raw)` 显式传给 `validate_gcode(text, total_bytes=…)`——**禁止改回 `read_text()`**：后者走 universal newlines 把 CRLF 塌成 LF，每行少算 1 字节。实测某 20846 行 CRLF 产物磁盘 524369 字节（超上限 81 字节）被判为未超并放行，回给 S3 的 `bytes` 少报 20846。单行 255 字节上限（Grbl `LINE_BUFFER_SIZE`）同理已按 `encode("ascii")` 计。

**换纸消息只做定点路由（别扩大修改面）**：原固件的换纸消息均走 `CLIENT_SERIAL`；§9 `e7f373f` 只把四处 `[PaperStatus] N` 结果码改为 `CLIENT_ALL`，其余 `[PaperAuto-N]` 进度仍只走 CH340。`[ESP901]` 的 `Changing=Off` 已经 Telnet 实测，`Changing=On` 与真实 `[PaperStatus]` 到达仍待有纸换纸验证。S3 判换纸靠三层：自发 `M30` 自知 + `[ESP901]` 查 `Changing` + `?` 探活。CH340 串口仍是最全诊断口。

**Grbl 侧可小改（口径已变更）**：换纸机械/时序、运动、授权、U1-Grbl 协议**一律不改**（现役换纸相关：`M701`/`M704`/`M711`/`M712`/`M713`/`M721`/`M30` 等）；但**消息路由、状态字段、WebUI 网络层**等不影响商业出图行为的小加固**可接受**。`docs/protocol.md` §9 清单已以 `e7f373f` 提交、fz `standard` pass 并烧录；keepalive 与 `[ESP901] Paper/Changing=Off` 已实机验证，`Changing=On`/自动换纸仍未验证。后续任何 Grbl 改动仍**必跑 fz 门禁**。

**画布与行程安全**：A4 纸 210×297mm（X 短边 Y 长边）。MCP 生成端限幅 X≤190 Y≤277mm（四周 10mm 边距），Grbl 行程 `$130=210 $131=297`。**无限位开关**（引脚注释掉了），软限位/归零不可用，MCP 端校验是唯一安全防线。**坐标原点已验证**：左下角机械死点 = (0,0)，X+ 向右，Y+ 向前（远离操作者）。归零方式 = 手动推死点后上电。

## 转发状态机的关键坑

详见 `docs/protocol.md`（v0.5）。摘要：逐行流控（一行一 ok，无 character-counting）、单写者（出图中只发 `?`，不发 M 命令）、Telnet 独占（`MAX_TLNT_CLIENTS=1`）、先下完再落笔（全量下载 + CRC32 + 授权门 → 才发第一字节）、abort ≠ 急停（换纸物理上打不断）。改转发逻辑前**必读原文**。

## 硬规则

- **本仓改动后**：跑 `python scripts/agent_gate.py`（默认 `hub`）。overall 非 pass 不得宣称本仓通过。定义见 `docs/agent-gate.md`。
- **改了 `D:/Users/Grbl_Esp32` 的任何代码后（含 §9 的消息路由/状态字段/keepalive 小改），必须自己跑 fz 门禁**：
  `python D:/Users/zhugu/fz/scripts/agent_gate.py`（先设好 `FZ_ROOT`/`GRBL_ROOT`）。overall 非 pass **不得声称修好**。细则见 `D:/Users/zhugu/fz/docs/AGENT_VIBE_CODING.md`。本仓 `--profile full` 的源码标记检查**不替代** fz。
- **改 Grbl 的边界**：换纸机械/时序、运动、授权、U1-Grbl 协议不改；**禁止**碰 `report_realtime_status()`（固定 `char[200]` + 无边界 `strcat`，有栈溢出风险）与 `report_state_text()`（状态字被现役商业上位机依赖）。
- **凭据禁入库**：`D:/Downloads/VPS.txt` 含 VPS 明文密码与 API key，绝不复制、引用、提交到任何仓库。
- **禁止自由发挥 / 幻觉 / 乱改**：见 `docs/agent-anti-drift.md`。不重开 USB Host/CH9350/阿里云；不跳 `bringup-sop`；不把 MCP 组 0–4 当待办重做；不覆盖 `worktree-inventory.md` 记载的脏树；不为门禁绿改换纸常量；用户未指定时按 handoff §6。

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

> **`-m uvicorn` 不读 `HUTUJI_HOST`**，只有 `python -m hutuji_mcp.server` 才走 `settings.host`；`--host` 漏写即 `0.0.0.0` 裸曝 `/mcp`。

### 本仓门禁

仓库根执行 `python scripts/agent_gate.py` → `results/hutuji_gate_last.md`

| Profile | 内容 |
|---------|------|
| `docs` | 仅文档锚点/一致性检查 |
| `hub`（默认） | `docs` + pytest + e2e |
| `full` | `hub` + 外仓文档 `path:line` 校验 + Grbl §9 源码标记（**必须设 `GRBL_ROOT`，未设时该层直接 fail、不再 skip**）；且仍须自己另跑 fz |

### 环境变量

关键变量（全表见 `mcp-server/README.md`）：`HUTUJI_LLM_API_KEY`（未配置时 `hutuji_draw` 报错）、`HUTUJI_LLM_BASE_URL`/`_MODEL`、`HUTUJI_PUBLIC_BASE_URL`（返回给 S3 的下载前缀）、`HUTUJI_OUTPUT_TTL_SECONDS`（默认 600）。配置全走环境变量，**无 `.env` 文件**。

### 网络安全

公网只经 nginx 暴露 `/files/`；`/mcp` 在 `deploy/nginx-hutuji.conf` 里显式 404，安全组不放行 8300。小智云工具发现冒烟：仅当前终端设 `MCP_ENDPOINT` 后跑 `scripts/cloud_bridge_smoke.py`（接入点禁入库）。

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
- **生产 venv 精简装，没有 pytest**：只能跑 `scripts/e2e_local.py` 与直接 import 校验函数实测。
- 改动前先备份，重启 `hutuji-mcp` 与 `hutuji-cloud-bridge` 后跑 `deploy/preflight.sh`（当前共 **17** 项，另核 bridge endpoint 为 `wss://` 及 bridge active，仍覆盖 HTTPS 前缀、enabled 精确目标、活动配置哈希及下载 200/长度/CRC32/正文）+ 公网复验 `/mcp` 须 404、直连明文 `:80 /files` 须 403、8300 须连不上。`69c2198` 已于 2026-08-06 部署并取得 **17/17**（含首次在生产真跑通过的「生成链」与「unit 一致性」）；该结果不替代后续发布复验。**scp 完 unit 必须 `daemon-reload`**，否则 `check_units_match_templates` 判 fail。
- **preflight 曾有两处「文件对了但跑的不对」的盲区，现已各补一项**：
  - **生成链（`check_generate_chain`）**：旧版 15 项没有一条执行过部署的 `hutuji_mcp` 代码——下载探针用 `printf "G21\nG90\nF8000\n"` 人工拼产物，那份内容连 `validate_gcode` 自己都不接受，所以只证明了「nginx 能按字节发文件」。已实测复现：把 `hutuji_gwrite.toml` 移走后 `import hutuji_mcp.server` 仍成功、服务保持 active、`/mcp` 与 `/files` 照常响应，而真实 `svg_to_gcode()` 报 `vpype 转换失败（exit 2）`——即 `pass=15 fail=0` 与「一画就整单失败」可以同时成立。根因是 `_PROFILE_PATH` 只在请求期被 `_vpype_command()` 用到，import 期不加载不校验，`systemctl active` 抓不到；`vpype` 更是 subprocess、从不 import，装坏同样只在请求期暴露。现在 `check_generate_chain` 用生产 venv 真跑 `svg_to_gcode()`，`check_download` 拿这份真产物验下载契约。**探针必须用 `/opt/hutuji/mcp-server/.venv/bin/python`，不能用系统 `python3`**：`_vpype_cmd()` 按 `sys.executable` 同目录找 vpype，换解释器即换掉被验对象。
  - **unit 漂移（`check_units_match_templates`）**：`systemctl is-active` 只证明「在跑」，不证明跑的是哪一版 unit——`--host 127.0.0.1`、`ProtectSystem=strict`、`SystemCallFilter` 等加固项可全部缺失而照样 active（监听面另有第 5/6 项覆盖，加固项原本无人验）。两个 unit 与 nginx 的安装方式完全同构（都从 `/opt/hutuji/deploy/` 复制到系统目录），却只有 nginx 有一致性判据。现在 `check_units_match_templates` 对两个 unit 各查四条：LF 归一化 md5 与模板一致、`NeedDaemonReload=no`（systemd 用加载时快照，忘了 `daemon-reload` 时磁盘一致而在跑的仍是旧配置）、`FragmentPath` 指向 `/etc/systemd/system/`（排除 `/lib` 残留）、**无 drop-in**（drop-in 能静默覆盖 ExecStart 与全部加固项，存在时前三条什么都证明不了；用文件系统 glob 判定，不能只信 `DropInPaths`——老版 systemd 返回空串即 fail-open）。
- **preflight 无 skip 状态**：输出 `pass=N fail=N expected=N`，放行判据 `PASS==EXPECTED_CHECKS && FAIL==0`。历史 `OPTIONAL_SKIP` 与 pass 等价放行，使「明文 `:80 /files` 须 403」可从未被验证却报全绿，已删除；**禁止重新引入 skip**，取不到非 loopback IPv4 一律判 fail。**探 8300 用 `curl`，别用 bash 的 `/dev/tcp`**（Git Bash 下会假阳性）。

## 路线图（当前进度）

1. **① 仓库奠基** — ✅ 文档地图、门禁、deploy 模板；见 `foundation-checklist.md`
2. **② 哑管道** — 🟡 源码 HEAD `83eb376` 已提交/推送；当前板上行为基线是 2026-08-04 烧录的 `55574cb`，`83eb376` 新增的 `TakeResponse()` 超时错误码回填**未烧录、未做硬件实测**。2026-08-03 P2 S4 使用 `ae4b524` 完成 551 行玫瑰图全量下发、自动换纸八步全绿与 `state=done`，`pbuf_free` 未复现（证据 `results/s4-rose-20260803-evidence.md`）；这份历史证据不证明后续源码 HEAD 已上板。**缺**：纸面实物画质人工目视、自适应 `Bf` 反压。
3. **③ 智能端** — 🟡 HTTPS 已通（Cloudflare Tunnel + Let's Encrypt），京东云 MCP+桥接已常驻；官方云路径已触发 `draw/status/abort`。缺 App 真实语音出图验收。
4. **④ 出货** — 🔴 bringup 证据 + 合规签核未做。

> **不可出货**。默认下一刀见 `docs/agent-handoff.md` §6：纸面实物画质人工目视 → App 真实语音端到端 → 断连恢复 `paper_active` 窗口 abort 复测 → 生产签核。

## 约定

- 修改项目结构、约定或工作流后，同步更新本文件、`README.md`、`AGENTS.md`、`docs/agent-gate.md`，以及 `.cursor/rules/hutuji-agent-rails.mdc`（`alwaysApply` 硬轨，与本文件的「禁止重开 / 禁乱改 / 门禁 / 默认下一刀」同源，改口径时勿漏）。
- 项目记忆/决策日志：`C:/Users/zhugu/.kimi-code/memory/projects/hutuji.md`。
