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
- 把 MCP 验收清单组 0–3 当待办重做一遍
- **幻觉**：编造测试结果 / commit / 硬件现象
- **乱改**：为门禁绿改换纸常量、覆盖用户工作树、越权「顺手重构」

本门禁用**可跑检查 + 禁止宣称表 + 防幻觉资料齐套**把这些钉死。  
行为细则：`agent-anti-drift.md`；改码白名单：`agent-edit-allowlist.json`；脏树：`worktree-inventory.md`。

## 2. Profile（选一个跑，禁止自造）

| Profile | 何时用 | 必须绿 |
|---------|--------|--------|
| `docs` | 只改文档/模板/规则 | 必备文件存在；决策锚点在位；禁止重开措辞未复活；文档 `path:line` 引用未越界 |
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

1. 有纸验证页尾显式 `M30` 自动换纸成功，并确认任务最终 `done/Idle`（无纸复测止于 `error:90`；当前烧录基线见 `docs/release-readiness.md`）
2. 有纸验证 `Changing=On` 与换纸态断连分流：不得发 reset/M30，并覆盖 `error:90` 与换纸中 abort
3. 完成 App 真实语音端到端及七工具逐项云端行为回归
4. 完成 P2 S3/S4 与生产签核

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

退出码：`0` = overall pass；非 0 = fail（不得宣称本 profile 通过）。

## 9. 与其它文档的关系

| 文件 | 关系 |
|------|------|
| `agent-handoff.md` | 5 分钟防呆；指向本门禁 |
| `foundation-checklist.md` | ① 闭环勾选；勾完仍须本门禁 `docs`/`hub` 绿 |
| `bringup-sop.md` | 硬件阶段门；本仓脚本不替代 |
| fz `agent_gate.py` | Grbl/仿真门；本仓脚本不替代 |
