# 28 · 五切片全链复核收口（2026-08-24）

> 来源：2026-08-24 并发 5 切片 code review（A 喂流节奏 / B 生成链 / C 换纸与 abort /
> D 状态与网络 / E 仓库卫生），参照系 = 奎享 Java 反编译 + 派曦 inkscape_px Python。
> 行号基准：本仓 HEAD `a34f92c`；Grbl `D:/Users/Grbl_Esp32`；S3
> `D:/Users/xiaozhi-esp32/main/boards/lichuang-dev`；奎享
> `D:/GitHub/kxnx/kx/tools/dump_agent/decompiled_full/`。
> 标注【亲读】= 主代理逐行核过。本轮代码锚点共 33 处（28 处门禁可见 + 5 处 `.java` 门禁面外，
> 见 §6），**全部亲读**；【scout】= 侦察代理证据。
>
> **结论先行**：本轮唯一确证的**行为缺陷**是 F1（换纸期 abort 的用户可见承诺与实际能力
> 错配，见 §1），落点在 §9 白名单外且涉及机构安全，故**只登记、不动作，请用户裁决**。
> 「有意差异」（§4）。仓库卫生 1 项 HIGH 已修文档并经窄规则封堵（§5）。代码零改动。

## 一、F1（确证 · 待裁决）换纸期 abort：承诺「完成后即停」但该路径确实能停

### 1.1 链路（9 处锚点全亲读）

Grbl 侧 0x18 在换纸阻塞期**能**触发停机，链路完整：

| 步 | 锚点【亲读】 | 事实 |
|---|---|---|
| 1 | `Serial.cpp:163` | `getClientChar` 覆盖 `CLIENT_TELNET`，S3 的实时字节能进 |
| 2 | `Serial.cpp:171` | `clientCheckTask` 是承载任务 |
| 3 | `Serial.cpp:179-180` | `is_realtime_command` → `execute_realtime_command`，**不排队**、实时拦截 |
| 4 | `Serial.cpp:250-252` | `Cmd::Reset` 先过 `paper_should_ignore_host_reset()` |
| 5 | `PaperSystem.cpp:58` + `PaperSystem.cpp:771` | 忽略窗口 = 换纸启动起 `millis() + 8000u`；**8s 后放行** `mc_reset()` |
| 6 | `Protocol.cpp:228` | 换纸阻塞期跑 `protocol_service_during_blocking()` |
| 7 | `Protocol.cpp:269` | 其尾部调 `protocol_execute_realtime()` |
| 8 | `Protocol.cpp:577` | `sys.abort = true`（源码注释自称 `Only place this is set true`） |
| 9 | `PaperSystem.cpp:155` → `PaperSystem.cpp:716` | `paper_blocking_abort_requested()` → `paper_auto_change_abort_cleanup()` 安全停机 + error:90 |

即：**换纸开始 8s 之后**，S3 发 0x18 可让换纸安全中止。

### 1.2 S3 侧却恒答「无法即停」

`hutuji_job.cc:480` `Job::RequestAbort()` 内两处 paper_active 分支：

- `hutuji_job.cc:520-522`（首次 paper_active 快照后早退）
- `hutuji_job.cc:533-535`（stream_mutex_ 下二次复核）

两处均 `Notify("写字机正在换纸，无法即停；换纸完成后任务将停止")` 并直接返回，
**不发 0x18**。而 S3 本身有该能力：`hutuji_job.cc:1283` 已在用 `pipe.SendRealtime(0x18)`
（仅 worker 路径用）。

### 1.3 切片 C 的建议动作被证伪

切片 C 提议「用 `!`（FeedHold）替代」——**无效**。`Serial.cpp:268` 的 `Cmd::FeedHold`
只置 `sys_rt_exec_state.bit.feedHold`；而换纸停机判据在 `Protocol.cpp:577` 只认
`bit.reset`。FeedHold 对换纸期停机零作用。正确手段只有 0x18。

### 1.4 为什么本轮不动手

- 落点 `hutuji_job.cc` 的 abort 分支属**机构安全相关**路径，8s 忽略窗口本身是
  防「换纸机械动作中途被打断」的设计；在窗口内/外发 0x18 的行为差异需实机取证。
- 该改动超出「review」范围（评审切片明令禁改代码）。
- 故：**登记为待裁决项**，请用户决定是否在实机窗口做「换纸中 abort 复测 + 分流修正」。
  这与 `AGENTS.md` 默认下一刀第 ③ 项（断连恢复 `paper_active` 窗口 abort 复测）同源。

## 二、F2（证伪）坐标 2 位小数「差异」= 层级混淆

切片 B 报「奎享统一 2 位小数、我方 3 位小数」为差异。**不成立**：

- 奎享**真实产物** XY 同为 3 位小数——`findings/02` 第 59 行已记（证据 = 9 份 SHA256
  相同的缓存文件）。
- 被引作依据的 `com/kvenjoy/drawsoft/lib/b/i.java:120-121`
  （`d(float) = Math.round(f*100)/100`）位于**路径编排单位换算层**，不是 G-code
  发射层。往返证实：换算层收 2 位，发射层仍打 3 位。
- 我方 `{x:.3f}` 与奎享真实产物逐字一致，profile 第 4 行声明准确。

附量化（3 份实机规模真实产物，全部 3dp）：bicycle 62563B/4840 坐标、cat 27524B/2182、
draw 13253B/1086。若改 2dp：字节 −7.74%/−7.93%/−8.19%，最大几何偏差 **0.0050mm =
半个步进**（`$100/$101=100` → 1 step = 0.01mm）。`findings/23` 第 162 行已登记
「不为省体积调精度」。**结论：无动作。**

## 三、F5（证伪）cloud_bridge 探针与在途请求的「竞争窗口」不存在

切片 D 报「探针超时 vs 在途请求竞争」。亲读守卫后**推翻**：

- `mcp-server/hutuji_mcp/cloud_bridge.py:738-741` 三个前置条件是 **AND**：
  `probe_pending_seconds() == 0` **且** `stall_seconds() == 0` **且**
  `idle_seconds() >= PROBE_IDLE_SECONDS`。
- `mcp-server/hutuji_mcp/cloud_bridge.py:479-481` `mark_inbound` 把任意带 id 的 request 武装进 `_pending`
  （`setdefault`，同 id 重发取最早时刻）。
- 故长事务在途时 `stall_seconds() > 0`，**探针根本不会发射**。切片 D 时序 A 的前提
  （`_pending={42:...}` 非空却仍发探针）与守卫自相矛盾。**结论：无动作。**

## 四、有意差异（两项，非缺陷，仅登记）

### 4.1 窗口准入处的 UI 节流

- 奎享 `machine/f/b.java:55-75`：窗口准入循环内**无任何 UI 调用**。窗口上界取
  `this.b.g()`（抽象方法，`machine/c.java:46` 声明；Grbl 实现
  `machine/grbl/a.java:312-313` 返回 **100**），判据含 `+1L`（换行符）且**严格小于**。
- 我方 `hutuji_job.cc:3254-3255`：`c_line_bytes_sum + need < kWindow || c_line.empty()`，
  `need = line.size() + 1`（`hutuji_job.cc:3038-3040` 维护 `c_line`/`c_line_bytes_sum`）。
  **不变量同构**；`kWindow` = `hutuji_recovery_core.h:17` `kStreamWindowBytes = 512`
  经 `hutuji_job.cc:122` 转名。
- 我方灌流循环内确有屏显调用，但已 250ms 节流（`hutuji_job.cc:3433` 注释 +
  `hutuji_job.cc:3441` 判据）——即 2026-08-24 喂流结案的修复本身。**2026-08-25
  更新**：节流在 lichuang-dev 全脸动画下不足（单次 LVGL 锁等待 230–640ms 致半墨
  复发），已根治——SetStatus 经 `Application::Schedule` 出灌流路径（xiaozhi
  `e623aec`），窗口循环现与奎享同态（无阻塞 UI 调用），见
  `docs/design/s3-stream-pacing-investigation.md` §7。**差异已消解，无动作。**

### 4.2 error 后的处置：fail-fast vs 状态兜底续跑

- 奎享 `machine/f/b.java:136-155`：error 路径清 `c`/`d` 双队列、`e = 0`，**fail-fast**。
- 我方 `hutuji_job.cc:3489-3505`：ok 超时后若 `ConfirmInFlightDoneByStatus` 确认在途
  已执行完，靠状态兜底释放整窗续跑（上限 `hutuji_job.cc:101` `kMaxOkFallback = 3`），
  并在释放前 `DrainResponses()` 防迟到 ok 错配。
- 我方多出的是**迟到 ok 容错**，源于 WiFi Telnet 传输（奎享该路径是串口）。
  abort 侧亦有同口径 quiesce（`hutuji_job.cc:3339-3348`）。**有意差异，无动作。**

## 五、仓库卫生（切片 E · 已修文档 + 窄规则封堵）

| 项 | 实测 | 处置 |
|---|---|---|
| `mcp-server/tmp/` 未 ignore | `git check-ignore` exit=1、`?? mcp-server/tmp/`、`git add -An` 列 314 文件（约 20MB，含 298 个 quickdraw-data `.jsonl`）；**0 staged** | `docs/worktree-inventory.md:362` 原写「gitignored 目录内」= 谎报，已改为实测真相。同日用户 advisory 点名窄例外：`edf68e7`「不新增忽略规则」对**整目录忽略 tmp/** 仍成立（整目录方案被否决——AGENTS.md 引用根 `tmp/` 刷机/抓包工具），`.gitignore` 仅新增 `mcp-server/tmp/quickdraw-data/` + `mcp-server/tmp/**/*.jsonl` 两条；实测数据集对 `git add -A` 不可见，剩余 12 个实验/工具件仍未忽略 → **禁止 `git add .`** 不变 |
| quickdraw 键数演进 | 实测 **387 别名键 → 298 类目 ↔ 298 个 `.jsonl`**，双向零差；较复核时 373 键新增 14 个多字别名 | `AGENTS.md:117`、`docs/worktree-inventory.md:357` 已同步 387 键；2026-08-25 v4 批后演进为 402 键（礼貌前缀循环剥离 + 15 个「小X」别名），见 hub readiness 行 13 |
| 测试计数演进 | `test_quickdraw` **34 项**、全套 **976/976** | `AGENTS.md:145-147`、`docs/worktree-inventory.md:361` 已同步；2026-08-25 v4 批后演进为 test_quickdraw 39 项、全套 981/981（C 组合质量路由 +2）；计数不变量测试批后 test_contract 592 项、全套 982/982；2026-08-28 全套 **1035**（含 `test_gcode_min_segment` 3 项）；2026-08-30 晚全套 **1070** |

## 六、门禁面观察（已修根因；候选项仍不动作）

- **Java 引用不受门禁校验**：`_DOC_REF_RE` 的扩展名白名单
  （`py|toml|c|cc|cpp|h|hpp|sh|json|mdc|md|conf|service|ino`）**不含 `.java`**，故本文
  引用的奎享行号在三层（越界 / 外仓 / 指纹）全部不可见，是**人工维护的死引用**。
- **KXNX_ROOT 纳入门禁的量化**：Python 实测 `decompiled_full/` 单树索引下 125 条去重
  引用 → **88 唯一命中 / 33 重名 / 4 缺失**；重名与缺失由既有 skip 语义安全跳过，
  不会像 inkscape 那样退化成假绿（`scripts/agent_gate.py:887` `_EXTERNAL_REPOS` 注释
  记的正是该坑）。**但**：① 改门禁属评审范围外的行为变更；② 即便纳入，仍需先给
  `.java` 开扩展名白名单，两步都会扩大判红面。故仅登记为候选。
- **basename 唯一性破裂 → 本仓引用假绿（本轮已修根因）**：根 `tmp/` 的部署/发布暂存副本此前不在 `_REF_SKIP_DIRS`，导致裸 basename 行号引用因同名多份被三层静默跳过；`scripts/agent_gate.py` 现将 `tmp` 纳入跳过目录，并由 `test_gate_repo_index_skips_tmp_staging_copies` 回归钉死。本文原 5 处引用已先路径限定并登记指纹；本轮根因修复后，后续暂存副本不再污染 basename 唯一性。若未来引用暂存树内文件，仍应使用明确路径或按散文式「文件名 第 NNN 行」说明。

## 七、限制与未验证

- F1 的 8s 忽略窗口**边界行为未实机取证**：窗口内发 0x18 被忽略后 S3 是否重试、
  窗口外发 0x18 的机械停位是否安全，均无设备证据。
- 本轮 33 处代码锚点为源码亲读，**非板上运行态**；Grbl 侧结论适用于工作树当前内容。
- 切片 A 报的 `Config.h:485`/`:497`/`:498` 缓冲常量为**注释态**，激活默认值在
  `Serial.h:26` = `RX_BUFFER_SIZE 256`（切片原文按 128 立论，前提错）。
- 奎享侧 Rx 窗口对照（我方 512B < `TelnetServer.h:33` `TELNETRXBUFFERSIZE = 1200`；
  奎享 100B < 其 `RX_BUFFER_SIZE`）不变量方向一致，但奎享固件侧缓冲大小未取证。
- 切片 A 早期笔记引用的 `eh.java` **在基准树中不存在**（已全树检索确认），该条已废弃、
  未采信。
- 切片 D 另有 4 项自报「未能定性」，本轮未追。
- **本轮 quickdraw 实现专项复核（非五切片；修复状态）**：
  - H1 最长优先测试缺口：**已修**，新增 4 个真实碰撞增强短语断言；`-len(k)` 变异会被测试击穿。
  - M1 14/298 类目子串兜底失效：**已修源码**，补入 14 个多字别名，映射现为 387 键→298 类；14 个增强短语回归全过，跨类子串碰撞为 0。
  - M2 命中路径无并发闸：**已修源码**，`svg_to_gcode_async` 统一以按事件循环懒建的信号量限制最多 2 个 vpype；取消、超时、真实管线与并发回归全过。
  - L1/L2/L3：**已修**，删除死的 `stripped` 第二候选、fixture 清理 `_substring_keys` 缓存、改用 mapping 下标并恢复顶层空行。
  - **部署边界**：以上修复仅有本机源码/测试证据，本轮尚未部署阿里云生产；生产命中率与运行态资源闸仍未验证。

## 八、2026-08-26 晚间 MCP 全量复审补遗

- **P3 三项（2026-08-26 用户指令「处理」后全部收口）**：
  1. `activation_relay.py` 400 响应体为合法 JSON 但非 dict（list/str/null）时 `.get`
     抛 AttributeError 未接 → **已修**：`except (ValueError, AttributeError)` 与同文件
     `_list_bound_macs` 同口径，设备收 502；新增 `test_relay_400_non_dict_json_is_upstream`
     三种坏形状回归。
  2. 设计 §10.7 QD 路径并发 single-flight 无专属测试 → **已补**：
     `test_draw_job_hit_concurrent_same_description_share_one_job`（5 并发同题共享
     一产物、lookup 仅一次、不占限流名额）。
  3. 设计 §11 里程碑 4「miss 日志回收一周迭代映射表」未建 → **观测面已落 +
     里程碑改组**：lookup 无匹配类目落 info（`test_lookup_unknown_category_logs_miss`
     钉死）；独立回收周期 supersede 进真实语音使用观察期（设计稿 §11 已改）。
  测试计数 982 → **985**（contract 593 / quickdraw 41），AGENTS 与 inventory 已同步。
- **复审干净面**：memory_store 读-改-写锁、llm_svg 重试/预算/finish_reason 语义（`length` 只解释「一段 SVG 都提取不到」、不拒已闭合 SVG）、gcode_gen 取消时 terminate→kill 阶梯与按事件循环信号量、bitmap_svg 对冲赛跑 loser 回收（gather return_exceptions 兜底）与 anyio 取消屏蔽循环，均无新发现。
- **验证**：`pytest tests/test_quickdraw.py` 41 passed；全套 985；hub 门禁与 e2e 见本轮提交报告；本节行号引用均为散文式，指纹基线不动。
