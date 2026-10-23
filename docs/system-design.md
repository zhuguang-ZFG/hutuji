# hutuji 系统级设计规划

> 2026-10-22 新增。本文是**系统级设计总览与演进规划**：架构大图、模块分解、端到端数据流、安全与可靠性设计、部署拓扑、测试验证体系、完成度快照、下一阶段规划。
>
> **真值优先级不变**：根 `README.md` 决策 → `docs/protocol.md` v0.5 → `docs/bringup-sop.md` → `docs/release-readiness.md`（状态真值）。本文不参与冲突裁决，与上述文档冲突时以高优先级为准。`docs/design/` 下的批次记录（`-日期.md` 命名）是历史证据，不随本文重写，§13 给出主题索引。
>
> 状态记号 ✅ / 🟡 / 🔴 与 `release-readiness.md` 同口径。本文不做出货级宣称（禁语表见 `docs/agent-gate.md` §5）；一切「板上/生产」状态以 readiness 为准。

## 1. 系统总览

**hutuji = AI 绘图自动换纸写字机**：用户语音或小程序下发意图 → 云端 MCP 生成 G-code → ESP32-S3 板下载校验后经 WiFi Telnet 转发 → Grbl 写字机出图（普通机型自动换纸；量产 nopaper 机型不自动换纸）。

系统由**三个代码仓 + 双云节点 + 两台 MCU** 组成：

| 层 | 组成 | 状态（2026-10-22） |
|---|---|---|
| 云端服务 | 阿里云主节点（现役生产）+ 京东云热备 | ✅ 双机部署多批有证 |
| 交互固件 | ESP32-S3（xiaozhi-esp32 fork；板型 `lichuang-dev` / Freenove 2.8 LCD / Waveshare 3.5） | ✅ 板上 1.0.48，1.0.49 已进发布目录待 OTA |
| 运动固件 | ESP32（Grbl_Esp32；量产交付 `massprod/nopaper-1.3a`） | ✅ 量产固件多批 OTA 有证 |
| 小程序 | draw-portal/weapp | 🟡 0.3.33 已上传开发版（上传 ≠ 平台发布） |
| 实体放行 | 工厂配对 / 纸面尺量 / 老化 / 交付签核 | 🔴 仍 Hold |

**完成度自评**：软件工程面（生成链、设备控制、OTA、双机部署、门禁与 CI）已高度成熟，近两个月以「三仓审查 → 修复 → 发布 → OTA → 控制回归」节奏滚动 20+ 批并逐批留证；剩余主线是**实体签核**与**少量安全收尾**（见 §11/§12）。

## 2. 顶层架构

### 2.1 仓库版图

| 仓库 | 本地路径 | 角色 | 口径 |
|---|---|---|---|
| hutuji（本仓） | `D:/Users/hutuji` | MCP 生成服务、Portal + 小程序、协议、部署、产线工具、门禁 | `main` |
| Grbl_Esp32 | `D:/Users/Grbl_Esp32` | 运动控制固件（普通分支含自动换纸系统） | 量产只交付 `massprod/nopaper-1.3a` |
| xiaozhi-esp32 | `D:/Users/xiaozhi-esp32` | AI 交互固件（S3 板型） | 产品 S3 固件源 |
| fz | `D:/Users/zhugu/fz` | Grbl 仿真 / 验证门禁 | `standard` profile |
| kxnx | `D:/Users/kxnx` | 奎享单线字 / 排版逆向参照 | 只读参考，不构成依赖 |

固件本体不入本仓、不向本仓合并（锁定决策 #7）。

### 2.2 运行时架构

```
                ┌────────────── 用户面 ──────────────┐
                │  微信语音 / 小派屏幕  │  小程序 weapp  │
                └──────────┬──────────────────────────┘
                           ▼
                小智官方云（+ MCP 接入点）
                           │ WSS（cloud bridge 单活：主 1 / 备 0）
                           ▼
      ┌── 阿里云主节点 lima（现役生产）──────┬── 京东云热备 jdcloud ──────┐
      │ hutuji-mcp（loopback 8300）        │ hutuji-mcp                │
      │ hutuji-cloud-bridge（active）      │ hutuji-cloud-bridge（off） │
      │ draw-portal（nginx 反代）           │ draw-portal               │
      │ draw-worker × 8（Redis 队列）       │                           │
      └──────┬─────────────────────────────┴───────────────────────────┘
             │ HTTPS：/files、/songs（公网下载）、POST /register
             ▼
      ┌─────────────┐  WiFi Telnet :23   ┌──────────────────┐
      │ ESP32-S3     │ ◄───────────────► │ ESP32 Grbl        │
      │ xiaozhi 固件 │  （家用路由器内网）  │ 写字机（nopaper）  │
      │ 下载+CRC+流控 │                    │ 行程/参数守门     │
      └─────────────┘                    └──────────────────┘
```

要点：

- **双芯片**：S3 跑 xiaozhi（语音 / 云 / 转发），独立 ESP32 跑 Grbl（运动）；不合并固件（决策 #1）。
- **板间 = WiFi Telnet :23**：S3 作 TCP 客户端，NVS 缓存 IP + 子网扫描发现写字机；USB Host / CH9350 已否决（决策 #2）。
- **哑管道**：G-code 只在云端生成与校验，S3 只下载 + CRC32 + 流控转发（决策 #3）。
- **AI = 小智官方云 + MCP 接入点**（决策 #4）；MCP 服务 = 阿里云主 + 京东云热备，公网 `https://hutuji.donglicao.com` 走 DNS A 直连，Cloudflare Tunnel 退役为回退路径（决策 #5）。
- **bridge 单 WSS 单活**：任一时刻只在一个节点运行，切换须先停旧 bridge 再启新。

### 2.3 公网边界

- nginx 只公开 `GET|HEAD /files/`、`GET|HEAD /songs/`（限速桶）与 `POST /register`；`/mcp` 显式 404（MCP 端点公网不可达）。
- 安全组只开 22 / 80 / 443；8300 仅 loopback。
- 小程序后端与 portal 同机反代；`/draw-upload/` 是同步长请求，limit_conn 单独放宽到 50（课堂 NAT 场景）。

## 3. 端到端数据流

### 3.1 主画链路（语音出图）

1. 用户对小派说「画一只猫」→ 小智官方云 → MCP 接入点 → 本机 `/mcp`。
2. `hutuji_draw` 规范化描述 → 查 `_draw_inflight` 同题搭车 → 过期产物清理 → Quick Draw 本地缓存（仅简笔风描述查询；命中免付费生图）→ 领取限流名额。
3. 位图链：商汤通道（代码按 `sensenova-u1.5-lite` URL / b64_json 双形态 + 75s 单次预算；生产待 key 轮换后切换）、Agnes、可选硅流 / 魔搭 / TCB 并发对冲，谁先出图用谁 → 统一下载 PNG（强制过 `url_guard`）→ 骨架化 + sknw 描摹 → 单线 SVG。
4. 精品图库走固定 SVG 白名单类目；照片走程序化 `sketch_render`（hatch / squiggle，非 AI i2i）。
5. `_validate_svg` 注入闸 → vpype 转 G-code（SVG 内容缓存 10 分钟 / 16 项）→ `validate_gcode()` §5 活闸 → 写 `output/draw_*.{svg,gcode,png}` → 返回 `{url, preview_url, lines, bytes, pen_lifts, ttl_seconds}`。
6. 异步模式（生产 = 1）：即回 accepted，完成经设备消息 API 链式推送预览；云端 confirm 在 90s 窗口内随 status 轮询重试，失败 detail 附人工兜底话术。
7. S3 经公网下载 G-code（PSRAM 全量缓冲 + CRC32 + 机型读闸）→ Telnet 512B 在途窗口灌流 → 页尾 fresh Idle 后旁路 `G1G90 X0Y0F8000` 归位（G1 不触发换纸）→ `M30`（普通机型触发换纸；量产 nopaper 无换纸）。
8. 全程单写者纪律：出图中 S3 只发 `?` 轮询，不发 M 命令；abort ≠ 急停（换纸物理上打不断）。

### 3.2 写字 / 文档 / 笔顺链路

- `hutuji_write`：奎享 .gfont 单线字库排版（折行 / 避头避尾 / 分页 / 手写抖动）→ 与画链同一条 SVG→G-code 产物面；多页经 `pages` 紧凑 JSON 一次链式推送，逐页错峰 TTL。
- 文档导入（portal 传图页）：docx / xlsx / pptx / csv / txt 纯 stdlib 解析（防御口径与 SVG 闸同族）→ 标题放大居中 → 表格排版（相邻行共享边框 / 跨页表头 / 超宽先压列再缩字号）→ 同链。
- `hutuji_teach`：hanzi-writer-data 标准笔顺中心线 + 田字格渐显面板，与写链同一产物面 / 同源 pages 参数（`article_pages` 唯一序列化）。

### 3.3 设备反向链路

- 状态：`hutuji.status` 设备工具 → 云端按 request_id / axis / 读回值核对调速类结果；`status.paper_config` 承载纸张快照。
- 绑定：一次扫码绑定 v2（S3 P-256 身份 + 单次 nonce，portal `device_identity.py` 验签），支持一账号多机；`active_device_mac` 为控制目标真值。
- OTA：portal 灰度 → 公网 catalog / latest 指针 → 设备 `hutuji.ota_start`（严格版本递增 + 下载 SHA + 镜像版本核对）→ uncertain 占位会话超时后才放行出图入口。
- 推送：设备 messaging token 文件优先、env 兜底；401 / 403 自愈重铸，404 不误判离线。

### 3.4 其余工具面

唱歌（`/songs` 目录 + `hutuji_sing` / `hutuji_stop_song`）、记忆三工具（按 device_id 分桶，上限 + LRU）、激活中转 `/register`、小派文字聊天（portal `/api/chat` → MCP `/internal/chat`，DRAW:: 意图标记）、语音画 + 写（ASR → LLM tool loop，生成后 auto_confirm = False 推预览，确认 / 取消走 portal 路由）、AI 写文 / 润色（`/api/draw/write/ai`）。

## 4. 模块分解

### 4.1 云端生成服务（`mcp-server/hutuji_mcp/`，37 模块）

| 组 | 模块 | 职责 |
|---|---|---|
| 位图生成链 | `bitmap_svg` / `image_racers` / `sketch_render` / `url_guard` | 多后端对冲、程序化照片渲染、SSRF 唯一闸 |
| 矢量 / 文本链 | `llm_svg` / `gcode_gen` / `arc_fit` / `gfont` / `write_text` / `doc_ingest` / `table_layout` / `article_pages` / `stroke_order` / `t2s` | LLM SVG、vpype 管线、弧拟合、字库 / 排版 / 文档 / 笔顺 |
| 服务面 | `server` / `cloud_bridge` / `config` / `draw_queue` / `breaker` / `output_store` / `device_messaging` / `activation_relay` / `job_ledger` | FastAPI + MCP 入口、桥接、队列、熔断、产物、推送 |
| 安全面 | `redact`（脱敏唯一实现）/ `llm_svg._validate_svg` / `validate_gcode` / `url_guard` | 见 §6 |
| 工具与数据 | `quickdraw` / `market`（curated 精品库）/ `chat` / `article_llm` | 缓存与运营面 |
| 仿真 | `grbl_sim` / `pipe_sim` | e2e 与本地调试 |

### 4.2 门户与小程序（`draw-portal/`）

- `draw_portal/`：FastAPI 应用——`app`（路由 / 认证 / 微信登录）、`pipeline`（生成 + 推送编排）、`firmware_ota`（灰度 / 占位 / 账本）、`device_identity`（P-256 验签）、`xiaopai`（音色 / 模型切换）、`chat` / `voice_agent` / `voice_jobs` / `tencent_asr`（文字 / 语音聊小派）、`telegram_archive`（TG 频道冷备份，默认关闭）、`milestones`（作品里程碑 + 连续打卡通知）；`tests/` 610 项。
- `weapp/`：小程序——创作 / 图库 / 历史 / 市场 / 我的 / 小派 / 手绘画板 / 设置；CLI 上传 ≠ 平台发布；境外服务表述红线全仓扫描。

### 4.3 S3 交互固件（xiaozhi-esp32 fork）

设备侧 MCP 工具（draw / status / confirm / abort / speed / ota 等）、G-code 下载（PSRAM 全量 + CRC32）、Telnet 客户端（NVS 缓存 IP + 扫网发现 + 指数退避重连）、512B 在途窗口流控、TLS 有限等待（Disconnect 3×10s 后放弃所有权）、OTA（双区 + 严格版本递增）、P-256 设备身份（NVS `hutuji_identity`）、屏幕交互（Freenove 2.8 / Waveshare 3.5 / lichuang）、TLS 堆预算一次性自愈任务、BLE 只读诊断辅助面（阶段 A，默认关闭，不承载 G-code / 控制）。

### 4.4 Grbl 运动固件（Grbl_Esp32）

- 量产交付分支 `massprod/nopaper-1.3a`：Telnet / Web / WebSocket 服务、STA 自愈看门狗（AP 回落每 120s 全量重试、断链 ≥15s 轻量异步重连、≥10min 兜底重启）、Telnet 会话代次隔离、请求上下文按任务隔离、奎享完成守护 beacon、超长行整行拒收至 CR / LF。
- 普通分支（paper 机型）含自动换纸系统（M701 / M721 / M30 族）；换纸机械 / 时序 / 运动 / 授权 / U1 协议不改（决策 #6；§9 清单内小改必过 fz 门禁）。
- 行程事实：无限位开关、软限位关闭（`$20/$21/$22=0`），MCP 端限幅 X≤277 / Y≤190mm 是唯一安全防线；量产速度 6000 / 6000 / 1200、加速度 3000×3、`$1=255`、X 长边（297 方向）/ Y 宽边（210 方向）已由用户实机确认。

### 4.5 生产工具（`scripts/factory/` + `production/` + `tests/production/`）

镜像打包、逐台身份 / 参数核验、烧录备份、验收台账、可迁移档案；已批准配方（速度 / 加速度 / 老化试行门槛）；候选包 `NOT_FOR_RELEASE` 纪律。产线回归独立运行 `tests/production`，不与 hub / Portal 测试互替。

### 4.6 部署与运维资产（`deploy/`、`scripts/`）

systemd unit / nginx 模板 / 拆分 env / `prepare-host.sh` / `install-from-repo.sh` / `preflight.sh`（34 项）；`auto_deploy.py`（LF blob 出包 + 逐文件 SHA-256 + 回滚号）、`cross_repo_sync.py --gate-all`、token 看门狗体系（控制台 JWT 自动续期 + 设备 token 探活 + 主备 ed25519 `command=` 锁定同步）。

## 5. 关键设计决策与不变量

锁定决策 7 条见根 `README.md`「决策记录」（编号以此为准，禁止重开）。由决策派生的系统级不变量：

1. **哑管道**：设备不生成 / 编辑 G-code；`validate_gcode()` 是云端最后一道活闸，禁止为速度改模型直写 SVG。
2. **§5 文件契约**：头尾幂等 Z 行、XY 恒 F24000、Z 恒 F10000、X≤277 / Y≤190、单文件 ≤512KiB、设备字节口径（禁 CRLF 塌陷回读）、单行 254B payload；页尾归位行与 `M30` 由 S3 旁路单发，不进文件。
3. **唯一实现纪律**：脱敏只走 `redact.py`；生图下载 URL 只走 `url_guard`；多页参数只由 `article_pages` 同源序列化；共享判错只走 `hutuji_mcp.tool_result`。
4. **凭据面**：只走环境变量、禁 `.env`；生产凭据落 `/etc/hutuji/*.env`（chmod 600）；凭据禁入库清单见 `AGENTS.md` 硬规则。
5. **换纸 / 运动 / 引脚 / OTA 校验策略不改**；Grbl 改动限定 §9 清单 + 用户点名授权例外。
6. **负坐标硬拒**（奎享参照系为负 X；不得为「对齐奎享」放宽限幅）。
7. **纸张快照**：生成 / 缓存 / 队列 / 收藏共用冻结 PaperConfig；选纸不扩边、不写 `$130/$131`；产物机型读闸分档放行。
8. **量产不自动换纸**：交付只用 nopaper 分支，禁止移植普通分支换纸模块满足旧门禁。

## 6. 安全设计

- **输入闸门**：SVG 元素 / 属性白名单（拒 DTD / 实体 / 事件属性 / href / url() / 非 none 填充，深度 ≤64，≤256KiB）、G-code 契约校验、文档导入防御（ZIP 条目 / 加密位 / DOCTYPE+ENTITY 字节级拒 / 行列字上限）、描述 ≤200 字符并剔除控制字符；LLM `finish_reason` 只解释「一段 SVG 都提取不到」，不替代 `_validate_svg` 判定。
- **网络闸门**：`url_guard`（https-only、非 global IP 字面量全拒、localhost / .local / .internal 拒、畸形端口 fail-closed）；内部 API loopback + 共享密钥；bridge 只记 JSON-RPC method 标签。
- **凭据与令牌**：控制台 JWT 看门狗（OCR 过验证码自动续期，失败回落人工）；设备 messaging token 文件优先、env 兜底；主备 token 同步用 `command=` 锁定 ed25519 仅放行 `set-token`；激活码中转永不记日志。
- **隐私**：手机号闸（phone_required / phone_secured 动作级）、msgSecCheck 输入 / 输出双审 fail-closed、小派克隆 per-user 隔离（「用户不能改所有人的」）、TG 备份三件套默认关闭、小程序文案境外服务表述红线。
- **日志面**：第三方 root / uvicorn / httpx / httpcore handler 装共享 filter，先 `getMessage()` 格式化再脱敏最终文本并单独脱敏 exc_info / exc_text / stack_info；journal 拉取脚本落盘前过同一 `_redact_secret_text()`。

## 7. 可靠性设计

- **通道熔断**（Phase-3）：shadow / enforce 两态、half-open 唯一探针、同态结果不刷新、内存锁 / Redis WATCH-MULTI 比较后写入（旧常规 racer 不得回滚探针刚写的 fresh open）；生产保持 `BREAKER_ENFORCE=0`。
- **队列**：Redis 队列（仅主节点）+ draw-worker × 8；限深、inflight 双键隔离、优雅停机以 `os._exit(0)` 跳过 Py_FinalizeEx 全堆遍历、错误路径不硬退出。
- **重试 / 幂等**：同题 draw 单事务（取消只断 waiter，约 35s 重试搭车复用限流名额与产物，不另起付费生成）、confirm 90s 轮询重试窗口、OTA uncertain 占位与墙钟超时分离、多页错峰 mtime、写侧日限 accepted 重试复用原事务。
- **设备自愈**：S3 TLS 堆预算连续耗尽 → 一次性自愈任务（10s 宽限复查非 busy / OTA → Notify → 20s → esp_restart，被接管即放弃）；Grbl STA 断链看门狗；电机释放纪律（整单 / 停止归位后才 `$MD`，新任务先 `G92 Z0`，停止复用同一 worker）。
- **产物可靠性**：TTL 600s 访问时顺带清理（无后台扫描线程）、内容绑定 `.paper.json` + `X-Hutuji-Paper` 对账、设备字节口径防 CRLF 假绿、机型分档读闸、市场图库按当前纸张重新转换（预生成 G-code 仅作入库证据）。

## 8. 数据与产物生命周期

- 产物命名 `draw_YYYYMMDD_HHMMSS_<22 位 token>.{svg,gcode,png}`；token 按 urlsafe base64 拆段（本身可能含 `_`，按最后一个下划线切会泄露约 29%）；`/files` 一切失败（名字不合法 / 缺文件 / 过期 / 竞态）统一塌 404。
- 精品图库 `mcp-server/market/`：467 件 20 类 Git 真值，`sources.json`（官方提交 / 许可 / 原图 SHA）+ `review.json`（逐件最终预览复核）；Twemoji CC-BY-4.0、Tabler MIT、MDI Apache-2.0 并排除品牌标志；发布窄白名单 + 2048 文件上限 + 32MiB 包预算。
- 数据资产不入库：Quick Draw（CC BY 4.0，署名文件随数据分发）、奎享 .gfont（专有素材，本机字库目录）、笔顺 strokes（ARPHICPL 随数据目录分发）、TCB 云函数源码在控制台仓外。
- 台账：Postgres shadow 账本（无 DSN 禁用）、usage 埋点、`telegram_archive_log`、`engagement_events` 幂等登记。

## 9. 部署与运维拓扑

| 节点 | 常驻服务 | 特别口径 |
|---|---|---|
| 阿里云 lima（主，现役生产） | hutuji-mcp / draw-portal / hutuji-cloud-bridge（active）/ draw-worker ×8 / token 看门狗 cron | 队列与 PG shadow 仅主节点；多租户 conf.d 布局，禁止整份覆盖 hutuji vhost，只准锚定插入 |
| 京东云 jdcloud（热备） | hutuji-mcp / draw-portal（bridge 保持 inactive） | sites-enabled 为普通文件非 symlink；不装控制台自动登录，防双节点互踢会话 |

- 发布流水线：显式 commit → `git cat-file blob` 逐文件导 LF（`git archive` 已实测产出 CRLF，禁用）→ 逐文件 SHA-256 + CR=0 → `auto_deploy.py` 三阶段（build / deploy / verify，失败可续回滚）→ 重启纪律（mcp 与 bridge 共用包 / venv 须同重启；unit 变更先 daemon-reload）→ `preflight.sh` 34 项**按节点口径读**（当前主 25/34、备 26/34 为既有已知差异）。
- 固件面：S3 镜像按 `hutuji.1.0.x/` 目录 + latest / catalog 指针发布（切换留备份、NOT_FOR_RELEASE 候选禁覆盖公开镜像），GitHub CI 跑 Build Boards 完整板型矩阵；量产 Grbl 走 Web OTA（`/updatefw`）并复核 48 项参数不变。
- 小程序：CLI 上传仅开发版；公众平台提审 / 发布需 MP 后台，版本描述与审核材料不得出现境外服务表述。
- 双机健康面：TG 运维通知（OnFailure / 巡检共用凭据面）、公网 health、bridge 单活对账。

## 10. 测试与验证体系

| 面 | 规模（当前口径） | 入口 |
|---|---|---|
| MCP 契约 / 注入回归 | 1692 项 | `mcp-server/.venv` pytest |
| Portal | 610 项 | draw-portal tests（hub 门禁不含，改动须单独跑） |
| 小程序逻辑 | 约 350 项 | weapp Node 测试 |
| S3 / Grbl host | 389 / 76 项（10-21 口径） | 各 fork host 测试 |
| 产线工具 | 独立套件 | `tests/production` |
| fz 门禁 | standard profile（仿真层 Wokwi / QEMU 缺口按 2026-10-03 拍板登记） | fz 仓 |
| 本仓门禁 | docs / hub / full 三层 | `scripts/agent_gate.py` |
| 闭环 e2e | `e2e_local.py` 打印 PASS（需本地 2323 端口仿真 Grbl） | mcp-server scripts |
| 生产 preflight | 34 项，双机数字分开读 | `deploy/preflight.sh` |
| 云 CI | S3 Build Boards 完整矩阵 + Hub / Grbl 工作流 | GitHub Actions |

验证纪律：接口调用 ≠ 屏幕点击 ≠ 纸面验收；仿真缺口不写成实机证据；测试计数按批次登记；`cross_repo_sync.py --gate-all` 不把「源码检查」写成「已构建」；改了 Grbl 必跑 fz 且以 fz 结论为准。

## 11. 当前完成度快照（2026-10-22）

> 本快照是系统级视图；状态真值仍以 `docs/release-readiness.md` 为准。

- ✅ 软件工程面：三仓「审查 → 修复 → 发布 → OTA → 控制回归」滚动 20+ 批全部留证；双机 deploy + verify、远端 SHA 逐位相符；S3 CI 完整矩阵、hub / fz / Portal / 产线门禁与各仓 pytest 全绿（规模见 §10）。
- ✅ 设备控制面：预览确认 / 取消、暂停 / 继续、停止归位、重画、试笔、分轴调速读回、OTA 拒绝矩阵（同版 / 降级 / 坏 SHA）均有实机证据；48 项参数多批复核不变。
- 🟡 收尾中：F49 五项待修（TLS 迟到回调访问已销毁 owner、WebSocket 析构销毁已放弃接收对象、Grbl 旧应答流入新连接、切换设备过期另一设备 OTA 锁、无效状态正文误作放锁证据）；Freenove 目标板 1.0.49 未 OTA（板上 1.0.48）；SenseNova key 泄露待轮换后切 u1.5-lite 并做付费 live draw；小程序 0.3.33 待平台提审发布。
- 🔴 实体放行：`factory_paired=false`、纸面尺量未回填、4 小时 / 30 次老化未启动、真实 SN / 操作员 / 照片、交付与法规签核全部 Hold——本系统**不出货级宣称**。

## 12. 演进规划（下一阶段）

**P0（放行阻断）**

1. 实体签核链：20mm 标准件纸面尺量回填 → 4 小时 / 30 次老化 → 工厂配对 → 交付资料与法规签核（顺序与判据见 `production-sop.md` / `release-checklist.md` / `compliance.md`）。
2. 安全收尾：商汤 key 轮换 → 生产切 `sensenova-u1.5-lite` → 付费 live draw 验证；F49 修复批次（先 S3 TLS 生命周期与 WebSocket 回收，后 Portal OTA 锁语义，再 Grbl 应答会话归属）。

**P1（体验与量产）**

3. 设备 HIL 收口：屏幕侧预览确认 / 取消与暂停 / 继续 / 停止 / 重画 / 试笔剩余项、断连恢复 `paper_active` 窗口 abort 复测（默认下一刀，见 `agent-handoff.md`）。
4. 量产爬坡：空白双板首烧 / 返修 SOP 实跑、中文 SSID 手机矩阵、portal 固件灰度实装、图库第二批扩充。

**P2（增长与技术债）**

5. 运营面：TG 冷备份启用评估、里程碑通知上线核对、官方号内容计划。
6. 仿真层：Wokwi / QEMU 启动缺口维持拍板口径（QEMU 窄豁免生效、Wokwi 阻断登记），不为求绿改产品。
7. BLE 阶段 B / C：先做 WiFi / Telnet 共存量化门槛与功耗 / 温升，再评估是否推进。

## 13. 文档导航

- 冲突裁决顺序：根 `README.md` → `protocol.md` → `bringup-sop.md` → `release-readiness.md`（§0）。
- 治理与门禁：`agent-handoff.md` / `agent-anti-drift.md` / `agent-constraint-matrix.md` / `agent-gate.md` / `agent-edit-allowlist.json` / `worktree-inventory.md`。
- 契约与执行：`protocol.md`（v0.5）、`bringup-sop.md`、`daytime-tuning-runbook.md`。
- 运维与诊断：`deployment.md` / `operations.md` / `troubleshooting.md`。
- 生产与合规：`production-sop.md` / `release-checklist.md` / `compliance.md` / `customer-quickstart.md`。
- 开发：`development.md` / `mcp-server/README.md` / `draw-portal/README.md`。
- 批次设计记录索引（`docs/design/`，按主题）：
  - 发布类：`three-repo-release-20261012`、`three-repo-release-20261021`、`dr10-release-20261011`、`tls-release-20261022` 等；
  - 审查类：`three-repo-f49-review-20261022`（最新一轮，含未闭项）、`three-repo-review-*` 系列；
  - 画质类：`photo-sketch-quality-20260912`、`ack-photo-quality-20260925`、`weapp-photo-followup-20261008`；
  - 体验类：`weapp-improvements-20261008`、`onboarding-improvements-20261021`、`s3-screen-polish-20261021`、`experience-optimization-20261022`；
  - 工具 / 产线类：`production-readiness-20261010`、`production-tools-review-20260914`、`drawing-market-20260914`、`fz-release-safety-20261008`、`fz-startup-qemu-exemption-20261003`。
