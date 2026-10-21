# hutuji 板间协议与 G-code 契约 v0.5

本文是 ②（S3 固件补丁）与 ③（MCP 服务）的共同契约。改动需两侧同步评审。

版本历史：
- v0.2：按两仓代码勘察修订（Grbl_Esp32 `c19a382`、xiaozhi-esp32 `38bc4f4`）。
- v0.3：物理层改 CH9350 桥接——**已否决**：CH9350 仅支持 HID 类（键盘/鼠标/扫码枪），不枚举 CH340（[WCH 官方手册](https://www.wch.cn/products/CH9350.html)）。
- v0.4：物理层改 **WiFi Telnet**（方案 E）。依据：Grbl_Esp32 内置 ESP3D WebUI 开放 Telnet:23，社区成熟模式；本机商业上位机已用 BT SPP 无线流式出图（`PAPER_AUTO_CHANGE_ON_BT_CONNECT`），无线流控经量产验证。USB Host 方案不可行的结论保留（实战派 C 口被 CH334F HUB 占为设备口）。
- **v0.5（2026-07-25，逐项 grill 定案）**：按 Grbl_Esp32 源码逐处核对修订。**纠正 v0.4 的关键错误假设**——`PaperSystem.cpp` 63 处消息路由全为 `CLIENT_SERIAL`、零 `CLIENT_ALL`，**S3 经 Telnet 收不到任何换纸进度/状态消息**（v0.4 的 §2/§3/§7 均误以为能收到）。据此新增 Grbl 侧最小改动清单（§9），并定案：Telnet 独占、半开连接 keepalive、断连物理复位与半成品纸处置、先下完再校验后发、授权前置门、abort 分状态、status 分级与单写者。
  > **历史标注约定**：v0.5 定稿时带 🔬 的条目代表待实测；其后是否完成以
  > `release-readiness.md` 和下方 2026-07-28 实测校正为准。
- **v0.5 实测校正（2026-07-28，不升协议号）**：Grbl §9、Telnet 逐行链路、授权探测、
  非换纸 abort 恢复和任务完成判定已有当前实验台证据。校正两条旧推断：soft reset 后
  单发 `M5` 不保证抬笔；最后一行 `ok` 不等于物理运动完成。换纸与断连重发仍未验证。
- **v0.5 辅助面增补（2026-08-18，不升协议号）**：定义 BLE 只读状态广播、GATT 只读诊断和可选 RSSI 近距提示的边界；不改变 WiFi Telnet 正式链路，也不表示已启用或已完成硬件验证。

## 1. 物理/链路层（方案 E：WiFi Telnet）

```
实战派 ESP32-S3 ──WiFi STA──► 家用路由器 ◄──WiFi STA── 写字机 Grbl_Esp32
        （S3 必须经路由器上云）              （Telnet server，端口 23）
```

| 项 | 约定 |
|----|------|
| 链路 | S3 TCP 客户端 → 写字机 **Telnet :23**（Grbl_Esp32 WebUI 原生，Config.h 已启用 WiFi） |
| 寻址 | 写字机 DHCP 入网；S3 先连 NVS 缓存 IP，未命中再扫本机 `/24`（候选须返回 Grbl banner 才接受并写回缓存）；路由器静态 DHCP 租约仍是运维首选，可免扫描与候选漂移 |
| 参数 | 链路内即原串口字节流：115200 语义不变，行协议逐行 `ok`/`error:NN` |
| 流向 | S3 → Grbl：G-code 行；Grbl → S3：`ok` / `error:NN` / 部分 `[MSG:]` / `?` 状态报告（**换纸消息不在其中，见下方「重要认知」**） |
| 重连 | TCP 断开 = 链路丢失：S3 指数退避自动重连（1s/2s/4s…上限 30s）；**槽位忙（半开占唯一客户端）固定 1s 重试缓存 IP、不扫网、不加倍等待**；重连成功后按 §2 重新探活；进行中的任务按 §2.2 处置 |

### 1.1 Telnet 独占（固件硬约束）

`TelnetServer.h:31` **`MAX_TLNT_CLIENTS = 1`**——Telnet 服务器同时只接受 **1 个**客户端，第二个客户端被 `clearClients()`（`TelnetServer.cpp:109`）直接 `.stop()` 拒绝。

**因此格局定为：Telnet 数据链 = S3 独占；CH340 串口 = PC 只读旁观。**

- PC 侧 `telnet <IP> 23` **仅用于 §1.3 一次性验链路**，验完**必须显式退出**，否则 S3 连不上。
- PC 全程观测走 CH340 串口——它是最全诊断口，且换纸消息**只**发这里（见「重要认知」）。
- 「PC 常连只读 + S3 独占发送」在物理上不可行（客户端上限为 1），不要尝试。

### 1.2 半开连接与 keepalive

`MAX_TLNT_CLIENTS=1` 叠加半开连接会产生**重连盲区**：S3 掉电/WiFi 瞬断/路由器 NAT 表项超时时**不发 FIN**，Grbl 侧 `_telnetClients[i].connected()` 可能仍为 true，唯一槽位被死连接占住，S3 重连一路撞 `.stop()` 拒绝，直到底层 socket 超时回收——**默认无 keepalive 时该盲区可能无限长**（`TelnetServer.cpp` 未设任何 keepalive；`WiFiClient.cpp:287` 有一处注释掉的 `SO_KEEPALIVE`，说明原作者考虑过但未启用）。

三层处置，全部必做：

| 层 | 动作 | 覆盖场景 |
|----|------|----------|
| S3 | 每次重连前对旧 socket 显式 `shutdown`+`close()`（发 FIN）；`abort`/错误路径同样保证关闭发出 | 优雅断连、主动 abort |
| S3（已修） | `SO_KEEPALIVE` **必须同时设 `TCP_KEEPIDLE=10`/`KEEPINTVL=3`/`KEEPCNT=3`** | 写字机掉电/断网时 S3 侧的发现 |
| 写字机（§9-A） | `clearClients()` 取得新客户端后同样设这四项（~20s 回收） | **S3 掉电、瞬断、NAT 超时**（FIN 发不出，S3 侧无法自救） |

> ⚠️ **只开 `SO_KEEPALIVE` 开关等于没开**：lwip 默认 `TCP_KEEPIDLE=7200s`(2h) / `KEEPINTVL=75s` / `KEEPCNT=9`，合计 **≈2h11m** 才发现死连接。M1 代码原本只设了开关（注释却写「尽快发现对端断电」），已于 v0.5 修正为显式三参数。

#### 断连发现时间常数（从代码算出，非实测）

| 场景 | 发现耗时 | 依据 |
|------|---------|------|
| 写字机优雅关闭（发 FIN） | **立即** | `recv` 返回 0 |
| 写字机断电/断网，S3 空闲等 `ok` | 默认 ≈2h11m → **改后 ≈19s** | `KEEPIDLE 10 + 3×3` |
| 写字机断电，S3 正在 `send` | 数十秒 | TCP 重传耗尽，`send` 返回 <0 |
| S3 掉电后重连（写字机侧） | 默认**可能永久** → §9-A 改后 ≈20s | `MAX_TLNT_CLIENTS=1` 槽位被死连接独占 |

**方案 E 验收判据**：判据**不以延迟/抖动为准**——严格逐行 `ok` 应答是 req/ack 协议，网络延迟只降吞吐、不破坏正确性。真正要标定的是上表四个时间常数，其中三个已由代码常量确定（不需实测）。

🔬 **仅剩两项需冒烟实测**：
1. **掉线频率**（取决于路由器/WiFi 环境，代码中不存在此数）：达标 = 代表性多页会话（连续流式 ≥10 分钟）TCP 零掉线。超阈或**换纸阻塞窗口内掉线**才评估回退备用链路。
2. **上表第 4 行实测值**（拔 S3 电 → 写字机回收槽位 → 重连成功的总时长），验证 §9-A 生效。

**重要认知（v0.5 更正）**：串口是最全诊断口，Telnet 只承载**一部分**输出。经源码核对：

- Telnet **收得到**：`ok` / `error:NN`、`?` 触发的 `<...>` 状态行（`report_realtime_status(client)` 按 client 直接回）、`report_init_message` banner、`CLIENT_ALL` 类消息。
- Telnet **收不到**：**全部换纸消息**——`PaperSystem.cpp` 63 处 `grbl_msg_sendf` 路由**全为 `CLIENT_SERIAL`**，`CLIENT_ALL` 出现 0 次；`GCode.cpp:1727` 的 `[MSG:PAGE_END_IMMINENT]`、`:1790` 的 `[Paper] page end` 同为 `CLIENT_SERIAL`。故 `[PaperAuto-N]` 进度、`[PaperStatus] N` 结果码在**未做 §9 改动前，S3 一个字节也收不到**。

**备用链路**（回退判据见 §1.2）：方案 B 拆机 TTL——写字机内 ESP32 GPIO1/3（CH340 输出侧）飞线 → 实战派多功能口 UART（IO10/IO11），3.3V、TX/RX 交叉 + GND。M1 代码的 UART 传输层实现已留档可切换。

### 1.3 写字机 WiFi 开通与验证（②开工第一步）

> ⚠️ 下列 `$Sta/...`、`$Telnet/...` 设置名沿用 ESP3D 通用写法，**本 fork 的确切拼写未经核对（🔬 串口一试便知）**。

1. PC 串口工具接写字机 CH340（115200），依次下发：
   - `$Sta/SSID=<家里WiFi>` / `$Sta/Password=<密码>`（写入即存 NVS）
   - 路由器后台给写字机 MAC 绑定静态租约（或 `$Sta/IPMode=Static`、`$Sta/IP=<addr>`）
   - `$Telnet/Enable=1`、`$Telnet/Port=23`（确认默认开启）
   - `$I` 或 `[ESP110]` 查当前 IP 与状态
2. PC 上 `telnet <写字机IP> 23`，发 `?` 应回 `<Idle|…>`，发 `$I` 应回 `[VER:…]`——链路通。
3. S3 侧联调时 `hutuji.status` 应显示 connected/ready。

### 1.4 BLE 只读诊断辅助面（阶段 A 已实现，默认关闭）

BLE 是 S3 的**本地只读诊断辅助面**，不是板间正式协议。正式 WiFi Telnet/G-code 契约仍为 v0.5；BLE 使用独立的 `BLE-DIAG schema v1`，不因 BLE 设计增补而升级 Telnet 或 G-code 版本。默认 S3 镜像仍关闭 `CONFIG_BT_ENABLED`，不改变默认镜像行为；本轮另以 ESP-IDF v6.0.1 构建了启用 NimBLE 的验证镜像，并在 COM14 完成阶段 A 广播与回滚验证。Grbl 写字机侧已有的经典蓝牙 SPP 与本节是不同器件、不同职责，不能混称为 S3 BLE。

#### 1.4.1 暴露面、隐私与授权

BLE 分为**公开广播面**和**认证 GATT 诊断面**，两者字段严格分级：

| 面 | 允许字段 | 禁止字段 | 读取条件 |
|---|---|---|---|
| 公开广播 | `schema_version`、粗粒度 `health`、粗粒度 `link_flags`、本次启动内的 `snapshot_seq`、`stale` | job 状态/名称/ID、故障细节、`boot_count`、`reset_reason`、能力 URL、token、密钥、文件名、prompt、G-code、用户内容、自由文本 | 附近设备可扫描；因此只放低敏字段 |
| GATT 诊断 | 固定二进制快照中的链路、job 枚举、故障分类、`boot_count`、`reset_reason`、上次 job 记录 | 同上所有秘密、用户内容、原始异常/堆栈、可重放命令 | 仅加密且认证的 LE Secure Connections + bonding；未认证读取返回 `insufficient authentication`，不得降级为明文或 Just Works 读取 |

实现必须使用可轮换的 BLE 地址：阶段 A 使用定时轮换的 NRPA；阶段 B 仅在已有 bond 的诊断窗口内使用 RPA。地址轮换失败时 fail-closed 停止对应广播，不得回退到公开 MAC 或稳定 ID。payload 不得携带 MAC、WiFi SSID、IP 或可长期关联用户的标识。v1 不启用 GATT notify/indicate，只提供明确列出的 `READ` 特征；不设置 `WRITE`/`WRITE_NR`，不设控制特征。所有写请求返回 ATT `write not permitted (0x03)`；未认证读返回 `insufficient authentication (0x05)`，授权窗口外返回 `insufficient authorization (0x08)`；非法 handle/长度分别返回 `invalid handle (0x01)`/`invalid attribute value length (0x0d)`。每个已认证 peer 的诊断读取限速为不超过 1 次/秒，最大同时诊断连接数为 1；超限返回 `insufficient resources (0x11)`，不改变设备状态。敏感字段审查必须同时覆盖广播、GATT 返回值、日志和错误路径。

配对只能通过本地维护动作发起：设备进入维护确认页后，用户必须在设备本地完成明确的触摸/按键确认。未绑定 peer 的配对准入窗口最多保持 60s；已绑定 peer 的诊断授权窗口最多保持 5min，连续 5min 无诊断活动或设备重启即关闭授权窗口，但不自动删除 bond。具体 UI 映射由 S3 实现文档落实，不得由 BLE 写特征、未认证 GATT 或远程 job 工具触发。B 阶段使用具备 MITM 保护的 LE Secure Connections 配对（Numeric Comparison 或 Passkey Entry），明确禁止 Just Works；若目标板型/IDF 无法提供该能力，B 阶段不得启用。只保留 1 个诊断 peer 的 bonding 记录。重新绑定前必须通过本地维护动作撤销旧 peer；撤销、配对失败、密钥丢失和未配对读取都只影响诊断面，不得重启、改变 job 或触碰 WiFi/Telnet。

阶段 A 使用 non-connectable、non-scannable legacy advertising，只启用公开广播，不提供 local name/scan response，不注册 GATT service，不启动 BLE 连接/配对路径。阶段 B 仅在本地维护授权窗口内暂停 A 面广播，开放 connectable advertising 和认证 GATT service；窗口超时、读取空闲超时或重启时必须终止现有诊断连接、停止 connectable advertising、清易失授权并恢复 A 面广播（若运行时总开关仍为 true）。A/B 的编译物和启动日志必须明确报告 `ble_surface=advertising_only` 或 `ble_surface=authenticated_read`，禁止用“BLE 已启用”这一模糊词掩盖实际暴露面。

#### 1.4.2 BLE-DIAG schema v1 与 UUID

下列 UUID 是本项目保留的 128-bit UUID，不是 Bluetooth SIG adopted service；正式实现必须逐字保持：

| 项 | UUID/编码 |
|---|---|
| Service `HUTUJI_BLE_DIAG` | `d3e6a7b0-7c22-4f61-9b18-2e4d5f6a7001` |
| Characteristic `DIAG_SNAPSHOT` | `d3e6a7b0-7c22-4f61-9b18-2e4d5f6a7002`，仅 `READ` |
| 广播承载 | Legacy Advertising 的 Service Data - 128-bit UUID（AD type `0x21`），使用上方 Service UUID；不启用持续扫描 |
| 数值编码 | little-endian；固定长度；无 UTF-8/JSON/自由文本 |
| 版本 | 第一个字节为 `0x10`（major=1、minor=0；高 4 bit=major、低 4 bit=minor）；major 变化必须使用新 UUID 或拒绝旧客户端；v1.0 payload 长度严格固定，收到其他长度须拒绝。未来 minor 只能在另行定义长度协商后追加字段，不能把长 payload 静默交给 v1.0 客户端 |

多字节字段的**线序**为 little-endian；128-bit UUID 在 Service Data AD structure 中按 Bluetooth 线序发送，消费者不得把文档字符串的字符顺序直接 memcpy 成空中 UUID。Service Data 的 AD value 由 16 字节 UUID + 上述 6 字节 payload 组成；加 length/type 共 24 字节，落在 legacy advertising 31 字节上限内。阶段 A 不附 flags、local name、TX power 或 scan response；任何新增 AD structure 都必须重新计算 31 字节预算并升级/复核 schema。

公开广播 Service Data 的 **v1.0 payload** 固定为 6 字节：

```text
schema_version:u8 | health:u8 | link_flags:u8 | snapshot_seq:u16 | stale:u8
```

`health`：`0=unknown`、`1=nominal`（快照新鲜、owner 正常且无当前故障）、`2=degraded`（诊断仍活跃但 WiFi/Telnet 不可用或存在可恢复异常）、`3=fault`（存在当前不可恢复或需人工处置的故障）。`link_flags`：bit0=`wifi_up`、bit1=`telnet_ready`，其余位必须为 0；不得借 bit 位暴露 SSID、IP、job 或错误细节。公开 `snapshot_seq` 为 GATT 32 位序列的低 16 位；两者均在完整快照发布时更新并分别回绕。`stale=1` 时消费者必须把 `health` 当作 `unknown` 处理。

认证 GATT `DIAG_SNAPSHOT` 的 **v1.0 value** 固定为 22 字节：

```text
schema_version:u8 | snapshot_seq:u32 | snapshot_age_s:u16 |
health:u8 | link_flags:u8 | job_state:u8 | fault_class:u8 |
reset_reason:u8 | boot_count:u32 | last_job_state:u8 |
last_job_result:u8 | record_flags:u8 | snapshot_flags:u8 | reserved[2]
```
`snapshot_age_s` 饱和为 `65535`；超过该值必须设置 `snapshot_flags` bit0=`stale`。`snapshot_flags` 其余位必须为 0。`reset_reason` 数值固定为 `0=unknown`、`1=power_on`、`2=software`、`3=panic`、`4=watchdog`、`5=brownout`、`6=external`、`7=other`；`boot_count=0` 只有在 `boot_record_valid=0` 时表示未知，不表示真实启动次数为零。

枚举必须固定：`job_state`=`0 unknown`、`1 idle`、`2 downloading`、`3 streaming`、`4 paused`、`5 paper_change`、`6 done`、`7 error`、`8 interrupted`、`9 aborted`；`last_job_state` 使用相同枚举；`fault_class`=`0 none`、`1 unknown`、`2 wifi`、`3 telnet`、`4 download`、`5 crc`、`6 grbl`、`7 paper`、`8 storage`、`9 reset`；`last_job_result`=`0 unknown`、`1 none`、`2 done`、`3 error`、`4 aborted`、`5 interrupted`。`reset_reason` 使用上一段固定编号并由 ESP-IDF `esp_reset_reason()` 映射，禁止直接传 SDK 自由文本。`record_flags` bit0=`boot_record_valid`、bit1=`job_record_valid`、bit2=`last_job_terminal`，未定义位必须为 0。`last_job_terminal=1` 只允许与 `last_job_result ∈ {done,error,aborted,interrupted}` 同时出现；任一记录无效时其对应值必须清为 `unknown`，不能保留旧值配无效 flag。

#### 1.4.3 快照一致性、新鲜度与未知值

- S3 在单一 owner 任务中组装快照，再以一次原子复制发布给广播和 GATT；不得跨任务分别读取 WiFi、Telnet、job、reset 字段后拼出混合时刻的数据。
- 快照刷新周期目标为 1s；`snapshot_age_s > 5` 或任一必需来源失效时，公开广播置 `stale=1`，GATT 置 `snapshot_flags.stale=1`，相关枚举置 `unknown`，不得用 `idle`、`false`、`0` 猜测；`snapshot_age_s` 按单调时钟计算，无法取得时钟只返回饱和值并置 stale。
- `snapshot_seq` 在每次完整快照发布后递增；GATT 读取返回同一份快照，不能触发 Telnet 命令、job 操作、重连、reset 或任何运动。
- 设备重启后，旧 RAM 状态立即失效；只有通过持久化记录恢复的字段才可填充，记录无效时返回 `unknown` 并设置对应 `record_flags` 为 0。
- v1 不提供通知；客户端必须按读取限速主动读取，并根据 `snapshot_seq`/`snapshot_age_s` 判断是否有新数据。

#### 1.4.4 `boot_count`、复位原因与上次 job 的持久化契约

诊断记录不得写入秘密。NVS 两个槽各保存完整记录：`schema_version`、`length`、64 位 `generation`、32 位 `boot_count`、固定枚举 `reset_reason/last_job_state/last_job_result`、flags 和末尾 CRC-32/IEEE（反射实现、poly=`0xedb88320`、init/xorout 均为 `0xffffffff`）。启动时分别校验 schema、length、枚举、flags 和 CRC，选择 generation 较大的有效槽；两个槽 generation 相等但正文不同视为冲突，整条记录无效。写入时写非当前槽、读回逐字校验成功后才成为新当前槽，掉电中断保留上一有效槽。只在启动和 job 状态边界写入，不按 G-code 行写入；generation 达 `UINT64_MAX` 后拒绝写新记录并标 unknown，不回绕复用旧记录。

固定观测时序：

1. 启动最早阶段读取 `esp_reset_reason()`，再尝试递增 `boot_count`；有效 boot record 提交成功后才允许把新计数对外提供。无有效槽或提交失败时 `boot_count=0` 且 `boot_record_valid=0`，不得猜测。
2. 观察到 job 接受时记录 `downloading`；在发送第一条 G-code 前先尝试记录 `streaming`；观察到暂停、换纸和最终 `done/error/aborted` 边界时分别记录对应状态。记录动作不得持有 Telnet 单写者锁，不得阻塞、取消、重试或改变正式 job 状态机。
3. 新启动发现上一条有效记录处于非终态时，将其解释为 `interrupted`/`reset`，把 `last_job_state=interrupted`、`last_job_result=interrupted` 写入新的有效记录；不得自动续画、自动重发、自动授权或自动换纸。若这次写入失败，只返回 `job_record_valid=0`，不改变启动路径。
4. NVS 记录损坏、schema 不支持、CRC 不符或 generation 冲突时，整条记录视为无效并返回 `unknown`；不得拼接两个槽的字段。

BLE 诊断持久化是**只读观测的证据面**，不是 job 执行前置条件。任何记录提交失败都只将 `fault_class=storage`、相应 `record_flags=0` 并写脱敏日志；正式下载、流控、暂停、abort、换纸和终态仍按既有 §2–§7 执行。终态记录失败后重启可能把上一份非终态保守解释为 `interrupted`，必须以 `job_record_valid=0`/storage 故障诚实暴露，不能据此自动处置机械。

持久化的 `last_job` 只描述最近一次已观测到状态边界的 job，不保存 URL、文件名、G-code、prompt 或 token。正常完成、下载失败、CRC 失败、流控失败、abort、换纸中断电、写入中断电和记录损坏都必须有固定枚举结果；未覆盖情况返回 `unknown`。

#### 1.4.5 编译开关、运行时开关与回滚

- `CONFIG_HUTUJI_BLE_DIAGNOSTICS` 默认 `n`；只有该开关和底层 `CONFIG_BT_ENABLED` 同时开启时才编译 BLE 诊断实现。当前默认关闭 S3 镜像保持 `CONFIG_BT_ENABLED` 未开启；本轮另以临时 overlay 构建了启用 NimBLE 的验证镜像。
- 即使编译启用，运行时 NVS 命名空间 `ble_diag` 的 `enabled` 键缺失、非法或为 false 时也不得启动 BLE controller、广播或 GATT；读取失败按 false 处理。由于 NVS 键名上限 15 字符，`ble_diag/enabled` 承载设计中的 `ble_diag_enabled` 语义。
- BLE controller/host 初始化失败、内存不足、配对栈异常或诊断任务退出时，必须 fail-closed 关闭 BLE 并记录脱敏分类；WiFi、Telnet、job 主任务继续按原路径运行，不得等待 BLE、不改 Telnet 超时/窗口、不自动重启整机。
- 现场回滚只能通过本地维护面/重新刷写默认关闭的构建完成；BLE 不得提供自我关闭或控制设备的写命令。回滚验收须证明无 BLE task/controller、WiFi/Telnet 仍可探活，且没有改变 job 状态。

#### 1.4.6 WiFi/Telnet 共存验收门槛

每个阶段都用同一块真实 S3、同一写字机、同一路由器和同一构建，仅切换 BLE 编译/运行开关；关闭 BLE 为基线，开启 BLE 为对照。每种模式至少 3 次、每次 30 分钟，包含 20 个确定性 job 闭环和 10 次受控 WiFi/Telnet 断连注入。以下是进入下一阶段的硬门槛，均为设计阈值而非当前实测结果：
RTT 必须使用同一类应用层探针、同一发送/接收路径和单调时钟；不得用 ICMP ping、BLE 往返或一次偶然样本替代 Telnet RTT。窗口应答和 job 终态须从带序号的 S3/Grbl 原始日志逐条核对，不能只看最终 `done`。每次运行必须保存 BLE off/on 构建身份、测试轮次、路由器/写字机/S3 标识、原始日志和统计脚本版本。

| 指标 | 允许范围 | 失败处理 |
|---|---|---|
| Telnet RTT | 开启 BLE 的 p95 ≤ `max(基线 p95+10ms, 基线 p95×1.25)`；p99 ≤ `max(基线 p99+20ms, 基线 p99×1.25)` | 关闭 BLE，禁止进入下一阶段 |
| 窗口应答 | `ok/error` 缺失、重复、错配、乱序均为 0 | 立即判失败，不调大超时掩盖 |
| job 闭环 | 20/20 终态正确；非预期 error、planner 继续执行、纸状态错判均为 0 | 回滚 BLE |
| 断连恢复 | 10/10 按既有 §2 状态机收敛；恢复时间不超过基线 p95+5s | 回滚 BLE，不改 Telnet 状态机 |
| 最低堆 | 开启 BLE 不低于基线最低值减 32KiB，且绝不低于 24KiB | 禁止放开 BLE |
| 功耗/温升 | 平均功耗增量 ≤10%，峰值增量 ≤15%，温升增量 ≤5°C | 关闭 BLE并登记原因 |
| panic/assert/watchdog/brownout | 0 次非预期事件 | 立即关闭 BLE，保留串口证据 |

基线不满足既有契约时，先修复基线，不得用 BLE 对照掩盖原有失败。任一指标失败都不得写入“共存通过”。

#### 1.4.7 RSSI 近距提示的范围

RSSI C 阶段的接收端默认为手机或 PC 诊断工具，不是 S3 扫描器；本仓和两个固件仓在 C 阶段前不实现持续扫描。S3 只发送上述广播，外部客户端负责采样和 UI。客户端使用至少 5 个有效样本的中位数和 5s 窗口；进入/退出阈值必须按具体接收器校准并保留迟滞，连续 3s 无样本显示 `unknown`，不得显示为“远”。

RSSI 只能输出 `near/unknown` 等相对提示，不得换算距离、方向、房间位置、安全边界或 UWB/AoA/Apple Precision Finding。阶段 A 使用轮换地址且不广播稳定身份，因此未绑定客户端只能把 RSSI 视为单次可见会话，不能跨地址轮换认定同一设备；阶段 B 的已绑定 peer 可在认证后关联设备，但仍不能把 RSSI 当定位。C 阶段验收至少覆盖多位置、多姿态、人体遮挡、多设备、地址轮换和无目标场景；多设备无法由认证身份消歧时显示 `unknown`，不得挑最强 RSSI 猜设备。正式链路回归时优先关闭持续扫描/提示，不修改 Telnet 流控迁就 RSSI。

#### 1.4.8 证据与外部参考登记

BLE 实现证据按阶段登记：实现仓的 host 测试、构建、刷写和启动身份留在 `D:/Users/xiaozhi-esp32` 及其 `AGENTS.md` 约定；本仓 `docs/release-readiness.md` 只登记状态，`docs/worktree-inventory.md` 登记跨仓工作树和本地证据路径；共存/HIL 记录必须包含测试矩阵、原始日志和关闭 BLE 基线。

外部参考与本地复核（访问日期均为 2026-08-18）：

| 来源 | 版本/范围 | 支撑结论 | 本地复核与限制 |
|---|---|---|---|
| [ESP32-S3 Datasheet](https://www.espressif.com/documentation/esp32-s3_datasheet_en.pdf) | v2.2，ESP32-S3 | 芯片提供 Bluetooth LE 5、LE Advertising Extensions 和 WiFi/BLE 内部共存；不列 UWB/AoA/CTE | 只证明芯片能力，不证明本固件启用；本地 `build-waveshare-35/sdkconfig` 与仓根 `sdkconfig` 均为 `CONFIG_BT_ENABLED is not set` |
| [ESP-IDF ESP32-S3 BLE Overview](https://docs.espressif.com/projects/esp-idf/en/release-v6.0/esp32s3/api-guides/ble/overview.html) | release-v6.0，ESP32-S3 | ESP32-S3 支持 Bluedroid/NimBLE；NimBLE 为 LE-only 且较省 heap/flash | 本项目目标工具链 v6.0.1；文档 URL 为 release-v6.0，具体实现仍须以本地 SDK commit 为准 |
| [ESP-IDF ESP32-S3 RF Coexistence](https://docs.espressif.com/projects/esp-idf/en/release-v6.0/esp32s3/api-guides/coexist.html) | release-v6.0，WiFi STA + BLE advertising/connected | 单一 2.4GHz RF 以时分复用；advertising 可抢占 WiFi 时隙；需软件共存开关与真实性能测试 | 这是设置 §1.4.6 RTT/功耗/HIL 硬门槛的依据，不代表门槛已通过 |
| [Bluetooth SIG Assigned Numbers](https://www.bluetooth.com/specifications/assigned-numbers/) | 在线 Assigned Numbers，访问 2026-08-18 | AD type `0x21` 为 128-bit Service Data；自定义 128-bit UUID 不冒充 adopted service | 本地 SDK `ble_hs_adv.h` 逐字核得 `BLE_HS_ADV_TYPE_SVC_DATA_UUID128=0x21` |
| 本地 ESP-IDF 源码 | v6.0.1 commit `8c19b156084a0753687347cca1f5355782893533` | `BLE_GAP_CONN_MODE_NON`/`DISC_MODE_NON`、SC/MITM、`INSUFFICIENT_AUTHEN/AUTHOR`、RPA 与 128-bit Service Data 均有实现入口 | 复核命中 `ble_gap.h`、`ble_sm.h`、`ble_att.h`、`ble_hs_adv.h`、NimBLE Kconfig；阶段 A 已在 xiaozhi 构建/COM14 启用态验证，阶段 B/C 仍未实现/验证 |

阶段 A 当前证据（加固源码 `f1aa0e7`）：`scripts/tests/test_hutuji_ble_diag_core.py` 专测 18/18、全 host suite 93/93；默认 Waveshare 构建 `build-waveshare-35/xiaozhi.bin` 2,974,336B、SHA-256 `58defc01…63b2`，BLE 启用构建 `build-hutuji-ble-enabled/xiaozhi.bin` 3,172,544B、SHA-256 `5a68fafe…80d6`，均为 ESP-IDF v6.0.1、Waveshare 3.5。启用镜像已刷 COM14 复验：4 分钟 1,683 行无内存错误/panic/assert/reboot，free SRAM 约 22.1KiB；Bleak 两轮收到 6 字节广播，seq 10→140 递增、`stale=0`、无 local name/scan response。回滚后 NVS byte-identical（`enabled=0`）、默认镜像自证、10s 目标 UUID 扫描为空。原始证据在枢纽仓 `results/local-only/2026-08-19/`（本机，不入库）。抓取期间 `minimal sram: 1647` 低于 §1.4.6 的 24KiB 最低堆门槛，但缺 BLE-off 同构基线对照，暂不能判定回归；完整共存门槛未通过。

阶段 A 仍未通过 §1.4.6 的 WiFi/Telnet 共存量化门槛，也未完成完整功耗/温升与阶段 B 认证 GATT；这些缺口不因阶段 A 广播验证而关闭。

任何后续社区、GitHub、官方文档、SDK 或硬件资料若影响本节设计，必须追加到本表或明确链接的 `docs/kxnx/` findings，记录来源 URL、版本/commit、访问日期、适用板型与范围、本地复核命令/结果、限制和未验证项。当前阶段 B/C 仍为规划；上述阶段 A 证据不替代后续共存、认证和 HIL 门槛。

本节是设计约束与分阶段实现记录，不是出货或完整硬件验收声明；阶段 B/C 仍须完成对应契约、共存门槛和 HIL 记录后才能改变 readiness 状态。

### 1.5 设备端零接触配网（S3 代配写字机，2026-08-22 起实现）

**2026-10-10 量产配对/中文SSID补充（本地候选，覆盖下文旧ASCII限制）**：工位在空白S3的NVS `hutuji_factory`写schema=1、SN、S3 STA MAC、Grbl STA MAC/AP BSSID。存在且有效时，Telnet候选先收banner再只读$I，完整STA报告末尾MAC匹配才进入连接/授权探测；错误或损坏身份拒连，不回落任意Grbl。未预置的旧机保留原发现兼容，产线放行必须有配对记录。AP扫描、嗅探及跳连均按登记BSSID过滤，不能用共享GRBL_ESP名称选邻机；嗅探继续保留原SSID与帧长度验证。设备状态新增factory_paired/serial_number/paired_grbl_mac供工位只读核验；配对不是密码学认证，不替代网络隔离与用户P-256绑定。

同批S3与Grbl配网校验接受1–32字节合法UTF-8（含中文），拒绝损坏编码、NUL/控制字符/代理区与超范围码点；密码长度和HTTP编码/错误应答判据不变。旧Grbl仍只接ASCII，须成对发布后再宣称中文网络可用。真实手机多机配网与首次空白板烧录尚需实测，见`design/production-readiness-20261010.md`。

**2026-10-10 PR10修复约定（本地候选）**：上述UTF-8支持指S3的HTTP配网路径；串口/Telnet输入仍有带内实时控制码，`grbl_wifi_setup`在开端口前拒绝非ASCII及`? ! ~`，提示使用HTTP配网，不修改Grbl实时控制分类。HTTP只规范化命令前缀，ESP参数中的首尾空格逐字节传到设置器；G-code保留原trim行为。工厂配对查询须按完整行同时取得匹配STA MAC与本次终止`ok`，消费终止行后才发布连接；缺失/截断终止行、错误、报警或重启均拒绝。单行512B、整次4KiB及原1500ms时间盒分别限制输入，后续正式授权探测不继承配对查询的应答。

目标：用户只给小派扫一次码（xiaozhi 官方配网），写字机的户网凭据由 S3 自动代配，全程不需要 PC 串口/网页操作。**Grbl 侧零改动**——利用 ESP3D WebUI 出厂 AP 的既有 `/command?plain=` 面。

- **触发**：户网 `Connected` 事件后 3s 启动巡检；另在工作台维护页提供「重新配置写字机」手动入口（同一状态机）。
- **流程**：先等既有管道发现 15s（已能找到写字机即退出，不打扰）→ 读回户网凭据并校验 → 双通道探测出厂热点 `GRBL_ESP`：**SSID 过滤扫描**（`cfg.ssid` 只赋值指针，禁 `memcpy`——指针成员空指针写已致崩溃循环；过滤让结果集不含户网 AP，防 esp-wifi-connect 的 WifiStation 抢 SCAN_DONE 无条件重连户网并清掉结果，2026-08-23 实测截胡）+ **混杂模式嗅探 beacon/probe response 兜底**（连接态过滤扫描的结果集实测可能漏收，嗅探不经过结果集，收到一帧即在场铁证）→ 不在场：自动场景静默退出，手动场景给话术 → 断开户网、裸跳连出厂 AP（`192.168.0.1`，口令 `12345678`）→ 逐条明文 HTTP GET 下发 `[ESP100]<ssid>` / `[ESP101]<密码>` / `[ESP110]STA` / `[ESP444]RESTART`（与量产串口脚本同序；NVS 设置重启生效）→ 回切户网 → 60s 内靠管道既有发现机制验证写字机上线。
- **安全口径**：暴露面 = 出厂 AP 存续窗口内本地射频可达者（与设备首次插电的既有状态相同，不新增远程面）；单次跳配 ≤60s 时间盒；任何失败 fail-closed 必回切户网；日志只记命令序号，**URL 与密码永不落日志**；应答判定 fail-closed——只认独立 `ok` 行或 `:ok` 尾缀，`error` 行优先，无 `ok` 即拒绝。
- **边界**：户网 SSID 须为 1..32 字节可打印 ASCII（镜像 Grbl WebSettings 校验边界），密码 0 或 8..64 字节；中文/非可打印 SSID 直接拒配并引导走 §1.3 手动网页配网。瞬态失败自动退避重试（60s/180s），上限 3 次后转人工话术，不再骚扰。
- **对既有行为的保护**：跳配窗口内 WiFi 掉线看门狗不武装（断连是流程一部分）；管道发现为跳窗歇工、巡检等待期与验证期照常；云端连接由应用层既有重连自愈。
- **状态**：host 契约测试 129/129 与双板（lichuang-dev / Waveshare 3.5）编译为软件证据；**2026-08-23 板上取证已通过**：出厂 AP 跳配全流程（嗅探命中→跳连 GRBL_ESP→4 命令→RESTART→写字机 STA 回连→S3 回切→管道接管→授权探测通过→巡检退出，全程 41s 无崩溃）与跳窗管道互斥（busy 歇工，`WaitingIp` 固定 1s 重试形态）均有双侧日志（`results/local-only/2026-08-23/hil-jump6-{s3-com14,plotter-com13}.log`，xiaozhi `4ce8b59`，板上 build-hil-ws35 镜像 SHA-256 `bb7ee987…32439`）；同版镜像的「管道已通则巡检退出」不打扰路径亦经 jump5 二次确认。中文 SSID 拒配话术仍只有 host 契约（需中文 SSID 户网环境，未板上取证）。

## 2. 就绪检测与会话重建

- **就绪判据（Telnet 版）**：连接候选先用主动 banner 验明 Grbl peer；正式连接后发 `$I`，
  收到版本行只进入下一探测阶段。必须完成 M5 抬笔、fresh 位置和授权零位移探测后才
  `ready=true`，此前不得启动 draw。
- **banner 语义 = 对端复位**：连接期间收到 `Grbl 1.3a ['$' for help]` 视为写字机复位
  （欠压等）→ 清任务应答并重新探活。当前任务通常置 error；仅 §4.1 标记的自发 abort reset
  进入受限恢复并最终置 `aborted`。
- **授权前置**：`[License] Authorized` 只发 `CLIENT_SERIAL`，Telnet 的 banner/`$I` 不可依赖。
  连接探活后先 `M5`、取一份新 `?` 位置，再在抬笔状态发送 `G1 G53 X<当前机器 X> F1500`
  零位移探测：**`G1` 必须是第一个 G 字**，因为商业固件授权前置门只检查行首 G0–G3；旧顺序 `G53 G1` 会绕过前置门，未授权运动在 GCode 内层被静默跳过后整行仍回 `ok`，从而误报已授权。正确顺序下 `ok`=已授权，`error:110`=未授权。探测结束前 `ready=false`，未授权见 §6。
- **解析器必须容忍**（异步混入字节流，均非应答）：`[MSG:…]`、`?` 触发的 `<Idle|MPos:…|Bf:…|FS:…>`、`ALARM:n`、`[VER:/[OPT:/[License]` 等裸 `[…]` 行。
  > 注意：`[MSG:[PaperAuto-N] …]` / `[MSG:[PaperStatus] N]` 这类**换纸消息在 §9 改动落地前不会出现在 Telnet**（见 §1.2）。解析器仍应容忍其格式（§9 落地后 `[PaperStatus]` 会到达）。

### 2.1 断连后的物理复位与半成品纸处置

**事实（源码 + 2026-07-28 实测）**：TCP 断连**不改变 Grbl 任何状态**——`TelnetServer`
断连路径不触发软复位/feed hold，planner 内已排队运动会继续。当前实机的 S3 自发 `0x18`
abort 会重放 banner，并进入需要 `$X` 的锁定恢复路径；因此不得再假设 reset 后可直接发普通行。
S3 只给“自己刚标记并发出的 abort reset”一次自动 `$X` 权限；其他复位、`ALARM:n`、
`error:9` 一律保持人工解锁。

**混合分流**（新连接只完成 Grbl banner 验真后、恢复授权运动探针之前，先按对端是否在换纸分两支）：

| 场景 | 判定方式 | S3 动作 |
|------|----------|---------|
| **普通画线行断连** | `[ESP901]` 查 `Changing=Off`（需 §9-B2′） | **2026-07-29 已实测到主路径**：受限 reset 恢复 → 确认抬笔/Idle/授权 → `M30` 走掉废纸（`[PaperStatus] 0`）→ 从 PSRAM 缓存开头重发（§5.1）→ 55/55 行完成并 fresh `Idle`；最终页尾自动换纸因无纸 `error:90`，完整任务仍非 `done` |
| **换纸窗口内断连** | `[ESP901]` 查 `Changing=On`，或本次断连前最后发出的是 M30/M721/[ESP910] | **不发 `0x18`、不发 `M30`**（`0x18` 会被 8s 窗吞、`M30` 会叠成二次换纸）；抬笔停机 + 推送提示用户，由人决定重画 |

- **`[ESP901]` 可达性已源码验证**（非推断，v0.5 勘察）：① `anyState()` 恒返回 `false`（`Settings.cpp:6-8`，守卫语义是「是否拒绝」）→ 任何 `sys.state` 下都不拒；② 换纸阻塞期 `protocol_poll_client()` 的换纸拦截（`Protocol.cpp:206`）**只拦 G0–G3**，`[ESP901]` 落到 `:227` 正常 `execute_line`；③ 响应经 `ESPResponseStream(client)` → `grbl_send(CLIENT_TELNET)` 回给发命令的 client（`ESPResponse.cpp:46,99`，且 `:55` 专为 Telnet 用 `\r\n`）。**故换纸期间 S3 发 `[ESP901]` 收得到含 `Changing` 的响应。**
- ✅ 已验证（2026-08-15 收口）：有纸时页尾自动换纸成功并最终 `done/Idle`（S4 2026-08-03 + 归位版 `4c60ca8`）、`Changing=On` 分流（B-13 2026-07-30 + §8.5 2026-07-31）、换纸物理耗时（多次实测，含归位版 M30→ok 16.5s）。🔬 仍缺：断连恢复 `paper_active` 窗口的 abort 复测、缺纸 error:90 路径（归位版）。
  半成品纸处置 2026-07-29 已实测主路径。非换纸 abort 自恢复通过不覆盖上述仍缺项。
- **v1 再做**：自动穿越换纸续接（在未完成的换纸上安全接续），v0 不赌机械安全。

#### 2.1.1 活跃任务重连恢复实现契约（S3 `05f4563` 起，`3ca9d31` 复测）

1. **范围/触发**：仅绘图会话内 TCP session 变化触发；初次建链仍走常规授权探测。
2. **接口**：`Pipe::SetTaskSessionActive(bool)` 保护会话（置 `false` 且未授权时同步清 `ready_`，
   禁止下一任务复用旧会话残留的 ready）；`Pipe::SendAbortReset(session, status_baseline,
   paper_baseline)` 是 `0x18` 的**唯一出口**，内部按 `CanSendAbortReset()` 六项与会话代号
   一次性令牌 `AbortResetToken`（`hutuji_recovery_core.h:680-683` 与 `hutuji_recovery_core.h:1190-1272`）判定，Arm 成功即先清
   `ready_/authorized_`；发送失败、断连、`CloseSocket`/`ShutdownSocket` 与新连接发布都必须
   `CancelAbortReset()`，banner 侧只能 `Consume(connection_seq)` 一次——跨连接的旧令牌一律失效。
3. **顺序契约**：重连只验 Telnet banner，保持 `ready=false` 且不发 `M5/G1`；`!` 之后必须先用 `?`
   取到**本次连接内新鲜**的停机状态行——`Idle` 或 `Hold:0`（判据 `IsStoppedForReset()`，`Hold`
   必须带子码 0 = holdComplete；`Run`/无子码 `Hold` 不算停稳），再发 `[ESP901]` 取本次新鲜
   `Changing`，只有 `Off` 才能发 `0x18`。reset 恢复、废纸 `M30` 与 fresh `Idle` 全过后，才从仍存活
   的 PSRAM 第 1 行重发，不重新下载。
4. **校验/错误矩阵**：`Changing=On/Unknown` → 禁止 reset/M30、停止自动续画；查询超时、
   reset banner 超时、未授权、废纸换纸失败 → 任务 error；任一步 abort → 不再续发。
5. **用例**：基准=无断连正常完成；好例=画线断连且 `Off` 后废纸并从头重画；坏例=
   `On` 时自动授权探针先发 G1，得到 `error:8` 后既无法 ready 也无法完成分流。
6. **必测断言**：bringup 8.3 断言 planner 被清、废纸被走掉、PSRAM 从第 1 行重发；8.4
   断言 `On` 时没有 `0x18/M30/G1`；8.5 断言未知/换纸保护窗内 abort 只挂起。
7. **错/对**：错=`重连 → $I/M5/G1 探活 → ready → 查 Changing`；对=
   `重连 banner → 查 Changing → Off 才 reset/探活，On 则停`。
8. **单 owner 事务**（原实现状态:工作树未提交、未构建、未烧录 → **2026-08-03 supersede：已提交 `6545ec9`、已构建并烧录 S3，551 行 S4 有纸实测通过**）：每个任务最多一个 reset owner
   ——`abort_reset_owner_.TryClaim()` 抢占，后到者走 `WaitForAbortReset()` 复用同一次结果，`busy_`
   在 `abort_reset_owner_.Complete()` 前不得释放（`hutuji_job.cc:959-1101`）；窗口模式的在途应答由
   `stream_window_active_` 表达,`wait_for_stream_quiescence` 路径必须等它清零才允许发 `!`，
   否则旧窗口迟到的 `ok` 会被计入 reset 之后的新流。

## 3. 流控（Grbl 行协议）

- **窗口化 character-counting 发送**（S3 `15f73c9` 起，S4 有纸实测；本节原「逐行等 ok、无 character-counting」表述 2026-08-14 起作废）：按字节计在途量，在途 ≤ `kStreamWindowBytes=512`（含每行换行符，`hutuji_recovery_core.h:15`，无行数上限），每收一个 `ok`/`error:NN` 释放一条在途行后继续灌；`error:NN` 按 §7 处理。窗口上界的依据：对端 Telnet `client_buffer` 仅 **2048 字节**（`WebUI/InputBuffer.h:55` `RXBUFFERSIZE`），且 `clientCheckTask` 写缓冲**不查可写容量**——饱和输入静默丢字节是上游已确认未修 bug（bdring/Grbl_Esp32 issue #983）；512B 占 25%，换纸阻塞期主循环停转、在途字节全数淤积也灌不满。奎享同法（窗口 100 字节，`docs/kxnx/findings/09-flow-control-deep.md`）。**禁止超窗预发**。
  - 暂停时停灌新行但**继续收在途应答**（在途行已进 planner，丢应答会让窗口计数永久漂移）；abort 先在途排空再走 §4.1 受控 `0x18`。
  - 窗口化普通行**不应收到 `error:8`**（换纸阻塞期 S3 自知停发 G0–G3，见下条）；防御口径是收到即整单失败（`hutuji_job.cc` 窗口化收分支），§7 的「原地重发同一行」只存在于换纸行路径（`Deferred` → 2s 后重发，上限 8 次）。
  - **窗口收到任意 `error` 必须先物理收敛再发布失败终态**：Grbl 官方 character-counting reservation 明确说明坏行虽被丢弃，已塞进 RX buffer 的后续行仍会继续解析执行。S3 收到 `Failed` 或窗口意外 `Deferred` 时立即发 `!`、清本地应答/在途窗口并发布 Quiesced，退出流循环后同步复用 §4.1 受控 reset 清 planner/RX；reset 完成前不得置 `error` 或释放 `busy_`。仅 `return false` 会形成「软件报错、机器继续画」的危险分叉。
  - **丢 `ok` 状态兜底必须绑定 fresh finite MPos**：通用状态序号只证明收到 `<...>`，不证明本帧有机器坐标。`$10` 可持久化为 WPos，`sscanf %f` 还接受 NaN/Inf；因此 Pipe 另维护只在完整有限 MPos 成功提交时递增的位置序号。释放整窗必须同时满足同连接的新状态序号、新位置序号、Idle 与目标容差；WPos/缺轴/尾缀/NaN/Inf 一律 fail-closed。
  - ⚠️ **错误码有两种线上形态，解析必须都认**：`$Errors/Verbose` 为真时 `Report.cpp:236` 把应答从 `error:11` 换成 **`error: Line too long`**（文本，取自 `Error.cpp` 的 `ErrorNames`）。出厂默认是 0（`Defaults.h:82`），但该 `$`-设置存在 NVS 里，能被任何上位机（奎享本体、ESP3D WebUI）改写并持久化。只认数字的解析器不是降级而是**行为反转**：`error:8`（换纸期推迟、需重发该行）解析成 -1 后走 `Failed` → 整单失败而非重试；`error:110`（未授权）同样落进 `Failed` 而非「已知未授权」。S3 侧解析实现见 `hutuji_recovery_core.h` 的 `ParseGrblErrorCode()`，两种形态由 host 编译单测钉住。
- **单行仅 ASCII 且 ≤ 254 字节 payload**（不含行尾换行符），非 ASCII 或超长回 `error:11`。MCP 生成端折行负责。
  - **是 254，不是 255**：`Protocol.h:34` 定义 `LINE_BUFFER_SIZE 256`，但 `Protocol.cpp:81` 的溢出判定排在换行判定**之前**——第 255 个 payload 字节能存进缓冲（`len` 变 255），紧随的换行符进来时先撞上 `len == LINE_BUFFER_SIZE - 1` → 回 `error:11`，且 `:218` 的 `empty_line()` 把整行丢掉，**该行从未进入 `execute_line()`**。即 255 字节的行不是「勉强执行」而是「整行被设备静默吃掉」。上游 grblHAL 把 `LINE_BUFFER_SIZE` 定为 257（`protocol.h:36`，注释 "256 characters plus terminator"）正是为绕开这个 off-by-one；Grbl_Esp32 未加 1，故本机可用 payload 是 254。校验器口径见 `gcode_gen.py` 的 `MAX_LINE_PAYLOAD_BYTES`。
- **CRLF 行尾会让设备每行回两个 `ok`；S3 必须重切行，禁止原样透传下载字节**。`\r` 触发一次 EOL（执行真实行），紧随的 `\n` 又触发一次 EOL，而 `execute_line()` 对 `line[0] == 0` 直接返回 `Error::Ok`（`Protocol.cpp:97-99`）于是再回一个 `ok`。CRLF 不是假想形态：奎享真实缓存产物就是 CRLF（946 CRLF / 0 裸 LF），Windows 上 `vpype gwrite` 走 `click.File("w")` 文本模式同样产 CRLF。当前 S3 安全是因为 `ParseLines()` 自己按 `\n`/`\r` 切行并跳过空行、`SendLine()` 只补一个 `\n`——**这是契约要求，不是实现细节**。一旦为省内存改成 zero-copy 直发 PSRAM 字节，每行多一个 `ok` 会让在途窗口计数持续正向漂移，表现为「越画越超发」。
- **换纸阻塞期**（数十秒）：该行的 `ok`/`error:90` 在**整个换纸完成后**才返回——这类行（M30/M721/[ESP910]）等 ok 超时 ≥ 60s（换纸行放宽到 90s）。此期间**禁发任何 G0–G3**（会被吞并回 `error:8`，见 §7）。换纸期间 Grbl 主循环不转，S3 周期性 `?` 不会收到状态行；Pipe 用 `expect_blocking_peer_` 标记换纸期，silent-poll 超时不掐链（TCP keepalive 兜底），换纸结束/任务收尾清回。
  - ⚠️ **进度反馈不能靠 `[PaperAuto-N]`**（§2：那些走 CLIENT_SERIAL，Telnet 收不到）。S3 判「仍在换纸」的手段只有：**自己发的是 M30/M721**（自知，正常出图期够用），或换纸期周期性 `?` 探活（走实时旁路、换纸期可响应，见 §2）。结果码 `[PaperStatus] N` 须靠 §9 的路由改动才能到达。
- 实时命令（`!` 暂停、`~` 恢复、`?` 状态、`0x18` 软复位）单字节立即生效，走实时旁路**不进 client_buffer、不吃 `ok`**（`is_realtime_command` 仅认这四个 + `≥0x80`）；`?` 的应答只回给发送 client。
- **`0x18` 特例**：换纸开始后 8s 窗口内被 `paper_should_ignore_host_reset()` 忽略（且「已忽略」提示只发 CLIENT_SERIAL，Telnet 收不到）；窗口后才中止换纸。`abort` 实现须注意（§4）。
- **单写者纪律（关键）**：S3 是 Telnet 唯一客户端（`MAX_TLNT_CLIENTS=1`），但其 `draw` 转发任务与 `status` 查询**不得并发写同一 socket**——否则 `M704`/`[ESP901]`（普通命令，吃 `ok`）会插进逐行流，打乱 `ok` 配对、或字节交错拼成 Grbl 认不得的行。全部 Telnet 写必须串行化（同一发送队列或一把发送锁）。
- **`!` 抢占例外（2026-08-02）**：普通行、`?`、`~`、`0x18` 继续持 `write_mutex_`；只有单字节 feed hold `!` 可绕过该锁。底层每次 `send(..., MSG_DONTWAIT)` 仅在持 `sock_mutex_` 时执行，因此 `!` 最多插在普通行的两次 send syscall 之间；Grbl 在任意字节边界先消费实时字符，不把它写入行缓冲，故不会拼坏 G-code。普通发送看到 `!` 等待者时让出，仲裁等待不计入 20s 活动停滞预算；正 partial write 不续期，预算耗尽、零进展或真实错误均半关连接并重建。该例外只解决「背压期间暂停最长被挡 20s」，不允许任何普通命令并发写。
- 空行、注释（`;` 与 `()`）由 S3 转发前剔除。

## 4. 任务协议（S3 设备侧 MCP 工具）

2026-09-24 并发修复候选：`hutuji.ota_start` 校验通过后、调度回调之前，必须与绘图/手动操作共用 `Job` 的 stream 锁及 busy 占位；重复升级、运动和停止/暂停/继续均不能抢占。外部 state 显示 upgrading，失败释放自身占位，成功持有到重启。源码与板上状态分开验收，见 [迭代修复记录](design/review-fixes-20260924.md)。

2026-10-09 OTA清理补充：成功分配的下载缓冲和SDK OTA句柄须覆盖读取失败、写入失败与异常退出的回收；`esp_ota_end`已释放条目后不得重复abort。该约定不改变镜像校验、启动槽切换或既有失败恢复行为。量产Grbl断线看门狗的时间戳只由handle任务读写，网络事件仅用原子标志通知连接恢复；保留既有15秒首重试/30秒重试间隔/10分钟持续断线重启和millis回绕语义。修复与板上状态见[三仓续审](design/three-repo-followup-review-20261009.md)。

**并发约束（勘察结论）**：xiaozhi 的 MCP 工具回调在**主任务同步执行**——禁止在回调里跑长任务。全部工具**异步化**：

| 工具 | 参数 | 行为 |
|------|------|------|
| `hutuji.draw` | `url,preview_url[,pages]` | 回调校验两个能力地址（`.gcode`/`.png`，缺一不可）→ 先只下载 PNG 上屏预览（零机械动作）→ 返回 `"previewing"`；**重入分两种**：预览流程中（`previewing`/`awaiting_confirmation`）以**完全相同参数**重入 → 幂等回 `"previewing"`（等价「你要的这张已在等确认」，为服务端链式 + 云端第二步双触发兜底）；参数不同或任务在预览流程之外（下载/灌流/换纸）→ 返回 `"busy"` 直接拒绝，不排队不抢占。用户点屏或调 `hutuji.confirm` 后才创建出图任务：**① 全量下载**（预览待确认期间后台预取 G-code，命中即复用；`Board::GetNetwork()->CreateHttp()`，`GetBodyLength()`/`GetResponseHeader()`/`Read()` 循环，模板见 `ota.cc` 的 `Ota::Upgrade`）→ **② 完整性校验**（§5.1，长度+CRC32）→ **③ 授权门**（§6，未授权则零字节下发）→ **④** 按行切分、Telnet 写、等 ok → **⑤** 等 fresh `Idle` → **⑥ S3 依次旁路 `G1G90 X0Y0F8000` 归位（G1 不触发换纸，ok 后等 fresh `Idle`）+ `M30` 换纸**，并等换纸最终应答与 fresh `Idle`。**不边下边转；归位行与 `M30` 都不在下载文件内**。`pages`（可选，多页文章）见 §4.2 |
| `hutuji.status` | — | 同步快速执行，返回 `{connected, ready, authorized, state, last_line, …}`。**OTA 增字段（2026-09-09）**：`firmware_version`（`esp_app_get_description()->version`，量产形如 `hutuji.x.y.z`）、`board`（编译期 `BOARD_NAME`，与固件 catalog 板型 key 一致，如 `freenove-esp32s3-display-2.8-lcd`）、`ota` 对象 `{state, progress, update_available[, reason]}`（缺省 `state=idle`/`progress=0`/`update_available=false`；`reason` 空则省略；升级态由 `hutuji.ota_start` 写入）。**增字段（2026-09-25，`e76cd54`+`8a9ec02` 已随 `hutuji.1.0.28` OTA 落板，当晚真画验证 done）**：`heap` 对象 `{internal_free, internal_largest, psram_free, psram_largest}`（纯本地读；internal_largest 即 TLS 堆预算判据，云端可先于故障看见碎片逼近）；**自愈**：TLS 堆预算连续 ≥2 次耗尽且落 error 时派生一次性自愈任务（PSRAM 栈），10s 宽限后复查（状态仍 error 且非 busy/OTA 预留才继续）→ Notify 用户 → 20s → `esp_restart()`，期间被新任务/OTA 接管即放弃；StartDraw 与预算成功清零计数：error 态输出 `last_error`（出图/预览链失败原因，与手动控制 `control_error` 并列；非 error 态或原因为空时字段不出现）；状态落 `idle`/`done`/`error`/`aborted` 终态即停出图计时，`elapsed_ms`/`eta_ms` 不再按旧起点递增（当日事故现场曾见 2.27h 陈旧计时误导排查）。**分级**：出图进行中（streaming/paper_change）**只发 `?`**（实时旁路，不吃 `ok`）；**仅空闲时**才发 `[ESP901]`（纸/电机/PanelHold/Changing）+ `M704`（GPIO34）——这两个是普通命令会吃 `ok`，出图中发会打乱流控（§3 单写者）。**实现状态（2026-08-24 对账）**：空闲期 `[ESP901]`/`M704` 遥测**契约已定、实现待做**——当前 `StatusJson` 只发 `?`，`M704` 全仓零命中，`Paper=/MotorEn=/PanelHold=` 未解析；补实现见 release-readiness 2026-08-24 条（P1-1 批次） |
| `hutuji.ota_check` | — | **只查不升**（2026-09-09）：GET 自建仓 `{public}/firmware/s3/{BOARD_NAME}/latest.json`，比较 `hutuji.x.y.z`；写 `status.ota.update_available`；返回 `{update_available,current,latest,notes,reason}`。每天 idle+WiFi 自检复用同路径（NVS 日历日，失败不打扰）。 |
| `hutuji.ota_start` | `url,version,board[,sha256]` | 量产远程升（2026-09-09；HIL 后口径）：Job **settled**（`idle`/`done`/`error`/`aborted`）且非进行中的 `kDeviceStateUpgrading`、`board==BOARD_NAME`、HTTPS host ∈ 允许列表时受理；出图/预览/换纸等非 settled → `{reason:busy}`。`UpgradeFirmware` **失败必须**回到 Idle（否则会永久 busy——1.0.1 曾踩坑，1.0.2 起修）。`sha256` 契约对齐、设备侧未核。成功 reboot；失败 `ota.state=failed`。**禁止**自检自动调用。 |
| `hutuji.abort` | — | **分状态**（§4.1）：下载/校验态只置取消；非换纸流式态停喂后排空、抬笔、归位（Hold 先 `~` 退出，正常路径无 `!`/reset）；换纸活动态**不发 `0x18`**，回报「换纸中无法即停、完成后即停」+置 abort 挂起。重复停止复用同一收尾事务；归位 worker 收敛前不发布 `aborted` |

**OTA `reason` 稳定 token（2026-09-09，与设计 F1–F8 / portal `assert_upgrade_allowed` 对齐；字符串勿随意改名）：**

| token | 含义 | 典型触发 |
|-------|------|----------|
| `ok` | 成功受理 / 检查无错 | `ota_start` 已调度；或检查路径成功且有结论 |
| `up_to_date` | 已是推荐版本 | `ota_check` 比较后无更新 |
| `check_failed` | 查版本失败 | DNS/TLS/4xx/5xx/坏 JSON（F1） |
| `download_failed` | 下载中断 | 中途断网、超时（F2；**未切启动槽**） |
| `verify_failed` | 镜像校验失败 | 长度/魔数/校验（F3；**未切启动槽**） |
| `write_failed` | 写入 flash 失败 | flash 错误（F4；**未切启动槽**） |
| `busy` | 忙拒升 | 非 idle / 已在 upgrading / 出图预览换纸配网（F5） |
| `board_mismatch` | 错板包 | `board` ≠ `BOARD_NAME` 或 catalog 板型不符（F8） |
| `rolled_back` | 新槽起不来后回旧槽 | PENDING_VERIFY 未 mark valid → 回滚（F6；**发版门槛 HIL，本批真机未验**） |

> `status.ota.reason` 空则省略该字段。门户/小程序文案映射：`busy`→「出图结束后再试」、`board_mismatch`→「固件与机型不符」等。故意坏包回滚 HIL 未做前**不得**写「升级绝对安全」。
| `hutuji.confirm` | — | 屏幕预览确认出图（「开始画/可以/就这个」）；仅 `awaiting_confirmation` 态有效，等价于点屏幕「开始画」钮 |
| `hutuji.pause` | — | 暂停当前绘图（可恢复）；停笔不发 `0x18`，流式态由 Job 层挂起投喂 |
| `hutuji.resume` | — | 恢复之前暂停的绘图，继续 PSRAM 缓存的行流 |
| `hutuji.repeat` | — | 用 §5.1 PSRAM 缓存把上一张再画一遍（不重新下载） |
| `hutuji.pen_test` | — | 笔测试：落笔停 1 秒再抬笔；不动纸、不进换纸路径 |
| `hutuji.manual` | `action` | 语音手动控制（2026-08-24 新增，复用屏幕手动面板机制）：`jog_x+/jog_x-/jog_y+/jog_y-` 按步距点动（`jog_step_1/jog_step_10` 切 1/10mm，NVS 持久）、`pen_up/pen_down` 抬落笔（落笔先 `G92 Z0` 校准再 `Z5.0`）、`home` 回原点（`G1G90 X0Y0F8000`，不触发换纸）。仅 settled 态（idle/done/error/aborted）可用，期间状态 `manual`，完成设备播报；点动前强制新鲜 `?` 坐标 + `DecideJog` 包线判定（与 §5 限幅同源 277/190，fail-closed）。语音白名单拒绝维护动作 `set_origin/unlock/motor_off/reset`（仅屏幕入口） |
| `hutuji.sing` / `hutuji.stop_song` | `url,title` / — | 唱歌（与绘图解耦），契约见 §10.2 |

- 任务完成/失败经 `Application::SendMcpMessage` **设备侧主动推送**；同步查询走 `hutuji.status`。
- 状态机：`idle → previewing → awaiting_confirmation → downloading → verifying → streaming ⇄ paused → (paper_change) → done | error | aborted`（预览确认链为当前唯一形态）；TCP 断开/banner 重放 → `error`（按 §2 走物理复位分流）。
- **完成判据**：最后一行的 `ok` 只表示该行进入 planner。S3 必须先等到一份 fresh `Idle`，
  再依次旁路单发 `G1G90 X0Y0F8000`（归位，G1 不触发换纸，ok 后等 fresh `Idle` 确认归位走完）
  与 `M30`（换纸）；`M30` 返回 `ok` 后还要再次取得 fresh `Idle`，此时才能置 `done`。
  不得用旧缓存 `Idle` 或固定 sleep 判完成。~~该路径已编码，尚未完成有纸实机验收~~ **【supersede 2026-08-15】** 有纸全链已实测通过（S4 玫瑰图 2026-08-03 + 归位版 `4c60ca8` 2026-08-15，见 release-readiness.md）。
- **同一时刻只允许一个任务**；任务期间 G-code PSRAM 缓存（§5.1）持有至任务终结。成功且换纸完成后
  保留一份供 `repeat` 复用，error/aborted 在任务收尾释放。**所有权缺口（2026-08-23 源码审查）**：预取命中（`AdoptPrefetch`）时旧 `buffer_` 直接被新指针覆盖而不释放；且预览下载成功但预取失败/中断时无 `CancelPrefetch` 补偿——重复「新画一幅」会持续累积泄漏（≤512KB/幅），修复待独立批次并补 host 测试，详见 `release-readiness.md` 文首审计补充。
- 代码挂点：`lichuang-dev` 板级 `InitializeTools()`；**板级行为不进核心模块**。

### 4.1 abort 语义（重要：不是万能急停）

**物理限制：换纸一旦启动，abort 打不断它。** `paper_auto_change` 是 ≥60s 的阻塞流程，固件**故意**在开始 8s 窗口内忽略 `0x18`（`paper_should_ignore_host_reset()`）以保护换纸不被中途打断；且「已忽略」提示只发 CLIENT_SERIAL，S3 **收不到任何失败回执**。

故 abort 语义定义为「**尽快中止**」而非「立即中止」：

| 时机 | S3 行为 | 回报用户 |
|------|---------|----------|
| 下载/校验态 | 只置取消标记，不向 Grbl 发 reset | 「已中止」 |
| 非换纸流式态 | 停喂 →（Hold 则先 `~` 退出）→ 在途排空至 fresh `Idle` → Z0 抬笔 → `G1G90 X0Y0F8000` 归位（**无 `!` 无 reset**，坐标全程不失效）；`~` 清不动 Hold 的极端情形才回落既有受控 reset | 先回「已受理」；完成后播「已停止并回原点」（归位失败播「已停止」） |
| 换纸活动态 | **不发 `0x18`**；置 abort 挂起，停止后续 G-code 下发 | 「写字机正在换纸，无法即停；换纸完成后任务将停止」 |

- 换纸挂起态下，等该行 `ok`/`error:90` 返回后**不再发任何后续行**，任务落 `aborted`。
- **停止收尾幂等（2026-09-13 实机回归）**：第一次停止提交后，重复请求回「停止处理中」并复用同一 worker；worker 创建口原子抢占，归位/失能应答只由一个收尾者消费。`aborted` 与屏幕「已取消」必须等 worker 收敛后发布，再释放 busy。量产机归位成功还须按 §10.4.15 确认 `$MD`；不能看到软件 `aborted` 就推断机械已经停止。
- 非换纸 reset 后的受限恢复顺序固定为：`$X` → `G1G90 Z0.0F10000` → 等一份新的
  `Idle` → `$I` → `M5` → `?` → `G1 G53 X<当前机器 X> F1500` 授权探测；`G1`
  必须置于 `G53` 前以命中 Grbl 行首授权门。只有 `PrepareAbortReset()` 标记对应的下一次 banner 可走 `$X`；任何其他 Alarm 不自动解锁。
- **不能把抬笔放在 reset 前排队**：`!` 后普通 G-code 只会进入 planner，`0x18` 又会丢弃它。
  实机还证明 reset 后单发 `M5` 可能因主轴模态已为 Off 而成为无动作，故先显式 Z=0。
- Z=0 行的 `ok` 仍只代表进入 planner；必须等 fresh `Idle` 再发 `$I`，否则运动未停时会回
  `error:8`。2026-07-28 回归最终为 `aborted/Idle`、Z=0，8 秒无 XY 漂移或 planner 续跑。
- **停止后自动归位（2026-08-28 用户决策「停止后也要自己回原点」；同日两轮 HIL 实证定稿）**：非换纸流式态 abort 分两路——
  - **纯流式（多数）**：不发 `!` 不发 `0x18`——停喂新行后在途行（≤512B 窗口+RX，十数段）自然排空到 fresh `Idle`，再 `G1G90 Z0.0F10000` 抬笔、`G1G90 X0Y0F8000` 归位、等 fresh `Idle`。全程无 reset，坐标不失效，规避「reset 清坐标 → 归位变空操作」与「Hold 不消费行命令」两个坑。
  - **先暂停后停止（Hold 态）**：先清 S3 侧 `paused_` 冻结闸（不清则流循环永停在收应答冻结、收口永不发布、任务悬挂——2026-08-28 二轮 HIL 实证），再发 `~` 退出 Grbl Hold 让残余在途段排空（用户已放弃本张，多画几笔无碍），随后与纯流式同一序列；`~` 清不动 Hold（3s 上限）或收口超时（5s）才回落受控 reset（其内仍带 Hold 快照 G92 复原归位）。受限 reset 不再出现在用户停止主路径，只保留给错误恢复/暂停超时/兜底——当日 HIL 实证 abort 后 `[ESP901]` 无应答会打崩 socket（2/2），用户路径绕开该步。
  归位失败不翻盘（机器停稳是主结果），仅分流播报「已停止并回原点」/「已停止」。换纸活动态与断连恢复路径**不适用**：前者不发 `0x18` 不动机械、后者只走固定恢复序列，两条禁令不因本条松动。
- ⚠️ 上层（云端 LLM / 用户提示语）**不得把 abort 描述为急停**，否则换纸中喊停无反应会被误判为系统故障。

### 4.2 多页文章 `pages`（2026-09-01，写字链，不升协议号）

云端 `hutuji_write`（奎享单线字库排版）一次产出 N 页产物，`_chain_device_draw` 把整份页表
**一次**推给设备侧 `hutuji.draw`；设备逐页写、每页写完自动换纸再写下一页。协议要点：

- **传输形态**：xiaozhi MCP `Property` 只有 `boolean/integer/string`（无 array），故 `pages` 是
  **紧凑 JSON 字符串**：`[{"url":"…gcode","preview_url":"…png"},…]`。键序固定 `url` 先、
  `preview_url` 后——设备侧 `hutuji::ParseArticlePages` 是纯扫描器（不引 cJSON，宿主测试单 TU
  零依赖），按此序配对。
- **校验（fail-closed，任一不合整体拒收）**：每条 `url` 过 `IsValidDrawCapabilityUrl(.gcode)`、
  `preview_url` 过 `.png`（§5 能力地址边界不放宽）；成对出现；同一页的 `url` 与其 `preview_url`
  之间不得再出现 `"url"` 键（防跨页错配把 A 页 G-code 配到 B 页预览）；值内出现 `\` 即判非法；
  条数 1..`kArticleMaxPages=20`（与云端 `write_text.max_pages` 同值）。
- **`pages[0]` 必须等于 `url`/`preview_url`**：否则屏幕预览与实际所写不是同一页。云端两个字段
  恒同时发（含单页），设备侧不猜。
- **两步回落透传（2026-09-03）**：链式推送未配置/失败时云端 LLM 自调 `hutuji.draw` 是合法
  回落，但设备幂等重入要求**完全相同参数**——LLM 从返回值重组 `pages` 几乎必然产出不同
  字节（多字段、键序、空白）而被 busy 拒绝。故 `hutuji_write` 返回值含 `pages_arg` 字段：
  与链式推送同一序列化点（`_pages_argument()`）产出的紧凑字符串，回落时**一字不改**透传
  为 `pages` 参数；未配 device token 时该字段也在（正是它服务的场景）。
- **语音播报引导 `note`（2026-09-06）**：`hutuji_draw` accepted 返回、`hutuji_write`/`hutuji_teach`
  完成返回新增 `note` 字段，与三者工具 docstring 注入同一条引导——给用户的回复只说一句简短确认、
  不复述产物细节。依据：设备侧预览下载有 `WaitForAudioOutputIdle` 门（功放+WiFi 叠峰防 VSYS 掉压，
  2026-09-06 HIL 定性），LLM 播报越长预览出现越晚（17:22 实测：复述细节后 preview-on-screen 40s+）。
  设备侧不消费该字段；现有测试按键取值、不钉键集，1243 项全绿不受影响。
- **页循环**（`Job::Run` 单一 while）：`ChangePaperAfterDraw()` **成功之后**才推进页索引，然后
  释放本页 PSRAM 缓冲 → 下载下一页 → §5.1 校验 → 灌流 → 归位 → `M30`。任一页失败/abort 即整单
  终止（不跳页续写）。预览只出第 1 页；预取只可能命中第 1 页，后续页 URL 不同自然回落直下载。
- **`hutuji.status` 增 `page`/`pages`**（1 基当前页 / 总页数）；单页模式两字段**不出现**。
- **错峰 TTL（2026-09-01 落地；2026-09-03 改为按页行数估时，云端单侧实现，设备侧不改）**：设备逐页「下载→灌流→换纸」**串行**推进，
  第 i 页要到 `t0 + Σ(前序页实际耗时)` 才被下载。页耗时按行数折算：实机实测 **8.8 行/s**（1021 行灌流 116.3s）+ 换纸 8s；
  注意 12mm 满页实测 10597 行 ≈ **20 分钟**（2026-09-01 服务端取证 `twopage_run`），原「一页 ≈125s」是短文本单页样本，不代表满页。
  若 N 页共用同一个 `output_ttl_seconds`（600s）窗口，第 6 页 404——**服务端 HTTP 层已实证**（`results/local-only/2026-09-01/ttl-stagger-http-counterfactual.log`：真 uvicorn + 真 `GET /files/`，第 i 页 mtime 倒推到 age=i×125s，不错峰组 1–5 页 200 / 第 6 页 404，错峰组 6 页全 200）；**设备侧真机时序仍待 HIL**。故云端
  `_run_write_job` 在 `finalize_job(created_at=…)` 把多页文章**各页（含第 1 页；单页文章不动、保持写盘时刻语义）**产物的 created 时刻
  前移到 `t0 + 600 + Σ_{j<i} est_j`，`est_j = (lines_j / 8.8 + 8) × 2`（常量同源 `hutuji_mcp/article_pages.py` 的 `WRITE_PAGE_LINES_PER_SECOND`/`WRITE_PAGE_CHANGE_SECONDS`/`WRITE_PAGE_TIME_MARGIN`/`WRITE_PAGE_CONFIRM_ALLOWANCE_SECONDS`
  ——2026-09-04 起从 server.py 下沉为独立叶子模块（server.py 保留同名 import 别名），portal 文档导入错峰共用；钉实测证据，不走 env；×2 余量覆盖设备重试与用户暂停）——旧版统一 300s/页预算（env `HUTUJI_PAGE_TTL_BUDGET_SECONDS`）建在 124s/页模型上，
  满页文章第 2 页必 404（2026-09-03 复核数字实锤），已拆除。**一次性确认补贴 600s**（2026-09-03 advisory 复核追加）：`t0` 是云端生成完成时刻，
  第 1 页要等人在屏幕上按「确认」才开画，这段等待无上界且与页大小无关——稀疏两页 est₁≈248s 时确认延迟 >12 分钟第 2 页必 404；补贴只加一次不按页乘
  （确认延迟本来就只发生一次），含第 1 页是因为确认延迟卡住的是第 1 页首次下载本身。
  于是第 i 页有效窗口关闭于 `t0 + 600 + Σ_{j<i} est_j + output_ttl_seconds`（满密度 20 页末页窗口十余小时——这是物理总时长（≈7h）加余量的必然映射，URL 为 128-bit 不可猜 capability，窗口长度不改变其不可猜性）。
  **单页文章不动时间戳**——窗口仍从写盘时刻起算，单页语义逐字节不变（确认延迟超过 TTL 导致单页 404 属既有行为，确认屏当场可见、可即时重试，与画到一半的后续页 404 不同级）。
  本地后端靠 `os.utime` 改 mtime（svg/gcode/png 三件同改）；OSS 后端写
  `x-oss-meta-hutuji-created`，`read_verified`/`cleanup_expired` 都以该元数据为过期真值
  （`Last-Modified` 恒是上传时刻，只看它会误删靠后页）。文件名格式**不变**
  （设备侧 `IsValidDrawCapabilityUrl` 正则硬编码，不需固件配合）。
  副作用：`hutuji_status.latest_ts` 在多页写字任务后可能显示未来时刻（age 为负），属已知记录性偏差。
- **pages 的第二生产者（2026-09-04，draw-portal 文档导入）**：portal 传图页接受 Word/Excel/PPT/CSV/TXT——docx/pptx/txt 抽文字走
  write_text 文章链「写出」，xlsx/csv 走 table_layout 表格链「画出」（格线+格内字同页笔画，格线是 `<path>` 折线非 `<line>`，同过 §5 闸）。
  每页独立 capability pair（`draw_…_<token>.{gcode,png}`，`_DRAW_FILE_RE` 不变），发送时 portal 用同一 `pages_argument()`（article_pages.py，键序/紧凑格式单源）
  组 `pages` 参数整篇下发；错峰 TTL 用同一 `page_ttl_offsets()` 按页行数估时。设备侧零改动——门户多页与语音写字多页共享本节全部契约。
  2026-09-04 补：docx 标题样式（w:pStyle 内置 Heading/标题/Title）与 pptx title 占位符检出的标题段**18mm 放大落墨（占 2 正文行带）+ 逐视觉行居中 + 免段首缩进 + 段前后各空一行**；
  docx 列表经 numbering.xml 还原编号前缀（decimal/字母/罗马/中文计数/bullet，嵌套按 Word 浅级重置语义，仅前 3 级）。
  OOXML 所有已读取 XML 部件（含编号、关系、共享字符串）均禁止 DTD/实体声明：字节预检之外，解析器须在识别 DOCTYPE 时、处理内部子集与正文前拒绝，不能只扫描 ASCII 字节或依赖展开后的字符预算。无 DTD 的正常 UTF-16 文档保留支持。
- **`hutuji.repeat` 语义**：文章重画 = 整篇从第 1 页重来（禁用 PSRAM 快路——留存的只是最后一页），
  各页在上述错峰窗口内有效；过期按下载失败如实报错。

### 4.3 笔顺教学链 `hutuji_teach`（2026-09-05，复用 §4.2 全部契约，不升协议号）

云端 `hutuji_teach`（标准笔顺教学：hanzi-writer-data@2.0.1 / Make Me A Hanzi 的 `medians`
**每笔中心线折点**——PRC 规范笔顺人工校订、天然单线、零描摹零骨架化；APL 许可商用可）
把汉字排成教学页并与写字链同一产物面/错峰 TTL/链式推送/`pages_arg` 同源（数据根
`HUTUJI_STROKE_DIR`，空值=工具禁用；设备侧零改动）。**版式（2026-09-11 用户拍板「多字集中到
单张纸」）**：单字 = 首行成形大字 + 田字格渐显面板（第 k 格 = 前 k 笔 + 中文步序数字）；
多字 = **单页字帖**——2 列成形田字格、每格全字 + 逐笔起点旁中文序号（格 56→24mm 自适应，
超 18 字/页才续页；`max_pages` 与单字路径同口径）。

## 5. G-code 文件契约（MCP 服务产出）

- **按设备的纸张快照（2026-09-14，§10.4.24）**：新增配置只改变本次产物的撰写尺寸、XY 映射和已核对的机器包线；`paper` 默认仍为 X277/Y190、`nopaper` 默认仍为 X205/Y290mm。选择 A3/A2 不自动增大行程，输入上界 2000mm 不是机器实测证据。动态产物必须携带内容绑定的 `.paper.json`，`/files` 按该份配置复核全部直线与圆弧包围盒，返回 `X-Hutuji-Paper`；新 S3 在发送首行前核对本机配置标记。元数据在场但损坏一律拒绝；无元数据的旧产物仍仅接受原有两个固定档，禁止用任意大包线兜底。下列 F/Z、文件大小、指令白名单与毫米容差保持原契约。
- **下载文件自包含**：生产 profile 文件头固定为精确的 `G21`（毫米）、`G90`（绝对）、`F24000`（2026-09-12 提速批：XY 生产进给由旧契约 F8000=133mm/s 提为 F24000=400mm/s，用户拍板「xy 轴提速 3 倍」；机器侧同批配套 `$110=$111=24000`、`$120=$121=12000`，回读证据见 `docs/deployment.md`；**2026-09-24 supersede（量产 nopaper 本机）**：用户调速后 `$110/$111=4500`，XY 加速度按厂商 P24/P25 参数参考显式降为 **`$120=$121=3000`**（仓外素材快照见 deployment.md 当日条目），文件契约 F24000 不变），随后
  连发两次 `G1G90 Z0.0F10000`。这两行是与奎享缓存兼容的幂等抬笔运动，**不能建立 Z 坐标基准**；
  校验器不接受注释或 `G210`/`G900` 等子串伪造，也不允许运动发生在 G21/G90 建立之前。
- **完整出图会话的 Z 基准**：奎享本机 `$1=25`，电机失能后弹簧回到自然抬笔位，但 Grbl 计数不会
  跟随机械回弹；因此每次首画/重画前，S3 必须先确认 fresh `Idle`，等待 100ms（大于 25ms），再以
  **受控旁路命令**发送固定 `G92 Z0`，随后才窗口化连续发送下载文件。下载文件无 `G92` 与会话旁路
  有 `G92 Z0` 不矛盾；`G92` 仍禁止进入下载文件。**呼图机（2026-08-28）**：机型默认
  `DEFAULT_STEPPER_IDLE_LOCK_TIME=255`（`$1` 常使能），COM13 已刷；与奎享 `$1=25` 分叉。
  `$RST=$`/换板现回落 **255**。云端另以落笔 G1≥1.0mm 地板减轻亚毫米链空窗。不改下载文件内的
  `$`/`Z5` 字面量。
- **量产任务收尾静音（2026-09-13 用户授权）**：仅 `nopaper` 在整单完成或停止归位时，先显式 `G1G90 Z0.0F10000`、ok + fresh Idle、100ms 弹簧沉降，再 `G1G90 X0Y0F8000`、ok + fresh Idle，最后旁路 `$MD` 并消费其应答。`$1=255` 保留，绘图/暂停/续页不释放；下一次运动由 Grbl `st_wake_up()` 自动使能，新任务仍先重新声明 `G92 Z0`。`$MD` 不进入下载文件；手动归位与试笔沿用原入口，手动失能仍走维护按钮。发送在单写者锁内检查同一连接、reset 横幅、新状态序号、Idle、nopaper 与授权/设置通过，失败不得声称释放；详见 §10.4.15。
- **避开隐式换纸触发点**：
  - 软复位后**第一条** `G0 X0 Y0 Z0`（首页触发）
  - `G28` / `G30`，以及执行后工件坐标 XY≈0 的 `G0`（回原点触发，含 `G0 X0 Y0` 收尾！）。
    若 G0 的模态 XY 实际终点无法完整解析，也从严拒绝，因为不能证明不会落到 `(0,0)`。
  - **结论：分页节奏必须显式控制**——MCP 产出的文件以抬笔结束并停在当前位置，**不含**
    `M30`/`M721`，也**不含归位行**；收尾**不要**用 `G0 X0 Y0`。页尾编排由 S3 在确认绘图
    fresh `Idle` 后依次旁路单发（2026-08-14 归位决策）：先 `G1G90 X0Y0F8000` 归位——
    **必须用 G1**，固件「回原点后换纸」触发仅认本行实际执行的 `G0`/`G28`/`G30` 且落点
    XY≈0（GCode.cpp 页尾分支），`G1` 落 `(0,0)` 不触发，归位因此留在可即停的常规行里，
    不进 90s 不可即停的换纸窗口——归位行 ok 后再单发 `M30` 换纸。断连恢复的废纸换纸
    **不归位**（受限恢复序列固定，见 §4.1）。
- **生产文件抬/落笔 = 纯 Z 运动**：`G0 X...Y...F24000` 空走到笔画起点，
  `G1G90 Z5.0F10000` 落笔，`G1 X...Y...F24000` 画线，`G1G90 Z0.0F10000` 抬笔；
  文件内不含 `G92`、`M3/M5`、`G4`。Grbl 的 `USE_M3_M5_AS_PEN_UP_DOWN` 仍保留，
  M3→Z=5/M5→Z=0 已用于手工与恢复路径验证，但**不是生产 profile 的出图指令**。abort reset
  恢复仍必须用明确 `G1G90 Z0.0F10000`，不能依赖 reset 后的 M5 模态变化。
- **坐标范围与越界策略（拒绝，不裁剪）**：实机 A4 行程 **已由 2026-08-29 实机 `$$` 取证确认**为 `$130=210.000`、`$131=297.000`（`$132=200.000`），且**无限位开关**（`$20=$21=$22=0`，归位/软限位均不可用）；越界可能直接撞机，限幅责任 100% 在 MCP 生成端。
  - **实机 `$$` 取证（2026-08-29，只读）**：CH340 `COM13` 上以 `tmp/query_grbl_settings.py` 仅发 `$I`/`$$`/`?`（`dtr`/`rts` 恒 `False`，不复位、不运动、不写设置），耗时 8.19s，落盘 `tmp/grbl-settings-com13-0829.txt`（`tmp/` 未入库，已登记 `docs/worktree-inventory.md`）。固件自证：`[VER:1.3a.20211103:]`、`[OPT:PHBSW]`、`[MSG:Using machine:Custom 3-Axis HR4988]`、`[MSG:Mode=STA:SSID=…:Status=Connected:IP=192.168.1.13:…]`、`[MSG:No BT]`；关键设置 `$1=255`（步进常励，配合 §5 短段地板）、`$3=7`、`$100=$101=100`、`$110=$111=12000`；状态行 `<Idle|MPos:0.000,0.000,0.000|Bf:249,128|FS:0,0|Pn:0>`（`Pn:` 空 = 无任何限位/探针触发，与无限位开关一致）。
  - **纸面方向（口径冲突已定性）**：2026-08-21 用户口径称 A4「X 为长边（297）、Y 为宽边（210）」，**与实机固件相反**——`$$` 实测 X 行程 210、Y 行程 297。故该口径**不作为行程真值**，只可能是机械安装/放纸方向的描述；**任何据此对调 `$130/$131` 或抬高 X 限幅的改动一律不做**。
  - **出图方向（2026-09-06 晚 B 案定案，supersede 当日下午 flip=true 与 09-04 flip=false）**：**竖版撰写页 190 宽 × 277 高 + G-code 层转置**（`gcode_gen._transpose_gcode_xy`：塌缩后、弧拟合前逐行互换 X↔Y，机器 X:=撰写 y、Y:=撰写 x；开关 = toml `transpose_xy = true`，`vertical_flip` 回 false）。用户拍板「不要横着写，要竖着写」——墙上竖装须站直横读，拒受 A 案（flip=true）的歪头阅读。实机方位（当日 teach「上」字页用户纸面双 bit 实测）：机器 X+ 朝房间下、Y+ 朝房间右、原点墙面左上——转置后撰写 x→房间右（从左往右写）、撰写 y→房间下（行序自上而下）、页左上角起笔落原点角，用户「从原点开始从左往右写」三条同时成立；撰写系内转置是反射（det −1），与竖装奇象限相消成正常横读（180° 旋转 det 不变治不了镜像，当日下午已验算）。同步面：`gcode_gen.PAGE_AUTHOR_WIDTH_MM/PAGE_AUTHOR_HEIGHT_MM`（撰写页 190×277）与 `MAX_X_MM/MAX_Y_MM`（物理限幅 277/190，R17-XREPO-01 互钉不动）分列；write_text/stroke_order/table_layout 版式常量；画图链 vpype layout 改适配竖版（页宽<页高时**不得**再加 `--landscape`）。**机器改摆放姿势（平放等）本定案作废，`transpose_xy` 须重新取证**。回归钉 `test_svg_to_gcode_transposes_xy_for_wall_portrait` + profile 字面量钉（transpose_xy=true 且 vertical_flip=false）。
  - **轴角色定案（2026-09-04 用户实机确认）：X 为长边（297 方向）、Y 为宽边（210 方向）**——与用户 08-21 口径一致。`$$` 读到的 `$130=210/$131=297` 是厂商配置值，与物理轴角色相反：本机无限位开关、软限位关闭（`$20=0`），配置行程不进任何安全链，MCP 限幅是唯一防线，故不对调 `$130/$131`（无安全收益且 §9-G′ 锁表只读）。同日中午曾按 `$$` 数值把 277 放到 Y，用户实机判定后当日反转到 X——**轴向争议以用户物理观察为最终裁决**。
  - **X 上限 190→277、Y 维持 190（2026-09-04 定案）**：用户纸面实测「长度没用满」拍板。X 277=297−2×10 边距。同步面：`gcode_gen.MAX_X_MM`（vpype layout 须 `--landscape`，其默认强制竖版会把 277x190 换成 190x277）、write_text `_PAGE_WIDTH_MM`、grbl_sim `_MAX_X_MM`、xiaozhi `kJogEnvelopeMaxXMm`（R17-XREPO-01 互钉）。注意：板上 S3 固件点动包线仍 190，重刷前点动到 190–277 区段会被固件拒——语音/文件出图不受此限。再放宽任何一轴须重新取证并同步点动包线。
  - **可用画幅（paper 默认；动态配置见 §10.4.24）**：≤ **277×190mm**，坐标须落在
    `0 ≤ X ≤ 277`、`0 ≤ Y ≤ 190`。
  - **越界处理 = 拒绝生成 + 明确告知用户**，**不得裁剪 / 不得缩放后静默出图**：任一坐标超界 → MCP 不产出文件，向云端回明确原因（超界轴 + 实际值 + 上限），由用户决定改小内容或换尺寸。
  - 理由：裁剪会把「越界」变成一张**静默残缺**的画（与 §5.1 静默截断同类失败——用户拿到看似完成实则缺一块的纸）；拒绝把失败前移到落笔之前，**零废纸**。自动缩放同理否决：改变用户预期的构图属于静默篡改。
  - MCP 生成端应在**输出前**统一做一次全量坐标扫描（含圆弧 `G2/G3` 的包围盒，不只是端点），越界即整单拒绝。
  - **实现状态（2026-07-26）**：`validate_gcode()` 的限幅覆盖 `G0/G1` 端点与 `G2/G3` **包围盒**（`_arc_bbox`）。包围盒取「起点 + 终点 + 落在实际扫过角度区间内的象限点」的极值——只算扫过的象限点，否则会把整个外接正方形当边界而误拒合法弧。圆心求解复刻 Grbl `GCode.cpp`：IJ 偏移模式圆心 = 起点 +(I,J)（`:1266`），R 半径模式解 `h_x2_div_d`（`:1216-1251`）且**负 R = 走 >180° 的大弧**（`:1245`）；IJ 模式另按 Grbl `delta_r > 0.5mm` 校验起终点半径自洽（`:1272-1279`）。**正 R 模式下弦长 < 0.5mm（`_ARC_MIN_CHORD_MM`）的近整圆歧义弧一律拒**（2026-08-12）：起终点近乎重合时解出的圆心贴在弦中点旁，正 R 取 minor 一侧圆心会让包围盒退化成弦点、把「绕远路的大弧」误判成原地不动而绕过限幅；vpype 生产管线实测 0 条真实圆弧，这种几何没有合法来源，fail-closed。回归见 `test_gcode_rejects_degenerate_radius_mode_arc`。当前生产 profile 不产圆弧，指令集为 `G0/G1/G21/G90`，故此路径只在接受外部 G-code 时生效。
    > 溯源：圆弧限幅的代码实际随 `8247c3b`（message 只写了「记录 IDF 实况与串口识别坑」）进入 main——原本的 PR #4 未合并，其分支被后一个文档 PR 夹带进来。按 message 查不到本功能，查 `8247c3b` 的 diff。
  - **IJ 模式整圆判定 epsilon 对齐固件（2026-08-13，R10-ARC-01 收口）**：`validate_gcode()` 的 IJ 模式整圆判定阈值 = 本仓 `gcode_gen.py` 常量 `_ARC_FULL_CIRCLE_EPSILON_RAD = 5e-7`，逐字对齐 Grbl `Config.h` 的 `ARC_ANGULAR_TRAVEL_EPSILON`。语义：扫过角度 `sweep`（mod 2π 折算 CCW 后）`< 5e-7` 判整圆、按 2π 全周扫角算包围盒。为什么是这个数：固件 `MotionControl.cpp` 对 `|angular_travel| ≤ 5e-7` 的 G2/G3 一律补 2π 当整圆画，校验器旧值 `1e-9` 会让 `(1e-9, 5e-7]` 区间的弧被当「原地微动」放行（包围盒退化成端点）、固件却画出整圆——圆越大越致命，本机无限位开关，这道限幅是唯一防线；两侧同阈值才能保证校验器与执行端对整圆/近整圆分类不分岔。该阈值不是可调精度参数，改动须与 Grbl 侧同步评审。回归见 `test_arc_full_circle_epsilon_matches_grbl_firmware` / `test_arc_full_circle_epsilon_constant_pinned_to_grbl`。
  - **圆弧限幅依赖逐行位置跟踪**：包围盒要起点，故校验器逐行维护当前 XY；首个定位指令之前出现 `G2/G3` 一律拒（不猜 `(0,0)`）。且**中途 `G91` 相对坐标一律拒**——位置跟踪假定全程 `G90`，一旦切相对模式，此后每个圆弧起点都错、包围盒静默失真。回归见 `test_arc_*` / `test_mid_file_g91_is_rejected`。
  - **指令识别不得依赖空格切分或本行显式 G 字**：`G0X0Y0`（无空格）、`G90 G0 X0 Y0`（模态前缀）都能绕过按首 token 判断的禁令；裸 `X500 Y500` 会继承 Grbl 当前运动模式，分轴 `G1 X50 Y0` → `G0 X0` 的实际 G0 落点也是 `(0,0)`。Grbl 复位默认运动模式是 G0；校验器同样从 G0 开始，并由生产文件头的两次 `G1G90 Z0.0F10000` 切到 G1，再逐行做模态继承、限幅、实际落点与位置跟踪。坐标正则也须接受 `X.5` / `X1e3`（否则 `1e3` 被截成 `1`，1000mm 越界漏放）。回归见 `test_gcode_rejects_bypass_variants` / `test_*modal*`。
  - **生产指令允许列表（2026-07-29）**：下载文件只允许整数码 `G0/G1/G2/G3/G21/G90` 与 `F/X/Y/Z/I/J/R` 数值字；任意 M 码、`G20/G4/G53/G92`、非整数或其他未知 G/字母均拒绝。G2/G3 原为外部 G-code 圆弧限幅兼容保留；**2026-09-05 起本仓 profile 自产 G2/G3**——`arc_fit.py` 在 vpype 产物上把落笔态连续短折线拟合成 I/J 模式圆弧（顶点容差 0.05mm、弦中点拐角容差 0.15mm、扫掠 ≤350°、半径 0.05–200mm、有向转角总量≈扫掠角 15° 判差；写字页实测砍行 45–50%，逐点复核最大形状偏差 146µm）。G92/G28/G30/M30 等禁令不变，`_arc_bbox` 限幅照常覆盖弧的包围盒。页结束的归位行 `G1G90 X0Y0F8000` 与 `M30` 由 S3 在文件执行完并确认 fresh `Idle` 后依次旁路单发（先归位、后换纸），均不属于下载文件。
  - **注释口径**：校验器会剥 `;` 及其后的行注释，避免注释中的 X/Y/G/M 字样误触发；暂不解析 `(...)` 括号注释，**含 `(` 的行一律整行拒绝**（`gcode_gen.py`，不区分括号内容），故括号注释里的 `G28`/`G0 X0 Y0` 与纯合法的括号注释都会被拒。当前 profile 不产注释行（实测产物中 `;` 与 `(` 均为 0）；接受外部 G-code 前若要兼容括号注释，须先实现确定性剥除并补回归。
  - **非有限数值必须拒成 `GcodeError`**：`G1E400` 之类会 `float()` 成 `inf`，`int(inf)` 抛 `OverflowError`——非 `GcodeError` 异常会穿透闸门变成 500，违背「越界即整单拒绝 + 明确告知」。故字号解析后须 `math.isfinite` 检查。回归见 `test_gcode_rejects_nonfinite_and_malformed`。
  - **畸形坐标不得静默跳过**：`X` 后跟 `NAN`/`INF`/空/`1.5.5` 时，取数正则匹配不上，坐标会被**静默丢弃**——等于把未知坐标当「本行无此轴」放行，限幅形同虚设。故须独立校验「轴字 + 其后连续数值样字符」整体是否为合法有限数，畸形即拒。**静默漏放比误拒危险**。
- **换纸失败语义**：`error:90`；`[MSG:[PaperStatus] N]`：0=成功，2=进纸超时，3=回找未定位，4=卡纸，5=缺纸。N≠0 → 暂停任务 + 云端提示用户，不自动重试。
  > ⚠️ `[PaperStatus] N` 经 Telnet 可达**以 §9 的 B1′ 改动为前提**（原路由为 `CLIENT_SERIAL`，S3 收不到）。未改动前 S3 只能靠 `error:90` 判失败、拿不到具体码。
- **大小上限**：单文件 ≤ **512KB（含，即 ≤ 512×1024 字节）**，超一字节直接拒。
  - **按「设备看到的字节」计，不按上位机的字符数**：S3 下载到的、`Content-Length` 与 CRC32 算的都是**磁盘原始字节**，故云端判上限必须量同一个对象。Python 的 `read_text()` 走 universal newlines，会把 CRLF 塌成单个 LF、每行少算 1 字节——实测某 20846 行 CRLF 产物磁盘 524369 字节（超上限 81 字节）被判为未超并放行，回给 S3 的 `bytes` 也少报 20846。512KiB 是 S3 侧 PSRAM 缓冲的硬约束，少算即等于把越限产物放给设备。单行 **254** 字节 payload（Grbl `LINE_BUFFER_SIZE` 的可用值，见 §3）同理按字节计。

- **MCP 生成事务取消所有权（2026-08-20，不升协议号）**：规范化后相同 `description` 的在途 `hutuji_draw` 必须共享一个完整事务（付费生图、中心线描摹、SVG→G-code、产物与返回 JSON）和一个并发名额。客户端 `notifications/cancelled` 只结清对应 JSON-RPC waiter，不得取消已计费事务、提前释放名额或让后续同题重试另起生成；重试搭车原任务，完成后拿同一 capability 结果。共享任务失败/取消后必须从在途表移除，使下一次调用能重新尝试；成功结果仍受 600 秒 capability TTL。该约束只合并同一逻辑生成轮，不取消该轮内已定案的商汤/Agnes/imagefree 并发对冲。
- **异步生成+完成推送（2026-08-28 决策，默认关闭）**：`HUTUJI_DRAW_ASYNC=1` 且已配 `HUTUJI_XIAOZHI_DEVICE_TOKEN`（或共享文件，见下）时 `hutuji_draw` 立即回 `{"accepted":true,"description":<安全化后>,"eta_seconds":45,"note":"生成完成会自动推送到设备预览，无需重复调用"}`，任务由 `_draw_inflight` 持有生命周期并在 `_run_draw_job` 唯一完成点经 `_chain_device_draw` 自动推送到设备预览（单 flight 复用，同题第二调搭车不另起生成）；缺 token 时回落同步并记一行 warning。
- **语音画图单设备日限（2026-09-10 用户拍板，与 portal 文生图同口径 20 次/天）**：`hutuji_draw` 在既有每分钟 6 次/设备（`HUTUJI_DRAW_PER_DEVICE_MINUTE`）之上新增**每日 20 次/设备**（`HUTUJI_DRAW_PER_DEVICE_DAY` 默认 20）：UTC+8 自然日键 `device_id:yyyymmdd`，按**新任务入队时刻**计数（防滥用闸非精确计费，portal 同款残余风险口径）；同题同推送模式命中在途事务则直接搭车，日限已满仍可等待原事务，waiter 取消后的重试与 `accepted` 重试均不重复扣额；空 `device_id` 跳过日限（与分钟限同口径）。三条路径同行为：内存 queue / Redis queue（日键 `...day:<device>:<yyyymmdd>`，48h TTL）/ 进程内回落（模块级 dict）；超限转白话「今日绘画次数已达上限，明天再来」（`/internal/draw` 映 429）。
- **云端中心线预览资源边界（2026-09-10 评审修复）**：`polylines_to_preview_png` 的高分辨率栅格单边 ≤2048、单个 float32 画布 ≤16MiB。现役 1024/2048 坐标保留 1px 栅格再降采样的墨密度；更大的 SVG `viewBox` 等比缩入预算。线段先裁到源视口再缩放、栅格化，不能靠 `line()` 后过滤像素阻止巨额坐标数组分配；非有限坐标、非正/非有限尺寸拒绝为 `BitmapError`。这是预览边界，机械产物继续走既有 SVG 白名单与本节 G-code 校验。
- **语音链式推送自愈（2026-09-08）**：小程序 `send` 走 portal `push_draw_self_heal`（401/403/404 重铸 messaging JWT）；语音/自生成走 MCP `_chain_device_draw`。异步已回 `accepted` 后云端**不会**再调第二步 `hutuji.draw`，链式失败且无自愈 = 屏上永无预览。量产口径：MCP 与 portal 共用 `device_messaging`——优先读 `/var/lib/hutuji/device_messaging.token`（`HUTUJI_DEVICE_TOKEN_FILE` 可覆盖），回落 `HUTUJI_XIAOZHI_DEVICE_TOKEN`；撞 401/403/404 时用控制台 JWT + `HUTUJI_DEVICE_MAC`（或 portal DB `device_bindings` 最新 MAC）重铸并落盘，worker 现读文件无需重启。portal 自愈成功亦写同一文件，避免「小程序能预览、小派自生成不能」。
### 5.1 下载完整性契约（先下完 + 校验 + 再发）

**放弃「边下边转」**：流式边下边发若中途截断，前半已画在纸上，且提前 EOF 可能不报错——会静默产出「看似画完实则缺半」的纸。故把完整性校验**前移到落笔之前**。

| 方向 | 约定 |
|------|------|
| MCP 服务产出 | 公网能力 URL **必须是 `https://`**；HTTP 响应头给 `Content-Length` + `X-Hutuji-CRC32:<hex>`。Cloudflare Tunnel 的明文回源只允许 loopback，公网直连 `:80 /files/` 必须拒绝 |
| MCP 控制面 | `/mcp` 只允许同机 bridge 通过 loopback 对端连接；服务应用层必须按 ASGI socket 对端地址再判一次，未知/非 loopback 一律 404。不得以 `Host`、`X-Forwarded-For`、`CF-Connecting-IP` 等请求头代替——攻击方可伪造。systemd loopback 监听、安全组不放行 8300、nginx `/mcp` 404 三层仍必须保留；应用层守卫只兜住控制面，不能防 `/files` 绕过 nginx 限速/ACL 直曝。 |
| 校验算法 | **CRC32**只负责发现传输截断/位翻转，**不是认证码**，不能抵抗同时篡改正文与响应头的中间人；公网传输认证由 HTTPS 承担，不上 SHA256。⚠️ **变体两侧必须钉死同一个**（zlib/IEEE，Python `zlib.crc32` ↔ ESP32 侧同变体） |
| S3 侧流程 | 全量下载入 PSRAM（`GetBodyLength()` / `GetResponseHeader()` / `Read()` 循环，模板 `ota.cc` 的 `Ota::Upgrade`）→ **先比长度**（快速失败）→ **再比 CRC32** → 全部通过**才**发第一行 |
| 任一不符 | 任务 error，**一个字节都不发写字机**，推送提示（见 §7） |
| URL 时效 | 只需保证**一次成功下载**（几分钟即可），**可用短时效签名 URL**——因重发走 PSRAM 缓存（§4），不再回源 |

部署门禁必须用一份合法临时产物同时验证 `HTTP=200`、`Content-Length`、
`X-Hutuji-CRC32` 与逐字节正文；只比较命令替换后的正文会吞掉尾部换行，也无法证明 S3 必需的响应头仍在。

**PSRAM 缓存生命周期**：G-code 缓冲校验通过后持有至任务终结（成功且换纸完成后保留一份供 `repeat` 复用，error/aborted 释放）；S3-N16R8 有 8MB PSRAM，512KB 占得起，跨多次重连、跨 ≥60s 换纸期间均须存活。预览 PNG 解码缓冲由预览对象析构释放；预取缓冲只被接管或作废释放，**当前实现已知缺口见 §4 所有权缺口条目**。

## 6. 授权前置

- **拦截范围（源码勘察）**：未授权时**只拦 G0–G3 运动**（执行阶段 `check_license()`，见 `GCode.cpp:1104` 注释）；`$I`、`?`、`M...`（含 `M800` 授权本身）**照常执行**。`error:110` = `Error::AuthenticationFailed`（`Error.h:84`）。
- **因此授权态在落笔之前即可知** → `draw` **加授权前置门**：下载校验通过后、发第一条
  G-code 之前，确认当前连接的零位移探测结果为 authorized；未授权则**直接任务 error、
  绘图载荷零字节下发**。探测行自身可能收到 `error:110`，但它固定在抬笔零位移、draw 前。
- `hutuji.status` 暴露 `authorized` 字段 → ② 冒烟第一步即可一眼诊断。
- 未授权 → 走人工 SOP（按 ChipID 算授权码，`M800 P<码>` 写 NVS），**S3 不自动授权**。
- 烧录不覆盖 NVS，授权与 `$` 设置持久；改机头默认需 `$RST=$`（联调 SOP 记录，非 S3 职责）。
- **联调前置条件**：② 冒烟测试前先确认 `status.authorized==true`（或串口 banner 含 `[License] Authorized`）；未授权先走授权 SOP 再开测。授权持久，实际上是**一次性**的。

### 6.1 Grbl `$` 设置指纹（2026-08-30）

奎享 / ESP3D WebUI 可经 Telnet 改写 `$` 并持久化到 NVS，会绕过 MCP 侧 X≤277/Y≤190mm 安全限幅（例：把 `$130/$131` 改小）。S3 在**授权探测之后、置 `ready=true` 之前**逐项只读查询 golden 表；Grbl 侧对同表项**拒绝写入**（`Error::SettingDisabled`=5）。`$RST=$` 出厂恢复 SOP 不受影响。

**Golden 表（2026-08-29 COM13 实机 `$$` + `[Errors/Verbose]` 只读取证）**：

| 项 | 期望值 |
|----|--------|
| `$1` | 255 |
| `$3` | 7 |
| `$20/$21/$22` | 0 |
| `$100/$101` | 100 |
| `$110/$111` | 12000 → **24000**（2026-09-12 提速批，`docs/deployment.md` 落证据后生效） |
| `$130/$131/$132` | 210 / 297 / 200 |
| `Errors/Verbose` | 0 |

**S3 行为**：

- 连接后授权探测完成 → 逐项 `$N` / `[Errors/Verbose]` 查询 → 比对通过才 `grbl_settings_ok=true` 且 `ready=true`。**提速批过渡窗口**：S3 金标 24000 与机上旧值 12000 不等价——Grbl 固件与 S3 固件必须**同批刷机**（先刷 Grbl → 机上 24000 → S3 旧金标拒画；反之亦然），窗口内 `grbl_settings_mismatch` 拒画是预期 fail-closed 行为。
- 任一不符 → `grbl_settings_ok=false`，`hutuji.status` 带 `grbl_settings_mismatch`（golden key，如 `"130"`）；`draw` / `confirm` / 试笔 / 手动点动 **拒画**，云端可见 `grbl_settings_mismatch` 错误 token。
- 实现：`hutuji_recovery_core.h` 纯函数 + `hutuji_pipe` 状态机 + host 单测 `scripts/tests/test_hutuji_recovery_core.py`。

**Grbl 行为（§9 G′）**：`ProcessSettings.cpp` 在 `setStringValue` 前调用 `ProtocolDecisionCore::is_hutuji_locked_setting_key()`；读/显示不受影响。判定键覆盖数字名与长名两种形态（2026-08-31 补长名，堵 `$X/MaxTravel=200` 经 `getName()` 循环绕过守卫的面，见 findings #30 R1）。

## 7. 错误处理

| 情况 | 含义 | S3 行为 |
|------|------|---------|
| `error:8` | 换纸进行中，运动行被推迟（实为 `Error::IdleError`，`Protocol.cpp:207`） | **不算失败**，但 **⚠️ 该行已被 `empty_line()` 丢弃、不会排队**——S3 必须**自己重发这一行**，不可假设写字机记着。做法：暂停发送、等换纸行 ok 后**重发被拒的那一行**再继续（`[PaperStatus]` 须先做 §9 B1′ 才可达）。注：写字机侧的 `[MSG:]` 推迟提示有 3s 节流（`protocol_notify_paper_motion_deferred`）且只发 SERIAL，S3 只看得到 `error:8` 本身 |
| `error:11` | 行含非 ASCII 或 payload 超 **254** 字节（见 §3；该行同样被 `empty_line()` 丢弃、从未执行） | 任务 error，上报（MCP 生成端 bug） |
| `error:90` | 换纸失败 | 任务 error，按 `[PaperStatus]` 码提示用户（**依赖 §9 B1′**；未改动前只能报「换纸失败，原因未知」） |
| `error:110` | 未授权 | 停止全部发送，提示走授权 SOP。**正常不应出现**——§6 前置门已挡住。该行同样被 `empty_line()` 丢弃（`Protocol.cpp:216-225`） |
| 其他 `error:NN` | 语法/参数错 | 停止，记录行号，任务 error 上报 |
| **`error: <文本>`** | `$Errors/Verbose=1` 时的等价形态（`Report.cpp:236`），如 `error: Command requires idle state` = 8 | **必须映射回数字码后按本表同一行处置**，不得当作未知错误。只认数字会把 `error:8` 变成整单失败、把 `error:110` 变成未知故障（见 §3）。映射表见 `ParseGrblErrorCode()` |
| `ALARM:n` | 报警 | 停止；一般 `$X`/`$RST` 解锁走人工 SOP。唯一例外是 S3 自己刚发出并标记的 abort reset，只允许一次 §4.1 受限 `$X` 恢复 |
| banner 重放 | 对端复位（欠压等） | 状态机复位，任务 error，上报 |
| TCP 断开 | 链路丢失/对端断电 | 指数退避重连；进行中任务置 error，按 §2.1 分流处置 |
| **下载完整性校验失败** | 长度 ≠ `Content-Length` 或 CRC32 不符 | **不发任何字节**，任务 error 上报（§5.1） |
| **下载超长** | 文件 > 512KB | 直接拒绝，零字节下发 |
| 下载失败/超时/HTTP 非 2xx | 含一次性 URL 已失效（403） | 不发任何字节，任务 error |
| 60s 无 `ok`（换纸行 90s） | 链路异常 | 发 `?` 探活一次（实时旁路，换纸阻塞期亦可响应），仍无则任务 error |
| 任务进行中收到新 `draw` | 并发冲突 | 拒绝，返回 `"busy"`，不排队不抢占 |

## 8. 明确不做

- 不用蓝牙走正式数据链路；WiFi 只走 Telnet 这一条（Grbl WebUI 其余功能不依赖）。
- S3 不做 G-code 生成/编辑/预览，只做校验与转发（哑管道原则）。
- **不改动 Grbl 侧的换纸机械/时序、运动、授权、U1-Grbl 协议逻辑**。消息路由与状态字段的小加固见 §9（口径已于 2026-07-25 放宽）。
- S3 不自动授权；除自身刚发出的 abort reset 外不自动 `$X`；不在已知换纸活动态发 `0x18`；不做行级断点续传。
- S3 不重新下载做重发（用 PSRAM 缓存，§4）、不排队并发任务、不把 abort 当急停。

## 9. Grbl 侧改动清单

2026-09-24 网络层并发补充（普通/量产工作树）：`WebCommand::action` 的应答流、参数数组与格式列属于本次调用；使用当前任务的栈上下文，嵌套调用结束恢复外层，另一任务不能覆盖或释放本任务上下文。临界区仅保护上下文链表，不跨网络输出、运动、换纸或等待。此变更不扩大下方商业动作许可，fz 与实机状态见 [迭代修复记录](design/review-fixes-20260924.md)。

> 口径：**换纸机械/时序、运动、授权、U1-Grbl 协议一律不改**；消息路由、状态字段、WebUI 网络层等**不影响商业出图行为**的小加固可接受。**改后必跑 `python D:/Users/zhugu/fz/scripts/agent_gate.py`，overall 非 pass 不得声称完成。**

| # | 文件:行 | 改动 | 为什么必须 |
|---|---------|------|-----------|
| A | `WebUI/TelnetServer.cpp:103` | 新客户端 `available()` 后设 **4 个** socket 选项：`SO_KEEPALIVE=1` + `TCP_KEEPIDLE=10` + `TCP_KEEPINTVL=3` + `TCP_KEEPCNT=3`（~19s 回收） | `MAX_TLNT_CLIENTS=1` + 无 keepalive → 半开死连接独占唯一槽位，S3 重连被 `:98` 拒死（§1.2）。`WiFiClient::setSocketOption(level, option, value, len)` 已暴露（`WiFiClient.h:90`，已核验） |

> ⚠️ **A 条必须设全 4 个参数，只开 `SO_KEEPALIVE` 是无效的**——lwip 默认 `KEEPIDLE=7200s`(2h)/`INTVL=75s`/`CNT=9`，等于 2h11m 后才回收，形同没有保活。同一坑在 S3 侧 M1 代码中已实际发生并修复（`hutuji_pipe.cc` 原仅设 `SO_KEEPALIVE`，注释却写「尽快发现对端断电」）。
| A2 | `WebUI/TelnetServer.cpp:43-74,82,105,153,175` | 文件作用域 `static SemaphoreHandle_t telnetClientMutex = xSemaphoreCreateRecursiveMutex()` + RAII `TelnetClientLock`，在 `begin()`/`end()`/`write()`/`handle()` 四个公开入口顶部各取一次（**只加锁，不改任何路由/状态字/换纸/运动**） | `Telnet_Server::write()` 跑在 loopTask（`Grbl_Esp32.ino:28 run_once` → `grbl_sendf`/`report_*` → `Serial.cpp:430 client_write`），`Telnet_Server::handle()` 跑在 clientCheckTask（`Serial.cpp:151` → `wifi_config.handle()` → `WifiServices::handle()`），两者同优先级同核（`SUPPORT_TASK_CORE`）并发操作**同一个 `WiFiClient` 与同一个 slot**：① `write()` 里 `WiFiClient::write()` 遇非 `EAGAIN` 错误会调 `stop()`（`WiFiClient.cpp:389-443`），`stop()` 把 `_rxBuffer` 置空并释放（`:209-214`），而读侧正在 `available()`/`read()` 的 `WiFiClientRxBuffer::fillBuffer` → `lwip_recv` → `pbuf_free` 中，命中 assert `pbuf_free: p->ref > 0`（上游 bdring/Grbl_Esp32#1364，reporter fork 的修复同样是加 semaphore）；② `clearClients()` 被两个任务并发调用，先 `available()` 再对同一 slot `stop()` + 拷贝赋值，等于在读侧脚下换掉 `_rxBuffer` 智能指针。`MAX_TLNT_CLIENTS=1` 不能规避——两个任务抢的就是同一个槽位 |

> ⚠️ **A2 必须用递归锁**：`handle()` 内 `report_init_message(CLIENT_TELNET)` 会经 `grbl_send` 回到 `write()`，同一任务需重入；普通 mutex 直接自锁死。
> **A2 的 abort 时延口径（不是新增风险）**：持锁期间 `write()` 最坏阻塞约 10s（`WIFI_CLIENT_SELECT_TIMEOUT_US=1s` × `WIFI_CLIENT_MAX_WRITE_RETRY=10`，`WiFiClient.cpp:26-28`），此窗口内 clientCheckTask 取不到字节，`!`/`0x18` 的**提取**被推迟。但 `!`/`0x18` 的**执行**本就在 loopTask 的 `protocol_execute_realtime()` 里，而 loopTask 正是被 `write()` 阻塞的那一个——即无锁时同样停摆。故 A2 不恶化 §4.1 abort 时延上界，仅把「崩溃」换成「已存在的阻塞」。
> **2026-08-02 A2 写路径收口（取代上段 10s 口径，源码未提交/未烧录 → **2026-08-03 supersede：已提交 Grbl `1b56393` 并烧录 COM11，551 行 S4 有纸实测通过，`pbuf_free: p->ref > 0` 本轮未复现**）**：`write()` 不再调用可能阻塞约 10s 的 `WiFiClient::write()`，改为 `send(..., MSG_DONTWAIT)` + **1s 绝对 deadline**；deadline 覆盖 EAGAIN 与每次正 partial write，EAGAIN 重试同时 `COMMANDS::wait(0)` + `vTaskDelay(1)`，完整写出才返回长度，超时/零进展/真实错误则关闭该 Telnet session 并返回 0。这样仍保持 A2 递归锁的跨任务串行化，同时把 loopTask/clientCheckTask 的单次写占用收窄到有界 1s；host 行为回归覆盖正 partial deadline、无效 fd、EAGAIN 让步/看门狗。
| B1′ | `PaperSystem.cpp:843,848,970,1051` | `[PaperStatus] %d` 的 `CLIENT_SERIAL` → `CLIENT_ALL` | §7 换纸失败码（0/2/3/4/5）的唯一来源；不改则 S3 永远收不到，§7 表空转 |
| B2′ | `PaperSystem.cpp:358` | `paper_get_status_str()` 的 `snprintf` 增 `Changing=%s` 字段，取现成 `paper_auto_change_is_running()`（`PaperSystem.cpp:54`） | §2.1/§4.1 abort 分流的**唯一跨 session 地基** |
| C′ | `Protocol.cpp:664-697`、`:763`、`:777-779` + `ProtocolDecisionCore.h:287-293` | `cycle_stop` 分支先快照本次停止的状态与意图，再由 `should_resume_segment_underflow()` 判定:**仅**「`Cycle` 中 + planner 仍有 block + 无 `endMotion`/`executeHold`/`motionCancel`/`soft_limit`」的真实断粮才置 `sys.state = State::Cycle` 并 `st_prep_buffer()+st_wake_up()` 续跑;其余停止意图全部落回原停止分支。续跑后 `:777` 不再重复 `st_prep_buffer()` | 恢复期 `?` 回 `<Idle>` 而步进仍在走，S3 侧 `WaitForIdle` 只看一次 fresh Idle 状态行即返回 true → 页尾提前 `M30` 撕在笔下。与 Grbl 自身 cycle-start 路径（`:627-629`）对齐:先置 Cycle 再 prep+wake。**但不得吞掉有意停止**:`motionCancel`（jog/探针取消）与 `soft_limit` 必须照原路进 `Idle`/`Alarm`，否则 abort 与限位保护会被这条恢复路径吃掉 |
| D′ | `WebUI/BTConfig.cpp`（`begin()`） | 经典蓝牙启动成功后 `esp_bredr_tx_power_set(ESP_PWR_LVL_P9, ESP_PWR_LVL_P9)`（GRBL_PAPER_SYSTEM 守卫内，失败只记消息不阻断） | 2026-08-15 用户决策：IDF 默认发射功率上限仅 P3=+3dBm（`esp_bt.h:331`），拉满 +9dBm 改善 BT 链路质量。纯射频参数，不碰数据面/换纸/运动/授权；§8「不用蓝牙走正式数据链路」不变 |
| F′ | `GCode.cpp:1732`、`:1789`、`PaperSystem.cpp:708-714`、`:837`、`:959`、`:1047-1049` | **`$1=255` 常使能守卫**：M30 页尾与换纸 abort/进纸超时/卡纸不得无条件失能 XYZ；`paper_finish_xyz_after_fault()` 在 `$1≠255` 时仍立即失能、在 `$1=255` 时显式 `motors_set_disable(false)` 恢复（含 M30 已失能的残留）；换纸成功路径仍恢复使能 | 商业固件在 M30 与 `PaperAuto-2` feeder timeout 等处硬编码 `motors_set_disable(true)`，弹簧笔架在「逻辑 Z 仍可能为 5」时失能抬笔 → 下一页没墨。2026-08-28 COM13 刷机 + 用户确认修复后正常 |
| E′ | `PaperSystem.cpp:42`、`:733-758` | 换纸流程**入口原子认领**：`paper_auto_change_running` 由 `volatile bool` 改 `std::atomic<bool>`，函数体在 `PAPER_DISABLED` 早退之后用 `compare_exchange_strong(false→true)` 做 test-and-set；败者只打一条 `[PaperAuto] Busy` 并回 `Error::MessageFailed`(90)，**不清 flag、不碰任何 GPIO/电机/状态字、不发 `[PaperStatus]`** | R20-GW-03 的 check-then-act 残余窗口：`WebSettings.cpp:1100` 的 `running` 预检跑在 clientCheckTask，与 loopTask 的 M30 交叉——预检到入口置位之间仍可双入，两个实例并发驱动同一组换纸 GPIO。认领点是四个调用者（`[ESP910]`、M30、M721 `PaperSystem.cpp:1239`、BT 连接 `:1113`）**唯一**的公共收口；`WebSettings.cpp:1100` 自此降级为 fast-path advisory（只负责 web 侧 `"busy"` 文本应答）。**位置不可上移到早退之前**（早退不清 flag → 认领永久泄漏，比原缺陷更重）；认领之后到函数结束的 16 个 return 全部已清 flag（13 处 `abort_cleanup:708`、缺纸 `:841`、卡纸 `:962`、成功 `:1039`），**禁止再插新出口**；函数共 18 个 return——另 2 个是认领点之前的 `PAPER_DISABLED` 早退与认领失败出口（后者故意不清 flag，归赢家所有） |
| G′ | `ProtocolDecisionCore.h`、`ProcessSettings.cpp`（`is_hutuji_locked_setting_key` → `SettingDisabled`） | **Golden `$` 写保护**：Telnet/WebUI 对 `$1/$3/$20-22/$100/$101/$110/$111/$130/$131/$132`/`Errors/Verbose` 的写入统一回 `Error::SettingDisabled`(5)，数字名与长名（`X/MaxTravel` 等 12 个）双形态同锁；S3 侧只读指纹见 §6.1 | 奎享/WebUI 可改 `$130/$131` 行程，绕过 MCP X≤277/Y≤190mm 安全限幅；2026-08-29 实机 golden 见 §6.1；2026-08-31 长名形态补锁（findings #30 R1） |
| H′ | `WebUI/StatusBeacon.{h,cpp}`（新）、`Config.h`（`ENABLE_STATUS_BEACON` 宏）、`WebUI/WifiServices.cpp`（begin/end/handle 三挂钩） | **状态 UDP 广播**：UDP :2325 广播 255.255.255.255，1s 心跳 + 状态变化即发；payload `<state\|Changing=On\|Seq=N>`（48B snprintf 有界）；`state` 是 `report_state_text()` **只读调用**（函数本体不改），`Changing` 取 `paper_auto_change_is_running()`，`Seq` 仅随真实发送递增（组包失败回滚不丢号）；默认关（编译期宏），UDP 失败静默丢包无回压 | 奎享完成守护检测层（2026-09-12 用户授权）：奎享独占 :23/串口驱动写字机时 S3 无法观察任务终态；UDP 无连接不占 `MAX_TLNT_CLIENTS=1` 槽位、奎享零感知。奎享多页任务页间换纸时 `sys.state` 是 Idle，观察者必须叠加 `Changing` 字段抑制完成误报。fz standard overall pass（2026-09-12，qemu 新镜像过；wokwi 可选层预置 exit 42 与本改动无关） |

> **E′ 的机制取舍（用 `std::atomic` CAS，不用 `portMUX_TYPE` 临界区）**：① 本仓同形先例就是 atomic——`Stepper.cpp:210` 的 `busy.compare_exchange_strong`（本认领落地前是全仓唯一 CAS），与本认领同为单 bool test-and-set；`Serial.cpp:66` 的 `portMUX` + `vTaskEnterCritical`（`:185`/`:232`）保护的是多字段接收缓冲区，范式不同。② 临界区只护写侧，护不到 flag 的 8 处跨任务读（`PaperSystem.cpp:55`/`:59`/`:115`/`:362`、`Protocol.cpp:144`/`:233`、`System.cpp:314`、`WebSettings.cpp:1100`），那些仍会是裸 `volatile` 读；改 atomic 后全部读写一并定义化，零改动。③ `vTaskEnterCritical` 关中断，而换纸全程依赖步进定时器 ISR。④ flag 无 ISR 读者：`system_exec_control_pin()`（唯一疑似 ISR 路径）在本机 `ENABLE_CONTROL_SW_DEBOUNCE`（`Machines/custom_3axis_hr4988.h:84`）下跑在 `controlCheckTask`，非 ISR。
> **E′ 的错误码取舍（`MessageFailed`=90，不用 `IdleError`=8）**：§7 把 error:8 定义为「不算失败、上位机须自己重发该行」——M30/M721 被拒后回 8 会诱发 S3 重发换纸命令 → **双次换纸**。90 与本函数既有三个失败出口同码，且 M30 路径本就被 `Custom/paper_system.cpp:182 user_m30` + `GCode.cpp:1653`/`:1766`/`:1787` 压平成 90，S3 侧零协议变更；`WebSettings.cpp:1105` 只在 `Ok` 时打 `"done"`，故 web 侧仍能区分「拒了 / 跑完」。`AnotherInterfaceBusy`(120) 语义更贴但全仓仅用于 client 抢占（`Serial.cpp:240`、`WebSettings.cpp:217`），且 M30 路径看不到它，换不来任何 S3 可见收益。
> **E′ 未覆盖**：物理并发触发（人手按 `[ESP910]` 与 S3 发 M30 精确交叠）未实机复现；本轮只有源码级证据与 host 侧推理。

**B2′ 的可行性已逐环核验（不再是推断）**：
1. **缓冲够**：`paperStatusHandler`（`WebSettings.cpp:1058`）用 `char buf[64]`，现输出 `Paper=OK MotorEn=On PanelHold=On` 约 34 字节，加 `Changing=Off` 仍富余；`snprintf` 自带边界保护。
2. **任何状态可执行**：`[ESP901]` 注册为 `anyState`（`WebSettings.cpp:1181`），而 `anyState()` **恒返回 false**（`Settings.cpp:6-8`，这些守卫返回的是「是否拒绝」）→ 永不被状态拦。
3. **换纸阻塞期可分发**：换纸中 `protocol_service_during_blocking()` 遍历全部 client 调 `protocol_poll_client()`；其换纸拦截（`Protocol.cpp:206`）**只拦 G0–G3**，`[ESP901]` 落到 `:227` 正常 `execute_line`。
4. **响应回得到 S3**：`ESPResponseStream stream(client, true)`（`ProcessSettings.cpp:617`）用**发命令进来的 client** 构造 → `_client = CLIENT_TELNET`（`ESPResponse.cpp:46`）→ `grbl_send(_client, data)`（`:99`）；`println` 还专为 `CLIENT_TELNET` 用 `\r\n` 行尾（`:55`）。
5. **调用时机安全**：`[ESP901]` 是普通命令会吃 `ok`（§3 单写者），但 Q5/Q8 需要它的时刻正是**重连后、尚未开始发 G-code** 的流控空闲期——恰好契合。

**明确不改**（评估后否决）：
- `report_realtime_status()`（Report.cpp:583）——固定 `char status[200]` + 一路无边界 `strcat`，加字段有栈溢出风险。
- `report_state_text()`——状态字 `<Idle|…>` 是 Grbl 协议核心，现役商业上位机与 ESP3D WebUI 均依赖，加非标状态会破坏商业出图。
- 换纸消息全量改 `CLIENT_ALL`（63 处）——无必要，徒增 Telnet 流量；v0 有 `?` + `[ESP901]` 已够。

**留档备用**：`Protocol.cpp:282-291` 有 BT 专属的 400ms 主动状态保活（「部分上位机只等状态行、不主动发 `?`」），**Telnet 无此待遇**。若 ② 实测 S3 在换纸长阻塞期判死，可考虑同类小改扩至 Telnet。

## 10. 唱歌与记忆契约（2026-08-21 落地）

### 10.1 云端 `hutuji_sing` 与 `/songs` 路由

- **工具**：`hutuji_sing(song: str = "")`。`song` 为空 → `{"found": false, "catalog": [...]}`（回全目录供设备选歌）；命中 → `{"found": true, "url": ..., "title": ..., "duration_seconds": ...}`（`size`/`crc32` 不进工具回包——设备校验走下载响应头，2026-08-25 对账撤正）；模糊匹配未命中 → `{"found": false, "catalog": [...]}`。目录文件 `songs/catalog.json` 与 `.ogg` 文件**必须同批部署**（单飞 catalog 会造成 404 塌缩）。
- **路由**：`GET|HEAD /songs/{name}`，`name` 正则 `^[a-z0-9_-]{1,64}\.ogg$`；名字不合法/文件缺失/读取竞态统一塌 `404`（同 `/files` 口径，不回显内部细节）。响应 `Content-Type: audio/ogg`，头 `X-Hutuji-CRC32: {crc32:08x}`（`zlib.crc32(payload) & 0xFFFFFFFF`），`Cache-Control: public, max-age=3600`。
- **公网面**：nginx 新增 `location /songs/`，与 `/files/` 同桶反代 uvicorn（同限速 zone）；`/mcp` 公网仍 404。
- **曲库内容纪律**：只放公版旋律合成音频，禁止采购/搬运有版权音频（现代流行歌连编曲翻唱也不可上——2026-09-12 用户拍板扩库时重申红线）。**工艺定案（2026-09-12 曲库扩容批）**：放弃 numpy 正弦合成（音色生硬），改 **fluidsynth 2.1.8 + FluidR3_GM.sf2（MIT，148MB）真乐器音色渲染**：自写 SMF 生成器（stdlib，单轨 type-0，Music Box/Piano 音色）出 40 首公版旋律 MIDI → lima `fluidsynth -g 0.5 -r 44100` → ffmpeg libopus 单声道 24kHz 48kbps（对齐 §10.2 设备解码口径）。40 首构成：公版儿歌 11（twinkle/tigers/birthday/lambs/london/rowboat/jingle/silent/tantan/newyear/paint/bee）+ 古典名曲 15（odetojoy/lullaby/canon/furelise/danube/turkish/moonlight/littleswans/swanlake/gymnopedie/spring/newworld/toreador/habanera/mountainking/minuetg/serenade/williamtell/hungarian5/gavotte）+ 中文传统 10（jasmine/farewell/kangding/zizhu/yimeng/xiuhebao/wuxijing/mengjiang）；旧 3 首（twinkle/tigers/birthday）同批换装 fluidsynth 版、id 不变。曲库真值 = `mcp-server/songs/catalog.json`（40 条 id/title/file/duration_seconds）；生成器与旋律序列在 `tmp/songs_melodies.py`+`tmp/songs_make.py`（工艺脚本，不入库维护面）。**未验证**：真机点播实听（音色/响度/节奏需用户屏幕+扬声器复核，旋律为手写简谱转录、以实听为准）。

### 10.2 设备侧 `hutuji.sing` / `hutuji.stop_song`

- `hutuji.sing(url, title)`：`url` 必须是 §10.1 云端返回的地址，**禁止瞎编**；播放期间再调本工具 = 自动切歌。`hutuji.stop_song()` 无参数、幂等，无歌在放时调用安全返回。
- **URL 白名单（固件硬校验）**：生产域仅允许 `https` + 443 + `/songs/*.ogg`；RFC1918 私网地址允许 `http`（本地调试）；其余一律拒绝。
- **完整性**：全量下载到 PSRAM → 校验长度 + CRC32（与 §5.1 同一 `hutuji::Crc32Ieee` 单一事实源）→ 校验通过才进播放。
- **播放路径**：与绘图完全解耦，只走 AudioService 播放泵 + Opus/OGG 解码，**不碰写字机管道、不触发任何笔芯运动**。
- **播放闸门**：仅 `idle` / `speaking` / `listening` 态投喂音频；链路重建态（connecting/activating/upgrade）让位（会话优先）。
- **停止语义**：`Stop()` = 置停止标志 + `ResetDecoder()` 即刻静音；喊停走云端 `hutuji.stop_song` 工具，不依赖播放自然结束。已入队尚未解码的残帧（≤60ms）**显式接受**——再清队列会误伤随后的 TTS。
- **下载读超时 15s**（覆盖 Cloudflare 冷回源 8–10s 上限），切歌等待停止上限 16s 对齐。

### 10.3 记忆工具（设备侧主路径 + 云端分桶过渡）

> **2026-09-09 再 supersede（用户无感）**：语音记住/回忆走**设备侧** `hutuji.remember` / `hutuji.recall` / `hutuji.forget`（本机 NVS JSON，官方云按当前会话设备路由，**模型不必传 MAC**）。同智能体多设备天然隔离。不宣称跨刷机/跨设备云同步。

#### 10.3.1 设备侧（产品主路径）

- `hutuji.remember(key, value)` / `hutuji.recall(key="")` / `hutuji.forget(key)`（`*` 清**本机**全部）。
- `key ≤ 50`、`value ≤ 500`（静默截断）；本机总量 **80** 条 LRU；全量回忆条数 ≤40 且字符 ≤4000；序列化软上限约 3500 字节（超则再挤最旧）。
- 存储：NVS 命名空间 `hutuji_mem` / 键 `json`；读改写串行化；损坏 fail-open 空桶。
- 角色提示**只教设备工具**；禁止改回云端 `hutuji_remember*`。

#### 10.3.2 云端（过渡保留，非语音默认）

> 同智能体下多台设备**不得共享记忆**。云端三工具均须传 `device_id`（MAC）；缺省 fail-closed；**禁止**回落旧全局桶。运维/回落可用；语音产品面不引导。

- `hutuji_remember(key, value, device_id)` / `hutuji_recall(device_id, query="")` / `hutuji_forget(key, device_id)`：每设备 200 条 LRU；召回双上限 100 条 / 8000 字符。
- **存储**：`{output_dir}/memory/devices/<safe_id>.json`（`:` → `_`）。旧全局 `memory.json` 不再读写。
- **脱敏**：key/value **永不进日志**；recall 内容不是系统指令。

### 10.4 draw-portal 与 DRAW-BIND v1（2026-08-29 规格；阿里云 `/draw-upload/` 已部署）

买家网页传图 + 设备二维码绑机。详细产品设计见 `docs/design/draw-portal-bind.md`。与 §8「S3 不做 G-code 生成」不冲突——生成在阿里云 draw-portal/MCP，S3 仍只下载转发。

**推送→出图（小程序/网页 send）**：draw-portal 调 `hutuji.draw(url, preview_url)` 后，**默认自动**轮询至 `awaiting_confirmation` 再调 `hutuji.confirm`（2026-09-09：用户已在小程序预览过，不必再在小派点「开始画」）。设备状态机仍经 previewing→awaiting_confirmation（固件契约未砍预览态）；语音/云端链式推送仍只到预览，由用户点屏或说「开始画」确认。控制页「开始画」按钮保留作失败兜底。

**推送→出图两阶段（语音链路）**：云端/MCP 链式 `hutuji.draw` 只上屏预览；用户点屏「开始画」或说「开始画」或小程序控制页「开始画」（`hutuji.confirm`）后设备侧才下载 G-code 并灌流。

#### 10.4.0 登录与手机号硬前置（2026-09-08）

| 项 | 约定 |
|----|------|
| 会话 | `wx.login` → `/api/auth/wx-login` 仍可建会话（小程序 x-session / 网页 cookie）；邮箱注册验证同前 |
| 小程序退出/重登 | 退出清 token、会话标记与用户/手机号态，置 `logged_out` 阻止静默重登；显式重登才清此标记。每轮登录及退出递增会话代次，并发 `ensureSession` 共用一轮请求；旧 `wx.login`、换会话和 `/me` 的成功/失败回包均失效。请求/上传的 401 只可清除发起时所属的当前 token 与代次，不能清掉后续登录（同秒重签 token 相同也须隔离） |
| 配网授权恢复（2026-09-12） | 手机号复查同样校验会话代次；配网/绑定页再叠加页面代次，重扫、卸载与退出后的回包/定时器不得写用户态或跳转。`ensureSession()` 会同步递增登录代次，页面须在调用后捕获它；授权在途标记也按页面代次持有，旧请求收尾不能清掉新请求 |
| 产品面 | **未绑定 `phone` 禁止**：`bind/*`（含 claim/complete/unbind/status）、`/api/draw/upload`、`/api/draw/write`、`/api/draw/write/ai`、`/api/draw/teach`、文字生图、`send`/saved、`/api/device/control` |
| 放行 | `/api/auth/me`、`wx-phone`、`bind-phone`（仅非微信邮箱账号补绑 11 位）、`wx-profile`/`wx-avatar`、登录注册登出 |
| 错误 | 产品面无号 → **403**，`detail={"code":"phone_required","message":"…"}`（稳定码供小程序统一跳「我的」） |
| me | `phone_bound: bool` 与 `!!phone` 同义。本人当前控制目标由 `active_device_mac` 明确返回，未绑定为 null；`devices[].active` 标记当前设备。`binding.device_mac` 仍为展示打码串，禁止当作控制参数或据此前后缀猜完整 MAC |
| 页面回包（2026-09-13） | 状态/资料/轮询与后续控制按页面展示及登录代次隔离，旧 401/403 不能踢走新会话。已发出的生成与文字回复在同一账号暂时切页时保留结果，隐藏页不续发分享/精修轮询，回屏再接手；退出、卸载、创作模式变化作废旧结果。系统相册/文件选择器造成的 hide/show 允许同一次选择回屏后上传，不能放宽账号/卸载边界；录音松手、取消或离页必须停止，作废 ASR 不续调工具 |
| 网页 QQ 邮箱 | 同闸：无 phone 不可进产品面；经 `POST /api/auth/bind-phone` 补绑 |
| 平台动作 | 备案改名、隐私条款、提审仍在 MP 后台，不由本仓代做 |

#### 10.4.1 角色划分

| 组件 | 职责 |
|------|------|
| draw-portal（阿里云，`/draw-upload/`） | 买家账号、多设备绑定、上传、**保存预览**、按用户拥有的目标设备 token 推送 |
| MCP `output/` + `/files/` | 短时 capability URL（默认 TTL 600s）；**已保存预览**须复制到用户域存储，不得仅 bookmark 公网 URL |
| S3 固件 | QR 绑机 UI + `bind/announce`；出图仍 `hutuji.draw` → 哑管道 |
| 小智官方云 | `/messaging/device/tools/call`，**每设备**一个 messaging token |
| kb-admin | 售后 FAQ；**无**买家账号（独立产品面） |

#### 10.4.2 QR 与 bind_code

**2026-09-12 identity v2 supersede 仅凭 MAC 撮合新设备的旧口径**。版本只属于 DRAW-BIND 身份扩展，板间 Telnet 仍 v0.5；源码/本地测试与生产、手机实测状态分开看 `release-readiness.md`。

- 设备本地进入配网或「绑定呼图账号」时生成一个持久化会话：**6～8 位** `bind_code` + 16 字节随机 `nonce`。服务端挑战窗口至多 **10 分钟**；短码和 nonce 单次消费，重试不延长窗口。
- 新绑定码：`https://hutuji.donglicao.com/draw-upload/bind?c=<code>&m=<MAC>&v=2&d=<device_id>&k=<public_key>&n=<nonce>`；新配网码将 `bind?c=<code>` 换为 `wifi?s=<AP SSID>`，其余字段相同。各值独立 URL 编码。
- S3 在 NVS `hutuji_identity` 保持独立 P-256 私钥；`public_key` 是 65 字节 SEC1 非压缩点，`device_id` 是公钥 SHA-256 前 16 字节的小写十六进制。公钥/nonce 均为无填充 canonical base64url。私钥不出设备；普通换网不换密钥，完成只清会话。
- 扫普通链接 `options.q`（含整体 URL 编码）、直接参数和小程序内 `wx.scanCode` 共用 `bind_identity.js`，按键解析、不依赖参数顺序；重复键、部分身份字段和未知版本拒绝。新扫码整体替换旧记录，手输/旧码必须清除旧 MAC 与 nonce。
- 旧 `bind?c=...`、`wifi?s=...&m=...`、只含 `s`、`WIFI:T:nopass;S:...;;` 和纯 SSID 继续解析；无 MAC 只能回落手动短码。**MAC 本身不是设备身份证明**；旧版兼容是否允许由 §10.4.3 决定。
- 二维码/阶段可本地保存最多 10 分钟用于授权返回；重复保存不续期。Wi-Fi 密码只在当前页面内存，成功或卸载后清空，不写 storage、URL 或日志。
- **二维码显示（2026-09-14 回归补充）**：完整身份载荷须经过实际编码器，不得沿用不足以容纳 v2 的旧容量或删字段兜底。当前显示端 V13/M 覆盖32字节热点名全转义的328B载荷，保留每侧4模块静区与至少2px整数模块；生成或分配失败仍显示失败提示与既有退出入口。完整配网、手动绑定和边界载荷的编码/像素/显隐回归见 `design/provisioning-qr-regression-20260914.md`，手机扫码与实体屏幕另行取证。

算法依据、NVS 失败与擦除后的恢复边界见 `design/device-identity-rollout.md`；不宣称软件密钥具备厂商证书链或抗物理提取能力。

#### 10.4.3 设备 announce（S3 → draw-portal）

```
POST /draw-upload/api/device/bind/announce
Content-Type: application/json

{"mac":"aa:bb:cc:dd:ee:ff","bind_code":"ABC123","device_token":"<固件自 NVS 读取；见下>"}
```

- 传输仅 HTTPS；日志**禁止**打印 `device_token` 与完整 `bind_code`（可记 mac + 结果）。
- 上例是旧版请求。identity v2 先 `POST /api/device/bind/challenge`，提交 `mac/bind_code/device_id/public_key/nonce/identity_version=2`，取得随机 `challenge` 与服务端 `expires_at`；同一 nonce 重试返回同一挑战，不续期。新挑战按 MAC 或请求 IP 的 5 分钟窗口最多 20 个，重复获取不重复扣额；不能因清理 nonce 台账而复活已用证明。
- 新 announce 另带上述身份字段及 `challenge/signature`；ECDSA P-256/SHA-256 签名为 64 字节大端 `r||s` 的 canonical base64url，签 UTF-8 文本 `hutuji-bind-v2|device_id|nonce|challenge|bind_code|mac|plotter_sku|sha256(device_token)`。MAC 小写带冒号、码大写、token 去首尾空白，token 摘要为小写十六进制。签名覆盖机型和 token，不得替换后复用。
- 验签与挑战关联通过后才写 `bind_sessions`；**成功绑定时**才登记 MAC/公钥身份。部分字段、未知版本、篡改、错误挑战拒绝，不能匿名 challenge/announce 抢占 MAC 身份。已登记身份的 MAC 即使兼容模式也必须验签。
- **token 真值（2026-08-29 HIL）**：小智 `/messaging/device/tools/call` 须 **messaging JWT**（控制台「主题配置 / assets-generator」URL 的 `token=` 参数，`purpose=messaging`）。固件 announce 当前仍可能上报 NVS `websocket.token`（WSS 连接用，**≠ messaging JWT**）→ 绑定后 `hutuji.status` 探活 401。**处置（2026-09-08 起，量产口径）**：`_finalize_binding` 探活失败即现场铸新（控制台 `POST /agents/generate-messaging-token {macAddress}`；控制台 JWT 从 portal 进程 env `DRAW_PORTAL_XIAOZHI_TOKEN` 读，`refresh-xiaozhi-token.sh` 已互联写入 draw-portal.env 并重启 portal——旧路径「等推送 401 再自愈」使用户第一单必失败，且自愈曾长期读 root:root 600 的 kb-admin.env、被 hutuji 用户进程级拒读而在生产从未成功）；铸新失败按原样落绑定。**推送/控制自愈触发面 = 401/403/404**（2026-09-08 扩：整片刷机擦 NVS 会重生设备 client UUID，旧 messaging token 指向失效 clientId → `404 Device not online`，重铸取注册表当前 clientId；真离线重试仍败不误报）。complete 时已有同 MAC 且探活正常的旧 token 仍保留（防重绑覆盖）。长期：固件 `ReadDeviceToken()` 优先 `messaging.token`、OTA 解析 `messaging` 段（xiaozhi 工作树未提交）。主题配置重签 token 后须更新绑定表。
- **2026-09-12 token 就绪闸 supersede 新版的「铸新失败也落绑定」**：签名流允许 token 为空，由服务端自动铸造；复用历史正常 token 和新铸 token 都须实际探活。尚不可调用时回 **503**、`detail.code=binding_pending`，保留短码/nonce/原绑定供自动重试；不能把历史 `token_probe_ok` 或「拿到 token 字符串」当作当前在线。旧版 unsigned 流保留原兼容行为。控制台 JWT 仍是服务端依赖，由现有续期机制维护，用户不复制 token。
- `bound:true` 对签名流仅表示**该 nonce 对应的绑定事务**已完成，不能因为同 MAC 曾绑定而提前停轮询。消费后的重复签名可恢复丢失的成功响应，但解绑、转让或后续重绑后不能用旧证明恢复绑定。
- 配置：`DRAW_PORTAL_DEVICE_IDENTITY_REQUIRED=0` 默认只兼容尚未登记身份的旧设备；`=1` 拒绝旧版绑定。`DRAW_PORTAL_DEVICE_IDENTITY_NONCE_TTL` 默认 600 秒、限在 60～600 秒。先部署兼容服务，再小程序与 S3，手机/设备实测后才按批次启用强制模式。
- 三板共用手动/无头 worker，网络连接与激活回调恢复 NVS 会话；重启/切网不另造 nonce。请求、worker 与 UI 回调按 generation 隔离，停止只通知 worker 正常退出，不在 HTTP/NVS 中外部删任务。首 12 轮间隔 5 秒，随后 30 秒，听/说期间让路；收到当前证明的成功才清会话。

#### 10.4.4 用户 complete（浏览器 → draw-portal，须登录）

```
POST /draw-upload/api/user/bind/complete
Cookie: <session>

{"bind_code":"ABC123"}
```

- 匹配未消费的 announce → 建立 `user_id` ↔ `device_mac` ↔ `device_token`（**加密存储**）。
- **2026-09-12 supersede 一用户一台设备**：一账号可多台、每台只有一个当前所有者。`user_device_bindings` 是主表（MAC 唯一），`users.active_device_mac` 只选默认控制目标；`device_bindings` 保留当前设备的旧客户端镜像。旧库先补列再按列名回填，重复迁移不覆盖主表；遗留镜像首次选择须先升入主表，冲突时不能覆盖主表所有权。
- 可选：用控制台 JWT 校验 `mac` 已在 agent 设备列表（与 `/register` 激活一致）。

**一次扫码合一（2026-09-12 更新，旧 MAC-only 路径仅兼容未登记身份设备）**：扫码 → 先完成登录/手机号闸并保留扫码上下文 → 连接设备 AP → TCP HTTP `/scan` 选家网、`/submit` 输入一次密码 → 自动重连家网 → `POST /api/user/bind/claim`。签名流 claim 带 `mac/force/device_id/public_key/nonce/identity_version`，与 announce 逐项匹配；两者任意先到均可完成。身份 claim 窗口不超过挑战窗口，旧版仍用 `DRAW_PORTAL_BIND_CLAIM_TTL_SECONDS` 默认 1800 秒；重复 claim 不续期。短码消费、nonce 消费、归属转移和身份登记同一 SQLite 事务，失败回滚保留原绑定。

小程序 HTTP 的 Content-Length 按 UTF-8 **字节**算；ArrayBuffer 先拼字节再解 JSON，支持定长与 chunked 帧，响应总量上限 64KiB、关闭/超时/取消只结算一次。AP 断连只代表提交结果不确定，**不代表配网成功**。claim 网络/5xx/429 最多额外重试 5 次；状态轮询每 5 秒、至多约 5 分钟并受服务端到期时间约束。系统 Wi-Fi 权限或切网受限时给出系统设置入口；不承诺绕过 iOS/Android 权限确认。

| API / 错误 | 契约 |
|---|---|
| `GET /api/user/bind/claim/status` | query 与 claim 的身份字段一致；仅返回本用户本次证明的 `claimed/bound/expired`，不拿账号另一台设备冒充成功，不暴露他人信息 |
| `POST /api/user/bind/complete` | 手动短码入口保留；新二维码同时传完整身份字段，校验与已验签 session 一致；页面只对 `binding_conflict` 弹换绑确认 |
| `409 binding_conflict` | 其他账号已占有，**新会话证明**加 `force:true` 才能转让；旧已消费证明不允许转让 |
| `409 identity_mismatch` | 二维码串台、身份重置归属不符等；不得自动强制换绑。NVS 全擦新密钥由原所有者恢复；原所有者解除绑定后可由新持有人重新登记，旧密钥随替换退役 |
| `410 binding_expired` | nonce 过期或已消费且不能恢复原成功；重新打开设备配网/绑定页再扫码 |
| `503 binding_pending` | 连接凭据尚未真正可调用；自动重试，不消费证明、不假报成功 |
| `GET /api/user/devices` | 当前账号设备列表，返回完整 MAC 与 `active`，不返回 token；`/api/auth/me.binding` 继续为脱敏当前设备摘要，当前完整控制目标取 `/api/auth/me.active_device_mac` |
| `POST /api/user/devices/select` | `{device_mac}`，只可选择本人设备，否则 404；切换不换绑 |
| `POST /api/user/bind/unbind` | 可选 `{device_mac}`，缺省解绑当前设备；事务内校验所有者，修复剩余设备的当前选择，旧账号迟到的解绑不得删除新所有者的设备 |

token、探活、机型和最后推送时间的异步回写必须绑定发起时捕获的 **user_id + MAC**，不能在回调时重新取当前选择。部署必须整批带上 Store/model/身份模块与调用方，不能再次出现只发布接口而漏掉 Store 方法的跨版本半更新。详细迁移/回滚及测试矩阵见 `design/device-identity-rollout.md`。

#### 10.4.5 网页传图与保存预览

| 步骤 | 约定 |
|------|------|
| 上传 | `POST /draw-upload/api/draw/upload` multipart；PNG/JPEG/WebP/GIF/BMP/SVG（≤4MB）；Word/Excel/PPT/CSV/TXT 文档链；描摹或 SVG 直转 + `validate_gcode` 同 §5。**article**（txt/docx/pptx）可选 form 字段 `size_mm`（2–40，默认 12）、`line_spacing`（1.0–3.0，默认 1.6）；**table**（xlsx/csv）忽略这两项 |
| 推送 | `POST .../draw/{job_id}/send` → `hutuji.draw` 后**自动** `hutuji.confirm`（小程序已预览）；confirm 失败在 90s 窗口内随 status 轮询重试（2026-09-25 起：应答丢失但下一轮 status 已越过确认门按成功收、预览落 idle/error/aborted 立即出局、`没有待确认` 判预览已撤不重试）；仍失败时 detail 含「自动开始画未完成」并附「可在小派屏幕或控制页点『开始画』，或稍后重新发送」，小程序发送入口须透出该 detail，不得只显示「已发送」 |
| **保存预览** | `POST .../draw/{job_id}/save` → 将 PNG（及可选 G-code 副本）复制到用户域持久化路径；**不依赖** §5.1 的 600s `/files/` TTL |
| 图库 | `GET .../draw/saved`；可再次 `send` 或 `DELETE`；响应含 `tg_backup`（`null`=未启用 / `pending` / `uploaded` / `failed`——2026-09-12 TG 频道冷备份 PoC 状态字段，仅预览图、逐字节校验后才算 uploaded；`DELETE` 时频道副本尽力撤回并同步 `telegram_archive_log` 台账 `tg_removed_at`；**不替换**本行主链路，`/files/` 与设备下载一字未动） |
| 冷备份停用（2026-09-13 审查） | 关闭 `HUTUJI_TG_UPLOAD_ENABLED` 同时阻止新增入队和已有队列继续上传；保留凭据时，旧副本的删除/人工恢复不依赖新增上传开关。重试上传仍要求开关开启。停用不擅自清空历史副本或台账，删除失败补偿尚未验收。注册通知只含事件，不发送设备 MAC。实际生产开关以 readiness 为准，再启用须补真实告知、处理依据与适用同意/评估证据 |

保存预览的 G-code 副本仅用于「再次发送到小派」；须仍过 §5 校验。仅保存 PNG 而无 gcode 副本时，不得发送出图。

**召回（2026-09-12 同日）**：上传成功即写 `telegram_archive_log` 台账（saved_id 主键、user_id/title/file_id/message_id/archived_at，行删除不删台账）；运维侧按用户批量召回 `python -m draw_portal.telegram_archive --db <path> --restore-user <id>`——本地文件丢失的在库行就地回拉、被 trim 清掉的行按台账重建（仅预览、`gcode_path=NULL` 故不可再发送）、用户主动删除（台账 removed）跳过。召回是 fail-open 恢复面，不是备份 SLA。

#### 10.4.6 与异步推送的关系

- 语音链路仍走 MCP `hutuji_draw` + 全局或 per-deployment 设备 token（现网 `HUTUJI_XIAOZHI_DEVICE_TOKEN`）。
- draw-portal 推送**必须**用绑定表内 **per-user + per-device token**；不得误用全局 token，或把切换后的设备 token 回写到另一台。
- 两入口可并存；推送 API 相同（`hutuji.draw` 双参数）。

#### 10.4.7 明确不做（本面）

- draw-portal 不生成/不编辑 G-code 规则（限幅/TTL 仍 §5）。
- 不经 draw-portal 向浏览器下发 device_token。
- 不用 BLE/USB 替代绑机 QR。
- 不做一机同时多账号共享；一账号多机已由 2026-09-12 用户授权 supersede，见 §10.4.4。

#### 10.4.8 网页文字生图与 MCP `/internal/draw`（2026-08-31 落地）

| 步骤 | 约定 |
|------|------|
| 入口 | `POST /draw-upload/api/draw/generate`（登录态）`{"description": ≤200 字符}` |
| 转发 | portal 调本机 MCP `POST /internal/draw`（`DRAW_PORTAL_MCP_INTERNAL_BASE_URL`，**仅允许 loopback**，portal 启动强校验） |
| 鉴权 | 头 `X-Hutuji-Usage-Secret`（`hmac.compare_digest`）；**与 usage ingest 同一密钥值**（mcp.env `HUTUJI_USAGE_INGEST_SECRET` = portal env `DRAW_PORTAL_USAGE_INGEST_SECRET`）。未配密钥 / 非 loopback / 密钥错误一律 404（不落 401 免得探测面暴露） |
| 生成语义 | 内部端点强制**同步 + 不推送**（`force_sync=True, chain_push=False`）：即使生产开了 `HUTUJI_DRAW_ASYNC=1`，网页单也等完整结果返回，且**不**经全局 device token 推到任何设备（推送由买家显式点「发送到小派」走 §10.4.5）。`_draw_inflight` 以 `(description, chain_push)` 分键，语音单与网页单互不搭车；Redis 队列旧 payload 缺省 `chain_push=true` 向后兼容 |
| 回包 | `{url, preview_url, lines, bytes, pen_lifts, ttl_seconds}`（同 `hutuji_draw` 同步面）。portal 只取文件名（正则 `draw_\d{8}_\d{6}_[A-Za-z0-9_-]{22}\.(gcode\|png)`）从 loopback 基址重拼下载，不经公网回绕；下载限长 512KiB/4MiB（对齐 §5） |
| 错误 | MCP 侧 400 输入白话 / 429 限流 / 504 超时 / 502 生图失败；**全忙瞬态（2026-09-12 忙批）**：位图链所有对冲通道同一轮全撞 429/5xx/超时判「忙」（`BitmapBusyError`，`_all_errors_busy` 分类判别，内容审核 400 与配置错不算忙），生成轮**整链真重试一次**（停 2.5s 重跑生图，不复用首轮产物，仅一次；二次仍忙原样上抛），`/internal/draw` 映射 **429 + 固定白话「生图通道都在忙，请稍等几秒再试」**（不透传聚合文本）；Redis 队列 worker 异常按类名重建（`draw_queue._exception_from_job` 注册 `BitmapBusyError`）。portal 只透传 400/429 的 detail，504/502 换固定白话（不回显内部细节，2026-08-31 评审裁决）；**429 忙类不推「生成失败」订阅消息**（瞬态忙重试即成，失败推送+随后的成功推送会误导；400/502/504 照推） |
| 配额 | 成功单记 `web_generate` usage 事件；滚动24h上限 `DRAW_PORTAL_GENERATE_DAILY_LIMIT`（默认20）。2026-10-11并发修复 supersede 旧超发残差：网页与语音画图在付费前经SQLite写事务共享预占，成功事件与预占结算同事务；明确失败释放，客户端取消等待不释放仍在执行的操作。进程退出或成功结算失败等结果不明时，预占保守保留最多24h，不自动重发付费请求；超限429。 |
| 公网暴露 | `/internal/*` 不经 nginx 反代（uvicorn 只听 loopback），无需 nginx 变更 |

物化后的 job（`draw.gcode` + `preview.png`）与上传流完全同形，`send`/`save` 零改动；语音链路的异步推送行为不受影响。

#### 10.4.9 照片两段式：Canny 先回 + 程序化风格精修（2026-09-10 切换，supersede 同节 AI i2i 版）

| 面 | 约定 |
|----|------|
| 触发 | `POST /draw-upload/api/draw/upload` 位图经 `is_photo_raster`（近白像素占比 <0.5 判照片）即起精修，**不再需要任何付费通道凭据**；线稿/SVG/文档走原单段，`refined=null` |
| 第一段（同步） | Canny 线稿照常同步返回，响应加 `refined={"status":"pending","style":<生效风格>}`；用户立即可用线稿版发送 |
| 风格档位 | 表单字段 `style` ∈ `hatch`（排线素描，默认）/ `squiggle`（连续波纹）；白名单外值静默回落 `hatch`（体验参数非安全闸，不 400） |
| 第二段（后台） | portal 后台任务：原始照片 → `hutuji_mcp.sketch_render` 程序化渲染直出**矢量折线**（2026-09-12 起 hatch 用原始亮度双尺度差的结构边缘 + 四层累积排线，supersede 固定亮度等高线；squiggle 沿用等值线 + 行扫描波纹 + 蛇形连接），**描摹环节（skeletonize/sknw）整体消失**，经同一条 `polylines_to_svg`/`_validate_svg`/vpype/§5 闸成独立 refined job → 回填原 job 的 `refined_status/refined_job_id`。零模型费、零下载、确定性可复现（同输入逐点同输出） |
| 稀疏几何保真 | 程序化折线的 SVG 与预览均显式 `smooth=False`，不得再套密集描摹点的五点均值平滑。排线串链从实际尾端出发（含反向段），整座连接桥按半像素间隔验本层暗区；结构线继续抽稀，已经串链的排线不再 RDP，防止重新切入白色缺口。hatch 明暗 gamma=0.55、波纹=0.7；对比依据见 `docs/design/photo-sketch-quality-20260912.md`，没有主体分割或去背景承诺 |
| 波纹连接 | 仅前一行末段与紧邻下一行首段可蛇形连接，且整条回折线采样都须处于暗区；同一行由白带隔开的段、空行和两行间白色缺口必须抬笔。行距、振幅和抽稀容差沿用既有值 |
| 轮询面 | `GET /draw-upload/api/draw/jobs/{job_id}`（登录态，属主校验，否则 404）→ `{job_id,status,gcode_url,preview_url,refined}`；refined ∈ `null` / `{"status":"pending","style"}` / `{"status":"failed"}` / `{"status":"ready",job_id,gcode_url,preview_url}`。小程序结果页 ~5s×36 轮轮询 |
| 失败口径 | 渲染任何失败只置 `refined=failed` 并记异常类型名，**绝不影响**已交付的 Canny job；停机取消同样置 failed 后再抛 CancelledError（fail-open 口径与旧链一致） |
| 发送 | refined job 是普通 job，`POST .../draw/{refined_job_id}/send` 走 §10.4.5 原路径，无特殊分支 |
| 明确不做 | v1 不做微信订阅消息推送，用页内轮询；不改 §5 闸任何参数；风格参数不开放数值调节（只两档枚举）；AI i2i 客户端模块（`modelscope_client`/`tcb_i2i`）保留为备用通道但本链不调用 |
| 并发上限 | portal 后台精修经 `_refine_slots`（`asyncio.Semaphore(4)`）排队，超出并发等待不拒绝（本地渲染 ~0.2-0.7s/张，排队代价远低于旧付费 API） |

合规面变化：照片精修不再出服务器（纯本地 numpy/skimage 计算），小程序隐私指引中的「照片传输给第三方 AI 服务」条款随本次切换失去事实基础（魔搭/TCB 条款保留至备用通道彻底移除为止）。旧 AI 链被 supersede 的实证依据：提示词 v6/v7 在 TCB 混元 5 样本配对 A/B 撞 run-to-run 噪声地板（单臂摆幅 76 抬笔 > 全部配对差 14–57），位图→骨架化的信息损耗是结构性的。

#### 10.4.10 小程序手输写字（2026-09-09 落地 hub `981c723` 链；设计 `docs/superpowers/specs/2026-09-09-xiaopai-write-ux-design.md`）

| 面 | 约定 |
|----|------|
| 入口 | `POST /draw-upload/api/draw/write`（登录态 + **须绑定 phone**，同 §10.4.0） |
| 请求 JSON | `text` 必填（繁→简规范化后 ≤5000）；可选 `size_mm` ∈ [2, 40]（**缺省=自动单页适配**，见下「单页适配」行；显式传值按该字号排版不缩）、`line_spacing` ∈ [1.0, 3.0]（默认 1.6）、`font`（字库 id=文件名词，取自 `GET /api/fonts` 的 `id` 字段；空=默认 blood8 真迹手写体；>60 字符或含 `/` `\` `..` → 400「字体名不合法」；具名不存在 → WriteTextError → 400） |
| 单页适配 | 2026-09-11 用户拍板「AI 写字最好也单张纸」：**未显式传字号的入口**（portal `size_mm` 缺省、小程序不传、语音 `voice.write`、MCP `hutuji_write` `size_mm=0` 哨兵）启用 `write_text.layout_text_fit_single`——先按 12mm 排，超 1 页逐轮缩 1mm（标题字号同比缩）凑单页，4mm 可读地板仍超页**回落原字号多页**（不静默截断、不出不可读小字）；显式传字号的调用方直接 `layout_text` 保持用户意图。与 §10.4.15 无换纸机「不做多页」同向：短文尽量单页，多页只剩长文 |
| 手写抖动（2026-09-12 批，用户授权「同意」） | 手输写字链**默认开**随机手写抖动（portal `/api/draw/write` 与语音/MCP `hutuji_write` 同 `layout_text(handwriting=True)`）：每个字形实例绕墨心 ±2.5° 旋转 + 双轴 ±5% 缩放 + ≤3% em 平移（`write_text._HAND_JITTER_*` 常量钉死），只动墨形不动版式——抖动在逐笔折点落页后做，不参与步进/折行/分页，页界由 §5 闸兜底（端到端测试按 0.115em 预算放行边距断言）。种子=字体 uuid+全文，同输入逐点复现（SVG 内容缓存/幂等不受影响）；文档导入/表格/笔顺链保持不抖 |
| 字体列表 | `GET /api/fonts`（`phone_secured`）→ `{fonts:[{id, name}…]}`：id=.gfont 文件名词（稳定契约，POST `font` 回传 id）；name=动力巢科技系列显示名（`write_text._FONT_DISPLAY_NAMES` 表驱动，未登记回落 `动力巢科技·<词>`；2026-09-10 用户拍板品牌改名）；纯文件名扫描不解析字库内容；缺 `HUTUJI_GFONT_DIR` → **503** |
| 默认选择 | 小程序选择器首项为「默认字体」，提交时省略 `font`，由服务端决定偏好及缺席回退；不得把按文件名排序的第一个字库当作默认。用户显式选择其余项才传对应 id，切回默认后再次省略 |
| 产物 | 与文档链同构：`pages/page_NNN.{svg,gcode,png}` + `pages.json` + 首页兼容 `draw.*`/`preview.png`；job 元数据 `kind=write` |
| 响应 | `{status, job_id, preview_url, gcode_url, lines, bytes, pages[{url, preview_url}], page_count, truncated, missing_chars, doc_kind, doc_mode}` |
| 复用 | `POST .../draw/{job_id}/send`（自动 confirm）、`POST .../save`、历史列表；与 §10.4.5 上传 job 零分叉 |
| 错误 | 空文/超长/版式越界 → 400 + 中文 detail；缺 `HUTUJI_GFONT_DIR` → **503**「写字字库未配置」；不回显内部路径 |
| 小程序 | 创作页「画图 \| 写字 \| 笔顺」三段（写字段行为同本表）；写字模式藏拍照/相册、保留「文档」；排版预览须显式点击；weapp 写字段 **v0.2.13** 起、字体选择器 **v0.3.1** 起（对象形态 id/name 与品牌名 **v0.3.2**）、笔顺见 §10.4.11 |
| 明确不做 | ~~选字库~~（2026-09-10 supersede：用户拍板加字体选择器，本表 font/`/api/fonts` 行）、字距；独立 `/api/write/*`；~~改 MCP `hutuji_write` 签名~~（2026-09-11 supersede：用户拍板单页适配，`size_mm` 默认改 0 哨兵=自动适配；显式正值语义不变）；改 §5/竖版/转置/换纸 |

**HIL**：纸面笔迹、多页发送、文档版式与收藏再发 **未验证**（见 `docs/release-readiness.md` 文首）。

#### 10.4.11 小程序手输笔顺（2026-09-09 落地 worktree `07c52e5`..`102bc59`；设计 `docs/superpowers/specs/2026-09-09-xiaopai-teach-ux-design.md`）

| 面 | 约定 |
|----|------|
| 入口 | `POST /draw-upload/api/draw/teach`（登录态 + **须绑定 phone**，同 §10.4.0） |
| 请求 JSON | `text` 必填（繁→简后随便写；服务端按序抽 CJK；无汉字 → 400「没有可教的汉字」） |
| 抽字 | 按出现顺序抽 CJK（`㐀-鿿豈-﫿`，不含全角标点区）；最多 **20** 字（与 `hutuji_teach` / `kArticleMaxPages=20` 同源）；超出截断并 `input_truncated: true` |
| 排版 | `stroke_order.layout_teach`：单字田字格渐显；多字混合行——每字一行「第 k 格 = 前 k 笔渐显 + 步序数字，行尾成形格 = 全字（无序号，临摹用）」，整组统一格宽（24 上限/16 地板），一行装不下折行（2026-09-12 用户拍板「分解行+成形格」，supersede 09-11「集中到单张纸」）；**无**字号/行距；`max_pages = ARTICLE_MAX_PAGES`（20） |
| 产物 | 与文档链同构；job 元数据 `kind=teach` |
| 响应 | 对齐写字多页块，另含 `chars_taught`、`input_truncated`、`missing_chars`、`truncated` |
| 复用 | `send`（自动 confirm）/`save`/历史；与上传 job 零分叉 |
| 错误 | 无汉字 → 400；缺 stroke 目录 → **503**「笔顺数据未配置」；`StrokeOrderError` → 400 |
| 小程序 | 创作页「笔顺」段；藏拍照/相册/文档；weapp **v0.2.14**（AI 写文见 §10.4.12，**v0.2.15**） |
| 明确不做 | 抬 `kArticleMaxPages`；笔画动画；改 MCP `hutuji_teach` 签名；改 §5/竖版/转置/换纸 |

**HIL**：笔顺预览、多页发送、田字格纸面 **未验证**（见 `docs/release-readiness.md` 文首）。

#### 10.4.12 小程序 AI 写文章（2026-09-09；用户定案 C：主题生成 + 草稿润色）

| 项 | 约定 |
|----|------|
| 路由 | `POST /api/draw/write/ai`（`phone_secured`） |
| 请求 | `mode=generate` + `prompt`（主题/提纲）；或 `mode=polish` + `text`（草稿） |
| 行为 | 调 `HUTUJI_LLM_*` 生成/润色**纯文本**；繁→简；长度 ≤2000；**禁止** SVG/G-code；不排版、不出纸 |
| 响应 | `{status:"ok", mode, text}`；失败 400 白话 |
| 小程序 | 写字模式「AI写一篇」「润色扩写」写入 `writeText`，用户可改后再「排版预览」；weapp **v0.2.20**（0.2.15–0.2.19 曾漏挂 wxml 按钮，仅 js/wxss 在场） |
| 环境 | portal 进程须配 `HUTUJI_LLM_BASE_URL/_API_KEY/_MODEL`（可与 mcp.env 同值） |
| 明确不做 | 自动出纸；改 `hutuji_write` 语音链 |

**HIL**：真机 AI 生成→排版→出纸 **未验证**。

#### 10.4.13 小程序语音聊小派（P1；2026-09-09）

2026-09-24 会话约束：历史按已认证 `user_id` 与客户端 `session_id` 联合隔离，客户端不能指定用户身份。同一会话完整模型/工具轮次互斥，重叠请求回 409；进程内 LRU 上限 256，仅淘汰非活动会话，全忙拒绝而不另建第二个动作所有者。普通退出已清除客户端 sid，不将本修复描述为“正常退出必然串历史”。

| 项 | 约定 |
|----|------|
| ASR | `POST /draw-upload/api/voice/asr`（`phone_secured`）；`multipart/form-data` 字段 `file`（`mp3`，≤1.5 MiB）→ `{status:"ok", text}` |
| 对话 | `POST /draw-upload/api/voice/turn`（`phone_secured`）；JSON `{session_id, text}`（`session_id` ≤64，**与设备麦会话独立**）→ `{status:"ok", reply, tool_traces?}` |
| 智能体 | portal 编排：`HUTUJI_LLM_*`；设备工具白名单 `hutuji.status/remember/recall/forget/abort`；**画/写** portal 工具 `voice.draw`/`voice.write`（生成+`hutuji.draw` 仅预览，**不**自动 confirm）；须已绑机 |
| 确认 | `POST /api/voice/confirm` → `hutuji.confirm`；`POST /api/voice/cancel` → `hutuji.abort`；聊天页预览卡按钮触发（LLM **不可**直接调 confirm）。2026-09-13 起预览卡提交生成时绑定的 `{device_mac}`；当前选择或所有权变化回 409，token 自愈重试前再次核对。复用普通控制的业务错误判定，设备 HTTP 200 内拒绝仍回 502，不记成功 usage、不把预览卡改成已开始/已取消。旧无目标调用保持兼容；新页面缺目标的旧卡提示重新生成 |
| turn 回包 | `{reply, tool_traces?, preview?}`；`preview` 含 `preview_url/job_id/kind/device_mac`，device_mac 为该轮生成时的完整设备目标，不使用打码展示字段 |
| 密钥 | `DRAW_PORTAL_TENCENT_SECRET_ID` / `DRAW_PORTAL_TENCENT_SECRET_KEY` / `DRAW_PORTAL_TENCENT_ASR_APP_ID` **只**落 portal env；**禁止**进 weapp / 日志明文 |
| 禁用 | 三键任一为空 → ASR **503**；LLM 缺配置 → turn **503** |
| 小程序 | chat 页按住说话 + 预览卡「开始画/取消」；weapp **v0.2.19** |
| 明确不做 | 小程序直连设备麦/喇叭；旁路官方云自建 WSS；TTS（2026-09-12 用户拍板不做，原 P1.5 排期取消——小程序里小派只出文字）；语音链路自动开画（与相册 send 的 auto_confirm 区分） |

**HIL**：真机画/写→预览卡→确认开画 **未验证**。

#### 10.4.14 小程序文字聊天（在语音/写字/笔顺之外新增文字通道）（/api/chat → MCP /internal/chat，2026-09-09 落地）

2026-10-09 失败恢复：未确认回复的用户轮次和本地错误提示不进入下一轮文字聊天历史。原文可主动恢复到空输入框，不自动发送、不覆盖新草稿；处理期间、隐藏页、退出及切换登录后恢复入口失效。网络超时仅表示回复未确认，不能据此声称请求未到达服务端。

| 面 | 约定 |
|----|------|
| 入口 | 小程序「小派」Tab → `POST /draw-upload/api/chat`（phone_secured）`{message ≤200 字, history ≤10 轮 {role:user\|assistant, content ≤500}}`；仅微信用户（msgSecCheck 需真实 openid），邮箱账号 403 |
| 内容安全 | 用户输入与模型回复各过一遍微信 `msgSecCheck`（version=2 / scene=2），凭据复用 `DRAW_PORTAL_WX_APPID/_WX_SECRET`（access_token 模块级缓存、过期重铸一次）；审核服务不可用 **fail-closed 503**（合规闸非可用性配件）；输入被拦 400 白话；输出被拦回落固定话术「这句话小派接不住…」不扣「违规」帽子 |
| 转发 | portal 调本机 MCP `POST /internal/chat`（与 /internal/draw 同一道闸：loopback + `X-Hutuji-Usage-Secret`，未配密钥 fail-closed 404，公网不可达）；body ≤16KiB |
| LLM | 复用 `HUTUJI_LLM_*` 三件套（不新增凭据）；20s 超时、max_tokens 400、64KiB 响应上限（网络读取阶段生效）；人格 = `hutuji_mcp/chat.py` SYSTEM_PROMPT（与 `docs/xiaozhi-agent/role-prompt.md` 同源裁剪，改人格两边同步）；聊天域不带工具、不真出图 |
| 画图意图 | LLM 输出 `DRAW::{"description": ≤60 字}` 独立行 → MCP 解析为 `draw_intent`：首条完整合法标记即升格（2026-09-23 supersede 首行契约——0.3.17 体验版实证重画措辞下模型先寒暄再放标记，旧契约静默丢意图致按钮消失）；残缺/多余标记行一律剥离按普通回复处理，绝不透给前端。小程序收到意图后由用户点按钮走既有 `/api/draw/generate` → send/save——§5 闸、限流、校验零改动 |
| 回包 | `{reply ≤500 字, draw_intent: null\|{description}, remaining}` |
| 配额 | 成功回话记 `wx_chat` usage 事件；滚动24h上限 `DRAW_PORTAL_CHAT_DAILY_LIMIT`（默认50），与web_generate共用SQLite原子预占/成功结算/失败释放及结果不明保守占额机制，超限429；remaining按结算后成功数读回且不小于0。 |
| 错误映射 | 400 输入白话透传 / 401 未登录 / 403 仅小程序账号 / 429 日配额 / 502·503 固定白话不回显内部细节 |

合规面：聊天消息与模型回复会传输给第三方 LLM 服务（`HUTUJI_LLM_*` 所配通道）生成回话——小程序《用户隐私保护指引》须一并补「文字对话传第三方 AI 生成回复」条款与 AI 生成标识（用户 MP 后台动作）；聊天内容只在小程序本地留存（服务端不落会话历史，仅 usage 事件 detail 留 120 字摘要）。

#### 10.4.15 量产无换纸 SKU（2026-09-10 用户拍板「量产暂时做不换纸机型」「不做多页」；Grbl 分支 `massprod/nopaper-1.3a`）

| 面 | 约定 |
|----|------|
| 机型识别 | Grbl `$I`/banner `[VER:1.3a.20260910:]` = 无换纸量产机型（GRBL_VERSION_BUILD 20260910）；现役 `[VER:1.3a.20211103:]` = 换纸机。S3 连上 Telnet 后读 `$I` 定机型，驱动页尾与多页行为分支 |
| S3 页尾 | 无换纸机型：显式抬笔并等 ok/fresh Idle + 100ms，再旁路 `G1G90 X0Y0F8000` 归位并等 ok/fresh Idle；**M30 段与「等换纸完成」整段跳过**，整单结束用现有 `$MD` 释放电机并等 ok 后置 done。用户手动取纸；`paper_change_notified_` 武装/播报路径不进入 |
| 归位后释放（2026-09-13） | 正常整单末尾与两条停止归位成功出口复用同一 `$MD` 事务；不把任意 Idle 当完成，不在绘图、暂停、换纸或继续下一页前释放。保留 `$1=255`，每次新任务 `G92 Z0`，下一运动自动使能。提交释放后迟到 pause/resume/abort 不能重新启动运动；失败拆原连接以隔离迟到 ok，不在新连接补发。Grbl 参数缓冲由 RAII 回收，避免每次失能累积 `strdup` 泄漏，掩码与错误语义不变 |
| 多页任务 | 无换纸机型**不做多页**（用户拍板）：`pages>1` 的写字/笔顺/文档任务在确认环节拒绝，话术「这台机器一次只能画一页哦」；单页任务（语音画图/传图/单页写字）零改动 |
| 云端/小程序 | 产物契约、§5、错峰 TTL、pages 推送格式**零改动**；按机型藏功能依赖能力标志位（S3 announce 上报 SKU → portal 绑定表存机型 → 小程序按机型显隐多页入口），该面**待首台点亮联调后定案**，当前两机型共用现 UI |
| 机型词汇（2026-09-11 定稿） | 字面量 `"nopaper"` \| `"paper"`；未知/缺席一律按 `"paper"`（现役 MK38 行为）。S3 `hutuji.status` 回包自 1.0.9 起带 `plotter_sku`（`pipe.IsNopaperMachine()`）；bind announce 自 1.0.9 起随 body 上报 `plotter_sku`（旧固件缺席 → portal 默认 paper） |
| 生成选档（2026-09-11） | `gcode_gen.MachineProfile`（transpose_xy/max_x_mm/max_y_mm）+ `PROFILE_NOPAPER = (False, 205.0, 290.0)`——物理 210×297 减 5mm 边；**不转置**（nopaper 竖放、X≈210/Y≈297，用户拍板 nopaper 取消转置）。paper/未知 = `None`（现役 MK38 默认档：转置 + 277/190）。语音路径 = 设备 `hutuji.status` 探测（60s 进程内缓存，失败也缓存）；显式 machine（portal `/internal/draw` body）优先于探测；非法值 400 |
| 防串图 | G-code 内容缓存键与 inflight 键均含机型成分（`_profile_cache_marker` / `_inflight_key_for`）：同 SVG/同题跨机型不得互串产物 |
| portal 学习 | `bind_sessions.plotter_sku` 随 announce 落库 → `_finalize_binding` 转存 `device_bindings.machine_type`（旧库迁移默认 `'paper'`）；portal 各生成入口按绑定机型选档：位图/SVG/文档/写字/笔顺/精修本地穿 profile，AI 生图把 machine 显式传 MCP |
| S3 点动包线 | `DecideJog` 增显式限幅重载 + `MachineJogEnvelope(nopaper)`：nopaper 205/290（镜像 PROFILE_NOPAPER，R17-XREPO-01 双侧同步）、paper 277/190；VER 未达 = 保守 paper 档。工具侧预检与 ManualTask 纵深防御同源改参 |
| 状态 | MCP/portal 代码面 + 回归绿（`test_contract.py` machine 白名单/缓存成分/inflight 隔离/probe 缓存 4 例 + nopaper profile 3 例；draw-portal 173 项全绿）；S3 host 点动测试绿；**实机 HIL 未做**（nopaper 不转置、205/290、MK38 双向不变），归 todo「构建 1.0.9 + 实机验证」 |
| 读闸分档（2026-09-11 热修） | `/files` 读时复核 `output_store._read_verified_gcode_bytes` **按机型分档放行**：默认档先验，`GcodeError` 再以 `PROFILE_NOPAPER` 重验，任一通过即下发；两档都不成立仍 stale 删除。理由：读时无法知道产物出自哪台机器（文件名无机型成分），只跑单档会把另一机型的合法产物判 stale 联动删除 → 设备 404（2026-09-11 生产事故实锤：nopaper Y>190 合法产物被删、语音画图全灭）。写入侧仍按生成时选定的单档校验，不放松 |
| 速度金标（supersede 2026-09-13） | `$110/$111` 已退出无换纸金表（12→10 项，Z 速度原本不在表），三轴独立调节与实际读回见 §10.4.21。X/Y 默认 10000，当前 Z 保留；NVS 普通重启保留，量产源码 X/Y 出厂默认也为 10000，Z 出厂 600 不改。速度不改变文件 F24000/F10000 与机器包线，旧「恢复出厂必须重写 24000 否则拒画」作废 |

#### 10.4.16 小程序订阅消息通知与分享卡片（2026-09-11 批；本仓 `2d81300`）

**订阅消息（`draw_portal/subscribe_notify.py`）**

| 面 | 约定 |
|----|------|
| 键位 | 默认模板字段键 `thing1`（描述）/`phrase2`（状态）/`time3`（时间）——MP 后台申请「描述/状态/时间」三字段模板后按号填 `DRAW_PORTAL_WX_SUBSCRIBE_FIELDS`（JSON，坏值回落默认） |
| 开关 | `DRAW_PORTAL_WX_SUBSCRIBE_TEMPLATE_ID` 空值=**整链禁用**（静默跳过不打扰主链；enabled() 还需 `DRAW_PORTAL_WX_APPID`）；发送走 `chat.get_wx_access_token` 同一凭据，40001/42001 失效重铸一次，43101（用户拒收）静默 |
| 触发面 | `GET /api/wx/subscribe-config`（登录态）回 `{enabled, template_id}`；`notify_job_result_async` 在 draw_generate/write/teach/upload 文档与位图/voice draw 与 write 各成功+失败分支触发（daemon 线程 fire-and-forget，同 user+status 5s 去重窗，上限 64 条）；**守卫级拒绝（429/未绑定/未配置）不通知**——只有已建任务才通知；thing ≤20 字截断、time `%Y-%m-%d %H:%M` |
| 前端 | `wx.requestSubscribeMessage` 必须在 tap 回调内同步调用 → `utils/subscribe.js` onLoad 预取模板 ID（缓存 `subscribe_tid`）、各生成/上传/重画/按住说话入口 tap 时请求授权；每次授权只发一条 |

**分享卡片（`draw_portal/share_token.py`，无状态 HMAC）**

| 面 | 约定 |
|----|------|
| 令牌 | `issue_token(secret, job_id)` = `b64url("{job_id}:{exp}") + "." + b64url(HMAC-SHA256)`，TTL 24h；`parse_token` compare_digest+exp 校验，任何失败→None（调用方 404）；密钥轮换即全废、不入库 |
| 端点 | `POST /api/draw/{job_id}/share`（phone_secured，须 ready）→ `{share_token, title}`；`GET /api/draw/share/{token}`（免会话）→ `{title, preview_url, created_at}`；`GET /api/draw/share/{token}/preview.png` → PNG。**只授予只读预览**，不含 G-code；预览经令牌自证端点代理（/files 直链带不上作者 x-session） |
| 前端 | 落地页 `/pages/share/share` 为独立非 Tab 页（规避 Tab 页 query 兼容）；`adoptJob` 时预取令牌 → `onShareAppMessage` 同步返回（title「看看我刚让写字机画的作品」）；失效态「分享已失效/链接过期或作品已删除」；接收者「我也要画」switchTab 回创作页 |

#### 10.4.17 奎享完成守护云端通知（2026-09-12；§9-H′ S3 观察端对接）

| 面 | 约定 |
|----|------|
| 端点 | 设备 `POST /draw-upload/api/device/kdraw/done`，body `{mac, title?}`（title ≤60，截 20 入模板） |
| 受理 | MAC 归一化查绑定表（同 §10.5 先例）：**未绑定一律 403**、坏 MAC 400；mac 属弱凭证——本端点只触发一次性订阅消息（不落库、无数据面），不引 device_token（避免 token 轮换 401 丢通知） |
| 通知 | `subscribe_notify.notify_job_result_async(phrase="绘制完成")`：thing=title 或「奎享作品」、phrase=「绘制完成」、time=当时；未启用/openid 缺席静默；同 user+status+phrase 5s 去重窗 |
| 设备侧 | S3 `hutuji_kdraw_watcher.cc`：UDP :2325 只收已由 Telnet banner 验证的目标 IP（允许其 NVS 缓存）与源端口 2325；严格要求 `Changing=On/Off`、uint32 `Seq`，拒缺字段/重复字段/畸形数值。仅同源、单调新 Seq（允许 uint32 环回）推进；Run 后连续新 `Idle && Changing=Off` ≥2.5s 判完成 → 本地 Alert（屏显+提示音）+ MCP `notifications/message` 云端转述 + 上述 POST（未绑定 403 静默容忍）。重复/乱序不续期；2.5s 失联、换目标或 Alarm/Check 清掉完成证据，收包超时永不触发完成 |

#### 10.4.18 作品里程碑与连续打卡通知（2026-09-12 粘合性批；portal-only）

| 面 | 约定 |
|----|------|
| 触发 | 复用 `_notify_subscribe(status="success")` 全部成功点（语音画/写、文字画、文档写、笔顺、写字）——成功事件即打卡与累计时刻；失败不触发 |
| 里程碑 | ready 作品累计 ≥1/10/50/100/500 时各推一次（跳档只推最高已跨过档）；文案 `milestones.DRAW_MILESTONE_TEXTS`（第一幅作品/第 N 幅作品）；thing=文案、phrase=「里程碑」 |
| 连续打卡 | ready 作品按服务器本地日历日去重，今天有作品时向前数连续天 ≥3/7/30 各推一次（`连续打卡 N 天`）；今天缺席不追溯 |
| 幂等 | `store.engagement_events(user_id, kind, key)` 主键 `INSERT OR IGNORE` 抢占登记，首次登记才推送——重复成功事件/服务重启不重发 |
| 通知 | 同一模板 thing1/phrase2/time3，phrase=「里程碑」（≤5 字）；与常规「生成成功」通知的 5s 去重键（含 phrase）互不吞并——单次成功最多 3 条（常规+里程碑+打卡），各消耗一条一次性订阅授权，未授权静默 43101 |
| 数据 | `draw_jobs(status='ready')` 计数/日期即时查询，不落计数快照；`engagement_events` 表仅做已发登记，无清理任务 |




#### 10.4.19 小派设置：音色/模型切换（2026-09-12 落地；用户拍板「用户不能改所有人的」）

| 面 | 约定 |
|----|------|
| 归属 | 音色/模型是**控制台 agent 级**配置（会话参数，固件零改动，生效=下一次对话，官方 FAQ Q21）。用户切换只写**自己的克隆 agent**；未个性化设备继续走共享 agent 2094251，互不影响。共享 agent 只能由卖家在小智控制台改——MCP 内部路由对基础 agent 一律 400 |
| 克隆 | 首次个性化（改音色/模型或绑设备）时 MCP `xiaozhi_console.clone_agent()` 从基础 agent 新建专属 agent 并**整包复制** 11 字段（language/tts_voice/character/asr_speed/tts_speech_speed/tts_pitch/llm_model/memory_type/teen_mode/mcp_endpoints/knowledge_base_ids）；白名单外字段拒写（`update_agent_config` 只收 tts_voice/llm_model） |
| 绑定 | weapp 小派设置页引导读设备屏幕「设置→设备验证码」6 位数字 → `POST /api/agents/{clone}/devices {"verificationCode"}`（控制台同款换绑语义）；无效码 400 `INVALID_VERIFICATION_CODE`（服务端无副作用） |
| 端点 | portal `GET/POST /api/settings/xiaopai`（phone_secured；POST 只收 tts_voice/llm_model，至少一项，清单内 fail-closed 未知 400）+ `POST /api/settings/xiaopai/bind`（code 6 位数字）→ 本机 MCP `GET /internal/xiaozhi/options`、`POST /internal/xiaozhi/config|agent/clone|agent/bind`（loopback+共享密钥同闸；404 fail-closed） |
| 凭据 | 控制台 JWT 只在 MCP 侧 env（HUTUJI_XIAOZHI_TOKEN，与激活中转共用；token-auto 续期后 restart 即新值）；portal 只持共享密钥转发 |
| 限频 | portal usage 事件限频：切换成功 20 次/日；绑定为每用户滚动 24h 最多 10 次尝试，SQLite 写事务内先登记再调用上游，成功/失败/中断均占一次，成功只更新本条结果、不重复扣额（2026-09-24 用户授权修复，supersede 原“绑定成功才计数”）。`agent_id` 不出 portal（回包只有 tts_voice/llm_model/device_bound） |
| 克隆成本 | 每个个性化用户占控制台 1 个 agent 位（人格/KB/MCP/记忆档随克隆带入；聊天历史清零、`hutuji.remember` 按 device_id 分桶不受重绑影响）；测试克隆用完即删，探针已验证建/写/绑/删四形状 |
| 部署 | lima+jd 双节点 2026-09-12 实测：options 200（61 中文音色）、clone→config 写入 200、无效码 400、共享 agent 拒改；jd 的 mcp.env `HUTUJI_USAGE_INGEST_SECRET` 曾与 portal 不一致（热备无流量未暴露），本次对齐后 jd options 200 |
| 入口 | weapp 聊小派页左下 ⚙ → 小派设置页（音色按语言分组 61 中文起步、模型 xz-lite/deepseek/qwen/doubao 四选、绑定卡） |

#### 10.4.20 小程序手绘画板（2026-09-12 批，用户授权「同意」）

| 面 | 约定 |
|----|------|
| 入口 | 创作页画图模式第 4 格「手绘」→ `pages/drawpad/drawpad`（canvas type="2d"，画布按 §10.4.24 的有效撰写宽高设置比例） |
| 工具 | 画笔、整笔橡皮、直线/方框/圆、选择移动/缩放、撤销重做、缩放和平移；新增镜像、六向笔迹、几何素材与逐笔回放。显示与 SVG 导出共用 `utils/drawpad.js` 路径；SVG 白名单仍无 `rect/ellipse` |
| 产出 | `_buildSvg()` 导出 `svg/g/path` 白名单子集，`fill=none`；保留画布留白、素材位置和对称变换。账号草稿独立保存，纸张变化后须显式适配，离页/退出停止回放和后续请求 |
| 上传 | 临时 SVG → 既有 `/api/draw/upload`，携 `device_mac`、`paper_marker`、`preserve_canvas=true` → finally 删除临时文件。服务端核对本次设备快照；`preserve_canvas` 仍运行 `vpype layout --no-bbox` 及既有 5mm 构图内边距、linemerge/linesort/linesimplify，不按墨迹边框放大角落小画。此条 supersede 初版「零后端改动」口径 |
| 回流 | 事件通道 `adopt` 回传 job 数据到创作页 `adoptJob`（SVG 无精修轮询，`refined=null` 直通）；订阅消息预授权在入口 tap 时点请求 |

**HIL**：手绘出图纸面效果 **未验证**（见 `docs/release-readiness.md` 文首）。


#### 10.4.21 写字机三轴调速（2026-09-13 用户要求 X/Y 默认 10000、Z 保留现值）

2026-10-09 控制页续作：速度读写客户端等待上限为60秒，覆盖门户 accepted 后40秒事务及网络余量。失败不自动重复写入，未重新读回前不允许继续调速。设备 error 态分别展示 `last_error` 与 `control_error`，离开 error 态清除；操作失败的完整原因留在页面直到下一次操作或重新进页，网络中断按结果未确认提示，不能视为未执行。

设备拒绝由共享`hutuji_mcp.tool_result`逐层判错与提取原因：外层失败不得被内层成功遮盖；门户只透出已登记的设备固定文案或其中文映射，限定长度并清理控制字符，未知网关/异常正文继续使用固定提示。传输层和控制路由兜底均复用该入口；拒绝后不能自动确认旧预览。

| 面 | 约定 |
|----|------|
| 金表 | 无换纸金表仍为 10 项，`$110/$111/$112` 不入表；只锁步进/行程/笔程/无限位/使能/方向。换纸机 §9-G′ 锁表不变，调速工具只开放无换纸量产机 |
| 轴与范围 | `axis=x/y/z/xy`，省略为 xy 兼容旧调用；X/Y 3000–16000、UI 步进 500，Z 100–3000、步进 100，单位毫米/分钟。Z 上限是本轮保守的软件调节范围，机械验证范围单独登记。`rate` 正整数只写所选轴；`rate=0` 或省略只读三轴 |
| 默认与持久化 | 用户指定 X/Y 默认 10000、当前 Z 保留（本次实机 1200）。量产 Grbl 机头 X/Y 出厂值由 5000 改成 10000，Z 出厂 600 不动；`FloatSetting::setStringValue` 写 NVS，普通重启/保留 NVS 的升级不重置速度，S3 启动不覆盖用户设置。`$RST=$` 才回各机型编译默认；换纸分支仍 24000，不能混用 |
| 受理 | `Job::RequestSpeed` try-lock + busy + settled（idle/done/error/aborted）+ connected/ready/authorized/settings + nopaper 闸；主线程立即回 `{status:"accepted",request_id:N,axis:A,rate:V}`，不等网络。`status.speed.axis_control=true` 声明分轴能力 |
| 事务 | worker 发 `?` 等 fresh Idle（6s），绑定同一连接，按选中轴写 `$110/$111/$112` 并消费各自 `ok`；只读请求不写任何参数。随后三次 `$110`/`$111`/`$112` 各等实际参数行与 `ok`（单条预算 5s），每轴报告序号必须较查询前更新、写入轴读回值须精确相等。全部成功才 done；失败/超时关闭本次连接隔离迟到应答，部分写入不报全成功 |
| 结果 | `hutuji.status.speed` 为 `{axis_control,state:idle/pending/done/error,request_id,axis,requested_rate,rate?,rates?:{x,y,z},reason?}`。读回完整且 done 时才发布 rates；rate 只用于成功写入。新请求清空上次实际结果。调速期间 abort 明确拒绝，不走绘图 reset；status 仍只发 `?`，参数查询仅由 worker 配对应答 |
| 门户读 | `GET /api/settings/machine-speed?device_mac=…`（phone_secured）先检查分轴能力，再以 rate=0 查询，完成后回 `{status:"ok",axis:"xy",rates:{x,y,z},result:"…"}`。查询不记速度写入 usage |
| 门户写 | `POST /api/settings/machine-speed {axis?,rate,device_mac?}`：轴白名单与每轴严格整数/范围闸；device_mac 匹配当前选择，调用/自愈前核对所有权。显式 axis 写入前必须查分轴能力，旧设备 409 且不发送 speed（旧设备会忽略 axis 而误写 XY）；省略 axis 保留旧同步 rate/新异步两轴兼容。accepted 最多 40s/80 轮查询，核 request_id/axis/目标值/三轴读回后回 `{status:"ok",axis,rate,rates?,result:"…"}` 并记 `web_machine_speed`。错任务、错轴、部分读回、HTTP 200 内设备错误与超时均失败 |
| 小程序 | 控制页三个滑块；正常 `/auth/me` → `active_device_mac` → status → 只读速度链读取实机值，未读取前禁调，不把默认值显示成已生效值；展示打码字段不能用于控制。每次只提交拖动轴，确认后显示三轴读回；错误可见、可重试并还原原生滑块。状态轮询串行，不在慢网叠加请求；首次查询不误报未绑定。页面/登录/设备代次拦迟到回包，隐藏页不重启轮询。完成/空闲/错误/停止/待确认不显示固件仍累加的耗时或ETA，进行中与暂停保留；不把空闲时长当已完成绘制耗时。仅就绪的无换纸机可调 |
| 普通控制目标 | `/api/device/control` 可传 `device_mac`；与当前选择不同回 409，第一次调用与 token 自愈重试前均核当前目标及所有权。设备响应 HTTP 200 内的 `isError` 或业务失败不得包装成控制成功。旧未传目标的客户端保持兼容 |
| 进给边界 | 文件契约仍为 XY F24000 / Z F10000，实际分别受 `$110/$111/$112` 钳制；归位仍为原 G1 F8000。最大速度不改变短段加减速、加速度、换纸、使能与行程契约 |
| 验收与部署 | 前段 1.0.13–1.0.16 速度/静音历史保留在 readiness/deployment；本批三轴设计、读回与单轴不串写回归见 `design/axis-speed-20260913.md`。构建和命令链不等于手机滑块或全幅纸面通过，实际上线与实机版本只看 readiness |


#### 10.4.22 小程序固件升级的目标与结果确认（2026-09-13 评审补充）

设备侧升级条件仍为 §4 的 `hutuji.ota_start`；本节约束门户与页面，延续量产 OTA 设计的用户确认和失败可恢复要求。

2026-09-24 会话领取：任何设备 I/O 前在数据库按用户原子占位，生成唯一 `session_id`；目标、受理结果及状态轮询均须条件回写。内部 `starting` 到命令成功应答才转 `upgrading`；504/超时转 `uncertain`，观察到设备 ota.upgrading 后才接收后续失败，避免旧失败解除忙闸。这三阶段对外均为 upgrading、均拦当前设备出图且受 600s 过期约束。starting 期间查询不能把上一轮设备失败或进度写进新会话；uncertain 可凭目标版本匹配直接对账成功。已成功/失败/回滚的终态不被后续设备失败回退，只有 status_timeout 可再按目标版本核对成功；异常只释放自身领取。数据库旧行迁移为空会话标识，新领取必须有唯一值。

| 面 | 约定 |
|----|------|
| 检查与下发 | `GET /api/device/firmware/check?device_mac=…`、`POST /api/device/firmware/upgrade {device_mac}` 均须手机号、归属及当前活动设备一致；不一致回 409。旧省略目标调用保持兼容；新页面未取得 `active_device_mac` 不发请求，检查失败清除旧升级按钮 |
| 下发复核 | `hutuji.ota_start` 发送前和 token 自愈重试前复核目标仍归该用户且为活动设备；设备 `isError` 或业务拒绝不得记为 started |
| 原设备状态 | `GET /api/device/firmware/ota_status?device_mac=…` 查询该用户仍拥有的原设备，可以与活动设备不同；升级会话须匹配同一 MAC，另一台设备的版本不能结清原设备会话 |
| 会话收敛 | 连续 600s 无实际进度的 upgrading 会话结束门户等待，记 failed/status_timeout（仅表示结果未确认，不表示设备刷写失败）；相同/回退进度及空闲应答不续期。发送出图只检查当前目标 MAC，其他设备升级不拦截；同账号一次仅保留一条升级会话，新升级须待旧会话终结。迟到回包须以 MAC、目标版本、状态及更新时间条件更新，不能覆盖新会话；同设备实际版本匹配可将超时会话重新对账为 ok。设备侧忙闸不因门户超时而解除 |
| 页面确认 | 版本检查、确认弹窗与升级下发锁定同一 MAC；弹窗期间切换设备不下发。请求超时/网关暂不可达保留待确认提示，按原设备继续查询；400/401/403/409 停止轮询并提示刷新。隐藏或退出页面不续发查询 |
| 验收 | 接口与页面回归见 `design/weapp-followup-review-20260913.md`；升级实机证据独立于软件回归，不触发静默升级 |

#### 10.4.23 精品图库（2026-09-14 用户拍板命名；设计 `design/drawing-market-20260914.md`）

2026-10-09 浏览恢复：同账号从详情或其他页面返回保留分类与已加载页（包括空分类），恢复可视区预览；缓存与在途预览不重复下载。目录、首页、翻页失败分别重试原请求，翻页保留游标与已有卡片，重试期间不叠加请求；下拉刷新重新读取目录与首页。隐藏页旧请求不能回写，切换账号清空原列表与重试状态。

小程序内浏览官方精选单线图库，一键发送写字机或收藏。用户原话定案六项：纯官方导入无 UGC；许可白名单；精品选品；约 20 类目 400-600 条；VPS 本地存储零 OSS；主按钮直达发送 + 次按钮收藏。

| 面 | 约定 |
|----|------|
| 数据 | `manifest.json` 单一真相源进 git（`mcp-server/market/`，双机部署同份）；条目包含固定来源提交、原 SVG SHA、许可全文及产物 SHA、`reviewed`。入库 G-code 是默认纸张的门禁证据；发送/收藏按本次设备 `PaperConfig` 重新转换并绑定内容元数据，缓存键包含 SVG SHA 与纸张 marker（§10.4.24 supersede 旧“发送零转换”口径） |
| 许可闸（fail-closed） | 白名单 {CC0-1.0, Apache-2.0, MIT, CC-BY-4.0}；CC-BY-SA/CC-NC 硬拒（商用产品不兼容）；`license` 缺失或不在白名单，加载期剔除不出现在市场。CC-BY 条目 `attribution` 必填，缺署名剔除 |
| 署名义务 | CC-BY 条目署名展示在详情页与发送确认卡两处；缺署名视为缺陷，不允许上线 |
| API（phone_secured） | `GET /api/market/categories`、`GET /api/market/list?category=&cursor=`（30/页）、`GET /api/market/item/{id}`、`GET /api/market/item/{id}/preview.png`、`POST /api/market/item/{id}/send`、`POST /api/market/item/{id}/favorite`。`DRAW_PORTAL_MARKET_DIR` 未配置或 manifest 缺失时全端点 404（功能禁用 fail-closed） |
| 发送 | 与 `saved_send` 同构：`_install_capability_pair` 刷 mtime 落 MCP output + `push_draw_self_heal` 推送；固件升级中拒绝；占用设备 busy 语义不占生图并发；usage 记 `web_market_send`（detail=条目 id） |
| 收藏 | 产物拷入用户 saved 目录落 `saved_previews` 表（标题=条目标题），TG 备份开关沿用同待遇；`trim_saved_previews` 50 件上限不变；usage 记 `web_market_favorite` |
| 安全 | 条目的 gcode/preview 相对路径解析后必须仍落在市场根内（`..` 越界拒）；manifest mtime 变更热重载，坏 manifest 沿用旧快照不半份覆盖 |
| 入库保真 | 彩色 SVG 按绘制顺序提可见区域边界，同色并区、保留非零/奇偶孔洞与可见描边；拒绝未扁平化的半透明素材。稀疏矢量禁五点均值挪动拐角；整数中间视口与向上取整余量防二次裁剪丢边。G-code/许可文本先规范为 LF 再算产物 SHA；原始许可 SHA 另存。每类至少20件且逐件预览复核后才设 `reviewed=true`，软件复核不替代纸面签核 |
| 未开放面 | UGC 上传/评分/搜索不在本期；任何用户上传入口属二期且须另过隐私与内容评审（参照 09-12 TG 备份单独同意教训） |

#### 10.4.24 纸张设置、设备行程与产物快照（2026-09-14）

本节实现用户已确认的 A4/A3/A2、自定义、横竖版与 XY 交换；设计和验收清单见 `design/paper-settings-20260913/`。软件、板上、手机与大行程纸面证据分别登记在 readiness。

| 字段 | 约定 |
|---|---|
| `paper` | `A4` / `A3` / `A2` / `custom`；预设物理尺寸分别为 210×297、297×420、420×594mm |
| `width_mm` / `height_mm` | 50–2000mm、最多一位小数；预设必须与名称一致，自定义由用户填写 |
| `landscape` / `swap_xy` | 严格布尔；先按横竖版确定纸面方向，再扣四周留边，最后交换 XY 映射机器轴 |
| `margin_mm` | 四边相同，0–50mm；扣边后每个撰写边至少 20mm；画图与文字原有内部排版边距继续保留 |
| `max_x_mm` / `max_y_mm` | 从当前原点起已核对的正向可绘制范围，10–2000mm、最多一位小数；整个撰写区必须装入该范围 |

共享 Python 入口为 `paper_config.PaperConfig`，JS 与 S3 纯核心校验相同字段，不接受缺项、额外字段、非有限数值或以布尔冒充数值。默认配置为 A4、竖版、留边 10mm；`paper` 用 `swap_xy=true`、X277/Y190，`nopaper` 用 `swap_xy=false`、X205/Y290。增大任一轴必须有本次显式 `range_confirmed=true`；不读取厂商 `$130/$131` 推导范围，也不写这些 Grbl 设置。

**设备与门户**：旧 `status.paper` 保留有纸/无纸遥测语义；新增 `status.paper_config={schema:1,config:{...},marker:"p1:...",updating?:bool}`。标记依次编码 paper、宽×10、高×10、landscape 的 0/1、边距×10、swap 的 0/1、X×10、Y×10，以冒号连接，避免浮点 JSON 表示差异。字段缺席表示旧固件，仅沿用默认档并禁用设置；字段在场却非法或 `updating=true` 不得降级猜 A4。

- `GET /api/settings/paper?device_mac=<完整MAC>` 返回 `config/marker/device_mac/machine/capable/online/page_width_mm/page_height_mm`。登录/手机号和当前设备归属闸保持；`online` 同时要求设备 `connected/ready=true`。离线仅可展示该归属最近一次合法读回，不可据离线缓存发送或修改。
- `POST /api/settings/paper` 接收 `{device_mac,config,expected_marker,range_confirmed}`，仅当前选中设备可写。Portal 先取新状态并核对 expected，再调用 `hutuji.paper {config:<紧凑JSON字符串>,expected:<marker>,range_confirmed:<bool>}`；设备仅在终态、worker 收敛且无预览/运动事务时保存 NVS。Portal 核对工具确认后再读状态，配置与标记均一致才报成功。账号/设备/绑定变动作废旧回包。
- 设备屏幕可选预设、横竖与交换并显示已存范围；选择预设不能隐式改行程。设置改变使旧预览不能静默重解释，重新生成后才可发送。

**生成和发送**：图画、照片风格、文字、笔顺、文档、表格、语音与手绘在事务入口冻结同一配置。文字按新页宽高重新折行/分页，字号与笔顺不随纸张整体拉伸；nopaper 不支持多页的限制保持。内容缓存、inflight 和 Redis payload 包含完整配置与目标；后台 worker 不再另猜机型。`job-context.json` 及收藏 `.context.json` 保留目标，保存/分享/重发核对本次绑定、配置及每页内容。

每份 G-code 配套非公开的 `.paper.json`（最多 2048B），形状为 `{version:1,paper:{...},sha256:<正文摘要>}`。文件服务读取普通文件并检查链接/竞态与内容 SHA，再按该配置过 §5，返回 `X-Hutuji-Paper`；损坏和不匹配统一 404，伴随文件随产物发布、OSS、收藏、清理而保存或回收。SHA 绑定本地元数据，不替代 §5.1 的传输 `Content-Length`、CRC32 或 HTTPS。新 S3 下载时同时核对本机 marker，无标记只允许默认纸张，任一不符在首行发送前拒绝。

**云端设备身份与发布顺序**：配置了 `HUTUJI_USAGE_INGEST_SECRET` 的云端 `hutuji_draw/write/teach` 必须传完整 MAC `device_id`。MCP 通过同机 `/api/internal/paper` 读取 `{device_id,machine,paper,owner_stamp}`，完成后 `/api/internal/paper/push` 复核归属和产物再推预览；两路只接受 loopback 与 `X-Hutuji-Usage-Secret`。未配密钥的独立生成服务仅用固定默认档。上线必须先准备设备能力与角色提示，再接门户内部接口，最后切换严格生成端；队列切换先暂停入口、排空旧队列、停止旧 worker，禁止混跑新旧 payload。回退前恢复默认纸张并确认无任务，不能把大幅产物交给旧校验链。

### 10.5 设备对话上报（隐私面明示，2026-08-31 落地 xiaozhi `0d041c9`；findings #30 R6）

| 面 | 约定 |
|----|------|
| 内容 | **整段会话文本**：`role + content + session_id + MAC`。固件侧 `kMaxContent=500` 截断、环形队列 cap 8 丢最旧、Idle 空闲冲刷、**失败即丢不重试**（`hutuji_conversation_report.cc`） |
| 传输 | HTTPS `POST /draw-upload/api/device/conversation/report` |
| 受理 | MAC 归一化后查绑定表，**未绑定呼图账号的 MAC 一律 403**（受理面 = 绑定 MAC 白名单）；role 走归一化白名单，非法 role 400；服务端再按 `DRAW_PORTAL_CONVERSATION_MAX_CONTENT`（默认 500）截断；`conversation_dedupe_key`（MAC+session+role+content+client_ts）幂等去重 |
| 留存 | `DRAW_PORTAL_CONVERSATION_RETENTION_DAYS`（默认 **90 天**）；每次上报顺带 `purge_conversation_before` 清过期事件——访问时清理，无后台线程（同 §5 TTL 模式） |
| 消费 | draw-admin 会话视图，运营面可见全部上报文本 |

**明示**：上报内容是用户与设备的完整语音对话文本，不是元数据摘要；购买/部署即接受该面。固件无本地持久化（重启即清），服务端唯一副本在 draw-portal 库内按上表留存。

### 2026-09-25 §10.3 / §10.4 绑定与记忆补充

绑定v2探活须解析真实工具状态、拒绝错误信封，并核对完整device_id与签名MAC；S3在线而Grbl离线不妨碍绑定，pending不消费扫码证明。云端记忆MAC统一小写并延用小写MAC旧路径，非MAC标识用原始标识SHA-256独立文件；旧歧义桶不自动迁移、仍保留，核实归属后再人工恢复，分桶不替代身份认证。筛选与全部回忆同受100条/8000字符预算。设备侧按Unicode码点限50/500字，严格恢复JSON，重复主题后写覆盖；回忆40条/4000码点，NVS软限仍3500字节，保存提交成功才可声称已记住。记忆不因绑定变更自动清空。设计与证据见 [本轮复审](design/binding-memory-motion-20260925.md)。

### 2026-10-08 §4 / §10.4.5 / §10.4.22 应答与自愈补充

2026-10-08 奎享参照续审补充（本地修复候选，板上状态看readiness）：受限reset后的`G92 X/Y Z0`必须独立等到本行ok且会话未变，才能发送固定G1归位；逐行模式不得连续发送两行再等两个ok。流内丢应答兜底核对最后XYZ目标，纯Z笔控不豁免；状态查询期间到达的error/Deferred不得作为迟到ok清掉，无法确认完成的超时先走既有受控停止再发布终态。正常流成功出口发布Quiesced，异常出口保持Failed。窗口、速度、行序、机械包线与换纸逻辑保持原约定。回归与来源见`design/kdraw-runtime-review-20261008.md`。

- `draw/repeat/manual/pen_test` 忙拒绝、试笔期间 `pause/resume` 拒绝、换纸期间 `pause` 拒绝均返回含非空 `error` 的对象；同预览重入与已暂停/已继续等幂等成功保留。Portal 和 MCP 共享设备业务判错，逐层检查 `success/ok/isError/error` 后再拆信封；兼容旧固件的完整拒绝文案，不按 HTTP 200 推断受理。推图被拒绝时不继续查询或自动确认其他预览。
- OTA 下发遇 HTTP 504、TimeoutError（含 URLError 包装）、RemoteDisconnected、ConnectionResetError 或 IncompleteRead 时，命令可能已送达，保持 `uncertain`，按原目标版本与进度对账，不提前释放升级等待或重复下发。
- S3 自愈任务与 WithCaps 创建接口成对删除；10s 宽限和20s通知结束均核对仍为 error、TLS失败计数仍达阈值、无运动/OTA/预览/停止收尾所有者。最终复核到 `esp_restart` 持续持有 `stream_mutex_`，锁争用则放弃本轮；不与新任务发布交错。StartDraw 或预算成功清零计数后，旧自愈不得据残留 error 重启。

### 2026-09-25 §3 / §9-A 短段网络流控补充

窗口化普通行保留512字节上限，并增加最多8条未应答行的限制；两者同时满足才发下一行。payload+LF计数、逐行单独发送、FIFO的ok/error配对、暂停/停止纪元、换纸独立编排和fresh Idle收尾均保持原契约。应答队列仍按字节窗理论最坏条数保留容量，不因发送节流缩小；设置/控制命令仍用独立逐行事务，不进入普通行窗口。8条依据量产机实测16段TCP队列满与短轨迹A/B选取，是当前实现值，不是Grbl上游常量或通用最优值；不同SDK/无线条件必须重新实测。量产Grbl的WiFiServer先begin后setNoDelay，避免Arduino1.0.4启动清零使新客户端回到Nagle模式。官方版本、现场队列快照、对照结果与未验证边界见 [卡顿调查](design/binding-memory-motion-20260925.md)，实际刷机/验收状态以readiness为准。

### 2026-10-08 §10.4 小程序交互补充

- 创作/AI写文/排版/笔顺请求显式300s、发送150s，普通读取仍20s；发送状态保留完整detail并提供主动查看控制页入口。不因超时自动重复生成、下发绘图或OTA。
- AI回稿不得覆盖用户等待期间的新编辑；原稿与候选仅留当前页面内存，账号切换清除。照片原图草稿/精修版的预览、保存、发送和分享绑定同一选中job_id；切换时旧分享令牌及旧回包作废。精修等待超时可重新读取既有job，不重新生图。
- 纸张未保存编辑只在同账号、同设备且原marker未变时恢复；设备或配置变化改读实际配置，不能把旧机器范围移交新设备；保存仍经原marker、范围确认与设备回读，无变化不重复写入。
- Wi-Fi密码保持仅内存，离页隐藏、网络切换清空旧密码；未扫码时不能提示连接空热点。OTA等待受绝对时长约束，结果未确认时只核对原升级MAC及目标版本；新活动设备的版本不能结清旧设备升级。

### 2026-10-09 §3 / §4 / §9 / §10.4.21–22 事务与输入边界补充

- 正常收尾、停止归位和调速事务固定连接与reset banner身份；每次普通命令在Pipe写锁内使用`SendLineForSession`核对，fresh Idle等待沿用调用方身份，禁止沉降或等待后重新接受新会话。停止退出Hold的`~`使用同锁内校验的`SendResumeForSession`；失败仅关闭原连接，正常归位收到用户abort时不拆停止worker仍持有的连接。调速受理时保存banner，跨会话失败不补写剩余轴。既有指令、动作、速率与机械范围不变。
- 量产Grbl同步Web命令入口限4096字节、256行；超限先返回413且零入队，拆行索引不回绕，队列满立即停止追加并返回原Error语义。ESP命令允许255字节并显式NUL终止，256字节及以上拒绝，不静默截断；保留原空行与实时字符处理。
- OTA的HTTP200或网关success不等于设备明确受理。只有有界解包后的设备结果含严格布尔`ok:true`才转明确upgrading；空/损坏JSON、HTML、标量、缺确认或非布尔ok保留uncertain，按原版本/设备进度对账，不消费上一轮failed，也不解除忙闸重复下发。明确拒绝仍由共享逐层判错处理。

来源、回归与发布验收见`design/three-repo-deep-review-20261009.md`；精确竞态窗口为host证据，板上验证范围以readiness为准。

### 2026-10-09 §3 / §7 / §10.4.5 发布后续审修复契约

- 首笔`G92 Z0`和试笔Z5/Z0固定任务受理时的连接/banner，发送复用`SendLineForSession`；沉降、应答和fresh Idle均复核同一身份。试笔从受理到收尾持有Pipe任务会话，失败/创建失败撤销；普通出图/重画从Run建立身份，受控断连恢复完成才更新。不得在每次发送前重新接受当前banner；Z值、速度与沉降时间不变。
- 量产Grbl每个客户端独立记录超长物理行拒收状态。首次Overflow后丢弃后续所有普通字符（包括backspace），直到CR或LF；换行自身触发Overflow时已到边界，不丢下一行。软复位初始化清除拒收状态，实时字节仍由Serial先行消费，合法payload上限仍254字节。此条修复原“超长行从未执行”实现缺口，不放宽输入预算。
- `hutuji.draw`只在有界解包后精确得到`previewing`才认明确受理；空、坏JSON、HTML、仅网关success/ok或其他结果返回“绘图受理结果尚未确认”，不自动确认、不自动重发。多层编码的error仍逐层判错；状态损坏时不以字符串包含awaiting_confirmation判定可确认，confirm坏正文也不能当started。
- 新S3 `status.confirm_by_url=true`声明支持`hutuji.confirm`可选参数`expected_url`与`expected_preview_url`；两者有任一非空就必须成对、与当前作品逐字匹配，校验和撤销awaiting在同一stream锁内完成，不匹配返回“预览已变化，请重新核对作品”。门户自动确认总是携带本次两个地址；旧固件不声明能力时保留已送达预览并提示人工确认。屏幕/语音原有无参确认保持。明确started才即时报成功；确认回包丢失仍按后续状态对账，但不把这种状态观察提升为完整任务身份凭证。

发布顺序为先升级S3并验证能力，再部署共享`tool_result.py`和Portal `push.py`；若后端提前更新，旧设备会落人工确认，不静默发送无参confirm。当前仅本地候选，实际部署以readiness为准，见`design/three-repo-next-review-20261009.md`。

### 2026-10-10 量产配方、台账与灰度约定

量产工位配方`production/nopaper-freenove-v1.json`冻结速度6000/6000/1200、加速度3000/3000/3000，已获用户批准；参数由工位逐轴写入并全表核验，源码默认/NVS旧值不得当作出厂配方。工位只写6个明确参数，$1=255、$20/$21/$22=0只读，不写引脚、授权或行程。4小时、30次标准图、尺寸误差≤1mm、零失步/漏笔为用户批准的首批试行门槛，仍必须有每台真实记录。

首次烧录只允许空白NVS且每台独立生成工厂记录；返修保留NVS/用户密钥，整片备份、ROM MAC/实际分区/镜像SHA、未改分区一致性核验后执行，失败不重发。S3验写后hard-reset，Grbl按工位SOP重启。台账按SN+代次保存历史证据，正在操作时禁止返修/放行，进程中断遗留锁须人工核实后显式解除。

参数识别只接受唯一完整STA报告的末尾MAC和独立VER行，不从SSID/AP或截断字段推断身份。人工测量携带SN与当前generation；自动回写复核操作令牌及代次，解除旧操作后拒绝旧结果。整片备份在写入前成为带SHA/大小的台账附件，成功/失败及放行报告随归档迁移，附件路径为恢复依据。固件版本取app descriptor，内部校验和/SHA与目标芯片也须通过，正文版本文字不能替代。

Portal稳定板型条目可带`rollout`：id、percent(0–100整数)、devices(MAC名单)、candidate(版本/路径/SHA/字节数)。同一id+MAC稳定分桶，未入组仍选稳定版，错误策略拒绝；check与start均按原绑定MAC选取，发布目录在期间改变版本则拒绝本次升级。设备直读latest仍为稳定版，灰度候选不写latest，升级仍走原busy/所有权/不确定结果闸，不自动触发。

**PR10修复补充**：Portal发送OTA前用第二次设备状态的`firmware_version`复核目标严格更新；缺失/无法解析回`check_failed`，同版或更高版回`up_to_date`，均不登记目标、不发送OTA并释放本轮占位。产线`released`保留历史签核事实，`status/export/batch`同时检查当前代次release的三份报告附件（存在、大小、SHA及附件登记齐全），异常列入`release:attachments`；返修后只对新代次计算缺项，旧报告仍由历史归档检查。

## DR10 软件修复补充契约（2026-10-10，覆盖对应旧实现口径）

- §1.2/§3：量产Telnet更换客户端或结束连接时同时清接收环形缓冲、发布Serial队列会话代次。网络线程不得直接写协议半行；协议线程收到队列屏障后清自己的半行/超长行丢弃状态再接收新会话。读出的旧代次字节不能排入新队列；其他client半行保留。已完整提交给运动规划器的命令不在此网络清理范围内。
- §1.5：S3每轮配网重新读取当前SSID凭据，当前SSID缺失时不跳配、不发上一轮凭据。开放网络使用`[ESP101=]`明确写空STA密码；旧`[ESP101]`保持查询；仅`Sta/Password`允许空字符串，AP及本地认证密码维持原最短长度。旧Grbl拒绝新语法时中止本轮，不继续切STA或重启，须先更新Grbl。非空密码、SSID首尾空格和右括号原样保留。
- §4：pause/resume仅接受streaming/paused绘制阶段；previewing、awaiting_confirmation和downloading等阶段不得向Grbl发送`!`/`~`或更改确认闸。nopaper缺少电机及压纸机构实时回读，`motor_en`和`panel_hold`均返回unknown，不能从Run/Idle或上次ESP901推断失能。完成/停止后的$MD及应答是独立的释放证据，不修改运动和失能策略。
- §10.4.22：设备`hutuji.ota_start`必填64位十六进制sha256；当前/目标版本必须严格为`hutuji.N.N.N`，目标严格较新，同版/旧版拒绝，非法版本/摘要拒绝。入队及实际执行点均复核版本，OTA继续持有原运动占位，失败释放、成功到重启才释放。下载层按收到的全部字节增量SHA-256，检查完整Content-Length、分区容量及镜像内版本；摘要/版本/长度不符不结束OTA或切启动分区，所有句柄、缓冲及hash上下文异常路径均回收。它是摘要绑定，不新增固件签名机制；非hutuji上游OTA入口保留默认可选校验参数。
- 产线追溯：成功flash_s3/flash_grbl必须登记flash_backup；pending/fail记录一旦已完成备份或尝试写入，同样不得丢失备份登记。status只核当前代次，archive核所有历史代次；release的三份签核报告也必须保留登记。历史released事实保留，缺证不允许新放行。
- 绑定回写：token更新必须在同一SQLite事务中比较user、MAC、bound_at和实际使用的旧密文，只有一个并发写者可成功；落败请求不得覆盖赢家或继续重试设备动作。调用链自愈成功后携带新的密文快照；当前绑定读取与旧请求身份复核不可混用。此约束不依赖云端是否使旧token失效。

**2026-10-11 固件续审补充**：绘图暂停/继续的实时字符必须核对原任务连接代次与reset banner，重连后旧任务不得控制新会话；暂停仍需保留对普通行背压的抢占能力。设备OTA入口除板型参数外，URL须精确指向本机板型和目标版本的不可变发布路径；下载异常须走失败恢复及占位释放。量产Grbl的WebUI JSON字符串须转义，WebSocket输出超过单帧缓冲时必须完整分块且不得越界。源码与本地验证见[续审修复](design/firmware-review-fixes-20261011.md)。

**2026-10-11 停止收尾控制隔离补充（§4/§4.1）**：`RequestAbort`受理并发布`abort_requested_`后，虽`state`暂留`streaming/paused`、`busy_`继续占用，外部`pause/resume`仍须在`stream_mutex_`内、修改暂停状态或发送实时字节前返回既有忙拒绝`error`。重复暂停/继续不能以幂等成功覆盖停止所有权，也不得再发`!`/`~`或更改控制纪元；停止worker内部为退出Hold发`~`、排空、抬笔、归位及确认`$MD`保持原顺序。停止任务创建失败回滚后，正常暂停/继续能力不变。

### 2026-10-20 CR20 七项修复契约（本地候选）

- **静默 Web 查询**：量产 `/command_silent` 的 ESP 命令也须传有效 `ESPResponseStream`，使用既有默认构造的 `CLIENT_INPUT` 丢弃输出；不得传空指针，也不得向其他客户端广播查询结果。非静默响应和命令执行语义保持。
- **聊天卡确认 supersede §10.4.13 的旧无目标兼容**：`POST /api/voice/confirm` 必须有 `job_id` 与生成时的 `device_mac`。服务端只从当前用户的 ready 任务及 `job-context.json` 取得作品/设备，不接受客户端自报能力地址；缺失身份、旧卡、错误设备/归属或坏快照均拒绝。先确认设备声明 `confirm_by_url=true`，再调用 `hutuji.confirm` 并成对传 `expected_url` / `expected_preview_url`，由 S3 原 stream 锁比较当前作品；只有明确 `started` 才记成功。设备不支持时要求在设备屏幕核对，不能无参降级。屏幕/口头当前任务确认和普通控制页兜底的既有语义不变；本批未改全局 abort。
- **激活任务生命周期**：一次性任务须先从持有 HTTP/载荷的函数正常返回，让 RAII 析构执行，再 `vTaskDelete`。`Http::Close` 不替代对象析构。
- **文本文档编码**：TXT/CSV 嗅探在既有上传字节预算内验证完整编码；不得把固定采样边界切出的半个 UTF-8/GBK 字符当成文件损坏。真实坏编码仍拒绝。
- **语音工具事务**：工具结果已经产生后，后续 LLM 措辞/回包失败须保留工具链、结果与已生成预览，给固定说明。未执行工具时的 LLM 失败仍报错；不得重新调用已经完成的工具来补一句回复。
- **冷备份删除**：上传后校验 false 或异常分支也必须检查回写是否找到本地行；用户已删除时尽力撤回已上传消息，不落可恢复台账。行仍在则保留 failed 状态以供重试。
- **会话 UTF-8**：保留 500 字节预算和失败即丢策略；截断必须退回完整码点边界，保证 cJSON 输出能被 UTF-8 JSON 解码器读取，不能一条中文残字符拖掉整个批次。

实现、回归与发布边界见 `docs/design/three-repo-review-20261020.md`；这些是本地软件候选，未部署或刷机。

### 2026-10-21 R20F 续审修复契约

- §10.4.5：用户主动删图库作品时，删行与台账 removed 必须在同一 SQLite 写事务提交。恢复下载结束后，在同一写事务重新核对 saved_id/user_id/file_id/message_id 与未删除标记，再原子落文件和按需重建行；失效快照计 skipped。自动 trim 保留台账资格，重建仅预览且无 G-code；不使用 INSERT OR REPLACE 覆盖现存作品。
- §10.5：每次会话上报只消费队首连续同 session_id 的事件；遇下一会话留队，下一批再发。cap=8、UTF-8 完整边界、Idle 冲刷与失败即丢策略保持。
- 量产 Grbl 查询守卫：依赖 notCycleOrHold 的查询在 Cycle 或 Hold 返回 IdleError，Idle 正常执行。用户于本批明确授权 Settings.cpp 窄谓词修复；不改运动设置、换纸、引脚或授权语义。

### 2026-10-21 量产屏幕显示约束

Freenove量产逻辑屏幕为320×240横屏。主题保持语义分色，触摸按钮高度至少56px，启用/禁用状态继续由原Job状态闸决定。停止在控制主页独占底行，任务活跃时仍回主页保留停止入口。预览加载时确认禁用、图像就绪后才启用；固定提示/图片/底栏预算按实际字体行高验证，图片等比完整显示，不裁掉作品。界面样式不改变确认/取消回调、运动命令、配网载荷、引脚或NVS约定。实现与未上板边界见`docs/design/s3-screen-polish-20261021.md`。

### 2026-10-21 配网与绑定交互补充

小程序误扫本域另一用途的合法设备码时保留原身份校验，明确用途并由用户确认跳正确页面；损坏/部分身份/异域码仍拒绝，不能把用途判断当绑定成功。切换SSID或扫码目标清理旧密码与显隐/锁定状态，临时进入系统Wi-Fi设置的同次配网保留内存密码，卸载/退出仍清空。两次均未建立TCP且未发送载荷时返回明确未发送；已发送后断流仍为待确认，不自动补发。绑定页隐藏停止后续绑定请求/计时，回屏先查原二维码身份的已完成记录；缺完整身份不拿账号其他设备冒充成功。原生扫码相机暂时遮挡不丢弃该次扫码。量产二维码固定图片与文字预算，保留至少2像素整模块与四模块静区；提示说明用途与小程序入口，不改变载荷、密钥、nonce或签名。软件状态与边界见`docs/design/onboarding-improvements-20261021.md`。

### 2026-10-21 N21停止失败与会话提交补充

S3断流归位worker独立发布运行/成功/失败/移交结果；未取得fresh Idle、发送/应答/归位或电机释放失败不能发布aborted或“已停止”。终态在worker收敛后决定，无需运动的预览/下载取消保持原行为，重画清除旧归位结果。Grbl实时输入在Telnet会话锁内复核代次并提交，与换连/断开同锁线性化；网络输出不进入Serial自旋临界区，非Telnet实时命令与普通入队边界不变。普通Grbl查询守卫在Cycle或Hold拒绝，量产既有OR判据保持。

配网页一般后台/离页停止TCP、计时与后续切网/认领重试；提交结果未知时回屏先GET核原身份，不自动再发凭据或认领。系统Wi-Fi设置同次往返保留内存密码；同账号原生扫码临时遮挡保留当前扫码，账号变化丢弃旧数据并结算自身操作标记，旧扫码不能清新扫码标记。版本与发布/实机证据见`docs/design/three-repo-next-review-20261021.md`和readiness。

### 2026-10-21 R2发布后收尾与恢复补充

重画在真正创建绘图任务前保持downloading准备态，停止只置取消；原入口在创建前同锁核对并收口取消，不创建归位worker、不释放其他worker占位。绘图任务已启动后的取消仍走既有停止归位流程。试笔已尝试运动后，应答或fresh Idle失败不能被取消标志覆盖成已停止；无运动取消保留成功。

量产Telnet使用独立接收会话活跃标记：断连时即使WiFiClient布尔为false，也必须一次性清网络环、推进Serial会话代次；重复观察同一断连不反复推进，实时提交拒绝未活跃会话，其他client半行不变。旧款无MAC配网码在已发送等待后回屏保留done/manual与下一步绑定入口；已确认bound保持完成，不因单次网络失败回退。登录代次变化仍重新认证。实现和发布真值见`docs/design/three-repo-postrelease-review-20261021.md`。
