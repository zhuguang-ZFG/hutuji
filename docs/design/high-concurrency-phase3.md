# 高并发 Phase-3 设计（观测 / 熔断 / CDN / 任务账本）

> 状态：**3a 代码已双机部署（`4af959d`，2026-08-30）但默认未开队列/PG/OSS**；breaker 仍 shadow（未写 `HUTUJI_BREAKER_ENFORCE=1`）。3b preflight 探针已入模板并在生产跑过（未配 env 则 pass）；3c CDN **未做**。依赖 Redis+worker 的生产容量验收仍未做。  
> 前置：`docs/worktree-inventory.md`「高并发 Phase-1/2」条；压测 `mcp-server/scripts/bench_draw_load.py`（假负载，未验证 Redis 生产路径）。  
> 非目标：改 `protocol.md` v0.5 板间契约、S3 哑管道行为、换纸/运动/Grbl §9 白名单外改动、合并固件。

## 1. 背景与 Phase-1/2 缺口

| 层 | Phase-1/2 已有 | Phase-3 要补 |
|----|----------------|--------------|
| 调度 | Redis 队列 + N×draw-worker；入队速率 / 深度 / 单设备限流 | 队列深度、worker 滞后、失败率的**可观测 + 告警** |
| 产物 | `output_store` 本地 / OSS；TTL 600s；nginx `/files/` 反代 + 10r/s 限速 | 多 worker + 高峰下载：**CDN 卸压**；OSS lifecycle 与 MCP 清理对账 |
| 生图 | 多 racer 对冲；`HUTUJI_DRAW_RACER_MAX` 硬截断；运营后台 `image_racers` 动态下发 | **按通道熔断**（429/5xx/超时），避免坏通道拖死整轮对冲 |
| 审计 | `usage_report` → draw-portal SQLite `usage_events`；Redis job hash 600s TTL | **跨 worker 任务账本**（Postgres），支撑排障、计费核对、SLA 报表 |
| 部署 | 双机 MCP + bridge 热备；preflight **33** 项（+3 项 Phase-3 条件探针，未配 env 仍 pass） | preflight / 运维面板补队列与 OSS/CDN 探针 |

**规模假设（用户拍板，未生产验证）**：峰值 **10 画/秒** 入队；500 台设备 classroom；单设备默认 **6 画/分钟**（`HUTUJI_DRAW_PER_DEVICE_MINUTE`）。  
**关键约束**：S3 仍只经 `https://hutuji.donglicao.com/files/{name}` 下载 G-code，响应须带 `x-hutuji-crc32`（§5 / nginx 契约）；不得把 OSS 裸桶或带长签名 URL 直接回给设备。

## 2. 目标与非目标

### 2.1 目标

1. **任务账本（Postgres）**：每笔 `hutuji_draw` 有 durable 记录（入队→处理→完成/失败），TTL 长于 Redis job hash（600s），可跨 worker / 跨日查询。  
2. **生图通道熔断**：按 racer/backend 维度自动 open/half-open/closed，与现有对冲逻辑兼容，默认 fail-open 可一键关闭。  
3. **OSS + CDN**：worker 写 OSS；公网下载走 CDN 回源 OSS（或回源 nginx），保持 URL 形态与 CRC 头不变。  
4. **队列监控**：结构化指标 + preflight 探针 + 运维只读 API（loopback），支撑 10 画/秒容量验收。

### 2.2 非目标

- 用 Postgres **替代** Redis 作热队列（BRPOP 延迟不可接受）。  
- 引入 Celery/Kafka/RabbitMQ 等新中间件（运维面过大）。  
- 多 uvicorn worker 扩 MCP（与「单进程 DrawLimiter 内存闸」决策冲突）。  
- 真实语音 HIL、纸面画质签核（仍属 `release-readiness.md` 默认下一刀）。  
- Phase-3 内实现完整 Grafana/Alertmanager（可先 journald + 脚本 + draw-admin 只读页）。

## 3. 总体架构

```
                    ┌─────────────────────────────────────────┐
  小智云 MCP call   │  hutuji-mcp (uvicorn ×1)                │
  hutuji_draw ─────►│  attach_or_enqueue → Redis              │
                    │  wait_result (poll Redis job hash)      │
                    └──────────────┬──────────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────────┐
                    │  Redis（单主；见 §7.1 HA）                 │
                    │  queue / job hash / rate keys / breaker   │
                    └──────────────┬──────────────────────────┘
                                   │ BRPOP
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
   draw-worker@1            draw-worker@2 …           draw-worker@N
          │                        │                        │
          └────────────┬───────────┴───────────┬────────────┘
                       │                       │
              _run_draw_job              async pg writer
              (生图+描摹+vpype)          (任务事件，非阻塞)
                       │                       │
                       ▼                       ▼
                 OSS 产物桶              Postgres（账本）
                       │
                       ▼
              CDN → 公网 /files/…
              （nginx 校验 CRC 或 CDN 回源 nginx）
```

**原则**：Redis 仍是**唯一热路径**；Postgres 写入失败不得阻断出图（与 `usage_report` 同口径：后台线程 + 日志告警）。

## 4. Postgres 任务账本

### 4.1 与现有存储的关系

| 存储 | 职责 | Phase-3 变化 |
|------|------|--------------|
| Redis `hutuji:draw:job:{id}` | 600s 内 wait_result 轮询 | 不变 |
| Redis `hutuji:draw:inflight:{hash}` | 同题 single-flight | 不变 |
| draw-portal SQLite | 用户/绑机/usage_events | 不变；usage 仍走现有 ingest |
| **Postgres** | 任务生命周期 + 通道样本 | **新增** |

不合并 draw-portal SQLite → Postgres（一期范围过大）；Postgres 实例可与 draw-admin 共用 RDS，库名 `hutuji_ops`。

### 4.2 表结构（v1）

```sql
-- 任务主表：job_id 与 Redis 一致（urlsafe token）
CREATE TABLE draw_jobs (
  job_id           TEXT PRIMARY KEY,
  description_hash TEXT NOT NULL,
  description_redacted TEXT NOT NULL,  -- 脱敏后前 80 字，禁存原文若含 PII
  device_id        TEXT NOT NULL DEFAULT '',
  public_base_url  TEXT NOT NULL,
  status           TEXT NOT NULL CHECK (status IN ('pending','processing','done','failed','timed_out')),
  route            TEXT,               -- curated / quickdraw / ai
  enqueued_at      TIMESTAMPTZ NOT NULL,
  started_at       TIMESTAMPTZ,
  finished_at      TIMESTAMPTZ,
  latency_ms       INTEGER,
  result_url       TEXT,
  error_type       TEXT,
  error_detail     TEXT,               -- ≤200 字，禁 URL/密钥
  worker_instance  TEXT,
  bytes_out        INTEGER,
  pen_lifts        INTEGER
);
CREATE INDEX draw_jobs_enqueued_at ON draw_jobs (enqueued_at DESC);
CREATE INDEX draw_jobs_device_id ON draw_jobs (device_id, enqueued_at DESC);

-- 通道级事件（熔断与排障）
CREATE TABLE draw_backend_events (
  id               BIGSERIAL PRIMARY KEY,
  job_id           TEXT REFERENCES draw_jobs(job_id),
  backend_id       TEXT NOT NULL,      -- sensenova / agnes / racer:{uuid} / …
  outcome          TEXT NOT NULL,      -- ok / timeout / http_429 / http_5xx / error
  latency_ms       INTEGER,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX draw_backend_events_backend ON draw_backend_events (backend_id, created_at DESC);
```

### 4.3 写入点

| 时机 | 写入方 | 说明 |
|------|--------|------|
| 入队成功 | MCP `attach_or_enqueue` 后 | `INSERT … status=pending`（`asyncio.create_task`） |
| 开始处理 | draw-worker `mark_processing` | `UPDATE … status=processing, started_at, worker_instance` |
| 完成 | `mark_done` | `UPDATE … done + result 摘要（url 文件名 token 可存，禁 query）` |
| 失败 | `mark_failed` | `UPDATE … failed + error_type` |
| 生图对冲 | `bitmap_svg` 每 racer 返回 | `INSERT draw_backend_events` |

**模块**：`hutuji_mcp/job_ledger.py`（`asyncpg` 连接池或 `psycopg` 3 async；env `HUTUJI_PG_DSN`，空=禁用）。

### 4.4 保留与合规

- 保留 **30 天**（可 env 配置）；日批 `DELETE WHERE enqueued_at < now()-interval`。  
- description 存 **hash + 截断脱敏**；完整 prompt 不入 PG（与日志脱敏 `redact.py` 同口径）。  
- 凭据禁入库；DSN 只走 `/etc/hutuji/mcp.env` `chmod 600`。

## 5. 生图通道熔断

### 5.1 熔断对象

| backend_id | 来源 |
|------------|------|
| `sensenova` | break-glass env 商汤 |
| `agnes` | 默认 Agnes |
| `imagefree` | `HUTUJI_IMAGE_FALLBACK_URL` |
| `siliconflow:schnell` / `siliconflow:kolors` | 硅流双 racer |
| `racer:{portal_id}` | 运营后台 `image_racers[]` |

Curated / Quick Draw **不经熔断**（无付费 HTTP 通道）。

### 5.2 状态机（每 backend_id 独立）

```
closed ──(滑动窗口失败率≥阈值 或 连续 N 次超时)──► open
open ──(冷却 T 秒)──► half_open
half_open ──(探测 M 次成功)──► closed
half_open ──(任一次失败)──► open
```

**默认参数（可 env 覆盖，实施前须 bench 调参）**：

| 参数 | 默认 | 说明 |
|------|------|------|
| 窗口 | 60s | Redis ZSET 存 `(timestamp, outcome)` |
| 失败率阈值 | 50% | 至少 5 样本才判 |
| 连续失败开断 | 3 | 超时或 http_5xx/429 |
| open 冷却 | 120s | |
| half_open 探测 | 1 次成功即 closed |  classroom 场景求快恢复 |

**存储**：Redis key `hutuji:breaker:{backend_id}`（JSON：`state`, `opened_at`, `stats`）。  
**进程内缓存**：100ms TTL，避免每 racer 打 Redis。

### 5.3 与对冲集成

在 `_race_image_backends` / `_race_dynamic_image_racers` 启动 task **前**过滤：

```python
racers = [r for r in racers if not breaker.is_open(r.id)]
if not racers:
    raise BitmapError("生图通道暂不可用，请稍后再试")
```

- 与 `HUTUJI_DRAW_RACER_MAX` **串联**：先熔断过滤，再截断数量。  
- 商汤瞬态 500 **现有**快速重试仍保留，但两次均失败计 2 次失败样本。  
- `HUTUJI_BREAKER_ENABLED=0` 关闭 enforce（与 paper_risk shadow 同模式）。

### 5.4 运维

- draw-admin 只读页：各通道 state + 最近 1h 失败率（读 PG `draw_backend_events` + Redis state）。  
- 手动强制 open/closed：loopback `POST /internal/breaker/{id}`（shared secret，nginx 404 公网）。

## 6. OSS + CDN 下载链

### 6.1 现状（Phase-2）

- Worker `OssOutputStore` 上传 `{prefix}/draw_….gcode`。  
- MCP `/files/{name}` 仍可读本地或 OSS（实现已抽象 `get_output_store()`）。  
- nginx `limit_req` 10r/s + burst 20（**单 IP**）；500 台 NAT 后可能共享公网 IP，需 CDN 或调高 burst（Phase-3 优先 CDN）。

### 6.2 目标链（推荐）

```
S3 ──HTTPS GET──► CDN（阿里云 CDN，CNAME files.hutuji.donglicao.com 或主域 /files/）
                      │ Origin: OSS 私有桶 或 nginx 回源
                      ▼
                 对象 draw_*.gcode
```

**契约保持**：

1. `HUTUJI_PUBLIC_BASE_URL` 仍为 `https://hutuji.donglicao.com`（或用户批准的 CDN 子域，须 HTTPS 443）。  
2. MCP 返回的 `url` 路径仍为 `/files/{name}`，**禁止**返回带 AK 签名 query 的 OSS URL 给设备。  
3. CRC32：优先 **nginx 回源 OSS 后计算**并写 `x-hutuji-crc32`（与现网一致）；若 CDN 直连 OSS，须在 OSS 元数据存 CRC 或由 origin nginx 负责（**禁止** S3 侧改校验逻辑）。

### 6.3 实现选项（二选一，实施前拍板）

| 选项 | MCP 读路径 | CDN | 复杂度 |
|------|------------|-----|--------|
| **A（推荐）** | nginx → uvicorn → OSS 读 | CDN 回源 **nginx** `/files/` | 低；CRC 逻辑零改 |
| B | MCP 302 到 CDN（同 path） | CDN 回源 OSS | 中；要证 S3 跟随 301/302 |

Phase-3 **默认 A**：CDN 仅加速，回源仍走现有 nginx+uvicorn 校验链。

### 6.4 OSS 运维

- Bucket 私有；CDN/OSS 回源鉴权用 RAM 子账号。  
- Lifecycle：前缀 `draw/` **1 天**过期删除（与 TTL 600s 对齐留余量）。  
- MCP `cleanup_expired`：OSS `list_objects` + `delete`（已有接口扩展），与本地逻辑同 stem 删除 svg/png/gcode。

### 6.5 env

```
HUTUJI_OUTPUT_BACKEND=oss
HUTUJI_OSS_*=（已有）
HUTUJI_CDN_ENABLED=0          # 1=返回 URL 仍用 PUBLIC_BASE_URL，CDN 在 DNS 层透明
```

不在 MCP 层暴露 OSS 公网 endpoint。

## 7. 队列监控与容量验收

### 7.1 Redis 高可用（Phase-3 前置）

| 方案 | 说明 |
|------|------|
| 单主 Redis（阿里云 ECS 本机） | Phase-1 最小；主节点故障则队列停 |
| 阿里云 Redis 云版主从 | **推荐** classroom 生产；同 VPC |
| 双机各跑 Redis | **禁止**（队列分裂） |

京东云热备：**不消费队列**（与 bridge 单 WSS 同构）；故障切 DNS 后在新主启 worker。

### 7.2 指标（结构化日志 + 可选 Prometheus textfile）

| 指标 | 来源 | 告警建议 |
|------|------|----------|
| `draw_queue_depth` | Redis LLEN | > 150 持续 60s |
| `draw_enqueue_rejected_total` | MCP 429 busy | 增速 > 10/min |
| `draw_worker_inflight` | worker gauge | = N 且 depth 涨 |
| `draw_job_latency_p95_ms` | PG 或 Redis 采样 | > 30s（AI 链参考） |
| `draw_worker_lag_seconds` | 最老 pending job | > 60s |
| `breaker_open_count` | Redis | > 0 持续 5min |

**模块**：`hutuji_mcp/queue_metrics.py`；MCP 与 worker 每 15s 写一行 JSON 到 journald（`queue_metrics` logger）。

### 7.3 preflight 新增项（Phase-3b）

| 项 | 判据 |
|----|------|
| `check_draw_queue` | 配了 `HUTUJI_DRAW_QUEUE_URL` 时：Redis PING、LLEN 可读、worker@1 is-active |
| `check_oss_roundtrip` | `output_backend=oss` 时：上传 1KiB 探针对象 → MCP read_verified → 删 |
| `check_pg_ledger` | 配了 `HUTUJI_PG_DSN` 时：`SELECT 1` + 迁移版本 |

未配队列 URL 的节点：**fail 改为 pass 并 detail「未启用队列」** 违反「无 skip」铁律 → 改为 **expected_checks 固定 33、探针函数内未配 env 则 return 0**（须在 preflight 与文档同步登记；生产节点 pass 数仍按角色口径读，不得把单节点历史 `30/30` 当双机共同结果）。

### 7.4 容量验收（10 画/秒）

在 **生产等价** Redis + ≥8 worker 环境：

```bash
# 假负载（已存在）
python scripts/bench_draw_load.py --backend redis --rps 10 --duration 300 --workers 32 --job-ms 3000

# 真负载（staging only，会产生付费调用）
HUTUJI_BENCH_LIVE=1 python scripts/bench_draw_live.py ...  # Phase-3 新脚本，默认不存在
```

通过线（假负载）：success_rate ≥ 95%，p95 ≤ 15s（job_ms=250）；真负载单独签核。

## 8. 环境变量汇总（Phase-3 新增）

| 变量 | 默认 | 说明 |
|------|------|------|
| `HUTUJI_PG_DSN` | 空 | `postgresql://…` 空=禁用账本 |
| `HUTUJI_PG_RETENTION_DAYS` | 30 | 任务表保留 |
| `HUTUJI_BREAKER_ENABLED` | 1 | 0=关闭熔断过滤 |
| `HUTUJI_BREAKER_WINDOW_SEC` | 60 | |
| `HUTUJI_BREAKER_FAIL_RATIO` | 0.5 | |
| `HUTUJI_BREAKER_OPEN_SEC` | 120 | |
| `HUTUJI_METRICS_INTERVAL_SEC` | 15 | |
| `HUTUJI_CDN_ENABLED` | 0 | DNS 层透明 CDN 标记 |

模板追加：`deploy/env.mcp.example`（实施时）。

## 9. 分阶段 rollout

| 阶段 | 内容 | 依赖 | 回滚 |
|------|------|------|------|
| **3a** | Postgres 账本 + metrics 日志；breaker 仅 shadow（记事件不过滤） | Phase-1/2 生产 Redis+worker | 空 env DSN；删 PG 表 |
| **3b** | breaker enforce + preflight 队列/OSS 探针 | 3a 稳定 48h | `HUTUJI_BREAKER_ENABLED=0` |
| **3c** | CDN 切 DNS + OSS lifecycle | 3b + 真下载 CRC 抽验 | DNS 回源 nginx 直连 |

**禁止跳阶段**：未做 3a shadow 不得开 breaker enforce。

## 10. 代码 touch 清单（实施白名单）

| 路径 | 变更 |
|------|------|
| `mcp-server/hutuji_mcp/job_ledger.py` | 新增 |
| `mcp-server/hutuji_mcp/breaker.py` | 新增 |
| `mcp-server/hutuji_mcp/queue_metrics.py` | 新增 |
| `mcp-server/hutuji_mcp/bitmap_svg.py` | 对冲前 breaker 过滤 + 事件上报 |
| `mcp-server/hutuji_mcp/draw_queue.py` | 入队/完成 hook 调 ledger |
| `mcp-server/hutuji_mcp/draw_worker.py` | metrics 周期任务 |
| `mcp-server/hutuji_mcp/config.py` | 新 env |
| `deploy/preflight.sh` | 条件探针 |
| `deploy/env.mcp.example` | 注释 |
| `mcp-server/tests/test_breaker.py` | 新增 |
| `mcp-server/tests/test_job_ledger.py` | 新增（可用 testcontainers 或 skip 若无 PG） |

**不改**：`protocol.md` §5 G-code 契约、`hutuji_gwrite.toml` 限幅、S3/xiaozhi 固件。

## 11. 风险与未决项

1. **Postgres 与 SQLite 双写报表**：draw-admin 是否要联表 PG？→ 一期仅 MCP 只写，admin 只读 API 后置。  
2. **CDN + CRC**：选项 A 下 CDN 缓存 600s TTL 是否与 capability TTL 一致？→ 建议 CDN cache TTL ≤ 300s，回源校验仍由 nginx 做。  
3. **500 台同 NAT**：单 IP 10r/s 限速 → 必须 CDN 或 `$hutuji_client_ip` 改 `$binary_remote_addr`（**改 key 须 restart nginx**，见 deployment 教训）。  
4. **京东云热备**：队列与 PG 是否同步？→ **否**；热备仅代码+env 待命，切流后人工启 worker。  
5. **成本**：10 画/秒 × AI 3s ≈ 30 并发生图 → 付费 API 费用线性涨；curated 命中可降压但 Phase-3 不假设命中率。

## 12. 验收标准（Phase-3 完成定义）

- [x] 3a（仓内+双机代码）：`job_ledger` / `breaker` shadow / `queue_metrics` + hooks 已部署 `4af959d`；默认 `HUTUJI_BREAKER_ENFORCE=0`；**未配 PG DSN → shadow 48h 未起**
- [ ] 3a：生产 shadow 48h，PG 有 pending/done 行，写入失败不阻断 draw  
- [x] 3b（探针模板）：preflight +3 条件探针已上机（未配 env 仍 pass）；**breaker enforce 未开**  
- [ ] 3c：公网下载经 CDN，CRC32 与源站一致（抽 10 文件）  
- [ ] bench：Redis 模式 10 rps × 300s 假负载 PASS  
- [x] 文档：`deployment.md` / `worktree-inventory.md` / `release-readiness.md` 已登记 `4af959d`  
- [ ] hub 门禁一次完整 pass（本环境 pytest 超 ~25s 被中断；非 contract 462 项 + e2e PASS + docs pass）；**不得**写「可出货 / 联调完成」

---

**参考**：Phase-1/2 实现 `draw_queue.py` / `output_store.py` / `bench_draw_load.py`；生产并发生图闸 `docs/deployment.md` 文首；nginx 限速 `deploy/nginx-hutuji.conf`。
