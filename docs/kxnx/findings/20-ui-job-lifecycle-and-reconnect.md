# 20 · UI → JobManager → Executor 生命周期与串口重连闭环

> 结论先行：奎享主 UI 把普通 stop 与 reset2 分开；断线时当前文件 `drawing → wait`，旧 executor 的 UI 订阅被卸载。它的自动重连**仅针对串口 transport**，且用“重连后状态坐标非零”作为可续画判据，再发布 `FirmwareEventReconnected`；窗口引擎随后只把最后一条在途命令挪回 pending 尾部。这条续画链同时存在状态判据过弱、丢更早在途行、行序后移三类正确性缺陷，我方不要对齐。
>
> 主源码基准：`D:/GitHub/kxnx/kx/tools/dump_agent/decompiled_java/`；重连对话框 `machine/ui/e/k.java` 取自同版本的 `decompiled_full/` 补全树。全部为读码，未在奎享实测。

## 1 控制器构造时决定执行器

`machine/c.java` 字段 `g` 是 transport，`j` 是 JobManager `machine/f/l`。基类默认构造（`:92-94`）：

```java
this.j = new l(this, new machine.f.f(false, this));
```

换 transport 时，旧 JobManager 先 `a(true)` stop、`c()` 销毁，再保存新 transport 并重建（`:74-90`）。

Grbl 派生控制器 `machine/c/a.java:24-28` 与 Marlin `machine/marlin/a.java:36-40` 再分流：

| transport | Executor | 行为 |
|-----------|----------|------|
| `machine/b/d`（网络/TCP） | `machine/f/g` | 上传 `kdraw.gco` / `KDRAW.GCO` 到设备后由固件执行，进度读设备百分比 |
| 其他（含串口 `machine/b/f`） | `machine/f/f(false, controller)` | PC 行流 + `f/b` character-counting 窗口 |

因此“奎享 TCP 也用 100B 行流窗口”是错误概括：该派生控制器的 TCP 路径走 SD/ESP 文件执行器 `g`，普通串口才走 `f`。hutuji 的 WiFi Telnet 是行流，架构与它的 TCP 路径不同。

## 2 主 UI 字段与事件订阅

`machine/ui/a.java:68-100,129-250`：

| 字段 | UI |
|------|----|
| `i` | 开始出图按钮 |
| `j` | 暂停/继续 |
| `k` | 普通 stop |
| `l` | 当前任务进度条（0–10000） |
| `m` | 总进度条（多任务/KAT） |
| `n` / `o` | ETA / 已用时间 |
| `x` | 独立 reset2 |

controller 通过全局 `machine.f.a().e()` 取；JobManager = `controller.r()`；Executor = `controller.r().b()`。

构造期订阅 controller 的 Connected/Disconnected/Status/Error/esp_print_finish（`:258-265`）。Executor 的 Progress/Complete 与 JobManager 的任务事件不是永久订阅：Connected 时挂载（`:650-658`），Disconnected 时卸载（`:637-645`），防止旧执行器继续更新 UI。

## 3 开始出图调用链

```
UI write 按钮 (ui/a.java:459-464)
  → UI c() 做连接/状态检查
  → JobManager 清队列、加入导出文件、b(0)
  → l.b(0): c=-1, f=true；可选先 autoSetZero
  → l.l(): wait→drawing；读文件每行到 k；绑定 Executor；a() 启动
  → f.a(): e=true；n() 预填窗口
  → f/b: pending → in-flight → transport
```

关键位置：`machine/ui/a.java:307-355`、`machine/f/l.java:96-124,161-182`、`machine/f/f.java:39-47,104-128`。

文件读失败只把当前项标 `error` 后继续推进（`f/l.java:183-187`），不是全批次失败。

## 4 pause、stop、reset2 三条路径

### pause / resume

主 UI 直接调当前 Executor `b()`，**JobManager 不参与**（`ui/a.java:465-475`）。流式 `f.b()` 在 `e()` 暂停与 `d()` 恢复之间切换：停灌并把抬笔排到 pending 尾，或恢复并排落笔（`f/f.java:56-70,165-171`）。

### 普通 stop

`k` → UI `e()` → `controller.r().a(true)`（`ui/a.java:415-429,477-479`）：

1. JobManager 调 Executor `b(true)`。
2. 流式 Executor 队尾追加抬笔 + 回原点/机型收尾，不清 pending/in-flight。
3. JobManager 清运行标志，当前 `drawing → wait`，发布 stop。

位置：`f/l.java:138-150`、`f/f.java:74-85`。这是非实时 stop。

### reset2

`x` → `controller.c(true)`（`ui/a.java:507-508`）：Grbl 清 `s`、发 `0x18`、调用 transport `f()` reset hook（`grbl/a.java:365-377`）。串口 hook 是 DTR/RTS 脉冲（`machine/b/f.java:98-113`），不是统一 close；reset2 也不先抬笔。

## 5 断线时任务状态回退

`FirmwareEventDisconnected` 分支（`ui/a.java:633-647`）：

1. 记录断线前 `JobManager.i()` 是否正在运行。
2. 调 `l.a(false)`：Executor stop **不追加收尾**，JobManager `f=false`，当前 `drawing → wait`。
3. 卸载旧 Executor / JobManager 的 UI 订阅。
4. 只有同时满足下列条件才弹自动重连框：
   - 非 K_AT
   - 断线前任务活跃
   - transport 是串口 `machine/b/f`
   - 不是主动断开
   - `Settings.autoReconnect=true`

所以 TCP/WiFi `machine/b/d` **不走这套自动重连 UI**。

## 6 重连不是 transport 自动恢复，而是 UI 对话框驱动

`machine/ui/e/k.java`（补全树）：

1. 对话框延迟 1s，订阅 FirmwareEventStatus（`:43-54`）。
2. 调 controller `C()` 复用 endpoint 建连；失败即关框（`:54-56`；controller 入口见 `machine/c.java:211-219`）。
3. 收第一帧状态后立即关框（`:67-70`）。
4. 若 XY **任一非零**，发布 `FirmwareEventReconnected`（`:71-73`）；若恰为 `(0,0)`，报 `redraw_failed`（`:75`）。
5. 流式 Executor 收 Reconnected 后 `e=true`，窗口引擎 `d()` 把 `in-flight.pollLast()` 取出的最后一行通过 `a()` 加到 **pending 尾部**（`f/f.java:152-155`、`f/b.java:83-88`）。

### 三个确定性缺陷

1. **恢复判据过弱**：XY 非零不证明 planner、模态、笔状态、坐标系与更早在途行已恢复。
2. **多行丢失**：窗口可能有多条 in-flight，只救最后一条；更早行静默丢失。
3. **行序后移**：救回行进入 pending 尾部，排在原先后续行之后。

另一个边界：合法任务若断在 `(0,0)`，会被拒绝续画；这只是启发式，不是协议状态。

## 7 FileStatus 与多文件/重复轮次

`machine/f/m.java` 新项默认 `wait`。`machine/f/l.java` 状态迁移：

```
wait → drawing → finished
               → error          （读文件 IOException）
               → uploadFailed   （SD 上传 UI 回调 null）
stop/disconnect: drawing → wait
```

证据：`f/l.java:126-150,161-187,238-250,278-285`。

重复轮次：`a(int)` 同时设置总轮次 `i` 与剩余 `h`（`:83-94`）；到队尾 `--h`，仍大于 0 时 `c=-1` 后从首文件重跑，归零则发布全批次完成（`:191-201`）。文件间：K_MT 发 `M1100`，AWAuto 调纸控，其余机型显示换纸对话框（`:202-219`）；K_MT 单文件完成发 `M1103`（`:238-249`）。

`uploadFailed` 只属于 SD Executor：`l` 注册为 `g` 上传回调，上传 UI 返回 null 才调用 `l.a()`（`f/l.java:58-62,278-285`、`f/g.java:36-50`），不是通用 transport 错误。

## 8 对 hutuji 的取舍

| 项 | 决定 | 理由 |
|----|------|------|
| stop/reset 分层 | 保持我方现状 | 奎享证明分层有价值，但其 stop 非实时、reset 无 Hold，安全性更弱 |
| 断线先撤销旧订阅/旧任务 | 可借鉴概念 | 防止失效对象继续上报；我方需落到 task generation/session id，不照搬 Swing 事件 |
| `drawing → wait` 后无条件续画 | 不对齐 | 我方有 PSRAM checkpoint、fresh Idle 与任务状态，应 fail-closed 判断 |
| XY 非零判可恢复 | 禁止 | 不是协议证据 |
| `pollLast()` 单行救回 | 禁止 | 已证实丢行与错序 |
| TCP 走设备 SD 文件 | 不对齐 | 哑管道架构是 S3 下载后 Telnet 转发，且本机无该 SD 协议 |

## 9 未验证与 CFR 边界

- `machine/ui/a.java:588-723` 被 CFR 标记 `Unable to fully structure code`；事件分支的核心调用可交叉确认，但临时变量/GOTO 不能视作原始 Java 控制结构。
- 串口断线续画三类缺陷均为读码结论，未在奎享实机注入断线。
- `machine/ui/e/k.java` 来自同版本 `decompiled_full/`；主树缺该类的可读实现，但主 UI 调用点与事件名一致。
