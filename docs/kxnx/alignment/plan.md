# 奎享 Stepper 笔控对齐实施记录

> 状态：**profile 已实施（2026-07-27）**。本文记录最终落地结果，不是待实施清单。
> 早期依据默认配置与校准生成器形成的草案参数已被缓存文件核对和实机运动验证推翻；
> 当前真值以 `docs/protocol.md` §5 与 `mcp-server/hutuji_mcp/hutuji_gwrite.toml` 为准。

## 已落地范围

- [x] 生产 G-code 改为 Stepper 纯 Z 笔控，不再使用 `M3/M5`。
- [x] 文件开头保留两次 `G1G90 Z0.0F10000`，作为缓存兼容的幂等抬笔运动；不把它们当作坐标校准。
- [ ] S3 代码对齐中：完整会话每次首画/重画前等 fresh `Idle` + 100ms，再旁路固定 `G92 Z0`；下载文件仍禁 `G92`。
      该编排已由 fork `13057e8` 提交/推送（`main/boards/lichuang-dev/hutuji_job.{h,cc}`、`hutuji_pipe.cc` 的
      `PreparePenOrigin`）；上一版 `15f73c9` 的 `hutuji_job.cc` 内不含任何 `G92`。含该编排的镜像
      （`xiaozhi.bin=0x2ce890`，内容对应 `13057e8`）已构建并 app-flash 到 COM6，但烧录日志未归档；落笔迄今只在**手动 Telnet 序列**
      （`G92 Z0` → `G1 Z5.0 F10000`）下纸面证实（见 `docs/troubleshooting.md:26`），S3 自动路径出墨仍无归档证据。
- [x] 画线与空走使用 `F8000`，Z 抬落笔使用 `F10000`，每个运动行显式带 F。
- [x] 文件内无 `G4`、`M2/M30`、`M7xx`，且不以 `G0 X0Y0` 回原点。
- [x] 文件结束只抬笔；S3 在绘图 fresh `Idle` 后将 `M30` 作为任务编排命令单独发送。
- [ ] 纸面笔迹/出墨、有纸自动换纸、`Changing=On` 与生产签核仍待实机验收；profile 落地不等于可出货。

## 当前生产模板

```toml
document_start = "G21\nG90\nF8000\nG1G90 Z0.0F10000\nG1G90 Z0.0F10000\n"
segment_first = "G0 X{x:.3f}Y{y:.3f}F8000\nG1G90 Z5.0F10000\n"
segment = "G1 X{x:.3f}Y{y:.3f}F8000\n"
line_end = "G1G90 Z0.0F10000\n"
document_end = "G1G90 Z0.0F10000\n"
```

与奎享缓存内容的有意差异只有收尾：奎享普通 Grbl 输出会追加 `G90G0 X0Y0`，
hutuji 必须省略，因为当前 Grbl fork 会把执行到工件 XY≈0 的 `G0` 映射为换纸触发。

更完整的**有意不对齐清单**（断线恢复、合批写、收尾、中止、笔控数值五项）见
`docs/kxnx/findings/17-reconnect-resend-hazard.md` §5。

## 证据边界

- `C:\Program Files (x86)\kdraw(x64)\cache\2..10.gcode` 是 9 个缓存文件，但 SHA256
  均为 `E9F5ACAC45DEECD629EDC09758DD26D95B93DCD5D61EA1B74958FE624E325B61`，因此只能算
  **1 个唯一 G-code 产物的 9 个副本**，不能表述为 9 份独立样本。
- `docs/kxnx/findings/02-gcode-template.md` 记录模板比对；
  `docs/kxnx/findings/12-summary-and-action-list.md` 记录逆向收口。
- 2026-07-27 COM3 直连只证明 Z=5/Z=0 物理运动与逐行应答；纸面出墨和生产闭环仍以
  `docs/release-readiness.md` 的未完成项为准。

## 验证与回退

文档或 profile 变更后运行：

```powershell
python scripts/agent_gate.py --profile hub
git diff --check
```

本方案不改 Grbl 源码，因此单独修改 profile 不触发 fz 门禁；若同时修改
`D:/Users/Grbl_Esp32` 的任何代码，仍必须另跑 fz `standard` 门禁。
