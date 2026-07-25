# 参考项目清单与使用边界

> GitHub 元数据核对日期：2026-07-25。star/更新时间只用于判断维护活跃度，不构成质量或安全保证；每次正式发布仍须重新读取 LICENSE 和依赖清单。

## A. 当前实际采用或直接依赖

| 项目 | 快照 | 许可证 | 本项目用途 | 商业出货要求 |
|------|------|--------|------------|--------------|
| [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) | 28k+ stars，活跃 | MIT | S3 固件上游，设备侧 MCP/语音/WiFi 基座 | 保留版权与 MIT 文本，记录 fork commit/上游基线 |
| [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | 23k+，活跃 | MIT | FastMCP 与 streamable HTTP | 锁版本、保留许可证、做协议回归 |
| [sparfenyuk/mcp-proxy](https://github.com/sparfenyuk/mcp-proxy) | 2.6k+，活跃 | MIT | 小智 WSS 桥接中的 stdio↔streamable HTTP | 锁发布版本，归档依赖许可证 |
| [abey79/vpype](https://github.com/abey79/vpype) | 900+，活跃 | MIT | SVG 读入、排序、路径处理 | 锁版本；用本项目校验器兜底，不能信任转换输出 |
| [plottertools/vpype-gcode](https://github.com/plottertools/vpype-gcode) | 50+，维护中 | MIT | `hutuji` gwrite profile 输出 G-code | 锁版本、保留 MIT 文本、回归 profile |
| [bdring/Grbl_Esp32](https://github.com/bdring/Grbl_Esp32) | 1.9k+，活跃 | GPL-3.0 | 写字机运动/换纸固件 fork 的上游 | **实际分发 GPL 固件：必须提供对应源码、许可证与修改说明；发布前法务复核** |
| [luc-github/ESP3D](https://github.com/luc-github/ESP3D) | 1.9k+，活跃 | GPL-3.0 | Grbl 固件中的 WebUI/Telnet 技术来源 | 核对是否随固件组合分发及对应源码义务 |

## B. 官方接入证据与可选替代

| 项目 | 许可证状态 | 可借鉴内容 | 当前决策 |
|------|------------|------------|----------|
| [78/mcp-calculator](https://github.com/78/mcp-calculator) | GitHub 未识别 LICENSE；README 声称 MIT | 小智官方 MCP 接入点的 `MCP_ENDPOINT`、WSS↔stdio 示例 | 只作协议证据；未补 LICENSE 前不复制源码 |
| [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) | MIT | 自建小智服务端、MCP/设备管理思路 | v0 不用；官方云失效后才评估，避免扩大运维面 |
| [sameer/svg2gcode](https://github.com/sameer/svg2gcode) | MIT | Rust SVG→G-code 备选 | 当前不用；vpype 管线失败或性能不足时做 A/B 契约测试 |
| [nulllaborg/xiaozhi-esp32-nulllab](https://github.com/nulllaborg/xiaozhi-esp32-nulllab) | MIT | 设备 MCP 工具映射硬件动作 | 低优先级参考，不能覆盖官方 fork 约定 |

## C. 强 copyleft：仅借架构思路，禁止复制进本仓闭源部分

| 项目 | 许可证 | 可借鉴内容 | 红线 |
|------|--------|------------|------|
| [bdring/FluidDial](https://github.com/bdring/FluidDial) | GPL-3.0 | ESP32 作为 Telnet 客户端连运动控制器 | Pipe 保持自研；复制前必须评估 GPL 传播范围 |
| [bdring/FluidNC](https://github.com/bdring/FluidNC) | LICENSE 正文为 GPL-3.0-or-later（GitHub 元数据 NOASSERTION） | 新一代 ESP32 运动控制、状态/网络架构 | v0 不迁移，避免破坏成熟换纸固件 |
| [ucodia/plotter-studio-mcp](https://github.com/ucodia/plotter-studio-mcp) | AGPL-3.0 | plot/status/stop 工具面、文件中转 | 只借接口思路；不复制、不部署为本服务组成部分 |
| [nornagon/saxi](https://github.com/nornagon/saxi) | AGPL-3.0 | AxiDraw 路径优化/驱动 | v0 不用；不复制算法实现 |
| [joansalasoler/vpype-gscrib](https://github.com/joansalasoler/vpype-gscrib) | GPL-3.0 | vpype 的另一种 G-code 输出 | 不作为 MIT 管线的无审查替换项 |

## D. 已否决的硬件/链路参考

| 来源 | 结论 |
|------|------|
| [WCH CH9350](https://www.wch.cn/products/CH9350.html) | 仅 HID，不枚举写字机 CH340；方案 D 否决 |
| [Espressif usb_host_ch34x_vcp](https://components.espressif.com/components/espressif/usb_host_ch34x_vcp) | API 可用，但实战派原生 USB 被 CH334F HUB 占作设备口；正式链路否决 |
| 蓝牙 SPP/拆机 TTL | BT 是商业现役先例但非新链路；TTL 仅 WiFi 不达标时的备用 B |

## 选择规则

1. 先看许可证，再看技术适配；star 不能替代 LICENSE。
2. MIT/Apache/BSD 仍需保留版权与许可证，并进入第三方通知清单。
3. GPL/AGPL 不是“不能商用”，但有源码/网络交互等义务；未完成合规评估前不得混入新闭源模块。
4. Grbl_Esp32 是既成 GPL 分发事实，不适用“只借思路”的简化口径。
5. 替换转换器、MCP SDK、桥接器或固件基座时，必须先用 `protocol.md` 和 `bringup-sop.md` 做等价性验证。

## 复核命令

发布前记录以下结果（不得记录 GitHub token）：

```powershell
gh api repos/<owner>/<repo> --jq '[.full_name,.html_url,.license.spdx_id,.archived,.updated_at] | @tsv'
gh api repos/<owner>/<repo>/license --jq '[.license.spdx_id,.html_url] | @tsv'
```

Python 运行依赖另按 `compliance.md` 生成 SBOM/第三方通知；本表不能替代完整依赖许可证扫描。

