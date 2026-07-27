# 发现 02：出图 G-code 完整模板

> 源文件：`decompiled/com/kenjoy/kenjoycnc/h/d.java:22-60`（校准测试图，含完整头-笔控-画线-尾结构）

## 完整模板（Grbl + Stepper，逆向还原）

```gcode
G21                  # 单位 mm
G90                  # 绝对坐标
F1500                # 进给速率（mm/min = 25mm/s）
G1X{x0}Y{y0}         # 空走到第一段起点（不带 F，继承上面 F1500；注：奎享用 G1 不是 G0）
G1G90 Z5.0F3000      # 落笔（b(false)，zOn=5）
G1X{x1}Y{y1}         # 画第一段
G1X{x2}Y{y2}         # 画第二段
G1G90 Z0.0F3000      # 抬笔（b(true)，zOff=0）
G1X{x3}Y{y3}         # 空走到下一段起点
G1G90 Z5.0F3000      # 落笔
...
G1G90 Z0.0F3000      # 收尾抬笔
```

## 逐字段说明（奎享实际拼接顺序，源码 `h/d.java:40-49`）

```java
stringBuilder.append("G21\n");
stringBuilder.append("G90\n");
stringBuilder.append("F1500\n");
stringBuilder.append("G1X" + x2 + "Y" + y3 + "\n");     // 起点（G1，不是 G0！）
stringBuilder.append("G1X" + x4 + "Y" + y5 + "\n");
stringBuilder.append(c2.b(true) + "\n");                 // 抬笔
stringBuilder.append("G1X" + x2 + "Y" + y3 + "\n");
stringBuilder.append(c2.b(false) + "\n");                // 落笔
stringBuilder.append("G1X" + x6 + "Y" + y7 + "\n");
stringBuilder.append(c2.b(true) + "\n");                 // 抬笔
```

## 关键细节

1. **空走用 `G1`，不是 `G0`**。奎享所有移动（含抬笔状态下的空走）都用 `G1` + F1500。G0（rapid）只在归零（`a.java:297` 的 `G90G0 X0Y0`）等场景出现。
   - 好处：抬笔空走也限速，避免惯性抖动；对带弹簧的笔架尤其稳。

2. **落笔/抬笔无 `G4` 停顿**。`toolOnDelay`/`toolOffDelay` 配置项在 Stepper 模式下**不被使用**（代码路径不经过）。
   - 我们现在的 `G4 P0.2` 是 vpype profile 自加的，奎享没有。每次落笔/抬笔省 0.2s，一幅画几十次笔控，能省十几秒。

3. **F 只声明一次**（开头 `F1500`），后续 `G1X..Y..` 不重复带 F。Z 运动单独带 `F3000`。

4. **Z 运动显式重声明 `G90`**：`G1G90 Z5`。防止前面的 G91（如果有）残留。

5. **无 `M30`/`M2`/`G0 X0 Y0`** 收尾——与我们 `docs/protocol.md §5` 禁令一致（这些会触发自动换纸）。

## 对比 hutuji 现状（hutuji_gwrite.toml）

```toml
document_start = "G21\nG90\nF1500\n"                              # ✓ 一致
segment_first = "G0 X{x} Y{y} F2500\nM3\nG4 P0.2\n"               # ✗ G0+M3+G4
segment       = "G1 X{x} Y{y} F1500\n"                            # ~ 带F（奎享不带）
line_end      = "M5\nG4 P0.2\n"                                   # ✗ M5+G4
document_end  = "M5\nG4 P0.2\n"                                   # ✗ M5+G4
```

差异精确定位在**笔控行**（segment_first / line_end / document_end）和**空走指令**（G0 vs G1）。

## 对齐后的 hutuji_gwrite.toml

```toml
document_start = "G21\nG90\nF1500\n"
# 先 G1 空走到起点（F2500），再 G1 落笔（F3000，Z=zOn）
segment_first = "G1 X{x:.3f} Y{y:.3f} F2500\nG1 G90 Z5.000 F3000\n"
segment       = "G1 X{x:.3f} Y{y:.3f}\n"
line_end      = "G1 G90 Z0.000 F3000\n"
document_end  = "G1 G90 Z0.000 F3000\n"
```

要点：
- `segment_first` 用 `G1`（不是 G0）+ F2500 空走，对齐奎享。
- 落笔/抬笔 `G1 G90 Z5/Z0 F3000`，对齐奎享 Stepper 模式。
- 去掉 `G4 P0.2`（奎享 Stepper 不用）。
- `segment` 去掉 F（继承 document_start 的 F1500）。
