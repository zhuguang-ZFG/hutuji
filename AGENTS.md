# AGENTS.md — hutuji（写字机）

> 本文件面向 AI 编码代理。阅读前请假设自己对本项目一无所知。
> 状态真值：`docs/release-readiness.md`；契约真值：`docs/protocol.md` **v0.5**；接手防呆：`docs/agent-handoff.md`。

## 项目概览

**hutuji** 是一台 **AI 绘图自动换纸写字机**：语音指令 → 云端 AI 生成绘图内容 → 写字机执行并自动换纸。

- 仓库：`https://github.com/zhuguang-ZFG/hutuji`（本地 `origin` 已指向此处）。
- 本仓库是**枢纽仓库**：放 MCP 生成服务（`mcp-server/`）、协议文档、硬件说明（`docs/hardware.md`；写字机板 `docs/hardware/mk38-board.md`；S3 板 `docs/hardware/lichuang-szpi-esp32s3-board.md`）、部署脚本（`deploy/`）、联调 SOP。固件本体在两个独立 fork 中，**不向本仓库合并**。
- 架构与全部已确认决策见 `README.md` 决策记录表（**7 条，编号以此为准**）；板间契约见 `docs/protocol.md`（**v0.5**）。这两份文件是权威来源，动手前先读。
- **接手防呆（先读）**：`docs/agent-handoff.md`——常见误判、禁止重开的决策、默认下一刀。注意其 §0 快照可能滞后，进度一律以 `docs/release-readiness.md` 为准。
- **防幻觉 / 禁乱改（必读）**：`docs/agent-anti-drift.md`；白名单 `docs/agent-edit-allowlist.json`；三仓矩阵 `docs/agent-constraint-matrix.md`；脏树 `docs/worktree-inventory.md`。
- **本仓门禁（必跑）**：`docs/agent-gate.md` + `python scripts/agent_gate.py`（默认 `--profile hub`）。报告在 `results/hutuji_gate_last.md`。
- 全部资料入口见 `docs/README.md`（含真值优先级）；不得把"代码已写"表述成"硬件已验证/可出货"。
- 要动手实测/联调，按 `docs/bringup-sop.md`（阶段 0–9，逐阶段有通过判据，未过不得进下一阶段）。
- 外部记忆文件可能过时；**以本仓库文档为准**。

## 三仓布局

| 仓库 | 本地路径 | 角色 | 当前基线（详见 `docs/release-readiness.md`） |
|------|----------|------|----------|
| hutuji（本仓库） | `D:/Users/hutuji` | 枢纽：MCP + 协议 + 文档 + 京东云 deploy + 本仓门禁 | ① 闭环；③ **京东云 HTTPS 已通**（mcp+bridge+nginx+Cloudflare Tunnel，llm7，preflight 8/8） |
| Grbl_Esp32（fork） | `D:/Users/Grbl_Esp32` | 运动控制+换纸（**成熟商业现役固件**） | 分支 `fix/panel-hold-after-align-116687e`；§9 `e7f373f` 已提交 + fz `standard` pass + **已烧录实机验证**（2026-07-27：Telnet:23、`[ESP901]` `Paper/Changing` 字段、keepalive 均过）；缺 bringup 阶段 2（有纸 `Changing=On`） |
| xiaozhi-esp32（fork） | `D:/Users/xiaozhi-esp32` | AI 交互（板型 `lichuang-dev`，立创实战派 ESP32-S3） | `feat/hutuji-usb-pipe` `05f4563`（IDF 6.0.1 构建通过，**尚未烧录该 commit**）；七个 `hutuji.*` 工具已注册，官方云路径已触发 `draw/status/abort`；Pipe 下载/CRC/逐行流控、fresh-`Idle` 完成判定与 abort 恢复已实机回归；显式 `M30` 与完整断连安全重画已编码，仍缺有纸换纸/断连实测、App 真实语音、P2 S3/S4 |

写字机实机侧：MK38 板，WiFi STA 固定 IP `192.168.1.13`，Telnet:23 连通；A4 行程已设（`$130=210 $131=297`）；坐标原点已验证（左下角死点=(0,0)，X+右 Y+前）；M3/M5 笔控 Z 轴已修复验证；PC 直连 Telnet 逐行流控 8/8 ok。**无限位开关，MCP 端限幅是唯一安全防线。**

## 已锁定的架构决策（与根 `README.md` 同编号）

1. **双芯片**：S3 跑 xiaozhi，独立 ESP32 跑 Grbl；禁止合并固件。
2. **板间通讯 = WiFi Telnet（方案 E）**：写字机 Grbl WebUI Telnet :23，STA 连家用路由器 + 固定 IP；S3 TCP 客户端直连，断线指数退避重连。USB Host 与 CH9350 均已否决（别再提）；拆机 TTL 仅备用（`docs/hardware.md`）。
3. **哑管道原则**：G-code 在京东云 MCP 服务生成并转换完毕，S3 板只下载+校验+流控转发。
4. **AI 服务用小智官方云**，自定义能力走 MCP 接入点 + 设备侧 MCP 工具。
5. **MCP 服务部署 = 京东云 VPS 单点**（2核4G / 5Mbps / `117.72.118.95`，专机只跑 ③）；无云端冗余。公网入口经 Cloudflare Tunnel `https://hutuji.donglicao.com`（Let's Encrypt）。
6. **换纸机构 + Grbl 小改口径**：机械/时序/运动/授权/U1 协议不改（现役含 `M701`/`M704`/`M711`–`M713`/`M721`/`M30` 等）；消息路由、状态字段、WebUI 网络层小加固可接受，清单见 `docs/protocol.md` §9，**改后必过 fz 门禁**。
7. **本仓库 = 枢纽仓库**：新代码+协议+文档；两 fork 各自独立。

## 硬规则

- **无证据不宣称 / 无清单不改代码 / 不覆盖用户工作树**——详见 `docs/agent-anti-drift.md`；禁止宣称表见 `docs/agent-gate.md` §5。
- **本仓改动后**：跑 `python scripts/agent_gate.py`（文档用 `--profile docs`，默认 `hub`）。overall 非 pass 不得宣称本仓通过。
- **改 `D:/Users/Grbl_Esp32` 的任何代码后（含 §9），必须自己跑**：
  `python D:/Users/zhugu/fz/scripts/agent_gate.py`（设好 `FZ_ROOT`/`GRBL_ROOT`），overall 非 pass 不得声称修好。细则：`D:/Users/zhugu/fz/docs/AGENT_VIBE_CODING.md`。本仓 `full` profile 的 §9 源码标记检查**不替代** fz。
- **Grbl 白名单外禁止改**：换纸机械/时序/运动/授权/U1；禁止碰 `report_realtime_status` / `report_state_text`；不为仿真绿改商业常量。
- **凭据禁入库**：`D:/Downloads/VPS.txt` 含 VPS 明文密码与 API key，绝不复制、引用、提交到任何仓库；真实小智 MCP 接入点严禁写入仓库或 `.env`。

## 构建与测试

技术栈：Python ≥3.12（纯 `requirements.txt` + 手动 venv，无 `pyproject.toml`，无 linter/formatter 配置）；固件侧为 ESP-IDF（S3 用 **v6.0.1**）+ Grbl_Esp32（PlatformIO），均不在本仓构建。

- **推荐入口**：仓库根 `python scripts/agent_gate.py`（含文档锚点 + pytest + e2e）。
- 安装开发依赖：在 `mcp-server/` 执行 `.venv/Scripts/pip install -r requirements-dev.txt`（`.venv` 未建则门禁 pytest/e2e 层直接 fail）。
- 契约/安全回归：`.venv/Scripts/python -m pytest -q`（86 条：契约/注入/脱敏/限流/TTL）。
- MCP → G-code → 假 Grbl 闭环：`.venv/Scripts/python scripts/e2e_local.py`，必须输出 `PASS`。
- 起开发服务：`.venv/Scripts/python -m uvicorn hutuji_mcp.server:app --host 127.0.0.1 --port 8300`（**`--host` 漏写即 `0.0.0.0` 裸曝 `/mcp`**；`-m uvicorn` 不读 `HUTUJI_HOST`）。
- 小智云工具发现冒烟：仅在当前终端设置 `MCP_ENDPOINT` 后运行 `.venv/Scripts/python scripts/cloud_bridge_smoke.py`。
- S3 固件补丁（②）：在 `D:/Users/xiaozhi-esp32` 用 ESP-IDF v6.0.1 构建（`python3 scripts/release.py lichuang-dev`），补丁说明放本仓库 `firmware/`。

## 部署与运维（京东云）

- 生产落点**仅京东云** `117.72.118.95`；部署见 `docs/deployment.md`、`docs/operations.md` 与 `deploy/`（systemd / nginx / env 模板，真实凭据不入库）。
- 拓扑：uvicorn 只监听 `127.0.0.1:8300`；nginx 仅公开反代 `GET/HEAD /files/`，`/mcp` 显式 404；安全组不放行 8300。公网经 Cloudflare Tunnel + TLS。
- **更新代码用 scp，不能 `git pull`**（`/opt/hutuji` 无 `.git`）；重启后跑 `deploy/preflight.sh`（应 8/8）+ 公网复验 `/mcp` 404、8300 不可达。
- 生产 venv 精简装，**没有 pytest**：只能跑 `scripts/e2e_local.py` 与直接 import 校验。

## 开发约定

- 文档与注释用**中文**。
- 配置全走环境变量，**无 `.env` 文件**；`output/` 产物名含随机 token、默认 TTL 600s。
- G-code 契约（`docs/protocol.md` §5，`validate_gcode` 强制）：头 `G21/G90/F1500`；落笔 `M3`、抬笔 `M5`；512KiB 上限；坐标限幅 X≤190 Y≤277mm（A4 四周 10mm 边距）；禁 `G28/G30`、禁 `G0` 落点 X0Y0（触发自动换纸）。
- SVG 注入防御：元素白名单 `{svg,g,path,line,polyline}` + 属性白名单 + 拒 DTD/实体（`tests/test_injection.py` 是活闸门，改校验必过它）。
- 路线图：① 仓库奠基 ✅ → ② S3 哑管道 🟡（`05f4563` 已形成，Pipe/abort/fresh-`Idle` 关键闭环已实测；显式 `M30` 与完整断连安全重画已编码/构建，缺该 commit 烧录、断连/换纸实测、P2 S3/S4）→ ③ MCP+云端 🟡（HTTPS 已通、已常驻，官方云已触发 `draw/status/abort`；缺 App 真实语音验收）→ ④ 小批量出货 🔴。真值页：`docs/release-readiness.md`。**当前不可商业出货。**
- 修改项目结构、约定或工作流后，同步更新本文件与 `README.md`、`CLAUDE.md`、`docs/agent-gate.md`（口径变更还要同步 `.cursor/rules/hutuji-agent-rails.mdc`）。
- 批次模板路径：`docs/templates/`（勿写成仓库根下的 `templates/`）。

## Agent 接手与完成定义

1. 先读 `docs/agent-handoff.md` → `docs/agent-gate.md` → `docs/README.md` 真值优先级，然后 `git status --short`。
2. 只改任务范围文件；工作树已有修改属于用户，不得覆盖或回退（对照 `docs/worktree-inventory.md`）。
3. 软件改动必须给出测试命令和结果；硬件结论必须给出设备编号、固件 commit、实测记录。
4. 本仓门禁与 fz 门禁分开报；文档不能替代任一扇门禁。
5. "可出货"仅在 `docs/production-sop.md` 与 `docs/release-checklist.md` 全部签核、且 `bringup-sop` 阶段 0–8 有证据后成立。
6. 交接时更新 `docs/release-readiness.md`，附上 `results/hutuji_gate_last.md`；重大口径变更同步 `docs/agent-handoff.md`。

## 给代理的提醒

- 不要假设任何固件代码在本仓库——运动控制去 `D:/Users/Grbl_Esp32` 查，AI 交互去 `D:/Users/xiaozhi-esp32` 查。
- 不要按过时记忆重开 USB Host / CH9350 / 阿里云 /「仓库无代码」；现行口径见 `docs/agent-handoff.md` 与 `docs/release-readiness.md`。
- 不要把 MCP 验收清单组 0–3 当待办重做（已落地，见 `mcp-server/README.md`）；京东云**已常驻**，勿再"从零部署"。
- 不要自由发挥跳阶段或重写文档地图；默认下一刀见 handoff §6 与 readiness「当前最近一步」。
- 串口识别坑：S3 板载 CH340K 也枚举为 CH340——判定看串口日志自证，别用 `esptool chip_id` 打断运行中固件；S3 的 COM 口会变化，2026-07-28 最新烧录为 COM6，每次仍须重查。
- 项目记忆文件：`C:/Users/zhugu/.kimi-code/memory/projects/hutuji.md`（须与本仓一致；冲突以本仓为准）。
