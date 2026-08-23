# hutuji 枢纽仓 Agent 门禁

> **本文件是「本仓宣称完成」的唯一门禁定义。**  
> 可执行入口：`python scripts/agent_gate.py`（见文末）。  
> Grbl 固件改动另有 **fz 门禁**（`D:/Users/zhugu/fz/scripts/agent_gate.py`），二者不互相替代。  
> 接手防呆：`agent-handoff.md`。状态真值：`release-readiness.md`。

## 1. 为什么要本仓门禁

上一任代理常见跑偏：

- 把「文档地图已齐」当成「工程完成」
- 重开已否决方案（USB Host / CH9350；「阿里云部署」2026-08-23 已由用户反转，不再属否决项）
- 改 Grbl 后不跑 fz 门禁就声称修好
- 跳过 `bringup-sop` 阶段直接写上层代码
- 把 MCP 验收清单组 0–4 当待办重做一遍
- **幻觉**：编造测试结果 / commit / 硬件现象
- **乱改**：为门禁绿改换纸常量、覆盖用户工作树、越权「顺手重构」
- 照文档「可选 / 可简化 / 可去掉 / 待做」措辞删除或弱化负载设计（应先对账现行定案，见 anti-drift 铁律7；门禁 `anti_drift` 层已钉死「不准简化设计」标记）
- 凭记忆下全称结论（「唯一 / 已清 / 干净 / 没有第二处」）而不先全仓检索（见 anti-drift 铁律8；门禁已钉死「先读后动 / 全称必证」标记）

本门禁用**可跑检查 + 禁止宣称表 + 防幻觉资料齐套**把这些钉死。  
行为细则：`agent-anti-drift.md`；改码白名单：`agent-edit-allowlist.json`；脏树：`worktree-inventory.md`。

## 2. Profile（选一个跑，禁止自造）

| Profile | 何时用 | 必须绿 |
|---------|--------|--------|
| `docs` | 只改文档/模板/规则 | 必备文件存在；决策锚点在位；禁止重开措辞未复活；**禁止宣称未被使用**；改码白名单完整；防幻觉/约束矩阵资料齐；文档 `path:line` 引用未越界且**行内容指纹与基线一致** |
| `hub`（**默认**） | 改本仓 MCP / 脚本 / 协议说明 | `docs` + 文档**外仓** `path:line` 引用未越界 + pytest 全绿 + `e2e_local.py` 输出 `PASS` |
| `full` | 改了 Grbl 或声称 §9/联调推进 | `hub` + 外仓引用**要求三仓至少一个在位**（全缺即 **fail**）+ Grbl §9 **源码标记**检查（未设 `GRBL_ROOT` 直接 **fail**，不再 skip）+ **提示**必须再跑 fz `agent_gate.py` |

> `full` **不会**自动替你跑 fz 门禁（耗时长、依赖本机 fz）。  
> 改了 `D:/Users/Grbl_Esp32` 后，你必须**自己**跑 fz，且 `overall_status=pass` 才能声称修好。

### 本门禁与 `deploy/preflight.sh` 的分工（别互相冒充）

本门禁跑在**开发机的仓库树**上；`preflight.sh` 跑在**生产主机的已部署树**上。两者证明的东西不重叠，任一为绿都不能替另一放行：

| | 本门禁（`hub`/`full`） | `preflight.sh`（生产，当前模板 30 项） |
|---|---|---|
| 对象 | 仓库工作树 | `/opt/hutuji` 已部署文件 + 在跑的服务/nginx |
| 生成链 | `e2e_local.py` 真跑一次（`hub` 起含） | `check_generate_chain` 用生产 venv 真跑 `svg_to_gcode()`；`check_bitmap_chain` 用生产 venv 真跑 `bitmap_svg --self-test`（与 unit 第二条 ExecStartPre 同入口） |
| 公网边界 | 只做静态配置断言 | 活动 curl 探针（含非 loopback） |
| 配置/unit 是否真生效 | 只断言仓内模板文本 | nginx conf 与两个 unit 各比 md5；unit 查 `NeedDaemonReload`/`FragmentPath`/无 drop-in，MCP 另查两条 ExecStartPre（生成链 + 位图描摹链）各自成功与主进程新鲜度；nginx 另查**在跑的进程是否真受理了这份配置**（带标记探针须进专用日志 + worker 不早于配置 mtime + 启动后无 `[emerg]`）——`md5` 相同只证明文件对，`reload` 被 `[emerg]` 拒绝时文件仍然是新的而服务跑的是旧配置 |

**「生成链」项（`check_generate_chain`）为什么必须存在**：在它之前，15 项里没有一条执行过部署的 `hutuji_mcp` 代码——下载探针用人工拼的三行 G-code 冒充产物，而 `validate_gcode` 本身就不接受那份内容。已实测复现：移走 `hutuji_mcp/hutuji_gwrite.toml` 后 `import hutuji_mcp.server` 仍成功、服务保持 active、`/mcp` 与 `/files` 照常响应，真实 `svg_to_gcode()` 却报 `vpype 转换失败（exit 2）`，于是 `pass=15 fail=0` 与「一画就整单失败」可以同时成立。根因是 `_PROFILE_PATH` 只被请求期的 `_vpype_command()` 使用、`vpype` 又是 subprocess 从不 import，两者都逃过 `import` 与 `systemctl active`。**推论**：本门禁的 pytest 全绿也证明不了生产能出图——生产的 profile、venv 依赖与代码是另一套文件，只有生产侧 `check_generate_chain` 与生产 e2e 能证明。

同类盲区还有三处，一并记住形状：**`systemctl is-active` 只证明「在跑」，不证明跑的是哪一版 unit**（加固项可全部缺失而照样 active；`daemon-reload` 只换 unit 快照、不重启旧进程；drop-in 更能静默覆盖 ExecStart），已由 `check_units_match_templates` 的模板/加载态判据、MCP 两条 `ExecStartPre` 成功结果和“主进程不早于已部署 unit/代码/profile”共同覆盖；**延迟 import 的重依赖缺失对 import、active 与生成链自检全隐形**（2026-08-09 本机实测：拦掉 imageio 后 server 可 import、`gcode_gen --self-test` 退 0、21/21 全绿，只有真实描摹抛 `BitmapError`），已由 unit 第二条 `ExecStartPre`（`bitmap_svg --self-test`，沙箱内 fail-closed）与 preflight `check_bitmap_chain`（真跑当前落盘代码）共同覆盖；以及**「大小/长度」一律按设备看到的字节计**，`read_text()` 会把 CRLF 塌成 LF、每行少算 1 字节，实测让超 512KiB 的产物被放行。共同的教训是：判据必须量在**设备/运行时真正消费的那个对象**上，量仓内文本或量塌陷后的字符都会假绿。

**2026-08-11 第二轮 review 又补了四条**（同一形状：判据在，但量错了对象或漏了对象）：① 「主进程不早于本次部署」的 mtime 比较原用裸 `stat -c %Y`，对符号链接取的是**链接自身**的时刻（`ln -sfn` 那一刻），故 `/etc/hutuji/mcp.env` 或 unit 是链接时，轮换 `LLM_API_KEY` 却不 restart 也能过——同文件的 nginx 项 2026-08-08 已因这个坑改用 `stat -L`（实测链接 08-07 17:31:15 vs 配置本体 08-08 20:54:01），本项没跟上，现已统一走 `newest_mtime_of()`。② 比较集里的 `.py` 用 bash 通配 `hutuji_mcp/*.py`，**只展开一层**；今天包是平铺的故无活缺口，但判据说的是「本次部署的代码」，新增子包即静默失去覆盖，现改 `find` 递归（`collect_deployed_py()`，找不到任何 `.py` 时 fail-closed）。③ **bridge 此前完全没有重启时效判据**：唯一的 `check_bridge_ready` 只比对 `MainPID` 与 ready 文件里的 PID，那证明「当前主进程写过 ready 文件」，证不到它读的是本次部署的 endpoint——`cloud_bridge.load_config()` 只在启动期读一次 `MCP_ENDPOINT`，进程内重连复用旧值，而 `check_bridge_env` 校验的是**磁盘上**的 `bridge.env`（该文件此前不在任何 mtime 比较集里）。新增第 23 项 `check_bridge_restart_recency`，把 bridge 的两份 unit、`bridge.env` 与包内 `.py` 一起纳入比较。④ 生成链的产物路径原用 `mktemp -u`，只预测一个尚不存在的名字、不创建文件、不确立属主，而 EXIT trap 随后无条件以 root `rm -f` 它——脚本以 root 跑在 `/tmp`（sticky 对 root 无效），预测名与真建名之间任何本机用户都能抢建成自己的文件或指向别处的符号链接，trap 就替他删；已改真 `mktemp`（同文件 `check_download` 本来就是真 `mktemp`，此处是唯一的 `-u`）。

`doc_line_refs` 层（三个 profile 都跑）扫 `*.md` / `docs/**/*.md` / `*/README.md` /
`firmware/*.md` / `.cursor/rules/*.mdc` 里的 `file.ext:12` 与 `file.ext:12-34` 引用，
校验目标文件行数覆盖该行号（区间取较大端），并校验区间本身的语义：

- 只认能**唯一**落到本仓的引用：相对路径直接命中，或裸文件名在本仓内恰好 1 个同名文件命中；`.venv`/`.claude`/`results`/`output` 等副本目录不参与解析（`.claude/worktrees/*` 是整仓副本，会把唯一性打破）。
- 落不到本仓的引用（fork 侧、逆向产物）本层一律跳过，由下面的 `external_doc_line_refs` 接管（`hub` 起就跑）。本层不管「路径拼错」。
- **扫描面是单一真值 `_doc_scan_files()`，三层共用**（本层 / `external_doc_line_refs` / `line_ref_fingerprints`）。原先三层各自内联同一份 glob，`GOVERNANCE_DOCS` 里的 `.cursor/rules/hutuji-agent-rails.mdc` 不落在任何一条 glob 上，被三层同时漏掉（2026-08-11 code review 实测）。理由同 `required_file_set()`：清单散成三处，新增一类文档要改三处，漏改一处那层就静默失去覆盖。`test_gate_doc_scan_surface_covers_every_governance_doc` 从 `GOVERNANCE_DOCS` 派生断言而非静态清单，下次新增治理文档漏进扫描面即红。**`firmware/*.md` 与那份 `.mdc` 当前都还没有 `path:line` 引用**，这两条 glob 是先把面封住，不是修好了既有漏判。
- **路径归一化两条硬约束**（均有回归测试，都是上述 review 里实测取到的 RED）：① 只剥真正的 `./` 前缀（`removeprefix`，不是 `lstrip("./")`）——按字符集剥离会把 `.cursor/rules/x.mdc` 吃成 `cursor/rules/x.mdc`，引用永远解析不到，且落进「跳过」而非判红。② `ROOT / ref` 之后必须 `relative_to(ROOT)` 成立才算本仓命中，否则交外仓层：`_DOC_REF_RE` 的字符集不含 `:`，故 `D:/Users/…` 的引用被截成 `/Users/…`，而 Windows 上 `Path("D:/Users/hutuji") / "/Users/xiaozhi-esp32/x.cc"` 恰好等于真实存在的 `D:/Users/xiaozhi-esp32/x.cc`，旧实现据此判为本仓命中，指纹层随后在 `relative_to` 抛 `ValueError` **让整扇门禁崩溃而非判红**。旧 `lstrip` 顺手剥掉了前导 `/` 才一直没暴露——那是巧合，不是防线。
- 按后缀收窄候选时，`ref` 已带前导 `/` 就不再补一层（`_narrow_by_path_suffix`，三层共用）。补出来的 `//Users/…` 没有任何路径以它结尾，引用会静默失去校验：本仓实测 2 处（`hutuji_pipe.cc` 与 `TelnetServer.h` 的绝对路径引用）长期只计跳过，修好后进入校验面，其中 `TelnetServer.h` 那条重登基线时指纹 `a2e0c3c2` 逐字节未变——证明它始终指着对的行，只是从未被验过。
- detail 同时报 `校验 N 处` 与 `跳过 M 处`；`N=0` 判 **fail**（解析规则失效的空转门禁比红更危险）。行号在范围内 ≠ 指向对的代码，语义仍须人工核对。
- **本层查不出「指向错」，而指向错是静默的**。2026-08-09 实测：`docs/release-readiness.md` 里指向 `AGENTS.md` **第 149 行**的引用，本意指硬规则「**禁止宣称**（除非 …）」，而该条早已随多轮插行漂到 **第 153 行**；149 一直落在文件行数内，故本层历轮全绿、从未报过。同轮另有两处同源漂移：写 `agent_gate.py` **第 131 行**（指 `GOVERNANCE_DOCS` 注释「新增治理文档请加进本清单」）被 `REQUIRED_FILES` 补行推到 **137**；本文 §5 那条写的「第 137 行」被 §3/§5 插行推到 **139**。
- **处置约定**：引用处若已逐字引了目标句（如上例已写出「**禁止宣称**（除非 …）」），**删掉行号、只留引文**——引文自带定位且不会过期，行号只是额外的过期源。确需行号时，改完插行的文件后须回扫指向它的引用（按 `<文件名>:<数字>` 检索），本层不会替你发现。
- **讲「过期行号」时不要用 `file.ext:NNN` 写法**（本节两条自身即按此改写）：本层会把它当**活指针**解析并计入 `校验 N 处`——那等于给一个按构造就指错的示例发通行证，还会在目标文件缩短时红在「关于门禁的文档」上而非真实内容。举例一律用「`文件名` 第 NNN 行」的散文式写法，本层不解析。
- **扩展名白名单收 `conf` / `service` / `ino`**（2026-08-11 第二轮 review 实测补入）：这三类在本仓文档里都有**能解析到真实文件**的引用（`docs/agent-handoff.md` 引 `nginx-hutuji.conf` 的行号、`docs/protocol.md` §9 引 `Grbl_Esp32.ino` 的行号），此前因不匹配 `_DOC_REF_RE` 而**三层同时漏掉**，是两条长期无人校验的活指针。补入后本层从校验 20 处升到 21 处、指纹基线 171 → 173 条。**刻意不收 `txt` / `log`**：本仓这两类命中全部落在 gitignore 的 `results/`，纳入会让门禁结论随机器上有没有残留产物而变。
- **区间语义本身也判红**：`:207-205`（倒序）与 `:0` / `:0-3`（行号自 1 计）在本层与外仓层都是 **fail**。此前两层只算 `max(start, end)` 再判越界，倒序区间的较大端在界内即整条放行；而指纹层用 `start`/`end` 原值切片，`start > end` 得空 body、`start < 1` 触发负索引，两者都进「越界残留」判红。于是同一条引用在越界层绿、指纹层红，读报告的人只看到「越界残留」却在越界层查不出任何越界。判据集中在 `_doc_ref_range()`，两层共用一份。

`external_doc_line_refs` 层（`hub` 与 `full`）把同一套校验延伸到三个只读参考仓——本仓文档里的
`path:line` 引用九成指向那边（`hutuji_pipe.cc`、`Serial.cpp`、`grbl_plotter.py`…），只在本仓解析
等于放着最大的漂移面不管（2026-08-11 本机实测：本仓层校验 21 处、跳过 174 处，本层再校验 162 处、跳过 18 处）：

- 仓库路径取环境变量 `GRBL_ROOT` / `XIAOZHI_ROOT` / `INKSCAPE_PX_ROOT`，缺省用 `AGENTS.md` 记载的本机布局；单个目录不存在则该仓跳过。
- **三仓全缺时按 profile 分岔**：`full` 判 **fail**（它的前提就是本机有 fork），`hub` 判 pass 但 detail 必须自陈「本层无可校验」——换机器做日常改动不该因为没 fork 而变红，但那次 pass 也不许被读成「查过且干净」。这条分岔由 `require_repos` 参数表达，并被 `mcp-server/tests/test_field_tools.py` 的门禁自测钉死。
- 每仓只索引白名单子目录（`Grbl_Esp32/` + `doc/`、`main/`、`inkscape-clean/`），并排掉 `.pio`/`build`/`managed_components`/`release*` 等副本。**inkscape_px 里那份旧 `Grbl_Esp32/` 副本被有意排除**——纳进来会让几乎所有 Grbl 源码引用退化成同名两份而跳过，整层假绿。
- 同名多份（如 `paixi_runtime/` 在 `tools/` 与 `share/extensions/` 各存一份）一律跳过：宁可漏判，不可误判。**有仓在位但 `校验 N=0` → fail**（解析规则坏了，两层都不放行）。
- 只跨仓**读**行数，绝不改外仓文件。fail 时先分清是文档行号过期还是源码已变；fork 工作树有未提交改动时行号本就会漂。
- 文档候选复用 `doc_line_refs` 的缓存/产物排除集；其余治理文档读失败必须返回结构化 **fail**，不能因权限错误抛 traceback 中断整扇门禁。

`line_ref_fingerprints` 层（三个 profile 都跑）兜上面两层查不出的**半漂移**——行号仍在界内、
但指向的那几行内容已变（mdBook 社区共识：行号引用无法根治此问题，正解是锚点/符号引用，
迁移锚点前先用指纹兜底）：

- 对每条能唯一解析的引用算「目标行内容 SHA256 前 8 位」，与入库基线
  `docs/agent-line-refs-baseline.json` 比对；**未登记**（基线没有该引用）、**半漂移**
  （指纹不符）、**陈旧**（基线条目在文档里已不存在）、**空锚点**（引用指向空白行）、
  **越界残留**（基线仍留着行号已越界的条目）、**目标消失** 六类全判 **fail**。
- 基线只能由 `python scripts/agent_gate.py --accept-line-refs` 显式重登；重登前必须**人工
  核对**被引用行仍是当初证据所指，否则本层退化成自动追认。accept 会保留「文档仍引用但当前
  解析不到」的基线条目（外仓缺席等），在缺 fork 的机器上重登不会冲掉外仓登记。
- 新增 `--fix-line-refs` 模式用于**纯移动**的自动重定位：对「半漂移」条目，若基线保存了原文、
  目标文件中恰好唯一命中、且文档里的 `path:line` 字符串只出现一次，则自动把引用改写为新行号、
  把基线该条重登（新行号 + 新指纹）。命中 0/多处、内容已变、基线无原文、文档中该引用字符串
  多次出现、空锚点、越界残留、目标消失等情况均保持判红并列入待人工清单，不得登记；全部自动
  修复成功（或无漂移）退 0，有待人工项退 1。它不能替代人工核对，只是减少行号纯移动带来的
  机械劳动。
- **目标消失**（2026-08-11 补）：基线登记的 target 是**本仓相对路径**而现在解析不到 —— 只有
  「文件被删或改名」一种解释，判 **fail**。此前这种情况在三层里全部只表现为「计数变小」：
  `doc_line_refs` 记跳过、外仓层记跳过、本层把它算进「保留」，没有任何一层会红，于是一条
  曾经真实校验过的证据引用可以静默失去全部校验。基线里 target 的形态是唯一凭据：本仓命中
  登记仓相对路径、外仓命中登记绝对路径，故**外仓条目仍容忍**（fork 可能整个缺席），只对
  本仓相对路径判红。accept 不能洗白它——保留逻辑会把该条目留在基线里，红一直红到文档修好。
- 行号越界与区间语义错的引用本层不算指纹（由上面两层报），同一缺陷不两层抢报。

## 3. 必备文件（`docs` 层）

下列文件缺失 → 门禁红；禁止用「差不多齐了」绕过。**实际校验面 = `REQUIRED_FILES` ∪ `GOVERNANCE_DOCS`**，由
`required_file_set()` 在调用时现算（保序去重，本机 34 ∪ 18 → **36**），不是一张手抄清单：治理文档必须存在
这条不变量因此是结构性的，新增治理文档只改 `GOVERNANCE_DOCS` 一处即自动获得存在性保护。下表列基础清单，
带「治理」标记的另受 §5 禁语与否决项复活两层扫描。

| 路径 | 作用 |
|------|------|
| `README.md` | 决策记录表（7 条） |
| `AGENTS.md` / `CLAUDE.md` | 代理硬规则 |
| `docs/agent-handoff.md` | 防呆页 |
| `docs/agent-gate.md` | 本文件 |
| `docs/agent-anti-drift.md` | 防幻觉 / 禁乱改铁律 |
| `docs/agent-constraint-matrix.md` | 三仓约束 / 门禁矩阵 |
| `docs/worktree-inventory.md` | 跨仓脏树清单 |
| `docs/agent-edit-allowlist.json` | 改码白名单（机读） |
| `docs/foundation-checklist.md` | ① 基础建设闭环清单 |
| `docs/release-readiness.md` | 状态真值 |
| `docs/protocol.md` | 契约 v0.5 |
| `docs/bringup-sop.md` | 联调阶段顺序 |
| `docs/daytime-tuning-runbook.md` | 单日取证编排（治理，2026-08-09 纳入，见下） |
| `docs/operations.md` | 运维手册（治理，2026-08-09 纳入，见下） |
| `docs/development.md` | 开发规范 |
| `docs/deployment.md` + `deploy/` | 京东云模板 |
| `docs/templates/device-test-record.md` | 逐台记录 |
| `docs/templates/release-manifest.md` | 批次清单 |
| `mcp-server/README.md` | ③ 验收 |
| `firmware/README.md` | ② 说明入口 |
| `.cursor/rules/hutuji-agent-rails.mdc` | Cursor 硬轨 |
| `docs/agent-line-refs-baseline.json` | 行号内容指纹基线（`--accept-line-refs` 重登） |

## 4. 决策锚点（禁止漂移）

`docs` 层会在根 `README.md` 中核对以下锚点字符串（缺失 = 红）：

1. `双芯片`
2. `WiFi Telnet`
3. `哑管道` 或 `云端生成`
4. `117.72.118.95`
5. `protocol.md` 与 `§9`（§9 清单必须仍在契约中）

其中 1–4 为**软锚点**（缺失只记进 detail、不判红），5 为硬判据（`protocol.md` 缺 §9 即红）。软锚点缺失
**必须真的印在 detail 里**：2026-08-11 review 实测，旧实现报的是「missing 里既出现在 README 又出现在
protocol 的那些」，而 missing 的构造条件恰是「两份都没有」——该表达式恒为空，缺多少条都印成空列表。
不判红是刻意的，不说出来不是。

并扫描关键文档，若重新主张下列**已否决**方向 → 红：

- 把 USB Host / CH9350 写成**现行**板间方案（允许出现在「已否决」叙述中）
- ~~把阿里云写成生产落点~~（2026-08-23 用户反转：新决策「阿里云主 + 京东云热备」，aliyun_as_prod 规则与 allowlist 已同步下线）
- 主张合并双芯片固件进单一仓库/单一固件

扫描按命中所在短语判断否决语境；`禁止重开：A / B / C`（含 Markdown 粗体标题）视为同一行并列项的显式否决清单。该例外只覆盖标题后的同一行；前文“已否决”不能掩盖后文另起短语的“当前采用”，不同并列项的否决也不能互相借用。

否决语境豁免的边界（两条判据，缺一即假绿，均有回归测试锁定）：

- **命中短语自身在做采纳断言时，任何上文禁令都不豁免它**（`_ADOPTION_RE`）。禁令清单只掩护*列举*否决项的并列项，掩护不了混进清单里的采纳句，也掩护不了标题名恰好含否决词、其下却在讲采纳实现的段落。
- **否决词必须紧贴冒号**，中间只容 Markdown 粗体标记与一段括号旁注。放宽成“同一行任何位置出现禁令词 + 冒号”，等于让前半句管自己宾语的禁令去豁免冒号后另起的主张。同理句窗起点在冒号处停；需要跨冒号的引述形态（`不证明：…`、`**禁止宣称**（除非…）：…`）由导语规则处理，不靠句窗。
补充实现约束：`当前禁止采用 X` 中「采用」是被禁止的宾语，不是采纳断言；Markdown 换行、粗体、HTML 注释不构成扫描逃逸。门禁运行标记以原子占有保证同一 `results` 目录只允许一个活动进程；存活 PID 的并发启动直接失败，不覆盖报告或 tombstone。文档 `path:line` 引用先 `resolve()` 再判定必须位于仓根内；越过仓根或失效引用不得作为证据。

## 5. 禁止宣称（claims_forbidden）

门禁绿也**不得**使用下列表述，除非 `release-readiness.md` 对应行已是 ✅ 且有证据路径：

| 宣称 | 前置条件 |
|------|----------|
| `可出货` / `商业放行` | bringup 0–8 有设备证据 + 生产/合规签核 |
| `§9 已完成` / `§9 已修好` | Grbl 源码三处落地 **且** fz `overall_status=pass` **且** 阶段 2 实机判据过 |
| `硬件已验证` / `联调完成` | `bringup-sop` 对应阶段记录表已填 |
| `京东云已上线` | `deployment.md` 实机项全部勾选 |
| `M2 已完成` | xiaozhi fork 构建日志 + 工具行为证据 |

脚本在报告里固定输出 `claims_forbidden`，代理交接时不得删改该列表来「做绿」。

`forbidden_claims` 层（三个 profile 都跑）**在治理文档正文里执行这张表**。此前这张表只被
用于两件事：与 `agent-edit-allowlist.json` 的列表比对是否漂移，以及打印报告末尾那句「仍受
claims_forbidden 约束」——**没有任何一层扫过文档里有没有真的使用这些禁语**。实测在
`README.md` 追加一句同时含表中三条禁语的完成式断言，`docs` 层七项全绿并照常打印那句提示。

- 扫描面 = `GOVERNANCE_DOCS`（**18 份**，见 `scripts/agent_gate.py`）。新增治理文档**必须**加进该清单，否则禁语与复活两层都会绕过它。2026-08-09 补入第 18 份 `docs/daytime-tuning-runbook.md`：它决定「哪几步真机验证可以跳过」，一旦写出「已完成/可跳过」后续代理就不再上真机复验，属 agent 行为的直接约束源，此前其状态措辞完全无机器把关。补入后 `test_gate_governance_docs_cover_repo_governance_surface` 的 `must` 清单已把它钉死，禁语与复活两层实测均 pass（该文零禁语命中、三条复活正则零命中）。
- **扫描面里的每一份都自动进入 §3 必备文件面**：`required_file_set()` 在调用时现算 `REQUIRED_FILES ∪ GOVERNANCE_DOCS`，故「治理文档必须存在」是结构不变量，新增一份只改 `GOVERNANCE_DOCS` 即获得存在性保护，无需人工同步两张清单。起因：两个扫描器对读不到的文件都只 `continue`，而 `forbidden_claims` 的 pass 文案直接打 `len(GOVERNANCE_DOCS)`——2026-08-09 实测往清单塞一个不存在的路径，该层仍回 pass 且把份数印成「19 份均未做出」（实际只扫了 18），`revived_decisions` 同样回 pass，**报告数字与真实扫描面脱钩**。当日 `docs/daytime-tuning-runbook.md` 与 `docs/operations.md` 正在扫描面内却不在必备清单，改名或删除即可静默退出两层扫描换全绿。
  - **不改扫描器对缺失判红**：`check_forbidden_claims` 的既有测试会把 `ROOT` 指向只放一份文档的 `tmp_path`，缺失在那里是合法测试构造，改判红实测误伤 20 项既有测试（其中 `allows_quoting_the_rule` 那组要求 pass，把 missing 折进 fail detail 也救不回来）。存在性交 `required_files` 层（三个 profile 都跑）。
  - **先落的修法是手工把两份补进 `REQUIRED_FILES`，已被 code review 判为不足**：静态清单只能证明当下没漂，管不住下一次新增——`test_gate_required_files_derives_governance_docs` 用 monkeypatch 加一份虚构治理文档取 RED 时，旧实现回 `pass count=35`，即新文档零保护。改为调用时现算并集后转 GREEN。
  - 变异复验：临时改名 `docs/operations.md` → `docs` 层 `overall=fail exit=1`、精确报 `missing=['docs/operations.md']`，复原后复绿；删掉 `check_required_files` 的 symlink 判据 → `test_gate_required_files_rejects_symlink` 转红（`required.md` 从 `missing` 里消失），证明并集没有把该判据稀释成「反正会红」。
  - **pass 文案的分子改成循环内累计的实测扫描份数**（2026-08-11 补，形态 `治理文档 18/18 份`）：上面那条并集只保证「清单里的文件都存在」，管不住「存在但读失败」——两个扫描器对读不到的文件仍是 `continue`，而分母是清单长度。分子分母分开印之后，任何一份没真扫到都会在报告里显形（`scanned != len` 时另附一句指向 `required_files` 层）；判红仍不在本层，与上一条的否决保持一致。
- 禁语扫描只接受专门讨论禁语的标题、`不证明：` 导语、直接否定前缀与 `≠` 链式语义；无关标题（如「禁止删除日志」）、任意 `不可`/`=` 前缀不得豁免。`agent-edit-allowlist.json` 的版本、决策数、§9 路径、Core 头文件恢复清单、禁用符号/意图、hub 文档 glob、禁用方案列表须与 `scripts/agent_gate.py` 精确一致。层函数抛异常、返回非字典、缺字段、id/status 不合法，统一塌成对应层 `fail`。
- 判据区分「做出宣称」与「引述规则」：命中若处于否定/免责语境（`不得` / `≠` / `不证明` / `不支持` / `除非` / `不提升` / `不升级` …），或被反引号包裹、或落在「禁语 → 前置条件」表格的定义列、或其上方是否决类标题/导语（可跨空行与同级列表项），一律放行。
- **句窗取「整句中禁语之前的那一半」**，不是整句、也不是最小短语。取整句是为了容纳否定词与禁语隔着顿号/斜杠/`≠` 的真实句式（`写了说明 ≠ 代码已改 ≠ 可出货`）；**只认前半句**是因为整句口径会让否定词反过来给裸宣称当挡箭牌——句尾补一个 `不支持` / `不升级` 就能整句放行（2026-08-07 实测 `现已可出货，后续不支持。` 与另一变异形态 `硬件已验证、可出货，本条不构成 M2 结论。` 在整句口径下双双 pass）。真实免责句式的否定词一律在禁语**之前**，故收紧后当时的 17 份治理文档零新误报（2026-08-09 扩到 18 份后复跑仍零误报）。切窗含 1 字重叠：禁语 `可出货` 是 `不可出货` 的子串、与否定词 `不可` 共享一个字，切在禁语起点会把 `不可` 拦腰截断而误报 `> **不可出货**`。
- 这样 §5 这张表自身、`.cursor` 硬轨的禁语清单、以及各文档里「本条不支持『可出货』任何一项宣称」都能正常存在，而散文式断言（「本机已联调完成」）判红。
- **`不提升` / `不升级` 是 2026-08-07 补的漏词**。`release-readiness.md` 每条实机记录的收尾免责句都是「本条不提升 X 状态」，缺这两词时凡在免责句里写全禁语字面量的条目一律误判判红（实测「本条不提升 M2、联调、硬件已验证或可出货状态」被判红两处），逼作者改写措辞绕开——**门禁把作者赶去改措辞，而非自己做实质判断，是比漏报更隐蔽的失效**。补词后端到端复验：注入裸断言仍判红 3 处、恢复后 pass。**但补词本身确实放宽了豁免面**：同日 code review 实测这两词在整句口径下新开了两处滥用形态（`现已可出货，后续不升级。` 等由判红转放行），已由上条的「只认禁语之前半句」一并堵死——最终净效果比补词前更严，而非更松。
- **反引号层的元语境判定用「行首到命中处」+ 句窗，不是句窗本身**。反引号命中要放行，同句还须有元语境标记（`判红` / `误报` / `滥用` / `口径` / `举例` / `形态` …）；但引述句的标准写法恰是「元语境导语 + `：` + 反引号例子」（`滥用形态举例：` `现已可出货，后续不升级。`），而 `_claim_sentence` 遇 `：` 即停，会把导语整段切掉，把作者的举例判成裸宣称（2026-08-07 实测本节自身的两句举例判红）。改取行首前缀后正好覆盖「谁在说这句话」，且不放宽滥用面：裸散文没有反引号，进不了这一层。
- 变异验证（2026-08-07 对**已安装**脚本跑 22 例双向矩阵，全绿）：15 类滥用形态 → 全部判红（7 类泛用前置词、2 类反引号裸宣称、2 类状态表首列、2 类尾部免责、1 类裸断言、1 类元语境借用）；7 类合法形态 → 全部放行（免责句、`≠` 符号链、规则表、「」引号表、反引号举例、禁令句、部署前置条件）。见 `mcp-server/tests/test_contract.py` 的 `test_gate_forbidden_claims_*`（补词两例与反引号举例两例在无修复时定向 RED；`test_gate_forbidden_claims_rejects_trailing_disclaimer` 4 例在整句口径下定向 RED，故均非弱测试；写免责句用例时不得混入词表已有的否定词，否则会被它们放行而钉不住目标词）。

同一份 `GOVERNANCE_DOCS` 也是 §4 复活扫描的扫描面。原先三条 `REVIVED_PATTERNS` 各带一份手写
文件清单，合计只覆盖 5 个文件，漏掉的恰是优先级最高的几份：`CLAUDE.md`（对 agent 是
OVERRIDE 级指令）、`.cursor/rules/hutuji-agent-rails.mdc`（`alwaysApply` 硬轨）、
`release-readiness.md`（进度真值）；`ch9350` 规则连 `protocol.md` 都不扫。实测把三个已否决方案
各写成「现行方案」的句子追加进 `CLAUDE.md`，以及把 CH9350 写成现行链路追加进 `protocol.md`，
门禁均全绿。

## 6. Grbl / fz 门禁（外部门禁，强制触发条件）

**触发**：改动了 `D:/Users/Grbl_Esp32` 下任何 `.c/.cpp/.h`（含 §9）。

```powershell
$env:FZ_ROOT='D:\Users\zhugu\fz'
$env:GRBL_ROOT='D:\Users\Grbl_Esp32'
python D:\Users\zhugu\fz\scripts\agent_gate.py --profile standard
```

- 读 `D:/Users/zhugu/fz/results/agent_gate_last.json` 的 `overall_status`
- 非 `pass` → **不得**声称修好；不得进入 bringup 阶段 2 烧录宣称
- 细则：`D:/Users/zhugu/fz/docs/AGENT_VIBE_CODING.md`
- **禁止**为了让 fz 绿去改换纸机械/时序/运动/授权/U1 协议，或碰 `report_realtime_status()` / `report_state_text()`

`full` profile 仅做 §9 **源码标记**抽检（见 `scripts/agent_gate.py`），不替代上面这条；
未设 `GRBL_ROOT` 时该层判 **fail**（此前是 skip——无证据的 skip 会让 `full` 假绿）。

## 7. 默认工程序（与 handoff 一致，禁止跳）

1. 完成 Waveshare 当前镜像的预览确认/取消与暂停/继续/停止/重画/试笔屏幕 HIL
2. 纸面实物画质人工目视（出墨连续性、曲线完整、断线）
3. 复测断连恢复 `paper_active` 窗口的 abort：不得 reset/M30
4. 完成生产与合规签核

真实语音出图、页尾 `M30` 前归位、自动换纸、换纸中 abort 与 P2 S4 链路已有历史实测，具体证据和未验证边界以 `release-readiness.md` 为准，不得作为默认待办重做。

用户明确指定任务时可偏离；**不得**在未指定时重写文档地图或重开否决项。

## 8. 怎么跑

```powershell
cd D:\Users\hutuji
python scripts\agent_gate.py              # 默认 hub
python scripts\agent_gate.py --profile docs
python scripts\agent_gate.py --profile full   # 需 GRBL_ROOT；仍要另跑 fz
```

`full` 的 `external_doc_line_refs` 层还会读 `XIAOZHI_ROOT`（缺省 `D:\Users\xiaozhi-esp32`）
与 `INKSCAPE_PX_ROOT`（缺省 `D:\GitHub\inkscape_px`）；三仓路径与本机不同就显式设，
全都不在本机时该层判 **fail**（不是 skip）。

`hub`/`full` 跑 pytest 时，脚本用 `mkdtemp` 在仓库内 `results/.tmp/` 下新建独占
临时目录（`pytest-*`）并禁用 pytest cache 写入，避免 Windows 系统 `%TEMP%` 权限
导致假失败；跑完即删。每次新建是必要的：pytest 启动时会清空 basetemp，固定名字
一旦被其它账号（如沙箱进程）持有就会 PermissionError，让所有用 tmp fixture 的
用例在 setup 阶段假失败。`results/.tmp/` 整个不可用才判 fail。

pytest 与 `e2e_local.py` 各带超时（600s / 300s）：卡死（如 e2e 的假 Grbl socket 不返回）
判 **fail** 并写明超时，不会让门禁无限期挂住。**启动失败同样是本层 fail**：`subprocess.run`
除 `TimeoutExpired` 还会抛 `OSError`（解释器被删、venv 换了架构、权限拒绝、ENOEXEC），
2026-08-11 前只捕超时，那类异常会一路穿过 `main()` 的层循环（那里没有 `try`）——两份报告
都不落盘、`.hutuji_gate_running` 标记留在盘上、终端只剩 traceback。结果是门禁「没有结论」
而不是判红，而读报告的人看到的是上一轮的旧报告。两个 runner 现各自把 `OSError` 塌成
该层 fail（detail 带异常类型名），报告照常落盘。

报告固定写到：

- `results/hutuji_gate_last.json`（机读）
- `results/hutuji_gate_last.md`（人读）

两份报告均经 `_atomic_write`（同目录临时文件 + `os.replace`）原子落盘，进程中断不会留下
半截/错配报告；子进程输出强制 `PYTHONIOENCODING=utf-8` 且门禁自身 stdout 重配 UTF-8，
避免 Windows GBK 终端打印 `\ufffd` 崩溃。

退出码：`0` = overall pass；非 0 = fail（不得宣称本 profile 通过）。**退出码是「本轮是否通过」的唯一真值**，报告文件只是展示——读报告前先确认它是本轮产物：

- 开工时门禁先写 `results/.hutuji_gate_running`（含 profile、计划层清单、pid、HEAD），两份报告都落盘后才删除；**该标记在场 = 上次没跑完**（崩溃/断电/Ctrl+C），旁边的报告一律不得当作结论。再次启动时若发现标记残留，门禁会打印提示并覆盖。
- 报告头绑定 `git_head`（非 git 环境记「未知」）：与 `git rev-parse HEAD` 不符即过期。

## 9. 与其它文档的关系

| 文件 | 关系 |
|------|------|
| `agent-handoff.md` | 5 分钟防呆；指向本门禁 |
| `foundation-checklist.md` | ① 闭环勾选；勾完仍须本门禁 `docs`/`hub` 绿 |
| `bringup-sop.md` | 硬件阶段门；本仓脚本不替代 |
| fz `agent_gate.py` | Grbl/仿真门；本仓脚本不替代 |
