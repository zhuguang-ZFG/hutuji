# 2026-10-09 三仓续审：断线时序、OTA资源与拒绝原因

用户「还有其他问题吗三仓」。本轮只审查和复现，不修改产品代码，不部署、OTA、串口连接或驱动机器。审查基线：枢纽`55fc05a`（产品代码`3074cef`）、S3 `f1fffce`（1.0.33）、量产Grbl `d723f4c`（`massprod/nopaper-1.3a`）。三者受跟踪文件起点无改动；普通Grbl原`.gitignore`和fz两个未跟踪文本保留。量产仓无独立AGENTS.md，约束取枢纽与普通Grbl入口，不移入换纸模块。

## 已确认问题

### R09-01 / P1：瞬时断线可能立即触发量产Grbl重启

位置：量产`Grbl_Esp32/src/WebUI/WifiConfig.cpp`的`WiFiEvent`与`handle`。

`handle`先采样`now=millis()`，随后调用WiFi状态接口，再读取共享的`_sta_link_down_since_ms`。网络事件任务可以在这期间把断线起点写成较新的时刻；`uint32_t down_ms = now - _sta_link_down_since_ms`随即下溢，被当作已超过600000毫秒，调用`ESP.restart()`。这不是必须等49天的计数器回绕：一次普通的线程交错即可发生。

确定性复现编译当前真实`handle`及`WiFiEvent`，只替换WiFi、时钟与重启接口：采样10000ms后，模拟独立事件任务在10001ms报告首次断线，结果`restarts=1`。无交错的首次断线为0；实际断线600000ms为1，两个对照符合预期。所用Arduino 1.0.4框架的`WiFiGeneric.cpp`由独立`network_event`任务消费事件并调用回调，确认此交错在真实任务结构中可达。

影响：短暂WiFi波动可能引发整板重启、打断正在执行的任务并丢失机器坐标；未在实机注入该窗口，不声称它就是此前每次断线事故的根因。

建议优先修复：统一断线时间状态的并发归属或同步快照，避免以早于断线起点的时钟做无符号差值；同时保留正常millis回绕、15秒首次重连、30秒重试和10分钟持续断线重启语义。不能只改阈值或加volatile。

### R09-02 / P2：S3 OTA读取失败后遗留OTA句柄

位置：S3 `main/ota.cc`的`Ota::Upgrade`中`http->Read`返回负数的分支。

函数在已执行`esp_ota_begin`及一次写入后遇到网络读取错误，只释放4096字节传输缓冲并返回false，没有`esp_ota_abort(update_handle)`。与写入失败分支的清理不一致。实际SDK为ESP-IDF v6.0.1提交`8c19b156084a0753687347cca1f5355782893533`：`esp_ota_init_entry`分配并挂入OTA操作链表，`esp_ota_abort`或`esp_ota_end`才摘除/释放。

真实`Ota::Upgrade`宿主故障注入：成功下载`begin=1/end=1/active=0`；首块写入后读失败`begin=1/abort=0/end=0/active=1`；建立OTA前读失败`begin=0/active=0`。三种情况下传输缓冲都释放，泄漏对象是SDK OTA操作条目，不能误写成每次泄漏4KiB。

影响：网络不稳下反复升级失败会累计遗留条目与堆占用，直到重启。建议在begin成功后以统一清理路径或RAII保证异常/早退均abort，正常end后不重复释放；补首读失败、写入后读失败、正常成功与写失败对照。未做真实闪存故障注入。

### R09-03 / P2：门户提前丢掉设备拒绝原因

位置：`draw-portal/draw_portal/push.py`的`push_tool_call`，以及`app.py`的`_device_control`。

`tool_result_failed(raw)`识别失败后，推送层立即把正文替换成「设备未执行该操作，请检查设备状态」。控制路由直接回这句；即使绕过推送层，路由的第二次判错也会换成通用文案。因此小程序0.3.25虽然能显示完整`detail`，也拿不到真实拒绝原因。

本机通过真实注册/绑定/控制路由，仅替换外部设备HTTP响应：设备返回`{"error":"正在调整速度，请稍候查询结果"}`，控制API实际返回`{"status":"error","detail":"设备未执行该操作，请检查设备状态"}`；设备HTTP替身调用1次，具体原因未保留。拒绝仍正确判失败，不是再次把拒绝当成功；缺陷是用户无法区分忙、升级中、待预览等原因。设备保持原任务状态时，`last_error`也不能补回这条拒绝信息。

建议保留共享的逐层判错，在业务拒绝时提取并限制长度/脱敏后的设备用户文案；传输错误仍用固定提示，不直接回显任意网关正文。补覆盖真实`push_tool_call`的API回归，不能只给页面喂手写的详细错误桩。

## 范围、证据与剩余边界

- 两组宿主探针共6个场景，直接提取并编译当前C++函数；1个Portal真实路由故障注入。它们证明上述软件路径，不能替代板上验收。本轮未重跑未修改产品代码的全套构建或门禁。
- 本机证据：`results/local-only/2026-10-09-trirepo-review/`中的C++源/编译日志、`grbl-watchdog-time-race.json`、`s3-ota-read-failure.json`、`portal-rejection-detail.json`。脚本在`tmp/trirepo-review-20261009/`，不提交设备凭据或用户照片。
- 已排除本轮疑点：S3状态返回使用`status_error_mirror_`，没有直接并发读取工作线程的`last_error_`；量产Grbl客户端输入缓冲已为2048B，不能把旧256B溢出结论当本次新缺陷；OTA参数SHA设备侧未核是§4已明示的既有边界，未计入新发现。
- 仍缺证的交付项沿用readiness：S3近期停止/自愈修复的完整故障注入与触屏HIL、纸面实拍、小程序正式发布等。生产preflight主25/34、备26/34的旧配置/角色差异不属于这次新发现。

修复建议顺序：R09-01 → R09-02 → R09-03。以上为有复现证据的问题清单，不是三仓全量无遗漏保证。

## 用户同意后的修复（2026-10-09，覆盖上方待修复状态）

用户明确「同意」上述修复顺序。改动白名单见`trirepo_followup_fixes_20261009_authorized`；外仓写入先核原SHA并经目录权限批准。普通Grbl和fz产品/验证源码不改，既有未跟踪资产保留。

- **R09-01**：量产`WifiConfig.cpp/.h`的断线时间戳只由`handle`任务读写，`WiFiEvent`仅原子发布GOT_IP恢复标志；恢复后即使立即再断线，也重新计时。12项真实函数回归覆盖1ms交错、正常10分钟重启、15秒/30秒重试、millis回绕、显式关射频、启动设置未就绪和AP回落。RED 1失败/11通过 → GREEN 12通过；未改四个时间阈值、网络参数、运动、换纸或引脚。
- **R09-02**：S3 `main/ota.cc`用局部RAII守卫管理OTA句柄，用带`heap_caps_free`删除器的`unique_ptr`管理下载缓冲；早退及异常统一回收，正常下载完成仍先释放4KiB缓冲再校验镜像，`esp_ota_end`返回后清零句柄避免二次abort。11项真实函数回归覆盖begin前后读失败、写失败、begin/end/启动槽设置失败、读取/进度回调异常和连续3次失败。RED 4失败/7通过 → GREEN 11通过；镜像校验与启动槽切换行为保留。
- **R09-03**：共享`tool_result`同一次逐层解析保留外层失败和可显示原因；`tool_result_detail`只返回固定设备拒绝文案或中文映射，最长输入320字符，清理控制字符并复用共享脱敏，未知网关正文仍返回固定提示。Portal `push.py`与`app.py`的第二道判错均复用它，`mcp_result.py`重导出兼容。新增27项共享解析回归及8项真实控制路由回归；API RED 8失败 → GREEN 8通过，连同原传输/自动确认/解包共69项通过。未修改小程序，不需要为本修复重新上传前端。

### 验证与构建

- 量产Grbl全部host **28项通过**；S3全部host **288项通过**；Portal **495项通过**；小程序 **314项通过**；MCP **1640项通过**与e2e PASS。MCP总数和47个文件的计数记录已同步。
- fz **standard PASS**（161.32秒，目标为修改后的量产`massprod/nopaper-1.3a`），报告记录Grbl dirty=true；observe hard=0、`agent_should_block_done_claim=false`，19项运动/日志用例通过。Wokwi仍为启动前quota非阻断，不能称云启动已验。
- 枢纽full首轮除两处旧OTA行号指纹外通过：真实测试/e2e/§9标记均通过。两处原引用指向HTTP读取模板，代码改动后改成`Ota::Upgrade`函数名引用；逐处核对后删除旧指纹，最终full **13层PASS**（MCP1640项/151.79秒、e2e PASS、266处指纹）。full的§9源码标记按枢纽既定普通Grbl契约检查；量产改动的真实编译及运行时契约由上面的量产构建、host和fz standard验证。
- 量产Grbl PlatformIO release **SUCCESS / 39.52秒**，bin **1844944B**，SHA-256 `f7eeb63c47f4b098459d82cde979f2716a7ebbd9fb4194fe6141572bbbdac122`。
- S3既有量产Freenove/ESP-IDF v6.0.1重编译`ota.cc`、链接与分区检查通过，bin **3080272B**，SHA-256 `626a92e2e2e2146c1222997c5e96ae016dc3d0c4ead3919748ca5cf34218b4a4`，应用分区余量25%。新增代码行clang-format检查通过。该构建仍用1.0.33版本号，只归档为未发布候选，不覆盖线上同号镜像。

验证环境问题已留证：`pio`不在PATH，改用现有PlatformIO绝对路径成功；S3首轮host误选ESP交叉clang，明确设置主机MinGW g++后全套通过；服务重启中断时先核对进程及日志再启动未执行的检查，没有重复部署动作。

证据仍在`results/local-only/2026-10-09-trirepo-review/`：`watchdog-{red,green}.log`、`ota-{red,green}.log`、`portal-{red,green}.log`、`{s3,grbl,portal,node,hub,fz}-all.log`及构建日志。最终11份源码与两个候选镜像SHA、门禁报告记录在`fix-verification.json`及`hub-final.json`/`fz-final.json`/`fz-observe-final.json`。正常OTA缓冲释放时序另经RED3错误→最终S3全套288通过（66.21秒）及SDK重编译复核。两份新外仓测试为`test_wifi_watchdog.py`、`test_ota_cleanup.py`。本批**尚未提交、部署、刷机或做真实故障注入**；软件通过不替代板上验收。

## 发布与实机续作（用户追加「处理」）

原三项已提交推送：hub `df8ba2f`、S3 `00fd3ee`（1.0.34）、量产Grbl `4b72ade`。云端四文件（`tool_result.py`及Portal `mcp_result.py/push.py/app.py`）已从hub提交的LF blob双机部署并通过带包verify和生产导入/拒绝原因冒烟；备份京东`bee65e6e8ebd46798dab35fa92808e50`、阿里`7d69238f5e3a45e4b6b6a01180289986`。preflight维持主25/34、备26/34既有差异。

S3固定Freenove/MAC `b8:1f:3f:c4:58:2c`已OTA并启动1.0.34，镜像3080272B、SHA `cea42da2657471c738066a64efc8ec2acb101c791a4f660d2a160cfced2818af`，ELF `2260ff077…`。通过随机单路径的临时代理进行了3次真实OTA断流：每次声明3080272B，仅送65536B后断流，串口3次出现`Failed to read HTTP data: ESP_FAIL`，均回failed/download_failed并恢复Idle；三次内部最大连续块均8704B，未用此粗粒度采样宣称全局零泄漏。最后已重新写入完整同版镜像，串口成功/重启/1.0.34/ELF相符，状态及公网latest/catalog确认。临时nginx代理已删除，原配置哈希一致、nginx -t通过，故障服务/清理定时器均停止。

量产Grbl MAC `4c:c3:82:cc:8a:9c`已通过现有Web OTA安装`4b72ade`：发送镜像1844944B、SHA `f7eeb63c…dac122`，返回SUCCESSFUL且串口捕获重启，前后全部Grbl参数一致（XY6000/Z1200、$1=255），最终Idle/原点。先从旧提交隔离重建回退镜像，SHA `1e742142…1e5ab7`与既有已发布镜像一致。串口一度无应答，用户重新插拔CH340后恢复；未在未核身份/原点时下发升级。

已完成3轮无线电OFF/ON恢复，无整板重启、设置不变；生产控制接口对Idle设备暂停的拒绝原文「当前没在出图」也已穿过真实设备/网关/Portal验证。随后错误SSID/AP回落验证发现下述新增阻塞，不能把该项记为通过。

### R09-04 / P1：AP回落自动重连时访问已释放WebSocket对象

实机日志：恢复原SSID后连上路由器，随后`LoadProhibited`，`EXCVADDR=0xa5a5a5a9`。对本次发布ELF做addr2line，调用链为`WebSocketsServer::broadcastBIN` → `Serial_2_Socket::flush/handle_flush/write` → `client_write/grbl_send` → `WiFiServices::begin` → `WiFiConfig::begin/handle`。`Web_Server::end`删除`_socket_server`却未解绑`Serial2Socket`；重启服务前的日志沿旧指针发送，构成释放后使用。待发缓冲和并发发送/loop也必须与析构同步，仅置空指针不够。

已准备`Serial2Socket.cpp/.h`与`WebServer.cpp`补丁：共享递归锁覆盖日志发送、WebSocket处理与begin/end，先解绑并丢弃旧待发日志再删除对象。新增5项真实C++回归（析构后日志、解绑后flush、并发broadcast/loop与析构、重复重建）从4失败变为5通过。用户随后明确「同意追加修复并刷机复验」，已按下段完成应用、提交和板上验证，原SSID、Idle及原点均恢复。

本段证据在`results/local-only/2026-10-09-trirepo-release/`，包括3次OTA断流日志、完整恢复串口、临时配置回收、Grbl OTA及3轮网络切换、`grbl-fallback-before-ws-fix.log`/回溯/对应ELF、`ws-red.log`/`ws-green.log`。Wokwi重新尝试仍为月度quota，用户明确选择保留未验证记录；其他工作继续。

### 追加修复发布与最终边界

量产Grbl追加提交`548c669c903880b8aef74e1c6184b30fe61d0dae`已推送：三份网络文件及`test_websocket_lifecycle.py`共4文件，不改运动、换纸、引脚或固件版本指纹。33项host通过，PlatformIO release 45.26秒成功；镜像1846416B、SHA `78d96db8ea80e84673aabf0d02f207f07de470001cb71a19bcb5ab8e031e62ec`。提交后fz standard PASS且绑定该SHA、dirty=false，observe无硬问题；枢纽full13层PASS（MCP1640、e2e、266处指纹）。

固定MAC量产Grbl再次Web OTA SUCCESSFUL并观察到重启，前后完整设置相同。**同一错误SSID/AP回落用例已在最终固件通过**：观察到看门狗重试，恢复原SSID后自动回STA，核对SSID已恢复、无panic/整板重启、设置不变、最终Idle/XYZ原点。证据为`grbl-ws-ota-result.json`、`grbl-fallback.json/.log`；首次崩溃保存在`grbl-fallback-before-ws-fix.*`，不覆盖失败证据。

S3最终为1.0.34、Grbl最终为`548c669`，云端运行`df8ba2f`四文件包。S3旧1.0.33镜像及catalog/latest备份保留；实际推荐指针备份目录为`/root/hutuji-bak-20261009-ota-1.0.32`（沿用模板后缀，目录内保存的是升级前1.0.33推荐）。Grbl旧镜像`grbl-rollback-d723f4c.bin`保留，可按既定Web OTA/串口恢复流程回退。临时故障服务和清理timer均已停止，nginx原文件哈希复原；未开放新防火墙端口。

已验的是本批OTA断流与网络恢复边界，没有进行绘图运动、纸面验收或完整触屏HIL。Wokwi按用户决定保留quota未验证，不把宿主/板上证据冒充云启动通过。此节覆盖本文前述“未提交/未部署/未刷机”状态。
