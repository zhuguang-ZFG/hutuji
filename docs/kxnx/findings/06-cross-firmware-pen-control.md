# 深度逆向：跨固件笔控对比（Grbl / Marlin / EBB / KAT）

> 源文件：`machine/grbl/a.java`、`machine/marlin/a.java`、`machine/ebb/a.java`、`machine/kat/a.java`、各 `Type.java`
>
> 目的：通过对比不同固件/笔控类型的实现，确认 **"步进电机笔控走 Z/E 轴运动指令"是通用模式**，不是 Grbl 特例。这强化了 hutuji 对齐这个方案的信心。

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

两者都**不归 Z**——因为笔控是相对的（G92 Z0 校准后，Z0 = 抬笔基准）。这与 hutuji"手动推死点归 XY，Z 靠 G92 校准"的思路一致。

## 休眠对比

| 固件 | 休眠指令 |
|------|----------|
| Grbl | `$SLP` |
| Marlin | `M18`（失能所有电机）|

注意：Marlin 的 `M18` 会让电机失能——对带弹簧的 Z 轴意味着笔被弹回。Grbl 的 `$SLP` 类似。hutuji 已设 `$1=255`（永不失能），规避了这个问题。

## 解锁对比

| 固件 | 解锁/homing |
|------|-------------|
| Grbl | `$X`（解锁）/ `$H`（homing）|
| Marlin | `M999`（重启）/ `G28`（homing）|

## 对 hutuji 的最终启示

1. **Stepper 走 `G1 Z` 是行业标准**——Grbl 和 Marlin 两个主流固件都这么做，不是奎享的私有约定。我们之前用 M3/M5 宏反而是非标准。
2. **G90 重声明是良好实践**——Grbl 内联 `G1G90 Z`，Marlin 先发 `G90`。我们在 toml 里写 `G1 G90 Z5.000 F3000` 对齐 Grbl 风格。
3. **`G92 Z0` 校准是步进电机笔控的标配**——没有绝对原点的 Z 轴（弹簧/无开关）必须靠 G92 建立基准。Grbl 在首次笔控前发，我们在 document_start 发，等价。
4. **Homing 不归 Z**——Z 基准靠 G92，不靠限位开关。我们无限位开关，完全契合。

这份跨固件对比，是 hutuji 笔控对齐奎享方案的**外部交叉验证**：不只是"奎享这么干"，而是"所有同类固件都这么干"。
