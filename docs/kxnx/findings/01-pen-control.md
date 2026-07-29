# 发现 01：笔控方式（Stepper / Servo / Laser）

> 源文件：`decompiled_java/com/kenjoy/kenjoycnc/machine/grbl/a.java:265-287`
> 关联：`decompiled_java/com/kvenjoy/drawsoft/lib/grbl/Type.java`
>
> **行号基准（2026-07-29 补注）**：本文行号已换算到主树 `decompiled_java/`
> （`D:/GitHub/kxnx/kx/tools/dump_agent/` 下）。旧版写的 `decompiled/...` 路径不存在，
> 那批行号是 `decompiled_full/` 基准；`grbl/a.java` 与 `marlin/a.java` 主树 = full **+3**，
> 但偏移逐文件不同（`kat/b.java` 是 +5、`ebb/a.java` 两树同号），**不要跨文件套用统一偏移**。
> 见 `docs/kxnx/README.md` §「反编译源码树」。

## Type 枚举（笔控类型）

奎享把 Grbl 笔控分成 5 种类型，由 `default.properties` 的 `grbl_type` 配置：

```java
// Type.java（简化）
public enum Type { Servo, Laser, Stepper, Servo_bachin, Custom }
```

| 类型 | 落笔 | 抬笔 | 适用 |
|------|------|------|------|
| `Servo` | `M3 S<laserS>` | `M5` | 舵机控制笔（PWM 占空比）|
| `Laser` | `M3 S<laserS>` | `M5` | 激光头 |
| **`Stepper`** | **`G1 G90 Z<zOn> F<zSpeed>`** | **`G1 G90 Z<zOff> F<zSpeed>`** | **步进电机控笔（我们这种）** |
| `Servo_bachin` | `M3 S<penDownPos>` | `M3 S<penUpPos>` | Bachin 舵机 |
| `Custom` | `customOnCode` | `customOffCode` | 用户自定义 |

## 关键代码（主树 `grbl/a.java:265-287`；`decompiled_full/` 树为 `:262-284`）

```java
@Override
public final String b(boolean bl) {   // bl = true 抬笔, false 落笔
    GrblSettings grblSettings = (GrblSettings) f.a().g();
    switch (grblSettings.type) {
        case Servo:
        case Laser: {
            return a(bl ? "M5" : "M3S" + grblSettings.laserS);
        }
        case Stepper: {
            if (bl && grblSettings.enableLongPenUp) {
                // 长距离抬笔：抬得更高，避免移动时刮纸
                return a("G1G90 Z", grblSettings.longPenUpMM, "F" + grblSettings.zSpeed);
            }
            return a("G1G90 Z",
                     Float.valueOf(bl ? grblSettings.zOff : grblSettings.zOn),
                     "F", grblSettings.zSpeed);
        }
        case Servo_bachin: {
            return a("M3S" + (bl ? grblSettings.penUpPos : grblSettings.penDownPos));
        }
        case Custom: {
            return a(bl ? grblSettings.customOffCode : grblSettings.customOnCode);
        }
    }
    return "";
}
```

`a(Object...)` 是字符串拼接器（主树 `grbl/a.java:354-362`；`decompiled_full/` 树为 `:351-361`），把入参原样拼接。所以 `a("G1G90 Z", 5.0, "F", 3000)` 产出 `G1G90 Z5.0F3000`。

## Stepper 模式的精确输出（我们的情况）

奎享本机 `C:/Program Files (x86)/kdraw(x64)/settings/默认Grbl机器/Grbl.json` 的实际配置为：

```text
type=Stepper, zOn=5, zOff=0, zSpeed=10000, $1=25, $3=7
```

因此实际笔控字面量（bl=false=落笔）为：
```
G1G90 Z5.0F10000
```
bl=true=抬笔：
```
G1G90 Z0.0F10000
```

`$1=25` 后电机失能，弹簧回到自然抬笔位，但 Grbl 计数不会跟随机械回弹；两次普通 `G1 Z0`
不能校准坐标。完整会话须在 fresh `Idle` 后等待超过 25ms，再旁路 `G92 Z0`。

## 长距离抬笔（enableLongPenUp）

当 `enableLongPenUp=true` 且**抬笔**时（bl=true），改发：
```
G1G90 Z<longPenUpMM> F<zSpeed>
```
抬到比平时更高的位置（`longPenUpMM` > `zOff`），用于笔画间长距离空移动时避免笔尖刮纸。落笔不受影响。我们目前用不到这个特性。

## 跨固件一致性验证

Marlin 固件控制器（`marlin/a.java:196-200`）的 Stepper 分支同样发 `G1 Z`：

```java
case Stepper: {
    if (bl && marlinSettings.enableLongPenUp) {
        return a("G1 Z", marlinSettings.longPenUpMM, "F" + marlinSettings.zSpeed);
    }
    return a("G1 Z",
             Float.valueOf(bl ? marlinSettings.penUpMM : marlinSettings.penDownMM),
             "F" + marlinSettings.zSpeed);
}
```

说明 **`G1 Z` 是步进电机笔控的通用做法**，不是 Grbl 特例。这增强了我们对齐这个方案的信心。

## 对 hutuji 的含义

| 维度 | 奎享本机 Stepper | hutuji 生产 profile | 结论 |
|------|------------------|----------------------|------|
| 落笔 | `G1G90 Z5.0F10000` | `G1G90 Z5.0F10000` | 直接 G1 Z |
| 抬笔 | `G1G90 Z0.0F10000` | `G1G90 Z0.0F10000` | 直接 G1 Z |
| 方向 | `$3=7` 下 zOn=5 / zOff=0 | 保留当前 `$3=7` | 不用历史固件方向背书 |

生产 profile 继续使用直接 `G1 Z`，不用 `M3/M5`。`inkscape_px` 历史固件的 M3/M5 方向与当前
固件不同，不能据其判断当前 Z 方向；当前短线真实出墨仍待 S3 新会话编排烧录后验证。
