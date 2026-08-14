# 13 · 路径参数与画质档位（与我们 vpype 管线直接对应）

> 源文件：
> - `com/kvenjoy/drawsoft/lib/PathParameters.java`
> - `com/kenjoy/kenjoycnc/model/Settings.java`
> - `com/kenjoy/kenjoycnc/machine/kat/KATSettings.java:70-128`
> - `com/kvenjoy/drawsoft/lib/remote/DrawQuality.java`
> - `com/kenjoy/kenjoycnc/h/c.java:72-97`（参数装配）

## 1. PathParameters 与 vpype 管线一一对应

奎享把「路径几何处理」的参数集中在 `PathParameters`，和我们 `hutuji_gwrite.toml` + vpype 命令链的职责完全对齐：

```java
public class PathParameters {
    public boolean optimizePaths;        // 路径排序优化
    public float nearDst;                // 邻近点/碎笔合并阈值
    public float offsetX, offsetY;       // 画布偏移
    public String startGcode, endGcode;  // 头尾自定义 G-code
    public boolean notAutoReturn;        // 收尾不回原点
    public float rotateMin, rotateMax;   // K_AT 专用（旋转机构）
    public float yScaleFactory;          // Y 轴缩放
    public float targetDistY;            // K_AT 专用
    public float paperWidth, paperHeight;
    public float simplePaths = 0.02f;    // 线简化容差
}
```

对应关系：

| 奎享字段 | 默认值 | hutuji 对应 | 我们的值 |
|---|---|---|---|
| `simplePaths` | **0.02mm** | vpype `linesimplify --tolerance` | **0.05mm** |
| `nearDst` | **0.3mm** | vpype `linemerge --tolerance` | **0.3mm** |
| `optimizePaths` | bool | vpype `linesort` | 固定启用 |
| `startGcode` / `endGcode` | 空 | `document_start` / `document_end` | 已用 |
| `notAutoReturn` | `not_go_zero`，默认 false | §5 禁令「收尾禁止 G0 X0 Y0」 | 恒不回零 |
| `paperWidth/Height` | 运行时传入 | MCP 端限幅 X≤190 Y≤277 | 已有 |

> **本表依据真实产物核对后修正**：`notAutoReturn` 默认 false 即"收尾会回原点"——
> 9 个缓存文件字节完全相同，只算 1 个唯一产物；该内容末尾是 `G90G0 X0Y0`。
> 我们是**故意偏离**（本机固件判为换纸触发，§5 禁止），不是对齐。

## 1.5 关键发现：G-code 生成在云端，不在上位机本地

`h/c.java` 的 `b()` 把整个**路径列表 GZIP 压缩后 HTTP POST 到云端**（`drawsoftapp`），
请求体带 `pathParam`(PathParameters JSON)、`createrParam`(CreaterParameters JSON)、
`machineType`、`pathsBounds*` 等；云端返回 G-code，本地解压接收（`DataInputStream.readInt` = 总长）。

也就是说：
- **几何→G-code 的转换（简化/合并/排序/进给装配）发生在云端服务端**，本地只做参数装配与收发。
- 反编译的客户端里**没有** `linesimplify`/`linemerge` 的实现代码——那是服务端逻辑。
- 我们能从客户端确定的只有**参数值**（`simplePaths=0.02`、`nearDst=0.3`、画质档表）和
  **产物格式**（真实 G-code 文本，见 [[02-gcode-template]]）；具体算法（Douglas-Peucker? 路径排序启发式?）
  不可见，只能由产物反推。
- 这和我们 hutuji 架构**同构**：我们也在云端（京东云 VPS）用 vpype 做几何处理，S3 只转发。
  奎享用这个架构是为了"手机/电脑共用同一台设备、随时切换"，我们是为了"S3 算力不足"。殊途同归。

## 2. 最重要的一条：奎享几乎不做线简化

**`simplePaths = 0.02mm`，是我们当前 `0.1mm` 的五分之一。**

0.02mm 的容差在实际几何上近乎「不简化」——远小于笔触精度（0.1mm），也远小于步进分辨率（Z 轴 50 steps/mm = 0.02mm/step，XY 同量级）。

这推翻了一个可能的误解：**奎享不是靠减少点数来保证流畅的**。它保留了原始高密度路径，靠的是 [[09-flow-control-deep]] 里的**窗口化流控**（character-counting）把往返延迟藏在运动时间之后。

对我们的含义：

- ~~我们当前的 `linesimplify 0.1mm` 是在**逐行等 ok** 这个约束下的实测权衡——用降低点密度换流畅度；0.05mm 以下开始卡顿。~~
- ~~如果将来实施窗口化流控（P2），就**可以把容差调回接近奎享的水平**（0.02–0.05mm），既流畅又保留细节。届时小字/密集细节的棱角问题（见 review 记录）也一并解决。~~
- ~~换句话说：`linesimplify 0.1mm` 是「用画质换流畅」的当前折中；窗口化流控才是「两者都要」的正解。~~
- **⚠️ 以上三行已 supersede（2026-07-31）**：窗口化流控已落地（S1–S3 烧录，`kWindow=512`），容差**已回降到 0.05mm 并由 `test_contract.py` 钉死**（`gcode_gen.py`）。即「上窗口化」这一前提**已满足**，0.05 是现行锁定值，**不是**待做的折中；不得据本段把容差抬回 0.1 或当作「未验证」。

## 3. nearDst = 0.3mm，比我们的 linemerge 激进 6 倍

奎享用 0.3mm 合并首尾邻近的碎笔画，我们设 0.05mm。

0.3mm 意味着两条笔画端点相距 0.3mm 以内就并成一条 —— 少一次抬笔和一次落笔。当前生产 profile
无 `G4`，Z 行程 5mm 使用 `F10000`（理论运动时间约 0.03s/单程）；减少抬落笔次数仍能降低机械动作与笔尖扰动。

~~可考虑把 `LINEMERGE_TOLERANCE_MM` 从 0.05 提到 0.2–0.3。风险低（只合并端点几乎相接的线，不改变构图），收益是抬落笔次数下降。但**这会改变画面**（原本断开的两笔变连笔），属于画质决策，需要用户确认后再改。~~ **⚠️ 已 supersede（2026-07-31）**：`LINEMERGE_TOLERANCE_MM` **已是 0.3**（`gcode_gen.py`，对齐奎享 `nearDst`，`test_contract.py` 钉死）。本段把现值写成 0.05 是过时陈述；0.3 为现行锁定值，非「待考虑」。

## 4. 画质档位 → 进给速率映射表

`KATSettings.getFeedRate(DrawQuality, custom)`（`:70-98`）与 `getJogFeedRate`（`:100-128`）：

| DrawQuality | 画线进给 `feedRate` | 空走进给 `jogSpeed` |
|---|---|---|
| `High` | 1500 | 2000 |
| `Medium` | 2000 | 3000 |
| `Low` | 3000 | 4000 |
| `VeryLow` | **5000** | 6000 |
| `Custom` | 用户值 | 用户值 |
| （default 兜底） | 4000 | 5000 |

要点：

- **档位名与速率成反比**：`High`（高画质）= 慢（1500），`VeryLow`（低画质）= 快（5000）。命名是「画质」不是「速度」，读代码时容易反向理解。
- **默认档位是 `VeryLow`**（`Settings.java:54`：`drawQuality = DrawQuality.VeryLow`）——即奎享出厂默认走 **5000mm/min 快速模式**，牺牲画质换速度。
- 空走速率恒比画线速率高一档，符合直觉（抬笔时可以跑快）。
- 当前 `hutuji_gwrite.toml` 的画线/空走均为 `F8000`，Z 抬落笔为 `F10000`。这些数值来自
  奎享缓存中的唯一 G-code 内容，不是上表 `Settings` 默认档位；`F8000` 已高于内置 `VeryLow`
  的画线 `F5000`，因此不能再据此宣称“比奎享默认更保守”，画质与失步风险须以实机结果判断。

对我们的含义：如果将来要「画快点」的用户需求（S3 曾考虑过 `hutuji.speed` 工具），奎享这张表可以直接作为档位参照，不用自己拍数字。注意 `F5000 = 83mm/s`，对无限位开关的机器要评估失步风险。

## 5. Settings 全量用户可调参数（反推奎享认为什么值得暴露）

`model/Settings.java` 全字段（含 `default.properties` 键名与默认值）：

| 字段 | properties 键 | 默认 | 说明 |
|---|---|---|---|
| `feedRate` | `feed_rate` | 1500 | 画线进给 |
| `jogSpeed` | `jog_speed` | 2500 | 空走进给 |
| `toolOnDelay` | `tool_on_delay` | 0.2 | 落笔后停顿（**仅 Servo 用**）|
| `toolOffDelay` | `tool_off_delay` | 0.2 | 抬笔前停顿（**仅 Servo 用**）|
| `nearDst` | — | 0.3 | 碎笔合并阈值（不暴露给 properties）|
| `singleStep` | `single_step_command` | false | 单步模式（调试）|
| `autoSetZero` | `auto_set_zero` | false | 自动设原点 |
| `offsetX/Y` | `offset_x/y` | 0 | 画布偏移 |
| `autoConnect` | `auto_connect` | true | 启动自动连接 |
| `autoReconnect` | `auto_reconnect` | false | 断线自动重连 |
| `notAutoReturn` | `not_go_zero` | false | 收尾不回原点 |
| `soundFileComplete` | `sound_complete` | false | 完成提示音 |
| `homeDelay` | `home_delay` | 1 | 每 N 次归零一次 |
| `autoNext` | `auto_next` | false | 自动下一张 |
| `autoNextTime` | `auto_next_time` | 10 | 自动下一张间隔(s) |
| `connectTimeout` | `connect_timeout` | **5** | 应用层看门狗超时(s)，见 [[10-transport-layer]] |
| `marlinHomeGcode` | `marlin_home_code` | `G28` | Marlin 归零码 |
| `unit` | `unit` | mm | 单位 |
| `drawQuality` | — | `VeryLow` | 画质档（不在 properties）|

几个观察：

- **`autoReconnect` 默认 false** —— 奎享不默认自动重连，断线要用户手动处理。我们 S3 侧 Pipe 是无限退避重连（更适合无人值守场景），这是合理的差异，不必对齐。
- **`nearDst` 与 `drawQuality` 不在 `default.properties`** —— 只能在 UI 改。说明奎享认为这两个是「普通用户会调」的（放 UI），而 properties 是给高级用户/OEM 预置的。
- **`singleStep`（单步模式）** —— 逐行手动确认，纯调试用。我们 `hutuji.pause/resume` 覆盖了类似需求。

## 6. 落地建议（补充 [[12-summary-and-action-list]]）

| 优先级 | 事项 | 依据 |
|---|---|---|
| ✅ 已做 | `linesimplify` 容差 0.2mm → 0.1mm → **0.05mm** | 实测算法拐点后继续下调；现值 0.05 由 `gcode_gen.py` 定义、`test_contract.py` 钉死 |
| ✅ 已做 | `linemerge` 容差 0.05mm → **0.3mm** | 奎享 `nearDst=0.3` 现役值（现役安装 `settings.json` 实测，见 [[22-installed-config-vs-machine]] §4），实测安全，减少抬落笔次数 |
| P2（若上窗口化流控） | `linesimplify` 可再降到 **0.02mm** 对齐奎享 | 需先做窗口化流控；现值 0.05 会简化掉奎享产物中 4.8% 的段（见 [[22-installed-config-vs-machine]] §6） |

**不建议对齐的**：`autoReconnect=false`（我们无人值守场景需要自动重连）、`drawQuality` 默认 `VeryLow`（我们优先画质）、`singleStep`（已有 pause/resume）。

## 7. 实测数据：我们能追到什么密度（2026-07-27）

步长中位数对比（实测）：

| 源 | 行数 | 中位步长 | 说明 |
|---|---|---|---|
| 奎享 2.gcode（手写字，云端 0.02 容差） | 946 | **0.254mm** | 靠窗口化流控 |
| 我们 0.2mm 容差 | 131 | 4.4mm | 几何过简化 |
| **我们 0.1mm 容差（现值）** | 143 | 4.4mm（曲线分段更密） | **逐行流控的流畅极限** |
| 我们 0.05mm 容差 | 249 | 2.2mm | 开始卡顿（往返>运动） |
| 我们 0.02mm 容差 | 279 | 2.2mm | 明显卡顿 |
| 我们 未简化 | 6407 | 0.1mm | 完全走停顿 |

结论：逐行流控下 0.1mm 是甜点（实机串口 92 行全在 6ms 收到 ok、0 行阻塞、整图 6.3s）。
要再追奎享的 0.254mm 密度，**只能上窗口化流控**（P2），几何侧已无余量。

相关：[[09-flow-control-deep]]（为什么奎享能不简化）、[[02-gcode-template]]（头尾模板）、[[12-summary-and-action-list]]（总清单）

## 8. 位图路线的像素量化地板（TRACE-GEOMETRY-GAP 补登记，2026-08-14）

> 本节澄清 §6 一条建议的**适用边界**，不推翻任何锁定值。

位图路线（agnes 1024² PNG → 骨架描摹）的**像素量化地板约 0.166–0.21mm/px**：
栅格像素本身就是段长的下界，任何简化/合并容差都叠在这层量化之上。
实测星形产物段长中位数 **1.93mm（n=211，p5 = 0.4573）**，与奎享现役产物
（findings/22 实测中位数 **0.254mm、n=673**）差约 **7.6 倍**。

含义：§6「`linesimplify` 向奎享 0.02mm 靠拢」这一行**只对 LLM 矢量路线有意义**——
矢量路线的段长地板是 `linesimplify` 容差本身（现值 0.05mm，`test_contract.py` 钉死），
收紧容差能实打实逼近奎享密度；而位图路线在 0.05/0.3 容差**之上**还有像素量化地板，
把简化容差收到 0.02mm 也追不平 7.6 倍差。二者不矛盾：一个是几何侧余量，一个是
输入侧地板；对位图输入谈「对齐奎享密度」应先解决栅格分辨率，而不是动容差。
