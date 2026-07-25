# 京东云部署

> ③ MCP 服务**唯一**生产落点：京东云 VPS。模板在 `../deploy/`。真实凭据不入库。  
> **当前**：模板与主机准备脚本已预置；**实机未验收**（见 `release-readiness.md`）。

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

## 阶段 0：上线前准备（可先做、不启业务）

目的：空出专机、建好目录与用户，**不**把密钥写进 git、**不**对公网开 8300。

1. 京东云控制台安全组：只放行 **22（或 SSH 改口）/ 80 / 443**；确认 **无 8300**。  
2. SSH 用密钥登录（勿把密码写进仓库脚本）。  
3. 将本仓同步到 `/opt/hutuji`。  
4. 执行：`bash /opt/hutuji/deploy/prepare-host.sh`  
5. （若机上有旧杂物）按 `../deploy/README.md`「清理旧环境」停掉无关服务、清空 `output/`。  
6. 勾选本页下方「阶段 0 清单」——勾完只代表**主机坑位就绪**，不代表已上线。

### 阶段 0 清单

- [x] 主机可登录；已建用户 `hutuji`、`/opt/hutuji`、`/etc/hutuji/{mcp,bridge}.env`（600）、`/etc/nginx/ssl/hutuji/`（2026-07-26 登机准备）  
- [ ] `/opt/hutuji` **本仓代码**就位（目前仅空目录 + output 坑位）  
- [ ] 安全组/UFW：确认无 8300；收紧与 ③ 无关的公网端口（见下「共存业务」）  
- [ ] 确认专机策略：决策 #5 为「专机只跑 ③」——实机上仍有其它业务，**停迁须用户点名**  
- [ ] env 去掉 `REPLACE_ME` 并放好 TLS（阶段 1）

### 共存业务（2026-07-26 勘察，未擅自停）

| 服务 | 端口/路径 | 备注 |
|------|-----------|------|
| nginx | 80/443 公网 | 现为 default site；hutuji site 未装 |
| dlc-drawing | 127.0.0.1:8081；`/opt/dlc-drawing` | unit `dlc-drawing.service` |
| qwen-gateway | 0.0.0.0:3002 | 公网暴露 |
| MySQL / Redis | 3306/6379（UFW 限来源） | 疑似其它项目 |
| cloudflared / tailscaled | 本机 | 隧道/组网 |
| UFW 另放行 | 8080/8081/8443/9090 等 | 与「只跑 ③」不符，待你决定是否收口 |

## 首次上线（阶段 1）

1. 填写 `/etc/hutuji/mcp.env`（LLM + `HUTUJI_PUBLIC_BASE_URL=https://117.72.118.95` 或域名）。  
2. 填写 `/etc/hutuji/bridge.env`（`MCP_ENDPOINT`；勿写入 mcp.env）。  
3. 安装 TLS 到 nginx 配置所指路径（默认 `/etc/nginx/ssl/hutuji/`）。  
4. `bash /opt/hutuji/deploy/install-from-repo.sh`  
5. `systemctl enable --now hutuji-mcp hutuji-cloud-bridge nginx`  
6. `bash /opt/hutuji/deploy/preflight.sh` 须 PASS。  
7. 公网验收：  
   - `curl -sI https://117.72.118.95/mcp` **失败/404**  
   - `https://…/files/…` 经 nginx；响应可见 `X-Hutuji-CRC32`（有有效文件时）  
8. 小智控制台工具发现 / `hutuji_status`（bringup 阶段 6）。  
9. 证据写入 `docs/templates/release-manifest.md`。

## 环境变量

| 文件 | 变量 | 说明 |
|------|------|------|
| `mcp.env` | `HUTUJI_HOST/PORT` | 服务内默认仍绑 127.0.0.1；unit 已写死 loopback |
| `mcp.env` | `HUTUJI_PUBLIC_BASE_URL` | HTTPS 公网前缀（S3 下载） |
| `mcp.env` | `HUTUJI_LLM_*` | LLM |
| `mcp.env` | `HUTUJI_OUTPUT_*` | 产出目录与 TTL |
| `bridge.env` | `MCP_ENDPOINT` | 仅桥接；小智控制台复制 |
| `bridge.env` | `HUTUJI_LOCAL_MCP_URL` | 默认 `http://127.0.0.1:8300/mcp` |

## 回滚

1. `systemctl stop hutuji-cloud-bridge hutuji-mcp`  
2. 检出上一发布 commit / 恢复上一 venv  
3. 恢复对应 EnvironmentFile 与 nginx conf  
4. 再 `start`；跑 `preflight.sh` + 一次 `hutuji_status`  
5. 在 `docs/templates/release-manifest.md` 记回滚原因与时间  

详细命令见 `../deploy/README.md`。运维日常见 `operations.md`。
