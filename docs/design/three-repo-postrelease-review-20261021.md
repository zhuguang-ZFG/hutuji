# 发布后继续三仓审查（2026-10-21）

修复续作授权：用户要求“全部修复部署验证刷机推送”并“继续”。R2-01至05已进入修复发布，历史未修复段落保留为发现时证据；机读授权r2_postrelease_fixes_authorized点名范围，S3递增1.0.45、小程序0.3.30。结果与边界见文末，不复用旧批CI/硬件证据。

用户要求“继续codereview三仓”。本轮审查Hub `a7ce429`、S3 `217253d`（1.0.44）和量产Grbl `e77c3dab`，普通Grbl只核相关分支差异；产品源码与正式测试只读。当前USB量产机身份继续沿用上一批已验证的COM6 `b8:1f:3f:c3:70:bc`和COM3 `4c:c3:82:cc:8a:9c`；没有设备调用、运动、刷机或生产修改。另一S3已被用户排除。

## R2-01 / P1：重画启动失败释放仍在归位的停止占位

位置：S3 `hutuji_job.cc` 的RequestRepeat、RequestAbort及StartAbortDrainHomeTask。

RequestRepeat先在stream锁内设置busy/repeat/streaming，然后解锁释放TLS并等待50ms，最后重新加锁创建绘图任务。窗口中RequestAbort看到streaming，会建立归位worker。最后的重画入口只检查busy，没有检查abort/worker；如果绘图任务创建失败，它无条件清busy、repeat并设置idle，却没有等待已在运行的归位worker，也没有另一个Run负责结算它。

编译当前真实三个入口及任务创建逻辑，在50ms替身中调用真实RequestAbort，再仅让 `hutuji_draw` 创建失败，输出 `busy=false / abort_worker_active=true / abort_requested=true / state=idle / creates=2 / pending_worker=true`。停止worker已占位，而新的作业/试笔入口仅依赖busy与preview_worker，具备与归位并发接管的条件。探针不执行真实运动，不将此扩大为已观测到板上撞车。

绘图任务创建成功对照得到busy=true，Run可以消费取消并等收尾；因此问题不是普通重画失败，而是启动前取消与创建失败交错。此前N21-01对运行中的Run终态修复不覆盖这个没有Run的入口。

建议将预启动取消交给明确的生命周期收尾者：创建失败不得释放其他worker拥有的busy；已经取消时不再创建绘图任务，保持停止收敛后才放开新任务。回归覆盖TLS释放期间停止、创建成功/失败、worker已完成/仍运行、重复停止以及新任务拒绝。

状态：**已复现，未修复**。

## R2-02 / P2：试笔失败与取消交错仍提示“笔测试已停止”

位置：S3 `hutuji_job.cc` 的RequestPenTest工作任务。

试笔已尝试Z运动后，会等待应答和fresh Idle并累积ok。收尾处却优先按abort_requested选择aborted和“笔测试已停止”，忽略ok=false。若Z运动已发送、应答超时/连接丢失，又收到用户停止，机器是否停稳及Z0完成均未确认，但页面和通知仍把停止当成成功。

编译真实试笔入口、PreparePenOrigin、WaitForIdleForSession及会话发送函数；在第一条运动应答处注入timeout、连接丢失与abort标记，输出 `sent_commands=3 / connected=false / busy=false / state=aborted / notice=笔测试已停止`。正常对照同样发送3条命令，连接有效、state=done、通知试笔完成。只是I/O替身故障注入，没有让实机断链。

建议让“取消了任务”和“物理停稳已确认”分别判定；已尝试运动时，取消不能压过未确认Idle/应答失败。无需运动的取消保留成功，失败须报告error/未确认停止，不为绿加入新的机械命令或reset。

状态：**已复现，未修复**；不与N21-01的普通绘图断流归位路径合并计数。

## R2-03 / P2：量产Telnet实际断开时未失效接收会话

位置：量产Grbl `WebUI/TelnetServer.cpp` 的handle、read及executeRealtimeForSession，Serial.cpp的会话队列。

handle检测到连接无效后，在 `_telnetClients[i]` 为true的内层条件中才清网络环并调用client_begin_session。然而当前实际构建的Arduino 1.0.4 `WiFiClient::operator bool()`就是connected()：断开后为false，这个内层分支不执行。没有新连接接管、end或写失败时，旧代次和旧环/Serial队列会一直保留；read没有连接判断，新锁内提交只比较_setupdone与_session，旧 `~` 仍可提交。

探针编译真实handle、read、实时提交和Serial入队/读取，WiFiClient替身按实际SDK的bool=connected语义建模，listener无新接入；队列预存 `$I`，网络环预存 `~`。断连后输出 `session_before=1 / session_after=1 / barrier=false / first_byte=36 / remaining_queue=2 / network_ring_bytes=3 / stale_realtime_dispatches=1`。现有宿主回归验证主动client_begin_session，未让真实断连handle消费实际bool语义。普通仓有独立_rx_active失效分支，源码上不具备同一遗漏，不能把这条写成两仓共同缺陷。

实际工具链证据：上一批本机release构建日志明确 `framework-arduinoespressif32 @ 3.10004.210126 (1.0.4)`。优先读取本机实际使用的包 `C:/Users/zhugu/.platformio/packages/framework-arduinoespressif32@3.10004.210126/libraries/WiFi/src/WiFiClient.h` 和对应cpp，package.json版本也为3.10004.210126；未使用旁边较新的2.0.17包作为结论依据。官方仓参考 [arduino-esp32 1.0.4 WiFiClient.h](https://github.com/espressif/arduino-esp32/blob/1.0.4/libraries/WiFi/src/WiFiClient.h)，本轮访问日期2026-10-21；证据为已安装实际SDK本地读取，未重新联网拉取上游。适用于当前ESP32量产构建，不推断其他SDK、目标芯片或板上出现频率。

建议用独立会话活跃标记或可证明的失效事件清接收环/Serial代次，不能靠bool已为false的客户端再判断是否值得清理。保持协议半行仅由协议线程处理、其他client隔离，不清已进入planner的命令。回归覆盖无新连接断开、网络环/Serial半行、旧实时字节、首次接入及重复handle幂等。

状态：**量产仓已复现，未修复**。这是N21-02锁内检查的上游失效发布缺席，不是该锁再次出现检查后窗口。

## R2-04 / P2：旧款无MAC配网页返回后丢失下一步绑定入口

位置：Hub小程序wifi.js的onShow、checkBinding及wifi.wxml。

旧款WIFI二维码仍合法，解析结果有ssid但mac为空。凭据已提交后phase=waiting、step=done，原页面为此显示“下一步：绑定设备”。一般后台返回时，onShow把有_scan的waiting交给checkBinding；缺mac分支改回creds/manual。下一步按钮只在done且无MAC时显示，creds中没有这个入口，却保留一键配网按钮。设备AP已经因联网关闭时，用户被引导重新连接/发送凭据，无法从当前向导继续绑定。

真实页面探针用合法旧款解析形状设置已提交等待后hide/show：`step=creds / bindState=manual / manualNextVisible=false / submitVisible=true`。列表阶段返回仍保持aps，是有限正常对照。没有手机实测，也不声称新v2二维码失败。

建议无MAC且已发送结果未知时保持done/manual与显式绑定入口；不能因为无法按身份查询就回到网络提交。继续保留MAC/nonce校验，不拿账号已有设备当成成功。回归从旧WIFI解析、凭据已发断连、后台返回到WXML可达入口验证。

状态：**已复现，未修复**。

## R2-05 / P3：已确认绑定回屏遇慢网退回“重试绑定”

位置：Hub小程序wifi.js的bindDone、onShow、checkBinding和bindError。

bindDone清pending并显示bound，却保留phase=waiting。返回页面时onShow不排除已确认bound，又调用checkBinding并先显示waiting；查询网络失败后bindError显示manual，隐藏开始创作/控制入口并给“重试绑定”。此前服务端成功回执被单次慢网覆盖，绑定事实没有撤销。

真实页面探针让claim返回bound，hide/show后对新增GET投network fail，最终 `bindState=manual / step=done / queryIssued=true`。旧无身份二维码查询返回expired的相邻对照也会退manual，但不是本条成立前提。只确认UI把已完成事实回退，不称后台绑定丢失或跨账号泄露。

建议完成阶段独立于等待恢复；回屏资料探测失败不得撤销已确认的绑定完成状态。账号/设备变化仍作废旧展示；若刷新发现真实解绑，使用明确归属结果处理，不能用网络失败等同解绑。

状态：**已复现，未修复**。

## 排除及观察项

- Telnet→WebSocket与WebSocket→Telnet两锁交错：宿主人工双线程得到两次超时，但实际clientCheckTask串行处理实时输入和Web handle，构造的第二线程不是这条正常产品路径，因此**不采纳死锁发现**。保存探针用于说明假设边界，不以其失败要求重构锁。
- Serial入队满2048字节时第2049个换行静默丢弃有真实函数输出；正式S3在途窗口只有512字节，本轮未证明它在交付正常链达到满队条件，**只登记通用客户端过载观察**，不算新增P1/P2、不改缓冲求绿。
- 原生扫码已推进页面代次，旧claim回包未把新扫码目标写为成功；探针新目标mac为空、bindState为空，**不采纳扫码旧回包越权论断**。
- 重画成功启动后取消由Run结算、试笔正常结果、新v2页面隐藏恢复与既有实时代次失效回归仍通过；发现不否定上一批已完成的普通成功路径验证。

## 证据与验证

探针 `tmp/review-20261021-r2/`；本机证据 `results/local-only/2026-10-21-review-r2/` 包含生成C++源码、编译日志、成功/失败输出、weapp-probe.json、s3-repeat-summary.json、s3-repeat-success.json、s3-pen.json、grbl-disconnect.json、grbl-queue.json和排除死锁的grbl-locks.json。source-snapshot.json保存审查提交、文件摘要与实际SDK片段，validation.json记录已跑结果。

相邻回归实际通过：S3 nopaper/pen_session/abort_drain_failure **26 passed**，量产Grbl realtime_session/telnet_session **5 passed**，小程序wifi/onboarding **66 passed**。本轮没有修改产品或正式测试，没有构建、fz、远端CI操作、部署、设备调用或刷机；只新增文档、状态登记和本机探针。文档门禁结果另记validation.json；五项未修复，不宣称发布完成。实体Hold及上一批设备身份/升级结论保持原范围。

文档门禁最终 **docs overall=pass / exit=0**，278处指纹相符、4处外仓未解析保留。首次仅六处旧引用因新增状态条目纯移动而失败，按唯一原文重定位后通过；首次日志保留。没有修改门禁、授权清单或批量追认未知指纹。

## 五项修复发布续作

重画预启动保持downloading，取消由原入口核对并清buffer/replay、发布aborted；取消后没有绘图或归位worker创建。试笔只有未尝试运动或ok为true的取消才发布成功停止；运动应答/fresh Idle失败保持error并提示停止未确认。新增8项真实入口/发送/尾段交错回归，成功、普通创建失败及无运动取消保留。

量产Telnet增独立_rx_active、共享reset_rx：主动关闭、换连、写失败/超时和被动断连统一清网络环/代次；断连不再依赖已false的客户端bool，未活跃会话拒实时提交。实际bool=connected的断连回归覆盖网络环、Serial屏障、陈旧~拒绝及重复handle幂等。旧短读桩只同步声明并编译真实reset，所有原断言保留。小程序无MAC等待回屏保留下一步绑定，完成phase为bound，同账号回屏不重新认领/回退；账号变化仍重新认证。新增3项页面/WXML入口回归。

当前本地S3全389、量产Grbl全76、Portal601、小程序350均通过，两S3真构建与量产Grblrelease构建通过。原full测试1691/e2e/§9标记通过但指纹24处纯移动失败，已按唯一原文自动迁移；fz首跑仅旧测试声明缺失与Wokwi配额问题，补声明后重跑，结果待本批回执。小程序0.3.30官方CLI明确√ upload、300718B，未提交平台审核。

本机本批证据 `results/local-only/2026-10-21-r2-release/`，编排 `tmp/r2-release/`。镜像仅按本次精确提交及SHA投放，单次OTA意图保护沿用；COM3已核量产MAC/Idle/原点/48项，S3云端身份c3:70:bc现1.0.44、Idle且就绪/授权/指纹通过。实际提交、部署刷机和控制回执后续补录，未完成项不能从测试推断。

### 已执行发布与当前实机边界

S3提交 `fe7120b0c92f7aa3753e2fad8d513bc081cd4dcd`（1.0.45）与量产Grbl `39ecd288bd9168807cd61b918397d9f8dffca156`已推送各自分支。旧码对照拆分重跑：重画2失败/2成功、试笔bad_ack1失败、量产断连1失败，当前新8+1项通过；首次合跑的额外宿主路径/超时错误不计作产品RED。fz standard复跑overall=pass，Wokwi月度配额在执行前耗尽为nonblocking失败，芯片云启动未验证；Hub full最终13层pass（1691+e2e），源码24处和状态4处引用纯移动均原文重定位，无人工追认。

量产Grbl COM3在身份/Idle/原点/48项设置闸通过后单次HTTP OTA，镜像1848512B、SHA `b6f356464d18897aaec4688acc24601b3046e2feb307126e298aba542b3749d4`，响应status=3，观察到重启且刷后48项与刷前相同。回退e77c3dab镜像SHA `217023f3797f0a941df637e79c58cc1a1e5c75fd1bc97b8061b1361ff1d0c59d`保留在本批目录（旧文件名grbl-rollback-bc2cf670.bin沿用工具，实际内容以SHA/回执为准）。S3主备均发布1.0.45，3092112B、SHA `4cebc939116c926ae0eb0fa7d07d122a36d272931432d3885233f65353fbd95c`；各节点latest/catalog及公网完整下载逐位一致，回退目录 `/root/hutuji-bak-20261021-r2-1.0.45`。立创3126784B、SHA `8b744f3161328ee963f57aac9dfaae712a6cf54937d562efc4bfa4056f923be0`仅构建。

S3 c3:70:bc生产Portal单次OTA从1.0.44升1.0.45，首次504保留unknown后未重发；随后ota_status ok/100、固件1.0.45、Idle/原点及连接/就绪/授权/指纹均true，热备独立MAC探活同样确认1.0.45。已完成预览取消、预览pause/resume和错作品confirm拒绝、正常402B小图至done/Idle/原点。重画探针在首次只读status就遇504，随后消息404，尚未发送repeat；再起stop/pen也都在初始status404退出，没有下发这些动作。此时COM3和COM6均从USB枚举消失，仅蓝牙COM4/5存在；最后控制后的48项复核未完成，不能写为全部控制HIL通过。已请求用户重新接好电源/USB，等待身份恢复再继续，未归因产品断连或重启。

小程序0.3.30已上传300718B，官方CLI明确√ upload，页面350项通过；真实手机后台/旧款配网恢复未手测。Hub本批无后端运行代码部署载荷。主备preflight为25/34、26/34，已登记的unit/drop-in/nginx和热备差异继续保留。CI以本批精确提交核对，尚未宣称完整矩阵通过。实体工厂配对、纸面、老化与交付签核保持Hold。
