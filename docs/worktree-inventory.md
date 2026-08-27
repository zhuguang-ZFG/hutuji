# 跨仓工作树清单（2026-08-24 更新）

> 本文件记录三仓未提交资产。接手时先对清单与 `git status`；清单过期就更新清单，**不是**把别人的改动回滚掉。
> 三仓路径：hutuji `D:\Users\hutuji` ｜ Grbl `D:\Users\Grbl_Esp32` ｜ xiaozhi `D:\Users\xiaozhi-esp32` ｜ fz `D:\Users\zhugu\fz`
> 
> 铁律：**不要假设任何固件源码在本仓库**——运动控制去 Grbl_Esp32 查，AI 交互去 xiaozhi-esp32 查。
> 登记范围含脏树、未推提交与「生产/板上运行态领先 GitHub」；HEAD 每次用命令现取，**不要把本页静态快照当作当前 HEAD 或板上镜像身份。**
> 2026-08-24 合并说明：旧版全文存档于 `tmp/inventory_head.md`；以下各「HEAD 存档并入」小节为旧版证据/决策原样保留（其中日期快照以当时为准）。

---

## 1. hutuji 本仓（`D:\Users\hutuji`）

### curated 精选库（2026-08-26，代码已合 main / PR #7；灌库未 flip）
| 项 | 说明 |
|----|------|
| 仓内 | `hutuji_mcp/curated*.py`、`scripts/curated_prep.py`、`CURATED-ATTRIBUTION.md`、测试与 `deploy/hutuji-curated-prep.service` 随文档批次入库；gate v3=`1353336` |
| 阿里云运行态 | `hutuji-curated-prep` enabled+active，写 `/opt/hutuji/curated`；暗部署：`HUTUJI_CURATED_DIR`/`REPLACE_QD` 未开。数据与 audit **不入 git** |
| 本机 | `tmp/deploy-20260826-curated-fill/` 部署草稿；`mcp-server/tmp/curated-fill.env` 含生产 env 镜像 → **禁止入库** |
| 未完成 | `manifest.ready`、抽检、京东云同步、REPLACE 探针；xiaozhi 空坐闸已推未刷机 |


### Twemoji 矢量源灌库通道（2026-08-27，已提交 `580bb02`/`639ee36`；阿里云首轮灌库完成）
| 文件 | 说明 |
|------|------|
| `mcp-server/scripts/curated_vector_prep.py` | 矢量源灌库脚本：下载 → vpype 展平 → RDP 自适应压缩（≤780 点、6 档）→ gate v3 → NNN.svg + manifest 重算 |
| `mcp-server/hutuji_mcp/twemoji_categories.json` | 212 类目 → ~250 codepoint 映射（已对账 QD 298 在册，剔 20 个不在册类目） |
| `mcp-server/hutuji_mcp/CURATED-ATTRIBUTION.md` | 追加 Twemoji（jdecked/twemoji，图形 CC-BY 4.0）署名与改动性质说明 |
| `mcp-server/tests/test_curated_vector.py` | +4 项（映射卫生/转换过闸/空路径/量化闸） |
| 阿里云数据面 | `/opt/hutuji/curated`：首轮 accept 456（两轮含 trumpet 补漏）→ 内容哈希去重 226 → cat 52/狗 52/树 52/房 52/鱼 53，**达标类目 142/298**，ready 仍 False；`/opt/hutuji/twemoji-cache/`（下载缓存，~250 SVG）；crab/1f980 单件 805>800 行放弃；prep 服务已恢复 active；**flip 机器侧抽检 142/142 OK**（运行时全链路），视觉样张 8 类目 7 优 1 弱（树偏小偏稀），多条目抬笔 51–88 软告警已登记为闸重钉样本 |
| 本机证据 | `tmp/curated-vector-data/`（230 条干净库）、`tmp/svg_pilot/`（五类转换预览 + 库内猫预览，均本机留档不入库） |

### curated 达标优先路由 + QD 退役（2026-08-27，已提交 `48b7d05` 并双机发布）
| 文件 | 说明 |
|------|------|
| `mcp-server/hutuji_mcp/curated.py` | 新增 `lookup_preferred`（类目 count≥n_min 即出、manifest 现读保鲜）+ `_pick_and_load` 共用件 |
| `mcp-server/hutuji_mcp/server.py` | 路由改 curated-prefer→AI；删 `_QD_STYLE_RE` 与 quickdraw 运行时查询 |
| 双机 env | 追加 `HUTUJI_CURATED_DIR=/opt/hutuji/curated`（京东云无数据前恒落 AI）；`HUTUJI_PAPER_RISK_GATE=0` 保持 shadow |
| 验证 | test_curated_server 5 项 preference 语义（QD 禁查负断言）、test_quickdraw 删 5 死路由用例留 36、全套 1025/1025、hub 门禁 pass；冒烟 `随便画个猫` → curated hit idx=003、3.4s 零付费零 QD |

### curated 预览坐标框修复（2026-08-27，已提交 `cfee84e` 并双机发布）
| 文件 | 说明 |
|------|------|
| `mcp-server/hutuji_mcp/curated.py` | `_preview_from_svg` 按 viewBox 解析源坐标框（原硬写 256 把 1024/36 空间点全裁出画布，小派预览空白实锤） |
| `mcp-server/tests/test_curated.py` | +2 项（1024/36 双空间像素级有墨断言）→ 6 项；全套 1027/1027 |
| 验证 | 冒烟 `画一只猫` → curated hit → 预览 1898B 完整猫（本机回看）；阿里云 196602/196603、京东云 75901 |

### AI 纸面风险闸批（2026-08-27，已提交 `7bf64a8`/`ee5eca0`/`ff718c8`/`1dbd310`/`0717e25` 并双机发布；设计 `docs/superpowers/specs/2026-08-27-ai-bitmap-paper-quality-design.md`）
| 文件 | 说明 |
|------|------|
| `mcp-server/hutuji_mcp/paper_risk.py` | 新模块：五指标纸面闸（short_g1_ratio/pen_lifts/travel_gap_p50/ink_span/scribble_core）+ `enforce_gcode`；阈值钉 08-26 废/好样本中位；Z5 落笔/Z0 抬笔状态机逐字对齐 `hutuji_gwrite.toml` |
| `mcp-server/hutuji_mcp/bitmap_svg.py`（已修改） | `_rdp_polylines` 迭代化（递归 O(n) 深度在地板低 tol 下打栈）+ `_decimate_polylines_with_floor`（逐级减半 tol、下限 0.3px、不回退未抽稀）+ `_suppress_hatch_clusters`（排线填色短段簇整簇丢弃）+ `generate_bitmap(..., attempt=)` 破缓存；trace 顺序 stitch→hatch→decimate |
| `mcp-server/hutuji_mcp/server.py`（已修改） | `_run_draw_job` AI 路径纸面闸重试环（最多 3 轮、Reject 清半成品、`PaperRiskExhaustedError` 无废 URL）；QD/curated 不经闸 |
| `mcp-server/hutuji_mcp/config.py`（已修改） | `paper_risk_gate`（`HUTUJI_PAPER_RISK_GATE`，默认开） |
| `mcp-server/tests/test_paper_risk.py` / `test_bitmap_decimate.py` | 新测试 13 + 8 项 |
| 验证状态 | 全套 1027/1027；hub 门禁 **overall=pass**（指纹 231/231）。闸批已提交至 `06daa95` 并**双机发布 + 转 shadow**（用户拍板：travel_gap 2.0 疑似误杀正常猫，攒样本重钉后再 enforce；`HUTUJI_PAPER_RISK_GATE=0`）。另修：scribble v2 平移不变窗（版本 1→2）+ 拒收缓存逐出 + shadow 指标日志 |

### 未跟踪文件（本次调研新增）
| 文件 | 说明 |
|------|------|
| `lineart-research-report.md` | 简笔图获取方式调研报告（纯调研产物） |
| `docs/design/quickdraw-local-cache.md` | Quick Draw 本地缓存落地设计稿 v0.1（待用户批准，未实施） |
| `USER_MEMORY.md` | 用户记忆备份（跨会话持久化） |
| `memory_user.json` | 用户记忆 JSON 备份 |
| `tmp/` | 临时工作目录；2026-08-25 半墨调查新增 `results/local-only/2026-08-25/{tree-redraw-com14,fix-boot-com14,tree-redraw-fixed-com14}.log`（复发抓包 10 次落笔期空窗 230–640ms / 刷机后启动验证 / 修复后复测空窗 max 30ms），gitignored 留本机；**2026-08-25 补登记**构建/取证产物组：`tmp/build-gate-logs/`（19 个 .log/.err）、`tmp/build_ws35.log`、`tmp/build_gate_env.sh`、`tmp/build_instr_{lichuang,ws35}.ps1`、`tmp/build_hil_ws35.ps1`、`tmp/boot_capture.py`、`tmp/zdraw_capture.py`（出图判别取证：出图前 `$#`/`?` + 画线期 `?` 轮询，COM13 只读、dtr/rts=False 铁律），以及 `results/local-only/2026-08-25/grbl-z-baseline-com13-20260825.txt`（COM13 只读 `$$`/`$#`/`?` 快照），均留本机不入库 |
| `mcp-server/tmp/{telnet_draw_test,two_opt_ab,capture_resilient,motion_profile_analysis}.py` | 喂流/运动实验件（F 提速 A/B 与 2-opt 排除用；2026-08-25 advisory 后已改：`telnet_draw_test.py` 送机前强制过生产 `validate_gcode`（exit 6 拒发），`two_opt_ab.py` 弃手抄步序改复用 `_vpype_command()` 仅注入 `--two-opt`）。保留本地不入库 |
| `mcp-server/tmp/qd_categories.txt` | QD 类目清单工作副本，保留本地 |
| `mcp-server/tmp/abkit/`（star.svg + draw_f8000/f10000/f12000.gcode） | F 变体实验件。**`draw_f12000.gcode` 违 §5（头行 F12000、全行 F12000>10000 上限）——禁上机**，现由 `telnet_draw_test.py` 的 validate 闸拦截；f8000/f10000 合法仅作对照。保留本地 |
| `mcp-server/tmp/twoopt_star/{cur,opt}.gcode` | 2-opt A/B 输出对照件，保留本地 |
| `mcp-server/tmp/qd_verify/cat.{gcode,jsonl}` | QD 命中验证产物（jsonl 已被 `.gitignore` 窄规则盖住）；保留本地 |
| `tmp/build_jog_ws35.ps1`、`tmp/flash_jog_ws35.ps1` | jog 批构建/烧录脚本（`--after=watchdog-reset` 路径，与既有 flash_hil 同构），保留本地 |

### Quick Draw 本地缓存落地（2026-08-24 设计稿用户批准后实施，已提交 `92b00b7`+`2e0b1f2` 并推 origin；本日复核修复 `1952caa` 已部署阿里云生产并同步京东云热备，两机运行时面与 HEAD 一致）

| 文件 | 说明 |
|------|------|
| `mcp-server/hutuji_mcp/quickdraw.py` | 运行时模块：类目映射 + 笔画加载 + 复用位图产物面 |
| `mcp-server/hutuji_mcp/quickdraw_categories.json` | 类目映射 v3（**387 别名键 → 298 官方在册类目**，已对账 categories.txt；手机改指 cell phone）。2026-08-24 实测：去 `_meta` 后 387 键、去重后 298 值，与 `quickdraw-data/` 的 298 个 `.jsonl` 双向零差；本轮补入 14 个多字别名覆盖原单字类目；2026-08-25 v4 批演进 402 键、C 组合双机部署见本页 C 组合节 |
| `mcp-server/hutuji_mcp/QUICKDRAW-ATTRIBUTION.md` | CC BY 4.0 署名文件 |
| `mcp-server/hutuji_mcp/{config.py,server.py}`（已修改） | `HUTUJI_QUICKDRAW_DIR` 配置项 + `_run_draw_job` 命中旁路 |
| `mcp-server/scripts/quickdraw_prep.py` | 数据准备脚本（蓄水池采样、quote 编码、固定种子可复现） |
| `mcp-server/tests/test_quickdraw.py` | **34 项**契约/集成测试（此前 16 项；本轮补入 4 个最长子串碰撞断言 + 14 个单字类目增强短语断言 + fixture 缓存隔离）；2026-08-25 v4 批后演进为 **39 项**（+3 礼貌前缀/循环剥离/NO_STRIP 守卫），全套 **1035** 项（2026-08-26 +6；2026-08-27 纸面闸批 +19 后 scribble v2 +1、拒收缓存逐出 +1、Twemoji 矢量源 +4；QD 退役 -5、curated 优先 +2、viewBox 预览 +2；junction 续接 +5：`test_paper_risk.py` 13、`test_bitmap_decimate.py` 13、`test_curated_vector.py` 4、`test_quickdraw.py` 36、`test_curated_server.py` 5、`test_curated.py` 6） |
| `mcp-server/tmp/quickdraw-data/` | 全量数据产物（2026-08-24 基线 298 类 × 200 条 ≈ 20MiB；2026-08-25 C 组合换装精选版每类 50 条，见 C 组合节）。历史误记「gitignored 目录内」已于 2026-08-24 撤（实测 `git check-ignore` exit=1、`?? mcp-server/tmp/`、`git add -An` 干跑可见 314 文件）。**决策状态（2026-08-24 用户 advisory 点名）**：本页 `edf68e7` 条目「不新增忽略规则」对**整目录忽略 tmp/** 仍成立——advisory 同文否决整目录方案：AGENTS.md 刷机坑段引用根 `tmp/` 工具（flash_hil_ws35.ps1、reset_wdt_capture.py），整目录忽略会把它们变成只在本机的隐形文件；仅对**数据集本体**开窄例外：`.gitignore` 新增 `mcp-server/tmp/quickdraw-data/` + `mcp-server/tmp/**/*.jsonl` 两条——后者兜住 qd_smoke/qd_trial/qd_verify 下 4 个散落 .jsonl（同属 CC BY 4.0 素材，入库即构成再分发）。实测 `check-ignore -v` 双向命中正确、`git add -An` 下数据集不可见；mcp-server/tmp/ 剩余可见面 = 12 个实验/工具件（abkit、twoopt_star、capture_resilient.py 等，干跑实测）**仍未忽略**，故**禁止 `git add .`/`git add -A`**，提交必须点名暂存 |
| `results/local-only/2026-08-24/aliyun-47.112.162.80-inventory.txt` | 阿里云盘点 + Recraft 可达性探测证据 |
| 验证状态 | hub 门禁 **PASS（2026-08-24，13/13）**；全量 pytest **976/976**、e2e `PASS`；docs 指纹 216/216、外仓引用 212/212；Quick Draw 映射 387 键→298 类、14 个单字孤儿类目均有多字别名且跨类子串碰撞为 0；最长优先 4 个真实碰撞断言通过；服务异步 vpype 转换最多 2 个并发且取消/超时回归通过。**已于 2026-08-24 部署阿里云生产**（3 文件 sha256 对账、双服务重启、ExecStartPre 过、hit 冒烟 0.714s 命中 cat、公网下载逐字节一致、/mcp 404）**并同步京东云热备**（6 产物+数据集 298 文件+env，仅重启 mcp、bridge 保持 disabled，preflight 25/30 其 5 fail 均为热备态预期分歧；详见 `deployment.md` 顶部两条）。**生产量化同日完成**：lookup 类目覆盖 23/23（14 修复类目全 HIT）；6 并发 hit 出图全 OK（墙钟 2.29s、同秒 6 产物、付费日志 0 条）。**仍未执行**：真实语音 HIL、真实使用命中率统计；根 `tmp/` 暂存副本已纳入 `scripts/agent_gate.py` `_REF_SKIP_DIRS`，原 basename 假绿根因已修 |
| `docs/design/s3-stream-pacing-investigation.md` | 喂流爬行调查 **已结案（§6+§7 两轮）**：第一轮根因=每 ok 一次 LCD 屏刷阻塞灌流循环，修复=屏显 250ms 节流 + 缺纸预检移除 + Grbl 关省电（§6）；第二轮 lichuang 全脸动画下节流不足致半墨复发，根治=屏显走 `Application::Schedule` 出灌流路径（§7，xiaozhi `e623aec`）。§3.1/§4.B/§4.C 已盖 `[superseded → §6]` 标记。用户未提交的结案编辑连同标记与行号修正已随本批入库（如实登记） |

### C 组合（样本精选 + 质量路由，2026-08-25 晚，已提交 `fdf9874`+`41650e4` 并推 origin/main）
| 文件 | 说明 |
|------|------|
| `mcp-server/scripts/quickdraw_curate.py` | 精选脚本：三因子质量分（平滑 0.4 + 构图 0.3 + 笔画数 0.3），每类留头部 `--keep`。**写盘纪律**（2026-08-25 advisory 后加固）：`--out-dir` 写新目录，缺省原地写但自动留 `.bak`；`kept` 为空跳过写盘并 stderr 标红；报告分列 `bad_lines`（整行解析失败）与 `skipped_empty`，避免整文件解析失败被静默清空且报告看不出 |
| `mcp-server/hutuji_mcp/server.py`（已修改，已提交） | `_QD_STYLE_RE` 质量路由门：默认走 AI 位图链保质量，仅描述含 简笔画/简笔/速写/涂鸦/快速/随便/灵魂 才查 QD 本地缓存 |
| `mcp-server/hutuji_mcp/quickdraw.py`（已修改，已提交） | 风格词并入 `_PREFIX_RE` 剥离链（与路由门解耦，**词表改动两处同步**） |
| `mcp-server/tmp/quickdraw-data/` | **已换装为精选版**：298 类 × 50 = 14900 样本（干净重跑：59600→14900，`bad_lines=0`、`skipped_empty=0`）。原始 200 条/类备份在 `mcp-server/tmp/quickdraw-data-uncurated/`（同属 CC BY 4.0 素材，`.gitignore` 的 `mcp-server/tmp/**/*.jsonl` 已兜住） |
| 目视验证 | 精选头部 top-3 渲染人眼过样（cat/tree/fish/pig/house 各 3 张）：完整猫脸、可识别鱼形与猪脸；对照随机抽样为抽象涂鸦。**顺序为先渲染过样、再换装真数据** |
| 双机部署 | 阿里云 + 京东云各 298 个 `.jsonl`、`.bak` 0 个；**全量清单收口**（2026-08-25 advisory 后）：本地 ↔ 两机按 `basename→sha256` map、`LC_ALL=C` 归一化后逐项 diff **零差异（298/298**，覆盖 `-`/`_`/空格混用类目名，取代此前 4 类目抽样结论）；代码 2 文件两机 sha256 亦相同（`27c8cb31…`/`a097617d…`）。旧数据保留为 `/opt/hutuji/quickdraw.old` |
| 生产冒烟 | 阿里云双链验证：`画一只猫`→AI 链 12.81s / 704 行（日志「位图链路分段：生成+下载 9.3s，描摹+预览 2.4s」）；`简笔画一只猫`→QD 0.80s / 60 行（日志「Quick Draw 命中 category=cat entries=50」，`entries=50` 即精选数据生效）|
| 验证状态 | hub 门禁 **PASS**（`56e2717`：pytest **981/981**、e2e `PASS`、指纹 216/216、外仓引用 212/212）；京东云本批后 preflight **25/30**（5 fail 与 08-24 热备基线同款预期分歧：bridge active/自启/重启 4 项 + 公网 /files 真下 1 项因 DNS 指阿里云）；阿里云本批后 preflight **20/30**（10 fail 为 Alibaba Linux/双 nginx 布局已知环境差异，同 `deployment.md`「30 项为京东云口径、阿里云不适用」口径，非本批回归）；fz `json_feed_hold_tcp` 隔离重放 **3/3 PASS**（同 simulator SHA、`-t 1.0`；standard profile 被 MinGW `limits.h` 头遮蔽致 `AxisMask` 未定义的环境层阻断，非该 case 回归）；COM13 只读快照留档（`$1=25` 未改、`$#` G92 Z=5.000、`?` MPos Z=5.000）。**仍未执行**：真实语音 HIL（C 组合部署后）、真实使用命中率统计 |

### 已修改文件（此前任务遗留，非本调研任务）
| 文件 | 变更类型 | 来源任务 | 说明 |
|------|----------|----------|------|
| `docs/daytime-tuning-runbook.md` | 行号更新 | daytime-tuning | 代码引用行号随提交前移 |
| `docs/design/p2-windowed-flow-control.md` | 行号+逻辑更新 | P2 窗口流控 | 窗口流控设计文档与实现同步 |
| `docs/kxnx/findings/17-reconnect-resend-hazard.md` | 行号更新 | kxnx 调查 | 重连重发 hazard 代码锚点更新 |
| `docs/kxnx/findings/21-transport-watchdog-and-sd-executor.md` | 行号更新 | kxnx 调查 | transport watchdog 代码锚点更新 |
| `docs/protocol.md` | 行号更新 | abort_reset 修复 | abort_reset_owner 行号 +8；~~未提交~~ **已随 2026-08-24 `766cfd1` 入库**（该行引用同批经外仓指纹层校验 212/212） |
| `docs/release-readiness.md` | 日期+摘要更新 | UX 审查收口 | 全链 UX 审查 2026-08-24 结论追加 |
| `docs/troubleshooting.md` | 行号更新 | 多轮调试 | hutuji_job.cc 多处代码行号随提交前移 |

### 证据基线与批次登记（HEAD 存档并入）

- **路径 / 分支**：`D:/Users/hutuji` / `main`
- **当前提交链**：实时用 `git log --oneline origin/main..HEAD` 与 `git rev-parse --short HEAD` 查询；不以本页静态快照代替。
- **生产运行态（2026-08-23 晚割接后）**：**阿里云 `47.112.162.80` 为主**——`hutuji-mcp`+`hutuji-cloud-bridge` 双 active（bridge cloud/proxy 双 connected，MainPID=4017183/4017186），DNS `hutuji.donglicao.com` A 记录直连，Let's Encrypt 双证书双 443 块，代码基线 `c2cdbd4`（14 模块 SHA-256 与 Git blob 逐文件相符）；真链出图公网下载 `200/16419B/CRC 5e85cf73`。el8 unit 差异：删 systemd 239 不识的 4 个 Protect/ProcSubset 行与 `set_mempolicy_home_node`，加 `SystemCallErrorNumber=ENOSYS`（修 Python 3.12 `pidfd_open` 被 @system-service 拦成 SIGSYS）；生效配置回同步本机 `results/.tmp/deploy-aliyun/`。**京东云 `117.72.118.95` 转热备**：bridge disabled+inactive（防 token 对拍），hutuji-mcp active 待命；回滚 = DNS 指回 CF Tunnel + 启京东云 bridge + 停阿里云 bridge。真实语音 HIL 未执行。
- **本地保留资产**：`results/local-only/2026-08-18/`、`2026-08-19/`、`2026-08-20/` 与 `2026-08-21/` 均保留且被 gitignore；08-21 新增唱歌部署候选与冒烟证据、刷机事故串口原始日志（`2026-08-21-sing-bootloop-raw.log`）、恢复启动日志（`2026-08-21-waveshare-restore-boot.log`）与触摸第三轮开机取证（`2026-08-21-touch-round3-boot.log`，含双门回读 `70 -> 20 peak 60 -> 40`）。**2026-08-22 新增 `results/local-only/2026-08-22/hil-draw-com14.log`**：设备消息 API 触发的五角星全链 HIL 串口证据（生成→预览→确认→下载 CRC32 `586d72ae`→流控出图→归位→M30→error:90 缺纸）。根目录杂散 `COM14` 文件已移入 `results/local-only/2026-08-20/`；`tmp/` 未跟踪保留。不新增忽略规则，避免同类误生成被静默掩盖。
- **2026-08-23 阿里云部署本机资产（gitignored，不删）**：`results/.tmp/deploy-aliyun/` = 灰度节点部署候选——14 模块 + `hutuji_gwrite.toml` + `requirements.txt` 由 `git cat-file blob HEAD` 导出（远端 SHA-256 已逐文件核对相符、CR=0）、`nginx-hutuji-gray{,-bootstrap}.conf`（阿里云直连入口模板，`$binary_remote_addr` 限速、`/mcp` 404，已生效于 `/etc/nginx/conf.d/hutuji-gray.conf`）、`hutuji-{mcp,cloud-bridge}-el8.service`（systemd 239 兼容 unit，已生效于 `/etc/systemd/system/`）；`results/.tmp/songs/` 是京东云歌曲目录副本（已同步到阿里云 output/songs，属主 hutuji）。**会话期 askpass 密码文件已删除**（用完即焚，凭据不出 `D:/Downloads/VPS.txt`）。**独立缺陷登记（不在本轮修）**：复活/禁语扫描面是 `GOVERNANCE_DOCS` 18 份，`results/` 整目录 gitignored 不在扫描面——治理文本若落进 results 下文件门禁看不见；本轮合规来自用户显式决策反转与源头规则下线，不依赖该盲区。
- **工作树（2026-08-22 现取）**：HEAD 以 `git rev-parse --short HEAD` 为准。本轮文档（发现收紧契约/行号、厂商参数快照、售后 KB 厂家信息）随文档提交入库；§9-E′ 契约+白名单+行号基线批次与 bridge-health 加固批次随后入库（见提交链）。原「桥接空闲死链探活」WIP（过期 id 本地消费、禁止覆盖 pending、回包刷新 `_last_traffic`、顶层 JSON-RPC error 立即探活、stdin 锁）**已于 2026-08-23 从 `stash@{0}` pop 并入库 `998e9f2`**：同批并入 review 中等项修复——`_run_proxy_session` spawn 包进 try/finally + shield 等 spawn 落定拿句柄，取消打在 `create_subprocess_exec` 中途不再孤儿 mcp-proxy（新增用例 RED→GREEN 实证：旧实现 `processes` 为空判红）；test_cloud_bridge 106→116、全量 908→918 绿，hub 门禁 PASS。stash 已清空。用户未跟踪资产 `memory_user.json` 与 `tmp/` 不纳入候选。**不要把静态 SHA 当作实时生产/板上身份**。
- **未推判断**：不用静态文字；执行 `git log --oneline origin/main..HEAD`。
- **已入库 `edf68e7`（2026-08-23 稳定性批次 R4，已推 origin）**：`bitmap_svg.py` 生图对冲/下载/描摹分段计时日志 + `server.py` G-code 段计时（只记耗时数字，不落描述/URL）；新增 `scripts/cloud_health_pull.py` 云端取证脚本（ssh BatchMode 拉生产 journalctl 两 unit → 能力名 token 脱敏 → 11 类关键事件计数，退出码 0/1/2，落盘 `results/local-only/<日期>/cloud-health-*`）+ `test_field_tools.py` 4 项新测试。独立 reviewer 审查后修复：脱敏正则尾部 `\b` 在 token 末位为 `-` 时整段漏码（改负前瞻 `(?![A-Za-z0-9_-])` + 三种尾随形态回归测试）、远端 shell 引号 `list2cmdline`→`shlex.quote`。reviewer 曾建议把 `memory_user.json`/`tmp/` 加进 .gitignore，**与本页既有决策（不新增忽略规则，避免同类误生成被静默掩盖）冲突，已回退**。pytest 924/924；`server.py` G-code 计时已随 `c2cdbd4` 部署生产（见下条日志脱敏批次），`bitmap_svg.py` 计时与 `cloud_health_pull.py` 仍为本地取证面未部署。
- **已入库 `5d91ba3`（2026-08-23，已推 origin）**：`scripts/agent_gate.py --fix-line-refs` 只自动处理指纹原文唯一命中的纯行号移动，内容变化/多命中/旧基线无原文等均退 1 交人工；同步 `docs/agent-gate.md`、Cursor 硬轨、带 `source` 的指纹基线与 5 项回归。AGENTS 同批固化 USJ + App 运行中 EN 硬复位根因、`--after=watchdog-reset` 与 WDT 抓包路径；全量 pytest 929/929，hub PASS。`tmp/` 脚本仍按既有决策不入库。
- **已入库 `84aaca0`（2026-08-24 P1-3 hub 批，已推 origin）**：`HUTUJI_XIAOZHI_DEVICE_TOKEN` 配置项；draw 唯一完成点经官方 `/messaging/device/tools/call` 链式调 `hutuji.draw`（双参数 fire-and-forget，失败回落两步），消灭 #1011 漏调「纸不动」；pytest **936/936**（+4 链式契约）+ hub 门禁 PASS（e2e PASS、指纹 178/178）。KB 缺纸问答+画得淡试笔、role-prompt 自动衔接幂等口径同批。**未部署生产；启用前提 = 有效设备消息 token 写入生产 mcp.env（当前无）**；云端 `sync 2094251` 待固件刷机 + token 配置后执行。
- **已部署日志脱敏批次（2026-08-23，提交 `c2cdbd4`）**：新增 `hutuji_mcp/redact.py` 作为 MCP/bridge/取证脚本唯一共享实现；`server.py` 在 lifespan 首动作给 root、uvicorn、httpx/httpcore handler 安装 filter，`cloud_bridge.py` 只迁移 import，JSON-RPC 标签/请求 ID/watchdog 未改；`cloud_health_pull.py` 落盘前复用完整 URL/凭据脱敏。三文件按 Git blob 导出、CR=0、远端哈希逐一相符，双服务重启后 active，`/bridge-health` 200，preflight `30/30`；生产 POST/GET 与 access 探针均实测脱敏，取证文件 `results/local-only/2026-08-23/post-redact-deploy/cloud-health-20260823-212814.log` 已复核无原始 URL/签名值。生产泄露 journal 已 rotate/vacuum，本地取证副本已涂除；删除日志不等于撤销 24h 预签名 URL。本条 supersede R4 条目的笼统“计时日志未部署”：`server.py` G-code 计时已上线，`bitmap_svg.py` 计时仍未上线。用户未跟踪资产 `memory_user.json` 与 `tmp/` 不纳入候选。

---

## 2. Grbl_Esp32（`D:\Users\Grbl_Esp32`）

**工作树干净** — 无未提交改动。

### 板上基线与验证边界（HEAD 存档并入）

- **路径 / 分支 / HEAD**：`D:/Users/Grbl_Esp32` / `fix/panel-hold-after-align-116687e` / 实时 HEAD 现取（2026-08-22 已提交 `fb2b3b5` §9-E′ 换纸入口原子认领，含 WebSettings.cpp 预检注释降级）；工作树干净。
- **有证据的板上行为基线**：`e3b1cad`（2026-08-15 烧录与启动自证）；源码后续 `847b564` 增加 §9-D′ 蓝牙功率，**没有本轮烧录证据，不能把源码 HEAD 写成板上基线**。
- **2026-08-22 换新写字机（COM13，CH340）**：烧前已完整取证并备份——原厂整片镜像 `results/local-only/2026-08-22/new-plotter-factory-backup.bin`（4MB，esptool read_flash）、串口证据 `new-plotter-serial-evidence.log`（烧前 `$I`/`$$`/`$102`/`$130`/`$131`：厂商默认 `$130/$131=200/200`；烧后 `$$` 快照为 fork 机型默认 `$130=210/$131=297`，`$20=0` 软限关闭，云端 190/190 限幅不受影响；X 长边/短边仍待装纸后单轴点动实测量尺）。烧入 app-only `0x10000`（`e3b1cad` 经 worktree 重建 `release`，PlatformIO SUCCESS，esptool `Hash of data verified`），未整片擦写，授权 NVS 保留（启动日志 `[License] Authorized (restored from NVS)`，ChipID `00002C876AE9BFB4`）。注意：本机 scoop mingw 16.1 对 fz 门禁不可用——rt_v14 的 `pthread.h`→`limits.h` 头链把 Grbl `src/limits.h` 经 `include_next` 卷入系统链（`-iquote` 下同样触发，实测复现），且该 winlibs 构建不附 libasan/libubsan（mingw-gcc 无 ASan 属社区已知，msys2/MINGW-packages#3163）；**解法：PATH 前置 scoop llvm `22.1.8/bin`（fz `find_compiler` 原生优先 clang++），fz `standard` 实测 overall=pass**（`wokwi_startup` 可选云层红，用 `.pio` 旧镜像、不含 E′，按仓规不阻断）。故新机用已验证基线 `e3b1cad` 而非当时的 HEAD；E′（`fb2b3b5`）已过 fz standard host SIL，**未烧录、未实机复现物理并发**，板上基线不变。配网走 AP 模式（`GRBL_ESP`，WebUI/命令行），静态 IP 待配网完成后按实读网段写 `.13`。
- **2026-08-23 烧录 `fb2b3b5`（COM13，用户批准后执行）**：`pio run -e release` 重建 SUCCESS（仅重编 PaperSystem.cpp/WebSettings.cpp，与 E′ 触碰面一致；新 bin 1857936 字节，取代 08-15 旧镜像）→ `pio run -t upload --upload-port COM13` app-only `0x10000`，esptool `Hash of data verified`，RTS 硬复位。启动自证 `results/local-only/2026-08-23/fb2b3b5-com13-boot.log`（抓取脚本 `tmp/cap_fb2b3b5_com13.py`）：`Grbl_ESP32 Ver 1.3a`、paper system vM716 ready、`[License] Authorized (restored from NVS)`、连上 `ChinaNet-jJmz` 获 `192.168.1.13`、HTTP/TELNET:23 起来，无崩溃重启。**矛盾待澄清**：本次启动自报 ChipID（efuse MAC）`0000FC7742C63C94`，与 §2 上文 08-22 记录的 `00002C876AE9BFB4` 不一致（本地证据文件中检索不到后者出处），COM 号漂移或多台写字机皆有可能——物理身份需用户确认；可确认的是「今天 COM13 这块板」已跑 `fb2b3b5` 且授权/.13 网络身份正常。物理并发复现（E′ 的 HIL）仍未做。**wokwi 同日登记**：复跑 `run_wokwi_smoke.py`（新镜像已就位）失败根因为 Wokwi 免费版月度 CI 配额耗尽（`API Error: ... Free plan monthly CI minute quota`，serial 0 行），属外部阻塞非固件回归；该可选层本月不可跑，不阻断产品门禁。
- **验证边界**：核心 §9、归位、自动换纸有历史证据；后续源码变化须另跑 fz、构建、烧录与启动身份核验。
- **观察项（2026-08-23，无抓包证据）**：写字机经 `[ESP110]AP`+`[ESP444]RESTART` 切入出厂 AP 后稳定在线约 1 小时（jump1–jump4 窗口串口自证），随后在两次抓包间隙自行回到 STA `192.168.1.13`（jump5 时 S3 缓存 IP 命中并授权通过）。AP 模式是否存在自发回退未定性——同期窗口有人肉拔插 S3 USB 的物理操作，不排除误碰写字机供电；后续若再现需在抓包覆盖下复测。

---

## 3. xiaozhi-esp32（`D:\Users\xiaozhi-esp32`）

**工作树干净（2026-08-24 语音手动控制批 `fb9eeb2` + 2026-08-25 落笔棘轮闸批 `25b7dd0` + 2026-08-25 半墨根治批 `e623aec` + 2026-08-25 点动越界同步预检批 `95884c7`，、2026-08-26 触摸第四轮批 `12b6751` + 扩展点击区 `0cf01ce` + 角钮 56px 合规 `a7e0495` + 几何日志 `135cecd`/`0ea1cbd`（均未刷机，最终镜像待刷 COM14）均已推 origin/feat/hutuji-usb-pipe；`95884c7` 及以前均已刷 COM14，当前板上为 `95884c7`）**：语音白名单 + 双板 `hutuji.manual` 注册（`fb9eeb2`）；落笔棘轮修复（`25b7dd0`）——pen_down 的 G92 重设基准序列重复触发会逐次压深 5mm（无限位、语音面摩擦最低），现以 `manual_pen_down_latched_` 原子锁存幂等短路，SetState 进任何非 settled/manual 态即清除强制重校准；双板描述逐字补幂等语义与方向映射（左=X- 右=X+ 前=Y+ 后=Y-）。半墨根治（`e623aec`）：`UpdateDisplayProgress` 的 SetStatus 经 `Application::Schedule` 回主任务，灌流路径零 UI 阻塞——此前内联 SetStatus 在 LVGL 锁上被全脸动画整帧渲染卡 230–640ms（TTS 播报期最烈），planner 排空 → `$1=25` 失能 → 弹簧抬笔 → 树冠/框左缘微段区无墨；250ms 节流（bf0b295）只降频率救不了单次锁等待。修复后实测落笔期空窗 max 30ms、>100ms 零次（修复前 640ms/10 次），语音出图**全墨**；证据与机制详见 hub `docs/design/s3-stream-pacing-investigation.md` §7。点动越界同步预检（`95884c7`）：实机语音 HIL 发现越界只在 ManualTask 拦时工具已回 started、失败仅进 notify，LLM 假播「已经往左挪啦」——现 `RequestManualControl` 于任务派发前以同一新鲜坐标+同一 `DecideJog` 预判，越界/坐标不可信以工具返回值直述失败，任务内判定保留纵深防御；复验原点左挪播报「已经在最左边啦」，1mm 细档 `$J=Y1.0` 实证。**语音 HIL 完成（两轮抓包 hub `results/local-only/2026-08-25/manual-hil-{com14,fix-com14}.log`）**：棘轮幂等「再落笔一次」零运动实证、点动/抬落笔/回原点/步距双档逐条过。host **135/135**。**板上镜像**：`build-jog-ws35/xiaozhi.bin` 3,017,968B SHA-256 `7325a367…c188`（= `95884c7`，2026-08-25 刷 COM14，`Hash of data verified` + WDT reset；上一版 `80e4ffe8…d7b7`=`e623aec` 已被覆盖）。

### 批次登记与板上镜像（HEAD 存档并入）

- **已入库 `a1afd2a`（2026-08-24 P1 固件批，已推 origin/feat/hutuji-usb-pipe，经 `git merge-base --is-ancestor a1afd2a origin/feat/hutuji-usb-pipe` 核实）**：pipe 解析 `[ESP901]` 三字段（Paper/MotorEn/PanelHold，与 Changing 同守卫、Unknown fail-open）；`StatusJson` 只读暴露遥测（禁发命令防孤儿 ok 在途漂移，host 源码断言）；遥测刷新走 `Preview()`/出图前预检消费式；**出图前预检**（[ESP901] 消费式遥测 + Changing=On 守卫；Paper 字段仅上云、fail-open，S3 侧无缺纸出图闸——「Paper=No 早退」旧表述 2026-08-25 源码复核撤正）；换纸 90s 补 30s 周期播报；**预览同参数重入幂等**（P1-3 配套，回 `previewing` 非「正忙」）。**幂等谓词 fixup（未提交，工作树 hutuji_job.cc + test）**：判据从 `awaiting_confirmation_.load()`（要等 PNG 下载完才 true，主路径不成立）改为「已在预览流程中」（`state_ == "previewing" || "awaiting_confirmation"`，取快照于 `state_mutex_` 下），host 源码断言钉死。host **135/135**（+4 契约）、ESP-IDF v6.0.1 双板构建通过（fixup 后重建 `ALL DONE`）、clang-format 干净；**未刷机**（板上仍 `93b61d5b…afa9`）。
- **已入库 `12b6751`（2026-08-26 触摸第四轮：拖动跟手性，已推 origin/feat/hutuji-usb-pipe，未刷机）**：`main/display/lcd_display.cc` 单文件双修复——①`SpiLcdDisplay` taskLVGL `priority` 1→4（上游 2025-03 遗留低于组件默认；audio_output(4)/opus_codec(2) 不绑核落核 1 反复插队 PRESSING 事件与重绘）；②`AccentDriftTimerCb` 指针按下期间整帧跳过（`lv_indev` 迭代判定；每 100ms 色相呼吸+阴影脉动常载渲染管线，拖动重绘排动画帧后）。用户体感「不怎么跟随手指」+ 08-21 诊断镜像芯片层排除结论为取证链。host **135/135**、clang-format dry-run 过；ESP-IDF v6.0.1 `build-touch4-ws35/xiaozhi.bin` 3,018,016B SHA-256 `c5bada19d46a2289ddc19e31df4d581e7717fc4cd60467280173be6ff94a76db`；构建脚本 `tmp/build_touch4_ws35.ps1`（复制 build_hil_ws35 改 build 目录名，仓库根 sdkconfig 会被重写为 waveshare 发布态）。**拖动跟手性/灵敏度体感 HIL 待实机**。`0cf01ce` 追加 `lv_obj_set_ext_click_area`（talk 12/角钮 8）；`a7e0495` 角钮 48→56px 合规恢复（PRODUCT.md:55/DESIGN.md:169 三处成文约束，钉死测试同步），最终重构建 SHA-256 `ce5a2d5fabe607c07b88b64f03dda4a2b3712b5cf22089585b82b6c336272b74`（3,018,064B）。`135cecd` 开机 ESP_LOGI 三钮实际几何（NVS 恢复后，命中归因依赖）+ 守卫测试收窄到真实意图；`0ea1cbd` 日志前补 `lv_obj_update_layout(screen)`（LV_SIZE_CONTENT 首帧前未决，同文件 2271 行先例），最终镜像 SHA-256 `e17ac8f878240c9301ac6f53005972d4b70826d91e8ea5b1da5b067efd175bf2`（3,018,464B）。**刷机事故记录**：首次误用 flash_hil_ws35.ps1（第 31 行写死 build-hil-ws35 路径）刷入旧镜像，advisory 抓获后建 `tmp/flash_touch4_ws35.ps1`（路径+日志名已改）；重刷时 COM14 进入 USJ 半死态（PnP Unknown、串口枚举消失），pnputil 无管理员权限拒绝，软件救不回——**用户拔插后看门狗自动重刷成功**（`Hash of data verified` = esptool 芯片侧摘要比对，镜像上板已证；WDT 抓包从 1.37s 加入，Touch registers/geom 两锚点在此前显示/触摸初始化窗口故未捕获——不再开 esptool 轮次补抓，读回验证被否为冗余）。HIL 被动抓包 `tmp/capture_passive_com14.py`（dtr/rts 清零不复位，30 分钟窗）已挂，geom 行折进 HIL 自身复位顺带抓。深度 review 落 `docs/kxnx/findings/29-touch-hit-geometry-review.md`。
- **工作树（2026-08-23 全链审计现取）**：HEAD `72a693a`（`feat/hutuji-usb-pipe`，与 origin 同步、工作树干净；本条为审计复核后的现态指针，下条批次明细与 `b0f29a8`/`4ce8b59` 的零接触配网实现记录保留不删）。含 R22-PIPE-02、点动 1/10mm、`$MD`、HUD/断连告警、发现收紧，以及**设备端零接触配网（协议 §1.5）**：`b0f29a8` 主体实现（新增 `plotter_provision_core.h`、`plotter_provision.{h,cc}`、`test_plotter_provision_core.py`；改 `main/CMakeLists.txt`、`hutuji_pipe.cc`、`lichuang_dev_board.cc`、`esp32-s3-touch-lcd-3.5.cc`、`main/display/lcd_display.{h,cc}`、双语 locale、`test_hutuji_recovery_core.py`），`4ce8b59` HIL 三修复（cfg.ssid 空指针写崩溃 → 指针赋值；全量扫描结果集被 esp-wifi-connect WifiStation 截胡清空 → SSID 过滤扫描防截胡；过滤扫描结果集漏收 → promiscuous 嗅探 beacon/probe response 兜底，IE 解析为 core 纯函数）。**实机 HIL 已通过**（2026-08-23 jump6 跳配闭环双侧日志 + jump5 不打扰路径二次确认，证据 `results/local-only/2026-08-23/hil-jump{5,6}-*.log`；细节与三轮失败现场见 protocol.md §1.5 状态行）。HIL  tooling 在 hub 仓 `tmp/`（`build_hil_ws35.ps1`/`flash_hil_ws35.ps1`（2026-08-23 晚起 `--after=watchdog-reset`：App 运行中 EN 硬复位间歇杀死 USJ 重枚举，RTC WDT 复位不掉 USB；复位后 esptool 关句柄撞重枚举窗口的装饰性 exit=1 由日志分类判成功）/`reset_wdt_capture.py`（免硬复位 boot 抓包，代价错过前 ~1-2s）/`reset_capture_full.py`（**已废弃**：RTS/DTR 脉冲即肇事路径）/`capture_com13.py`/`capture_com14.py`/`hil_probe.py`/`hil_phaseb_plotter.py`）。注意：ESP-IDF v6 `set-target` 会重写仓库根 `sdkconfig`（gitignored），最终根 sdkconfig 由 ws35 构建落回唤醒词状态。**审计新增登记**：源码审查实测 `hutuji_job.cc` 预取接管（`AdoptPrefetch`）在上一张成功留 buffer 且本张预取命中时不释放旧 PSRAM（无所有权交换），且预览下载成功但预取失败/中断时无 `CancelPrefetch` 补偿——重复「新画一幅」累积泄漏 ≤512KB/幅，修复待用户批准独立批次并补 host 测试；`scripts/xiaozhi_console.py` 白名单缺 `preview_url`，CLI 直调 `hutuji.draw` 现会被设备拒。
- **已入库 `72a693a`（2026-08-23 稳定性批次，已推 origin、已刷 COM14 三轮画图 HIL 通过）**：在 `4ce8b59` 之上——双板 `SetPowerSaveLevel` 门控（lichuang-dev 新增 override、waveshare 门控扩为拒绝一切非 PERFORMANCE 回落）+ 双板稳态 LOW_POWER→BALANCED 映射；`hutuji_recovery_core.h` 新增 `kDiscoverFastRetryAttempts`/`NextDiscoverBackoffMs` 纯函数（前 5 次 1s 后翻倍封顶），`hutuji_pipe.cc` 加 `miss_attempts` 计数；`hutuji_job.cc/.h` 两处修复——`WaitForAudioOutputIdle` 等待信号从 `codec output_enabled`（双工 codec 监听期永不关输出，恒吃 30s 上限）改为 speaking 态；G-code 预取从独立 xTaskCreate 任务（内存低谷静默失败，W 日志实锤）改为预览任务尾部内联（删 `PrefetchTaskEntry`）。host 131/131；三轮画图 HIL 证据 `results/local-only/2026-08-23/hil-stable-draw{,2,3}-com14.log`（0 次 LOW_POWER、ok 均值 73/72ms、确认后 10ms 复用预取进 streaming），启动核对 `hil-stable-boot{3,4,5}.log`（稳态 ps type 1），扫描兜底观察 `hil-rescan-com14.log`。**观察项进展（2026-08-23 晚）**：R3 退避已故障注入验证——写字机断电后断开秒级检测（errno 113），重试阶梯 1s×6→2/4/8/16s→30s 封顶（与 `kDiscoverFastRetryAttempts=5` 一致：第 6 次打印仍 1s 是打印先于递增），回电后自动重连 + 版本探活 + 授权探测通过，全程 WiFi 零省电档横跳（证据 `results/local-only/2026-08-23/hil-r3-backoff-com14.log`）；COM14 掉枚举已定性为 App 运行中 EN 硬复位间歇杀死 USJ 重枚举（PnP 实锤 VID_303A&PID_1001；esptool#970 / esp-idf#13287 / arduino-esp32#6762；当日 7/7 次 RTC_SW_CPU_RST 软件复位全程在线对照；`reset_capture_full.py` RTS 脉冲复位 1s 进 boot 即 PORT-LOST 实录于 `hil-stable-boot.log`）；另实测两次刷机**写入中途** `Cannot configure port`，根因未定性（板无电池排除电池掉压，拔插后同参数成功）；boot4 写字机缓存 IP 验证失败一次、子网扫描兜底接管成功（写字机当时是否短暂离网未定性）。同日另实测 S3 重启后写字机侧旧 TCP 僵尸槽位占住 :23（`MAX_TLNT_CLIENTS=1`），约 10s 自动释放并由新连接接管，属预期行为。
- **历史批次（工作树此前干净时登记）**：`7af5cbb`/`5109c9b` 打断进监听与 MQTT 会话清理（goodbye 复核、空通道无副作用、迟到 hello 丢弃）。`70f1c58` 批次 4 文件（板 cc、lcd_display.cc、settings.cc、host 测试）：FT6336 TH_GROUP 70→30（FT5x06 官方寄存器文档 + Linux EDT 20–80 区间）、indev 读定时器 33ms→10ms（`LV_DEF_REFR_PERIOD=33` 实测 + LVGL issue #8152）；复审 P1-1 断连看门狗门控早退后原地续表 + 已恢复连接不进配网、P2-1 `Settings::SetInt` 句柄守卫、P2-2 talk/wifi 点按回弹、P2-3 贴边存档回默认。host 99/99。`37be0e9` 批次 6 文件（lcd_display.{h,cc}、板 cc、双语 locale、host 测试）：「说话」大圆钮 + 「控制」「配网」角落钮、三钮按下跟随拖动 + NVS `hutuji_ui` 布局记忆、二维码「关闭」（Schedule+StopConfigAp 事件链）、断连 120s 看门狗；含实机事故修复（拖动回调 user_data 误传 `this` → LoadProhibited 白屏重启，两轮同 Backtrace `0x42026ca6` 取证，改传 state + 防回退测试钉死）。原「分页抽屉 + taskLVGL 栈 12288 + 触摸/点动调参 + WiFi 省电持有 + 测试 + DESIGN」8 文件批次已提交 `2bc04a6`。Y 限幅收紧批次已提交 `75e83d7`（`hutuji_recovery_core.h` + host 测试 + 文档）。
- **板上镜像**：COM14 当前为 `72a693a` 内容的 HIL 构建（`build-hil-ws35/xiaozhi.bin` SHA-256 `93b61d5b…afa9`）。含零接触配网全链 + 扫描段三修复 + 省电门控/退避前段/播报等待信号/预取内联。2026-08-23 晚经新 `--after=watchdog-reset` 路径同镜像重刷验证：写入+`Hash of data verified`、WDT 复位后 COM14 无需拔插、App 正常启动并接管写字机（此前 flash 中途掉口两次致 App 区被擦除半砖，由该次重刷恢复）。此前镜像 `bb7ee987…32439`（`4ce8b59`）、`303B7A77…2D54`（`c4e9c1b`）等已被覆盖。
- **当前源码验证与构建**：全 host suite **131/131**（含零接触配网 core 契约、802.11 嗅探 IE 解析、省电门控双板锚点、退避前段序列编译测试）。ESP-IDF v6.0.1 `build-hil-ws35` 已刷 COM14（SHA-256 `93b61d5b…afa9`）；门禁双板编译走 `tmp/build_gate_prov.ps1`（hub 仓）。最近一次 BLE 启用构建仍是 `build-hutuji-ble-enabled/xiaozhi.bin` 3,172,544B、SHA-256 `5a68fafe…80d6`（`CONFIG_HUTUJI_BLE_DIAGNOSTICS=y`，生命周期代码 `f1aa0e7`）。
- **取证脚本已修正**：`results/local-only/2026-08-20/serial_capture.py` 旧版靠 pyserial 默认 open，DTR/RTS 在 open/close 各复位一次板子（S3 原生 USB Serial/JTAG 把它当复位线），曾把工具复位误读成「自发重启」；现改为清 `dtr`/`rts` 后再 `open()`。同目录 `serial_reset_capture.py` 是**有意**用 RTS 发复位脉冲——但 2026-08-23 已定性 RTS（EN）脉冲会在 App 运行中间歇杀死 USJ 重枚举，复位一律改走 WDT 路径（`tmp/reset_wdt_capture.py`），该脚本仅留作历史。
- **BLE 板上窄证据与回滚（生命周期代码 `f1aa0e7`，当前 HEAD 未改该模块）**：启用镜像 `5a68fafe…80d6` 刷入 COM14 后 4 分钟 1,683 行日志无 `BLE_ERR_MEM_CAPACITY`/panic/assert/reboot，free SRAM 稳定约 22.1KiB（`minimal sram: 1647` 低于 24KiB 门槛，缺 BLE-off 基线，不判定回归）；Bleak 30s/15s 均收到目标 UUID、固定 6 字节 payload、seq 10→140、`stale=0`、无 local name/scan response。NVS 回滚读回 byte-identical（`enabled=0`），默认镜像重刷自证，10s 目标 UUID 扫描无匹配。证据在 `results/local-only/2026-08-19/`（本机，不入库）。BLE 仍 🟡：尚未完成共存量化矩阵（含 off 基线）、完整功耗/温升与阶段 B 认证 GATT。

---

## 4. fz（`D:\Users\zhugu\fz`）

### 未跟踪文件
| 文件 | 说明 |
|------|------|
| `native_sim/telnet_server_behavior.cpp` | Telnet 服务器行为 native sim 实现 |
| `native_sim/test_telnet_server_behavior.py` | 对应 pytest 测试 |

### 分支与规则（HEAD 存档并入）

- **路径 / 分支 / HEAD**：`D:/Users/zhugu/fz` / `main` / `c2bf1a3`。
- **工作树**：2 个未跟踪实验文件 `native_sim/telnet_server_behavior.cpp`、`native_sim/test_telnet_server_behavior.py`；属于用户资产，保留、不上传到 hutuji。
- **规则**：改 Grbl 代码后必须在 fz 跑 `scripts/agent_gate.py --profile standard`；本仓 `full` 只做 §9 标记检查，不能替代。

---

## 5. 当前汇总（证据快照，HEAD 存档并入）

| 仓库 | 实时 HEAD 命令 | 已知状态 |
|---|---|---|
| hutuji | `git rev-parse --short HEAD` | 日志脱敏发布 `c2cdbd4` 已部署并复验：三文件 Git blob 哈希/CR=0 对账、双服务 active、`/bridge-health` 200、preflight 30/30；生产 POST/GET/access 日志与 health pull 落盘均无原始 URL/签名值。R4 中 `server.py` G-code 计时已随本批上线，`bitmap_svg.py` 计时仍未部署生产。2026-08-23 全链审计登记：`xiaozhi_console.py` 白名单缺 `preview_url`（CLI 直调 `hutuji.draw` 会被设备拒），待用户批准独立批次修复。 |
| xiaozhi-esp32 | `git -C D:/Users/xiaozhi-esp32 rev-parse --short HEAD` | `72a693a` 已推 `feat/hutuji-usb-pipe`（origin 实际此前已到 `4ce8b59`，本条仅推 `72a693a`）；工作树干净；板上 `93b61d5b…afa9`（= `72a693a` 内容）三轮画图 HIL 通过；host 131/131。2026-08-23 全链审计登记：`hutuji_job.cc` 预取接管在上一张成功且预取命中时不释放旧 G-code PSRAM（重复新画累积泄漏），修复待独立批次，见 §3 工作树条。 |
| Grbl_Esp32 | `git -C D:/Users/Grbl_Esp32 rev-parse --short HEAD` | `fb2b3b5`（§9-E′ 已提交、fz standard pass、**2026-08-23 已烧 COM13 并启动自证**；ChipID 与 08-22 记录矛盾待澄清，见 §2）；COM13 板基线升为 `fb2b3b5`，物理并发 HIL 未复现 |
| fz | `git -C D:/Users/zhugu/fz rev-parse --short HEAD` | `c2bf1a3`；2 个用户实验文件保留；门禁须走 clang 路径（见 §2）；wokwi 可选层 2026-08-23 起受免费 CI 配额阻塞，非固件回归（见 §2） |

---

## 禁令要点（claims_forbidden，绿了也不能说）

- 可出货
- 商业放行
- §9 已完成
- §9 已修好
- 硬件已验证
- 联调完成
- 京东云已上线
- M2 已完成

---

## 同步契约

| 你改了什么 | 必须更新 |
|------------|----------|
| 跨仓脏树 | 本文件 |
| 进度 | `docs/release-readiness.md` |
| 契约 | `docs/protocol.md` |
| 结构/约定 | `AGENTS.md` + `README.md` + `CLAUDE.md` |
| 门禁口径 | `docs/agent-gate.md` + `.cursor/rules/hutuji-agent-rails.mdc` |

> 每次新增/清除工作树后立即更新。本页的静态 SHA 是证据快照，不替代实时命令；源码 HEAD 也不自动等于生产/板上运行态。
