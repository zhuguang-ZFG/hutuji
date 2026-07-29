# 深度逆向：跨固件笔控对比（Grbl / Marlin / EBB / KAT）

> 源文件：`machine/grbl/a.java`、`machine/marlin/a.java`、`machine/ebb/a.java`、`machine/kat/a.java`、各 `Type.java`
>
> **行号与树基准（2026-07-29 补注）**：本文正文的 `a.java:NNN` 除已就地标注者外都是
> **`decompiled_full/` 树**基准；换算到主树 `decompiled_java/` 时 `grbl/a.java` 与 `marlin/a.java` **+3**、
> `ebb/a.java` 两树同号、`kat` 下 **+5**（偏移逐文件不同，别套统一值）。
> 另注：`machine/kat/a.java` **只存在于 `decompiled_full/`**，主树 `machine/kat/` 只有
> `b c d f g h KATSettings`，其中控制器是 `kat/b.java`（`:42-43` `class b extends marlin.a`）。
> 见 `docs/kxnx/README.md` §「反编译源码树」。
>
> 目的：通过对比不同固件/笔控类型的实现，确认 **"步进电机笔控走 Z/E 轴运动指令"是通用模式**，不是 Grbl 特例。这强化了 hutuji 对齐这个方案的信心。

> **hutuji 状态勘误（2026-07-29）**：本文对各固件分支的静态分析仍有效。下载文件无 `G92`，
> 开头两次 `G1G90 Z0.0F10000` 只是缓存兼容的幂等抬笔运动；完整会话仍须在每次首画/重画前
> fresh `Idle` + 100ms 后旁路固定 `G92 Z0`。画线/空走 `F8000`，Z 抬落笔 `F10000`。
> `inkscape_px` 历史固件的 M3/M5 方向与当前固件不同，只能参考轴运动模式，不能为当前方向背书。

## 笔控类型枚举全集

### Grbl 的 Type（`drawsoft/lib/grbl/Type.java`）

```java
public enum Type { Servo, Laser, Stepper, Servo_bachin, Custom }
```

### Marlin 的 Type（`drawsoft/lib/marlin/Type.java`）

```java
public enum Type { Servo, Stepper, EStepper, Fan, Custom }
```

Marlin 多两个：
- `EStepper`：挤出头电机当笔控（`G1 E` 而非 `G1 Z`）
- `Fan`：用风扇 PWM 当激光/笔（`M106/M107`）

## Grbl vs Marlin Stepper 笔控对比

### Grbl（主树 `machine/grbl/a.java:273-278`；`decompiled_full/` 树为 `:270-275`）

```java
case Stepper:
    if (bl && enableLongPenUp) {
        return a("G1G90 Z", longPenUpMM, "F" + zSpeed);
    }
    return a("G1G90 Z", bl ? zOff : zOn, "F", zSpeed);
// 输出：G1G90 Z5.0F3000
```

### Marlin（主树 `machine/marlin/a.java:199-203`）

```java
case Stepper:
    if (bl && enableLongPenUp) {
        return a("G1 Z", longPenUpMM, "F" + zSpeed);
    }
    return a("G1 Z", bl ? penUpMM : penDownMM, "F" + zSpeed);
// 输出：G1 Z5.0F3000
```

### 差异

| 维度 | Grbl | Marlin |
|------|------|--------|
| 运动指令 | `G1 G90 Z` | `G1 Z` |
| 距离参数 | `zOn` / `zOff` | `penDownMM` / `penUpMM`（字段名不同，语义同）|
| 距离单位 | mm | mm |
| 速度参数 | `zSpeed` | `zSpeed` |
| 显式 G90 | 有（重声明绝对模式）| 无（Marlin 默认行为，或 a(boolean) 里另发）|

Marlin 的 `a(boolean)`（行 183-187）会**先单独发 `G90`**：

```java
public final void a(boolean bl) {
    this.a("G90", false);          // ← Marlin 先发 G90
    a2.a(a2.b(bl), true);
}
```

所以两者最终都是 `G90 G1 Z<val> F<speed>`，只是 G90 的发出时机不同（Grbl 内联在字符串里，Marlin 作为独立指令先发）。

**结论：步进电机笔控 = `G1 Z/E` + F 限速 + 绝对坐标。这是跨固件通用模式。**

## EStepper（Marlin 独有，挤出当笔）

```java
case EStepper:
    return a("G1 E", bl ? penUpMM : penDownMM, "F" + zSpeed);
// 输出：G1 E5.0F3000
```

用挤出电机（E 轴）代替 Z 轴控笔——某些 3D 打印机改装的写字机笔架挂在挤出头上。hutuji 不涉及，但验证了"运动轴控笔"的通用思路。

## Servo 类对比（hutuji 不用，仅参考）

| 固件 | Servo 落笔 | Servo 抬笔 |
|------|-----------|-----------|
| Grbl | `M3 S<laserS>` | `M5` |
| Marlin | `M280 P0 S<penDownPos>` | `M280 P0 S<penUpPos>` |

Marlin 用 `M280`（舵机控制），Grbl 用主轴 `M3/M5`。不同。

## Fan（Marlin 独有，激光/风扇）

```java
case Fan:
    return a(bl ? "M107" : "M106 S" + fanSpeed);
// 落笔=开风扇 M106 S255，抬笔=关风扇 M107
```

低成本的"激光"方案：用风扇 PWM 口接激光二极管。

## Custom（自定义）

两种固件都支持 `customOnCode` / `customOffCode`——用户在配置里写任意 G-code 字符串。这是奎享留给特殊硬件的逃生口。

## 归零对比

| 固件 | 归零指令 | 特点 |
|------|----------|------|
| Grbl | `G90G0 X0Y0` | 只归 XY，不归 Z |
| Marlin | `G0 X0Y0F<jogSpeed>` | 带速度，只归 XY |

两者的回原点命令都**不归 Z**。这只能说明 XY 与笔控分开。下载文件开头两次
`G1G90 Z0.0F10000` 不建立坐标基准；完整会话须另由 S3 受控旁路 `G92 Z0`。

## 休眠对比

| 固件 | 休眠指令 |
|------|----------|
| Grbl | `$SLP` |
| Marlin | `M18`（失能所有电机）|

注意：奎享本机 `$1=25`，电机失能后弹簧回到自然抬笔位，但 Grbl 计数不会随回弹更新。
因此须先 fresh `Idle`、等待 100ms，再旁路 `G92 Z0`；不能用两次普通 Z0 代替。

## 解锁对比

| 固件 | 解锁/homing |
|------|-------------|
| Grbl | `$X`（解锁）/ `$H`（homing）|
| Marlin | `M999`（重启）/ `G28`（homing）|

## 对 hutuji 的最终启示

1. **Stepper 走 `G1 Z` 是跨固件常见模式**——这支持生产 profile 使用纯 Z 笔控，而不是文件内 M3/M5。
2. **G90 重声明可避免模态漂移**——当前字面量是 `G1G90 Z5.0F10000` / `G1G90 Z0.0F10000`。
3. **文件契约与会话旁路必须分层**——下载文件无 `G92` 且校验器拒绝它；首画/重画前会话旁路固定 `G92 Z0`。
4. **Homing 不归 Z**——当前设备无限位开关，Z 基准由 fresh `Idle` + 100ms 后的旁路 `G92 Z0` 建立。

这份对比只用于支持“Stepper 可用轴运动笔控”；`inkscape_px` 历史固件的 M3/M5 方向不能为当前
固件背书。生产文件精确模板以实际 profile/缓存为准，会话基准以 S3 编排为准。
