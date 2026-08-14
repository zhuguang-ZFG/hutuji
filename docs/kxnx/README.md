# 奎享雕刻（KDraw）逆向素材

> 用途：为 hutuji 写字机**笔控方式对齐奎享**提供依据。奎享是这套 Grbl 写字机硬件的**原配上位机**，它的 G-code 序列在现役机器上验证过能正常出图——以它为参照比从零推导可靠得多。
>
> **反编译源码与完整素材已迁至独立仓库**：[github.com/zhuguang-ZFG/kxnx](https://github.com/zhuguang-ZFG/kxnx)（**私有仓**：匿名/外部访问返回 404 属预期，2026-08-14 实测；代理不得把 404 当链接失效处理，需要素材时向用户要访问权）。本目录只保留**逆向发现与对齐计划文档**，避免主仓膨胀（混淆名 .java 集中在 kxnx 仓，确切路径见下方「反编译源码树」——两棵树混淆名不同，引用前必须确认用的是哪棵）。

## 目录结构

```
docs/kxnx/
├── README.md                      ← 本文件：索引 + 指向 kxnx 仓
│   ├── findings/                      ← 逆向发现（27 份，深度递进）
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
│   ├── 13-path-params-and-quality.md ← 路径参数与画质档位（simplePaths / nearDst / DrawQuality）
│   ├── 14-engineering-details.md ← 工程细节补遗
│   ├── 15-task-scheduling-and-progress.md ← 任务调度/进度反馈 + 线程模型/看门狗勘误
│   ├── 16-wifi-discovery-and-connect.md ← WiFi 发现与连接全链路（含一处我方真实缺陷）
│   ├── 17-reconnect-resend-hazard.md ← 奎享重连重发缺陷与我方「不对齐」决定
│   ├── 18-paixi-inkscape-px-comparison.md ← 派曦绘图（inkscape_px）对照：z_stepper 笔控序列、两 fork 宏方向差异（非奎享，因 findings/17 §6 引用而归入本目录）
│   ├── 19-pause-abort-error-and-dual-executor.md ← 暂停/stop/reset2/error 白名单 + f/g 双执行器；勘误 findings/09 不存在的 f/c、f/i
│   ├── 20-ui-job-lifecycle-and-reconnect.md ← UI→JobManager→Executor 生命周期、FileStatus、串口重连弱判据与 pollLast 缺陷闭环
│   ├── 21-transport-watchdog-and-sd-executor.md ← 传输基类 `l`/`o` 发送侧语义勘误、三套探活互斥、缓冲 autoExpand 结案、ESP32-Grbl SD 执行器全链路
│   ├── 22-installed-config-vs-machine.md ← **非反编译**：kdraw 3.9.9 现役安装配置 × 真机 `$$` 快照（14/16 与我方机器头逐字同）× 产物几何反推容差；负 X 成因 = 左上原点 + XY 互换
│   ├── 23-drawsoft-gen-comm-review.md ← 全量复审收口：生成链+通信链 × GitHub 社区基线（三条 hutuji 现状勘误 + 逐条分诊总表；基准树 = windows_hutuji 第四棵树）
│   ├── 24-network-and-account-security.md ← 网络/账号/升级面：明文 HTTP 默认 + 带内密钥「加密」+ Jetty :90/LAN 共享零认证（基准树 decompiled_full）
│   ├── 25-batch-excel-and-programme.md ← 批量出图：本地 .gcode 直发旁路 + .pg 任意命令载体 + 批量编排四缺陷
│   ├── 26-cloud-fonts-and-wordx.md ← 云字库零校验下载 + XTEA 硬编码密钥 + wordx 绝对路径内嵌；.gfont 无 Zip Slip（正面）
│   ├── 27-awauto-and-processtabs.md   ← awauto 自动换纸机型控制器全量（主动换纸/续纸/恢复序列）+ processTabs 生成链全量（位图骨架化/SVG 硬化 SAX/云端生成）
│   └── 12-summary-and-action-list.md ← 总结 + hutuji 落地清单（P0/P1/P2/P3）
└── alignment/
    └── plan.md                    ← hutuji 对齐奎享的实施计划
```

## 反编译源码树（四棵，混淆名与行号互不通用）

kxnx 仓 `kx/tools/dump_agent/` 下并存三棵，另有第四棵在 `kx/windows_hutuji/decompiled/`（2026-08-14 登记），**引用前必须确认用的是哪棵**：

| 树 | .java 数 | 混淆命名体系 | 判据：CommandSender 所在 |
|----|---------|------------|------------------------|
| `decompiled_java/` ← 本目录 findings 05 / 17 的基准 | 446 | A | `machine/f/b.java` |
| `decompiled_full/` ← findings 16 的基准；A 体系超集，多含 `com/kvenjoy/drawsoft/lib/remote/` | 920 | A | `machine/f/b.java` |
| `windows_reverse/decompiled_java/` | 958 | B（字母整体位移）| `machine/g/b.java` |
| `kx/windows_hutuji/decompiled/` ← findings 23 的基准（Windows 版 kdraw.jar 反编译、清理后子集；**不在** `kx/tools/dump_agent/` 下） | 291 | A | `machine/f/b.java` |

- 判定方法：`grep -rl BlockingDeque <树>`，命中 `machine/f/b.java` 是 A 体系，命中 `machine/g/b.java` 是 B 体系。
- A 体系两棵树的同名文件内容基本相同，但 `decompiled_java/` 少数文件头多 3 行 CFR `Could not load the following classes` 注释，**行号比 `decompiled_full/` 大 3**。实测：`machine/grbl/a.java` 的 `G90G0 X0Y0` 在 `decompiled_java/` 是 `:300`、在 `decompiled_full/` 是 `:297`；而 `machine/f/b.java` 两树均 159 行、逐字节相同。
- 因此**行号只在声明了基准树时才有意义**，各 findings 头部均已注明。
- `kx/windows_hutuji/decompiled/` 于 2026-08-14 实测**存在**（291 文件，A 体系；行号与 `decompiled_full/` 同口径、比主树小 3——实测 `machine/grbl/a.java` 的 `g()=100` 在该树为 312-314、主树为 315-317）。本行此前写「不存在」是该树创建前的过时陈述，已修正；四棵树行号仍互不通用，引用前必须确认基准树。

## 三句话结论

1. **Stepper 笔控 = `G1 Z` 运动指令**（`zOn` 落笔 / `zOff` 抬笔，带 `F<zSpeed>`），**不用 M3/M5**。Servo/Laser 才用 M3/M5。
2. **下载文件不含 `G92 Z0`**，开头两次 `G1G90 Z0` 只是缓存兼容的幂等抬笔运动，不能建立基准。
   完整会话里，反编译 `grbl/a.java:253-263` 会在连接/首次笔控前旁路发送 `G92 Z0`；hutuji
   对齐为每次首画/重画前 fresh `Idle` + 100ms 后旁路固定 `G92 Z0`，随后才发送下载文件。
   kdraw 缓存的 9 个文件是 **1 个唯一产物的 9 个副本**，其内容无 G92；这与会话旁路有 G92 不矛盾。
3. **进给 F8000（画线与空走）、Z 轴 F10000**，无 G4 停顿；**每行都带 F**。
   （早期文档写的 F1500/F3000 来自代码默认值误读，已被真实产物推翻。）

`findings/*.md` 保留逆向过程，正文中的早期 hutuji 建议可能已被后续证据推翻；当前落地结果见
`findings/02-gcode-template.md`、`findings/12-summary-and-action-list.md` 与 `alignment/plan.md`。

## 笔控核心速查

| 类型（`grbl_type`）| 落笔 | 抬笔 |
|--------------------|------|------|
| `Servo` / `Laser` | `M3 S<laserS>` | `M5` |
| **`Stepper`** ← hutuji 这种 | **`G1 G90 Z<zOn> F<zSpeed>`** | **`G1 G90 Z<zOff> F<zSpeed>`** |
| `Servo_bachin` | `M3 S<penDownPos>` | `M3 S<penUpPos>` |
| `Custom` | `customOnCode` | `customOffCode` |

## 关键源文件索引（行号基准 = 主树 `kx/tools/dump_agent/decompiled_java/`）

| 文件 | 看什么 |
|------|--------|
| `com/kenjoy/kenjoycnc/machine/grbl/a.java` | `b(boolean)` 笔控分支（`decompiled_full/` 树行 262-284，主树 265-287，见 findings/01）；`a(boolean)` 在连接/首次笔控前旁路 `G92 Z0`（full 树行 253-263）；下载产物无 G92（见 findings/02、12）|
| `com/kenjoy/kenjoycnc/machine/grbl/GrblSettings.java` | `zOn/zOff/zSpeed/type` 字段定义（`2.0/0.0/3000` 是**出厂缺省**，不是我方取值；我方实测 `5.0/0.0/F10000`）|
| `com/kenjoy/kenjoycnc/machine/marlin/a.java` | Stepper 笔控跨固件一致性（行 196-200）|
| `com/kenjoy/kenjoycnc/h/d.java` | **校准测试图案**生成器（固定箭头+10×10方框+追加 test.gcode），**不是**出图拼接模板；勿据此推出图序列 |
| `com/kenjoy/kenjoycnc/machine/grbl/a.java` `b()` :300 | 出图收尾回原点 = `G90G0 X0Y0`（我方 toml 有意省略：本机固件判为换纸触发）。注：在 `decompiled_full/` 树里同一句是 `:297` |
| `com/kenjoy/kenjoycnc/machine/f/b.java` | CommandSender：窗口 100 字节 character-counting（`:70`），重连只把最后一条在途行挪到队尾（`:83-88`，缺陷，见 findings/17）|
| `com/kvenjoy/drawsoft/lib/grbl/Type.java` | 笔控类型枚举 |
| `com/kenjoy/kenjoycnc/machine/c/a.java` | ESP32-Grbl 专用控制器：`[ESP220]` 上传（`:148-151`）、SD 进度（`:54-85`）、**收到首个状态行才算已连上**（`:76-84`）|
| `com/kenjoy/kenjoycnc/machine/b/a.java` + `machine/b/b.java` | 传输基类与看门狗：`l`/`o` 是**发送侧**语义（`:143-144`），两级 5s + **约** 1s（见 findings/21 §1-2）|
| `com/kenjoy/kenjoycnc/machine/f/g.java` | SD/ESP 执行器：模态上传 UI（`:39`）、状态行 SD 字段消失判完成、stop = 抬笔 + `G90G0 X0Y0`（`:91-95`）|

## 合规说明

- 反编译仅为**互操作目的**（理解上位机发给自家写字机硬件的 G-code 协议），不重新分发奎享源码、不用于规避授权。
- hutuji 项目**不包含**奎享的二进制或原代码，仅参考其 G-code 协议格式（G-code 本身是开放标准）。
- 完整合规说明见 kxnx 仓 README。
