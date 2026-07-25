# 开发规范

> 面向软件开发与 AI agent。契约真值：`protocol.md` v0.5。联调顺序：`bringup-sop.md`。状态：`release-readiness.md`。

## 环境

| 层 | 要求 |
|----|------|
| 本仓 MCP（③） | Python ≥3.10；在 `mcp-server/` 建 venv，装 `requirements-dev.txt` |
| S3 固件（②） | `D:/Users/xiaozhi-esp32`；ESP-IDF ≥5.5.2（仓要求 v6.0.2）；板型 `lichuang-dev` |
| Grbl（① 运动） | `D:/Users/Grbl_Esp32`；分支 `fix/panel-hold-after-align-116687e`；改 §9 后必跑门禁 |
| 部署目标 | **仅京东云** VPS：`117.72.118.95`（2核4G / 5Mbps，专机只跑 ③） |

凭据只进进程环境（`HUTUJI_LLM_API_KEY`、`MCP_ENDPOINT` 等）。禁止写入仓库、`.env`、systemd unit 正文。

## 日常命令（本仓）

```powershell
cd D:\Users\hutuji\mcp-server
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts\e2e_local.py          # 须输出 PASS
.venv\Scripts\python -m uvicorn hutuji_mcp.server:app --port 8300
# 小智云工具发现（仅当前终端注入 MCP_ENDPOINT）：
.venv\Scripts\python scripts\cloud_bridge_smoke.py
```

## 代码边界

- **哑管道**：G-code 只在京东云 MCP 服务生成；S3 只下载 + 校验 + 流控转发。
- **双芯片不合并**：固件改动分别在两个 fork；本仓只留协议、补丁说明、部署。
- **Grbl 可改 / 不可改**：换纸机械/时序、运动、授权、U1 协议不改；消息路由/状态字段/WebUI 网络层小加固可改（`protocol.md` §9），改后 `agent_gate.py` overall=pass。
- **禁止碰**：`report_realtime_status()`、`report_state_text()`（见 CLAUDE.md / protocol §9）。

## 评审与证据

改动合并前至少具备：

1. 相关自动化：本仓 pytest / e2e；Grbl 则门禁；S3 则构建日志。
2. 文档同步：契约变更 → `protocol.md`；进度 → `release-readiness.md`；结构/约定 → `AGENTS.md` + 根 `README.md`。
3. 不得用“代码已写”替代“硬件已验证”。硬件结论须设备编号、固件 commit、实测记录。

## 调试入口

| 目标 | 先看 |
|------|------|
| 契约语义 | `protocol.md` §1–§7、§9 |
| 联调卡住 | `bringup-sop.md` 当前阶段判据 |
| 现象排障 | `troubleshooting.md` |
| MCP 实现细节 | `../mcp-server/README.md` |
| S3 补丁 | `../firmware/README.md`、`m1-usb-pipe.md` |
| 京东云上线 | `deployment.md`、`../deploy/README.md` |
