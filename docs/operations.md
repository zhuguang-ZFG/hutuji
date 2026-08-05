# 运维与值守

> 生产落点：京东云 `117.72.118.95`。部署见 `deployment.md`。事件不得靠口头闭环。

## 常驻进程

| unit | 职责 | EnvironmentFile | 健康信号 |
|------|------|-----------------|----------|
| `hutuji-mcp` | uvicorn：`/mcp` + `/files`（仅本机） | `/etc/hutuji/mcp.env` | `preflight.sh`；或本机 curl `:8300` |
| `hutuji-cloud-bridge` | 小智 WSS ↔ 本机 MCP | `/etc/hutuji/bridge.env` | journal 无连续鉴权失败；控制台工具在线 |
| `nginx` | TLS + 仅 `/files` | — | `https://…/files/` 可达；`/mcp` 404 |

上线前准备 / 安装脚本：`../deploy/prepare-host.sh`、`install-from-repo.sh`、`preflight.sh`。

## 监控（最低集）

- 磁盘：`HUTUJI_OUTPUT_DIR` 与根分区（TTL 应对涨，但仍告警）
- 进程：上述三个 unit `active`
- 证书：TLS 到期前 ≥14 天告警
- 🔬 可选：5 分钟一次本机 `hutuji_status`；公网 HEAD 探测（勿扫枚举 `/files`）

## 日志

```bash
journalctl -u hutuji-mcp -u hutuji-cloud-bridge -n 200 --no-pager
```

桥接与 MCP **不得**把完整 `MCP_ENDPOINT` / API key 打进日志。若发现泄露：轮换密钥 + 记事件。

## 备份

| 内容 | 频率 | 说明 |
|------|------|------|
| 本仓发布 commit / 构建产物哈希 | 每发布 | 见 release-manifest |
| EnvironmentFile 密文侧 | 变更时 | 只存密钥保险柜，不进 git |
| nginx/systemd 已安装副本 | 变更时 | 与 `deploy/` 模板 diff。**必须 LF 归一化再比**（本仓工作副本是 CRLF）：`tr -d '\r' < deploy/nginx-hutuji.conf \| md5sum` 对解引用后的 `/etc/nginx/sites-enabled/hutuji`；`readlink -f` 必须精确得到 `/etc/nginx/sites-available/hutuji`。只检查“是符号链接”会漏掉指向旧配置（详见 `deployment.md`） |

EnvironmentFile 核对不得 `source`/`eval`：只解析 `KEY=VALUE`、注释/空行和可选成对引号；重复键按 systemd 的**最后一次赋值**取有效值。`HUTUJI_PUBLIC_BASE_URL` 只允许 HTTPS 默认 443 或显式 `:443`。

`output/` G-code **不备份**（短 TTL、可再生成）。

## 事件分级与响应

| 级 | 例子 | 动作 |
|----|------|------|
| P1 | `/mcp` 公网可达；密钥泄露 | 立刻关端口/轮换；事后写事件 |
| P2 | 桥接断、出图全失败 | 重启 bridge/mcp；查上游 LLM/小智 |
| P3 | 单次 draw 失败、TTL 404 | 查限流/描述/磁盘；不升级除非复现 |

事件记录至少含：时间、影响、commit、处置、是否需客户通知。

## 变更窗口

- 生产变更绑定 `release-checklist.md`；禁止无清单热改协议相关行为。
- 改完跑：本机契约回归（若改代码）+ 一次真实 `hutuji_status`（若改部署）。
- nginx 变更顺序固定：同步 `sites-available` → 精确重建 `sites-enabled/hutuji` 链接 → `nginx -t` → reload → 当前 15 项 preflight。活动 localhost `:80` 探针必须在 reload 后运行；`bdbb107` 的 15/15 只证明 2026-08-05 当次部署，不证明后续配置。
