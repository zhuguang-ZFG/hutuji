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

- 位置：`mcp-server/hutuji_mcp/doc_ingest.py:607`（**修复已落地**：改为对 `orig_to_out` 键集合与 `[r1,r2]` 求交迭代，次数有界于真实行数 ≤`MAX_TABLE_ROWS`）；原缺陷为 `for r in range(r1, r2+1)` 配无上限正则，`_merge_ref` 第 509 行 `r2 = int(m.group(4)) - 1`，正则 `(\d+)` 对行号不设上限。
- 触发：portal 文档导入上传含 `<mergeCell ref="A1:A999999999"/>` 的 xlsx；A 列 `c1=0 < ncols` 必进内层循环，单个 <100B 元素即触发。
- 期望 vs 实际：期望合并展开有界——同文件对**列**展开已用 `_XLSX_COL_SPEC_SPAN=64` 显式钳死（第 497–498 行注释「展开必须有界」）。实际 `r2` 直接取自无上限正则，`range(r1, r2+1)` 对单元素产生 1e9+ 次 `orig_to_out.get(r)` 空转；解析在 portal 同步导入路径执行，CPU 耗尽、worker 挂死，与已解析行数（≤`MAX_TABLE_ROWS=500`）完全脱钩。
- 测试覆盖：`test_doc_ingest.py:284` 仅覆盖 `A1:B1` 横向小合并（range 迭代 1 次）；无大范围/纵向合并回归。
- 修复落地（已实现）：内层改迭代 `orig_to_out.items()` 与 `[r1,r2]` 求交，与 `_XLSX_COL_SPEC_SPAN` 列展开上界对齐；回归 `test_doc_ingest.py` 的 `test_xlsx_merge_row_range_bounded`（单个 `A1:A100000000` 合并断言产物只覆盖真实两行 + 墙钟 <5s，旧 O(ref 跨度) 实现必红）。
- 残留收口（已实现）：单元素行范围封堵后，`raw_merges` 条数本身仍无上界——32MiB 部件可塞 ~1.2M 条 `<mergeCell>`，每条逐行求交即 O(merges×rows) 仍可打成 DoS。`doc_ingest.py:36` 增 `MAX_MERGE_CELLS=2048`（与 `_XLSX_COL_SPEC_SPAN=64` 同族显式上界），`doc_ingest.py:550-551` 累积即查、超限抛 `DocIngestError("合并单元格过多…")`，不等整段收齐再逐行展开；回归 `test_doc_ingest.py` 的 `test_xlsx_merge_cell_count_bounded`（`MAX_MERGE_CELLS+1` 条 `<mergeCell>` 断言拒绝，无上界旧码必不抛必红）。

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

## 边界

本轮及续作未修改任何产品源码、正式测试或部署文件；F-NEW-01 尚未修复，NR12-GRBL-01 撤回。原始 P3 级别与“双板重刷”建议不再作为行动依据。宿主探针/静态复核结果不等于实机故障或出货结论；实体纸面、4h/30 次老化、工厂配对与出货签核仍缺证，出货放行继续 Hold。
