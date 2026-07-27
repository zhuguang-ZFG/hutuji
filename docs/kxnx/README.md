# 奎享雕刻（KDraw）逆向素材

> 用途：为 hutuji 写字机**笔控方式对齐奎享**提供依据。奎享是这套 Grbl 写字机硬件的**原配上位机**，它的 G-code 序列在现役机器上验证过能正常出图——以它为参照比从零推导可靠得多。
>
> **反编译源码与完整素材已迁至独立仓库**：[github.com/zhuguang-ZFG/kxnx](https://github.com/zhuguang-ZFG/kxnx) → `kx/windows_hutuji/`。本目录只保留**逆向发现与对齐计划文档**，避免主仓膨胀（反编译 291 个混淆名 .java 集中在 kxnx 仓）。

## 目录结构

```
docs/kxnx/
├── README.md                      ← 本文件：索引 + 指向 kxnx 仓
├── findings/                      ← 逆向发现（12 份，深度递进）
│   ├── 01-pen-control.md          ← 笔控核心（Stepper vs Servo vs Laser）速查
│   ├── 02-gcode-template.md       ← 出图 G-code 完整模板
│   ├── 03-init-sequence.md        ← 连接/校准/原点序列
│   ├── 04-config-keys.md          ← default.properties 配置键
│   ├── 05-grbl-controller-deep.md ← Grbl 控制器逐方法剖析（连接握手/ESP 路由/笔控三方法）
│   ├── 06-cross-firmware-pen-control.md ← 跨固件对比（Grbl/Marlin/EBB/KAT）
│   ├── 07-gcode-generation-deep.md ← G-code 生成与校准测试图
│   ├── 08-config-architecture.md  ← 配置三层架构（properties→POJO→出图参数）
│   ├── 09-flow-control-deep.md   ← 流控引擎：窗口 100 字节 character-counting
│   ├── 10-transport-layer.md     ← 传输层：串口 vs WiFi，与 hutuji Telnet 对照
│   ├── 11-esp32grbl-wifi-connect.md ← WiFi 连接与 mDNS 设备发现
│   └── 12-summary-and-action-list.md ← 总结 + hutuji 落地清单（P0/P1/P2/P3）
└── alignment/
    └── plan.md                    ← hutuji 对齐奎享的实施计划
```

**反编译源码**（笔控/出图/配置相关 291 个 .java）在 kxnx 仓：`kx/windows_hutuji/decompiled/`。

## 三句话结论

1. **Stepper 笔控 = `G1 Z` 运动指令**（`zOn` 落笔 / `zOff` 抬笔，带 `F<zSpeed>`），**不用 M3/M5**。Servo/Laser 才用 M3/M5。
2. **连接后首次笔控前发 `G92 Z0`**，把"当前物理位置"声明为 Z 基准。对带弹簧的 Z 轴是关键。
3. **落笔/抬笔带 F3000 限速，无 G4 停顿**；线段 `G1 X.. Y..` 继承首个 F1500。

详见各 `findings/*.md`，对齐步骤见 `alignment/plan.md`。

## 笔控核心速查

| 类型（`grbl_type`）| 落笔 | 抬笔 |
|--------------------|------|------|
| `Servo` / `Laser` | `M3 S<laserS>` | `M5` |
| **`Stepper`** ← hutuji 这种 | **`G1 G90 Z<zOn> F<zSpeed>`** | **`G1 G90 Z<zOff> F<zSpeed>`** |
| `Servo_bachin` | `M3 S<penDownPos>` | `M3 S<penUpPos>` |
| `Custom` | `customOnCode` | `customOffCode` |

## 关键源文件索引（在 kxnx 仓 `kx/windows_hutuji/decompiled/`）

| 文件 | 看什么 |
|------|--------|
| `com/kenjoy/kenjoycnc/machine/grbl/a.java` | `b(boolean)` 笔控分支（行 262-284）、`a(boolean)` 首次 G92 Z0（行 250-260）|
| `com/kenjoy/kenjoycnc/machine/grbl/GrblSettings.java` | `zOn/zOff/zSpeed/type` 字段定义 |
| `com/kenjoy/kenjoycnc/machine/marlin/a.java` | Stepper 笔控跨固件一致性（行 196-200）|
| `com/kenjoy/kenjoycnc/h/d.java` | 完整 G-code 拼接器模板（行 22-60）|
| `com/kvenjoy/drawsoft/lib/grbl/Type.java` | 笔控类型枚举 |

## 合规说明

- 反编译仅为**互操作目的**（理解上位机发给自家写字机硬件的 G-code 协议），不重新分发奎享源码、不用于规避授权。
- hutuji 项目**不包含**奎享的二进制或原代码，仅参考其 G-code 协议格式（G-code 本身是开放标准）。
- 完整合规说明见 kxnx 仓 README。
