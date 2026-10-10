# 三仓深度复审（2026-10-10）

本轮基线为 hub `f430258`、S3 `301eb65`、量产 Grbl `b280e525`。产品源码只读，未连接设备、未部署、未刷机；复现只替换网络、RTOS、串口和数据库边界。证据目录为 `results/local-only/2026-10-10-trirepo-deep-review/`，探针在 `tmp/trirepo-deep-review-20261010/`。

## 发现

### DR10-01 / P1：Telnet 断线不清空该客户端的半行缓冲

量产 Grbl 的 `Protocol.cpp` 为每个 client 保留 `client_lines[client]`，超长行和普通半行都要等 CR/LF 才结束；`Serial.cpp:223` 的 `client_reset_read_buffer()` 只在协议启动时调用。`TelnetServer::end()` 与 `clearClients()` 只关闭 `WiFiClient`，没有重置 `CLIENT_TELNET` 的 `client_buffer` 或 `client_lines`。

真实函数宿主探针 `cpp-probes.json` 的 `telnet_session` 证明了边界：上一连接发送未终止的 `G1 X10 F100` 后断开，新连接发送 `$I\n` 时，协议层仍可见旧连接数据。当前测试覆盖短读、零读、关闭和重新 accept，但没有跨连接半行用例。影响是旧连接残片可与新连接首行拼接，造成错误命令或在特定拼接下执行旧运动参数；这是会话隔离缺口。

### DR10-02 / P1：自动配网找不到当前 SSID 时复用上一轮凭据

`plotter_provision.cc:477-484` 只在 `SsidManager::GetSsidList()` 找到当前 SSID 时覆盖 `home_ssid_`/`home_password_`；对象成员没有在每次 `ProvisionTask()` 开始清空。当前 SSID 不在凭据表时，代码检查的是上一轮留下的非空 SSID，随后仍可跳到工厂 AP 并发送旧凭据。

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
