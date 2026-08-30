# 跨仓工作树清单（2026-08-24 更新）

> 本文件记录三仓未提交资产。接手时先对清单与 `git status`；清单过期就更新清单，**不是**把别人的改动回滚掉。
> 三仓路径：hutuji `D:\Users\hutuji` ｜ Grbl `D:\Users\Grbl_Esp32` ｜ xiaozhi `D:\Users\xiaozhi-esp32` ｜ fz `D:\Users\zhugu\fz`
> 
> 铁律：**不要假设任何固件源码在本仓库**——运动控制去 Grbl_Esp32 查，AI 交互去 xiaozhi-esp32 查。
> 登记范围含脏树、未推提交与「生产/板上运行态领先 GitHub」；HEAD 每次用命令现取，**不要把本页静态快照当作当前 HEAD 或板上镜像身份。**
> 2026-08-24 合并说明：旧版全文存档于 `tmp/inventory_head.md`；以下各「HEAD 存档并入」小节为旧版证据/决策原样保留（其中日期快照以当时为准）。

---

## 1. hutuji 本仓（`D:\Users\hutuji`）

### kb-admin 发布幂等（2026-08-30 20:09，阿里云已上机）

| 项 | 说明 |
|----|------|
| 现象 | 后台点发布回 failed，「控制台 API 异常退出 code=1」 |
| 根因 | 内容 MD5 未变时小智 create 回 400「文档已存在」；`api()` 用 `sys.exit(1)`，库调用方捕不到真错误 |
| 改动 | `scripts/xiaozhi_console.py`：`_api_lib`/`_api_upload_lib`；「文档已存在」当成功。`kb-admin` 静态页失败 toast 带 `error_summary`。`test_field_tools.py` +1 |
| 生产 | 两文件 scp 阿里云 + restart `hutuji-kb-admin`；复测 SUCCESS。脚本 `tmp/deploy_kb_publish_fix.py`（不入库） |
| Git | `113b1d4` 已 push origin/main |

### bridge proxy ENOSYS 回归（2026-08-30 20:13–20:26，阿里云已修）

| 项 | 说明 |
|----|------|
| 现象 | 用户「画小猪」小派复读「云端生成服务有问题」；`conversation_events` 调 `% hutuji.draw` 非 `hutuji_draw` |
| 根因 | bridge WSS 在线但 `mcp-proxy` 未 spawn（`proxy_connected=false` 自 19:36）；磁盘 unit 缺 `SystemCallErrorNumber=ENOSYS` |
| 生产 | 补 ENOSYS 于 bridge+mcp unit；备份 `/root/hutuji-bak-20260830-bridge-enosys/`；restart bridge → 双 connected；20:28:49 首条 `tools/call` |
| 仓内 | `deploy/hutuji-{cloud-bridge,mcp}.service` + `test_contract.py` 钉 ENOSYS（本批待 push） |
| 未验证 | 修复后完整语音「画 xxx → hutuji_draw」HIL |

### 高并发 Phase-1/2 + Phase-3a + 假负载压测（2026-08-30，**`4af959d` 已双机；阿里云队列 19:04 已启用**）

| 项 | 说明 |
|----|------|
| 动机 | 用户拍板峰值 **10 画/秒** 架构 sizing；Phase-1 Redis 队列 + 独立 worker，Phase-2 OSS 产物 / 单设备限流 / 对冲降档 / 多 worker systemd；Phase-3a Postgres 账本 + breaker shadow + queue_metrics |
| 新增模块 | `mcp-server/hutuji_mcp/{draw_queue,draw_worker,output_store,job_ledger,breaker,queue_metrics}.py` |
| 修改 | `server.py`（队列入队 + ledger/breaker hooks）、`config.py`、`bitmap_svg.py`（`HUTUJI_DRAW_RACER_MAX` + breaker 采样）、`draw_queue.py`/`draw_worker.py`、`requirements.txt`（+`redis`、`oss2`、`psycopg[binary]`） |
| 部署模板 | `deploy/hutuji-draw-worker*.service/target`；`deploy/env.mcp.example` 追加队列/OSS/PG/breaker env；`deploy/preflight.sh` **EXPECTED_CHECKS=33**（+3 条件探针） |
| 压测 | `mcp-server/scripts/bench_draw_load.py`（替身 `_run_draw_job`，不调付费 API）；`tests/test_draw_queue.py` 含 subprocess smoke |
| 测试 | 本机 pytest 收集 **1068** 项（+19）；Phase-3 专测 `test_breaker.py` 3 / `test_job_ledger.py` 5 / `test_queue_metrics.py` 1；`test_draw_queue.py` 4 + `test_output_store.py` 2 全绿；bench `--rps 10 --duration 5 --workers 4 --job-ms 250` memory PASS |
| 本机 bench | `--rps 10 --duration 10 --workers 8 --job-ms 250` → success 100% PASS（memory://，早前一轮） |
| 默认行为 | **未配** `HUTUJI_DRAW_QUEUE_URL` = 进程内 asyncio；**未配** `HUTUJI_PG_DSN` = 账本禁用；**默认** `HUTUJI_BREAKER_ENFORCE=0` = shadow。阿里云主节点 2026-08-30 19:04 已配队列 URL + DSN |
| 推荐上线 env（未验证） | Phase-1/2 同前 + `HUTUJI_PG_DSN=postgresql://…`、`HUTUJI_BREAKER_ENFORCE=0`（3a shadow；3b 稳定 48h 后改 1）、`HUTUJI_METRICS_INTERVAL_SEC=15`；3c 另需 CDN DNS + `HUTUJI_CDN_ENABLED=1` |
| 设计 | `docs/design/high-concurrency-phase3.md`（3a 仓内已落地；3b preflight 已入模板；3c CDN/lifecycle **未生产验证**） |
| 生产部署（2026-08-30 17:07 CST） | 双机代码+preflight 33 项已 scp；备份 `/root/hutuji-bak-20260830-phase3-4af959d/`；当时 **未配** 队列/PG/OSS env，worker 未 enable。冒烟阿里云 8533B 公网 200/CRC `a1ca7ce8`。停京东云误启 bridge 后：阿里云 24/33、京东云 27/33 |
| 生产启用（2026-08-30 19:04 CST，仅阿里云） | Redis + `HUTUJI_DRAW_QUEUE_URL` + worker@1..4；`HUTUJI_PG_DSN` + `HUTUJI_BREAKER_ENFORCE=0`。worker unit 去掉 `SystemCallFilter`（clone3 SIGSYS 实证）。队列冒烟 9799B/431 行/0.9s。京东云仍不跑队列 |
| 生产 bench（2026-08-30 19:22 CST） | 阿里云 Redis 假负载：`10 rps×300s`、job-ms=3000、32 bench workers（停 systemd worker 隔离）→ 2977/2977 PASS；证据 `tmp/bench-redis-20260830.log`（本机不入库） |
| Git | `2710739` 已 push origin/main（worker seccomp 移除 + breaker samples 跳过 + 文档） |
| 未完成 | 3a shadow 满 48h（起算 19:04）、3b enforce、3c CDN、hub 全量门禁在本 HEAD 一次完整跑通（pytest 层曾被环境中断/600s 超时） |

### 写字机 `$1=255`（2026-08-28：NVS → 机型默认已固化并刷 COM13）
| 项 | 说明 |
|----|------|
| 源码 | `Grbl_Esp32/.../Machines/custom_3axis_hr4988.h`：`DEFAULT_STEPPER_IDLE_LOCK_TIME` 25→255（**已提交 `7f48217`**） |
| 构建/门禁 | `pio run -e release` SUCCESS；`firmware.bin` SHA-256 `8FE663BE…80E635`；fz `standard` overall=pass（llvm 22.1.8 前置） |
| 烧录 | COM13 upload `Hash of data verified`；`$$` 仍见 `$1=255`（NVS）；证据 `results/local-only/2026-08-28/grbl-dollar1-default255-com13-verify.log` |
| 早前 NVS | `$1=250`→`$1=255`+`$S`（`grbl-set-dollar1-{250,255}-com13.log`） |
| 注意 | `$RST=$` 现回落 **255**。S3 **无需**为此刷机 |

### Grbl M30/换纸 `$1=255` 失能守卫（2026-08-28 晚，**已提交 `2cecd2d` 已推 origin**）
| 项 | 说明 |
|----|------|
| 源码 | `Grbl_Esp32/src/GCode.cpp`（M30 页尾 2 处 `$1==0xff` 跳过失能）、`PaperSystem.cpp`（`paper_finish_xyz_after_fault()`：abort/Step2 超时/Step6 卡纸 + 换纸成功恢复） |
| 构建 | `pio run -e release` SUCCESS；`firmware.bin` 1858112B SHA-256 `12275654AF0D85462A4FB30403B8E19402406FA184AC95A5A3DF070C146429A5` |
| 烧录 | COM13 upload `Hash of data verified`（2026-08-28 晚） |
| 验证 | 用户确认画完后电机保持使能、出图正常；证据 `results/local-only/2026-08-28/grbl-m30-enable-hil-monitor.log` |
| fz | 本机构建 SUCCESS；fz `standard` 本机 native_sim 环境预存红（mingw/limits.h），不阻断产品结论 |

### draw-portal 买家网页传图（2026-08-29 入库 + 阿里云已部署）

| 项 | 说明 |
|----|------|
| 路径 | `draw-portal/`（FastAPI + SQLite + 静态页） |
| 部署 | `deploy/hutuji-draw-portal.service`、`deploy/env.draw-portal.example`、`deploy/nginx-draw-portal-snippet.conf` |
| 探活脚本 | `scripts/probe_device_messaging_token.py`（token 勿入库） |
| 契约 | `docs/protocol.md` §10.4、`docs/design/draw-portal-bind.md`、`docs/design/conversation-capture.md` |
| 本批（hub `781dcb3` 已推 main） | 对话上报 API + `conversation_events`；usage 埋点 + `usage_events`；`draw-admin` 会话/usage 视图；MCP `usage_report.py`；固件 `hutuji_conversation_report.*`（xiaozhi `0d041c9` 已推，[PR #1](https://github.com/zhuguang-ZFG/xiaozhi-esp32/pull/1)） |
| 生产 MCP（2026-08-30 12:24） | 双机 `server.py`/`config.py`/`usage_report.py`/`bitmap_svg.py`/`gcode_gen.py` LF blob 与 HEAD 逐位相符；阿里云 restart mcp+bridge、京东云 restart mcp；脚本 `tmp/deploy_mcp_delta_0830.py`（未入库） |
| 生产 draw-portal | 阿里云 active；venv 共用 MCP；数据 `/var/lib/hutuji/draw-portal/` |
| 后台 UI 美化（2026-08-30 14:4x 阿里云） | `draw_admin/static/index.html`（dusk 主题 + 手机卡片布局）与 `kb-admin/kb_admin/static/index.html`（prod 草稿入库 + 移动 sheet/横向滚动）已 scp；SHA-256 逐位相符、CR=0；restart `hutuji-draw-admin` + `hutuji-kb-admin`；公网 `/draw-admin/`、`/kb-admin/` **200**，页面含 `viewport-fit=cover`；脚本 `tmp/deploy_admin_ui_0830.py`（未入库） |

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
| `tmp/s3-confirm-capture-0829.log` / `tmp/grbl-confirm-capture-0829.log` / `tmp/s3-confirm-tail-0829.log` | 2026-08-29「能预览却画不了」复现抓包（COM14+COM13）；确认→预取复用→streaming→有墨；本机留档不入库 |
| `tmp/cap_confirm_hil_0829.py` / `tmp/diag_nodraw_0829.py` / `tmp/cap_s3_nodraw_0829.py` | 同日只读诊断/抓包脚本（不入库） |
| `lineart-research-report.md` | 简笔图获取方式调研报告（纯调研产物） |
| `docs/design/quickdraw-local-cache.md` | Quick Draw 本地缓存落地设计稿 v0.1（待用户批准，未实施） |
| `USER_MEMORY.md` | 用户记忆备份（跨会话持久化） |
| `memory_user.json`、`.superpowers/sdd/` | 用户记忆 JSON 备份；`.superpowers/sdd/` 为 SDD 工作流本地任务简报/报告/评审 diff（27 个文件）。`.gitignore` 第 44 行注释虽写 “Superpowers”，实际只列 `.worktrees/`，`git check-ignore -v .superpowers/` 实测 exit=1 **未被忽略**，故必须登记；保留本机不入库 |
| `tmp/` | 临时工作目录；2026-08-25 半墨调查新增 `results/local-only/2026-08-25/{tree-redraw-com14,fix-boot-com14,tree-redraw-fixed-com14}.log`（复发抓包 10 次落笔期空窗 230–640ms / 刷机后启动验证 / 修复后复测空窗 max 30ms），gitignored 留本机；**2026-08-25 补登记**构建/取证产物组：`tmp/build-gate-logs/`（19 个 .log/.err）、`tmp/build_ws35.log`、`tmp/build_gate_env.sh`、`tmp/build_instr_{lichuang,ws35}.ps1`、`tmp/build_hil_ws35.ps1`、`tmp/boot_capture.py`、`tmp/zdraw_capture.py`（出图判别取证：出图前 `$#`/`?` + 画线期 `?` 轮询，COM13 只读、dtr/rts=False 铁律），以及 `results/local-only/2026-08-25/grbl-z-baseline-com13-20260825.txt`（COM13 只读 `$`/`$#`/`?` 快照）；**2026-08-29 补登记**门禁/发布暂存组：`tmp/{gate-hub,gate-docs,gate-docs2,gate-docs-2,gate-docs-accept,gate-docs-paper,gate-docs-paper2}.{out,err}`（门禁 stdout/stderr 留档，其中 `gate-hub.{out,err}` 是 2026-08-27 被 `KeyboardInterrupt` 打断的未完成轮，**不可当通过证据**）、`tmp/{fix-refs,fix-refs2,accept-refs,accept-refs2}.{out,err}`（`--fix-line-refs` / `--accept-line-refs` 重登明细留档）、`tmp/{stage-deploy-0829,stage-scripts-0829,deploy-20260829-prompt,deploy-20260829-drift}/`（08-29 两批发布候选暂存）、`tmp/{pubdl_20260829,pubdl_drift_20260829,pub-draw,pub-draw2}.gcode`（公网真下载复核件）、`tmp/{head-deploy-manifest,head-mcp.service,head-bridge.service,node-lima-manifest,node-jdcloud-manifest,head_pkg,prod_pkg}.txt`（双机运行时包逐文件对账清单）、`tmp/dstage/`（`deploy/` 模板暂存副本，含 `.gitignore`/两个 env example/unit/两个安装脚本）、`tmp/inv_head.md`（本页 HEAD 存档另一份副本，与第 8 行提到的 `tmp/inventory_head.md` 同源）、`tmp/gate-hub-final.{out,err}`（提交前末轮 `profile=hub` 完整通过留档，逐轮覆盖同名文件；末轮 exit=0、12 层全 pass、pytest 1043 passed、指纹 238 处全对，精确时间戳以 `results/hutuji_gate_last.json` 为准）、`tmp/gate-backup/hutuji_gate_last.b7f88e30.{json,md}`（重跑前对上一轮 hub 报告的原样备份，防单槽覆盖丢证）、`tmp/preflight-{lima,jdcloud}-0829.txt`（2026-08-29 晚收口后双机 `deploy/preflight.sh` 复跑原文：阿里云 `pass=21 fail=9`（1 项 unit 系 08-23 手调 el8 + 8 项 nginx 判据锁 Debian `sites-*` 布局）、京东云 `pass=25 fail=5`（4 项 bridge 按单 WSS 热备设计不满足 + 1 项公网 `/files` 安全组 hairpin），逐项与部署当时基线相同，无回归）；**2026-08-29 补登记**实机 `$$` 取证组：`tmp/query_grbl_settings.py`（COM13 只读取证脚本，仅发 `$I`/`$$`/`?`，`dtr`/`rts` 恒 `False`，不复位/不运动/不写设置）与 `tmp/grbl-settings-com13-0829.txt`（取证原文落盘：`$130=210.000`/`$131=297.000`/`$132=200`、`$20=$21=$22=0`、固件 `[VER:1.3a.20211103:]`、`[OPT:PHBSW]`、机型 `Custom 3-Axis HR4988`、STA IP、`<Idle|MPos:0.000,0.000,0.000|Bf:249,128|FS:0,0|Pn:0>`），是 `docs/protocol.md` §5 纸面方向定案与 `docs/bringup-sop.md` 第 5 步行程钉死的唯一本机证据；均留本机不入库 |
| `mcp-server/tmp/{telnet_draw_test,two_opt_ab,capture_resilient,motion_profile_analysis}.py` | 喂流/运动实验件（F 提速 A/B 与 2-opt 排除用；2026-08-25 advisory 后已改：`telnet_draw_test.py` 送机前强制过生产 `validate_gcode`（exit 6 拒发），`two_opt_ab.py` 弃手抄步序改复用 `_vpype_command()` 仅注入 `--two-opt`）。**2026-08-29 补登记**同目录三项：`mcp-server/tmp/models.json`（上游模型清单探测快照，仅型号/模态元数据，无凭据）、`mcp-server/tmp/curated-probe/manifest.json`（入库闸干跑清单，`n_target=1` 各类 count=0，非达标证据）、`mcp-server/tmp/curated-vector-data/manifest.json`（第 49 行 230 条干净库的清单文件，库体本身已被窄忽略规则盖住）。均保留本地不入库 |
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
| `mcp-server/tests/test_quickdraw.py` | **34 项**契约/集成测试（此前 16 项；本轮补入 4 个最长子串碰撞断言 + 14 个单字类目增强短语断言 + fixture 缓存隔离）；2026-08-25 v4 批后演进为 **39 项**（+3 礼貌前缀/循环剥离/NO_STRIP 守卫），全套 **1087** 项（2026-08-30 停机宽限批 +1：worker unit TimeoutStopSec 形状钉死；2026-08-30 优雅停机批 +3：test_draw_queue.py 17→20，worker stop 事件迭代边界退出/画途中跑完当前 job/main 接线子进程回归；2026-08-30 评审修复批 +13：test_draw_queue.py 4→17，_decide_attach 决策矩阵 + Redis inflight 残留 replace/NX 冲突重判回归；此前 2026-08-30 高并发 Phase-1/2/3a +19：`test_draw_queue.py` 4、`test_output_store.py` 2、`test_breaker.py` 4、`test_job_ledger.py` 5、`test_queue_metrics.py` 1、`test_portal_settings.py` 4；2026-08-30 +6：`test_draw_limits_config.py` 3、`test_usage_report.py` 2、`test_field_tools.py` 92→93；2026-08-30 晚 +1：`文档已存在` 幂等；余下历史批次见 git 历史） |
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
- **生产运行态（2026-08-30 20:26 bridge ENOSYS 修复后）**：**阿里云 `47.112.162.80` 为主**——`hutuji-mcp`+`hutuji-cloud-bridge` 双 active（bridge **cloud/proxy 双 connected**，MainPID 871455/871459 为修复后当场值；不以静态 PID 作长期身份）；代码基线 `2710739` + kb-admin/console 热修 + **bridge unit ENOSYS 热修**（模板本批入库防再丢）。队列/worker/PG shadow 仍仅阿里云（19:04 启用）。**京东云 `117.72.118.95` 热备**：bridge disabled+inactive；回滚 = DNS 指回 CF Tunnel + 启京东云 bridge + 停阿里云 bridge。真实语音 HIL 未执行。
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

**工作树干净（2026-08-30 §9-G′ `$` 写保护已提交 `40616de` 已推 origin，[PR #1](https://github.com/zhuguang-ZFG/Grbl_Esp32/pull/1)）** — 见下条。

### §9-G′ Golden `$` 写保护（2026-08-30，**已提交 `40616de` / 已推 origin**）

| 文件 | 说明 |
|------|------|
| `Grbl_Esp32/src/ProtocolDecisionCore.h` | `is_hutuji_locked_setting_key()` |
| `Grbl_Esp32/src/ProcessSettings.cpp` | Telnet/WebUI 写入 golden 项 → `Error::SettingDisabled`(5) |

- **板上（2026-08-30 COM13 刷入）**：`firmware.bin` SHA-256 `7069D20E95DDF58DA8B4C15650FC115D636B8CD2783D2B5DE20956E76405C4E1`；`pio run -e release` + upload `Hash of data verified`。
- **实机**：`$130=200` → `error:5`；`$130` 只读 → `$130=210.000`（脚本 `tmp/test_grbl_write_lock_com13.py`）。
- **fz 门禁**：**未验证**。

**工作树干净（2026-08-28 晚 M30/换纸 `$1=255` 守卫已提交）** — 历史见下条。

### 板上基线与验证边界（HEAD 存档并入）

- **路径 / 分支 / HEAD**：`D:/Users/Grbl_Esp32` / `fix/panel-hold-after-align-116687e` / **`2cecd2d`**（源码；§9-G′ 写保护为工作树未提交改动）；COM13 板上镜像 SHA **`7069D20E…05C4E1`**（2026-08-30 刷，取代 `12275654…429A5`）。
- **有证据的板上行为基线**：`e3b1cad`（2026-08-15 烧录与启动自证）；源码后续 `847b564` 增加 §9-D′ 蓝牙功率，**没有本轮烧录证据，不能把源码 HEAD 写成板上基线**。
- **2026-08-22 换新写字机（COM13，CH340）**：烧前已完整取证并备份——原厂整片镜像 `results/local-only/2026-08-22/new-plotter-factory-backup.bin`（4MB，esptool read_flash）、串口证据 `new-plotter-serial-evidence.log`（烧前 `$I`/`$$`/`$102`/`$130`/`$131`：厂商默认 `$130/$131=200/200`；烧后 `$$` 快照为 fork 机型默认 `$130=210/$131=297`，`$20=0` 软限关闭，云端 190/190 限幅不受影响；X 长边/短边仍待装纸后单轴点动实测量尺）。烧入 app-only `0x10000`（`e3b1cad` 经 worktree 重建 `release`，PlatformIO SUCCESS，esptool `Hash of data verified`），未整片擦写，授权 NVS 保留（启动日志 `[License] Authorized (restored from NVS)`，ChipID `00002C876AE9BFB4`）。注意：本机 scoop mingw 16.1 对 fz 门禁不可用——rt_v14 的 `pthread.h`→`limits.h` 头链把 Grbl `src/limits.h` 经 `include_next` 卷入系统链（`-iquote` 下同样触发，实测复现），且该 winlibs 构建不附 libasan/libubsan（mingw-gcc 无 ASan 属社区已知，msys2/MINGW-packages#3163）；**解法：PATH 前置 scoop llvm `22.1.8/bin`（fz `find_compiler` 原生优先 clang++），fz `standard` 实测 overall=pass**（`wokwi_startup` 可选云层红，用 `.pio` 旧镜像、不含 E′，按仓规不阻断）。故新机用已验证基线 `e3b1cad` 而非当时的 HEAD；E′（`fb2b3b5`）已过 fz standard host SIL，**未烧录、未实机复现物理并发**，板上基线不变。配网走 AP 模式（`GRBL_ESP`，WebUI/命令行），静态 IP 待配网完成后按实读网段写 `.13`。
- **2026-08-23 烧录 `fb2b3b5`（COM13，用户批准后执行）**：`pio run -e release` 重建 SUCCESS（仅重编 PaperSystem.cpp/WebSettings.cpp，与 E′ 触碰面一致；新 bin 1857936 字节，取代 08-15 旧镜像）→ `pio run -t upload --upload-port COM13` app-only `0x10000`，esptool `Hash of data verified`，RTS 硬复位。启动自证 `results/local-only/2026-08-23/fb2b3b5-com13-boot.log`（抓取脚本 `tmp/cap_fb2b3b5_com13.py`）：`Grbl_ESP32 Ver 1.3a`、paper system vM716 ready、`[License] Authorized (restored from NVS)`、连上 `ChinaNet-jJmz` 获 `192.168.1.13`、HTTP/TELNET:23 起来，无崩溃重启。**矛盾待澄清**：本次启动自报 ChipID（efuse MAC）`0000FC7742C63C94`，与 §2 上文 08-22 记录的 `00002C876AE9BFB4` 不一致（本地证据文件中检索不到后者出处），COM 号漂移或多台写字机皆有可能——物理身份需用户确认；可确认的是「今天 COM13 这块板」已跑 `fb2b3b5` 且授权/.13 网络身份正常。物理并发复现（E′ 的 HIL）仍未做。**wokwi 同日登记**：复跑 `run_wokwi_smoke.py`（新镜像已就位）失败根因为 Wokwi 免费版月度 CI 配额耗尽（`API Error: ... Free plan monthly CI minute quota`，serial 0 行），属外部阻塞非固件回归；该可选层本月不可跑，不阻断产品门禁。
- **验证边界**：核心 §9、归位、自动换纸有历史证据；后续源码变化须另跑 fz、构建、烧录与启动身份核验。
- **观察项（2026-08-23，无抓包证据）**：写字机经 `[ESP110]AP`+`[ESP444]RESTART` 切入出厂 AP 后稳定在线约 1 小时（jump1–jump4 窗口串口自证），随后在两次抓包间隙自行回到 STA `192.168.1.13`（jump5 时 S3 缓存 IP 命中并授权通过）。AP 模式是否存在自发回退未定性——同期窗口有人肉拔插 S3 USB 的物理操作，不排除误碰写字机供电；后续若再现需在抓包覆盖下复测。

---

## 3. xiaozhi-esp32（`D:\Users\xiaozhi-esp32`）

### Grbl `$` 设置指纹 + 对话上报（2026-08-30，**已提交 `0d041c9` / 已推 origin，[PR #1](https://github.com/zhuguang-ZFG/xiaozhi-esp32/pull/1)**）

| 文件 | 说明 |
|------|------|
| `main/boards/lichuang-dev/hutuji_recovery_core.h` | golden 表 + `ParseGrblSettingLine`（含 `Off/On`） |
| `main/boards/lichuang-dev/hutuji_pipe.{h,cc}` | 授权后 13 项只读指纹；`grbl_settings_ok` / mismatch |
| `main/boards/lichuang-dev/hutuji_job.cc` | confirm/出图/试笔/点动拒画；status JSON 字段 |
| `main/boards/lichuang-dev/hutuji_conversation_report.{h,cc}` | 对话上报（Idle 冲刷） |
| `main/application.cc` | 唤醒词 speaking 期关自打断 |
| `scripts/tests/test_hutuji_recovery_core.py` | +指纹 host 单测 |

- **板上 COM14（2026-08-30）**：`build-hil-ws35/xiaozhi.bin` SHA-256 `1555415246ADF3DF4CBD42ADDE53B53DF9C82627745BD78219972CBA56C1BA62`；抓包 `tmp/settings-fp-fixed-com14.log`：`Grbl 设置指纹通过（13 项）`。
- **首刷 bug**：`Errors/Verbose=Off` 未解析 → 已修并重刷（同 SHA 系增量构建）。

### DRAW-BIND 绑机固件（2026-08-29，**已随 `0d041c9` 入库并推 origin**）

| 文件 | 说明 |
|------|------|
| `main/boards/lichuang-dev/hutuji_draw_bind.{h,cc}` | 绑机 QR/announce；单任务 `hutuji_bind` 栈 8192（修 COM14 `hutuji_bind_pol` 栈溢出） |
| `main/display/lcd_display.{h,cc}` | 维护页「绑定呼图账号」可见性；预览 UI |
| `main/boards/waveshare/esp32-s3-touch-lcd-3.5/esp32-s3-touch-lcd-3.5.cc` | 挂 `StartDrawBind` |
| `main/boards/lichuang-dev/hutuji_job.cc` | （若本批有改动则登记） |
| `main/ota.cc` | OTA 解析 `messaging` 段写 NVS（待刷机验证） |
| 证据 | `tmp/bind-reboot-com14.log`（旧镜像栈溢出）；绑机栈修复后 announce 200 无重启 |

**板上**：绑机+预览+屏上确认出图已 HIL；messaging token 自动写入仍靠运维。**勿与 hub draw-portal 代码提交混为一谈**——固件在外仓单独 commit。

**工作树（2026-08-29）**：DRAW-BIND 绑机固件脏树见上表；历史语音/触摸/稳定性批次见下「批次登记」小节。

### 批次登记与板上镜像（HEAD 存档并入）

- **已入库 `67507c6`（2026-08-28 预取 PSRAM 泄漏修复，已推 origin/feat/hutuji-usb-pipe，未刷机）**：`AdoptPrefetch` 在 `prefetch_mutex_` 锁内、Ready+URL 核验后先 `heap_caps_free` 旧 `buffer_` 再接管——上一张成功留存（`buffer_replayable_`）+ 命中预取的重复新画每次漏 ~512KiB（08-24 审计登记项，§5 xiaozhi 行原「修复待独立批次」就此关闭）；host 138/138 含新增 `test_prefetch_adopt_frees_old_psram_buffer` 钉死释放顺序。刷机随下一联调日（runbook 附 C）。
- **已入库 `5876c13`（2026-08-28 唤醒/Grbl 主屏可视性批，已推 origin/feat/hutuji-usb-pipe，已刷 COM14）**：用户主诉「和小派说话时不知道有没有被唤醒、写字机在不在线」。四文件（`grobot_eyes.cc`/`lcd_display.{h,cc}`/`test_hutuji_recovery_core.py`）四路指示——①Grobot 脸聆听 3s/周 0.90 连续扫光（说话 1.5s、空闲 6s/34% 之间，配合瞳孔放大 18%）；②`SetStatus` 状态胶囊聆听=绿/连接中=柔和色，聆听判定收敛局部布尔（`status_listening_` 缓存）；③`AccentDriftTimerCb` 聆听时说话大圆钮+呼吸光晕整钮切绿（其余 3 钮品牌漂移不变）；④顶栏右图标组最左 Grbl 三级圆点（绿=在线就绪已授权/琥珀=连上未就绪未授权/灰=离线；`EnsureGrblStatusDot` + 500ms timer 读 `Pipe` 原子量，与手动页 HUD 同源；**WeChat/普通双 SetupUI 分支均挂**——Waveshare 编 WeChat 分支、lichuang 编普通分支，漏挂一即某板主屏永久无指示）。host **137/137**（两处旧源码断言改钉新合同 + 新增 `test_grbl_status_dot_on_topbar_three_levels`）；ESP-IDF v6.0.1 `build-waveshare-35/xiaozhi.bin` 3,020,608B SHA-256 `b33e1c3f…0f49`；构建脚本 `tmp/build_ws35_wake_indicators.ps1`（本机不入库；PATH 无 python 是 export.ps1 前置失败的根因，脚本内显式前置 idf6.0_py3.13_env）；clang-format 本机无安装未跑。**刷机**：`tmp/flash_wake_indicators_ws35.ps1`（复制 flash_hil_ws35 改镜像路径/日志名）写 1,793,961B `Hash of data verified` + WDT 复位、COM14 免拔插；复位后 25s 被动抓包 304 行（`results/local-only/2026-08-28/wake-indicators-postflash-com14.log`）无 panic/assert、HutujiPipe fresh `Idle` 且随后正常灌流作画。**屏幕指示观感 HIL 待用户目视**（唤醒绿钮/绿胶囊/聆听扫光辨识度、圆点三级变色）。提交后工作树干净（tmp 脚本/构建目录忽略）。
- **已入库 `c3cf3d7`（2026-08-28 顶栏/状态胶囊 z-order 修复，已推 origin、已刷 COM14，HIL 已关闭）**：`5876c13` 刷机后用户实锤「主屏看不到圆点」——WeChat SetupUI 分支栏先建、`InitializeEmotionUi` 后建 460x300 整屏脸，顶栏/状态胶囊（连网络/电池图标）全被脸压在下；该函数旧注释自称「状态栏独立叠在最前层」从未兑现（普通分支栏在脸后创建所以 lichuang 没事）。修复：脸定尺寸后两栏 `move_foreground` 抬到脸上，顶栏先 `lv_obj_set_parent` 到 screen（z-order 只在兄弟间生效）再抬并补 TOP_MID 对齐。host 137/137（dot 测试 +3 z-order 钉）；镜像 3,020,672B SHA-256 `31e3f3e5…2376` 写入校验 + WDT 复位；抓包 `results/local-only/2026-08-28/wake-indicators-zorder-postflash-com14.log` 无 panic、pipe fresh `Idle`、唤醒状态迁移正常。**用户照片确认（11:51）**：WiFi/电池图标、状态胶囊、Grbl 绿点均上屏；聆听态变色/扫光未留照片证据，用户 2026-08-28 目视验收后指示关闭。
- **已入库 `a1afd2a`（2026-08-24 P1 固件批，已推 origin/feat/hutuji-usb-pipe，经 `git merge-base --is-ancestor a1afd2a origin/feat/hutuji-usb-pipe` 核实）**：pipe 解析 `[ESP901]` 三字段（Paper/MotorEn/PanelHold，与 Changing 同守卫、Unknown fail-open）；`StatusJson` 只读暴露遥测（禁发命令防孤儿 ok 在途漂移，host 源码断言）；遥测刷新走 `Preview()`/出图前预检消费式；**出图前预检**（[ESP901] 消费式遥测 + Changing=On 守卫；Paper 字段仅上云、fail-open，S3 侧无缺纸出图闸——「Paper=No 早退」旧表述 2026-08-25 源码复核撤正）；换纸 90s 补 30s 周期播报；**预览同参数重入幂等**（P1-3 配套，回 `previewing` 非「正忙」）。**幂等谓词 fixup（未提交，工作树 hutuji_job.cc + test）**：判据从 `awaiting_confirmation_.load()`（要等 PNG 下载完才 true，主路径不成立）改为「已在预览流程中」（`state_ == "previewing" || "awaiting_confirmation"`，取快照于 `state_mutex_` 下），host 源码断言钉死。host **135/135**（+4 契约）、ESP-IDF v6.0.1 双板构建通过（fixup 后重建 `ALL DONE`）、clang-format 干净；**未刷机**（板上仍 `93b61d5b…afa9`）。
- **已入库 `12b6751`（2026-08-26 触摸第四轮：拖动跟手性，已推 origin/feat/hutuji-usb-pipe，未刷机）**：`main/display/lcd_display.cc` 单文件双修复——①`SpiLcdDisplay` taskLVGL `priority` 1→4（上游 2025-03 遗留低于组件默认；audio_output(4)/opus_codec(2) 不绑核落核 1 反复插队 PRESSING 事件与重绘）；②`AccentDriftTimerCb` 指针按下期间整帧跳过（`lv_indev` 迭代判定；每 100ms 色相呼吸+阴影脉动常载渲染管线，拖动重绘排动画帧后）。用户体感「不怎么跟随手指」+ 08-21 诊断镜像芯片层排除结论为取证链。host **135/135**、clang-format dry-run 过；ESP-IDF v6.0.1 `build-touch4-ws35/xiaozhi.bin` 3,018,016B SHA-256 `c5bada19d46a2289ddc19e31df4d581e7717fc4cd60467280173be6ff94a76db`；构建脚本 `tmp/build_touch4_ws35.ps1`（复制 build_hil_ws35 改 build 目录名，仓库根 sdkconfig 会被重写为 waveshare 发布态）。**拖动跟手性/灵敏度体感 HIL 待实机**。`0cf01ce` 追加 `lv_obj_set_ext_click_area`（talk 12/角钮 8）；`a7e0495` 角钮 48→56px 合规恢复（PRODUCT.md:55/DESIGN.md:169 三处成文约束，钉死测试同步），最终重构建 SHA-256 `ce5a2d5fabe607c07b88b64f03dda4a2b3712b5cf22089585b82b6c336272b74`（3,018,064B）。`135cecd` 开机 ESP_LOGI 三钮实际几何（NVS 恢复后，命中归因依赖）+ 守卫测试收窄到真实意图；`0ea1cbd` 日志前补 `lv_obj_update_layout(screen)`（LV_SIZE_CONTENT 首帧前未决，同文件 2271 行先例），最终镜像 SHA-256 `e17ac8f878240c9301ac6f53005972d4b70826d91e8ea5b1da5b067efd175bf2`（3,018,464B）。**刷机事故记录**：首次误用 flash_hil_ws35.ps1（第 31 行写死 build-hil-ws35 路径）刷入旧镜像，advisory 抓获后建 `tmp/flash_touch4_ws35.ps1`（路径+日志名已改）；重刷时 COM14 进入 USJ 半死态（PnP Unknown、串口枚举消失），pnputil 无管理员权限拒绝，软件救不回——**用户拔插后看门狗自动重刷成功**（`Hash of data verified` = esptool 芯片侧摘要比对，镜像上板已证；WDT 抓包从 1.37s 加入，Touch registers/geom 两锚点在此前显示/触摸初始化窗口故未捕获——不再开 esptool 轮次补抓，读回验证被否为冗余）。HIL 被动抓包 `tmp/capture_passive_com14.py`（dtr/rts 清零不复位，30 分钟窗）已挂，geom 行折进 HIL 自身复位顺带抓。深度 review 落 `docs/kxnx/findings/29-touch-hit-geometry-review.md`。
- **工作树（2026-08-23 全链审计现取）**：HEAD `72a693a`（`feat/hutuji-usb-pipe`，与 origin 同步、工作树干净；本条为审计复核后的现态指针，下条批次明细与 `b0f29a8`/`4ce8b59` 的零接触配网实现记录保留不删）。含 R22-PIPE-02、点动 1/10mm、`$MD`、HUD/断连告警、发现收紧，以及**设备端零接触配网（协议 §1.5）**：`b0f29a8` 主体实现（新增 `plotter_provision_core.h`、`plotter_provision.{h,cc}`、`test_plotter_provision_core.py`；改 `main/CMakeLists.txt`、`hutuji_pipe.cc`、`lichuang_dev_board.cc`、`esp32-s3-touch-lcd-3.5.cc`、`main/display/lcd_display.{h,cc}`、双语 locale、`test_hutuji_recovery_core.py`），`4ce8b59` HIL 三修复（cfg.ssid 空指针写崩溃 → 指针赋值；全量扫描结果集被 esp-wifi-connect WifiStation 截胡清空 → SSID 过滤扫描防截胡；过滤扫描结果集漏收 → promiscuous 嗅探 beacon/probe response 兜底，IE 解析为 core 纯函数）。**实机 HIL 已通过**（2026-08-23 jump6 跳配闭环双侧日志 + jump5 不打扰路径二次确认，证据 `results/local-only/2026-08-23/hil-jump{5,6}-*.log`；细节与三轮失败现场见 protocol.md §1.5 状态行）。HIL  tooling 在 hub 仓 `tmp/`（`build_hil_ws35.ps1`/`flash_hil_ws35.ps1`（2026-08-23 晚起 `--after=watchdog-reset`：App 运行中 EN 硬复位间歇杀死 USJ 重枚举，RTC WDT 复位不掉 USB；复位后 esptool 关句柄撞重枚举窗口的装饰性 exit=1 由日志分类判成功）/`reset_wdt_capture.py`（免硬复位 boot 抓包，代价错过前 ~1-2s）/`reset_capture_full.py`（**已废弃**：RTS/DTR 脉冲即肇事路径）/`capture_com13.py`/`capture_com14.py`/`hil_probe.py`/`hil_phaseb_plotter.py`）。注意：ESP-IDF v6 `set-target` 会重写仓库根 `sdkconfig`（gitignored），最终根 sdkconfig 由 ws35 构建落回唤醒词状态。**审计新增登记**：源码审查实测 `hutuji_job.cc` 预取接管（`AdoptPrefetch`）在上一张成功留 buffer 且本张预取命中时不释放旧 PSRAM（无所有权交换），且预览下载成功但预取失败/中断时无 `CancelPrefetch` 补偿——重复「新画一幅」累积泄漏 ≤512KB/幅，修复待用户批准独立批次并补 host 测试；`scripts/xiaozhi_console.py` 白名单缺 `preview_url`，CLI 直调 `hutuji.draw` 现会被设备拒。
- **已入库 `72a693a`（2026-08-23 稳定性批次，已推 origin、已刷 COM14 三轮画图 HIL 通过）**：在 `4ce8b59` 之上——双板 `SetPowerSaveLevel` 门控（lichuang-dev 新增 override、waveshare 门控扩为拒绝一切非 PERFORMANCE 回落）+ 双板稳态 LOW_POWER→BALANCED 映射；`hutuji_recovery_core.h` 新增 `kDiscoverFastRetryAttempts`/`NextDiscoverBackoffMs` 纯函数（前 5 次 1s 后翻倍封顶），`hutuji_pipe.cc` 加 `miss_attempts` 计数；`hutuji_job.cc/.h` 两处修复——`WaitForAudioOutputIdle` 等待信号从 `codec output_enabled`（双工 codec 监听期永不关输出，恒吃 30s 上限）改为 speaking 态；G-code 预取从独立 xTaskCreate 任务（内存低谷静默失败，W 日志实锤）改为预览任务尾部内联（删 `PrefetchTaskEntry`）。host 131/131；三轮画图 HIL 证据 `results/local-only/2026-08-23/hil-stable-draw{,2,3}-com14.log`（0 次 LOW_POWER、ok 均值 73/72ms、确认后 10ms 复用预取进 streaming），启动核对 `hil-stable-boot{3,4,5}.log`（稳态 ps type 1），扫描兜底观察 `hil-rescan-com14.log`。**观察项进展（2026-08-23 晚）**：R3 退避已故障注入验证——写字机断电后断开秒级检测（errno 113），重试阶梯 1s×6→2/4/8/16s→30s 封顶（与 `kDiscoverFastRetryAttempts=5` 一致：第 6 次打印仍 1s 是打印先于递增），回电后自动重连 + 版本探活 + 授权探测通过，全程 WiFi 零省电档横跳（证据 `results/local-only/2026-08-23/hil-r3-backoff-com14.log`）；COM14 掉枚举已定性为 App 运行中 EN 硬复位间歇杀死 USJ 重枚举（PnP 实锤 VID_303A&PID_1001；esptool#970 / esp-idf#13287 / arduino-esp32#6762；当日 7/7 次 RTC_SW_CPU_RST 软件复位全程在线对照；`reset_capture_full.py` RTS 脉冲复位 1s 进 boot 即 PORT-LOST 实录于 `hil-stable-boot.log`）；另实测两次刷机**写入中途** `Cannot configure port`，根因未定性（板无电池排除电池掉压，拔插后同参数成功）；boot4 写字机缓存 IP 验证失败一次、子网扫描兜底接管成功（写字机当时是否短暂离网未定性）。同日另实测 S3 重启后写字机侧旧 TCP 僵尸槽位占住 :23（`MAX_TLNT_CLIENTS=1`），约 10s 自动释放并由新连接接管，属预期行为。
- **历史批次（工作树此前干净时登记）**：`7af5cbb`/`5109c9b` 打断进监听与 MQTT 会话清理（goodbye 复核、空通道无副作用、迟到 hello 丢弃）。`70f1c58` 批次 4 文件（板 cc、lcd_display.cc、settings.cc、host 测试）：FT6336 TH_GROUP 70→30（FT5x06 官方寄存器文档 + Linux EDT 20–80 区间）、indev 读定时器 33ms→10ms（`LV_DEF_REFR_PERIOD=33` 实测 + LVGL issue #8152）；复审 P1-1 断连看门狗门控早退后原地续表 + 已恢复连接不进配网、P2-1 `Settings::SetInt` 句柄守卫、P2-2 talk/wifi 点按回弹、P2-3 贴边存档回默认。host 99/99。`37be0e9` 批次 6 文件（lcd_display.{h,cc}、板 cc、双语 locale、host 测试）：「说话」大圆钮 + 「控制」「配网」角落钮、三钮按下跟随拖动 + NVS `hutuji_ui` 布局记忆、二维码「关闭」（Schedule+StopConfigAp 事件链）、断连 120s 看门狗；含实机事故修复（拖动回调 user_data 误传 `this` → LoadProhibited 白屏重启，两轮同 Backtrace `0x42026ca6` 取证，改传 state + 防回退测试钉死）。原「分页抽屉 + taskLVGL 栈 12288 + 触摸/点动调参 + WiFi 省电持有 + 测试 + DESIGN」8 文件批次已提交 `2bc04a6`。Y 限幅收紧批次已提交 `75e83d7`（`hutuji_recovery_core.h` + host 测试 + 文档）。
- **板上镜像**：COM14 当时为 `72a693a` 内容的 HIL 构建（`build-hil-ws35/xiaozhi.bin` SHA-256 `93b61d5b…afa9`）；**2026-08-28 起板上为 `c3cf3d7`**（含触摸第四轮链与主屏可视性，经用户照片确认，见 §3 157-158 行）。含零接触配网全链 + 扫描段三修复 + 省电门控/退避前段/播报等待信号/预取内联。2026-08-23 晚经新 `--after=watchdog-reset` 路径同镜像重刷验证：写入+`Hash of data verified`、WDT 复位后 COM14 无需拔插、App 正常启动并接管写字机（此前 flash 中途掉口两次致 App 区被擦除半砖，由该次重刷恢复）。此前镜像 `bb7ee987…32439`（`4ce8b59`）、`303B7A77…2D54`（`c4e9c1b`）等已被覆盖。
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
| hutuji | `git rev-parse --short HEAD` | 本仓 HEAD 以左列命令现取（08-29 起为 docs-only 证据登记提交，与 `origin/main` 同步；此处不钉 SHA 防自指陈旧）；**生产现役应用基线 `b7f88e3`**（2026-08-29 晚生产漂移收口并复验：运行时包 `hutuji_mcp/` 全 22 文件与 HEAD blob SHA-256 双机逐位相符、CR=0，见 deployment.md 首条；前基线 `befba0a` 为 08-29 上午登记）：冒烟「画一只猫」curated hit 零付费（不注入设备 token 故不推屏），公网真下载 8923B SHA 相符、`/mcp` 404，双服务 active（阿里云 mcp+bridge MainPID 422803/422823，京东云仅 mcp MainPID 359004）。**已关闭的滞后登记**：`preview_url` 白名单 2026-08-24 `55a8d0a` 已修；`c2cdbd4` 之后 13 个服务端 commit 已于 08-28 `34b44d4` 全部部署，两处登记均已关闭。 |
| xiaozhi-esp32 | `git -C D:/Users/xiaozhi-esp32 rev-parse --short HEAD` | `72a693a` 已推 `feat/hutuji-usb-pipe`（origin 实际此前已到 `4ce8b59`，本条仅推 `72a693a`）；工作树干净；板上当时 `93b61d5b…afa9`（= `72a693a` 内容）三轮画图 HIL 通过；host 131/131（2026-08-28 起板上为 `c3cf3d7`，host 已 137/137，见 §3）。2026-08-23 全链审计登记：`hutuji_job.cc` 预取接管在上一张成功且预取命中时不释放旧 G-code PSRAM（重复新画累积泄漏），修复待独立批次，见 §3 工作树条。 |
| Grbl_Esp32 | `git -C D:/Users/Grbl_Esp32 rev-parse --short HEAD` | **`2cecd2d`**（§9-F′ M30/换纸 `$1=255` 守卫 + `7f48217` 机型默认）；COM13 板上 SHA `12275654…429A5`（2026-08-28 晚）；用户确认使能/出图正常 |
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

---

## 评审修复批：draw_queue inflight 残留（2026-08-30，全面评审发现 #1）

| 文件 | 说明 |
|------|------|
| `mcp-server/hutuji_mcp/draw_queue.py` | 修 single-flight 残留窗：新增 `_decide_attach(existing, status)` 纯函数（attach/create/replace 单源决策，两后端共用）；Redis `attach_or_enqueue` 重写为有界重试环——inflight 指向 failed 或 job_key 已消失（JOB 600 < INFLIGHT 620 的 20s 窗口、worker 崩在 mark_failed 与 clear_inflight 之间）时比较后删残留重新入队，不再把重试钉到死任务上；NX 冲突后重判 winner 死活。TTL 保持 600/620：replace 已兜底残留，反转无害，缩短 inflight 反而引入处理中重复付费生成（注释钉死）。限深/限流首判后只扣一次；「首轮扣配额后仍 attach 到并发 winner」为已记录已知不对称（仅真并发竞争发生，额度软限制） |
| `mcp-server/tests/test_draw_queue.py` | 4→17 项：`_decide_attach` 决策矩阵 8 项参数化 + `_FakeRedis` 桩回归 5 项（残留 replace × 2、存活搭车、NX 冲突重判 × 2）。memory 后端无 TTL 无法复现窗口，Redis 决策环走桩测（advisory 裁决口径） |
| `AGENTS.md`、`docs/worktree-inventory.md` | 计数同步 1070→1083（自洽测试 `test_test_count_claims_self_consistent` 同源钉死） |
| 验证状态 | pytest **1083/1083**（94.02s）；hub 门禁 **PASS exit=0**（2026-08-30，指纹 238/238 重登后，两处变更均对应本批有意编辑）；e2e PASS。**已部署生产**（2026-08-30 22:07 双机，提交 `811426e` 已推 origin）：双机 draw_queue.py 与 HEAD blob SHA-256 `23120277b4a7…50a0c861` 逐位相符、CR=0；阿里云 restart mcp+bridge+draw-worker@{1..8}、京东云仅 mcp；阿里云生产 venv 真 Redis 命名空间冒烟三场景全过（`ATTACH_REPLACE_SMOKE_OK`），bridge-health 双 connected；preflight 阿里云 24/33（9 fail nginx 布局类已知差异）、京东云 28/33（5 fail 热备预期分歧），详见 deployment.md 首条 |
| 本机运维脚本（不入库） | `tmp/deploy_drawqueue_fix_0830.py`（双机部署 + 真 Redis 命名空间冒烟）、`tmp/preflight_both_0830.py`（双机 preflight 运行器）、`tmp/prod_recon_0830.py`（阿里云生产问题排查：journal/队列/对话/埋点）：按既有决策本机解析 `VPS.txt` 凭据，从未 add/commit/push（`git log --all` 双路径为空取证），同 `tmp/deploy_mcp_delta_0830.py` 先例 |

## 优雅停机批：draw_worker SIGTERM 语义（2026-08-30 晚，22:31 recon 发现项闭环）

| 文件 | 说明 |
|------|------|
| `mcp-server/hutuji_mcp/draw_worker.py` | `run_forever(stop=None)` 支持停止事件：循环改「迭代边界观察 stop」，画途中跑完当前付费 job 再退出；`main()` 用 `loop.add_signal_handler(sig, stop.set)` 替代 `loop.stop`（旧路径 `run_until_complete` 抛 RuntimeError → 协程悬挂销毁 → systemd 记 exit=1）。**事故注记**：`0e2fb3d` 首版误删 `_install_secret_redaction()` 与 `loop = asyncio.new_event_loop()` 两行（edit 范围错位），22:57 部署后 worker 全量 NameError 崩溃回环约 4 分钟；`3efec02` 补回并加子进程回归钉死 |
| `mcp-server/tests/test_draw_queue.py` | 17→20 项：停机后干净返回、画途中置 stop 跑完当前 job、main 接线子进程回归（缺队列地址须 SystemExit 文案、不得 Traceback——防同类误删） |
| 验证状态 | pytest **1086/1086**；hub 门禁全绿（指纹重登 2 处均为本批有意计数编辑）。**已部署生产**（2026-08-30 23:01，提交 `3efec02` 已推 origin）：双机 draw_worker.py 与 HEAD blob SHA-256 `93230e02d3b7…fa6ff89f` 逐位相符、CR=0；阿里云 restart worker@{1..8} 全 active，京东云仅同步文件（不跑队列不重启）；**优雅停机生产实证**——二次重启（新码对新码）journal 现「收到停止事件，退出消费循环」→ systemd `Succeeded`/`Result=success`/`ExecMainStatus=0`；备份 `/root/hutuji-bak-20260830-worker-graceful/`（备份先行 + 幂等防覆写），详见 deployment.md 首条 |
| 本机运维脚本（不入库） | `tmp/deploy_worker_graceful_0830.py`（双机部署 + 重启前接线冒烟——空 `HUTUJI_DRAW_QUEUE_URL` 起 `python -m hutuji_mcp.draw_worker` 须见「未配置」且无 Traceback，防 NameError 类事故直达生产）；凭据处理同上节先例 |

## 停机宽限 + 仓库卫生批（2026-08-30 晚，advisory + 评审 findings 裁决）

| 文件 | 说明 |
|------|------|
| `deploy/hutuji-draw-worker{,@}.service` | 补 `TimeoutStopSec=300`：优雅停机只置事件，默认 90s 宽限会让长 job 照样被 SIGKILL；300s 为保守取值（付费全链路上界未实测，当日 5 个生产 job 全为缓存快路径约 2s）且 < JOB_TTL 600s（advisory 指出，原部署只证明了空闲 worker 干净退出） |
| `mcp-server/tests/test_contract.py` | +1 项（606→607）：`test_worker_units_grant_stop_grace_longer_than_paid_job` 钉两 unit 恰好一条 TimeoutStopSec、240≤v<600；全套 1086→**1087** |
| `.gitignore` | 窄规则 `tmp/*password*`（finding #1：`tmp/pg-password-20260830-phase3-rollout.txt` 明文 PG 密码此前未被忽略，`git add -A` 会入库；git 历史为空取证从未入库；`check-ignore`+`add -An` 干跑实测生效）。文件留原位不动——6 个本机 rollout 脚本引用该路径，且与 quickdraw/curated「本机留档+窄规则」先例同口径 |
| `tmp/prod_recon_0830.py`（本机不入库） | finding #2/#3 修复：删除本地手写 token 正则，输出统一走 `hutuji_mcp.redact._redact_secret_text` + 能力 token 打码（同 `scripts/cloud_health_pull.py` 口径）；对话内容同管道过脱敏。实测 `token=abc123`→`<redacted>`、能力文件名打码正常 |
| 验证状态 | pytest **1087/1087**；hub 门禁全绿（指纹重登 2 处均为本批有意计数编辑）。finding #4（paramiko AutoAddPolicy TOFU）维持既有先例记为已知风险不阻塞；#5 登记面 OK 无动作；#7 评审方未读生产内容，不构成生产结论 |
