# hutuji 枢纽仓 Agent 门禁

> **本文件是「本仓宣称完成」的唯一门禁定义。**  
> 可执行入口：`python scripts/agent_gate.py`（见文末）。  
> Grbl 固件改动另有 **fz 门禁**（`D:/Users/zhugu/fz/scripts/agent_gate.py`），二者不互相替代。  
> 接手防呆：`agent-handoff.md`。状态真值：`release-readiness.md`。

## 1. 为什么要本仓门禁

上一任代理常见跑偏：

- 把「文档地图已齐」当成「工程完成」
- 重开已否决方案（USB Host / CH9350 / 阿里云）
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
| `docs` | 只改文档/模板/规则 | 必备文件存在；决策锚点在位；禁止重开措辞未复活；**禁止宣称未被使用**；改码白名单完整；防幻觉/约束矩阵资料齐；文档 `path:line` 引用未越界 |
| `hub`（**默认**） | 改本仓 MCP / 脚本 / 协议说明 | `docs` + pytest 全绿 + `e2e_local.py` 输出 `PASS` |
| `full` | 改了 Grbl 或声称 §9/联调推进 | `hub` + 文档**外仓** `path:line` 引用未越界 + Grbl §9 **源码标记**检查（未设 `GRBL_ROOT` 直接 **fail**，不再 skip）+ **提示**必须再跑 fz `agent_gate.py` |

> `full` **不会**自动替你跑 fz 门禁（耗时长、依赖本机 fz）。  
> 改了 `D:/Users/Grbl_Esp32` 后，你必须**自己**跑 fz，且 `overall_status=pass` 才能声称修好。

`doc_line_refs` 层（三个 profile 都跑）扫 `*.md` / `docs/**/*.md` / `*/README.md` 里的
`file.ext:12` 与 `file.ext:12-34` 引用，校验目标文件行数覆盖该行号（区间取较大端）：

- 只认能**唯一**落到本仓的引用：相对路径直接命中，或裸文件名在本仓内恰好 1 个同名文件命中；`.venv`/`.claude`/`results`/`output` 等副本目录不参与解析（`.claude/worktrees/*` 是整仓副本，会把唯一性打破）。
- 落不到本仓的引用（fork 侧、逆向产物）本层一律跳过；`full` profile 由下面的 `external_doc_line_refs` 接管。本层不管「路径拼错」。
- detail 同时报 `校验 N 处` 与 `跳过 M 处`；`N=0` 判 **fail**（解析规则失效的空转门禁比红更危险）。行号在范围内 ≠ 指向对的代码，语义仍须人工核对。

`external_doc_line_refs` 层（**仅 `full`**）把同一套校验延伸到三个只读参考仓——本仓文档里的
`path:line` 引用九成指向那边（`hutuji_pipe.cc`、`Serial.cpp`、`grbl_plotter.py`…），只在本仓解析
等于放着最大的漂移面不管：

- 仓库路径取环境变量 `GRBL_ROOT` / `XIAOZHI_ROOT` / `INKSCAPE_PX_ROOT`，缺省用 `AGENTS.md` 记载的本机布局；目录不存在整仓跳过（换机器不该因为没有这些 fork 而变红）。
- 每仓只索引白名单子目录（`Grbl_Esp32/` + `doc/`、`main/`、`inkscape-clean/`），并排掉 `.pio`/`build`/`managed_components`/`release*` 等副本。**inkscape_px 里那份旧 `Grbl_Esp32/` 副本被有意排除**——纳进来会让几乎所有 Grbl 源码引用退化成同名两份而跳过，整层假绿。
- 同名多份（如 `paixi_runtime/` 在 `tools/` 与 `share/extensions/` 各存一份）一律跳过：宁可漏判，不可误判。`校验 N=0` 或三仓全缺 → **fail**。
- 只跨仓**读**行数，绝不改外仓文件。fail 时先分清是文档行号过期还是源码已变；fork 工作树有未提交改动时行号本就会漂。
- 文档候选复用 `doc_line_refs` 的缓存/产物排除集；其余治理文档读失败必须返回结构化 **fail**，不能因权限错误抛 traceback 中断整扇门禁。

## 3. 必备文件（`docs` 层）

下列文件缺失 → 门禁红；禁止用「差不多齐了」绕过：

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
| `docs/development.md` | 开发规范 |
| `docs/deployment.md` + `deploy/` | 京东云模板 |
| `docs/templates/device-test-record.md` | 逐台记录 |
| `docs/templates/release-manifest.md` | 批次清单 |
| `mcp-server/README.md` | ③ 验收 |
| `firmware/README.md` | ② 说明入口 |
| `.cursor/rules/hutuji-agent-rails.mdc` | Cursor 硬轨 |

## 4. 决策锚点（禁止漂移）

`docs` 层会在根 `README.md` 中核对以下锚点字符串（缺失 = 红）：

1. `双芯片`
2. `WiFi Telnet`
3. `哑管道` 或 `云端生成`
4. `117.72.118.95`
5. `protocol.md` 与 `§9`（§9 清单必须仍在契约中）

并扫描关键文档，若重新主张下列**已否决**方向 → 红：

- 把 USB Host / CH9350 写成**现行**板间方案（允许出现在「已否决」叙述中）
- 把阿里云写成生产落点
- 主张合并双芯片固件进单一仓库/单一固件

扫描按命中所在短语判断否决语境；`禁止重开：A / B / C`（含 Markdown 粗体标题）视为同一行并列项的显式否决清单。该例外只覆盖标题后的同一行；前文“已否决”不能掩盖后文另起短语的“当前采用”，不同并列项的否决也不能互相借用。

否决语境豁免的边界（两条判据，缺一即假绿，均有回归测试锁定）：

- **命中短语自身在做采纳断言时，任何上文禁令都不豁免它**（`_ADOPTION_RE`）。禁令清单只掩护*列举*否决项的并列项，掩护不了混进清单里的采纳句，也掩护不了标题名恰好含否决词、其下却在讲采纳实现的段落。
- **否决词必须紧贴冒号**，中间只容 Markdown 粗体标记与一段括号旁注。放宽成“同一行任何位置出现禁令词 + 冒号”，等于让前半句管自己宾语的禁令去豁免冒号后另起的主张。同理句窗起点在冒号处停；需要跨冒号的引述形态（`不证明：…`、`**禁止宣称**（除非…）：…`）由导语规则处理，不靠句窗。

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

- 扫描面 = `GOVERNANCE_DOCS`（17 份，见 `scripts/agent_gate.py`）。新增治理文档**必须**加进该清单，否则禁语与复活两层都会绕过它。
- 判据区分「做出宣称」与「引述规则」：命中若处于否定/免责语境（`不得` / `≠` / `不证明` / `不支持` / `除非` …，按**整句**取窗口而非最小短语），或被反引号包裹、或落在「禁语 → 前置条件」表格的定义列、或其上方是否决类标题/导语（可跨空行与同级列表项），一律放行。
- 这样 §5 这张表自身、`.cursor` 硬轨的禁语清单、以及各文档里「本条不支持『可出货』任何一项宣称」都能正常存在，而散文式断言（「本机已联调完成」）判红。
- 变异验证：6 份文档各追加一句真断言 → 全部判红；3 类合法引述 → 全部放行。见 `mcp-server/tests/test_contract.py` 的 `test_gate_forbidden_claims_*`。

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

1. 纸面实物画质人工目视（出墨连续性、曲线完整、断线）
2. 完成 App 真实语音端到端及七工具逐项云端行为回归
3. 复测断连恢复 `paper_active` 窗口的 abort：不得 reset/M30
4. 完成生产与合规签核

页尾 `M30`、`Changing=On`、换纸中 abort 与 P2 S4 已有历史实测，具体证据和未验证边界以 `release-readiness.md` 为准，不得作为默认待办重做。

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
判 **fail** 并写明超时，不会让门禁无限期挂住。

报告固定写到：

- `results/hutuji_gate_last.json`（机读）
- `results/hutuji_gate_last.md`（人读）

两份报告均经 `_atomic_write`（同目录临时文件 + `os.replace`）原子落盘，进程中断不会留下
半截/错配报告；子进程输出强制 `PYTHONIOENCODING=utf-8` 且门禁自身 stdout 重配 UTF-8，
避免 Windows GBK 终端打印 `\ufffd` 崩溃。

退出码：`0` = overall pass；非 0 = fail（不得宣称本 profile 通过）。

## 9. 与其它文档的关系

| 文件 | 关系 |
|------|------|
| `agent-handoff.md` | 5 分钟防呆；指向本门禁 |
| `foundation-checklist.md` | ① 闭环勾选；勾完仍须本门禁 `docs`/`hub` 绿 |
| `bringup-sop.md` | 硬件阶段门；本仓脚本不替代 |
| fz `agent_gate.py` | Grbl/仿真门；本仓脚本不替代 |
