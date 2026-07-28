# 16 · WiFi 设备发现与连接：奎享 vs hutuji（含一处我方真实缺陷）

> 源文件（kxnx 仓 `kx/tools/dump_agent/decompiled_full/`）：
> `machine/ui/esp32grbl/a.java`（连接对话框 + 端口默认值）、`b.java`（mDNS）、
> `c.java`（mDNS ServiceListener）、`d.java`（SSDP 回调）、`e.java`（手输 IP:端口）、
> `f.java`（历史列表）、`MachineItem.java`（SSDP `description.xml` 校验）、
> `machine/ui/esp32grbl/a/c.java`（配网状态页）、`data/HistoryConnectedMachine.java`
>
> 我方对照：`xiaozhi-esp32/main/boards/lichuang-dev/hutuji_pipe.cc`
>
> **本文最重要的一条不是逆向发现，是 §四 我方的缓存 IP 活锁**——单独看奎享看不出来，
> 是把两边发现策略并排才暴露的。

## 一、奎享的发现是三级回退，不是单一手段

连接对话框 `ui/esp32grbl/a.java:44-51` 建三个 Tab，优先级从左到右：

| Tab | 实现 | 机制 |
|---|---|---|
| **历史连接**（`histroy_connect`） | `f.java` + `data/HistoryConnectedMachine` | 持久化过的 IP 列表，等价我方 NVS 缓存 |
| **自动搜索**（`auto_search`） | `b.java` | **两种发现并行**（见 §二） |
| **手动输入**（`user_connect`） | `e.java` | IP 文本框 + 端口文本框（预填默认端口） |

三条路径最终都汇到同一个 `a(MachineItem)`（`a.java:66-96`）：
先断开旧连接 → `new machine.b.d(...)` → `b(address, port)` → **成功才写入历史**：

```java
boolean bl = ((machine.b.a)object).b(machineItem.getAddress(), machineItem.getPort());
if (bl) {
    HistoryConnectedMachine h = (HistoryConnectedMachine)Storage.getStore(...);
    h.addItem(machineItem);            // ← 仅 TCP 连上就写历史，未校验是否 Grbl
    Storage.flashStore(h);
}
```

> 注意：**奎享这里也只以「TCP 连上」为写历史的条件**，没有协议校验。
> 但它不会因此卡死——因为历史只是 UI 列表里的一个候选项，人可以另选一个；
> 我方是无人值守自动选，同一个宽松条件就变成了缺陷（§四）。

## 二、自动搜索：mDNS + SSDP 双通道（此前文档只写了 mDNS）

`ui/esp32grbl/b.java:48-53` 同时起两套：

```java
this.e = new c.a.a.a.a.a();              // :48  SSDP/UPnP 客户端
object = c.a.a.c.c.c();
this.e.a(((c.a.a.c.d)object).a(), new d(this));   // :50  注册 SSDP 回调 → d.java
this.a = JmDNS.create();                 // :52  mDNS
this.a.addServiceListener("_http._tcp.local.", this.f);   // :53  → c.java
```

### 通道 A：mDNS，查的是 `_http._tcp`（不是 telnet）

`c.java` 的 `serviceAdded` 先 `requestServiceInfo` 再在 `serviceResolved` 里取
`getHostAddress()`，端口取 `a.a(...)`（见 §三），**服务类型是 `_http._tcp.local.`**。

即奎享靠 **ESP3D WebUI 的 HTTP 服务**来发现设备，然后连一个**完全不同的端口 23**。
mDNS 记录里的端口被丢弃不用。

### 通道 B：SSDP/UPnP，靠 `description.xml` 认型号

`d.java:27` 把发现结果包成 `new MachineItem(f2, 23, list)`，走 `MachineItem` 的
SSDP 构造器（`MachineItem.java:24-53`）：

```java
Object object2 = ((f)object).b();
if (!((String)object2).endsWith("description.xml")) return;     // :26 必须是 UPnP 描述文档
object2 = new URL((String)object2).openStream();
... SAXBuilder 解析 XML ...
object2 = this.getChild(root, "device");
if (object2 != null && (object2 = this.getChild(object2, "modelNumber")) != null
        && object2.getText().equals("Marlin")) {               // :36 型号必须是 "Marlin"
    this.address = ((f)object).a().getHostAddress();
    this.port = n2;                                            // :38 端口用传进来的 23
    ... 去重后加入列表 ...
}
```

**这是奎享唯一的「确认对端是自家设备」的校验**，而且它在 SSDP 通道上：
下载 `description.xml`、解析、比对 `<modelNumber>Marlin</modelNumber>`。

> `modelNumber == "Marlin"` 是 ESP3D WebUI 的固定值，**不代表运动固件类型**
> （跑 Grbl 也报 Marlin）。别误读成「奎享把这类机器当 Marlin 机」。
>
> **我方取用的是思路，不是这个字段**：值得借鉴的只有「发现阶段就做协议校验、
> 校验不过不入候选」。我方有更直接的手段（`$I` → `[VER:`），见 §四。
> 奎享上位机能否发现我方设备不在本项目范围内，不做讨论。

两个通道都往同一个 `JList` 加，并且**都在 `synchronized (jList)` 里做**
（`c.java:31-34`、`d.java:25-29`）+ `MachineItem` 构造器内按 address 去重。

## 三、端口默认值按机型分（8080 / 23）

`ui/esp32grbl/a.java:137-142`：

```java
static /* synthetic */ int a(a a2) {
    if (machine.f.a().e() instanceof machine.marlin.a) return 8080;
    return 23;
}
```

| 机型 | 默认端口 | 用途 |
|---|---|---|
| Marlin（`machine.marlin.a`） | **8080** | 大概是 ESP3D 的 HTTP 端口 |
| **其余（含 Grbl / ESP32Grbl）** | **23** | Telnet 裸串口流 ← 我方就是这条 |

这个值同时被 mDNS 通道（`c.java` 传 `a.a(...)`）和手输 Tab 的预填（`e.java:26`）使用。
SSDP 通道 `d.java:27` 则**硬编码 23**，没走机型判断——即 SSDP 发现的设备一律按 23 连。

另有一处独立的硬编码 23：配网成功页 `ui/esp32grbl/a/c.java:59`，
收到串口消息 `Connected with <ip>` 后直接 `new MachineItem(null, ip, 23)` 存进历史。
配网流程本身是往机器发 `ESP115 ON`（`a/c.java:33` 的 `lib.a.a(115, "ON")`）。

## 四、我方缓存 IP 活锁（**已修复，错误候选 HIL 仍待补**）

缺陷发现时 `hutuji_pipe.cc` 的 `ConnectOnce()` 是：

```
0) NVS 缓存 IP → TryConnect(2s)      命中即 found=true，【跳过扫描】
1) 子网扫描 .1 ~ .254，50ms/地址     首个 TCP 连上 23 的即 found=true，break
2) getpeername → 若与缓存不同则 SaveCachedIp()   ← 【只要 TCP 连上就写缓存】
```

协议层校验在别处：`ready_` 只有收到 `[VER:` 才置 true（`:391-394`），
`Job` 的所有出图入口都查 `IsReady()`（`hutuji_job.cc:124/238/271/...`）。
**所以不会把错误设备当写字机去出图**——这点是安全的。

**但连接与缓存这一层没有任何协议校验，会自锁**：

1. 子网扫描**从 .1 开始顺序试**（`:239` `for host = 1; host < 255`）。家用路由器
   通常就是 `.1`，且部分型号开着 telnet:23。
2. 一旦连上 `.1`，`break` 退出扫描 → `SaveCachedIp(.1)` 落 NVS。
3. 此后每次 `ConnectOnce()`：缓存命中 → `found=true` → **`if (!found)` 为假，
   永不再扫描子网**。
4. 连上后发 `$I` 收不到 `[VER:` → `ready_` 恒 false → 出图全部被拒。
5. recv 循环 21s 判死（`kSilentPollLimit`）→ break → 重连 → **回到第 3 步**。

即：旧实现中**一个错误但 TCP 可连的缓存 IP 会永久阻止发现真写字机**，且靠重启无法
自愈（NVS 持久化）。这一控制流缺陷已经修复，不再是当前实现状态。

> 严格说明证据强度：以上是**逐行读代码推出的控制流**，`.1` 开着 telnet 的家用路由器
> 我没有在本机实测复现。但「缓存命中就跳过扫描」+「写缓存不校验协议」这两行是确定的，
> 活锁只需要一个前提：局域网内存在任何一台 23 端口可连且不回 `[VER:` 的主机。

### 已落地修复（2026-07-28，xiaozhi 侧，不碰 Grbl）

| 改动 | 当前实现 | 内容 |
|---|---|---|
| **A. 候选先验明正身** | `VerifyGrblPeer()` | 缓存命中和扫描候选都先被动读取 Telnet banner，1.5s 内必须包含 `Grbl ` 才接受；失败即关 socket，扫描继续 |
| **B. 只缓存已验证候选** | `ConnectOnce()` | `SaveCachedIp()` 移到 peer 校验成功之后，普通 TCP connect 不再污染 NVS |
| **C. 失真缓存自愈** | `ConnectOnce()` | 缓存 IP 连到非 Grbl 主机后标记 suspect；本轮扫描仍找不到写字机时擦除该 NVS key，下轮全量重扫 |
| **D. 区分唯一槽位忙** | `PeerCheck::ClosedBeforeBanner` | TCP 建立后立即被关视为 Grbl 旧半开连接仍占 `MAX_TLNT_CLIENTS=1`，不是缓存失真；先保留缓存快速重试，累计 5 次后才扫描旁路 |

选择被动 banner 而不是先发 `$I`，是因为 banner 在 Telnet accept 路径产生，不依赖可能被
换纸阻塞的 Grbl 主循环。banner 会被校验函数消费；空闲建链阶段仍主动 `$I`、抬笔、取位置并
做授权零位移探测，最终 `ready=true` 不依赖 banner 残段。`05f4563` 起，活跃绘图 session
重连是安全例外：先查 fresh `Changing`，禁止在状态未知时先发 `M5/G1` 探针（protocol §2.1.1）。

2026-07-28 的 `e035efc` + 当时未提交 diff 曾用 IDF 6.0.1 构建并烧录 COM6，正常缓存连接与后续任务
回归通过；随后源码固化为 `05f4563` 并再次构建，但尚未烧录。仍未专门搭建“错误 :23 主机 + 失真缓存”做对抗 HIL，因此当前证据证明代码路径
已改和正常链路不回归，不宣称该对抗场景已经硬件复现通过。

### 顺带记下的两个次要点（不影响正确性，先不改）

1. **扫描期长时间持锁**：`:238` 在整个 254 地址循环外层持 `sock_mutex_`，
   最坏 254×50ms ≈ 12.7s。期间其它需要 `sock_mutex_` 的路径（如 `CloseSocket`）会阻塞。
2. **只适配 /24**：`base = ip & netmask` 后 `base | htonl(host)` 只枚举**最后一个八位组**。
   若路由器给的是 /16（少见但存在），真机在 `x.y.5.30` 而 base 是 `x.y.0.0`，
   扫描只会试 `x.y.0.1-254`，**找不到**。当前家用环境几乎都是 /24，风险低。

## 五、双方发现策略总对照

| 维度 | 奎享 | hutuji 现状 | 评价 |
|---|---|---|---|
| 一级 | 历史列表（持久化） | NVS 缓存 IP | 同构 |
| 二级 | mDNS `_http._tcp` + SSDP 并行 | 子网扫描 23 端口 | 我方更暴力但不依赖 mDNS（曾因 mDNS 不稳去掉，见 CLAUDE.md 路线图②） |
| 三级 | 手动输入 IP:端口 | **无** | 无人值守场景本就没有「手动」，但缺一个「配置指定 IP」的旁路 |
| 协议校验 | SSDP 通道校验 `description.xml` 的 `modelNumber` | 候选先验 `Grbl ` banner；正式连接再 `$I` + 授权零位移探测 | 活锁已从连接/缓存决策层修复 |
| 选中方式 | 人在列表里点 | 自动取扫描到的第一个 | 我方无人兜底，故校验更重要 |
| 端口 | 机型分流 8080/23，SSDP 硬编码 23 | 硬编码 23 | 一致（我们只有 Grbl） |
| 写持久化的条件 | TCP 连上即写 | `VerifyGrblPeer()==Valid` 后才写 | 我方无人值守，采用更严格条件 |

## 六、证据边界

- 奎享部分仍为 CFR 反编译静态阅读，未抓包。§四 缺陷来自旧版我方源码控制流；修复已
  构建、烧录并通过正常链路回归，但**未用错误 :23 主机专门复现活锁及自愈**。
- `c.a.a.a.a` / `c.a.a.c.c` 是被混淆的第三方 SSDP 库（构造 `description.xml` URL 的
  `f.b()` / `f.a()` 未展开读），故「SSDP 通道」的具体协议细节（M-SEARCH 报文、
  超时、重试次数）**未查证**。只确认了「拿到一个以 `description.xml` 结尾的 URL 后
  下载解析并比对 modelNumber」这段业务逻辑。
- 奎享 mDNS 服务类型、SSDP 端口 23 硬编码、机型端口分流三条均为**字面代码**，强证据。
- 我方固件侧（`D:/Users/Grbl_Esp32/Grbl_Esp32/src/`，只读未改）：`Config.h:118-123`
  HTTP/Telnet/mDNS/SSDP 全开；`WebServer.cpp:206` 注册 `MDNS.addService("http","tcp",80)`；
  `WebServer.cpp:188-192` 的 `setModelNumber` 等已注释。**这些仅用于理解奎享发现逻辑
  的适用边界，不评估「奎享能否发现我方设备」**——那不在本项目范围内。
