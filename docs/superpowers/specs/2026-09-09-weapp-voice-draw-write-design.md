# 小程序语音画+写（小程序确认）设计

> 状态：**已批准**（2026-09-09 用户确认方案 3 + 小程序确认）  
> 前置：P1 ASR/对话已通（§10.4.13）；本批补「能出图」缺口  
> 范围：draw-portal + weapp；不改固件、不改 §5/换纸

## 0. 决策摘要

| 决策点 | 选择 |
|--------|------|
| 能力 | **画 + 写**（描述生图 / 文本排版写字） |
| 确认门 | **小程序聊天页**「开始画 / 取消」；**不**自动 `hutuji.confirm` |
| 设备预览 | 生成成功后仍 `hutuji.draw` 只上预览（机身可同步见预览） |
| 取消 | 点取消 → messaging `hutuji.abort`（清预览/待确认） |
| 绑机 | 未绑定 → 工具回「请先绑定写字机」，不生成 |

## 1. 背景

P1 白名单仅 `status/remember/recall/forget/abort`，用户说「画/写」时智能体无法触发生成与推送。机身麦克风走官方云 MCP 链；小程序语音走 portal 编排，须自接生成 + 推送。

## 2. 流程

```
按住说话 → ASR → turn
  → LLM 调 voice.draw(description) 或 voice.write(text)
  → portal：日限额检查 → 生成（复用 /internal/draw 或 process_write_text）
  → publish 公网 url/preview_url → push_draw_self_heal(auto_confirm=False)
  → 回包带 preview{preview_url,job_id,kind} + 口语 reply
  → 聊天气泡预览卡 +「开始画」「取消」
  → 开始画：POST /api/voice/confirm → hutuji.confirm
  → 取消：POST /api/voice/cancel → hutuji.abort
```

## 3. 工具与 API

### 3.1 LLM 可调（portal 侧，非设备原语）

| 名 | 参数 | 行为 |
|----|------|------|
| `voice.draw` | `description` | 同网页文字生图：`request_internal_draw` → materialize → publish → 推预览 |
| `voice.write` | `text` | 同 `/api/draw/write`：排版 → publish 多页 → `pages_arg` 推预览 |

仍保留设备工具：`hutuji.status` / `remember` / `recall` / `forget` / `abort`。  
**禁止** LLM 直接调 `hutuji.confirm` / `hutuji.draw`（确认只经预览卡按钮）。

### 3.2 HTTP

| 路径 | 约定 |
|------|------|
| `POST /api/voice/turn` | 回包增可选 `preview: {preview_url, job_id, kind}` |
| `POST /api/voice/confirm` | 须登录+绑机 → `hutuji.confirm` |
| `POST /api/voice/cancel` | 须登录+绑机 → `hutuji.abort` |

### 3.3 限额与超时

- 画：计入与网页生图同一 `web_generate` 日限额（或新增 `web_voice_draw` 同源日 cap，实施选一、文档钉死）
- 写：沿用写字路径；超长/缺字库同 `/api/draw/write`
- turn 内同步等待生成；超时文案白话，不回显上游

## 4. 小程序

- chat 页：assistant 消息可带 `previewUrl` / `jobId` / `kind`；渲染图 + 两按钮
- 确认/取消后按钮禁用并 toast；失败可再试或去控制页「开始画」兜底
- weapp 版本 bump + CLI 上传

## 5. 非目标

- 自动开画（与相册 send 的 auto_confirm 区分）
- TTS、设备麦同会话、改固件预览态
- 宣称可出货 / 联调完成

## 6. 验收

- 已绑机：说「画一只猫」→ 气泡出预览 → 点开始画 → 机台开始灌流（或 status 进 downloading/streaming）
- 说「写：春眠不觉晓」→ 同路径写字预览 → 确认开画
- 点取消 → 预览取消、未落笔
- 未绑机 → 提示绑定，无预览卡
