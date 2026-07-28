# 排障手册

> 按现象定位。契约定义见 `protocol.md`；联调阶段见 `bringup-sop.md`。本文不改写契约。

## 日志优先级

CH340 串口（最全）→ S3 `ESP_LOG` → Telnet 字节流 → 京东云 MCP / 桥接日志。

PC 观测一律走 CH340；Telnet 槽位 `MAX_TLNT_CLIENTS=1` 留给 S3。

## 链路 / 写字机

| 现象 | 可能原因 | 处置 |
|------|----------|------|
| S3 连不上写字机 | 不同路由器；NVS IP 缓存失真；候选 :23 不是 Grbl；Telnet 未开；PC telnet 占唯一槽位 | 看 S3 发现日志与候选 banner；确认同一 `/24`、写字机 Telnet 已开；退出 PC telnet，让缓存失败后重新扫描 |
| connected 但 ready=false | 候选未通过 Grbl banner 或 fresh-`Idle` 探活 | 查写字机是否复位/忙；确认 `$I`/banner 与 `?` 状态，不要把仅端口连通当 ready |
| G0–G3 全 `error:110` | 未授权 | 人工 `M800`；S3 **永不**自动授权 |
| 运动行 `error:8` | 换纸期发了 G0–G3 | 等换纸行 ok（≥60s）后**重发该行** |
| 换纸后无 `[PaperStatus]` | 未触发真实结果码；或 §9 定点路由回归 | 同时看 CH340 与 Telnet；核对 Grbl `e7f373f`，不要把其他 `[PaperAuto-N]` 进度也期待为 Telnet 广播 |
| `[ESP901]` 无 `Changing=` | 烧录的不是含 §9 的 Grbl 镜像 | 核对 Grbl `e7f373f` 和串口 banner；重新按 bringup 阶段 2 验证 |
| 出图中 `ok` 配对错乱 | 违反单写者：status 发了 `M704`/`[ESP901]` | 出图中 status **只发 `?`** |
| 画到一半停住 | TCP 断连；或换纸行正常阻塞 | 看 S3 日志；换纸可阻塞 ≥60s |
| 撞机 / 越界 | 无限位开关，Grbl 侧无保护 | 查 ③ 侧 `X≤190`、`Y≤277` 限幅是否生效 |
| banner `Grbl 1.3a…` 中途出现 | 写字机复位 | 复位状态机、任务 error、重新探活 |

## MCP / 京东云

| 现象 | 可能原因 | 处置 |
|------|----------|------|
| `hutuji_draw` 报未配置 LLM | 缺 `HUTUJI_LLM_API_KEY` | 进程环境注入；勿写仓库 |
| CRC32 总失败 | 变体不一致 | 两侧钉 zlib/IEEE（`zlib.crc32`） |
| 下载 404 | TTL 过期或 token 错 | 重新 `hutuji_draw`；查 `HUTUJI_OUTPUT_TTL_SECONDS` |
| 公网能打 `/mcp` | nginx/防火墙配错 | **立即**关掉；只暴露 `/files` + TLS |
| 小智看不到工具 | 桥接未起或 `MCP_ENDPOINT` 错 | 查 `cloud_bridge` 与 smoke；接入点禁入库 |
| VPS 服务起不来 | venv/路径/EnvironmentFile | 见 `deployment.md` 与 `journalctl -u hutuji-mcp` |

## 分层排查顺序

1. 写字机自身（阶段 0：授权 + `$I`）
2. Telnet（阶段 1：PC 验完**立刻退出**）
3. Grbl §9（阶段 2）
4. 本机 MCP pytest/e2e（阶段 3–4）
5. S3 探活（阶段 5）
6. 京东云 + 小智桥接（阶段 6）
7. 语音端到端（阶段 7）

未过当前阶段判据，禁止跳到更上层“碰运气”。
