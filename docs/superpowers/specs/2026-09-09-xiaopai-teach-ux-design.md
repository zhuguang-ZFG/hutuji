# 小程序笔顺 / 字分解 P0 设计

> 状态：**已批准**（2026-09-09：§1 OK；用户指示「不要问了」——余节按方案 1 默认拍板并进入实现）  
> 前置：写字 P0 `docs/superpowers/specs/2026-09-09-xiaopai-write-ux-design.md`  
> 排期：OTA §9 → 写字 → **本批笔顺**  
> 范围：draw-portal + weapp；复用 `hutuji_mcp.stroke_order` / `layout_teach`；不改 §5/竖版/设备 `kArticleMaxPages=20`

## 0. 决策摘要

| 决策点 | 选择 |
|--------|------|
| 入口 | 创作页 **画图 \| 写字 \| 笔顺** 三段 |
| 输入 | **随便写**；服务端抽出汉字 |
| 上限 | 抽出后最多教 **20** 个汉字（与 `hutuji_teach` / 设备 20 页同源）；超出截断并提示 |
| 后端 | `POST /api/draw/teach` 扩 draw job（方案 1） |
| 预览 | 共 N 页可翻页；`missing_chars` / `truncated` 弱提示 |
| 底栏 | 笔顺模式藏拍照/相册/文档 |
| 发送 | 复用 send/save + 自动 confirm |

## 1. 产品流

见对话已 OK 的第 1 节。切模式清未发送 job；草稿分模式保留。主按钮「生成笔顺」。

## 2. API

**`POST /api/draw/teach`**（phone 鉴权）

| 字段 | 约束 |
|------|------|
| `text` | 必填；随便写；规范化后抽 CJK；无汉字 → 400「没有可教的汉字」 |
| （无字号行距） | 笔顺版式由 `layout_teach` 固定 |

流程：抽汉字 → 若 >20 则取前 20 并 `input_truncated=true` → `StrokeData.load` + `layout_teach(..., max_pages=20)` → `_materialize_laid_pages`（`kind=teach`, `mode=article`）→ `publish_doc_pages`。缺 `stroke_dir` → **503**。

响应对齐 write/upload 多页：`job_id`, `pages`, `page_count`, `truncated`（含页上限截断）, `missing_chars`, `chars_taught`（实际教的字串）, `input_truncated`（因 20 字帽截断原文抽出结果）。

## 3. 小程序

- 第三段「笔顺」；输入 maxlength 可高于 20（例如 200，反正服务端抽字截断）
- 翻页 / 收藏 / 发送同写字；`_writeToken` 类令牌防切模式竞态
- 脚注 bump **v0.2.14**

## 4. 验收 / 不做

验收：随便写句子 → 见田字格页；>20 汉字截断提示；缺字提示；发送自动 confirm；portal pytest 绿。  
不做：抬 `kArticleMaxPages`；笔画动画；改 MCP `hutuji_teach` 签名；纸面 HIL 宣称完成。

## 5. 成功标准

用户在小程序笔顺模式随便输入，得到标准笔顺田字格预览并可收藏/发到写字机。
