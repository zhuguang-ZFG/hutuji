# 09 · 流控引擎深度剖析（本次逆向最重要的发现）

> 结论先行：**奎享不是「一行一 ok」的逐行流控，而是 Grbl 官方推荐的 character-counting 窗口化流控**。
> 窗口 = 100 字节（Grbl）/ 96 字节（Marlin）。这是它出图连续不停顿的**根本原因**，
> 比线简化（[[07-gcode-generation-deep]]）更本质。
>
> 与 hutuji 的关系：我们 `hutuji_job.cc::StreamToGrbl()` 用的是逐行等 ok，
> 每行都要付一次 WiFi 往返（~20–40ms）。奎享的做法可以让往返与运动**重叠**。
>
> **hutuji 状态勘误（2026-07-30）**：上面这段「逐行等 ok」已过时。S3 自
> `15f73c9` 起就是 character-counting 窗口化，窗口 `kWindow = 512` 字节
> ≈ 21 行在飞（`hutuji_job.cc:66-67`），奎享这套机制已落地。剩下的差异只是介质：
> 奎享支持三条传输通道：USB 串口（`machine/b/f.java`，jssc 事件驱动，我们 COM3 实测往返 ~6ms）、
> WiFi TCP（`machine/b/d.java`，Socket → port 23，与 hutuji Telnet 同构）、
> **蓝牙 SPP**（固件侧 `CLIENT_BT` + `BluetoothSerial`，商业上位机无线流式出图的主路径，
> `PAPER_AUTO_CHANGE_ON_BT_CONNECT` 量产验证；逆向 Java 侧未覆盖该 transport 子类）。
> 下方 6ms 是**串口实测基线**，不是奎享商业无线路径的 RTT（BT SPP RTT 未知）。
> hutuji 走 WiFi Telnet。
>
> **「20–40ms」这个估计值已被实测证否（2026-07-30 当日晚，同一台 MK38）**：
> 应用层往返下界 **77ms**、p50 **117–134ms**、p90 可到 **246ms**，比原估计高
> 3–6 倍（脚本 `results/.tmp/probe_rtt.py` / `probe_ws.py`，`results/` 不入库）。
> 主因是 Grbl 侧 WiFi 省电常开：`[ESP420]` 回报 `Sleep mode: Modem`
> （`Grbl_Esp32/src/WebUI/WebSettings.cpp:433`），而 `Signal: 98%`——不是链路弱。
> 因此本文下面凡以 20–40ms 为前提的收益/门槛推演都要按 77ms 下界重算，
> 重算结果与被推翻的结论见 `docs/troubleshooting.md` 笔控一节。

> **hutuji 状态勘误（2026-07-28）**：本文对奎享的逆向结论保留，但早期用来对照的
> hutuji abort 已变化。当前非换纸 abort 是 `!` 后用 `?` 轮询到 `Hold/Idle`（最长约 8s），
> 再标记并发送受限 `0x18`；reset 后显式执行 Z=0、等待 fresh `Idle` 再探授权。
> 固定 `! → 8s → 0x18`、reset 前排队/直发 Z 的描述均已被实机回归否决。

> **行号基准（2026-07-29 补注）**：本文的反编译行号已核到**主树**
> `kx/tools/dump_agent/decompiled_java/`。CFR 头部注释让偏移**逐文件不同**：
> `grbl/a.java`、`marlin/a.java` 比 `decompiled_full/` 大 3，`kat/b.java` 大 5，
> `ebb/a.java` 两树同号——跨文件不要套用统一偏移。

## 相关源文件

| 文件 | 角色 |
|------|------|
| `machine/f/b.java` | **窗口化流控核心**（双 Deque + 字节计数 + 泵 `g()`） |
| `machine/f/f.java` | 任务级喂料/编排器（**ok 事件驱动，非定时轮询**；暂停/恢复/中止/计时） |
| `machine/f/k.java` | 命令队列源（LinkedList，`e()`=peek / `c()`=pop） |
| `machine/f/e.java` | 队列源抽象（进度 = a/b） |
| `machine/f/g.java` | **SD/ESP 文件级执行器**（上传对话框 + 固件端打印；暂停走实时 `!`/`~`，旁路窗口引擎） |
| `machine/f/l.java` | 多文件/重复出图调度器（文件队列 + 重复次数 + 换纸对话框） |
| `machine/c.java` | 控制器基类，`a(String, boolean)` 是单行发送出口 |
| `machine/grbl/a.java` | `g()` 返回窗口大小 100 |

> **勘误（2026-07-29）**：旧表曾写 `f/c.java`、`f/i.java`——主树 `machine/f/` **无此二文件**（实有 `a b e f g h k l m`）。SD 路径是 `f/g.java`，多文件总调度是 `f/l.java`。详见 findings/19 §1、§7。

## 一、流控的两层结构（不是两套并存实现）

早前本文写「两个发送器并存」，措辞会误导。实际是**同一条链路的两层**：
`machine/f/f.java`（任务级喂料器，决定发哪些行、何时停）套在
`machine/f/b.java`（字节窗口在途记账 + 发送泵）之上，两层各自维护一份字节计数
（`f.n()` 的局部 `l2` 镜像 `b.e`），构成双层预填充。**只有一套字节窗口机制。**

### 1. `machine/f/b.java` — character-counting（出图主用）

```java
public final class b {
    private final BlockingDeque c = new LinkedBlockingDeque();  // 待发队列
    private final BlockingDeque d = new LinkedBlockingDeque();  // 在途队列（已发未确认）
    private long e;                                            // 在途字节数
```

`b` 是 **双队列 + 字节计数**结构：

- `c` = 待发送（pending）
- `d` = 已发出但还没收到 `ok`（in-flight）
- `e` = 在途字节总数

发送循环（`g()`，CFR 反编译后控制流有些糊，但语义清楚）：

```java
private synchronized void g() {
    do {
        object = (a)this.c.poll();          // 从待发队列取一条
        if (object != null) {
            this.e += (long)(((a)object).b() + 1);   // 在途字节 += 该行长度 + 1（换行符）
            this.d.add(object);                       // 移入在途队列
            this.b.a(((a)object).a(), false);         // 真正发送（不等 ok）
        }
        ...
    } while (a2 == null ? false
             : ((b)object).e + (long)a2.b() + 1L < (long)((b)object).b.g()   // ← 关键条件
               || ((b)object).b.g() == -1);
}
```

**循环条件是核心**：

```
在途字节 + 下一行长度 + 1 < 窗口大小
```

只要下一行**塞得进剩余窗口**，就继续发，**不等 `ok`**。这就是 Grbl 官方
[stream.py / character-counting protocol](https://github.com/gnea/grbl/blob/master/doc/script/stream.py)
的标准实现。

`g() == -1` 是**无限窗口**的逃逸口（不限流，全速灌）。

### 2. 收到 ok 后释放窗口（`onEvent`）

```java
case "FirmwareEventResponse_OK":
    if (!d.isEmpty()) {
        a a2 = (a)d.removeFirst();          // 在途队列出队（FIFO）
        if (a2.c() != -1) {
            // 发 Command_complete 事件（带行号，供 UI 显示进度）
        }
        e -= (long)(a2.b() + 1);            // 在途字节 -= 该行长度 + 1
    }
    g();                                     // 立刻尝试再发（窗口腾出来了）
```

每收一个 `ok`：在途队列出队一条、在途字节数减回去、**立刻再调 `g()` 补发**。
所以发送是**连续的流**，不存在「发一行→干等→再发一行」。

### 3. `machine/f/f.java` — 任务级喂料器（**不是**定时轮询）

> **勘误（2026-07-28 直读 `f.java` 全文 194 行推翻）**：本文早前在此处写
> 「`f.java` 是 10ms Timer 定时轮询 + 按在途**条数**限窗」，并附了一段
> `new Timer("Sender")` / `l2.n().size()` 的代码。**这段代码在反编译树里不存在**：
> `f.java` 全文**零个 Timer/schedule**，全树搜 `l2.n()` 零匹配。
> 「行数窗口」这套实现根本不存在——奎享**只有字节窗口一套**。

真实实现是**事件驱动的喂料函数** `n()`（`f.java:101-126`）：

```java
private void n() {
    if (this.d) return;                       // :105 暂停中 → 停灌（d = 暂停标志）
    if (!this.e) return;                      // :107 任务未运行
    this.a(new machine.d.a("Progress", this));      // :111 推进度事件
    long l2 = this.c.e();                            // :112 取 b 的当前在途字节
    while (this.a.a()) {                             // :113 还有待发行
        i.a a2 = this.a.e();                         // :115 peek 下一行（不弹出）
        boolean bl = this.b.g() > 0;                 // :116 窗口是否有限
        if (l2 + a2.b() + 1L >= this.b.g() && bl) break;  // :117 塞不下就停（R1 的 +1）
        a2 = this.a.c();                             // :118 真弹出
        this.c.a(a2);                                // :119 交给 b（b 内部再泵）
        l2 += a2.b() + 1;                            // :120 镜像累加
        if (this.c.a() || !bl) break;                // :121 单步或无限窗口则只发一条
    }
    if (this.c.b()) this.m();                        // :123 双队列皆空 → 任务完成（R4）
}
```

驱动源是 `onEvent`（`f.java:143-148`）：收到 `FirmwareEventResponse_OK` 就调 `n()`。
**没有任何定时器**——是「ok 到达即续灌」的纯事件驱动。

两点值得注意：

- **窗口算术在 `f.java` 与 `b.java` 各算了一遍**（`l2` 镜像 `b.e`），形成双层预填充。
- **`e()` = peek 是窗口化的硬前提**（`k.java:52` 的 `getFirst()` 不移除，`c():41` 才 `pop()`）。
  这正是我方 S2 必须先做预解析拿到 peek 能力的原因。

### 勘误：「奎享 error 只记录不中断」是错的（2026-07-28 直读源码推翻）

本文早前此处引用过一段署名 `machine/f/c.java` 的 `a(l l2, String string)` 代码，声称「记录错误但不中断」。**该代码和该文件都不存在**：主树 `machine/f/` 只有 `a b e f g h k l m`。历史上又把 SD 执行器 `f/g.java` 误记成 `f/c.java`；实际 `g` 也没有该 error 处理。全树搜 `n().remove(0)` 零匹配，`f/l.java` 也没有 `n()`/`m()` 方法。那段代码是早前会话的臆造，由它推出的「error 容忍」结论随之作废。

**实际实现：奎享对 `error` 同样中止。** 全树仅一处 `startsWith("error")`，
在 `machine/grbl/a.java:82-101`：

```java
if (((String)object).startsWith("error") && this.t()) {
    boolean bl = false;
    object = ((String)object).substring(6);          // 取 NN
    int n3 = Integer.parseInt((String)object);
    if (n3 == 120) {
        bl = true;                                   // ← error:120 白名单，静默忽略
    } else if (n3 == 9 && JOptionPane.showConfirmDialog(...) == 0) {
        bl = true;                                   // ← error:9(locked) 弹框问用户
        this.l();                                    //    确认则发 $X 解锁并继续
    }
    ...
    if (!bl) {
        // 其余一律发 FirmwareEventError
        this.a(new machine.d.b("FirmwareEventError", this));
    }
}
```

`FirmwareEventError` 的**唯一订阅者**是 `machine/f/b.java:28`，其处理（`:155`）调
`this.f()` = 清空待发队列 + 清空在途队列 + 窗口计数归零（`b.java:94-98`），
即**终止当前 job**。

**修正后的对照**：双方都对 error 中止。差别只在奎享有两个例外——
`error:120`（`Error.h:86` `AnotherInterfaceBusy=120`，本机固件也有定义）静默忽略；
`error:9`（locked）弹对话框，用户确认后 `$X` 解锁续打。
我们无人值守/语音远程场景不能弹框，故保持无条件中止——
**理由是「场景不允许交互」，不是「我们比奎享严格」**。

## 二、窗口大小

```java
// machine/grbl/a.java
@Override public final int g() { return 100; }

// machine/marlin/a.java
@Override public final int g() { return 96; }
```

| 固件 | 窗口 | 说明 |
|------|------|------|
| Grbl | **100** 字节 | `grbl/a.java:315-317`（主树；`decompiled_full/` 为 `:312-314`）。Grbl 官方 RX 缓冲是 128 字节，留 28 字节余量 |
| Marlin | **96** 字节 | `marlin/a.java:233-235`（主树；`decompiled_full/` 为 `:230-232`）。Marlin RX 缓冲典型 96–128，取下界 |
| KAT / K_MT | **96** | 无 override，直接继承 `marlin/a.g()`（`kat/b.java:42-43` extends `marlin.a`，主树）|
| ESP32Grbl | **100** | 无 override，继承 `grbl/a.g()` |
| EBB | **-1**（无限） | `ebb/a.java:93` |

窗口是**字节数**（`b.java`）。「单步/逐行」不是某机型的窗口值，而是
`f.java:26` 把 `任务强制 || Settings.singleStep` 传给 `b`，由 `b.java:66`
的「在途非空即停」退化成窗口 = 1 行。

### 勘误：「KAT 窗口 = 1」是错的（2026-07-28 二次深挖）

早前本节写「`machine/kat/b.java::g()` 返回 1 = 逐行等 ok」，**这是错的**。
反编译全树只有 4 个 `int g()` override（grbl/marlin/ebb + 一个无关的 `f/l.java`），
**`kat/b.java` 没有 `g()`**，从 Marlin 继承 = 96。窗口=1 的真实来源是 `singleStep`
布尔开关，与机型无关。原先「奎享自家机型最保守所以窗口=1」的解读不成立。

**对我们的意义**：窗口值必须有**实测依据**，不能照抄 100。

> hutuji 实测 + 源码核对（2026-07-28 更新，**修正了本文早前一处错误**）：
> - **早前写「`Bf` 第二个数实测 1200」是错的**。COM3 实测串口路径下该值**恒为 128**
>   （`<Idle|...|Bf:249,128|...>`），因为 `Report.cpp:603` 的 `DEFAULTBUFFERSIZE = 64`
>   与 `client_get_rx_buffer_available(CLIENT_SERIAL)` 走的是另一条分支。
> - **1200 只在 Telnet 客户端查询时出现** = `TELNETRXBUFFERSIZE`（`WebUI/TelnetServer.h:33`）。
>   `Report.cpp:605-607`：`client == CLIENT_TELNET` 才取 `telnet_server.get_rx_buffer_available()`。
>   所以「S3 自己 `?` 读到的余量是自己的」这条成立，但**不能用串口抓的数字代测**。
> - **planner 反压实测存在**：连灌 30 行短段时 `Bf` 第一数从 249 掉到 **222**，
>   证明 planner 深度是真实反压信号。
> - 详细设计见 `docs/design/p2-windowed-flow-control.md`。

## 三、命令队列源（`k.java` / `e.java`）

```java
public abstract class e {
    protected long a;              // 已消费条数
    protected long b;              // 总条数
    public final float b() { return (float)this.a / (float)this.b; }   // 进度 0..1
    abstract boolean a();          // 是否还有下一条
    abstract a c();                // 取下一条（消费）
    abstract a e();                // 看下一条（不消费）
}
```

`k.java` 是 `LinkedList` 实现：

```java
public k(List list) {
    for (int n2 = 0; n2 < list.size(); n2++) {
        this.c.add(new a((String)list.get(n2), n2 + 1));   // 行内容 + 行号（1-based）
    }
    this.b = this.c.size();
}
@Override public final a c() { ++this.a; return (a)this.c.pop(); }
@Override final a e() { return (a)this.c.getFirst(); }      // peek，供窗口判断用
```

**`e()`（peek）是窗口化流控的必要条件**——要判断「下一行塞不塞得进窗口」，
必须能先看长度再决定发不发。我们现在的 `StreamToGrbl` 是边解析边发，
没有 peek 能力，改窗口化需要先重构成「预解析成行列表」。

每条命令带**行号**（`n2 + 1`），`Command_complete` 事件回传行号供 UI 高亮当前行。

## 四、暂停 / 恢复 / 中止

### 暂停恢复：停灌 + 主动注入笔控（不是"纯停灌"）

> **勘误（2026-07-28 直读 `f/f.java` 全文 194 行推翻）**：本节早前引用过一段署名
> `machine/f/l.java` 的 `b()/c()/d()/e()` + "启动/停止 Sender Timer" 代码，并说
> "发送泵每 10ms 查一次"。**那段代码不存在**：`f/l.java` 的 `e()` 实际返回
> `Vector`（`l.java:74`），该文件无 `clear()`+Timer 的暂停语义；`f/f.java` 全文
> **零个 Timer/schedule**。这是本文第三处臆造代码（另两处见 §一.3 与 §一.4 的勘误框）。

真实实现在 `machine/f/f.java:56-68`，**两件事**：置标志停灌 + 往队列注入笔控命令。

```java
public final void e() {                  // :63  暂停
    this.d = true;                       //      置标志；n():105 靠它 return 停灌
    this.h = System.currentTimeMillis(); //      记本次暂停起点（用于扣净作画时长）
    this.c.a(new i.a(this.b.b(true)));   //      ← 注入抬笔 G1G90 Z0
}
public final void d() {                  // :56  恢复
    this.d = false;
    this.g += System.currentTimeMillis() - this.h;   // 累加暂停总时长
    this.c.a(new i.a(this.b.b(false)));  //      ← 注入落笔 G1G90 Z5
}
```

停灌本身在 `n():101-110` 的 `if (this.d) return`。注意**注入的笔控行是普通受控行**，
仍进 `b` 的队列并占窗口字节。

**关键差异**：这一层的暂停**不发 Grbl 实时字符 `!`**，只停止灌新行。
已经在 Grbl planner 里的行会继续走完，所以是**软暂停**——机器把缓冲里的动作做完才停。
但它同时**抬了笔**，所以长暂停不会渗墨；我方发 `!` 停得更快，代价是 Z 停在纸面。
详细对照与可借鉴项见 `docs/design/p2-windowed-flow-control.md` §8.3。

`grbl/a.java` 里另有真正的实时控制（`b()`/`z()`/`A()` 走 `machine/c` 基类），
以及中止：

```java
// machine/grbl/a.java:365
public final void c(boolean bl) {
    this.s = false;
    try {
        this.g.a((byte)24);      // ← 0x18 = Ctrl-X 软复位
    }
    ...
}
```

`0x18` 软复位用于**中止**。注意它同时把 `this.s = false` —— 这是
「首次笔控是否已发过 `G92 Z0`」的标志（见 [[03-init-sequence]]），
复位后下次落笔会**重新校准 Z 基准**。这个细节很重要：软复位会让 Grbl
丢失坐标系，所以奎享的完整会话会重发 `G92 Z0`。这不是下载文件生成契约；hutuji
同样把它作为受控会话旁路，首画/重画前 fresh `Idle` + 100ms 后发送，下载文件仍拒绝 G92。

### 状态查询驱动进度

```java
// machine/f/g.java
if (object.b().equals("FirmwareEventStatus")) {
    if (this.b.w()) {
        this.a(new com.kenjoy.kenjoycnc.machine.d.a("Progress", this));
    }
    this.a(new com.kenjoy.kenjoycnc.machine.d.a("Status", this));
}
```

进度不是靠计数推的，而是**由 `?` 状态报告事件驱动**——收到状态就发 Progress 事件。

> **勘误（2026-07-29，findings/21 §2.3-2.4）**：原写"`?` 的发送周期在 `machine/c` 基类的定时器里
> （`grbl/a.java:236` 有个 2000ms Timer）"，三处都不准。① 那个 Timer 在主树
> `machine/grbl/a.java:239-240` 的 `a(String,int)` 里，不在 `machine/c` 基类（`:236` 主树是
> `c(false)` 软复位）；② 它是 `schedule(task, 2000L)` **一次性**延时任务，不是 2000ms 周期；
> ③ WiFi 分支 `machine/c/a.java:43-45` 跳过 `super.a()`，该 Timer 在 ESP32-Grbl WiFi 下**根本不创建**。
> WiFi 的 `?` 由控制器自己的 `m()`（`grbl/a.java:379-408`）发，且**收到 `<...>` 才发下一个**。

## 五、出图总调度勘误：主树无 `machine/f/i.java`

早期本节引用的“`f/i.java` 文件级续打 + 每份文件前插 `G92`”代码在主树**不存在**，因此以下旧结论全部撤销：

- 不再声称奎享有“从第 N 个文件继续”的 `continue_wirte` 实现；
- 不再声称每份文件开头由 `f/i.java` 自动插 `G92 ... Z0`；
- 不再把不存在的 `i.java` 视为出图总调度。

可复核的实际编排层是 `machine/f/l.java`：它维护 `FileStatus`、多文件与重复轮次；读文件时仅 `trim()`、跳空行，并在 **K_MT** 路径前置 `controller.d()`，普通 Grbl 文件不在这里统一插入 G92（`f/l.java:161-180`）。普通会话的 Z 基准来自 `grbl/a.java` 首次 Stepper 笔控旁路 `G92 Z0`，见 findings/03；完整 UI/JobManager 生命周期见 findings/20。

## 六、与 hutuji 现状的差异对照

> **hutuji 列勘误（2026-08-14）**：本表「hutuji 现状」列冻结在窗口化落地前——「流控：逐行等 ok / 卡顿根源」「peek：无 peek」已被文首 2026-07-30 勘误取代（S3 `15f73c9` 起 512B 字节在途窗口、buffer 预解析具备 peek，S4 有纸实测通过）；「error:NN：直接放弃整幅」不适用于换纸行路径（`error:8` 原地重发，protocol §7）；§七-B 的窗口化「可行改进」已落地，`docs/protocol.md` §3 已于同日改写为窗口化契约。奎享列结论不变。

| 维度 | 奎享 | hutuji 现状 | 影响 |
|------|------|-------------|------|
| **流控** | character-counting 窗口 100 字节 | 逐行等 ok | **卡顿根源**：每行付一次往返 |
| peek 能力 | `e()` 可看下一行长度 | 边解析边发，无 peek | 改窗口化的前置障碍 |
| ok 处理 | 出队 + 释放窗口 + 立刻补发 | 解除阻塞，继续下一行 | — |
| `error:NN` | **也中止**（清空双队列），但有 2 个白名单码 | 直接放弃整幅 | 双方同向；差别在白名单与交互解锁 |
| 暂停 | 软暂停（停灌新行） | `!` 进给保持 + 停灌 | 我们更硬（能立即停） |
| 普通 stop | 队尾追加抬笔+回原点，非实时；`drawing→wait` | `!` → 状态轮询后按任务语义处理 | 我方停得更快；不照搬队尾收尾 |
| reset2 | 直接 `0x18` + 清 Z 基准标志 + transport reset hook | Hold/Idle 后受限 `0x18` → reset 后 Z=0/fresh-`Idle` | 我方先确认运动已保持 |
| 进度 | `?` 状态事件驱动 | 行计数 + 5s 定时推送 | — |
| 坐标系 | 连接/首次 Stepper 笔控前旁路 `G92 Z0` | 首画/重画前 fresh `Idle` + 100ms 后旁路固定 `G92 Z0` | 同为会话编排；下载文件均无 G92 |
| 断线续打 | 串口重连框以 XY 非零判可恢复，再 `pollLast()` | 断连后从 checkpoint/首行受控重画 | 奎享判据与重发均有缺陷（findings/20） |
| 注释 | 不处理（自产无注释） | 剥 `;` | 我们必须保留 |

## 七、对 hutuji 的可行改进（按性价比排序）

### A. 补齐会话旁路 `G92 Z0`，文件仍禁 G92

9 个缓存文件字节完全相同，只能算一个唯一产物；该下载内容确实**一个 `G92` 都没有**。
但这不能推出完整出图会话无 G92：反编译 `grbl/a.java:253-263` 会在连接/首次笔控前旁路发送。

奎享本机 `$1=25`，电机失能后弹簧回到自然抬笔位，而 Grbl 计数不会随机械回弹变化；开头两次
`G1G90 Z0.0F10000` 只是缓存兼容的幂等运动，不能校准。现行对齐要求每次首画/重画前先等
fresh `Idle` + 100ms，再由 S3 旁路固定 `G92 Z0`，随后窗口化连续发送原下载文件。
下载文件校验器仍拒绝 `G92`，且不改 `$1/$3`。详见 [[02-gcode-template]]、
[[12-summary-and-action-list]] §五。

### B. 窗口化流控（收益最大，但需重构）

现状每行往返**实测 p50 117–134ms、下界 77ms**（2026-07-30 实测，非早前写的 20–40ms
估计值，见 `docs/troubleshooting.md` 笔控一节），`linesimplify` 已把行数从 9266 降到 172，
所以**当前卡顿已缓解**。但窗口化能让往返完全隐藏，是更彻底的解法。

改动量：
1. `StreamToGrbl` 先把 buffer 预解析成 `vector<string>`（获得 peek 能力）
2. 维护 `in_flight_bytes` + 在途队列
3. `Pipe::WaitResponse` 改成「消费一个 ok 事件」而非「阻塞等一个 ok」
4. 窗口起步 512 字节（依据 Telnet `_RXbuffer` = 1200，见设计文档 §3）

**完整设计已独立成文**：`docs/design/p2-windowed-flow-control.md`
（含官方 `stream.py` 的 R1–R4 四条必抄规则、缓冲链路反压分析、四阶段落地）。

**风险**：这是 `docs/protocol.md` 定的契约（§3 逐行流控），改动需要
两侧同步评审 + 实机验证。

> **2026-07-28 实测修正**：窗口化的收益**只存在于高往返链路**。
> COM3 串口直连做过 A/B（183 行，窗口 1 vs 512）：40.38s vs 40.42s，**无差异**——
> 串口往返 6ms，瓶颈 100% 是电机运动时间，无可隐藏的延迟。
> 所以这条改进的**唯一适用场景是 S3 的 WiFi Telnet 链路**（往返实测下界 77ms、
> p50 117–134ms，见 `docs/troubleshooting.md` 笔控一节；比本文早前估的 20–40ms 高一个量级，
> 窗口化的收益因此比原估更大），
> 且必须在 Telnet 链路上验收，串口实测不能作为收益证据。

### C. `error:NN` 处理（双方都中止，理由不同，不改）

**2026-07-28 勘误**：本文早前写「奎享 error 只记录不中断」是错的，见上文
「二次勘误」。真实情况是**双方都中止**，差别只在两个白名单码和交互解锁：

| | 奎享 | hutuji |
|---|---|---|
| 一般 `error:NN` | 清空待发+在途+计数（`b.java:155`→`f()`），终止 job | `return false` 放弃整幅 |
| `error:120` | **静默忽略**（`grbl/a.java:88` `bl=true`） | 无特例 |
| `error:9`（locked） | **弹框问用户**，确认则 `$X` 解锁继续（`:90-93`） | 无交互，直接失败 |

**保持现状**。理由不是「我们更严格」（这个说法已被推翻），而是
**无人值守语音场景不能弹框**——`error:9` 我们没法问用户，只能失败上报。
`error:120` 本机固件**也有定义并可产生**（`Error.h:86` `AnotherInterfaceBusy=120`；
`Serial.cpp:191` SD 占用时以空格形态 `error 120` 发出、`WebSettings.cpp:167` WebCommand
checker 拒时返回），但 hutuji 链路实际触达不到——换纸族命令挂 anyState、不启 SD——
fail-closed 方向不变，无需特例。（2026-08-13 勘误：早前「本机不产此码」与实文不符。）

### D. 暂停机制对照（不改，但可借鉴抬笔）

我们的 `hutuji.pause` 发 `!` 立即进给保持；奎享是应用层停灌
（`f.java:105` 的 `if (d) return`）**并主动注入一条抬笔**（`f.java:67`）。
停得快是我们的优势，**但笔停在纸上会渗墨**——注入抬笔这点值得借鉴。
详见 `docs/design/p2-windowed-flow-control.md` §8.3。

## 八、证据边界

- 全部结论来自 CFR 反编译源码静态阅读，**未运行奎享实测抓包**。
- CFR 对 `b.java::g()` 的控制流还原不完美（`do-while` 条件里有冗余
  `bl2` 变量和 `!true` 死分支），但字节计数与窗口判断的语义明确。
- 窗口值 100/96 是硬编码返回值，无歧义。
- 「奎享出图不卡顿」是**推断**（基于窗口化流控的原理），
  未在同一台机器上做奎享 vs hutuji 的对比实测。
