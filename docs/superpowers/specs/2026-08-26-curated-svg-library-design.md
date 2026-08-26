# 精选单线 SVG 库（替换 Quick Draw 同主题）设计

> 状态：设计已批准（2026-08-26 头脑风暴），待实现计划与落地。  
> 仓库：hutuji（MCP / 出图链）。  
> 关联：`docs/design/quickdraw-local-cache.md`（已实施的 QD 本地缓存）、`lineart-research-report.md`。

## 0. 决策摘要

| 决策点 | 选择 |
|---|---|
| 与 QD 关系 | **精选覆盖的类目完全替换 QD**；同主题不再出涂鸦风 |
| 内容来源 | **AI 出干净线稿 → 自动闸门入库**；人工每类抽检 2–3 张 |
| 上线节奏 | **大而全**：映射到的全部 QD 英文类齐备后再开替换 |
| 每类张数 | 对齐 QD 习惯：**目标 N=50**；覆盖就绪门槛 **N_min=30** |
| 质检 | **自动闸门硬拒为主**；人工只抽检，打回则补生成 |
| 架构 | **独立 `curated/` 库 + 覆盖率闸门**；不伪装成 QD ndjson；不按类渐进替换 |

非目标：OpenClipart phase-2、改固件、改 vpype profile、未达标时按类渐进替换、把未过闸图先入库。

## 1. 背景与动机

- Quick Draw 命中快、天然单线，但是 **20 秒涂鸦**，儿童主题观感简陋。
- AI 位图描摹路径可出「更像」的图，但抬笔多、空走多、耗时长且质量方差大。
- 需要第三层：**离线定稿的精选单线 SVG**——观感好于 QD，流畅性好于现网 AI 热路径，查找延迟接近 QD。

## 2. 目录与索引

### 2.1 生产布局

```
/opt/hutuji/curated/
  manifest.json
  tiger/
    000.svg
    001.svg
    …
  cat/
  …
```

- 类目目录名 = QD **英文**类名（与 `quickdraw_categories.json` 的 value 一致）。
- 中文 → 英文映射 **复用** `quickdraw_categories.json`，不另起表。
- 构建产物默认在开发机 `mcp-server/tmp/curated-data/`（不入 git）；部署同步到 `/opt/hutuji/curated/`。

### 2.2 manifest.json（契约要点）

至少包含：

- `gate_version`：闸门规则版本号（改阈值须 bump）
- `n_target`：每类目标张数（默认 50）
- `n_min`：覆盖就绪门槛（默认 30）
- `categories`：每类 `{ "count": int, "updated_at": … }`
- `ready`：布尔；当且仅当「映射到的全部唯一英文类（当前 **298**）均 `count >= n_min`」为 true
- 可选：拒绝原因直方图、抽检清单指针

类目集合以 `quickdraw_categories.json` 去重后的英文 value 为准（排除 `_meta`）；类目表扩容时须同步补齐 curated 并重算 `ready`。

### 2.3 配置

| 环境变量 | 含义 |
|---|---|
| `HUTUJI_CURATED_DIR` | curated 根目录；空 = 关闭 curated |
| `HUTUJI_CURATED_REPLACE_QD` | `0`（默认）/ `1`；仅当 `1` **且** `manifest.ready` 才激活替换 |

`replace=1` 但 `ready=false`：**不得**激活替换；打 error 一次并按 `replace=0` 行为运行（防半成品上线）。

## 3. 生成流水线与自动闸门

### 3.1 离线脚本

`mcp-server/scripts/curated_prep.py`（同步、可断点续跑、可按类并行、限 QPS）：

1. 遍历全部目标英文类。
2. 提示词模板：「单线简笔 {category}，白底黑线，无填充无阴影无文字」。
3. 走现有生图对冲 → 描摹 / 缝合 / 抽稀 → SVG（后续可换矢量线稿后端；**闸门契约不变**）。
4. 通过自动闸门才写入 `curated/<cat>/NNN.svg`。
5. 每类凑满 `n_target`；失败重试有上限；拒绝记入日志。
6. 更新 `manifest.json`。
7. 写出人工抽检清单：每类随机 2–3 张路径 → `tmp/curated-audit/`。

### 3.2 自动闸门（入库硬拒）

比运行时 `PEN_LIFT_WARN_THRESHOLD=50` 软警告更严：

| 闸 | 阈值 | 说明 |
|---|---|---|
| `_validate_svg` | 必须过 | 与现网白名单一致 |
| vpype → `validate_gcode` | 必须过 | 与现网 §5 活闸同口径 |
| 抬笔次数 | **不作硬拒**（2026-08-26 supersede：初值 ≤25→试过 ≤35；用户口径「抬笔多仍能画，纠结次数无意义」） | 运行时仍可有软警告；入库只拦画不出/会卡死 |
| G1 总行数 | 可配置上限 | 防碎线卡顿 |
| 有效墨迹 bbox | 占版面合理带 | 拒空白 / 极小点 |
| 过短段占比 | 可配置上限 | 拒毛刺刷 |
| SVG 体积 | ≤ 现网 SVG 上限 | 防异常膨胀 |

改闸门须 bump `gate_version`；未过新闸的样本须重检或剔除后补生成。

### 3.3 人工抽检

- 自动过闸即计入 `count`（抽检不阻塞覆盖率）。
- 打回：文件移出库、记 `human_reject`、该类 `count` 减 1，prep 补生成。

## 4. 运行时查找与 QD 切换

### 4.1 模块

新建 `hutuji_mcp/curated.py`（对称 `quickdraw.py`）：

- `lookup(description) -> BitmapResult | None`
- `is_replace_active() -> bool`（`REPLACE_QD=1` 且 `manifest.ready`）

类目匹配：**复用** `quickdraw._match_category`（或抽出共享函数），保证与 QD 同一套中文/别名语义。

### 4.2 `_run_draw_job` 顺序

```
1. 若 curated 启用且 replace 已激活：
     curated.lookup(desc)
       ├─ hit  → 下游 vpype / validate_gcode（不占付费限流）
       └─ miss → AI 对冲（绝不回 QD）
2. 否则（未启用 / 未激活替换）：
     行为与现网一致：quickdraw.lookup → miss → AI
     （建库期 curated.lookup 短路为 None，避免风格分裂）
```

| 场景 | 行为 |
|---|---|
| 建库中 / `REPLACE_QD=0` | 100% 现网（QD → AI） |
| 达标 + `REPLACE_QD=1` | 只 curated；空洞 → AI，不碰 QD |
| `CURATED_DIR` 空 | 与今天完全一致 |

命中路径与 QD/AI **共用**下游：`_validate_svg`（定稿入库已过，热路径可再防篡改）、vpype、`validate_gcode`、同一 JSON 产物面。

### 4.3 日志（脱敏，不落完整 description）

- `curated hit category=<cat> idx=<n>`
- `curated replace active` / `curated replace blocked: coverage=<k>/298`
- `curated miss under replace category=<cat> → AI`（warning）

### 4.4 回滚

1. `HUTUJI_CURATED_REPLACE_QD=0` 或清空 `HUTUJI_CURATED_DIR` → 立刻回 QD。
2. 单类损坏：删除该类目录或文件并补跑；整库异常则撤目录。

## 5. 打包、部署与里程碑

### 5.1 产物

| 产物 | 位置 | 入 git |
|---|---|---|
| `curated.py`、prep、闸门、测试、本设计 | 仓内 | 是 |
| SVG 库 + manifest | `tmp/curated-data/` → `/opt/hutuji/curated/` | 否 |
| 抽检清单 | `tmp/curated-audit/` | 否 |

体积粗估：298 × 50 × 数 KB ≈ 数十～百余 MB；`rsync`/`tar` 同步 lima（主）与 jdcloud（备）。

### 5.2 部署顺序

1. 部署代码；默认 `REPLACE_QD=0`。
2. 同步 `/opt/hutuji/curated/`，校验 `manifest.ready`。
3. 抽样：replace 关时 journal 仍应为 Quick Draw（或 AI），证明未误切。
4. 覆盖率 298/298 且抽检无大面积打回。
5. 双机设 `HUTUJI_CURATED_REPLACE_QD=1` → restart。
6. 探针：「画一只老虎」须 `curated hit`，**不得** `Quick Draw 命中`。
7. 回滚：关 replace 或撤目录（秒级）。

### 5.3 里程碑

| 阶段 | 完成标准 |
|---|---|
| M1 工具链 | prep + 硬闸门 + 单类 50 张；单元测试绿 |
| M2 运行时（暗） | curated 接线，replace 默认关；未达标 = 现网 |
| M3 全量填充 | 298 类均 ≥ `n_min`；`ready=true`；抽检清单交付 |
| M4 切换上线 | replace=1；主题 HIL 纸面验收；许可笔记归档 |
| M5（可选） | 归档 QD 包（非必须，可留作回滚油） |

### 5.4 风险

| 风险 | 缓解 |
|---|---|
| 生成成本/耗时长 | 断点续跑、按类并行、限 QPS |
| AI 线稿不稳 | 硬闸拦碎线卡顿/无墨迹/§5 非法；**抬笔次数不作入库硬拒**（2026-08-26 用户口径 supersede）；不足补跑 |
| 半成品误开 replace | `ready=false` 强制不激活 |
| 许可 | 生图 ToS + 自有编排入库；替换后运行时不再出 QD，文档明确 curated ≠ Google QD |

## 6. 测试要点

- 未达标 + `REPLACE_QD=1` → 仍走 QD（替换被 block）。
- 达标 + replace + 有 `tiger` → 调用栈永不进 `quickdraw.lookup`。
- 达标 + replace + 故意删 `tiger` → AI，且不碰 QD。
- 闸门：抬笔 26 的样本不得入库；空白 / 非法 SVG 不得入库。
- manifest：缺一类则 `ready=false`。
- 回归：现有 `test_quickdraw.py` 在 `CURATED_DIR` 空或 replace 关时行为不变。

## 7. 许可与归因

- Quick Draw：CC BY 4.0；**替换激活后同主题不再分发 QD 笔画**，署名义务随出图路径消失；QD 包若仍驻留磁盘仅作回滚油，须在部署文档标明。
- Curated：由本产品流水线生成的定稿资产；遵守所用生图后端 ToS；不声称来自 Google Quick, Draw!。

## 8. 明确不做

- 不把精选笔画伪装成 QD ndjson 覆盖官方缓存。
- 不在覆盖未满时按类渐进替换（避免风格分裂）。
- 本设计不改 S3 固件、不改 vpype hutuji profile。
- 不上 OpenClipart / 已否决的 SVG 源（见 QD 设计稿 §13）。
