# Agent 防幻觉与禁乱改

> **目的**：下一任代理不得凭训练记忆编造事实、不得为「做绿」乱改程序。  
> 本页是行为约束；契约仍以 `protocol.md` 为准，状态仍以 `release-readiness.md` 为准。  
> 可跑检查见 `agent-gate.md` / `scripts/agent_gate.py`。

## 0. 铁律（违反即停）

1. **无证据不宣称**：命令输出、commit、设备编号、日志路径缺一，就只能写「未验证」。  
2. **无清单不改代码**：动手前必须能指出「改哪些路径 + 为什么 + 对应哪条契约/任务」；指不出 → 只读调查或问用户。  
3. **不覆盖用户工作树**：`git status` 已有改动默认属于用户；禁止 `checkout`/`reset --hard`/`clean` 清掉。  
4. **不重开已否决决策**：USB Host / CH9350 / 阿里云 / 合并双芯片固件 —— 见根 `README.md` 决策表。  
5. **不为门禁绿去改产品行为**：禁止改换纸机械/时序/运动/授权/U1；禁止碰 `report_realtime_status()` / `report_state_text()`。  
6. **不把文档当实现**：写了说明 ≠ 代码已改 ≠ 硬件已验 ≠ 可出货。
7. **不准简化设计**：禁止以「可选 / 可去掉 / 可省略 / 看起来没必要 / 标了 optional」为由删除或弱化任何负载设计（管线步骤、校验、安全边界、画质参数、流控、状态机分支、每行带 F 等）。文档里凡是把**现行负载项**写成「可选 / 待做 / 可放宽」的许可，**默认视为假的**——先与 `protocol.md` §5、`mcp-server/hutuji_mcp/hutuji_gwrite.toml`、`gcode_gen.py` 常量、`test_contract.py` 钉死值核对；若文档与现行定案冲突，**以定案为准**并把文档标「已否决 / supersede」，绝不照文档去删。注意：`linesimplify` / `linemerge` 是 vpype 管线的**负载步骤**（不是「简化设计」），其容差由测试钉死，不得顺手调参或移除。

## 1. 真值表（幻觉高发区）

| 问题 | 唯一真值 | 禁止用 |
|------|----------|--------|
| 做什么/不做什么 | 根 `README.md` 决策 **7 条** | 记忆文件、聊天摘要、过时 blog |
| 板间契约 | `protocol.md` **v0.5** | 口播「应该差不多」 |
| 现在卡在哪 | `release-readiness.md` | 「感觉快好了」 |
| 本仓是否通过 | `scripts/agent_gate.py` 报告 | 「我跑过类似的」 |
| Grbl 是否通过 | fz `results/agent_gate_last.json` 的 `overall_status` | 本仓 `full` 源码标记 |
| 联调是否过 | `bringup-sop.md` 阶段判据 + 记录模板 | 仿真绿 |
| 工作树有什么 | 三仓 `git status` + `worktree-inventory.md` | 假设「干净」或「没改过」 |

外部记忆 `C:/Users/zhugu/.kimi-code/memory/projects/hutuji.md` **可能过时**；冲突一律以本仓文档为准。

## 2. 允许改动的表面积（白名单思维）

### 2.1 用户未指定任务时

只允许按 `agent-handoff.md` §6 默认下一刀推进；**禁止**自发重构、重写文档地图、「顺便优化」换纸/运动。

### 2.2 任务类型 → 可碰路径

| 任务 | 可改 | 默认不可改 |
|------|------|------------|
| 本仓文档/门禁 | `docs/**`、`AGENTS.md`、`CLAUDE.md`、`README.md`、`.cursor/rules/**`、`scripts/agent_gate.py` | `mcp-server/` 业务逻辑（除非任务点名） |
| ③ MCP | `mcp-server/**`（不含把密钥写入仓库） | Grbl / xiaozhi 固件 |
| Grbl §9 | **仅** `protocol.md` §9 列出的文件与改动意图：`TelnetServer.cpp` keepalive、`PaperSystem.cpp` 四处 `[PaperStatus]`→`CLIENT_ALL`、`Changing=` 字段；以及供 fz 的 `*Core.h` **header-only 恢复** | 换纸步进/延时/电流、授权、`Report.cpp` 状态字、任意「顺手重构」 |
| ② S3 哑管道 | `D:/Users/xiaozhi-esp32` 板级工具类 + 本仓 `firmware/*.md` 说明 | xiaozhi 核心模块大改、合并进 hutuji |
| 京东云 | `deploy/**`、`docs/deployment.md`（模板）；实机操作另留证据 | 把真实密钥写进 unit/仓库 |
| fz 契约对齐 | `D:/Users/zhugu/fz/hardware_sim/paper_firmware_contract.json` 等**仿真期望** | 为对齐仿真去改商业固件常量 |

### 2.3 Grbl 绝对禁止（再列一遍）

- 换纸机械/时序/运动/授权/U1-Grbl 协议逻辑  
- `report_realtime_status()`、`report_state_text()`  
- 把 63 处换纸 `grbl_msg_sendf` 全改 `CLIENT_ALL`（§9 明确否决）  
- 为让 `paper_contract` 绿而把商业分支常量改回旧分支（应改 **fz 契约**）

## 3. 禁止幻觉的输出

代理在回复/文档/提交说明中**不得编造**：

- 未跑过的测试结果、门禁 overall、pytest 数量  
- 未查看的 commit hash / 分支 HEAD  
- 未实测的 IP、串口现象、换纸时长、Telnet 收包内容  
- 「已烧录 / 已出货 / 客户已验收」类状态  
- 不存在的文件路径、工具名、MCP 接入点  

不确定时写：**未知 / 未跑 / 未见工作树**，并给出下一条可执行命令。

## 4. 改代码前的强制自检（复制到思考里）

```
[ ] 我读过 agent-handoff + release-readiness 本节相关行
[ ] 我跑过 git status，知道三仓脏文件不属于我乱删范围
[ ] 我能指出契约段落或用户原话作为改动依据
[ ] 改动文件列表 ⊆ 上表白名单
[ ] 改完知道要跑哪扇门禁（本仓 hub / full / fz standard）
[ ] 我不准备用「应该」「大概」「记得」代替证据
```

任一未勾 → **只读**或先问用户。

## 5. 证据最小集（按层）

| 层 | 最小证据 |
|----|----------|
| 本仓软件 | `results/hutuji_gate_last.md` 且 overall=pass |
| Grbl 改动 | fz `results/agent_gate_last.json` → `overall_status=pass` + diff 范围说明 |
| 烧录/实机 | 设备编号 + 固件 commit + `docs/templates/device-test-record.md` 填写 |
| 京东云 | `deployment.md` 勾选 + 不暴露密钥的命令输出摘要 |
| 出货 | `release-checklist.md` + `production-sop.md` 签核 |

## 6. 与工作树清单的关系

`docs/worktree-inventory.md` 记录**跨仓未提交资产**。  
接手时先对清单与 `git status`；清单过期就更新清单，**不是**把别人的改动回滚掉。

## 7. 发现冲突时

1. 停手  
2. 记下：文件、段落、冲突双方  
3. 按 `docs/README.md` 真值优先级修正低优先级文档  
4. 需要改契约 → 两侧评审 `protocol.md`，禁止静默改语义  
