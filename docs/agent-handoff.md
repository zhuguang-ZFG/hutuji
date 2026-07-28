# Agent 接手防呆页

> **给下一个 AI / 人类代理**：先读本页 5 分钟，再动代码。  
> 状态真值：`release-readiness.md`。契约真值：`protocol.md` **v0.5**。  
> **完成定义 / 可跑门禁**：`agent-gate.md` + `python scripts/agent_gate.py`。  
> **防幻觉 / 禁乱改**：`agent-anti-drift.md` + `agent-edit-allowlist.json`；三仓矩阵 `agent-constraint-matrix.md`；脏树见 `worktree-inventory.md`。  
> 本页只消除「不明所以」——不新增契约，不宣布出货。

## 0. 一句话现状（2026-07-28）

枢纽仓：协议 v0.5、门禁、京东云 `hutuji-mcp`+bridge+nginx+Cloudflare Tunnel 已常驻，preflight 8/8；官方云路径已实际触发 `draw/status/abort`。
Grbl：§9 `e7f373f` 已提交、fz `standard` pass、已烧录；Telnet、keepalive 与 `[ESP901] Paper/Changing=Off` 已实机验证。当前源码 `8d13b5c` 已提交/推送，固化 A4 默认行程、M3/M5 笔控同步与 segment underflow 收敛，并通过 fz `standard`、PlatformIO `release`、COM3 烧录与空闲最小核验（授权、Telnet:23、`[ESP901] Changing=Off`、`$130=210 $131=297`）；仍缺有纸 `Changing=On` 与自动换纸实测。
S3：`feat/hutuji-usb-pipe` 源码 HEAD `27eff73` 已提交并推送；实机烧录基线仍为 `05f4563`。`05f4563` 已完成 Pipe 下载/CRC/逐行流控、fresh-`Idle` 完成判定及 abort 恢复实机回归，含“绘图 fresh `Idle` → 显式 `M30` → 最终 fresh `Idle`”和完整断连安全重画代码，已用 IDF 6.0.1 构建并烧录 COM6（`xiaozhi.bin=0x2ccde0`，app 余 29%，SHA256 `A77C8635...F29F80CC`）；S3-LC-01 启动日志确认 8MB PSRAM、七工具注册、Telnet/授权探测通过及持续 fresh `Idle`，两轮未见 panic。`27eff73` 只新增默认关闭的 8.3 自动测试 URL 参数化与 CMake 注入，默认构建和 dummy URL 自动测试构建均通过，尚未烧录。**尚未做有纸自动换纸/断连重画实测**。
**没有**：App 真实语音、有纸 `Changing=On`/自动换纸验收、完整断连复位/废纸换纸/缓存重发实测、P2 S3/S4、生产签核。
→ **不可商业出货**。  
→ 下一刀：bringup 8.3 完整断连重画 → 有纸 `Changing=On`/显式 `M30` → App 真实语音。
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

1. **先做完整断连**：按 bringup 阶段 8.3 验证复位、废纸换纸与 PSRAM 从头重发；换纸态断连必须停止自动续画。
2. **再做有纸换纸**：验证 `Changing=On`、页尾显式 `M30` 成功、`error:90` 与换纸中 abort。
3. **云端/App**：完成 App 真实语音 → 小智云 → MCP → S3 → Grbl 的端到端验收，并补七工具逐项云端行为回归。
4. **P2/生产**：完成 P2 S3/S4；`bringup-sop.md` 与生产签核按证据补齐。京东云已常驻，勿再「从零部署」。

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
| 批次模板 | `docs/templates/` |
| 京东云模板 | `deploy/`（生产已常驻；更新用 scp，重启后跑 preflight 8/8） |
| 小智智能体/知识库 | `docs/xiaozhi-agent/`（角色提示 + 知识库，控制台粘贴） |
| MCP 服务 | `mcp-server/` |
| S3 补丁说明 | `firmware/m1-usb-pipe.md` |
| Cursor 硬轨 | `.cursor/rules/hutuji-agent-rails.mdc` |
