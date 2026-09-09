# 小程序写字体验 P0 设计

> 状态：**已批准**（2026-09-09 用户回复「批准」）→ 实现计划见 `docs/superpowers/plans/2026-09-09-xiaopai-write-ux.md`  
> 前置：小派 OTA 设计/计划已收口 → `docs/superpowers/specs/2026-09-09-xiaopai-ota-mass-prod-design.md` §9  
> 范围：draw-portal + weapp；复用 `hutuji_mcp.write_text` / 文档链；**不改**竖版/转置/§5/换纸  
> 用户拍板（2026-09-09）：创作页「画图|写字」；字号+行距细调；多页可翻预览；写字模式藏拍照/相册留文档；实现走扩展 draw job（方案 1）

## 0. 决策摘要

| 决策点 | 选择 |
|--------|------|
| 入口 | 创作页 **画图 \| 写字** 切换（不新开 tab） |
| 版式 | **字号 mm + 行距倍率** 步进；默认字库；P0 **不选字库、不调字距** |
| 预览 | **共 N 页 + 可翻页** 看每页 PNG |
| 写字底栏 | **隐藏拍照/相册**，保留「文档」；文档文章模式吃同一套字号/行距 |
| 后端 | **扩展现有 draw job**：`POST /api/draw/write` + upload 可选版式参数；send/save/历史复用 |
| 发送 | 与画图同：portal `send` → `hutuji.draw` + **自动 confirm** |
| 非目标 | 笔顺/字分解；独立 `/api/write/*`；改 MCP `hutuji_write` 签名；改 §5/竖版/转置 |

## 1. 背景与约束

- 云端已有 `hutuji_write`（5000 字、`WriteLayout` 默认 size=12 / line_spacing=1.6、max 20 页）。
- 门户已有文档上传 → `process_upload_doc` → `pages.json` → 整篇 `send`；小程序创作页尚无「手输写字 + 调版式」面。
- OTA §9 排期：写字 P0 → 笔顺；本批只交付写字 P0。
- 无限位行程与限幅仍以 `protocol.md` §5 为准；排版页区与现网写字链一致（不在本批改坐标/转置）。

## 2. 产品流

### 2.1 画图模式

保持现状：一句话生成 / 拍照 / 相册 / 文档 → 预览 → 收藏 / 发送。

### 2.2 写字模式

1. 顶栏切到「写字」：文案改为「想写点什么」；隐藏拍照/相册；保留「文档」。
2. 大文本框（去控制字符后 ≤ 5000）+ 字号（2–40 mm，默认 12，步进 1）+ 行距（1.0–3.0，默认 1.6，步进 0.1）。
3. 「排版预览」→ 服务端排版 → 「共 N 页」+ 当前页预览；左右翻页或 swiper。
4. 改正文或版式后须再点「排版预览」（不自动重排）。
5. 「发送到写字机」/「收藏」复用现有 job API；多页走 `pages.json` 整篇推送 + 自动 confirm。
6. 「文档」：article（txt/docx/pptx）使用当前字号/行距；table（xlsx/csv）**忽略**版式参数，沿用表格默认 `WriteLayout`。

切模式时：清空当前未发送 job 预览；画图描述与写字正文各自保留草稿。

## 3. API / 门户

### 3.1 `POST /api/draw/write`（手机号鉴权）

| 字段 | 类型 | 约束 |
|------|------|------|
| `text` | string | 必填；规范化后 ≤ 5000 |
| `size_mm` | number | 可选，默认 12；∈ [2, 40] |
| `line_spacing` | number | 可选，默认 1.6；∈ [1.0, 3.0] |

- 默认字库 + `write_text.layout_text`；`max_pages = ARTICLE_MAX_PAGES`（20）；超出截断并 `truncated: true`。
- Job 元数据区分写字（如 `kind: "write"` 或等价 source 标记），便于以后历史展示；P0 历史 UI 可不改文案。
- 产物布局与文档链同构：`pages/page_NNN.{svg,gcode,png}` + `pages.json` + 首页兼容 `draw.*` / `preview.png`。

**响应（对齐现有多页 upload）**：`job_id`、`page_count`、`truncated`、`preview_url`、`pages[]`（每页 `preview_url`）、汇总 `lines`/`bytes`。

### 3.2 文档上传扩展

`POST /api/draw/upload` 对 **article** 增加可选 form 字段 `size_mm`、`line_spacing`（缺省 = 今日 `WriteLayout()` 默认）。**table** 模式忽略这两项。

### 3.3 复用

`GET /api/draw/jobs/{id}`（写字结果以 write 响应的 `pages` 为主；若 job 状态接口尚无多页列表，write/upload 响应已够小程序预览）、`POST .../send`、`POST .../save`、历史列表。send 仍自动 confirm。

### 3.4 错误

空文、超长、字号/行距越界 → 4xx + 中文 detail；不回显内部路径。缺 gfont 目录（未配置字库）→ **503**（与现网文档链一致）。

## 4. 小程序

- 分段控件「画图 | 写字」；写字主按钮文案「排版预览」。
- 预览：「第 i / N 页」+ 翻页；`truncated` 时弱提示超过 20 页上限。
- 文档按钮上传时附带当前版式参数。
- weapp 版本脚注在实现时 bump。
- 历史：同一列表；P0 不强制改卡片文案。

## 5. 验收

1. 短文排版可见 ≥1 页；加大字号再排，页数或版面有可见变化。  
2. 多页文翻页与「共 N 页」一致。  
3. 发送走多页链（或单页）+ 自动 confirm（与画图同口径）。  
4. 收藏进历史并可再发。  
5. txt/docx 自定义字号成功；xlsx 忽略字号仍出表。  
6. 越界/空文/超长 → 明确中文错误。  
7. portal 相关 pytest 绿。  

**禁止宣称**：可出货；纸面笔迹已验（除非另做 HIL）。

## 6. 明确不做

- 笔顺 / 字分解（下一批，复用 `hutuji_teach`）  
- 选字库、字距、标题单独控件  
- 改竖版 / 转置 / §5 / 换纸机械  
- 独立 `/api/write/*`；改 MCP `hutuji_write` 工具签名  
- OTA 真机 HIL（仍挂账，不挡本批开发）

## 7. 成功标准

用户在小程序能把一段话（或文章类文档）调好字号与行距、翻页看完、收藏或发到写字机；排版与现有文档/语音写字同源。

## 8. 开放问题

无（对话已拍板）。批准本文件后 → `docs/superpowers/plans/2026-09-09-xiaopai-write-ux.md` 实现计划。

---

请审阅本文件；无异议请回复 **「批准」**。

**已批准（2026-09-09）** → 实现计划：`docs/superpowers/plans/2026-09-09-xiaopai-write-ux.md`
