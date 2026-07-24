# 成熟方案调研（2026-07-24，scout 调研）

结论：**每个环节都有成熟先例可拼，最大的"不用重造"在 ③**。

## 直接采用（MIT，可商用）

| 项目 | 用在哪 | 怎么用 |
|------|--------|--------|
| [abey79/vpype](https://github.com/abey79/vpype) | ③ MCP 服务 | `pip install vpype`，SVG 读入 + `linesort`/`linemerge` 路径优化 |
| [plottertools/vpype-gcode](https://github.com/plottertools/vpype-gcode) | ③ MCP 服务 | 写一个 `hutuji` gwrite profile（笔起落 M3/M5、200×200 行程、进给率），`vpype read in.svg linesort gwrite --profile hutuji out.gcode` 一条命令出成品 |
| [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) | ② 固件基座 | 官方设备侧 MCP 框架内加 hutuji 工具（已在做） |

## 只借思路（GPL/AGPL，不抄代码）

| 项目 | 许可证 | 借什么 |
|------|--------|--------|
| [bdring/FluidDial](https://github.com/bdring/FluidDial) | GPL-3.0 | **方案 E 的直接先例**：ESP32 当 Telnet 客户端连 Grbl_Esp32 :23——连接管理、断线处理、`<Idle|…>` 状态解析。我们的 Pipe 已是自研等价实现 |
| [luc-github/ESP3D](https://github.com/luc-github/ESP3D) | GPL-3.0 | G-code 逐行流式发送的状态机（发一行等 ok） |
| [ucodia/plotter-studio-mcp](https://github.com/ucodia/plotter-studio-mcp) | AGPL-3.0 | ③ MCP server 工具面设计：`plot_start/plot_stop/plot_status` + HTTP 文件中转（与我们"G-code 存 URL 设备下载"同构）——照抄接口划分，代码全自研 |
| [nornagon/saxi](https://github.com/nornagon/saxi) | AGPL-3.0 | 路径优化/运动规划算法思路（本期不用） |
| [nulllaborg/xiaozhi-esp32-nulllab](https://github.com/nulllaborg/xiaozhi-esp32-nulllab) | MIT | 设备侧把"AI 意图"映射到硬件动作的工具定义方式 |

## 调研判断

- **没找到现成的"小智控制写字机"完整方案**——hutuji 的集成形态有新意，差异化在双芯片分工。
- 备选转换器：[sameer/svg2gcode](https://github.com/sameer/svg2gcode)（Rust，MIT 未核实）、[vpype-gscrib](https://pypi.org/project/vpype-gscrib/)（更新）。优先 vpype-gcode（与 MCP 服务同 Python 生态）。
- 许可证红线：AGPL/GPL 项目（saxi、plotter-studio-mcp、ESP3D、FluidDial、FluidNC、DrawingBotV3）一律不抄代码、不部署调用。
- star 数与部分许可证未经逐一核实，集成前核对 LICENSE。
