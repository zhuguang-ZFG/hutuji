# 小程序机台进度 + 语音聊小派 设计

> 状态：**已批准**（2026-09-09）→ P0 计划 `docs/superpowers/plans/2026-09-09-weapp-progress-p0.md`；P1 语音另开计划  
> 对话定案：进度先 + 语音方案 A；小程序与设备麦 **独立会话 ID**  

> 范围：draw-portal + weapp 为主；P0 **不改**固件 `hutuji.status` JSON 形状；P1 ASR 密钥只落 portal  
> 禁止重开：双芯片、WiFi Telnet、官方云 MCP、换纸机械/时序

## 0. 决策摘要

| 决策点 | 选择 |
|--------|------|
| 分期 | **P0 进度先**，**P1 语音方案 A**，P1.5 可选 TTS |
| 进度数据源 | 只消费现有 `hutuji.status`（经 portal `/api/device/control`） |
| 进度推送 | P0 **不加** portal 代拉推送 / WebSocket；仅客户端自适应轮询 |
| 行进度 | **不造** `lines_sent` 百分比（当前 status 未暴露行计数）；用 state + 页 + `elapsed_ms`/`eta_ms` |
| 语音架构 | 小程序 ASR → 文本 → **同一智能体 / 同一套 `hutuji.*`**；**独立会话 ID**（不与设备麦合并） |
| 音频路径 | **禁止**小程序直连设备麦/喇叭；**禁止**旁路官方云自建对话栈 |
| 密钥 | 腾讯云 ASR 密钥只在 portal env；不进 weapp 包 |

## 1. 背景与约束

- 控制页已有 5s 轮询、`connected`/`state`/`mpos_*`/`page`/`pages`/`grbl_state` 人话行；缺时间线、缺出图中加速刷新、缺页进度条与 ETA。
- 固件 `Job::StatusJson()`（xiaozhi）已提供：`connected/ready/authorized/state`、可选 `page/pages`、`grbl_state`、`mpos_*`、`last_line`、纸/电机遥测、出图中 `elapsed_ms`/`eta_ms`、`ota{state,progress,...}`。契约见 `docs/protocol.md` §4 `hutuji.status`。
- 用户要「小程序语音聊小派 + 看写字机进度」：进度对齐 RainMaker/涂鸦类属性订阅思路，本仓等价物 = messaging + `hutuji.status`；语音对齐「端侧 ASR → 官方智能体」而非端到端设备音频。

## 2. P0：控制页进度

### 2.1 字段 → UI 映射

| `hutuji.status` 字段 | UI | 规则 |
|----------------------|-----|------|
| `connected` | 顶栏点 +「已连接/未连接」 | 保持现状 |
| `state` | 主标签 + **时间线当前步** | 见 §2.2 |
| `page` / `pages` | 页进度条与 `page/pages` 文案 | 仅 `pages > 0` 时显示；单页字段不出现则整块隐藏 |
| `mpos_x/y/z` + `grbl_state` | 次行坐标 | 禁止原始 `<Idle\|MPos:…>` / `Bf:` / `FS:` |
| `elapsed_ms` / `eta_ms` | 「已画 mm:ss · 预计 mm:ss」 | 固件 `eta_ms` = 按已发比例外推的**预计总时长**（非剩余）；UI 勿标「约剩」。有则显示；缺省省略；**不**伪造完成百分比（status 未暴露 `lines_sent`） |
| `paper_changing` / `paper` | 换纸提示 | `paper_changing=on` 时时间线落在换纸步 |
| `ota.state` / `ota.progress` | OTA 角标或次行 | 仅非 `idle`；与出图时间线分离 |

### 2.2 时间线（人话步）

固定展示（当前步高亮，终态着色）：

1. 空闲（`idle` / `ready` / 空）
2. 预览（`previewing`）
3. 待确认（`awaiting_confirmation`）
4. 准备中（`downloading` / `verifying` 合并）
5. 出图中（`streaming`；`paused` 同步但标「已暂停」）
6. 换纸（`paper_change` 或 `paper_changing=on`）
7. 结束（`done` / `error` / `aborted`；错误/中止用警示色）

未到达的步灰色；不单独展示 `verifying` 字面量。

### 2.3 轮询

| 条件 | 间隔 |
|------|------|
| 默认 / 空闲终态 | **5s**（现状） |
| `state ∈ {downloading, verifying, streaming, paused, paper_change}` 或 OTA 进行中 | **1.5s** |
| 回 `idle`/`done`/`error`/`aborted` 且 OTA idle | 恢复 **5s** |

实现：`onShow` 仍设 interval；每次 refresh 后按派生态 `clearInterval` + 重设，或单 timer 内读 `this._pollMs`。离开页必须清 timer（现状 `onHide`/`onUnload`）。

链路不变：`GET` 会话 → `POST /api/device/control` `{tool:"hutuji.status"}` → `mcpResult.parseToolJson`。

### 2.4 P0 改动面

- `draw-portal/weapp/pages/control/control.{js,wxml,wxss}`
- weapp 版本号 bump + 手测清单一行（控制页时间线/页条/加速轮询）
- 白名单：本批批准后追加 `weapp-progress-voice`（或等价）条目，含 control 页路径
- **不改**固件、**不改** `protocol.md` status 字段（除非另批暴露行计数）

### 2.5 P0 验收

- 绑定机：推一页写字/画图 → 时间线经准备中→出图中；空闲恢复 5s 感观可接受
- 多页文章：页条显示 `page/pages` 且随页前进
- 出图中：网络正常时状态更新明显快于空闲轮询
- 禁止宣称可出货

## 3. P1：语音（方案 A）

### 3.1 流

```
按住说话 → 小程序 RecorderManager → 上传 portal
  → 服务端腾讯云 ASR（密钥仅 portal env）
  → 文本 → 官方智能体（独立 session_id，工具仍 hutuji.*）
  → 小程序聊天气泡显示回复（P1 先文字；P1.5 再 TTS）
```

### 3.2 会话与并发

- 小程序会话 ID **独立于**设备麦当前会话；不要求和设备「同一会话」。
- 工具面与设备语音相同（`hutuji.*`）；设备侧 busy/幂等既有契约继续生效。
- 不在小程序侧实现「抢麦」协议。

### 3.3 P1 改动面（意向）

- portal：ASR 签权/转发 API + 智能体文本入口（具体路径实施计划钉死）；env 腾讯云密钥
- weapp：按住说话 + 聊天气泡页或控制页附属区
- 测：ASR 代理鉴权负向；不落密钥到客户端产物
- 文档：新增 API 时补 `protocol` 门户小节；readiness/inventory 随证据

### 3.4 P1 验收

- 按住说一句 → 气泡出字
- 能触发只读或记忆类工具冒烟（如 status / remember，messaging HIL）
- P1.5 TTS 另开，不挡 P1 收口

## 4. 非目标

- 小程序 WebRTC/直连设备音频
- 自建 LLM 对话栈绕过小智官方云
- P0 portal 服务端推送进度
- 固件新增行级进度字段（可后续单独立项）
- 宣称可出货 / 联调完成（除非 readiness 对应行已 ✅）

## 5. 参考口径（证据级，非本仓实现）

- 腾讯云实时 ASR WebSocket：密钥服务端持有、客户端只拿短时凭证或走代理上传
- IoT 属性订阅（RainMaker / 涂鸦类）：本仓用 messaging + 轮询 `hutuji.status` 等价；P0 不做真长连接

## 6. 实施顺序

1. 本 spec 用户过目确认  
2. `writing-plans` → `docs/superpowers/plans/2026-09-09-weapp-progress-voice.md`（P0 可执行；P1 可同文件后半或拆文件）  
3. 白名单 + P0 实现 + 手测 + 文档证据  
4. P1 实现（密钥就绪后）
