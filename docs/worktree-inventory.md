# 跨仓工作树清单（防误删 / 防重复劳动）

> **状态日期：2026-07-26（约束闭环 + §9 源码已提交）。**  
> 用途：告诉下一任代理「哪些改动已经在磁盘上、不要当不存在、不要 checkout 清掉」。  
> 每次相关提交或丢弃后**必须更新**本页；与 `git status` 冲突时以 `git status` 为准并回写本页。

## 怎么用

1. 开干前：三仓各跑 `git status --short`，对照本页。  
2. 发现本页未记载的脏文件：先问是否用户资产，默认保留。  
3. 提交/丢弃后：更新本页对应行（或标「已清空」）。

## hutuji（本仓库）`D:/Users/hutuji`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| 已推/待推 | 门禁、防幻觉、约束矩阵 | ① 文档 + agent 约束闭环 | 见 `foundation-checklist.md` / `agent-constraint-matrix.md` |
| 已推 | `docs/hardware/*`（MK38 + S3 原理图/对照） | 双板级资料 | 保持；禁改固件脚位 |
| **本提交** | `docs/hardware/lichuang-szpi-official/` | 官方资源标注图 + ES7210/GC0308 等补档 | 推远程 |
| 本地忽略 | `results/` | 门禁报告 | 不入库 |

## Grbl_Esp32 `D:/Users/Grbl_Esp32`  
分支：`fix/panel-hold-after-align-116687e`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| **已提交** `cdf923f` | §9（Telnet keepalive / PaperStatus→CLIENT_ALL / Changing=）+ 六份 `*Core.h` + 根 `AGENTS.md` | 源码就绪；fz 曾 pass | **仍须烧录 + bringup 阶段 2** 才算 §9 完成；可推远程 |
| 干净（头文件） | `custom_3axis_hr4988.h` | `#define` 未改；误改注释已还原 | 勿动脚位 |
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
| **已提交**（keepalive 续） | `main/boards/lichuang-dev/hutuji_pipe.cc` | TCP keepalive 10/3/3 | 已推远程；M2 仍未做 |

## 明确不要做的事

- 不要为了「工作区干净」丢弃上表未提交资产。  
- 不要把 `Branch_736afa70` 整支合并进商业分支「图省事」。  
- 约束矩阵闭环 ≠ §9 已烧录 ≠ M2 完成 ≠ 可出货。  
