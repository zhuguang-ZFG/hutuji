# Repository Guidelines

> 本文件是 AI 编码代理在 hutuji（枢纽仓）的入口。真值优先级：根 `README.md` 决策 → `docs/protocol.md` v0.5 契约 → `docs/bringup-sop.md` 执行顺序 → `docs/release-readiness.md` 状态 → 子系统 README / runbook。外部记忆可能过时，冲突一律以本仓文档为准。硬规则见文末，**不可跳**。

## Project Overview｜项目概览

hutuji 是一台 **AI 绘图自动换纸写字机**：语音 → 云端 MCP 生成 G-code → S3 板下载转发 → Grbl 写字机出图并自动换纸。

本仓 = **枢纽仓**：MCP 生成服务（`mcp-server/`）、板间协议、硬件说明、京东云部署脚本、门禁与联调 SOP。**固件本体不在本仓，也不向本仓合并**：

- 运动控制 + 自动换纸（成熟商业现役固件）→ `D:/Users/Grbl_Esp32`
- AI 交互（ESP32-S3，板型 `lichuang-dev`）→ `D:/Users/xiaozhi-esp32`

路线图：① 仓库奠基 ✅ → ② S3 哑管道 🟡 → ③ MCP + 云端 🟡 → ④ 小批量出货 🔴。状态真值只看 `docs/release-readiness.md`。

## Architecture & Data Flow｜架构与数据流

```
语音 → 小智官方云(+MCP 接入点) → 京东云 MCP 服务 hutuji_draw
     → LLM 出 SVG → 校验 → vpype 转 G-code → validate_gcode → output/ + 能力 URL
     → 云端把 URL 回给 S3 → S3 下载 + CRC32 校验（PSRAM 全量缓冲）
     → WiFi Telnet :23 逐行发给 Grbl → 页尾 fresh `Idle` 后 S3 单独发 `M30` → 自动换纸
```

服务内部链路（代码实测顺序）：`server.hutuji_draw()` → `_draw_limiter.slot()`（覆盖全程）→ `_cleanup_expired_outputs()` → `bitmap_svg.generate_bitmap_svg()`（位图路线，2026-08-09 起为现行：`_sanitize_description` → agnes 文生图 `/images/generations` → 下载 PNG → skimage 骨架化 + sknw 建图描摹 → 单线 SVG → `_validate_svg`）→ `gcode_gen.svg_to_gcode_async()`（可取消 vpype：`read → layout 归一化（--fit-to-margins 10mm 190x277mm，居中+等比缩放）→ linemerge → linesort → linesimplify → gwrite --profile hutuji`）→ `validate_gcode()` → 写 `output/draw_YYYYMMDD_HHMMSS_<22 位 token>.{svg,gcode}` → 返回 `{url, lines, bytes, pen_lifts}`。LLM 直写 SVG（`llm_svg.generate_svg`）只留给 `hutuji_preview_svg` 调试。

**锁定决策（编号同根 `README.md`，禁止重开）**

1. 双芯片：S3 跑 xiaozhi，独立 ESP32 跑 Grbl，不合并固件。
2. 板间 = **WiFi Telnet :23**（写字机 STA 固定 IP `192.168.1.13`，S3 作 TCP 客户端 + 指数退避重连）；USB Host 与 CH9350 均已否决，别再提；拆机 TTL 仅备用。
3. **哑管道**：G-code 只在云端生成与转换，S3 只做下载 + 校验 + 流控转发。
4. AI = 小智**官方云**，自定义能力走 MCP 接入点 + 设备侧 MCP 工具。
5. 部署 = **仅京东云** `117.72.118.95`（专机，无云端冗余），公网入口经 Cloudflare Tunnel `https://hutuji.donglicao.com`。
6. 换纸机构机械/时序/运动/授权/U1 协议**不改**；只允许 `docs/protocol.md` §9 清单内的消息路由/状态字段/网络层小加固，改后必过 fz 门禁。
7. 本仓 = 枢纽仓，两个 fork 各自独立演进。

实机侧：MK38 板，A4 行程已设（`$130=210`、`$131=297`），原点为左下角死点，X+ 右 / Y+ 前。**写字机无限位开关，MCP 侧限幅（X≤190 / Y≤277mm）是唯一安全防线。** `MAX_TLNT_CLIENTS=1`：PC Telnet 与 S3 不能同时占 :23，PC 查完必须退出，观察走 CH340 串口。

## Key Directories｜关键目录

| 路径 | 用途 |
|---|---|
| `mcp-server/hutuji_mcp/` | ③ MCP 生成服务包（9 个模块 + `hutuji_gwrite.toml`） |
| `mcp-server/tests/` | pytest 契约/注入回归 + `test_star.svg` 夹具 |
| `mcp-server/scripts/` | `e2e_local.py`（无硬件闭环）、`cloud_bridge_smoke.py`（云工具发现） |
| `scripts/` | 本仓门禁 `agent_gate.py` + 实机工具（WiFi 配网、故障注入抓包、小智控制台） |
| `deploy/` | 京东云 systemd / nginx / env 模板 + `prepare-host.sh`、`install-from-repo.sh`、`preflight.sh` |
| `docs/` | 契约、状态、SOP、硬件、逆向 findings；批次模板在 `docs/templates/`（**不是**仓库根 `templates/`） |
| `firmware/` | 只放 S3 补丁**说明文档**（`README.md`、`m1-usb-pipe.md`），不放固件源码 |
| `results/` | 门禁报告与实机证据；整目录被 `.gitignore` 忽略，**证据只在本机**，故须登记进 `docs/worktree-inventory.md` |

## Development Commands｜开发命令

```powershell
# 本仓门禁（推荐入口；默认 hub = 文档锚点 + 外仓行号 + pytest + e2e）
cd D:\Users\hutuji
python scripts\agent_gate.py                   # 等价 --profile hub
python scripts\agent_gate.py --profile docs    # 只改文档时
python scripts\agent_gate.py --profile full    # 只有改 Grbl 才用；需 $env:GRBL_ROOT

# MCP 服务开发（venv 手动建，无 pyproject）
cd D:\Users\hutuji\mcp-server
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts\e2e_local.py      # 必须打印 PASS
.venv\Scripts\python -m uvicorn hutuji_mcp.server:app --host 127.0.0.1 --port 8300
.venv\Scripts\python -m hutuji_mcp.grbl_sim    # 假 Grbl，监听 127.0.0.1:2323
```

改 `D:/Users/Grbl_Esp32` 任何代码（含 §9）后**必须自己再跑 fz 门禁**，本仓 `full` 的 §9 源码标记检查不替代它：

```powershell
$env:FZ_ROOT='D:\Users\zhugu\fz'; $env:GRBL_ROOT='D:\Users\Grbl_Esp32'
python D:\Users\zhugu\fz\scripts\agent_gate.py --profile standard
```

实机 / 运维工具：

```powershell
python scripts\grbl_wifi_setup.py --port COM3 --ssid YOUR_SSID --dry-run   # 密码走 GRBL_WIFI_PASSWORD
python scripts\bringup_fault_capture.py --s3-port COM9 --grbl-port COM3 --duration 180
python scripts\xiaozhi_console.py list | show <agent_id> | sync <agent_id> | update-kb <kb_id>
python scripts\xiaozhi_console.py device-tools                              # 另需 XIAOZHI_DEVICE_TOKEN
python scripts\xiaozhi_console.py device-call hutuji.status
.venv\Scripts\python scripts\cloud_bridge_smoke.py    # 仅当前终端注入 MCP_ENDPOINT
```

**坑**：`-m uvicorn hutuji_mcp.server:app` **不读** `HUTUJI_HOST`，漏写 `--host 127.0.0.1` 监听面就是 `0.0.0.0`。反过来 `HUTUJI_HOST` 也不再能悄悄写错——`config.py` 在 import 期只放行 loopback，非 loopback 会让服务（含 uvicorn 路径）启动即抛错。监听面裸开后 `/mcp` 本身仍有应用层兜底：`server.LoopbackOnlyMcpGuard` 按 ASGI 对端地址拒掉非 loopback 连接并回 404（`/files` 不受影响）。**但仍别漏写 `--host`**——那会把 `/files` 也直曝到公网，绕开 nginx 的限速与 ACL。

## Code Conventions & Common Patterns｜代码约定与常用模式

- **语言**：文档与注释一律中文，标识符英文。注释解释「为什么这个数」，不复述代码。
- **配置**：只走环境变量，**禁止 `.env` 文件**。服务侧 `HUTUJI_LLM_BASE_URL/_API_KEY/_MODEL`、`HUTUJI_IMAGE_MODEL`（位图路线生图模型，默认 `agnes-image-2.1-flash`）、`HUTUJI_OUTPUT_DIR`、`HUTUJI_PUBLIC_BASE_URL`、`HUTUJI_HOST`（默认 `127.0.0.1`，**只允许 loopback**，非 loopback 直接抛错；监听面真值是 unit 的 `--host`）、`HUTUJI_PORT`（`8300`）、`HUTUJI_OUTPUT_TTL_SECONDS`（`600`）；桥接侧 `MCP_ENDPOINT`（必须 `wss://`）、`HUTUJI_LOCAL_MCP_URL`（必须 loopback 且路径 `/mcp`）。生产凭据只落 `/etc/hutuji/{mcp,bridge}.env`（`chmod 600`）。
- **状态与注入**：模块级单例代替 DI 容器——`config.settings` 在 import 时构造，`server._draw_limiter` 是模块全局；测试用 `monkeypatch.setattr(module, "settings", ...)` 或改环境变量覆盖，没有 FastAPI 依赖注入。
- **错误处理**：窄类型异常 `GcodeError` / `LlmError` / `BitmapError` / `DrawLimitError`；`/files` 的一切失败（名字不合法、缺文件、过期、读取竞态）统一塌成 `HTTPException(404)`，不回显内部细节；vpype 失败只报 exit code，不带 stderr。`generate_svg` 用 `finish_reason` **只解释**「一段 SVG 都提取不到」（`length` → 报 `max_tokens` 预算耗尽），**不得**据它拒掉已闭合的 SVG，也**不得**盖住 `_validate_svg` 的白名单拒绝（那是注入事件，处置完全不同）。
- **async/sync 分工**：MCP 工具是 `async`；TTL 清理走 `asyncio.to_thread`，vpype 用 `asyncio.create_subprocess_exec` 并在取消时回收子进程；LLM 用 `httpx.AsyncClient`；`pipe_sim` / `e2e_local.py` 保持同步 socket + 子进程。
- **脱敏是硬约定**：能力文件名用 `_DRAW_FILE_PARTS_RE` 拆 token（token 是 urlsafe base64，**本身可能含 `_`**，按最后一个下划线切会泄露约 29%）；桥接只记 JSON-RPC method 标签，stderr 过 `_safe_stderr_line`，网络异常只记类型。第三方 logging 必须在 handler filter 中先 `record.getMessage()` 再脱敏最终文本，并单独脱敏 `exc_info`/`exc_text`/`stack_info`；只扫字符串 `msg/args` 会漏异常对象和 traceback。
- **产物与 TTL**：只有匹配 `draw_YYYYMMDD_HHMMSS_<22 位 urlsafe token>.{gcode,svg}` 的名字可下载；TTL 600s，过期即删并返 404；清理是访问时顺带做的，**没有后台扫描线程**。
- **G-code 契约**（`docs/protocol.md` §5，`validate_gcode()` 是活闸门）：头 `G21/G90/F8000` + 两次幂等 `G1G90 Z0.0F10000`（**不建立 Z 基准**）；`G0 ... F8000` 空走、`G1G90 Z5.0F10000` 落笔、`G1G90 Z0.0F10000` 抬笔。下载文件里禁 `G92`/`M3`/`M5`/`M30`/`G4`/`G28`/`G30`，禁 `G0` 落点 X0Y0，Z 只能 0 或 5，`0<F≤10000`，X≤190mm、Y≤277mm、单文件 ≤512KiB。落笔前的 `G92 Z0` 与页尾 `M30` 由 S3 旁路单发，不进文件。
- **profile 陷阱**：`hutuji_gwrite.toml` **故意不以 `G90G0 X0Y0` 收尾**——回原点会触发本机换纸行为。
- **SVG 注入防御**：元素白名单 `{svg,g,path,line,polyline}` + 属性白名单，拒 DTD/实体/事件属性/`href`/`url(...)`/非 `none` 填充，深度 ≤64，SVG ≤256KiB，`description` ≤200 字符并剔除控制字符。改校验必须先过 `tests/test_injection.py`。
- **容差是显式决策**：`LINESIMPLIFY_TOLERANCE_MM = 0.05`、`LINEMERGE_TOLERANCE_MM = 0.3`，有专测钉死，不许顺手调参。
- **脚本退出码有语义**：门禁 pass=0 / 任一层 fail=1；`grbl_wifi_setup.py` 2=缺密码、130=用户取消；`bringup_fault_capture.py` 2=缺端口或缺 pyserial、1=串口报错（打开/读取失败、注入写失败，以及**排过 `[ESP115]ON` 却在日志里找不到发送确认**——这一路会往报告追加显式失败行，不再以 0 收场）。
- **状态标记**：✅ 有命令/commit/设备/日志证据；🟡 代码或设计在但缺生产/硬件证据；🔴 未实现或阻塞。

## Important Files｜关键文件

- `mcp-server/hutuji_mcp/server.py` — FastAPI + FastMCP 入口；工具 `hutuji_draw` / `hutuji_status` / `hutuji_preview_svg`（下划线名是 Python 工具；文档里点号 `hutuji.draw/status/abort` 指**设备侧**七个工具）；路由只有 `GET /files/{name}`，`/mcp` 挂在根、本机可达、公网由 nginx 404。
- `mcp-server/hutuji_mcp/gcode_gen.py` — vpype 管线、限幅常量、`validate_gcode()`。
- `mcp-server/hutuji_mcp/hutuji_gwrite.toml` — 生产 G-code profile（逐字对齐 §5）。
- `mcp-server/hutuji_mcp/llm_svg.py` — LLM 调用 + `_validate_svg()` 注入闸门。
- `mcp-server/hutuji_mcp/cloud_bridge.py` — 小智云 WSS ↔ 本机 `/mcp`（spawn `mcp-proxy`，带脱敏与重连）。
- `mcp-server/hutuji_mcp/{grbl_sim,pipe_sim}.py` — 假 Grbl / 假 S3，e2e 与本地调试用。
- `scripts/agent_gate.py` — 本仓门禁；报告固定写 `results/hutuji_gate_last.{json,md}`。开工先写 `results/.hutuji_gate_running`（含计划层清单与 HEAD），报告落盘后才删——**标记在场 = 上次没跑完，其间报告不可信**；**退出码是「本轮是否通过」的唯一真值**，报告头另绑 `git_head` 供比对新旧。
- `docs/protocol.md` — v0.5 契约（§5 G-code、§9 Grbl 小改清单）。
- `docs/release-readiness.md`（状态真值）、`docs/agent-handoff.md`（接手防呆）、`docs/agent-anti-drift.md`（防漂移）、`docs/agent-edit-allowlist.json`（机读白名单）、`docs/agent-constraint-matrix.md`（三仓矩阵）、`docs/worktree-inventory.md`（脏树登记）、`docs/agent-gate.md`（门禁定义）、`docs/bringup-sop.md`（阶段 0–9 判据）。
- `docs/daytime-tuning-runbook.md` — 白天联调单日取证编排（11 步，每步 命令/期望/失败回退/取证落点；判据引用 `bringup-sop.md` 不复制，取证填 `templates/`，结论勾回 `release-readiness.md`）。**基线按 2026-08-09 复核过**：第 4/5 步（烧 Grbl/S3）与第 9 步（换纸中 abort）已标「板上已是 / 已有历史取证，正常联调日跳过」——原文钉的 `12f26ac`/`255f133`/`f1cf066` 经 `git merge-base --is-ancestor` 实测**都是板上基线的祖先**，照旧文烧即固件回退；换板/回退才用那两步，§8.5 复测须从当前 HEAD 带 `-DHUTUJI_AUTO_TEST_ABORT_ON_PAPER=ON` 构建（**构建期开关**，不是某个旧 commit 的固有行为），禁 checkout 旧 commit。**第 1/2 步已于 2026-08-09 执行通过**（token 续期 → `sync 2094251` → 三类问法召回逐条命中；同步前云端 KB 还是 08-07 版，08-08 那组契约纠错本轮才真正上云），但**第 1 步是每日重做项**，控制台 JWT 会被服务端作废，不得标「已完成跳过」。文首适用基线**已不再钉 HEAD SHA**（钉 SHA 正是它过期 9 天的原因），改为现取 `git log --oneline -1` 并与 readiness 文首对齐。该文件**已于 2026-08-09 补入** `GOVERNANCE_DOCS`（第 18 份），故禁语与复活两层现在覆盖它；但门禁只查禁语与复活词，**查不出「已完成/可跳过」这类状态措辞是否属实**，改它仍须人工对账 `release-readiness.md`。
- `.cursor/rules/hutuji-agent-rails.mdc` — Cursor 侧同口径硬轨，口径变更要同步。

## Runtime/Tooling Preferences｜运行时与工具链

- Python ≥3.12（本机 3.13）。**纯 `requirements.txt` + 手动 venv：无 `pyproject.toml`、无 lockfile、无 linter/formatter 配置**——不要引入 ruff/black/poetry/uv，也不要新增 CI 配置。
- 依赖是范围下界而非精确 pin：`fastapi>=0.115`、`uvicorn[standard]>=0.30`、`httpx>=0.27`、`vpype>=1.14`、`vpype-gcode>=0.13`、`mcp>=1.28.1,<2`、`mcp-proxy>=0.8.2,<1`、`websockets>=13,<17`、`scikit-image>=0.24,<0.27`、`networkx>=3,<4`、`sknw>=0.1,<1`、`imageio>=2.34,<3`、`numpy>=1.26,<3`；dev 只多 `pytest>=8.3,<9`。
- 开发机 Windows（PowerShell，venv 在 `.venv\Scripts\`）；生产 Ubuntu + systemd + nginx（venv 在 `/opt/hutuji/mcp-server/.venv/bin/`）。
- 生产拓扑：uvicorn 只监听 `127.0.0.1:8300`；nginx 只公开反代 `GET/HEAD /files/`（限速 10r/s），`/mcp` 显式 404；安全组只开 22/80/443，**不放行 8300**。
- 生产更新用 `scp`，**不能 `git pull`**（`/opt/hutuji` 无 `.git`）；本地工作树是 CRLF 检出，**scp 前必须 LF 归一化**（`git show HEAD:<path>` 或 `tr -d '\r'`），否则 `preflight.sh` 上机即 `set: pipefail: invalid option name`。scp unit 后先 `daemon-reload`，再**显式重启** `hutuji-mcp` 与 `hutuji-cloud-bridge`（`enable --now` 不会重启已 active 的服务），最后跑 `bash deploy/preflight.sh`（**当前 25 项**——其中 2 条为 2026-08-12 R16 新增公网面探针，已于 2026-08-12 部署，生产实测 `pass=25 fail=0 expected=25`；含有效 `wss://` bridge endpoint、两个服务 active、**两个 unit 与模板一致、无 drop-in，且 MCP 的两条 ExecStartPre（`gcode_gen` 与 `bitmap_svg` 自检）均已成功、主进程不早于本次 unit/代码/profile**、HTTPS 公网前缀、enabled 链接精确目标、活动配置 LF md5、隧道 Host 路由、**应用层已关 FastAPI 自动文档端点**、**兜底路径隧道回源态 404 / 明文直连 301**、**在跑的 nginx 确实是本次配置（探针进专用日志 + worker 新鲜度 + 无 [emerg]）**，以及**用生产 venv 真跑一次 `svg_to_gcode()` 的生成链**、**用生产 venv 真跑 `bitmap_svg --self-test` 的位图描摹链**（与 unit 第二条 ExecStartPre 同入口；skimage/sknw/imageio 是延迟 import，缺失时服务照样 active、生成链自检照样绿，只有真实描摹会炸——2026-08-09 本机实测复现，与生成链同款 fail-open）与拿真产物验的 200/长度/CRC32/正文（**2026-08-12 R16 起：下载探针对 localhost:80 与公网 HTTPS:443 各真跑一遍，另加公网 HTTPS:443 `/mcp` 须恰 404、000 不算 pass**）；须全 pass）。这些项都在堵「文件对了但跑的不对」：**生成链**——旧版没有任何一项执行过部署的 `hutuji_mcp` 代码，下载探针用人工拼的三行文本冒充产物（`validate_gcode` 本身就不接受它），故 `hutuji_gwrite.toml` 漏传、vpype 装坏、profile 与校验器漂移全部逃过体检，已实测复现「服务 active + 15/15 全绿 + 真实 `svg_to_gcode()` 报 exit 2」同时成立；**位图描摹链**——2026-08-09 本机实测：拦掉 imageio 后 `import hutuji_mcp.server` 成功、`gcode_gen --self-test` 退 0，只有真实描摹抛 `BitmapError`，故 unit 加第二条 `ExecStartPre`（沙箱内 fail-closed）且 preflight 单独真跑当前落盘代码（堵「scp 了坏依赖但没 restart」）；**unit 一致性**——`is-active` 不证明跑的是哪一版，加固项可全缺而照样 active；`daemon-reload` 只更新 unit 快照，不会重启旧进程，故还须验证 ExecStartPre 结果与进程启动时间；drop-in 更能静默覆盖 ExecStart；**兜底路径**（2026-08-07 新增并当日在生产修复+验过）——隧道 `service: http://localhost:80` 使全部公网流量落进 `:80` 块，`:443` 的 `location / { return 404; }` 永远走不到，而旧 `:80` 兜底无条件 `301 https://<隧道主机名>$request_uri` → 边缘再回源 `:80` → **自指 301 死循环**（实测公网 `/docs`、`/redoc`、`/openapi.json`、随机路径全是 `301 Location: 自身`，`curl -L` 也停在 301）；危害不是暴露而是**判据造假**：「公网非白名单路径须 404」从未成立过，却因 `301 ∉ 2xx` 被一切「须失败」式复验蒙过。修法是 `:80` 兜底按 `$http_x_forwarded_proto`（隧道实测确带 `https`）分流：隧道回源 404、真明文 301，故该判据必须**同时**验两个方向——只验 404 会丢掉明文强制 HTTPS；**nginx 运行态**（第 16–17 项，2026-08-08 新增，起因见下条 reload 静默失败）。**脚本已删 skip 状态**：现输出 `pass=N fail=N expected=N`，须 `PASS==EXPECTED_CHECKS && FAIL==0`；旧 `OPTIONAL_SKIP` 与 pass 等价放行，会让「明文 `:80 /files` 须 403」从未被验证却报全绿，禁止重新引入。历史 `pass=19` 结果不得复用为放行依据。
- **改 `limit_req` 的 key 或 zone 必须 `systemctl restart nginx`，`reload` 会被静默丢弃**（2026-08-08 生产实测 + 隔离复现）：`nginx -t` 起的是新进程、没有继承的共享内存区，故必然通过；`systemctl reload` 走 `nginx -s reload`，只发信号不等受理，恒 `status=0/SUCCESS`；master 若拒绝新配置只在 error.log 写 `[emerg] limit_req "hutuji_files" uses the "$hutuji_client_ip" key while previously it used the "$binary_remote_addr" key` 并**继续用旧配置服务**，旧 worker 不换、`is-active` 照样 active。生产因此跑了 37 分钟旧配置而 19 项 preflight 全绿（其余判据在旧配置下同样成立）。可靠检查是 worker 启动时间与专用日志的 fd/落行，不是 `-t`/`reload` 的退出码；`stat` 查配置 mtime 必须 `-L`，否则读到的是 `ln -sfn` 建链接的时刻。preflight 第 16–17 项已把行为面与进程面都钉死。
- 生产 venv 精简装**没有 pytest**：那边只能跑 `scripts/e2e_local.py` 或直接 import 校验。
- 固件不在本仓构建：S3 用 ESP-IDF **v6.0.1**（`python3 scripts/release.py lichuang-dev`），Grbl 用 PlatformIO。串口坑：S3 板载 CH340K 也枚举为 CH340，COM 号每次会变，判定看串口日志自证，别用 `esptool chip_id` 打断运行中的固件。

## Testing & QA｜测试与门禁

- 框架 pytest，**无 `conftest.py` / `pytest.ini` / 自定义 marker**；`.venv\Scripts\python -m pytest -q` 当前收集 **645 项**。
- 测试约定：函数式 `def test_*`，无测试类，私有辅助如 `_settings()` / `_minimal_gcode()`；负向契约用 `pytest.raises(..., match=...)`；拒绝矩阵用 `@pytest.mark.parametrize`（`(输入, 原因)`）；async 直接 `asyncio.run(...)`（无 async 插件）；临时状态用 `tmp_path`；时间用可注入 clock 或 `os.utime`，不引 freeze-time；**不要引入 `TestClient`**——现有测试直接调 `server._serve_file()` / `server.hutuji_status()` 并 monkeypatch 模块全局。
- 五个测试文件职责：`test_contract.py`（418 项：§5 契约、限流、TTL、脱敏（含 `scripts/xiaozhi_console.py` 三个 HTTP 入口的错误正文脱敏与 `URLError` 分支同口径）、TOCTOU、profile 形状、容差钉死、退化正 R 弧 fail-closed（弦长 <0.5mm 的近整圆歧义拒收）、`/mcp` 对端 loopback 守卫、`deploy/preflight.sh` 与两个 unit 的形状/条数钉死（含 `deployed_files` 数组结构解析钉死 `/etc/hutuji/mcp.env`）、管线 layout 归一化、位图路线描摹/重试/接线/自检与缺依赖诊断，以及门禁自身的 tombstone/HEAD 绑定/行号指纹回归（含 AG-F2/AGR-001/AGR-002/AGR-003 四条 P1 修复新增的 6 个测试，与 AG-F1 禁语模糊匹配/段落反引号计数新增的 11 个测试；另有 2026-08-11 第二轮 P2 加固：扫描份数与 pass 文案挂钩、软锚点缺失真报出、`conf`/`service`/`ino` 扩展名纳入引用校验、区间语义（倒序/从零起）判红、`subprocess` 启动失败塌成本层 fail、基线登记过仓相对路径而今解析不到即判红，以及 `preflight.sh` 的 `stat -L`／`.py` 递归展开／bridge 重启时效／真 `mktemp` 四条），以及 R7-BMP-01 单边像素上限新增 1 测试）、`test_injection.py`（35 项：SVG/LLM 输出闸门（含父 `<g>` 非 none fill 沿继承链拒掉子 path/line/polyline），改校验必过）、`test_cloud_bridge.py`（83 项：桥接配置与不泄密日志，含 `HUTUJI_LOCAL_MCP_URL` 拒 userinfo 且报错不回显凭据，以及 R7-WD-01/02 新增 5 测试、R7-WD-03 id 类型收窄新增 4 测试、R7-WD-06 cancelled str/int 归一化新增 1 测试）、`test_field_tools.py`（42 项：`scripts/` 实机与门禁工具，如 `classify_response()`、`check_external_doc_line_refs` 的 `require_repos` 分岔、指纹层 profile 归属，以及 `bringup_fault_capture.py` 的标记按来源归属（`[capture]`/`[inject]` 不算设备实收）与 WiFi 恢复发送确认/退出码）、`test_sim_server.py`（67 项：PC 仿真 MCP 的 stdio `initialize`/`tools/list`/`tools/call`、场景白名单、grbl_sim §5 契约边界/error 语义/MAX_TLNT_CLIENTS 与空闲超时建模、pipe_sim 行内注释与 error:8 重发、并发 e2e 端口互斥）。
- 闭环冒烟：`scripts/e2e_local.py` = `test_star.svg → svg_to_gcode → pipe_sim → grbl_sim`，判据是 stdout 出现 `PASS`（需 `127.0.0.1:2323` 可用）。
- 门禁分层（`docs/agent-gate.md` 是定义真值）：`docs` = 必备文件 + 决策锚点 + 否决项复活扫描 + **禁止宣称扫描** + 白名单 JSON + 防漂移文档 + 三仓矩阵 + 文档 `path:line` 引用 + **行号内容指纹**（半漂移防线，基线 `docs/agent-line-refs-baseline.json`）；`hub` 再加**外仓 `path:line` 引用** + pytest + e2e；`full` 再加 Grbl §9 源码标记，并把「三仓全缺」从 pass 收紧为 fail。任一层 fail → overall fail、exit 1。分层真值在 `scripts/agent_gate.py` 的 `profile_layers()`（纯函数，有门禁自测钉死），不要再在 `main()` 里另加分支。
- **复活扫描与禁语扫描共用 `GOVERNANCE_DOCS`（18 份）**：新增治理文档必须加进 `scripts/agent_gate.py` 的该清单，否则两层都绕过它。此前三条复活规则各带手写清单、合计只覆盖 5 份，`CLAUDE.md` 与 `.cursor/rules/` 硬轨都不在其中；`claims_forbidden` 更是只做列表比对、从不扫正文（实测在 README 追加一句含三条禁语的完成式断言，门禁全绿）。**2026-08-09 补入第 18 份 `docs/daytime-tuning-runbook.md`**：它决定「哪几步真机验证可以跳过」，写出「已完成/可跳过」后续代理就不再上真机复验，属行为直接约束源，此前无机器把关；已由 `test_gate_governance_docs_cover_repo_governance_surface` 的 `must` 清单钉死（RED→GREEN 实测），两层扫描均 pass。
- **扫描面每一份都自动是必备文件**：`check_required_files()` 校验的是 `required_file_set()`＝`REQUIRED_FILES ∪ GOVERNANCE_DOCS`（调用时现算、保序去重，本机 34 ∪ 18 → **36**），故「治理文档必须存在」是结构不变量，新增一份只改 `GOVERNANCE_DOCS` 一处。起因：两个扫描器对读不到的文件都只 `continue`，`forbidden_claims` 的 pass 文案却直接打 `len(GOVERNANCE_DOCS)`——2026-08-09 实测塞一个不存在的路径进清单，该层仍 pass 且印成「19 份均未做出」（实际只扫 18），`revived_decisions` 同样 pass，**报告数字与真实扫描面脱钩**；当日 `daytime-tuning-runbook.md` 与 `operations.md` 正在扫描面内却不在必备文件，改名即可静默退出两层扫描换全绿。不改扫描器判红（既有测试把 `ROOT` 指向只放一份文档的 `tmp_path`，缺失是合法测试构造，改了误伤 20 项），存在性交 `required_files` 层兜；也不手抄清单（静态补录管不住下一次新增，`test_gate_required_files_derives_governance_docs` 用虚构治理文档取 RED 钉死这点）。**三条复活规则的 `files` 现已全为 `None`**（均回落 `GOVERNANCE_DOCS`），上文「合计只覆盖 5 份」是已修复的历史观察。
- 写文档注意：门禁会校验 `path:line` 引用的行号是否越界，行号最易悄悄过期——不确定就别写行号。**讲「过期行号」当反例时别用 `file.ext:NNN` 写法**，本层会当活指针解析并计数；举例写成「`文件名` 第 NNN 行」。**新增或改动 `path:line` 引用后必须重登指纹基线**：先人工核对被引用行仍是当初证据所指，再跑 `python scripts\agent_gate.py --accept-line-refs` 并把基线 diff 随文档一起提交；不登记指纹层判红，无脑重登等于把指纹层退化成自动追认。
- **无覆盖率工具、无 CI**：门禁 + 实机证据就是验收面。软件改动必须给出测试命令与结果；硬件结论必须给设备编号、固件 commit、实测记录（模板在 `docs/templates/`）。

## Hard Rails｜硬规则（不可跳）

动手前按序读：`docs/agent-handoff.md` → `docs/agent-anti-drift.md` → `docs/agent-constraint-matrix.md` → `docs/worktree-inventory.md` + 三仓 `git status --short` → `docs/release-readiness.md` → `docs/agent-gate.md` + 任务相关契约段落。改外仓还要读该仓根 `AGENTS.md`。

- **无证据不宣称 / 无清单不改代码 / 不覆盖用户工作树 / 不为门禁绿去改产品行为。** 未跑的测试、未见的 commit、未实测的硬件现象一律写「未验证」。
- **不准简化设计 / 全称必证**：禁止照文档「可选 / 可去掉 / 可简化 / 待做」措辞删除或弱化任何负载（vpype 管线步骤、校验、安全边界、画质容差 `linesimplify`/`linemerge`、流控、每行带 F 等）——这类许可默认是**假的**，先与 `protocol.md` §5 / `hutuji_gwrite.toml` / `gcode_gen.py` 常量 / `test_contract.py` 钉死值对账，冲突以定案为准并盖章 `supersede`。说「唯一 / 全部 / 已清 / 干净 / 没有第二处」前必须先全仓检索取证。详见 `docs/agent-anti-drift.md` §0 铁律 7–8（已被门禁 `anti_drift` 层机器钉死，删铁律即红）。
- 改代码前必须能指出「白名单路径 + 契约段落或用户原话」；指不出就只读或问用户。机读白名单：`docs/agent-edit-allowlist.json`。
- Grbl 侧只可碰 §9 清单内文件意图（`src/WebUI/TelnetServer.cpp`、`src/PaperSystem.cpp`、`src/Protocol.cpp`、§9-C′ 判定谓词所在的 `src/ProtocolDecisionCore.h`）与其余 `*Core.h` 的 header-only 恢复；**禁止**碰 `report_realtime_status` / `report_state_text`、禁止全量 `CLIENT_ALL` 广播、禁止为仿真绿改换纸机械常量、禁止顺手重构。
- **禁止重开**：合并固件 / USB Host / CH9350 / 阿里云部署 /「G-code 在设备端生成」/「本仓无代码」/ 跳阶段 / 把 MCP 验收组 0–3 当待办重做——这些都已否决或已落地。
- **禁止宣称**（除非 `docs/release-readiness.md` 对应行已 ✅）：可出货、商业放行、§9 已完成、§9 已修好、硬件已验证、联调完成、京东云已上线、M2 已完成。门禁报告里的 `claims_forbidden` 清单不得为了绿而删。
- **凭据禁入库**：`D:/Downloads/VPS.txt`（VPS 明文密码 + API key）、真实小智 MCP 接入点、`小智控制台token.txt`，一律不复制、不引用、不提交。
- **默认下一刀**（用户未指定时）：① 纸面实物画质人工目视；② App 真实语音端到端验收；③ 断连恢复 `paper_active` 窗口的 `abort` 复测；④ 生产签核。S4 有纸出图与换纸中 `abort` 已有历史取证，禁止作为默认待办重做。
- **改完必同步**：契约 → `docs/protocol.md`；进度 → `docs/release-readiness.md`；脏树 → `docs/worktree-inventory.md`；结构与约定 → 本文件 + `README.md` + `CLAUDE.md`；门禁口径 → `docs/agent-gate.md` + `.cursor/rules/hutuji-agent-rails.mdc`。
