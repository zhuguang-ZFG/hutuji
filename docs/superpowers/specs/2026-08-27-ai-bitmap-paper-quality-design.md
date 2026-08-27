# AI 位图链「纸面可画」质量闸设计

> 状态：规格已批准（2026-08-27；用户确认「规格 OK」；开放项：立刻 enforce、整轮新生图、五类纸面目视由用户签字）。  
> 日期：2026-08-27  
> 仓库：hutuji（MCP / 出图链）  
> 用户主诉：**预览还行，纸上废**（虚线、乱麻、涂鸦核、看不出主题）；「整条都要」，当前几乎画不成。

## 0. 决策摘要

| 决策点 | 选择 |
|---|---|
| 主修面 | **描摹后 / 落盘前的纸面风险闸** + 拒收重试；不以预览 PNG 当合格证 |
| 重试策略 | **宁可多等一轮也不交废图**：同题最多 **3 次尝试**（1 初试 + 2 重试），耗尽后诚实失败话术 |
| 与 curated 入库闸 | **分立**：入库闸（gate v3）拦「画不出/会卡死」；本闸拦「能过 §5 但纸面仍废」 |
| 与 QD/curated 运行时 | **并行减伤**：curated `ready` 前不 flip；本闸只约束 **AI 位图热路径** |
| 提示词 | **非主修**：仅在拒收样本上做受控 A/B；证据不足不改 `_BITMAP_STYLE` 默认 |
| 设备/固件 | **本批不改** xiaozhi / Grbl（空坐闸等另案）；本批只动 hub MCP 描摹与出图事务 |

非目标：换生图供应商全家桶、改 §5 行程/F、刷机 HIL、把抬笔次数重新塞回 curated 入库硬拒、未达标时宣称可出货。

## 1. 问题与根因分层

### 1.1 现象

- 屏上预览：折线被渲染器「看起来连」→ 人眼可接受。  
- 纸面：每段独立落笔；缝合失败/毛发纹理骨架化 → **虚线 + 涂鸦核**；主题不可辨。

### 1.2 已落地但仍不够

已有（`bitmap_svg.py` / 生产部署记录，见 `docs/deployment.md`）：实心块→外环、形态学闭合、端点缝合、缝合后抽稀、多后端对冲。

仍见风险证据：

- 抽稀过狠日志：`1133→6` 点——预览仍「有墨」，纸面主题塌缩。  
- 抬笔软警告存在（`PEN_LIFT_WARN_THRESHOLD`）但 **仍放行下发**。  
- curated 入库闸 v3 **故意**不硬拒抬笔；不能指望它挡住热路径纸面废图。

### 1.3 根因分层（实施优先级）

1. **交付闸缺失**：§5 / vpype 通过 ≠ 纸面可认。  
2. **描摹残留**：平行排线簇、局部高密度乱核、缝合后仍碎。  
3. **抽稀失控**：无「最少点数 / 最大压缩比」地板。  
4. **生图纹理**（辅）：毛发排线偶发；提示词杠杆弱，用拒收重试消化方差。

## 2. 架构：纸面风险闸（PaperRiskGate）

### 2.1 挂点

仅 **AI 位图路径**（`generate_bitmap` → SVG → `svg_to_gcode` 之后、写入可下载产物并返回给设备之前）：

```
generate_bitmap (可带 attempt/seed 破缓存)
  → svg_to_gcode + validate_gcode（§5 不变）
  → paper_risk.evaluate(gcode, polys_meta?)  → Pass | Reject(reason)
  → Pass: 落盘返回
  → Reject 且 attempt < 3: 换 attempt 密钥重跑（生图+描摹）
  → Reject 且 attempt == 3: 清理半成品，返回明确失败（不把废 G-code URL 交给设备）
```

QD 命中与 curated 命中：**不经本闸重试环**（库内已有各自质检；本批不改其契约）。若未来 curated 抽检发现纸面废图，另开入库闸增强，不与本设计混写。

### 2.2 指标（初值；须用 5～10 张已知废图/好图标定后冻结）

| 指标 | 含义 | 初值方向（标定前标 `TBD`，合入前必须钉死数字） |
|---|---|---|
| `short_g1_ratio` | 相邻 G1 XY 段长 &lt; 0.5mm 占比 | 复用 curated `MAX_SHORT_G1_RATIO=0.35` 作起步 |
| `pen_lifts` | 抬笔次数 | **热路径可硬拒**（与入库闸分立）；初值候选：相对墨迹复杂度的分位或绝对上限，标定后写入常量 |
| `travel_gap_p50_mm` | 抬笔空走距离中位 | 虚线代理；过大 → 拒 |
| `scribble_core` | 小包围盒内顶点/短段密度异常高 | 涂鸦核；用描摹折线 bbox 密度或 G1 局部窗 |
| `decimate_ratio` / `points_after` | 抽稀压缩比与抽稀后总点数 | 例如压缩比 &gt; 0.95 且结果点数 &lt; 地板 → 拒或在描摹侧强制抬 tol 下限重算 |
| `ink_span_mm` | 墨迹跨度 | 与 curated `MIN_INK_SPAN_MM=40` 对齐，防「空图」 |

原则：

- **预览 PNG 不参与 Pass/Reject。**  
- 抬笔：用户已否决「入库为抬笔纠结」；本闸抬笔只服务 **热路径不交废纸**，不回写 curated gate v3。  
- 所有阈值进命名常量 + 单测钉死；改阈值 bump `PAPER_RISK_GATE_VERSION`。

### 2.3 拒收重试

| 项 | 约定 |
|---|---|
| 最大尝试 | 3（`attempt=0..2`） |
| 破缓存 | `description` 缓存键必须纳入 `attempt`（或等价 slot），避免命中同一废图 |
| 对冲后端 | 沿用现有多后端竞速；重试是 **新一轮完整 generate_bitmap**，不是只重描摹（纹理源在位图） |
| 可选二段 | 若同图描摹参数可调（闭合核/缝合容差/抽稀 tol）：允许 **同 PNG 二次描摹** 算半次尝试（实现计划里二选一钉死，避免双倍计费歧义）。**默认推荐**：每 attempt = 新生图，逻辑简单、对准纹理方差 |
| 耗尽 | 工具返回明确错误（中文可读：「这张线稿太碎，纸上会画糊，请换个说法再试」）；**不得**返回废 G-code URL |
| 计费/限流 | 重试占用同一 draw 事务 / 同一限流名额（与现有 single-flight 一致）；日志记 `paper_risk reject reason=… attempt=n` |

### 2.4 描摹侧硬修复（与闸门同批）

1. **抽稀地板**：禁止无下限压缩；`points_after` 下限 + `decimate_ratio` 上限；触顶则减小 tol 或跳过该层抽稀并打日志（优先保主题，接受略密 G1）。  
2. **排线簇抑制**（描摹域）：检测近平行短段簇并丢弃或合并为单中心线（实现细节计划阶段选型；须有合成夹具单测）。  
3. **涂鸦核**：实心块环已有；补「高密度碎段局部」抑制或整区降权。  
4. **缝合**：保持现有端点缝合；若闸门 `travel_gap` 仍高，允许同批微调容差并回归小狗等历史样本（禁止无证据猛抬导致五官粘连）。

## 3. 并行减伤（方案 C，不阻塞本闸）

- 继续阿里云 `hutuji-curated-prep` 灌库；`manifest.ready` + 抽检前 **禁止** `REPLACE_QD=1`。  
- 本闸上线后，AI 热路径废图率应下降；curated flip 仍按原设计另验。  
- 文档：`deployment.md` / `release-readiness.md` 登记「纸面风险闸」启用与版本，禁止写成出货完成。

## 4. 提示词（方案 B，辅）

- 默认 `_BITMAP_STYLE` **本批不改**。  
- 仅当 journal 中某 `reason` 连续占拒收大头时，对应该 reason 开 n≥5/臂 A/B；显著才改默认。  
- 禁止用「加一句无排线」代替本闸。

## 5. 验收

### 5.1 自动化（合入门禁）

- 纸面风险闸：Pass/Reject 契约单测（合成 G-code / 折线夹具）。  
- 抽稀地板：压缩比触顶行为单测。  
- `_run_draw_job`：模拟连续 Reject→第三次失败不落 URL；模拟第二次 Pass 只留最终产物。  
- 既有 bitmap / curated / §5 回归全绿；hub `docs`/`hub` 门禁。

### 5.2 人工纸面（第一阶段「能画成」）

同题固定 5 类（建议：猫、狗、树、房子、鱼），各至少 1 张实纸：

- 能认出主题；  
- 无明显涂鸦核；  
- 虚线不再是主观主病。  

预览好看 **不能**单独算过。未做纸面则 readiness 只写「闸已部署，纸面未验」。

### 5.3 生产观测

- journal：`paper_risk pass|reject reason=… attempt=` 计数。  
- 拒收耗尽率、平均 attempt、各 reason 直方图。  
- 不把「拒收率下降」写成画质已过——须纸面目视。

## 6. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 误杀好图 | 阈值用废/好样本标定；先 shadow 日志一周可选——**若用户要尽快止血，可直接 enforce**（本设计默认 **enforce**，因「几乎画不成」） |
| 延迟/费用上升 | 上限 3 次；single-flight 不变；日志可观测 |
| 抽稀地板导致卡顿 | 与短段比闸联动；密但可画优于疏到没主题 |
| 与 curated 语义混淆 | 文档与常量前缀 `PAPER_RISK_*` / `GATE_VERSION` 分立 |

回滚：环境变量 `HUTUJI_PAPER_RISK_GATE=0` 关闭 enforce（仍可打 shadow 日志）；或回退模块文件 + restart。

## 7. 文件触点（实现时白名单意图）

- `mcp-server/hutuji_mcp/paper_risk.py`（新）或等价模块  
- `mcp-server/hutuji_mcp/bitmap_svg.py`（抽稀地板、排线/乱核）  
- `mcp-server/hutuji_mcp/server.py`（`_run_draw_job` 重试环）  
- `mcp-server/hutuji_mcp/config.py` + `deploy/env.mcp.example`  
- 测试：`tests/test_paper_risk.py` 等  
- 文档：`docs/deployment.md`、`docs/release-readiness.md`、`docs/worktree-inventory.md`

不改：Grbl 换纸机械、protocol §5 限幅字面量（除非闸门槛与限幅冲突并经评审）、xiaozhi 固件（本批）。

## 8. 开放项（审阅时请拍板）

1. **Enforce 立即开** vs 先 shadow 24～48h 再 enforce？ → 设计默认 **立即 enforce**（止血优先）。  
2. 每 attempt 是否允许「同 PNG 只重描摹」？ → 设计默认 **否，整轮新生图**。  
3. 五类纸面验收是否由你执行？ → 闸合入后需要你的目视签字。

---

## 规格自审

- 无 TBD 占位冒充已定阈值：数字阈值标为「标定后钉死」，合入前必须有常量。  
- 与 curated gate v3 抬笔政策不矛盾（热路径 vs 入库）。  
- 范围未膨胀到固件/出货宣称。  
- 成功标准与用户主诉一致：纸面可认，而非预览好看。
