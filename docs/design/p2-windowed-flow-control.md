# P2 设计：窗口化流控（character-counting）

> 状态（2026-08-18 治理复核）：**S1/S2/S3 已实施并烧录；S4 的容差回调已在代码里生效**
> （`mcp-server/hutuji_mcp/gcode_gen.py` `LINESIMPLIFY_TOLERANCE_MM = 0.05`）。本文设计的窗口化
> character-counting 方案已落地；当前仍缺 S4 纸面画质照片证据与**自适应 `Bf` 反压**（明确缓办，TODO
> 在当前已推的 xiaozhi `dcbec6f` 对应 `hutuji_job.cc`）。本文不把设计已落地写成 S4 验收通过。
> 自适应 `Bf` 反压仍明确缓办；当前源码以 `dcbec6f` 的 `hutuji_job.cc` 符号和 TODO 为准，不在本设计文档钉外仓行号。
> 目标是把 Telnet 往返延迟藏在电机运动时间之后，从而允许把 `linesimplify` 容差降回 0.02–0.05mm 而不回到「笔走停停」。
>
> 前置阅读：`docs/protocol.md` §3（现行逐行契约）、`docs/kxnx/findings/09-flow-control-deep.md`（奎享逆向）。
> 本文取代 findings/12 里那份 4 条要点的草稿。

## 0. 三方证据来源（本文每条结论都可溯源）

| 来源 | 位置 | 用途 |
|---|---|---|
| **Grbl 官方参考实现** | `D:/GitHub/inkscape_px/Grbl_Esp32/doc/script/stream.py`（MIT，Sungeun K. Jeon 2012-2017） | 算法权威原型 |
| **本机固件源码** | `D:/Users/Grbl_Esp32/Grbl_Esp32/src/`（`Serial.cpp` / `WebUI/TelnetServer.*` / `WebUI/InputBuffer.*` / `Report.cpp`） | 缓冲容量与反压行为 |
| **奎享逆向** | `D:/GitHub/kxnx/kx/tools/dump_agent/decompiled_full/.../machine/f/b.java`、`machine/f/f.java`、`machine/grbl/a.java` | 商业现役实现的取值与取舍 |
| **我方现状代码** | `xiaozhi-esp32/main/boards/lichuang-dev/hutuji_pipe.cc` / `hutuji_job.cc` | 改造切口 |

> 说明：GitHub / 官方文档站在本机被网络策略拦截（WebFetch 失败），
> 故官方算法取自**本地 inkscape_px 仓内的 Grbl 原版 `stream.py`**，
> 与 gnea/grbl 官方 `doc/script/stream.py` 同一文件（含 MIT 头与 CHANGELOG）。

## 1. 为什么要做（以及为什么可以先不做）

当前是**逐行等 ok**：发一行 → 等 `ok` → 发下一行。每行都要付一次 WiFi 往返。

几何侧已经先做了缓解（`linesimplify` 0.2→0.1mm），但**已无余量**：

| | 步长中位数 | 最小步长 | 行数 |
|---|---|---|---|
| 未简化 | 0.100mm | 0.081mm | 6407 |
| 0.1mm（本文成文时的生产值） | 2.203mm | 1.273mm | 279 |
| **奎享真实产物** | **0.254mm** | **0.0240mm** | 673 |

奎享靠窗口化流控**保住了 0.024mm 的曲率细节**。再调容差只会在「棱角」和「卡顿」
之间来回换，不会同时变好。

**先不做的理由**：这动的是 `docs/protocol.md` §3 契约（明文写着「无 character-counting，
禁止预发抢跑」），需两侧同步评审 + 实机验证。成文时 0.1mm 已可出图，
**触发条件是出现「小字/曲线不好看」的实际反馈**。

> 2026-07-30 更新：S3 已落地后生产容差已回降到 **0.05mm**（`gcode_gen.py`），
> 本节「当前 0.1mm」只作成文时的历史读数，不再是现状。

## 2. 官方参考算法（照抄，不自创）

`stream.py` 的核心循环（原文，仅加注释）：

```python
RX_BUFFER_SIZE = 128
g_count = 0          # 已确认条数
c_line = []          # 在途各行的字节数（FIFO）

for line in f:
    l_count += 1
    l_block = re.sub('\s|\(.*?\)','',line).upper()   # ← 去所有空白 + 去括号注释 + 转大写
    c_line.append(len(l_block)+1)                     # ← +1 = 换行符
    while sum(c_line) >= RX_BUFFER_SIZE-1 | s.inWaiting():
        out_temp = s.readline().strip()
        if out_temp.find('ok') < 0 and out_temp.find('error') < 0 :
            print "MSG: ..."                          # ← 非应答行（[MSG:]/banner）不消耗窗口
        else :
            if out_temp.find('error') >= 0 : error_count += 1
            g_count += 1
            del c_line[0]                             # ← ok 与 error 都释放窗口
    s.write(l_block + '\n')

# 收尾排空：等所有在途应答回来
while l_count > g_count :
    out_temp = s.readline().strip()
    if ... else :
        g_count += 1
        del c_line[0]
```

从官方实现提取出**四条必须照抄的规则**（我第一版设计稿漏了三条）：

| # | 规则 | 漏掉的后果 |
|---|---|---|
| **R1** | 计数单位是 `len(line)+1`（含换行符） | 少算 1 字节/行，21 行在途就少算 21 字节，逼近上限时溢出 |
| **R2** | **`error` 也释放窗口**（`del c_line[0]` 在 ok/error 两分支共用） | 只在 ok 时释放 → 遇 error 窗口永久少一格，最终死锁 |
| **R3** | **非应答行不消耗窗口**：`[MSG:...]`、banner、`<...>` 状态行都不是应答 | 把 `[MSG:]` 当 ok → 在途计数提前释放 → 真正溢出 |
| **R4** | **收尾必须排空**：发完最后一行后还要等 `l_count - g_count` 个应答 | 提前判「出图完成」→ 实际还在画 → 过早报完成 / 过早释放 buffer |

### 2.1 官方的两个例外（二次深挖补充）

- **settings 模式例外**（`stream.py:129-149`）：`-s/--settings` 写 EEPROM 时**退回逐行等 ok**，
  且行只做 `line.strip()`，**不做**激进规范化——因为 EEPROM 写周期会关闭串口中断。
  含义：换纸行、设置类命令应保留逐行语义，不能一刀切窗口化。
- **G02/G03 例外**（`simple_stream.py:5-12`）：圆弧把短直线段直接注入 planner，弧期间
  **可能没有 `ok` 响应**。我方 profile 当前不产圆弧，但 `validate_gcode` 支持 G2/G3 限幅；
  若未来接受外部含弧 G-code，窗口化计数须确认弧期间不被「无 ok」假死。

### 2.2 行规范化口径差异（窗口计数的隐性偏差）

| 实现 | 去空白 | 剥 `;` 注释 | 剥 `(...)` 注释 | 转大写 | 换行计入窗口 |
|---|---|---|---|---|---|
| 官方 `stream.py` 出图 | ✅ | ✅ | ✅ | ✅ | `len+1` |
| 官方 settings | 只 strip | — | — | — | 逐行 |
| inkscape_px 控制台 | 只 strip | 只剔整行 | 只剔整行 | — | `len+1` |
| **我方 S3 转发** | strip + 剥 `;` | ✅ 剥行内 | ❌ 不剥 `(...)` | — | `len+1` |

我方只 strip + 剥 `;`，**不剥 `(...)`、不转大写、不去空白**。后果：我方窗口字节计数
**高估**实际占用（含空格/注释残段），偏保守、浪费容量，但**不会**因此溢出——方向是
安全的。若要与官方窗口语义完全对齐，需在 `ParseLines` 里加同样的规范化；当前不必，
只须知道**我方窗口比官方同值更松**。`protocol.md` 的「不解析注释（从严）」是与官方
激进规范化**有意偏离**的安全策略，应视为设计选择而非缺陷。

R3 对我们尤其关键：`docs/protocol.md` §7 列了一堆 Telnet 能收到的非应答内容
（banner 重放、`?` 的 `<...>` 状态行）。我方 `hutuji_pipe.cc` 的接收泵已经分类处理，
但**窗口化后必须确保只有 ok/error 两类才 `Give` 计数信号量**。

### 与奎享实现的一致性核对

奎享 `machine/f/b.java`：

```java
this.e += (long)(((a)object).b() + 1);        // :59   在途 += 行长 + 1        → 与 R1 一致
...e + (long)a2.b() + 1L < (long)b.g()        // :70   下一行塞得进才继续发
((b)object).e -= (long)(a2.b() + 1);          // :144  收到应答 -= 行长 + 1
```

三处算术与官方 `stream.py` 完全同构（2026-07-28 二次深挖复核）。

**窗口值 `g()`**（反编译全树仅 4 个 override + 继承链逐一核）：
Grbl / ESP32Grbl = **100**（主树 `grbl/a.java:315-317`）；Marlin / KAT / K_MT = **96**
（主树 `marlin/a.java:233-235`，KAT 无 override 直接继承）；EBB = **-1**（无限，`ebb/a.java:93-95`，两树同号）。

> **勘误**：本文与 findings/09 早先写「KAT 窗口 = 1」是**错的**。反编译里 KAT 没有
> 自己的 `g()`，从 Marlin 继承 = 96。「逐行单步」的真实来源是 `f.java:26` 把
> `任务强制 || Settings.singleStep` 传给 `b`，由 `b.java:66` 的「在途非空即停」
> 退化成窗口 = 1 行，**与机型无关**。原先那句「奎享自家机型最保守所以窗口=1」的
> 解读不成立，应改为「窗口=1 是 singleStep 开关，任何机型都可开」。

**R2 的精确化（重要取舍差异）**：奎享 `b.java` 的 `e -=` **只在 OK 分支**（:144）。
Error / Resend 走的是 `f()`「**清空全部在途并停止整个 job**」（:149-155），
**不是**官方 R2 的「error 也逐条 -1 释放窗口」。奎享用更粗暴的「error 即整批作废」
规避了死锁。我方 `TakeResponse` 按官方 R2「ok/error 都消费一条」——因为**我方遇到
非换纸 error 本来就严格中止任务**（与奎享「整批作废」结果等价），窗口计数在任务
终止时被整体丢弃，不存在「error 不释放导致窗口收紧」的长期漂移。两者殊途同归，
但**不能引用奎享 error 路径作为「error 也释放窗口」的依据**。

**reconnect 策略差异**：奎享 `FirmwareEventReconnected` → `b.d()` **重发在途队尾一条**
（`b.java:83-88`），假设它可能没送达，其余靠后续 ok 对齐。我方 §4.1 选择
「连接断开即 `DrainResponses()` + Job 任务置 error」的**全清重来**，更保守，
规避了奎享「补发 vs 迟到 ok 双计数」的推断竞态。

**旁路命令**：奎享设置/一次性命令（`G92`/`$$`/回零）走 `machine.c.a(String,bool)`
直连 transport，**完全绕过窗口**（`c.java:100-118`）；实时字符（`?`/`!`/`~`/0x18）
走 `g.a(byte)` 字节旁路。我方每次首画/重画前同样先等 fresh `Idle` + 100ms，再旁路固定
`G92 Z0`，随后窗口化发送下载文件；该旁路不改变“下载文件必须拒绝 `G92`”的安全规则。

## 3. 窗口值：本机缓冲链路的真实约束

Telnet 数据到达 Grbl 解析器要过**两级缓冲**，两级的反压行为**不一样**：

```
S3 → [TCP/lwip] → ①Telnet 环形缓冲 1200B → ②client_buffer[TELNET] 2048B → 行缓冲 256B → 解析
                   TelnetServer._RXbuffer      InputBuffer._RXbuffer         LINE_BUFFER_SIZE
```

| 级 | 容量 | 源码 | 满了会怎样 |
|---|---|---|---|
| ① | **1200** | `WebUI/TelnetServer.h:33` | **有反压**：只从 socket 读 `min(available, 1200-已用)`（`TelnetServer.cpp:159-171`），剩下的留在 lwip → TCP 窗口关闭 → 发送方阻塞。**安全** |
| ② | **2048** | `WebUI/InputBuffer.h:55` | **无反压**：`getClientChar()` 搬字节时**不检查** ②的余量（`Serial.cpp:160-165`），而 `InputBuffer::write()` 满了直接 `return 0` **静默丢字节**（`InputBuffer.cpp:48-63`） |
| 行缓冲 | 256 | `Protocol.h:33-34` | 超长回 `error:11` |

### 一个必须知道的固件不对称（新发现，决定窗口不能省）

`getClientChar()` 对串口有空间检查，对 Telnet **没有**：

```cpp
// Serial.cpp:134-140  串口：先问 client_buffer 有没有空位
if (client_buffer[CLIENT_SERIAL].availableforwrite() && (res = Uart0.read()) != -1) { ... }

// Serial.cpp:161-164  Telnet：不问，直接搬
if (WebUI::telnet_server.available()) {
    *data = WebUI::telnet_server.read();      // ← 从①取出（①的计数已减）
    return CLIENT_TELNET;                      // ← 随后写②，若②满则静默丢弃
}
```

**含义**：字节一旦离开①就没有退路。若②满，字节**凭空消失**——不回错误、不报警，
表现是画面残缺或 `error:NN`（残行拼接成非法指令）。这是最危险的失败模式，
也是「逐行等 ok」契约当初写死的根本原因（一次只允 1 行在途，②永远塞不满）。

**②何时会满**：主循环持续从②取字节拼行，②只在主循环停摆时堆积。而主循环停摆
有两个原因——planner 满（250 块，此时 `ok` 也不回，窗口自然收紧，安全）；
或主循环在执行**换纸**等长阻塞动作。**后者正是危险窗口**：换纸期间主循环不动，
若此刻在途还有字节涌入，②就会堆积直到静默溢出。所以换纸行前排空在途不是
「稳妥起见」，是**防静默丢字节的硬约束**（见 4.3 第一条）。

### 取值

| 候选 | 依据 | 结论 |
|---|---|---|
| 127 | 官方 `RX_BUFFER_SIZE-1`，为原版 128B 缓冲设计 | 太保守（我们①有 1200） |
| 100 | 奎享 Grbl 档 | 同上，且是给原版设计的 |
| **512** | ①的 43%，②的 25%；按每行 24 字节 ≈ **21 行在途** | **建议起步值** |
| 800 | ①的 67% | 实测稳定后的上调目标 |
| >1200 | 超①容量 | **禁止** |

21 行在途足以盖住 20–40ms 往返（每行执行 ≥ 数十 ms）。**注：20–40ms 已被实测证否，实测下界 77ms / p50 117–134ms（`docs/troubleshooting.md` 笔控一节），短段图下 21 行在途盖不住，勿照此结论定窗口。**

> **额外安全网（我们比奎享多的手段）**：`?` 的 `Bf` 第二个数对 Telnet 客户端返回
> `telnet_server.get_rx_buffer_available()`（`Report.cpp:605-607`），即①的实时余量。

## 3.1 实机实测（2026-07-27，COM3 串口路径）

三项实测，其中两项**修正了本文早前的判断**。

### 实测 A：`Bf` 第二数在串口路径恒为 128，不是 1200

```
<Idle|MPos:40.000,77.000,0.000|Bf:249,128|FS:0,0|Pn:0>
```

早前记录的「实测 `Bf:249,1200`」**是错的**——串口路径下 `Report.cpp:616` 走
`client_get_rx_buffer_available(CLIENT_SERIAL)`，而 `RX_BUFFER_SIZE=128`
（`Serial.h:26` 的 256 是另一处定义，实际报告值为 128）。

**1200 只会在 Telnet 客户端自己查询时出现**（`client == CLIENT_TELNET` 分支）。
即：S3 从 Telnet 读到的 `Bf` 第二数才是①的余量，PC 从串口读到的是串口缓冲余量。
**两者不可混用**。自适应反压这个手段只有 S3 侧能用，PC 侧调试看不到。

### 实测 B：planner 反压真实存在且可观测

连灌 40 行 G1 期间采样：

```
 637ms  <Run|...|Bf:222,128|FS:8000,0>     ← planner 从 249 掉到 222
1201ms  <Run|...|Bf:236,128|FS:8000,0>     ← 回升
1761ms  <Run|...|Bf:249,128|FS:0,0>        ← 排空
```

第一数（planner 块余量）确实随灌入下降。这印证了 §3「planner 满会推迟回 ok，
天然反压」的机制**真实可观测**，不再是纯推断。

### 实测 C：串口路径下窗口化零收益（重要的反面证据）

同一份 183 行 G-code，逐行 vs 窗口 512 双跑对比：

| | 逐行（窗口=1） | 窗口=512 |
|---|---|---|
| 发送/确认 | 183 / 183 | 183 / 183 |
| 在途峰值 | 1 行 | **1 行**（跑不起来） |
| 纯发送耗时 | 39.93s | 39.97s |
| 总耗时（含执行完） | 40.38s | **40.42s** |

**窗口开了但在途峰值仍是 1**——因为串口 `ok` 只要 6ms 就回来了，
下一行还没准备好发，窗口根本填不满。瓶颈 100% 是电机运动（F8000 下 183 行需 40s）。

**这条必须写进文档，因为它划定了 P2 的适用边界**：

- 窗口化的收益 = `min(往返延迟, 单行执行时间) × 行数`
- 串口往返 6ms « 单行执行 ~220ms → **收益 ≈ 0**
- WiFi 往返 **实测下界 77ms / p50 117–134ms**（2026-07-30，原写 20–40ms 为估计值已证否，见 `docs/troubleshooting.md` 笔控一节）→ 有收益，且**比原估计大 3–6 倍**；但上限仍是每行省下往返那部分

推论：**P2 不会让出图变快**，它的价值是**允许把容差降回 0.05mm**
（行数从 279 涨到 ~500 时仍不卡顿）。若把 P2 当「提速方案」，预期就错了。

> 验证边界：以上三项均为 **COM3 串口路径**实测。Telnet 路径的往返延迟、
> ①的余量变化、②是否会溢出，**均未实测**——需 S3 固件改造后才能测（见 §5 S3 阶段）。
> 出图期周期性 `?`，若余量掉到 300 以下就临时收窄窗口。建议 S3 阶段作为兜底。
> 实测 `Bf:249,1200` 的 1200 与 `TELNETRXBUFFERSIZE` 吻合，两条证据一致。

## 4. 改造方案

### 4.1 Pipe 侧：EventGroup → 计数信号量 + 应答队列

```cpp
// hutuji_pipe.h 新增
SemaphoreHandle_t ok_count_ = nullptr;      // 计数信号量，每个 ok/error 给一次
QueueHandle_t     resp_queue_ = nullptr;    // 应答明细（error 码/文本），深度 16

/** 消费一个应答。窗口化流控用：返回值代表「这一条」的结果，不是「有没有」。 */
WaitResult TakeResponse(uint32_t timeout_ms, int* error_code = nullptr);
```

接收泵把 `xEventGroupSetBits` 换成 `xSemaphoreGive(ok_count_)` + `xQueueSend(...)`。

**必须严格照抄 R3**：接收泵里 `ok` 与 `error` 两分支才 `Give` + `Send`；
`[MSG:]` / banner / `<...>` 状态行分支**绝不**碰这两个原语。

**兼容性**：`WaitResponse`/`WaitOk` 保留，实现为 `TakeResponse` 的包装
（等一条 + 读明细），`status`/`pen_test`/`abort` 等现有单命令调用点不用改。

**连接重建时必须清空**：`ConnectOnce()` 成功后 `xQueueReset(resp_queue_)` +
把 `ok_count_` 抽干。否则上次连接遗留的 ok 会被当成本次的。

### 4.2 Job 侧：预解析 + 在途队列（照抄官方 `stream.py` 结构）

现状（改造前）`StreamToGrbl()` 是**边扫 buffer 边发**（`hutuji_job.cc` 改造前旧 573-604 段，现已按本设计窗口化重写），无 peek 能力。

```cpp
// 第一步：预解析（对应官方逐行读文件）
//   buffer_ → std::vector<std::string> lines_   // 已剥注释/空行，走 PSRAM
//   代价：一张图 ~300 行 × ~30 字节 ≈ 10KB

// 第二步：官方 stream.py 的 c_line / g_count 结构
std::deque<size_t> c_line;          // 在途各行字节数（含 +1，R1）
size_t next_ = 0;                   // 下一条待发
constexpr size_t kWindow = 512;

while (next_ < lines_.size()) {
    // —— 灌：对应官方 while 条件「塞得下才继续发」
    size_t need = lines_[next_].size() + 1;                 // R1
    if (c_line_bytes() + need < kWindow || c_line.empty()) { // c_line.empty() 保证至少发一条
        // —— 换纸行特殊处理：见 4.3
        if (!pipe.SendLine(lines_[next_])) → error
        c_line.push_back(need);
        ++next_;
        continue;
    }
    // —— 收：对应官方 del c_line[0]，ok 与 error 都释放（R2）
    WaitResult wr = pipe.TakeResponse(timeout, &err);
    if (wr == Ok || wr == Failed) {
        c_line.pop_front();
        ++lines_sent_;                                       // R2：已确认数
    }
    // 非应答行由 Pipe 内部消化，不会到这里（R3）
}

// —— 收尾排空（R4）：发完不等于画完
while (lines_sent_ < lines_.size() && !c_line.empty()) {
    WaitResult wr = pipe.TakeResponse(timeout, &err);
    if (wr == Ok || wr == Failed) { c_line.pop_front(); ++lines_sent_; }
}
```

### 4.3 必须保留的现有语义（逐条对照，不能丢）

| 现有行为 | 位置 | 窗口化下怎么办 |
|---|---|---|
| **换纸行独占** | `LooksLikePaperLine` | 发换纸行前**先把在途全部排空**（等 `c_line` 空），换纸行单独走逐行（窗口临时=1）。理由：换纸行 ok 要等数十秒，且期间禁发 G0–G3 |
| **`error:8` 重发本行** | `WaitResult::Deferred` | 窗口化下**无法定位被拒行**（应答不带行号）。靠上一条保证：`error:8` 只可能出现在逐行（换纸）模式 |
| **暂停停在行边界** | `stream_mutex_`+`paused_` | 暂停=**停止灌新行**，但**必须继续收在途 ok**（否则 `c_line` 永不释放，恢复后计数错乱）。奎享的机制**不同**，见 §8.3 |
| **暂停冻结超时** | `WaitWhilePaused()` | 同现状，在 `TakeResponse` 分段等待里保留 |
| **pause/resume 笔位** | `RequestPause/Resume` | 当前只发实时 `!`/`~`，不注入普通 Z 行：Hold 中 Z 行只排入 planner，且其 `ok` 会与主流配对漂移。S3 开窗后保持该语义 |
| **abort 抬笔恢复** | `PrepareAbortReset` + Pipe 探活状态机 | `!` 后观察 `Hold/Idle` 再 reset；reset 清空应答和 planner 后，在受限 `$X` 恢复路径显式 Z=0，再等 fresh `Idle`。它发生在旧窗口已销毁后，不计入 `c_line` |
| **abort** | `abort_requested_` | 检查点从「每行」变成「每次灌/收循环」；**abort 后也要排空或显式清零 `c_line`**，否则在途计数残留 |
| **ALARM 立即中止** | `GetGrblState()` | 同现状，循环内检查 |
| **单写者纪律** | §3 契约 | **不变**：`status` 仍只发 `?`（实时旁路，不吃 ok） |
| **进度推送** | 5s 定时 | 用 `lines_sent_`（已确认数），**不是** `next_`（已发数）——已发≠已画 |

### 4.4 超时语义的变化（易错点）

逐行模式下「30s 没 ok」= 这一行卡住。窗口化下在途有多条，超时含义变成
「**最老那条**等了 30s」。实现没区别（FIFO），但**日志要报 `c_line` 队首对应行的内容**，
否则排障时行号是错的。

## 5. 分阶段落地

| 阶段 | 内容 | 验收 |
|---|---|---|
| **S1** | ✅ Pipe 侧应答队列 + 兼容包装 | 已编译、烧录 COM9、Telnet 连通（§5.1） |
| **S2** | ✅ Job 侧预解析成零拷贝行索引，窗口=1 | 旧/新解析对真实产物与边界集逐字一致；IDF 构建、烧录、45s 启动观察通过（§5.2） |
| **S3** | ✅ 窗口=512（`kStreamWindowBytes` / `kWindow`），应答队列按最短非空行最坏在途数推导为 255，换纸行前排空 `c_line`（`paper_pending`） | 历史镜像已烧录并通过 B-12 409 行连续出图；2026-08-06 容量修复 host 回归和 ESP-IDF v6.0.1 构建通过，**新镜像未烧录** |
| **S4** | 🟡 容差已回调 0.05mm（`gcode_gen.py`）；自适应 `Bf` 反压**未做**（`hutuji_job.cc:2322-2323` TODO） | 缺 0.05mm 真实图样的**有纸**出图证据：曲线细节改善且无停顿 |

S2 是**关键安全阀**：窗口=1 时行为必须与现状完全一致，S3 出问题一个常量回退。

### 5.1 S1 已实施（2026-07-28，已编译烧录）

**改动**（`hutuji_pipe.h` / `hutuji_pipe.cc`，未动 `hutuji_job.cc`）：

| 项 | 改前 | 改后 |
|---|---|---|
| 应答载体 | `EventGroupHandle_t response_events_` 单 bit | `QueueHandle_t resp_queue_`，深度由窗口推导为 255，元素 `{WaitResult, error_code}` |
| 新增 API | — | `TakeResponse(timeout, err*)` 消费一条；`SetWindowed(bool)`；`DrainResponses()` |
| 旧 API | `WaitResponse` / `WaitOk` | 保留，改为 `TakeResponse` 的包装（行为不变） |
| 队列满 | 不适用 | 容量覆盖 512B 窗口内最短非空行形成的最大 255 条在途应答；满仍告警，但正常窗口不得触发 |

**`SendLine` 的隐患已按开关处理**：原先每次发送前 `xEventGroupClearBits` 清残留。
现在由 `drain_on_send_` 控制——**默认 true = 逐行模式**（发前 `DrainResponses()`，
与改前逐字等价）；`SetWindowed(true)` 后置 false，队列里的应答成为「在途」合法凭据。
S1 阶段没有任何调用点开这个开关，故**行为与改前完全一致**。

**接收泵天然满足 R3**（`hutuji_pipe.cc:758-783`）：只有 `line == "ok"` 与
`line.rfind("error",0)==0` 两个分支调 `PushResponse`；`[VER:` / `Grbl ` banner /
`<...>` 状态行各自 `return`，不入队。R3 无需额外代码。

**清空点**：连接断开（`PipeTask` 重连前）、检测到对端 banner 复位（`ProcessLine`）
两处由 `xEventGroupClearBits` 换成 `DrainResponses()`，语义对齐。

**验证**：
- 编译通过（IDF 6.0.1，`hutuji_pipe.cc.obj` 无 warning），产物 `build/xiaozhi.bin`
  时间戳 00:40 新于源码 00:32（已核验非 `release.py` 假绿：先移开 releases zip）
- 烧录 COM9（S3 板，CH340K）成功，Hash verified
- 启动日志正常：`Project name: xiaozhi` / `ESP-IDF v6.0.1`，Pipe 连上写字机并
  收到 `<Idle|MPos:115.000,137.000,0.000|Bf:249,1200|FS:0,0|Pn:0>`

**这条日志顺带证实了 §3 的关键前提**：S3 从 Telnet 读到的 `Bf` 第二个数是
**1200**（= `TELNETRXBUFFERSIZE`），而 PC 从串口读到的是 128。两者不同，
`Report.cpp:605-607` 的 per-client 分支得到实机确认——S3 可以用这个数做自适应反压。

### 5.2 S2 已实施（2026-07-28，已编译烧录）

**INTENT**：改造前 `StreamToGrbl` 边扫描 PSRAM buffer、边剥注释/空白、边逐行发送；
S3 窗口化需要在发送前知道「下一行长度」（peek）；本设计要求 S2 先预解析、但窗口保持 1，
输出行序列与逐行时序不得改变。

第一版实现按草稿用了 `std::vector<std::string>`，编译烧录后复核内存配置时发现一个
**必须修正的惊喜**：`CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=2048` 会把每行约 30 字节的
字符串堆分配强制放内部 RAM。512KB G-code 上限按奎享真实平均行长 20.8 字符估算，
可含约 24,075 行，即约 **705KB 内部 RAM 小分配**，必然 OOM（ESP32-S3 内部 RAM 约
512KB，且 WiFi/LVGL/音频已占大半）。不能把「测试小图能跑」当成量产安全。

**最终实现**：`std::vector<LineSpan>`，每个元素只有 `{uint32_t offset,len}`（8 字节），
指向原 PSRAM `buffer_`：

- 最坏约 24,075 行 × 8 = **188KB**；vector 的大块存储超过 2048 阈值，落 PSRAM
- 解析阶段零逐行字符串分配；仅发送当前行时构造一次 `std::string`
- `LineAt(span)` 返回 `std::string_view`，S3 可直接读取 `len` 做 R1 的 `+1` 窗口计数
- 删除已无调用的 `CountLines()`，`lines_total_ = spans.size()`，空行/纯注释行不再虚增进度

**逐字等价验证**：把改造前拷贝字符串规则与改造后边界移动规则各自实现成独立脚本，
对以下输入逐行比较：

| 输入 | 结果 |
|---|---|
| hutuji 真实 14 行产物 | PASS，14==14 |
| 奎享 `2.gcode` / `10.gcode` | PASS，各 946==946 |
| CRLF、连续空行、纯注释、行内注释、Tab、尾部无换行边界集 | PASS |
| 空文件 / 只有换行 | PASS |

**固件验证**：
- IDF 6.0.1 编译成功；`hutuji_job.cc.obj` 时间戳 01:43 新于源码 01:37，
  `xiaozhi.bin` 01:43，确认不是 `release.py` zip 假绿
- 烧录 COM9 成功，所有分区 `Hash of data verified`，RTS 硬复位完成
- 启动串口自证：`Project name: xiaozhi`、编译时间 `Jul 28 2026 01:42:37`、
  `ESP-IDF v6.0.1`；7 个 hutuji MCP 工具注册；45 秒内无 assert/panic/Guru/OOM
- 本仓门禁：86 pytest + e2e + docs 全 pass

**TWINS**：全 `main/boards/lichuang-dev` 搜索同类手写行扫描、
`vector<std::string>` 和循环内 `push_back` 小分配——除本次 `LineSpan` 索引外无其它同类点。

**2026-07-28 补充实机回归**：官方小智消息 API 已经通过云端路径触发 `draw/status/abort`。
完整运动任务先观察到 `streaming/Run`，未出现 `done/Run`，最终为 `done/Idle`、
`MPos=(100,100,0)`、`repeat_available=true`。这证明 S2 当前窗口=1 的下载、解析、逐行转发和
物理完成判定闭环；仍不等于 App 语音对话、纸面笔迹、换纸或 S3 窗口化已验收。

完成判定另修复一处与 P2 R4 同类的旧状态竞态：最后一行 `ok` 只代表进入 planner，
`WaitForIdle()` 现在记录 `status_report_seq_`、发送 `?`，只接受**之后新解析**的 `Idle`。
旧实现固定 sleep 150ms 后可能复用历史 `Idle`，形成 `done/Run`。

## 6. 需要同步改的契约与门禁

- `docs/protocol.md` §3：「无 character-counting，禁止预发抢跑」需改写为
  「窗口化，窗口 N 字节，换纸行排空后逐行」。**这是 ②③ 共同契约，改动需两侧同步评审**。
- 本仓门禁：`python scripts/agent_gate.py`。
- 改 xiaozhi 侧**不触发** fz 门禁；但若为此动 Grbl 源码（本方案不需要）则必跑 fz。

## 7. 明确不做

1. **不改 Grbl 固件**：缓冲已足够，无需动固件侧。
2. **不放宽 `error:NN` 中止**：奎享**同样中止**（`grbl/a.java:82-101` 发 `FirmwareEventError`
   → `f/b.java:155` 清空双队列）。此前文档写「奎享记录不中断」是错的，见 §8.6。
   差别只在它有 `error:120` 静默白名单与 `error:9` 弹框解锁；我们无人值守不能弹框。
3. **不改硬暂停**：我们 `!` 立即减速比奎享软暂停更符合用户直觉。
4. **不做行级续打**：奎享文件级，我们 `repeat` 整幅重画已够。
5. **窗口不超 800**：`>1200` 超①容量，必然静默丢字节。

## 8. 证据边界

| 结论 | 强度 | 依据 | 仍未验证 |
|---|---|---|---|
| R1–R4 算法规则 | **权威** | 本地 `stream.py`（= 官方 gnea/grbl `doc/script/stream.py`，MIT）+ 奎享 `b.java:59/70/144` 算术同构 | 无 |
| 窗口=512 与窗口=1 语义等价 | **实测** | COM3 183 行 A/B：确认数 183==183、错误 0、非应答干扰 0（§3.1） | 仅串口；Telnet 未测 |
| planner 反压真实存在 | **实测** | 灌数据时 `Bf` 第一数 249→222（§3.1） | 未测到逼近 0 的极端 |
| **串口路径窗口化零收益** | **实测** | 40.38s vs 40.42s，往返 6ms 完全被运动掩盖（§3.1） | — |
| 串口 `Bf` 第二数恒 128 | **实测** | 6 次采样全 128；`Report.cpp:55` `DEFAULTBUFFERSIZE=64` 不匹配，实为 `client_get_rx_buffer_available` 结果 | — |
| **Telnet `Bf` 第二数 = 1200** | **实测** | S3 侧抓到 `<Idle|...|Bf:249,1200|...>`（S1 烧录后首条应答，§5.1） | 未在灌数据时观察下降 |
| ①容量 1200、②容量 2048 | 源码 | `TelnetServer.h:33`、`InputBuffer.h:55` | 未做溢出实测（静默丢字节，需长文件+CRC 比对写字机实收） |
| ①有反压、②无反压 | 源码 | `TelnetServer.cpp:159-171`、`Serial.cpp:160-165`、`InputBuffer.cpp:48-63` | 逻辑闭环 |
| 换纸期是②溢出的危险窗口 | **推断** | 主循环阻塞 → ②只进不出（§3 推理） | 未实测（换纸期灌数据风险高，不建议实测） |
| 512 起步值 | 工程判断 | ①的 43% | 非最优值，S3 定夺 |
| 窗口化能消除 WiFi 卡顿 | **推断** | 原理 + 奎享现役产品间接证据 | **S3 必须走 Telnet 实测**；串口实测已证明不能外推 |
| 奎享窗口值 Grbl=100/Marlin=96/KAT 继承 96/EBB=-1 | **强** | 反编译全树 4 个 `g()` override + 继承链（§2 二次深挖） | CFR 泵循环控制流重构不可考（算术可靠） |
| 奎享 error/resend=整批作废，非逐条释放 | **中** | `b.java:144/149-155`（与官方 R2 取舍不同，见 §2） | — |
| inkscape_px 控制台窗口化实现存在但 error 抛异常 | **中** | `serial_runtime.py:130-203`，窗口默认 120B，无测试覆盖 | 不能当测试完备参考 |

### 8.0 本地参考仓 inkscape_px 的两套发送器（2026-07-28 二次深挖新增）

`inkscape_px` 提供了**可直接对照的 Python 窗口化实现**，此前文档完全未引用：

| 实现 | 位置 | 流控 | 窗口值 |
|---|---|---|---|
| Inkscape 插件 | `inkscape-clean/share/extensions/paixi_runtime/grbl_serial_ops.py:128-157` | 逐行 `write_and_wait` | 1（逐行） |
| 后台控制台 | `inkscape-clean/tools/paixi_runtime/serial_runtime.py:130-203` | **字节窗口化** | **默认 120 字节** |

`serial_runtime.send_program` 与官方 `stream.py` 同构：`line_bytes = len(text)+1`（R1），
`inflight_bytes + size <= buffer_bytes` 才发（窗口判定），收到 `ok` 才 `inflight.pop(0)` 释放。
**但它与官方 R2 不同**：收到 `error:`/`ALARM:` 直接 `raise`，**不释放窗口**——因其同步
抛出终止任务，窗口对象随即销毁，与奎享「整批作废」等价。**它没有任何单元测试覆盖
窗口计数**，只能作结构参考，不能当背书。其 `QueueService` 还提供文件级队列、失败重试
（max_retry=3 backoff=5s）、未连接保留等参考语义。

> **窗口值差异注意**：inkscape_px 默认 120B（接近官方 128 原版口径），我方提议 512B。
> 差异根源是缓冲不同——inkscape_px 面向原版 Grbl 128B 缓冲，我方 Grbl_Esp32 Telnet 路径
> 有 1200B。512 的依据在 §3，不是因为 inkscape_px 用 120 就该跟着用 120。

### 8.1 旧记录 `Bf:1200` 已由 S3 侧实测确认

早前 CLAUDE.md 与 findings/09 记的 `Bf:249,1200` 来源存疑（串口实测恒 128）。
**S1 烧录后（2026-07-28）从 S3 日志抓到真实 Telnet 应答 `<Idle|...|Bf:249,1200|...>`**，
证实 1200 确实是 Telnet 客户端查询时出现的值——旧记录不矛盾，只是此前没有留存
Telnet 侧证据。窗口值可以基于 1200 推算；自适应反压阈值也可用（待 S3 观察灌数据时下降）。

### 8.2 参考实现的重连恢复语义（奎享 = 反面案例，2026-07-28 三次深挖）

窗口化流控下「断连后在途怎么办」是必须定契约的一项。奎享的做法**不能照抄**。

奎享重连后的恢复只有一条动作，`machine/f/b.java:83-88`：

```java
public final void d() {
    a a2 = (a)this.d.pollLast();   // d = 已发未确认队列，取【最后一条】
    if (a2 != null) { this.a(a2); }  // 重新入待发
}
```

队列里其余在途行**既不清空也不重发**，窗口字节计数 `e`（`b.java:21`）同样**不回退**。
触发链：`machine/ui/e/k.java:67-77` 判坐标非零 → 发 `FirmwareEventReconnected`
→ `machine/f/f.java:149-152` 消费并调 `c.d()`。

这在窗口化下必然产生**在途计数漂移**。奎享靠两个条件把概率压到接近零：
`Settings.autoReconnect` 默认 **false**（`model/Settings.java:49`，已核）、
且重连仅对串口开放（`machine/ui/a.java:642` 的 `instanceof machine.b.f` 闸门）——
换言之这条路径实际上几乎走不到，缺陷被"默认不启用"掩盖了。

**我方契约（保持现状，不按奎享改）**：断连后 `rx_buffer_.clear()` + `DrainResponses()`
**全清**（`hutuji_pipe.cc:277-283`），重连后重发 `$I` 重新探活（`hutuji_pipe.cc:134-160`）；见 banner 同样全清
（`hutuji_pipe.cc:715-736`）。代价是断点续传须由 Job 层按已确认行号自行重建，不能依赖传输层残留队列——
这个取舍是对的，因为「全清 + 上层重建」可证，「补最后一条」不可证。

### 8.3 奎享的暂停/恢复/中止不是"纯停灌"（2026-07-28 直读 `machine/f/f.java` 全文）

此前 §4.3 写"与奎享软暂停一致"，**这个类比不准确**，已改。`f.java:56-83` 的实现是
**停灌 + 主动注入笔控命令**两件事：

```java
public final void e() {                  // :63  暂停
    this.d = true;                       //      置暂停标志（n():105 靠它 return 停灌）
    this.h = System.currentTimeMillis(); //      记本次暂停起点
    this.c.a(new i.a(this.b.b(true)));   //      ← 往发送队列【注入抬笔】G1G90 Z0
}
public final void d() {                  // :56  恢复
    this.d = false;
    this.g += System.currentTimeMillis() - this.h;  // 累加暂停总时长
    this.c.a(new i.a(this.b.b(false)));  //      ← 注入落笔 G1G90 Z5
}
```

停灌本身在 `n():101-110`：`if (d) return`，即暂停时喂料函数直接返回不再取新行。
但**注入的抬笔行本身仍进 `b` 的队列并占窗口**——它是一条普通受控行。

| 维度 | 奎享 | hutuji 现状 | 取舍 |
|---|---|---|---|
| 停止机制 | 应用层停灌（`d` 标志） | Grbl 实时字符 `!` 进给保持 | 我方停得更快（planner 内的动作也立即减速） |
| 笔的处置 | **主动抬笔**，恢复时落笔 | Z 停在原位（可能压在纸上） | 奎享防墨渗/压痕更好 |
| 在途 ok | 照常收（事件驱动不受 `d` 影响） | 同（§4.3 要求） | 一致 |

**当前取舍（2026-07-28 实机调试后）**：不采用 pause/resume 直发 Z。`!` 进入 Hold 后，
普通 `G1 Z...` 只会排入 planner，不能即时抬笔；它产生的 `ok` 还会进入同一应答 FIFO，
破坏正在流式任务的行应答配对。当前保持 `!`/`~` 原位暂停，10 分钟上限控制长时间压笔风险。
将来若要暂停抬笔，必须先设计独立且可配对的受控序列，不能在窗口外旁路注入。

### 8.4 净作画时长与 ETA（奎享有，我方缺）

`f.java:18-20` 三个计时字段 + `j()`/`h()`：

```java
private long f;   // :18  任务开始时刻
private long g;   // :19  累计暂停时长
private long h;   // :20  本次暂停起点

public final long j() {                                  // :182 净作画时长
    if (this.e) return System.currentTimeMillis() - this.f - this.g;   // 扣掉暂停
    return 0L;
}
public final long h() {                                  // :86  ETA（预计总时长）
    return Math.round((float)this.j() / this.a.b());     // 净时长 / 进度(0..1)
}
```

`a.b()` 是 `machine/f/e.java:14-16` 的 `已消费条数 / 总条数`。即
**ETA = 净作画时长 ÷ 已完成比例**。

我方 `hutuji.status` 只报 `lines_sent/lines_total` 百分比，**无 ETA、且未扣暂停**。
`kMaxPauseMs` 允许暂停 10 分钟，若将来加 ETA 而不扣暂停会离谱。建议若做 ETA：
用 `lines_sent_/lines_total_` 作进度、并维护一个「累计暂停毫秒」在 `WaitWhilePaused()`
里累加——这与奎享 `g` 字段同构。**当前不做**，属体验增强非 P2 必需。

### 8.5 中止时的收尾姿态（实机否决“reset 前排队抬笔”）

`f.java:71-83` `b(boolean bl)`：

```java
if (this.e && bl) {                                    // :73 运行中且要求收尾
    this.c.a(new i.a(this.b.b(true)));                 // :74 注入抬笔
    if (t.b.type == MachineType.K_AT) { ... }          // :75-77 KAT 专用收尾
    else { this.c.a(new i.a(this.b.b())); }            // :79 注入 b() = "G90G0 X0Y0" 回零
}
```

第一版照此思路在 abort 时做 `!`→排队 Z=0→固定等待 8s→`0x18`，实机证明不可靠：

1. Hold 中普通 Z 行只进入 planner，reset 会把它连同未执行运动一起丢弃。
2. soft reset 后主轴模态已经是 Off，单发 `M5` 可能被当成无状态变化，实测会留下 Z=5。
3. Z 行回 `ok` 只表示进入 planner；紧接 `$I` 时物理 Z 仍可能在 Run，返回 `error:8`。

**当前实现**：非换纸 abort 发 `!`，以 `?` 轮询到 `Hold/Idle`（最长约 8s）后标记并发
`0x18`。只有这次自发 reset 的 banner 可走 `$X`，随后显式
`G1G90 Z0.0F10000`；收到该行 `ok` 后继续等一份 fresh `Idle`，再 `$I`、`M5`、`?`、
`G1 G53 X<当前 X> F1500` 恢复 ready/授权。`G1` 必须置于 `G53` 前以命中行首授权门；其他 Alarm 仍人工处理。

COM6 最新镜像实测从 `streaming/Run,Z=5` 恢复到 `aborted/Idle,Z=0`，
`ready=true`、`authorized=true`；继续观察 8 秒 XY 无漂移、planner 未恢复。绝不照抄奎享
`G90G0 X0Y0` 回零：该指令在我方固件有自动换纸触发语义。

### 8.6 勘误：「奎享 error 只记录不中断」是编造的（2026-07-28 直读核实）

此前 findings/09 §一.3 引了一段署名 `machine/f/c.java` 的代码，声称奎享收到 error
只 `l2.a(string)` 记录、继续发后面的行，并由此在 3 个文件 9 处写下「我们比奎享严格」。

**该代码在反编译树里不存在。** 核实方法与结果：

| 检查 | 结果 |
|---|---|
| `f/c.java` 全文（127 行） | 是 KAT/WiFi 文件级调度器（发 `[ESP913]` 上传 SD），**无任何 error 处理** |
| 全树搜 `l2.n()` 签名特征 | **0 匹配** |
| 全树搜 `startsWith("error")` | **仅 1 处**：`machine/grbl/a.java:82` |
| `f/l.java` 是否有 `n()`/`m()` | **无** |
| `FirmwareEventError` 订阅者 | **仅 `f/b.java:28`**，处理是 `this.f()` = 清空双队列 + 计数归零 |

**奎享的真实 error 语义**（`grbl/a.java:82-101`，字面代码）：

```java
if (((String)object).startsWith("error") && this.t()) {
    boolean bl = false;
    object = ((String)object).substring(6);        // 取 NN
    int n3 = Integer.parseInt((String)object);
    if (n3 == 120) { bl = true; }                  // ← error:120 静默忽略
    else if (n3 == 9 && JOptionPane.showConfirmDialog(...) == 0) {
        bl = true; this.l();                       // ← error:9 弹框问用户，确认则发 $X 解锁
    }
    if (!bl) { ...发 FirmwareEventError... }       // ← 其余一律上报 → 清空队列 = 中止
}
```

即**双方都对 error 中止**。差别是奎享有两个特例：`error:120` 静默放行（该码非原版 Grbl，
应为其私有固件扩展）、`error:9`（locked）弹框征询后 `$X` 解锁续跑。

**对我方的影响**：结论不变（保持严格中止），但**理由要改**——不是「我们比它严格」，
而是「无人值守的语音场景不允许弹框征询」。普通 `error:9` 不自动解锁；唯一例外是
S3 自己刚发出并标记的 abort reset，状态机只消费下一次 banner，并执行受限 `$X` + 抬笔 +
重新探活。来自其他来源的 Alarm 机器状态未知，仍必须人工确认。
