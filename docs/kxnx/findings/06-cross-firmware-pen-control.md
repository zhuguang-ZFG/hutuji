# 深度逆向：跨固件笔控对比（Grbl / Marlin / EBB / KAT）

> 源文件：`machine/grbl/a.java`、`machine/marlin/a.java`、`machine/ebb/a.java`、`machine/kat/a.java`、各 `Type.java`
>
> 目的：通过对比不同固件/笔控类型的实现，确认 **"步进电机笔控走 Z/E 轴运动指令"是通用模式**，不是 Grbl 特例。这强化了 hutuji 对齐这个方案的信心。

> **hutuji 状态勘误（2026-07-29）**：本文对各固件分支的静态分析仍有效，但早期把
> 连接/点动路径的 `G92 Z0` 外推到出图文件是错误的。当前生产 profile 不发 `G92`，而是
> 开头两次 `G1G90 Z0.0F10000`；画线/空走 `F8000`，Z 抬落笔 `F10000`。以下涉及旧 hutuji
> 方案的叙述以本勘误和文末更新后的结论为准。

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

### Grbl（`machine/grbl/a.java:270-275`）

```java
case Stepper:
    if (bl && enableLongPenUp) {
        return a("G1G90 Z", longPenUpMM, "F" + zSpeed);
    }
    return a("G1G90 Z", bl ? zOff : zOn, "F", zSpeed);
// 输出：G1G90 Z5.0F3000
```

### Marlin（`machine/marlin/a.java:196-201`）

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

两者的回原点命令都**不归 Z**。这只能说明 XY 与笔控分开，不能推出出图文件必须修改 Z 工件坐标；
hutuji 当前以两次真实 `G1G90 Z0.0F10000` 建立抬笔基准，不发 `G92`。

## 休眠对比

| 固件 | 休眠指令 |
|------|----------|
| Grbl | `$SLP` |
| Marlin | `M18`（失能所有电机）|

注意：Marlin 的 `M18` 会让电机失能——对带弹簧的 Z 轴意味着笔被弹回。Grbl 的 `$SLP` 类似。
hutuji 已确认弹簧回位方向就是抬笔方向，当前 `$1=25`，不依赖电机常使能。

## 解锁对比

| 固件 | 解锁/homing |
|------|-------------|
| Grbl | `$X`（解锁）/ `$H`（homing）|
| Marlin | `M999`（重启）/ `G28`（homing）|

## 对 hutuji 的最终启示

1. **Stepper 走 `G1 Z` 是跨固件常见模式**——这支持生产 profile 使用纯 Z 笔控，而不是文件内 M3/M5。
2. **G90 重声明可避免模态漂移**——当前字面量是 `G1G90 Z5.0F10000` / `G1G90 Z0.0F10000`。
3. **不能从连接路径推出文件需要 `G92`**——缓存内容无 `G92`，当前 profile 也明确禁用。
4. **Homing 不归 Z**——当前设备无限位开关，Z 抬笔基准由开头两次真实 Z0 运动建立。

这份对比只用于支持“Stepper 可用轴运动笔控”；生产文件的精确模板仍以实际 profile、缓存内容与
本机验证为准，不能从其他固件分支外推出 `G92` 或具体进给值。
