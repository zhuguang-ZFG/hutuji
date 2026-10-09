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
