# 精品图库（小程序官方精选单线图库）设计

2026-09-14 19:51 用户截图复现：阿里云的环境文件与 Portal 进程均未设置 `DRAW_PORTAL_MARKET_DIR`，默认目录没有 `manifest.json`；同一有效用户的本机/公网目录接口均返回 404「精品图库正在准备中」。证据 `results/20260914-market-release/ali-catalog-195151-611215.log`。继续完成已授权的首批来源核验、逐件选品、入库、双机数据部署和真实浏览/收藏验证，质量闸与每类20件门槛保持。

本轮改动清单：`mcp-server/market/**` 真实数据与许可记录；`market_ingest.py` 与复用转换器的市场导出保真；对应回归；本设计、契约与部署/状态台账。固定官方提交归档与八件首轮真实转换留在同一证据目录。预览发现旧五点平滑会改变稀疏矢量拐角，先用真实入库回归复现，再修市场调用；旧 curated 默认行为另行保留。

> 2026-09-14 定案。六项决策全部用户拍板，见决策记录。状态：入库工具、许可/哈希闸、Portal API 与小程序已有工作树候选和回归；首批467件/20类已于09-15合库；双机数据部署及真实页面验收待本批收口。

## 目标与范围

让用户（尤其孩子）不写描述、不等 AI 生成，浏览即得现成可画的精品单线稿，一键发送写字机。用户拍板命名「精品图库」（2026-09-14，拒绝「生图市场」：与 AI 文生图入口语义错位）。

**MVP 做**：官方导入外部免费图源 → 入库闸 → 小程序浏览/详情/署名展示 → 直达发送 + 收藏。

**MVP 不做**：UGC 上传、评分/评论、搜索（只按类目浏览）、多语言。

## 决策记录（全部用户拍板，禁止重开）

| # | 决策点 | 定案 |
|---|---|---|
| 1 | 运营模式 | A 纯官方导入，无 UGC（二期观察项） |
| 2 | 许可红线 | CC0/Apache/MIT 直收；CC-BY 4.0 收且**署名展示**（详情页 + 发送预览卡）；CC-BY-SA / CC-NC 硬拒 |
| 3 | 选品标准 | 精品路线：质量极高、经典，不凑数量 |
| 4 | 类目规模 | 约 20 类目（动物/鸟类/昆虫/水生/植物/花卉/食物/水果/蔬菜/交通工具/建筑/日常小物/文具/玩具/运动/乐器/节日/字母数字/几何装饰/表情），每类 15-30 条，总量 400-600；不足 20 条的类目不上线（沿用 curated `coverage_ready` n_min 先例） |
| 5 | 存储 | **VPS 本地目录**，git 为真相源，双机部署即冗余；不引入 OSS。图库涨到 GB 级或浏览流量可观再迁（manifest 存相对路径，后端可换） |
| 6 | 发送语义 | 详情页**主按钮直达发送**（不落 saved 表，临时走 svg→gcode→chain_push）；**次按钮收藏**（进 saved 表，50 件上限沿用 store.trim_saved_previews） |

## 数据模型

真相源进 git：`mcp-server/market/`（由明确Git提交出包部署到双机 `/opt/hutuji/mcp-server/market/`）。

```
market/
├── manifest.json          # 单一真相源，入库时生成
└── svg/<category>/<id>.svg
```

manifest 条目 schema（v1，字段名定死，后端可换存储时不改）：

```json
{
  "id": "twemoji-dog-1f415",
  "title": "小狗",
  "category": "动物",
  "license": "CC-BY-4.0",
  "attribution": "Twemoji (https://twemoji.twitter.com/), CC-BY 4.0",
  "source": "twemoji",              // 图源标识，采集溯源用
  "source_url": "https://github.com/jdecked/twemoji/...",
  "path": "svg/动物/twemoji-dog-1f415.svg",
  "viewBox": "0 0 36 36",
  "bytes": 4211,
  "quality_score": 0.92,            // 采集管线的三因子分（沿用 quickdraw_curate 思路）
  "ingested_at": "2026-09-14",
  "gate_version": "4"
}
```

`license` 必填，缺失或不在白名单 {CC0-1.0, Apache-2.0, MIT, CC-BY-4.0} → 入库拒收。

## 入库管线（离线工具脚本，非运行时）

```
图源抓取/手工下载
  → license 闸：白名单过滤，SA/NC 硬拒
  → 清洗转换：外部 SVG → 单线 polyline（复用 curated Twemoji 转换/量化闸思路）
  → _validate_svg 白名单（注入闸）
  → curated_gate v4 硬闸（vpype/§5、过碎、无墨、行数）
  → 预览 PNG 生成（viewBox 坐标框，复用 curated 教训：不准硬写 256）
  → 写 manifest + 产物过 git review
```

图源候选（采集阶段逐个核许可与稳定性）：Twemoji（CC-BY 4.0，仓内已有转换先例）、OpenClipart 社区镜像（CC0，注意主站 2024 已关，只收可信镜像）、Material Design Icons（Apache-2.0）、simple-icons（CC0）、Public Domain Vectors。每个源的许可文件随数据落 `market/licenses/<source>.txt`。

## 服务端 API（portal，FastAPI 函数式 + user_dep，风格对齐 `draw_portal/app.py` 的 `draw_saved_send` 路由）

| 端点 | 行为 |
|---|---|
| `GET /api/market/categories` | 类目列表 + 每类条目数（读 manifest，内存缓存） |
| `GET /api/market/list?category=&cursor=` | 条目卡片（id/标题/预览 URL/许可徽标），分页 30 |
| `GET /api/market/item/{id}` | 详情：预览大图 + 署名 + license + 发送/收藏状态 |
| `POST /api/market/item/{id}/send` | 直达发送：本机读 SVG → `_validate_svg` 复核 → `_svg_to_gcode_cached`（market 命名空间，独立缓存键）→ 现有设备 busy 语义拒并发 → chain_push 推送。产物走 output_store 两档 TTL |
| `POST /api/market/item/{id}/favorite` | 收藏：SVG 转产物后落 saved 表（标题=条目标题），50 件上限沿用 |

发送**不占 `_draw_limiter` 生图并发**（无付费生成），但占设备侧幂等闸——与 `hutuji_write` 发送链同一 busy 语义。

## 小程序

- **入口**：创作首页（index）上部加「精品图库」入口卡片；不加 tabBar（已满 5 个，微信上限）。
- **列表页** `pages/market/market`：顶部类目横滑 tab + 双列网格卡片（预览图 + 标题 + 许可徽标）。预览图走 portal 既有 `/api/draw/...` 图片通道（x-session 头，参照 api.js downloadFile 先例）。
- **详情页** `pages/market/detail`：大预览 + **署名行**（CC-BY 条目强制展示，缺署名视为缺陷）+ license 徽标 + 「发送给写字机」主按钮 + 「收藏」次按钮。发送中/成功/失败态复用 control 页 busy 文案。

## 契约（protocol.md §10.4.23）

- 市场条目的 license/attribution 字段服务端强制，缺省拒绝发送（fail-closed）
- CC-BY 署名在详情页与发送确认卡两处展示
- 发送链路复用 §4.2 多页推送语义与设备幂等；产物 TTL 与命名空间与 draw 隔离
- UGC 未开放；任何用户上传入口属二期，须另过隐私评审（参照 09-12 TG 备份单独同意教训）

## 部署与双机

- `auto_deploy.py` 从明确Git提交LF对象生成节点包；图库仅开放三个JSON、许可及既定中文类目SVG/G-code/PNG，最多2048文件、32MiB总包、4MiB单文件。真实临时Git提交的出包/安装/回滚已测。
- 双机逐文件SHA与manifest单独核验，保存备份号；既有preflight的 `check_market_optional` 只覆盖清单/许可/三类产物存在，不替代本批完整哈希核验。
- 数据完整后备份并设置 `/etc/hutuji/draw-portal.env` 的 `DRAW_PORTAL_MARKET_DIR=/opt/hutuji/mcp-server/market`（保留其余配置与600权限），重启Portal并核进程环境。后续同目录清单mtime变化触发缓存重载。

## 测试矩阵（pytest + Node 回归，进 draw-portal/tests 与 mcp-server/tests）

- 入库闸：license 白名单拒绝矩阵（SA/NC/缺 license/未知 license 全拒）、署名字段缺失拒收
- gate 复用：curated_gate v4 对市场 SVG 同样生效（过碎/无墨/行数爆炸拒绝矩阵抽 3 例）
- API：列表分页边界、详情 404、发送 busy 拒并发、收藏 50 上限 trim、未登录 401
- 发送：market 缓存键与 draw 命名空间隔离（同 SVG 内容不同键）、产物过 §5 闸
- 署名：CC-BY 条目详情回包必含 attribution 且前端渲染（Node 断言）

## 里程碑

1. **M1 入库管线 + 首批 20 类目采集**（预估大头工作量：选品/清洗人工审）→ manifest 入 git
2. **M2 portal API + 发送链路**（含契约 §10.4.23）
3. **M3 小程序列表/详情/入口** + Node 回归
4. **M4 双机部署 + 数据/接口/页面核验 + readiness 登记**

M1 完成前 M2/M3 可并行开工（用 3-5 个手工 SVG 夹具驱动）。

## 2026-09-15 首批交付事实

500候选中493件过既定硬闸，逐件最终预览再排除26件，正式467件/20类。每类20–27件，Twemoji326、Tabler101、MDI40。真实 `MarketCatalog` 冷加载2.143秒，数据1407文件/8405465B，manifest SHA-256 `5e1cb98010cbfc39dbce9728f957a64c9bfba1175ddc1cd9b19699a724685605`。分类数、拒收理由、改名及逐件复核记录见 `mcp-server/market/review.json`，官方URL/固定commit/访问日期/许可及限制见 `sources.json`。最终20张对照板在 `results/20260914-market-release/final/selection-review/`，LF重建逐件断言SVG/PNG与已复核版字节相同。

真实来源回归揭示并修复：五点平滑移动凹角；被遮挡轮廓穿透/同色接缝；毫米换算与像素中心导致右下边裁掉；隐藏透明描边回落；Windows CRLF导致Git发布哈希失配。市场转换保留稀疏顶点，按绘制顺序处理可见区域和孔洞，拒绝半透明；整数视口与预览留边覆盖完整轮廓。文本先LF规范化再算产物SHA，原许可保留单独原字节SHA。旧curated调用默认行为保持。7件硬闸拒收未靠放宽边界接收。

入库回归20项、发布工具43项通过；含真实SVG/vpype/PNG、nonzero/evenodd孔洞、嵌套transform、隐藏/透明/半透明、CRLF许可与Git归档/安装/回滚/1025–2049文件边界。本批hub12层PASS（1566项/155.28秒、e2e PASS、261处指纹）；Portal425项/154.43秒、Node226项与28份JS语法通过。归档 `results/20260914-market-release/hub-gate.{json,md}`，页面/Portal明细在 `results/20260913-followup-review/` 当次00:14记录。首轮失败仅为新增31项后文档计数未更新和一处旧行号，已按实际collect和证据行修正再全量通过。部署/页面验收随后登记readiness。
