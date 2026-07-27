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
| `simplePaths` | **0.02mm** | vpype `linesimplify --tolerance` | **0.2mm** |
| `nearDst` | **0.3mm** | vpype `linemerge --tolerance` | **0.05mm** |
| `optimizePaths` | bool | vpype `linesort` | 固定启用 |
| `startGcode` / `endGcode` | 空 | `document_start` / `document_end` | 已用 |
| `notAutoReturn` | `not_go_zero`，默认 false | §5 禁令「收尾禁止 G0 X0 Y0」 | 恒不回零 |
| `paperWidth/Height` | 运行时传入 | MCP 端限幅 X≤190 Y≤277 | 已有 |

## 2. 最重要的一条：奎享几乎不做线简化

**`simplePaths = 0.02mm`，比我们的 `0.2mm` 小一个数量级。**

0.02mm 的容差在实际几何上近乎「不简化」——远小于笔触精度（0.1mm），也远小于步进分辨率（Z 轴 50 steps/mm = 0.02mm/step，XY 同量级）。

这推翻了一个可能的误解：**奎享不是靠减少点数来保证流畅的**。它保留了原始高密度路径，靠的是 [[09-flow-control-deep]] 里的**窗口化流控**（character-counting）把往返延迟藏在运动时间之后。

对我们的含义：

- 我们当前的 `linesimplify 0.2mm` 是在**逐行等 ok** 这个约束下的正确权衡——用降低点密度换流畅度，已实测消除卡顿（9266 行 → 172 行）。
- 如果将来实施窗口化流控（P2），就**可以把容差调回接近奎享的水平**（0.02–0.05mm），既流畅又保留细节。届时小字/密集细节的棱角问题（见 review 记录）也一并解决。
- 换句话说：`linesimplify 0.2mm` 是「用画质换流畅」的临时手段；窗口化流控才是「两者都要」的正解。

## 3. nearDst = 0.3mm，比我们的 linemerge 激进 6 倍

奎享用 0.3mm 合并首尾邻近的碎笔画，我们设 0.05mm。

0.3mm 意味着两条笔画端点相距 0.3mm 以内就并成一条 —— 少一次抬笔 + 一次落笔 + 两次 `G4` 稳定（如果有）。对抬落笔较慢的机构（我们 Z 行程 5mm @ F3000 ≈ 0.1s 单程），减少抬落笔次数对总耗时的影响比减少线段更大。

可考虑把 `LINEMERGE_TOLERANCE_MM` 从 0.05 提到 0.2–0.3。风险低（只合并端点几乎相接的线，不改变构图），收益是抬落笔次数下降。但**这会改变画面**（原本断开的两笔变连笔），属于画质决策，需要用户确认后再改。

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
- 我们 `hutuji_gwrite.toml` 固定 `F1500`（画线）/ `F2500`（空走），相当于奎享的 `High` 档 + 稍快的空走。**我们的画质设定比奎享默认保守得多。**

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
| P2（随窗口流控一起） | `linesimplify` 容差从 0.2mm 回调到 0.02–0.05mm | 奎享 `simplePaths=0.02`；前提是先做窗口化流控 |
| P3（需用户确认画质） | `linemerge` 容差从 0.05mm 提到 0.2–0.3mm | 奎享 `nearDst=0.3`，减少抬落笔次数 |
| P3（若做调速工具） | 画质档位表直接引用奎享映射 | `getFeedRate` 表，注意 High=慢 |

**不建议对齐的**：`autoReconnect=false`（我们无人值守场景需要自动重连）、`drawQuality` 默认 `VeryLow`（我们优先画质）、`singleStep`（已有 pause/resume）。

相关：[[09-flow-control-deep]]（为什么奎享能不简化）、[[02-gcode-template]]（头尾模板）、[[12-summary-and-action-list]]（总清单）
