# 跨仓工作树清单（防误删 / 防重复劳动）

> **状态日期：2026-07-26。**  
> 用途：告诉下一任代理「哪些改动已经在磁盘上、不要当不存在、不要 checkout 清掉」。  
> 每次相关提交或丢弃后**必须更新**本页；与 `git status` 冲突时以 `git status` 为准并回写本页。

## 怎么用

1. 开干前：三仓各跑 `git status --short`，对照本页。  
2. 发现本页未记载的脏文件：先问是否用户资产，默认保留。  
3. 提交/丢弃后：更新本页对应行（或标「已清空」）。

## hutuji（本仓库）`D:/Users/hutuji`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| 可能已脏 | `docs/agent-handoff.md`、`docs/release-readiness.md` | §9/fz 绿后的状态回填 | 与防跑偏文档一并提交 |
| 已提交基线 | `docs/agent-gate.md`、`scripts/agent_gate.py`、`.cursor/rules/` 等 | ① 门禁闭环（commit `e314042`） | 勿删 |
| 本地忽略 | `results/`、`firmware_out/` | 门禁报告 / 构建产物 | 不入库（见 `.gitignore`） |

## Grbl_Esp32 `D:/Users/Grbl_Esp32`  
分支：`fix/panel-hold-after-align-116687e`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| 未提交修改 | `Grbl_Esp32/src/WebUI/TelnetServer.cpp` | §9-A keepalive 四参数 | **保留**；随 §9 提交 |
| 未提交修改 | `Grbl_Esp32/src/PaperSystem.cpp` | §9-B1′ `[PaperStatus]`→`CLIENT_ALL`；§9-B2′ `Changing=` | **保留**；勿改换纸步进/延时 |
| 已暂存/新增 | `LicenseCore.h` `PaperBtAckCore.h` `PaperSearchCore.h` `PaperSystemCore.h` `ProtocolDecisionCore.h` `WebUI/BTStateCore.h` | 从 `Branch_736afa70` **header-only** 恢复，供 fz native_sim | **保留**；不要改成去动换纸机械 |
| 未跟踪 | `firmware_out/` | 本地构建输出 | 勿提交 |

**证据**：fz `standard` 曾报 `overall_status=pass`（2026-07-26）。  
**仍缺**：提交、烧录、bringup 阶段 2 → 故 **不得**写「§9 已完成」。

## fz `D:/Users/zhugu/fz`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| 未提交 | `hardware_sim/paper_firmware_contract.json` | 契约对齐商业分支常量（timeout=10000、yield=50、Plant 1/4 等） | **保留**；不要改回旧分支 15000/25 除非商业分支也变 |
| 未提交 | `scripts/agent_gate.py` | wokwi 可选失败不进 hard_fail；protocol 超时 600s | **保留** |

## xiaozhi-esp32 `D:/Users/xiaozhi-esp32`

| 状态 | 路径 | 含义 | 处置建议 |
|------|------|------|----------|
| 可能脏 | `main/boards/lichuang-dev/hutuji_pipe.cc` | M1 keepalive 参数修正等 | 属用户/既有补丁；勿覆盖；M2 另开任务 |

## 明确不要做的事

- 不要为了「工作区干净」丢弃上表资产。  
- 不要把 `Branch_736afa70` 整支合并进商业分支「图省事」。  
- 不要在未读本页时重做 §9 或重写 paper_contract。  
