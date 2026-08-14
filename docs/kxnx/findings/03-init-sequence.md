# 发现 03：连接/校准/原点序列

> 源文件：`decompiled_full/com/kenjoy/kenjoycnc/machine/grbl/a.java`
>
> **行号基准（2026-07-29 补注）**：本文正文所有 `a.java:NNN` 都是 **`decompiled_full/` 树**的行号
> （旧版头部写的 `decompiled/...` 路径不存在）。换算到主树
> `D:/GitHub/kxnx/kx/tools/dump_agent/decompiled_java/` 时，`machine/grbl/a.java` 一律 **+3**
> （例：本文 `:250-260` 的笔控入口 `a(boolean)` 在主树是 `:253-263`，`:295-298` 的 `b()` 是 `:298-301`）。
> 偏移逐文件不同，别跨文件套用；见 `docs/kxnx/README.md` §「反编译源码树」。
> 本轮只校正基准与路径，**未逐句复核握手内容**。

## 奎享连接 Grbl 后的初始化序列

### 1. 检测到 Grbl 就绪（识别 banner）

```java
// a.java:211
protected boolean g(String string) {
    return string.startsWith("Grbl");   // 收到 "Grbl 1.1..." banner → 视为对端在线
}
```

### 2. 收到 banner 后发 `$I`（查版本）

```java
// a.java:241-247
@Override
public void b(String string, int n2) {
    super.b(string, n2);
    if (this.g instanceof com.kenjoy.kenjoycnc.machine.b.f) {
        this.g.a(true);
    }
    this.a("$I", true);    // 发 $I 查版本信息
}
```

### 3. 收到 `[VER:` 应答 → 触发 `b_()`（就绪）

`b_()` 在收到 `[VER:...]` 时被调用（`a.java:104-107` 解析 VER），里面：

```java
// a.java:180-188
protected void b_() {
    ...FirewareEventResponse_OK...
    if (this.o) {                          // o = 已查过 $$ 配置
        ...FirewareEventSettings...
        this.o = false;
        this.a("G92 Z0", true);            // ← 把当前位置设为 Z=0
    }
}
```

**关键：连接就绪后，若 `type == Stepper`，发 `G92 Z0` 校准 Z 基准。**

但这只是其中一处。更准确的位置在首次笔控时——

### 4. 首次笔控前必发 `G92 Z0`（更可靠的入口）

```java
// a.java:250-260
@Override
public final void a(boolean bl) {        // bl = 抬笔/落笔
    if (!this.s) {                        // s = "已发过 G92 Z0" 标志
        this.s = true;
        GrblSettings grblSettings = ...;
        if (grblSettings.type == Type.Stepper) {
            this.a("G92 Z0", false);      // ← 首次笔控前，校准 Z 基准
        }
    }
    a2.a(a2.b(bl), true);                 // 再发实际笔控 G1Z...
}
```

**语义：连接后第一次抬笔或落笔时，先把"当前 Z 物理位置"声明为 Z=0，之后所有 Z 值都基于这个基准。**

这对带弹簧的 Z 轴尤其重要——上电时 Z 物理位置不定（弹簧可能拉到任意位），靠 `G92 Z0` 把"现在的位置"定为抬笔基准 Z=0，后续 Z5 才能稳定表达"向下走 5mm 触纸"。

### 5. 归零（用户操作，非自动）

```java
// a.java:295-298
@Override
public final String b() {
    return a("G90G0 X0Y0");    // 归 XY 原点（不归 Z）
}
```

```java
// a.java:331-333
@Override
public final String d() {
    return "G92 X" + offsetX + " Y" + offsetY + " Z0";   // 设当前位置为原点
}
```

归零是用户点 UI 按钮触发，不是每次出图自动发。奎享不归 Z（弹簧 + 无限位开关，归 Z 无意义）。

### 6. 解锁（ALARM 时）

```java
// a.java:342-348
public final void k() { this.a("$H", true); }   // 归位（homing，我们无限位开关用不了）
public final void l() { this.a("$X", true); }   // 解锁 ALARM
```

## 完整时序（连接到首次出图）

```
PC → Grbl: (TCP/串口连上)
Grbl → PC: "Grbl 1.1h:..."              ← banner（g() 识别）
PC → Grbl: "$I\n"                        ← 查版本
Grbl → PC: "[VER:1.1h...:]"             ← 触发 b_()
PC → Grbl: "G92 Z0\n"                    ← 校准 Z 基准（若 Stepper）
... 用户点"开始画" ...
PC → Grbl: "G21\nG90\nF1500\n"          ← 出图头
PC → Grbl: "G1 X.. Y..\n"               ← 空走到起点
PC → Grbl: "G1G90 Z5.0F3000\n"          ← 落笔（首次笔控前已 G92 Z0）
...
```

## 对 hutuji 的含义

> **已落地勘误（2026-08-14，R16）**：本节写于对齐决策前，两处均已过时——「出图直接发 M3」已被 2026-07-29 定案推翻（Stepper 笔控 = 纯 `G1 Z` 运动，不用 M3/M5）；「在 `document_start` 开头加 `G92 Z0`」**已被否决**——下载文件禁止含 G92（`validate_gcode` 拒收），现行方案是 S3 固件在每次首画/重画前 fresh `Idle` + 100ms 后**旁路**发送 `G92 Z0`（`PreparePenOrigin`），与奎享「连接/首次笔控前旁路」同构。奎享侧的事实记录（§1–§6 与完整时序）仍然有效。

我们当前流程：
- 手动推死点归零（XY），上电 → Z 物理位置不定
- 出图直接发 M3（走固件宏），**没有 G92 Z0 校准**

奎享流程：
- 连接后/首次笔控前发 `G92 Z0` 校准 Z 基准
- 出图发 `G1 Z`（直接 Z 运动）

**对齐要点**：
1. 在 `document_start` 开头加 `G92 Z0\n`，每次出图自动校准 Z 基准（等价奎享的首次笔控前校准）。
2. 这要求**出图开始时笔处于"抬起的自然位"**（弹簧拉到最高），否则 Z0 基准不准。
3. 配合现有的"手动推死点归 XY 零"，操作流程仍可接受。

详见 `alignment/plan.md`。
