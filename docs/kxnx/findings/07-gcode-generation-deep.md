# 深度逆向：出图 G-code 生成与校准测试图

> 源文件：`machine/h/d.java`（校准测试图拼接器，6.3K）
> 关联：`machine/f/a/d.java`（批量出图入口，10.5K）、`KenjoyDraw.java`（主应用）

本文剖析奎享如何把笔画数据转成完整 G-code 文件，以及校准测试图（笔尖对位用）的精确序列。

> **2026-07-28 勘误**：`h/d.java` 生成的是校准测试图，不能代表普通出图的收尾。
> 普通 Grbl 出图由 `Settings.notAutoReturn`（配置键 `not_go_zero`，默认 `false`）控制，
> 经 `PathParameters` 传到生成端；缓存目录的 9 个文件字节完全相同，只能算 1 个唯一产物，
> 该唯一内容以 `G90G0 X0Y0` 回原点结束。
> `not_go_zero=true` 和 CardWriter 是例外。下文“无 M30/M2”仍成立，但“结束就是抬笔”
> 只适用于该校准生成器，不能外推为普通出图不回原点。
>
> **hutuji profile 勘误（2026-07-29）**：当前生产 profile 无 `G92`，开头两次
> `G1G90 Z0.0F10000`；空走/画线 `F8000`，Z 抬落笔 `F10000`。本文中从校准生成器推导出的
> 旧 hutuji 模板已作废。

## h/d.java：校准测试图生成器

奎享有个"画校准图"功能——画一个固定形状让用户对位笔尖。这个类生成它的 G-code。

### 静态模板数据（行 14）

```java
private static float[] a = new float[]{
    0.0f, 0.0f,      // P0: 原点
    20.0f, 0.0f,     // P1
    17.0f, -3.0f,    // P2
    17.0f, 3.0f      // P3
};
```

4 个点定义一个十字校准标记。

### 生成逻辑（行 22-100）

```java
public static String a() {
    StringBuilder sb = new StringBuilder();
    c c2 = f.a().e();                    // 当前固件控制器
    Object[] pts = Arrays.copyOf(a, 8);  // 复制静态点

    // 第一遍：画原始方向
    sb.append(c2.b(false) + "\n");       // 落笔
    if (c2 instanceof EBB) {
        // EBB 固件走专有命令
        sb.append(EBB.move(pts[2], pts[3], 1500, 0));
        ...
    } else {
        // Grbl/Marlin 走标准 G-code
        sb.append("G21\n");
        sb.append("G90\n");
        sb.append("F1500\n");
        sb.append("G1X" + pts[2] + "Y" + pts[3] + "\n");
        sb.append("G1X" + pts[4] + "Y" + pts[5] + "\n");
        sb.append(c2.b(true) + "\n");    // 抬笔
        sb.append("G1X" + pts[2] + "Y" + pts[3] + "\n");
        sb.append(c2.b(false) + "\n");   // 落笔
        sb.append("G1X" + pts[6] + "Y" + pts[7] + "\n");
        sb.append(c2.b(true) + "\n");    // 抬笔
        sb.append("G1X" + pts[0] + "Y" + pts[1] + "\n");
    }

    // 旋转 90 度再画一遍（验证方向）
    pts = rotate(pts, 90.0f);
    sb.append(c2.b(false) + "\n");
    ... (同样序列)

    // 画一个 10x10 的方框
    sb.append(c2.b(false) + "\n");
    sb.append("G1X0Y10\n");
    sb.append("G1X10Y10\n");
    sb.append("G1X10Y0\n");
    sb.append("G1X0Y0\n");
    sb.append(c2.b(true) + "\n");

    // 追加内置 test.gcode 资源（用 MessageFormat 注入 b(true)/b(false)）
    String tpl = IOUtils.toString(
        KenjoyDraw.class.getResourceAsStream("resources/test.gcode"));
    if (tpl != null) {
        tpl = MessageFormat.format(tpl, c2.b(true), c2.b(false));
        sb.append(tpl.replaceAll("\r", "") + "\n");
    }

    return sb.toString();
}
```

### 提炼出的 Grbl + Stepper 完整 G-code 序列

```
G21                          ← 单位 mm
G90                          ← 绝对坐标
F1500                        ← 进给
G1G90 Z5.0F3000              ← c2.b(false) 落笔
G1X17.0Y-3.0                 ← 画线
G1X17.0Y3.0
G1G90 Z0.0F3000              ← c2.b(true) 抬笔
G1X17.0Y-3.0                 ← 空走
G1G90 Z5.0F3000              ← 落笔
G1X17.0Y3.0                  ← 画线
G1G90 Z0.0F3000              ← 抬笔
G1X0.0Y0.0                   ← 空走回原点
... (旋转 90 度重复) ...
G1G90 Z5.0F3000              ← 落笔
G1X0Y10                      ← 画方框
G1X10Y10
G1X10Y0
G1X0Y0
G1G90 Z0.0F3000              ← 抬笔
... (内置 test.gcode 模板) ...
```

### 关键观察（验证 findings/02）

1. **空走用 `G1` 不是 `G0`**：所有移动（含抬笔状态下的空走）都是 `G1X..Y..`。
2. **F 只在开头声明一次**：`F1500` 后的 `G1X..Y..` 都不带 F。
3. **落笔/抬笔无 G4**：直接 `b(false)`/`b(true)`，无延时。
4. **Z 运动带独立 F3000**：与画线 F1500 分开。
5. **坐标系：绝对（G90）**：所有坐标都是绝对值。
6. **校准序列无 M30/M2 收尾**：本生成器结束于抬笔；普通出图还会按
   `notAutoReturn=false` 追加 `G90G0 X0Y0`。

## h/d.java 揭示的"对齐坐标系"细节

校准图第一行就是 `G21\nG90\nF1500`，然后直接 `b(false)` 落笔——**没有显式 G92 Z0**。

为什么？因为 G92 Z0 在**连接时**已发（见 `findings/03` 的 `a(boolean)` 首次笔控前校准）。校准图是连接后画的，Z 基准已建立。

> **历史推论（已作废）**：这里曾建议 hutuji 每次出图修改 Z 工件坐标。后续缓存核对证明
> 普通出图文件没有该指令；当前 profile 使用两次真实 Z0 运动，见 findings/02。

## f/a/d.java：批量出图（KAT 模式）

行 92-113 是 KAT 批量出图的 G-code 头：

```java
private void a(ExcelTab excelTab) {
    ...
    String string = "G21\nG90\n";
    float feed = Math.max(500.0f, excelTab.getPageSize().heightCM * 10.0f + 5.0f);
    string = string + com.kvenjoy.drawsoft.lib.kat.a.a(feed);
    fos.write(string.getBytes("utf-8"));
    ...
}
```

头部更简单：`G21\nG90\n` + `kat.a.a(feed)` 拼的换纸指令。注意 feed 按纸张高度动态算（`heightCM*10+5`）。

`kat.a.a()` 是换纸控制（`drawsoft/lib/kat/a.java`）：

```java
// lib/kat/a.java:13-23
String string = "M106\n";                                    // 开风扇（吸纸）
string += "G0 Y" + f2 + " F" + a + "\n";                     // Y 轴送纸
string += "M400\n";                                          // 等完成
string += "M107";                                            // 关风扇
```

这是 KAT 自动上纸机的控制（风扇吸附走纸），hutuji 的换纸走 M30 触发固件内置流程，不同。

## document_start 对比（已按当前 profile 修正）

| 来源 | document_start |
|------|----------------|
| 奎享校准图 | `G21\nG90\nF1500` |
| 奎享批量出图 | `G21\nG90` + 换纸指令 |
| hutuji 当前生产 profile | `G21\nG90\nF8000\nG1G90 Z0.0F10000\nG1G90 Z0.0F10000` |

校准图生成器与普通出图文件不是同一路径，不能据前者决定 hutuji 模板。当前模板取自缓存中
唯一 G-code 内容，并有意省略其回原点收尾。

## 当前 hutuji 生产模板

```gcode
G21                              # document_start
G90
F8000
G1G90 Z0.0F10000                 # 开头第 1 次抬笔
G1G90 Z0.0F10000                 # 开头第 2 次抬笔
G0 X{x0}Y{y0}F8000               # segment_first：空走到起点
G1G90 Z5.0F10000                 # 落笔
G1 X{x1}Y{y1}F8000               # segment：画线
G1 X{x2}Y{y2}F8000
G1G90 Z0.0F10000                 # line_end：抬笔
G0 X{x3}Y{y3}F8000               # 下一段空走
G1G90 Z5.0F10000                 # 落笔
...
G1G90 Z0.0F10000                 # document_end：收尾抬笔
```

当前真值仍以 `mcp-server/hutuji_mcp/hutuji_gwrite.toml` 为准；这里用于解释逆向来源与
“省略 `G90G0 X0Y0`”这一有意差异。
