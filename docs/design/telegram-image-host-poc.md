# Telegram 图床 PoC 调研（2026-08-30）

> 状态：**PoC 已接入（2026-09-12，频道 @danqing45，lima 节点 `HUTUJI_TG_UPLOAD_ENABLED=1`）**：`draw_portal/telegram_archive.py` 图库保存后台归档 + 图库删除尽力撤回 + 列表 `tg_backup` 状态字段；可作个人/备份降本实验，**不替代** `hutuji.donglicao.com/files/` 与 draw-portal 图库主链路（主链路契约一字未动）。

## 1. 用户诉求

GitHub 上常见「Telegram 无限云盘 / 图床」项目，能否用于网页生图结果的长期、低成本存储？

## 2. 常见开源方案（外部证据）

| 项目 | 语言 | 机制 | 单文件上限 | 备注 |
|------|------|------|-----------|------|
| [tgdrive/teldrive](https://github.com/tgdrive/teldrive) | Go | 用户 MTProto + 频道当目录 | ~2GB（用户账号） | 文档警告滥用会封号/删频道 |
| [Dominux/Pentaract](https://github.com/Dominux/Pentaract) | Rust | Bot 上传到私有频道，分块重组 | Bot API 20MB，分块可更大 | 需 Postgres + storage worker bot |
| [fbpatel003/tgdrive](https://github.com/fbpatel003/tgdrive) | TS | 浏览器 GramJS 直连，零后端 | 2GB | 凭据存 IndexedDB，不适合服务端 |
| [GeekLuffy/PixEdge](https://github.com/GeekLuffy/PixEdge) | Next.js | Bot API v1 + 可选 MTProto v2 | 20MB / 2GB | 图床向，302 到 Telegram CDN |
| [realldz/tele-drive](https://github.com/realldz/tele-drive) | Node | 分块加密 + S3 兼容 API | 分块突破 20MB | 运维面最重 |

访问日期：2026-08-30。以上均为社区项目，**非 Telegram 官方「无限存储」产品**。

## 3. Telegram 平台硬限制（官方 Bot API + 社区实测归纳）

| 维度 | Bot API（@BotFather bot） | MTProto（用户账号 / gramjs） |
|------|---------------------------|------------------------------|
| 单文件上传 | **20 MB**（常见实现上限） | **~2 GB** |
| 存储配额 | 无公开「无限」SLA；频道/账号受 ToS 与滥用检测约束 | 同左 |
| 下载 | `getFile` → 临时 URL，有时效 | CDN 直链 / 302 跳转 |
| 合规 | 内容在 Telegram 服务器；商用与用户隐私需单独评估 | 同左 |
| 风险 | 频率限制、频道被封、**无 SLA** | 账号 API 滥用可封禁 |

**「无限」含义**：通常指「不按 GB 计费、个人频道可堆很多文件」，**不是**企业级对象存储的可用性保证。

## 4. 与呼图现有架构的关系

当前生产链路（禁止替换）：

```
MCP hutuji_draw → /files/{draw_*.gcode,svg} TTL 600s → S3 哑管道下载
draw-portal  → 用户图库副本在 /var/lib/hutuji/draw-portal/（SQLite + 文件）
```

Telegram 图床**适合**：

- 个人作品**冷备份**（PNG 预览，非 G-code）
- 运营/客服**外部分享链接**（只读、非设备链路）
- 降本实验（小流量、可丢）

Telegram 图床**不适合**：

- 设备 `hutuji.draw` 的 capability URL（需 HTTPS 自有域、CRC、TTL 契约）
- G-code 主分发（512KiB 虽可上传，但链路不可控）
- 买家数据合规主存（对话/生图审计在自有 SQLite）

## 5. 推荐 PoC 范围（最小可验证）

### 5.1 架构（PixEdge / Pentaract 简化版）

```
draw-portal 保存预览 PNG
    → 可选后台任务 upload_telegram_preview()
    → Bot sendDocument 到私有频道 @hutuji_archive
    → SQLite 增列 telegram_file_id / telegram_message_id
    → 对外仅暴露「备份状态」，不替换现有 preview_url
```

### 5.2 环境变量（PoC，不入库）

| 变量 | 说明 |
|------|------|
| `TG_BOT_TOKEN` | @BotFather 签发（与运维通知面同凭据面，2026-09-12 实测口径：模块读 `TG_*`，不是 `HUTUJI_TG_*`） |
| `TG_CHANNEL_ID` | 私有频道 chat_id（bot 为管理员） |
| `TG_PROXY` | 出网代理（lima=`http://127.0.0.1:7890`；缺它 python 直发 curl 直连被墙 http=0） |
| `HUTUJI_TG_UPLOAD_ENABLED` | `0/1`，默认 0 |

### 5.3 验收判据

1. 上传 500KB PNG → 返回 `file_id`，可 `getFile` 下载字节一致
2. 连续 50 张不触发 429（或记录退避策略）
3. 频道内可检索；**删除 bot 后链接失效**（记录为限制）
4. **不**改 MCP `/files/` 与设备下载路径

### 5.4 实现工作量（估）

| 项 | 估时 |
|----|------|
| `draw_portal/telegram_archive.py` + 单测 | 0.5d |
| 图库「备份到 TG」按钮 + 状态列 | 0.5d |
| 运维：建频道、bot、env | 0.5d |
| **合计** | ~1.5d（仍不进入设备主链路） |

## 6. 风险与否决条件

- Telegram 封号 / 频道删除 → **无恢复 SLA**，PoC 必须 fail-open（主图库仍在本地）
- Bot API 20MB → 只存 PNG 预览，不存大 SVG/G-code
- 用户生图内容经第三方 → 绑机隐私文案须增「可选冷备份」说明（若上线）
- 中国大陆网络访问 Telegram CDN **不稳定** → 不能作为大陆买家唯一预览源

## 7. 决策建议

| 选项 | 建议 |
|------|------|
| 生产主存储改 Telegram | **否决** |
| draw-portal 图库可选 TG 冷备份 PoC | **可试**（1.5d，默认关闭） |
| 长期降本 | 优先阿里云 OSS 低频 / 京东云对象存储，有 SLA 与备案路径 |

## 8. 下一步（若用户确认 PoC）

1. 用户提供 Bot token + 私有频道（**勿入库**）
2. 实现 `tmp/telegram_upload_probe.py` 本地探针
3. 通过后挂 draw-portal 可选备份，默认 `HUTUJI_TG_UPLOAD_ENABLED=0`

## 9. 探针复核（2026-09-12 深夜，条件已变化）

与 08-30 调研时的差异：TG bot 已现役（`/etc/hutuji/tg.env`，双机通知面共用凭据面）、lima sing-box（127.0.0.1:7890）已实证出网。当日用现有 bot 在 lima 跑了三轮真实链路探针（临时脚本 `tmp/tg_probe.py` 思路，curl 直发，**非用户数据**：确定性噪声 PNG）：

| 样本 | 上传 | getFile→下载 | sha256 一致 |
|------|------|--------------|-------------|
| 3.4KB PNG | — | 1.45s | ✅（`f0aab7ab…`） |
| 600KB 随机 PNG | 4.6s | 2.35s | ✅（`521c1916…`） |
| 同一 file_id 连续两次 getFile | — | 两次均成功 | — |

结论维持第 7 节不变，新增两条实测注脚：

1. **file_path URL 1h 时效不构成检索障碍**：file_id 长期有效、可随时重新 `getFile` 换新 URL（2026-09-12 两次连发实测），归档读取语义成立；但 file_id 只属于签发它的 bot，删 bot 即失效（PoC §6 已记）。
2. **代理吞吐 ≈130/255KB/s**（600KB 上传 4.6s / 下载 2.35s，经 sing-box）：适合后台逐张归档，**不适合**实时预览热路径。

§8 已按此实施（频道由用户提供：@danqing45 / chat_id -1004350290412，写入服务器 env 不入库；探针改为真实频道复跑，500KB 上传 3.0s、sha256 一致、`message_id=4`）。上线范围 = §5.1 的 PoC 形态（默认关、仅 lima 启用），`tg_backup` 状态经 `/api/draw/saved` 暴露，weapp 展示层后批再挂。

## 10. 已实施记录（2026-09-12 同日深夜，`f89fa25`→`e19eecb`）

按 §5.1 形态上线（默认关 `HUTUJI_TG_UPLOAD_ENABLED=0`，仅 lima=1；jd 不启用），契约落 `docs/protocol.md` §10.4.5。上线当晚两次升级：

1. **worker 横库缺陷修复**（用户评审发现）：原实现 `_ensure_worker(store)` 首次入队永久绑定 Store 实例，同进程多 app/多库（测试、迁移脚本、多租户）会把后续任务写进第一个库。修复 = `_Task` 自带 `store`/`user_id`，worker 只用任务自带实例回写；回归测试 `test_enqueue_task_binds_own_store` 钉双库路由。
2. **分用户批量召回**（用户原话「要做分用户批量召回照片的功能」）：新表 `telegram_archive_log` 台账（上传成功即记 saved_id/user_id/title/file_id/message_id/archived_at，行删除不删台账；用户删图撤回频道副本时标 `tg_removed_at`）。召回面 = `restore_user(store, user_id)` + CLI：

   ```
   python -m draw_portal.telegram_archive --db /var/lib/hutuji/draw-portal/data.db --restore-user <uid>
   ```

   三类处置：文件丢失的在库行就地回拉；trim 清掉的行按台账重建（**仅预览、`gcode_path=NULL`，按 §10.4.5 契约不可再次发送**）；用户主动删除（台账 removed）跳过。逐条 fail-open，`{"restored":N,"recreated":N,"skipped":N,"failed":N}` JSON 摘要。

生产证据：真实用户行归档 message_id=8 → sha256 一致 → `uploaded`+台账；召回演练（删 PNG → CLI → 文件回位 sha256 逐位一致）。部署坑两条：draw-portal.env 缺 `TG_PROXY` 直连被墙；模块重写曾漏 `_json_or_none`（生产 `_upload_document` NameError，`e19eecb` 补回）。
