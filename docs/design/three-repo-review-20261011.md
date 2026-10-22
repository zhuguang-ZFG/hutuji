# 三仓深度复审（2026-10-11，FR11-05 推送后）

承接 `three-repo-deep-review-20261010.md`（DR10-01..08）与 `firmware-review-fixes-20261011.md`（FR11-01..06）。本轮基线：hub `4835965`（main）、量产 Grbl `2a7350c9`（massprod/nopaper-1.3a，FR11-05 已推送）、S3 `3d94f8f`（feat/hutuji-usb-pipe，板上 1.0.40）。产品源码**只读**，未连设备、未部署、未刷机。三仓并行安全复审（security-reviewer 子代理），每条发现基于实读源码并经主代理二次核验。

## 结论

**2026-10-11 续作 supersede**：上一轮的“两条 P3、均有后盾”结论撤回。目标工具链与本地并发探针复核后，保留 **F-NEW-01 / P2（Portal 配额并发超发，尚未修复）**，撤回 **NR12-GRBL-01（目标平台 char 符号性误判）**。本轮没有重新全量审查 DR10/FR11；下方既有复核记录仅代表上一轮范围。

## 续作复核与证据（2026-10-11）

沿用文首三个提交基线，产品源码只读；真实 Portal 路由、认证与临时 SQLite 参与验证，付费生成、聊天和内容安全服务使用本地替身，不访问生产或设备。探针位于 `tmp/review-20261011-resume/`，证据位于 `results/local-only/2026-10-11-review-resume/`，均仅本机保留。

### F-NEW-01 / P2：并发超发超过已接受残差，原报告高估下游保护范围

- **契约校正**：`protocol.md` §10.4.8 生图与 §10.4.14 聊天约定为**滚动 24 小时、成功才计数、失败不占额**，并非自然日；生图明确接受 check-then-act 超发 1–2 单。因此，“存在先查后记”本身不是新增违约依据，问题在于超发范围超过文档接受的残差。
- **实际复现**：同一已认证用户、限额 1、8 个同事件循环并发 HTTP 请求；生图与聊天分别得到 **8 个 HTTP 200、SQLite 8 条成功 usage、下一请求 429**。聊天 8 个回包均 `remaining=0`，没有体现已经超发 7 次。两个探针保留原路由配额读取与入库逻辑，使用上游替身屏障稳定放大真实等待窗口；这证明 Portal 受理和记账缺口，不代表生产发生了 8 次真实付费扣费。
- **下游保护校正**：`/internal/chat` 直接调用 `run_chat` → `_call_llm`，不经过 `DrawLimiter` 或设备绘图日限，不能引用绘图并发 8 为聊天兜底。未绑定设备的合法网页生图带空 `device_id`，MCP 设备日限对此跳过；绘图并发/速率保护仍存在，但不等价于每用户滚动 24 小时成功配额。具体生产超发规模取决于请求时序、队列、代理与服务容量，本轮未测其上界。
- **优先级理由**：已认证用户可并发触发多次聊天付费路径，超过明确接受的少量残差，应优先补齐配额受理原子性；不是 DR10/FR11 引入的新回归。语音生图与小派设置仍只有源码同构观察，本轮未并发实测，不将它们写成已复现。
- **修复约束**：应在调用付费服务**之前**原子检查并预占名额（跨 worker 使用 SQLite 事务），成功结算、失败释放，明确进程退出与在途预占回收语义，并保留滚动 24h 与失败不占额契约。原建议“生成成功后 CAS / 超额回 429”不能挽回已经发起的付费调用，不足以解决成本风险；单进程锁也不能证明跨进程成立。方案尚未实施。
- 证据：`quota-generate-8.json`、`quota-chat-8.json`；测试使用真实认证而不是手工调用 endpoint 跳过依赖。

### NR12-GRBL-01：撤回目标平台缺陷判断

量产 `platformio.ini` 选择 espressif32 3.0.0/Arduino。所用 `toolchain-xtensa32` GCC 5.2 的预定义宏包含 **`__CHAR_UNSIGNED__=1`**，`WString.h` 下标返回 `char`，但该目标默认 `char` 无符号。项目配置、平台构建器与 Arduino 构建脚本的检索未发现 `-fsigned-char` 覆盖。

直接使用该编译器对原比较式 `static_assert(char(0xC2) == 0xC2, ...)` 做语法编译，默认参数 **exit 0**；仅人为增加 `-fsigned-char` 的负对照 **exit 1**。故“xtensa 默认 signed、该分支恒不触发”的前提为假，不能据宿主平台常见默认值修改量产固件。证据 `target-char.json` 记录编译器 SHA、命令、宏和两组退出码。本轮只证伪符号性原因，不宣称整个 WebUI 实时命令路径已实机验收，也无需为此项双板重刷。

### 验证边界

首次探针的 `generate-8` 与 `chat-8` 通过；额外 `chat-24` 在本机默认 executor 下同步屏障超时（`BrokenBarrierError`），不是产品结论，失败记录保留在 `initial-run.txt`。最终验证收窄到可调度的两组 8 请求，不修改产品线程池，未取得 24 次超发证据。两组探针加现有 `draw-portal/tests/test_generate.py`、`test_chat.py` 共 **26 passed / 27.42s / exit 0**（1 条既有 TestClient 弃用警告），同目录 `pytest-final.xml` 留证；不将这些局部检查写成 Portal 全套或三仓全量门禁。

## 上一轮原始发现（以下由续作结论 supersede）

### F-NEW-01 / P3：Portal 按用户日配额为非原子 read-check-record，并发可小幅越限

`draw-portal/draw_portal/app.py` web 生图入口（约第 2277–2287 行先 `usage_counts_for_user_since` 读 `used` 并判 `used >= generate_daily_limit`，第 2343–2349 行在生成**成功后**才 `_record_portal_usage` 落库）。读→判→记之间无锁/无原子自增，窗口横跨整次付费生成。同一登录用户极短时间并发 N 个 `/api/draw/generate` 时，各请求可同时读到 `used=limit-1` 全部通过；越额量约等于瞬时并发度。语音路径 `web_generate`（约第 1134–1142 / 1182–1187 行）、`wx_chat`（约第 2910–2918 / 2958–2965 行，`chat_send` 返回的 `remaining` 亦基于陈旧 `used`）、`xiaopai_setting`（约第 3103–3107 行）同构。

- 期望：每用户每自然日成功生图/聊天不超过 `generate_daily_limit`/`chat_daily_limit`。
- 实际：并发下各请求基于陈旧 `used` 通过闸门，`remaining` 并发下为错误值。
- 对照：MCP 侧 `DrawLimiter.enter`（临界区无 await 以保原子）、`draw_queue` 的 Lua `ENQUEUE_SCRIPT`（day_count 原子判增）、`server._device_daily_limit_reached`（同步无 await）均为原子；portal 此路径是唯一非原子配额写回，口径不一致。
- 后盾：`draw_max_concurrent`（MCP 侧并发 8）与 Redis 侧 `draw_per_device_day` 两道，故非无界绕过，越额上限受限。
- 测试覆盖：无并发配额回归；现有 usage 测试均串行调用。
- 修复方向：配额判定与计数合并为单条原子 SQL（成功落库时带 WHERE 的条件 INSERT/UPDATE 做 CAS，或插入后回查 count 对超额请求回退 429），或对同 `user_id` 生成入口加进程内串行化闸，使之与 MCP 侧原子限流器口径一致。注意生成是付费 LLM/图像调用。
- 性质：latent 一致性弱点，非 DR10/FR11 回归。

### NR12-GRBL-01 / P3：WebServer 的 0xC2 UTF-8 实时命令前缀解包因 signed char 比较恒不触发

量产 Grbl `src/WebUI/WebServer.cpp`（约第 535 行 `if (!silent && (scmd.length() == 2) && (scmd[0] == 0xC2))`）。xtensa gcc 默认 `char` 为 signed，`String::operator[]` 返回 `char`；字节 0xC2 作 signed char 为 -62，提升为 int(-62) 与 int(194) 比较恒 false，解包分支永不执行，0xC2 前缀命令被当作 2 字节普通输入推入 RX 环。

- 期望：WebUI HTTP 命令通道以 UTF-8 0xC2 前缀编码的 GRBL 实时命令（0x90、0xa0–0xbf，如进给保持/倍率）被剥前缀后作单字节实时命令下推。
- 实际：解包恒不触发，HTTP 命令通道的 UTF-8 实时命令失效。
- 性质：**原始上游代码**，非 FR11/DR10 改动引入；且 hutuji 实时控制走 S3→Telnet `:23`（非浏览器 WebUI HTTP 命令通道），不在 hutuji 活链路。
- 测试覆盖：固件侧无该路径单测，PC SIL 不覆盖。
- 修复方向：`static_cast<unsigned char>(scmd[0]) == 0xC2`（或 `(uint8_t)scmd[0] == 0xC2u`）消除 signed char 提升歧义。该文件在 §9 白名单内，但本项非 hutuji 功能性需求、属上游既有缺陷，是否修由用户定；修 Grbl 须另过 fz standard 门禁并双板重刷。

## 复核确认（无新问题）

- **S3 固件（0 发现）**：FR11-01/06 pause/resume 代次隔离经 `connection_seq`/`banner_seq` 双序号 + `stream_control_epoch` 线性化双重保证；停止受理后迟到控制被 `abort_requested_`/状态标志拒绝；worker 以 atomic exchange 单例守卫、xTaskCreate 失败回滚、无交错窗口。FR11-02 `ota_start` URL 板型/版本/签名形状四重闸（路径精确 `/firmware/s3/<BOARD>/<version>/xiaozhi.bin`、拒片段、签名仅 `?exp=<≤16位数字>&sig=<64hex>`、host allowlist 精确等值）+ strict 版本单调/sha256，不可被构造 URL 绕过。FR11-03 `UpgradeFirmware` 失败路径状态与 OTA 预约清理完整。`kNopaperGrblBuild` 精确互校 + 认不出按换纸机保守默认。pipe 下载 content_length/PSRAM/CRC32 边界与 preview 解码所有权均 fail-closed。排除两个伪缺陷（ota_start URL 绕过、1.0.40 版本前缀致 OTA 永久失败）。
- **Grbl（除 NR12-GRBL-01 外无新问题）**：FR11-04 JSONEncoder `quoted()` null-guard 到位、逐字节转义 `"` `\` 与 <0x20、UTF-8 多字节/代理对原样透传。FR11-05 WebServer `pushError()` 递归 `WebSocketAccessGuard` 与 `end()` 的 detach+delete 共用 `webSocketMutex` 全程持锁、无 UAF；Serial2Socket 分块写满 1200B 立即 flush→broadcastBIN 整段长度正确、无截断/长度字段破坏、`_TXbufferSize(uint16_t)≤1200` 不溢出、write/begin/end/attach/detach/available/peek/read/push/flush/析构全部 Guard 覆盖、全仓无绕锁调用点。e3054175 Telnet 半行隔离：end/clearClients/handle 断链/write 五处均 `client_begin_session` 自增代次清队列 + 消费侧 `client_session_current` 丢弃陈旧 + `-2` 会话屏障 + Protocol 半行清理，隔离彻底。注：任务清单中的 `StatusBeacon.cpp/.h` 与 `ProtocolDecisionCore.h` 在量产仓不存在（glob 无命中）。
- **hub Python（除 F-NEW-01 外无新问题）**：token 自愈的全部生产写回路径均走 `expected_token_enc` 的 CAS（非 CAS 版仅测试调用）——DR10-08 修复完整，无遗漏同类写回。`validate_gcode` 在塌缩/转置/弧拟合**之后**对最终落盘字节执行、与服务时复核同口径。SVG 白名单（DTD/PI/url()/命名空间/大小写/深度闸）与 `url_guard`（https + 非 global IP + 本地域名 fail-closed）未见可绕过路径。`/files`/`/songs`/`/internal/*`/`/register` 的鉴权/路径遍历/脱敏均 fail-closed，能力文件名 token 切分无泄露。`device_identity` P-256 ECDSA 验签与长度数学正确。

## 第二轮扩展审查（R2，2026-10-11）

续作后补一轮扩展面：MCP 算法/解析/几何/排版/生成链 + portal article（R2Hub）、量产 Grbl §9 白名单深读（R2Grbl）、S3 pipe/job 内存与并发（R2S3b）。产品源码只读，未连设备。每条经主代理实读二次核验。

### R2-01 / P2（confirmed）：xlsx mergeCell 行范围未设上界 → 文档导入 CPU 耗尽 DoS

- 位置：`mcp-server/hutuji_mcp/doc_ingest.py:632`（**修复已落地**：改为对 `orig_to_out` 键集合与 `[r1,r2]` 求交迭代，次数有界于真实行数 ≤`MAX_TABLE_ROWS`）；原缺陷为 `for r in range(r1, r2+1)` 配无上限正则，`_merge_ref` 第 509 行 `r2 = int(m.group(4)) - 1`，正则 `(\d+)` 对行号不设上限。
- 触发：portal 文档导入上传含 `<mergeCell ref="A1:A999999999"/>` 的 xlsx；A 列 `c1=0 < ncols` 必进内层循环，单个 <100B 元素即触发。
- 期望 vs 实际：期望合并展开有界——同文件对**列**展开已用 `_XLSX_COL_SPEC_SPAN=64` 显式钳死（第 497–498 行注释「展开必须有界」）。实际 `r2` 直接取自无上限正则，`range(r1, r2+1)` 对单元素产生 1e9+ 次 `orig_to_out.get(r)` 空转；解析在 portal 同步导入路径执行，CPU 耗尽、worker 挂死，与已解析行数（≤`MAX_TABLE_ROWS=500`）完全脱钩。
- 测试覆盖：`test_doc_ingest.py:360` 仅覆盖 `A1:B1` 横向小合并（range 迭代 1 次）；无大范围/纵向合并回归。
- 修复落地（已实现）：内层改迭代 `orig_to_out.items()` 与 `[r1,r2]` 求交，与 `_XLSX_COL_SPEC_SPAN` 列展开上界对齐；回归 `test_doc_ingest.py` 的 `test_xlsx_merge_row_range_bounded`（单个 `A1:A100000000` 合并断言产物只覆盖真实两行 + 墙钟 <5s，旧 O(ref 跨度) 实现必红）。
- 残留收口（已实现）：单元素行范围封堵后，`raw_merges` 条数本身仍无上界——32MiB 部件可塞 ~1.2M 条 `<mergeCell>`，每条逐行求交即 O(merges×rows) 仍可打成 DoS。`doc_ingest.py:36` 增 `MAX_MERGE_CELLS=2048`（与 `_XLSX_COL_SPEC_SPAN=64` 同族显式上界），`doc_ingest.py:575-576` 累积即查、超限抛 `DocIngestError("合并单元格过多…")`，不等整段收齐再逐行展开；回归 `test_doc_ingest.py` 的 `test_xlsx_merge_cell_count_bounded`（`MAX_MERGE_CELLS+1` 条 `<mergeCell>` 断言拒绝，无上界旧码必不抛必红）。

### R2-02 / P3（confirmed）：article_llm LLM 回包无字节上限，与 chat.py 既有防线不一致

- 位置：`mcp-server/hutuji_mcp/article_llm.py:108`（**修复已落地**：`client.stream`+`aiter_bytes(16384)` 流式读取，第 110 行累计超 `ARTICLE_LLM_MAX_RESPONSE_BYTES=64KiB` 即中断为 `LlmError`）；原缺陷为非流式 `client.post` 后 `resp.json()` 一次性缓冲整个 body。
- 触发：配置的 `HUTUJI_LLM` 网关故障或被接管，返回超大响应正文（generate/polish 同路径）。
- 期望 vs 实际：期望与同源同网关的 `chat._call_llm` 一致——流式 `aiter_bytes` 并在累计超过 `MAX_LLM_RESPONSE_BYTES=64KiB` 时中断（`chat.py:154-161`，注释明示「防故障上游推爆内存」）。实际 `article_llm` 用非流式 `client.post` 后 `resp.json()` 一次性缓冲整个 body，无任何大小闸；`ARTICLE_LLM_MAX_TOKENS` 只约束正常生成长度，约束不了故障/恶意回包的实际字节数。
- 测试覆盖：无超大回包测试；现有仅覆盖正常文本与 `_reject_non_article` 启发式。
- 修复落地（已实现）：改 `client.stream`+`aiter_bytes` 流式读取并在超限中断，复用 `chat` 同一 64KiB 口径；回归 `test_article_llm.py` 的 `test_oversized_response_rejected`（~74KB 回包断言收敛 `LlmError` 大小上限，旧非流式 `resp.json()` 无字节闸必红）。

### R2 复核确认（无新问题）

- **MCP 深读面（除 R2-01/R2-02 外 0 发现）**：`arc_fit` 弧拟合 → `validate_gcode._arc_bbox` 的 §5 包围盒复核链（含半径/弦长/扫角闸）健全；`svg_to_gcode` 塌缩/转置/弧拟合后对 fitted 本体重校验字节口径正确；`sketch_render` 段数预算即便超限仍由 `validate_gcode` 512KiB 兜底；`write_text` 与 `table_layout` 的 CJK/拉丁逐字槽位公式一致；`modelscope`/`tcb`/`bitmap` 三处生图下载统一过 `url_guard.validate_public_https_url`（`follow_redirects=False`，无 SSRF 缺口）；`chat.py` `DRAW::` 标记解析边界、`cloud_bridge` watchdog/探活/pending idle 剔除、`gfont` 多版头解密与点数/verb 上限（字库为服务端可信资产）、`doc_ingest` 的 ZIP 条数/成员字节/XXE/字符上限防线均健全。
- **量产 Grbl §9 白名单（0 发现）**：`GCode.cpp` 为纯上游 rs274/ngc 解析，无 hutuji §9-F inline guard——§5 契约由 hub `validate_gcode()` + stock `limitsCheckSoft` 执行，是文档化职责划分非缺陷；`ProcessSettings.cpp` 的 §9-C `auth_failed` 为 stock ESP3D 权限谓词，`motor_disable` strdup/unique_ptr 全出口回收正确，`[ESP101=]` 空值写 vs `[ESP101]` 读区分正确；`WebSettings.cpp` 的 `WebCommandContext` 栈序 per-task + `split_params` i≥9 溢出守卫正确，权限位同 stock；`nopaperStatusHandler` 固定返回 `MotorEn=Unknown PanelHold=Unknown`（DR10-06 修复已落地，S3 侧只解析 `Paper=`/`Changing=` 故为无害占位）；`WifiConfig.cpp` STA 自愈 watchdog 逐行核验（NULL 守卫、仅 STA 模式、millis 回绕算术、down-window 仅真恢复复位、retry 仅在链路已断时触发不打断健康流、跑在 clientCheckTask 非 motion loop）均正确；**`StatusBeacon.cpp/.h`、`ProtocolDecisionCore.h`、§9-D BT 功率逻辑、paper handlers 在量产仓不存在/编译排除**（`GRBL_PAPER_SYSTEM` 未定义），与任务清单/编辑白名单的记载存在差异（见下）。
- **S3 固件 pipe/job 内存与并发（0 发现，R2S3b 定向复读）**：`hutuji_job.cc` 的 PSRAM 下载缓冲在每次下载尝试入口（`ReleaseBuffer` 3236）与每个非 ok/abort worker 出口（3163-3164）回收，短读/截断先 `ReleaseBuffer` 再 continue（3346），abort-mid-read 由 worker 终态 else 分支释放，prefetch→buffer_ 交接在 `prefetch_mutex_` 下带 epoch/cancel 门控、adopt 前先释放旧 buffer_（2612-2616）——无 double-free/泄漏/UAF；`RequestPause/RequestResume` 先取 `stream_mutex_` 再于 `paused_.exchange`、`SendFeedHold/ResumeForSession` 之前检查 `abort_requested_.load()`，`stream_control_epoch_` 同锁推进、发送失败回滚同锁，无锁外实时发送、无 abort→send TOCTOU；`Pipe` 为进程生命期 Meyers 单例、socket 拆除经 `ShutdownSocket` 在 `write_mutex_` 下序列化并由 `connection_seq_` 代次门控，停工/陈旧 worker 不能关掉轮换会话的 fd——无跨任务 UAF；NVS 身份（`factory_identity.cc`/`hutuji_bind_identity.cc`）读写向 `nvs_get_str` 传缓冲容量、blob 按固定记录长度 + 版本字节校验、`sn≤48`，不匹配 fail-closed；`application.cc` 的 hutuji 回调（headless bind/会话回报/OTA 日检/激活码回报）均不在停止后解引用或改写已释放的 Job 状态。

### R2-DOC-01 / 文档差异（非代码缺陷）：编辑白名单引用的 §9 文件在量产仓不存在

`AGENTS.md` / `docs/agent-edit-allowlist.json` 把 `src/WebUI/StatusBeacon.{h,cpp}`、`src/ProtocolDecisionCore.h` 列为 §9 可碰文件，但 R2Grbl `glob` 实测在 `Grbl_Esp32-massprod` 分支**无命中**（§9-H′ StatusBeacon 奎享守护、§9-C′ 决策谓词 header 均属普通分支，未并入 massprod/nopaper-1.3a）。非代码缺陷，但白名单对 massprod 而言含不存在路径，易误导后续代理。建议在白名单/矩阵标注这些条目仅适用普通分支。

## 第三轮扩展审查（R3，2026-10-11）

沿用 R2-01 镜头（攻击者可控 count/length/value → 无界循环或分配，且须绕过既有字节/`MAX_*` 闸才算真缺陷），续读 `doc_ingest.py` OOXML 解析剩余面。产品源码只读；两条均为 R2-01 同族防御纵深，发现即修复并补回归。

### R3-01 / P3（confirmed，已修复）：xlsx `<col>` 列定义条数无上界 → `col_widths` 构建 O(ncols×specs) 空转

- 位置：`doc_ingest.py` 的 `_xlsx_sheet` 列定义循环（`for col in root.iter(f"{_S}col")`）累积 `col_specs` 无条数上界——行范围与 `MAX_MERGE_CELLS` 已封堵，但 `<col>` 条数本身只受部件字节上限约束。
- 触发：高压缩 xlsx 塞入海量互不匹配的 `<col min=i max=i width=7/>`（每条 ~40B，32MiB 部件可塞 ~1e5+ 条）；后续 `for c in range(ncols): for lo,hi,w in col_specs` 为 O(ncols×specs)，小文件即可放大到千万级空转。
- 期望 vs 实际：期望与 `MAX_MERGE_CELLS=2048` 同族显式钳死；实际 `col_specs` 无上界，与真实列数（≤`MAX_TABLE_COLS=24`）脱钩。
- 修复落地（已实现）：`doc_ingest.py:37` 增 `MAX_COL_SPECS=2048`，列循环累积即查、超限抛 `DocIngestError("列定义过多…")`；回归 `test_doc_ingest.py` 的 `test_xlsx_col_spec_count_bounded`（`MAX_COL_SPECS+2` 条非匹配 `<col>` 断言拒绝，无上界旧码必不抛必红）。

### R3-02 / P3（confirmed，已修复）：pptx `sldIdLst` 重复 rId 引用致同一 slide 部件被逐条重解析 → CPU/内存 DoS

- 位置：`doc_ingest.py` 的 `_parse_pptx`（`for sld_id in pres.iter(f"{_P}sldId")` 内按 `r:id` 查 `rels` 并逐条 `_parse_xml_part`）；原实现对同一 target 不去重。
- 触发：`presentation.xml` 的 `<p:sldIdLst>` 塞入海量 `<p:sldId r:id="rId1"/>` 全指向同一 slide 部件（每条 ~40B，32MiB 可塞 ~数十万条）；每条都重新 `_parse_xml_part` 解析同一（可较大）部件 → O(N) 重解析，`_check_total_chars` 仅在循环末尾触发、拦不住过程中的 CPU/内存累积。
- 期望 vs 实际：期望每个 slide 部件至多解析一次；实际同一 rId 被引用 N 次即重解析 N 次。合法 pptx 的 `sldIdLst` 每条指向不同 slide，去重对正常文件不可见。
- 修复落地（已实现）：`doc_ingest.py:450`（`seen_targets` 定义）、`doc_ingest.py:461-463`（去重守卫：同一 target 命中即 `continue`、否则登记）——展开上界收敛为不同部件数 ≤`MAX_ZIP_ENTRIES=256`；回归 `test_doc_ingest.py` 的 `test_pptx_duplicate_slide_ref_deduped`（64 条 `sldId` 全指向 `rId1`，断言 `paragraphs == ["重复页"]`：去重后单页无分页标记、正文仅一份；旧实现产 64 份正文 + 64 个「—— 第 X 页 ——」标记，throwaway 实证 OLD len=128 / NEW len=1）。

### R4-01 / P2（confirmed，已修复）：pptx 跨部件抽字累积绕过 `MAX_TEXT_CHARS` → 内存放大 DoS

- 位置：`doc_ingest.py` 的 `_parse_pptx`——`_check_total_chars` 仅在循环末尾（展平 `slides`→`paragraphs` 之后）触发；过程中逐 slide 把抽出文本累积进 `paras`/`slides` 无在途上界。
- 触发：`MAX_MEMBER_BYTES=32MiB` 是**单部件**上界，`MAX_ZIP_ENTRIES=256` 允许至多 256 个不同 slide 部件（去重后仍是 256）。每个部件可塞满 32MiB 文本 → 聚合抽出文本上界 = 256×32MiB ≈ 8GiB，全部累积进 `paragraphs` 后 `MAX_TEXT_CHARS=5000` 才报错——post-hoc 卡太晚，单部件字节闸拦不住跨部件聚合（R2-01 同族：攻击者控的部件数 → 无界累积）。
- 期望 vs 实际：期望抽字累积以 `MAX_TEXT_CHARS` 为界；实际累积以 256 部件字节总量为界、仅末尾一次性校验。
- 修复落地（已实现）：`doc_ingest.py:451`（`acc_chars` 计数）、`doc_ingest.py:475-476`（每追加一段正文即 `acc_chars += len(text)` 并 `_check_total_chars(acc_chars)`）——越过 5000 立即抛，累积上界收敛为 `MAX_TEXT_CHARS` + 单段文本（≤单部件）；对合法 pptx（远低于 5000 字）行为不变，仅把同一 `文字过多` 报错前移。回归 `test_doc_ingest.py` 的 `test_pptx_aggregate_char_budget_bails_early`（50 个各 ~1000 字的独立 slide 部件，监视 `_parse_xml_part` 命中 `ppt/slides/` 次数：新码越过 5000 后 `parsed_slides<=7`、旧码摸满 50，throwaway 掐 `_check_total_chars` 实证循环自然摸满 50 部件 /100 段）。

## 第五轮扩展审查（R5，2026-10-12）

沿用 R2-01 镜头移到位图解码面：攻击者可控**图幅**（而非 count）→ 解码即 OOM，且绕过既有字节上限与下游像素闸。产品源码只读，发现即修复并补 fails-before/pass-after 回归。门户专用改动，不动 MCP 侧 1649 项。

### R5-01 / P2（confirmed，已修复）：门户位图上传解码前无尺寸闸 → 高压缩比图幅解码放大 OOM

- 位置：`draw-portal/draw_portal/pipeline.py` 的 `normalize_raster_to_png`、`is_photo_raster`、`shrink_oversized_png` 三处均直接 `iio.imread(<上传原件>)`，解码前无任何像素/单边上界。
- 触发：`app.py` 的 `_MAX_UPLOAD_BYTES=4MB` 只钳**字节**；纯色/低熵 JPEG/PNG/WebP 可在 4MB 内头部声明数亿像素（如 10000×10000=100Mpx、乃至 40000×40000=1.6Gpx）。`iio.imread` 全量解码 RGB + `_looks_like_photo` 的 `rgb2gray` float64 峰值 ~11 字节/像素，100Mpx ≈ 1GB、远超 worker 预算 → OOM DoS。
- 为何绕过既有闸：`hutuji_mcp.bitmap_svg._reject_oversized_png`（5Mpx/32768 边 IHDR 预检）只护 **MCP 侧**（输入恒为模型产出 PNG，≤1760×2368）；门户 `/api/draw/upload` 直收 JPEG/WebP/GIF/BMP 原件，且该闸在门户链里位于 `shrink_oversized_png`→`trace_bitmap_to_polylines` **下游**——门户已先 `iio.imread` 全量解码（OOM 点）才轮到它，形同虚设。另：Pillow 自带 DOS 闸 **179Mpx（2×MAX_IMAGE_PIXELS）才 `DecompressionBombError`**、~89Mpx 起只 `DecompressionBombWarning` 仍放行解码——故 >179Mpx 原本已被 Pillow 以异常拦下（崩为 500、非 OOM），**本修复真正新封堵的是 64–179Mpx「只告警仍全量解码」窗口**（OOM 点），并把全部超限统一前移为解码前的干净 `ValueError`。
- 期望 vs 实际：期望解码前按文件头拒绝 OOM 级图幅；实际 4MB 字节闸放行后直接全量解码。
- 修复落地（已实现，门户专用）：`draw-portal/draw_portal/pipeline.py:26` 新增 `_guard_raster_dimensions`，用 Pillow 惰性 `Image.open(...).size` 只读头部尺寸、在 `iio.imread` 前拦；阈值 `_MAX_RASTER_PIXELS=64_000_000`（`draw-portal/draw_portal/pipeline.py:22`）、`_MAX_RASTER_SIDE=32768`（`draw-portal/draw_portal/pipeline.py:23`）刻意**远高于** bitmap_svg 的 5Mpx 描摹闸，使门户既有的 5–5.9Mpx 手机照片 `shrink_oversized_png` 等比缩放链（2026-09-08 生产修复）不被误拒；Pillow 自带 `DecompressionBombError/Warning` 一并视作炸弹拒。三处解码入口（`draw-portal/draw_portal/pipeline.py:96`/`draw-portal/draw_portal/pipeline.py:116`/`draw-portal/draw_portal/pipeline.py:436`）均前置该闸；`is_photo_raster` 走 fail-soft（超限返回 False、不改既有语义）。
- 回归（门户 `tests/test_pipeline_raster.py` 4→10，+6；门户全量 574→580 passed 实测）：`test_raster_bomb_rejected_before_decode`（1.6Gpx 头部，断言 `iio.imread` 未被调用且抛「像素过多」）、`test_shrink_oversized_rejects_bomb_before_decode`、`test_is_photo_raster_bomb_returns_false_without_decode`、`test_oversized_side_rejected`（40000×3 走单边闸）、`test_decode_memory_band_rejected_above_pillow_warn`（100Mpx 带：先证 `Image.open().size` 不报错即 Pillow 本会放行解码，再证本地 64Mpx 闸拦下——证明守卫带来额外保护而非复述 Pillow）、`test_normal_small_raster_passes_guard`（合法 JPEG 仍描摹出图不误伤）。fails-before 实证：中和守卫后 `normalize_raster_to_png(100Mpx)` 到达 `iio.imread`（`imread_called=True`）且不抛「像素过多」。
- 范围说明：第 4 处 `iio.imread` 在 `strip_corner_watermark`（`draw-portal/draw_portal/pipeline.py:413`）**不在本修复内**——全仓检索其仅有定义与单测、无生产调用方，且设计输入为 TCB/魔搭 i2i 模型产出 PNG（经 `url_guard` + 16MiB 字节闸的受信中间件），非攻击者可控上传原件，不属上传 OOM 威胁面；其自身 `except Exception` fail-soft 返回原图，不额外加闸以免改既有可用性语义。

## 第六轮扩展审查（R6，2026-10-12）

继续位图/文档解析面扫描，锁定 docx 列表编号前缀的 O(N²) 字符放大——与 R4-01（pptx 跨部件累积）同族但发生在 docx 单部件内、放大来自前缀**长度随项序增长**而非部件数。

### R6-01 / P2（confirmed，已修复）：docx roman 列表编号前缀 O(N²) 累积绕过 `MAX_TEXT_CHARS` → CPU/内存放大 DoS

- 位置：`doc_ingest.py` 的 `_docx_paragraphs`——`for p in root.iter(w:p)` 循环对每个列表项调 `prefix_for`→`_fmt_counter`→`_roman`，`lowerRoman`/`upperRoman` 前缀长度为 O(k)/项（第 k 项 ≈ k/10 字符）；`_check_total_chars(MAX_TEXT_CHARS=5000)` 仅在整个循环跑完后一次性触发。
- 触发：`document.xml` 为单部件、受 32MiB 流式上限约束，但 roman 列表前缀累积字符为 Σ(k/10) = O(N²)。实测几十 KB 的 document.xml（n=100000 项仅 ~37KB）即可在 post-hoc 校验前把前缀生成烧到分钟级 CPU：n=25000→3.1s、n=50000→14.2s、n=100000→70.7s、n=200000→>300s 超时（典型 O(N²)：N 翻倍、耗时约 4–5 倍）。
- 为何绕过既有闸：`_check_total_chars` 是 post-hoc——`root.iter` 虽惰性，但旧码循环体无提前退出，必摸满全部 N 个 `<w:p>` 并为每项生成 O(k) 前缀后才在末尾求和校验；字节上限（32MiB 部件）钳的是输入体量、钳不住前缀生成的平方级放大。
- 期望 vs 实际：期望编号前缀累积以 `MAX_TEXT_CHARS` 为界即止损；实际必跑完整循环、生成全部 N 项前缀后才校验。
- 修复落地（已实现）：`doc_ingest.py:359` 新增在途累计 `acc_chars`，`doc_ingest.py:364-371` 把段落追加收敛进 `_emit`——每追加一段即 `acc_chars += len(text)` 并 `_check_total_chars(acc_chars)`，越过 5000 立即抛，放大收敛为 O(bail²)（纯 roman 约第 274 项即越界）。合法文档（远低于 5000 字）行为不变，仅把同一 `文字过多` 报错前移；与 R4-01 pptx 增量卡同族。
- 回归（`test_doc_ingest.py` 38→40，+2）：`test_docx_roman_list_bails_incrementally_not_quadratic`（4000 项 lowerRoman 列表，监视 `_roman` 调用次数：新码越界即停 274 次 < n/4、旧码摸满 4000，throwaway 掐增量卡实证 `calls==4000` 判别器红）、`test_docx_roman_list_legit_still_correct`（3 项 roman 仍逐项成形 `i. / ii. / iii.`，增量止损不破坏正常路径）。fails-before 实证：注释掉 `_check_total_chars(acc_chars)` 后 `_roman` 调用 4000 次、判别器断言红。

## 第七轮续作（R7，2026-10-12）：UTF-16 DTD 拒绝旁路

用户已选择“继续软件复审与修复”（会话 `01a12f2d-968b-7de3-83e5-b6b222b8fa6d`）；本轮从该会话遗留的九项 UTF-16 回归继续，基线为 hub `97d2caa`。改动范围是 `mcp-server/hutuji_mcp/doc_ingest.py`、`mcp-server/tests/test_doc_ingest.py` 与相关文档，依据 `docs/agent-anti-drift.md` §2.2 的 MCP 白名单和 protocol §4.2 文档导入契约。

### R7-01 / P2：UTF-16 声明绕过 ASCII 预检（本地已修复，未部署）

- 根因类别：隐含假设（E）及测试覆盖缺口（D）。`_parse_xml_part` 只搜索 ASCII `<!DOCTYPE` / `<!ENTITY`，ElementTree 却按实际编码识别 UTF-16 并展开内部实体；外部 DTD 也被接受，违反已有统一拒绝口径。这里不声称 ElementTree 会读取外部实体或发生网络访问。
- 旧防线不足：ZIP/部件字节上限限制输入大小，正文字符预算发生在 XML 已经解析之后；Expat 的放大率限制也不等于“一律拒绝 DTD”。此前 R2–R6 的循环与聚合修复未覆盖这个编码差异。
- 修复：保留字节预检，公共入口改用 `_NoDoctypeTreeBuilder`；`doctype` 回调在内部子集及正文处理前抛出既有 `DocIngestError`，无新增依赖，合法 UTF-16 保持原样支持。
- 回归：修改前 `pytest -q tests/test_doc_ingest.py -k utf16 --tb=short -p no:cacheprovider` 为 **6 failed / 3 passed / 40 deselected**；内部实体及外部 DTD × UTF-16/BOM、LE、BE 均抵达了被禁止的正文提取。修改后文档解析套件 **49 passed**（新增九项）；拒绝文案仍包含“实体”。
- 同类面复查：本模块的 DOCX 主文档/编号、PPTX 演示文稿/幻灯片、XLSX 工作簿/共享串/工作表及关系文件都走 `_parse_xml_part`。MCP `llm_svg._validate_svg` 接收已解码的字符串并先拒 DTD/实体，未发现本次字节编码旁路；未改其行为。
- 防复发：在 protocol §4.2 固化“按实际编码识别、内部子集前拒绝”的约束，回归同时断言恶意输入拒绝时机与正常 UTF-16 兼容性。验证日志与门禁报告登记于 `results/local-only/2026-10-12-utf16-review/`；最终验证结果见该目录与 readiness 本批条目。

## 边界

本轮复审本体为只读；R2-01/R2-02（已提交 `6853c24` 并双机部署）之后，R3-01/R3-02/R4-01/R6-01（改 `mcp-server/hutuji_mcp/doc_ingest.py` 与 `mcp-server/tests/test_doc_ingest.py`）与 R5-01（改 `draw-portal/draw_portal/pipeline.py` 与 `draw-portal/tests/test_pipeline_raster.py`）各带 fails-before/pass-after 回归已在工作树落地，**尚未提交、未部署**。F-NEW-01 已在工作树由未提交的 quota 工作流（`draw-portal/draw_portal/quota.py` 新增 + `store.py` `reserve_usage`/`settle_usage`/`release_usage` 走 `BEGIN IMMEDIATE` 跨进程预留）实现，**但本轮未复跑 8 并发屏障探针复测、不作已修复结论**（该工作流非本轮产出，提交须按文件选择性暂存、勿与 R3/R4/R5 混并）。NR12-GRBL-01 撤回。原始 P3 级别与“双板重刷”建议不再作为行动依据。宿主探针/静态复核结果不等于实机故障或出货结论；实体纸面、4h/30 次老化、工厂配对与出货签核仍缺证，出货放行继续 Hold。
