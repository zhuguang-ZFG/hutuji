# Agent 接手防呆页

> **给下一个 AI / 人类代理**：先读本页 5 分钟，再动代码。  
> 状态真值：`release-readiness.md`。契约真值：`protocol.md` **v0.5**。  
> **完成定义 / 可跑门禁**：`agent-gate.md` + `python scripts/agent_gate.py`。  
> 本页只消除「不明所以」——不新增契约，不宣布出货。

## 0. 一句话现状（2026-07-26）

枢纽仓 `hutuji` 已有：协议 v0.5、文档地图、**本仓门禁**（`scripts/agent_gate.py`）、京东云 `deploy/` 模板、③ MCP 本地加固 + 小智云工具发现。  
**没有**：Grbl §9 经 fz 门禁的完成宣称、S3 M2、硬件联调证据、京东云实机、出货签核。  
→ **不可商业出货**。  
→ 用户未指定任务时：按 §6 工程序；**禁止**再写一版文档地图当进度。  
→ ① 基础建设闭环清单：`foundation-checklist.md`（须门禁 `hub` 绿才算勾完）。

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
| 「验收清单还要按组 0 重做一遍」 | 组 0–2、3.2–3.4 **代码已落地**；下一步是**组 4 京东云** |
| 「Telnet 能收到 `[PaperStatus]`」 | **不能**，除非 §9-B1′ 落地且实机验证；源码标记齐 ≠ 已完成 |
| 「出图中 status 发 `[ESP901]`/`M704`」 | **禁止**；只发 `?`（单写者） |
| 「abort = 急停」 | 换纸中打不断；见 protocol §4.1 |
| 「PC telnet 旁观 + S3 同时连」 | `MAX_TLNT_CLIENTS=1`，互斥；PC 看 CH340 |
| 「可出货了，文档都齐了」 | 文档 ≠ 证据；见 `release-readiness.md` + `claims_forbidden` |
| 「本仓门禁绿 = Grbl 也绿」 | **否**；Grbl 必须另跑 fz `agent_gate.py` |
| 「记忆文件里写 USB Host / 阿里云」 | **过时**；以本仓为准 |

## 4. 动手前强制顺序

1. 本页 → `release-readiness.md` → `agent-gate.md`  
2. 根 `README.md` 决策表 + `AGENTS.md`  
3. `protocol.md` §1.1 / §1.2 / §2.1 / §4.1 / §5.1 / §9  
4. `git status --short` — **不覆盖**用户已有改动  
5. 按任务打开：`bringup-sop.md` / `mcp-server/README.md` / `deployment.md`  
6. 结束前跑本仓门禁；动过 Grbl 再跑 fz 门禁

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

1. `D:/Users/Grbl_Esp32`：`protocol.md` §9 A / B1′ / B2′ → **fz** `agent_gate.py` overall=pass → 烧录  
2. `D:/Users/xiaozhi-esp32`：M2 哑管道 → IDF 构建  
3. `bringup-sop.md` 阶段 0 起留真实设备证据  
4. 京东云按 `deployment.md` + `deploy/` 上线并验收 `/mcp` 不上公网  

> 注意：Grbl 工作树若已有 §9 相关未提交改动，属于用户资产——核对、跑 fz、更新状态，**不要** `git checkout` 清掉，也**不要**在 fz 非 pass 时宣称完成。

## 7. 改完必须同步的文件

| 你改了什么 | 必须更新 |
|------------|----------|
| 契约语义 | `protocol.md`（两侧评审） |
| 进度/证据 | `release-readiness.md` |
| 结构/约定/命令 | `AGENTS.md` + `README.md` + `CLAUDE.md` |
| 门禁定义 | `docs/agent-gate.md` + 必要时 `scripts/agent_gate.py` |
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
| ① 闭环清单 | `docs/foundation-checklist.md` |
| 状态 | `docs/release-readiness.md` |
| 契约 | `docs/protocol.md` |
| 联调 SOP | `docs/bringup-sop.md` |
| 批次模板 | `docs/templates/` |
| 京东云模板 | `deploy/` |
| MCP 服务 | `mcp-server/` |
| S3 补丁说明 | `firmware/m1-usb-pipe.md` |
| Cursor 硬轨 | `.cursor/rules/hutuji-agent-rails.mdc` |
