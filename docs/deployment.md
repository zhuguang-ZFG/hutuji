# 京东云部署

> ③ MCP 服务**唯一**生产落点：京东云 VPS。模板在 `../deploy/`。真实凭据不入库。

## 目标机

| 项 | 值 |
|----|-----|
| 公网 | `117.72.118.95` |
| 规格 | 2 核 4G / 5Mbps（🔬 上行带宽待实测） |
| 角色 | 专机只跑 ③（uvicorn + 小智桥接 + nginx） |
| 冗余 | 无；单点 |

## 拓扑

```
公网客户端 / S3 板
        │ HTTPS（仅 /files）
        ▼
   nginx :443  ──► 127.0.0.1:8300/files
        │
        ✗  /mcp  公网 404（禁止暴露）

小智官方云 WSS
        │
        ▼
 hutuji-cloud-bridge（本机）──mcp-proxy──► 127.0.0.1:8300/mcp
```

## 部署步骤（摘要）

1. Linux + Python ≥3.10；安全组**不放行 8300**；只放 80/443（及 SSH 管理口）。
2. 拉取本仓，在 `mcp-server/` 建 venv，装 `requirements.txt`（生产可不装 dev）。
3. 复制 `deploy/env.example` → 权限 `600` 的 EnvironmentFile（如 `/etc/hutuji/mcp.env`），填入：
   - `HUTUJI_LLM_*`
   - `HUTUJI_PUBLIC_BASE_URL=https://<域名或IP>`
   - `MCP_ENDPOINT`（桥接用；勿进 git）
4. 安装 systemd：`hutuji-mcp.service`、`hutuji-cloud-bridge.service`（见 `../deploy/`）。
5. 安装 nginx：只反代 `GET/HEAD /files/`；其余 404；上 TLS。
6. `systemctl enable --now hutuji-mcp hutuji-cloud-bridge nginx`。
7. 验收：
   - 本机 `curl -sI https://127.0.0.1/files/...`（经 nginx）见 `X-Hutuji-CRC32`
   - 公网 `curl` `/mcp` 不可达
   - `cloud_bridge_smoke` 或小智控制台可见工具（阶段 6）

## 环境变量（生产必填）

| 变量 | 说明 |
|------|------|
| `HUTUJI_HOST` | 保持 `127.0.0.1` |
| `HUTUJI_PORT` | `8300` |
| `HUTUJI_PUBLIC_BASE_URL` | HTTPS 公网前缀（S3 下载用） |
| `HUTUJI_LLM_BASE_URL` / `API_KEY` / `MODEL` | LLM |
| `HUTUJI_OUTPUT_DIR` | 绝对路径，可写 |
| `HUTUJI_OUTPUT_TTL_SECONDS` | 默认 600 |
| `MCP_ENDPOINT` | 仅桥接进程；小智控制台复制 |

## 回滚

1. `systemctl stop hutuji-cloud-bridge hutuji-mcp`
2. 检出上一发布 commit / 恢复上一 venv 或发布包
3. 恢复对应 EnvironmentFile 与 nginx conf
4. 再 `start`；跑 smoke：本机 pytest 子集或 `hutuji_status` 一次
5. 在 `docs/templates/release-manifest.md` 记回滚原因与时间

详细 unit / nginx 片段见 `../deploy/README.md`。运维日常见 `operations.md`。
