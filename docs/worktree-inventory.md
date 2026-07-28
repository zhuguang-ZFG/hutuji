# 跨仓工作树清单（防误删 / 防重复劳动）

> **状态日期：2026-07-28（Grbl `8d13b5c` 已提交/推送后，三仓 `git status` 已复核）。**
> 用途：告诉下一任代理「哪些改动已经在磁盘上、不要当不存在、不要 checkout 清掉」。  
> 每次相关提交或丢弃后**必须更新**本页；与 `git status` 冲突时以 `git status` 为准并回写本页。

## 怎么用

1. 开干前：三仓各跑 `git status --short`，对照本页。  
2. 发现本页未记载的脏文件：先问是否用户资产，默认保留。  
3. 提交/丢弃后：更新本页对应行（或标「已清空」）。

## hutuji（本仓库）`D:/Users/hutuji`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| **已推** `1186948` | `scripts/bringup_fault_capture.py`、`docs/bringup-sop.md`、`scripts/agent_gate.py`、`docs/agent-gate.md` | bringup 8.3 串口采集与 Grbl WiFi OFF/ON 断链注入辅助；门禁 pytest 临时目录固定到仓库内，避免系统 `%TEMP%` 权限假失败 | 不替代硬件验收 |
| **已推** `2c6f174` | `AGENTS.md`、`docs/agent-handoff.md`、`docs/release-readiness.md`、`docs/worktree-inventory.md` | 同步 Grbl `8d13b5c` 已提交/推送、fz `standard` pass、PlatformIO `release` pass；保留未新增烧录/换纸验收边界 | 不替代烧录/换纸验收 |
| 已推 | `docs/hardware/*`（MK38 + S3 原理图/对照） | 双板级资料 | 保持；禁改固件脚位 |
| **提交后工作树** | tracked clean | 本批次提交后 `git status --short` 应无输出 | 若不符，以 Git 为准回写本页 |
| 本地忽略 | `results/` | 门禁报告 | 不入库 |

## Grbl_Esp32 `D:/Users/Grbl_Esp32`  
分支：`fix/panel-hold-after-align-116687e`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| **已提交** `e7f373f` | §9 keepalive 编译修复及既有 §9 改动 | fz `standard` 曾 pass；已烧录并通过 keepalive、`[ESP901]`/`Changing=Off` 实机项 | 仍缺有纸 `Changing=On`；不要把已过子项扩大成完整换纸验收 |
| **已提交/已推** `8d13b5c` | `Grbl_Esp32/src/GCode.cpp`、`Protocol.cpp`、`Machines/custom_3axis_hr4988.h` | 固化此前未提交的笔控/运动相关改动：M3/M5 `protocol_buffer_synchronize()`、A4 默认行程 210×297、segment underflow 收敛；fz `standard` pass（208.91s）+ PlatformIO `release` pass | 尚未新增烧录/实机回归证据；不要把源码提交扩大成 `Changing=On` 或自动换纸验收 |
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
| **当前源码基线** `05f4563` | `feat/hutuji-usb-pipe` | 5 文件 S3 资产已提交；含显式 `M30`、重连先判 `Changing`、受限 reset、废纸换纸与 PSRAM 从头重发 | IDF 6.0.1 构建并烧录 COM6；`xiaozhi.bin=0x2ccde0`，余 `0x123220`/29%，app SHA256 `A77C8635405B7FB18B18CE2486621A7635374D75FEE37E0078CD1423F29F80CC`，merged SHA256 `9328F2B71CAC67603E9B9BF063980AE11D575A749CE7F124A4F13C0CFA690E1D`，release zip SHA256 `F11E51853E0D6E7CD223344F748330CCB583E7CC913E02E43DCD4E08D52941C2`；S3-LC-01 启动核验通过，不等于断连/换纸实测 |
| **工作树** | tracked clean | `git status --short` 无输出 | `build/`、`sdkconfig*`、`releases/` 为生成/忽略产物，不提交；下一步做完整断连重画实测 |

## 明确不要做的事

- 不要为了「工作区干净」丢弃上表未提交资产。  
- 不要把 `Branch_736afa70` 整支合并进商业分支「图省事」。  
- 约束矩阵闭环 ≠ §9 已烧录 ≠ M2 完成 ≠ 可出货。  
