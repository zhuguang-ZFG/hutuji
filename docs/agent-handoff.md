# Agent 接手防呆页

> **给下一个 AI / 人类代理**：先读本页 5 分钟，再动代码。  
> 状态真值：`release-readiness.md`。契约真值：`protocol.md` **v0.5**。  
> **完成定义 / 可跑门禁**：`agent-gate.md` + `python scripts/agent_gate.py`。  
> **防幻觉 / 禁乱改**：`agent-anti-drift.md` + `agent-edit-allowlist.json`；三仓矩阵 `agent-constraint-matrix.md`；脏树见 `worktree-inventory.md`。  
> 本页只消除「不明所以」——不新增契约，不宣布出货。

## 0. 一句话现状（2026-07-31）

枢纽仓：协议 v0.5、门禁、京东云 `hutuji-mcp`+bridge+nginx+Cloudflare Tunnel 已常驻，preflight 全 PASS（9 项）；官方云路径已实际触发 `draw/status/abort`。**2026-07-31 审查修复批次**（8 文件未提交）：P1-3 httpx→LlmError、P1-4 websockets 脱敏 filter + backoff 复位、P1-5 nginx :80 /files 反代、§9-C′ 治理（allowlist + protocol 行）、release-readiness 同步、3 条回归测试（pytest 118 passed）；**nginx 未部署到 VPS**。
Grbl：§9 `e7f373f` 已提交、fz `standard` pass、已烧录；Telnet、keepalive 与 `[ESP901] Paper/Changing=Off` 已实机验证。当前源码 `8d13b5c` 已提交/推送，固化 A4 默认行程、M3/M5 笔控同步与 segment underflow 收敛，并通过 fz `standard`、PlatformIO `release`、COM3 烧录与空闲最小核验（授权、Telnet:23、`[ESP901] Changing=Off`、`$130=210 $131=297`）；有纸 `Changing=On` 已于 2026-07-30 B-13 验证。**2026-07-31 审查修复**：§9-C′ `Protocol.cpp:753` underflow 恢复置 `State::Cycle`（fz `standard` pass）；**未烧录**。
S3：`feat/hutuji-usb-pipe` 源码 HEAD `f1cf066` 已提交/推送（`c1267ec` WiFi 三修 → `ed5b132` ETA → `15f73c9` 窗口化流控 S3 → `13057e8` 落笔旁路 + `ok` 兜底 → `f1cf066` 默认关闭的 §8.5 abort 取证脚手架，产品行为不变、未烧录）。**实机烧录基线仍是 `13057e8`**。**2026-07-31 审查修复**：`hutuji_job.cc` 暂停超时补 soft reset 冲 planner；**未构建/未烧录**。
**没有**：App 真实语音、换纸中 abort、P2 S3/S4、生产签核。
→ **不可商业出货**。  
→ 下一刀：部署 nginx + 烧录 Grbl/S3 审查修复 → 换纸中 abort → App 真实语音。
→ **禁止**再写一版文档地图当进度；真值页 `release-readiness.md`。

## 1. 三仓与代码在哪

| 仓 | 路径 | 你该改什么 | 你不该假设 |
|----|------|------------|------------|
| hutuji | `D:/Users/hutuji` | MCP、协议、文档、`deploy/`、本仓门禁 | 这里有写字机/S3 固件源码 |
| Grbl_Esp32 | `D:/Users/Grbl_Esp32` | 仅 §9 小改（若任务要求） | 可以改换纸机械/运动/授权 |
| xiaozhi-esp32 | `D:/Users/xiaozhi-esp32` | 哑管道 M2（若任务要求） | 补丁已在 hutuji 仓里 |

本仓 `firmware/` **只有说明**，补丁在 xiaozhi fork（分支名 `feat/hutuji-usb-pipe` 是历史名，链路已是 **WiFi Telnet**）。

## 2. 已锁定、禁止重开的决策（编号 = 根 `README.md`）

1. 双芯片，不合并固件  
2. 板间 = **方案 E：WiFi Telnet :23**（USB Host / CH9350 **已否决**，别再提）  
3. 哑管道：G-code 只在云端 MCP 生成  
4. AI = 小智**官方云** + MCP  
5. 部署 = **仅京东云** `117.72.118.95`（专机只跑 ③）  
6. 换纸机械不动；§9 消息路由/状态/keepalive 可小改且**必跑 fz 门禁**  
7. hutuji = 枢纽仓  

决策编号以根 `README.md` 为准；`AGENTS.md` 已对齐。勿自造「决策 #5 = 换纸」。

## 3. 常见误判（看到就停）

| 误判 | 真相 |
|------|------|
| 「仓库只有文档」 | `mcp-server/` 已可跑；先跑 `scripts/agent_gate.py` |
| 「CRC 还没做 / 还在用 StaticFiles」 | 已自定义 `/files` + `X-Hutuji-CRC32`；见 `mcp-server/README` 组 0 ✅ |
| 「验收清单还要按组 0 重做一遍」 | 组 0–4 已落地并上云；当前缺口是 App 语音与设备端剩余实测 |
| 「所有换纸进度都能从 Telnet 收到」 | **不能**；§9 只定点放开四处 `[PaperStatus]`，且真实结果码到达仍待换纸验证；其余进度看 CH340 |
| 「出图中 status 发 `[ESP901]`/`M704`」 | **禁止**；只发 `?`（单写者） |
| 「abort = 急停」 | 换纸中打不断；见 protocol §4.1 |
| 「PC telnet 旁观 + S3 同时连」 | `MAX_TLNT_CLIENTS=1`，互斥；PC 看 CH340 |
| 「可出货了，文档都齐了」 | 文档 ≠ 证据；见 `release-readiness.md` + `claims_forbidden` |
| 「本仓门禁绿 = Grbl 也绿」 | **否**；Grbl 必须另跑 fz `agent_gate.py` |
| 「记忆文件里写 USB Host / 阿里云」 | **过时**；以本仓为准 |
| 「文档某处写可选/可去掉/可简化，那就删」 | **假许可**；先对账 `protocol.md` §5 / toml / `gcode_gen.py` / `test_contract.py`，冲突以现行定案为准并盖章，禁止照删（anti-drift 铁律7） |
| 「我记得/应该只有一处，改完就行」 | 全称判断必须先全仓 `grep` / 三仓 `git status` 取证，没检索不说「唯一/已清/干净」（anti-drift 铁律8） |
| 「这文档过时了，顺手精简/重写一下」 | 过时只盖章 `supersede`；禁止精简负载、禁止重写文档地图当进度（铁律7 + anti-drift §2.1） |
| 「外仓我熟，直接改」 | 改外仓先读该仓根 `AGENTS.md` + 白名单，不凭记忆；Grbl 改完必跑 fz（anti-drift 铁律8） |

## 4. 动手前强制顺序

1. 本页 → `agent-anti-drift.md` → `worktree-inventory.md`  
2. `release-readiness.md` → `agent-gate.md`  
3. 根 `README.md` 决策表 + `AGENTS.md`  
4. `protocol.md` §1.1 / §1.2 / §2.1 / §4.1 / §5.1 / §9  
5. 三仓 `git status --short` — **不覆盖**用户已有改动；对照 inventory  
6. 按任务打开：`bringup-sop.md` / `mcp-server/README.md` / `deployment.md`  
7. 结束前跑本仓门禁；动过 Grbl 再跑 fz 门禁；更新 inventory / readiness

## 5. 本仓立刻能跑的命令（无硬件）

```powershell
cd D:\Users\hutuji
python scripts\agent_gate.py --profile hub   # 默认；期望 overall=pass

cd D:\Users\hutuji\mcp-server
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts\e2e_local.py    # 期望 PASS
```

小智云冒烟：仅当前终端设 `MCP_ENDPOINT` 后跑 `scripts/cloud_bridge_smoke.py`。  
**禁止**把接入点 / API key 写入仓库、`.env`、systemd unit 正文。

## 6. 工程优先级（默认下一刀）

若用户未指定别的任务，按此序，**禁止跳阶段**：

1. **换纸中 `abort` 分流（需装纸 + 先烧 8.5 脚手架镜像）**：页尾换纸与 `Changing=On` 已有证据（B-12 自动路径出墨、页尾 `M30` → `[PaperStatus] 0` / `state=done`；B-13 `Changing=On` 专测）。**剩下的空白**是换纸态里的 abort：换纸中不得发 reset / `M30`，`error:90` 只当换纸态记录。⚠️ 设备侧 abort 的唯一入口是云端 MCP 工具 `hutuji.abort`（板上无按键/串口/HTTP 触发），而保护窗只有约 10s（B-13 实测 0.14s→10.11s），所以这项在拿到 token 前**只能**靠 B-15 加的默认关闭编译宏 `HUTUJI_AUTO_TEST_ABORT_ON_PAPER` 本地命中窗口；剧本、判据与回刷要求见 `bringup-sop.md` §8.5。
2. **App 真实语音端到端（需用户重取 token）**：App 语音 → 小智云 → MCP → S3 → Grbl 全链验收，并补七工具逐项云端行为回归。**当前阻塞**：控制台 JWT 被服务端作废（`/api/agents` 返回 401 `Invalid token`，JWT 自带 `exp` 仍在），需用户在控制台重取 `localStorage.getItem('token')`；同一 token 也是拿 `/files/` 200 正例的前提。
3. **P2 S4 有纸出图（与第 1 项同一次装纸一起做完）**：0.05mm 真实图样的待打样件已过 `validate_gcode`（551 行 / 13,804 B / CRC32 `ae1b25b2`，见 `results/b14-edge-and-pipeline-probe-evidence-20260730.md`），落纸即可取证；自适应 `Bf` 反压仍未做。
4. **生产签核**：`bringup-sop.md` 与生产/合规签核按证据补齐。京东云已常驻，勿再「从零部署」。

> fz 绿 ≠ 已烧录 ≠ 阶段 2 通过。

## 7. 改完必须同步的文件

| 你改了什么 | 必须更新 |
|------------|----------|
| 契约语义 | `protocol.md`（两侧评审） |
| 进度/证据 | `release-readiness.md` |
| 结构/约定/命令 | `AGENTS.md` + `README.md` + `CLAUDE.md` |
| 门禁定义 | `docs/agent-gate.md` + 必要时 `scripts/agent_gate.py` |
| 防幻觉/白名单 | `docs/agent-anti-drift.md` + `docs/agent-edit-allowlist.json` |
| 跨仓脏树 | `docs/worktree-inventory.md` |
| ① 闭环勾选 | `docs/foundation-checklist.md` |
| ③ 行为/验收 | `mcp-server/README.md` |
| 批次出货 | `docs/templates/*` |

Grbl 任何代码（含 §9）：`python D:/Users/zhugu/fz/scripts/agent_gate.py`，非 pass 不得声称修好。

## 8. 路径速查

| 用途 | 路径 |
|------|------|
| 文档总入口 | `docs/README.md` |
| 本防呆页 | `docs/agent-handoff.md` |
| **本仓门禁** | `docs/agent-gate.md` / `scripts/agent_gate.py` |
| 防幻觉/禁乱改 | `docs/agent-anti-drift.md` |
| 改码白名单 | `docs/agent-edit-allowlist.json` |
| 工作树清单 | `docs/worktree-inventory.md` |
| ① 闭环清单 | `docs/foundation-checklist.md` |
| 状态 | `docs/release-readiness.md` |
| 契约 | `docs/protocol.md` |
| MK38 写字机板 | `docs/hardware/mk38-board.md` + `docs/hardware/MK38-总图.pdf` |
| 实战派 S3（小智） | `docs/hardware/lichuang-szpi-esp32s3-board.md` + `lichuang-szpi-esp32s3-sch.pdf` |
| 联调 SOP | `docs/bringup-sop.md` |
| 白天联调 runbook | `docs/daytime-tuning-runbook.md`（单日取证编排：重取 token→sync→nginx 取证→烧录→装纸→语音/暂停/换纸abort/S4，每步带判据/回退/取证落点；判据引用 bringup-sop 不复制） |
| 批次模板 | `docs/templates/` |
| 京东云模板 | `deploy/`（生产已常驻；更新用 scp，重启后跑 preflight 全 9 项绿） |
| 小智智能体/知识库 | `docs/xiaozhi-agent/`（角色提示 + 知识库，控制台粘贴） |
| MCP 服务 | `mcp-server/` |
| S3 补丁说明 | `firmware/m1-usb-pipe.md` |
| Cursor 硬轨 | `.cursor/rules/hutuji-agent-rails.mdc` |
