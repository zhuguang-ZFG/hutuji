# M1 最小管道固件补丁（feat/hutuji-usb-pipe，方案 E：WiFi Telnet）

> 补丁位置：`D:/Users/xiaozhi-esp32`，分支 `feat/hutuji-usb-pipe`（基于 main `38bc4f4`，未 commit）。
> 目标板型：`lichuang-dev`（立创实战派 ESP32-S3）。
> **2026-07-24 传输层第三版**：USB Host（硬件被 CH334F 占用否决）→ UART/CH9350（否决）
> → **方案 E：写字机 Grbl_Esp32 内置 WebUI 的 Telnet 服务（端口 23），S3 做 TCP 客户端直连**。
> 链路内字节流与串口完全同构：行协议、`ok`/`error:NN`、banner、实时字符一致。

## 硬件链路（方案 E）

写字机：Grbl_Esp32 开 Telnet（端口 23），STA 连家用路由器拿**固定 IP**。
实战派 S3：WiFi 由 xiaozhi WifiBoard 正常管理（连同一路由器），Pipe 只做 TCP 客户端。
目标地址编译期常量在 `hutuji_pipe.cc` 文件头：`HUTUJI_PIPE_HOST "192.168.1.50"` / `HUTUJI_PIPE_PORT 23`（占位，**联调时改**）。

## 改动清单

| 文件 | 说明 |
|------|------|
| `main/idf_component.yml` | 无 hutuji 相关依赖（lwip socket 走 IDF 内置） |
| `main/boards/lichuang-dev/hutuji_pipe.h/.cc` | `hutuji::Pipe` 单例：lwip socket 轻量同步 TCP 客户端 |
| `main/boards/lichuang-dev/lichuang_dev_board.cc` | `InitializeTools()` 里 `Pipe::Start()` + 三个 MCP 工具（三次改版均零改动，仅注释随方案更新） |

未改任何核心模块（`mcp_server.cc`、`main/CMakeLists.txt` 等未动）。

## hutuji::Pipe API（对外语义不变）

- `Start()`：幂等。只创建 `hutuji_tcp` 连接管理任务；**不管 WiFi**（WifiBoard 负责），WiFi 未就绪时 connect 自然失败走退避。
- 连接循环：`socket/connect`（同步）→ 成功后 `connected_=true` → **就绪探活：发 `$I\n`，等 `[VER:` 置 `ready_`** → `recv` 阻塞读 → 行聚合（512 上限）→ 应答解析。断线（recv 返回 0/<0）→ 清 `connected_/ready_` → 关 socket → **指数退避 1s/2s/4s…上限 30s** 重连。
- **TCP keepalive（v0.5 修正的实际 bug）**：原代码只设 `SO_KEEPALIVE=1`，注释写「尽快发现对端断电」——**实际是假的**：lwip 默认 `TCP_KEEPIDLE=7200s`(2h)/`KEEPINTVL=75s`/`KEEPCNT=9`，合计 **≈2h11m** 才发现死链。已改为显式设 `KEEPIDLE=10`/`INTVL=3`/`CNT=3`（≈19s 发现）。写字机侧同一坑见 `docs/protocol.md` §9-A。
- `SendLine(const std::string&)`：send（自动补 `\n`，部分写循环）；未连接返回 false。
- `WaitOk(timeout_ms, response*)`：EventGroup 等 `ok` / `error:`（机制三版未变）。
- `IsConnected()` = TCP 已连接；`IsReady()` = 探活成功；`GetLastLine()` 不变。
- banner `Grbl ` 前缀检测保留，语义改为**对端复位**：清 `ready_` + 清应答位 + 重新发 `$I` 探活。

## MCP 工具（不变）

- `hutuji.status`：返回 JSON `{connected, ready, last_line}`。
- `hutuji.draw(url)`：M1 仅校验 url 非空并返回 `"started"`，下载+转发为 M2。
- `hutuji.abort`：M1 占位返回 `"ok"`。

## M2 待办（按 protocol v0.5 更新）

> ⚠️ v0.5 定案推翻了 M1 时期的若干假设，下列条目已对齐。**开工前先实施 `docs/protocol.md` §9 的 Grbl 侧小改**，否则 S3 收不到任何换纸状态。

1. `hutuji.draw` 内 `xTaskCreate` 独立任务，四阶段**串行**（禁止在 MCP 回调主任务内阻塞）：
   **① 全量下载**（`GetBodyLength()` / `GetResponseHeader("X-Hutuji-CRC32")` / `Read()` 循环，模板 `ota.cc:292-322`）→ **② 校验**长度 + CRC32 → **③ 授权门**（未授权零字节下发）→ **④** 才逐行 `SendLine()`+`WaitOk()`。
   **不边下边转**（截断会静默产出半成品纸，protocol §5.1）。缓存进 PSRAM（≤512KB）并**持有至任务终结**，断连重发走缓存、不重新下载。
   任务进行中收到新 `draw` → 返回 `"busy"` 直接拒绝，不排队不抢占。

2. ⚠️ **必须先改 `Pipe` 的 API 形状**（M1 现状实现不了 §7 的错误分流，这两条文档规避不了）：

   **(a) `WaitOk` 丢失了错误语义 —— 必须能区分 `error:8`。**
   现状 `ProcessLine`（`hutuji_pipe.cc:~247`）把所有 `error*` 归到同一个 `kResponseErrorBit`，`WaitOk` 只返回 `bool`。而 protocol §7 要求 **`error:8` 不算失败**（换纸中运动行被推迟）、且该行已被写字机 `empty_line()` 丢弃**必须重发**；`error:11/90/110` 才是任务 error。
   若 M2 转发循环按 `WaitOk` 返回值判断（最自然的写法），`error:8` 会被当成失败而终止任务 —— **本可继续的出图被误判，废掉一张画了一半的纸**。
   **改法**：`WaitOk` 增加错误码出参（如 `bool WaitOk(uint32_t timeout_ms, std::string* response, int* error_code)`），或返回一个三态枚举 `{Ok, Deferred(8), Failed(NN), Timeout}`。`ProcessLine` 解析 `error:NN` 的数字并存下来。转发循环据此分流：`Ok`→下一行；`Deferred`→暂停、等换纸行 ok 后**重发本行**；`Failed`→任务 error；`Timeout`→按 §7 发 `?` 探活一次。
   注：`error:` 前缀判断用 `rfind("error", 0)` 可同时匹配 `error:NN` 与旧式 `error NN`，保留；但**必须把 `NN` 解析出来**。

   **(b) 接收缓冲超长时不可整体 `clear()` —— 会连带丢掉同分片里已到达的 `ok`。**
   现状 `OnRxData` 先 `append` 再判 `size() > kRxLineMax` 就 `rx_buffer_.clear()`，**发生在按 `\n` 拆行之前**。一个分片内若含超长残行 + 其后完整的正常行，正常行会被一起丢弃 —— **丢一个 `ok` 就是 60/90s 超时**（§3/§7）。
   触发门槛不高：§4 要求出图中发 `?` 探活，状态行 `<Idle|MPos:…|Bf:…|FS:…>`（含可选字段）与 `ok` 同分片到达并不罕见；`kRxLineMax=512` 虽宽于单行上限 255（§3），但异常对端/粘包下仍可越界。
   **改法**：先拆完所有完整行（`find('\n')` 循环）**再**判剩余残段是否超长；超长只丢**当前残段**（`rx_buffer_.clear()` 挪到拆行之后），已完整的行一律先交给 `ProcessLine`。

3. `hutuji.abort` **分状态**（protocol §4.1，不是万能急停）：
   非换纸态 → `!` → ≥8s → `0x18`；**换纸活动态 → 不发 `0x18`**（会被 `paper_should_ignore_host_reset()` 吞，且 ignored 回执只发 SERIAL 收不到），改为置 abort 挂起 + 回报「换纸中无法即停、完成后即停」，等该行 ok 后不再续发。
   ⚠️ **实现约束（socket 生命周期，务必遵守）**：**只有 pipe 任务可以关闭 `sock_`。** abort 终止转发任务时**不要**从 MCP 回调里直接 `close(sock_)` 或调 `CloseSocket()`。
   - 原因：接收泵 `recv(sock_, …)`（`hutuji_pipe.cc:80`）**不持 `sock_mutex_` 裸读 fd**（与 `hutuji_pipe.h:68` 的注释承诺不一致）。外部线程关闭后，fd 号会被 lwip 立即复用给下一个 socket，阻塞中的 `recv` 可能作用到**别人的 fd**——读走其他连接的字节或永久阻塞，且症状随机、极难定位。
   - 正确做法：置**原子 abort 标志**让转发循环自行退出；若需立刻唤醒阻塞的 `recv`，用 **`shutdown(sock_, SHUT_RDWR)`**（只半关、不释放 fd 号，无复用风险），由 pipe 任务在 recv 返回后统一走既有的 `CloseSocket()` 路径。
   - **不要改成"给 recv 也加 `sock_mutex_`"**：那会让 `CloseSocket()` 阻塞在 recv 的整个持锁期间（可能数十秒），反而更糟。

4. **单写者纪律**：`draw` 转发与 `status` 查询**不得并发写同一 socket**（加发送锁或统一排队）。出图中 `status` **只发 `?`**（实时旁路不吃 ok）；`M704`/`[ESP901]` 进 client_buffer 吃 ok，仅空闲时可发。
5. 断连物理复位（protocol §2.1）：重连 ready 后，普通行断连 → `0x18`→`M30`→从头重发；换纸窗口内断连 → 抬笔停机 + 人工提示。重连前先显式 `close()` 旧 socket（避免 `MAX_TLNT_CLIENTS=1` 槽位被半开连接占死）——注意这个 `close()` **本就发生在 pipe 任务内**（现有 `CloseSocket()` 路径），与第 3 条的「只有 pipe 任务可关 socket」不矛盾。
6. `status` 返回加 `authorized` 字段；失败/进度经 `Application::SendMcpMessage` 主动推送云端。
7. 写字机侧：配 STA 固定 IP、确认 Telnet 开启（设置名拼写待串口核对）。

## 验证状态

- 本机无 ESP-IDF 环境，**未编译**，仅代码评审级自查。
- lwip API 按 IDF 5.5/6.0 现行写法：`lwip/sockets.h` + `lwip/netdb.h`（socket/connect/recv/send/close/setsockopt/inet_addr/htons）；ESP-IDF lwip 不产生 SIGPIPE，send 用 flags=0 + 返回值判断断连。
- 依赖可达性：`main` 的 `PRIV_REQUIRES` 未显式列 `lwip`，但常备编译的 `ml307_board.cc` 依赖的 `78/esp-ml307`（AT/socket）公开传递 lwip，`esp-hi` 等板文件直接 include `esp_wifi.h` 亦可编译，判定可过；若构建报 lwip 头文件找不到，一行修复：`main/CMakeLists.txt` 的 `PRIV_REQUIRES` 加 `lwip`。
- 首次构建：`python3 scripts/release.py lichuang-dev`（仓库要求 IDF v6.0.2）。
- 硬件冒烟：写字机起 Telnet 后看日志 `已连接写字机 Telnet` 与 `Grbl 探活成功（[VER:...]`，再调 `hutuji.status` 确认 `connected/ready`。
- 本机无 clang-format，未跑格式检查。

## 历史留档

- **方案 D（UART/CH9350，已废弃）**：写字机 USB-B(CH340) → CH9350 模块（下位机模式，TTL 115200 8N1）
  → 实战派外拓口 UART1（TX=GPIO10，RX=GPIO11）。`IsConnected` 当时语义为「收到过任意字节」。
  UART API 查证来源：IDF v5.5.2 `components/esp_driver_uart/include/driver/uart.h`。
- **USB Host 方案（已废弃）**：`espressif/usb_host_ch34x_vcp` + `esp_usb::CH34x` 经 S3 原生 USB Host
  直连 CH340，因 S3 原生 USB 被板载 CH334F HUB 占为设备口否决。API 查证来源：
  https://components.espressif.com/components/espressif/usb_host_ch34x_vcp 、
  esp-usb 仓 `host/class/cdc/` 头文件、IDF 示例 `cdc_acm_vcp`。
