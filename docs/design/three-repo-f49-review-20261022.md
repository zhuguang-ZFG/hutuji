# F49 三仓代码审查（2026-10-22）

用户要求「继续改进优化，查找逻辑错误和不合常理的地方，三仓code review」。本轮沿 F48 后的 TLS 生命周期、OTA 会话与 Grbl 请求/应答归属复审，确认 5 项缺陷。**产品代码未修改，以下均待修复**。没有部署、刷机、设备调用或运动；现役状态仍以 readiness 为准。

## 基线与范围

| 对象 | 本轮读取的基线 | 范围与限制 |
|---|---|---|
| Hub | `bec84b07b9d57da981dd62a663d62ff32d125f5a` | Portal OTA、SQLite 会话及出图入口；受跟踪文件接手时无改动 |
| S3 | `3b0d6df3f72fc905a959d68cd8218acde5d54ee8`，源码 1.0.49 | 在册 TLS 补丁及本地受管组件 HTTP/WebSocket 消费路径；受跟踪文件接手时无改动 |
| 量产 Grbl | `a268e05c96d7f1207ca2f4db2169d8ee17f288e4`，`massprod/nopaper-1.3a` | 原工作树目录缺失，Git 标为 prunable；从本地 Git 对象导出该提交的源码/测试快照，不宣称量产工作树干净或做过真实构建 |
| 普通 Grbl | `e5d7199a` | 读取仓库说明和分支差异，未改产品；量产 findings 的动态证据来自上行固定快照 |

契约依据：`docs/protocol.md` §1.1/§1.2（连接与会话）、§3/§4（应答与控制）、§10.4.22（OTA 占位和状态对账）。不改变换纸、运动、引脚、身份、CRC/SHA 或出图校验。

## F49-01 / P1：保留 EspSsl 仍挡不住回调访问已销毁的 HttpClient

位置：S3 `managed_components/78__esp-ml307/src/http_client.cc` 的 `Open`、`LeakAbandonedTransport`、析构；`src/esp/esp_ssl.cc` 的 `ReceiveTask`。

`Open` 将两个捕获 `[this]` 的函数注册到 Tcp。Close 的 3×10s 等待耗尽后，F48 修复释放 unique_ptr 所有权，保留 EspSsl，但 HttpClient 本身和事件组仍照常销毁。阻塞读随后返回错误或正字节时，`ReceiveTask` 都会调用原回调；它只在进入 while 时检查 connected，读返回后并未阻断回调。目标仍是已销毁的 HttpClient，形成释放后使用。若同一 HttpClient 先 Open 新请求，旧回调也可能把新请求写成断连或混入旧数据。

本机证据：提取真实 `HttpClient::~HttpClient`、Close、Leak 和 `EspSsl::Disconnect/ReceiveTask` 编译。在读桩内部完成 owner 析构后再返回，两种结果均为 `wait_rounds=3`、`transport_destroys=0`、`late_owner_callbacks=1`。探针将业务回调替换为生命周期计数器，避免主动执行未定义内存访问；真实回调捕获 HttpClient 的事实由源码核对。**这证明危险回调顺序，不等于板上崩溃或 ASan 实测。**

建议：将回调接收状态与 owner 寿命绑定，并解决正在执行的回调。只清空 std::function 或在回调前读一次布尔值仍有检查后销毁竞态；只把 EspSsl 泄漏也不足。保留有限等待要求，不用无限等待回避问题。

验收：延迟 read 的正/负返回、回调已进入、Close 后销毁、同对象 Open 新请求四类交错均不得访问旧 owner 或污染新请求，正常断连仍须唤醒读取等待者。

## F49-02 / P1：WebSocket 析构仍销毁已放弃的 TLS 接收对象

位置：S3 `managed_components/78__esp-ml307/src/web_socket.cc` 的析构与 `Connect`；`include/web_socket.h` 的 `std::unique_ptr<Tcp> tcp_`。

`WebSocket::~WebSocket` 调用 `Disconnect` 后直接结束，成员 unique_ptr 随之 delete Tcp。有限等待超时只避免 SDK TLS 析构，不阻止 EspSsl 对象自身被释放。F48 的 `ReceiveAbandoned` 所有权处理只加到 HttpClient，WebSocket 路径未接入；`Connect` 替换 tcp_ 也没有统一回收协议。官方语音 WebSocket 使用此消费路径，因此问题不局限于下载。

本机证据：真实 WebSocket/EspSsl 析构与 Disconnect 编译注入未退出接收者，结果 `wait_rounds=3`、`tls_destroys=0`、`transport_destroys=1`。接收线程尚未证明退出，对象已执行析构。未在真机制造此故障。

建议：统一所有 Tcp owner 的关闭/放弃协议，并与 F49-01 的回调生命周期一起解决，覆盖 WebSocket 替换、握手失败和析构。不能仅再补一个裸 release 就宣称完整回收。

## F49-03 / P2：Grbl 已领取旧命令的应答可以流入新连接

位置：量产快照 `Grbl_Esp32/src/Protocol.cpp` 的 `ClientCommandOwner` 与行执行结果发送；`src/WebUI/TelnetServer.cpp` 的 `write`、`clearClients`。

当前领取契约允许已领取命令在连接失效后执行完，这是既定行为；但结果仍只携带 `CLIENT_TELNET`，没有原 session。发送入口先调用 `clearClients()`，可以接入新连接，然后把旧命令的 `ok` 发给新 socket。即使新连接已经由 handle 接入，同一发送入口也不会校验应答归属。

本机证据：真实领取/释放、会话重置、clearClients/write 函数编译。正常对照 session 1 的 4 字节应答发 socket 41；旧连接失效、新连接待接入时，发送函数将 session 切到 2，并把同一个 `ok\r\n` 的 4 字节发往 socket 42，旧 socket 收到 0 字节。

影响：新客户端可收到未请求的旧结果；若有待应答命令，会产生应答错配。**本轮未证明 S3 的完整 banner/认证检查被绕过，未证明纸面运动错误，因此按 P2 记录。**

建议：把已领取命令的响应也绑定原会话，发送时在同一连接锁下核验，旧会话结果丢弃；保留“已领取执行者可完成”的现行语义，不扩大锁到规划器等待或修改运动行为。覆盖旧 ok、旧 error、多行查询结果与新会话独立应答。

## F49-04 / P2：切换设备后的出图检查会过期另一设备的 OTA 锁

位置：Hub `draw-portal/draw_portal/firmware_ota.py` 的 `is_upgrading`；`app.py` 的 `_reject_if_firmware_upgrading`。

用户 A 设备的 uncertain 会话有目标版本、超过 600s，用户切到 B 设备。出图入口不查询 A，随后调用 `is_upgrading(..., device_mac=B)`。函数因 MAC 不同把 in_flight 设为 false，跳过“已有目标保持锁”的早返回，却执行按 user_id 的无目标过期 SQL，把 A 改为 failed/status_timeout。

本机证据：真实 Store/SQLite 和函数，同设备检查保留 uncertain/busy；另一设备检查返回不忙的同时，将 A 的 uncertain 改为 failed/status_timeout，期间没有任何设备查询。B 应可出图，但不应改变 A 的升级账本。锁被清除后，同账号也能再次领取升级会话。

建议：MAC 不匹配时只返回该设备不忙；按设备查询得到的可信状态才能结束对应升级。过期写入应与 session_id/MAC/目标/状态快照绑定。回归须把现有“另一设备可出图”的案例扩展到超过 600s 的会话。

## F49-05 / P2：HTTP 200 的无效状态仍被用作 OTA 放锁证据

位置：Hub `draw-portal/draw_portal/firmware_ota.py` 的 `_fetch_device_status`、`get_ota_status`。

`_fetch_device_status` 只要传输 ok 就返回 True；正文解包失败会得到空字典。get_ota_status 把“缺少 ota.upgrading”理解成“已离开 upgrading”，同时缺版本被当成目标版本不匹配。因此会话超过 600s 后，空 JSON、HTML 网关正文、仅 success 信封或缺 ota 的状态都会触发 failed/status_timeout。

本机证据：四种无效/不完整正文都通过现有 `tool_result_failed`，真实解析函数返回 ok=True；真实 SQLite 会话均由 uncertain 转 failed。对照完整 upgrading 状态保持锁，完整 idle 且版本未匹配按既定契约过期。传输成功不构成设备已经结束 OTA 的证据。

建议：过期判定须验证设备正文的状态、OTA 状态和有效当前版本；无法确认字段语义时保留占位。缺失/未知状态不能默认 idle。版本匹配成功对账与主动清理未下发目标的 reservation 应分别保留其现行用途。

## 验证与证据

本机目录 `results/local-only/2026-10-22-f49-review/`；脚本与固定量产快照位于 `tmp/three-repo-review-f49-20261022/`。两者仅本机，已登记 inventory。

| 验证 | 结果 |
|---|---|
| `probe_ota.py` | 8 个真实函数/数据库场景，含正常对照；5 个场景复现上述 OTA 缺陷 |
| `probe_cpp.py` | 3 个 TLS 生命周期场景；2 个 Grbl 应答场景，含 1 个正常对照；宿主 x86_64 g++ 真编译 |
| Portal `test_firmware_ota.py` | 46 passed；1 个既有 Starlette TestClient 弃用提示 |
| S3 `test_tls_lifecycle.py` | 6 passed |
| 量产固定快照的 command_session / telnet_disconnect / telnet_receive / telnet_session / realtime_session | 18 passed |
| Hub `agent_gate.py --profile docs` | PASS；6 处引用仅行号移动，原文与 hash 保持相同；`git diff --check` 通过 |

13 个新探针场景、70 项现有测试通过是审查复现和基线证据，**不是缺陷已修复的证明**。现有测试缺少本文交错；TLS 现有 leak 检查偏源码字符串，不能证明 owner 寿命安全。C++ 平台调用为确定性桩，未跑 IDF/PIO 构建、fz standard、全量 Hub/Portal 门禁或硬件注入。

证据文件：`ota-probe.json`、`tls-owner-lifetime.json`、`grbl-reply-session.json`、对应生成 C++/编译日志、`source-sha256.json`、`verification.json`、`docs-gate.json` 与 `docs-gate.log`。本轮没有使用外部资料或上游网络结论。

## 2026-10-23 六项续修与发布

本节承接上方 5 项审查：本轮用户批准「全部修复部署验证，上传推送ci变绿」，范围固定为当时复核确认的六项缺陷（S3 WebSocket 主动关闭漏业务通知、消费者回调寿命；Hub OTA 旧观察污染新会话、页面重建后无法恢复；索引 PNG 镜像 EXIF；量产 Grbl 应答标签跨任务误用）。其余历史项保持各自状态，不在本批宣称修复。

### 修复与判据

| 项 | 提交 | 行为判据 | 回归（旧码 RED → 新码 GREEN） |
|---|---|---|---|
| S3 WebSocket 生命周期（1.0.51） | xiaozhi `be52d51` | 每次连接独占状态对象；传输回调只持弱引用；主动/远端/失败/替换/析构统一一次性终结，先退休唤醒等待者、锁外清传输并通知；关闭通知只发一次；失败路径先降功耗再回 Idle | `test_websocket_protocol_lifetime.py` 源码编译交错 36 项；旧码关闭通知缺失且析构窗访问失效事件 |
| Portal OTA 观察快照 | draw-portal `31b555e` | 观察在 I/O 前捕获同设备 session/binding，只对原 session 一次条件更新或过期；starting/uncertain/离线/畸形正文不消费旧结果；100% 不代替目标版本确认；busy 只核对不下发 | `test_firmware_ota.py` 真实 SQLite 交错：旧观察返回不改替换后会话；busy 版本核对不增加 `hutuji.ota_start` |
| 小程序固件页恢复（0.3.34） | draw-portal `2a243fa` | 冷启动先只读查询捕获 MAC 的 `ota_status`，idle 才查版本；busy 只查状态；单飞查询；离页/登录代次/切机/迟到回包防护 | Node 页面实际脚本 380 项；GET/POST 计数与在途单飞断言 |
| 索引 PNG 方向 | draw-portal `c2f48d6` | `exif_transpose` 先于调色板展开，镜像方向不落到颜色通道；输出无方向元数据；幂等 | `test_pipeline_raster.py` 70 项；2/4/5/7 方向旧码 14 项失败 |
| 量产 Grbl 应答标签 | massprod `e3cc4f81` | `client_response_tag` 只对领取任务返回标签；欢迎与 `?` 实时应答任务得到 0；释放只结算匹配任务；不加长锁 | `test_telnet_response_session.py` 编译真实 `client_write` 分派；同核任务切换与 CLIENT_ALL 分派矩阵 |

S3 版本 `hutuji.1.0.51`；小程序 `0.3.34`。补丁链（六个 `esp-ml307-*.patch`）按 CI 方式在纯净组件上重放通过，宿主 48 项专项通过且组件恢复后逐字节一致。

### 本机验证

| 范围 | 命令 | 结果 |
|---|---|---|
| S3 host | `python -m unittest discover -s scripts/tests -v`（x86_64 MinGW g++） | 444 OK（含补丁链重放与新增交错） |
| S3 firmware | ESP-IDF v6.0.1，两板真实配置，`CCACHE_DISABLE=1` | Freenove `xiaozhi.bin` 3,105,360 B SHA-256 `5366f053…0b7a`；LICHUANG_DEV_S3 3,140,016 B SHA-256 `e24d628e…51fc`；各 215 个 CXX 对象，版本串 `hutuji.1.0.51` |
| Portal | `python -m pytest -q tests` | 764 passed |
| 小程序 | `node --test draw-portal/tests/weapp/*.test.js` | 380 passed |
| Hub 门禁 | `python scripts/agent_gate.py --profile full` | 13/13 层 PASS，pytest 1692、e2e PASS，exit 0 |
| MCP/生产工具 | `pytest -q tests` / `tests/production` / `scripts/e2e_local.py` | 全套通过，e2e PASS |
| 量产 Grbl host | `python -m pytest -q scripts/tests` | 85 passed |
| 量产 release | `pio run -e release`（e3cc4f81 精确提交） | SUCCESS，`firmware.bin` 1,849,168 B SHA-256 `25b8a507…6566` |
| fz | `agent_gate.py --profile standard`（GRBL_ROOT=量产仓，Python 3.10） | overall pass exit=0，hard=0 soft=2；`wokwi_startup` 云端不可用（月度配额，非阻断）明确启动未验证；`qemu_startup`/`product_host`/`qwen_evidence` PASS |

`wokwi_startup` 未通过属既有配额例外，不能写成芯片云端启动已验证；实体纸面/老化/交付签核仍 Hold。

证据目录 `results/local-only/2026-10-23-f49-followup/`（三仓日志、构建 JSON、RED/GREEN 记录）与暂存 `tmp/f49-followup-20261023/` 均仅本机，已登记 inventory。
### 发布与远端回执

- **Hub 提交与 CI**：分支 `worktree-f49-followup-20261023` 已推送，产品提交 `31b555e`、`c2f48d6`、`2a243fa`，CI 引用 `fab2aa3`。Hub Tests `39916022920` completed/success（headSha `fab2aa3`）；Hutuji Mass Production `39915771591` success（`e3cc4f81`）；Hutuji S3 `39915609896` success（`be52d519`）；S3 PR Build Boards `39915614313` success；S3 push 全量 Build Boards `39915613218` completed/success（`be52d519`，175 job：174 success、1 skipped、0 failed）。本仓记录用文档提交 `699ccce` 的 Hub Tests `39931549083` completed/success（workflow_dispatch，test job success）。
- **双机部署**：京东热备先行、阿里主节点随后，均从 `fab2aa3` 出包部署，`verify` 逐文件 SHA PASS，离线烟测 `SMOKE_PASS`（P 模式镜像 EXIF 1/2/4 与真实 SQLite OTA CAS）。备份：京东 `51aab6dd78934556a5b94b72bd2cce4f`，阿里 `8889ad39bec7491897afe57c5d6872e5`。两节点 `app.py` 未变；`store.py`/`pipeline.py`/`firmware_ota.py` 运行时 SHA-256 与 `fab2aa3` 的 LF blob 一致。
- **偏差一（包内容）**：生产运行时原为 `d58a671`，缺 `b4508f9` 的 `expire_firmware_ota_session(expected=...)` 条件写签名。计划预期只含 `pipeline.py`/`firmware_ota.py` 的包会让新 `firmware_ota.py` 在过期路径抛 TypeError（京东首次部署烟测实测）。本批改为三文件包（加入 `store.py`），京东先回滚到 `4b7b44ce2481438083d010a0b3ebeb0c` 再重发；未改 `app.py` 与数据库 schema。
- **偏差二（阿里窗口）**：阿里首次部署因 `hutuji-curated-prep` 处于 `activating (auto-restart)` 被 `_active()` 拒绝（既有循环失败，与本批无关）；在 active 窗口重试成功，部署按常态停/启该服务，未改 unit。
- **preflight**：部署前后失败集逐条相同——阿里 25/34（9 项既有 nginx 布局差异）、京东 27/34（7 项热备预期差异），无新增失败，不能称全绿。
- **固件 1.0.51**：Freenove `xiaozhi.bin` 3,105,360 B、SHA-256 `5366f053429d11b49dad73b9bff016b5e35ded0554d76dca7df4c830c0ab0b7a`。先京东后阿里执行 `publish_s3_firmware.py`；发布前指针备份在 `/root/hutuji-bak-20261023-f49-1.0.51/`（catalog `5b8a01c8…`、latest `1a80b7a8…`，原推荐 1.0.49）。发布后两节点目录/manifest/catalog/latest 哈希一致（catalog `c64660a2…`、latest `0d791fa8…`），公网下载与京东定向下载均 HTTP 200 且 SHA-256 相符。推荐指针为 1.0.51；目标板实际版本因设备离线未取证。
- **小程序**：`0.3.34` 已用微信开发者工具 CLI 上传（AppID `wxbf3c1e0013b46343`，79 文件，307,291 B，来源 `fab2aa3`，回执 `weapp-upload.json`）；仅上传，未提审、未正式发布。
- **实机未验证**：当前 LAN 未见 `192.168.1.13`，本机仅蓝牙 COM4/COM5、无 USB 串口；授权 S3 `b8:1f:3f:c3:70:bc` 与 nopaper Grbl `4c:c3:82:cc:8a:9c` 的身份、Idle、备份、48 项与单次 OTA 均未执行。WebSocket 专项因此未在板上验证，不得以源码和宿主交错替代。
