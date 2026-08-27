# 奎享 Stepper 笔控对齐实施记录

> 状态：**profile 已实施（2026-07-27）**。本文记录最终落地结果，不是待实施清单。
> 早期依据默认配置与校准生成器形成的草案参数已被缓存文件核对和实机运动验证推翻；
> 当前真值以 `docs/protocol.md` §5 与 `mcp-server/hutuji_mcp/hutuji_gwrite.toml` 为准。

## 已落地范围

- [x] 生产 G-code 改为 Stepper 纯 Z 笔控，不再使用 `M3/M5`。
- [x] 文件开头保留两次 `G1G90 Z0.0F10000`，作为缓存兼容的幂等抬笔运动；不把它们当作坐标校准。
- [x] S3 代码已对齐：完整会话每次首画/重画前等 fresh `Idle` + 100ms，再旁路固定 `G92 Z0`；下载文件仍禁 `G92`。
      该编排已由 fork `13057e8` 提交/推送（`main/boards/lichuang-dev/hutuji_job.{h,cc}`、`hutuji_pipe.cc` 的
      `PreparePenOrigin`）；上一版 `15f73c9` 的 `hutuji_job.cc` 内不含任何 `G92`。含该编排的镜像
      （`xiaozhi.bin=0x2ce890`，内容对应 `13057e8`）已构建并于 2026-07-29 20:41 app-flash 到 COM6，通过 esptool 自校验
      （`Hash of data verified`，证据 `results/s3-13057e8-com6-flash-evidence-20260729.md`）；落笔迄今只在**手动 Telnet 序列**
      （`G92 Z0` → `G1 Z5.0 F10000`；2026-07-30 追加：同序列接 `G91` + `G1 X10.0 F800` 也在纸面出墨）下纸面证实
      （见 `docs/troubleshooting.md:94`）；画线 `F8000` 与 S3 自动路径出墨已于 2026-07-30 B-12 归档下证。
- [x] 画线与空走使用 `F8000`，Z 抬落笔使用 `F10000`，每个运动行显式带 F。
- [x] 文件内无 `G4`、`M2/M30`、`M7xx`，且不以 `G0 X0Y0` 回原点。
- [x] 文件结束只抬笔；S3 在绘图 fresh `Idle` 后依次旁路归位行 `G1G90 X0Y0F8000`（2026-08-14 起）与 `M30` 作为任务编排命令单独发送。
- [ ] `Changing=On` 分流、换纸中 abort、App 语音与生产签核仍待实机验收；profile 落地不等于可出货。

## 当前生产模板

```toml
document_start = "G21\nG90\nF8000\nG1G90 Z0.0F10000\nG1G90 Z0.0F10000\n"
segment_first = "G0 X{x:.3f}Y{y:.3f}F8000\nG1G90 Z5.0F10000\n"
segment = "G1 X{x:.3f}Y{y:.3f}F8000\n"
line_end = "G1G90 Z0.0F10000\n"
document_end = "G1G90 Z0.0F10000\n"
```

与奎享缓存内容的**逐行模板**有意差异只有收尾：奎享普通 Grbl 输出会追加 `G90G0 X0Y0`，
hutuji 必须省略，因为当前 Grbl fork 会把执行到工件 XY≈0 的 `G0` 映射为换纸触发。

> ⚠️ **但「只有收尾」仅限模板字面，坐标系是第二处、且是更大的一处偏差**（2026-08-06 补记）。
> 用真实缓存产物复核发现：奎享工作在**负 X 坐标系**——`2.gcode` 的包络是
> X ∈ [-8.126, 0.000]、Y ∈ [0.000, 117.670]，763 个 X 值里 762 个为负，Y 无一为负。
> hutuji 校验器则**硬拒负坐标**（`gcode_gen.py` 中 `val < -_EPS` 即 `GcodeError`）。
> 把该产物直接喂 `validate_gcode()` 的实测结果：
>
> | 输入 | 结果 |
> |------|------|
> | 原样 | 拒绝：第 946 行（文件末尾）必须是 `G1G90 Z0.0F10000`（实际 `G90G0 X0Y0`） |
> | 去收尾 | 拒绝：第 6 行 `X-2.205` 越界（限幅 0~190.0mm） |
> | 去尾 + 平移 +20mm | 通过，`lines=945 bytes=21729 pen_lifts=91` |
>
> 这**不是缺陷**：本机无限位开关，硬拒负坐标是唯一安全防线上的正确 fail-safe
> （派曦是软警告缩放，我们是硬限，见 `findings/18` §3）。记录在此是因为
> 「有意差异只有收尾」这句话经不起用真实产物复核，容易让后来者误以为可以
> 直接拿奎享产物做回归基线。**不得为「对齐奎享」放宽负坐标限幅。**
>
> 附带一条待定项：该产物 `pen_lifts=91`，超 `PEN_LIFT_WARN_THRESHOLD = 50` 近一倍，
> 而它只是 8×118mm 的窄条。阈值 50 的来源无依据记录，机头实测后再锁定（README 组 1.3）。

更完整的**有意不对齐清单**（断线恢复、合批写、收尾、中止、笔控数值五项）见
`docs/kxnx/findings/17-reconnect-resend-hazard.md` §5；坐标系一项即上述补记，
该 §5 表格成文时未收录。

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
