# Telegram 图床 PoC 调研（2026-08-30）

> 状态：**调研完成，未接入生产**。结论：可作个人/备份降本实验，**不替代** `hutuji.donglicao.com/files/` 与 draw-portal 图库主链路。

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
| `HUTUJI_TG_BOT_TOKEN` | @BotFather 签发 |
| `HUTUJI_TG_CHANNEL_ID` | 私有频道 chat_id（bot 为管理员） |
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
