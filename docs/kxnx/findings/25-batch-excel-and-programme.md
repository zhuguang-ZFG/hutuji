# 25 · 批量出图 + programme + 本地文件直发：两条绕过生成链的「文件即指令」通道

> 结论先行：本轮确认一个**新事实**——奎享除云端生成链外，存在**本地 `.gcode` 文件逐行直发固件**的旁路（仅 trim+跳空行，无白名单/行长/内容校验），以及 **`.pg` 程序文件 = 任意机器命令载体**（JSON 反序列化无校验直拼 G-code，负数循环不死）。批量 Excel 面另有空作业误派发、静默丢行、AIOOBE 停摆、POI 无大小防护四类编排缺陷。**对 hutuji：印证「不开放本地 G-code 导入」现状，零动作。**
>
> **行号基准树**：`D:/Users/kxnx/kx/tools/dump_agent/decompiled_full/`（A 混淆体系，920 文件）。
>
> 日期：2026-08-14（第十八轮）。方法：子代理深审 + 主代理复核高载证据。【已复核】= 主代理亲读确认。
>
> 与 findings/23 的关系：23-H1「生成在云端」结论**仅覆盖内容生成路径**；本文的本地文件直发是独立**输入**路径，不推翻 23，但补全了「到达机器的字节还可以来自用户文件」这一事实。

---

## 1. 批量出图面地图

```
f/a/d.java 扫码会话（15s Timer 轮询固件取条码）
  → 条码精确匹配 Excel 矩阵 → MapItem 行绑定（主列索引无边界检查）
  → getExportGcodeGenerator 云端生成（h/c.java，GZIP + uin/token header）
  → H 写临时 .gcode → DrawFileItem 上传机器文件表 → machine/f/l 逐行读文件直发
旁路 A：machine/ui/g.java h() JFileChooser 选 .gcode → 入队 → 同一逐行直发（无校验）
旁路 B：programme .pg 程序文件 → JSONArray 逐动作反序列化 → programme/d 执行器直写命令通道
```

## 2. HIGH

### H1. 本地 `.gcode` 文件直接下发链路：逐行直发固件，零内容校验【已复核】

- 证据：`machine/f/l.java:168-174`（`BufferedReader` 逐行读本地文件，仅 `trim()` + 跳空行即 `arrayList.add`，整批经窗口引擎发送——**无指令白名单、无行长限制、无内容解析、无长度上限**）；`machine/ui/g.java:298-305`（JFileChooser 仅扩展名过滤后 `this.a(File)` 入队）；`KenjoyDraw.java:1088`（测试页同样直发用户自选文件）。
- 细节：恶意/畸形文件内容原样到达固件——可含 `M30`（触发换纸）、`G92`（改坐标基准）、超行程坐标、超长行（真机 254 字节 payload 上限外被静默吃行，见 findings/09）。与云端生成链完全独立。
- **hutuji 对照**：hutuji 无本地 G-code 导入面（S3 只从云端 URL 下载 + CRC32 + 校验后转发），本条印证该设计。若未来开放导入，必须过 `validate_gcode()` 同级闸门。

### H2. `.pg` 程序文件 = 任意机器命令载体【已复核】

- 证据：`programme/i.java:201-209`（选 `.pg` → JSONArray 逐动作 `read(JSONObject)`）；`actions/machine/GcodeAction.java:89`（`gcode` 字段原样逐行执行【已复核】）；`actions/machine/MoveAction.java:115`（JSON 字符串 `setText` 进坐标框，执行时直拼 `G1X<txt>...`——可注入任意片段）；`actions/control/LoopAction.java:80`（`count` 无下限，负数 → 死循环持续发指令）；`programme/d.java:36`（执行器 `this.c.a(cmd, true)` 直写命令通道）。
- 细节：无 schema 校验、无值域、无白名单、执行前无机器连接/忙/纸张状态门。
- **hutuji 对照**：hutuji 无宏/程序文件概念。若做脚本类功能：schema 校验 + 数值边界 + 指令白名单 + 状态机门，四件套缺一不可。

## 3. MEDIUM

| # | 发现 | 证据 | 要点 |
|---|------|------|------|
| M1 | programme 执行器：固件 error 事件即清双队列**静默终止**，无恢复/重试/失败行记录 | `programme/d.java:36`；`MachineAction.java:92` | 传输层错误直接杀高层程序且无日志面 |
| M2 | 批量失败语义三连缺：条码 match 失败**仍派发空表头作业**（`G21\nG90\n`+进给率）；生成异常码（非 0/2）静默丢行无日志无重试；need login 中断后不续跑 | `f/a/d.java:181`、`:97`、`:235`、`:249` | 「skip」实际打印一页空白；中断行永远丢失 |
| M3 | MapItem 主列索引无边界检查：列号 ≥ 表宽 → AIOOBE 未捕获 → 扫码处理线程静默崩溃（Timer 仍轮询） | `f/a/d.java:195-196`（对比 plus 表达式分支有 `n9 < b.f` 检查，主列独漏） | 配置引用外部数据维度的索引必须边界断言 + 用户可见错误 |
| M4 | Excel 解析无大小防护：POI 全量内存加载，1M 行/列上限形同虚设（二维数组平方膨胀/zip bomb → OOM） | `excel/c/c.java:40`；`excel/c/b.java:129,142` | 解析上传文件须 size cap + 解压比限制 |
| M5 | CSV 静默失败：非 xls/xlsx 扩展名落入 docx 分支，异常仅 `printStackTrace`，无 UI 报错 | `excel/c/c.java:78`；`excel/c/b.java:615` | 不支持格式必须显式失败 + 支持清单 |
| M6 | 单元格内容无长度/字符过滤直入文本项与云端 payload（GZIP≠加密，走明文 HTTP） | `f/a/d.java:196`；`h/c.java:155,184` | 出网 payload 字段级约束（hutuji description ≤200 字符先例） |

## 4. INFO

| # | 发现 | 证据 |
|---|------|------|
| I1 | XXE 无显式加固但依赖 POI ≥4.0 默认防护（CellType 枚举佐证版本），不算可利用面；公式求值无超时 | `excel/m.java:217`、`excel/c/c.java:44` |
| I2 | 批量临时文件：deleteOnExit 管理到位，但进程内自增计数器命名跨会话重名覆盖、非线程安全；扫码日志单日文件无限增长 | `t.java:33`、`H.java:97` |

## 5. 分诊

全部 10 条为**厂商侧**缺陷/观察。hutuji 无本地文件导入、无宏程序、无 Excel 解析、无批量扫码——对应面均不存在，**零改动、零新增固件观察项**。若未来开放「本地 G-code 导入」或「宏」，本文 H1/H2 的四件套（白名单/行长/值域/状态机门）是准入条件。

## 6. 局限

- 「本地直发」的窗口引擎下游行为（错误恢复、完成判定）与云端链共用 `machine/f/*`，前轮已审，不重复。
- POI 具体版本号反编译不可见，以 API 特征（CellType 枚举）推下限 ≥4.0。
