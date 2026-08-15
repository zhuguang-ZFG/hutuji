# 21 · 传输基类、三套探活与 ESP32-Grbl SD 执行器

> 结论先行：奎享传输基类的 `l`/`o` 两个字段是**发送侧**语义（"已 flush 但还没收到任何字节" / "最后一次 flush 的时刻"），不是收侧；由此推出的第一级探活是**由控制器发 `?`**，与 RTS/DTR 硬件复位无关——后者只在"连接时可选复位"和"reset 带硬复位"两处被调用。串口与 WiFi 的探活是**互斥的两套**，由 `machine/c/a.java:30-46` 分流。收发缓冲的 2048 是**初始容量**（MINA IoBuffer，autoExpand 已开），不是硬上限。ESP32-Grbl 的 TCP 路径走 `[ESP220]` + HTTP 上传 + 模态 UI 的 SD 执行器，完成判据是状态行 SD 字段消失。
>
> 主源码基准：`D:/GitHub/kxnx/kx/tools/dump_agent/decompiled_java/`。本文简写 `machine/x` = `<主树>/com/kenjoy/kenjoycnc/machine/x`；`a/b/b.java` 与 `org/apache/a/a/a/a.java` 位于主树顶层（不在 `com/` 下）。主树缺失的类（`machine/grbl/c.java`、`machine/ui/i.java`、`com/kvenjoy/drawsoft/lib/a.java`）取自同版本 `decompiled_full/`，**逐条标注**。全部为读码，未在奎享实测。

## 1 传输基类 `machine/b/a.java`（248 行）的字段与收发路径

字段声明在 `:15-30`，与 findings/10 §一列的表相比多两项、并纠正两项语义：

| 字段 | 行 | 真实含义 |
|------|----|---------|
| `d` / `e` | `:16-17` | 收 / 发缓冲，`new a.b.b(2048)`（初始容量，见 §4）|
| `a` | `:18` | 已连接 |
| `f` | `:19` | `LinkedBlockingDeque`，**完整行**队列 |
| `g` | `:20` | 行分隔正则 |
| `b` | `:21` | **controller 引用**，类型 `com.kenjoy.kenjoycnc.machine.c`（import 在 `:7`）|
| `h` | `:22` | 行分发线程 |
| `i` | `:23` | XOR 混淆 key |
| `k` / `n` | `:26` / `:29` | 看门狗 Timer / 开关 |
| `l` | `:27` | **已 flush 但尚未收到任何字节**（不是"最近是否收到过数据"）|
| `m` | `:28` | 初值 `true`；"上一轮探活后收到过字节" |
| `o` | `:30` | **最后一次 flush 的时刻**（不是"最后收到数据的时间戳"）|

定死这两个语义的是赋值点，不是命名：

- `:127-155` `h()`（真 flush）：`e.c()` 取已写入长度 → `e.a(0)` 归零读位 → `e.a(byArray,0)` 读出 → `e.a()` 清空 → `:137-140` 逐字节 XOR `i` → `:142` 交子类 `a(byte[],int)` → **`:143-144` `l = true; o = System.currentTimeMillis();`** → 异常走 `:149` `b(false)` 断连。
- `:187-192` 接收入口 `a(byte[],int,int)` 头两句就是 **`l = false; m = true;`**，随后 `:193` 起写入收缓冲、UTF-8 解码、正则拆行、完整行入 `f`、残片回写 `d`。
- `:157-159` `a(String)` 只把字符串攒进发缓冲，**不发**。

看门狗读写这三个字段全部经合成访问器（`:224-246`），映射关系是本文所有推论的地基：

```java
static boolean b(a a2) { return a2.l; }   // :228-230
static long    c(a a2) { return a2.o; }   // :232-234
static boolean d(a a2) { return a2.m; }   // :236-238
static void a(a a2, boolean bl) { a2.m = false; }   // :240-242 忽略入参，硬置 false
static void b(a a2, boolean bl) { a2.l = false; }   // :244-246 同上
```

## 2 探活：三套机制，互斥启用

### 2.1 基类看门狗判的是"发出去没回音"

`machine/b/a.java:50-60` `a(boolean)` 启停 1s 周期 Timer，任务体是 `machine/b/b.java`（42 行）：

```java
// machine/b/b.java:19-36
if (a.b(this.a) && System.currentTimeMillis() - a.c(this.a)
        >= Settings.getInst().connectTimeout * 1000) {   // :20  l && (now - o) >= 5s
    if (a.d(this.a)) {          // :22  m：上一轮探活后收到过字节
        this.a.b.f();           // :23  ← controller.f()，不是 transport.f()
        a.a(this.a, false);     // :24  m = false
        return;
    }
    this.a(false);              // :27  transport 断连
    a.b(this.a, false);         // :28  l = false
}
```

代入 §1 的字段语义：触发条件是"**最后一次 flush 之后静默满 `connectTimeout`**"（默认 5s，`model/Settings.java:62`），不是"链路空闲 5s"——一条命令都没发过时 `l` 恒为 false，看门狗不会动。

`:23` 的 `this.a.b` 是 `machine/b/a.java:21` 那个 controller 字段，所以调的是 **controller 的 `f()`**：

- `machine/c.java:41-42` 基类 `f()` 是空实现；
- Grbl 覆写在 `machine/grbl/a.java:428-431` `f() → H()`，而 `:410-426` `H()` 就是 `this.g.a((byte)63)` = **发一个 `?`**，异常则 `s().b(false)`。

两级的时间常数是 **5s + 约 1s**，不是 5s + 5s：单字节发送口 `machine/b/a.java:161-179` `a(byte)` 直接把 `new byte[]{by}` 交给子类写出，**全程不碰 `l`/`o`**；只有 `h()` 才在 `:143-144` 刷新 `o`。所以第一级发出 `?` 之后 `o` 保持不变，判据在**下一个 tick** 就再次成立（Timer 周期见 `:57-58` `schedule(new b(this, 0), 1000L, 1000L)`，1s 首延 + 1s 周期），而此时 `m` 已被 `:24` 置假 → 直接进 `:27` 断连。也就是说这次探活只有**一个 tick 的宽限**；宽限内收到任何字节都会在 `:191` 把 `l` 清成 false，从而取消断连。

### 2.2 RTS/DTR 硬复位与看门狗无关

`machine/b/f.java:99-113` 的 `f()`（DTR/RTS 拉低 100ms 再拉高）是**串口 transport 自己**的方法，同名但不同类。全树 grep `.f();` 后确认它只有两个调用点：

- `machine/b/f.java:42-44`：`a(String,int)` 打开端口后 `if (this.e.reset) this.f();`——连接时的可选复位；
- `machine/grbl/a.java:364-377` `c(boolean)`：`:368` 先发 `0x18`（Ctrl-X 软复位），`:374-376` 若入参为真再 `this.s().f()`——即 findings/19 记的 reset2 路径。

**看门狗从不调它。** findings/10 §五 B 的两级对照表原写"奎享串口第一级探活 = RTS/DTR 硬件复位"是错的，已在该文件加勘误块（不引行号，避免该文件行号变动后再漂）。

### 2.3 谁启用哪一套：`machine/c/a.java` 是分流点

`machine/c/a.java`（173 行）是**ESP32-Grbl 专用控制器**，`:20` `extends machine.grbl.a`。

```java
// :30-37  连接成功
public final void b(String string, int n2) {
    super.b(string, n2);                       // → machine/grbl/a.java:243-250
    this.i = false;                            // SD 打印中标志复位，见 §5
    if (this.s() instanceof d) { this.m(); }    // 仅 WiFi 才启 ? 轮询
}

// :39-46  连接入口
public final void a(String string, int n2) {
    this.e = string; this.f = n2;
    if (!(this.g instanceof d)) { super.a(string, n2); }   // WiFi 跳过父类
}
```

父类两侧各做一件事：

- `machine/grbl/a.java:243-250` `b()`：`:246-248` **只有 `g instanceof machine.b.f`（串口）才 `g.a(true)`** 启基类看门狗，然后 `:249` 发 `$I`。
- `machine/grbl/a.java:232-241` `a()`：`:235-237` `!k` 时发软复位 `c(false)` 并返回；否则 `:239-240` 起一个 **2s 一次性 Timer** `machine/grbl/b.java`，其 `run()` 在 `:20-21` 对串口再启一次基类看门狗、`:23` **无条件** `H()` 发一个 `?`。因为 `machine/c/a.java:43-45` WiFi 分支跳过 `super.a()`，这个 Timer 在 ESP32-Grbl WiFi 下**根本不会创建**。

### 2.4 WiFi 的 `?` 轮询是请求-响应节奏，失联后不重发

```java
// machine/grbl/a.java:379-408  m()
if (this.p) return;  this.p = true;           // :381-384 防重入
KenjoyDraw.a.execute(() -> {
    while (this.p && this.t()) {
        if (this.r == 0) { ++this.r; this.H(); }        // :388-390 只有 r==0 才发 ?
        else {
            ++this.r;
            if (this.r >= 20) { this.r = 0; this.g.b(false); }   // :391-397 判死断连
        }
        Thread.sleep(1000L);                                     // :398
    }
});
```

`r` 由 `machine/grbl/a.java:82` 收到 `<...>` 状态行时清零。所以精确语义是：**发一个 `?` → 等状态行 → 收到才发下一个**；一旦失联，`r` 单调累加、循环里**再也不会发 `?`**，只空转到第 20 拍（≈19-20s）才 `b(false)` 断连。

我方 `hutuji_pipe.cc` 是失联期间持续每 3s 重发 `?`、7 次静默（≈21s）判死——判死时长同量级，**探活重发策略相反**，交叉引用时不要混为一谈。

## 3 攒批粒度：一次 write 可含多条 G-code 行

`machine/f/b.java:50-81` `g()`（窗口引擎的补发环）确认了 findings/10 §一的推测：

```java
do {
    object = this.c.poll();                    // :57  从 pending 取一条
    if (object != null) {
        this.e += object.b() + 1;              // :59  在途字节数 += len + 1
        this.d.add(object);                    // :60  记入在途队列
        bl = true;
        this.b.a(object.a(), false);           // :62  ← 只攒进发缓冲，不发
    }
    ...
} while (this.e + peek.b() + 1 < this.b.g() || this.b.g() == -1);   // :70 窗口判据
if (bl) { this.b.s().h(); }                    // :71-73 本轮有新命令才 flush 一次
```

窗口 `b.g()` 对 Grbl 是 100（`machine/grbl/a.java:315-317`），Marlin 96，EBB `-1`（不限）。因此**一次 `write()` 的负载通常是若干条行的拼接**，攒到再放一条就会越过 100 字节为止；`-1` 时把 pending 一次抽干。

我方是逐行 `send()`，每行一次系统调用。lwip 有内部缓冲、瓶颈在等 `ok` 的往返，此处**不对齐**（findings/10 §五 A 的结论仍然成立）。

## 4 收发缓冲结案：2048 是初始容量，autoExpand 已开

findings/10 §六「证据边界」原把"`a.b.b` 的 `a(boolean)` 是 autoExpand 还是 autoShrink"列为未定项，可以关闭了（该文件已就地标注结案）。

`a/b/b.java`（196 行）只是 MINA `IoBuffer` 的薄包装：`:16` 持有 `org.apache.a.a.a.a`，`:18-21` 构造即

```java
this.a = org.apache.a.a.a.a.a(n2);   // IoBuffer.allocate(capacity)
this.a.a(true);                      // ← MINA 的 setAutoExpand
```

被包装类 `org/apache/a/a/a/a.java`（343 行）的方法身份可从实现直接读出：

| 混淆签名 | 行 | 等价 API |
|---------|----|---------|
| `a(boolean)` | `:54-60` | `setAutoExpand`：`!e` 时抛 `"Derived buffers and their parent can't be expanded."`（`:56`），否则 **`d = true`（忽略入参硬置 true）** |
| `b()` | `:62-64` | `position()` |
| `c(int)` | `:66-73` | `position(int)` |
| `c()` | `:75-77` | `limit()` |
| `d(int)` | `:79-84` | `limit(int)` |

**当年查不清的原因是一处签名冲突**：包装类自己也有 `a(boolean)`（`a/b/b.java:56-58`），但它是"写入一个 0/1 字节"（`this.a.a((byte)(bl?1:0))`），与被包装类的 `a(boolean)`=setAutoExpand 毫无关系。只看包装类会得出错误结论。

结论：收发缓冲**没有 2048 硬上限**，写满自动扩容。这也解释了 `machine/b/a.java` 为什么全程不检查溢出。我方 `kRxLineMax=512` 的显式上限是**有意不对齐**的加固项，不是补齐缺失。

## 5 SD/ESP 执行器 `machine/f/g.java`（170 行）全链路

findings/20 §1 只确认了"TCP 走 `machine/f/g`"，这里补完整条链。

### 5.1 启动：拼大字符串 → 模态上传框 → `[ESP220]`

- `:25-30` 构造记文件名（`kdraw.gco` / `KDRAW.GCO`），订阅 `FirmwareEventStatus` 与 `FirmwareEventesp_print_finish`。
- `:125-132` `n()` 把 pending 里**全部**命令用 `\n` 拼成一个字符串。
- `:36-53` `a()`：`:39` 弹**模态**上传对话框 `machine/ui/i`（主树缺该类，取 `decompiled_full/machine/ui/i.java`）；回调非 null → `:41` `this.b.f("/" + this.e)`；回调 null → `:45-47` `d.a()`，即 findings/20 §7 的 `uploadFailed`。
- `machine/c/a.java:148-151` `f(String)` = `a(lib.a.a(220, string), true)`。`decompiled_full/com/kvenjoy/drawsoft/lib/a.java:16-23` 是 `MessageFormat.format("[ESP{0}]{1}", n, s)`，`esp_enable_auth` 为真时追加 `" pwd=" + esp_admin_pwd`（默认 `admin`，`:9-10`）。**实际发出的是 `[ESP220]/kdraw.gco`**，不是 `$SD/Run`。
- 上传本体是 **HTTP POST**：`machine/c/a.java:164-171` 静态 `i(String)` 拼 `http://<host>/upload[?query]`，host 取 `((machine.b.d)machine.f.a().e().s()).d()`。`decompiled_full/machine/ui/i.java:70-96` 负责落临时文件、起 `com.kenjoy.kenjoycnc.e.h` 线程上传、显示进度/速度、失败给 retry 按钮、成功 `dispose()` 并回调 `onUploaded`。

### 5.2 进度与完成：判据是状态行 SD 字段消失

```java
// machine/c/a.java:54-85  h(String) 处理 <...> 状态行
object = string.split("\\|");                     // :57
for (每段) if (段.contains("SD")) {                // :62-70
    this.h = Float.parseFloat(段.substring(3, 段.indexOf(',')));   // :65 跳过 "SD:"
    bl = true; break;
}
boolean bl2 = this.i;
this.i = bl || this.y();                          // :71-72 注意 || y()：hold 也算在打印
if (bl2 && !this.i) {                             // :73-75 由真变假 → 完成
    this.a(new machine.d.b("FirmwareEventesp_print_finish", this));
}
```

`machine/f/g.java:64-85` 收事件：`FirmwareEventStatus` → `b.w()` 为真时发 `Progress`，状态 == `grbl.c.a`（alarm）且首次 → 提示 `unlock_first`，总是再发 `Status`；`FirmwareEventesp_print_finish` → `b(false)` + 发 `Complete`。

状态枚举取自 `decompiled_full/machine/grbl/c.java:8-15`：`a`=alarm(0)、`b`=idle(1)、`e`=sleep(2)、`c`=run(3)、`f`=jogging(4)、`g`=homing(5)、`d`=**hold**(6)。故 `machine/c/a.java:159-162` `y()` 就是"当前处于 hold"。

顺带定死一条连接判据：`machine/c/a.java:76-84` 只在**收到第一个状态行**时才发 `FirmwareEventConnected`，并硬写版本 `b = "1.1"`。ESP32-Grbl 的"已连上"既不是 TCP 建连、也不是 banner。

### 5.3 停止与暂停走实时字节

- `:87-103` `b(true)`：`:91` 先 `b.v()`（`machine/c/a.java:153-157` = `0x85` jog-cancel + `0x7E` resume），再 `:92-95` 新建临时 `machine/f/f(true, controller)` 塞一条 `k(b.b(true), b.b())` 收尾。查 `machine/grbl/a.java:266-287`：`b(bl)` 的 `bl=true` 分支取 `zOff` / `M5` / `penUpPos` / `customOffCode`，即 **`b(true)` 是抬笔**；`:298-301` `b()` 返回 `G90G0 X0Y0`。所以收尾 = **抬笔 + 回原点**。
- `:134-154` pause/resume：`b()` 按 `b.y()`（是否 hold）在 `A()` / `z()` 之间切换；`d()` = `A()`，`e()` = `z()`。定义在 `machine/grbl/a.java:438-446`：`z()` 发 `0x21`（`!` feed hold）、`A()` 发 `0x7E`（`~` cycle start）。

## 6 对 hutuji 的取舍

逐条过一遍"要不要对齐"，结论是**本轮 10 条里没有一条需要改我方代码**：

| 奎享机制 | 我方现状 | 取舍 |
|---------|---------|------|
| 发送侧看门狗 5s + 约 1s（`machine/b/a.java:143-144`/`:191-192`、`machine/b/b.java:20-28`）| `hutuji_pipe.cc:38/47` `kPollIntervalSec=3` × `kSilentPollLimit=7` ≈ 21s，与 TCP keepalive 19s 互补 | 概念已借鉴，**不改**（我方阈值更宽，容得下换纸类长阻塞）|
| 失联后**不重发** `?`，只空转计数（`machine/grbl/a.java:388-397`）| 每次 recv 超时都重发 `?`（`hutuji_pipe.cc:175-212`）| **不对齐**：重发能穿越瞬时丢包，奎享那套一旦丢掉那唯一一个 `?` 就必然误判 |
| RTS/DTR 硬复位（`machine/b/f.java:99-113`）| WiFi Telnet 无 DTR 线 | **不适用** |
| 攒够 ~100 字节一次 flush（`machine/f/b.java:50-81`、窗口 `machine/grbl/a.java:315-317`）| 逐行等 `ok` | **不改**：瓶颈是等 `ok` 的往返，攒批既不解决又会毁掉逐行错误定位 |
| 收发缓冲 autoExpand（`a/b/b.java:18-21`）| `kRxLineMax=512` 显式上限（`hutuji_pipe.cc:32`）| **故意不对齐**，属加固项 |
| 模态上传对话框（`machine/f/g.java:39`）| 语音下发、无人值守 | **禁止对齐**（决策 ③ 哑管道）|
| `[ESP220]` + HTTP `/upload`（`machine/c/a.java:148-151`/`:164-171`）| 本机固件无该协议，走 Telnet 逐行 | **不适用** |
| `i = bl \|\| y()` 把 hold 也算"在打印"（`machine/c/a.java:71-72`）| 以 fresh `Idle` 判完成，fail-closed | **禁止对齐**：hold 计为进行中会把卡死当正常 |
| 收到第一个状态行才算"已连上"、版本硬写 `"1.1"`（`machine/c/a.java:76-84`）| Grbl banner 验证对端身份（`hutuji_pipe.cc:49-55`）| **不对齐**：banner 更早，且能挡住局域网里别的 `:23` 主机 |
| stop = `0x85`+`0x7E` 后**排入** G-code 抬笔 + `G90G0 X0Y0`（`machine/f/g.java:91-95`、`machine/grbl/a.java:266-287`/`:298-301`）| `!` → 轮询到 Hold/Idle → `0x18` 丢 planner，抬笔交给复位后探活的首个 `M5`（`hutuji_job.cc:340-363`）| **不对齐**：奎享的收尾行要等 planner 排空才执行，卡死时永远到不了 |

## 7 未验证项与 CFR 边界

- **全部为静态读码**，未在奎享实机抓包或计时。`connectTimeout` 默认 5s 出自 `model/Settings.java:62` 的 `a("connect_timeout", 5)`，实际部署是否被改过未知。
- §2.1 的"5s + 1s"、§2.4 的"20 拍 ≈ 19-20s"都是按 Timer 周期（`machine/b/a.java:57-58`）与 `Thread.sleep(1000)`（`machine/grbl/a.java:398`）**推算**的，未计时验证。
- 三个补全类取自 `decompiled_full/`：`machine/grbl/c.java`、`machine/ui/i.java`、`com/kvenjoy/drawsoft/lib/a.java`。混淆名与主树一致，**但行号基准不同**，不可与主树行号互引。
- CFR 反编译噪音，别当缺陷记：`machine/grbl/a.java:101-103`、`machine/c/a.java:79-82`/`:96-99`/`:103-105`/`:109-111`/`:118-120`/`:140-142` 都是"new 两个事件对象、给第二个赋值、发第一个"的产物。
- 尚未读：`machine/e/h.java`（HTTP 上传线程本体）、`machine/grbl/GrblSettings.java` 全貌、`machine/kat/b.java`（仅备案"禁止对齐"）、full 树 `machine/b/e.java` 忙轮询的确切行号。
- **原记「已知文档漂移，本轮未改」的两处，2026-08-11 复核已在 fork 侧修好**：`D:/Users/xiaozhi-esp32/main/boards/lichuang-dev/hutuji_pipe.cc:44` 现写「串口侧两级静默 5s + 约 1s」（原为 "5s+5s"，与 §2.1 一致），同行引的 `machine/grbl/a.java` 行号也已由 `:382-404` 改成 `:379-408`。此条保留作记录，不再是待办；本仓无改动，改 fork 仍须按 `docs/agent-constraint-matrix.md:34` 跟一次 IDF 构建。

