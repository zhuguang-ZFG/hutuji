# 端到端联调 SOP（从零到「说一句话画出来」）

> 本文是 hutuji 整链联调的**执行顺序表**，给第一次接手的人/agent 照着做。  
> 动手前先读 `agent-handoff.md`（防呆）与 `release-readiness.md`（别跳阶段）。
> 原则：**自底向上、每阶段有明确通过判据；未过判据不得进入下一阶段**——
> 越靠上层的失败越难定位（语音链失败可能根因在 Telnet 抖动）。
>
> 契约真值：`protocol.md` v0.5。本文只讲「怎么验」，不重复定义契约。
> 标记：⛔ 阻断项（不过就停）｜🔬 待实测填数｜⚠️ 安全/机械风险点

## 阶段总览

| 阶段 | 验什么 | 依赖 | 判据 |
|------|--------|------|------|
| 0 | 写字机自身可用（授权/机械/串口） | 无 | 串口 `$I` 有 `[VER:]` + `[License] Authorized` |
| 1 | 写字机 WiFi + Telnet 可达 | 0 | PC `telnet <IP> 23` 发 `?` 回 `<Idle\|…>` |
| 2 | Grbl 侧 §9 三处小改已生效 | 1 | `[ESP901]` 回显含 `Changing=`；keepalive 生效 |
| 3 | ③ MCP 服务本地跑通（无硬件） | 无 | `mcp-server/scripts/e2e_local.py` 输出 PASS |
| 4 | ③ 产出契约合规（CRC32/512KB/限幅） | 3 | `curl -I` 见 `X-Hutuji-CRC32`；越界样本被拒 |
| 5 | S3 固件下载+转发（哑管道） | 1,2,4 | `hutuji.status` 显示 connected/ready/authorized |
| 6 | 小智云 MCP + 京东云 | 3,4 | 云端成功调用 `hutuji_status` |
| 7 | 语音端到端 + 单/多页出图 | 5,6 | 语音触发、纸上成图、`M30` 换纸成功 |
| 8 | 故障注入、恢复与标定 | 7 | 拔电/abort/CRC/缺纸行为符合契约并填实测值 |
| 9 | 小批量商业出货放行 | 0–8 | 发布清单、逐台记录、合规与客户资料全部签核 |

---

## 阶段 0：写字机自身可用

**为什么先做**：写字机是**成熟商业现役固件**，它自己不通，上面全是白搭。且授权是隐性前置——未授权时 G0–G3 全回 `error:110`，但 `$I`/`M...` 照常，容易误判「链路通了」。

1. USB-B 接 PC，串口工具连 **CH340，115200 8N1**。上电看 banner。
2. 发 `$I`，确认回 `[VER:…]` / `[OPT:…]`。
3. ⛔ **确认授权**：banner 或 `$I` 上下文含 `[License] Authorized`。
   - 若无 → 走人工授权 SOP：按 ChipID 算授权码 → `M800 P<码>`（超 float 精度时 `M800 P<高段> Q<低段>`，码=P*1000+Q）。
   - 授权写 NVS，**烧录不覆盖**，一次性持久。S3 侧**永不自动授权**（§6）。
4. 发 `?` 确认状态字 `<Idle|MPos:…>`。若回 `<Alarm>` → 走人工 `$X`/`$RST`，**不自动解锁**。
5. ⚠️ 机械确认（避免后续撞机）：当前 A4 行程记录为 `$130=210`、`$131=297`（**2026-08-21 用户口径 X 长边/Y 宽边，待实机 `$$` 复核**，若属实应对调为 `$130=297/$131=210`）；**无限位开关**，限幅责任全在 ③ 生成端（§5，X≤190、Y≤190——Y 已按用户口径收紧，X 待实机证据后抬 277）。
6. 记下 `$102`（Z steps/mm），供阶段 6 锁定抬落笔 Z 值（🔬 §5 待实测项）。

**通过判据**：`$I` 有 `[VER:]` **且** `[License] Authorized`，`?` 回 `<Idle>`。

---

## 阶段 1：写字机 WiFi + Telnet

**为什么**：方案 E 的物理层。⚠️ 下列设置名沿用 ESP3D 通用写法，**本 fork 确切拼写未核对**（🔬 串口一试便知，见 §1.3）。

1. 串口下发（写入即存 NVS）：
   - `$Sta/SSID=<家里WiFi>`、`$Sta/Password=<密码>`
   - `$Telnet/Enable=1`、`$Telnet/Port=23`
2. **固定 IP**：路由器后台给写字机 MAC 绑静态 DHCP 租约（首选），或 `$Sta/IPMode=Static` + `$Sta/IP=<addr>`。
   - 固定 IP 仍是运维基线；S3 会先试 NVS 缓存，再扫描本机 `/24`，且候选必须返回 Grbl banner 才会被接受并写回缓存。固定 IP 可避免每次扫描和错误候选漂移。
3. `$I` 或 `[ESP110]` 查当前 IP。
4. PC 上 `telnet <写字机IP> 23`：
   - 发 `?` → 应回 `<Idle|…>`
   - 发 `$I` → 应回 `[VER:…]`
5. ⛔ **验完立即退出 telnet**。`MAX_TLNT_CLIENTS=1`（TelnetServer.h:31 硬上限），PC 占着槽位 S3 就连不上。
   - 定局：**Telnet = S3 独占；CH340 串口 = PC 只读旁观**（§1.1）。之后一律看串口，不再用 PC telnet。

**通过判据**：`telnet <IP> 23` 能收发，且退出后槽位释放。

---

## 阶段 2：Grbl 侧 §9 三处小改

**为什么**：不做这三处，**S3 拿不到任何换纸状态**（`PaperSystem.cpp` 63 处路由全是 `CLIENT_SERIAL`），§2.1 断连分流与 §4.1 abort 分状态**没有地基**。清单与行号见 `protocol.md` §9。

口径提醒：换纸机械/时序、运动、授权、U1-Grbl 协议**一律不改**；这三处只是消息路由 + 状态字段 + 网络层加固。

1. 实施 §9 的 A / B1′ / B2′（约 12 行）。
2. ⛔ **跑门禁（两扇，缺一不可）**：
   - 本仓抽检：`set GRBL_ROOT=D:\Users\Grbl_Esp32` 后 `python D:/Users/hutuji/scripts/agent_gate.py --profile full`（仅源码标记）。
   - **fz 真门禁**：`python D:/Users/zhugu/fz/scripts/agent_gate.py`（先设 `FZ_ROOT`/`GRBL_ROOT`）。
   **fz overall 非 pass 不得继续，也不得声称改好。** 本仓 `full` 绿 ≠ §9 完成。
3. 重烧固件（NVS 不覆盖，授权与 `$` 设置保留）。
4. 验 B2′：Telnet（或串口）发 `[ESP901]` → 回显应含 **`Changing=Off`**（原本只有 `Paper=/MotorEn=/PanelHold=`）。
5. 验 B1′：触发一次换纸（`M30` 或面板），确认 **Telnet 侧**能收到 `[PaperStatus] N`（改动前只有串口能收到）。
6. 验 A（keepalive）：见阶段 8 的拔电测试（🔬 实测回收时长）。

**通过判据**：`[ESP901]` 含 `Changing=`；换纸后 Telnet 能收 `[PaperStatus]`；门禁 pass。

**当前记录（2026-07-28）**：Grbl `8d13b5c` 已提交/推送并烧录 COM3；烧录时 bootloader、
partition 与 app 均 `Hash of data verified`，NVS 授权保留。串口最小核验：WiFi 回到
`192.168.1.13`、`TELNET Started 23`、`?` 回 `Idle`、`[ESP901]` 回
`Paper=No MotorEn=On PanelHold=Off Changing=Off`、`$130=210 $131=297`。该记录仍不等于
有纸 `Changing=On` 或自动换纸验收。

---

## 阶段 3：③ MCP 服务本地跑通（不接硬件、不需 LLM key）

1. `cd mcp-server && python -m venv .venv`
2. `.venv/Scripts/pip install -r requirements-dev.txt`（Linux 用 `.venv/bin/pip`）
3. ⛔ `.venv/Scripts/python -m pytest -q` → 全绿；随后运行 `.venv/Scripts/python scripts/e2e_local.py` → **应输出 PASS**。
   - 这一步跑的是无硬件闭环：`grbl_sim`（假 Grbl :2323）← `pipe_sim`（模拟 S3 转发）← `tests/test_star.svg`。
   - 它验证的是「SVG → vpype → G-code → 逐行转发 → 收 ok」整条软件链，**不依赖 LLM，也不依赖写字机**。
4. 起服务：`.venv/Scripts/python -m uvicorn hutuji_mcp.server:app --port 8300`

**通过判据**：pytest 全绿且 `e2e_local.py` 输出 PASS；命令输出归档到发布记录。

---

## 阶段 4：③ 产出契约合规

**为什么**：v0.5 §5.1 定的完整性契约（长度 + CRC32）已经实现并有自动化测试；本阶段是在候选发布版本上再次验收，防止代码/部署漂移。**不过这关，S3 侧一定校验失败**。

按 `mcp-server/README.md` 的组 0–3 逐项复验：

1. **CRC32 头**（组 0.2）：`curl -sI <url>` 应见 `X-Hutuji-CRC32` 与 `Content-Length`。
   - 本地核对：`python -c "import zlib;print(hex(zlib.crc32(open('f.gcode','rb').read())&0xffffffff))"` 与响应头一致。
   - ⛔ **CRC32 变体两侧必须同为 zlib/IEEE**——不一致会每次都失败且极难排查，联调首包务必核对。
2. **512KB 上限**（组 0.3）：构造 >512KiB 的 G-code，`validate_gcode` 应抛 `GcodeError`。
3. **越界拒绝**（§5）：构造超 190mm 坐标的样本 → 应**拒绝生成**（不是裁剪、不是缩放）。
4. **元素白名单 + 注入**（组 1.1/2.1/2.3）：跑注入回归测试（组 2.3 的 6 条 case），恶意 SVG 全拒、合法简笔画通过。
5. **换纸触发点规避**（§5）：产出的 G-code 不得含 `G28`/`G30`、任何 `G0` 落点 XY≈0、首行不得 `G0 X0Y0Z0`。
6. **安全**（组 3）：确认 `/mcp` **不对公网监听**、`/files` 文件名带随机 token、限流生效。

**通过判据**：上述 6 项全过；`hutuji_status()` 显示 `llm_configured`。

---

## 阶段 5：S3 固件（②）刷入与 Telnet 探活

补丁位置：`D:/Users/xiaozhi-esp32`，分支 `feat/hutuji-usb-pipe`（分支名沿用历史，链路已是方案 E）。说明见本仓 `firmware/m1-usb-pipe.md`。

1. **确认发现链**：S3 与写字机接入同一路由器；连接器先试 NVS 缓存，失败后扫本机 `/24`。只有返回 Grbl banner 的 :23 候选可进入 ready，禁止只凭“端口可连”缓存目标。
2. 构建：使用 **ESP-IDF v6.0.1** 执行 `python3 scripts/release.py lichuang-dev`。
   - 2026-07-28 证据：`05f4563` 已提交并以 IDF 6.0.1 构建通过（`0x2ccde0`，SHA256 `A77C8635...F29F80CC`），已烧录 COM6；esptool 对 bootloader/partition/OTA/assets/app 全部写入哈希校验通过。
   - 2026-07-29 证据：`3ca9d31` 默认构建通过（`0x2cce30`，SHA256 `5D5FE23C...BBA6`）；8.3 自动触发镜像构建并烧录 COM6（`0x2cd070`，SHA256 `98C3B6AF...C122C`）。
3. 刷入后看日志：应见候选 banner 校验通过、已连接写字机 Telnet 与 Grbl 探活成功。
4. 调 `hutuji.status` → 应返回 `connected=true, ready=true`。
5. ⛔ **确认 `authorized`**（组 0 之后 status 应含此字段）——未授权则回阶段 1 步骤 5 走授权 SOP。

**通过判据**：`hutuji.status` 显示 connected + ready + authorized。

**当前记录（2026-07-30）**：历史 2026-07-29 记录中，S3-LC-01 已烧录 `3ca9d31` 的 8.3 自动触发镜像，COM9/COM3 采集
`results/bringup-8.3-fault-capture-20260729-010436.md` 确认普通画线断连后完成重连、`Changing=Off`、
废纸 `M30`、`[PaperStatus] 0`、PSRAM 从第 1 行重画、55/55 行完成与 fresh `Idle`；最终页尾自动换纸
因无纸返回 `[PaperStatus] 2` + `error:90`，任务最终 `state=error`。2026-07-30 B-12/B-13 在 `13057e8`/`0x2ce890` 上补齐：S3 自动路径出墨、页尾 `[PaperStatus] 0` + `state=done`，并通过 HTTP `[ESP901]` 高频轮询在 `M30` 后 0.14s–10.11s 捕获 `Changing=On`。因此阶段 5 的构建/烧录、出墨、换纸成功和 `Changing=On` 均有证据；仍缺换纸中 abort、App 真实语音。

**排障**：
- 连不上 → 查 S3 与写字机是否同一路由器、IP 是否写对、写字机 Telnet 是否开。
- 连上但 ready=false → 探活 `$I` 没收到 `[VER:`，查是否有别的 Telnet 客户端占着唯一槽位（`MAX_TLNT_CLIENTS=1`）。

---

## 阶段 6：小智云 MCP 接入（③ 上云）

1. 部署 ③ 到**京东云 VPS**（2核4G / 5Mbps / `117.72.118.95`，专机只跑 ③）：Python≥3.10、systemd 托管 uvicorn。
2. `HUTUJI_PUBLIC_BASE_URL` 改成京东云公网地址。
3. ⛔ **安全边界**（组 3）：`/mcp` **只绑 localhost/内网**，桥接器同机部署；防火墙**只放 `/files` 所需端口**。
   - 理由：`/mcp` 裸奔上公网 = 任何人可白嫖你的 LLM key、打爆 VPS。
4. 桥接器接入小智云：`小智云(wss) ← hutuji_mcp.cloud_bridge(ws↔stdio) ← mcp-proxy(stdio↔http) ← 本服务 /mcp`。
   - 接入点从小智控制台取（智能体 → MCP 接入点），只以 `MCP_ENDPOINT` 环境变量注入，**禁止写入仓库/.env/systemd unit**。
   - 运行：`.venv/bin/python -m hutuji_mcp.cloud_bridge`（Windows 用 `.venv/Scripts/python`）。
   - 本机预检：设置变量后运行 `python scripts/cloud_bridge_smoke.py`，2026-07-25 已验证 `initialize` + `tools/list` PASS。
5. 在小智控制台确认工具已注册：`hutuji_draw` / `hutuji_status` / `hutuji_preview_svg`。

**通过判据**：小智云侧能看到并成功调用 `hutuji_status`。

> **历史记录（2026-07-31；已被后续生产记录 supersede）**：京东云 MCP+bridge+nginx+Cloudflare Tunnel 已常驻；当时 preflight 为 14 项且全 PASS，合法命名产物经公网 Tunnel 与 `:80` 回源均 HTTP=200，`/mcp` 404、`POST /files/` 403。官方小智消息 API 已发现七个设备侧工具并触发 `draw/status/abort`。阶段 6 云端调用子项已过，但 App 真实语音不属于该证据，阶段 7 另行验收。**当前生产真值**：SPEED-01/PNG 预览运行态已部署，preflight **26/26 PASS**；真实语音出图、归位、自动换纸已有 2026-08-17 历史证据，当前未闭项看 `release-readiness.md`。

---

## 阶段 7：端到端（语音 → 出纸）

⛔ 前置：阶段 0–6 全部通过。

1. 对小智说一句绘图指令（如「画一只小鸟」）。
2. 云端 LLM 调 `hutuji_draw(description)` → ③ 生成 SVG → vpype 转 G-code → 校验 → 返回 URL。
3. 云端把 URL 交给设备侧 `hutuji.draw(url)`。
4. S3：**全量下载**入 PSRAM → 比 `Content-Length` → 比 CRC32 → **授权门** → 才开始逐行发。
   - 注意这是 v0.5 定案：**不边下边转**，校验前移到落笔之前。
5. 写字机逐行执行，`ok` 一行一行回。
6. 下载文件最后一行 `ok` 后，S3 发 `?` 等 fresh `Idle`；随后由 **S3 状态机依次旁路** `G1G90 X0Y0F8000` 归位（G1 不触发换纸，ok + fresh `Idle`）再单发
   `M30`（文件内禁止 M30 与归位行）→ 触发完整换纸（最长按 90s 等最终 `ok/error:90`）。
7. `M30` 成功后再等一份 fresh `Idle`，任务完成才经 `Application::SendMcpMessage` 推送云端。

**通过判据**：纸上出现图，且换纸自动完成，云端收到完成推送。

**关键观测点**：
- 全程用 **CH340 串口**看最全日志（Telnet 只有 `ok`/`error`/`?` 状态/banner）。
- 换纸期间 S3 应仍能 `?` 探活（实时旁路，不吃 `ok`）。
- ⛔ 出图中调 `hutuji.status` **只应发 `?`**，不得发 `[ESP901]`/`M704`（会吃 `ok` 打乱流控，§3 单写者）。

### 7.0 有纸前的 LAN 排练（S4 打样，B-15 待跑）

云端链路被控制台 token 卡住时（`/api/agents` 返回 401，见 `release-readiness.md`「当前最近一步」第 10 条），
可先用 **LAN stub + autodraw 镜像**排练同一条 S3→Grbl 路径，只把「云端下发 URL」换成本机静态服务；
其余（PSRAM 全量下载、`Content-Length`、CRC32、窗口化流控、页尾 `M30`）与阶段 7 完全一致。

⛔ 铁律：**装纸之后才允许起 8300**。autodraw 镜像在每次复位时自动下载并出图，
空载出图会让笔尖压在压板上（见 `troubleshooting.md`「本地临时测试服务(8300)」）。

1. 装纸，`[ESP901]` 预检。**判据是 `Changing=Off` 且（`Paper=OK` 或 `PanelHold=On`）**——
   `Paper=OK` 只在纸恰好压住台面传感器时出现；**经 `M30` 自动换纸就位的纸，终态是
   `Paper=No` + `PanelHold=On`**（第 6 步快送到尾缘越过传感器、第 7 步回找、第 8 步再进
   350 步又越过，2026-08-07 COM10 两次实测）。只写 `Paper=OK` 的护栏会把换纸成功误判为无纸。
2. 从串口日志读出镜像里**硬编码的下载 URL**（编译期常量，历次批次不同：B-11/B-12 记录过 `192.168.1.12`，
   `firmware/README.md` 记录 `13057e8` 为 `192.168.1.3`）——**以本次日志自证为准**，不要按记忆填 IP。
3. 用真管线产物覆盖被服务的文件内容、**不改文件名**（URL 是常量）。
   B-14 已备好打样件：`test_rose_s4.svg` → `linesimplify 0.05mm` → **551 行 / 13,804 字节 / CRC32 `ae1b25b2`**，
   已过真 `validate_gcode`；>512 字节，可压满 S3 `kWindow=512`。
   重新生成：`mcp-server/.venv/Scripts/python` 调 `hutuji_mcp.gcode_gen.svg_to_gcode`。
4. 起静态服务（只暴露该一个文件，`/mcp` 返回 404），复位 S3 触发 autodraw。
5. 出图结束后**立刻停掉 8300**，避免下次意外复位空载出图。

**通过判据**：551 行全部出墨且曲线连续（S4 的「细节改善且无停顿」）、页尾 `M30` 后
`[PaperStatus] 0` + `state=done`，全程无 `error:90`。过则 P2 S4 证据缺口关闭。

---

## 阶段 8：故障注入（🔬 这批数字要填回 protocol.md）

这阶段是 v0.5 里所有标 🔬 的定案的**实测验收**。逐项做，把测得的数字回填 `protocol.md`。

| # | 注入方式 | 期望行为（v0.5 定案） | 要记的数 |
|---|----------|----------------------|---------|
| 8.1 | **出图中拔 S3 电**，再上电 | 写字机侧死连接被 keepalive 回收，S3 重连成功 | **回收+重连总时长**（§1.2 预期 ≈20s；填回 §1.2 表） |
| 8.2 | **出图中断写字机电** | S3 侧 keepalive 发现（已修为 10+3×3）→ 指数退避重连 | 发现耗时（预期 ≈19s） |
| 8.3 | **普通画线行断连**后重连 | 受限 reset 恢复 → 确认抬笔/`Idle`/授权 → `M30` 弃废纸 → 从 PSRAM 缓存开头重发（不回源） | planner 是否清空、是否真弃纸、是否重画成功 |
| 8.4 | ⚠️ **换纸进行中断连** | **不发 `0x18`、不发 `M30`**；抬笔停机 + 提示用户 | `[ESP901] Changing=On` 是否被正确识别（**最脆的一环**） |
| 8.5 | **换纸中调 abort** | 诚实回报「换纸中无法即停，完成后即停」；不得在保护窗盲发 reset，换纸结束后不再续发 | `Changing=On` 分流、无叠加 reset/M30、任务不续跑 |
| 8.6 | **下载中断/CRC 不符** | **零字节下发**，任务 error，一张纸都不废 | 是否真的没落笔 |
| 8.7 | **超 190mm 越界样本** | ③ 侧**拒绝生成**（不裁剪、不缩放） | 拒绝提示是否说清超界轴与值 |
| 8.8 | **缺纸/卡纸** | `error:90` + `[PaperStatus] N`（N=2/3/4/5）→ 暂停 + 提示，不自动重试 | 各故障码是否都经 Telnet 到达（依赖 §9-B1′） |

**当前 8.3 记录（2026-07-29）**：`3ca9d31` 自动触发镜像 + 本地 658B/CRC32 `2290095f` G-code；
采集报告 `results/bringup-8.3-fault-capture-20260729-010436.md`。本次通过的子项：WiFi OFF/ON 注入后
S3 检测断连并重连；`[ESP901]` 返回 `Changing=Off`；S3 做受限 reset/恢复后发废纸 `M30`；
Grbl 返回 `[PaperStatus] 0`；S3 从 PSRAM 第 1 行重画；第二轮页尾 `M5` 未再 60s 误超时；
重画到 55/55 并 `Run -> Idle`。未通过项：页尾自动换纸无纸返回 `[PaperStatus] 2` + `error:90`，
任务最终 `state=error`，不能判定 8.3 完整通过。

**8.3 实验台注入建议**：若不方便物理断开路由器，可在普通画线已经进入 `streaming/Run`
后，通过写字机 CH340 串口发送 `[ESP115]OFF`，等待数秒后发送 `[ESP115]ON`。该方式关闭/恢复
Grbl WiFi 服务，S3 不重启，PSRAM 缓存应保留；比拔 S3 电更适合验证“重连后从缓存第 1 行重画”。
本仓提供采集脚本：

```powershell
python scripts\bringup_fault_capture.py --s3-port COM9 --grbl-port COM3 --duration 240 `
  --inject-grbl-wifi-cycle-at 20 --wifi-off-seconds 8 `
  --note "先通过云端/设备工具启动一幅普通画线，确认进入 streaming/Run 后注入"
```

脚本默认只采集日志；只有显式传 `--inject-grbl-wifi-cycle-at` 才会向 Grbl 串口发
`[ESP115]OFF/ON`。打开 S3 串口可能导致部分开发板复位，若需要无干扰采集，应先用短采集确认
当前 COM 口行为。采集结果位于 `results/`，仍需人工核对纸面、废纸是否走掉和最终重画是否成功；
脚本标记扫描不能替代硬件判定。

### 8.5 换纸中调 abort 的取证剧本（B-15 提供脚手架，✅ 2026-07-31 已取证）

**为什么需要脚手架**：设备侧 abort 的唯一入口是云端 MCP 工具 `hutuji.abort`
（`xiaozhi-esp32/main/boards/lichuang-dev/lichuang_dev_board.cc` 的 `hutuji.abort` 注册处；
板上无按键/串口/HTTP 触发路径），而换纸保护窗实测只有约 10s（B-13：`M30` 后 0.14s 起
`Changing=On`，10.11s 已回 `Changing=Off`）。控制台 JWT 当前 401，云端下发不可用；即便可用，
语音链路也难稳定命中这 10s。故 B-15 在 xiaozhi fork 加了**默认关闭**的编译期脚手架
`HUTUJI_AUTO_TEST_ABORT_ON_PAPER`（`main/CMakeLists.txt` option + 板型校验、
`lichuang_dev_board.cc` 监视任务）：50ms 轮询 `Job::IsPaperActive()`，窗口一开即本地调
`RequestAbort()`，并打印 abort 前后的 `status`。默认镜像行为不变。
脚手架已提交为 xiaozhi `f1cf066`（`feat/hutuji-usb-pipe`，`13057e8..f1cf066` 已推送，`+44/−0` 纯新增），
可直接 `git -C D:/Users/xiaozhi-esp32 show f1cf066` 复核。**✅ 2026-07-31 21:49–21:50 实测取证**：基于 HEAD `6b39e66` 构建带宏镜像 `build-hutuji-8_5-abort/xiaozhi.bin`（`0x2cfc40`，SHA256 `e2ae19e0…1dad`），app-flash COM6；AutoTest 触发五角星（257B/CRC32 `87475d77`）→ 画完 `Run->Idle`、`state=paper_change` → 脚手架命中窗口调 `RequestAbort()` → 返回「换纸中无法即停，完成后即停」→ 页尾 `M30` → `[PaperStatus] 0` → `state=aborted` 不续跑。四判据全过；abort 前 status `state=paper_change` 证明命中 8.5 非 8.4。取证后已回刷不带宏默认镜像（`build/xiaozhi.bin`，Hash verified）。证据 `results/s3-8_5-abort-on-paper-evidence-20260731.md`。**仍缺**断连恢复 `paper_active` 窗口 abort 单独复测。

**前置**：装纸（`[ESP901]` 预检见 `Changing=Off` 且 `Paper=OK` 或 `PanelHold=On`，判据同阶段 6）；PC 起本机静态服务托**小**测试件
（8.3 用过 658B / CRC32 `2290095f`；件越小越快到页尾换纸窗口）；**先备份当前板上镜像对应的
`xiaozhi.bin`**，取证后要回刷。

**构建取证镜像**（PowerShell。URL 必须加引号，否则 PowerShell 会把 `http://…` 切成新参数——
历史上已踩过一次，见 `build-hutuji-f1000-autodraw/log/` 里 `-DHUTUJI_AUTO_TEST_DRAW_URL=http:` 的记录）：

```powershell
. D:/zhugu-home/.espressif/v6.0.1/v6.0.1/esp-idf/export.ps1
cd D:/Users/xiaozhi-esp32
idf.py -B build-hutuji-8_5-abort -DHUTUJI_AUTO_TEST_DRAW=ON `
  '-DHUTUJI_AUTO_TEST_DRAW_URL=http://<PC_IP>:8300/files/<小测试件>.gcode' `
  -DHUTUJI_AUTO_TEST_ABORT_ON_PAPER=ON build
```

烧录只刷 app：`idf.py -B build-hutuji-8_5-abort -p COM<n> app-flash monitor`（S3 的 COM 口每次重查）。

**观察点**：S3 串口 `AutoTest` 三行（窗口命中前 status / abort 返回值 / abort 后 status）+
Grbl 串口 `[MSG:[PaperStatus] N]`。

**通过判据**（锚点均在 `xiaozhi-esp32/main/boards/lichuang-dev/hutuji_job.cc`）：

1. abort 返回 `换纸中无法即停，完成后即停`，且有 Notify「写字机正在换纸，无法即停；换纸完成后任务将停止」（`RequestAbort` 的 `paper_active` 分支）。
2. **全程**（含换纸结束后）**无** `0x18`、**无** reset banner、**无**第二条 `M30`。源码保证：`paper_active` 分支**直接 return**，根本不创建 `hutuji_abort` 子任务，所以这条路径下永远不会发实时复位；子任务里那两处 `IsPaperActive()` 复核是**非换纸态** abort 的竞态兜底，不是本项判据。
3. 换纸自身正常走完：`[PaperStatus] 0`，写字机回 `Idle`。
4. 换纸结束后任务以 `aborted` 收尾且**不续跑**：`hutuji.status` 的 `state=aborted`，此后无新行下发（`ChangePaperAfterDraw` 末尾的 abort 检查）。

**判错**：`paper_active` 也覆盖**断连恢复**保护窗（`WaitForReconnect` 里的 `paper_active_.store(true)`，那条路径不设 `paper_change`），故必须看 abort 前 status 的 `state`：`paper_change` 才算 8.5；若是断连恢复窗口，记为 8.4 附证并重跑 8.5。另一条会置 `paper_change` 的路径是「文件内含换纸行」的 deferred 分支，但 §5 契约禁 `M`、下载文件不含换纸行，故本项目里 `paper_change` 只可能来自页尾 `ChangePaperAfterDraw()`。

**收尾**：取证后**回刷不带宏的镜像**，并在本节与 `docs/release-readiness.md` 记录 commit + 镜像 SHA256。

**另需实测标定**：
- **抬笔次数**（③ 组 1.3 软阈值 50）：实际画一幅简笔画抬笔多少次？机头抖不抖？→ 决定硬阈值。
- **抬/落笔 Z 值**（§5 待锁）：先查 `$102`（Z steps/mm），实测锁定 `PEN_UP/DOWN_Z_MM`。
- **京东云上行带宽**：2026-07-26 已测——公网下载 512KiB 中位 **~1.13s（~3.72 Mbps）**，满足「先下完再落笔」；若日后明显变慢再升带宽。详见 `deployment.md`。
- **方案 E 达标判据**（§1.2）：连续流式 ≥10 分钟零掉线；掉线频率与换纸窗口内掉线情况。

---

## 阶段 9：小批量商业出货放行

⛔ 前置：阶段 0–8 全部通过，且证据绑定到本批次三仓 commit/固件哈希。任何“口头通过”不算。

1. 按 `release-checklist.md` 冻结三仓版本、依赖、构建环境和回滚包。
2. 按 `production-sop.md` 做首件确认、逐台功能测试、老化、抽样故障注入和包装检查。
3. 每台填写 `docs/templates/device-test-record.md`；每批填写 `docs/templates/release-manifest.md`。
4. 按 `compliance.md` 完成电气/无线/标签/开源许可/隐私/商业条款负责人签核。
5. 把客户版 `customer-quickstart.md` 的支持联系方式、型号、批次和安全信息替换为正式内容。
6. 发布负责人复核所有偏差；未关闭偏差必须有书面风险接受人与截止日期。

**通过判据**：生产、研发、QA、运维/发布负责人均签字；逐台记录可追溯；回滚演练成功；无开放的安全/法规/撞机风险。

> 本仓目前尚未达到阶段 9，真实状态见 `release-readiness.md`。

---

## 附：出问题先看哪里

| 现象 | 先查 |
|------|------|
| S3 连不上写字机 | 同路由器？IP 对？Telnet 开？**唯一槽位是否被 PC telnet 占着**（`MAX_TLNT_CLIENTS=1`） |
| 连上但 ready=false | `$I` 探活没回 `[VER:` |
| G0–G3 全回 `error:110` | 未授权 → 授权 SOP（`M800`），S3 不自动授权 |
| 运动行回 `error:8` | 正在换纸，**该行已被丢弃**，等换纸完成后**重发该行** |
| 换纸后没收到 `[PaperStatus]` | §9-B1′ 没做（原路由 `CLIENT_SERIAL`） |
| `[ESP901]` 没有 `Changing=` | §9-B2′ 没做 |
| CRC32 每次都不符 | 两侧变体不一致（须同为 zlib/IEEE） |
| 出图中途 `ok` 配对错乱 | 违反单写者：`status` 在出图中发了 `[ESP901]`/`M704` |
| 画到一半停住不动 | 查是否 TCP 断连（S3 侧日志）；换纸行 `ok` 可能正常阻塞 ≥60s |
| 撞机/越界 | 软限位 `$20=0` 关闭，Grbl 不保护——查 ③ 侧限幅是否生效 |

**日志优先级**：CH340 串口（最全）> S3 侧 ESP_LOG > Telnet 流 > 云端 MCP 日志。
