# 运维与值守

> 生产落点：京东云 `117.72.118.95`。部署见 `deployment.md`。事件不得靠口头闭环。

## 常驻进程

| unit | 职责 | 健康信号 |
|------|------|----------|
| `hutuji-mcp` | uvicorn：`/mcp` + `/files`（仅本机） | `curl -sf http://127.0.0.1:8300/...` 或 `hutuji_status` |
| `hutuji-cloud-bridge` | 小智 WSS ↔ 本机 MCP | journal 无连续鉴权失败；控制台工具在线 |
| `nginx` | TLS + 仅 `/files` | `https://…/files/` 可达；`/mcp` 404 |

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
| nginx/systemd 已安装副本 | 变更时 | 与 `deploy/` 模板 diff |

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
