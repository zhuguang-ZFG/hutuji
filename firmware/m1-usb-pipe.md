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

## M2 进度（2026-07-26，无设备代码落地）

> 代码在 `feat/hutuji-usb-pipe`（含 `hutuji_job.*`，已提交）。  
> **未 IDF 构建 / 未烧录 / 未实机**——不可宣称联调完成。  
> Grbl §9 仍建议烧录后再做换纸态精确分流；无 §9 时靠 `error:8` 重发 + 换纸行长超时。

| # | 项 | 状态 |
|---|-----|------|
| 1 | `hutuji.draw` 独立任务：下载→长度/CRC→授权门→逐行转发；busy 拒绝 | ✅ 代码 |
| 2a | `WaitResponse`：`Ok/Deferred(8)/Failed/Timeout` + 解析 `error:NN` | ✅ 代码 |
| 2b | `OnRxData` 先拆行再丢超长残段 | ✅ 代码 |
| 3 | abort 分状态；MCP 回调不 sleep；`!`→8s→`0x18` 独立任务；换纸中不发 `0x18` | ✅ 代码 |
| 4 | 单写者 `write_mutex_`；出图中 status 只 `?` | ✅ 代码 |
| 5 | 断连物理复位（`0x18`→`M30`→缓存重发） | 🔴 未做（无设备；重连后当前任务 error） |
| 6 | status 含 `authorized` + MCP notify | ✅ 代码 |
| 7 | 写字机固定 IP / Telnet | 🔬 实机 |

### 联调注意

- `HUTUJI_PIPE_HOST` 仍为占位 `192.168.1.50`，上板前改。  
- 京东云自签阶段：`HUTUJI_PUBLIC_BASE_URL=http://117.72.118.95`（nginx 80 已提供 `/files`）；正式证后再改 https。  
- 授权依赖应答行含 `[License] Authorized`；未看到则授权门挡下发。

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
