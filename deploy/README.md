# deploy/ — 京东云部署模板（已上线，模板可重建）

> 目标机：`117.72.118.95`（2核4G / 5Mbps，专机只跑 ③）。  
> 真实密钥、密码、`MCP_ENDPOINT` **禁止**写入本目录已跟踪文件。  
> 规范：`../docs/deployment.md`；值守：`../docs/operations.md`。

## 状态

| 项 | 状态 |
|----|------|
| 本目录模板 / 脚本 | ✅ 已预置（空机重建从 `prepare-host.sh` 起） |
| VPS 实机部署与验收 | ✅ 已部署并常驻（`117.72.118.95`，HTTPS 经 Cloudflare Tunnel）——真值以 `../docs/release-readiness.md` 为准；本节不重复记状态 |

> 已上线机器**更新代码用 `scp`，不能 `git pull`**（`/opt/hutuji` 无 `.git`）；
> 重启后跑 `preflight.sh` 并按实际输出回填计数（脚本**当前 14 项**：9 项应用/权限 + 5 项 nginx 层）。
> 下面的「首次上线」流程保留给**重建/换机**，不是待办。
>
> **scp `nginx-hutuji.conf` 有两个坑**（2026-07-31 实测，详见 `../docs/deployment.md`「nginx 站点收敛」）：
> ① 本仓工作副本是 **CRLF**，上机后先 `sed -i 's/\r$//'`，否则与线上 md5 永远对不上（漂移判据用
> `tr -d '\r' … | md5sum`，当前双方 `5a424341…`）；
> ② 只 `cp` 到 `sites-available` **可能什么都没生效**——`nginx.conf` 只 include `sites-enabled/*`，
> 而 `sites-enabled/hutuji` 曾是**普通文件**而非符号链接。改完务必
> `ls -l /etc/nginx/sites-enabled/hutuji` 确认是链接，再 `nginx -t && systemctl reload nginx`。

## 文件

| 文件 | 用途 |
|------|------|
| `prepare-host.sh` | **上线前**：建用户/目录/空 env 壳、打印安全组要求 |
| `install-from-repo.sh` | 代码到位后：venv + systemd + nginx 站点 |
| `preflight.sh` | 本机冒烟（loopback、权限、未公网听 8300） |
| `env.mcp.example` | → `/etc/hutuji/mcp.env`（LLM + `/files` 相关） |
| `env.bridge.example` | → `/etc/hutuji/bridge.env`（仅 `MCP_ENDPOINT`） |
| `hutuji-mcp.service` | uvicorn 固定 `127.0.0.1:8300` |
| `hutuji-cloud-bridge.service` | 小智 WSS 桥接 |
| `nginx-hutuji.conf` | 仅 `/files`；显式 404 `/mcp` |

## 阶段 0：主机准备（密钥未齐也可先做）

在空机或待清理机上（root）：

```bash
# 1) 把本仓同步到 /opt/hutuji（git clone / rsync，勿带本地 .venv）
# 2) 准备目录与用户
bash /opt/hutuji/deploy/prepare-host.sh
# 3) 京东云控制台：安全组只放 22/80/443，确认无 8300
```

做完即「为未来上线留好坑位」；此时**不要** `enable --now` 业务 unit（env 仍是 REPLACE_ME）。

## 首次上线

1. 填 `/etc/hutuji/mcp.env`、`bridge.env`（`chmod 600`）。  
2. 放 TLS 到 `/etc/nginx/ssl/hutuji/{fullchain,privkey}.pem`（或改 nginx 路径）。  
3. `bash /opt/hutuji/deploy/install-from-repo.sh`  
4. `systemctl enable --now hutuji-mcp hutuji-cloud-bridge nginx`  
5. `bash /opt/hutuji/deploy/preflight.sh`  
6. 公网：`curl -sI https://117.72.118.95/mcp` 须失败；`/files/` 走 HTTPS。  
7. 小智控制台 `tools/list`（阶段 6）；证据写入 `docs/templates/release-manifest.md`。

## 清理旧环境（若机上曾装过杂物）

```bash
systemctl disable --now hutuji-mcp hutuji-cloud-bridge 2>/dev/null || true
# 确认无其它业务占用后再删；output/ 可清，密钥文件勿进 git
rm -rf /opt/hutuji/mcp-server/output/*
# 保留 /etc/hutuji/*.env（若已填好）；否则按 example 重建
```

验收铁律：公网不能访问 `/mcp`；8300 不进安全组。
