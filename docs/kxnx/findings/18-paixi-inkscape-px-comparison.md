# 18 · 派曦绘图（inkscape_px）对照研究：它为什么落笔没问题

> 结论先行：**派曦「落笔没问题」与 hutuji 不矛盾——两家的 Z 方向定义一致（Z 正=落笔、0=抬笔），只是数值与进给不同**（派曦 `G1 Z4` 软着陆两段 `F1200/F250`，hutuji 实测 `G1 Z5.0 F10000`）。真正需要注意的是：**派曦旧固件 fork 的 M3/M5 宏方向与本机相反**（DOWN=0/UP=5），而 hutuji fork 的 M3/M5 宏方向与本机一致（M3→Z5 落、M5→Z0 抬）——两套宏不通用，交叉参考时必须带机器语境。
>
> 本文证据均为读码 + git 史，**未重新实机运行派曦**；用户此前的实测口径是「源树部署版（4131 行）落笔没问题」。

## 1 研究对象与版本坑

`D:/GitHub/inkscape_px` 里同一份「派曦绘图」有三个形态，**行为不同，勿混**：

| 形态 | 路径 | 笔控行为 |
|------|------|----------|
| release bundle 旧快照 | `release/paixi_draw_bundle_20260412_004444/app/grbl_plotter.py`（1817 行） | 默认 `M3 S1`/`M5` 主轴语义，**删除全部纯 Z 行**（`:1667-1684`），笔动作靠固件解释 M3/M5 |
| **源树部署版（用户实测版）** | `inkscape-clean/share/extensions/grbl_plotter.py`（4131 行） | 默认 `penControlMode=z_stepper`（`:311-329`），**保留并规范化 Z 运动行**，M3/M5 仅 `mcode` 模式可选 |
| 固件 fork 子模块 | `Grbl_Esp32/`（zhuguang-ZFG/Grbl_Esp32 fork，检出 `7b0a69d`） | `NullSpindle.cpp` 在 `PAIXI_PEN_M3_M5_CONTROL` 下 M3/M5→Z（DOWN=0/UP=5，F600，三同步） |

bundle 版还有两处残缺：缺 `paixi_runtime/control_bridge.py`（import 即 ImportError）、`grbl_plotter.py` 用了 `Path` 却没 import——bundle 不是可独立运行的整体，是源树快照。

## 2 源树 4131 行版 z_stepper 笔控序列（默认参数）

生成链三段式：gcodetools 原生生成 → `normalize_z_stepper_path_lines` 规范化（`paixi_runtime/gcode_line_utils.py:74-127`）→ `gcode_finalize.py` 后处理（笔动作去重 `:1448-1471` → Z 进给重写 `:1473-1518` → 软着陆 `:1624-1695` → 共线合并）。

每条子路径最终行序列：

```
G0 Z0 F2000          ← 抬笔（G0，注意不是 G1）
G00 X..Y..           ← 空走，无 F（走固件 $110/$111 max rate；UI 的 travelSpeed 不进文件）
G1 Z3.3 F1200        ← 软着陆快段（z_mid = 落点 − 0.7）
G1 Z4 F250           ← 软着陆慢段（最终压笔）
G01 X..Y.. F1100     ← 首段画线带 F，后续 modal 无 F（有机型档案时被推荐值覆盖）
G0 Z0 F2000          ← 段尾抬笔
```

- 文件头仅 `G21`/`G90`，**全文无 G92**（置零只有手动按钮发 `G92 X0 Y0`，`:3118-3120`）。
- 收尾 `M5` + `G0 X0 Y0`（`returnHomeAfterJob` 默认开，`:227`），**无 M2/M30**。
- 软着陆默认开（0.7mm/F250，`:295`）；`effect()` 强制 `penUpZ=zSurface=zSafe=0.0`、`zDraw=penDownZ`（`:323-335`）。
- `penDownZ` 取值轨迹（git 史）：`1e32c5b2` 引入时 **5.0** → `b3eba569` 改 1.0 → `d27ae537` 改 **4.0**，两天三改的经验值，无机型档案化。**初值 5.0 与 hutuji 实测值一致**，说明该值强机种相关。

## 3 与 hutuji 生产模板逐项差异

| 项 | 派曦 z_stepper | hutuji 生产模板 |
|----|----------------|-----------------|
| 落笔行 | `G1 Z3.3 F1200` + `G1 Z4 F250` 两段 | `G1G90 Z5.0F10000` 单行 |
| 抬笔行 | **`G0`** `Z0 F2000` | **`G1`** `G90 Z0.0F10000` |
| Z 值 | 落 4.0 / 抬 0 | 落 **5.0** / 抬 0（本机实测包络） |
| Z 进给 | 1200 / 末端 250 / 抬 2000 | 统一 **F10000**（实测稳定值） |
| 空走 | `G00 X..Y..` 无 F | `G0 X..Y..F8000` 显式 F |
| 画线 | 首段 F1100，后续 modal；6 位小数 | 每行 `F8000` |
| init | `G21`/`G90` 两行 | `G21/G90/F8000` + 两次幂等 `G1G90 Z0.0F10000` |
| G92 | 全文无 | S3 首画/重画前旁路 `G92 Z0`（fresh Idle +100ms） |
| 收尾 | `M5` + `G0 X0 Y0` | 文件只抬笔；S3 依次旁路 `G1G90 X0Y0F8000` 归位 + `M30`（2026-08-14 起） |
| 限幅 | 软警告（按 `$130/$131` 档案缩放居中） | MCP 端硬限 X≤190/Y≤277（唯一安全防线） |

**直接搬派曦输出喂 hutuji 机器不行**：Z 段 F1200/F250/F2000 与 `G0` 抬笔都在本机实测包络之外（本机 Z 运动要 F10000 才稳，且方向定义虽同、行程 4.0 未必压得住纸）。反过来 hutuji 模板也不能搬给派曦旧机器——那是另一台 Z 方向相反的机器。

## 4 两个固件 fork 的笔控实现对比（同一远端，不同分支）

两边都是 `zhuguang-ZFG/Grbl_Esp32` fork，但 M3/M5→Z 是**两次独立实现**，`880af29`（派曦侧）不在 hutuji 分支历史中：

| | 派曦 fork（`7b0a69d`） | hutuji fork（`8d13b5c`） |
|--|------------------------|--------------------------|
| 实现点 | `Spindles/NullSpindle.cpp`（`PAIXI_PEN_M3_M5_CONTROL`） | `GCode.cpp:1484-1496`（`USE_M3_M5_AS_PEN_UP_DOWN`） |
| Z 目标 | DOWN=**0.0** / UP=**5.0**（那台机器 Z 正=抬） | M3→**5.0** 落 / M5→**0.0** 抬（本机 Z 正=落，`custom_3axis_hr4988.h:52-54`） |
| 运动方式 | `mc_line()` 插补，F600 | `mc_line()` + `rapidMotion=1`（走 G0 seek rate） |
| 同步 | `protocol_buffer_synchronize()` + `plan_sync_position()` + `gc_sync_position()` 三同步 | 仅 `protocol_buffer_synchronize()`，再手改 `gc_state.position[Z]`（`:1493-1495`） |

观察（读码，未验证影响）：hutuji 版少了 `plan_sync_position()`，planner 侧 Z 位置与 `gc_state` 的同步只靠 `mc_line` 自身；但 **hutuji 生产路径不用 M3/M5**（下载文件禁 M3/M5，笔控走显式 `G1 Z`），这段代码在生产中不触发，仅手动 M3/M5 时走到。

## 5 流控对比与对行序错乱案的启示

| | 派曦 | hutuji |
|--|------|--------|
| 通道 | PC 串口直连 115200 8N1 无流控；控制台连接时 `rts=False/dtr=False` 避免复位踢动笔轴（`serial_session.py:39-50`） | WiFi Telnet，S3 转发 |
| 队列主路径 | **120 字节 inflight 窗口流式**（`serial_runtime.py:130-203`），`error:`/ALARM → 中止任务入 `failed/`，重试 ≤3 次退避 5s | 窗口 512 字节 + fresh `Idle` 完成判定 + PSRAM 断连重画 |
| 调试直发 | 逐行等 ok，2s 超时/error 即整任务中止 | — |
| 手动单发 | 3s 窗口，**吞 error 不抛**（`serial_runtime.py:100-128`） | — |

启示（读码推断，非实测）：

1. 派曦队列路径也是窗口流式（120B）且不错序——**窗口化本身不是原罪**；但它没有 hutuji 的「断连 PSRAM 重画 / abort 恢复 / fresh-`Idle` 旁路 `G92 Z0`」这些状态耦合点，它「不错序」不能为 hutuji 转发层背书，嫌疑面仍然收敛在 S3 转发/窗口化逻辑（与 troubleshooting 既有 10 条证伪一致：字节通路已排除，下一刀仍是 echo 二分）。
2. 派曦手动路径吞 error 与奎享队列路径吞 error 是同款隐患（质量受损但不错序），hutuji 无人值守选择无条件中止是三家之中唯一 fail-closed 的，保持。

## 6 明确不对齐清单（派曦侧）

| 项 | 派曦 | hutuji 决定 | 理由 |
|----|------|-------------|------|
| 抬笔用 G0 | `G0 Z0 F2000` | 保持 `G1G90 Z0.0F10000` | 本机 Z 实测包络是 G1+F10000；G0 走 seek rate 未验证 |
| 软着陆两段 | 0.7mm/F250 慢段 | 不引入 | 本机 F10000 单行已实测稳定出墨；多加状态无收益 |
| 收尾 `G0 X0 Y0` | 默认开 | 禁止 | 本机固件把 `G0` 落点 XY≈0 判为换纸触发（`docs/protocol.md` §5） |
| 文件无 G92 | 依赖机器当前 WCS | 保持 S3 旁路 `G92 Z0` | 断连重画必须重建 Z 基准，派曦没有重画路径所以不需要 |
| penDownZ=4.0 | 经验值 | 保持 5.0 | 本机实测；派曦初值也是 5.0 |

## 7 未验证

- 本文全部结论为读码 + git 史，未重新运行派曦扩展生成 G-code 逐字核对，未实机复跑派曦。
- 「派曦旧机器 Z 方向与本机相反」的物理解释（两台不同机器/不同弹簧装配）未与用户核实，仅为使两套宏自洽的推断。
- hutuji fork M3/M5 路径缺 `plan_sync_position()` 的实际影响未验证（生产路径不触发，优先级低）。
