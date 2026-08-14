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
| [Grbl_Esp32](https://github.com/zhuguang-ZFG/Grbl_Esp32) | `D:/Users/Grbl_Esp32` | 运动控制固件（现役分支 `fix/panel-hold-after-align-116687e`，源码 HEAD `966723b`；当前板上行为基线 `f5c8985`，`966723b` 仅修注释、未重烧） |
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
- `scripts/xiaozhi_console.py` — 小智控制台配置同步 + 官方设备消息 API 的七个 `hutuji.*` 白名单直调（凭据只走环境变量）
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
2. **② 哑管道**：S3 设备侧 MCP + 下载校验 + Telnet 流控 — 🟡 源码 HEAD `68a1eef`（R9/R10 S3 修复的当前收口提交）已提交/推送，并于 2026-08-13 13:31 CST app-flash 到 S3-LC-01（COM12）；板上启动 ELF SHA 前缀 `2c17f4e27` 与发布镜像一致，七工具注册、WiFi、Telnet、授权探针、持续 fresh-`Idle` 及写字机 13:46 CST 上电后连接/banner/授权探测全链路已复核。标准 `releases/v2.4.0_lichuang-dev.zip` 已重建（SHA256 `F31FA589...C3D3`，68a1eef 版；上一轮 zip 已另名 `releases/v2.4.0_lichuang-dev.pre-1b05f99-20260813.zip`），8 月 1 日旧包另名保留。2026-08-03 的 S4 证据仍使用较早的 `ae4b524`，不能替代当前 HEAD 的窗口应答配对和恢复路径回归。历史自动路径出墨、换纸、断连重发与 §8.5 abort 证据继续有效。**仍缺**：当前 HEAD 的真实下载出图/窗口应答配对、纸面实物画质人工目视、自适应 `Bf` 反压
3. **③ 智能端**：MCP 生成服务 + 小智官方云 + 京东云 — 🟡 HTTPS、MCP+桥接常驻及官方云历史 `draw/status/abort` 调用已有证据；**2026-08-14 生产应用基线推进到 `1082400`**（第十二轮 R12 修复 13:57–13:59 CST 部署，8 个产物 LF-md5 对齐上机，含桥接超大整数 id fail-open 收口 + SVG 白名单大小写敏感化 + preflight 第 26 项；前一日第九+十轮 R9/R10 已随 `5104b93` 上机），生产 preflight **26 项全绿**（`pass=26 fail=0 expected=26`，AGENTS.md「当前 26 项」口径；含生成链与位图描摹链双自检、unit 一致性与两条 ExecStartPre/主进程新鲜度、应用文档端点关闭、隧道兜底 404、「在跑的 nginx 确实是本次配置」+「worker 新鲜度且无 `[emerg]`」、公网 HTTPS:443 `/files` 真产物 200 + `/mcp` 404、is-enabled 开机自启与 8300 监听者身份核验），公网 `/mcp` 404、直连 `:80 /files` 403、8300 不可达，真实产物经 HTTPS 200/243B/CRC `775b4f31` 且访问日志已记录真实公网客户 IP。2026-08-08 同轮修掉一次 nginx **reload 静默失败**（限速 key 不能热改共享内存区，`restart` 才生效；此前 19 项全绿而服务跑着旧配置）。按 ASGI socket 对端判定的 `/mcp` loopback 应用层守卫已随 `8184860` 部署上线并在生产逐项实测：loopback `initialize` 200 且 `tools/list` 三工具齐全，同机非 loopback 对端伪造 `Host` 打 `/mcp` 得 **404（正文恰 9 字节 `not found`，即守卫路径）**（此向量打的是**同一份已部署代码的临时实例** `--host 0.0.0.0 --port 8399`、探完即杀；生产 unit 只绑 `127.0.0.1:8300`，非 loopback 根本连不上、`curl` 回 `000`），同对端打 `/files/nope.gcode` 仍是应用 404（`{"detail":"not found"}`），公网 HTTPS `/mcp` 404；守卫只兜控制面，不替代 unit `--host 127.0.0.1`、安全组与 nginx 三层。Agnes 生成链已恢复；官方设备消息 API 客户端已落地但缺真实设备 token 验证，App 两步语音仍待复验
4. **④ 小批量出货**：逐台质检、追溯、合规、客户交付 — 🔴 流程已建，须待阶段 0–8 真实证据后放行

实时状态以 `docs/release-readiness.md` 为准。改本仓先跑 `python scripts/agent_gate.py`；改 Grbl 另跑 fz 门禁。
