# F50 三仓代码审查（2026-10-24）

用户要求三仓 code review。本轮为 F49 五项修复的**修复验证**加热点区新缺陷排查：逐项核对 Hub OTA 放锁、S3 TLS 回调寿命、量产 Grbl 应答会话三批修复是否真正闭合 F49-01…05,并沿修复触碰面继续找逻辑错误。结论：**五项 F49 缺陷修复均充分**;新发现 2 项 P2、9 项 P3 与 2 项记录/对照项，均待修复或待用户决断。产品代码未修改；没有部署、刷机、设备调用或运动；现役状态仍以 readiness 为准。

## 基线与范围

| 对象 | 本轮读取的基线 | 范围与限制 |
|---|---|---|
| Hub | `b069bbc`（代码增量 `d58a671`、`b4508f9`） | draw-portal OTA 会话/放锁/过期 CAS、`app.py` 出图闸、`pipeline.py` EXIF 方向;受跟踪文件接手与结束时均干净 |
| S3 | `be52d51`（1.0.51；增量 `393b2ff` 1.0.50、`be52d51`） | `patches/esp-ml307-zz-shared-receive-state.patch`（产品代码唯一真源，已核对与工作树一致）、`websocket_protocol.cc` 重写、`application.cc`;未做 IDF/PIO 真构建 |
| 量产 Grbl | `e3cc4f81`（增量 `586e072d`、`e3cc4f81`） | `Serial.cpp` 标签/分派、`TelnetServer` 会话写出、`Protocol.cpp` 领取契约；工作树干净 |
| 普通 Grbl | `e5d7199a` | 只对照分支差异，未改产品 |

契约依据：`docs/protocol.md` §10.4.22（OTA 占位与状态对账）。不改变换纸、运动、引脚、身份、CRC/SHA 或出图校验。

## F49 修复验证结论

| 缺陷 | 修复 | 结论 | 残留 |
|---|---|---|---|
| F49-01 S3 HttpClient 迟到回调 UAF | `393b2ff` shared State + weak_ptr + retired 闸 | **充分**：四类验收交错均有结构性答案，宿主回归 12 例过 | F50-08/09/10（有界 P3） |
| F49-02 S3 WebSocket 析构销毁在用 TLS | `393b2ff` + `be52d51` 统一终结 | **充分**：放弃/替换/握手失败/析构统一走 Shutdown，泄漏仅限故意放弃路径 | F50-11/12 |
| F49-03 量产 Grbl 旧应答流入新连接 | `586e072d` + `e3cc4f81` | **充分**：write_for_session 同锁核验会话且不收养，"已领取执行者可完成"语义保留 | F50-13/14 |
| F49-04 切机检查过期他机账本 | `b4508f9` | **充分**：MAC 失配提前 return False 不执行任何 SQL;expire 绑定五元组快照 CAS | F50-03（窄窗 P3） |
| F49-05 HTTP 200 无效正文误放锁 | `b4508f9` | **充分**：须传输 ok 且 state/版本/ota.state 齐备且 settled 才放锁 | F50-05/07（保守方向缺口） |

## F50-01 / P2：终态 OTA 会话使每次出图都触发一次同步设备推送

位置：Hub `draw-portal/draw_portal/app.py:1682`（`_reject_if_firmware_upgrading`）+ `firmware_ota.py:402`（`get_ota_status` 无条件 `_fetch_device_status`）。

出图闸对"存在同 MAC 会话"即调 `get_ota_status`,无 state 过滤;`get_ota_status` 先做设备推送再做状态分派（`starting/uncertain/upgrading` 过滤在取数之后,`firmware_ota.py:405`）。而 `firmware_ota_sessions` 以 user_id 为主键,终态行（ok/failed）无任何删除或清理路径（全仓无 `DELETE FROM firmware_ota_sessions`）,只被下一次 reserve 覆盖。于是用户只要历史上完成过一次升级,之后每次出图发送（`app.py` 三处 send 入口）都先做一次 `hutuji.status` 推送（timeout 15s）;终态会话这次调用不可能改变任何状态,纯属浪费。设备关机/失联时单次请求最坏可停顿 ~45s（推送超时 + 401/403/404 时补 token 重试）才放行出图。

本机证据：源码行复核确认调用条件与无条件取数;一次性内联探针（真实 Store + 真实 `get_ota_status`,monkeypatch 推送计数）对 state=ok、updated_at 极旧的会话仍产生 device_pushes=1、is_upgrading=False。既有出图测试全部 mock 推送,故时延不可见。

建议：出图闸调用条件收紧为 in-flight 态集合（或追加超时对账 reason）;或给终态会话加确认后清理/管理端终结（与 F50-04 合并考虑）。验收须含"终态会话 + 设备不可达时出图闸零推送"。

## F50-02 / P2：远端断开后第一次唤醒被"退休回合"吞掉

位置：S3 `main/protocols/websocket_protocol.cc:255-298`（OpenAudioChannel previous 存在即返回 false；行号随 F50/F51 批次顺移）+ `application.cc` 失败分支静默回 Idle。

远端断开时 OnDisconnected 只 RetireLocked 不清 `channel_`;120s 空闲超时同样留下未清通道。下一次唤醒/按键触发 OpenAudioChannel 时,只要 previous 存在就"只退休+返回 false",唤醒词被静默丢弃,**第二次触发才真正重连**。代码注释仅论证了"关闭通知未消费（previous_closed 非空）时排队 Idle 可能覆盖新会话"的情形;对"已退休且通知已消费"（previous_closed 为空）的通路不存在该风险,却同样失败。官方语音 WebSocket 使用此路径,属 1.0.51 相对 1.0.49 的用户可见回归（旧码同场景第一枪即新建连接成功）。

本机证据：源码行复核确认返回条件与注释论证范围;`scripts/tests/test_websocket_protocol_lifetime.py` 36 例覆盖替换时序正确,但未覆盖"通知已消费后下一枪应成功"。

建议：previous 已退休且 previous_closed 为空时,本回合继续走新建通道路径;仅存在未决关闭通知时才失败本轮。验收须含"远端断开→通知已消费→单次唤醒即重连成功"交错。

## F50-03 / P3：进程死于 set_target 之后,starting+版本匹配的会话永久卡死

位置：Hub `firmware_ota.py:426`（expire 被 version_match 挡住）、中段对账块与 is_upgrading 同函数内（行号随 F50/F51 修复批次顺移）。

`start_upgrade` 在 `set_firmware_ota_target` 之后、finally 之前若进程死亡（部署重启/OOM）,会话停在 starting 且带 target。设备随后完成升级并永远上报目标版本:stale 与 device_stopped 均成立,但 version_match=True 阻止 expire;中段版本对账成 ok 只覆盖 upgrading/uncertain/status_timeout。会话永远 starting→该设备出图永久被拦、同账号新升级永久 busy,设备自身完全健康。

本机证据：内联探针构造 starting+target+极旧 updated_at,伪造设备返回 idle/目标版本/ota idle,真实 `get_ota_status` 后 db_state=starting、is_upgrading=True、reserve 返回 None。

建议：settled 且版本等于目标时 starting 也应能收口为 ok;或 version_match 挡板只保护 uncertain/upgrading。窗口窄（set_target 到 finally 之间,含最长 20s 推送期）但后果永久无自愈。

## F50-04 / P3（运维风险）：设备永久离线 ⇒ 升级锁无限保持,无逃生门

位置：Hub `firmware_ota.py:426`（放锁必须设备可信终态）+ `store.py:1542`（reserve 要求旧会话终结）。

"查询失败不放锁、新升级须待旧会话终结"是 d58a671/b4508f9 的**有意设计**（方向正确,防误放锁）。代价是被升级设备永久失联/退役时,该账号在任何设备上都无法再发起升级,draw_admin 无会话管理,只能手工改库。不算修复缺陷,记录为运维风险;建议补管理侧手动终结或超长龄人工确认释放通道。

## F50-05 / P3：ota.state 两处判定口径不对称（失效方向为保守）

位置：Hub `firmware_ota.py:408`（device_upgrading 先 `str().strip().lower()`）vs `:414`（device_stopped 用原串做集合成员判断）。

设备上报 `"Idle"` 或 `"idle "` 时 device_stopped 永远 False→不放锁,而大写 `"Upgrading"` 却能被识别。fail-closed 方向,不构成误放锁;协议契约为小写缺省,现网大概率不触发。建议成员判断同样归一化。

## F50-06 / P3：无 EXIF 的 PNG 也被强制重解码+重编码

位置：Hub `draw-portal/draw_portal/pipeline.py:592`（PNG 魔数一律先 normalize）。

d58a671 之后每个 PNG 上传固定付出"normalize 全量解码+PNG 重编码 + shrink 二次解码",即使图片没有 EXIF;`shrink_oversized_png` 的零拷贝快路径经 `process_upload` 已不可达。方向消费功能本身正确（五条图片路径口径一致、8 个方向参数化测试过）,这是纯增量成本。建议 normalize 前低成本探测方向标签,无方向且已是 PNG 时直接透传。

## F50-07 / P3：回归矩阵缺口

位置：Hub `draw-portal/tests/test_firmware_ota.py:33`（无效正文矩阵）。

缺"state=idle + 版本有效 + `ota` 键整体缺失"组合（F50-05 的直接形态,静态推导不放锁但无测试钉住）;无"终态会话下出图闸推送次数"断言（F50-01 因此不可见）;无 starting+version_match 交错（F50-03）。b4508f9 新增 4 项回归本身为真实新交错,质量良好。

## F50-08 / P3：State::Open 复用检查竞态把瞬断变成硬失败，并留死代码

位置：S3 补丁 `managed_components/78__esp-ml307/src/http_client.cc:189`（`tcp_ && !can_reuse` 即 Shutdown 返回 false）及 `:196`（旧重建分支不可达）。

外层 CanReuse 判定与 State::Open 之间远端恰好断开时,本可自愈的复用失败被直接 Shutdown 返回 false,OTA 下载等调用方吃到一次硬失败（依赖上层重试）。守卫必要（防同 State 内重演 F48 撞车）但代价未注释,且旧分支成死代码。建议该路径让外层换新 State 重试,或注释声明"调用方必须重试"。

## F50-09 / P3：Close/析构不再唤醒 GetStatusCode 的事件组等待者

位置：S3 `http_client.cc:255`（Shutdown 不置事件位）对照 `:311`（OnTcpDisconnected 的置位被 retired 闸拦住）。

另一线程正在 `GetStatusCode()` 的 `xEventGroupWaitBits`（默认 30s）中时,Close 后要睡满超时才返回 -1。有界、且需跨线程共用同一 client。建议 Shutdown 置 retired 后补 ERROR|COMPLETE 位。

## F50-10 / P3：EspSsl::Send 与干净路径 Disconnect 的 TOCTOU 残留

位置：S3 `managed_components/78__esp-ml307/src/esp/esp_ssl.cc:147`（Send 循环顶部 connected_ 检查）与 `:139`（Disconnect 等接收任务退出即销毁 tls_client_）。

补丁保留借用指针只闭合了**放弃路径**;干净路径 Send 过 connected_ 检查后进入 `esp_tls_conn_write`,并发 Disconnect 析构 tls_client_ 仍是写侧 UAF 窗口。上游既有竞态,本轮收窄未闭合。方向:Send/Disconnect 共享计数或互斥,或析构前对在途 Send 有限等待。

## F50-11 / P3：FailChannel 在调用线程上同步销毁 WebSocket（阻塞点前移）

位置：S3 `main/protocols/websocket_protocol.cc:222`（审查时为锁外 `websocket.reset()`，F50/F11 批次已改异步回收任务）。

~WebSocket→Shutdown→EspSsl::Disconnect 3×10s 有限等待;接收任务卡死时音频编码任务或主循环被拖住最长 ~30s 后才收到 on_error/on_closed。旧码发送失败只 SetError、销毁推迟到下次 Open。有界（符合 F48 纪律）,但停滞点从"下次打开"前移到"失败当场"。建议失败路径延迟销毁,留待下次 OpenAudioChannel 替换时回收。

## F50-12 / P3（既有）：握手成功位置竞争可吞一次断连通知

位置：S3 `web_socket.cc:164`（OnDisconnected 以 exchange(false) 为门）与 `:185`（握手成功后才置 true）。

接收线程置 HANDSHAKE_SUCCESS_BIT 后、连接线程置 connected_ 前远端断开,通知被吞,死 socket 上 connected_=true。旧码语义相同,非本轮引入;恢复途径为发送失败/Pong 超时。仅记录。

## F50-13 / P3：领取期间 CLIENT_ALL 广播被会话限定，新 Telnet 客户端丢一条广播行

位置：量产 Grbl `Grbl_Esp32-massprod/Grbl_Esp32/src/Serial.cpp:460`（client_write 对 CLIENT_ALL 也走 write_for_session）。

loopTask 领取的行执行中触发软/硬限位→ALARM 以 CLIENT_ALL 发出（`Grbl_Esp32-massprod/Grbl_Esp32/src/Report.cpp:246`,可从领取中的 execute_line 经 protocol_buffer_synchronize 重入）;若旧连接已失效、新连接已被收养,标签=旧会话→失配→ALARM 在 Telnet 侧被丢弃（串口/BT 不受影响）。一次性丢一条广播行,新客户端下一次 `?` 仍能看到 Alarm 状态。建议 session 失配时对 CLIENT_ALL 来源回退广播;影响小,可接受不改。

## F50-14 / P3（既有）：未领取路径残留同类"收养泄漏"

位置：量产 Grbl `Grbl_Esp32-massprod/Grbl_Esp32/src/Protocol.cpp:229`（行溢出 error:20）、同文件 `:183`（SD 逐行 ok/error）、`Grbl_Esp32-massprod/Grbl_Esp32/src/Serial.cpp:273`（SD busy error）。

无领取登记的应答走广播 write(),仍会先 clearClients() 收养新连接,新客户端可收到一条未请求的 error/ok。概率与影响都低（S3 行长远小于缓冲、量产流不用 SD）,属修复前已有行为。彻底收口需这三处也携带会话校验,本轮不要求。

## F50-15 / P3（记录）：标签结构仅支持"每客户端至多一个领取任务"

位置：量产 Grbl `Grbl_Esp32-massprod/Grbl_Esp32/src/Serial.cpp:84`。第二次 claim 会覆盖标签,先来者应答误路由。当前生产不变量成立:ClientCommandOwner 唯一构造点在协议主循环（`Grbl_Esp32-massprod/Grbl_Esp32/src/Protocol.cpp:215`）,领取/释放同任务串行,注释已声明并有测试钉住。若未来让 webserver 等任务直接执行 Telnet 行,需先改 per-owner 结构。

## F50-16（对照）：普通 Grbl 分支缺同等应答会话修复

`D:/Users/Grbl_Esp32` @ `e5d7199a` 有等价 claim/release,但无 client_response_session/write_for_session/write_locked,Telnet 分支仍直接 `telnet_server.write()`（先 clearClients 收养）,F49-03 场景在普通分支仍存在,也无对应回归测试。量产交付只依赖 massprod 分支,此为分支同步欠账记录,不阻塞。

## 验证与证据

量产 Grbl 引用以 `Grbl_Esp32-massprod/` 前缀指向该工作树（门禁外仓索引不含它，按无法唯一定位跳过、不登记指纹）。本轮未新建入仓证据目录;验证 = 三仓既有回归实跑 + 源码行复核 + 一次性内联探针（系统临时目录,不入仓）。审查全程未修改产品代码,三仓受跟踪文件接手与结束时均干净。

| 验证 | 结果 |
|---|---|
| Hub `draw-portal` `tests/test_firmware_ota.py`（mcp-server venv + CI 等价 PYTHONPATH） | 56 passed |
| Hub `draw-portal` `tests/test_pipeline_raster.py` | 27 passed |
| S3 `scripts/tests/test_transport_owner_lifetime.py`（真实组件源码宿主 g++ 编译） | 12/12 OK |
| S3 `scripts/tests/test_tls_lifecycle.py` | 6/6 OK |
| S3 `scripts/tests/test_websocket_protocol_lifetime.py` | 36/36 OK |
| 量产 Grbl `scripts/tests/test_telnet_response_session.py`（真码提取编译） | 4 passed |
| 量产 Grbl 全量 `python -m pytest -q scripts/tests`（CXX=g++、工具链目录前置 PATH） | 85 passed（= 586e072d 时 83 + e3cc4f81 新增 2） |
| Hub `scripts/agent_gate.py --profile docs` | PASS（287 处指纹全对；基线重登 12 处，全部为本轮有意新增引用） |

本轮门禁维护（顺带修复,均非产品代码）:①`scripts/agent_gate.py` 的 `_REF_SKIP_DIRS` 补 `.qoder`——10-23 新建的 `.qoder/worktrees/f49-followup-20261023/` 整仓副本使裸名引用解析歧义,21 处基线引用变"目标消失";补录后与 `.worktrees` 同规则跳过,自测 140 项过。②readiness 两处 `application.cc` 行号随 S3 1.0.51 插行顺移（:991→:996 唤醒词 #if 分支、:1260→:1265 SendMcpMessage）,kxnx 28 一处随本条①顺移（:928→:930）。
已过测试是修复回归与基线证据,不是本文新发现已修复的证明。未跑：IDF/PIO 真构建、QEMU、wokwi、fz、全量 hub 门禁（pytest 1673 + e2e）、真机注入;C++ 结论基于源码结构推演 + 宿主编译回归。Grbl 首跑 9 例失败为本机 Git Bash MinGW DLL 遮蔽问题（0xC0000139）,前置工具链 PATH 后全过,非代码回归。
