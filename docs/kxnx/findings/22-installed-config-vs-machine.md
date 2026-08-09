# 发现 22：现役安装配置 × 真机 `$$` 快照 × 产物几何（2026-08-07）

> 素材来源与前 21 份不同：**不是反编译代码，而是 kdraw 在本机的现役安装目录**
> `C:/Program Files (x86)/kdraw(x64)/`（`version.xml`：`versionName=3.9.9 versionCode=193`）。
> 因此本文的值是**这台机器上真实持久化的用户配置**，不是 `Settings.java` 的出厂缺省——
> 两者多处不同，前 21 份记的默认值不因本文作废，但**引用「奎享现役值」时应以本文为准**。
>
> 复核命令（只读，未改 kdraw 任何文件）：读 `settings/默认Grbl机器/{Grbl,AxesData,page_settings,settings,FontsSetData,JSSParams}.json`
> 与 `cache/{2..10}.gcode`。9 个 `.gcode` 的 md5 **全为 `df6d2d748661…`**，即 1 个唯一产物的 9 个副本
> （与 `docs/kxnx/README.md` 既有记载一致，本文不改该结论）。

## 1 `Grbl.json.kv` 是真机 `$$` 快照，不是 kdraw 推下去的值

方向必须先定死，否则整表会被读反。`GrblSettings.readFromMachine(int n, float v)`
（findings/08 §「readFromMachine」）只做 `this.kv.put(n, v)`，触发时机是 kdraw 发 `$$`
**查询**后逐行解析 `$N=V` 应答（findings/05 的握手序列 `banner → $I → [VER: → $$ → ok → G92 Z0`）。
21 份 findings 全树**没有任何 `$N=V` 写回路径**，`toProperties()` 只用于持久化/导出。

> 因此 `kv` 是**从真机读回的 46 键快照**。推论方向是「真机 → kdraw」，
> 不是「kdraw → 真机」。**kdraw 不会覆盖本机行程设定**，这条风险不成立。

## 2 14/16 逐字相同：机器头的独立实机佐证

把 `kv` 与我方机器头 `Grbl_Esp32/src/Machines/custom_3axis_hr4988.h:314-351` 对账：

| `$` | 含义 | kdraw 快照 | 我方机器头 | 判定 |
|---|---|---|---|---|
| `$0` | step pulse µs | 3 | 3 | 同 |
| `$1` | idle lock ms | 25 | 25 | 同 |
| `$3` | dir invert mask | 7 | 7 | 同 |
| `$4` | inv st enable | 0 | 0 | 同 |
| `$100` | X steps/mm | 100 | 100 | 同 |
| `$101` | Y steps/mm | 100 | 100 | 同 |
| `$102` | Z steps/mm | 50 | 50 | 同 |
| `$110` | X max rate | 12000 | 12000 | 同 |
| `$111` | Y max rate | 12000 | 12000 | 同 |
| `$112` | **Z max rate** | **10000** | **10000** | **同（见 §3）** |
| `$120` | X accel | 3000 | 3000 | 同 |
| `$121` | Y accel | 3000 | 3000 | 同 |
| `$122` | Z accel | 8000 | 8000 | 同 |
| `$132` | Z travel | 200 | 200 | 同 |
| `$130` | X travel | **200** | **210** | **异** |
| `$131` | Y travel | **200** | **297** | **异** |

`same=14/16`。含义：我方 `custom_3axis_hr4988.h` 里的机电常数**不是估值**，
而与原配商业上位机从真机读回的快照逐字吻合。这是该机器头此前没有过的独立佐证。

两处异只在行程。`AGENTS.md` 记载本机 A4 行程是**运行时设的** `$130=210`、`$131=297`
（NVS 覆盖编译期缺省）。[INFERENCE] 该快照很可能取自 A4 行程调整**之前**，或取自同型另一台；
无时间戳可证，故不断言。**不得据此把我方行程改回 200**。

## 3 `F10000` 是 Z 轴速率上限，不是口味选择

`$112 = 10000` 与 `DEFAULT_Z_MAX_RATE 10000.0` 相同，而 kdraw 的 `Grbl.json` 里
`zSpeed = 10000`——即**上位机把 Z 速度直接顶到轴的速率上限**。

此前 findings/12 §五把「进给 F8000 / Z F10000」评为**中**（依据是「唯一缓存内容 + 本机 `zSpeed=10000`」）。
现在 Z 侧多了一条结构性锚点：`F10000` 恰是 `$112` 天花板，不是任取值。
`hutuji_gwrite.toml:22-25` 那句「保持奎享原值 F10000，不采用未经证明的 F1000/F300 降速方案」
因此有了机电依据。**注意**：这只加强 `F10000` 的**出处**，不构成纸面出墨证据——
该 toml 第 26-29 行记的「F8000 画线尚无干净纸面证据」仍然成立。

## 4 `settings.json` 现役值 ≠ `DrawQuality` 默认表

findings/13 §4 那张 `KATSettings.getFeedRate(DrawQuality)` 表（`VeryLow` → 画线 5000 / 空走 6000）
是**代码里的档位映射**。现役安装的 `settings.json` 是**显式持久化值**：

| 键 | 现役安装值 | findings/13 §5 记的默认 | 我方对应 |
|---|---|---|---|
| `feedRate` | **8000** | 1500 | toml 画线 `F8000` |
| `jogSpeed` | **8000** | 2500 | toml 空走 `F8000` |
| `toolOnDelay` | **0.0** | 0.2 | 无 `G4`（已去） |
| `toolOffDelay` | **0.0** | 0.2 | 无 `G4`（已去） |
| `nearDst` | **0.3** | 0.3 | `LINEMERGE_TOLERANCE_MM = 0.3` |
| `drawQuality` | `VeryLow` | `VeryLow` | 不对齐（我方优先画质） |
| `dpi` | 96.0 | — | 与 vpype 无关（我方按 mm） |

三条含义：

1. **`F8000` 有了第二个独立来源**。此前只有「唯一缓存产物」一条；现在配置侧
   `feedRate=8000`/`jogSpeed=8000` 与产物里的 `F8000` 互相印证。findings/13:101-103
   说「`F8000` 已高于内置 `VeryLow` 的 5000，不能宣称比奎享默认更保守」——该判断仍对，
   但要补一句：**这台机器上的奎享实跑值就是 8000**，不是 5000。
2. **`toolOn/OffDelay = 0.0`** 给「Stepper 不用 `G4`」补上配置侧证据（findings/04 §结论
   此前只有代码推理）。
3. **`nearDst = 0.3`** 是现役安装值，不只是反编译默认值——`LINEMERGE_TOLERANCE_MM = 0.3`
   的对齐依据从「默认值」升级为「现役值」。

## 5 负 X 包络的成因：原点在左上 + XY 互换

findings/17:63 记了「负 X 工件坐标系」这个**事实**，但没有成因。`AxesData.json`：

```json
{"revxy":true,"xAxeRev":false,"yAxeRev":false,"pos":"LeftUp","manget":false,"lock":false}
```

`pos = "LeftUp"`（原点左上）+ `revxy = true`（XY 互换）。我方原点是**左下角死点**、
`X+` 右 / `Y+` 前（`AGENTS.md`）。左上原点下纸面向下为正，经 XY 互换后落到机床 X 负向——
这解释了 `2.gcode` 为何 763 个 X 值里 762 个为负、且 X 包络仅 `[-8.126, 0]` 而 Y 达 `[0, 117.670]`
（8mm 宽 × 118mm 高的窄条 = 一列竖排手写字，与 `page_settings.json` 的 `contentDir: 3` 一致）。

**含义**：复用奎享产物当回归基线时，需要的是**原点变换**，不是随手平移。
`docs/kxnx/alignment/plan.md:45-46` 记的「去尾 + 平移 +20mm 才通过」仍然成立；
本轮复测 **+10mm 亦足够**（`lines=945 bytes=21729 pen_lifts=91`），两者不矛盾，+10 只是更紧。

## 6 用产物反推出 `simplePaths → linesimplify` 的映射

findings/13 §2 明说：`linesimplify`/`linemerge` 是服务端逻辑，客户端不可见，
「只能由产物反推」。本轮做了这个反推：

- `FontsSetData.json` 的 `simple = 0.02`（= findings/13 §1 的 `simplePaths=0.02mm`）。
- 产物 673 段画线的**最短段 = 0.0240mm**，p5 = 0.0510mm。

最短段 0.024mm 紧贴 0.02mm 容差下界——**容差即产物的段长地板**，映射成立。
这是 §2 那句「只能由产物反推」的一次实际兑现。

段长分布（673 段，本轮重算，与 findings/13:155 的中位 0.254mm 一致）：

| 统计量 | 值 |
|---|---|
| min | 0.0240mm |
| p5 | 0.0510mm |
| p50 | 0.2537mm |
| p95 | 1.3583mm |
| max | 3.3969mm |

**画质差量（可量化）**：我方 `LINESIMPLIFY_TOLERANCE_MM = 0.05`，是奎享 0.02 的 2.5 倍。
奎享产物里 **32/673 = 4.8%** 的段短于 0.05mm，这批细节在我方管线里会被简化掉。
这是与商业基线的**已知画质差量**，不是缺陷：0.05 是显式定案且被
`test_contract.py:1174` 钉死，findings/13 §6 记的前置条件（窗口化流控）未做之前不改。

## 7 笔画结构：抬笔数与「碎笔」的判别尺度

按 `Z5.0`（落笔）/`Z0.0`（抬笔）切分产物：

| 统计量 | 值 |
|---|---|
| 笔画数 | 89 |
| 画线段总数 | 673 |
| 段/笔画 均值 | **7.56** |
| 段/笔画 min / p50 / max | 3 / 7 / 17 |
| 单段笔画数 | **0** |

`pen_lifts=91`（= 89 次落笔对应的抬笔 + 文件头两次幂等抬笔）超
`PEN_LIFT_WARN_THRESHOLD = 50` 近一倍这件事，`alignment/plan.md:53-54` 已记为待定项。
本文补的是**判别尺度**：这 89 个笔画**没有一个是单段**，均值 7.56 段/笔画，
即高抬笔数来自「89 个各有 3–17 段的正常笔画」，**不是**碎笔病态。

含义：抬笔次数的绝对值无法区分「复杂手写」与「碎笔病态」，段/笔画均值可以
（病态趋近 1.0，本产物 7.56）。**本文不改该阈值**——`gcode_gen.py` 记明硬阈值须待机头实测
（README 组 1.3），且仅一个真实产物不足以标定新阈值或新指标。此处只留下标定用的实测基线。

## 8 `JSSParams.json` 佐证 DTR/RTS 复位坑

```json
{"dataBits":8,"stopBits":1,"parity":0,"setRTS":true,"setDTR":true,"reset":false}
```

kdraw 开串口时 **DTR/RTS 均置 true**，与 `docs/troubleshooting.md` 记的
「`DtrEnable/RtsEnable=$true` 后 `Close()` 的跳变会复位 Grbl」不冲突：
kdraw 整个任务期间**不关串口**，因此从不触发关闭时的跳变。
这与我方结论一致——危险在 `Close()`，不在 `setDTR` 本身。

## 9 校验器对第三方真产物的行为（实测）

把 `2.gcode` 原样喂 `validate_gcode()`，逐层剥离以枚举独立违规：

| 输入 | 结果 |
|---|---|
| 原样 | 拒：第 946 行（文件末尾）必须是 `G1G90 Z0.0F10000`（实际 `G90G0 X0Y0`） |
| 去收尾 | 拒：第 6 行 `X-2.205` 越界（限幅 0~190.0mm） |
| 去尾 + X 平移 +10mm | **通过**，`lines=945 pen_lifts=91`，附软告警「抬笔 91 次，超过软阈值 50；本次仍放行」 |

产物最长行 **23 字节**，远低于 `MAX_LINE_PAYLOAD_BYTES` 的 254；
全文 Z 字面量只有 `0.0`/`5.0`，F 字面量只有 `8000`/`10000`——与我方 §5 契约允许集一致。
两条拒绝都是**设计意图**（收尾回原点在本机判为换纸触发；负坐标在无限位机器上会撞机），
不得为「对齐奎享」放宽。

## 10 奎享自身的一处不安全默认

`page_settings.json` 默认幅面 `pageWidth=200.0 / pageHeight=290.0`，
而同一安装的 `$131`（Y 行程）快照是 **200**——**幅面比行程长 90mm**。
`kv` 里 `$20=0`（软限位关）、`$21=0`（硬限位关）、`$22=0`（无归零），
所以 Grbl **不会报错**，只会一路顶进机架。

两条含义：

1. **原配商业上位机的机器同样是「三关」配置**。我方 `$20/$21/$22` 全 0
   （`Grbl_Esp32/src/Defaults.h:97-106` 缺省 + 本机无限位引脚，`custom_3axis_hr4988.h:307-308`
   三个 `*_LIMIT_PIN` 全注释）不是配置疏漏，而与原配一致。
2. **正因三关，`$130/$131` 在软限位关闭时不参与任何门禁**——它们是纯信息字段。
   这从反面证实了 `docs/agent-anti-drift.md` 与 `findings/18` §3 的立场：
   **MCP 端硬限幅是唯一安全防线**，奎享是软警告缩放，我们是硬拒。

## 11 与本文相关的既有文档修正

本轮顺带发现的过时行（已在对应文档改掉，此处留档以便追溯）：

| 位置 | 原文 | 实际 |
|---|---|---|
| `findings/13` §1 表 | 我方 `linesimplify` = 0.1mm | **0.05**（`gcode_gen.py`，`test_contract.py:1174` 钉死） |
| `findings/13` §1 表 | 我方 `linemerge` = 0.05mm | **0.3**（§3 早已 supersede，但表格行未跟改） |
| `findings/13` §6 表 | 「已做：`linesimplify` 0.2 → 0.1」 | 现值 **0.05** |
| `findings/04` §对照表 | `feed_rate 1500` → toml `F1500`；`jog_speed 2500` → `F2500` | toml 画线/空走均 **F8000** |
| `findings/04` §对照表 | `zOn/zOff` 待定、`zSpeed` 待加 | 已定案 **5.0 / 0.0 / F10000** |
| `findings/04` §结论 | 「只需把 `zSpeed=3000` 注入」 | 现役值与我方均 **10000** |

相关：[[04-config-keys]]（配置键）、[[13-path-params-and-quality]]（画质档位）、
[[17-reconnect-resend-hazard]]（负 X 坐标系）、[[12-summary-and-action-list]]（证据强度评级）、
[[../alignment/plan.md]]（对齐计划与不对齐清单）。
