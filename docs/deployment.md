# 京东云部署

> ③ MCP 服务**唯一**生产落点：京东云 VPS。模板在 `../deploy/`。真实凭据不入库。  
> **当前**：生产服务已常驻并实机验收；最新 MCP 校验器发布为 `b50a10a`（2026-07-29），
> 证据见 `release-readiness.md`。

## 目标机

| 项 | 值 |
|----|-----|
| 公网 | `117.72.118.95` |
| 规格 | 2 核 4G / 5Mbps |
| 上行实测 | **2026-07-26**：公网拉 512KiB 中位 **1.13s / ~3.72 Mbps**（本机→`http://117.72.118.95/…`，3 轮；测完文件已删） |
| 角色 | 专机只跑 ③（uvicorn + 小智桥接 + nginx） |
| 冗余 | 无；单点 |
| 实机业务 | **2026-07-27**：`hutuji-mcp` + **Cloudflare Tunnel HTTPS**（Let's Encrypt） + `hutuji-cloud-bridge` 已连小智 WSS；`HUTUJI_PUBLIC_BASE_URL=https://hutuji.donglicao.com`；绘图 LLM = llm7 |

## 拓扑

```
公网客户端 / S3 板
        │ HTTPS（hutuji.donglicao.com）
        ▼
   Cloudflare (Let's Encrypt 证书)
        │ Cloudflare Tunnel（QUIC，出站连接绕过京东云 ICP）
        ▼
   cloudflared (localhost)  ──► nginx :80 /files  ──► 127.0.0.1:8300/files
        │
        ✗  /mcp  nginx 404（禁止暴露）

小智官方云 WSS
        │
        ▼
 hutuji-cloud-bridge（本机）──mcp-proxy──► 127.0.0.1:8300/mcp
```

### Cloudflare Tunnel（2026-07-27 上线）

京东云对未备案域名做 HTTP Host + TLS SNI 双重拦截，无法直连 HTTPS。
使用 Cloudflare Tunnel（免费）从 VPS 出站建隧道，完全绕过。

| 项 | 值 |
|----|-----|
| 隧道名 | `hutuji` |
| 域名 | `hutuji.donglicao.com` |
| DNS 记录 | CNAME → `<tunnel-id>.cfargotunnel.com`（已代理） |
| 证书 | Let's Encrypt（Cloudflare 管理），ESP-IDF cert bundle 信任 |
| cloudflared 配置 | `/etc/cloudflared/config.yml`（本地管理模式） |
| 服务 | `systemctl status cloudflared`（enabled，开机自启） |
| 入口规则 | `hutuji.donglicao.com` → `http://localhost:80`；其余 404 |
| Cloudflare 账号 | 域名 `donglicao.com` 托管于 Cloudflare |

> 尝试过的失败方案：Flexible SSL（JD Cloud SNI 拦截）、Origin Rules（付费功能）、Workers（fetch 不允许裸 IP → 1003）。

## 阶段 0：上线前准备（可先做、不启业务）

目的：空出专机、建好目录与用户，**不**把密钥写进 git、**不**对公网开 8300。

1. 京东云控制台安全组：只放行 **22（或 SSH 改口）/ 80 / 443**；确认 **无 8300**。  
2. SSH 用密钥登录（勿把密码写进仓库脚本）。  
3. 将本仓同步到 `/opt/hutuji`。  
4. 执行：`bash /opt/hutuji/deploy/prepare-host.sh`  
5. （若机上有旧杂物）按 `../deploy/README.md`「清理旧环境」停掉无关服务、清空 `output/`。  
6. 勾选本页下方「阶段 0 清单」——勾完只代表**主机坑位就绪**，不代表已上线。

### 阶段 0 清单

- [x] 主机可登录；已建用户 `hutuji`、`/opt/hutuji`、`/etc/hutuji/{mcp,bridge}.env`（600）、`/etc/nginx/ssl/hutuji/`  
- [x] 点名业务已停删（dlc-drawing / MySQL / Redis / qwen-gateway / nginx new-api）；UFW 仅 22/80/443  
- [x] `/opt/hutuji` **本仓代码**就位  
- [x] UFW 仅 22/80/443；8300 未放行（安全组请再复核一眼）  
- [x] `mcp.env` 已填（llm7）；TLS 自签已放 `/etc/nginx/ssl/hutuji/`  
- [x] `bridge.env` 已填真实 `MCP_ENDPOINT`（600；禁入库）

### 实机清理（2026-07-26，用户点名后已执行）

已停并删除：

| 项 | 处置 |
|----|------|
| dlc-drawing + `/opt/dlc-drawing` | unit/目录删除；`chat.donglicao.com` nginx 配置删除 |
| qwen-gateway + `/opt/qwen-gateway` | 进程/目录删除；3002 已释放 |
| MySQL / Redis | 包 purge + 数据目录删除；3306/6379 已释放 |
| nginx new-api / newapi / newapi-managed | sites-available 删除；`/root/newapi-backups` 删除 |
| UFW | 仅保留 22/80/443 |

第二轮（建议可删，已执行）：Docker/containerd、lima 残留与 `/root` 杂目录（openclaw/prometheus/dlc-retire 等）。cloudflared 已重新安装用于 Cloudflare Tunnel。

仍保留：nginx、Tailscale、京东云 agent（jdog/ifrit/jcs）、hutuji 业务。

### 阶段 1 进度（2026-07-26）

| 项 | 状态 | 证据 |
|----|------|------|
| 代码 → `/opt/hutuji` | ✅ | `requirements.txt` 在位；venv IMPORT_OK |
| `mcp.env`（llm7） | ✅ | 本机 `generate_svg` 出 SVG_LEN≈1.4k |
| Cloudflare Tunnel HTTPS | ✅ | `https://hutuji.donglicao.com/files/…` 经隧道到 nginx；Let's Encrypt 证书 |
| `hutuji-mcp` | ✅ | `systemctl active`；`127.0.0.1:8300` |
| `hutuji-cloud-bridge` | ✅ | WSS 已连接；云端 `initialize` + `tools/list` 已进日志 |
| `preflight.sh` 全 PASS | 🟡 计数未复验 | 旧记录 `pass=8 fail=0`（2026-07-26）；但脚本自 `99fcafa` 建档起就是 **9 项**且此后未改过，任何提交过的版本都不可能输出 `pass=8`——须重跑按实际输出（应为 `pass=9 fail=0`）回填 |
| 小智控制台工具可见 | 🔬 | 控制台刷新后应见 `hutuji_draw` 等 |
| 小智知识库 | ✅ | 用户已上传 `docs/xiaozhi-agent/knowledge-base.md`（2026-07-26）；请确认智能体页已**关联** |

> TLS 已切 Cloudflare Tunnel + Let’s Encrypt（2026-07-27），ESP-IDF cert bundle 信任，S3 无需 `-k`。  
> `install-from-repo.sh` 默认清华 PyPI 源（京东云直连 PyPI 易卡住）。

## 首次上线（阶段 1）剩余

1. 小智控制台：确认知识库已**关联到智能体**；刷新 MCP 工具列表；试 `hutuji_status` / 语音「画一只侧脸简笔小猫」。  
2. 角色提示若未贴：`docs/xiaozhi-agent/role-prompt.md`。  
3. 证据写入 `docs/templates/release-manifest.md`。

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
