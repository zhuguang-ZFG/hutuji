# 三仓深度复审（2026-10-10）

本轮基线为 hub `f430258`、S3 `301eb65`、量产 Grbl `b280e525`。产品源码只读，未连接设备、未部署、未刷机；复现只替换网络、RTOS、串口和数据库边界。证据目录为 `results/local-only/2026-10-10-trirepo-deep-review/`，探针在 `tmp/trirepo-deep-review-20261010/`。

## 发现

### DR10-01 / P1：Telnet 断线不清空该客户端的半行缓冲

量产 Grbl 的 `Protocol.cpp` 为每个 client 保留 `client_lines[client]`，超长行和普通半行都要等 CR/LF 才结束；`Serial.cpp:223` 的 `client_reset_read_buffer()` 只在协议启动时调用。`TelnetServer::end()` 与 `clearClients()` 只关闭 `WiFiClient`，没有重置 `CLIENT_TELNET` 的 `client_buffer` 或 `client_lines`。

真实函数宿主探针 `cpp-probes.json` 的 `telnet_session` 证明了边界：上一连接发送未终止的 `G1 X10 F100` 后断开，新连接发送 `$I\n` 时，协议层仍可见旧连接数据。当前测试覆盖短读、零读、关闭和重新 accept，但没有跨连接半行用例。影响是旧连接残片可与新连接首行拼接，造成错误命令或在特定拼接下执行旧运动参数；这是会话隔离缺口。

### DR10-02 / P1：自动配网找不到当前 SSID 时复用上一轮凭据

`plotter_provision.cc:479-486` 只在 `SsidManager::GetSsidList()` 找到当前 SSID 时覆盖 `home_ssid_`/`home_password_`；对象成员没有在每次 `ProvisionTask()` 开始清空。当前 SSID 不在凭据表时，代码检查的是上一轮留下的非空 SSID，随后仍可跳到工厂 AP 并发送旧凭据。

`cpp-probes.json` 的 `stale_credentials` 用真实 `ProvisionTask` 函数和替身复现：第一轮缓存 `Home-A`，第二轮当前变为 `Home-B` 且列表为空，仍发送 `Home-A`；空对象才不发送。后果是把上一网络的凭据写入另一台/另一轮配网目标，并可能随后重启设备。

### DR10-03 / P2：开放网络空密码被当成查询，不能清除旧密码

`plotter_provision_core.h` 允许空密码，`BuildCommandSequence()` 生成 `[ESP101]`。量产 Grbl `ProcessSettings.cpp` 把没有值的 `[ESP101]` 解析为 `value == nullptr`，进入读取分支；因此不会写入空字符串。真实 HTTP handler、ESP 解析器和 `StringSetting` 宿主组合见 `cpp-probes.json` 的 `empty_password`：空密码命令回 HTTP 200/ok，但旧密码保持不变；显式空值在 StringSetting 长度闸下也被拒绝。配网流程会继续发送 `[ESP110]STA` 和 `[ESP444]RESTART`，用户会看到“成功”而设备仍尝试旧密码。

### DR10-04 / P2：待确认预览接受暂停/继续，状态可伪装成 streaming

`hutuji_job.cc:1285` 的 `RequestPause()` 只检查 `busy_`，没有拒绝 `awaiting_confirmation_`；`RequestResume()` 同样只看 `paused_`。在待确认态调用暂停会向 Grbl 发送 `!`，继续会发送 `~` 并把状态写成 `streaming`，但确认门仍为真且没有出图任务。探针 `cpp-probes.json` 的 `pause_preview` 输出为 `!~`、`confirm_gate_still=1`、`local_state=aborted`（随后 abort 取消预览）。现有 `test_hutuji_job_rejections.py` 只覆盖 streaming/试笔/换纸和重复暂停，没有把 awaiting_confirmation 作为暂停/继续输入。

### DR10-05 / P2：产线状态/归档不要求烧录事件保留整片备份附件

`Store.status()` 只调用 `_attachments_intact()`；空附件列表会返回 true，且对 `flash_s3`/`flash_grbl` 没有强制 `flash_backup` 名称。`archive()` 同样只遍历数据库当前存在的附件，记录被删除后不会报缺失。

`hub-probes.json` 使用真实 `Store` 和真实 `hardware.flash` 流程：初始烧录事件带 `flash_backup`，删除 `event_attachments` 记录后 `missing_backup_reported=false`，`archive_without_backup_accepted=true`。这会让状态和迁移档案显示烧录通过，却没有整片回退证据；现有测试覆盖附件内容篡改和普通 release 报告损坏，没有覆盖“附件登记记录被删除”。

## 其他核对

- 量产发布包校验能拒绝普通换纸 Grbl：massprod 镜像被接受，普通 Grbl 镜像因芯片标识不符而在写入前拒绝，写入调用数为 0。
- 量产 Grbl 目标回归 `test_protocol_overflow.py`、`test_telnet_receive.py`、`test_web_credentials.py` 共 **23 passed**；它们没有覆盖 DR10-01 的跨连接半行。
- 产线 `test_traceability.py`、`test_package_flash.py`、`test_inspect.py` 共 **17 passed**；未覆盖 DR10-05 的缺附件登记记录。
- S3 纸张专项执行 15 项，其中 11 项通过、4 项在当前 Windows/ESP clang host 链接阶段失败（`_Unwind_*` 未定义）；该工具链错误不能归因到本轮产品行为。
- OTA 设备端 `hutuji.ota_start` 仍只检查 `version.empty()`，不比较当前固件版本，也不验证传入 `sha256`；Portal 二次版本复核不能覆盖设备消息工具的直达调用。该点与上轮 PR10-03 的“Portal发送前复核”不同，属于设备端独立闸缺失，需另开修复批次。

本轮没有修改产品源码、测试或部署文件；以上问题保持未修复，不能把宿主探针结果写成实机故障或出货结论。

## 续审新增问题（2026-10-10 22:00）

基线为 hub `24bfc8e`、S3 `301eb65`、量产 Grbl `b280e525`。本轮仍只读产品源码，复用了本次量产控制 HIL 的真实串口日志；没有改源码、部署或刷机。探针汇总在 `results/local-only/2026-10-10-trirepo-deep-review/followup-probe.json`。

### DR10-06 / P2：量产 nopaper 的电机遥测固定为 Off，出图中会陈旧

量产 Grbl 的 `nopaperStatusHandler` 固定返回 `Paper=No MotorEn=Off PanelHold=Off Changing=Off`（`WebSettings.cpp`）。S3 的 `StatusJson` 只读取最近一次 `[ESP901]` 缓存；出图状态分级又禁止在流式阶段发送普通 `[ESP901]`，因此 `motor_en` 在 `Run`/`Hold` 期间不会刷新。实际量产 HIL 日志同时出现 Grbl `<Run|MPos:...>` 和 status `motor_en=off`，停止串口随后仍记录 `$MD` 释放。

这会让 Portal/小程序把“电机实际可能已使能”显示成 `off`。当前控制入口按 `state` 另有忙态闸，暂未证明会直接放开危险操作，但遥测字段已经不能作为电机安全状态依据。修复方向是明确该字段在 nopaper/流式期间返回 `unknown`，或增加不干扰流控的真实状态来源；不能继续把固定 Off 当作实时事实。

### DR10-07 / P1：设备直连 OTA 工具绕过版本和摘要校验

`hutuji_ota.cc` 的 `hutuji.ota_start` 只检查当前设备空闲、board、允许 host 和 `version` 非空，然后异步调用 `Application::UpgradeFirmware(url, version)`。传入的 `sha256` 明确被 `(void)sha256` 丢弃，也没有把请求版本与 `CurrentFirmwareVersion()` 比较。Portal 的 `start_upgrade` 二次版本检查只覆盖 Portal 路径，不能保护持有 messaging token 的设备工具直调。

因此允许域名下的旧版本可被请求降级，镜像内容也没有设备侧摘要绑定；云端 catalog/Portal 校验与设备直调之间存在完整性断层。该问题与 Portal PR10-03 是两条独立路径，修复应在设备工具入口再次钉当前版本单调性，并让下载校验使用已核对的摘要；在此之前不能把“Portal OTA 二次版本复核”描述成设备端 OTA 安全闭环。

### DR10-08 / P2：Portal token 自愈写回缺少绑定代次 CAS

`draw_portal.app._device_control` 在 401/403/404 后铸造新 messaging token，调用 `store.update_binding_token(user_id, ..., device_mac=...)` 无条件写回；同文件的语音/推送路径也有同类写法。Store 已提供带 `device_mac + user_id + bound_at` 条件的 `update_binding_token_if_current`，但该控制路径没有使用它。

两个并发请求可同时拿旧 token 失败、分别铸造新 token，然后后完成的旧绑定快照覆盖先完成的新 token。若云端铸 token 会轮换 client/session，下一次请求会再次 401，形成偶发“刚自愈又失效”；换绑同时发生时还可能把旧所有权的 token 写回当前绑定。现有回归覆盖单次自愈与换绑，不覆盖两个并发自愈的交错顺序。

### 既有问题复核

DR10-01（Telnet 半行跨连接）、DR10-02（配网旧凭据复用）、DR10-03（空密码查询语义）、DR10-04（待确认预览接受暂停/继续）和 DR10-05（产线附件删除后历史仍可归档）本轮均未见修复提交，仍按上一节风险等级保持打开。设备 OTA 的 DR10-07 另列，避免与已修复的 Portal PR10-03 混为同一问题。

## DR10-01 至 08 修复批次（用户“继续完全修复”）

用户授权覆盖八项问题的代码、回归、构建及门禁。本批保持量产nopaper，未改变运动参数、换纸或GPIO，未部署或刷机；此前控制HIL是1.0.38旧发布的证据，不能替代本候选实机验证。

| 项目 | 最终实现 | 回归边界 |
|---|---|---|
| DR10-01 | Telnet持锁清ring并发布Serial代次；Serial清队列并向协议线程返回屏障，半行由协议线程清理；入队比较读取时代次 | 真实Serial队列和Protocol解析泵覆盖半行、超长行、旧排队命令、已取出旧字节与其他client隔离；原短读/发送截止回归保留 |
| DR10-02 | 读取当前SSID前清本轮home_ssid_/home_password_ | 真实ProvisionTask先有A、再缺B时不发送A，再取得B时仅发送B |
| DR10-03 | S3空密码发`[ESP101=]`；Grbl明确写空与查询分开，仅STA密码放行空值 | 真实HTTP/ESP解析/StringSetting组合验证空密码清旧值，中文/空格/右括号边界保留 |
| DR10-04 | stream锁内再取state锁，只放行streaming/paused，确认门未消耗时拒绝 | 真实RequestPause/RequestResume在previewing和awaiting四种组合均零实时字节；既有幂等/试笔/换纸拒绝断言保留 |
| DR10-05 | 当前成功flash要求备份登记，archive核所有历史pass或backup_complete/write_attempted记录 | S3/Grbl × pass/fail/pending × 原代次/返修代次12场景删除登记均拒绝归档；原档案迁移与签核损坏用例保留 |
| DR10-06 | Grbl nopaper回MotorEn/PanelHold Unknown，S3对nopaper无条件上报unknown | 真实nopaper handler与S3字段解析组合覆盖已有On缓存更新为Unknown；未改变$MD策略 |
| DR10-07 | 工具严格版本/摘要校验和执行点复核；Application透传摘要/版本给Ota，PSA增量hash与完整长度/镜像版本检查在启动切换前 | 真实OTA函数覆盖摘要相符/不符、响应截断、短头、分片头、镜像版本不符、句柄/缓冲/hash回收；真实工具回调验证同/旧/非法版本与非法摘要拒绝，持有占位和调度失败释放 |
| DR10-08 | Store四字段CAS（含旧密文）；控制、速度、纸张、语音/发送及OTA自愈统一使用；同链新快照继续推进 | 两SQLite实例争同一旧token仅一方写入；真实控制路由在铸token期间换token、换代次、换所有者均409且不重发动作 |

续审结论校正：原文把已有绑定代次CAS当成能阻止同代次token覆盖并不充分，本次增加旧密文比较；“并发铸token必使另一token 401”未取得上游保证，故回归证明的是丢失更新与旧快照拒收，不宣称云端撤销语义。原电机问题证实的是设备status字段失真，未发现Portal/小程序当前页面实际渲染该字段，不能写成已观察到页面错误。

验证过程的失败保留：早期变更导致宿主I/O替身缺字段；OTA SDK真实构建识别到误加在SetupHttp中的返回类型检查，已撤除该无关改动；测试摘要夹具长度错误和测试数据库属性名错误均已修正。fz旧Telnet夹具新增Serial会话边界替身，既有断言不变；产品会话组合回归另实跑。所有最终数字/退出码/源码摘要与候选镜像见`results/local-only/2026-10-10-dr10-fixes/verification.json`。临时脚本在`tmp/dr10-fixes/`，无凭据。

OTA下载候选仍沿用1.0.38版本号，仅用于构建验证，标记NOT_FOR_RELEASE，不能覆盖现役公开镜像。后续正式发布须新版本、Grbl先行（开放网络新语法）、再S3、最后Portal；受保护设备工具不支持降级，回退走已留存的本地工位刷写流程。现场配网、配对、OTA拒绝/升级和触屏、纸面、老化均未由本轮软件测试替代。

### 2026-10-11 续作校正与验证

DR10-08追加连续自愈修复：纸张查询更新凭据后，内部推送原来仍以旧binding写回；语音画写在发送前纸张复核后，也仍携带进入请求时的旧密文。三条真实路由分别复现409。现内部推送以实际发送的current快照做CAS，语音发送前复核返回最新密文并同步回调持有的binding；三条回归由失败转为通过，原并发换token/换代次/换所有者拒绝用例仍通过。该修复只接力已经重新核验归属的凭据，不在CAS失败时读取赢家后强行覆盖。产线另补损坏detail JSON/非对象元数据的拒绝路径，避免状态与归档抛出未处理异常。

续作测试：产线78项、Portal567项、S3宿主355项通过；量产Grbl65项、DIO release与fz standard通过记录保留。S3裸环境第一次误用ESP clang导致宿主链接失败，显式MinGW环境复跑355项通过；Portal使用现有MCP venv，NVS测试显式选择IDF Python，未安装或改动依赖。编译核验纠正了上一轮立创配置的不存在符号`CONFIG_BOARD_TYPE_LICHUANG_DEV`：它实际选中默认bread-compact-wifi，且日志未到构建完成，不计立创通过。改用全新配置输入`CONFIG_BOARD_TYPE_LICHUANG_DEV_S3`，核对生成配置仅此板型为true，完成真实构建3125072 B；共享ccache挂起后关闭缓存重跑，失败日志保留。Freenove生成配置单独核对为对应量产板型。完整命令/退出码、源码及三份候选镜像SHA见本批verification.json；固件候选均标NOT_FOR_RELEASE，未发布、未刷机。

门禁仓库映射：枢纽full的历史§9、行号指纹和13项金表引用普通`D:/Users/Grbl_Esp32`，它不验证量产分支；量产候选独立使用`D:/Users/Grbl_Esp32-massprod`跑宿主、DIO构建及fz standard。本次临时脚本一度把所有模式统一指向量产仓，产生78处历史指纹漂移、7处越界、缺入口身份及MCP 1 failed/1641 passed/1 skipped；该失败保留，不通过重登指纹或移植普通机型逻辑消除。修正模式对应的根目录后，只剩真实app行号纯移动，`--fix-line-refs`唯一匹配重定位，未修改引用内容。

最终full 13层PASS，MCP1643项（172.24秒）、e2e PASS、264处行内容指纹一致；文档收尾单独复跑docs。量产fz报告绑定`b280e525`的本批dirty候选（fz `e36c125`，含Serial代次接口夹具），overall=pass；宿主/SIL不能作为板上证据，Wokwi月度quota仍未验。三仓候选均保持未提交、未部署和未刷机。
