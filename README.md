# hutuji — AI 绘图自动换纸写字机

语音说一句话，云端 AI 生成绘图内容，写字机自动画出来、自动换纸。

## 架构（双芯片）

```
用户语音
   │
   ▼
┌─────────────────────┐   WiFi/HTTPS   ┌──────────────────────┐
│ AI 交互板            │ ◄────────────► │ 小智官方云 + MCP 服务  │
│ 立创实战派 ESP32-S3  │                │ (京东云 VPS)          │
│ xiaozhi-esp32 固件   │ ◄─ HTTPS 下载 G-code ─┘               │
└─────────┬───────────┘    (Cloudflare Tunnel 绕过 ICP 拦截)
          │ WiFi Telnet :23（两板经家用路由器，写字机固定 IP）
          ▼
┌─────────────────────┐
│ 运动控制板（ESP32）   │  ← 成熟商业化现役固件
│ Grbl_Esp32 固件      │     含自动换纸系统（M701/M721…）
└─────────────────────┘
```

## 决策记录（2026-07-24，grill-me 逐项确认）

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 芯片架构 | **双芯片**：S3 跑 xiaozhi，独立 ESP32 跑 Grbl；不合并固件 |
| 2 | 板间通讯 | **WiFi Telnet**（方案 E）：写字机 STA 连路由器开 Telnet:23，S3 TCP 直连。USB Host 被 CH334F HUB 堵死、CH9350 仅 HID 不支持 CH340——均否决；拆机 TTL 留作备用 |
| 3 | AI 绘图链路 | **云端生成+转换 G-code**，S3 板只做下载与串口转发（哑管道） |
| 4 | AI 服务 | 小智**官方云**，自定义能力走 MCP 接入点 |
| 5 | MCP 服务部署 | **京东云 VPS 单点**（2核4G / 5Mbps / 公网 `117.72.118.95`，专机只跑 ③）；无云端冗余（2026-07-25 锁定） |
| 6 | 换纸机构 | 已有成熟商业化方案，由 Grbl 现役固件控制，**机械/时序/运动/授权不改动**；消息路由、状态字段、WebUI 网络层等不影响商业出图的小加固可接受（2026-07-25 口径调整，清单见 `docs/protocol.md` §9） |
| 7 | 本仓库定位 | **枢纽仓库**：新代码+协议+文档；两 fork 各自独立 |

## 仓库布局

| 仓库 | 路径 | 角色 |
|------|------|------|
| hutuji（本仓库） | `D:/Users/hutuji` | MCP 服务、协议文档、部署脚本、联调 SOP |
| [Grbl_Esp32](https://github.com/zhuguang-ZFG/Grbl_Esp32) | `D:/Users/Grbl_Esp32` | 运动控制固件（现役分支 `fix/panel-hold-after-align-116687e`，源码 HEAD `8d13b5c`） |
| [xiaozhi-esp32](https://github.com/zhuguang-ZFG/xiaozhi-esp32) | `D:/Users/xiaozhi-esp32` | AI 交互固件（板型 `lichuang-dev` S3） |

## 目录

- `docs/agent-handoff.md` — **Agent 防呆页**（误判表 / 默认下一刀；接手先读）
- `docs/agent-anti-drift.md` — **防幻觉 / 禁乱改**铁律
- `docs/agent-constraint-matrix.md` — 三仓 agent 开发约束矩阵
- `docs/agent-edit-allowlist.json` — 改码白名单（机读）
- `docs/worktree-inventory.md` — 跨仓未提交资产清单
- `docs/agent-gate.md` — **本仓门禁定义**（profile / 禁止宣称）
- `docs/foundation-checklist.md` — ① 基础建设闭环勾选
- `scripts/agent_gate.py` — **可跑门禁**（默认 `hub` → `results/hutuji_gate_last.md`）
- `.cursor/rules/hutuji-agent-rails.mdc` — Cursor 硬轨（防跑偏 / 防幻觉）
- `docs/README.md` — **文档总入口**：新 agent/开发/测试/运维/生产从这里开始
- `docs/release-readiness.md` — 当前真实就绪度与出货阻断项
- `docs/protocol.md` — 板间链路协议与 G-code 文件约定（**v0.5**，②③ 的共同契约）
- `docs/bringup-sop.md` — 阶段 0–9 联调与小批量出货门禁
- `docs/deployment.md` / `docs/operations.md` — 京东云部署与值守
- `docs/templates/` — 逐台测试记录、批次发布清单模板
- `mcp-server/` — MCP 生成服务（③，契约/安全回归、京东云 HTTPS 常驻与官方云实际调用均已有证据；App 真实语音仍待验，见 `mcp-server/README.md`）
- `firmware/` — S3 板固件补丁说明（②，补丁本体在 xiaozhi-esp32 fork）
- `deploy/` — 京东云 systemd/nginx/环境变量模板（真实凭据不入库）

## 路线图

1. **① 仓库奠基**：协议、文档入口、SOP、生产/部署模板、**本仓门禁/硬轨** — ✅ 见 `docs/foundation-checklist.md`
2. **② 哑管道**：S3 设备侧 MCP + 下载校验 + Telnet 流控 — 🟡 源码 HEAD `13057e8` 已提交/推送（`15f73c9` 窗口化流控 S3 → `13057e8` 笔控 `G92 Z0` 旁路 + `ok` 超时兜底）；当前 COM6 板上镜像 `0x2ce890` 内容对应 `13057e8`，2026-07-29 20:41 `app-flash` 成功（esptool `Hash of data verified`，证据 `results/s3-13057e8-com6-flash-evidence-20260729.md`）；2026-07-30 B-12 已归档 S3 自动路径出墨：`G92 Z0` + `Z5.0F10000` 自动落笔，409 行测试件全线连续，页尾 `M30` 后 `state=done`。七工具注册、Telnet/授权与持续 fresh-`Idle` 启动核验通过；Pipe 下载/CRC/窗口化流控、fresh-`Idle` 完成判定与 abort 恢复已实机回归；普通画线断连主路径已实测到废纸 `M30`、`[PaperStatus] 0`、PSRAM 从第 1 行重画、55/55 行完成和 fresh `Idle`；缺有纸 `Changing=On` 分流、P2 S3/S4
3. **③ 智能端**：MCP 生成服务 + 小智官方云 + 京东云 — 🟡 HTTPS、MCP+桥接常驻及官方云 `draw/status/abort` 调用已通；缺 App 真实语音验收
4. **④ 小批量出货**：逐台质检、追溯、合规、客户交付 — 🔴 流程已建，须待阶段 0–8 真实证据后放行

实时状态以 `docs/release-readiness.md` 为准。改本仓先跑 `python scripts/agent_gate.py`；改 Grbl 另跑 fz 门禁。
