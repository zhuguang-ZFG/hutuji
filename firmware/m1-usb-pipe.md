# M1 最小管道固件补丁（feat/hutuji-usb-pipe，方案 E：WiFi Telnet）

> 补丁位置：`D:/Users/xiaozhi-esp32`，分支 `feat/hutuji-usb-pipe`，当前源码 HEAD
> `f1cf066`（链 `3ca9d31` → `c1267ec` → `ed5b132` → `15f73c9` → `13057e8` → `f1cf066`；末位是
> 默认关闭的 §8.5 abort 取证脚手架）。**实机烧录基线（07-31 更新）= HEAD `06b962c` + 未提交第二轮审查修复**（COM6 镜像 `0x2cec90`，证据 `results/s3-06b962c-dirty-com6-flash-evidence-20260731.md`）；历史基线 `13057e8`/`0x2ce890` 勿当现状。
> **2026-08-06 最新实机证据（supersede 上述旧“实机烧录基线”措辞）**：源码 `16f2f83` 的默认镜像已于 COM6 `app-flash`，`xiaozhi.bin=0x2cfdb0`，SHA-256 `AE634CD7FE7A6CB45A1F744799C6FB94C693C0FD83DE0E49DD8FC07EC024C132`，esptool 报 `Hash of data verified`。启动确认 ESP32-S3 rev0.2、8MB PSRAM、SKU `lichuang-dev`、七工具、WiFi `192.168.1.12`、MQTT 激活，无 panic/assert。写字机恢复上电后，S3 命中缓存 `192.168.1.13` 并连接 Telnet，收到 `[VER:1.3a.20211103:]`，`M5`→`ok`、fresh `Idle`，再发更新后的 `G1 G53 X0.000 F1500`→`ok` 并记录授权探测通过。未验证下载/CRC、verbose error、暂停恢复、换纸或出图。

> 下文历史段落里的「当前」按各自 commit 读，勿当现状。
> 8.3 自动触发镜像已构建并烧录 COM6；普通画线断连后已实测到废纸 `M30`、PSRAM 从第 1 行重画、
> 55/55 行完成与 fresh `Idle`。历史 8.3 最终页尾自动换纸曾因无纸返回 `error:90`，**仍不得表述为有纸换纸、
> App 语音或发布验收通过。**
> 目标板型：`lichuang-dev`（立创实战派 ESP32-S3）。
> **2026-07-24 传输层第三版**：USB Host（硬件被 CH334F 占用否决）→ UART/CH9350（否决）
> → **方案 E：写字机 Grbl_Esp32 内置 WebUI 的 Telnet 服务（端口 23），S3 做 TCP 客户端直连**。
> 链路内字节流与串口完全同构：行协议、`ok`/`error:NN`、banner、实时字符一致。

## 硬件链路（方案 E）

写字机：Grbl_Esp32 开 Telnet（端口 23），STA 连家用路由器拿**固定 IP**。
实战派 S3：WiFi 由 xiaozhi WifiBoard 正常管理（连同一路由器），Pipe 只做 TCP 客户端。
Pipe 先尝试 NVS 缓存 IP，再扫描当前 `/24` 子网的 Telnet:23；缓存命中和扫描候选都必须
先读到 Grbl banner 才接受并写缓存。当前实机写字机为 `192.168.1.13:23`。

## 改动清单

| 文件 | 说明 |
|------|------|
| `main/idf_component.yml` | 无 hutuji 相关依赖（lwip socket 走 IDF 内置） |
| `main/CMakeLists.txt` | `27eff73` 新增 `HUTUJI_AUTO_TEST_DRAW` / `HUTUJI_AUTO_TEST_DRAW_URL` CMake 调试构建选项；B-15 再加 `HUTUJI_AUTO_TEST_ABORT_ON_PAPER`（bringup §8.5 取证脚手架）。三者默认 OFF，且仅允许 `lichuang-dev` |
| `main/boards/lichuang-dev/hutuji_pipe.h/.cc` | `hutuji::Pipe` 单例：lwip socket 轻量同步 TCP 客户端 |
| `main/boards/lichuang-dev/lichuang_dev_board.cc` | `InitializeTools()` 注册 Pipe 与设备侧 MCP 控制工具；末尾两段默认关闭的取证任务：`HUTUJI_AUTO_TEST_DRAW`（自动起画）与 `HUTUJI_AUTO_TEST_ABORT_ON_PAPER`（50ms 轮询 `Job::IsPaperActive()`，换纸保护窗一开即本地调 `RequestAbort()` 并打印前后 `status`） |
| `main/boards/lichuang-dev/hutuji_job.h/.cc` | M2 Job：下载/CRC/逐行转发，以及暂停、恢复、重画、试笔控制；`3ca9d31` 将 `M3/M5` 笔控行按 planner-sync 行等待，避免页尾 `M5` 在剩余绘图时间较长时被普通 60s `ok` 超时误杀 |

未改运行期核心模块（`mcp_server.cc`、协议层、音频链路等未动）。`main/CMakeLists.txt` 仅承载默认关闭的调试编译定义，常规构建不启用自动测试。取证镜像用完**必须回刷不带宏的镜像**。

## hutuji::Pipe API（对外语义不变）

- `Start()`：幂等。只创建 `hutuji_tcp` 连接管理任务；**不管 WiFi**（WifiBoard 负责），WiFi 未就绪时 connect 自然失败走退避。
- 连接循环：`socket/connect` → banner 验明 Grbl peer → `connected_=true` → 发 `$I`，依次完成
  版本、抬笔、fresh 位置与授权零位移探测后才置 `ready_` → `recv` 读/分行/解析。断线后清
  connected/ready/authorized 与应答队列，关闭旧 socket，按 1s/2s/4s…上限 30s 重连。
- **TCP keepalive（v0.5 修正的实际 bug）**：原代码只设 `SO_KEEPALIVE=1`，注释写「尽快发现对端断电」——**实际是假的**：lwip 默认 `TCP_KEEPIDLE=7200s`(2h)/`KEEPINTVL=75s`/`KEEPCNT=9`，合计 **≈2h11m** 才发现死链。已改为显式设 `KEEPIDLE=10`/`INTVL=3`/`CNT=3`（≈19s 发现）。写字机侧同一坑见 `docs/protocol.md` §9-A。
- `SendLine(const std::string&)`：send（自动补 `\n`，部分写循环）；未连接返回 false。
- `TakeResponse()` / `WaitOk()`：FIFO 接收 `ok` / `error:`；容量由 512B 窗口推导为 255，覆盖最短非空行可形成的最大在途应答数，避免队满丢失 error 后错配后续 ok。
- `IsConnected()` = TCP 已连接；`IsReady()` = 版本、抬笔、授权探测序列完成；
  `IsAuthorized()` 由抬笔状态下的 `G1 G53` 零位移探测结果决定。`G1` 必须在 `G53` 前，命中 Grbl 行首授权门。
- banner `Grbl ` 前缀检测表示**对端复位**：清 ready/授权/应答队列并重新探活。只有
  `PrepareAbortReset()` 标记过的本机 abort 复位允许自动 `$X`；其他复位或 Alarm 不自动解锁。

## 设备侧 MCP 工具（2026-07-27）

| 工具 | 用户意图 | 动作与边界 |
|------|----------|------------|
| `hutuji.status` |「连上了吗 / 画完了吗」| 返回链路、授权、任务、Grbl 状态与坐标。出图中只发实时 `?`。|
| `hutuji.draw(url)` | 云端生成后执行 | 全量下载、CRC 校验、授权门、逐行 Telnet 转发；绘图 fresh `Idle` 后由 S3 单独发 `M30`，等最终应答和 fresh `Idle` 才完成。|
| `hutuji.abort` |「停下 / 取消」| 非换纸态发 `!`，轮询到 `Hold/Idle` 后 `Ctrl-X` 清 planner；随后只对这次自发复位执行受限 `$X`、明确 Z=0 抬笔和重新探活。换纸期间延后中止。|
| `hutuji.pause` |「暂停 / 等一下」| 发实时 `!`，Job 在行边界停止继续灌入；**不丢进度**，换纸中拒绝暂停。|
| `hutuji.resume` |「继续 / 接着画」| 发实时 `~`，清除 Job 暂停门，原地续画。|
| `hutuji.repeat` |「再画一张 / 一样的再画一次」| 成功出图后留存一份最多 512KiB 的 PSRAM G-code，优先跳过下载+CRC 重画；留存不存在则用上次 URL 重下。repeat 与 draw 共用“画完后 S3 显式 `M30`”路径；MCP 文件本身仍不含 M30。|
| `hutuji.pen_test` |「试一下笔 / 笔能用吗」| 独占任务：`M5`→`M3 S1000`→停 1 秒→`M5`，每步等 `ok`；先抬笔建立确定起点，否则已落笔时 `M3` 是零位移、纸上不留点。结束以 MCP notification 提醒检查点迹。|

并发与超时约束（code review 后加固）：

- 暂停与已发出行的 `ok` 等待都冻结超时计数，避免长暂停被误判为转发失败。
- `RequestPause`/`RequestResume` 检查 `SendRealtime` 返回值，失败即回滚本地状态，不把断链说成已暂停/已继续。
- `stream_mutex_` 让「检查 `paused_` + 写入该行」成为临界区，暂停命令发出后不会再多灌一行进 planner。
- `pen_test` 通过 `busy_` 与出图互斥；`pen_test_active_` 让试笔期间的 pause/resume 返回「正在试笔，请稍候」。
- 暂停仅用实时 `!`/`~`；Hold 中普通 Z 行只会排入 planner，并会破坏 `ok` 配对，因此不再在 pause/resume 中注入抬落笔。
- 暂停超过 10 分钟自动取消并向云端通知，避免 `busy_` 无限占用使全部工具返回 `busy`。
- Grbl fork 中 `M3/M5` 笔控会同步 planner 后才返回 `ok`；`3ca9d31` 将 `M3/M5` 行的
  `ok` 等待窗口放宽到任务级 idle 超时。纸张指令仍优先使用 90s 换纸等待，普通 G0–G3 与普通
  M-code timeout 不变。
- 下载/校验或换纸结束时，任务状态按 `paused_` 写为 `paused` 或 `streaming`，不得谎报正在画。
- **落笔旁路（实施状态：fork `13057e8` 已提交/推送）**：每次首画/重画发送下载文件前，S3 先等待一份
  **fresh `Idle`**，再等待 100ms（奎享本机 `$1=25`，须让电机失能并由弹簧回到自然抬笔位），
  随后以不计入下载文件窗口的受控旁路发送固定 `G92 Z0`。下载文件仍禁止 `G92`，并原样连续
  发送其中的 `Z5 + XY + Z0`；不改 `$1/$3`。
  该实现由 fork `13057e8` 提交/推送（`main/boards/lichuang-dev/hutuji_job.{h,cc}`、`hutuji_pipe.cc`
  的 `PreparePenOrigin`）；上一版 `15f73c9` 的 `hutuji_job.cc` 内不含任何 `G92`。含该编排的镜像
  `xiaozhi.bin=0x2ce890`（内容对应 `13057e8`）已于 2026-07-29 20:41 app-flash 到 COM6 并通过 esptool
  `Hash of data verified`（证据 `results/s3-13057e8-com6-flash-evidence-20260729.md`）；S3 自动路径真实出墨
  已于 2026-07-30 B-12 归档：同一 `0x2ce890` 镜像下载 409 行/9782 字节/CRC32 `733095ec` 测试件，自动旁路 `G92 Z0` + `Z5.0F10000` 落笔，8 条线全部完整连续，页尾 `M30` 后 `[PaperStatus] 0`、`state=done`。
- 文件最后一行收到 `ok` 只表示进入 planner；任务还会发 `?` 并等待一份**新解析的**
  `Idle` 状态报告；随后 S3 单独发 `M30`，等换纸最终 `ok/error:90`，并再次确认 fresh
  `Idle` 后才置 `done`。`status_report_seq_` 防止复用旧 `Idle`。
- abort 与 `M30` 提交点共用 `stream_mutex_`：abort 先到则不启动换纸；M30 先到则进入
  `paper_change`，换纸完成后再落 `aborted`，不会竞态发送 `0x18`。
- `hutuji.status` 新增 `repeat_available`，智能体据此判断「再画一张」是否可执行。

## M2 进度（2026-07-28，云端触发与实机任务回归）

> 代码在 `feat/hutuji-usb-pipe`（含 `hutuji_job.*`，板级 `lichuang-dev`）。  
> 最新镜像以 IDF v6.0.1 构建并烧录 **COM6**。官方小智消息 API 已发现七个
> `hutuji.*` 工具，并通过云端路径实际触发 `draw/status/abort`；这证明云端工具调用可达，
> **不等于 App 真实语音对话已验收**。
>
> 非换纸 abort 回归：运动中 `Z=5` 调 abort 后，状态恢复为 `aborted/Idle`，
> `ready=true`、`authorized=true`、`Z=0`；继续观察 8 秒 XY 无漂移、planner 未恢复。
> 实测同时证明：soft reset 后单发 `M5` 可能因主轴模态已为 Off 而无动作，必须显式
> `G1G90 Z0.0F10000`；Z 抬笔行的 `ok` 后仍要等新 `Idle`，否则紧接 `$I` 会回 `error:8`。
>
> 完整任务回归：先看到 `streaming/Run`，过程中未出现 `done/Run`；最终为 `done/Idle`，
> `MPos=(100,100,0)`，`repeat_available=true`。这证明最后一行后的物理完成判定，不证明
> 纸面笔迹、自动换纸或 App 语音出图。
> Grbl §9 已烧录并验证 `Changing=Off`、`[ESP901]` 与 keepalive；有纸换纸时的
> `Changing=On` 仍未验证，不能把 abort 回归替代为换纸态分流证据。

| # | 项 | 状态 |
|---|-----|------|
| 1 | `hutuji.draw` 独立任务：下载→长度/CRC→授权门→逐行转发；busy 拒绝 | ✅ 代码 |
| 2a | `WaitResponse`：`Ok/Deferred(8)/Failed/Timeout` + 解析 `error:NN` | ✅ 代码 |
| 2b | `OnRxData` 先拆行再丢超长残段 | ✅ 代码 |
| 3 | abort 分状态；`!` 后轮询 `Hold/Idle` 再 `0x18`；仅自发复位受限 `$X`，显式 Z=0 后重探活；换纸中不发 `0x18` | ✅ 实机回归 |
| 4 | 单写者 `write_mutex_`；出图中 status 只 `?` | ✅ 代码 |
| 5 | 断连物理复位、废纸换纸与缓存重发 | 🟡 已实测到废纸 `M30`、`[PaperStatus] 0`、PSRAM 从第 1 行重画、55/55 行完成与 fresh `Idle`；最终无纸自动换纸 `error:90`，完整任务仍未 `done` |
| 6 | status 含 `authorized` + MCP notify | ✅ 代码 |
| 7 | 写字机固定 IP / Telnet | 🔬 实机 |
| 8 | `pause`/`resume`：`!`/`~` + 行边界暂停门 + 冻结 ok 超时；发送失败回滚状态 | 🟡 底层实机；最新云端路径未单独回归 |
| 9 | `repeat`：成功后留存 PSRAM buffer 跳下载+CRC，回落重下；`status` 报 `repeat_available` | 🟡 `repeat_available=true` 已实测；快路重画未验 |
| 10 | `pen_test`：`M5`→`M3`→1s→`M5` 建立确定起点；`pen_test_active_` 挡 pause | 🟡 底层动作已实测；最新云端路径未单独回归 |
| 11 | 最后一行后等待 fresh `Idle`，禁止 `done/Run` | ✅ 实机回归 |
| 12 | 绘图 fresh `Idle` 后显式 `M30`，等最终应答和第二次 fresh `Idle`；与 abort 提交点互斥 | 🟡 无纸状态下已收到 `[PaperStatus] 2` + `error:90` 并落 error；待有纸成功换纸实测 |

### 联调注意

- 自动发现当前只扫描 `/24`；缓存与候选均先校验 Grbl banner。错误但可连的 :23 主机自愈路径
  已写入固件，恶意/错误候选场景尚未做专门 HIL。
- 公网文件入口使用 `https://hutuji.donglicao.com/files/`；设备只接受该生产域的 HTTPS 默认 443 或显式 `:443`，拒绝明文 HTTP、控制字符、userinfo、畸形/非 443 端口、非 `/files/` 路径与 fragment。HTTP 仅保留给 RFC1918 联调主机；真实 MCP 接入点和 token 不入库。
- 需要烧录自动触发绘图的 8.3 调试镜像时，用 CMake 选项注入短时效 G-code URL，不要手写
  `CMAKE_CXX_FLAGS` 字符串宏转义，也不要把真实 URL/token 写入源码：
  `idf.py -DBOARD_NAME=lichuang-dev -DBOARD_TYPE=lichuang-dev -DHUTUJI_AUTO_TEST_DRAW=ON -DHUTUJI_AUTO_TEST_DRAW_URL='<当前短时效 /files/*.gcode URL>' build`。
- `[License] Authorized` 只发串口、Telnet 不可见；Pipe 以抬笔后的 `G1 G53` 零位移探测
  `ok/error:110`。`G1` 必须置于 `G53` 前以命中行首授权门；探测完成前 `ready=false`，禁止 draw 抢跑。

## 验证状态

- `05f4563` 已用 IDF v6.0.1 构建并烧录 COM6；esptool 对 bootloader、partition、OTA、
  assets、app 均报告 `Hash of data verified`。COM9 两轮启动日志确认 IDF 6.0.1、8MB PSRAM、
  七个 `hutuji.*` 工具、Telnet 连接、授权零位移探测与持续 fresh `Idle`，未见
  panic/assert/backtrace。这只证明当前基线的基础启动与 Telnet 探活，不证明自动换纸或断连重画。
- `05f4563` 最终构建：`xiaozhi.bin=0x2ccde0`，最小 app 分区剩余 `0x123220`（29%），
  `xiaozhi.bin` SHA256 `A77C8635405B7FB18B18CE2486621A7635374D75FEE37E0078CD1423F29F80CC`，
  `merged-binary.bin` SHA256 `9328F2B71CAC67603E9B9BF063980AE11D575A749CE7F124A4F13C0CFA690E1D`，
  `releases/v2.4.0_lichuang-dev.zip` SHA256 `F11E51853E0D6E7CD223344F748330CCB583E7CC913E02E43DCD4E08D52941C2`。
- 2026-08-06 S3 深度复审工作树已用 ESP-IDF v6.0.1 全量构建：`hutuji_pipe.cc`、`hutuji_job.cc` 实际重编，`xiaozhi.bin=0x2cfdb0`，最小 app 分区余 29%。host unittest **13 passed**，C++ formatter check 通过；覆盖授权探针行首、CRC 头严格解析、生产 HTTPS/443 origin、255 深应答队列和 verbose error fail-closed。该镜像**未烧录**，不构成硬件证据。
- `27eff73` 已提交并推送：移除过期硬编码自动测试 URL，改为 CMake 选项注入。
  IDF v6.0.1 验证两条构建路径：默认构建通过（`xiaozhi.bin=0x2ccde0`，余 29%，自动测试关闭）；
  `-DHUTUJI_AUTO_TEST_DRAW=ON -DHUTUJI_AUTO_TEST_DRAW_URL='http://127.0.0.1/files/test.gcode'`
  dummy URL 构建通过（`xiaozhi.bin=0x2ccfe0`，余 29%）。
- `3ca9d31` 已提交/推送：修复 `M3/M5` planner-sync `ok` 等待窗口。IDF v6.0.1 默认构建通过
  （`xiaozhi.bin=0x2cce30`，余 `0x1231d0`/29%，SHA256
  `5D5FE23CE0DF6EE364EBACC2503E8374174781AEAE5E9BFC9BACDF227987BBA6`）；8.3 自动触发镜像构建通过并
  烧录 COM6（`xiaozhi.bin=0x2cd070`，余 `0x122f90`/29%，SHA256
  `98C3B6AF0CB136AF79A0256B5FB8A5279FE0715FE087322419A791D3BD3C122C`）。
- 8.3 复测证据：`results/bringup-8.3-fault-capture-20260729-010436.md` 和
  `results/s3-3ca9d31-8.3-pen-timeout-evidence-20260729.md`。本次看到断连、重连、
  `Changing=Off`、废纸 `M30`、`[PaperStatus] 0`、PSRAM 从第 1 行重画、55/55 行完成与
  fresh `Idle`；上一轮页尾 `M5` 60s 误超时未复现。最终页尾自动换纸在无纸状态下返回
  `[PaperStatus] 2` + `error:90`，任务 `state=error`，不能判 8.3 完整通过。
- 已证：七工具云端发现、`draw/status/abort` 云端路径、abort 后受限恢复、Z=0、
  fresh-`Idle` 完成判定、`done/Idle`；普通画线断连后的废纸处理和缓存重发主路径。
- 未证：App 语音对话、纸面笔迹、`Changing=On`、有纸自动换纸成功、8.3 最终 `done/Idle`、P2 S3/S4、
  小批量出货。

## 历史留档

- **方案 D（UART/CH9350，已废弃）**：写字机 USB-B(CH340) → CH9350 模块（下位机模式，TTL 115200 8N1）
  → 实战派外拓口 UART1（TX=GPIO10，RX=GPIO11）。`IsConnected` 当时语义为「收到过任意字节」。
  UART API 查证来源：IDF v5.5.2 `components/esp_driver_uart/include/driver/uart.h`。
- **USB Host 方案（已废弃）**：`espressif/usb_host_ch34x_vcp` + `esp_usb::CH34x` 经 S3 原生 USB Host
  直连 CH340，因 S3 原生 USB 被板载 CH334F HUB 占为设备口否决。API 查证来源：
  https://components.espressif.com/components/espressif/usb_host_ch34x_vcp 、
  esp-usb 仓 `host/class/cdc/` 头文件、IDF 示例 `cdc_acm_vcp`。
