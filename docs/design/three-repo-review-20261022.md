# 三仓续审：完整行提交、试笔错误与认证后恢复（2026-10-22）

修复发布续作授权：用户要求“完全修复下载刷机部署验证推送”。四项已进入修复与发布，发现时“未修复”段落为历史快照；本批机读范围为c22_fixes_release_authorized，S3候选1.0.46、小程序0.3.31。实际完成与未完成边界见文末，不拿上批证据代替本批。

用户要求“继续三仓codereview”。本轮产品源码和正式测试只读，承接R2发布后的相邻路径；临时探针与审查文档不进入产品。未调用设备、付费接口或生产服务，未部署、刷机、上传、提交或推送。

| 仓库 | 当前审查提交 | 覆盖范围 |
|---|---|---|
| Hub | `9ae7d55` | 小程序0.3.30配网/绑定完成后的登录代次变化、重新认证与页面恢复；对应Portal绑定路由 |
| S3 | `fe7120b`，1.0.45 | 重画/确认预启动、试笔真实发送与终态、SetState错误快照和StatusJson消费 |
| 量产Grbl | `39ecd288` | Telnet断连/实时提交、Serial代次队列与Protocol完整行执行边界 |
| 普通Grbl（分支对照） | `c0ed039a` | 独立编译真实Serial及protocol_poll_client，核完整行边界是否也遗漏 |

接手四个产品仓及fz已跟踪文件均无未提交修改；既有未跟踪资产保留。当前设备版本/身份与尚未完成的R2控制回归继续以readiness为准，本轮没有重新探活，不能据源码推断设备在线或硬件效果。仓库没有`.trellis`，恢复上下文后沿用现有契约与审查文档，不创建虚构任务。

## C22-01 / P2：完整旧命令行在会话失效后仍交给执行器

位置：量产`Grbl_Esp32/src/Protocol.cpp`行197–207的Eol分支和`Serial.cpp`的client_read/client_begin_session；普通仓对应protocol_poll_client的Eol分支行179–227。

现有会话隔离在读取每个字符时检查代次，断连时清队列、发布屏障，协议线程在下一次读取时清半行。但换行已经取出并形成完整行后，Eol分支先调用protocol_execute_realtime，再调用execute_line；这段没有保留并核对该行所属代次。并发clientCheckTask的断连处理可以在这段间隔推进代次，下一次读取的屏障来不及撤销当前尚未交给执行器的行。

两仓分别编译当前真实Serial会话函数、解析器和完整行分流。在Eol的实时检查点替身中调用真实client_begin_session，量产和普通均得到`before=1 / after=2 / executed=1 / line=G1 X10 F100`。无失效对照正常执行1行；在换行读取前失效的对照执行0行。说明既有半行/队列隔离仍有效，遗漏集中于最后一次读取到完整行执行之间。这里gc_execute_line只记录提交，没有执行运动或规划器。

建议为普通命令行明确提交线性化点并保留其会话所有者：提交前已失效的行应丢弃；已完成提交的命令不受网络清理影响。不能把可能阻塞、等待规划器或产生网络输出的整段G-code执行放进Serial自旋临界区，也不能用一次可被打断的锁前复核冒充原子提交。回归应覆盖换行后/执行前失效、未失效、换行前失效、串口隔离与实际Hold恢复路径。

这不是N21-02实时字节锁内提交的再次失败，也不是R2-03断连未发布代次：探针中代次已经正确推进，受影响的是普通完整行的下游提交。**两仓均已复现，未修复**；未证明板上发生旧行运动，不提升为实机事故或取消已进入planner的要求。

## C22-02 / P2：试笔应答失败的status错误为空或沿用上一任务

位置：S3`main/boards/lichuang-dev/hutuji_job.cc`的RequestPenTest（行1446起，TakeResponse分支行1514和终态行1535）、SetState（行489）及StatusJson（行2106）。

试笔的Z5/Z0行已提交后，TakeResponse非Ok只设置局部ok=false，没有设置last_error_；如果随后fresh Idle及会话复核成功，WaitForIdleForSession也不会补错误。进入error时SetState把last_error_复制到status_error_mirror_。入口又没有清理上一任务last_error_，所以本次状态可能没有原因，也可能带另一任务的错误。

探针编译真实RequestPenTest、PreparePenOrigin、WaitForIdleForSession、Pipe会话发送及SetState，只有外部I/O/时钟/显示为替身。正常试笔为done、3条命令；仅让运动应答超时、fresh Idle保持成功，结果为`state=error / status_error_mirror=""`。预置上次`old-draw-error`后同一失败得到`state=error / status_error_mirror=old-draw-error`。StatusJson只在镜像非空时输出last_error，小程序control.js也只读取这个字段显示设备原因。因此成功返回“started”后再轮询的用户看不到本次失败原因，或看到旧原因；串口/云通知仍有固定“笔测试失败”提示，不能写成所有提示均消失。

建议在试笔领取阶段清理本次错误状态，并为运动发送/应答/会话失败设置对应本次原因，先发布原因再发布error。保持R2已修复的取消失败判据，不把已尝试运动但应答不明改为成功。回归应覆盖干净入口失败、带旧错误失败、正常完成、取消及连接变化，并验证StatusJson最终字段。

**已复现，未修复**。未在设备上强制丢运动应答；现有试笔会话与取消失败回归通过不代表这个错误字段已覆盖。

## C22-03 / P2：已完成配网重新认证后自动再连旧设备热点

位置：Hub`draw-portal/weapp/pages/wifi/wifi.js`的onShow（行31）、ensureAuthAndInit（行87起）与onStart（行198）。

bindDone把_phase置bound并清密码，同登录代次回屏正常保持完成。登录代次变化时onShow清authReady与bindState但保留_scan和_phase=bound，随后重新认证。认证完成只特殊处理waiting，其余阶段无条件启动Wi-Fi并对存在_scan调用onStart，于是没有新扫码/重试意图也会切到旧设备AP，页面回working。已联网设备的AP通常已经关闭，这条恢复路径会把用户带回热点重试。

真实页面和api/身份模块探针：claim成功bound→hide→登录代次变化→show→本次手机号复查成功，得到`phase=bound / step=working / bindState="" / connectWifi=[device-ap]`。同代次成功回屏没有新请求和切网。探针只记录wx调用，没有连接手机或设备；“登录代次变化”也可能是同账号重签，不能据此声称已发生跨账号越权。

现有“已确认绑定遇登录代次改变”回归只断言authReady=false及发出/auth/me，未完成认证回包，所以遗漏真正恢复分支。建议完成记录持有账号/设备归属；重新认证后先核当前归属并显示相应完成/待核状态，重新配网由明确的新扫码或重试动作触发。不能为了保住旧bound展示跳过认证或身份检查。

**已复现，未修复**；未发现本分支自动发送密码（成功后密码已清），不扩大为凭据泄漏。

## C22-04 / P2：绑定完成记录跨登录代次保留，并自动重发旧短码

位置：Hub`draw-portal/weapp/pages/bind/bind.js`的onShow（行26）、ensureAuth（行69）、doBind、pollStatus与_boundMac消费；后端`draw_portal/app.py`的bind_complete作为结果边界对照。

onShow遇登录代次变化只清_authReady，没有清step=bound、macSuffix和_boundMac。随后_resumeCheck因为step已经bound而置false；认证完成时只看code是否存在，有旧短码就再次POST /api/user/bind/complete，无短码则查询设备列表。pollStatus在列表没有active设备时没有清除旧bound展示。

真实页面两个入口复现：手输ABC123并成功绑定→hide→登录代次变化→show→本次认证成功，得到一个新的旧短码POST，提交期间页面仍bound；投binding_pending后转waiting并安排重试。无短码入口先从设备列表显示bound，同样回屏重新认证，再投`devices=[]`，页面仍bound且_boundMac为旧设备。两个同代次回屏正常对照不重发POST。

后端仍按当前认证用户复核归属、挑战和冲突；本轮没有绕过后端，也没有实际换绑。影响是新会话没有新提交意图却重复绑定请求、陈旧成功展示及可触发无谓重试。旧_boundMac也被解绑按钮使用，但后端目标归属检查仍存在，不能声称能解绑他人设备。

建议让完成展示和操作目标记录拥有明确账号所有者；账号/登录代次变化后先作废旧展示，再只读核当前账号的目标归属。已完成旧短码不能当成新认领意图，无码查询空结果须明确退回输入/未绑定态。保留同账号后台完成状态与现有服务端身份闸，追加完成认证后的两类分支回归。

**已复现，未修复**；未做真实账号切换或微信生命周期手测。

## 证据、已跑验证与边界

本机证据`results/local-only/2026-10-22-review/`：两仓grbl-eol-session源码/构建日志/三种结果，s3-pen-error真实SetState与四种结果，weapp-probe.json、source-snapshot.json及validation.json；临时入口`tmp/review-20261022/`。未包含凭据、真实用户资料或付费响应。

相邻正式回归：S3重画预启动/试笔取消/会话**18 passed**，量产Grbl断连/会话/实时提交/溢出**15 passed**（同次pytest合计33）；小程序wifi/onboarding**69 passed**。已有通过用例证明正常与既有修复分支保留，不覆盖本轮新增交错。普通分支额外只跑独立真实完整行探针，不声称普通全套通过。

探针完善过程的两次失败为宿主夹具问题：普通分支Error枚举缺AuthenticationFailed声明，旧错误对照错误沿用了“应为空”的断言；仅补临时夹具与对照断言，未改产品/正式测试，最终两仓及试笔全部探针exit=0。

文档门禁接手时通过；报告/状态登记后的最终结果见validation.json。本轮未跑产品全套、固件构建、fz、远端CI或硬件验收；未修复上述四项。上批部署与R2未完成控制复核保持原边界，工厂配对、纸面、老化与交付签核继续Hold。

## 四项修复与发布续作

C22-01两Grbl在EOL实时检查后以Serial短临界区原子比较代次并领取完整行，RAII结算执行者，不持Telnet/Serial锁调用执行器。领取前失效拒绝，领取后原行继续执行且回收；增加两仓各5项真实入口回归，串口隔离和正常成功保留。C22-02试笔入口清旧错误/TLS计数，运动发送/应答/会话失败设置本次错误后发布error，新增真实SetState/试笔4项回归。C22-03完成配网重新认证只读查归属，不自动切AP；C22-04绑定完成跨代次清旧展示/目标，旧短码转显式重试，无码空列表回输入，新增6项完整认证回包页面回归。

旧码在独立目录对照：量产/普通各1项失败、4项正常对照通过，S3旧码3项失败/1项成功；当前新增全部通过。没有回滚工作树。当前宿主S3 393、量产81、普通53，Portal601及小程序356通过；Freenove/立创S31.0.46、量产/普通Grbl release真构建完成。首次夹具参数/RAII缺声明与重复定义、Portal默认临时目录权限及日志GBK打印错误保留过程日志，修的是接口夹具和运行目录，未删除断言。Hub full的MCP1691/e2e/源码层通过，53处行号纯移动（50自动+3重复引用人工按逐字原文定位）已修正，最终门禁另记。

本机证据 `results/local-only/2026-10-22-c22-release/`，工具 `tmp/c22-release/`。当前USB COM7核量产GrblMAC/Idle/原点/48项，S3绑定身份c3:70:bc为Freenove1.0.45、Idle/原点及就绪/授权/机设通过；本次升级、主备发布、小程序上传、控制回归与CI尚在执行，不预记完成。fz首跑产品81与QEMU通过，硬件仿真json_feed_hold_tcp未见Hold失败、Wokwi配额为nonblocking失败，保留原报告后完整重验，不改门禁/仿真或产品运动逻辑求绿。

### 根因与防复发（trellis-break-loop）

命令提交为跨层契约/测试盲区：前轮只护字节队列和实时提交，EOL后的普通执行还没有领取点；本次把接收提交定义为短锁领取，分别核领取前后失效。试笔错误为变更传播遗漏：局部ok已修，但status原因没有传播；本次通过真实SetState测试可见结果。页面恢复为隐含假设与测试盲区：认证成功被当作可恢复旧写入意图，已有测试停在/me发起点；本次完整投认证回包并检查无POST/切网，旧完成展示独立作废。防复发合同已写protocol末节，本仓无.trellis，知识落到现有契约和本记录，不创建空架构或改测试门槛。

### 继续发布回执（2026-10-22）

外仓S3 `161174d22f7f31b5ef7bbec4df95f8a4fd65c30c`、量产Grbl `a268e05c96d7f1207ca2f4db2169d8ee17f288e4`、普通Grbl `e5d7199acb3d7f736552b27feb9235000a438a74`均已推送；Hub CI三份外仓快照改为这三个精确提交。Hub full最终通过（MCP1691/e2e及源码层），两Grbl fz standard最终overall=pass；Wokwi配额非阻塞但没有真实云启动回执，不能记该层验证通过。首次量产硬件仿真失败保留，最终完整重验通过。

量产COM7 Web `/updatefw`单次发送 `a268e05c` 镜像1848864B，SHA-256 `d9cf7aaa9d8b1c69c4ca5eaa031b26e4e10002e7d2d41f417e7536ea56443847`，应答status=3、重启及48项参数前后相同均有证；精确字节数以grbl镜像回执为准。主备Freenove1.0.46均发布并验证3092496B、SHA-256 `783e3272a2a1edbee34942033ca385797b466d9b72269add05e4a9ad2f5d9158`，catalog/latest及公网文件逐位相符；推荐备份均为`/root/hutuji-bak-20261022-c22-1.0.46`。本批后端没有产品改动，小程序0.3.31上传301395B成功。

S3本次单次OTA经当前绑定发送，受理回包HTTP504，随后只读核到目标仍1.0.45、设备ota=failed/reason=download_failed，未记升级成功。Portal本轮为uncertain（对外upgrading），按既有保护规则未用一次failed读取直接清占位。当前只有COM7，未见目标S3串口，已请求接回c3:70:bc的数据线以读日志；未重复下发OTA、未刷另一台S3。控制回归、控制后机设复核、远端CI及最终收口继续执行，实体Hold不变。

### 最终技术发布与验证（覆盖上段执行中状态）

用户接回USB后COM6再次核ROM MAC `b8:1f:3f:c3:70:bc`。被动运行日志曾见约2.9小时uptime及SRAM最大连续8KiB，作为下载失败线索保留，未认定根因。首次整片读取断流，改为64个256KiB块，保留一次块重读，最终16MB完整备份SHA-256 `31dae9b248b852e5a71355355c444879ec00b404d3828e39a85e521160380b32`；分区与构建一致。ota_0虽有1.0.46应用头，但完整候选摘要不符，证实不能据头宣称升级完成。

USB单次仅写otadata和ota_0，两个段的写入摘要及独立verify-flash均通过；启动前NVS与备份逐字节一致，SHA-256 `866e756868ba3bc99b19297d1055229f716afb795ea70608f210acef4d0bccbc`。watchdog-reset因USB重枚举退出1，日志确有重启指令，随后同MAC串口与绑定探活确认运行1.0.46、原点Idle、连接/ready/授权/机设均通过，无需再次写入。Portal按实际目标版本将原uncertain结清为ok/100，这不把首次HTTP OTA改记成功。普通换纸镜像未刷nopaper板，另一S3继续排除。

七项真实控制回归最终通过：预览期pause/resume拒绝、错误作品确认拒绝及取消；正常小图；暂停后持续Hold再恢复；重画；先见非原点Run再连续停止并归位；试笔Z5/Z0至done；同版/旧版/非法版本/坏SHA升级拒绝。暂停首轮在预览下载期间遇云504，串口随后TLS读失败；没有发送pause/confirm，脚本只收敛已领取任务，保留失败日志，新测试件完整复验通过。控制后COM7重新读取48项与升级前完全相同，最终S3为done/Grbl Idle/XYZ全0/ota idle。正常/停止收尾仍有`$MD`应答；试笔保持既有不自动释放策略。

Hub产品提交 `4ca1f0c2ce7543b1a169dcc39f9ebeeb4e1939b4`及三个外仓均已推送。Hub Tests [39728108710](https://github.com/zhuguang-ZFG/hutuji/actions/runs/39728108710)、Hutuji S3 [39722949285](https://github.com/zhuguang-ZFG/xiaozhi-esp32/actions/runs/39722949285)三作业、量产Grbl [39722952796](https://github.com/zhuguang-ZFG/Grbl_Esp32/actions/runs/39722952796)与完整Build Boards [39722953286](https://github.com/zhuguang-ZFG/xiaozhi-esp32/actions/runs/39722953286)175作业均completed/success，失败/取消清单为空，未缩减矩阵。产品源码自本批验证后无新增改动。

主备发布是同一Freenove镜像与catalog/latest验证；本批Hub后端产品没有改动，无需服务器代码重启。小程序0.3.31上传成功不等于平台审核发布。最新preflight阿里25/34、京东27/34，京东首次SSH超时后重验；京东公网HTTPS files探针本次失败，不能写与26/34历史基线一致或全绿，其余unit/drop-in/nginx/热备角色差异继续保留。真实手机账号切换/后台生命周期、强制运动应答丢失和Wokwi云启动未验证；factory_paired=false、纸面、老化及交付签核继续Hold。

本机总索引`results/local-only/2026-10-22-c22-release/verification.json`，包括红绿/宿主/构建、双fz及Hub门禁、发布SHA/上传、首次OTA与TLS失败、完整Flash备份/NVS、单次USB下载、控制与48项复核、完整CI回执。临时脚本和备份不入Git，不覆盖既有未跟踪资产。
