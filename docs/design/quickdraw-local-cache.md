# Quick Draw 本地缓存落地设计（v1.0，已实施）

> 状态：✅ 已实施（2026-08-24 用户批准后同日落地）：`quickdraw.py` + 类目映射 v3
> （387 键 → 298 官方在册类目，同日应用户「更多主题」扩类）+ `quickdraw_prep.py` + 34 项测试全绿 + hub 门禁 PASS
> （976/976 + e2e）；全量数据 298 类 × 200 条 ≈ 20MiB 已备于 `mcp-server/tmp/quickdraw-data/`。
> v3 已于同日部署阿里云生产（373 键 / 298 类）；本轮新增 14 个增强别名至 387 键，尚未部署生产。真机 HIL 见 §11 里程碑 3。
> 依据是仓根 `lineart-research-report.md`（2026-08-24 调研）的首推路线。
> 非目标：OpenClipart（phase-2 可选，仅当 CC BY 署名成问题）、SVG-Repo /
> PublicDomainVectors / PlotterFiles（已否决，见 §13）、汉字笔顺库（另行立项）。

## 1. 背景与决策依据

- 现行出图链 100% 依赖 AI 生图对冲（商汤/Agnes/硅流 schnell/硅流可图/imagefree，
  见 `bitmap_svg._race_image_backends`），单张端到端 7.5–11.4s，且 AI 输出存在
  手抖/畸形/乱码等质量方差。
- 调研结论：Quick Draw 数据集（345 类简笔画，笔画序列 `[x,y,t]`，256×256 归一化，
  已按 1px 简化）**天然单线、无需骨架化描摹**，本地缓存命中 <200ms，估计覆盖
  60–70% 儿童高频语音请求；miss 时回落现有 AI 对冲，体验无损。
- 收益：命中场景端到端 7.5–11.4s → 0.2–0.5s（提速 15–57×），质量确定性反而更高；
  同时减少付费后端调用量（当前对冲策略下同题双倍调用费）。
- 许可：Quick, Draw! dataset by Google, Inc.，**CC BY 4.0（需署名，非 CC0）**，
  署名落点见 §7。

## 2. 总体数据流

```
语音 description → _sanitize_description（既有）
  → _draw_inflight 同题 single-flight（既有，不变）
  → _run_draw_job：
      _cleanup_expired_outputs（既有）
      quickdraw.lookup(safe_description)          ← 新增，纯本地读，<50ms
        ├─ 命中 → 由笔画数组现拼 SVG 字符串 ─┐
        └─ 未命中/禁用/数据损坏 → 走既有 AI 路径：_draw_limiter.slot()
              → generate_bitmap（5 路对冲 → PNG → 描摹 → SVG）
                                              ↓（两路汇合，下游完全一致）
        _svg_to_gcode_cached（vpype read → layout 10mm/190×190 → linemerge 0.3
          → linesort → linesimplify 0.05 → gwrite hutuji）
        → validate_gcode（§5 活闸门）
        → 写 draw_<时间戳>_<token>.{svg,gcode} → 返回同一 JSON 形态
```

- 命中路径**不占用** `_draw_limiter` 名额：该名额的语义是「付费 LLM/生图调用的
  并发闸」，本地查找零付费调用，占名额只会无谓串行化命中流量。
- 命中路径**不产 PNG**：产物只有 svg+gcode（/files 契约本来只认 `.svg/.gcode` 名）；
  `preview_url` 与 AI 路径一致指向 svg。返回 JSON 字段不变
  （`{url, preview_url, lines, bytes, pen_lifts, ttl_seconds}`）。

## 3. 数据准备链（离线，不进运行时热路径）

新脚本 `mcp-server/scripts/quickdraw_prep.py`（同步、可重复执行）：

1. 按类目清单逐个下载官方简化版 ndjson：
   `https://storage.googleapis.com/quickdraw_dataset/full/simplified/<category>.ndjson`。
   **可达性风险**：该域名的国内直连未实测；准备工作在开发机/经代理完成，生产只消费
   准备好的本地目录，运行时无任何外网依赖。备选来源：GitHub 镜像仓
   （hardmaru/quickdraw-ndjson-to-npz 产物）或 npz 反转 strokes。实测量与失败回退
   记入实施 PR 描述。
2. 过滤 `recognized=true`（官方识别标记），每类**固定随机种子**采样 N 条
   （默认 N=200；存储估算：90 类 × 200 条 × ~1KB ≈ 18MB）。采样数与类目清单是
   显式决策参数，改参即改产出，可复现。
3. 丢掉 `t` 通道（写字机不需要时间维），每类产出一个 JSONL：
   `<category>.jsonl`，每行 `{"strokes": [[[x…],[y…]], …]}`。
4. 输出目录默认 `./quickdraw-data/`（**不入 git**，加 .gitignore 与既有
   「不新增忽略规则」决策的冲突须在实施时裁决：该决策原话是「不新增忽略规则，
   避免同类误生成被静默掩盖」——数据目录是**有意的构建产物**而非误生成，
   建议改为 prep 脚本默认输出到 `tmp/quickdraw-data/`（tmp/ 已被忽略），
   生产部署到 `/opt/hutuji/quickdraw/`，绕开新增忽略规则。）

类目种子集：先 80–100 个高频儿童主题（动物/水果/交通/植物/家居/人物/天气），
与 §6 映射表同源维护，避免「有数据无映射」或「有映射无数据」。

## 4. 运行时模块 `hutuji_mcp/quickdraw.py`

契约（供 `_run_draw_job` 调用的唯一入口）：

```python
def lookup(description: str) -> str | None:
    """本地缓存查找。命中返回可过 _validate_svg 的 SVG 字符串；未命中/禁用/异常返回 None。"""
```

- **类目匹配**：`_sanitize_description` 的输出 → 映射表（§6）精确命中 → 画动词前缀
  剥离（含名词语素 `_NO_STRIP` 拦截）→ **最长优先子串兜底（≥2 字键）**；不调 LLM
  （热路径零网络零付费）。miss 返回 None。子串兜底是 2026-08-24 生产实证后的修订：
  role-prompt 让云端把 description 写成「单线简笔侧脸小猫」类增强短语，纯精确匹配
  首日零命中；子串只收 ≥2 字键防「天安门→门」类误配，画字名词（画家/画板）由
  `_NO_STRIP` 拦截。strip 词干非精确键不得短路（「画一只可爱的小猫」须落到子串）。
- **取图**：类目内用 `random.SystemRandom` 均匀随机取一条（同一 description 两次
  命中可能出不同图——与 AI 路径的方差语义一致，可接受；并发同题仍被
  `_draw_inflight` 合并）。
- **SVG 现拼**（~30 行，零新依赖）：
  ```xml
  <svg xmlns="http://www.w3.org/2000/svg" width="190mm" height="190mm"
       viewBox="0 0 256 256" version="1.1">
    <polyline points="x1,y1 x2,y2 …" fill="none" stroke="black"
              stroke-width="1" stroke-linecap="round" stroke-linejoin="round"/>
    …（每条 stroke 一个 polyline）
  </svg>
  ```
  元素与属性全部落在 `_ALLOWED_TAGS`/`_ALLOWED_ATTRS` 白名单内（polyline 的
  `points`、fill="none" 均是既有放行项）；产出字符串仍**强制过一遍**
  `_validate_svg`——纵深防御：即使数据文件被篡改，注入面也止于既有白名单。
  256 坐标系由 vpype `layout --fit-to-margins 10mm 190x190mm` 等比归一化，
  `vertical_flip=true` 与现有路径同口径，无需额外变换。
- **配置**：`HUTUJI_QUICKDRAW_DIR`，空值（默认）= 禁用并回落 AI；非空时在
  `config.py` 按既有模式 strip 后 `Path(...).resolve()`；import 期不探测目录
  存在性（生产目录由部署保证，启动期不新增 fail-closed 面），首次 lookup 时
  惰性确认，缺失/不可读按 miss 处理并 warning 一次（防日志刷屏：同类错误
  进程内只记一次）。
- **日志**：命中记 `quickdraw hit category=<cat> idx=<n>`；异常只记类型与类目，
  不落 description（与现有脱敏口径一致）。

## 5. 主链改动点（server.py，预计 <40 行）

`_run_draw_job` 在 `_cleanup_expired_outputs` 之后、`_draw_limiter.slot()` 之前插入：

```python
svg = quickdraw.lookup(safe_description) if _quickdraw_enabled() else None
if svg is None:
    async with _draw_limiter.slot():
        bitmap = await generate_bitmap(safe_description)  # 既有
        svg = bitmap.svg
        …（PNG 写出等既有逻辑）
```

- 单飞/取消/超时/产物命名/TTL/返回 JSON：全部沿用既有代码路径，无分叉。
- 超时钟：命中路径不重置既有 deadline（命中本身 <50ms，无意义也不掩盖 AI 慢路径）。

## 6. 类目映射表

- 文件：`mcp-server/hutuji_mcp/quickdraw_categories.json`（入 git，可独立 PR 迭代）。
  形态：`{"猫": "cat", "小猫咪": "cat", "画只猫": "cat", …}`——key 为
  `_sanitize_description` 之后的规范化串，value 为 Quick Draw 类目英文名。
- 首版 80–100 条人工对照（报告估算 2–4h 工时），来源 = 真实语音请求日志高频词
  + 儿童常见主题清单；后续按 miss 日志迭代（命中/未命中计数进日志，便于发现
  该补的词）。
- **不做** LLM 在线分类：热路径引入一次付费调用就把缓存的延迟收益吃光；
  低频长尾交给 AI 对冲本来就合理。

## 7. 合规（CC BY 4.0 署名）

- 仓内：`mcp-server/hutuji_mcp/quickdraw_categories.json` 旁放
  `QUICKDRAW-ATTRIBUTION.md`：数据集名、作者（Google, Inc.）、许可 CC BY 4.0、
  官方仓库 URL、本地做了「过滤 recognized、采样、去 t 通道」的说明。
- 产品侧：说明书/关于页注明「简笔画图库基于 Quick, Draw! dataset by Google, Inc.，
  CC BY 4.0」。产品对外分发 SVG/G-code 产物时同页注明。
- 部署目录 `/opt/hutuji/quickdraw/` 内置同款 ATTRIBUTION 文件。

## 8. 安全与契约复用（不新增 G-code 面）

- 命中产物与 AI 产物走**同一条** `_svg_to_gcode_cached` + `validate_gcode`：
  §5 全部约束（限幅 X≤190/Y≤190、Z∈{0,5}、0<F≤10000、禁 G92/M 码/落点 X0Y0、
  ≤512KiB、≤254B/行）原样生效，本方案**零新增** G-code 攻击面。
- `_validate_svg` 对缓存 SVG 强制执行（§4），数据文件即使被写坏也翻不过白名单。
- prep 脚本只读官方数据源 + 写本地目录，不碰运行时状态；同步风格，与
  `e2e_local.py` 同约定。

## 9. 失败语义：一律 fail-open 到 AI

禁用、目录缺失、类目文件损坏、JSONL 解析失败 → warning + 返回 None → 既有 AI 路径。
这是可用性特性而非安全闸门：缓存坏了**不能**把「画不了画」变成故障；
AI 对冲是既定的完整兜底。反向（AI 全挂而缓存可用）时缓存仍是独立存活面，
天然提升整体可用性。

## 10. 测试计划（pytest，沿用函数式 + monkeypatch 模块全局约定）

1. 映射：精确命中 / 同义词命中 / 规范化后命中 / 未收录返回 None / 空串。
2. strokes→SVG：产出过 `_validate_svg`；polyline 数 == stroke 数；fill 全为 none；
   坐标原样透传（抽查一条已知笔画）。
3. 命中集成：`tmp_path` 造两类各 3 条数据的假目录 → 调 `_run_draw_job`（monkeypatch
   settings/output 目录）→ 断言：`generate_bitmap` **未被调用**（spy）、
   `_draw_limiter` 名额未被占用（并发计数不变）、产物 svg/gcode 落盘且
   `validate_gcode` 通过、返回 JSON 字段齐全。
4. miss 集成：未收录主题 → 断言落入既有 bitmap 路径（spy 被调一次）。
5. 禁用：`HUTUJI_QUICKDRAW_DIR` 空 → lookup 返回 None 且不触盘。
6. 损坏容忍：类目 JSONL 写一行坏 JSON → 该类目按 miss 处理且进程内 warning 只记一次。
7. 并发：同一 description 并发 5 个 waiter → 只拼一次 SVG（single-flight 复用既有断言模式）。

## 11. 部署、回滚与里程碑

- 部署：代码按既有纪律 `git cat-file blob` 导出 + scp；数据目录 scp 到
  `/opt/hutuji/quickdraw/`；`/etc/hutuji/mcp.env` 追加 `HUTUJI_QUICKDRAW_DIR`；
  重启 `hutuji-mcp`（cloud-bridge 共用代码也重启）；preflight 全绿后放量。
  preflight 是否加「缓存目录可读」检查项，实施时另评（不阻塞 MVP）。
- 回滚：置空 `HUTUJI_QUICKDRAW_DIR` 重启即回到纯 AI 链，秒级、零数据迁移。
- 里程碑：
  1. prep 脚本 + 类目映射首版 + 数据样本（开发机验证，打印各类目条数/体积）；
  2. `quickdraw.py` + server 插入点 + §10 全部测试绿 + hub 门禁 PASS；
  3. 真机一轮：语音「画一只猫」命中场景秒出图，与 AI 路径出图质量人工对比；
  4. 生产部署 + miss 日志回收一周，迭代映射表。

## 12. 验收标准（MVP）

- 命中场景端到端（MCP 调用到返回 URL）≤1s（本地实测，不含语音链路）。
- 命中产物与 AI 产物经同一 `validate_gcode`，hub 门禁 PASS（含全部既有 940 项测试）。
- 未命中场景行为与今日完全一致（spy 断言 + 既有回归不翻红）。
- 实机出图人工确认：Quick Draw 风格（儿童涂鸦）可接受——**此项须用户现场签字**，
  风格是否符合「小派」产品调性不在工程判据内。

## 13. 已否决项记录（防重开）

| 方案 | 否决理由 |
|---|---|
| SVG-Repo / PublicDomainVectors / PlotterFiles 批量导入 | 许可逐文件审计（天级）+ 通用矢量需人工筛选与骨架化（3–5 天），边际覆盖收益不成立（2026-08-24 用户确认） |
| 命中路径绕开 vpype 直写 G-code | 跳过 §5 活闸门=新开攻击面；vpype 段本身 <1s，非瓶颈 |
| 热路径 LLM 在线分类 | 一次付费调用吃掉缓存全部延迟收益；长尾归 AI 对冲 |
| 汉字笔顺库（写字场景） | 真实需求（写字机写名字/拼音），但与简笔画缓存是两套数据源与匹配逻辑，另行立项，不混进本 MVP |
| OpenClipart 并入 MVP | 仅当 CC BY 署名成为实际障碍再启（phase-2），避免首版范围膨胀 |

> **现态注（2026-08-25，supersede 文首「尚未部署」等表述）**：映射已演进 v4 **402 键**并部署双机；数据按 C 组合换装**精选版每类 50 条（14900）**（`quickdraw_curate.py` 三因子质量分，原始 200 条/类本地备份）；测试演进 **39 项**/全套 **981**；质量路由 `_QD_STYLE_RE`：普通描述走 AI 位图链、仅简笔风描述查 QD。部署与对账见 `docs/deployment.md` 2026-08-25 条。本页 v3/387 键/200 条/976 等数字为设计批准时基线，保留作历史。
