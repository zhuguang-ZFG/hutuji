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
| [Grbl_Esp32](https://github.com/zhuguang-ZFG/Grbl_Esp32) | `D:/Users/Grbl_Esp32` | 运动控制固件（现役分支 `fix/panel-hold-after-align-116687e`；实时 HEAD 与板上证据看 `docs/worktree-inventory.md`，不得复制静态 SHA 当当前真值） |
| [xiaozhi-esp32](https://github.com/zhuguang-ZFG/xiaozhi-esp32) | `D:/Users/xiaozhi-esp32` | AI 交互固件（板型 `lichuang-dev` / Waveshare 3.5；实时 HEAD 与板上镜像证据看 `docs/worktree-inventory.md`） |

## 目录

- `docs/agent-handoff.md` — **Agent 防呆页**（误判表 / 默认下一刀；接手先读）
- `docs/agent-anti-drift.md` — **防幻觉 / 禁乱改**铁律
- `docs/agent-constraint-matrix.md` — 三仓 agent 开发约束矩阵
- `docs/agent-edit-allowlist.json` — 改码白名单（机读）
- `docs/worktree-inventory.md` — 跨仓未提交资产清单
- `docs/agent-gate.md` — **本仓门禁定义**（profile / 禁止宣称）
- `docs/foundation-checklist.md` — ① 基础建设闭环勾选
- `scripts/agent_gate.py` — **可跑门禁**（默认 `hub` → `results/hutuji_gate_last.md`）
- `scripts/xiaozhi_console.py` — 小智控制台配置同步 + 官方设备消息 API 的七个 `hutuji.*` 白名单直调（凭据只走环境变量）
- `.cursor/rules/hutuji-agent-rails.mdc` — Cursor 硬轨（防跑偏 / 防幻觉）
- `docs/README.md` — **文档总入口**：新 agent/开发/测试/运维/生产从这里开始
- `docs/release-readiness.md` — 当前真实就绪度与出货阻断项
- `docs/protocol.md` — 板间链路协议与 G-code 文件约定（**v0.5**，②③ 的共同契约）
- `docs/bringup-sop.md` — 阶段 0–9 联调与小批量出货门禁
- `docs/deployment.md` / `docs/operations.md` — 京东云部署与值守
- `docs/templates/` — 逐台测试记录、批次发布清单模板
- `mcp-server/` — MCP 生成服务（③，契约/安全回归、京东云 HTTPS 常驻与真实语音出图均已有证据；当前未闭项见 `docs/release-readiness.md`）
- `firmware/` — S3 板固件补丁说明（②，补丁本体在 xiaozhi-esp32 fork）
- `deploy/` — 京东云 systemd/nginx/环境变量模板（真实凭据不入库）

### BLE 只读诊断辅助面（规划，默认关闭）

正式 Telnet/G-code 契约仍为 v0.5；BLE 使用独立 `BLE-DIAG schema v1`。阶段 A 仅提供 6 字节公开粗粒度广播，不注册 GATT；阶段 B 才提供需本地确认配对、LE Secure Connections + MITM、单 peer、每秒一次读取上限的 22 字节只读诊断快照；阶段 C 由手机/PC 消费广播并显示 `near/unknown` RSSI 近距提示，S3 不做持续扫描。WiFi Telnet :23 仍是唯一正式板间链路。BLE 不承载 G-code，不直接控制电机、笔、换纸、授权、abort 或 job 生命周期；不以 RSSI 冒充距离/方向/UWB/AoA/Apple Precision Finding。当前 S3 `CONFIG_BT_ENABLED` 未开启，A/B/C 实现与共存 HIL 均未验证，量化门槛见 `docs/protocol.md` §1.4.6。

### 外部参考证据纪律

涉及社区、GitHub、上游文档、SDK 或硬件资料时，先查官方来源，再查高质量社区；影响设计的资料登记 URL、版本/commit、访问日期、适用板型与范围、本地复核命令/结果、限制和未验证项，落在相关设计段落或明确链接的 findings。外部资料不能覆盖本仓契约/状态真值，且必须区分上游、本地 fork、源码、构建物、板上固件和实机行为；网络不可用时使用本地留档须标明来源层级与快照日期，不能把社区共识写成实机证据。

## 路线图

1. **① 仓库奠基**：协议、文档入口、SOP、生产/部署模板、本仓门禁/硬轨 — ✅ 见 `docs/foundation-checklist.md`
2. **② 哑管道**：S3 设备侧 MCP + 下载校验 + Telnet 流控 — 🟡 xiaozhi `dcbec6f` 已提交并推送；当前 Waveshare 镜像 ELF `031cafd5…` 已完成触摸、七工具、Telnet、授权、fresh-`Idle` 与控制抽屉低层动作的部分 HIL。**仍缺**：预览确认/取消与暂停/继续/停止/重画/试笔五个屏幕动作、纸面画质目视、断连恢复 `paper_active` 窗口 abort 复测。
3. **③ 智能端**：MCP 生成服务 + 小智官方云 + 京东云 — 🟡 HTTPS、MCP+桥接常驻、真实语音出图和 SPEED-01/设备 PNG 预览运行态已有证据；生产 preflight 26/26。仓库实现已提交，剩余设备预览/控制人机验收与生产签核。

> **生图后端边界（2026-08-17）**：正式 `hutuji_draw` 始终保持「PNG → 中心线描摹 → SVG → G-code」。可选商汤 `sensenova-u1-fast` 只作为 PNG 主后端，失败回退 Agnes，再失败才走 imagefree；禁止为追求速度切成模型直写 SVG。商汤真实 A/B 只在轮换后的新 key 注入生产 600 权限 env 后执行，聊天中出现过的 key 视为泄露，不得使用。
4. **④ 小批量出货**：逐台质检、追溯、合规、客户交付 — 🔴 流程已建，须待阶段 0–8 真实证据后放行

实时状态以 `docs/release-readiness.md` 为准。改本仓先跑 `python scripts/agent_gate.py`；改 Grbl 另跑 fz 门禁。
