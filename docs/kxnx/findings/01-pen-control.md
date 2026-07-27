# 发现 01：笔控方式（Stepper / Servo / Laser）

> 源文件：`decompiled/com/kenjoy/kenjoycnc/machine/grbl/a.java:262-284`
> 关联：`decompiled/com/kvenjoy/drawsoft/lib/grbl/Type.java`

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

## 关键代码（grbl/a.java:262-284）

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

`a(Object...)` 是字符串拼接器（`grbl/a.java:351-361`），把入参原样拼接。所以 `a("G1G90 Z", 5.0, "F", 3000)` 产出 `G1G90 Z5.0F3000`。

## Stepper 模式的精确输出（我们的情况）

奎享 `default.properties` 默认值：
```properties
grbl_type=Servo          # 默认舵机；步进电机要改成 Stepper
zOn=2                    # 落笔 Z 位置（mm）
zOff=0                   # 抬笔 Z 位置（mm）
zSpeed=3000              # Z 轴速度（mm/min，= 50mm/s）
```

我们实测 **Z=5 触纸、Z=0 抬起**，所以对应配置应为 `zOn=5, zOff=0`。

实际产生的 G-code（bl=false=落笔）：
```
G1G90 Z5.0F3000
```
bl=true=抬笔：
```
G1G90 Z0.0F3000
```

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

| 维度 | 奎享 Stepper（验证可行） | hutuji 现状 | 差异 |
|------|--------------------------|-------------|------|
| 落笔 | `G1 G90 Z5 F3000` | `M3` + `G4 P0.2` | M3 走固件宏，且 G4 增延迟 |
| 抬笔 | `G1 G90 Z0 F3000` | `M5` + `G4 P0.2` | 同上 |
| 速度 | F3000（50mm/s）| 无（走宏内默认）| — |

**根因**：hutuji 现在走 `M3`/`M5` → Grbl 的 `USE_M3_M5_AS_PEN_UP_DOWN` 宏 → 宏内部做 Z 运动。但实测 `G0 Z5`（直接 Z 指令）能落笔，`M3`（宏路径）不能——宏路径在这套硬件上失效了（原因待查，但既然直接 Z 指令可用，绕开宏最稳）。

**对齐方向**：放弃 M3/M5，直接发 `G1 G90 Z<5|0> F3000`，和奎享 Stepper 模式完全一致。
