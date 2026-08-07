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

> **⚠️ 本表是成文时（早期）的对照，多行已被后续证据推翻。**「奎享默认」一列指
> `default.properties` / `Settings.java` 的**出厂缺省**；这台机器上的**现役持久化值**见
> [[22-installed-config-vs-machine]] §4（`feedRate=8000`、`jogSpeed=8000`、`toolOn/OffDelay=0.0`）。
> 「hutuji 对应」一列已按现行 `hutuji_gwrite.toml` 更新。

| 参数 | 奎享出厂缺省 | 奎享本机现役值 | hutuji 现值 | 备注 |
|------|----------|----------|-------------|------|
| `feed_rate` | 1500 | **8000** | toml 画线 `F8000` | 早期写的「✓ 一致 F1500」已过时 |
| `jog_speed` | 2500 | **8000** | toml 空走 `F8000` | 早期写的「✓ 一致 F2500」已过时 |
| `zOn` | 2 | **5.0** | `segment_first` 的 `Z5.0` | 已定案，非「待定」 |
| `zOff` | 0 | **0.0** | `line_end` 的 `Z0.0` | 已定案，非「待定」 |
| `zSpeed` | 3000 | **10000** | Z 行 `F10000` | 已定案；`10000` 恰是 `$112` 轴上限（[[22-installed-config-vs-machine]] §3）|
| `tool_on_delay` | 0.2 | **0.0** | 无 `G4` | Stepper 不用；现役值 0.0 佐证 |
| `tool_off_delay` | 0.2 | **0.0** | 无 `G4` | 同上 |

## 结论

奎享的 `default.properties` 是这套硬件的**出厂配置参照**。它明确告诉我们：
- 步进电机笔控走 Z 轴运动，参数是 `zOn/zOff/zSpeed`
- Stepper 模式不用 `tool_*_delay`（我们的 G4 是多余的）

> **⚠️ 早期结论已 supersede**：本文成文时写的「只需把 `zOn=5, zOff=0, zSpeed=3000` 注入
> vpype profile」中 **`zSpeed=3000` 是出厂缺省的误用**。真实产物与本机现役配置均为
> **`zSpeed=10000`**，且该值恰为 Z 轴速率上限 `$112=10000`。现行 `hutuji_gwrite.toml`
> 用的是 `F10000`，已正确；不要照本段旧文把它改成 3000。出图进给同理：
> 出厂缺省 1500/2500，本机现役与我方产物均为 **8000**。详见
> [[22-installed-config-vs-machine]] §3–§4。
