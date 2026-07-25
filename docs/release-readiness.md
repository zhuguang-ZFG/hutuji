# 发布与出货就绪度

> 状态日期：2026-07-25。本文是“现在到底做到哪”的唯一状态页；每次通过新门禁后同步更新。

## 结论

**当前不可商业出货。** MCP 本地基础与小智云工具发现已通过；Grbl §9 小改、S3 M2、真实硬件联调、VPS 常驻、法规/客户资料签核均未闭合。

## 证据矩阵

| 层 | 当前状态 | 已有证据 | 出货前缺口 |
|----|----------|----------|------------|
| 架构/协议 | ✅ 已定案 | `README.md` 决策表；`protocol.md` v0.5 | 实测数字回填后评审 v0.6/或保留 v0.5 实测附录 |
| 基础文档地图 | ✅ 已齐 | `docs/README.md` + `agent-handoff.md` + `docs/templates/` + `deploy/` | 按阶段回填 🔬 与签核，不把文档当证据 |
| MCP 本地服务 | ✅ 自动化通过 | pytest **23** 项；`e2e_local.py` 15/15 行 PASS | 在发布 commit 上重跑并归档输出 |
| MCP 安全基础 | ✅ 代码/测试通过 | CRC32、512KiB、SVG 白名单、TTL、限流、localhost；stderr/status 脱敏 | 京东云 nginx/TLS/防火墙实机验证 |
| 小智云桥接 | 🟡 工具发现通过 | WSS 鉴权、`initialize`、`tools/list` PASS | 实际 `hutuji_status`/`hutuji_draw`、长期重连 |
| Grbl §9 加固 | 🔴 待实施 | `protocol.md` §9 有评审清单 | 修改、`agent_gate.py overall=pass`、烧录、实机验证 |
| S3 M1 | 🟡 有未提交补丁 | `firmware/m1-usb-pipe.md` 代码评审记录 | 在 fork 中确认 diff、构建、提交、烧录 |
| S3 M2 哑管道 | 🔴 未实现 | v0.5 设计与待办完整 | 下载/校验/授权/流控/恢复/abort 全实现并测试 |
| 写字机实机 | 🔴 零本批次证据 | 仅成熟现役固件事实与源码推导 | 阶段 0–2、7–8 真实记录 |
| 京东云生产环境 | 🔴 未部署 | `deployment.md` + `deploy/` 模板已齐 | systemd/nginx/TLS/密钥/监控/回滚演练 |
| 生产/合规 | 🔴 未签核 | SOP + `docs/templates/` + `compliance.md` | BOM、标签、法规、开源通知、保修/售后负责人签核 |

## 当前最近一步

1. 在 `D:/Users/Grbl_Esp32` 实施 `protocol.md` §9 A/B1′/B2′，跑强制门禁。
2. 在 `D:/Users/xiaozhi-esp32` 完成 M2，并用规定 ESP-IDF 版本构建。
3. 按 `bringup-sop.md` 自阶段 0 起留真实证据，禁止跳阶段。

## 状态更新规则

- ✅：命令、commit、设备编号、日志或记录表可复核。
- 🟡：设计/代码存在但缺生产环境或硬件证据。
- 🔴：未实施、未验证或存在硬阻断。
- 状态提升必须在 `docs/templates/release-manifest.md` 或 `docs/templates/device-test-record.md` 有证据路径。
- 失败记录不得删除；修复后追加复测结果和关联 commit。

