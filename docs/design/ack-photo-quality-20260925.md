# ACK 延迟与照片画质继续修复（2026-09-25）

用户目标：解决 ACK 延迟的底层原因，并改善照片生图质量。照片验收重点由用户确认是“线条杂乱、阴影太黑或细节太碎”。工程侧已收口：ACK 对照判决完成、量产机刷回 1.0.27 并复验、照片滞回候选待用户目视验收。

## 起点与验收

- 上轮量产 S3 1.0.27、Grbl massprod/nopaper-1.3a，512B 加 8 条预算已有实机改善。2026-09-25 本轮只读查询确认仍为该版本、nopaper、指纹通过、原点 Idle，XY6000/Z1200。保留该有效流控，诊断对照镜像不得冒充最终产品。
- ACK：必须区分驱动收包、TCP 栈处理、发送输出及应用应答。采样只保留 Telnet TCP 头部数值与相对时间，不保留业务载荷；不修改 SDK、机械参数或换纸逻辑。当前缺少抓包证据，不能把低 RX 缓冲、无线丢包或 delayed ACK 当成已确认根因。
- 照片：在现有本地程序化链上复核输入灰度、结构线与排线密度。使用既有真实照片和公开来源样张，固定旧提交作对照；比较最终 SVG/G-code 回放、局部暗区墨密度、碎路径/抬笔、大小及耗时，检查人物结构保留。仅降低全图墨覆盖不构成画质验收。
- 修复必须过相关测试和真实转换/生产验证；固件更新须量产构建及板上复验。已有 fz 启动失败不作为通过。照片保留本机，不上传给新增第三方。

## 当前工作树与范围

hub 起点 46c9d53，原用户 README.md/CLAUDE.md 状态及 deployment 前部四行保持。S3 和量产 Grbl 已跟踪文件无未提交差异；fz 的已有改动不覆盖。量产 Grbl worktree 没有 AGENTS.md，已读取主 Grbl 工作树的同仓规则并以用户量产分支要求为准。

本轮允许实现路径登记在 agent-edit-allowlist.json 的 ack_photo_quality_20260925_authorized；依据是用户本轮完整目标。临时诊断代码先放 tmp，精确核对源码后再应用；最终须移除临时探针或作为默认关闭的受测诊断功能交付。

## 2026-09-25 头部对照与画质候选

固定轨迹为 727 行、18812B、Z0、60mm 包线，XY6000/Z1200。1.0.28-diag 保留正式 8 条预算，3559 个头部记录，D→I 最大 1.801ms，输出到累计 ACK 最大 63.563ms，重复输出序列区间 0。1.0.29-diag 仅改 20 条预算，4005 个头部记录，D→I 最大 2.787ms，累计 ACK 最大 2500.544ms，重复输出区间 154。两个案例为 results/20260925-binding-memory-motion/passive-6000-024420 和 passive-6000-025430，含原始串口及 ack-headers.json/ack-analysis.json。

D 为 esp_netif_receive 驱动交付，I 为 tcp_input，O 为 ip4_output_if 输出尝试，E 为失败返回。两轮无 E、无采样溢出，D 均匹配 I。已观察序列缺口、重复 ACK 和重传；缺包在 D 之前，尚不能区分驱动、射频、AP 或对端。O 不证明实际无线发送。现有证据不支持把秒级停滞归因于 S3 TCP 线程调度。两轮归位 Idle，Grbl 本轮未刷写或复位。

诊断镜像 1.0.28-diag SHA 7e612cb25aefa855a4953f00db28a963a72118a1162e69524e285aef9d6b9e8a，1.0.29-diag SHA b1b4a099584fa9d246415d2cbd1da18fcd67ef72c0b71fb1f7868e94cb6ffa05，均真实编译 215 CXX 并核 MAC 后刷 COM15。1.0.30-diag 仅加动态 RX 6→16（静态 RX3、BA3、20 条保持）和收尾堆内存日志，SHA e2d366010cd34d5e7d2e3aa2d90466e8726af61b171a701831c9c4249658d592，3079232B，真实编译 215 CXX、刷写哈希与运行版本通过；因果对照与判决见文末「凌晨续作」节（探针已全部清理、量产机刷回 1.0.27 复验）。

照片新增 9 项回归在 46c9d53 基线均失败，候选 34 项全过；Portal 照片链 13 项通过。已修复输入按图内最大值归一化、RGB 等权灰度和忽略 alpha 的问题。排线两组嵌套格点避免同向线局部重合，竖图按长边换算密度，增加高度上限。保留色调阈值、几何容差与结构线算法，后者仍需继续复核。

用户 720×1280 原图复现旧 129×220 设备预览；旧图差异为 JPEG 量化级，不能用此作为画质评分。最终抬笔 1533→926，G-code 186829→103364B，均过 §5。候选对比页位于 results/local-only/2026-09-25/ack-photo/candidate/index.html。用户最新反馈：“第三张略微好点，但是还是有进步空间”；不得写为最终验收。眼周、下巴的黑块变少，但五官轮廓和碎边仍待改善。

另 5 张既有照片 × 两种风格 × 两版本，共 20 份 G-code 产物均过 §5，指标在 regression/metrics.json。外层 PowerShell 把 stderr 软抬笔警告记作 NativeCommandError，命令 exit1 如实保留；不冒充测试全绿，后续使用 subprocess 记录真实退出码。hatch 抬笔分别 2305→1416、1640→1042、953→855、588→522、697→608。减少负载不等于审美验收。

## 外部依据与适用边界

- Espressif 官方 ESP-IDF 源码快照 8c19b156084a0753687347cca1f5355782893533：<https://github.com/espressif/esp-idf/blob/8c19b156084a0753687347cca1f5355782893533/docs/en/api-guides/wifi-driver/overview.rst>，访问 2026-09-25；静态 RX、动态 RX 生命周期及 AMPDU 建议作为诊断依据。Freenove ESP32-S3 的实际配置和头部实验才是本地证据，不能由建议直接断定故障。原文与清单见 results/20260925-binding-memory-motion/references/manifest.json。
- Praun/Hoppe/Webb/Finkelstein，Real-Time Hatching，SIGGRAPH 2001：<https://hhoppe.com/hatching.pdf>，访问 2026-09-25，SHA 0e43ea2695d00cbf3dfd0abf302a9564fe895fe7234eec9148710e79c5145800。已用 pypdf 读取原文；只借鉴笔画嵌套及尺度一致性，3D 纹理论文不直接证明写字机纸面画质。
- scikit-image 0.26.x 官方 color 文档：<https://scikit-image.org/docs/0.26.x/api/skimage.color.html>，访问 2026-09-25，rgb2gray 感知权重与 rgba2rgb 白底合成。已读下载原文并以本机回归复核；原文 SHA 在 results/local-only/2026-09-25/ack-photo/reference-manifest.json。

## 2026-09-25 凌晨续作：对照判决、卡死恢复与滞回落地

### 设备两次卡死与恢复（COM7 串口路径，已两次实证）

- codex 停止时量产机卡 `state=error` + `grbl_state=Hold`（MPos 39.950,28.950），云端 status 的 `plotter_sku=paper`、`authorized=false`、`grbl_settings_ok=false` 不是真 SKU/配置变化：Hold 态 Grbl 用 error:8 拒 `$I`，VER 未达时 `plotter_sku` 按设计回落 `paper` 保守档，授权与指纹核对同样停在断链前状态。
- Hold 态没有任何云端工具可达复位：`hutuji.abort` 在非 busy 时是 no-op；`hutuji.resume` 需要 paused+busy；`hutuji.manual` 的 reset/set_origin/unlock/motor_off 维护动作云端与语音一律拒（仅屏幕入口）；固件在该终态的播报建议是「请断电重启」。
- 替代路径（本机 COM7=写字机 CH340）：打开串口时 DTR/RTS 恒 False，发 `?` 确认 `<Hold:0|MPos:...>` 后发 `0x18` 软复位——Grbl 软复位**保留机器坐标**（两次实证 39.950,28.950 与 32.080,39.780 复位后原值），S3 经 Telnet 收到 banner 后自动走 WaitingBuildInfoOk→`$I`→VER→授权探测→指纹核对全链恢复；随后 `hutuji.manual home` 归位（settled 态含 error 可用），任务层 error 锁存随新动作自清。脚本 `tmp/recover_grbl_reset_0925.py`、`tmp/recover_home_0925.py`，证据 `results/20260925-binding-memory-motion/grbl-reset-040621.log`、`grbl-reset-052206.log`、`rx16-recovery-home.log`。
- 第二次卡死（05:22，MPos 32.080,39.780）由 1.0.31-diag 对照轮触发，同一路径恢复。

### ACK 因果对照判决（同一 727 行/18812B 抬笔轨迹、XY6000/Z1200、60mm 包线）

| 轮次 | 固件 | 配置 | 绘图窗重传 | dup-ACK | O→累计ACK p50/p90/max | 结果 |
|---|---|---|---|---|---|---|
| passive-6000-024420 | 1.0.28-diag | 动态RX6+8条 | 0 | 6 | 10.6/15.8/63.5ms | 干净 |
| passive-6000-025430 | 1.0.29-diag | 动态RX6+20条 | 154 | — | 14.6/1295.7/2500.5ms | 停滞（20 条复现事故形态） |
| passive-6000-040758 | 1.0.30-diag | 动态RX16+20条 | 169 | 241 | 16.6/1256/1511ms | 链路劣化期停滞 |
| passive-6000-041539 | 1.0.30-diag | 同上 | 195 | — | max 2650ms | 同上 |
| passive-6000-043645 | 1.0.31-diag | 动态RX6+20条 | — | — | — | Grbl 主动 RST（errno=104）→卡 Hold |
| passive-6000-052227 | 1.0.31-diag | 动态RX6+20条 | 194 | 318 | 27.9/1294/1515ms | 链路劣化期停滞 |

判定（每条都有头部证据）：
1. **在途行数预算对照（同夜最干净的 A/B）**：02:44 的 8 条轮（1.0.28-diag，RX6）0 重传、ACK 最大 63.5ms；10 分钟后 02:54 的 20 条轮（1.0.29-diag，同 RX6、同轨迹）154 重传、最大 2500.5ms——20 条复现秒级停滞，与 09-24 量产根因（26B 短包填满 lwIP 段队列）两条独立证据互证；正式 8 条预算维持定案，诊断探针曾把 1.0.29–1.0.31 全部带成 20 条（hutuji_recovery_core.h 未回退所致），本表 1.0.30/1.0.31 各轮均为此口径。**动态 RX 6→16 无任何可测效果**（20 条口径下 169/195 vs 194 重传），不投产，已回退。另注：今晚两次 Grbl 主动 RST 全部发生在 20 条固件上（样本小，不足定性，只作保留 8 条的旁证）。
2. 停滞形态 = S3→Grbl 方向帧丢失触发的 lwIP RTO 簇（≈1.5s），Grbl 通告接收窗口全程 ≥3.9KB（非零窗口背压），S3 栈内 D→I ≤2.8ms、D==I 无内丢失、输出零失败——**链路劣化是唯一未被排除的变量**；两次 Grbl 主动 RST（errno=104）伴随重连后 6 秒内二度断链，abort reset 在断链窗口无法完成 → 卡 Hold。
3. 劣化随时间加重（02:44 干净 → 03:10 起反复断链 → 05:36 每次查询即断），且 Grbl UART 全程静默（无崩溃/无 ALARM）。**Grbl 主板 DTR 硬复位（物理在原点时执行，坐标 believed==physical 零损失）后链路立即恢复**：05:39 硬重启 → 05:40 量产 1.0.27 冒烟 passive-6000 全程零断链、done 归位（`grbl-hardreset-053941.log`、passive-6000-054033）。这指向 Grbl 侧 WiFi/Telnet 栈随运行时长与重连次数退化；为何退化（射频/AP/板卡状态）S3 头部证据无法区分，留作观察项——若再按小时级复发，需 Grbl 侧 WiFi 事件取证或定期重启策略，超出本轮范围。
4. 缓冲分析必须按 esp_timer uptime 切窗：1.0.30/1.0.31 的 dump 缓冲覆盖整轮启动至今的会话，聚合统计会把失败窗混入干净窗（030x 三轮因中途断链未触发 dump，无任何头部）。

### 照片结构线滞回双阈（落地）

- `_structure_contours` 在零交叉段上改 Canny 式滞回：弱阈 `_STRUCTURE_WEAK_CONTRAST=0.008` 起段、段内含强阈 0.014 锚点才保留；孤立弱段与全程弱梯度的缓慢光照等高线照旧被拒。三档裁切 A/B（0.006/0.008/0.010，`experiments/hysteresis-crops.png`）：0.006 前额碎波明显，0.010 五官续接变弱，0.008 保眉眼/下颌线且前额噪声回落；与 `tmp/photo_structure_candidate_0925.py` 原型（0.008 门+0.022 锚）独立收敛。
- 回归：桥接测试（连续弱段不得断，旧单阈 47px 断裂变红/新 >100px 连续变绿）+ 孤立弱边拒绝测试；`test_sketch_render.py` 36/36；MCP 全量 1598/1599（唯一失败为计数台账测试，台账同步后复核）；portal 照片链 13/13。
- 六样本（5 张既有 + 用户图）× hatch/squiggle × 新旧两版共 24 份 G-code 全过 §5（`experiments/hysteresis-final/metrics.json`）；用户图 hatch 947 线/941 抬笔/117702B（基线 1562 线/1533 抬笔/186829B），三向裁切对比 `experiments/final-crops.png`。**用户目视验收未做，不得宣称画质定案。**

### 固件收口

- xiaozhi 工作树探针（CMakeLists 版本/链接包装、hutuji_pipe.cc 109 行、hutuji_recovery_core.h 8→20、freenove config.json 动态RX16）全部回退 HEAD；探针源与各版镜像/构建清单留证 `tmp/ack-trace-stage/`、`results/20260924-review-fixes/s3-builds/hutuji.1.0.{28,29,30,31}-diag/`。
- 量产 1.0.27 重建真编译 215 CXX、bin 3077712B SHA `f24938ce…`，刷 COM15 核 MAC 后板上复核：`hutuji.1.0.27`/nopaper/指纹通过/ready/Idle 原点/速度 XY6000 Z1200；冒烟 passive-6000 done 归位零断链。诊断镜像 1.0.28–1.0.31-diag 均不得再刷作交付。
