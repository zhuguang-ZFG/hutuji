# 量产批三仓续审（2026-10-10）

用户要求“继续 code review 三仓”。审查基线为 hub `3413e22e837d6a715a77131ed91562149cf499b5`、S3 `d6b7d98d0bf94932044c49bcb9266766341c98aa`（1.0.37）、量产 Grbl `3184ea9668ebe3c860eb68ea1ddf0bb265302de1`（`massprod/nopaper-1.3a`）。三仓受跟踪产品文件在本轮开始时无改动。

本轮审查量产工位、固件灰度、工厂配对及配网入口。产品源码只读；复现使用本地 SQLite、网络替身和从当前源码抽取的真实 C++ 函数，没有连接设备、部署、刷机或执行运动。以下五项为本轮确认的问题，未修复；“本轮发现”不表示都由最新提交首次引入。

## PR10-01 / P2：串口中文 SSID 被实时控制层吞掉

位置：量产 `Grbl_Esp32/src/Serial.cpp` 的 `is_realtime_command`（第240–245行）及 `clientCheckTask`（第179–180行）；hub `scripts/grbl_wifi_setup.py` 的输入校验和 `send_cmd`（第195行）。

配网脚本允许中文并以默认 UTF-8 编码发送，但 Grbl 在行解析前把所有大于等于0x80的字节当作实时指令消费。真实分类函数复现：`[ESP100]家里网络` 的12个UTF-8字节全部被消耗，只留下 `[ESP100]`，设置操作退化为查询。其编码中还有0x91，对应固件的进给倍率增加指令；不是单纯的显示乱码。脚本可能在收到查询的 `ok` 后继续写密码并重启。

范围是串口和走同一输入泵的原始Telnet。S3的HTTP配网直接进入ESP命令解析，不经过这层，因此不能宣称当前S3中文HTTP配网也失败。宿主探针验证了字节分类及真实指令枚举映射，没有在设备上执行倍率改变或验证机械后果。

建议先在现有串口配网工具明确拒绝非ASCII并指引已支持的HTTP路径，或单独设计并授权能区分配置字符串与实时控制的接收协议。不能简单删掉高位实时控制判据。回归应覆盖真实发送字节到接收分类的链路，UTF-8校验器单测无法覆盖本问题。

## PR10-02 / P2：工厂配对探测未消费本次命令的终止应答

位置：S3 `main/boards/lichuang-dev/hutuji_pipe.cc` 的 `VerifyGrblPeer`（第342–343行），以及 `PipeTask` 发布连接后再次发送 `$I` 的路径（第149、172行）。

带有效工厂身份的连接先发只读 `$I` 核对Grbl MAC，但检测到匹配MAC行即返回Valid，没有等待并消费本次查询的最终 `ok`。TCP将MAC报告与 `ok` 分开交付时，旧应答留在socket；随后正式连接再次发送 `$I`，旧 `ok` 进入 `WaitingBuildInfoOk`，因此新旧事务的应答错位。

真实函数宿主分包已复现“返回Valid后仍剩独立 `ok`”，并进一步执行当前 `ProcessLine`、`ParseStatusReport`、`HandleAuthProbeResponse` 与指纹检查。Grbl在独立输入任务处理实时 `?`，允许状态报告先于普通M5应答到达。在这一合法时序下，迟到的M5 `ok`被认作授权探针应答，真正的授权探针 `ok`又误入尚未收到参数值的 `$1` 查询，最终进入 `ready=true, authorized=true, settings_verified=false, Complete`。随后正确的 `$1=255` 也不能恢复指纹，连接保持期间持续拒画。合包对照则在 `$1=255` 后正常推进到下一指纹项。

应在发布连接前完成整个身份查询，处理完整行及终止成功/失败应答，覆盖MAC行与终止行合包、分包、部分行等边界。本轮已证明参数指纹误拒；没有宣称通过最终绘图闸门、绕过Grbl授权或发生越权运动。

影响要求设备已有工厂配对NVS；未配置工厂身份的旧机走banner兼容分支，不能用旧机1.0.37冒烟结果代替配对连接验收。

## PR10-03 / P2：OTA二次状态查询没有复核当前版本

位置：Portal `draw_portal/firmware_ota.py` 的 `_start_upgrade_claimed`（第283–306行）。

首次查询生成升级计划后，发送前第二次状态只检查任务忙闲和板型，没有再比较 `firmware_version`。本地真实 `start_upgrade` 与SQLite会话复现：第一次设备版本为1.0.36，目录推荐1.0.37；第二次设备已是1.0.38，仍发送1.0.37并返回 `started`、会话进入 `upgrading`。

S3 `hutuji_ota.cc` 的工具入口只要求请求版本非空，没有以设备当前版本做严格更新比较，因此设备其他升级入口在这两次查询之间完成更新时，没有设备端兜底阻止该降级请求。建议发送前使用第二次查询的版本重新计算，版本缺失/无法解析时拒绝；同版或更高版不下发。复现截获下发参数，没有安装旧固件或在实机实施降级。

## PR10-04 / P2：HTTP配网静默修改凭据尾空格

位置：量产 `Grbl_Esp32/src/WebUI/WebServer.cpp` 的 `_handle_web_command`（第467行）。

S3 `ValidateHomeCredentials` 接受SSID或密码尾空格，命令构造与URL编码也保留该字节，但Grbl对整条HTTP命令执行 `cmd.trim()`，随后才取ESP设置值。真实handler对照显示：`[ESP100]Home Wifi` 保真；`[ESP100]Home Wifi ` 和 `[ESP101]password ` 都失去最后一个空格，仍走成功响应。合法路由器凭据被改写，导致配网无法连接。

建议ESP设置值逐字节保真，仅对确需规范化的命令键或G-code做处理。回归要包含S3校验、URL编码、真实HTTP handler与setter的组合，不能只验证两端各自的字符串校验函数。探针的HTTP分发及NVS写入为替身，没有真实改写网络设置。

另观察到 `system_execute_line` 用最后一个右方括号定位ESP命令边界，会把合法SSID `Home]Wifi` 拆成错误命令键；本轮只保留解析探针，未单列为第三个Grbl缺陷或声称已验证完整配网失败。

## PR10-05 / P2：状态与批次导出漏检放行报告附件

位置：产线 `scripts/factory/store.py` 的 `status`（第146–153行）；`scripts/factory/__main__.py` 的批次导出复用该状态。

`status` 只遍历 `CHECKS` 内的检查事件并核对证据及附件，放行事件 `release` 不在其中。放行时归档的许可、电气、无线三份报告因此没有进入动态完整性检查。真实临时台账正常对照和报告附件被改写后，都得到 `released=1, missing=[]`；批次导出也会显示无缺项。

应保留“曾签核放行”的历史事实，同时检查当前代次放行附件的存在、大小和摘要，在状态与批次输出中报告完整性异常。`archive` 已正确拒绝本轮损坏报告，因此此项是状态展示漏检，不能描述为损坏报告能通过归档。

## 复现与范围

- hub：`tmp/trirepo-review-20261010/reproduce_hub.py`；结果 `results/local-only/2026-10-10-trirepo-review/hub-reproduction.json`。使用真实目录选择、升级会话及产线台账，仅替换设备网络边界。
- Grbl与S3配网校验：`tmp/trirepo-review-20261010/grbl_credential_probe.py`；结果同证据目录下 `grbl-credential-probe.json`，展开源码 `grbl_credential_probe.cpp`。MinGW C++17编译与运行均返回0。
- 配对握手：`tmp/trirepo-review-20261010/s3_factory_handshake_repro.py`；结果同证据目录下 `s3-factory-handshake-repro.txt`。真实握手、行解析、授权与指纹状态机的合包/分包对照，编译及运行均返回0。只替换socket/RTOS/日志等宿主边界，不执行真实运动。

Python入口使用本仓 `mcp-server/.venv/Scripts/python.exe`；宿主编译器为现有 `D:/zhugu-home/mingw64/mingw64/bin/g++.exe`。本轮没有修改产品源码，因此没有重新运行全套服务测试或固件SDK构建。宿主及网络注入不代替工厂配对实机、纸面、老化或完整手机矩阵验收。

本轮只新增本审查记录和工作树证据登记；上述缺陷尚未修复，不改变readiness中的既有发布与实机结论。
