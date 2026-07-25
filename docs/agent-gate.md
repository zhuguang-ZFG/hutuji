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

本门禁用**可跑检查 + 禁止宣称表**把这些钉死。

## 2. Profile（选一个跑，禁止自造）

| Profile | 何时用 | 必须绿 |
|---------|--------|--------|
| `docs` | 只改文档/模板/规则 | 必备文件存在；决策锚点在位；禁止重开措辞未复活 |
| `hub`（**默认**） | 改本仓 MCP / 脚本 / 协议说明 | `docs` + pytest 全绿 + `e2e_local.py` 输出 `PASS` |
| `full` | 改了 Grbl 或声称 §9/联调推进 | `hub` + Grbl §9 **源码标记**检查 + **提示**必须再跑 fz `agent_gate.py` |

> `full` **不会**自动替你跑 fz 门禁（耗时长、依赖本机 fz）。  
> 改了 `D:/Users/Grbl_Esp32` 后，你必须**自己**跑 fz，且 `overall_status=pass` 才能声称修好。

## 3. 必备文件（`docs` 层）

下列文件缺失 → 门禁红；禁止用「差不多齐了」绕过：

| 路径 | 作用 |
|------|------|
| `README.md` | 决策记录表（7 条） |
| `AGENTS.md` / `CLAUDE.md` | 代理硬规则 |
| `docs/agent-handoff.md` | 防呆页 |
| `docs/agent-gate.md` | 本文件 |
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

`full` profile 仅做 §9 **源码标记**抽检（见 `scripts/agent_gate.py`），不替代上面这条。

## 7. 默认工程序（与 handoff 一致，禁止跳）

1. Grbl §9 → fz pass → 烧录 → bringup 阶段 2  
2. S3 M2 → IDF 构建  
3. bringup 阶段 0 起留证据  
4. 京东云按 `deployment.md` 上线  

用户明确指定任务时可偏离；**不得**在未指定时重写文档地图或重开否决项。

## 8. 怎么跑

```powershell
cd D:\Users\hutuji
python scripts\agent_gate.py              # 默认 hub
python scripts\agent_gate.py --profile docs
python scripts\agent_gate.py --profile full   # 需 GRBL_ROOT；仍要另跑 fz
```

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
