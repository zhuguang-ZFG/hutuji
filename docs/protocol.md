# hutuji 板间协议与 G-code 契约 v0.5

本文是 ②（S3 固件补丁）与 ③（MCP 服务）的共同契约。改动需两侧同步评审。

版本历史：
- v0.2：按两仓代码勘察修订（Grbl_Esp32 `c19a382`、xiaozhi-esp32 `38bc4f4`）。
- v0.3：物理层改 CH9350 桥接——**已否决**：CH9350 仅支持 HID 类（键盘/鼠标/扫码枪），不枚举 CH340（[WCH 官方手册](https://www.wch.cn/products/CH9350.html)）。
- v0.4：物理层改 **WiFi Telnet**（方案 E）。依据：Grbl_Esp32 内置 ESP3D WebUI 开放 Telnet:23，社区成熟模式；本机商业上位机已用 BT SPP 无线流式出图（`PAPER_AUTO_CHANGE_ON_BT_CONNECT`），无线流控经量产验证。USB Host 方案不可行的结论保留（实战派 C 口被 CH334F HUB 占为设备口）。
- **v0.5（2026-07-25，逐项 grill 定案）**：按 Grbl_Esp32 源码逐处核对修订。**纠正 v0.4 的关键错误假设**——`PaperSystem.cpp` 63 处消息路由全为 `CLIENT_SERIAL`、零 `CLIENT_ALL`，**S3 经 Telnet 收不到任何换纸进度/状态消息**（v0.4 的 §2/§3/§7 均误以为能收到）。据此新增 Grbl 侧最小改动清单（§9），并定案：Telnet 独占、半开连接 keepalive、断连物理复位与半成品纸处置、先下完再校验后发、授权前置门、abort 分状态、status 分级与单写者。
  > **历史标注约定**：v0.5 定稿时带 🔬 的条目代表待实测；其后是否完成以
  > `release-readiness.md` 和下方 2026-07-28 实测校正为准。
- **v0.5 实测校正（2026-07-28，不升协议号）**：Grbl §9、Telnet 逐行链路、授权探测、
  非换纸 abort 恢复和任务完成判定已有当前实验台证据。校正两条旧推断：soft reset 后
  单发 `M5` 不保证抬笔；最后一行 `ok` 不等于物理运动完成。换纸与断连重发仍未验证。

## 1. 物理/链路层（方案 E：WiFi Telnet）

```
实战派 ESP32-S3 ──WiFi STA──► 家用路由器 ◄──WiFi STA── 写字机 Grbl_Esp32
        （S3 必须经路由器上云）              （Telnet server，端口 23）
```

| 项 | 约定 |
|----|------|
| 链路 | S3 TCP 客户端 → 写字机 **Telnet :23**（Grbl_Esp32 WebUI 原生，Config.h 已启用 WiFi） |
| 寻址 | 写字机**固定 IP**：路由器静态 DHCP 租约（首选）或 `$Sta/IPMode=Static` + `$Sta/IP` |
| 参数 | 链路内即原串口字节流：115200 语义不变，行协议逐行 `ok`/`error:NN` |
| 流向 | S3 → Grbl：G-code 行；Grbl → S3：`ok` / `error:NN` / 部分 `[MSG:]` / `?` 状态报告（**换纸消息不在其中，见下方「重要认知」**） |
| 重连 | TCP 断开 = 链路丢失：S3 指数退避自动重连（1s/2s/4s…上限 30s），重连成功后按 §2 重新探活；进行中的任务按 §2.2 处置 |

### 1.1 Telnet 独占（固件硬约束）

`TelnetServer.h:31` **`MAX_TLNT_CLIENTS = 1`**——Telnet 服务器同时只接受 **1 个**客户端，第二个客户端被 `clearClients()`（`TelnetServer.cpp:98`）直接 `.stop()` 拒绝。

**因此格局定为：Telnet 数据链 = S3 独占；CH340 串口 = PC 只读旁观。**

- PC 侧 `telnet <IP> 23` **仅用于 §1.3 一次性验链路**，验完**必须显式退出**，否则 S3 连不上。
- PC 全程观测走 CH340 串口——它是最全诊断口，且换纸消息**只**发这里（见「重要认知」）。
- 「PC 常连只读 + S3 独占发送」在物理上不可行（客户端上限为 1），不要尝试。

### 1.2 半开连接与 keepalive

`MAX_TLNT_CLIENTS=1` 叠加半开连接会产生**重连盲区**：S3 掉电/WiFi 瞬断/路由器 NAT 表项超时时**不发 FIN**，Grbl 侧 `_telnetClients[i].connected()` 可能仍为 true，唯一槽位被死连接占住，S3 重连一路撞 `.stop()` 拒绝，直到底层 socket 超时回收——**默认无 keepalive 时该盲区可能无限长**（`TelnetServer.cpp` 未设任何 keepalive；`WiFiClient.cpp:287` 有一处注释掉的 `SO_KEEPALIVE`，说明原作者考虑过但未启用）。

三层处置，全部必做：

| 层 | 动作 | 覆盖场景 |
|----|------|----------|
| S3 | 每次重连前对旧 socket 显式 `close()`（发 FIN）；`abort`/错误路径同样保证 FIN 发出 | 优雅断连、主动 abort |
| S3（已修） | `SO_KEEPALIVE` **必须同时设 `TCP_KEEPIDLE=10`/`KEEPINTVL=3`/`KEEPCNT=3`** | 写字机掉电/断网时 S3 侧的发现 |
| 写字机（§9-A） | `clearClients()` 取得新客户端后同样设这四项（~20s 回收） | **S3 掉电、瞬断、NAT 超时**（FIN 发不出，S3 侧无法自救） |

> ⚠️ **只开 `SO_KEEPALIVE` 开关等于没开**：lwip 默认 `TCP_KEEPIDLE=7200s`(2h) / `KEEPINTVL=75s` / `KEEPCNT=9`，合计 **≈2h11m** 才发现死连接。M1 代码原本只设了开关（注释却写「尽快发现对端断电」），已于 v0.5 修正为显式三参数。

#### 断连发现时间常数（从代码算出，非实测）

| 场景 | 发现耗时 | 依据 |
|------|---------|------|
| 写字机优雅关闭（发 FIN） | **立即** | `recv` 返回 0 |
| 写字机断电/断网，S3 空闲等 `ok` | 默认 ≈2h11m → **改后 ≈19s** | `KEEPIDLE 10 + 3×3` |
| 写字机断电，S3 正在 `send` | 数十秒 | TCP 重传耗尽，`send` 返回 <0 |
| S3 掉电后重连（写字机侧） | 默认**可能永久** → §9-A 改后 ≈20s | `MAX_TLNT_CLIENTS=1` 槽位被死连接独占 |

**方案 E 验收判据**：判据**不以延迟/抖动为准**——严格逐行 `ok` 应答是 req/ack 协议，网络延迟只降吞吐、不破坏正确性。真正要标定的是上表四个时间常数，其中三个已由代码常量确定（不需实测）。

🔬 **仅剩两项需冒烟实测**：
1. **掉线频率**（取决于路由器/WiFi 环境，代码中不存在此数）：达标 = 代表性多页会话（连续流式 ≥10 分钟）TCP 零掉线。超阈或**换纸阻塞窗口内掉线**才评估回退备用链路。
2. **上表第 4 行实测值**（拔 S3 电 → 写字机回收槽位 → 重连成功的总时长），验证 §9-A 生效。

**重要认知（v0.5 更正）**：串口是最全诊断口，Telnet 只承载**一部分**输出。经源码核对：

- Telnet **收得到**：`ok` / `error:NN`、`?` 触发的 `<...>` 状态行（`report_realtime_status(client)` 按 client 直接回）、`report_init_message` banner、`CLIENT_ALL` 类消息。
- Telnet **收不到**：**全部换纸消息**——`PaperSystem.cpp` 63 处 `grbl_msg_sendf` 路由**全为 `CLIENT_SERIAL`**，`CLIENT_ALL` 出现 0 次；`GCode.cpp:1728` 的 `[MSG:PAGE_END_IMMINENT]`、`:1791` 的 `[Paper] page end` 同为 `CLIENT_SERIAL`。故 `[PaperAuto-N]` 进度、`[PaperStatus] N` 结果码在**未做 §9 改动前，S3 一个字节也收不到**。

**备用链路**（回退判据见 §1.2）：方案 B 拆机 TTL——写字机内 ESP32 GPIO1/3（CH340 输出侧）飞线 → 实战派多功能口 UART（IO10/IO11），3.3V、TX/RX 交叉 + GND。M1 代码的 UART 传输层实现已留档可切换。

### 1.3 写字机 WiFi 开通与验证（②开工第一步）

> ⚠️ 下列 `$Sta/...`、`$Telnet/...` 设置名沿用 ESP3D 通用写法，**本 fork 的确切拼写未经核对（🔬 串口一试便知）**。

1. PC 串口工具接写字机 CH340（115200），依次下发：
   - `$Sta/SSID=<家里WiFi>` / `$Sta/Password=<密码>`（写入即存 NVS）
   - 路由器后台给写字机 MAC 绑定静态租约（或 `$Sta/IPMode=Static`、`$Sta/IP=<addr>`）
   - `$Telnet/Enable=1`、`$Telnet/Port=23`（确认默认开启）
   - `$I` 或 `[ESP110]` 查当前 IP 与状态
2. PC 上 `telnet <写字机IP> 23`，发 `?` 应回 `<Idle|…>`，发 `$I` 应回 `[VER:…]`——链路通。
3. S3 侧联调时 `hutuji.status` 应显示 connected/ready。

## 2. 就绪检测与会话重建

- **就绪判据（Telnet 版）**：连接候选先用主动 banner 验明 Grbl peer；正式连接后发 `$I`，
  收到版本行只进入下一探测阶段。必须完成 M5 抬笔、fresh 位置和授权零位移探测后才
  `ready=true`，此前不得启动 draw。
- **banner 语义 = 对端复位**：连接期间收到 `Grbl 1.3a ['$' for help]` 视为写字机复位
  （欠压等）→ 清任务应答并重新探活。当前任务通常置 error；仅 §4.1 标记的自发 abort reset
  进入受限恢复并最终置 `aborted`。
- **授权前置**：`[License] Authorized` 只发 `CLIENT_SERIAL`，Telnet 的 banner/`$I` 不可依赖。
  连接探活后先 `M5`、取一份新 `?` 位置，再在抬笔状态发送 `G1 G53 X<当前机器 X> F1500`
  零位移探测：**`G1` 必须是第一个 G 字**，因为商业固件授权前置门只检查行首 G0–G3；旧顺序 `G53 G1` 会绕过前置门，未授权运动在 GCode 内层被静默跳过后整行仍回 `ok`，从而误报已授权。正确顺序下 `ok`=已授权，`error:110`=未授权。探测结束前 `ready=false`，未授权见 §6。
- **解析器必须容忍**（异步混入字节流，均非应答）：`[MSG:…]`、`?` 触发的 `<Idle|MPos:…|Bf:…|FS:…>`、`ALARM:n`、`[VER:/[OPT:/[License]` 等裸 `[…]` 行。
  > 注意：`[MSG:[PaperAuto-N] …]` / `[MSG:[PaperStatus] N]` 这类**换纸消息在 §9 改动落地前不会出现在 Telnet**（见 §1.2）。解析器仍应容忍其格式（§9 落地后 `[PaperStatus]` 会到达）。

### 2.1 断连后的物理复位与半成品纸处置

**事实（源码 + 2026-07-28 实测）**：TCP 断连**不改变 Grbl 任何状态**——`TelnetServer`
断连路径不触发软复位/feed hold，planner 内已排队运动会继续。当前实机的 S3 自发 `0x18`
abort 会重放 banner，并进入需要 `$X` 的锁定恢复路径；因此不得再假设 reset 后可直接发普通行。
S3 只给“自己刚标记并发出的 abort reset”一次自动 `$X` 权限；其他复位、`ALARM:n`、
`error:9` 一律保持人工解锁。

**混合分流**（新连接只完成 Grbl banner 验真后、恢复授权运动探针之前，先按对端是否在换纸分两支）：

| 场景 | 判定方式 | S3 动作 |
|------|----------|---------|
| **普通画线行断连** | `[ESP901]` 查 `Changing=Off`（需 §9-B2′） | **2026-07-29 已实测到主路径**：受限 reset 恢复 → 确认抬笔/Idle/授权 → `M30` 走掉废纸（`[PaperStatus] 0`）→ 从 PSRAM 缓存开头重发（§5.1）→ 55/55 行完成并 fresh `Idle`；最终页尾自动换纸因无纸 `error:90`，完整任务仍非 `done` |
| **换纸窗口内断连** | `[ESP901]` 查 `Changing=On`，或本次断连前最后发出的是 M30/M721/[ESP910] | **不发 `0x18`、不发 `M30`**（`0x18` 会被 8s 窗吞、`M30` 会叠成二次换纸）；抬笔停机 + 推送提示用户，由人决定重画 |

- **`[ESP901]` 可达性已源码验证**（非推断，v0.5 勘察）：① `anyState()` 恒返回 `false`（`Settings.cpp:6-8`，守卫语义是「是否拒绝」）→ 任何 `sys.state` 下都不拒；② 换纸阻塞期 `protocol_poll_client()` 的换纸拦截（`Protocol.cpp:184`）**只拦 G0–G3**，`[ESP901]` 落到 `:205` 正常 `execute_line`；③ 响应经 `ESPResponseStream(client)` → `grbl_send(CLIENT_TELNET)` 回给发命令的 client（`ESPResponse.cpp:46,99`，且 `:55` 专为 Telnet 用 `\r\n`）。**故换纸期间 S3 发 `[ESP901]` 收得到含 `Changing` 的响应。**
- 🔬 **仍待验证**：有纸时页尾自动换纸成功并最终 `done/Idle`、`Changing=On` 分流、
  换纸物理耗时与半成品纸处置。非换纸 abort 自恢复通过不覆盖这些项。
- **v1 再做**：自动穿越换纸续接（在未完成的换纸上安全接续），v0 不赌机械安全。

#### 2.1.1 活跃任务重连恢复实现契约（S3 `05f4563` 起，`3ca9d31` 复测）

1. **范围/触发**：仅绘图会话内 TCP session 变化触发；初次建链仍走常规授权探测。
2. **接口**：`Pipe::SetTaskSessionActive(bool)` 保护会话（置 `false` 且未授权时同步清 `ready_`，
   禁止下一任务复用旧会话残留的 ready）；`Pipe::SendAbortReset(session, status_baseline,
   paper_baseline)` 是 `0x18` 的**唯一出口**，内部按 `CanSendAbortReset()` 六项与会话代号
   一次性令牌 `AbortResetToken`（`hutuji_recovery_core.h:14-45`）判定，Arm 成功即先清
   `ready_/authorized_`；发送失败、断连、`CloseSocket`/`ShutdownSocket` 与新连接发布都必须
   `CancelAbortReset()`，banner 侧只能 `Consume(connection_seq)` 一次——跨连接的旧令牌一律失效。
3. **顺序契约**：重连只验 Telnet banner，保持 `ready=false` 且不发 `M5/G1`；`!` 之后必须先用 `?`
   取到**本次连接内新鲜**的停机状态行——`Idle` 或 `Hold:0`（判据 `IsStoppedForReset()`，`Hold`
   必须带子码 0 = holdComplete；`Run`/无子码 `Hold` 不算停稳），再发 `[ESP901]` 取本次新鲜
   `Changing`，只有 `Off` 才能发 `0x18`。reset 恢复、废纸 `M30` 与 fresh `Idle` 全过后，才从仍存活
   的 PSRAM 第 1 行重发，不重新下载。
4. **校验/错误矩阵**：`Changing=On/Unknown` → 禁止 reset/M30、停止自动续画；查询超时、
   reset banner 超时、未授权、废纸换纸失败 → 任务 error；任一步 abort → 不再续发。
5. **用例**：基准=无断连正常完成；好例=画线断连且 `Off` 后废纸并从头重画；坏例=
   `On` 时自动授权探针先发 G1，得到 `error:8` 后既无法 ready 也无法完成分流。
6. **必测断言**：bringup 8.3 断言 planner 被清、废纸被走掉、PSRAM 从第 1 行重发；8.4
   断言 `On` 时没有 `0x18/M30/G1`；8.5 断言未知/换纸保护窗内 abort 只挂起。
7. **错/对**：错=`重连 → $I/M5/G1 探活 → ready → 查 Changing`；对=
   `重连 banner → 查 Changing → Off 才 reset/探活，On 则停`。
8. **单 owner 事务**（原实现状态:工作树未提交、未构建、未烧录 → **2026-08-03 supersede：已提交 `6545ec9`、已构建并烧录 S3，551 行 S4 有纸实测通过**）：每个任务最多一个 reset owner
   ——`abort_reset_started_` CAS 抢占，后到者走 `WaitForAbortReset()` 复用同一次结果，`busy_`
   在 `abort_reset_done_` 置位前不得释放（`hutuji_job.cc:346-464`）；窗口模式的在途应答由
   `stream_window_active_` 表达,`wait_for_stream_quiescence` 路径必须等它清零才允许发 `!`，
   否则旧窗口迟到的 `ok` 会被计入 reset 之后的新流。

## 3. 流控（Grbl 行协议）

- **逐行发送，收到 `ok\r\n` 才发下一行**；`error:NN\r\n` 按 §7 处理。**无 character-counting**，禁止预发抢跑（client_buffer 溢出会丢字节）。
  - ⚠️ **错误码有两种线上形态，解析必须都认**：`$Errors/Verbose` 为真时 `Report.cpp:236` 把应答从 `error:11` 换成 **`error: Line too long`**（文本，取自 `Error.cpp` 的 `ErrorNames`）。出厂默认是 0（`Defaults.h:82`），但该 `$`-设置存在 NVS 里，能被任何上位机（奎享本体、ESP3D WebUI）改写并持久化。只认数字的解析器不是降级而是**行为反转**：`error:8`（换纸期推迟、需重发该行）解析成 -1 后走 `Failed` → 整单失败而非重试；`error:110`（未授权）同样落进 `Failed` 而非「已知未授权」。S3 侧解析实现见 `hutuji_recovery_core.h` 的 `ParseGrblErrorCode()`，两种形态由 host 编译单测钉住。
- **单行仅 ASCII 且 ≤ 254 字节 payload**（不含行尾换行符），非 ASCII 或超长回 `error:11`。MCP 生成端折行负责。
  - **是 254，不是 255**：`Protocol.h:34` 定义 `LINE_BUFFER_SIZE 256`，但 `Protocol.cpp:68` 的溢出判定排在换行判定**之前**——第 255 个 payload 字节能存进缓冲（`len` 变 255），紧随的换行符进来时先撞上 `len == LINE_BUFFER_SIZE - 1` → 回 `error:11`，且 `:218` 的 `empty_line()` 把整行丢掉，**该行从未进入 `execute_line()`**。即 255 字节的行不是「勉强执行」而是「整行被设备静默吃掉」。上游 grblHAL 把 `LINE_BUFFER_SIZE` 定为 257（`protocol.h:36`，注释 "256 characters plus terminator"）正是为绕开这个 off-by-one；Grbl_Esp32 未加 1，故本机可用 payload 是 254。校验器口径见 `gcode_gen.py` 的 `MAX_LINE_PAYLOAD_BYTES`。
- **CRLF 行尾会让设备每行回两个 `ok`；S3 必须重切行，禁止原样透传下载字节**。`\r` 触发一次 EOL（执行真实行），紧随的 `\n` 又触发一次 EOL，而 `execute_line()` 对 `line[0] == 0` 直接返回 `Error::Ok`（`Protocol.cpp:84-86`）于是再回一个 `ok`。CRLF 不是假想形态：奎享真实缓存产物就是 CRLF（946 CRLF / 0 裸 LF），Windows 上 `vpype gwrite` 走 `click.File("w")` 文本模式同样产 CRLF。当前 S3 安全是因为 `ParseLines()` 自己按 `\n`/`\r` 切行并跳过空行、`SendLine()` 只补一个 `\n`——**这是契约要求，不是实现细节**。一旦为省内存改成 zero-copy 直发 PSRAM 字节，每行多一个 `ok` 会让在途窗口计数持续正向漂移，表现为「越画越超发」。
- **换纸阻塞期**（数十秒）：该行的 `ok`/`error:90` 在**整个换纸完成后**才返回——这类行（M30/M721/[ESP910]）等 ok 超时 ≥ 60s（换纸行放宽到 90s）。此期间**禁发任何 G0–G3**（会被吞并回 `error:8`，见 §7）。换纸期间 Grbl 主循环不转，S3 周期性 `?` 不会收到状态行；Pipe 用 `expect_blocking_peer_` 标记换纸期，silent-poll 超时不掐链（TCP keepalive 兜底），换纸结束/任务收尾清回。
  - ⚠️ **进度反馈不能靠 `[PaperAuto-N]`**（§2：那些走 CLIENT_SERIAL，Telnet 收不到）。S3 判「仍在换纸」的手段只有：**自己发的是 M30/M721**（自知，正常出图期够用），或换纸期周期性 `?` 探活（走实时旁路、换纸期可响应，见 §2）。结果码 `[PaperStatus] N` 须靠 §9 的路由改动才能到达。
- 实时命令（`!` 暂停、`~` 恢复、`?` 状态、`0x18` 软复位）单字节立即生效，走实时旁路**不进 client_buffer、不吃 `ok`**（`is_realtime_command` 仅认这四个 + `≥0x80`）；`?` 的应答只回给发送 client。
- **`0x18` 特例**：换纸开始后 8s 窗口内被 `paper_should_ignore_host_reset()` 忽略（且「已忽略」提示只发 CLIENT_SERIAL，Telnet 收不到）；窗口后才中止换纸。`abort` 实现须注意（§4）。
- **单写者纪律（关键）**：S3 是 Telnet 唯一客户端（`MAX_TLNT_CLIENTS=1`），但其 `draw` 转发任务与 `status` 查询**不得并发写同一 socket**——否则 `M704`/`[ESP901]`（普通命令，吃 `ok`）会插进逐行流，打乱 `ok` 配对、或字节交错拼成 Grbl 认不得的行。全部 Telnet 写必须串行化（同一发送队列或一把发送锁）。
- **`!` 抢占例外（2026-08-02）**：普通行、`?`、`~`、`0x18` 继续持 `write_mutex_`；只有单字节 feed hold `!` 可绕过该锁。底层每次 `send(..., MSG_DONTWAIT)` 仅在持 `sock_mutex_` 时执行，因此 `!` 最多插在普通行的两次 send syscall 之间；Grbl 在任意字节边界先消费实时字符，不把它写入行缓冲，故不会拼坏 G-code。普通发送看到 `!` 等待者时让出，仲裁等待不计入 20s 活动停滞预算；正 partial write 不续期，预算耗尽、零进展或真实错误均半关连接并重建。该例外只解决「背压期间暂停最长被挡 20s」，不允许任何普通命令并发写。
- 空行、注释（`;` 与 `()`）由 S3 转发前剔除。

## 4. 任务协议（S3 设备侧 MCP 工具）

**并发约束（勘察结论）**：xiaozhi 的 MCP 工具回调在**主任务同步执行**——禁止在回调里跑长任务。全部工具**异步化**：

| 工具 | 参数 | 行为 |
|------|------|------|
| `hutuji.draw` | `url` | 回调仅校验参数+幂等检查 → 立即返回 `"started"`（任务进行中则返回 `"busy"` **直接拒绝**，不排队不抢占）；独立任务执行：**① 全量下载**（`Board::GetNetwork()->CreateHttp()`，`GetBodyLength()`/`GetResponseHeader()`/`Read()` 循环，模板见 `ota.cc:292-322`）→ **② 完整性校验**（§5.1，长度+CRC32）→ **③ 授权门**（§6，未授权则零字节下发）→ **④** 按行切分、Telnet 写、等 ok → **⑤** 等 fresh `Idle` → **⑥ S3 单独发送 `M30`**并等换纸最终应答与 fresh `Idle`。**不边下边转；`M30` 不在下载文件内** |
| `hutuji.status` | — | 同步快速执行，返回 `{connected, ready, authorized, state, last_line}`。**分级**：出图进行中（streaming/paper_change）**只发 `?`**（实时旁路，不吃 `ok`）；**仅空闲时**才发 `[ESP901]`（纸/电机/PanelHold/Changing）+ `M704`（GPIO34）——这两个是普通命令会吃 `ok`，出图中发会打乱流控（§3 单写者） |
| `hutuji.abort` | — | **分状态**（§4.1）：下载/校验态只置取消；非换纸流式态发 `!` 并用 `?` 轮询到 `Hold/Idle` 后 `0x18`；换纸活动态**不发 `0x18`**，诚实回报「换纸中无法即停、完成后即停」+置 abort 挂起 |

- 任务完成/失败经 `Application::SendMcpMessage` **设备侧主动推送**；同步查询走 `hutuji.status`。
- 状态机：`idle → downloading → verifying → streaming → (paper_change) → done | error | aborted`；TCP 断开/banner 重放 → `error`（按 §2 走物理复位分流）。
- **完成判据**：最后一行的 `ok` 只表示该行进入 planner。S3 必须先等到一份 fresh `Idle`，
  再单独发送 `M30`；`M30` 返回 `ok` 后还要再次取得 fresh `Idle`，此时才能置 `done`。
  不得用旧缓存 `Idle` 或固定 sleep 判完成。该路径已编码，尚未完成有纸实机验收。
- **同一时刻只允许一个任务**；任务期间 PSRAM 缓存（§5.1）持有至任务终结。成功且换纸完成后
  保留一份供 `repeat` 复用，error/aborted 释放。
- 代码挂点：`lichuang-dev` 板级 `InitializeTools()`；**板级行为不进核心模块**。

### 4.1 abort 语义（重要：不是万能急停）

**物理限制：换纸一旦启动，abort 打不断它。** `paper_auto_change` 是 ≥60s 的阻塞流程，固件**故意**在开始 8s 窗口内忽略 `0x18`（`paper_should_ignore_host_reset()`）以保护换纸不被中途打断；且「已忽略」提示只发 CLIENT_SERIAL，S3 **收不到任何失败回执**。

故 abort 语义定义为「**尽快中止**」而非「立即中止」：

| 时机 | S3 行为 | 回报用户 |
|------|---------|----------|
| 下载/校验态 | 只置取消标记，不向 Grbl 发 reset | 「已中止」 |
| 非换纸流式态 | `!` → 周期发 `?`，观察到 `Hold/Idle`（最长约 8s）→ 标记本次为自发 abort reset → `0x18` | 先回「已受理」；恢复序列完成后状态为 `aborted/Idle` |
| 换纸活动态 | **不发 `0x18`**；置 abort 挂起，停止后续 G-code 下发 | 「写字机正在换纸，无法即停；换纸完成后任务将停止」 |

- 换纸挂起态下，等该行 `ok`/`error:90` 返回后**不再发任何后续行**，任务落 `aborted`。
- 非换纸 reset 后的受限恢复顺序固定为：`$X` → `G1G90 Z0.0F10000` → 等一份新的
  `Idle` → `$I` → `M5` → `?` → `G1 G53 X<当前机器 X> F1500` 授权探测；`G1`
  必须置于 `G53` 前以命中 Grbl 行首授权门。只有 `PrepareAbortReset()` 标记对应的下一次 banner 可走 `$X`；任何其他 Alarm 不自动解锁。
- **不能把抬笔放在 reset 前排队**：`!` 后普通 G-code 只会进入 planner，`0x18` 又会丢弃它。
  实机还证明 reset 后单发 `M5` 可能因主轴模态已为 Off 而成为无动作，故先显式 Z=0。
- Z=0 行的 `ok` 仍只代表进入 planner；必须等 fresh `Idle` 再发 `$I`，否则运动未停时会回
  `error:8`。2026-07-28 回归最终为 `aborted/Idle`、Z=0，8 秒无 XY 漂移或 planner 续跑。
- ⚠️ 上层（云端 LLM / 用户提示语）**不得把 abort 描述为急停**，否则换纸中喊停无反应会被误判为系统故障。

## 5. G-code 文件契约（MCP 服务产出）

- **下载文件自包含**：生产 profile 文件头固定为精确的 `G21`（毫米）、`G90`（绝对）、`F8000`，随后
  连发两次 `G1G90 Z0.0F10000`。这两行是与奎享缓存兼容的幂等抬笔运动，**不能建立 Z 坐标基准**；
  校验器不接受注释或 `G210`/`G900` 等子串伪造，也不允许运动发生在 G21/G90 建立之前。
- **完整出图会话的 Z 基准**：奎享本机 `$1=25`，电机失能后弹簧回到自然抬笔位，但 Grbl 计数不会
  跟随机械回弹；因此每次首画/重画前，S3 必须先确认 fresh `Idle`，等待 100ms（大于 25ms），再以
  **受控旁路命令**发送固定 `G92 Z0`，随后才窗口化连续发送下载文件。下载文件无 `G92` 与会话旁路
  有 `G92 Z0` 不矛盾；`G92` 仍禁止进入下载文件，且不修改 Grbl `$1/$3`。
- **避开隐式换纸触发点**：
  - 软复位后**第一条** `G0 X0 Y0 Z0`（首页触发）
  - `G28` / `G30`，以及执行后工件坐标 XY≈0 的 `G0`（回原点触发，含 `G0 X0 Y0` 收尾！）。
    若 G0 的模态 XY 实际终点无法完整解析，也从严拒绝，因为不能证明不会落到 `(0,0)`。
  - **结论：分页节奏必须显式控制**——MCP 产出的文件以抬笔结束并停在当前位置，**不含**
    `M30`/`M721`；S3 确认绘图 fresh `Idle` 后，再以任务编排命令单独发送 `M30`。
    收尾**不要**用 `G0 X0 Y0`。
- **生产文件抬/落笔 = 纯 Z 运动**：`G0 X...Y...F8000` 空走到笔画起点，
  `G1G90 Z5.0F10000` 落笔，`G1 X...Y...F8000` 画线，`G1G90 Z0.0F10000` 抬笔；
  文件内不含 `G92`、`M3/M5`、`G4`。Grbl 的 `USE_M3_M5_AS_PEN_UP_DOWN` 仍保留，
  M3→Z=5/M5→Z=0 已用于手工与恢复路径验证，但**不是生产 profile 的出图指令**。abort reset
  恢复仍必须用明确 `G1G90 Z0.0F10000`，不能依赖 reset 后的 M5 模态变化。
- **坐标范围与越界策略（拒绝，不裁剪）**：实机 A4 行程已设为 `$130=210`、`$131=297`，且**无限位开关**；越界可能直接撞机，限幅责任 100% 在 MCP 生成端。
  - **可用画幅**：≤ **190×277mm**（A4 四周留 10mm），坐标须落在
    `0 ≤ X ≤ 190`、`0 ≤ Y ≤ 277`。
  - **越界处理 = 拒绝生成 + 明确告知用户**，**不得裁剪 / 不得缩放后静默出图**：任一坐标超界 → MCP 不产出文件，向云端回明确原因（超界轴 + 实际值 + 上限），由用户决定改小内容或换尺寸。
  - 理由：裁剪会把「越界」变成一张**静默残缺**的画（与 §5.1 静默截断同类失败——用户拿到看似完成实则缺一块的纸）；拒绝把失败前移到落笔之前，**零废纸**。自动缩放同理否决：改变用户预期的构图属于静默篡改。
  - MCP 生成端应在**输出前**统一做一次全量坐标扫描（含圆弧 `G2/G3` 的包围盒，不只是端点），越界即整单拒绝。
  - **实现状态（2026-07-26）**：`validate_gcode()` 的限幅覆盖 `G0/G1` 端点与 `G2/G3` **包围盒**（`_arc_bbox`）。包围盒取「起点 + 终点 + 落在实际扫过角度区间内的象限点」的极值——只算扫过的象限点，否则会把整个外接正方形当边界而误拒合法弧。圆心求解复刻 Grbl `GCode.cpp`：IJ 偏移模式圆心 = 起点 +(I,J)（`:1266`），R 半径模式解 `h_x2_div_d`（`:1216-1251`）且**负 R = 走 >180° 的大弧**（`:1245`）；IJ 模式另按 Grbl `delta_r > 0.5mm` 校验起终点半径自洽（`:1272-1279`）。当前生产 profile 不产圆弧，指令集为 `G0/G1/G21/G90`，故此路径只在接受外部 G-code 时生效。
    > 溯源：圆弧限幅的代码实际随 `8247c3b`（message 只写了「记录 IDF 实况与串口识别坑」）进入 main——原本的 PR #4 未合并，其分支被后一个文档 PR 夹带进来。按 message 查不到本功能，查 `8247c3b` 的 diff。
  - **圆弧限幅依赖逐行位置跟踪**：包围盒要起点，故校验器逐行维护当前 XY；首个定位指令之前出现 `G2/G3` 一律拒（不猜 `(0,0)`）。且**中途 `G91` 相对坐标一律拒**——位置跟踪假定全程 `G90`，一旦切相对模式，此后每个圆弧起点都错、包围盒静默失真。回归见 `test_arc_*` / `test_mid_file_g91_is_rejected`。
  - **指令识别不得依赖空格切分或本行显式 G 字**：`G0X0Y0`（无空格）、`G90 G0 X0 Y0`（模态前缀）都能绕过按首 token 判断的禁令；裸 `X500 Y500` 会继承 Grbl 当前运动模式，分轴 `G1 X50 Y0` → `G0 X0` 的实际 G0 落点也是 `(0,0)`。Grbl 复位默认运动模式是 G0；校验器同样从 G0 开始，并由生产文件头的两次 `G1G90 Z0.0F10000` 切到 G1，再逐行做模态继承、限幅、实际落点与位置跟踪。坐标正则也须接受 `X.5` / `X1e3`（否则 `1e3` 被截成 `1`，1000mm 越界漏放）。回归见 `test_gcode_rejects_bypass_variants` / `test_*modal*`。
  - **生产指令允许列表（2026-07-29）**：下载文件只允许整数码 `G0/G1/G2/G3/G21/G90` 与 `F/X/Y/Z/I/J/R` 数值字；任意 M 码、`G20/G4/G53/G92`、非整数或其他未知 G/字母均拒绝。其中 G2/G3 仅为外部 G-code 圆弧限幅兼容保留，当前 profile 只产 G0/G1/G21/G90。页结束的 `M30` 由 S3 在文件执行完并确认 fresh `Idle` 后单独发送，不属于下载文件。
  - **注释口径**：校验器会剥 `;` 及其后的行注释，避免注释中的 X/Y/G/M 字样误触发；暂不解析 `(...)` 括号注释，**含 `(` 的行一律整行拒绝**（`gcode_gen.py`，不区分括号内容），故括号注释里的 `G28`/`G0 X0 Y0` 与纯合法的括号注释都会被拒。当前 profile 不产注释行（实测产物中 `;` 与 `(` 均为 0）；接受外部 G-code 前若要兼容括号注释，须先实现确定性剥除并补回归。
  - **非有限数值必须拒成 `GcodeError`**：`G1E400` 之类会 `float()` 成 `inf`，`int(inf)` 抛 `OverflowError`——非 `GcodeError` 异常会穿透闸门变成 500，违背「越界即整单拒绝 + 明确告知」。故字号解析后须 `math.isfinite` 检查。回归见 `test_gcode_rejects_nonfinite_and_malformed`。
  - **畸形坐标不得静默跳过**：`X` 后跟 `NAN`/`INF`/空/`1.5.5` 时，取数正则匹配不上，坐标会被**静默丢弃**——等于把未知坐标当「本行无此轴」放行，限幅形同虚设。故须独立校验「轴字 + 其后连续数值样字符」整体是否为合法有限数，畸形即拒。**静默漏放比误拒危险**。
- **换纸失败语义**：`error:90`；`[MSG:[PaperStatus] N]`：0=成功，2=进纸超时，3=回找未定位，4=卡纸，5=缺纸。N≠0 → 暂停任务 + 云端提示用户，不自动重试。
  > ⚠️ `[PaperStatus] N` 经 Telnet 可达**以 §9 的 B1′ 改动为前提**（原路由为 `CLIENT_SERIAL`，S3 收不到）。未改动前 S3 只能靠 `error:90` 判失败、拿不到具体码。
- **大小上限**：单文件 ≤ **512KB（含，即 ≤ 512×1024 字节）**，超一字节直接拒。
  - **按「设备看到的字节」计，不按上位机的字符数**：S3 下载到的、`Content-Length` 与 CRC32 算的都是**磁盘原始字节**，故云端判上限必须量同一个对象。Python 的 `read_text()` 走 universal newlines，会把 CRLF 塌成单个 LF、每行少算 1 字节——实测某 20846 行 CRLF 产物磁盘 524369 字节（超上限 81 字节）被判为未超并放行，回给 S3 的 `bytes` 也少报 20846。512KiB 是 S3 侧 PSRAM 缓冲的硬约束，少算即等于把越限产物放给设备。单行 **254** 字节 payload（Grbl `LINE_BUFFER_SIZE` 的可用值，见 §3）同理按字节计。

### 5.1 下载完整性契约（先下完 + 校验 + 再发）

**放弃「边下边转」**：流式边下边发若中途截断，前半已画在纸上，且提前 EOF 可能不报错——会静默产出「看似画完实则缺半」的纸。故把完整性校验**前移到落笔之前**。

| 方向 | 约定 |
|------|------|
| MCP 服务产出 | 公网能力 URL **必须是 `https://`**；HTTP 响应头给 `Content-Length` + `X-Hutuji-CRC32:<hex>`。Cloudflare Tunnel 的明文回源只允许 loopback，公网直连 `:80 /files/` 必须拒绝 |
| MCP 控制面 | `/mcp` 只允许同机 bridge 通过 loopback 对端连接；服务应用层必须按 ASGI socket 对端地址再判一次，未知/非 loopback 一律 404。不得以 `Host`、`X-Forwarded-For`、`CF-Connecting-IP` 等请求头代替——攻击方可伪造。systemd loopback 监听、安全组不放行 8300、nginx `/mcp` 404 三层仍必须保留；应用层守卫只兜住控制面，不能防 `/files` 绕过 nginx 限速/ACL 直曝。 |
| 校验算法 | **CRC32**只负责发现传输截断/位翻转，**不是认证码**，不能抵抗同时篡改正文与响应头的中间人；公网传输认证由 HTTPS 承担，不上 SHA256。⚠️ **变体两侧必须钉死同一个**（zlib/IEEE，Python `zlib.crc32` ↔ ESP32 侧同变体） |
| S3 侧流程 | 全量下载入 PSRAM（`GetBodyLength()` / `GetResponseHeader()` / `Read()` 循环，模板 `ota.cc:292-321`）→ **先比长度**（快速失败）→ **再比 CRC32** → 全部通过**才**发第一行 |
| 任一不符 | 任务 error，**一个字节都不发写字机**，推送提示（见 §7） |
| URL 时效 | 只需保证**一次成功下载**（几分钟即可），**可用短时效签名 URL**——因重发走 PSRAM 缓存（§4），不再回源 |

部署门禁必须用一份合法临时产物同时验证 `HTTP=200`、`Content-Length`、
`X-Hutuji-CRC32` 与逐字节正文；只比较命令替换后的正文会吞掉尾部换行，也无法证明 S3 必需的响应头仍在。

**PSRAM 缓存生命周期**：校验通过后持有至任务终结（`done`/`error`/`aborted` 均释放）。S3-N16R8 有 8MB PSRAM，512KB 占得起。跨多次重连、跨 ≥60s 换纸期间均须存活。

## 6. 授权前置

- **拦截范围（源码勘察）**：未授权时**只拦 G0–G3 运动**（执行阶段 `check_license()`，见 `GCode.cpp:1104` 注释）；`$I`、`?`、`M...`（含 `M800` 授权本身）**照常执行**。`error:110` = `Error::AuthenticationFailed`（`Error.h:84`）。
- **因此授权态在落笔之前即可知** → `draw` **加授权前置门**：下载校验通过后、发第一条
  G-code 之前，确认当前连接的零位移探测结果为 authorized；未授权则**直接任务 error、
  绘图载荷零字节下发**。探测行自身可能收到 `error:110`，但它固定在抬笔零位移、draw 前。
- `hutuji.status` 暴露 `authorized` 字段 → ② 冒烟第一步即可一眼诊断。
- 未授权 → 走人工 SOP（按 ChipID 算授权码，`M800 P<码>` 写 NVS），**S3 不自动授权**。
- 烧录不覆盖 NVS，授权与 `$` 设置持久；改机头默认需 `$RST=$`（联调 SOP 记录，非 S3 职责）。
- **联调前置条件**：② 冒烟测试前先确认 `status.authorized==true`（或串口 banner 含 `[License] Authorized`）；未授权先走授权 SOP 再开测。授权持久，实际上是**一次性**的。

## 7. 错误处理

| 情况 | 含义 | S3 行为 |
|------|------|---------|
| `error:8` | 换纸进行中，运动行被推迟（实为 `Error::IdleError`，`Protocol.cpp:186`） | **不算失败**，但 **⚠️ 该行已被 `empty_line()` 丢弃、不会排队**——S3 必须**自己重发这一行**，不可假设写字机记着。做法：暂停发送、等换纸行 ok 后**重发被拒的那一行**再继续（`[PaperStatus]` 须先做 §9 B1′ 才可达）。注：写字机侧的 `[MSG:]` 推迟提示有 3s 节流（`protocol_notify_paper_motion_deferred`）且只发 SERIAL，S3 只看得到 `error:8` 本身 |
| `error:11` | 行含非 ASCII 或 payload 超 **254** 字节（见 §3；该行同样被 `empty_line()` 丢弃、从未执行） | 任务 error，上报（MCP 生成端 bug） |
| `error:90` | 换纸失败 | 任务 error，按 `[PaperStatus]` 码提示用户（**依赖 §9 B1′**；未改动前只能报「换纸失败，原因未知」） |
| `error:110` | 未授权 | 停止全部发送，提示走授权 SOP。**正常不应出现**——§6 前置门已挡住。该行同样被 `empty_line()` 丢弃（`Protocol.cpp:195-204`） |
| 其他 `error:NN` | 语法/参数错 | 停止，记录行号，任务 error 上报 |
| **`error: <文本>`** | `$Errors/Verbose=1` 时的等价形态（`Report.cpp:236`），如 `error: Command requires idle state` = 8 | **必须映射回数字码后按本表同一行处置**，不得当作未知错误。只认数字会把 `error:8` 变成整单失败、把 `error:110` 变成未知故障（见 §3）。映射表见 `ParseGrblErrorCode()` |
| `ALARM:n` | 报警 | 停止；一般 `$X`/`$RST` 解锁走人工 SOP。唯一例外是 S3 自己刚发出并标记的 abort reset，只允许一次 §4.1 受限 `$X` 恢复 |
| banner 重放 | 对端复位（欠压等） | 状态机复位，任务 error，上报 |
| TCP 断开 | 链路丢失/对端断电 | 指数退避重连；进行中任务置 error，按 §2.1 分流处置 |
| **下载完整性校验失败** | 长度 ≠ `Content-Length` 或 CRC32 不符 | **不发任何字节**，任务 error 上报（§5.1） |
| **下载超长** | 文件 > 512KB | 直接拒绝，零字节下发 |
| 下载失败/超时/HTTP 非 2xx | 含一次性 URL 已失效（403） | 不发任何字节，任务 error |
| 60s 无 `ok`（换纸行 90s） | 链路异常 | 发 `?` 探活一次（实时旁路，换纸阻塞期亦可响应），仍无则任务 error |
| 任务进行中收到新 `draw` | 并发冲突 | 拒绝，返回 `"busy"`，不排队不抢占 |

## 8. 明确不做

- 不用蓝牙走正式数据链路；WiFi 只走 Telnet 这一条（Grbl WebUI 其余功能不依赖）。
- S3 不做 G-code 生成/编辑/预览，只做校验与转发（哑管道原则）。
- **不改动 Grbl 侧的换纸机械/时序、运动、授权、U1-Grbl 协议逻辑**。消息路由与状态字段的小加固见 §9（口径已于 2026-07-25 放宽）。
- S3 不自动授权；除自身刚发出的 abort reset 外不自动 `$X`；不在已知换纸活动态发 `0x18`；不做行级断点续传。
- S3 不重新下载做重发（用 PSRAM 缓存，§4）、不排队并发任务、不把 abort 当急停。

## 9. Grbl 侧改动清单

> 口径：**换纸机械/时序、运动、授权、U1-Grbl 协议一律不改**；消息路由、状态字段、WebUI 网络层等**不影响商业出图行为**的小加固可接受。**改后必跑 `python D:/Users/zhugu/fz/scripts/agent_gate.py`，overall 非 pass 不得声称完成。**

| # | 文件:行 | 改动 | 为什么必须 |
|---|---------|------|-----------|
| A | `WebUI/TelnetServer.cpp:92` | 新客户端 `available()` 后设 **4 个** socket 选项：`SO_KEEPALIVE=1` + `TCP_KEEPIDLE=10` + `TCP_KEEPINTVL=3` + `TCP_KEEPCNT=3`（~19s 回收） | `MAX_TLNT_CLIENTS=1` + 无 keepalive → 半开死连接独占唯一槽位，S3 重连被 `:98` 拒死（§1.2）。`WiFiClient::setSocketOption(level, option, value, len)` 已暴露（`WiFiClient.h:90`，已核验） |

> ⚠️ **A 条必须设全 4 个参数，只开 `SO_KEEPALIVE` 是无效的**——lwip 默认 `KEEPIDLE=7200s`(2h)/`INTVL=75s`/`CNT=9`，等于 2h11m 后才回收，形同没有保活。同一坑在 S3 侧 M1 代码中已实际发生并修复（`hutuji_pipe.cc` 原仅设 `SO_KEEPALIVE`，注释却写「尽快发现对端断电」）。
| A2 | `WebUI/TelnetServer.cpp:43-74,82,105,153,175` | 文件作用域 `static SemaphoreHandle_t telnetClientMutex = xSemaphoreCreateRecursiveMutex()` + RAII `TelnetClientLock`，在 `begin()`/`end()`/`write()`/`handle()` 四个公开入口顶部各取一次（**只加锁，不改任何路由/状态字/换纸/运动**） | `Telnet_Server::write()` 跑在 loopTask（`Grbl_Esp32.ino:28 run_once` → `grbl_sendf`/`report_*` → `Serial.cpp:380 client_write`），`Telnet_Server::handle()` 跑在 clientCheckTask（`Serial.cpp:120` → `wifi_config.handle()` → `WifiServices::handle()`），两者同优先级同核（`SUPPORT_TASK_CORE`）并发操作**同一个 `WiFiClient` 与同一个 slot**：① `write()` 里 `WiFiClient::write()` 遇非 `EAGAIN` 错误会调 `stop()`（`WiFiClient.cpp:389-443`），`stop()` 把 `_rxBuffer` 置空并释放（`:209-214`），而读侧正在 `available()`/`read()` 的 `WiFiClientRxBuffer::fillBuffer` → `lwip_recv` → `pbuf_free` 中，命中 assert `pbuf_free: p->ref > 0`（上游 bdring/Grbl_Esp32#1364，reporter fork 的修复同样是加 semaphore）；② `clearClients()` 被两个任务并发调用，先 `available()` 再对同一 slot `stop()` + 拷贝赋值，等于在读侧脚下换掉 `_rxBuffer` 智能指针。`MAX_TLNT_CLIENTS=1` 不能规避——两个任务抢的就是同一个槽位 |

> ⚠️ **A2 必须用递归锁**：`handle()` 内 `report_init_message(CLIENT_TELNET)` 会经 `grbl_send` 回到 `write()`，同一任务需重入；普通 mutex 直接自锁死。
> **A2 的 abort 时延口径（不是新增风险）**：持锁期间 `write()` 最坏阻塞约 10s（`WIFI_CLIENT_SELECT_TIMEOUT_US=1s` × `WIFI_CLIENT_MAX_WRITE_RETRY=10`，`WiFiClient.cpp:26-28`），此窗口内 clientCheckTask 取不到字节，`!`/`0x18` 的**提取**被推迟。但 `!`/`0x18` 的**执行**本就在 loopTask 的 `protocol_execute_realtime()` 里，而 loopTask 正是被 `write()` 阻塞的那一个——即无锁时同样停摆。故 A2 不恶化 §4.1 abort 时延上界，仅把「崩溃」换成「已存在的阻塞」。
> **2026-08-02 A2 写路径收口（取代上段 10s 口径，源码未提交/未烧录 → **2026-08-03 supersede：已提交 Grbl `1b56393` 并烧录 COM11，551 行 S4 有纸实测通过，`pbuf_free: p->ref > 0` 本轮未复现**）**：`write()` 不再调用可能阻塞约 10s 的 `WiFiClient::write()`，改为 `send(..., MSG_DONTWAIT)` + **1s 绝对 deadline**；deadline 覆盖 EAGAIN 与每次正 partial write，EAGAIN 重试同时 `COMMANDS::wait(0)` + `vTaskDelay(1)`，完整写出才返回长度，超时/零进展/真实错误则关闭该 Telnet session 并返回 0。这样仍保持 A2 递归锁的跨任务串行化，同时把 loopTask/clientCheckTask 的单次写占用收窄到有界 1s；host 行为回归覆盖正 partial deadline、无效 fd、EAGAIN 让步/看门狗。
| B1′ | `PaperSystem.cpp:802,807,929,1006` | `[PaperStatus] %d` 的 `CLIENT_SERIAL` → `CLIENT_ALL` | §7 换纸失败码（0/2/3/4/5）的唯一来源；不改则 S3 永远收不到，§7 表空转 |
| B2′ | `PaperSystem.cpp:351` | `paper_get_status_str()` 的 `snprintf` 增 `Changing=%s` 字段，取现成 `paper_auto_change_is_running()`（`PaperSystem.cpp:50`，现成 `volatile bool`） | §2.1/§4.1 abort 分流的**唯一跨 session 地基** |
| C′ | `Protocol.cpp:639-672`、`:763`、`:777-779` + `ProtocolDecisionCore.h:287-293` | `cycle_stop` 分支先快照本次停止的状态与意图，再由 `should_resume_segment_underflow()` 判定:**仅**「`Cycle` 中 + planner 仍有 block + 无 `endMotion`/`executeHold`/`motionCancel`/`soft_limit`」的真实断粮才置 `sys.state = State::Cycle` 并 `st_prep_buffer()+st_wake_up()` 续跑;其余停止意图全部落回原停止分支。续跑后 `:777` 不再重复 `st_prep_buffer()` | 恢复期 `?` 回 `<Idle>` 而步进仍在走，S3 侧 `WaitForIdle` 只看一次 fresh Idle 状态行即返回 true → 页尾提前 `M30` 撕在笔下。与 Grbl 自身 cycle-start 路径（`:627-629`）对齐:先置 Cycle 再 prep+wake。**但不得吞掉有意停止**:`motionCancel`（jog/探针取消）与 `soft_limit` 必须照原路进 `Idle`/`Alarm`，否则 abort 与限位保护会被这条恢复路径吃掉 |

**B2′ 的可行性已逐环核验（不再是推断）**：
1. **缓冲够**：`paperStatusHandler`（`WebSettings.cpp:1008`）用 `char buf[64]`，现输出 `Paper=OK MotorEn=On PanelHold=On` 约 34 字节，加 `Changing=Off` 仍富余；`snprintf` 自带边界保护。
2. **任何状态可执行**：`[ESP901]` 注册为 `anyState`（`WebSettings.cpp:1121`），而 `anyState()` **恒返回 false**（`Settings.cpp:6-8`，这些守卫返回的是「是否拒绝」）→ 永不被状态拦。
3. **换纸阻塞期可分发**：换纸中 `protocol_service_during_blocking()` 遍历全部 client 调 `protocol_poll_client()`；其换纸拦截（`Protocol.cpp:184`）**只拦 G0–G3**，`[ESP901]` 落到 `:205` 正常 `execute_line`。
4. **响应回得到 S3**：`ESPResponseStream stream(client, true)`（`ProcessSettings.cpp:615`）用**发命令进来的 client** 构造 → `_client = CLIENT_TELNET`（`ESPResponse.cpp:46`）→ `grbl_send(_client, data)`（`:99`）；`println` 还专为 `CLIENT_TELNET` 用 `\r\n` 行尾（`:55`）。
5. **调用时机安全**：`[ESP901]` 是普通命令会吃 `ok`（§3 单写者），但 Q5/Q8 需要它的时刻正是**重连后、尚未开始发 G-code** 的流控空闲期——恰好契合。

**明确不改**（评估后否决）：
- `report_realtime_status()`（Report.cpp:583）——固定 `char status[200]` + 一路无边界 `strcat`，加字段有栈溢出风险。
- `report_state_text()`——状态字 `<Idle|…>` 是 Grbl 协议核心，现役商业上位机与 ESP3D WebUI 均依赖，加非标状态会破坏商业出图。
- 换纸消息全量改 `CLIENT_ALL`（63 处）——无必要，徒增 Telnet 流量；v0 有 `?` + `[ESP901]` 已够。

**留档备用**：`Protocol.cpp:257-266` 有 BT 专属的 400ms 主动状态保活（「部分上位机只等状态行、不主动发 `?`」），**Telnet 无此待遇**。若 ② 实测 S3 在换纸长阻塞期判死，可考虑同类小改扩至 Telnet。
