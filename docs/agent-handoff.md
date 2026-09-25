# Agent 接手防呆页

> **给下一个 AI / 人类代理**：先读本页 5 分钟，再动代码。  
> 状态真值：`release-readiness.md`。契约真值：`protocol.md` **v0.5**。  
> **完成定义 / 可跑门禁**：`agent-gate.md` + `python scripts/agent_gate.py`。  
> **防幻觉 / 禁乱改**：`agent-anti-drift.md` + `agent-edit-allowlist.json`；三仓矩阵 `agent-constraint-matrix.md`；脏树见 `worktree-inventory.md`。  
> 本页只消除「不明所以」——不新增契约，不宣布出货。
> **2026-08-20 MCP 取消重试修复（生产基线 `7eac656`）**：完整 `hutuji_draw` 事务改由独立任务持有，云端取消只结清 waiter，同题重试复用同一个限流名额与付费生成；位图 single-flight 扩到 PNG→描摹→缓存。仓内 863 项 + e2e PASS；生产三文件 LF/hash 对账、双服务重启、两条 ExecStartPre 与 preflight 26/26 均通过。标准 MCP cancel 两次后第三次接管成功，三次 call 仅一轮生图对冲。设备协议/固件未改，当前 UI/HIL 缺口不变。
> **2026-08-23 当前 S3 候选（HEAD `72a693a`）**：本条为全链审计复核后的现态指针；下条 `f1aa0e7` 快照保留为 BLE 生命周期代码证据，不删除。`72a693a`（在 `cb82edb`/`f1aa0e7` 之上）已推 `feat/hutuji-usb-pipe`、已刷 COM14 三轮画图 HIL 通过，host 131/131；板上镜像 SHA-256 `93b61d5b…afa9`（`git merge-base --is-ancestor` 实测 `cb82edb`/`f1aa0e7`/`5e1a91b`/`dcbec6f` 均为其祖先）。**仍未验证**：预览确认/取消与暂停/继续/停止/重画/试笔五个触控动作 HIL、纸面画质目视、断连恢复 `paper_active` 窗口 abort。审计另登记：PSRAM 预取接管所有权缺口（重复新画累积 PSRAM，源码审查，待独立批次修复）、`xiaozhi_console.py` 缺 `preview_url`。BLE-DIAG 阶段 A 证据见下条历史快照与 `worktree-inventory.md`。
> **2026-08-24 Quick Draw 本地缓存落地 + G-code 产生链 Java 参照 review（生产基线 `eccccf1`，已部署阿里云）**：缓存命中旁路不占限流名额、类目映射 v2 对账官方 345 类（199 键→145 类）、prep 固定种子蓄水池可复现；数据 145 类×200 条约 11MiB 已上 `/opt/hutuji/quickdraw/`，生产冒烟 hit/miss 双过（preflight 20/30 经置空 env A/B 证实为阿里云基线漂移，与本批无关，另案回查）。**G-code 产生链 review**（参照 `decompiled_full` 树 + 缓存产物模板）：Stepper 笔控字面量（`machine/grbl/a.java:262-284`）与 `hutuji_gwrite.toml` 逐字一致；`G92 Z0` 首笔控旁路（`:250-260`）、归位 `G90G0 X0Y0` 文件内省略（S3 旁路 G1 归位）、0 M 码、出图路径无 G4、每行带 F、Z 一位小数/XY 三位小数全部对齐或有意差异有锚点；当前真实产物（Quick Draw 猫）头尾字节级复核一致。发现 2 条：①尾部双抬笔（最后 segment 的 line_end + document_end 各一条幂等 `G1G90 Z0.0F10000`，比奎享产物多一条，25ms 级，**有意保留**作恒抬笔兜底，不改锁定模板）；②findings/23「全树 G4 字面量零匹配」表述不精确——`programme/actions/machine/FirmwareDelayAction.java:31` 有一条手动编排延时动作 `G4 P`，不进任何出图文件，结论不受影响。review 轮另修 3 条并已部署：`quickdraw_prep` 断流重试上移到整类目级（同种子产物逐字节不变，证在跑数据零污染）、`_NO_STRIP` 拦截画字名词语素（「画家」曾被剥成「家」错配 house）、`_load_entries` 数据边界拒收非有限坐标。门禁 hub PASS（pytest 954/954 + e2e）。**同日 v3 扩类**（用户提出「要更多主题」）：372 键→298 官方在册类目（官方 345 的 86%，含 cell phone 修正）；「手机」改指 `cell phone`（v2 误指 `telephone` 座机）；成人向/无类目主题（wine/rifle/恐龙/爱心等）仍刻意留空走 AI。


## 0. 一句话现状（2026-08-14）

> **2026-08-14 第十四轮深度 review 收口（软件改动，未部署；无硬件面变化）**：8 路只读审查（S3 哑管道固件×仿真对账——首轮超时拆两半重放 / Grbl §9 / 测试盲区枚举 / asyncio / deploy bash / 编码边界）→ 33 条报告主代理逐条实证：**采纳 8 条、驳回/登记 25 条**。采纳：`_CONTROL_CHAR_RE` 扩 Cf 全族（U+202E 双显覆盖/零宽/TAG 等流入生图 prompt 面）、grbl_sim 模块 docstring 勘误 + [ESP901]/授权门登记为建模边界、install-from-repo.sh venv no-op 注释、四个零覆盖分支补钉（G2/G3 缺 F、/files 读中 OSError 塌 404、TTL `>=` 边界、多 svg 块取首个）。驳回侧要点：S3Flow 七条全系层次误置（把真机固件路径当 e2e 面，自报 P1 亦属此类）、TG-04 前提实测证伪、DEP-04/05 撞子 shell trap 语义。§9 五条契约与实现逐条核对全部落地；固件侧四条观察项登记待下次固件窗口。pytest **773** 全绿 + e2e PASS + hub 门禁 12 层 pass。findings 见 `results/round14-findings.md`（本机）。

> **2026-08-14 第十三轮深度 review 收口（软件改动，未部署；无硬件面变化）**：6 路只读审查（G-code 链 / 位图链 / server / 桥接 / 仿真×实机固件 / 门禁×preflight，GitHub 上游 issue × venv vendored 源码 × `docs/kxnx/` 逆向交叉）→ 34 条报告主代理逐条实证：**采纳 14 条、驳回 20 条**。P2×4：grbl_sim `'M 30'` 行内空白按真机 `collapseGCode` 建模（真机剥空白后=M30 触发换纸，仿真曾回 ok）、换纸窗口内 `G21` 按真机「行首首位数 0-3」谓词回 error:8、门禁零宽字符逃逸三层扫描（`_INVISIBLE_RE` 剥离接入五层）、全角冒号引用见即判红。P3×10：accept 重登明细、preflight `/files` 拒 3xx、桥接云端帧换行规整、看门狗注释勘误（进度流误杀今日不可达，登记 progressToken 义务）、status 列表 to_thread、cleanup 移出 slot（AGENTS.md 链路口径已同步）、竖线零跨度回归、整圆 epsilon `<=` 对齐固件、toml↔校验器 F 值交叉钉死。位图路 10 条全驳（含 2 条幻觉引用不存在函数，逐条证伪）。pytest **768** 全绿 + e2e PASS + hub 门禁 12 层 pass。findings 见 `results/round13-findings.md`（本机）。

> **2026-08-14 第十二轮深度 review 收口 + 当日部署（31 条：P1×1/P2×4/P3×26，全部修复并发布生产；无硬件面变化）**：6 路并行审查（GitHub 社区上游 × 奎享逆向 Java 交叉）→ BRG-11-01 [P1]（云端可下发超 CPython int↔str 4300 位上限的整数 id，`json.loads`/`int()` ValueError 击垮 pump task）等 31 条当日全修，提交 `1082400` 已推 origin/main。本仓落 `cloud_bridge.py`/`bitmap_svg.py`/`gcode_gen.py`/`llm_svg.py`/`server.py`/仿真三件套/`deploy/preflight.sh`（25→26 项：is-enabled 开机自启 + 8300 监听者 `ss -lntp` 身份核验 + `requirements.txt` mtime）等 26 文件。pytest **759** 全绿 + e2e PASS + hub 门禁 12 层 pass。**2026-08-14 13:57–13:59 CST 已部署**：8 产物 LF-md5 对齐上机、显式重启两服务（主进程 2017026/2017028，两条 ExecStartPre `status=0`）、生产 `pass=26 fail=0 expected=26`、e2e PASS、真 draw「五角星」全链 228 行/5418B 公网 `HTTP=200` 复验、journald `Received session ID:` 已 `<redacted>`；回滚副本 `/root/hutuji-bak-20260814-135642-pre-1082400/`。证据见 `release-readiness.md` 文首 2026-08-14 条。

> **2026-08-11 第二/三轮修复全量部署 + 生产可执行面 md5 全量对账（`05786ce`，生产 `pass=23 fail=0 expected=23`；无硬件/固件变化）**：本轮起点是文档漂移而非漏部署——`server.py`/`bitmap_svg.py`/`cloud_bridge.py`/两个 unit 实际已领先于文档登记的 `f25d8d1`；真实应用基线是 `a5ae774`，部署前先取基线证据 `pass=22 fail=0 expected=22`（22 项版本下无失败被掩盖）。本轮实发 **6 个产物**（`a5ae774..HEAD` 可执行差集 5 个 + 盘点新发现 1 个）：`bitmap_svg.py`（注释补 httpx `timeout=` 慢滴攻击说明）、`llm_svg.py`（深度守卫 `depth > 64` → `>=`，**唯一改变拒收面的产物**）、`cloud_bridge.py`（`HEALTHY_SESSION_SECONDS=60`、`load_config()` 拒 userinfo、`_message_label()` 补捕 `RecursionError`）、`preflight.sh`（22→23 项、`find -print0`、`stat -L -c %Y`）、`nginx-hutuji.conf`（`server_tokens off;`）、`install-from-repo.sh`（去 `enable --now` → `enable` + 显式 `restart`）。部署步骤：LF 归一化 → scp → daemon-reload → 显式重启两服务 → preflight。**`pass=23 fail=0 expected=23`**；`server_tokens off` 两向复验（裸 `Server: nginx`，`:80`/`:443` 均无版本号泄露）；真产物 E2E（`draw_20260811_235518_…gcode`，13 行/219 字节/4 pen_lifts，HTTPS `200/219B/CRC32 bc057220`，§5 全量符合）；公网面 `/mcp`/`/docs`/`/redoc`/随机路径均 `404`，无自指 301 循环。回滚副本 `/root/hutuji-bak-20260811-234834-pre-05786ce/`。另回填三次漏记部署记录（`0db62f5`/`1f8ec57`/`a5ae774`）至 `deployment.md`，并修正 `release-readiness.md`/`worktree-inventory.md` 的漂移状态描述（旧文保留为历史证据，未删）。**本轮零硬件/固件变化。** `docs/agent-gate.md` §5 禁止宣称表所有 8 项仍受约束。
> **2026-08-13 第十轮深度 review 收口（软件改动，未部署；无硬件面变化）**：5 路只读侦察 + 主代理独立复现 → P1×4 + P2×2 + P3 约 20，全部修复。本仓 8 条：`bitmap_svg.py`（sknw 骨架像素闸堵整进程崩溃 / `multi=True` 修 63% 丢线 / POST 流式上限 / try 包描摹段 / 取消路径）、`requirements.txt`（sknw>=0.15）、`gcode_gen.py`（弧 epsilon 对齐固件 5e-7 / 零运动闸 / 行拆分与控制字符门 / vpype_cli 回落）、`cloud_bridge.py`（Session-terminated 判死重连 / watchdog 300s / session-id 脱敏 / EOF 残行）、`server.py`（session_idle_timeout / HEAD /files / guard 拦 websocket）、`deploy/hutuji-cloud-bridge.service`（PartOf）。xiaozhi 4 项已提交 `68a1eef`（R10-S3-01 暂停期收分支冻结计时 + 超时统一收敛；R10-PIPE-01 授权探测有界重试；S3-P3 群；host 16 tests OK + ESP-IDF v6.0.1 构建成功）。Grbl 零改动。KX-P3a/b 勘误入 `docs/kxnx/`。pytest **674** 全绿 + e2e PASS。findings 见 `results/round10-findings.md`（本机）。**2026-08-13 13:31–13:36 CST 已烧录+部署**：`68a1eef` app-flash COM12（串口自证七工具 + ELF SHA `2c17f4e27…` 对齐，板上行为基线现为 `68a1eef`）；R9+R10 共 11 个产物 LF-md5 对齐上机，应用基线 `26d220a`→`5104b93`，`daemon-reload` + 显式重启两服务，preflight `pass=25 fail=0 expected=25`，公网四路 404 复验过。**13:46 CST 写字机上电后已实测**：板上 `68a1eef` 连接/banner/授权探针全链路通过（含 Telnet 槽位忙退避接管），fresh `Idle` 探活正常。仍未验证：R10-S3-01 暂停 ≥60s 实机用例、七工具云端逐项调用、纸面回归（均卡真实设备 token）；证据见 `release-readiness.md` 顶部部署条。
> **2026-08-13 第九轮深度 review 收口（软件改动，未部署；无硬件面变化）**：6 路只读侦察（仿真三件套 / server-config 剩余面 / 依赖 advisory / 奎享 Java 逆向×S3 交叉 / 契约三方对账 / scripts+deploy）→ **无 P0/P1**，1 条 P2（grbl_sim 契约边界）+ 15 条 P3，全部修复。本仓 14 条：sim 三件套（grbl_sim `_contract_violation` §5 契约边界 + error:8/90 语义 + MAX_TLNT_CLIENTS/空闲超时；pipe_sim error:8 重发 + 行内注释剥除；sim_server e2e 端口互斥）、config（loopback 判据统一 / 空白 OUTPUT_DIR fail-closed）、console（成功路径 JSON 守卫 / JWT 形状脱敏 / 退出码统一）、mcp unit StartLimit、capture `--note` 误报、README 条数。xiaozhi 2 条已提交 `1b05f99`（换纸行分支 expect_blocking_peer + ok 兜底 DrainResponses），**未烧录**（板上 S3 行为基线仍 `16f2f83`），ESP-IDF v6.0.1 release 已重建。OSV 13 包 advisory 零命中（闭合 R7 部分覆盖缺口）；§5 三方对账零漂移。pytest **645** 全绿 + e2e PASS。~~生产应用基线仍 `26d220a`，本批未部署~~ **【supersede】已于 2026-08-13 13:35–13:36 CST 随 `5104b93` 与第十轮一并部署**（生产 `pass=25 fail=0 expected=25`，见 `release-readiness.md` 文首 2026-08-13 部署条；当前基线已再推进至 `1082400`）。findings 见 `results/round9-findings.md`（本机）。


> **2026-08-09 白天联调 runbook 基线纠偏（纯文档，无代码/固件/部署动作）**：`docs/daytime-tuning-runbook.md` 文首基线块与第 0/4/5/8/9 步此前仍钉 2026-07-31 的待烧目标（Grbl `12f26ac`、S3 `255f133` + `f1cf066`）。`git merge-base --is-ancestor` 实测**三者全是板上基线的祖先**（`12f26ac`→`f5c8985`；`255f133`→`f1cf066`→`55574cb`→`16f2f83`），**照原文执行 = 固件回退**：Grbl 会丢 §9-A2 Telnet 序列化（`fdf2ad8`）、非阻塞 write（`1b56393`）、§9-C′ 收窄（`4ddc945`/`1a4fea1`）；S3 会丢 11 个 pipe 契约加固提交。已就地纠正 8 处：文首加 2026-08-09 复核块（本仓 HEAD `94004b9`，板上 = Grbl `f5c8985` + S3 `16f2f83`）；第 0 步钉死 SHA 换成防腐断言（`log --oneline -1` 对齐 readiness、`status -sb` 无 ahead/behind、`GetPortNames()` 每次认口）；第 4/5 步标「板上已是…，正常联调日跳过」并保留换板/回退用法（Grbl 源码 HEAD `966723b` 相对 `f5c8985` 只差 `src/Protocol.cpp` 一处注释，重烧零行为差）；第 8 步注明修复点已含于 `16f2f83`；第 9 步标「已于 2026-07-31 取证」并纠正机制——§8.5 脚手架在当前 HEAD 是 `main/CMakeLists.txt` 的 `option(HUTUJI_AUTO_TEST_ABORT_ON_PAPER … OFF)` **构建期开关**，不是 `f1cf066` 的固有行为，复测须**从当前 HEAD 带 `-D…=ON` 构建**、禁 checkout 旧 commit，且备份→烧→取证→回刷顺序不变；第 10/11 步把「本轮真正开放项」收窄为 **readiness 第 10 条（App 真实语音，卡用户重取 token）+ S4 纸面画质人工目视**，第 7/8/8a 条与 S4 出图链路均已有历史取证、禁止当待办重做。本仓 `hub` `overall=pass exit=0`（pytest **414 passed** + e2e `PASS`）。注意该 runbook **不在** `agent_gate.py` 的 `GOVERNANCE_DOCS`（17 份）内，其状态措辞无机器把关，改它必须人工对账 `release-readiness.md`。当次 `[ESP901]` 回 `Paper=No MotorEn=On PanelHold=Off Changing=Off`，故第 6/7 步（有纸联调）仍卡在用户装纸。

> ↑ **同日 supersede（上条两处细节）**：①上条记「文首加 2026-08-09 复核块（本仓 HEAD `94004b9`）」，该 SHA 当日即被后续纯文档提交顶掉。已把 runbook 文首**改为不钉 HEAD SHA**——现取 `git log --oneline -1` 并与 `release-readiness.md` 文首最新条对齐。教训写进原处：钉 SHA 正是让该 runbook 过期 9 天的原因，**别再往文首钉 SHA**（板上固件 SHA 例外，那是实测事实，且另有串口自证兜底）。②上条记「该 runbook 不在 `GOVERNANCE_DOCS`（17 份）内」——**已补入，现为 18 份**。理由是它决定「哪几步真机验证可以跳过」，写出「已完成/可跳过」后续代理就不再上真机复验，与当初漏扫 `CLAUDE.md` 同一形态。走 RED→GREEN：先把它钉进 `test_gate_governance_docs_cover_repo_governance_surface` 的 `must` 清单取 RED（`AssertionError: docs/daytime-tuning-runbook.md`），再改 `scripts/agent_gate.py` 转 GREEN；该文零禁语命中、三条复活正则零命中，两层扫描 pass，pytest 仍 **414 passed**（改的是既有测试，条数不变）。**但门禁只查禁语与复活词，查不出「已完成/可跳过」是否属实**——改 runbook 状态措辞仍须人工对账 `release-readiness.md`。

> **2026-08-09 控制台 token 已续期 → 知识库真同步 + 召回复核通过（纯文档回填，无代码/固件/部署动作）**：runbook 第 1/2 步已执行并通过——`GET /api/agents` 回 200（智能体 `2094251` 呼图机，设备 1/1 在线，`knowledge_base_ids=[71153]`），`sync 2094251` 成功；**同步前**云端 KB 文档时间戳仍是 `2026-08-07 11:32:37 +0800`，早于 `ee2ba4e`/`ef67df9`，即 08-08 那组契约纠错（越界整幅拒绝、纸不动首因是漏调 `hutuji.draw(url)`、换纸中停止要等本次换纸完成）此前一直没上云，本轮才真正生效；同步后三类问法召回逐条命中标记。故 `release-readiness.md` 第 10 项的「2026-08-08 知识库同步缺口」**已闭合**，该项余下阻塞只剩 **App 真实语音两步衔接** 与 **`XIAOZHI_DEVICE_TOKEN`**。**口径提醒**：`71153` 是知识库**容器** id（`update-kb`/`recall` 的参数），`63334`/`65569` 是**文档** id，`update-kb` 删旧上新故文档 id 每次都变（本轮新号未单独留证），勿混用。同轮两个**未定因**观察：①首轮 `recall` 三问全回 `401 Invalid token`，紧随的 `list` 回 200、重跑 `recall` 即成功 → 非 token 失效，疑为新建文档后 `recall-test` 瞬时拒绝；②S3→Grbl 与 S3→小智云**同时**瞬断约 158s 后自愈（uptime `35420290`→`35578250`ms 单调递增无重启，Grbl 侧 `[ESP901]` 可答且 :23 槽位空闲），属 WiFi 层瞬断，**不得**据此判 readiness 第 7 条断连重发（本轮没跑图、没验重发）。步骤 6/7 仍卡「用户装纸」：`[ESP901]` 持续 `Paper=No MotorEn=On PanelHold=Off Changing=Off`。公网面只读复核四项均 404（`/mcp`、`/files/nope`、`/docs`、随机路径）、明文 `:80` 回 301 精确指向 HTTPS，**但这不等于** 21 项 preflight 复跑（本轮未 SSH 上机）。

> **2026-08-08 最新 S3/发布包基线（supersede 下方旧烧录状态）**：xiaozhi HEAD `16f2f83` 已推远端、工作树无改动；host 13 tests OK。ESP-IDF v6.0.1 标准 release 已完整重建，app/merged/ZIP SHA256 分别为 `EAE65EB0...70ED8` / `6A5102D4...E3EC4` / `53BB05ED...2FF33`；最终 app 已写入 COM12 并通过 hash verify，板上 ELF SHA `62e1a12e5...` 与发布构建一致。七个 `hutuji.*`、WiFi、MQTT、Grbl Telnet、`M5`、fresh `Idle` 与行首 `G1 G53 X0.000 F1500` 授权探针均通过。当前 HEAD 的错误 CRC 路径也已用构建开关探针实测：48B 完整下载后置 error，零 `G92`/streaming/`M30`/Run，COM13 同期 174 条状态均 Idle/MPos=0；随后已回刷正式 app，板端 ELF 恢复且 `AutoTest` 0 命中。当前仍缺真实 `XIAOZHI_DEVICE_TOKEN`，所以正确 CRC、窗口应答配对、有效文件出图、纸面目视和 App 两步语音尚未复验；启动时写字机连接通知偶发早于 MQTT 的竞态也保留为观察项。详见 `release-readiness.md` 顶部与 `worktree-inventory.md` 最新条。

> **2026-08-06 Grbl GitHub/社区复核结论（只读，无固件改动）**：针对「S3 修复而 Grbl 未改是否有遗漏」复核本 fork、`bdring/Grbl_Esp32` 上游与社区报告。结论是维持 Grbl `966723b`：255 字节 payload 丢行与 CRLF 双 `ok` 均来自上游接收语义，生产链已分别由 MCP 限制 254 字节和 S3 重切行封住；verbose error 与授权探针顺序同属客户端兼容责任。Telnet 社区已知的 `pbuf_free`/长任务停顿风险，本 fork 已有 §9-A/A2 的四参数 keepalive、递归锁和 `MSG_DONTWAIT` + 1s 绝对 deadline。`upstream/main...HEAD` 为 `0/101`，没有漏合并的上游提交；Grbl 与 S3 工作树均为空。重新执行 fz `standard` 得 `overall: pass exit=0`（optional Wokwi 层失败不改变该 profile；host SIL 不替代实机）。因此不扩大 §9 白名单，不改 `LINE_BUFFER_SIZE`、错误格式或 CRLF 接收逻辑。
> **2026-08-06 生产部署验真与真机下载（S3 COM6，Grbl COM12）**：生产更新 `server.py`/`preflight.sh` 后首次重启尚未收敛，preflight 短暂为 `pass=12 fail=6`；等待 WSS、8300 与 MCP 会话稳定后重跑 **`pass=18 fail=0 expected=18`**。生产 venv 生成 14 行/243 字节合法 G-code；S3 通过生产 HTTPS 下载，日志记录 `下载完成 243 字节 crc_hdr=775b4f31`，完成授权后逐行发送、`Run→Idle`、`M30`、`[PaperStatus] 0`、最终 `state=done err= replayable=1`。真机下载、CRC、逐行转发和有纸换纸均已通过；出现多次普通 1s 应答等待超时但最终收敛，需后续专项调查。App 语音、纸面画质目视与出货签核仍未验证。

> **2026-08-06 Grbl 恢复后的 S3 联网/授权探活（COM6 + Grbl COM12）**：写字机恢复上电后，`192.168.1.13` ping 成功，S3 日志显示缓存 IP 命中并连接 `192.168.1.13:23`，收到 `[VER:1.3a.20211103:]`、`[OPT:PHBSW]`，发送 `M5` 得 `ok`，收到 fresh `<Idle|MPos:0.000,0.000,0.000|Bf:249,1200|FS:0,0|Pn:0>`，随后发送 **`G1 G53 X0.000 F1500`** 得 `ok`，日志明确记录「授权探测通过」。本次确认 S3 实际执行了更新后的行首 `G1 G53` 探针，且 Grbl Telnet/授权/空闲探活闭环通过；尚未执行下载文件、CRC、verbose error、暂停/恢复、换纸或纸面出图。

> **2026-08-06 S3 实机复核（COM6，源码 `16f2f83`，镜像 SHA-256 `AE634CD7FE7A6CB45A1F744799C6FB94C693C0FD83DE0E49DD8FC07EC024C132`）**：ESP-IDF v6.0.1 `app-flash` 成功，写入 `xiaozhi.bin=0x2cfdb0` 并报告 `Hash of data verified`；启动日志确认 ESP32-S3、8MB PSRAM、SKU `lichuang-dev`、七个 `hutuji.*` 工具、WiFi 获取 `192.168.1.12`、MQTT 激活成功，无 panic/assert/backtrace。S3 随后按 1s/2s/4s/8s 退避连接写字机并扫描 `/24`，但当前 `192.168.1.13` ping 不通，PC 对 `192.168.1.13:23` 的 `connect_ex` 为 `10035`；因此未到 Grbl banner，`ready/authorized`、`G1 G53` 授权探针、verbose error、CRC/逐行流控及实机出图均未验证。S3 端口为 COM6，Grbl 串口为 COM12（CH340K）；下一步先恢复写字机供电/WiFi/Telnet，再继续协议实测。


> **历史快照（2026-08-06；已被后续部署/烧录记录 supersede，仅保留审计过程）**：以下两条中的「未部署/未烧录/未验证」只描述当日状态，不是当前状态；当前生产、固件与 HIL 真值以本页顶部、`release-readiness.md`、`worktree-inventory.md` 为准。
> **2026-08-06 逆向/社区参照深度审查 + 服务出口审查（仓内，未部署；preflight 17→18 项）**：分两轮。**第一轮**（参照 GitHub 上游、社区、逆向文件）四条发现全部实证复现：①**Grbl 行长上限是 254 字节 payload、不是 255**——`Protocol.h:34` 是 `LINE_BUFFER_SIZE 256`，但 `Protocol.cpp:68` 的溢出判定排在换行判定**之前**，第 255 个 payload 字节能进缓冲，紧随的换行符先撞 `len == 255` 回 `error:11`，`:218` 的 `empty_line()` 把**整行丢掉、从未进 `execute_line()`**：旧校验器 `> 255` 会放行这种「设备静默吃行」的产物。上游 grblHAL 把 `LINE_BUFFER_SIZE` 定为 257（`protocol.h:36`，注释 "256 characters plus terminator"）正是为绕开这个 off-by-one。已改 `MAX_LINE_PAYLOAD_BYTES = 254` 并补两条测试（含一条逐字转写固件收行逻辑的独立复现）。②**CRLF 会让设备每行回两个 `ok`**（`\r`、`\n` 各触发一次 EOL，空行也回 ok）——奎享真实产物就是 CRLF（946 CRLF / 0 裸 LF），Windows 上 vpype gwrite 同样产 CRLF。当前 S3 安全靠 `ParseLines()` 自己重切行，但该不变量此前只存在于实现里；已写进 `protocol.md` §3：**S3 必须重切行、禁止原样透传下载字节**，否则在途窗口计数正向漂移。③**`$Errors/Verbose=1` 时错误码变文本形态**（`Report.cpp:236` 回 `error: Line too long` 而非 `error:11`），只认数字不是降级而是**行为反转**：`error:8`（换纸推迟需重发）解析成 -1 → 整单失败；`error:110`（未授权）落进未知故障。该 `$`-设置存在 NVS、可被任何上位机改写。已把 `ParseErrorCode` 提成 `hutuji_recovery_core.h` 的 `ParseGrblErrorCode()` 并补五条文本映射（字面量逐字对齐 `Error.cpp`），由 host 编译单测钉住。④**奎享工作在负 X 坐标系**（763 个 X 值里 762 个为负），我方硬拒负坐标——这是无限位开关下正确的 fail-safe，但「有意差异只有收尾」的记述漏了这一项，已补进 `findings/17` §5 与 `alignment/plan.md`；**奎享产物不能直接当回归基线**。**第二轮**（MCP 服务 HTTP 出口）：**FastAPI 自动文档端点未关**——`/docs`、`/redoc`、`/openapi.json`、`/docs/oauth2-redirect` 实测全回 200，公网访问不到但那份安全**完全来自 nginx 兜底**、应用层零防御，且 preflight 与 pytest 都无判据会红（对比 `/mcp` 有三层保护）。已传三个 `None` 关掉，并新增 preflight 第 18 项**直连 `:8300`** 验它们必须 404（经 nginx 探证不到应用层）。另两条查实为非缺陷、已记文档：`HEAD /files/` 回 404 而非 405（S3 只用 GET，`hutuji_job.cc:3181`）、`/docs` 经隧道的 301 自指环（`nginx-hutuji.conf:225` 与 `troubleshooting.md:268` 已知，是为「`:80` 必须能服务 `/files/`」刻意保留）。`hub overall=pass`（pytest **296 passed**、e2e `PASS`）；变异验证：行长口径回退 2/2、verbose 文本表删除 1/1、docs 开关删除 1/1、新 preflight 项三种假绿形态 3/3。收敛中修掉三处自己的重构债（helper 依赖隐式 `self.compiler`、无用默认参数、空值路径靠巧合）。**未部署、未烧录、未做硬件验证**；第 18 项与 S3 侧改动都未在生产/实机验过。
> **2026-08-06 S3 复审收敛（已构建，后续刷写/启动见上方最新条）**：在上述服务审查后继续核对真实授权/下载/流控链，修复 `G53 G1` 绕过 Grbl 行首授权门、CRC 头宽松解析及遗留双状态、生产 capability URL 接受 HTTP/非 443/authority 混淆、512B 窗口配 32 深应答队列会丢 error、verbose error 数字分支溢出/尾缀误判。当前生产 origin 仅接受 HTTPS 默认 443 或显式 `:443`，HTTP 仅留 RFC1918 联调；队列由窗口常量推导为 255。证据：非 443 用例修复前 RED、修复后 GREEN；xiaozhi host **13 passed**；C++ formatter check 通过；ESP-IDF v6.0.1 全量构建成功（`xiaozhi.bin=0x2cfdb0`，app 分区余 29%）；本仓 full overall=pass（pytest **296 passed** + e2e `PASS`）；fz standard overall=pass。该镜像后续已刷写并完成 S3 自身启动验证，但因 Grbl 网络不可达，协议与出图实测仍阻塞，见上方最新条。


> **2026-08-05 门禁自身深度审查修复（仓内，未部署）**：查出并修掉两个让门禁可假绿的同源缺陷（均实测复现）：①`claims_forbidden` 8 条禁语表此前只做列表漂移比对与报告打印，**从未扫过文档正文**——在 `README.md` 写一句含三条禁语的完成式断言，`docs` 层全绿；②三条 `REVIVED_PATTERNS` 各带手写文件清单、合计只覆盖 5 份，`CLAUDE.md`（OVERRIDE 级指令）与 `.cursor/rules/` 硬轨都不在其中，`ch9350` 规则连 `protocol.md` 都不扫。修复引入 `GOVERNANCE_DOCS`（17 份）作两层共同扫描面 + 新增 `forbidden_claims` 层，判据区分「做出宣称」与「引述规则」（整句语境窗口 + inline code / 定义表格 / 否决标题清单放行）。`hub overall=pass`（pytest **281 passed**、e2e `PASS`），**变异验证 13/13**（10 真违规判红、3 合法引述放行）。**新增治理文档必须加进 `GOVERNANCE_DOCS`**，否则两层都绕过它。收口后自查又查出第三处：为放行仓内真实免责清单而放宽的 `_has_rejection_heading`（同行任意禁令词 + 冒号即豁免整行余下内容、句窗跨冒号借否定词）把判据偷换成「附近有没有否决词」，开出 5 例假绿（同行禁令掩护冒号后的真主张 ×2、禁令清单尾部混采纳句、否决词标题下讲采纳实现、引用块内真复活）；已改为「命中短语自身含采纳断言词即不豁免」+「否决词须紧贴冒号，中间只容粗体与括号旁注」，18 例判据探针与 13 例变异验证全对，`hub overall=pass`（pytest **286 passed**、e2e `PASS`）。**改这两个豁免前先跑 `test_gate_rejection_heading_does_not_shield_adoption_claims`**。未部署、未改固件、未做硬件验证。
> **2026-08-05 「文件对了但跑的不对」三处修复（仓内，未部署；preflight 15→17 项）**：主题是**体检绿 ≠ 设备拿到的东西对**。①**生成链盲区**：旧 15 项没有一条执行过部署的 `hutuji_mcp` 代码，下载探针用 `printf "G21\nG90\nF8000\n"` 人工拼产物，那三行连 `validate_gcode` 自己都不接受，只证明了「nginx 能按字节发文件」。实测：移走 `hutuji_gwrite.toml` 后 `import hutuji_mcp.server` 仍成功、服务保持 active、`/mcp` 与 `/files` 照常响应，而真实 `svg_to_gcode()` 报 `vpype 转换失败（exit 2）`——**`pass=15 fail=0` 与「一画就整单失败」可同时成立**。根因：`_PROFILE_PATH` 只被请求期的 `_vpype_command()` 用到，`vpype` 更是 subprocess、从不 import。新增 `check_generate_chain` 用**生产 venv 解释器**（不能换系统 `python3`，`_vpype_cmd()` 按 `sys.executable` 同目录找 vpype）真跑一次 `svg_to_gcode()`，下载项改用该链的真产物验 200/长度/CRC32/正文。②**§5 字节口径**：`_read_generated_gcode` 用 `read_text()` 读产物，universal newlines 把 CRLF 塌成 LF，每行少算 1 字节。实测某 20846 行 CRLF 产物磁盘 **524369 字节（超 512KiB 上限 81 字节）被判为未超并放行**，回给 S3 的 `bytes` 少报 20846；512KiB 是 S3 侧 PSRAM 硬约束，少算即把越限产物放给设备。改为 `read_bytes()` + 显式 `total_bytes=len(raw)`。**一条既有测试原本正钉着错口径**（拿 `read_text()` 的 243 对比真实 257），已改为断言磁盘真实字节。③**unit 漂移**：`is-active` 只证明在跑，不证明跑的是哪一版——加固项可全缺而照样 active，忘了 `daemon-reload` 时磁盘一致而在跑的是旧快照，drop-in 更能静默覆盖 ExecStart。新增 `check_units_match_templates` 对两个 unit 各查 md5 一致 / `NeedDaemonReload=no` / `FragmentPath` / 无 drop-in（glob 判定，不能只信 `DropInPaths`，老版返回空串即 fail-open）。`hub overall=pass`（pytest **292 passed**、e2e `PASS`）；变异验证 11/11（profile 与 `F8000`→`F6000` 各 1、字节口径回退 4、unit 四条判据加漏查 bridge 共 5）。**已于 2026-08-06 随 `69c2198` 全量对齐部署并推送**：生产 preflight `pass=17 fail=0 expected=17`（生成链与 unit 一致性两项**首次在生产真跑通过**）、生产 e2e `PASS`、四服务 active、bridge ready 文件 == MainPID、站外 404/404/403/000；回滚副本 `/root/hutuji-bak-20260806-69c2198/`。本次同时补发了 `bdbb107` 之后积压的 unit 加固、nginx 开放重定向修复、bridge ready 机制、`llm_svg` 流式读取、依赖上界与两个安装脚本；profile 落笔 F1000→F10000 **会改变纸面笔迹**（已获授权，未目视验证）。**未改固件、未烧录、未做硬件验证。**
> **2026-08-05 部署面深度审查修复（仓内，未部署）**：`deploy/`（preflight 活动 ACL 判据消歧 + allow/deny 顺序、删 `OPTIONAL_SKIP`、公网前缀拒私网、`/mcp` 谓词收紧、nginx 301 去 `$host`、安装脚本去 `bash -lc` 并清 `PIP_*`、chown 顺序、两个 unit 加固与 `StartLimit*` 移入 `[Unit]`）+ `requirements.txt` 上界 + 契约测试同步。**核心发现**：「明文 `:80 /files` 须 403」这条铁律此前在 preflight 与 pytest 两层都因 `deny all;` 在 `limit_except` 行内重复出现而无法被执行，删掉 ACL 兜底 deny 后公网可读却两层全绿（已实测复现）；`OPTIONAL_SKIP` 又让唯一验证它的探针可以静默跳过并计入放行。本地 hub `overall=pass`（pytest **262 passed**、e2e `PASS`），ACL 与 301 均做过变异验证。**未部署、未改固件、未做硬件验证**；生产仍 `bdbb107`。
> **2026-08-05 审查与部署基线（覆盖下方历史“当前 HEAD/已合流”措辞）**：hutuji 完整 `full` 门禁的最后已验证提交为 `c434831`（门禁文件读错收敛为结构化 fail，pytest `261 passed` + e2e `PASS`；前一已验证提交 `28a2074` 为 `258 passed`），**未推 `origin/main`、未部署生产**；实时本仓 HEAD 必须用 `git rev-parse --short HEAD` 查询，不能由本文静态快照代替。生产仍是已复验 15/15 的 `bdbb107`。Grbl 源码 HEAD `966723b`，当前板上行为基线 `f5c8985`（后续仅注释修正、未重烧）；S3 源码 HEAD `83eb376`，当前板上行为基线 `55574cb`，新增超时错误码回填未烧录。fz HEAD `c2bf1a3`，保留两个未跟踪用户/工具资产。不得把源码 HEAD、已烧录基线与生产部署版本互相替代。

> **2026-08-03 S4 硬件证据（不是当前源码 HEAD）**：板上实跑 = Grbl `1b56393`（烧 COM11）+ S3 `ae4b524`（烧 COM6，默认镜像 md5 `e8488814ab64660ffca19a595f0e7add`）。**P2 S4 有纸实测已通过**：551 行玫瑰图 CRC32 `ae1b25b2` 匹配、全量下发无丢行、页尾 `M30` 触发自动换纸八步全绿、`state=done`；**`pbuf_free`/`Guru Meditation`/`assert failed` 两侧日志 0 命中，07-31 那次崩溃未复现**。**仍未验证**：纸面实物画质（需人工目视）、App 真实语音端到端、断连恢复 `paper_active` 窗口 abort 复测、`[ESP901] Paper=No` 现象根因。证据 `results/s4-rose-20260803-evidence.md`。本条**不支持**「联调完成」「可出货」「§9 已修好」「硬件已验证」任何一项宣称。

> **2026-08-05 本仓审查修复已提交并部署**：代码提交 `bdbb107` 修复 vpype 取消后子进程继续运行与并发名额提前释放、清理异常引用未定义 logger、G-code 行长按字符而非设备字节、控制台跨源重定向携带认证头、preflight 漏 bridge 健康检查，以及 pip 默认绕过 TLS。本地 hub `overall=pass`（pytest `249 passed`、e2e `PASS`）；生产四文件哈希一致，preflight `pass=15 fail=0 skip=0`，生产 e2e `PASS`，bridge 重连完成 `initialize`/`tools/list`/`ping`，公网边界 404/404/403/8300 超时均符合契约。回滚副本 `/root/hutuji-bak-20260805-bdbb107/`。未碰外仓固件，未做硬件验证。

枢纽仓：协议 v0.5、门禁、京东云 `hutuji-mcp`+bridge+nginx+Cloudflare Tunnel 已常驻；**新版 14 项 preflight 已于 2026-07-31 21:20–21:22 部署生产并复验 `pass=14 fail=0`**（有效末次 env、HTTPS:443 非 loopback 前缀、enabled 精确链接 + loopback-only `:80 /files` ACL、真下载 200/Length/CRC/body），公网面复验全绿（HTTPS `/mcp` GET/POST 404、直连 `:80 /files` 403、HTTPS `/files` 合法产物 200）；回滚副本 `/root/hutuji-bak-20260731-212012/`。官方云路径已实际触发 `draw/status/abort`，桥接重连后云端 `initialize`+`tools/list`+`ping` 已进日志且新脱敏 filter 生效。**2026-07-31 代码审查修复批次**：hub 侧 URL 校验/日志脱敏/部署加固（`preflight.sh`、`nginx-hutuji.conf`、`config.py`、`cloud_bridge.py`）与 S3 单 owner reset 事务（`hutuji_{job,pipe,recovery_core}`，host 9/9 + IDF build pass）、Grbl §9-C′ 收窄（fz `standard` pass + PlatformIO `release` pass）——四仓已分别提交并推送 `f964aa2`/`1a4fea1`/`c2bf1a3`/`6b39e66`，清单见 `worktree-inventory.md` 顶部快照。
**2026-08-01 preflight 回归修复（历史记录，已被 2026-08-04 部署 supersede）**：`f3ef14b`/`8e4b9b4` 重构活动 ACL 检查时把**调用点搬到了变量初始化之前**，`set -euo pipefail` 下脚本第一条顶层语句就 `TUNNEL_HOST: unbound variable` 退出，14 项一个都不跑（本机 Git Bash 实测）。随后已修回并加硬：无非 loopback IPv4 时打印 `[skip]` 而非拿 loopback 半边冒充整项 `[pass]`、第 6 项加 `command -v ss` 前置、全部 curl 统一 `--noproxy "*" --max-time 5`；`hub` 门禁 overall=pass（pytest **137 passed**、e2e PASS）。该 14 项修复已于 2026-08-04 部署；2026-08-05 新增的第 15 项（bridge 健康）已随 `bdbb107` 部署并复验 `pass=15 fail=0 skip=0`；同日再新增的「生成链」项使总数到 **16**，该项仓内已就绪但**未部署**。
部署踩坑（下任必看）：nginx 模板要同步**两处**——`/etc/nginx/sites-available/hutuji` 与比较基准 `/opt/hutuji/deploy/nginx-hutuji.conf`，漏后者会让第 12 项「线上 conf 与本仓模板一致」fail。ACL 安全前提：`/etc/cloudflared/config.yml` 回源 `http://localhost:80`，本机 `localhost` 只解析 `::1` 而 nginx 只 `listen 80`（IPv4），实测 access.log `remote_addr=127.0.0.1`（cloudflared 在 `::1` 被拒后回落 IPv4），故 loopback-only ACL 不切隧道。
Grbl：§9 `e7f373f` 已提交、fz `standard` pass、已烧录；Telnet、keepalive 与 `[ESP901] Paper/Changing=Off` 已实机验证。当前源码 `8d13b5c` 已提交/推送，固化 A4 默认行程、M3/M5 笔控同步与 segment underflow 收敛，并通过 fz `standard`、PlatformIO `release`、COM3 烧录与空闲最小核验（授权、Telnet:23、`[ESP901] Changing=Off`、`$130=210 $131=297`）；有纸 `Changing=On` 已于 2026-07-30 B-13 验证。**2026-07-31 审查修复**：§9-C′ underflow 恢复先提交 `12f26ac`，再收窄并提交 `4ddc945`→`1a4fea1`——`should_resume_segment_underflow()`——只救真实 Cycle 断粮，jog/探针取消与 soft limit 照原路 `Idle`/`Alarm`（fz `standard` pass + 7 组判定用例）；PlatformIO `release` **SUCCESS**（`firmware.bin` 1857056B / Flash 94.4%）；**已于 2026-07-31 21:30 烧录 COM10**（USB 重插后 COM3→COM10，`SetCommState` err=31 消失）并完成串口空闲核验（`?` 回 `<Idle|MPos:0,0,0|Bf:249,128|FS:0,0|Pn:0>`、WiFi `192.168.1.13`、`$130=210 $131=297`、`[ESP901] Changing=Off`；证据 `results/grbl-1a4fea1-com10-flash-evidence-20260731.md`）。本次改动未碰 `TelnetServer.cpp`；Telnet :23 握手成功但 `?` 后中止（`WinError 10053`），判为刚烧录后服务未稳的瞬时现象。**C′ underflow 恢复路径与 Telnet 稳定性仍待有纸复测**。
S3：`feat/hutuji-usb-pipe` 源码 HEAD `f1cf066` 已提交/推送（`c1267ec` WiFi 三修 → `ed5b132` ETA → `15f73c9` 窗口化流控 S3 → `13057e8` 落笔旁路 + `ok` 兜底 → `f1cf066` 默认关闭的 §8.5 abort 取证脚手架，产品行为不变、未烧录）。**实机烧录基线（07-31 更新）= HEAD `06b962c` + 当时未提交的第二轮审查修复（现已提交为 `6545ec9`；未重新构建回读比对，不得声称板上镜像 == `6545ec9`）**：已 app-flash COM6 并运行自证（回读 `ota_0` app_desc + 启动横幅 ELF SHA `d01c895d5…`、7 个 `hutuji.*` MCP 工具、Telnet:23 持续 `<Idle|…Bf:249,1200…>` 约 8.5 分钟；证据 `results/s3-06b962c-dirty-com6-flash-evidence-20260731.md`）。**2026-07-31 审查修复**：`hutuji_job.cc` 暂停超时补 soft reset 冲 planner（已推 `255f133`）；**第二轮（已提交并推送 `6545ec9`）**：reset 单 owner 事务（`abort_reset_started_` CAS + `WaitForAbortReset()` + `AbortResetToken` 会话代号一次性令牌 + 窗口在途静默 + `SetTaskSessionActive(false)` 清 `ready_`），新增 `hutuji_recovery_core.h` 与 host 用例（`python -m unittest discover -s scripts/tests` 9 passed）；IDF v6.0.1 `idf.py build` **exit=0**（`xiaozhi.bin=0x2cec90`）；**已于 07-31 烧录 COM6**（逐台记录 `results/device-test-record-s3-lc-01-20260731.md`），但暂停超时/换纸中 abort 行为仍未实测。
**没有**：App 真实语音、生产签核。**换纸中 abort 已于 2026-07-31 取证**（§8.5 四判据全过，已回刷默认镜像）。**P2 S4 已于 2026-08-03 有纸实测通过**（见本节顶部条；纸面画质仍未验证）。
→ **不可商业出货**。  
→ ~~下一刀：**先定位 Grbl `23:27:07` 的 lwIP 崩溃并复测 S4**~~ **【supersede 2026-08-03】此项已完成**：烧 Grbl `1b56393` + S3 `ae4b524` 后同一 551 行玫瑰图有纸实测通过，`pbuf_free` 未复现（`results/s4-rose-20260803-evidence.md`）。**新的下一刀** = ① 纸面实物画质人工目视（本轮唯一未验证的 S4 判据）→ ② App 真实语音（需用户重取 token）→ ③ 断连恢复 `paper_active` 窗口 abort 复测 → ④ 生产签核。以下历史归因保留备查：551 行玫瑰图已下载且 CRC 匹配；`results/.tmp/s4_grbl.log:14-19` 显示 Grbl 在 `23:26:54–23:27:07` 进入 `Run`，MPos 多次非零且 planner 余量 `247→200→238→243→236`，证明运动块已送达并执行；`:20-23` 随后出现 `pbuf_free: p->ref > 0` 断言并重启；S3 到 `23:27:14` 才见 EAGAIN，故 EAGAIN 是该次崩溃后的链路症状，不是已证实首因。`71acfc9` 只针对 S3 EAGAIN 处置，且第二轮审查发现其循环把 `-1` 转成 `size_t` 后误报发送成功；该缺陷已由 **xiaozhi `90e6f2c`** 修正——RED→GREEN host 回归钉住发送游标契约。**§8.5 换纸中 abort 已于 2026-07-31 21:49 取证**（四判据全过，已回刷默认镜像）。nginx 已上线。
→ **禁止**再写一版文档地图当进度；真值页 `release-readiness.md`。

## 1. 三仓与代码在哪

| 仓 | 路径 | 你该改什么 | 你不该假设 |
|----|------|------------|------------|
| hutuji | `D:/Users/hutuji` | MCP、协议、文档、`deploy/`、本仓门禁 | 这里有写字机/S3 固件源码 |
| Grbl_Esp32 | `D:/Users/Grbl_Esp32` | 仅 §9 小改（若任务要求） | 可以改换纸机械/运动/授权 |
| xiaozhi-esp32 | `D:/Users/xiaozhi-esp32` | 哑管道 M2（若任务要求） | 补丁已在 hutuji 仓里 |

本仓 `firmware/` **只有说明**，补丁在 xiaozhi fork（分支名 `feat/hutuji-usb-pipe` 是历史名，链路已是 **WiFi Telnet**）。

## 2. 已锁定、禁止重开的决策（编号 = 根 `README.md`）

1. 双芯片，不合并固件  
2. 板间 = **方案 E：WiFi Telnet :23**（USB Host / CH9350 **已否决**，别再提）  
3. 哑管道：G-code 只在云端 MCP 生成  
4. AI = 小智**官方云** + MCP  
5. 部署 = **阿里云主 + 京东云热备**（阿里云 `47.112.162.80` **现役生产**，2026-08-23 晚割接 DNS A 直连；京东云 `117.72.118.95` 热备；2026-08-23 用户反转原「仅京东云」锁定；cloud bridge 单 WSS 任一时刻只在一个节点跑）
6. 换纸机械不动；§9 消息路由/状态/keepalive 可小改且**必跑 fz 门禁**  
7. hutuji = 枢纽仓  

决策编号以根 `README.md` 为准；`AGENTS.md` 已对齐。勿自造「决策 #5 = 换纸」。

## 3. 常见误判（看到就停）

| 误判 | 真相 |
|------|------|
| 「仓库只有文档」 | `mcp-server/` 已可跑；先跑 `scripts/agent_gate.py` |
| 「CRC 还没做 / 还在用 StaticFiles」 | 已自定义 `/files` + `X-Hutuji-CRC32`；见 `mcp-server/README` 组 0 ✅ |
| 「验收清单还要按组 0 重做一遍」 | 组 0–4 已落地并上云；当前缺口是 App 语音与设备端剩余实测 |
| 「所有换纸进度都能从 Telnet 收到」 | **不能**；§9 只定点放开四处 `[PaperStatus]`，且真实结果码到达仍待换纸验证；其余进度看 CH340 |
| 「出图中 status 发 `[ESP901]`/`M704`」 | **禁止**；只发 `?`（单写者） |
| 「abort = 急停」 | 换纸中打不断；见 protocol §4.1 |
| 「PC telnet 旁观 + S3 同时连」 | `MAX_TLNT_CLIENTS=1`，互斥；PC 看 CH340 |
| 「可出货了，文档都齐了」 | 文档 ≠ 证据；见 `release-readiness.md` + `claims_forbidden` |
| 「本仓门禁绿 = Grbl 也绿」 | **否**；Grbl 必须另跑 fz `agent_gate.py` |
| 「记忆文件里写 USB Host / CH9350」 | **过时**；以本仓为准（「阿里云部署」2026-08-23 已由用户反转，见决策 #5） |
| 「文档某处写可选/可去掉/可简化，那就删」 | **假许可**；先对账 `protocol.md` §5 / toml / `gcode_gen.py` / `test_contract.py`，冲突以现行定案为准并盖章，禁止照删（anti-drift 铁律7） |
| 「我记得/应该只有一处，改完就行」 | 全称判断必须先全仓 `grep` / 三仓 `git status` 取证，没检索不说「唯一/已清/干净」（anti-drift 铁律8） |
| 「这文档过时了，顺手精简/重写一下」 | 过时只盖章 `supersede`；禁止精简负载、禁止重写文档地图当进度（铁律7 + anti-drift §2.1） |
| 「外仓我熟，直接改」 | 改外仓先读该仓根 `AGENTS.md` + 白名单，不凭记忆；Grbl 改完必跑 fz（anti-drift 铁律8） |

## 4. 动手前强制顺序

1. 本页 → `agent-anti-drift.md` → `worktree-inventory.md`  
2. `release-readiness.md` → `agent-gate.md`  
3. 根 `README.md` 决策表 + `AGENTS.md`  
4. `protocol.md` §1.1 / §1.2 / §2.1 / §4.1 / §5.1 / §9  
5. 三仓 `git status --short` — **不覆盖**用户已有改动；对照 inventory  
6. 按任务打开：`bringup-sop.md` / `mcp-server/README.md` / `deployment.md`  
7. 结束前跑本仓门禁；动过 Grbl 再跑 fz 门禁；更新 inventory / readiness

## 5. 本仓立刻能跑的命令（无硬件）

```powershell
cd D:\Users\hutuji
python scripts\agent_gate.py --profile hub   # 默认；期望 overall=pass

cd D:\Users\hutuji\mcp-server
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts\e2e_local.py    # 期望 PASS
```

小智云冒烟：仅当前终端设 `MCP_ENDPOINT` 后跑 `scripts/cloud_bridge_smoke.py`。  
**禁止**把接入点 / API key 写入仓库、`.env`、systemd unit 正文。

## 6. 工程优先级（默认下一刀）

若用户未指定别的任务，按此序，**禁止跳阶段**：

1. **Waveshare 当前镜像 UI HIL**：在实际屏幕完成预览确认/取消，以及 streaming/paused/idle 状态下的暂停、继续、停止、重画、试笔；直接调用 `Job::Request*` 会绕过触摸映射、置灰门控和回调接线，不能替代。
2. **断连恢复 `paper_active` 窗口 abort 复测**：不得追加 reset/M30；与 2026-07-31 已通过的正常换纸中 abort 分流不同。
3. **生产与合规签核**：含出货级纸面签核；阿里云主 + 京东云热备已常驻，勿从零部署。**AI 虚实交替**已于 2026-08-28 用户确认可画（hub 短 G1 地板 + 机设 `$1=255`），禁止作为默认待办重做。
> ~~异步生成+完成推送~~ 已于 2026-08-28 当天完成并生产启用（`34b44d4` + 双机 env + KB/role-prompt 上云 + 用户屏幕目视确认），不再占默认下一刀。

> fz 绿 ≠ 已烧录 ≠ 阶段 2 通过。

## 6.1 BLE 只读诊断辅助面实施顺序（默认关闭）

BLE 只作为 S3 本地**只读诊断辅助面**推进，不改变 WiFi Telnet :23 正式板间链路。Grbl 侧经典蓝牙 SPP 与 S3 BLE 分属不同器件和职责；不得把两者合并描述。正式 Telnet/G-code 契约仍为 v0.5，BLE 使用独立 `BLE-DIAG schema v1`。

| 阶段 | 交付 | 必须证据 | 明确不做 |
|---|---|---|---|
| A | 默认关闭的 6 字节公开粗粒度状态广播；公开面不放 job/fault/reset 细节 | **已完成**：host/构建、广播字段解码、无 GATT 面、COM14 启用态约 4 分钟稳定性与回滚；**未完成**：关闭/开启 BLE 的 RTT、重连、20 个 job、功耗、堆内存、温升和看门狗完整对照 | 不发 G-code，不控制电机/笔/换纸/job；不广播 URL、token、密钥或用户内容 |
| B | 需 LE Secure Connections + bonding 的 22 字节 GATT `READ` 诊断快照；每 peer ≤1 次/s、最多 1 个连接 | schema/UUID/枚举契约；未认证读取被拒、所有写入被拒；NVS 双槽 CRC 记录；重启/断连后 `reset_reason`、`boot_count` 与上次 job 状态可解释 | 不做任务提交、授权、abort、notify/indicate 或任意命令通道 |
| C | 手机或 PC 诊断工具消费广播，显示 `near/unknown` RSSI 近距提示；S3 不做持续扫描 | 多位置/姿态/遮挡/设备/无目标实测；5 样本中位数、5s 窗口、3s 无样本为 unknown；正式链路无 RTT、吞吐、断连、功耗回归 | 不宣称距离、方向、房间定位、UWB、AoA 或 Apple Precision Finding |

实施共存门槛：每阶段使用同一 S3、写字机、路由器和构建，关闭 BLE 为基线，开启 BLE 至少 3 次、每次 30 分钟；包含 20 个确定性 job 闭环和 10 次受控断连。Telnet RTT、应答错配、job 终态、恢复时间、堆内存、功耗、温升和 panic/watchdog 的具体硬门槛以 `docs/protocol.md` §1.4.6 为准；任一失败即关闭 BLE，不得调 Telnet 超时或窗口掩盖回归。

进入下一阶段前，必须把构建、烧录、启动身份、schema 解码、共存实测和 HIL 原始证据写入 `docs/release-readiness.md` 与 `docs/worktree-inventory.md`。外部资料必须按 `docs/protocol.md` §1.4.8 登记。默认镜像仍关闭 `CONFIG_BT_ENABLED`；阶段 A 已实现并完成窄范围板上验证，但完整共存矩阵与阶段 B/C 仍未完成，不得把阶段 A 写成整个 BLE 面通过。
## 7. 改完必须同步的文件

| 你改了什么 | 必须更新 |
|------------|----------|
| 契约语义 | `protocol.md`（两侧评审） |
| 进度/证据 | `release-readiness.md` |
| 结构/约定/命令 | `AGENTS.md` + `README.md` + `CLAUDE.md` |
| 门禁定义 | `docs/agent-gate.md` + 必要时 `scripts/agent_gate.py` |
| 防幻觉/白名单 | `docs/agent-anti-drift.md` + `docs/agent-edit-allowlist.json` |
| 跨仓脏树 | `docs/worktree-inventory.md` |
| ① 闭环勾选 | `docs/foundation-checklist.md` |
| ③ 行为/验收 | `mcp-server/README.md` |
| 批次出货 | `docs/templates/*` |

Grbl 任何代码（含 §9）：`python D:/Users/zhugu/fz/scripts/agent_gate.py`，非 pass 不得声称修好。

## 8. 路径速查

| 用途 | 路径 |
|------|------|
| 文档总入口 | `docs/README.md` |
| 本防呆页 | `docs/agent-handoff.md` |
| **本仓门禁** | `docs/agent-gate.md` / `scripts/agent_gate.py` |
| 防幻觉/禁乱改 | `docs/agent-anti-drift.md` |
| 改码白名单 | `docs/agent-edit-allowlist.json` |
| 工作树清单 | `docs/worktree-inventory.md` |
| ① 闭环清单 | `docs/foundation-checklist.md` |
| 状态 | `docs/release-readiness.md` |
| 契约 | `docs/protocol.md` |
| MK38 写字机板 | `docs/hardware/mk38-board.md` + `docs/hardware/MK38-总图.pdf` |
| 实战派 S3（小智） | `docs/hardware/lichuang-szpi-esp32s3-board.md` + `lichuang-szpi-esp32s3-sch.pdf` |
| 联调 SOP | `docs/bringup-sop.md` |
| 白天联调 runbook | `docs/daytime-tuning-runbook.md`（现取三仓 HEAD 与板上启动身份，不再把历史 SHA 当当前；真实语音、归位、自动换纸和换纸中 abort 有历史证据，当前开放项以 readiness 为准） |
| 京东云模板 | `deploy/`（当前 preflight **30** 项；更新仍须用 scp，装 unit 后先 `daemon-reload`，再显式重启适用服务并跑全 30 项） |
| 小智智能体/知识库 | `docs/xiaozhi-agent/`（角色提示 + 知识库，控制台粘贴） |
| MCP 服务 | `mcp-server/` |
| S3 补丁说明 | `firmware/m1-usb-pipe.md` |
| Cursor 硬轨 | `.cursor/rules/hutuji-agent-rails.mdc` |
