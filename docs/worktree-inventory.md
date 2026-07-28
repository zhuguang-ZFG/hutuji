# 跨仓工作树清单（防误删 / 防重复劳动）

> **状态日期：2026-07-28（S3 实机回归后，三仓 `git status` 已复核）。**
> 用途：告诉下一任代理「哪些改动已经在磁盘上、不要当不存在、不要 checkout 清掉」。  
> 每次相关提交或丢弃后**必须更新**本页；与 `git status` 冲突时以 `git status` 为准并回写本页。

## 怎么用

1. 开干前：三仓各跑 `git status --short`，对照本页。  
2. 发现本页未记载的脏文件：先问是否用户资产，默认保留。  
3. 提交/丢弃后：更新本页对应行（或标「已清空」）。

## hutuji（本仓库）`D:/Users/hutuji`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| **本轮文档批次** | 当前 `git diff --name-only` 的 23 份规则/协议/SOP/逆向/固件说明 | 状态纠错、逆向勘误、显式 `M30` 与断连恢复契约、`05f4563` 证据同步 | hub 门禁通过后随本批次提交/推送；不包含业务代码 |
| 已推 | `docs/hardware/*`（MK38 + S3 原理图/对照） | 双板级资料 | 保持；禁改固件脚位 |
| **提交后工作树** | tracked clean | 本批次提交后 `git status --short` 应无输出 | 若不符，以 Git 为准回写本页 |
| 本地忽略 | `results/` | 门禁报告 | 不入库 |

## Grbl_Esp32 `D:/Users/Grbl_Esp32`  
分支：`fix/panel-hold-after-align-116687e`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| **已提交** `e7f373f` | §9 keepalive 编译修复及既有 §9 改动 | fz `standard` 曾 pass；已烧录并通过 keepalive、`[ESP901]`/`Changing=Off` 实机项 | 仍缺有纸 `Changing=On`；不要把已过子项扩大成完整换纸验收 |
| **本地修改（用户资产）** | `Grbl_Esp32/src/GCode.cpp`、`Protocol.cpp`、`Machines/custom_3axis_hr4988.h` | 当前工作树已有的笔控/运动相关改动；本次续接未修改 | **禁止回退、清理或顺手调整**；若后续任务要碰，先重读白名单并单独跑 fz 门禁 |
| **外仓提交** | 根 `AGENTS.md` 硬件链（MK38 指针 + 禁改脚） | 仅文档 | 随 Grbl 仓推送 |
| 未跟踪 | `firmware_out/` | 本地构建输出 | 勿提交 |

## fz `D:/Users/zhugu/fz`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| **已提交** `4c26bbc` | `paper_firmware_contract.json`；`scripts/agent_gate.py`；`AGENTS.md` | 商业基线契约 + wokwi 软失败 | 可推远程 |

## xiaozhi-esp32 `D:/Users/xiaozhi-esp32`  
分支：`feat/hutuji-usb-pipe`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| **已提交** `305b016` | 根 `AGENTS.md` hutuji 专节 | 约束入口 | 已推远程 |
| **当前源码基线** `05f4563` | `feat/hutuji-usb-pipe` | 5 文件 S3 资产已提交；含显式 `M30`、重连先判 `Changing`、受限 reset、废纸换纸与 PSRAM 从头重发 | IDF 6.0.1 构建通过（`0x2ccde0`，余 `0x123220`/29%，SHA256 `5C79178B...92ABD`）；尚未烧录/实测该 commit |
| **工作树** | tracked clean | `git status --short` 无输出 | `build/`、`sdkconfig*`、`releases/` 为生成/忽略产物，不提交；下一步烧录 `05f4563` |

## 明确不要做的事

- 不要为了「工作区干净」丢弃上表未提交资产。  
- 不要把 `Branch_736afa70` 整支合并进商业分支「图省事」。  
- 约束矩阵闭环 ≠ §9 已烧录 ≠ M2 完成 ≠ 可出货。  
