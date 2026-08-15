# 27 · awauto 自动换纸机型控制器 + processTabs 生成链全量剖析

> 来源：2026-08-14 R19 深审（此前 findings/23 §8 自认「`processTabs/`、`machine/ui/` 除 esp32grbl 未深读」，awauto 仅抽查 `g.java:46-62`，本轮补洞）。
> 行号基准树：`D:/GitHub/kxnx/kx/tools/dump_agent/decompiled_full/`（A 体系）。标注【亲读】=主代理逐行核过；【scout】=侦察代理证据 + 主代理抽核。
> 结论先行：两包均为**参照存档/反面印证**，hutuji 零动作。awauto 证明「换纸由上位机流水线主动触发」是奎享在**带传感机型**上的方案，与我方纯 Grbl 无传感机型的页尾 `M30` 被动触发是两种合理形态；processTabs 证明我方 SVG 白名单闸门与位图骨架化路线与奎享同方向。

## 一、awauto 包：AW 自动换纸写字机控制器（10 文件全读）

### 1.1 是什么

`MachineType.AW_Auto` 机型的控制器，**`machine/awauto/g.java` extends `machine/grbl/a.java`**【亲读：类头】——Grbl 控制器的子类，换纸能力以 override 叠加。纸事件不是 `[ESP901]`，而是固件行包含 **`AW_AT_Paper`**【亲读：`g.java` `g(String)`】。

| 文件 | 职责【scout 全读，关键处亲读】 |
|------|------|
| `AWAutoSettings.java` | 配置：`zHeight`（键 `aw_z`，默认 **38.53f**）、`repeatPaperTime`（键 `aw_repeat_paper_delay`，默认 **20** 秒）【亲读：`:12-13`】；extends GrblSettings，type=Stepper |
| `a.java` | 连接控制：三段波特率轮换 `{115200, 250000, 9600}`【亲读：静态数组】+ 5s 超时 + 5s 稳定等待 + `FirmwareEventConnected` 判活 |
| `b.java` / `c.java` | Callable 串口连接实现 / 监控线程（等待 Callable + 超时 + 稳定确认） |
| `d.java` / `f.java` | 回调接口 / 连接失败弹窗 |
| `e.java` | 主控制器（extends grbl.a），响应 `AW_AT_Draw` 初始化 g+h |
| `g.java` | **换纸时序核心**（全文亲读，见 §1.2–1.4） |
| `h.java` | 进纸批 Complete 后 `Thread.sleep(6000)` 发 `aw_paper_ready` 事件【亲读：`:22-23`】 |
| `i.java` | TimerTask：纸未到且 `n>0` → 递归 `e(false)` 再进一次；`n==0` → `E()` 弹窗【亲读：`:48-57`】 |
| `j.java` | 换纸完成后回原点 + 调 `l.k()` 继续下一任务 |

### 1.2 进纸序列（`g.java` `e(boolean)`，亲读全文）

```
$X → $21=1 → G91 G0 X-30 Y30 [首进纸追加 Z-<zHeight>] → G91 G0 Y350
```

随后 `new Timer(true).schedule(new i(this), repeatPaperTime * 1000L)` 启动续纸看门狗。`n` 初值 2、每次 `e()` 递减；Timer 到点纸未到（`m` 未置位）且 `n>0` → 再进一次；`n==0` → `E()` 弹「进纸错误，请检查好机器和纸张后继续」对话框（继续=`e(true)` 重进，终止=停任务 + 取消 Timer）。

### 1.3 Reset 恢复序列（`g.java` `c(String)` override，亲读）

收到 `MSG:Reset to continue` → 置 `m`、取消 Timer、`c(false)`，再整批发送 `$X, $21=0, G91 G0 Y-15, G91 G0 X1 Y7`（解除软限位 + 小距离退纸避让）。

### 1.4 换纸发起方与完成判据（与我方的本质差异）

| 维度 | 奎享 awauto | hutuji |
|------|------------|--------|
| 发起方 | **上位机任务流水线主动**：换页处 `machine/f/l.java:201-204` `instanceof e` → `D().e(true)`；取消路径 `:141-142` `D().D()`【亲读】 | 页尾 S3 旁路 `G1G90 X0Y0F8000` 归位 + `M30` 单发（2026-08-14 归位决策；此前仅 M30），固件被动触发（`docs/protocol.md` §5） |
| 完成判据 | 进纸批 Complete 后**固定 sleep 6s** 发 `aw_paper_ready`（`h.java:22-23`）——**是定时器不是纸到位传感确认** | `[ESP901]` `Changing` 字段 + `[PaperStatus]` 定点消息 |
| 失败恢复 | 有界重进纸（n=2）→ GUI 对话框人工决策 | §4.1 abort 分流 + 云端状态 |

判：hutuji 机型是纯 Grbl 无纸传感，M30 push 模型更简单且已定案（findings/23 I6 外部印证）；awauto 的 `$21` 软限位开关序列、进纸距离、6s 延时、20s 续纸均为**机构专属参数，参照存档，禁改我方换纸机械/时序**（§9 白名单外）。「有界重进纸次数 → 人工决策」的失败恢复形态是可借鉴思路，但 hutuji 无 GUI，等价物是云端/用户通知，登记为候选不动作。

### 1.5 与我方共用的部分

错误白名单与窗口流控复用 grbl 父类（`g()=100` 窗口，双树 `:313` 亲验一致）——findings/09/17 的流控结论对 awauto 机型同样适用。

## 二、processTabs 包：17 类输入 → 路径 → G-code（全读）

### 2.1 架构

`IProcessTab` 19 方法契约 → `BaseTab` 抽象基类（Gson 设置持久化、`processAsync` 线程池）→ `ImageTab` 图片基类（Thumbnailator 缩放、`maxImageSize` 限制）→ 各具体 Tab【scout】。位图→路径的原子操作全部走 `CLibraryUtils`/`CLibrary`（JNA）调 **kenjoycncc.dll** 原生方法：`binaryImage`/`skeletonize`/`pathGenernal`/`genernalSquiggle`/`RDPppd`/`getThreshold`【亲读：`CLibraryUtils.java:89` skeletonize 原生调用】。

### 2.2 位图链（与 hutuji 位图路线对照的核心）

- **ThinningTab**【亲读：`:82-84`】：`binPixels = skeleton(w, h, binPixels, Integer.MAX_VALUE)`（迭代到收敛）→ `createPath(binPixels, w, 1, smooth, 0.01f)`。UI 参数：threshold [0,250] 默认 30、smooth [0,10] 默认 5【scout】。
- **EdgeTab**：Potrace 或 binaryImage+skeletonize 二选一（`checkBoxThin`）；threshold [0,255] 默认 50、noise [0,10] 默认 0【scout】。Potrace 是**外部二进制** `potrace-1.16.win64/win32/mac-x86_64`，经 `av.a(...)` 包装的外部进程调用【亲读：`excel/c/f.java:444-451`】。
- 对照 hutuji：`bitmap_svg.py` = skimage `skeletonize` + sknw 建图描摹，**同形态同方向**（反面印证）。奎享 `createPath` 容差 0.01f 小于我方 `linesimplify 0.05mm`，但原生库单位体系未考据，**不可直比、不作为调参依据**（容差是钉死决策）。Potrace 作为位图→路径成熟备选登记（我方目前仅 sknw 单一路径，sknw 属低维护依赖）——候选，不动作。

### 2.3 SVG 链与解析硬化（R19 初审误判，本轮再核定案）

调用链【亲读】：`SvgTab.getSVG()` → `q_0.a(new FileInputStream(file))` → **`r/q_0.java:32-35`** `new ae_0().a(inputStream)` → **`r/ae_0.java:156-161`** `k.newSAXParser()` → `getXMLReader()` → parse。`k` 是静态 SAXParserFactory，其 static 块（`ae_0.java:124-126`）禁用三项：

```
external-general-entities = false
external-parameter-entities = false
nonvalidating/load-external-dtd = false
```

且 `a(InputStream)` 开头探测 gzip 魔数（`0x8B1F`）自动套 `GZIPInputStream`（`ae_0.java:141-153`）。**故奎享 SVG 解析（含 SvgTab 与 DxfTab）具备 XXE/外部实体防护**，与我方 `_validate_svg` 白名单闸门方向一致（我方更严：元素/属性白名单 + 拒 `href`/`url(...)` + 深度/体积上限）。
勘误：R19 初审以「processTabs 内仅 DxfTab 引用 ae_0」判「SvgTab SAX 三禁」张冠李戴——漏了 `r/q_0.java` 委托层，已撤回；三项 setFeature 准确归属是 `r/ae_0.java`（共享解析器），不是 SvgTab 自有代码。
另：`com/kvenjoy/drawsoft/lib/b.java:31-34` 存在**未硬化** `DocumentBuilderFactory`（无 setFeature）【亲读】，调用方未追，厂商侧观察。

### 2.4 其余 Tab 一句话【scout】

| Tab | 策略 | 关键参数 |
|-----|------|---------|
| SquiggleTab | 波浪描摹（原生 `genernalSquiggle`） | ymult=30, xstep=95, smooth=50 |
| LinesTab | Opencv 轮廓 + 蛇形行扫描 | threshold 默认 60, toolsize 默认 3 |
| DotTab | 点阵（4 法含杂点） | linesPreMM 默认 4, delay [0.01,0.5]s |
| DrawTab | 手绘 8 工具，直接路径生成 | 背景透明度 [0,100] |
| GridTab / NoteTab / SplitNoteTab / WordTab | 模板排版/批注/分栏/文档 | 行距字距页边距（UI 层） |
| ExcelTab | POI 读 xlsx + Potrace 路径 | 单元格映射 |
| DxfTab | Kabeja DXF→SVG 转换，复用 ae_0 硬化 SAX | — |

### 2.5 G-code 生成在云端

`h/c.java:155`【亲读】`new HttpPost(<host> + "/kvenjoyweb/drawsoftapp")`——本地 Tab 只产路径数据，逐行 G-code 模板/限幅/排序的真实实现在云端，**本地不可审计**（维持 findings/23「云端生成器不可见」结论）。与 hutuji「云端生成」同架构（反面印证）。

## 三、未验证

- awauto 的 `b.java`/`c.java` 连接细节、`e.java` 的 `AW_AT_Draw` 初始化序列为 scout 证据，未逐行亲读。
- kenjoycncc.dll 原生算法（skeletonize/createPath 内部）不可见，参数语义以调用点为准。
- `lib/b.java` 未硬化 XML 解析的实际调用方与可达性未追（厂商侧，不影响我方）。
- 全部静态读码，无真机/无云端账号动态验证。
