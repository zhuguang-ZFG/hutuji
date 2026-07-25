# AGENTS.md — hutuji（写字机）

> 本文件面向 AI 编码代理。阅读前请假设自己对本项目一无所知。

## 项目概览

**hutuji** 是一台 **AI 绘图自动换纸写字机**：语音指令 → 云端 AI 生成绘图内容 → 写字机执行并自动换纸。

- 仓库：`https://github.com/zhuguang-ZFG/hutuji`（本地 `origin` 已指向此处）
- 本仓库是**枢纽仓库**：放 MCP 生成服务、协议文档、接线图、部署脚本、联调 SOP。固件本体在两个独立 fork 中，不向本仓库合并。
- 架构与全部已确认决策见 `README.md` 决策记录表；板间契约见 `docs/protocol.md`（**v0.5**）。这两份文件是权威来源，动手前先读。
- 全部资料入口见 `docs/README.md`；当前真实进度与阻断项见 `docs/release-readiness.md`。不得把“代码已写”表述成“硬件已验证/可出货”。
- 要动手实测/联调，按 `docs/bringup-sop.md`（9 阶段，逐阶段有通过判据，未过不得进下一阶段）。
- 外部来源/上游服务约束（含 LLM 限流阈值）查 `docs/research.md` 的「外部参考索引」。

## 三仓布局

| 仓库 | 本地路径 | 角色 | 当前基线 |
|------|----------|------|----------|
| hutuji（本仓库） | `D:/Users/hutuji` | 枢纽：新代码+协议+文档 | 首个提交奠定结构 |
| Grbl_Esp32（fork） | `D:/Users/Grbl_Esp32` | 运动控制+换纸（**成熟商业现役固件**） | 分支 `fix/panel-hold-after-align-116687e`，HEAD `c19a382` |
| xiaozhi-esp32（fork） | `D:/Users/xiaozhi-esp32` | AI 交互（板型 `lichuang-dev`，立创实战派 ESP32-S3） | main `38bc4f4` |

## 已锁定的架构决策（2026-07-24）

1. **双芯片**：S3 跑 xiaozhi（AI/语音/WiFi），独立 ESP32 跑 Grbl（运动+换纸）。禁止合并固件。
2. **板间通讯 = WiFi Telnet（方案 E）**：写字机 Grbl WebUI Telnet :23，STA 连家用路由器 + 固定 IP；S3 TCP 客户端直连，断线指数退避重连。S3 原生 USB 被板载 CH334F HUB 占为设备口（Host 不可行）；CH9350 仅 HID 不枚举 CH340（已否决）；拆机 TTL 仅备用（`docs/hardware.md`）。
3. **哑管道原则**：G-code 在云端（京东云 VPS 的 MCP 服务）生成并转换完毕，S3 板只下载+校验+流控转发，不做任何生成/编辑。
4. **AI 服务用小智官方云**，自定义能力走 MCP 接入点 + 设备侧 MCP 工具。
5. **换纸机械与逻辑不动**——现役固件 `PaperSystem.cpp`（M701/M704/M721/M30）已是成熟商业方案。
6. **Grbl 侧小改口径（2026-07-25 调整）**：换纸机械/时序、运动、授权、U1-Grbl 协议**一律不改**；**消息路由、状态字段、WebUI 网络层**等不影响商业出图行为的小加固**可接受**，改后必过门禁。待实施清单见 `docs/protocol.md` §9（keepalive + `[PaperStatus]` 路由 + `Changing` 字段，约 12 行）。

## 硬规则

- **改 `D:/Users/Grbl_Esp32` 的 G-code/运动/U1-Grbl 协议相关代码后，必须自己跑**：
  `python D:/Users/zhugu/fz/scripts/agent_gate.py`（设好 `FZ_ROOT`/`GRBL_ROOT`），overall 非 pass 不得声称修好。细则：`D:/Users/zhugu/fz/docs/AGENT_VIBE_CODING.md`。
- **凭据禁入库**：`D:/Downloads/VPS.txt` 含 VPS 明文密码与 API key，绝不复制、引用、提交到任何仓库。

## 构建与测试

本仓库已有 `mcp-server/` Python 服务；无硬件测试命令：

- 安装开发依赖：在 `mcp-server/` 执行 `.venv/Scripts/pip install -r requirements-dev.txt`。
- 契约/安全回归：`.venv/Scripts/python -m pytest -q`。
- MCP → G-code → 假 Grbl 闭环：`.venv/Scripts/python scripts/e2e_local.py`，必须输出 `PASS`。
- 小智云工具发现冒烟：仅在当前终端设置 `MCP_ENDPOINT` 后运行 `.venv/Scripts/python scripts/cloud_bridge_smoke.py`；真实接入点严禁写入仓库或 `.env`。
- S3 固件补丁（②）：在 `D:/Users/xiaozhi-esp32` 用 ESP-IDF 构建，补丁说明放本仓库 `firmware/`。

## 开发约定

- 文档与注释用**中文**。
- 路线图：① 仓库奠基 → ② S3 哑管道 → ③ MCP+云端 → ④ 小批量商业出货。当前：①文档地图与京东云部署模板已齐；② M2/构建/实机未完成；③本地与工具发现通过、京东云未部署；④仅流程就绪。
- 修改项目结构、约定或工作流后，同步更新本文件与 `README.md`。
- 生产落点仅京东云 `117.72.118.95`；部署见 `docs/deployment.md` 与 `deploy/`。

## Agent 接手与完成定义

1. 先读 `docs/README.md` 的真值优先级与角色路由，再看 `git status --short`。
2. 只改任务范围文件；工作树已有修改属于用户，不得覆盖或回退。
3. 软件改动必须给出测试命令和结果；硬件结论必须给出设备编号、固件 commit、实测记录。
4. 涉及 Grbl G-code/运动/U1 协议仍须跑上文强制门禁；文档不能替代门禁。
5. “可出货”仅在 `docs/production-sop.md` 阶段 9 与 `docs/release-checklist.md` 全部签核后成立。
6. 交接时更新 `docs/release-readiness.md`，列清已完成、证据、未完成和下一条命令。

## 给代理的提醒

- 不要假设任何固件代码在本仓库——运动控制去 `D:/Users/Grbl_Esp32` 查，AI 交互去 `D:/Users/xiaozhi-esp32` 查。
- 项目记忆文件：`C:/Users/zhugu/.kimi-code/memory/projects/hutuji.md`（决策日志）。
