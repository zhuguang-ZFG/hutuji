# 成熟方案调研（2026-07-24，scout 调研）

结论：**每个环节都有成熟先例可拼，最大的"不用重造"在 ③**。

## 直接采用（宽松许可证；出货仍须保留通知）

| 项目 | 用在哪 | 怎么用 |
|------|--------|--------|
| [abey79/vpype](https://github.com/abey79/vpype) | ③ MCP 服务 | `pip install vpype`，SVG 读入 + `linesort`/`linemerge` 路径优化 |
| [plottertools/vpype-gcode](https://github.com/plottertools/vpype-gcode) | ③ MCP 服务 | 写一个 `hutuji` gwrite profile（笔起落 M3/M5、200×200 行程、进给率），`vpype read in.svg linesort gwrite --profile hutuji out.gcode` 一条命令出成品 |
| [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) | ② 固件基座 | 官方设备侧 MCP 框架内加 hutuji 工具（已在做） |
| [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | ③ MCP 服务 | 官方 Python SDK / FastMCP，当前钉在 v1 稳定线 |
| [sparfenyuk/mcp-proxy](https://github.com/sparfenyuk/mcp-proxy) | ③ 云桥接 | streamable HTTP ↔ stdio，供 `cloud_bridge.py` 调用 |

## 只借思路（GPL/AGPL，不抄入新增闭源部分）

| 项目 | 许可证 | 借什么 |
|------|--------|--------|
| [bdring/FluidDial](https://github.com/bdring/FluidDial) | GPL-3.0 | **方案 E 的直接先例**：ESP32 当 Telnet 客户端连 Grbl_Esp32 :23——连接管理、断线处理、`<Idle|…>` 状态解析。我们的 Pipe 已是自研等价实现 |
| [luc-github/ESP3D](https://github.com/luc-github/ESP3D) | GPL-3.0 | G-code 逐行流式发送的状态机（发一行等 ok） |
| [ucodia/plotter-studio-mcp](https://github.com/ucodia/plotter-studio-mcp) | AGPL-3.0 | ③ MCP server 工具面设计：`plot_start/plot_stop/plot_status` + HTTP 文件中转（与我们"G-code 存 URL 设备下载"同构）——照抄接口划分，代码全自研 |
| [nornagon/saxi](https://github.com/nornagon/saxi) | AGPL-3.0 | 路径优化/运动规划算法思路（本期不用） |
| [nulllaborg/xiaozhi-esp32-nulllab](https://github.com/nulllaborg/xiaozhi-esp32-nulllab) | MIT | 设备侧把"AI 意图"映射到硬件动作的工具定义方式 |

> 例外且必须正视：`Grbl_Esp32` 本身就是产品实际分发的 GPL-3.0 固件 fork，不是“只借思路”。小批量商业出货必须履行对应源码、许可证和修改说明义务，见 `reference-projects.md` / `compliance.md`。

## 调研判断

- **没找到现成的"小智控制写字机"完整方案**——hutuji 的集成形态有新意，差异化在双芯片分工。
- 备选转换器：[sameer/svg2gcode](https://github.com/sameer/svg2gcode)（Rust，MIT 未核实）、[vpype-gscrib](https://pypi.org/project/vpype-gscrib/)（更新）。优先 vpype-gcode（与 MCP 服务同 Python 生态）。
- 许可证红线：AGPL/GPL 参考项目不得未经评估复制进新增闭源模块；实际随产品分发的 Grbl/ESP3D 相关代码必须履行 GPL 义务。
- 参考项目的 2026-07-25 GitHub 元数据、许可证和使用边界已集中到 `reference-projects.md`。

---

# 外部参考索引（2026-07-25 补：接手 agent 的「该去哪查」总表）

> 本节收拢全项目散落的外部来源，按用途分组，每条标**验证状态**：
> ✅ 已核实（读过源码/文档或本地实测）｜🔬 待实机验证｜❓ 未核实（仅记录，用前须查）。
> 2026-07-25 已通过 GitHub API/官方文档核对核心仓库元数据；发布前仍须重新读取当时版本的 LICENSE。

## A. 上游服务与外部依赖（③ 运行时依赖，最关键）

| 依赖 | 端点/来源 | 关键约束 | 状态 |
|------|-----------|----------|------|
| **LLM API（FreeModel）** | `https://api.freemodel.dev/v1`（OpenAI 兼容）；控制台 `https://freemodel.dev/dashboard/keys` | **硬限流：每分钟 40 次 / 每秒 2 次 / 每天 100 万 token；单次请求上限 128k 字符。** ③ 全局限流必须设更低余量（见 `mcp-server/README.md` 组 3.4）。凭据在 `D:/Downloads/VPS.txt`（**禁入库**），env 变量 `HUTUJI_LLM_API_KEY` 注入 | 🔬 |
| **LLM 模型选择** | FreeModel 共 15 个免费模型，默认 `default`/`auto` | 官方建议**不要写死模型 ID**（避免官方调整策略时失效）。`HUTUJI_LLM_MODEL=auto` 让其自动路由 | ✅ 文档载明 |
| **备用 LLM provider** | `https://dash.llm7.io/#/api-keys`、魔搭社区（含 MCP 服务） | 负载均衡/故障切换的候选源（凭据在 `VPS.txt`）。多 provider 轮换是应对 FreeModel 限流的手段之一 | ❓ 未接入 |
| **小智官方云 MCP 接入点** | `wss://api.xiaozhi.me/mcp/?token=xxx`（控制台→智能体→MCP 接入点） | WebSocket 接入；本仓 `hutuji_mcp.cloud_bridge` + `mcp-proxy` 转本机 streamable HTTP。2026-07-25 本机实测 WSS 鉴权、`initialize`、`tools/list` 均通过；真实 `tools/call` 待阶段 6 | ✅ 工具发现实测 |
| **小智官方接法** | [飞书 MCP 接入点文档](https://my.feishu.cn/wiki/HiPEwZ37XiitnwktX13cEM5KnSb)、[关联示例 78/mcp-calculator](https://github.com/78/mcp-calculator) | 官方示例以 `MCP_ENDPOINT` 环境变量注入 WSS 接入点，`mcp_pipe.py` 做 ws↔stdio；本仓按相同协议自研项目专用桥接 | ✅ 已核对并实测 |
| **小智社区教程** | [CSDN 接入教程](https://blog.csdn.net/Extreme_wei/article/details/161058979)、[腾讯云 4步教程](https://cloud.tencent.com/developer/article/2645010) | 仅作辅助拓扑参考，非权威 | ❓ 教程质量未核 |

## B. 硬件/协议事实来源（决策依据，勿丢）

| 来源 | 支撑哪条决策 | 状态 |
|------|--------------|------|
| [WCH CH9350 手册](https://www.wch.cn/products/CH9350.html) | 决策 #2 否决 CH9350：仅枚举 HID 类，不认写字机 CH340（vendor-specific 串口）。见 `docs/hardware.md`/`protocol.md` | ✅ 手册载明 |
| [立创实战派 ESP32-S3 wiki](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/beginner/introduction.html) | 决策 #2 否决 S3 原生 USB Host：C 口经 CH334F HUB 占为设备口，GPIO19/20 无排针。见 `docs/hardware.md` | ✅ wiki 载明 |
| **Grbl_Esp32 源码**（本地 `D:/Users/Grbl_Esp32`，分支 `fix/panel-hold-after-align-116687e`） | protocol.md v0.5 **全部**换纸/流控/授权/状态事实的权威来源（`PaperSystem.cpp` 路由、`TelnetServer.cpp` 的 `MAX_TLNT_CLIENTS=1`、`Protocol.cpp` 阻塞期服务等）。§9 改动清单直接引其行号 | ✅ 逐处核对 |
| [Espressif CH34x VCP 组件](https://components.espressif.com/components/espressif/usb_host_ch34x_vcp) | 已否决 USB Host 方案的 API 查证留档（`firmware/m1-usb-pipe.md`） | ✅ 留档 |

## C. 代码复用来源验证状态（补注 §5/§13 上表）

上文「直接采用」「只借思路」两表的**许可证与可用性核对状态**：

- ✅ **vpype / vpype-gcode**：③ 已实际用于 `gcode_gen.py` 管线（`read→linesort→gwrite --profile hutuji`），MIT，可商用。
- ✅ **FluidDial**：方案 E 的直接先例，已确认为「Telnet 客户端连 Grbl:23」同构；GPL-3.0，**仅借思路、Pipe 自研**。
- ❓ **其余 GPL/AGPL 项目**（ESP3D / plotter-studio-mcp / saxi）：star 数、许可证细则**未逐一核实**；红线是一律不抄代码/不部署调用，集成前须核对 LICENSE。
- ❓ **备选转换器** svg2gcode（Rust）/ vpype-gscrib：未核实，当前不用（优先 vpype-gcode 同 Python 生态）。

## 待补（联网环境或实机可用时）

1. FreeModel 限流/模型清单以**实际控制台**为准复核（🔬）。
2. 小智云部署后的**真实 `tools/call` 与常驻重连稳定性**（🔬，阶段 6 联调）。
3. 京东云 VPS **上行带宽**实测值（5Mbps 通常指下行，S3 下载走 VPS 上行——见决策 #5）。
4. CRC32 变体两侧一致性首包核对（zlib/IEEE，见 `mcp-server/README.md` 组 0.2）。
