# hutuji — AI 绘图自动换纸写字机

语音说一句话，云端 AI 生成绘图内容，写字机自动画出来、自动换纸。

## 架构（双芯片）

```
用户语音
   │
   ▼
┌─────────────────────┐   WiFi/HTTPS   ┌──────────────────────┐
│ AI 交互板            │ ◄────────────► │ 小智官方云 + MCP 服务  │
│ 立创实战派 ESP32-S3  │                │ (阿里云 VPS)          │
│ xiaozhi-esp32 固件   │ ◄──── HTTP 下载 G-code ────┘          │
└─────────┬───────────┘
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
| 5 | MCP 服务部署 | **阿里云 VPS**（京东云备份/负载分担） |
| 6 | 换纸机构 | 已有成熟商业化方案，由 Grbl 现役固件控制，本项目不改动 |
| 7 | 本仓库定位 | **枢纽仓库**：新代码+协议+文档；两 fork 各自独立 |

## 仓库布局

| 仓库 | 路径 | 角色 |
|------|------|------|
| hutuji（本仓库） | `D:/Users/hutuji` | MCP 服务、协议文档、部署脚本、联调 SOP |
| [Grbl_Esp32](https://github.com/zhuguang-ZFG/Grbl_Esp32) | `D:/Users/Grbl_Esp32` | 运动控制固件（现役分支 `fix/panel-hold-after-align-116687e`） |
| [xiaozhi-esp32](https://github.com/zhuguang-ZFG/xiaozhi-esp32) | `D:/Users/xiaozhi-esp32` | AI 交互固件（板型 `lichuang-dev` S3） |

## 目录

- `docs/protocol.md` — 板间串口协议与 G-code 文件约定（②③ 的共同契约）
- `mcp-server/` — MCP 生成服务（③，待建）
- `firmware/` — S3 板固件补丁说明（②，补丁本体在 xiaozhi-esp32 fork）

## 路线图

1. **① 仓库奠基**：协议文档、目录结构 ← 当前
2. **② 哑管道**：S3 板固件补丁（设备侧 MCP 工具 + HTTP 下载 + 串口流控转发），PC 本地起 HTTP 服务验证「下载 → 串口 → 写字机画出来」
3. **③ 智能端**：MCP 生成服务（LLM 生成内容 → SVG/字体笔画 → G-code），官方云联调，部署阿里云
