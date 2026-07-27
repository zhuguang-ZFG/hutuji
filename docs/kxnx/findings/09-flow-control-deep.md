# 09 · 流控引擎深度剖析（本次逆向最重要的发现）

> 结论先行：**奎享不是「一行一 ok」的逐行流控，而是 Grbl 官方推荐的 character-counting 窗口化流控**。
> 窗口 = 100 字节（Grbl）/ 96 字节（Marlin）。这是它出图连续不停顿的**根本原因**，
> 比线简化（[[07-gcode-generation-deep]]）更本质。
>
> 与 hutuji 的关系：我们 `hutuji_job.cc::StreamToGrbl()` 用的是逐行等 ok，
> 每行都要付一次 WiFi 往返（~20–40ms）。奎享的做法可以让往返与运动**重叠**。

## 相关源文件

| 文件 | 角色 |
|------|------|
| `machine/f/b.java` | **窗口化流控核心**（双 Deque + 字节计数） |
| `machine/f/f.java` | 定时发送泵（10ms Timer，行数窗口版） |
| `machine/f/k.java` | 命令队列源（LinkedList，逐行 pop） |
| `machine/f/e.java` | 队列源抽象（进度 = a/b） |
| `machine/f/c.java` | ok 回调处理（出队 + 错误记录） |
| `machine/f/l.java` | 任务状态容器（计数/耗时/暂停标志） |
| `machine/f/i.java` | 出图总调度（多文件队列 + 续打 + 进度事件） |
| `machine/c.java` | 控制器基类，`a(String, boolean)` 是单行发送出口 |
| `machine/grbl/a.java` | `g()` 返回窗口大小 100 |

## 一、两套并存的流控实现

奎享代码里有**两个**发送器，用途不同。

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

### 3. `machine/f/f.java` — 定时轮询（行数窗口版）

```java
public final void a(l l2) {
    this.c = new Timer("Sender", true);
    this.c.schedule((TimerTask)new d(this), 0L, 10L);   // 每 10ms 跑一次
}

final void a() {
    if (this.d == null || !this.d.f()) return;      // 任务未运行
    if (this.d.g()) return;                        // ← 暂停中，直接返回（不发）
    l l2 = this.d;
    if (l2.n().size() >= this.b.g()) return;       // ← 在途「条数」已达窗口，不发
    String string = l2.a().a();                    // 取下一行
    if (string == null) return;                    // 队列空
    l2.n().add(string);                            // 记入在途
    this.b.a(string, false);                       // 发送（不等 ok）
    l2.a(l2.h() + 1);                              // 已发计数 +1
}
```

这一版更简单：**10ms 定时轮询 + 按「在途条数」限窗**（而非字节数）。
同样不等 `ok`。`f` 用于 `machine/f/l.java` 那套任务模型。

对应的 ok 处理在 `machine/f/c.java`：

```java
public void a(l l2, String string) {
    if (l2.n().isEmpty()) return;
    l2.n().remove(0);              // 在途出队
    l2.b(l2.i() + 1);              // 已确认计数 +1
    if (string.startsWith("error")) {
        l2.a(string);              // 记录错误，但不中断
    }
}
```

注意：**`error` 只记录不中断**。奎享对单行错误是容忍的——记下最后一个错误
（`l.m()` 可读），继续发后面的行。这和我们 `StreamToGrbl` 遇 `error:NN` 直接
`return false` 放弃整幅画的策略不同。

## 二、窗口大小

```java
// machine/grbl/a.java
@Override public final int g() { return 100; }

// machine/marlin/a.java
@Override public final int g() { return 96; }
```

| 固件 | 窗口 | 说明 |
|------|------|------|
| Grbl | **100** 字节 | 保守值。Grbl 官方 RX 缓冲是 128 字节，留 28 字节余量防溢出 |
| Marlin | **96** 字节 | Marlin RX 缓冲典型 96–128，取下界 |
| KAT | **1** | `machine/kat/b.java::g()` 返回 1 = **逐行等 ok**（无窗口） |

窗口是**字节数**（`b.java`）或**条数**（`f.java`），取决于用哪套发送器。
`b.java` 的字节版更精确，是 Grbl 官方推荐做法。

### KAT 窗口 = 1 是个关键反例

`machine/kat/b.java`：

```java
@Override
public int g() { return 1; }
```

KAT 是奎享**自家机型**，却用最保守的逐行模式（窗口 1 = 发一条等一条）。
这说明窗口大小是**按固件 RX 缓冲能力逐个配置**的，不是一刀切的优化：

- Grbl / Marlin 是通用固件，RX 缓冲规格公开（128 / 96 字节），可以安全开窗口
- KAT 是私有固件，或缓冲小、或行为不确定，奎享选择不冒险

**对我们的意义**：窗口值必须有**实测依据**，不能照抄 100。
我们的 Grbl_Esp32 改过缓冲（`Bf` 第二个数实测 1200，远大于原版 128），
所以既不能直接用 100（太保守，浪费能力），也不能想当然开到 1200
（Telnet 链路下这个数是否仍代表真实可用余量，未验证）。
取值前必须实测：见本文 §八「若要落地窗口化流控」的验证步骤。

> hutuji 实测：我们的 Grbl_Esp32 `?` 状态报告里 `Bf:249,1200` —— 第二个数
> 1200 是 **RX 缓冲余量**（远大于原版 Grbl 的 128）。这个数字来自
> `Grbl_Esp32` 加大的串口缓冲。理论上我们的窗口可以开到 ~1000 字节，
> 但要实测确认 Telnet 链路下这个数是否仍然可信。

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

### 暂停恢复：只停发送泵，不发实时字符

```java
// machine/f/l.java
public final void b() {                  // 开始
    this.e = true;   this.f = false;
    this.i = System.currentTimeMillis();
    this.b.a(this);                      // 启动 Sender Timer
}
public final void c() { this.f = true; }   // 暂停 = 置标志
public final void d() { this.f = false; }  // 恢复 = 清标志
public final void e() {                    // 停止
    this.e = false;  this.f = false;
    this.c.clear();                        // 清在途
    this.b.b(this);                        // 停 Timer
}
public final boolean g() { return this.f; }   // 供发送泵查询
```

发送泵每 10ms 查一次 `g()`，暂停时直接 `return` 不发新行。

**关键差异**：这一层的暂停**不发 Grbl 实时字符 `!`**，只是停止灌新行。
已经在 Grbl planner 里的行会继续走完。所以奎享这个"暂停"是**软暂停**——
机器会把缓冲里的动作做完才停，不是立即减速。

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
| `error:NN` | 记录不中断 | 直接放弃整幅 | 我们更严格（可能过严） |
| 暂停 | 软暂停（停灌新行） | `!` 进给保持 + 停灌 | 我们更硬（能立即停） |
| 中止 | `0x18` 软复位 + 清 Z 基准标志 | `!` → 8s → `0x18` | 我们有 grace period |
| 进度 | `?` 状态事件驱动 | 行计数 + 5s 定时推送 | — |
| 坐标系 | 每份文件重发 `G92 ... Z0` | 无 G92 | **落笔失败根源之一** |
| 续打 | 文件级断点续画 | 整幅 repeat | — |
| 注释 | 不处理（自产无注释） | 剥 `;` | 我们必须保留 |

## 七、对 hutuji 的可行改进（按性价比排序）

### A. 补 `G92 Z0`（必须，且最简单）

见 [[03-init-sequence]]。这是当前落笔失败的直接原因之一。
一行改动，风险极低。

### B. 窗口化流控（收益最大，但需重构）

现状每行 ~20–40ms 往返，`linesimplify` 已把行数从 9266 降到 172，
所以**当前卡顿已缓解**。但窗口化能让往返完全隐藏，是更彻底的解法。

改动量：
1. `StreamToGrbl` 先把 buffer 预解析成 `vector<string>`（获得 peek 能力）
2. 维护 `in_flight_bytes` + 在途队列
3. `Pipe::WaitResponse` 改成「消费一个 ok 事件」而非「阻塞等一个 ok」
4. 窗口大小取值需实测（Grbl_Esp32 的 `Bf:,1200` 暗示可以开大）

**风险**：这是 `docs/protocol.md` 定的契约（§7 逐行流控），改动需要
两侧同步评审 + fz 门禁 + 实机验证。**不建议现在做**——先把落笔修好，
出图跑通，再考虑。

### C. `error:NN` 容忍策略（存疑，暂不改）

奎享记录不中断。但我们的场景不同：奎享是本地串口，用户看着屏幕；
我们是语音远程出图，用户不在跟前。单行 error 继续画可能产出废图。
**保持现状（严格中止）更安全**。

### D. 软暂停 vs 硬暂停（我们的实现更好）

我们的 `hutuji.pause` 发 `!` 能立即减速停住，奎享的软暂停要等
planner 排空。用户说「暂停」时期望立刻停 —— 我们的行为更符合直觉。
**不改**。

## 八、证据边界

- 全部结论来自 CFR 反编译源码静态阅读，**未运行奎享实测抓包**。
- CFR 对 `b.java::g()` 的控制流还原不完美（`do-while` 条件里有冗余
  `bl2` 变量和 `!true` 死分支），但字节计数与窗口判断的语义明确。
- 窗口值 100/96 是硬编码返回值，无歧义。
- 「奎享出图不卡顿」是**推断**（基于窗口化流控的原理），
  未在同一台机器上做奎享 vs hutuji 的对比实测。
