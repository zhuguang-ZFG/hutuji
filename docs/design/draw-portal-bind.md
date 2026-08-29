# 呼图云 draw-portal：买家绑机 + 网页传图（设计稿）

> 状态：**P0 已上线（阿里云 2026-08-29）**；契约正文：`docs/protocol.md` §10.4（DRAW-BIND schema v1）。  
> 契约正文：`docs/protocol.md` §10.4（DRAW-BIND schema v1）。  
> 与 kb-admin（售后 FAQ 运维后台）**分产品面**；draw-portal 须完整用户体系。

## 0. 决策摘要

| 项 | 选择 |
|---|---|
| 入口 | 公网 `https://hutuji.donglicao.com/draw-upload/`（独立服务，nginx 前缀 `/draw-upload/`） |
| 用户 | 买家注册/登录（手机号或邮箱 + 密码；短信验证码二期） |
| 绑机 | **设备屏 QR + bind_code**（DRAW-BIND v1）；不用粘贴 token |
| 推送 | 转换完成后经**该用户绑定 device token** 调小智 `/messaging/device/tools/call` → `hutuji.draw` |
| 预览保存 | 用户可「保存预览」进个人图库；**不依赖** `/files/` 600s TTL |
| agent | 仍共用一个官方 agent（现网 `2094251`）；靠 per-device token 区分买家 |
| 固件 | S3 板级挂点：设置/控制抽屉「绑定呼图账号」；Grbl/出图主链不改 |

硬约束：G-code 只在阿里云 MCP/draw-portal 生成；S3 仍哑管道。不替代小智对话云。

## 1. 与现有能力的关系

```
OTA 激活 (/register)          DRAW-BIND（本设计）
      │                              │
      ▼                              ▼
  设备进 agent 2094251          买家账号 ↔ 这台 S3 ↔ device token
      │                              │
      └────────── 两步都要 ──────────┘
```

| 已有 | 本设计复用 |
|------|-----------|
| `hutuji_activation_relay` + `/register` | 激活仍走原路径；绑机**不替代**激活 |
| `lcd_display` QR 渲染（配网同源） | 绑机屏 QR |
| `websocket_protocol` NVS `token` | announce 时上报（HIL 验证是否等于 messaging JWT） |
| `trace_bitmap_to_polylines` + `validate_gcode` | 网页上传 PNG/JPEG → 同描摹链 |
| `_chain_device_draw` 模式 | 改为 **per-user token** 参数化 |

## 2. 用户可见流程

### 2.1 首次绑机

1. 小派联网且已激活（能跟小智云对话）。
2. 设置 → **绑定呼图账号** → 屏上显示 **6 位码 + QR**（10 分钟有效）。
3. 买家手机扫 QR → 打开 `/draw-upload/bind?c=XXXXXX` → 登录/注册 → 点 **确认绑定**。
4. 屏提示「绑定成功」；网页进入传图主页。

### 2.2 传图 + 保存预览 + 推送

1. 登录 → **上传图片**（PNG/JPEG，线稿为佳）。
2. 云端描摹 → G-code + 屏幕用 PNG 预览（与语音链路同契约）。
3. 网页展示预览图；用户可选：
   - **发送到小派**：调 `hutuji.draw`，屏上预览；**须在设备屏点「开始画」**（或语音确认）才真正出图；
   - **保存预览**：写入个人「已保存」图库（见 §4.3）。
4. 已保存条目可：**再次发送到小派**、**下载预览**、**删除**（不删他人数据）。

### 2.3 解绑 / 换机 / token 轮换

- 网页 **解绑此设备** → 删除 server 侧 token；设备可再出 QR 绑新账号。
- 用户在小智控制台重开「主题配置」会使 token 作废 → 网页提示「推送失败，请在小派上重新绑定」；固机再出 QR 即可。

## 3. API 概要（draw-portal 服务）

前缀：`/draw-upload/api/`（uvicorn `--root-path /draw-upload`）。

### 3.1 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auth/register` | 邮箱/手机 + 密码 |
| POST | `/auth/login` | 会话 cookie |
| POST | `/auth/logout` | |
| GET | `/auth/me` | 当前用户 + 是否已绑机 |

### 3.2 DRAW-BIND（契约见 protocol §10.4）

| 方法 | 路径 | 调用方 | 说明 |
|------|------|--------|------|
| POST | `/device/bind/announce` | S3 | `mac`, `bind_code`, `device_token` |
| POST | `/user/bind/complete` | 已登录浏览器 | `bind_code` |
| POST | `/user/bind/unbind` | 已登录 | 解绑当前设备 |
| GET | `/user/bind/status` | 已登录 | mac 摘要、绑定时、token 探活结果 |

### 3.3 传图与保存预览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/draw/upload` | multipart 图片 → 转换 → 返回 `job_id`、临时 `preview_url`/`gcode_url` |
| POST | `/draw/{job_id}/send` | 用**本用户** device token 推 `hutuji.draw` |
| POST | `/draw/{job_id}/save` | **保存预览**到个人图库（见 §4.3） |
| GET | `/draw/saved` | 已保存列表（缩略图 + 标题 + 时间） |
| GET | `/draw/saved/{id}` | 单条详情（含预览、可选 gcode 能力 URL 若仍有效） |
| POST | `/draw/saved/{id}/send` | 从图库再次推到小派（须 gcode 仍可用或存盘副本） |
| DELETE | `/draw/saved/{id}` | 删除已保存条目 |

限速：upload 按用户；announce 按 MAC+IP（nginx 独立 zone）。

## 4. 数据模型（SQLite，路径 `/var/lib/hutuji/draw-portal/`）

### 4.1 `users`

| 字段 | 说明 |
|------|------|
| `id` | UUID |
| `email` / `phone` | 二选一唯一 |
| `password_hash` | bcrypt 或 argon2 |
| `created_at` | |

### 4.2 `device_bindings`

| 字段 | 说明 |
|------|------|
| `user_id` | FK |
| `device_mac` | 小写 `aa:bb:…` |
| `device_token_enc` | AES-GCM；**禁止明文日志** |
| `bound_at` / `last_push_at` | |
| `token_probe_ok` | 最近一次 `hutuji.status` 探活 |

一用户 **一期仅一台设备**（`UNIQUE(user_id)`）；二期可多机。

### 4.3 `bind_sessions`（短期）

| 字段 | 说明 |
|------|------|
| `bind_code` | 6～8 位 |
| `device_mac` | |
| `device_token_enc` | announce 写入 |
| `expires_at` | 10 分钟 |
| `consumed_at` | complete 后标记 |

### 4.4 `draw_jobs`（单次上传事务）

| 字段 | 说明 |
|------|------|
| `id` | UUID |
| `user_id` | |
| `status` | `processing` / `ready` / `failed` |
| `source_name` | 原始文件名（脱敏后存） |
| `preview_stem` | 与 MCP output 命名对齐或独立 |
| `gcode_stem` | |
| `error_summary` | |
| `created_at` | |

### 4.5 `saved_previews`（保存预览 — 用户要求）

| 字段 | 说明 |
|------|------|
| `id` | UUID |
| `user_id` | |
| `job_id` | 可选 FK |
| `title` | 用户可改，默认「上传 YYYY-MM-DD HH:mm」 |
| `preview_path` | **用户域持久化 PNG**（不跟 `/files/` TTL 走） |
| `gcode_path` | 可选持久化 G-code 副本（再次发送用；≤512KiB） |
| `saved_at` | |

**保存预览语义**：

- 用户点「保存预览」时，从本次 job 的 PNG（及可选 G-code）**复制**到 `saved_previews` 专属目录（`preview_path` / `gcode_path`），与 MCP `output/` 600s TTL **解耦**。
- 图库列表只 serve 用户域文件（draw-portal 鉴权路由 `GET /api/draw/saved/{id}/preview.png`），**不**把 capability token URL 暴露给前端长期 bookmark。
- 默认保留上限：每用户 **50 条** saved（LRU 挤掉最旧）；可配置。
- 「再次发送」：若 `gcode_path` 在，重新签发短时 HTTPS 能力 URL（或内网直读）→ `hutuji.draw`；若仅保存了预览无 gcode，按钮灰显并提示「仅保存了预览，需重新上传出图」。

## 5. 固件（xiaozhi-esp32，板级）

新增模块建议：`hutuji_draw_bind.{h,cc}`（lichuang-dev + waveshare 共用逻辑）。

| 项 | 约定 |
|----|------|
| 入口 | 设置或控制抽屉「绑定呼图账号」 |
| QR 载荷 | `https://hutuji.donglicao.com/draw-upload/bind?c=<code>` |
| `bind_code` | 密码学随机 6 位 `[A-Z0-9]`，10 分钟；屏上大字 + 倒计时 |
| announce | `POST https://hutuji.donglicao.com/draw-upload/api/device/bind/announce` JSON；模式同 `hutuji_activation_relay.cc` |
| `device_token` | 固件 announce 上报（当前常为 `websocket.token`）；**绑定表须存 messaging JWT**（见 §9） |
| 成功反馈 | 轮询 `GET .../bind/status?code=` 或 MQTT custom（二期）；一期可 3s 轮询 announce 返回的 session 直到 `consumed` |
| NVS | `hutuji_bind/bound=1`、最近绑定时间 |

**HIL 必验**：announce 的 token 能否通过 `/messaging/device/tools/call`。若 401，规格记「未验证」，二期改 token 来源或走小智官方 device API。

## 6. 部署

| 项 | 值 |
|----|-----|
| 服务名 | `hutuji-draw-portal` |
| 端口 | `127.0.0.1:8302` |
| venv | **共用** `/opt/hutuji/mcp-server/.venv`（含 vpype/描摹；独立 venv 无描摹链） |
| env | `/etc/hutuji/draw-portal.env`（独立；含 `DRAW_PORTAL_SESSION_SECRET`、加密主密钥、SMTP、可选共用 `HUTUJI_PUBLIC_BASE_URL`） |
| nginx | `location /draw-upload/` → 8302；upload body ≤4MB |
| MCP 包 | `PYTHONPATH=/opt/hutuji/mcp-server` 复用 `hutuji_mcp` 描摹链 |

京东云热备：**一期仅阿里云**（与 kb-admin 同口径）。

## 7. 安全

- device_token / 会话 cookie：**不入库到 git**；draw-portal 专用加密。
- 浏览器**永不**接收 device_token 明文；只显示「已绑定 / 未绑定」。
- announce：仅 HTTPS；bind_code 单次消费；过期拒绝。
- complete：须登录；bind_code 与 announce 的 mac 一致。
- 可选：complete 前用控制台 JWT 校验 mac ∈ agent 设备列表（同 `activation_relay`）。

## 8. 实施顺序（P0 之后）

| 阶段 | 内容 | 估时 |
|------|------|------|
| M1 | kb-admin 批量 FAQ（并行） | ~3d |
| M2a–M2d | draw-portal 绑机 + 传图 + 部署 | ✅ 阿里云已部署；见 §9 HIL |
| **M3a** | 传图扩格式（SVG 直转 + WebP/GIF/BMP 转 PNG） | ~2–3d |
| **M3b** | `/draw-admin/` 运营后台（用户/封禁/代解绑/统计） | ~4–5d |
| **M3-email** | 邮箱验证码注册（SMTP；短信 M3c 暂缓） | ~2d |
| M3c | 手机号 + 短信（阿里云 SMS） | 暂缓 |

### M3a 传图格式

- **SVG**：`_validate_svg` → `svg_to_gcode`（不经描摹）
- **PNG/JPEG**：现有描摹链
- **WebP/GIF/BMP**：`imageio` 转 PNG 后描摹
- 上限 4MB；注入闸门与 MCP 同源

### M3b 运营后台

- 入口：`https://hutuji.donglicao.com/draw-admin/`（8303）
- 鉴权：`DRAW_ADMIN_PASSWORD` + 独立会话 cookie
- 能力：用户列表、详情（绑机/传图/图库）、封禁/解封、代客解绑、平台统计、审计日志

### M3-email 注册

- `POST /auth/register/request` → SMTP 发 6 位码
- `POST /auth/register/verify` → 创建用户并登录
- 未验证/封禁账号不可传图；生产须配置 `DRAW_PORTAL_SMTP_*`
- 开发可设 `DRAW_PORTAL_EMAIL_DEV_EXPOSE_CODE=1` 在 JSON 回显码（**生产禁开**）

## 9. HIL 与未闭合项（2026-08-29）

### 已验证（不得写成出货级结论）

- [x] 网页 QQ 邮箱注册/登录、上传描摹、保存预览、图库重发（API 8/8）
- [x] 设备 QR 绑机 → announce 200 → complete（COM14 实锤 bind 栈溢出已修：单任务 8192）
- [x] messaging token 写入绑定表 → `hutuji.status` 探活 OK → `hutuji.draw` 推送 OK
- [x] 设备屏预览出现；用户点「开始画」后纸面出图（2026-08-29 用户确认）

### 未验证 / 待闭合

- [ ] 固件 announce 自动带 messaging JWT（当前靠运维写绑定表；`hutuji_draw_bind` + `ota.cc` messaging 段在 xiaozhi 工作树未提交）
- [ ] `websocket.token` == messaging JWT（已证伪；两 token 职责不同）
- [ ] 单 agent 多设备量产上限（须问小智官方）
- [ ] 保存预览 50 条上限与磁盘用量长期观测
- [ ] 多买家/换机/主题配置重签 token 的运维 SOP 自动化
- [ ] 真实非运维账号全流程（扫码绑机 → 传图 → 确认出图）书面签核
