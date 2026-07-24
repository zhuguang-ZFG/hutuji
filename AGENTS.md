# AGENTS.md — hutuji（写字机）

> 本文件面向 AI 编码代理。阅读前请假设自己对本项目一无所知。

## 项目概览

**hutuji** 是一台 **AI 绘图自动换纸写字机**：语音指令 → 云端 AI 生成绘图内容 → 写字机执行并自动换纸。

- 仓库：`https://github.com/zhuguang-ZFG/hutuji`（本地 `origin` 已指向此处）
- 本仓库是**枢纽仓库**：放 MCP 生成服务、协议文档、接线图、部署脚本、联调 SOP。固件本体在两个独立 fork 中，不向本仓库合并。
- 架构与全部已确认决策见 `README.md` 决策记录表；板间契约见 `docs/protocol.md`。这两份文件是权威来源，动手前先读。

## 三仓布局

| 仓库 | 本地路径 | 角色 | 当前基线 |
|------|----------|------|----------|
| hutuji（本仓库） | `D:/Users/hutuji` | 枢纽：新代码+协议+文档 | 首个提交奠定结构 |
| Grbl_Esp32（fork） | `D:/Users/Grbl_Esp32` | 运动控制+换纸（**成熟商业现役固件**） | 分支 `fix/panel-hold-after-align-116687e`，HEAD `c19a382` |
| xiaozhi-esp32（fork） | `D:/Users/xiaozhi-esp32` | AI 交互（板型 `lichuang-dev`，立创实战派 ESP32-S3） | main `38bc4f4` |

## 已锁定的架构决策（2026-07-24）

1. **双芯片**：S3 跑 xiaozhi（AI/语音/WiFi），独立 ESP32 跑 Grbl（运动+换纸）。禁止合并固件。
2. **板间通讯 = USB Host**：S3 原生 USB（Host 模式）+ `usb_host_ch34x_vcp` 枚举写字机的 CH340，115200 8N1 透传。不用蓝牙、不用 WiFi 走正式链路；TTL 飞线仅备用；WiFi 仅 Grbl Telnet 调试。
3. **哑管道原则**：G-code 在云端（阿里云 VPS 的 MCP 服务）生成并转换完毕，S3 板只下载+校验+流控转发，不做任何生成/编辑。
4. **AI 服务用小智官方云**，自定义能力走 MCP 接入点 + 设备侧 MCP 工具。
5. **换纸机械与逻辑不动**——现役固件 `PaperSystem.cpp`（M701/M704/M721/M30）已是成熟商业方案。

## 硬规则

- **改 `D:/Users/Grbl_Esp32` 的 G-code/运动/U1-Grbl 协议相关代码后，必须自己跑**：
  `python D:/Users/zhugu/fz/scripts/agent_gate.py`（设好 `FZ_ROOT`/`GRBL_ROOT`），overall 非 pass 不得声称修好。细则：`D:/Users/zhugu/fz/docs/AGENT_VIBE_CODING.md`。
- **凭据禁入库**：`D:/Downloads/VPS.txt` 含 VPS 明文密码与 API key，绝不复制、引用、提交到任何仓库。

## 构建与测试

本仓库目前只有文档，无可构建代码。后续：

- `mcp-server/`（③）：Python 服务，构建/测试命令落地后补记于此。
- S3 固件补丁（②）：在 `D:/Users/xiaozhi-esp32` 用 ESP-IDF 构建，补丁说明放本仓库 `firmware/`。

## 开发约定

- 文档与注释用**中文**。
- 路线图：① 仓库奠基（协议文档）→ ② S3 哑管道固件补丁 → ③ MCP 服务+云端联调。当前处于 ①。
- 修改项目结构、约定或工作流后，同步更新本文件与 `README.md`。

## 给代理的提醒

- 不要假设任何固件代码在本仓库——运动控制去 `D:/Users/Grbl_Esp32` 查，AI 交互去 `D:/Users/xiaozhi-esp32` 查。
- 项目记忆文件：`C:/Users/zhugu/.kimi-code/memory/projects/hutuji.md`（决策日志）。
