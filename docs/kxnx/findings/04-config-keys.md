# 发现 04：配置键（default.properties）

> 源：本机 `C:\Program Files (x86)\kdraw(x64)\default.properties`（奎享安装目录自带，明文）
> 关联 POJO：`decompiled_java/com/kenjoy/kenjoycnc/machine/grbl/GrblSettings.java`
> （旧版写的 `decompiled/...` 路径不存在，见 `docs/kxnx/README.md` §「反编译源码树」）

## 全文（与笔控相关部分）

```properties
machine=kvenjoy                          # 软件类型：kvenjoy=奎享雕刻
auto_connect=true
feed_rate=1500                           # 出图进给（mm/min = 25mm/s）
tool_on_delay=0.2                        # 落笔后停顿（秒）—— 仅 Servo/Laser 用
tool_off_delay=0.2                       # 抬笔前停顿（秒）—— 仅 Servo/Laser 用
jog_speed=2500                           # 点动/空走速度（mm/min）
autoSleep=false
single_step_command=false
auto_set_zero=true

###################grbl 设置#######################
grbl_type=Servo                          # ← 笔控类型：我们这种步进电机要改 Stepper
zOn=2                                    # 落笔 Z 位置（mm）—— Stepper 专用
zOff=0                                   # 抬笔 Z 位置（mm）—— Stepper 专用
zSpeed=3000                              # Z 轴速度（mm/min = 50mm/s）—— Stepper 专用
laser_power=1000                         # 激光功率（M3 S 值）—— Laser 用
laser_border_power=100                   # 激光预览功率
pen_up_pos=12000                         # 舵机抬笔位置 —— Servo 用
pen_down_pos=18000                       # 舵机落笔位置 —— Servo 用
max_pen_speed=32767
max_pen_value=30000
servo_speed=750

#################marlin#######################
marlin_type=Servo
marlin_pen_down_mm=2
marlin_pen_up_mm=...
```

## 笔控类型对应表（与 `Type.java` 一致）

| `grbl_type` | 落笔指令 | 抬笔指令 | 用到的参数 |
|-------------|----------|----------|------------|
| `Servo` | `M3 S<laser_power>` | `M5` | `laser_power` |
| `Laser` | `M3 S<laser_power>` | `M5` | `laser_power` |
| **`Stepper`** | **`G1 G90 Z<zOn> F<zSpeed>`** | **`G1 G90 Z<zOff> F<zSpeed>`** | **`zOn`, `zOff`, `zSpeed`** |
| `Servo_bachin` | `M3 S<pen_down_pos>` | `M3 S<pen_up_pos>` | `pen_down_pos`, `pen_up_pos` |
| `Custom` | `customOnCode` | `customOffCode` | 用户自定义字符串 |

## 与 hutuji 的参数对照

| 参数 | 奎享默认 | hutuji 对应 | 备注 |
|------|----------|-------------|------|
| `feed_rate` | 1500 | `hutuji_gwrite.toml` document_start 的 `F1500` | ✓ 一致 |
| `jog_speed` | 2500 | `segment_first` 的 `F2500` | ✓ 一致 |
| `zOn` | 2 | 待定 | 我们实测 **Z=5 触纸**，应取 5 |
| `zOff` | 0 | 待定 | Z=0 抬起，取 0 |
| `zSpeed` | 3000 | 待加 | F3000 = 50mm/s |
| `tool_on_delay` | 0.2 | 现在的 `G4 P0.2` | **Stepper 模式不用，应去掉** |
| `tool_off_delay` | 0.2 | 现在的 `G4 P0.2` | 同上 |

## 结论

奎享的 `default.properties` 是这套硬件的**出厂配置参照**。它明确告诉我们：
- 步进电机笔控走 Z 轴运动，参数是 `zOn/zOff/zSpeed`
- 出图 feed/jog 速度是 1500/2500（我们已对齐）
- Stepper 模式不用 `tool_*_delay`（我们的 G4 是多余的）

我们只需把 `zOn=5, zOff=0, zSpeed=3000` 注入 hutuji 的 vpype profile，去掉 G4 和 M3/M5，就完全对齐了。
