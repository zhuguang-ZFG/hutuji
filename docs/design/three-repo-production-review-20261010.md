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

## 五项修复批次（2026-10-10）

用户明确要求“5项问题……修复啊”并要求继续；以下更新覆盖上文“未修复”的历史状态。本批仅修复对应输入/应答/证据处理，不改运动、换纸、引脚或设备OTA策略。

| 问题 | 修复及行为验证 |
|---|---|
| PR10-01 | 串口工具开端口前拒绝非ASCII和实时字符`? ! ~`，提示HTTP配网；5个错误输入与1个ASCII保真用例。保留Grbl实时控制语义，中文走既有HTTP路径。 |
| PR10-02 | S3逐行处理配对$I，匹配MAC后继续等待并消费本次完整ok；错误/报警/重启/缺失终止/短写/错MAC拒绝。单行512B、整次4KiB；配对后断连不误判成连接槽忙。16项覆盖合包、分包、逐字节、较长正常报告与正式授权/参数指纹推进。 |
| PR10-03 | Portal用第二次状态重新比较当前版本，版本坏回check_failed，同版/更高版回up_to_date；不发送、不挂起会话。7个真实目录/SQLite业务用例。原有两个并发测试补齐有效版本桩，保留并发断言。 |
| PR10-04 | Grbl HTTP先限总字节预算，再线性清命令前缀；ESP参数保留首尾空格，G-code仍trim。6项从S3校验/URL编码、HTTP handler、真实ESP解析到StringSetting写入验证字节保真；NVS与网络边界使用替身。 |
| PR10-05 | 当前代次已签核台增加release三报告附件登记、存在、大小、SHA检查，异常进入missing；released历史事实不改。4项覆盖等长篡改、截短、删除文件/登记，包含档案迁移和真实batch CSV导出、返修代次隔离。 |

新增回归共39项。旧实现先验红：串口/产线9失败、Portal6失败、两固件13失败；修复后的产线全套62、Portal560、S3宿主344、Grbl宿主61通过。原始命令、退出码和脱敏日志保存在`results/local-only/2026-10-10-pr10-fixes/`，临时暂存/应用/验证脚本在`tmp/pr10-fixes/`。外仓仅按冻结SHA应用四个文件；宿主测试抽取真实实现，不以复制算法替代产品代码。

串口中文修复是主动拒绝不支持的输入，不能称为串口已支持UTF-8。原审查观察到的`Home]Wifi`右方括号解析问题已由下方补修覆盖。现役设备尚无工厂配对NVS，软件回归不能替代已配对双板实机；中文手机矩阵、纸面和老化结论沿用readiness。

## 右括号 SSID 补修（2026-10-10）

针对上文保留的 `Home]Wifi` 观察，量产 Grbl `ProcessSettings.cpp` 已将 `[ESPxxx]` 前缀分隔从最后一个 `]` 改为第一个 `]`；SSID/密码其余字节保持原样。新增真实 HTTP handler、命令解析器、StringSetting 和配网校验组合回归 `ssid_right_bracket`，连同既有配网边界共 19 项通过。fz standard 的协议、宿主、硬件模拟、构建均通过；Wokwi 受月度 CI quota 限制未能启动，QEMU 在刷新 release 镜像后报告 `firmware_stale_vs_source=false`、协议响应通过，首轮 Arduino 多核 Guru 仍按既有仿真豁免登记。该修复未刷机、未部署，不改变运动、换纸、引脚或 OTA 策略。

## 发布与实机复核（2026-10-10，覆盖上文未部署状态）

用户追加“刷机部署验证，提交推送”，后续多次“继续”沿用该授权。发布提交为 hub `ca545620924f1fd40d8937da800b47e2556eb6d0`、S3 `301eb6570354bb11960ed4f82e2016b621ea2926`、量产 Grbl `b280e52595e724302ef154f8ff177af153a5fbc6`；三仓远端分支均核对一致。收口文档另行提交，不改变上述发布载荷。

S3 量产 Freenove 独立 `build-1038` 构建完成，应用分区剩余25%。正式1.0.38镜像3084848 B，SHA-256 `3b3b91bc9708227c748c7b5f4ec2c697b58b9db82138eadc9b2a276246766a0a`；与旧1.0.37同号修复候选区分，后者继续NOT_FOR_RELEASE。固定MAC `b8:1f:3f:c4:58:2c` 单次OTA返回504，未重发；状态轮询确认1.0.38，后续Grbl升级和SSID回归后仍为idle、connected/ready/authorized/grbl_settings_ok均true、nopaper、Grbl Idle/XYZ原点。公网完整下载和catalog/latest均与该镜像相同。

量产Grbl MAC `4c:c3:82:cc:8a:9c` 在COM7通过ASCII只读查询核身份/48项参数/Idle原点，DTR/RTS保持false。单次Web OTA上传 `b280e525` 镜像1847344 B，SHA-256 `a4d3fb6a4ae2f8378e7ee425c64630e3086ab911bc38747dfc4c3e4d3a87a0f1`，HTTP回status=3并捕获重启。升级后48项参数逐项相同，速度6000/6000/1200、加速度3000/3000/3000、$1=255。

实机HTTP参数回归使用两条临时SSID：`PR10]Wifi `和` 产测网络] `。通过真实HTTP setter写入，串口仅发ASCII `[ESP100]` 查询，逐字节读回均相等；finally恢复原SSID并核对，未改密码或重启网络。当前实现的HTTP设置查询正文只回ok，不能将它当作值读回；此前探针匹配失败发生在写入前，最终改用独立串口回读。4097 B总预算和256 B ESP行两项均回413。回归后48项设置不变、Idle原点、S3连接正常。此证据证明设备setter保真，不证明能连接真实中文路由器或手机配网全链；密码尾空格仍仅宿主回归。`factory_paired=false`，本机启动仍走未配对兼容分支，不能代替PR10-02配对实机验收。

Portal主备两文件由hub提交LF blob部署，备份号主`6b766951316b4e67889215665dee2f29`、备`2a260b216d134ddd8d09b14f1133da2c`，deploy/带包verify通过。独立复核线上文件SHA/LF，并在两机各自生产Python环境使用临时SQLite和设备网络替身运行7项真实start_upgrade：较旧版允许、同版/新版拒绝、空/缺失/非法/布尔版本拒绝，拒绝后无下发且占位释放。它是线上代码业务冒烟，不向设备实际降级。主MCP/Portal/bridge active，备MCP/Portal active且bridge inactive，公网Portal健康200。

本批旧通用smoke脚本仍引用`df8ba2f`文件快照，两次断言失败原日志保留，不能作为通过证据；最终使用本次提交和真实systemd单元名/各机Python路径的`*-release-smoke.json`。preflight仍为主25/34、备26/34，既有nginx布局、drop-in与热备角色差异不写作全绿。

软件验证：产线62/Portal560沿用与冻结源码逐文件SHA一致的完整回归；当前S3344宿主重新通过，量产Grbl62宿主及fz standard重新通过，fz报告绑定b280e525、dirty=false。full13层/MCP1643/e2e沿用18:06已通过且hub产品源码一致的报告；此前Redis环境失败和fz锁权限失败保留，未跳过测试。文档收口另跑docs门禁。Wokwi月度quota未验，QEMU豁免口径保持既有规则。

回退材料：S3旧1.0.37完整镜像重新下载核SHA `34bf5478…18d51`，对应catalog/latest独立备份在`/root/hutuji-bak-20261010-pr10-1.0.38/`。旧脚本复用的`/root/hutuji-bak-20261010-ota-1.0.37/`保留原内容，不作为本批回退指针。Grbl保留上次明确落板的2cd03f2镜像，SHA `20cdd471…036dab`。汇总、原始回执、镜像、测试日志索引与摘要在`results/local-only/2026-10-10-production/release-1038/release-verification.json`；暂存脚本同名release-1038目录位于`tmp/production-20261010/`，不入库。

本批未发运动，没有新增纸面/老化结论。工厂配对双板、密码尾空格实机、真实中文路由器/手机矩阵、首件尺寸和4小时/30次老化、交付主体及法规签核继续缺证，实体放行仍Hold。

## 量产控制接口续验（2026-10-10 21:21）

用户补充“继续已接量产板”，随后指出“你设置，jwt部署在阿里云啊”。先前只检查本机过期控制台JWT便要求用户刷新，是探测来源错误；应先使用阿里云现役凭据。此次执行阿里云既有自动登录/refresh链路，第1轮登录成功、主备同步及相关服务重启返回成功，随后独立使用`/etc/hutuji/mcp.env`控制台JWT访问agents得200。设备控制直接使用`/var/lib/hutuji/device_messaging.token`（600），主MCP/Portal/bridge均active；不需要在本机设置或持久保存JWT。

实机仅为现役量产Freenove COM15/MAC `b8:1f:3f:c4:58:2c`，S3状态确认为1.0.38、nopaper、连接/就绪/授权/指纹检查为true，Grbl为COM7/MAC `4c:c3:82:cc:8a:9c`。没有将旧Waveshare/paper_active默认待办用于量产板。使用与先前首件相同的402B G-code（SHA `821d0e9bab7c36f89e240b5fdcd347b5dc0933f76e76a1725f349bcfee10222d`，XY测试轨迹10–30mm），在阿里云按实时纸张快照重新发布；下载正文SHA与`X-Hutuji-Paper`核对后才推送。未更改机械参数、NVS、身份或固件。

| 实机用例 | 实际证据 |
|---|---|
| 待确认、作品隔离与取消 | 新产物进入awaiting_confirmation；expected_url不匹配返回“预览已变化，请重新核对作品”，原预览仍待确认；取消后aborted/Idle/原点。 |
| 确认、暂停与继续 | 正确成对expected_url/expected_preview_url确认回started，streaming/Run后pause回ok；2秒后仍paused且Grbl Hold，resume后完成并归位done。 |
| 重画 | repeat_available=true时repeat回started，缓存重画经过Run，最终done/Idle/原点。 |
| 试笔 | pen_test回started，观察到Z约4.95mm后回Z0、done/Idle；未以该用例宣称自动释放。 |
| 运动中重复停止 | 新预览确认后观察到Run且XY约9.75mm，第一次abort回ok，第二次回“停止处理中”；排空已提交运动后归位，最后才发布aborted。不是瞬时切断运动测试。 |

正常完成、重画和运动中停止的串口均记录`$MD（归位后释放）`、终止ok及“量产机归位后电机释放已确认”，最后Grbl Idle/XYZ原点。status.motor_en在Run期间仍显示off，属于旧遥测，不能作为实时电机状态证据。结束时通过产线Grbl只读snapshot核身份、48项参数与发布前快照逐项相同：6000/6000/1200、3000/3000/3000、$1=255。

前序失败不计通过：旧13:35测试件未重新发布便推送，previewing后error、confirm被拒，未完成暂停验证；初次停止只观察streaming/Idle；之后两次停止脚本因repeat_available=false在下发前退出（不是两次固件停止失败），改为新预览→确认→实际Run才完成最终用例。20:48旧bringup采集器打开COM7触发了Grbl复位，S3随后重新握手、发M5及G53零位移授权探测，不能描述为全程没有控制命令；默认设备号S3-LC-01也不能作量产身份真值。后续五组串口采集均在开口前设置DTR/RTS=false，未使用旧采集器。早期shell/curl探针引号解析失败，改为Python经SSH标准输入传入后才获得有效回执。

完整接口事件、串口、设置、凭据探活和SHA索引在`results/local-only/2026-10-10-production/control-hil-1038/verification.json`；暂存脚本位于`tmp/production-20261010/control-hil-1038/`。本轮只证明上述控制接口路径，不能替代实际触屏点击、纸面测量、4小时/30次老化或工厂配对实机；factory_paired仍false，实体放行Hold。同期深审记录中的待确认暂停/继续缺陷未在本轮修复，streaming阶段暂停通过不覆盖它。
