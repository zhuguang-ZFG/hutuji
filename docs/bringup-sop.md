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
5. ⚠️ 机械确认（避免后续撞机）：`$130`/`$131` 应为 200/200；**`$20=0` 软限位关闭**——越界不报错直接撞机，限幅责任全在 ③ 生成端（§5）。
6. 记下 `$102`（Z steps/mm），供阶段 6 锁定抬落笔 Z 值（🔬 §5 待实测项）。

**通过判据**：`$I` 有 `[VER:]` **且** `[License] Authorized`，`?` 回 `<Idle>`。

---

## 阶段 1：写字机 WiFi + Telnet

**为什么**：方案 E 的物理层。⚠️ 下列设置名沿用 ESP3D 通用写法，**本 fork 确切拼写未核对**（🔬 串口一试便知，见 §1.3）。

1. 串口下发（写入即存 NVS）：
   - `$Sta/SSID=<家里WiFi>`、`$Sta/Password=<密码>`
   - `$Telnet/Enable=1`、`$Telnet/Port=23`
2. **固定 IP**：路由器后台给写字机 MAC 绑静态 DHCP 租约（首选），或 `$Sta/IPMode=Static` + `$Sta/IP=<addr>`。
   - 固定 IP 是必须的——S3 侧目标地址是编译期常量 `HUTUJI_PIPE_HOST`。
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

1. **改目标地址**：`hutuji_pipe.cc` 文件头 `HUTUJI_PIPE_HOST` 改成阶段 1 记下的写字机固定 IP（占位值是 `192.168.1.50`，**必须改**）。
2. 构建：`python3 scripts/release.py lichuang-dev`（仓库要求 IDF v6.0.2，≥5.5.2）。
   - 若报 lwip 头找不到：`main/CMakeLists.txt` 的 `PRIV_REQUIRES` 加 `lwip`（一行修复）。
   - ⚠️ 本机无 ESP-IDF 环境，此前补丁为**评审级自查、未编译**——首次构建可能暴露编译错误，属正常。
3. 刷入后看日志：应见 `已连接写字机 Telnet` 与 `Grbl 探活成功（[VER:...]`。
4. 调 `hutuji.status` → 应返回 `connected=true, ready=true`。
5. ⛔ **确认 `authorized`**（组 0 之后 status 应含此字段）——未授权则回阶段 1 步骤 5 走授权 SOP。

**通过判据**：`hutuji.status` 显示 connected + ready + authorized。

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

---

## 阶段 7：端到端（语音 → 出纸）

⛔ 前置：阶段 0–6 全部通过。

1. 对小智说一句绘图指令（如「画一只小鸟」）。
2. 云端 LLM 调 `hutuji_draw(description)` → ③ 生成 SVG → vpype 转 G-code → 校验 → 返回 URL。
3. 云端把 URL 交给设备侧 `hutuji.draw(url)`。
4. S3：**全量下载**入 PSRAM → 比 `Content-Length` → 比 CRC32 → **授权门** → 才开始逐行发。
   - 注意这是 v0.5 定案：**不边下边转**，校验前移到落笔之前。
5. 写字机逐行执行，`ok` 一行一行回。
6. 页尾 `M30` → 触发完整换纸（阻塞 ≥60s，该行 `ok` 要等换纸走完）。
7. 任务完成经 `Application::SendMcpMessage` 推送云端。

**通过判据**：纸上出现图，且换纸自动完成，云端收到完成推送。

**关键观测点**：
- 全程用 **CH340 串口**看最全日志（Telnet 只有 `ok`/`error`/`?` 状态/banner）。
- 换纸期间 S3 应仍能 `?` 探活（实时旁路，不吃 `ok`）。
- ⛔ 出图中调 `hutuji.status` **只应发 `?`**，不得发 `[ESP901]`/`M704`（会吃 `ok` 打乱流控，§3 单写者）。

---

## 阶段 8：故障注入（🔬 这批数字要填回 protocol.md）

这阶段是 v0.5 里所有标 🔬 的定案的**实测验收**。逐项做，把测得的数字回填 `protocol.md`。

| # | 注入方式 | 期望行为（v0.5 定案） | 要记的数 |
|---|----------|----------------------|---------|
| 8.1 | **出图中拔 S3 电**，再上电 | 写字机侧死连接被 keepalive 回收，S3 重连成功 | **回收+重连总时长**（§1.2 预期 ≈20s；填回 §1.2 表） |
| 8.2 | **出图中断写字机电** | S3 侧 keepalive 发现（已修为 10+3×3）→ 指数退避重连 | 发现耗时（预期 ≈19s） |
| 8.3 | **普通画线行断连**后重连 | 自动 `0x18` → `M30`（弃废纸上新纸）→ 从头重发（走 PSRAM 缓存，不回源） | 是否真弃纸、是否重画成功 |
| 8.4 | ⚠️ **换纸进行中断连** | **不发 `0x18`、不发 `M30`**；抬笔停机 + 提示用户 | `[ESP901] Changing=On` 是否被正确识别（**最脆的一环**） |
| 8.5 | **换纸中调 abort** | 诚实回报「换纸中无法即停，完成后即停」；换纸结束后不再续发 | `0x18` 是否确实被 8s 窗吞 |
| 8.6 | **下载中断/CRC 不符** | **零字节下发**，任务 error，一张纸都不废 | 是否真的没落笔 |
| 8.7 | **超 190mm 越界样本** | ③ 侧**拒绝生成**（不裁剪、不缩放） | 拒绝提示是否说清超界轴与值 |
| 8.8 | **缺纸/卡纸** | `error:90` + `[PaperStatus] N`（N=2/3/4/5）→ 暂停 + 提示，不自动重试 | 各故障码是否都经 Telnet 到达（依赖 §9-B1′） |

**另需实测标定**：
- **抬笔次数**（③ 组 1.3 软阈值 50）：实际画一幅简笔画抬笔多少次？机头抖不抖？→ 决定硬阈值。
- **抬/落笔 Z 值**（§5 待锁）：先查 `$102`（Z steps/mm），实测锁定 `PEN_UP/DOWN_Z_MM`。
- **京东云上行带宽**：5Mbps 通常指下行，S3 下载走 VPS 上行 → 实测 512KB 下载耗时。
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
