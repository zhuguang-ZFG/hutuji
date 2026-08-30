# 对话采集 + usage 事件（双链）

> 状态：仓内实现（2026-08-29）；生产部署须 scp + restart + 固件 OTA。

## 双链

| 链 | 数据源 | 入库 | 关联用户 |
|----|--------|------|----------|
| **1 对话** | 固件 STT/TTS（`application.cc` 与 `>>`/`<<` 同源） | `conversation_events` | 设备 MAC → `device_bindings` |
| **2 usage** | draw-portal 埋点 + MCP `tool_*` | `usage_events` | portal 事件带 `user_id`；MCP 仅全局计数 |

## API

### 设备上报（链 1）

```
POST /draw-upload/api/device/conversation/report
Content-Type: application/json

{
  "mac": "aa:bb:cc:dd:ee:ff",
  "session_id": "98e635b3",
  "events": [
    {"role": "user", "content": "画一个农民。", "client_ts": 1734567890.0},
    {"role": "assistant", "content": "已经开始生成…", "client_ts": 1734567891.0}
  ]
}
```

- 仅 **已绑定** MAC 接受（403 否则）
- 单条 content ≤500 字；保留 **90 天**（`DRAW_PORTAL_CONVERSATION_RETENTION_DAYS`）
- 幂等：`dedupe_key` 去重

### MCP / 内部 usage（链 2）

```
POST http://127.0.0.1:8302/api/internal/usage
X-Hutuji-Usage-Secret: <与 portal 相同>
Content-Type: application/json

{"source":"mcp","event_type":"tool_draw","ok":true}
```

- 仅 **loopback** 客户端
- MCP env：`HUTUJI_USAGE_INGEST_URL` / `HUTUJI_USAGE_INGEST_SECRET`

## 环境变量

| 变量 | 服务 | 说明 |
|------|------|------|
| `DRAW_PORTAL_USAGE_INGEST_SECRET` | draw-portal | 内部 usage 写入密钥 |
| `DRAW_PORTAL_CONVERSATION_RETENTION_DAYS` | draw-portal | 默认 90 |
| `HUTUJI_USAGE_INGEST_SECRET` | hutuji-mcp | 与 portal 相同 |
| `HUTUJI_USAGE_INGEST_URL` | hutuji-mcp | 默认 `http://127.0.0.1:8302/api/internal/usage` |

## 固件

- `hutuji_conversation_report.cc`：RAM 队列 + 异步 HTTPS POST
- 挂点：`application.cc` STT/TTS 与 `SetChatMessage` 同级
- **须 OTA 刷机后生效**；未刷机前仅链 2（网页/MCP）有数据

## 运营后台

- `draw-admin`：`/api/users/{id}/conversations`、近 7 天 `usage_7d`
- 部署：`hutuji-draw-admin` + nginx `/draw-admin/`（8303）
- 对话查看写入 `admin_audit_log`：`view_conversations` / `view_conversation_detail`

## 隐私

- 绑机页已展示隐私说明 + 勾选同意框；确认绑定即授权对话文本采集（90 天、不含录音）
- 后台访问记 `admin_audit_log`（封禁/解绑/对话查看）

## 未验证

- 固机对话上报端到端 HIL（固件 OTA 进行中）
- 队列断网重传长期压测
- Telegram 图床 PoC（见 `docs/design/telegram-image-host-poc.md`，未接入）
