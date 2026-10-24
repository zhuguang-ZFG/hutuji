# F51 三仓深度代码审查（2026-10-24）

用户要求「继续深度 code review」。本轮不再围绕 F49 修复增量，转向三仓**尚未深审的高价值面**：量产 Grbl 网络/命令栈全文、S3 hutuji 业务层全文（pipe/ota/绑定/身份）、Hub Portal 小型关键模块（认证/令牌/市场/推送）与共享判错真值。结论：**未发现新的 P1/P2**；新登记 1 项 P3 分支同步欠账与 1 项 P3 观察项；另有一批「已验证干净」区域与如实登记的未覆盖面。产品代码未修改；没有部署、刷机、设备调用或运动。

## 基线与范围

| 对象 | 基线 | 本轮覆盖 |
|---|---|---|
| Hub | `b069bbc` | auth.py/share_token.py/market.py/push.py 全文;app.py 出图与市场入口定向越权排查;`hutuji_mcp/tool_result.py` 全文 |
| S3 | `be52d51`（1.0.51） | hutuji_pipe.cc 全文（1550 行）、hutuji_ota.cc 全文、main/ota.cc 生命周期段、hutuji_bind_identity.cc 全文、hutuji_draw_bind.cc 主流程、hutuji_job.cc 定向（OTA 预约/互斥/StatusJson） |
| 量产 Grbl | `e3cc4f81` | TelnetServer.cpp 全文、Serial.cpp 全文、Serial2Socket.cpp 全文、WebSettings.cpp 上下文段、CLIENT_ALL 发送方全量枚举、ProcessSettings 分发段 |

本轮因模型并发配额受限，两个深审子任务（Portal 全量业务面、mcp-server 服务面）两次派发均中断，由主审以定向抽样替代，未覆盖面见文末如实登记。

## F51-01 / P3：量产 Grbl WebSettings 缺 f632f587 的空输出丢弃流守卫（分支同步欠账）

位置：量产仓 `Grbl_Esp32-massprod/Grbl_Esp32/src/WebUI/WebSettings.cpp:223`（`WebCommandContext context(out);`），对照普通分支 f632f587（`out ? out : &discarded` + 两个回归用例）。

量产仓已按内容移植普通分支另两项 Web 加固——per-task WebCommandContext（7398a264）与参数容量闸（f62ccf52，`params[10]` 越界写防护）均在位；但 f632f587 的三行守卫未随之移植。缺失后果：任何以空 `out` 调到 `WebCommand::action` 的路径，`listSettings`（ESP400）的 `espresponse->client()` 是空指针解引用，`showWebHelp`（ESP0）的 `webPrintSetColumn` 在 `espresponse` 为空时 `webColumn` 永不前进——列对齐循环死转。

当前可达性核查（本机源码行复核）：量产仓全部运行时入口都传非空流——协议行路径 `Grbl_Esp32-massprod/Grbl_Esp32/src/Protocol.cpp:109` → `Grbl_Esp32-massprod/Grbl_Esp32/src/ProcessSettings.cpp:617-619` 构造局部 `ESPResponseStream(client, true)`；Web 路径 `Grbl_Esp32-massprod/Grbl_Esp32/src/WebUI/WebServer.cpp:490-491` 含 silent 分支也 `new ESPResponseStream()`。唯一空值来源是 `Grbl_Esp32-massprod/Grbl_Esp32/src/WebUI/WebSettings.cpp:706`（ESP401 子分发透传环境 `espresponse`），而该值恒来自 action 已建的上下文。**故按潜伏缺口记 P3，不构成现网缺陷**；普通分支测试（`help.action(nullptr, user, nullptr)` 须 Ok 且零输出）已把空流钉为契约，量产缺守卫也缺该测试。

建议：随下一批量产改动移植 f632f587 三行修复与两个回归用例；不建议为此单独发版。与 F50-16（普通分支缺量产的应答会话修复）互为镜像，同记分支同步欠账。

## F51-02 / P3（观察）：量产 Grbl statusReport 标志位无 setter（死路径）

位置：量产仓 `Grbl_Esp32-massprod/Grbl_Esp32/src/Protocol.cpp:333-336`。

`rt_exec_state.bit.statusReport` 分支会以 `report_realtime_status(CLIENT_ALL)` 发状态；但全仓检索该位只有声明（量产仓 Grbl_Esp32/src/Exec.h 的 statusReport 位）与判读（同文件 Protocol.cpp 第 333 行判读)，**无任何置位点**——`?` 状态查询走 clientCheckTask 的直呼路径（Grbl_Esp32-massprod/Grbl_Esp32/src/Serial.cpp:346 → executeRealtimeForSession，无领取标签、广播语义）。因此 F50-13 记录的「领取期间 CLIENT_ALL 被会话限定丢弃」类不会经由自动状态报告扩大；该分支为上游遗留死代码。仅观察，不要求处理（删除属上游风格清理，非必要面）。

## CLIENT_ALL 发送方全量枚举（取证）

对量产仓 `src/` 全量 grep `grbl_send*/CLIENT_ALL`（覆盖性检索，非抽样），领取期间可达的 CLIENT_ALL 发送方逐一判定：

| 发送方 | 领取期间判定 |
|---|---|
| `Grbl_Esp32-massprod/Grbl_Esp32/src/Report.cpp:246` ALARM | F50-13 已记录（丢一条广播行，可接受） |
| `Grbl_Esp32-massprod/Grbl_Esp32/src/Report.cpp:222` SD 行错误、同文件 `:227`、`Grbl_Esp32-massprod/Grbl_Esp32/src/SDCard.cpp:60` 文件列表 | F50-14 已记录（量产流不用 SD） |
| `Grbl_Esp32-massprod/Grbl_Esp32/src/Report.cpp:812` 位置打印、`Grbl_Esp32-massprod/Grbl_Esp32/src/MotionControl.cpp:263` 归位警告 | 它们是领取命令自身的应答，被会话限定恰为**正确**行为 |
| `Grbl_Esp32-massprod/Grbl_Esp32/src/Serial.cpp:342` 复位 Debug、WiFi/BT/Telnet 服务消息（WifiConfig/BTConfig/TelnetServer.begin） | 均来自非领取任务（clientCheckTask/WiFi 任务），标签为 0 走广播，无丢失 |
| Spindles/*（VFD/Laser 等） | 本机型（Custom 3-Axis HR4988）不适用 |

## 已验证干净区（本轮深读未发现缺陷）

- **量产 Grbl TelnetServer.cpp（397 行）**：递归锁+锁序（telnet → Serial 临界区单向）、环形缓冲算术（单字节/批量 push 边界）、收养-会话 bump 全触发点、写超时 stop+reset_rx 收尾、keepalive 四参数查返回值——F49/F50 修复后无新缺陷。广播 write 收养后新客户端先收数据后收欢迎语属已知既定语义。
- **量产 Grbl Serial.cpp（479 行）**：领取/标签/任务句柄临界区、会话屏障（client_read 的 -2 先清半行）、实时字符会话核验（executeRealtimeForSession 同锁）、SD busy 错误路径——无新缺陷。
- **量产 Grbl Serial2Socket.cpp（214 行）**：2026-09-09 修复契约（先解绑、丢旧待发、发送/loop/析构同递归锁）保持；满帧即发防越界。
- **S3 hutuji_pipe.cc（1550 行）**：缓存/扫网发现与 banner/工厂身份验真（4KiB/512B 限额、槽位忙分流）、窗口化流控应答队列（满丢最老、断连清空、banner 重建清空）、授权探测状态机（R10/R21/R22 有界重试与挂起分流）、epoch 化 abort reset 事务、feed-hold 抢占仲裁、发送停顿预算；锁序 `reset_receive → write → sock` 全程一致——无新缺陷。
- **S3 hutuji_ota.cc + main/ota.cc**：URL/host 白名单与形状校验严实（userinfo/反斜杠/控制字符/IPv6/端口/查询串 `exp/sig` 规范形状全拒）、版本严格递增、OTA 句柄 guard 全早退回收、end 后置零防双 abort、SHA-256 先验后切分区、预约-调度-复核事务成对——无新缺陷。
- **S3 hutuji_bind_identity.cc / hutuji_draw_bind.cc**：P-256 一次生成坏记录不换钥、失败路径销毁 PSA 键并 zeroize、nonce 单次消费（仅成功时 erase）、短码无模偏置（256%32==0）、绑定运行世代守卫——无新缺陷。
- **S3 hutuji_job.cc（定向）**：TryReserveOta 双锁原子占用、失败成对释放、预约期 StatusJson 如实报 upgrading 与 JobIsIdleForOta settled 集合闭环。全文深读未做（见未覆盖）。
- **Hub auth.py / share_token.py**：HMAC 会话与分享令牌（常数时间比较、TTL、rsplit 容忍 job_id 含冒号、无撤销为已知设计）——无缺陷。
- **Hub market.py**：清单版本/条目/逐件 sha256 校验、半份清单不覆盖已验快照、inflight Future 去重、LRU 16、异常不泄漏清单文本——无缺陷。
- **Hub push.py**：token 不入日志、`_device_payload` 有界拆包、auto_confirm 状态机（awaiting/已越过门/idle/error/aborted 三分、无待确认早退、confirm_by_url 能力门）、401/403/404 铸新-回写-共享落盘-重试一次——无缺陷。
- **Hub app.py（定向）**：`/api/draw/{job_id}/send` 与 `/api/draw/saved/{saved_id}/send` 均按 user_id 作用域取记录、绑定必查、纸张快照核验在位;越权排查未见漏 WHERE。全量逐行未做（见未覆盖）。
- **Hub mcp-server `hutuji_mcp/tool_result.py`**：外层失败不被内层成功覆盖、每轮最多拆一层、深度 8 封顶 fail-closed、只透出白名单文案并过脱敏——无缺陷。

## 未覆盖面（如实登记，不得宣称已审）

- Hub `app.py` 全量逐行（3204 行；本轮仅入口定向）、`store.py` 迁移/事务（2832 行）、voice/quota/upload_kinds、device_identity.py、paper_settings.py、machine_speed.py。
- mcp-server 服务面：draw_queue/draw_worker/job_ledger、cloud_bridge 单 bridge 语义、device_messaging/activation_relay、curated_gate 白名单、config/redact 覆盖面（2026-10-13 已深审过 breaker/racers/bitmap_svg，本轮未重复）。
- S3 `hutuji_job.cc` 全文（4869 行；本轮仅 OTA 预约/互斥/StatusJson 定向）、websocket_protocol.cc 二刷（F50 已作结构审查，本轮未再深挖）、hutuji_kdraw_watcher/music/memory/conversation_report 等外围模块。
- 普通 Grbl 分支全文（仅核对了 Web 修复的内容在位情况）。
- 本轮未跑 IDF/PIO 构建、QEMU、fz 与全量 hub 门禁；未新增入仓证据目录。

以上未覆盖面的原因：两个深审子代理因模型并发配额两次中断，由主审定向抽样替代；下一轮建议优先补 mcp-server 队列/worker/bridge 与 hutuji_job.cc 全文。

## 验证与证据

纯读审查：源码行复核 + 覆盖性检索（CLIENT_ALL 全量 grep、statusReport setter 全量 grep、量产 Web 修复内容比对 `git show f62ccf52/7398a264/f632f587` 对照量产仓现行文件）。量产 Grbl 引用按 F50 惯例以 `Grbl_Esp32-massprod/` 前缀书写（门禁外仓索引不含该工作树，按无法唯一定位跳过、不登记指纹）。

| 验证 | 结果 |
|---|---|
| Hub draw-portal 全量 pytest（mcp-server venv，CI 等价 PYTHONPATH） | 629 passed，2 warnings（既有 DecompressionBomb/Starlette 弃用提示），132s |
| 三仓受跟踪文件 | 审查前后均干净（本轮零产品代码改动） |

