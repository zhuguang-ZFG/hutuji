# 19 · 暂停/中止/error 白名单 + 双执行器路径（补 findings/09、15）

> 结论先行：奎享把**普通 stop** 与 **reset2** 分成两条路径。普通 stop 走 `JobManager.a(true) → Executor.b(true)`，只停止任务并把抬笔/回原点追加到普通发送队列；reset2 才走 `controller.c(true)`，发 `0x18` 并清 `G92 Z0` 已发标志 `s`。主路径暂停是**软停灌 + 注入抬笔**，不发 `!`；SD 执行器暂停才用实时 `!`/`~`。`error:120` 静默、`error:9` 弹框 `$X`，其余 error 清双队列 fail-closed。`FirmwareEventResend` 只由 Marlin 路径发出——与串口重连对话框触发的 `pollLast()` 是两件事。
>
> 源码基准树：`D:/GitHub/kxnx/kx/tools/dump_agent/decompiled_java/`。全部为读码，**未在奎享实测**。

## 1 双执行器：`f`（行流）vs `g`（SD/ESP 文件）

| | `machine/f/f.java` | `machine/f/g.java` |
|--|-------------------|-------------------|
| 用途 | **普通出图主路径**：把文件行灌进窗口引擎 `f/b` | **KAT/ESP SD 上传出图**：弹上传对话框 → 固件端打印 → 等 `FirmwareEventesp_print_finish` |
| 流控 | 自持 `f/b` 窗口引擎（character-counting） | **不持窗口**；进度靠固件 `w()`/`x()` 百分比 |
| 暂停 `e()` | `d=true` 停灌 + 注入 `b.b(true)` 抬笔（`:63-70`） | 直接 `b.z()` = 发实时字节 `!`（`:152-154`） |
| 恢复 `d()` | `d=false` + 注入 `b.b(false)` 落笔（`:56-61`） | 直接 `b.A()` = 发 `~`（`:147-149`） |
| 中止 `b(boolean)` | 若正在跑：注入抬笔 +（K_AT 专有收尾）+ `b()` 回原点（`:74-85`） | 若 `bl`：`v()` 后另起一个 **singleStep 的 f**，只发「抬笔 + 回原点」两行（`:88-102`） |
| 完成信号 | 窗口双队列空 → `m()` → `Complete` | `FirmwareEventesp_print_finish` → `Complete`（`:80-83`） |

**我方对齐对象是 `f`，不是 `g`。** hutuji 没有 SD 卡上传路径；S3 是 PSRAM 缓存 + Telnet 转发。findings/09 相关源文件表里的 `f/c.java`、`f/i.java` **在主树不存在**（已在 §7 勘误）——历史上把 `g`（SD）误写成 `c`/`i`。

## 2 暂停/恢复完整调用链（主路径 `f`）

主 UI 暂停按钮**绕过 JobManager，直接切换当前 Executor**（`machine/ui/a.java:465-475`）：

```
UI pause 按钮 → controller.r().b().b()
                  ├ 已暂停 → f.d() 恢复：d=false + 注入落笔
                  └ 未暂停 → f.e() 暂停：d=true  + 注入抬笔
```

注入行进入窗口引擎 `b` 的**待发队列尾部**，仍占窗口字节、等 `ok`；既有 pending/in-flight 不清空。笔控文本来自 `grbl/a.java:266-286`：

- `true` = 抬笔：Stepper → `G1G90 Z<zOff> F<zSpeed>`；若 `enableLongPenUp` 则用 `longPenUpMM`
- `false` = 落笔：Stepper → `G1G90 Z<zOn> F<zSpeed>`
- 默认 `zOn=2/zOff=0/zSpeed=3000` 是出厂缺省；真实缓存使用 Z5/F10000（findings/02）

关键语义：

1. 主路径暂停**不发 `!`**；planner 与在途窗口会继续排空，因此不是立即停机。
2. 抬笔在队尾，不能保证先于已经 pending 的 XY 行执行；“暂停立刻防渗墨”只是意图，未实测时序。
3. `f.b():165-171` 切换状态；`f.n()` 见 `d=true` 后停止从文件源继续喂行。
4. 实时 `!`/`~`（`grbl/a.java:448-454`）仅确认被 SD 执行器 `f/g.java` 调用；主 UI 普通路径不直接调用它们。

## 3 stop 与 reset2 是两条不同路径

### 3.1 普通 stop：任务级、非实时

`machine/ui/a.java:477-479` 的 stop 按钮调用 `e()`，普通路径再调用 `controller.r().a(true)`（`:415-425`）：

1. `JobManager l.a(true)` 调当前 Executor `b(true)`（`f/l.java:138-150`）。
2. 流式 Executor 若正在跑，在**普通待发队列尾部**追加抬笔 + K_AT 专有收尾，或抬笔 + `G90G0 X0Y0`（`f/f.java:74-85`）。
3. JobManager 把运行标志清掉，当前 `drawing → wait`，发布 stop 事件。
4. 既有 pending/in-flight **不清空**；停止延迟取决于窗口与固件 planner。

因此普通 stop 不是急停，也不发 `0x18`。

### 3.2 reset2：控制器软复位

独立 `reset2` 按钮（`machine/ui/a.java:166-168,507-508`）直接调用 `controller.c(true)`：

```java
this.s = false;           // 清「已发过 G92 Z0」标志
this.g.a((byte)24);       // 0x18 软复位
if (bl) this.s().f();     // 调 transport reset hook
```

`transport.f()` **不是统一的断连 API**：基类为空（`machine/b/a.java:93-94`），串口实现是 DTR/RTS 复位脉冲（`machine/b/f.java:98-113`）。所以只能表述为“`0x18` + transport reset hook”，不能写成“复位 + 断连”。reset2 也没有先注入抬笔。

与 hutuji 对照（已决，不改）：

| 项 | 奎享 | hutuji |
|----|------|--------|
| 普通 stop | 队尾追加抬笔/回原点，非实时 | `!` → 状态轮询，优先立即停 |
| reset | reset2 直接 `0x18` + transport reset hook | Hold/Idle 后受限 `0x18` |
| 复位后 Z 基准 | `s=false`，下次笔控旁路 `G92 Z0` | reset 后显式 Z=0/fresh `Idle`，首画/重画前旁路 `G92 Z0` |
| 回原点 | stop 追加 `G90G0 X0Y0` | 禁止（本机近原点 `G0` 触发换纸） |

## 4 error 白名单与窗口反应

### 4.1 解析层 `grbl/a.java:85-105`

前置条件：`startsWith("error") && t()`，`t()` = 仍处于已连接（`machine/c.java:165-167` 的 `c` 标志）。

```
error 正文 = substring(6) 再 parseInt
  120 → bl=true，静默（不发 FirmwareEventError）
  9   → 弹 JOptionPane；用户确认 → bl=true 且 l() 发 $X
  其它/解析失败 → bl=false → 发 FirmwareEventError
```

注意：`startsWith("error")` 覆盖 `error:` 与 `error` 无冒号两种；`substring(6)` 在 `error:` 时刚好去掉前缀，在 `error` 无冒号时会吃掉第一个数字字符的前一字节——**依赖固件始终输出 `error:N` 形式**（读码推断）。

### 4.2 窗口层 `f/b.java:111-157` `onEvent`

| 事件 | 行为 |
|------|------|
| `FirmwareEventResponse_OK` | 在途 `removeFirst`，`e -= len+1`，再 `g()` 补发 |
| `FirmwareEventError` | `f()` = 双队列 clear + `e=0`（**整任务窗口清空，不重发**） |
| `FirmwareEventResend` | 同样双队列 clear + `e=0`（**不是**重发那一行） |

### 4.3 `FirmwareEventResend` 的真实来源

全主树仅 **`machine/marlin/a.java:64-67`** 在匹配 resend 行号正则后发出该事件。`grbl/a.java` **不发** Resend。

因此：

- findings/17 的重连恢复 = **串口重连对话框**确认状态坐标非零后发布 `FirmwareEventReconnected` → `b.d()` = `pollLast` 挪队尾（详见 findings/20）
- 本节 Resend = Marlin 固件要求从某行重发时，奎享反而清空双队列（上层若不重建任务，当前窗口即丢失）

两套机制都有缺陷，都不要对齐。我方 error:8 仅在换纸逐行分支重发、窗口化分支 fail-closed（findings/17 §4），方向正确。

## 5 状态轮询与断连判定（`grbl/a.java:380-446`）

`m()` 启动后台线程（仅一份，`p` 防重入）：

```
每 1s:
  r==0 → H() 发实时 '?'（byte 63）
  r 递增；r>=20 → r=0 且 transport.b(false)  // 约 20s 无状态帧则主动断连
收到 '<' 状态行 → r=0 复位
```

与 hutuji：我方 keepalive / silent-poll 是另一套（S3 侧防误杀 TCP）；奎享这 20s 判死属于 PC 上位机逻辑，**不引入**到 S3。

## 6 换纸相关（明确非本机路径）

`f/l.java` 多文件调度在控制器是 `machine/e/a`（K_MT，Marlin 系）时：

- 文件间发 `M1100`（`:206`）触发放纸/换纸 UI
- 完成后发 `M1103`（`:245`）
- 听 `paper ready` / `remove paper` / `paper removed` 事件（`e/a.java:45-55`）

**本机 Grbl + `M30` 换纸不走这条。** 记录以免有人把 `M1100` 当对齐项。awauto 路径用 `aw_paper_ready` 事件，同理不对齐。

`grbl/a.java` 的 `[ESP1]…[ESP6]` 路由（`:115-152`）是配网/版本/扫网/向笔记页塞内容，**不是换纸状态机**；换纸在本机固件是 `M30` + `Changing` 字段（见 `docs/protocol.md`）。

## 7 findings/09 源文件表勘误

主树 `machine/f/` 实际只有：`a b e f g h k l m`（9 个文件，共 1009 行）。**不存在** `c.java`、`i.java`。

| findings/09 旧写法 | 实际 |
|--------------------|------|
| `f/c.java` KAT/WiFi 文件级调度器 | 不存在；SD/ESP 文件路径是 **`f/g.java`** |
| `f/i.java` 出图总调度 | 不存在；多文件队列是 **`f/l.java`**，总控在 UI/`KenjoyDraw` |

本文 §1 的双执行器表取代上述两行。findings/15 的四层图（l→f→b→transport）对**主路径**仍正确；SD 路径是 l→**g**→固件自打，旁路 b 窗口。

## 8 对 hutuji 的取舍（本轮不改代码）

| 项 | 建议 | 理由 |
|----|------|------|
| 暂停注入抬笔 | **P2 可研究，不直接照搬** | 抬笔在 pending 队尾，不保证先于已有 XY；须先验证窗口时序 |
| 暂停改软停灌、去掉 `!` | **不改** | 语音「停下」需要立即停；软暂停用户体感是「还在跑」 |
| error:120 白名单 | **不改** | 本机固件不产此码 |
| error:9 弹框 | **不改** | 无人值守 |
| stop/reset 分层 | **保留我方现有分层** | 奎享普通 stop 非实时、reset2 无安全 Hold；我方 abort 更严格 |
| 对齐 M1100 换纸 | **禁止** | 非本机协议 |
| 对齐 g 的 SD 上传 | **禁止** | 哑管道 + 云端生成，无 SD |

## 9 未验证

- 暂停注入的抬笔行与窗口内已有 pending/in-flight XY 行的真实执行时序，未在奎享实机抓轨迹。
- 主 UI 已确认普通 stop → `l.a(true)`、reset2 → `c(true)`；未发现主 UI 调 `c(false)`，其他工具窗口是否调用仍未全树穷尽。
- `error` 无冒号时 `substring(6)` 的边界行为未用固件样本验证。
