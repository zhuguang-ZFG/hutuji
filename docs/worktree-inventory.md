# 跨仓工作树清单（防误删 / 防重复劳动）

> **状态日期：2026-07-29（Grbl `8d13b5c` 已提交/推送/烧录；xiaozhi `3ca9d31` 已提交/推送，8.3 自动触发镜像已烧录 COM6；三仓 `git status` 已复核）。**
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
| **当前未提交** | `AGENTS.md`、`docs/protocol.md`、`docs/research.md`、`mcp-server/README.md`、`mcp-server/hutuji_mcp/hutuji_gwrite.toml`（仅注释）、`docs/kxnx/{README.md,alignment/plan.md,findings/02,06,07,09,12,13*.md}`；以及此前已有的 `docs/agent-gate.md`、`firmware/README.md`、`scripts/agent_gate.py` | 同步 S3 `3ca9d31`/8.3 证据，修正生产 G-code 纯 Z、F8000/F10000、页尾编排和奎享缓存证据边界的文档漂移；门禁 pytest basetemp 改为可写探测（`results/.tmp/pytest` 被其它账号占用时退到 `pytest-<pid>`） | 已跑 `docs` + `hub` profile pass（`hub`：pytest 86 passed、e2e PASS）；提交前保留并整体复核，不能为“干净”回退 |
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
| **当前源码 HEAD** `3ca9d31` | `feat/hutuji-usb-pipe` | `05f4563` 行为基线 + `27eff73` 默认关闭的 `HUTUJI_AUTO_TEST_DRAW` / `HUTUJI_AUTO_TEST_DRAW_URL` CMake 注入 + `M3/M5` planner-sync `ok` 长等待修复 | 已提交/推送；默认 IDF 6.0.1 构建通过（`xiaozhi.bin=0x2cce30`，余 `0x1231d0`/29%，SHA256 `5D5FE23C...BBA6`）；8.3 自动触发镜像构建并烧录 COM6（`xiaozhi.bin=0x2cd070`，余 `0x122f90`/29%，SHA256 `98C3B6AF...C122C`） |
| **当前实机烧录基线** `3ca9d31` 自动测试镜像 | `feat/hutuji-usb-pipe` | 含显式 `M30`、重连先判 `Changing`、受限 reset、废纸换纸与 PSRAM 从头重发；修复页尾 `M5` 长等待 | S3-LC-01/MK38 8.3 复测：`results/bringup-8.3-fault-capture-20260729-010436.md`。普通画线断连后已出现重连、`Changing=Off`、废纸 `M30`、`[PaperStatus] 0`、PSRAM 重画、55/55 行完成与 fresh `Idle`；最终页尾自动换纸因无纸 `[PaperStatus] 2` + `error:90`，任务 `state=error`，不等于有纸自动换纸或完整 `done/Idle` |
| **工作树** | untracked build dir only | `git status --short` 显示 `?? build-hutuji-8_3-autodraw/` | 构建产物，不提交；不要为工作树干净删除。下一步做有纸自动换纸/`Changing=On` |

## 明确不要做的事

- 不要为了「工作区干净」丢弃上表未提交资产。  
- 不要把 `Branch_736afa70` 整支合并进商业分支「图省事」。  
- 约束矩阵闭环 ≠ §9 已烧录 ≠ M2 完成 ≠ 可出货。  
