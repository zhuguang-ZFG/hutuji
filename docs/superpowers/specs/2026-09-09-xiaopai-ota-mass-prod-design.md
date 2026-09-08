# 小派量产远程 OTA 设计（Grbl 暂缓；失败可恢复）

> 状态：**已批准**（2026-09-09 用户回复「批准」）→ 实现计划见 `docs/superpowers/plans/2026-09-09-xiaopai-ota-mass-prod.md`  
> 仓库：hutuji（分发/portal/小程序）+ xiaozhi-esp32（设备侧，复用既有 OTA）  
> 用户拍板：先做小派全远程升；Grbl 暂不做；**必须考虑升级失败**；**写字体验 OTA 完成后再做**；**版本号自定义**；**每天 idle 自检（只查不升）**。

## 0. 决策摘要

| 决策点 | 选择 |
|--------|------|
| 范围 | **仅小派（xiaozhi）** 远程 OTA；Grbl pull-OTA **P2 预留接口，本批不实现** |
| 触发 | 小程序一键 **+ 每天 idle 自检**；**禁止**要求用户同 WiFi / 本地传 bin；**禁止静默强升** |
| 版本号 | **自定义 hutuji 版本串**（不跟上游 `2.4.0` 锁死）；manifest / status / 小程序同一套字符串比较 |
| 技术路径 | **复用**现有 `Ota::Upgrade` / `Application::UpgradeFirmware` / MCP `self.upgrade_firmware`；分发改走**自建 HTTPS 固件仓 + manifest** |
| 失败原则 | **下载/校验失败不切槽、旧固件继续跑**；切槽后启动失败靠 **ESP 双槽 rollback（PENDING_VERIFY）**；小程序可见明确失败态 |
| 写字体验 | **OTA P0 完成后再做**（本批不交付字号/行距/页数） |
| 非目标 | Grbl 远程升（本批）；合并固件；USB 刷机当量产路径；出图中强升；强制全量静默升 |

## 1. 背景与约束

- 量产机在用户家，只能 **设备 pull**。
- 小派已有完整 OTA：`ota.cc` 双槽写入、`UpgradeFirmware` 失败则重启音频继续跑、成功则 reboot；启动时若 `ESP_OTA_IMG_PENDING_VERIFY` 则 `esp_ota_mark_app_valid_cancel_rollback()`（`ota.cc` 约 278–281 行）。
- 官方 MCP 已有 `self.upgrade_firmware(url)`（user-only）；量产应经 **绑定用户的 portal → messaging**，勿把任意 URL 暴露给公网匿名。
- 板型多（waveshare / freenove / lichuang…）：错板包必须拒收（board 字段）。
- 双芯片不合并：本批只升小派；写字机固件版本仅在 UI **只读展示**（来自 `hutuji.status` / 绑定表缓存），不提供升级按钮（灰显「即将支持」）。

## 2. 架构

```
运营发版 → 阿里云固件仓
  /firmware/s3/{board}/{version}/xiaozhi.bin
  /firmware/s3/{board}/{version}/manifest.json
        ↑
portal GET /api/device/firmware/check  （登录+已绑定）
portal POST /api/device/firmware/upgrade （签发短时升级许可）
        ↓ messaging tools/call
小派：校验 board/version → UpgradeFirmware(url) → 进度上屏
        ↓
成功：reboot，下次启动 mark valid
失败：不 reboot（或启动 rollback），status 回报 failed
小程序轮询展示进度/结果
```

### 2.1 Manifest（示例字段）

```json
{
  "device": "s3",
  "board": "freenove-esp32s3-display-2.8-lcd",
  "version": "hutuji.1.0.0",
  "url": "https://hutuji.donglicao.com/firmware/s3/.../xiaozhi.bin",
  "sha256": "...",
  "size": 3048832,
  "min_compat": "hutuji.1.0.0",
  "notes": "修复多页下载卡死…",
  "upstream_note": "based on xiaozhi 2.4.0（仅备注，不参与比较）"
}
```

- **版本串约定**：`hutuji.<major>.<minor>.<patch>`（例 `hutuji.1.0.0`）。比较用三段整数；与上游官方版号解耦，上游变更只写进 `upstream_note` / 发版说明。设备编译进 `BOARD_NAME` + 该自定义串，与 manifest `version` 一致才算「已是最新」。
- `url` 仅 HTTPS；门户返回给设备的 URL 可带短时签名（防盗链），TTL ≥ 单次下载最坏时间（建议 ≥ 15 min）。
- 设备侧至少校验：**HTTPS、board 匹配、可选 sha256**（若当前 `Ota::Upgrade` 无 sha，本批补强或门户只发已校验包 + Content-Length）。

### 2.2 每天自检（已拍板）

| 项 | 行为 |
|----|------|
| 何时 | 设备 **idle** 且联网；**每天最多一次**（本地记上次 check 日历日；跨日才再查） |
| 做什么 | 只调 portal/manifest **检查**；**绝不自动开始下载/切槽** |
| 有新版本 | 主屏/胶囊轻提示「有固件更新」；小程序打开「固件升级」时高亮；**仍须用户点「立即升级」并二次确认** |
| 无新版本 / 查失败 | 静默（失败可打 debug 日志，不打扰）；次日可再试 |
| 非 idle | 跳过本次；不排队强升 |
| 与小程序 | 小程序「检查更新」随时可手动触发，不受「每天一次」限制 |

### 2.3 小程序 UX

「我的 → 固件升级」：

| 元素 | 行为 |
|------|------|
| 小派当前版本 | 来自最近 `hutuji.status` / 设备上报（自定义 `hutuji.x.y.z`） |
| 写字机版本 | 只读；按钮禁用 +「后续支持」 |
| 检查更新 | 调 portal check |
| 立即升级 | 二次确认（升中勿断电/勿出图）→ upgrade |
| 进度 | 轮询 portal 缓存的设备上报，或 status 扩展字段 `ota={state,progress}` |
| 失败 | 文案区分：网络失败 / 校验失败 / 忙碌拒升 / 启动回滚 |

### 2.4 拒升条件（设备）

任一条满足则拒绝开始升级并回明确 token：

- 非 idle（出图 / 预览 / 换纸 / 配网 / 已在 upgrading）
- board 与 manifest 不符
- 电量/充电策略若日后有电池板再加；当前有线板可省略
- URL 非 https 或主机不在允许列表（防任意 URL 刷恶意包；相对裸 `self.upgrade_firmware` 收紧）

## 3. 升级失败模型（本批必须写清）

| 阶段 | 失败形态 | 设备行为 | 用户感知 | 运维 |
|------|----------|----------|----------|------|
| F1 查版本 | DNS/TLS/4xx/5xx | 保持旧固件 | 「检查更新失败，请稍后」 | journal / portal 记原因码 |
| F2 下载中断 | 中途断网、超时 | **未 `esp_ota_set_boot`** → 仍跑旧槽 | 「下载失败，可重试」 | 可重试；不强制 |
| F3 镜像损坏 | 长度/魔数/校验失败 | 同 F2，abort OTA handle | 「固件校验失败」 | 禁发该包；回滚 CDN |
| F4 写入失败 | flash 错误 | 同 F2 | 「写入失败，请重试」 | 硬件嫌疑工单 |
| F5 忙拒 | 出图中点升级 | 不开始 | 「出图结束后再试」 | — |
| F6 切槽后起不来 | 新固件 boot 崩 | **PENDING_VERIFY 未 mark valid → 看门狗/次回滚旧槽**（ESP-IDF 标准行为；须在发版前用故意坏包 HIL 验一次） | 自动回到旧版后可再连；小程序显示「已回滚」若能上报 | 坏包下架 |
| F7 升后连不上云 | 新固件配网/证书回归 | 旧槽已切且已 mark valid 则难自动回 | 需远程再推修复包或售后 | **发版门槛：升后必过联网冒烟** |
| F8 错板包 | board 不符 | 拒下 | 「固件与机型不符」 | 发版流水线板型闸 |

**硬规则**：

1. **未验证通过的新槽不得永久成为唯一启动项**（依赖 PENDING_VERIFY；发版 checklist 含「故意坏包应回滚」HIL）。  
2. **下载失败路径必须恢复语音/业务**（现 `UpgradeFirmware` 已 `audio_service_.Start()` —— 回归测试钉死）。  
3. 门户 **不得**在升级中再推画图/写字任务（busy 闸）。  
4. Grbl 日后 OTA **复用同一失败表**（F1–F5、F6 双槽）；本批只在文档预留 `device=grbl` manifest，不实现。

## 4. 与现有代码的衔接

| 层 | 动作 |
|----|------|
| xiaozhi | 优先包装：绑定态可调的 `hutuji.ota_check` / `hutuji.ota_start`（或收紧调用官方 `self.upgrade_firmware`：仅接受门户签发的 host）；status 增 `firmware_version` / `ota`；**出图态拒升** |
| hutuji portal | check/upgrade API；固件元数据表或静态 manifest；升级会话状态 |
| hutuji 静态/nginx | `/firmware/` 限速 + 仅 GET；可选签名查询参数 |
| weapp | 升级页；Grbl 按钮占位禁用 |
| 运维 | 发版脚本：板型矩阵构建 → 算 sha256 → 上传 → 改「当前推荐版本」 |

## 5. 写字体验（本批不做）

**已拍板：OTA 做完再做。** 本文件不交付写字 P0。后续另开 `write-ux` 设计：小程序字号/行距 + 发前页数；写字 send 自动 confirm（对齐画图）；不改竖版/转置/§5。

## 6. 验收（小派 OTA P0）

1. 绑定用户在小程序看到当前版本与新版本说明（自定义 `hutuji.x.y.z`）。  
2. idle 下一键升级：进度可见 → 自动重启 → 版本号变新 → 语音/绑机/出图冒烟通过。  
3. 故意断网下载：失败后设备仍可用，版本不变。  
4. 故意坏包（若可构造）：启动后回滚旧槽，设备可再连。  
5. 出图中点升级：明确拒绝，任务不被打断。  
6. 错 board 包：拒绝。  
7. Grbl：界面无升级入口或禁用。  
8. 每天自检：idle 跨日检查有更新时只提示、不自动下载；小程序仍可随时手动检查。  

**不得宣称**：Grbl 已支持远程升；未做坏包回滚 HIL 前宣称「升级绝对安全」。

## 7. 明确不做（本批）

- Grbl pull-OTA / WebUI 远程代传  
- 强制全量静默升级 / 自检后自动下载切槽  
- 手机本地选择 .bin 上传  
- 为 OTA 放宽 Telnet 多客户端或合并双芯片固件  
- 写字体验 P0（排期在 OTA 之后）  

## 8. 开放问题（已拍板）

| # | 问题 | 结论 |
|---|------|------|
| 1 | 版本号 | **自定义** `hutuji.<major>.<minor>.<patch>`；上游版号仅备注 |
| 2 | 自检 | **每天 idle 自检一次**（只查不升）；升前仍须用户二次确认；小程序可随时手动查 |
| 3 | 写字 P0 | **OTA 完成后再做** |

若以上无异议，回复「批准」后进入实现计划（`docs/superpowers/plans/`）。

**已批准（2026-09-09）** → 实现计划：`docs/superpowers/plans/2026-09-09-xiaopai-ota-mass-prod.md`

## 9. 后续镜像排期（2026-09-09 用户确认）

OTA 收口之后，小程序再镜像云端能力（设备侧能力保留，不迁移走）：

1. **写字 P0**：文本输入 + 字号/行距/页数预览 + 发送/收藏（复用 `hutuji_write` / portal 文档链）
2. **笔顺 / 字分解**：输入汉字 → 田字格教学预览 → 发送（复用 `hutuji_teach` / `stroke_order`）

在 OTA Task 7–8 完成前不插队实现以上两项。
