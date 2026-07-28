# 09 · 流控引擎深度剖析（本次逆向最重要的发现）

> 结论先行：**奎享不是「一行一 ok」的逐行流控，而是 Grbl 官方推荐的 character-counting 窗口化流控**。
> 窗口 = 100 字节（Grbl）/ 96 字节（Marlin）。这是它出图连续不停顿的**根本原因**，
> 比线简化（[[07-gcode-generation-deep]]）更本质。
>
> 与 hutuji 的关系：我们 `hutuji_job.cc::StreamToGrbl()` 用的是逐行等 ok，
> 每行都要付一次 WiFi 往返（~20–40ms）。奎享的做法可以让往返与运动**重叠**。

> **hutuji 状态勘误（2026-07-28）**：本文对奎享的逆向结论保留，但早期用来对照的
> hutuji abort 已变化。当前非换纸 abort 是 `!` 后用 `?` 轮询到 `Hold/Idle`（最长约 8s），
> 再标记并发送受限 `0x18`；reset 后显式执行 Z=0、等待 fresh `Idle` 再探授权。
> 固定 `! → 8s → 0x18`、reset 前排队/直发 Z 的描述均已被实机回归否决。

## 相关源文件

| 文件 | 角色 |
|------|------|
| `machine/f/b.java` | **窗口化流控核心**（双 Deque + 字节计数 + 泵 `g()`） |
| `machine/f/f.java` | 任务级喂料/编排器（**ok 事件驱动，非定时轮询**；暂停/恢复/中止/计时） |
| `machine/f/k.java` | 命令队列源（LinkedList，`e()`=peek / `c()`=pop） |
| `machine/f/e.java` | 队列源抽象（进度 = a/b） |
| `machine/f/c.java` | **KAT/WiFi 文件级调度器**（发 `[ESP913]` 上传 SD；**与 error 处理无关**） |
| `machine/f/l.java` | 多文件/重复出图调度器（文件队列 + 重复次数 + 换纸对话框） |
| `machine/f/i.java` | 出图总调度（多文件队列 + 续打 + 进度事件） |
| `machine/c.java` | 控制器基类，`a(String, boolean)` 是单行发送出口 |
| `machine/grbl/a.java` | `g()` 返回窗口大小 100 |

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

本文早前此处引用过一段署名 `machine/f/c.java` 的 `a(l l2, String string)` 代码，
声称「记录错误但不中断」。**该代码在反编译树里不存在**：
`machine/f/c.java` 全文（127 行）是 KAT/WiFi 的文件级调度器（发 `[ESP913]` 上传 SD），
没有任何 error 处理；全树搜 `n().remove(0)` 零匹配，`f/l.java` 也没有 `n()`/`m()` 方法。
那段代码是早前会话的臆造，由它推出的「error 容忍」结论随之作废。

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
`error:120`（原版 Grbl 无此码，应为其固件私有）静默忽略；
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
| Grbl | **100** 字节 | `grbl/a.java:312`。Grbl 官方 RX 缓冲是 128 字节，留 28 字节余量 |
| Marlin | **96** 字节 | `marlin/a.java:230`。Marlin RX 缓冲典型 96–128，取下界 |
| KAT / K_MT | **96** | 无 override，直接继承 `marlin/a.g()`（`kat/b.java:37` extends marlin.a） |
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
丢失坐标系，所以必须重发 `G92 Z0`。

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
`?` 的发送周期在 `machine/c` 基类的定时器里（`grbl/a.java:236` 有个 2000ms Timer）。

## 五、出图总调度（`machine/f/i.java`）

`i.java` 是多文件出图的编排层，有几个值得注意的设计：

### 1. 续打（断点续画）

```java
int n3 = v2.e();                    // 上次画到第几个文件
if (n2 == 0 && n3 > 0) {
    int n4 = JOptionPane.showConfirmDialog(..., "continue_wirte", ..., 1, 3);
    if (n4 == 0)      this.d = n3;              // 是 → 从断点继续
    else if (n4 == 1) v2.a(FileStatus.wait);    // 否 → 全部重画
    else return;                                 // 取消
}
```

**文件级**续打（不是行级）。我们的 `hutuji.repeat` 是整幅重画，
奎享这个是「多文件队列里从第 N 个文件继续」。

### 2. 每份文件开头重发坐标系

```java
if (super.k()) {
    arrayList.add("G92Y" + f2 + "Z0");     // KAT 机器：带 Y 偏移
} else {
    arrayList.add(((i)object).e.d());       // Grbl：G92 X<offsetX> Y<offsetY> Z0
}
// 然后逐行读文件内容追加
while ((string = reader.readLine()) != null) {
    if (av.g(string = string.trim())) continue;   // 跳空行
    arrayList.add(string);
}
g.a(new k(arrayList));                      // 整份变成命令队列
g.a();                                       // 启动
```

**每份文件出图前都先发 `G92 ... Z0`**，不依赖上一份的坐标系状态。
这是幂等设计——即使中间发生过软复位，每份文件也能自己建立基准。

### 3. 文件读取时预处理

- `trim()` 每行
- 跳过空行（`av.g()` 是 isEmpty 判断）
- **不做注释剥离**（说明奎享自己生成的 G-code 里没有注释）

我们 `StreamToGrbl` 会剥 `;` 注释 —— 这是必要的，因为
vpype-gcode 可能产出注释行。

## 六、与 hutuji 现状的差异对照

| 维度 | 奎享 | hutuji 现状 | 影响 |
|------|------|-------------|------|
| **流控** | character-counting 窗口 100 字节 | 逐行等 ok | **卡顿根源**：每行付一次往返 |
| peek 能力 | `e()` 可看下一行长度 | 边解析边发，无 peek | 改窗口化的前置障碍 |
| ok 处理 | 出队 + 释放窗口 + 立刻补发 | 解除阻塞，继续下一行 | — |
| `error:NN` | **也中止**（清空双队列），但有 2 个白名单码 | 直接放弃整幅 | 双方同向；差别在白名单与交互解锁 |
| 暂停 | 软暂停（停灌新行） | `!` 进给保持 + 停灌 | 我们更硬（能立即停） |
| 中止 | `0x18` 软复位 + 清 Z 基准标志 | `!` → 轮询 `Hold/Idle` → 受限 `0x18` → reset 后 Z=0/fresh-`Idle` | 我们先确认运动已保持，并限制自动解锁范围 |
| 进度 | `?` 状态事件驱动 | 行计数 + 5s 定时推送 | — |
| 坐标系 | 每份文件重发 `G92 ... Z0` | 无 G92 | **落笔失败根源之一** |
| 续打 | 文件级断点续画 | 整幅 repeat | — |
| 注释 | 不处理（自产无注释） | 剥 `;` | 我们必须保留 |

## 七、对 hutuji 的可行改进（按性价比排序）

### A. ~~补 `G92 Z0`~~ —— **已作废，反向执行**

早前此处写「补 `G92 Z0` 是落笔失败的直接原因之一，必须加」。
**这条已被奎享 9 份真实产物推翻**：产物里**一个 `G92` 都没有**，
奎享靠开头连发两次 `G1G90 Z0.0F10000`（真实运动）做机械自校准。
反编译里的 `G92 Z0`（`grbl/a.java` 读设置后）属**连接/点动路径，不进文件输出**。

现行做法（`hutuji_gwrite.toml`）已按真实产物对齐：**不发 `G92 Z0`**，
改为两次 `G1G90 Z0.0F10000`。另有一层原因：固件宏 `USE_M3_M5_AS_PEN_UP_DOWN`
直接用机械坐标（`GCode.cpp:1488`），一旦发过 `G92` 就与工件坐标错开。
详见 [[02-gcode-template]]、[[12-summary-and-action-list]] §五。

### B. 窗口化流控（收益最大，但需重构）

现状每行 ~20–40ms 往返，`linesimplify` 已把行数从 9266 降到 172，
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
> 所以这条改进的**唯一适用场景是 S3 的 WiFi Telnet 链路**（往返 20–40ms），
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
`error:120` 是奎享固件私有码，我们的 Grbl_Esp32 不产生它，无需特例。

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
