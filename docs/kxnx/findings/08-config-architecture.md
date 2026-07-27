# 深度逆向：配置体系与参数传递链

> 源文件：`machine/grbl/GrblSettings.java`、`machine/FirmwareSettings.java`、`drawsoft/lib/CreaterParameters.java`、`drawsoft/lib/grbl/GrblParameters.java`、`machine/grbl/Type.java`、本机 `default.properties`
>
> 目的：厘清奎享的配置如何从 properties 文件 → POJO → 出图参数，为 hutuji 参数化笔控深度提供对照。

## 三层配置架构

```
default.properties（明文键值）
        ↓ a.a().a(key, default)   ← 配置读取门面（drawsoft/lib/data/a.java）
FirmwareSettings 子类（GrblSettings 等，运行时 POJO）
        ↓ toParams()
CreaterParameters 子类（GrblParameters，出图参数快照）
        ↓ 传给 G-code 生成器
实际 G-code 字符串
```

## 第 1 层：default.properties（明文配置）

本机 `C:\Program Files (x86)\kdraw(x64)\default.properties` 关键键（Stepper 相关）：

| 键 | 默认值 | 用途 |
|----|--------|------|
| `grbl_type` | `Servo` | 笔控类型枚举名（我们改 `Stepper`）|
| `zOn` | `2` | 落笔 Z 位置（mm）|
| `zOff` | `0` | 抬笔 Z 位置（mm）|
| `zSpeed` | `3000` | Z 轴速度（mm/min）|
| `feed_rate` | `1500` | 出图进给（mm/min）|
| `jog_speed` | `2500` | 点动/空走速度（mm/min）|
| `tool_on_delay` | `0.2` | 落笔停顿（秒，仅 Servo/Laser）|
| `tool_off_delay` | `0.2` | 抬笔停顿（秒，仅 Servo/Laser）|
| `enable_long_pen_up` | `false` | 启用长距离抬笔 |
| `long_pen_up_pos` | `0` | 长抬笔 Z（mm）|
| `long_move_dist` | `20` | 触发长抬笔的空走距离阈值（mm）|
| `pen_up_pos` | `12000` | 舵机抬笔位置（Servo 用）|
| `pen_down_pos` | `18000` | 舵机落笔位置（Servo 用）|
| `laser_power` | `1000` | 激光/舵机功率（M3 S 值）|
| `auto_set_zero` | `true` | 自动设原点 |

## 第 2 层：FirmwareSettings（运行时 POJO）

### 抽象基类 `FirmwareSettings.java`

```java
public abstract class FirmwareSettings {
    public int penUpPos;                                           // 舵机抬笔
    public int penDownPos;                                         // 舵机落笔
    public boolean enableLongPenUp = a.a().a("enable_long_pen_up", false);
    public float longPenUpMM = a.a().a("long_pen_up_pos", 0.0f);
    public float longMoveDist = a.a().a("long_move_dist", 20.0f);
    public String customOnCode = a.a().a("custom_on_code", "M3");
    public String customOffCode = a.a().a("custom_off_code", "M5");

    public abstract void apply();
    public abstract void applyToMachine();
    public abstract void reset();
    public abstract CreaterParameters toParams();
}
```

通用字段（所有固件共享）：抬笔长距、自定义码。

### Grbl 子类 `GrblSettings.java`

```java
public class GrblSettings extends FirmwareSettings {
    public Type type = Type.valueOf(a.a().a("grbl_type", Type.Servo.name()));
    public boolean ecoServo;
    public float zOn = a.a().a("zOn", 2.0f);           // ← Stepper 落笔 Z
    public float zOff = a.a().a("zOff", 0.0f);         // ← Stepper 抬笔 Z
    public int laserS = a.a().a("laser_power", 1000);
    public int laserPreviewS = a.a().a("laser_border_power", 100);
    public int zSpeed = a.a().a("zSpeed", 3000);       // ← Stepper Z 速度
    public final HashMap kv = new HashMap();            // 从机器读回的 $N= 配置

    public void readFromMachine(int n, float v) {       // 收到 $N=V 时回调
        if (n == 6) this.ecoServo = (v == 0);
        this.kv.put(n, v);
    }
    ...
}
```

Grbl 专有字段：`type`（笔控类型）、`zOn/zOff/zSpeed`（Stepper 三参数）、`laserS/laserPreviewS`（激光功率）。

## 第 3 层：CreaterParameters（出图参数快照）

### 抽象基类 `CreaterParameters.java`

```java
public class CreaterParameters {
    public int jogSpeed;
    public int feedRate;
    public boolean enableLongPenUp;
    public float longPenUpMM;
    public float longMoveDist;
    public boolean enableTail;        // 笔画尾巴优化
    public float minTailSize;
    public float maxTailSize;
    public String customOff = "M5";
    public String customOn = "M3";
}
```

### Grbl 子类 `GrblParameters.java`

```java
public class GrblParameters extends CreaterParameters {
    public Type type = Type.Servo;
    public float toolOnDelay;
    public float toolOffDelay;
    public float laserS;
    public float laserPrviewS;
    public float zOff;
    public float zOn;
    public int penUpPos;
    public int penDownPos;
    public int zSpeed;
}
```

### 转换：`GrblSettings.toParams()`（行 95-123）

```java
@Override
public CreaterParameters toParams() {
    GrblParameters p = new GrblParameters();
    Settings s = Settings.getInst();
    s.offsetX = 0.0f;  s.offsetY = 0.0f;
    p.feedRate = s.feedRate;          // ← 从全局 Settings 取
    p.jogSpeed = s.jogSpeed;
    p.laserS = this.laserS;
    p.laserPrviewS = this.laserPreviewS;
    p.penDownPos = this.penDownPos;
    p.penUpPos = this.penUpPos;
    p.type = this.type;
    p.zOff = this.zOff;               // ← Stepper 抬笔
    p.zOn = this.zOn;                 // ← Stepper 落笔
    p.zSpeed = this.zSpeed;           // ← Stepper Z 速度
    p.toolOffDelay = s.toolOffDelay;  // ← 从全局 Settings 取
    p.toolOnDelay = s.toolOnDelay;
    p.enableLongPenUp = this.enableLongPenUp;
    p.longPenUpMM = this.longPenUpMM;
    p.longMoveDist = this.longMoveDist;
    p.customOff = this.customOffCode;
    p.customOn = this.customOnCode;
    // 字体尾巴优化
    FontsSetData fsd = Storage.getStore(FontsSetData.class);
    p.enableTail = fsd.enableTail;
    p.minTailSize = fsd.minTail;
    p.maxTailSize = fsd.maxTail;
    return p;
}
```

注意：`feedRate/jogSpeed/toolOnDelay/toolOffDelay` 来自全局 `Settings`（`Settings.getInst()`），不是 `GrblSettings`。这些是出图通用参数（所有固件共享），与笔控类型无关。而 `zOn/zOff/zSpeed/type` 是 GrblSettings 专有（笔控参数）。

## readFromMachine：从 Grbl 读回配置

```java
public void readFromMachine(int n, float v) {
    if (n == 6) this.ecoServo = (v == 0);   // $6=0 表示 eco 舵机
    this.kv.put(n, v);                       // 存所有 $N 配置
}
```

当奎享发 `$$` 查询，Grbl 回 `$6=0\n$1=25\n...` 时，逐行进这里。`$N` → `kv` map，特殊处理的只有 `$6`（eco servo 标志）。

`toProperties()` 反向把 `kv` map 转成 `Properties`，用于持久化或导出。

## Settings 全局单例

`com.kenjoy.kenjoycnc.model.Settings.getInst()` 承载出图通用参数（与固件无关）：

- `feedRate` / `jogSpeed` — 速度
- `toolOnDelay` / `toolOffDelay` — 停顿（仅 Servo/Laser）
- `offsetX` / `offsetY` — 原点偏移

这些在 UI 设置页改，存全局。`toParams()` 时合并进 GrblParameters。

## hutuji 的配置对照

hutuji 没有 properties 文件，参数硬编码在 `hutuji_gwrite.toml`。对照映射：

| 奎享配置 | hutuji 现状 | hutuji 对齐后 |
|----------|-------------|---------------|
| `zOn=2`（实测改 5）| 无（走 M3 宏）| toml 里 `G1 G90 Z5.000` |
| `zOff=0` | 无 | toml 里 `G1 G90 Z0.000` |
| `zSpeed=3000` | 无 | toml 里 `F3000` |
| `feed_rate=1500` | `F1500` | ✓ 一致 |
| `jog_speed=2500` | `F2500` | ✓ 一致 |
| `tool_on_delay=0.2` | `G4 P0.2` | 去掉（Stepper 不用）|
| `tool_off_delay=0.2` | `G4 P0.2` | 去掉 |
| `grbl_type` | （隐式走 M3/M5）| 显式走 Stepper（G1Z）|

## 参数化建议（对齐后）

第一版硬编码 Z=5/Z=0/F3000 在 toml。验证通过后，若要可调（不同纸厚、不同笔），两种方案：

**方案 A：toml 占位 + sed 替换**（轻量）
- vpype-gcode 的 toml 不支持变量，但在 `gcode_gen.py` 生成后用 Python 替换占位符（如 `__PEN_DOWN_Z__`）。
- 优点：不动 vpype pipeline。
- 缺点：多一步后处理。

**方案 B：环境变量 + Python 生成**（灵活）
- 把笔控模板从 toml 移到 `gcode_gen.py`，用 env var 注入 Z 值。
- 优点：运行时可调，无需改代码。
- 缺点：偏离 vpype-gcode 的 toml 约定。

**第一版不做参数化**——硬编码，验证通再说。奎享的 properties 体系是完整 GUI 应用才需要的，hutuji 走 headless 云端服务，硬编码 + 偶尔改常量足够。

## 未解之谜

- `ecoServo`（`$6=0` 触发）的完整用途：疑似某种省电舵机模式，hutuji 不涉及。
- `enableTail` 笔画尾巴优化：字体笔画收尾处理，hutuji 走 vpype 不涉及。
- `enableLongPenUp` + `longMoveDist`：空走 > 阈值时抬更高。hutuji 暂不用，但若发现抬笔空走刮纸，可借鉴。
