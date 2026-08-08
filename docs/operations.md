# 运维与值守

> 生产落点：京东云 `117.72.118.95`。部署见 `deployment.md`。事件不得靠口头闭环。

## 常驻进程

| unit | 职责 | EnvironmentFile | 健康信号 |
|------|------|-----------------|----------|
| `hutuji-mcp` | uvicorn：`/mcp` + `/files`（仅本机）；`/mcp` 另有 socket 对端 loopback 守卫（2026-08-09 起在生产运行） | `/etc/hutuji/mcp.env` | `preflight.sh`；或本机 curl `:8300`；非 loopback 对端打 `/mcp` 须 404 且正文恰 9 字节 `not found`（守卫路径，区别于 `/files` 的 `{"detail":"not found"}`） |
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
- nginx 变更顺序固定：同步 `sites-available` → 精确重建 `sites-enabled/hutuji` 链接 → `nginx -t` → reload（**改了 `limit_req` 的 key 或 zone 就必须 `restart`，见下条**）→ 当前 21 项 preflight。活动 localhost `:80` 探针必须在 reload/restart 后运行；`2026-08-08` 的 **21/21** 只证明当次部署，不证明后续配置（2026-08-07 的 19/19 与 `69c2198` 的 17/17 均已被 supersede）。**`:80` 兜底行为按 `$http_x_forwarded_proto` 分流**：隧道回源（带 `https`）须 404、真明文直连须 301。改这段时两个方向都要验——旧模板无条件 301 曾造成自指死循环。
- **`limit_req` 的 key/zone 改了就必须 `systemctl restart nginx`**：nginx 不允许热改共享内存区的 key，master 会写 `[emerg] limit_req "<zone>" uses the "$new" key while previously it used the "$old" key` 然后**继续用旧配置服务**。这条失败路径对三道常规检查全部隐形：`nginx -t` 是新进程、没有继承的区，必过；`systemctl reload` 只发信号不等受理，恒 `status=0/SUCCESS`；`is-active` 仍 active。2026-08-08 生产实测跑了 37 分钟旧配置而 19 项 preflight 全绿。现由 preflight 第 16–17 项覆盖：带唯一标记的 loopback 探针须出现在 `hutuji-access.log`（旧配置无该 `access_log`，请求会落到 http 级默认 combined 日志），且所有 worker 启动时间不早于配置 mtime、启动后 error.log 无 `[emerg]`。**手工核查用 `stat -L`**——不解引用会读到 `ln -sfn` 建链接的时刻而不是配置本体的 mtime。
- preflight **只有 pass / fail 两种状态，没有 skip**：历史上的 `OPTIONAL_SKIP` 让「公网访问 `:80 /files` 被拒」这条铁律可以从未被验证过却报全绿（实测 `pass=14 fail=0 skip=1` 输出 PASS），已删除。放行判据是 `PASS==EXPECTED_CHECKS && FAIL==0`，输出为 `pass=N fail=N expected=N`。任何「这项没法验」都是红，须由人确认，不得由脚本自行豁免；请勿重新引入 skip。
