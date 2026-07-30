# 跨仓工作树清单（防误删 / 防重复劳动）

> **状态日期：2026-07-29（Grbl `8d13b5c` 已提交/推送/烧录；xiaozhi `c1267ec` 已提交/推送/构建/烧录 COM6，有纸完整画线+自动换纸 `done` 已实测；三仓 `git status` 已复核）。**
> 用途：告诉下一任代理「哪些改动已经在磁盘上、不要当不存在、不要 checkout 清掉」。  
> 每次相关提交或丢弃后**必须更新**本页；与 `git status` 冲突时以 `git status` 为准并回写本页。

## 怎么用

1. 开干前：三仓各跑 `git status --short`，对照本页。  
2. 发现本页未记载的脏文件：先问是否用户资产，默认保留。  
3. 提交/丢弃后：更新本页对应行（或标「已清空」）。

## hutuji（本仓库）`D:/Users/hutuji`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| **已推** | `scripts/bringup_fault_capture.py`、`docs/bringup-sop.md`、`scripts/agent_gate.py`、`docs/agent-gate.md` | bringup 8.3 串口采集与 Grbl WiFi OFF/ON 断链注入辅助；门禁 pytest 临时目录固定到仓库内，避免系统 `%TEMP%` 权限假失败 | 不替代硬件验收 |
| **本轮同步** | `AGENTS.md`、`README.md`、`CLAUDE.md`、`.cursor/rules/hutuji-agent-rails.mdc`、`docs/agent-handoff.md`、`docs/release-readiness.md`、`docs/bringup-sop.md`、`docs/protocol.md`、`docs/worktree-inventory.md`、`firmware/m1-usb-pipe.md` | 同步 S3 `3ca9d31` 构建/烧录与 8.3 复测结论：断连重发主路径到 55/55+fresh `Idle`，最终无纸自动换纸 `error:90`，仍不可商业出货 | 已跑本仓 docs/hub 门禁；不替代有纸 `Changing=On`/自动换纸验收 |
| 已推 | `docs/hardware/*`（MK38 + S3 原理图/对照） | 双板级资料 | 保持；禁改固件脚位 |
| 已推 `61f487d` | `AGENTS.md`、`docs/agent-gate.md`、`docs/protocol.md`、`docs/research.md`、`mcp-server/README.md`、`mcp-server/hutuji_mcp/hutuji_gwrite.toml`（仅注释）、`docs/kxnx/{README.md,alignment/plan.md,findings/02,06,07,09,12,13*.md}`、`firmware/README.md`、`scripts/agent_gate.py` | 同步 S3 `3ca9d31`/8.3 证据，修正生产 G-code 纯 Z、F8000/F10000、页尾编排和奎享缓存证据边界的文档漂移；门禁 pytest basetemp 改为可写探测（`results/.tmp/pytest` 被其它账号占用时退到 `pytest-<pid>`） | 已跑 `docs` + `hub` profile pass（`hub`：pytest 86 passed、e2e PASS）；不替代硬件验收 |
| 已推 `04733f9`/`3fcab88` | `docs/troubleshooting.md`、`docs/release-readiness.md`、`docs/worktree-inventory.md`、`mcp-server/hutuji_mcp/gcode_gen.py` 等 16 文件 | 记录 `15f73c9` 窗口化流控有纸 draw 无回归；新增 troubleshooting 偶发行序错乱一节（10 条字节通路假设读码证伪）；`LINESIMPLIFY_TOLERANCE_MM` 0.1→0.05 | `hub` 门禁 pass（pytest 110 passed、e2e PASS）；容差调整**仅 pytest+e2e 背书，无纸面证据**；京东云生产仍是旧版（scp 更新后需重跑 preflight） |
| 已推 `2071a83` | `docs/kxnx/README.md`、`docs/kxnx/findings/05`、`docs/kxnx/findings/17-reconnect-resend-hazard.md`（新）、`docs/kxnx/alignment/plan.md`、`docs/troubleshooting.md` | 修正反编译树路径（`kx/windows_hutuji/` 不存在；三树对照 + 行号基准声明）；新增 findings/17 奎享重连重发缺陷与不对齐决定；troubleshooting 证伪表补 `error:8` 重发/InputBuffer 临界区行号/Y 互换说明 | `docs` 门禁 pass；findings/17 第 2/3 节为读码推断，**未在奎享实测** |
| 已推 `ba8334f` | `docs/kxnx/findings/18-paixi-inkscape-px-comparison.md`（新）、`docs/kxnx/README.md`、`docs/worktree-inventory.md` | 派曦绘图（inkscape_px）对照研究：源树 4131 行版 z_stepper 笔控序列（`G1 Z4` 软着陆两段 vs 我方 `G1 Z5.0 F10000`）、两 fork M3/M5 宏方向相反（派曦 DOWN=0/UP=5 vs 我方 M3→Z5/M5→Z0）、流控对照 | `docs` 门禁 pass；全部读码 + git 史，**未重跑派曦实机** |
| 已推 `7545e5a` | `docs/kxnx/findings/19-pause-abort-error-and-dual-executor.md`（新）、`findings/20-ui-job-lifecycle-and-reconnect.md`（新）、`findings/{09,12,17}.md`、`docs/kxnx/README.md`、`docs/worktree-inventory.md` | 深挖并勘误 pause/普通 stop/reset2、f/g 双执行器、UI→JobManager→Executor、FileStatus 与串口重连闭环；撤销不存在的 `f/c.java`/`f/i.java` 结论；确认 TCP 路径走设备文件执行器、串口才走 100B 行流窗口 | `docs` 门禁 pass、`git diff --check` 干净；全部读码，**未在奎享实机注入断线** |
| 已推 `be27037` | `docs/kxnx/findings/21-transport-watchdog-and-sd-executor.md`（新）、`docs/kxnx/findings/{01,02,03,04,06,09,10,12,15}.md`、`docs/kxnx/README.md`、`docs/design/p2-windowed-flow-control.md`、`docs/worktree-inventory.md` | ①深挖传输基类与 ESP32-Grbl 控制器并勘误 findings/10：`l`/`o` 是**发送侧**语义（`b/a.java:143-144`）、两级探活 = 5s + 约 1s（不是 5s+5s）、RTS/DTR 与看门狗无关、`a.b.b` = MINA `IoBuffer` autoExpand（2048 是**初始容量**）结案、`machine/b/e.java` 只存在于 `decompiled_full/`；补齐 SD/ESP 执行器全链路（`machine/f/g.java` + `machine/c/a.java`）。②文档漂移治理：findings/01/03/04 头部的 `decompiled/...` 死路径换成实际树名并加行号基准补注（`grbl`/`marlin` 主树 = full **+3**、`kat` +5、`ebb` 同号，**禁止跨文件套统一偏移**）；`grbl/a.java` 旧号统一到主树（`:262-284`→`:265-287`、`:351-361`→`:354-362`、`:312`→`:315-317`、`:270-275`→`:273-278`、WiFi 看门狗 `:374-413`→`:379-426`）；`marlin` Stepper 分支 `:196-201`→主树 `:199-203`；标注 `machine/kat/a.java` **只在 `decompiled_full/`**（主树控制器是 `kat/b.java:42-43`）；推翻 findings/09「`?` 周期 2000ms Timer 在 `machine/c` 基类」旧说（实为 `grbl/a.java:239-240` 的**一次性** 2s Timer，且 WiFi 分支 `machine/c/a.java:43-45` 跳过 `super.a()` → 该 Timer 在 WiFi 下不创建） | `docs` 门禁 pass、`git diff --check` 干净；全部读码，**未在奎享实机抓包**；结论是**不改我方代码**，唯一待修漂移记在 findings/21 §7（xiaozhi `hutuji_pipe.cc:44-45` 注释，留待与固件改动一并处理）|
| **当前未提交** | `AGENTS.md`、`docs/agent-gate.md`、`docs/protocol.md`、`docs/release-readiness.md`、`docs/worktree-inventory.md`、`mcp-server/hutuji_mcp/gcode_gen.py`、`mcp-server/tests/test_contract.py`、`scripts/agent_gate.py`、`scripts/grbl_wifi_setup.py`、`scripts/xiaozhi_console.py` | 按「文档 + 逆向对照」code review 的 10 条待批清单落地：①`validate_gcode` 默认模态 `active_motion` 初值 `G1`→`G0`（对齐 Grbl `GCode.h` `Seek = 0` 默认），补裸 `X0 Y0` 必拒 + 显式 `G1 X0 Y0` 仍放行两条回归；②`--profile full` 未设 `GRBL_ROOT` 由 skip 改 **fail**；③`grbl_wifi_setup.py` 凭据占位符化 + `getpass`/`--dry-run`/`--yes` 与非零退出，并删掉与固定 IP 矛盾的 mDNS 结语；④`xiaozhi_console.py` 知识库更新改「先上传成功再删旧」，各失败路径非零退出；⑤linesimplify 注释 `F1500`/`~20ms` → `F8000`/`~25ms` 校正，测试 docstring 与假 vpype 产物头同步；⑥新增容差字面量（0.05/0.3）与生产 profile 形状逐键断言（直接读 `hutuji_gwrite.toml`）；⑦`protocol.md` 括号拒绝口径改指 `gcode_gen.py:239-243`（原指 235-237 是注释段，非拒绝点）；⑧门禁新增 `doc_line_refs` 层（basename 唯一解析 + `:12-34` 区间 + `checked=0` 判 fail）并给 pytest/e2e 加 600s/300s 超时；⑨`DECISION_ANCHORS` 补「哑管道 / 云端生成」；⑩pytest 条数 110→115 | `hub` 门禁 pass（pytest **115 passed**、e2e PASS、`doc_line_refs` 校验 2 处 / 跳过 112 处）；纯离线，**无实机、无 VPS 复验**；`.claude/worktrees/agent-*` 两个旧 worktree（各 6.2M、`84ad62b`、干净）仍在册，已在门禁解析里排除，是否 `git worktree remove` 待用户决定；git 史里的真实 WiFi 口令未清除，是否轮换待用户决定 |
| 本地忽略 | `results/` | 门禁报告 | 不入库 |

## Grbl_Esp32 `D:/Users/Grbl_Esp32`  
分支：`fix/panel-hold-after-align-116687e`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| **已提交** `e7f373f` | §9 keepalive 编译修复及既有 §9 改动 | fz `standard` 曾 pass；已烧录并通过 keepalive、`[ESP901]`/`Changing=Off` 实机项 | 仍缺有纸 `Changing=On`；不要把已过子项扩大成完整换纸验收 |
| **已提交/已推/已烧录** `8d13b5c` | `Grbl_Esp32/src/GCode.cpp`、`Protocol.cpp`、`Machines/custom_3axis_hr4988.h` | 固化此前未提交的笔控/运动相关改动：M3/M5 `protocol_buffer_synchronize()`、A4 默认行程 210×297、segment underflow 收敛；fz `standard` pass（208.91s）+ PlatformIO `release` pass；COM3 烧录成功并完成授权/Telnet/`Changing=Off`/A4 行程最小核验 | 不要把源码/空闲核验扩大成有纸 `Changing=On`、自动换纸或断连重画验收 |
| **外仓提交** | 根 `AGENTS.md` 硬件链（MK38 指针 + 禁改脚） | 仅文档 | 随 Grbl 仓推送 |
| 本地忽略 | `.claude/`、`firmware_out/` | 本地 Claude 配置与构建输出；已通过 Grbl `.git/info/exclude` 忽略 | 勿提交；无需为工作树干净删除 |

## fz `D:/Users/zhugu/fz`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| **已提交** `4c26bbc` | `paper_firmware_contract.json`；`scripts/agent_gate.py`；`AGENTS.md` | 商业基线契约 + wokwi 软失败 | 可推远程 |

## xiaozhi-esp32 `D:/Users/xiaozhi-esp32`  
分支：`feat/hutuji-usb-pipe`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| **已提交** `305b016` | 根 `AGENTS.md` hutuji 专节 | 约束入口 | 已推远程 |
| **当前源码 HEAD** `15f73c9` | `feat/hutuji-usb-pipe` | `c1267ec` WiFi 修复 + C: ETA（对齐奎享 f.java j()/h()）+ A: 窗口化流控 S3（window=512、换纸行排空、c_line FIFO） | 已提交/推送/构建/烧录 COM6；自动测试镜像 `xiaozhi.bin=0x2cdcb0`（SHA256 `090D65AB...8D0E`）；有纸窗口化 draw `state=done` 无回归（`results/s3-ed5b132-windowed-flow-eta-paper-draw-evidence-20260729.md`）；窗口满路径未压到（图<512B） |
| **当前实机烧录基线** `15f73c9` 自动测试镜像 | `feat/hutuji-usb-pipe` | `c1267ec` 基线 + 窗口化流控 S3 + ETA；换纸期防 silent-poll 误杀 TCP | S3-LC-01/MK38 有纸复测：窗口化 draw 14 行 → `M30` → `state=done`，无 `error:8`/窗口死锁；窗口满路径未压到（图<512B）；8.3 断连重画未在本固件回归 |
| **工作树** | untracked build dir only | `git status --short` 显示 `?? build-hutuji-8_3-autodraw/` | 构建产物，不提交；不要为工作树干净删除。下一步做有纸自动换纸/`Changing=On` |

## 明确不要做的事

- 不要为了「工作区干净」丢弃上表未提交资产。  
- 不要把 `Branch_736afa70` 整支合并进商业分支「图省事」。  
- 约束矩阵闭环 ≠ §9 已烧录 ≠ M2 完成 ≠ 可出货。  
