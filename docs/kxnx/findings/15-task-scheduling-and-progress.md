# 15 · 任务调度与进度反馈（`machine/f/` 全包 + `machine/b/` 线程模型）

> 源文件：`machine/f/a.java`（任务基类）、`f/b.java`（窗口引擎）、`f/f.java`（执行器）、
> `f/l.java`（队列）、`f/k.java`（命令流）、`f/e.java`（进度基类）、`f/m.java`（文件项）、
> `machine/c.java`（控制器基类）、`machine/b/a.java`（传输基类）、`b/b.java`（串口看门狗）、
> `b/d.java`（WiFi 传输）、`b/e.java`（WiFi 读线程）、`i/a.java`（命令项）、`model/Settings.java`
>
> 本文填两块之前没系统写的：**出图任务在内存里怎么组织/排队**、**进度/ETA 怎么算**；
> 顺手勘误 findings/10 里的两处线程/看门狗结论，并钉死一处孤证（resend）。

> **hutuji 状态勘误（2026-07-28）**：本文关于奎享窗口内注入的结论保留，但不能把
> “hutuji 暂停/中止抬笔目前直发”当成当前 abort 实现。当前 abort 不在 reset 前排队 Z，
> 而是等待 `Hold/Idle` 后受限 reset，再显式 Z=0 并等 fresh `Idle`。pause/resume 与 P2
> 窗口化的最终注入策略仍须在 S3/S4 阶段重新验证。

## 〇、一句话架构

```
JobManager(l) 持命令队列 Queue(k) ──喂给──▶ Executor(f)
                                              │ n() 泵
                                              ▼
                                    WindowEngine(b) ──flush──▶ Transport(a)
                                              ▲                   │
                          ok/error/status ────┘                   │ read
                                          Controller(c/grbl.a) ◀──┘ 行聚合
```

四层各管一件事：**l** 管多文件队列与换纸编排，**f** 管单个命令流的泵送与暂停/计时，
**b** 管字节窗口与 ok/error 反应，**b/a+d/e** 管字节收发与行聚合。

## 一、任务组织：`f/l.java` 是"文件队列 + 换纸编排器"

`l` 持一个 `Vector<m>`（每份文件一个 `m`，`m.a = FileStatus{wait/drawing/finished/error/uploadFailed}`），
逐份把行读进 `ArrayList<String>`，整体塞进一个 `k` 命令流交给执行器 `f` 跑。

### 1.1 行加载（`l.java:156-184`）

```java
private void l() {
    m m2 = this.b.get(this.c);
    m2.a = FileStatus.drawing;
    ArrayList<String> arrayList = new ArrayList<String>();
    BufferedReader br = new BufferedReader(new FileReader(m2.b));
    while ((string = br.readLine()) != null) {
        if (av.g(string = string.trim())) continue;   // ← 跳过空行
        arrayList.add(string);
    }
    this.a.a(new k(arrayList));   // 整个文件一次性进命令流
    this.a.a();                   // 执行器开始跑
}
```

- **空行在入队前就剔除**（`av.g()`），不是发给固件被忽略。hutuji 的 MCP 端已不产空行（§ 注释剥离），等价。
- **整文件一次性进内存**。hutuji S2 的 `ParseLines()` 把 512KB 缓冲预解析成 `LineSpan` 索引，
  内存模型一致（都是整文件驻留），只是我们为了防 ESP32 内部 RAM 溢出用 PSRAM 零拷贝，他们用 JVM 堆。

### 1.2 多文件 + 换纸编排（`l.java:186-208`）

```java
public final void k() {
    if (this.c == this.b.size() - 1 || !this.f) { ... --this.h; 跑批次数 ... }
    ++this.c;
    if (this.d instanceof machine.e.a) {
        this.d.a("M1100", true);   // ← e.a 机型（带自动换纸）：发 M1100 触发换纸
    }
    this.e = new d(() -> this.c(null));   // 弹"请换纸"对话框，等 FirmwareEventPaper_ready
}
```

奎享的换纸是**事件驱动**：`FirmwareEventPaper_ready` → `l.c()` → 接着画下一份。
**hutuji 不看这块**（用户已确认上位机/下位机换纸不兼容，我们的换纸是 M30 触发固件内置流程）。

## 二、执行器：`f/f.java` 的泵、暂停、计时

`f` 是单个命令流的"运行器"，继承任务基类 `a`。

### 2.1 泵 `n()`（`f.java:101-126`）—— 核心调度

```java
private void n() {
    if (this.d || !this.e) return;                 // 暂停中 或 未运行 → 不泵
    this.a(new d.a("Progress", this));              // 每泵一次发一次进度事件
    long l2 = this.c.e();                            // l2 = 窗口引擎当前在途字节
    while (this.a.a()) {                             // 命令流还有行
        a2 = this.a.e();                             // peek 下一行（不出队）
        bl = this.b.g() > 0;                         // bl = 有窗口上限
        if (l2 + a2.b() + 1 >= this.b.g() && bl) break;  // ← 窗口要满 → 停
        a2 = this.a.c();                             // 真正出队（推进进度计数 a）
        this.c.a(a2);                                // 交给窗口引擎
        l2 += a2.b() + 1;
        if (this.c.a() || !bl) break;                // 单步模式 / 无窗口 → 停
    }
    if (this.c.b()) this.m();                        // 全发完 → Complete
}
```

- **事件驱动，不是定时轮询**：`n()` 由 `FirmwareEventResponse_OK`（`f.java:145-147`）和
  启动 `a()`（`f.java:37-45`）触发。来 ok 才补发，对齐官方 `stream.py` 的 R2/R3。
  （这是早前「10ms Timer 轮询」伪代码勘误的根：`f.java` 全文零 Timer。）
- **peek 先于 pop**：先 `e()`（peek）判窗口，确认放得下才 `c()`（pop 推进计数）。
  这正是 hutuji S2 预解析索引想拿到的能力——**先 peek 行长度再决定发不发**。
- **每泵发一次 `Progress` 事件**（`f.java:111`）：进度推送是泵驱动的，不是定时器。

### 2.2 暂停/恢复/中止（`f.java:56-83`）

```java
public final void d() {   // resume
    this.d = false;
    this.g += System.currentTimeMillis() - this.h;    // 累计暂停时长（从 ETA 里扣）
    this.c.a(new i.a(this.b.b(false)));               // 注入落笔 b(false)=pen down
}
public final void e() {   // pause
    this.d = true;
    this.h = System.currentTimeMillis();
    this.c.a(new i.a(this.b.b(true)));                // 注入抬笔 b(true)=pen up
}
public final void b(boolean bl) {  // abort
    if (this.e && bl) {
        this.c.a(new i.a(this.b.b(true)));            // 先注入抬笔
        ... K_AT 发 kat.a.b()/a()；否则发 b()=G90G0 X0Y0 ...
    }
    this.e = false;
}
```

- 奎享的暂停/恢复/中止都**经窗口引擎 `c.a(...)` 注入笔控**，走同一窗口、被同一窗口节流。
  hutuji 当前 abort 已改为“保持 → 受限 reset → reset 后显式 Z=0/fresh-`Idle`”，不再沿用
  本文早期记录的 reset 前直发抬笔。pause/resume 在窗口化后的注入语义仍须在 P2 S3/S4 重审。

### 2.3 进度/ETA（`f.java:86-99, 182-192` + `f/e.java`）

```java
// e.java（基类）
public final float b() { return (float)this.a / (float)this.b; }  // 完成比 = 已发行/总行数

// f.java
public final long h() { return Math.round(this.j() / this.a.b()); }        // ETA ms = 已耗时/完成比
public final long j() { return this.e ? (now - this.f - this.g) : 0; }    // 净耗时（扣掉暂停 g）
public final long k() { return this.g; }                                   // 累计暂停时长
```

- **进度 = 已发行数 / 总行数**（`k` 每 pop 一行 `++a`，`b` 构造时定 `b=size`）。
- **ETA = 净耗时 / 完成比**，净耗时**扣掉累计暂停时间 `g`**。这正是 hutuji 设计文档 §8.4
  「ETA 应排除暂停时间」的实现蓝本——奎享就是这么做的。
- hutuji `hutuji.status` 目前是 5s 定时 + 行计数，**等价**但 ETA 未扣暂停。低优先改进项（可选）。

## 三、窗口引擎补遗：`f/b.java` 与 `machine/c.java`

`b.java` 的窗口机制 findings/09 已详写，这里只钉三个调度相关的点。

### 3.1 单步模式 = 窗口退化为 1（`b.java:23-26` + `f.java:26`）

`b` 的布尔 `a`（单步）来自 `f` 构造：`new b(bl || settings.singleStep, c2)`。
`singleStep = a.a().a("single_step_command", false)`（`Settings.java:41`，**默认 false**）。
单步为真时 `b.g()` 每发一行就停（`b.java:66` 的 `d.isEmpty()` 判）→ 窗口退化 1。
**窗口=1 来自这个开关，不是机型**（二次勘误已并入 findings/09/12）。

### 3.2 命令项是 `{文本, 行号}` 对（`i/a.java:25-54`）

```java
public a(String string, int n2) {
    this.a = string;  this.f = n2;              // 文本 + 行号
    this.j = d.b(string);                        // 提注释
    if (有注释) this.a = d.a(string);            // 剥注释后的命令体
}
public final int b() { return this.a.length(); } // ← 窗口计数用剥注释后的长度
```

**窗口计数用的是剥掉注释后的命令长度**，不含注释。hutuji MCP 端已剥注释（§ G-code 注释剥离），
所以长度口径天然一致。

### 3.3 resend 是 Marlin 专属，Grbl 无此路径（孤证澄清）

`f/b.java:27` 订阅了 `FirmwareEventResend`，处理器 `d()`（重发在途最后一行）。
但**全树只有 `marlin/a.java:62-64` 生成这个事件**（Marlin 的 `Resend: N` 行号协议），
Grbl 控制器 `grbl/a.java` 不生成。`machine/c.java:27-35` 也没把 resend 列进抽象契约。
**结论：resend 是 Marlin 行号重传协议的残留，Grbl 路径用不到，hutuji 不用理。**
`b.java:83-88` 的 `d()`（重发队尾）只在 Marlin 下会被真正触发。

## 四、线程模型勘误（findings/10 §一）

findings/10 **早期版本**说「读线程阻塞在 `InputStream.read`」，**对 WiFi 不成立**
（该文件 2026-07-28 已按本节结论就地勘误，结论先行与 §五 B 对照表现已写成「忙轮询」）。

| 传输 | 读线程 | 实际行为 |
|------|--------|----------|
| WiFi `b/d.java` + `b/e.java` | `net read` 线程 | **忙轮询**：`if (available() <= 0) continue;`（`e.java:22`）再 `read`。**无 sleep、无阻塞，是 CPU 烧的 spin**；读缓冲 `e.java:11` 是 `new byte[128]` |
| 串口 `b/f.java` | jssc 回调线程 | 事件驱动 `serialEvent` → `readBytes`，不烧 CPU |
| 行分发 | `b/c.java`（`read thread:`） | `f.take()` 阻塞取行 → `controller.c(string)` |

> **行号基准补注（2026-07-29）**：本节 `machine/b/e.java` 的行号基准是 `decompiled_full/`——
> 主树 `decompiled_java/.../machine/b/` 下只有 `a b c d f`，**没有 `e.java`**（本文头部「源文件」
> 一行同此）。full 树该文件共 42 行。其余 `machine/b/*` 文件两树行号相同。

- WiFi 读线程是**唯一**的（每连接一个 `net read`），`while(a)` 自旋。
- 对 hutuji 的启示：奎享 WiFi 这套 spin-read 在桌面 JVM 无所谓，但**思路不可搬到 ESP32**。
  我们的 `recv` + `SO_RCVTIMEO=3s` 阻塞 + 超时发 `?` 是对的做法，**不要学它的忙轮询**。
- 行分发与读分离（读→聚合→入队→分发线程 take），和我们的 `OnRxData` 行聚合 +
  应答队列同构；当前 S3 队列容量由 512B 发送窗口推导为 255，覆盖最短非空行的最大在途应答数。

## 五、看门狗两级架构勘误与钉死（findings/10 §五）

> **二次勘误（2026-07-29，findings/21 §2）**：本节 5.1 原写「串口第一级探活 = 串口子类
> `f()` 的 RTS/DTR 硬件复位」，**错**。看门狗调的是 `machine/b/a.java:21` 持有的
> **controller 引用**的 `f()`（基类 `machine/c.java:41-42` 是空实现，Grbl 在
> `grbl/a.java:428-431` 覆写为 `H()` → 发 `?`）；RTS/DTR 那个同名 `f()` 属 transport 子类
> （`b/f.java:99-107`），全树只有连接时 `if (e.reset)`（`b/f.java:42-44`）和 reset2
> （`grbl/a.java:374-376`）两个调用点，**看门狗从不碰它**。第二级宽限也不是 5s，而是
> **约 1s**。5.4 表格与 §七 评级表已同步改正。

findings/10 把两级看门狗讲得不够干净。钉死如下，奎享实际有**三套**独立机制：

### 5.1 串口路径：基类应用层看门狗（`b/a.java:50-60` + `b/b.java:20-28`）

```java
// a.java: 只在串口子类上启用
public final void a(boolean bl) {
    if (this.n) { this.k = new Timer(true); this.k.schedule(new b(this, 0), 1000L, 1000L); }  // :57-58 1s 首延+1s 周期
}
// b.java run():
if (l /*已 flush 但还没收到任何字节*/ && now - o /*最后一次 flush 的时刻*/ >= connectTimeout*1000) {
    if (m /*上一轮探活后收到过字节*/) { this.a.b.f();  /* controller.f()；Grbl 覆写 = 发 '?' */  m=false; }
    else                              { this.a.b(false); /* 断链 */ }
}
```

- `connectTimeout = a.a().a("connect_timeout", 5)`（`Settings.java:62`，**5 秒**）。
- 第一级探活 = **controller `f()`**（Grbl = 发一个 `?`）。发探活字节走 `a(byte)`
  （`b/a.java:161-179`），它**不刷新 `o`**，所以判据在**下一个 1s tick** 又成立 →
  两级时长是 **5s + 约 1s**，探活只有一个 tick 的宽限；宽限内收到任何字节会在
  `b/a.java:191` 把 `l` 清成 false 从而取消断连。
- **启用点**：`grbl/a.java:246-248`（主树；`decompiled_full/` 为 `:243-245`）—— 仅当传输是
  串口子类 `b/f` 的实例才 `a(true)` 启动。**WiFi（`b/d`）不启动这个基类看门狗。**

### 5.2 Grbl WiFi 路径：控制器级 `?` 看门狗（主树 `grbl/a.java:232-241`、`:379-426`）

```java
// a(String,int) 连接后（k=true）2s 一次性 Timer 启动（schedule 在 :240）：
((Timer)object).schedule(new b(this), 2000L);   // b.run(): 串口则 a(true)，再调 H()
// m()（主树 :380-）后台线程：
while (p && t()) {
    if (r == 0)      { ++r; H(); }              // H() 发 '?'(0x3F)，仅 r==0 时
    else { ++r; if (r >= 20) { r = 0; g.b(false); } }  // 20 拍无 <...> → 断链，期间不重发 ?
    Thread.sleep(1000L);                        // :398
}
```

- 1s 一拍。**只有 `r==0` 才发 `?`**——这是请求-响应节奏：收到 `<...>`（`grbl/a.java:73` 判前缀、
  `:82` 清零 `r`）才允许下一次探活，失联期间 `r` 只累加、**不重发** `?`。
- **20 拍（≈20s）无 `<...>` → `g.b(false)` 判死断链**。
- 这套是 **WiFi 唯一**的判死机制（基类看门狗不覆盖 WiFi；串口/WiFi 由 `machine/c/a.java:30-46` 分流）。
- 行号基准：本节 `grbl/a.java` 行号已统一到主树 `decompiled_java/`（`decompiled_full/` 少 3）。

### 5.3 KAT WiFi 路径：`kat/c.java:19`，5s 检查、20s 无 `[ESP]` 消息判死。

### 5.4 与 hutuji 的对照（已落地）

| 机制 | 奎享 | hutuji |
|------|------|--------|
| WiFi 探活 | 首拍发 `?`，**等到 `<...>` 才发下一个**；失联期间不重发 | `SO_RCVTIMEO=3s` 超时**无条件重发** `?`（有意不对齐，见 findings/21 §6）|
| WiFi 判死 | 20 拍 ≈20s 无 `<...>` | **EAGAIN 7 连 ≈21s 无数据**（P1 已实现，对齐此） |
| TCP 层兜底 | 无（应用层自理） | `SO_KEEPALIVE` KEEPIDLE/INTVL/CNT ≈19s |
| 串口探活 | **controller `f()` 发 `?`**（5s 静默触发，宽限约 1s）| 不涉及（走 WiFi） |

hutuji P1 的 `kSilentPollLimit=7`（≈21s）就是照 5.2 的 20 拍量级定的。**已对齐，无需再改。**

## 六、对 hutuji 的可借鉴点（按性价比）

### A. ETA 排除暂停时间 —— 可选，低优先

奎享 `j() = 净耗时（扣暂停 g）`，`h() = 净耗时/完成比`。hutuji `hutuji.status` 的 ETA
目前没扣暂停。做法已明：维护累计暂停时长，ETA 用 `净耗时/完成比`。**可选**，不影响正确性。

### B. peek-then-pop 已在 S2 拿到，但窗口化注入要重新审视

奎享泵 `n()` 是 `e()`(peek) 判窗口 → `c()`(pop) 才发，且**笔控注入也走窗口**。
hutuji S2 的 `ParseLines()` 索引已具备 peek 能力（拿行长度不出队）。
早期 P2 草案把暂停/中止抬笔记为直发；2026-07-28 abort 实机回归已否决这条作为 abort
现行语义。**一旦 S3 开窗（窗口内可能在途多行），任何 pause/resume 笔控都不能插在窗口
字节计数之外**。S3/S4 必须明确：计入窗口，或排空后再发；abort 则继续遵守当前受限 reset
恢复序列。（设计文档 §5 S3 已列「换纸行前排空」，笔控应共享同一不变量。）

### C. 不要学 WiFi 忙轮询

奎享 WiFi `available()<=0 → continue` 的 spin 在 ESP32 上是灾难。我们的阻塞 `recv` 正确。

### D. 进度推送节奏

奎享每泵发一次 `Progress` 事件（泵一次可能发多行）。hutuji 5s 定时 + 行计数够用，不改。

## 七、证据强度评级

| 结论 | 强度 | 依据 |
|------|------|------|
| 任务 = 文件队列 + 换纸编排 | 强 | `f/l.java` 全文，`Vector<m>` + `FileStatus` |
| 泵是 ok 事件驱动非定时轮询 | 强 | `f/f.java:101-126,145-147`，全文零 Timer |
| 进度 = 已发行/总行；ETA 扣暂停 | 强 | `f/e.java:15`、`f/f.java:86-99,182-192` |
| 单步=窗口退化 1，默认关 | 强 | `f/b.java:23-26`、`f/f.java:26`、`Settings.java:41` |
| 窗口计数用剥注释后长度 | 强 | `i/a.java:25-54`（`b()` 返回剥注释 `a.length()`） |
| resend 是 Marlin 专属 | 强 | 全树唯一生成者 `marlin/a.java:62-64`；`c.java:27-35` 无此契约 |
| WiFi 读是忙轮询 spin | 强 | `b/e.java:20-24`（`available()<=0 continue`）；**该文件只在 `decompiled_full/` 树存在** |
| 串口看门狗 5s 静默 → controller `f()` 发 `?`，再约 1s 无回音断链 | 强 | `b/a.java:50-60,57-58,143-144,161-179,191`、`b/b.java:20-28`、`grbl/a.java:428-431`、`Settings.java:62`（**旧版本此行写"RTS/DTR 复位"，2026-07-29 已推翻，见 5.1 勘误**）|
| RTS/DTR 硬复位与看门狗无关 | 强 | `b/f.java:99-107` 仅两个调用点：`b/f.java:42-44`、`grbl/a.java:374-376` |
| Grbl WiFi 看门狗 1s 一拍 / 20 拍判死 / 失联不重发 `?` | 强 | `grbl/a.java:232-241,379-426,73,82,398`（主树行号）|
| 串口看门狗仅串口子类启用 | 强 | `grbl/a.java:246-248`（主树；full 树 `:243-245`）（`instanceof b/f` 才 `a(true)`）|
| 20 拍≈20s 未实机计时 | 推断 | 1s/拍 × 20 推算，未抓包 |

## 八、合规边界

静态阅读 CFR 反编译，未运行奎享抓包。仅互操作目的，不含奎享二进制/原代码。
