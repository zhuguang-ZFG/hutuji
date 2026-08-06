# 17 · 奎享的断线重连重发缺陷，与我方「不对齐」决定

> 结论先行：**奎享的断线恢复是有缺陷的，我方不要对齐。** 它在重连后只把最后一条在途命令挪到待发队列**尾部**，这会丢掉更早的在途行、把救回的那行排到后续行之后（真正的行序错乱），并永久缩小有效窗口。我方 S3 的做法（断连后从第 1 行整体重画）在正确性上严格更优，保持不变。
>
> 本文引用的反编译源码均在**主树** `D:/GitHub/kxnx/kx/tools/dump_agent/decompiled_java/`（混淆名以此树为准，副树字母有位移，见 `docs/kxnx/README.md`）。

## 1 奎享的重连恢复路径

调用链只有一条：

| 位置 | 行为 |
|------|------|
| `machine/f/f.java:152-155` | 收到 `FirmwareEventReconnected` → 调 `this.c.d()`（`c` 是 CommandSender） |
| `machine/f/b.java:83-88` | `d()` = `d.pollLast()` 取出**最后一条在途命令**，再 `a(a2)` 塞回**待发队列尾部**；`e`（在途字节数）**不减** |

对比同文件里的另外两条清理路径，可见 `d()` 是刻意的「保守恢复」而非清空：

- `machine/f/b.java:149-153` `FirmwareEventResend` → `c.clear()` + `d.clear()` + `e = 0`
- `FirmwareEventError` → `this.f()`，同样是两队列清空 + `e` 归零

## 2 三条后果（读码推断，未在奎享上实测）

1. **更早的在途行被静默丢弃**。窗口 100 字节（`machine/grbl/a.java:315-317` `g()` 返回 `100`）下在途通常是 4–5 行，`pollLast()` 只救回 1 行，其余直接消失，且不报错。
2. **行序错乱**。被救回的那行进的是待发队列**尾部**，会在它后面的若干行**之后**才执行。对写字机就是「一段线画到别处去」。
3. **有效窗口永久缩小**。`d()` 不回退 `e`，而泵条件是 `e + len + 1 < b.g()`（`machine/f/b.java:70`）。每次重连都让 `e` 虚高一截，直到窗口卡死不再泵出新行。

这三条正好解释了「同一份文件两次不同轨迹」这一类故障的**成因模板**——但请注意：我方 run1 的行序错乱**不是**这条路径，见第 4 节。

## 3 缓冲层对照：奎享自动扩容，我方固定 2048

| 层 | 奎享 | 我方（Grbl_Esp32 fork） |
|----|------|------------------------|
| 传输暂存 | `a/b/b.java:18-21` 包装 Apache MINA `IoBuffer`，`setAutoExpand(true)` → **自动扩容，不会溢出、不会静默丢字节** | `WebUI/InputBuffer.h:55` `RXBUFFERSIZE = 2048` 固定；`InputBuffer.cpp:48-63` 满则 `return 0` |
| 写入返回值 | Java 侧无「写不进」这一状态 | `Serial.cpp:186` `client_buffer[client].write(data)` **返回值被忽略**，满时丢字节且无日志 |
| 并发保护 | 单读线程 + `LinkedBlockingDeque` | `InputBuffer` 自身无锁，但 `Serial.cpp:185-187`（写）与 `:232-234`（读）都在 `vTaskEnterCritical(&myMutex)` 内 —— **已核实，不是竞态点** |

另两个观察（尚未确认是否有实际影响）：

- `InputBuffer::flush()`（`InputBuffer.cpp:113-116`）是**空实现**，清缓冲只能靠 `client_reset_read_buffer()`（`Serial.cpp:222-228`），而后者**没有进临界区**。
- `getClientChar()` 只在 CLIENT_SERIAL 分支（`Serial.cpp:134`/`:136`）查了 `availableforwrite()`，telnet / BT / Serial2Socket 分支没查。奎享窗口 100 时在途 4–5 行，几乎撞不到 2048 上限；我方窗口 512 字节，余量小一个量级。

## 4 我方 error:8 重发：设计上已避开奎享的坑

我方也有「行被丢弃需重发」的路径，但**限定在窗口=1 的逐行模式**，因此重发位置可定位、不会错序：

| 位置 | 行为 |
|------|------|
| `hutuji_pipe.cc:606-617` | `error:8` 单独归类为 `WaitResult::Deferred`，其余 error 归 `Failed` |
| `hutuji_job.cc:1434-1445` | 换纸行逐行分支：`Deferred` → 等 2s 原地重发同一行，上限 `kDeferredMaxRetries = 8`（`:53`） |
| `hutuji_job.cc:1574-1579` | 窗口化普通行分支：`Deferred` **按失败处理并 fail closed**，注释写明「窗口化不应发生」 |

**由此证伪一条假设**（我今晚提出后自行推翻，记在此处防止重走）：「run1 的 A/B 对调 = error:8 触发重发把 A 排到 B 之后」。窗口化分支收到 `error:8` 会直接终止任务，而 run1 是完整走完的，且日志无 error。已同步记入 `docs/troubleshooting.md` 的证伪表。

## 5 明确不对齐清单

| 项 | 奎享 | 我方 | 取舍理由 |
|----|------|------|----------|
| 断线恢复 | `pollLast()` 挪到队尾（缺陷） | 断连后从第 1 行整体重画 | 正确性优先；重画代价是废一张纸，错序代价是废一张纸**且不自知** |
| 合批写 | 一个窗口多行拼进 TX 暂存，`machine/f/b.java:73` 泵完只 flush 一次 → 一次 socket write | 每行一次 `send()` | 本机固件实测多行同包会少应答、窗口计数永久漂移（`hutuji_job.cc:1473-1474`） |
| 出图收尾 | `machine/grbl/a.java:298-301` `b()` = `G90G0 X0Y0` 回原点（字面量在 `:300`） | 文件只抬笔，S3 在 fresh `Idle` 后单发 `M30` | 本机固件把 `G0 X0Y0` 判为换纸触发，见 `docs/protocol.md` §5 |
| stop/reset | 普通 stop 队尾追加抬笔+回原点；独立 reset2 才由 `machine/grbl/a.java:365-373` 发 `0x18` | 发 `!` 后按 Hold/Idle/任务状态处理 | 奎享 stop 非实时，reset2 又无安全 Hold；均不对齐（findings/20） |
| 笔控落点 | `GrblSettings.java` 默认 `zOn=2.0 / zOff=0.0 / zSpeed=3000` | 实测 `zOn=5.0 / zOff=0.0 / F10000` | **结构对齐、数值按本机实测**；奎享默认值是出厂缺省，不是本机可用值 |
| **坐标系**（2026-08-06 补） | **负 X 工件坐标系**：唯一缓存产物 `2.gcode` 包络 X ∈ [-8.126, 0.000]、Y ∈ [0.000, 117.670]，763 个 X 值里 **762 个为负** | **硬拒负坐标**（`gcode_gen.py` 中 `val < -_EPS` 即 `GcodeError`），限幅 0~190 / 0~277 | 本机**无限位开关**，MCP 端限幅是唯一安全防线，负向越界会直接撞机；派曦是软警告缩放、我们是硬限（`findings/18` §3）。**不得为「对齐奎享」放宽此项** |

> 上表第 6 行是本文成文时漏记的一项，且是模板字面之外**最大**的一处偏差。
> 实测：把 `2.gcode` 原样喂 `validate_gcode()` 先在收尾 `G90G0 X0Y0` 被拒（已文档化），
> 去掉收尾后第 6 行 `X-2.205 越界（限幅 0~190.0mm）` 被拒；去尾 + 平移 +20mm 才通过
> （`lines=945 bytes=21729 pen_lifts=91`）。含义：**奎享产物不能直接当我方回归基线**，
> 需先平移到正象限。参见 `docs/kxnx/alignment/plan.md` 的同日补记。

## 6 参考仓价值判定（省下下一次挖掘）

| 路径 | 是什么 | 判定 |
|------|--------|------|
| `D:/GitHub/kxnx/kx/tools/dump_agent/decompiled_java/` | KDraw 运行时 dump→CFR，446 个 .java | **主树，权威**，混淆名以此为准 |
| `D:/GitHub/kxnx/.../windows_reverse/decompiled_java/` | 另一版本静态反编译，字母整体位移（CommandSender 在 `machine/g/b.java`） | 副树，**勿与主树混引** |
| `D:/GitHub/kxzz/` | 奎享**字体编辑器**（`app2.jar` / `com.kvenjoy.drawfont.DrawFont` / `.gfont` `.gap`） | **与写字机协议无关**，不必挖 |
| `D:/GitHub/kxapp/` | 安卓 `kdraw.apk`（39.4M）脱壳/Frida 工具集 | 未挖；若要查移动端协议再看 |
| `D:/GitHub/COMMUNICATION_PROTOCOL_SUMMARY.md` | Kiro AI 写的 `xcx` 小程序 **BLE** 协议总结 | 非奎享产物，与 WiFi Telnet 路径无关 |
| `D:/GitHub/inkscape_px/release/paixi_draw_bundle_*/app/` | PC 串口直连 Grbl（`grbl_plotter.py` / `writing_console.py`），笔控默认 `M3 S1` / `M5` 可配 | 用户实测能落笔，但口径是串口 + 伺服/激光默认，参考价值有限 |

一条横向观察：奎享（`machine/grbl/a.java:266-281` 按 `GrblSettings.type` 分支）、`grbl_plotter.py:114-115`、`writing_console.py:358-359` **三个上位机都把笔控做成「可配置命令串」**。我方把 `G1G90 Z5.0F10000` / `G1G90 Z0.0F10000` 硬编码进生成的 G-code，是哑管道原则（决策 3：S3 不做笔控决策）下的有意简化，机型一旦扩展需要重新设计这一层。

## 7 未验证

- 第 2 节三条后果均为读码推断，**未在奎享上实测**。
- 第 3 节末两条（`flush()` 空实现、`client_reset_read_buffer` 未加锁）**未确认是否有实际影响**，也未确认断连路径是否真的调用它。
- 窗口 512 是否要回退到 100–256：**在 echo 二分定案前不改**（`docs/troubleshooting.md` 下一刀）。理由是隐患随窗口线性放大，而奎享用 100 从未出问题；但没有证据表明 512 是当前缺陷成因。
