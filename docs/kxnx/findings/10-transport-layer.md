# 10 · 传输层深度：串口 vs WiFi，与 hutuji Telnet 的对照

> 源文件：`machine/b/a.java`（传输抽象基类）、`machine/b/d.java`（WiFi/TCP）、
> `machine/b/f.java`（串口 jssc）、`machine/b/b.java`（看门狗 Timer）、
> `machine/b/c.java` + `machine/b/e.java`（读线程）
>
> 结论先行：**奎享的 WiFi 传输（`machine/b/d.java`）用阻塞 `InputStream.read` + 单独读线程，
> 发送侧 `OutputStream.write` + 显式 `flush()`，和我们 `hutuji_pipe.cc` 的 lwip socket 几乎同构**。
> 但有两处差异值得对齐：**批量 flush** 和 **看门狗超时重连**。

## 一、传输抽象基类 `machine/b/a.java`

奎享把"怎么发字节"抽象成 `a`，串口和 WiFi 各一个子类。核心字段：

```java
public abstract class a {
    private a.b.b d = new a.b.b(2048);   // 接收环形缓冲（2048 字节）
    private a.b.b e = new a.b.b(2048);   // 发送环形缓冲（2048 字节）
    protected boolean a = false;          // 已连接标志
    private LinkedBlockingDeque f = new LinkedBlockingDeque();   // 接收到的完整行队列
    private Pattern g;                    // 行分隔符（Grbl 是 \n）
    private Thread h;                      // 行分发线程
    private int i;                        // XOR 混淆 key（串口通常 0）
    private Timer k;                       // 看门狗 Timer
    private boolean l;                    // 最近是否收到过数据
    private boolean m = true;             // 连接是否健康
    private long o;                       // 最后收到数据的时间戳
}
```

### 发送路径（攒批 + XOR + flush）

```java
// 攒进发送缓冲（不发出）
public final void a(String string) {
    this.e.b(string);
}

// 真正 flush：取出缓冲、XOR、发字节
public final void h() {
    int n2 = this.e.c();
    if (n2 > 0) {
        byte[] byArray = new byte[n2];
        this.e.a(0);
        this.e.a(byArray, 0);
        this.e.a();
        // XOR 混淆（key=0 时等于不混淆）
        for (int n3 = 0; n3 < byArray.length; n3++) {
            byArray[n3] = (byte)(byArray[n3] ^ this.i);
        }
        this.a(byArray, n2);     // 子类实现：串口 writeBytes / WiFi OutputStream.write
        this.l = true;
        this.o = System.currentTimeMillis();
    }
}
```

**关键设计**：发送缓冲 + 显式 flush。上层（流控 `b.java`）调 `a(string, false)`
时只攒不发，攒满窗口后统一 `h()` 一次 flush。**批量发送**——减少系统调用
和 TCP 小包数量。

对照 hutuji：我们的 `Pipe::SendLine` 每行一次 `send()` + `write_mutex_` 加锁。
没有攒批机制。但 lwip 的 send 有内部缓冲（`SO_SNDBUF`），不至於每行一个 TCP 包。
**暂不改**——收益不如窗口化流控大，且 lwip 已做了一层缓冲。

### 接收路径（行聚合 + BlockingDeque）

```java
protected final void a(byte[] byArray, int n2, int n3) {
    this.l = false;
    this.m = true;
    this.d.a(byArray, 0, n3);                        // 写进接收环形缓冲
    int n4 = this.d.c();
    byte[] buf = new byte[n4];
    this.d.a(0);
    this.d.a(buf, 0);
    this.d.a();
    String s = new String(buf, "UTF-8");
    Matcher matcher = this.g.matcher(s);              // 按行分隔符拆
    int last = 0;
    while (matcher.find()) {
        String line = s.substring(last, matcher.start()).trim();
        if (line.length() > 0) this.f.add(line);      // 完整行入队
        last = matcher.end();
    }
    if (last < s.length()) this.d.b(s.substring(last));   // 残片回写缓冲
}
```

用正则按 `\n` 拆行，完整行塞进 `LinkedBlockingDeque`，残片（半行）回写缓冲
等下次拼。**和我们 `hutuji_pipe.cc::OnRxData` 的行聚合逻辑同构**。

### 行分发线程（`machine/b/c.java`）

```java
// machine/b/c.java
public final void run() {
    while (this.a.a) {                        // a.a = 已连接
        String string = (String)a.a(this.a).take();   // 阻塞取一行
        this.a.b.c(string);                   // 调控制器 c(string) 分发
    }
}
```

单线程消费行队列，`take()` 阻塞。和我们的 `PipeTask` recv 循环等价。

> **勘误（2026-07-28，findings/15 §四）**：上文表头说 WiFi「读线程阻塞在 `InputStream.read`」
> **不成立**。WiFi 读线程 `machine/b/e.java:20-24` 是**忙轮询**：
> `if (available() <= 0) continue;` 再 `read`——无 sleep、无阻塞，是纯 spin。
> 阻塞 `take()` 的是**行分发线程** `b/c.java`（另一根），不是读线程。
> hutuji **不要学这个 spin**：我们的 `recv` + `SO_RCVTIMEO=3s` 阻塞是对的。

## 二、看门狗 Timer（`machine/b/b.java` + `a.java:a(boolean)`）

```java
// a.java
public final void a(boolean bl) {
    if (this.k != null) { this.k.cancel(); this.k = null; }
    this.n = bl;
    if (this.n) {
        this.k = new Timer(true);
        this.k.schedule(new b(this), 1000L, 1000L);   // 每 1s 跑一次
    }
}

// b.java（TimerTask）
public final void run() {
    if (a.b(this.a)                                  // l = 最近发过数据
        && System.currentTimeMillis() - a.c(this.a)  // o = 最后活动时间
           >= Settings.getInst().connectTimeout * 1000) {
        try {
            if (a.d(this.a)) {                       // m = 连接健康
                this.a.b.f();                         // ← 主动发探活
                a.a(this.a, false);                   // m = false（等下次 recv 置 true）
            } else {
                this.a.b(false);                      // ← 超时无响应，断连
                a.b(this.a, false);
            }
        } catch (Exception e) {}
    }
}
```

**看门狗逻辑**：

1. 每 1s 检查：距离上次收发数据是否超过 `connectTimeout` 秒
2. 若超过，且连接还健康（`m=true`）：主动发探活（`f()`，子类实现），
   置 `m=false`
3. 若超过且已发过探活仍无响应（`m=false`）：判定断连，`b(false)`

这是**应用层 keepalive + 探活**，独立于 TCP keepalive。对照 hutuji：
我们靠 `SO_KEEPALIVE` + `KEEPIDLE/INTVL/CNT`（见记忆 `lwip-keepalive-...`）。
**两种方案都可行**，奎享的在应用层更可控（能自定义探活命令），
我们的在协议层更省（不占带宽）。**不改**。

## 三、WiFi 传输（`machine/b/d.java`）—— 与 hutuji 最像

```java
public final class d extends a {
    private Socket d;
    private InputStream e;
    private OutputStream f;
    private Thread g;     // 读线程

    @Override
    protected final boolean a(String host, int port) {
        this.d = new Socket();
        this.d.connect(new InetSocketAddress(host, port), 10000);   // 10s 连接超时
        this.e = this.d.getInputStream();
        this.f = this.d.getOutputStream();
        this.a = true;
        this.g = new e(this);                       // 启动读线程
        this.g.setDaemon(true);
        this.g.start();
        return true;
    }

    @Override
    public final void a(byte[] byArray, int n2) {
        this.f.write(byArray, 0, n2);
        this.f.flush();                            // ← 每次写都显式 flush
    }
}
```

和 hutuji `hutuji_pipe.cc` 的对照：

| 维度 | 奎享 WiFi（`d.java`）| hutuji Telnet（`hutuji_pipe.cc`）|
|------|----------------------|----------------------------------|
| 连接 | `Socket.connect(InetSocketAddress, 10000)` | `connect(sock, ..., timeout)` |
| 读 | 独立线程 `InputStream.read` 阻塞 | `recv` 阻塞在 PipeTask |
| 写 | `OutputStream.write` + `flush` | `send` 循环到全部发出 |
| 行聚合 | 基类 `a.java` 正则拆 + BlockingDeque | `OnRxData` + `rx_buffer_` + 事件位 |
| 连接超时 | 10s | 我们 NVS 缓存命中 2s，非阻塞 connect |
| 单写者锁 | 无（单线程发）| `write_mutex_` |
| 探活 | 应用层看门狗 Timer | `SO_KEEPALIVE` 三参数 |

### 一个 hutuji 可以借鉴的细节

奎享的 `a(byte[], int)` 每次写都 `flush()`。Java 的 `OutputStream` 默认有
缓冲，不 flush 数据会攒在 JVM 缓冲里。**lwip socket 的 send 没有 JVM 那层
缓冲**，所以我们的 `send` 本身就是 flush 的——这点无需改。

## 四、串口传输（`machine/b/f.java`）—— 参考

```java
public final class f extends a implements SerialPortEventListener {
    private SerialPort d;
    private JSSParams e;

    @Override
    public final boolean a(String port, int baud) {
        this.d = new SerialPort(port);
        this.d.openPort();
        this.d.setParams(baud, e.dataBits, e.stopBits, e.parity, e.setRTS, e.setDTR);
        this.d.addEventListener(this);
        if (e.reset) this.f();                      // RTS/DTR 复位
        return true;
    }

    @Override
    public final void a(byte[] byArray, int n2) {
        this.d.writeBytes(Arrays.copyOf(byArray, n2));
    }

    // 事件驱动接收（jssc 回调）
    public final void serialEvent(SerialPortEvent object) {
        object = this.d.readBytes();
        this.a((byte[])object);
    }

    @Override
    public final void f() {                          // 硬件复位
        this.d.setDTR(false); this.d.setRTS(false);
        Thread.sleep(100L);
        this.d.setDTR(true);  this.d.setRTS(true);
    }
}
```

串口用 jssc 库，事件驱动接收（vs WiFi 的轮询线程）。`f()` 是 RTS/DTR
硬件复位——Arduino 类设备上电复位用的。hutuji 走 WiFi Telnet，不涉及。

## 五、传输层对 hutuji 的可借鉴点（按性价比）

### A. 批量 flush（暂不改）

奎享攒满窗口再 flush，减少系统调用。但 lwip send 已有内部缓冲，
且我们的瓶颈是逐行等 ok（往返延迟），不是发送系统调用数。
**不改**，除非将来改窗口化流控时顺手做。

### B. 应用层探活看门狗 —— **旧结论已推翻，这是我们真实的缺口**

> 本节 2026-07-28 重写。旧结论是「TCP keepalive 已够用，不改」，**错**。
> keepalive 只能发现「TCP 连接断了」，发现不了「TCP 活着但 Grbl 主循环假死」。

奎享在这件事上有**两级**判定，我们只有一级：

| | 第一级（探活） | 第二级（判死） |
|---|---|---|
| 奎享串口 | 静默 `connectTimeout`(默认 5s) → **RTS/DTR 硬件复位**（`b/f.java:99-107`；基类看门狗 `b/a.java:50-60` 仅串口子类启用，见 `grbl/a.java:243-245`） | 再静默 5s → `b(false)` 断链（`machine/b/b.java:20-28`） |
| 奎享 Grbl WiFi | 1s 周期发 `?`（`grbl/a.java:382-404`，连接后 2s 一次性 Timer 启动 `:230-237`） | 计数器 `r` 累计 **20 拍**没收到 `<...>` → `g.b(false)` 断链（收到 `<...>` 于 `:79` 清零 `r`） |
| 奎享 KAT WiFi | 5s 周期检查 | **20s** 无 `[ESP]` 消息 → 断链（`kat/c.java:19`） |
| **hutuji 现状** | `SO_RCVTIMEO=3s` 超时 → 发 `?`（`hutuji_pipe.cc:118-122`） | **EAGAIN 7 连 ≈21s 无数据判死重连**（P1 已实现，对齐 Grbl WiFi 的 20 拍） |

> 三套看门狗是**独立**的，别混：串口走基类 `b/a+b/b`（探活=RTS/DTR 复位）；
> WiFi 走控制器 `grbl/a.java` 的 `?` 循环（基类看门狗**不覆盖** WiFi）；KAT 走 `kat/c.java`。
> 详见 findings/15 §五。

**已实机抽验确认的固件缺陷**（`hutuji_pipe.cc:118-123`）：

```cpp
if (errno == EAGAIN || errno == EWOULDBLOCK) {
    // recv 超时：发 ? 轮询 Grbl 状态
    std::lock_guard<std::mutex> wlock(write_mutex_);
    SendRawLocked("?", 1);
    continue;                      // ← 无计数、无上限，永远转圈
}
```

若对端 TCP 栈存活（keepalive 因此**不会**触发）而 Grbl 主循环假死，
S3 会每 3s 发一个 `?`、永远收不到 `<...>`、**永不 break、永不重连**。
出图中撞上这种状态，任务会挂在等 ok 上直到 60s 超时报错，而链路本身
不会复位——下一次 `draw` 仍然连着一条死链。

**建议改法**（一个计数器，约 6 行）：连续 N 次 `EAGAIN` 且期间未收到任何
行 → 主动 `break` 走重连；收到任何数据即清零。N 取 7（≈21s，与奎享
20s 判死同量级，也与我们 keepalive 的 ~19s 相当）。这条**独立于 P2**，
优先级高于开窗口——死链不复位是稳定性问题，不是性能问题。

> **已落地（2026-07-28，P1）**：上面的「建议改法」已实现——`hutuji_pipe.cc`
> 加 `kSilentPollLimit=7` + `silent_polls` 计数器，连续 7 次 EAGAIN（≈21s）
> 无数据即 `break` 走重连，收到任何字节清零。已编译烧录 COM9 启动通过。
> 详见记忆 `pipe-watchdog-and-pen-injection` 与 findings/15 §五。

### C. 连接健康双标志 `l` / `m`（参考）

奎享用 `l`（最近收发过）+ `m`（探活后有无响应）两个标志区分
"正常" / "等探活" / "已断" 三态。我们的 `connected_` + `ready_` 二元
状态粒度更粗。若以后断连检测不够灵敏可参考，**当前不改**。

## 六、证据边界

- 全部静态阅读，未运行奎享抓包验证。
- **勘误（2026-07-28）**：本节早先写「`Settings.connectTimeout` 默认值未在反编译树里
  找到，已被清理」是**错的**。`model/Settings.java` 完整存在，且已读到两个关键默认值：
  `connectTimeout = a.a().a("connect_timeout", 5)`（:62，**5 秒**）、
  `autoReconnect = a.a().a("auto_reconnect", false)`（:49，**默认关闭**）。
- `a.b.b` 的底层实现（`a(boolean)` 是 autoExpand 还是 autoShrink）**仍未查证**，
  故「2048 是初始容量还是硬上限」未定，组帧层的溢出行为随之未定。
  影响有限：我们不照抄这一层（有 `kRxLineMax=512` 显式防护）。
- 奎享 WiFi 判死时长 **20s** 来自 `grbl/a.java:382-404` 的计数器逻辑推算
  （1s/拍 × 20 拍），**未实机计时验证**。
