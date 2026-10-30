# Telegram 运维告警降噪与中继幂等（2026-10-30）

## 现象与实证

- 京东热备 `lavm-klp187b5eh` 从 2026-10-29 21:41 起每小时报告 `not active: hutuji-draw-admin`；但该节点按既定角色不部署 bridge/admin，属于巡检角色漏判。
- 同一消息中的 `failed: ● ● ●` 实际对应 `lima-probe.service`、`mcp-proxy-ccc.service`、`motd-news.service`。旧脚本扫描整台宿主且未传 `systemctl --plain`，把状态圆点当作单元名；三项均不是 hutuji 业务单元。
- 2026-10-30 08:41 同一条中继告警出现两次。京东外层请求超时为 10 秒，中继内部发送最长可阻塞 15 秒；首请求已送达但外层超时重试时存在重复投递窗口。
- 阿里主节点同刻 mcp/bridge/portal/admin 全 active；京东 mcp/portal active、bridge/admin inactive，符合热备角色设计。两机 cron 各只有一组 `41 * * * *` 与 `3 8 * * * --digest`，不是重复 crontab。

## 修复

1. `tg-ops-check.sh` 统一用 `is_expected_inactive` 判定京东热备的 bridge/admin，并同时应用于 active 与 failed 检查。
2. failed 扫描收窄为 `hutuji-*`，加 `--plain` 输出真实 unit 名；宿主依赖改为显式检查 nginx 与 ssh/sshd 任一实现，避免同机其他项目及可选 timer 污染项目告警。
3. worker failed 使用 plain 列格式解析；脚本持有 `flock`，并发 cron/手工触发只允许一个实例发送。
4. 京东中继请求在一次调用的三次重试间复用 `X-Hutuji-Notify-Id`，外层超时放宽到 20 秒。阿里中继以 5 分钟 TTL 原子认领 request id；成功重试返回 202 不重复发送，内部发送失败则释放 id 并回 503 允许重试。
5. `tg-notify.sh --stdin` 在 Telegram 发送失败时向中继返回非零；普通业务通知仍维持“通知失败不阻断业务”的 exit 0 语义。

## 部署与验证

- 阿里与京东均先备份至 `/root/hutuji-bak-20261030-ops-notify-v1/`；最终 ops/notify/relay SHA 分别为 `3c540b14…0d48`、`69184697…56b`、`b36572d3…48a8`。
- 两机 `bash -n`、阿里 relay Python compile、MCP 运维专项 4 项通过；阿里 `hutuji-tg-relay.service` 重启后 active。最终 hub overall=pass，pytest 1696/1696、e2e PASS、行号指纹 287 处全对。
- 最终真实 `bash -x` 巡检均以空 `msg` 退出：京东不再把 bridge/admin 或三项旁路 failed unit 当故障，阿里同时兼容 `sshd.service` 命名。
- 首次阿里手工复验只检查 `ssh.service`，曾额外发出一条依赖告警；发现后立即改为 ssh/sshd 任一 active，并完成最终静默复验。

## 边界

- 本批只改运维通知脚本和中继，不改 MCP、Portal、固件、运动控制或凭据。
- `lima-probe`、`mcp-proxy-ccc`、`motd-news` 的宿主级故障仍保留原状，本项目巡检不再代替其各自的运维责任。
- 本批不处理同机三个旁路 unit 自身的缺目录/陈旧 failed 状态；它们应由各自项目或宿主维护处理。
