# hutuji 板间协议与 G-code 契约 v0.4

本文是 ②（S3 固件补丁）与 ③（MCP 服务）的共同契约。改动需两侧同步评审。

版本历史：
- v0.2：按两仓代码勘察修订（Grbl_Esp32 `c19a382`、xiaozhi-esp32 `38bc4f4`）。
- v0.3：物理层改 CH9350 桥接——**已否决**：CH9350 仅支持 HID 类（键盘/鼠标/扫码枪），不枚举 CH340（[WCH 官方手册](https://www.wch.cn/products/CH9350.html)）。
- v0.4：物理层改 **WiFi Telnet**（方案 E）。依据：Grbl_Esp32 内置 ESP3D WebUI 开放 Telnet:23，社区成熟模式；本机商业上位机已用 BT SPP 无线流式出图（`PAPER_AUTO_CHANGE_ON_BT_CONNECT`），无线流控经量产验证。USB Host 方案不可行的结论保留（实战派 C 口被 CH334F HUB 占为设备口）。

## 1. 物理/链路层（方案 E：WiFi Telnet）

```
实战派 ESP32-S3 ──WiFi STA──► 家用路由器 ◄──WiFi STA── 写字机 Grbl_Esp32
        （S3 必须经路由器上云）              （Telnet server，端口 23）
```

| 项 | 约定 |
|----|------|
| 链路 | S3 TCP 客户端 → 写字机 **Telnet :23**（Grbl_Esp32 WebUI 原生，Config.h 已启用 WiFi） |
| 寻址 | 写字机**固定 IP**：路由器静态 DHCP 租约（首选）或 `$Sta/IPMode=Static` + `$Sta/IP` |
| 参数 | 链路内即原串口字节流：115200 语义不变，行协议逐行 `ok`/`error:NN` |
| 流向 | S3 → Grbl：G-code 行；Grbl → S3：`ok` / `error:NN` / `[MSG:]` / 状态报告 |
| 重连 | TCP 断开 = 链路丢失：S3 指数退避自动重连（1s/2s/4s…上限 30s），重连成功后按 §2 重新探活；进行中的任务置 `error`（不做断点续传，v0 从头发） |

**重要认知**：串口日志仍是最全诊断口，但 Telnet 通道同样承载 `ok`/`error:`/`[MSG:]` 应答（`grbl_msg_sendf` 按 client 路由，`CLIENT_ALL` 消息 Telnet 也收得到）。S3 解析器按 §2 容忍全部输出类型。

**备用链路**（WiFi 实测抖动不可接受时回退）：方案 B 拆机 TTL——写字机内 ESP32 GPIO1/3（CH340 输出侧）飞线 → 实战派多功能口 UART（IO10/IO11），3.3V、TX/RX 交叉 + GND。M1 代码的 UART 传输层实现已留档可切换。

### 1.1 写字机 WiFi 开通与验证（②开工第一步）

1. PC 串口工具接写字机 CH340（115200），依次下发：
   - `$Sta/SSID=<家里WiFi>` / `$Sta/Password=<密码>`（写入即存 NVS）
   - 路由器后台给写字机 MAC 绑定静态租约（或 `$Sta/IPMode=Static`、`$Sta/IP=<addr>`）
   - `$Telnet/Enable=1`、`$Telnet/Port=23`（确认默认开启）
   - `$I` 或 `[ESP110]` 查当前 IP 与状态
2. PC 上 `telnet <写字机IP> 23`，发 `?` 应回 `<Idle|…>`，发 `$I` 应回 `[VER:…]`——链路通。
3. S3 侧联调时 `hutuji.status` 应显示 connected/ready。

## 2. 就绪检测与会话重建

- **就绪判据（Telnet 版）**：TCP 连接建立后 S3 **主动探活**——发 `$I`，收到 `[VER:…]`/`[OPT:…]` 即视为就绪；banner 文本（`Grbl 1.3a ['$' for help]`）在对端复位重放时也会出现，收到时按"对端复位"处理（重置状态机、当前任务置 error）。
- **授权前置不变**：探活后检查 `[License] Authorized`（banner 或 `$I` 上下文），未授权见 §6。
- **解析器必须容忍**（异步混入字节流，均非应答）：`[MSG:…]`（含 `[MSG:[PaperAuto-N] …]`、`[MSG:[PaperStatus] N]` 双层括号）、`?` 触发的 `<Idle|MPos:…|FS:…>`、`ALARM:n`、`[BT-EOL …]` 等裸 `[…]` 行。

## 3. 流控（Grbl 行协议）

- **逐行发送，收到 `ok\r\n` 才发下一行**；`error:NN\r\n`（数字格式）按 §7 处理。**无 character-counting**，禁止预发抢跑（client_buffer 溢出会丢字节）。
- **单行 ≤ 255 字符**（`LINE_BUFFER_SIZE 256`），超长回 `error:11`。MCP 生成端折行负责。
- **换纸阻塞期**（数十秒）：该行的 `ok`/`error:90` 在**整个换纸完成后**才返回——这类行（M30/M721/[ESP910]）等 ok 超时 ≥ 60s，期间用 `[MSG:[PaperAuto-N]…]` 进度行做 UI 反馈；且此期间**禁发任何 G0–G3**（会被吞并回 `error:8`，见 §7）。
- 实时命令（`!` 暂停、`~` 恢复、`?` 状态、`0x18` 软复位）单字节立即生效，Telnet 流中同样可混发；`?` 的应答只回给发送 client。
- **`0x18` 特例**：换纸开始后 8s 窗口内被忽略；窗口后才中止换纸。`abort` 实现须注意（§4）。
- 空行、注释（`;` 与 `()`）由 S3 转发前剔除。

## 4. 任务协议（S3 设备侧 MCP 工具）

**并发约束（勘察结论）**：xiaozhi 的 MCP 工具回调在**主任务同步执行**——禁止在回调里跑长任务。全部工具**异步化**：

| 工具 | 参数 | 行为 |
|------|------|------|
| `hutuji.draw` | `url` | 回调仅校验参数+幂等检查 → 立即返回 `"started"`；独立任务执行：HTTP 流式下载（`Board::GetNetwork()->CreateHttp()` + `Read()` 循环，模板见 `ota.cc:299-367`）→ 校验（§5）→ 按行切分 → Telnet 写 → 等 ok |
| `hutuji.status` | — | 同步快速执行：返回 TCP 连接/就绪状态；已连接时透传 `?` + `[ESP901]`（纸/电机/PanelHold）+ `M704`（GPIO34 纸传感器）汇总 |
| `hutuji.abort` | — | 发 `!` 暂停 → 延迟 ≥8s 后发 `0x18`（避开换纸忽略窗）；终止下载转发任务 |

- 任务完成/失败经 `Application::SendMcpMessage` **设备侧主动推送**；同步查询走 `hutuji.status`。
- 状态机：`idle → downloading → streaming → (paper_change) → done | error`；`abort → idle`；TCP 断开/banner 重放 → `error`。
- 同一时刻只允许一个任务。
- 代码挂点：`lichuang-dev` 板级 `InitializeTools()`；**板级行为不进核心模块**。

## 5. G-code 文件契约（MCP 服务产出）

- **自包含**：文件头 `G21`（毫米）、`G90`（绝对）、`F` 进给。
- **避开隐式换纸触发点**：
  - 软复位后**第一条** `G0 X0 Y0 Z0`（首页触发）
  - `G28` / `G30`，以及执行后工件坐标 XY≈0 的 `G0`（回原点触发，含 `G0 X0 Y0` 收尾！）
  - **结论：分页节奏必须显式控制**——页尾用 `M30`（自动走完整换纸流程），队首可用 `M721`；收尾**不要**用 `G0 X0 Y0`，抬笔后停当前位置即可。
- **抬/落笔**：机头用 `USE_M3_M5_AS_PEN_UP_DOWN`（M3=落笔 / M5=抬笔，Z 步进非伺服）。现役样例 `两页示例_插入换页.gcode` 用 `Z30` 落 / `Z0` 抬 + `G4 P0.2` 稳定，机头默认 `PEN_UP/DOWN_Z_MM=0/5`——**以 ② 实测锁定**（先查 `$102` Z steps/mm）。
- **坐标范围**：`$130/$131 = 200/200mm`（`custom_3axis_hr4988.h`），**软限位 `$20=0` 关闭**——越界不报错只撞机，MCP 生成端必须自行限幅（建议 ≤190×190 留边距）。
- **换纸失败语义**：`error:90`；`[MSG:[PaperStatus] N]`：0=成功，2=进纸超时，3=回找未定位，4=卡纸，5=缺纸。N≠0 → 暂停任务 + 云端提示用户，不自动重试。
- **大小上限**：单文件 ≤ 512KB（边下边转，②实测修订）。

## 6. 授权前置

- 未授权芯片上电后 **G0–G3 全部回 `error:110`**。探活后必须确认 `[License] Authorized`。
- 未授权 → 走人工 SOP（按 ChipID 算授权码，`M800 P<码>` 写 NVS），**S3 不自动授权**。
- 烧录不覆盖 NVS，授权与 `$` 设置持久；改机头默认需 `$RST=$`（联调 SOP 记录，非 S3 职责）。

## 7. 错误处理

| 情况 | 含义 | S3 行为 |
|------|------|---------|
| `error:8` | 换纸进行中，运动行被推迟 | **不算失败**：暂停发送，等换纸行 ok / `[PaperStatus]` 后继续 |
| `error:11` | 行超 255 字符 | 任务 error，上报（MCP 生成端 bug） |
| `error:90` | 换纸失败 | 任务 error，按 `[PaperStatus]` 码提示用户 |
| `error:110` | 未授权 | 停止全部发送，提示走授权 SOP |
| 其他 `error:NN` | 语法/参数错 | 停止，记录行号，任务 error 上报 |
| `ALARM:n` | 报警 | 停止；`$X`/`$RST` 解锁走人工 SOP，不自动解锁 |
| banner 重放 | 对端复位（欠压等） | 状态机复位，任务 error，上报 |
| TCP 断开 | 链路丢失/对端断电 | 指数退避重连；进行中任务置 error |
| 下载失败/超时 | — | 不发任何字节，任务 error |
| 60s 无 `ok`（换纸行 90s） | 链路异常 | 发 `?` 探活一次，仍无则任务 error |

## 8. 明确不做

- 不用蓝牙走正式数据链路；WiFi 只走 Telnet 这一条（Grbl WebUI 其余功能不依赖）。
- S3 不做 G-code 生成/编辑/预览，只做校验与转发（哑管道原则）。
- 不改动 Grbl 侧任何代码（现役商业固件；其 Telnet/换纸/授权行为已满足全部对接需求）。
- S3 不自动授权、不自动 `$X` 解锁、不在换纸 8s 窗口内发 `0x18`、不做断点续传。
