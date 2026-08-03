# 路线1 评估：整文件下发 + Grbl 本地自跑（**已否决，2026-08-03**）

> 结论先行：**否决**。最优形态（SD 卡异步跑图）被 MK38 板硬件否决——原理图无 SD/TF 卡槽、无 SPI 存储接口；
> 唯一可选的次优形态（内部 flash SPIFFS 同步跑图）受 192KB 分区上限与同步阻塞实现双重限制，
> 且要 supersede 根 `README.md` 锁定决策 #2（板间 WiFi Telnet 逐行）与 #3（哑管道），性价比不成立。
>
> 本文为**只读调研 + 静态分析**产出：未编译、未烧录、未实机。所有「改了会怎样」的判断均为推断，非验证结论。
>
> 归档目的：以后再有人（含未来 agent）提「为什么不把 G-code 传到 Grbl 本地跑」，直接看本文，不必重新调研。
> 若要重开，必须先推翻本文 §1 的硬件事实（拿出 MK38 带 SD 卡槽的证据）。

## 0. 动机与提问

背景：S3 通过 WiFi Telnet :23 **逐行**把云端生成的完整 `.gcode` 喂给 Grbl。
这条链路上反复出现的问题（`pbuf_free: p->ref > 0`、Telnet 背压、20s 发送停滞、半行拼接、
feed-hold 抢占与单写者锁契约冲突），共性都是「240MHz 单片机同时跑 lwIP + 实时步进 + 逐行网络喂料」。

社区共识（V1Engineering 论坛、FluidNC 作者公开表态）：**WiFi 逐行流 G-code 是公认反模式**。

路线1 的设想：我方 G-code 是云端一次性生成的**完整文件**，不是交互式实时流。
既然如此，把「S3 逐行喂」改成「S3 把整份文件传到 Grbl 侧本地存储 → 发一条 run 命令 → Grbl 本地自跑」，
跑图期间网络不参与实时喂料，那一整类 bug 从根上消失，且**保留 WiFi**（云端下发仍走 WiFi，只是不逐行喂），
语音 + 云端生成的产品卖点不受影响。

需要回答：① Grbl 侧有没有现成能力；② S3 侧改造工作量；③ 硬件代价；④ 值不值。

## 1. 硬件事实：MK38 板无 SD 卡槽（决定性否决项）

| 检查项 | 方法 | 结果 |
|---|---|---|
| 原理图文本层 | 解压 `docs/hardware/MK38-总图.pdf` 全部 stream，提取 21.9 万字符文本层，检索关键词 | `SD` / `TF` / `CARD` / `MicroSD` **0 命中**；`MISO` / `MOSI` / `SCK` / `SPI` **0 命中** |
| 提取有效性对照组 | 同一文本层检索已知器件 | `HR4988` 14 次、`595` 11 次、`CH340` 2 次 —— 证明文本层提取成功，0 命中不是解析失败 |
| 板级器件表 | `docs/hardware/mk38-board.md` §1（U1–U11 全表） | ESP32 模组、7×HR4988SQ、74HC595D、CH340C、78M05H、AMS1117、DC 座、按钮。**无任何存储器件** |
| 现役固件引脚 | `Grbl_Esp32/src/Machines/custom_3axis_hr4988.h` | 只有 `I2S_OUT_DATA/BCK/WS`（IO21/16/17，供 74HC595）。**无任何 `GRBL_SPI_*` 或 SD 引脚定义** |
| 脚位冲突 | 同上 | **IO21 已被 74HC595 的 SI 占用**；Grbl_Esp32 SD 默认走 VSPI（SCK=18/MISO=19/MOSI=23/SS=5），机型头注释另提到 GPIO5 作 `RCLK`——脚位本就紧张 |

**判定：MK38 物理上没有 SD 卡槽，也没有预留 SPI 存储接口。SD 路线（下文 §2 路线 A）被硬件否决。**

要走 SD 就得改板：加卡槽 + 腾出 SPI 脚位。而 IO21 已占用，脚位重排会撞上 `mk38-board.md` §4
「默认禁止改任何 GPIO / I2S / 步进 / 595 相关 `#define`」的铁律，须用户逐脚点名批准 +
fz `machine_pin_erc` + 实机复测。这是**硬件改版级工程，不是软件改造**。

## 2. Grbl 侧能力：双路线现成，但只有被否决的那条好用

调研对象：`D:/Users/Grbl_Esp32`，分支 `fix/panel-hold-after-align-116687e`（HEAD `1b56393`）。
本 fork **保留**了上游的本地文件跑图能力，不需移植：

| 路线 | 存储 | 触发命令 | 执行机制 | 硬件 |
|---|---|---|---|---|
| **A. SD 卡** | 外置 SD（SPI 模式） | `[ESP220]/file.gcode` 或 `$SD/Run=/file.gcode` | **异步**逐行：`Protocol.cpp` 主循环按 `SD_ready_next` 从卡读一行执行一行 | **需卡槽 → 本板无，已否决** |
| **B. SPIFFS** | 内部 flash 分区 | `[ESP700]/file.gcode` 或 `$LocalFS/Run=/file.gcode` | **同步阻塞**：`while (file.available()) { execute_line(...); COMMANDS::wait(1); }` | 零硬件 |

关键代码位置（供复核，行号可能随改动漂移）：

- SD 异步机制：`Grbl_Esp32/src/Protocol.cpp` 主循环 `SD_ready_next` 分支；`SD_ready_next` 由 `Report.cpp` 在状态报告/`ok` 处理时置位
- SD 文件操作：`Grbl_Esp32/src/SDCard.cpp` 的 `openFile()` / `readFileLine()` / `closeFile()` / `get_sd_state()`
- SD 跑图命令：`WebSettings.cpp` 的 `runSDFile`（注册名 `"SD/Run"` / `"ESP220"`）
- SPIFFS 跑图命令：`WebSettings.cpp` 的 `runLocalFile`（注册名 `"LocalFS/Run"` / `"ESP700"`）
- 命令分发链：`Protocol.cpp` `execute_line` 行首 `$`/`[` → `system_execute_line` → `ProcessSettings.cpp` `do_command_or_setting` 遍历 `Command::List` 匹配 `getName()` / `getGrblName()`
- SD 开关：`Config.h` 的 `#define ENABLE_SD_CARD`（默认开，机型头未覆盖）
- 分区表：`min_spiffs.csv`（`platformio.ini` 指定）—— `spiffs data 0x3D0000 0x30000` = **196608 B ≈ 192KB**

文件上传路径：**只有 HTTP**（SD 用 `POST /upload` → `SDFile_direct_upload`；SPIFFS 用 `/files` → `SPIFFSFileupload`）。
无 Telnet 上传路径。`Config.h` 的 `ENABLE_AUTHENTICATION` 被注释，**上传无需认证**。

命令可从任意客户端下发（Telnet / 串口 / 蓝牙 / WebSocket 共用同一分发器），
所以 `[ESP700]` 经现有 Telnet :23 就能触发，不需新通道。

## 3. S3 侧改造评估：工作量中等，但收益随路线 A 一起蒸发

调研对象：`D:/Users/xiaozhi-esp32`，分支 `feat/hutuji-usb-pipe`（HEAD `ae4b524`），
文件在 `main/boards/lichuang-dev/`。

现状：`Job::Run()` 编排为 `DownloadToPsram` → `VerifyCrc` → 授权门 → `PreparePenOrigin`（G92 Z0）→
**`StreamToGrbl()`** → `WaitForIdle` → `ChangePaperAfterDraw`（页尾单发 `M30`）。
G-code 全程在 **PSRAM 内存 buffer**，不落 flash（`DownloadToPsram` 用 `heap_caps_malloc(MALLOC_CAP_SPIRAM)`，
上限 `kMaxGcodeBytes = 512KB`，CRC32 取自 `X-Hutuji-CRC32` 响应头）。

改造清单（推断，未实施）：

- **可删（约 360 行）**：`StreamToGrbl()` 主循环 —— 窗口化 `c_line` 字节计数（`kWindow=512`）、
  `TakeResponse` 等 `ok`、`ConfirmInFlightDoneByStatus()` ok 丢失兜底、`paper_pending` 逐行换纸模式。
  `SetWindowed` / `drain_on_send_` 机制随之失效。
- **可复用**：`DownloadToPsram`（上传的数据源）、`VerifyCrc`、`PreparePenOrigin`（本地自跑同样要 Z 基准）、
  `WaitForIdle`（可作 run 完成判定）、`WaitWhilePaused`、以及 **abort 全链路**
  （`!` / `0x18` 是 Grbl 带内实时字符，对本地自跑同样有效；只需把「Quiesced = 窗口在途应答消费完」
  的判定换成「Grbl 已停稳」，后者 `abort_hold_confirmed_` / `HasFreshStoppedStatus` 已有实现）。
- **须新增**：文件上传函数（HTTP POST 到 Grbl，或复用 `SendRawLocked` 走约定协议）、run 触发、
  状态轮询循环（`?` 替代逐行 `ok` 流控）。
- **涉及**：`hutuji_job.{cc,h}`（大头）、`hutuji_pipe.{cc,h}`（+1~2 个函数）；约 8–10 个函数。
  **工作量：中**。

不可避免的功能退化（推断）：

| 能力 | 现状 | 路线1 之后 |
|---|---|---|
| 进度精度 | 按**已确认行数**，精确到行 | 只能靠 `?` 状态报告 + MPos 估算，精度下降 |
| 断连恢复 | `RecoverDisconnectedDraw()` + 从 PSRAM 第 1 行重画（≤2 次） | 本地自跑**无断点续传**，只能整体重传重跑 |

## 4. SPIFFS 路线（唯一可选形态）的具体缺陷

| 维度 | SD 异步（本板不可用） | SPIFFS 同步（唯一可选） |
|---|---|---|
| 网络是否参与实时喂料 | 不参与 ✅ | 不参与 ✅ |
| 跑图期间能否响应命令 | 能（主循环正常轮询） | **存疑**——命令上下文被 `runLocalFile` 占住 |
| `?` 状态查询 | 正常 | **未确证** |
| `!` / `0x18` 抢占 | 正常 | 实时字符大概率仍有效，但**未确证** |
| 容量上限 | 卡多大装多大 | **192KB 死上限** |
| 换纸 `[ESP9xx]` 交互 | 未验证 | 未验证，风险更高（见下） |

两条要命的点：

1. **容量与契约冲突**：`docs/protocol.md` §5 G-code 契约允许单文件 **≤512KiB**，
   而 SPIFFS 分区只有 **192KB**——**装不下一个合规上限的文件**。实际玫瑰图仅几十 KB 可放，
   但复杂图会直接撞墙，且无优雅降级路径。
2. **换纸依赖命令通道**：本项目换纸是**页尾由 S3 单独发 `M30` 旁路触发**（下载文件内禁 `M30`，见 §5 契约）。
   本地自跑模式下，Grbl 跑完文件后 S3 要能及时插进去发 `M30`——这恰好要求**跑图期间命令通道还活着**。
   同步阻塞实现能否满足，无证据，须实测。等于用一类已知 bug 换一类未知风险。

其他未确证项（若将来重开须先补）：

- `ENABLE_SD_CARD` / SPIFFS 跑图路径是否真的编入现役固件产物（`Config.h` 默认开，机型头未关，但未验证产物）
- SD/SPIFFS 跑图与本 fork 新增换纸 `[ESP9xx]` 命令族、`report_realtime_status` 改动路径的交互
- HTTP 上传**无 CRC 校验**（云端链路有 CRC32，Grbl 侧 `SDFile_direct_upload` 只写盘），端到端可靠性须自补

## 5. 与锁定决策的冲突

路线1 直接顶到根 `README.md` 两条**锁定决策**：

- **决策 #2**：板间 = WiFi Telnet :23，S3 作 TCP 客户端逐行转发
- **决策 #3**：哑管道 —— S3 只做下载 + 校验 + 转发

路线1 使 S3 不再是哑管道（要管上传协议、run 触发、状态解释）。
改这两条需走 `supersede` 流程并同步 `docs/protocol.md`，**不是技术可行性问题，是决策问题**。

## 6. 否决理由（按权重）

1. **最优形态被硬件否决**（§1）。要走需硬件改版 + 破 GPIO 铁律，超出软件改造范畴。
2. **次优形态收益打折**（§4）。SPIFFS 能消掉逐行喂料的已知 bug，但同步阻塞引入未知风险，
   而换纸恰恰依赖被它占住的命令通道。
3. **192KB 上限与 §5 契约的 512KiB 直接冲突**，无优雅降级。
4. **要 supersede 两条锁定决策**（§5），换来一个半残方案，性价比不成立。

## 7. 替代方向与正确的下一步

不换架构，走**路线 2**：保持逐行 Telnet，把流控继续向 grblHAL / UGS 那套成熟的 character-counting 协议收敛。
S3 侧本已是窗口化（`kWindow=512`），问题集中在**异常路径**（背压、断连、feed-hold 抢占、`ok` 丢失兜底），
而这批异常路径在 2026-08-02 的 code review 中已修一轮：

- Grbl `1b56393`：`Telnet_Server::write()` 改 `MSG_DONTWAIT` + 1s 绝对 deadline（已推，**未烧录**）
- S3 `2ea10b2` / `ae4b524`：`SendRawLocked` 锁粒度收窄至单次非阻塞 syscall；`!` 以等待者优先级绕 `write_mutex_`；
  `SendFeedHold` 失败 teardown 走 `ShutdownSocket(session)` 恢复单写者锁契约（已推，**未编译验证外未烧录**）

**理性的下一步不是重构架构，是先把已修的固件烧上去实测。** 当前处境：修了一批源码级问题，
但板上跑的仍是旧镜像（COM10 = `fdf2ad8`；S3 = 2026-08-01 镜像），**无人知道这批修复是否真的解决了问题**。
在缺实测反馈时讨论换架构，等于盲猜病根。

> **2026-08-03 supersede（仅更新上段的烧录状态，不改本文否决结论）**：Grbl `1b56393` 与 S3 `ae4b524` 均已烧录
> （Grbl 在 COM11，S3 经 `release.py lichuang-dev`），并以 551 行玫瑰图完成有纸 S4 实测：全量下发 + 自动换纸八步全绿、
> `state=done`，`pbuf_free: p->ref > 0` **本轮未复现**（单次通过不等于已修复）。证据 `results/s4-rose-20260803-evidence.md`。
> §1 的硬件否决（MK38 无 SD 卡槽）不受影响，本文仍为已否决方案的归档评估。

建议顺序：

1. 烧 Grbl `1b56393` + S3 当前 HEAD，装纸，跑 551 行 S4。
2. 观察 `pbuf_free: p->ref > 0` 是否复现、20s 发送停滞是否消失。
3. **修完就稳** → 架构不动，本文归档结论生效。
4. **仍不稳** → 届时重开路线1 讨论，且**那时有实测数据支撑「非改不可」，改板才有理由**。

## 8. 证据边界

- 全部结论来自**只读源码调研 + 原理图 PDF 文本层解析**，未编译、未烧录、未实机。
- §3 的改造工作量、§4 的能力退化均为**推断**，非验证结论。
- §1 的硬件否决是**文档与源码事实**（原理图文本层 0 命中 + 器件表无存储器件 + 固件无 SD 引脚定义），
  强度高于推断，但仍非实物拆机核对；若要推翻须拿实物或新版原理图证据。
