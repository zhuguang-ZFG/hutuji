# 深度逆向：Grbl 控制器 `machine/grbl/a.java`

> 源文件：kxnx 仓 `kx/tools/dump_agent/decompiled_java/com/kenjoy/kenjoycnc/machine/grbl/a.java`（CFR 反编译，混淆名；**行号基准=主树 `decompiled_java/`**，在 `decompiled_full/` 树里同文件行号小 3，见 `docs/kxnx/README.md`「反编译源码树」）
>
> 这是奎享与 Grbl 写字机交互的**完整控制器**。本文逐方法剖析，覆盖：连接握手、状态解析、笔控、校准、归零、点动、休眠、解锁、ESP 命令路由。是 hutuji 对齐奎享的**权威依据**。

## 类身份与继承

```java
public class a extends c {   // com.kenjoy.kenjoycnc.machine.c
```

`a` 是 Grbl 固件的具体控制器，继承 `c`（固件控制器抽象基类）。`c` 又实现 `com.kenjoy.kenjoycnc.machine.e`（固件接口）。整个继承链：

```
e (接口) ← c (抽象：收发 + 事件) ← a (Grbl 具体实现)
```

其他兄弟：`machine/marlin/a.java`、`machine/ebb/a.java`、`machine/kat/a.java`、`machine/awauto/a.java`，每种固件一个 `a`。

## 成员字段（混淆名还原）

| 字段 | 类型 | 推断含义 |
|------|------|----------|
| `l` | Pattern | `[0-9]*\.[0-9]*` — 从 Grbl banner 提取版本号（如 "1.1h"）|
| `m` | Pattern | `<(.*?)>` — 匹配 `<Idle|...>` 状态报告 |
| `n` | Pattern | `\$(\d+)=([^ ]*)` — 匹配 `$1=25` 配置应答 |
| `o` | boolean | "正在/已查 $$ 配置"标志（触发 b_ 里发 G92 Z0）|
| `p` | boolean | （次要状态标志）|
| `q` | long | `1000L` — 常量（延时用）|
| `r` | int | 状态报告重试计数 |
| `s` | boolean | **"已发过首次 G92 Z0"标志**（Stepper 笔控校准用）|
| `t` | String | `[VER:]` 解析出的 build info（版本字符串）|

## 方法分类索引

### 通信层（继承自 c，a 覆写部分）

| 方法 | 行 | 职责 |
|------|----|------|
| `a()` | 40 | 返回行分隔符 `"\n"` |
| `g(String)` | 210 | 识别 Grbl banner：`string.startsWith("Grbl")` |
| `c(String)` | 45 | **应答分发器**：收到一行，按内容路由（见下）|

### 应答分发 `c(String)`（行 45-154，核心）

奎享收到 Grbl 的每一行都进这里，按内容分支：

| 收到内容 | 处理 |
|----------|------|
| `"ok"` | 调 `b_()`（就绪钩子，可能发 G92 Z0）|
| `"Grbl"` 开头 | 提取版本号；触发连接成功事件 |
| `$N=...`（配置项）| 正则解析，写入 `GrblSettings.kv` |
| `"<...>"` 状态报告 | 调 `h()` 解析状态机位置 |
| `"error<N>"` | 弹错误提示；`error:9` 询问解锁 |
| `"[VER:...]"` | 提取 build info 存 `t` |
| `"[ESP<N>]<msg>"` | ESP 命令路由（换纸、状态等，见下）|

### ESP 命令路由（行 112-152）

`[ESP<N>]` 是 Grbl_Esp32 的 WebUI 扩展命令。奎享处理：

| ESP 编号 | 动作 |
|----------|------|
| 1 | `ui.a.c()` — 刷新某 UI 面板 |
| 2 | 换纸相关：`f.a().e().r().b().e()` + `ui.a.f()` |
| 3 | 换纸相关：`f.a().e().r().b().d()` + `ui.a.f()` |
| 4 | `ui.a.e()` — 另一 UI 刷新 |
| 5 | 网络消息对话框（含 WiFi/通知）|
| 6 | **`i(string)` — 写入文本到当前 Tab**（NoteTab/WordTab/GridTab/ExcelTab）|

`[ESP6]` 特别值得注意：这是写字机把文本"推"给上位机的通道（比如换纸状态文本）。我们 hutuji 的 `docs/protocol.md §9` 讨论的 `[ESP901]` 等就是同一族命令。**奎享只处理 ESP1-6，不处理 901**——说明奎享不靠 901 查换纸状态，而是靠 ESP2/3 的事件推送。

> **对 hutuji 的含义**：我们之前纠结 Telnet 收不到换纸消息（CLIENT_SERIAL 路由问题，见记忆 `hutuji-telnet-paper-msg-routing`）。奎享的解法是：**它根本不从 Telnet 读换纸消息**，而是用 ESP2/3 的异步事件。但我们走 Telnet 逐行流控，这条路不通——我们的方案（靠 `error:8` 重发 + 超时）与奎享不同，是有意为之的取舍。

## 笔控三方法（hutuji 对齐的核心）

### `a(boolean bl)`（行 250-260）— 笔控入口（含首次校准）

```java
public final void a(boolean bl) {        // bl = true 抬笔, false 落笔
    if (!this.s) {                        // s = "首次笔控"标志
        this.s = true;
        GrblSettings grblSettings = ...;
        if (grblSettings.type == Type.Stepper) {
            this.a("G92 Z0", false);      // ← Stepper 模式首次笔控前校准 Z
        }
    }
    a2.a(a2.b(bl), true);                 // 发实际笔控指令
}
```

**语义**：程序启动后第一次调抬笔/落笔时，若是 Stepper 模式，先发 `G92 Z0` 把当前位置定为 Z=0 基准。`this.s` 保证全程只发一次。

### `b(boolean bl)`（行 262-284）— 笔控指令生成

按 `type` 分支返回不同 G-code（详见 `findings/01-pen-control.md`）。Stepper 分支：

```java
case Stepper:
    if (bl && enableLongPenUp) {
        return a("G1G90 Z", longPenUpMM, "F" + zSpeed);   // 长距离抬笔
    }
    return a("G1G90 Z", bl ? zOff : zOn, "F", zSpeed);    // 普通抬/落笔
```

注意拼接：`a("G1G90 Z", 5.0, "F", 3000)` → `"G1G90 Z5.0F3000"`（无空格）。

### `e()`（行 286-293）— 激光预览

仅 Laser 模式用：返回 `M3S<laserPreviewS>`（低功率预览）。非 Laser 调用 `b(true)`（抬笔）。

## 校准 / 归零 / 点动

### `b_()`（行 180-188）— 就绪钩子

```java
protected void b_() {
    // 触发 FirmwareEventResponse_OK
    if (this.o) {                  // o = 已查过 $$ 配置
        // 触发 FirmwareEventSettings
        this.o = false;
        this.a("G92 Z0", true);    // ← 查完配置后校准 Z
    }
}
```

每次收到 `ok` 都调 `b_()`，但只在 `o=true`（刚查完 `$$` 配置）时发 `G92 Z0`。

### `d()`（行 328-333）— 设原点（用户操作）

```java
public final String d() {
    Settings settings = Settings.getInst();
    return "G92 X" + offsetX + " Y" + offsetY + " Z0";
}
```

发 `G92 X<ox> Y<oy> Z0`——把当前位置设为带偏移的原点。Z 永远归 0（弹簧轴无绝对原点概念）。

### `b()`（行 295-298）— 归零（XY）

```java
public final String b() {
    return a("G90G0 X0Y0");    // G0 rapid 归 XY 原点（不归 Z）
}
```

### `a(float,float,float,float)`（行 301-309）— 点动（jog）

```java
public final void a(float dx, float dy, float dz, float feed) {
    float ver = Float.parseFloat(this.b);   // Grbl 版本
    if (ver >= 1.1) {
        // Grbl 1.1+ 用 $J= jog 命令（可被 jog-cancel 打断）
        this.a("$J=G21G91 X.. Y.. Z.. F..");
    } else {
        // 旧版用 G21G91 相对运动
        this.a("G21G91 X.. Y.. Z.. F..");
    }
}
```

**版本自适应**：`>=1.1` 用 `$J=`（实时 jog），旧版退化为相对运动。我们 Grbl 是 1.3a，走 `$J=` 分支。

### `j()` / `k()` / `l()` / `h()`（行 335-348）

| 方法 | 动作 |
|------|------|
| `j()` | 归位：发 `b()` 返回的 `G90G0 X0Y0` |
| `k()` | Homing：发 `$H` |
| `l()` | 解锁：发 `$X`（清 ALARM）|
| `h()` | 休眠：发 `$SLP` |

## 状态报告解析 `h(String)`（行 190-208）

解析 `<Idle|MPos:1.0,2.0,3.0|...>`：

```java
protected void h(String object) {
    d d2 = new d();                         // 位置容器
    if (object.contains("|")) {
        // Grbl 1.1+ 格式：<State|MPos:x,y,z|...>
        String[] parts = object.split("\\|");
        this.d = parts[0];                  // 状态字符串
        d2.a = Float.parseFloat(parts[1].substring(5, ...));   // X
        d2.b = Float.parseFloat(parts[1].substring(...));      // Y
    } else {
        // 旧格式：State,MPos:x,y
        ...
    }
    // 触发 FirmwareEventStatus 事件
}
```

`com.kenjoy.kenjoycnc.machine.grbl.c` 是状态枚举（`valueOf(this.d)`）。

## 连接握手完整时序（综合各方法）

```
PC → Grbl: (串口/TCP 连上)
Grbl → PC: "Grbl 1.3a ['$' for help]"        ← g() 识别，提取版本 "1.3a"
                                              ← 触发 FirmwareEventConnect
PC → Grbl: "$I\n"                              ← b() 在连接事件里发
Grbl → PC: "[VER:1.3a.2021103:]"              ← c() 解析存 t
PC → Grbl: "$$\n"                              ← G() 查所有配置
Grbl → PC: "$0=10\n$1=25\n$2=0\n..."          ← c() 逐行解析存 GrblSettings.kv
Grbl → PC: "ok"                                ← b_() 检测 o=true → 发 "G92 Z0"
PC → Grbl: "G92 Z0\n"                          ← Z 基准校准（Stepper 模式）
... 用户开始出图 ...
PC → Grbl: "G21\nG90\nF1500\n"                ← 出图头
PC → Grbl: "G1 X.. Y..\n"                      ← 空走
PC → Grbl: "G1G90 Z5.0F3000\n"                 ← 首次落笔（a(false) 已确保 G92 Z0）
...
```

## 与 hutuji 的差异对照（对齐清单）

| 维度 | 奎享（逆向） | hutuji 现状 | 对齐动作 |
|------|-------------|-------------|----------|
| 笔控指令 | `G1G90 Z5F3000` / `G1G90 Z0F3000` | `M3` / `M5` | 改 toml segment_first/line_end |
| 笔控停顿 | 无 G4 | 有 `G4 P0.2` | 去掉 G4 |
| Z 校准 | 连接后首次笔控前 `G92 Z0` | 无 | document_start 加 `G92 Z0` |
| 空走指令 | `G1` + F2500 | `G0` + F2500 | G0 → G1（可选，奎享更稳）|
| 线段 F | 不重复（继承首 F1500）| 每行带 F1500 | 去掉 segment 的 F（可选）|
| 状态查询 | `<...>` 主动推送 + 轮询 | `?` 轮询 | 一致 |
| 换纸感知 | ESP2/3 事件 | error:8 重发 + 超时 | **有意不同，不改** |
| 归零 | 用户点按钮 `G90G0 X0Y0` | 手动推死点 | 一致（都不自动归 Z）|

**必改**：笔控指令（M3/M5 → G1Z）、G92 Z0 校准。
**可选**：G0→G1、去掉 segment 的 F。第一版只做必改，验证通过再考虑可选。

## 未解之谜

1. **`b_()` 里 `G92 Z0` 的 `true` vs `a()` 里的 `false`**：第二个参数（int n2）含义未明，可能是"是否等待 ok"。两次发 G92 Z0 用不同值，语义差异待查基类 `c.a(String, boolean)`。
2. **`enableLongPenUp` 的触发条件**：`longMoveDist` 配置控制何时启用长抬笔（距离 > 阈值才抬更高）。我们暂不用。
3. **`r`（状态报告重试计数）的完整逻辑**：在 c() 里 `this.r = 0`，但累加点未在 a.java 出现，可能在基类。

这些不影响笔控对齐，留作后续深挖。
