# 三仓续审：停止失败、会话提交与页面生命周期（2026-10-21）

2026-10-21 修复发布续作：用户要求“完全修复，部署验证上传推送刷机”，五项均已开始修复，历史“未修复”段落保留为发现时快照。S3候选1.0.44、小程序0.3.29；本批授权路径登记于agent-edit-allowlist.json的n21_fixes_release_authorized。当前实现与后续验证/部署结果见文末修复发布节，不用前批证据代替本批。

用户要求“继续codereview三仓”。本轮承接当日普通Grbl/S3/Hub续审，产品源码只读；量产Grbl仅作相关分支对照。接手时四仓已跟踪文件均无未提交修改，未使用旧发布或CI结果代替当前探针。

| 仓库 | 审查基线 | 本轮覆盖 |
|---|---|---|
| Hub | `e21f5e6` | 小程序0.3.28配网/绑定页、身份查询调用链与生命周期 |
| S3 | `03854cf` | RequestAbort、Run尾段、断流归位worker、重画启动和量产预览显示相邻路径 |
| 普通Grbl | `f9719a15` | Telnet会话失效、Serial入队/实时分流、协议超长行与Web命令上下文相邻路径 |
| 量产Grbl（对照） | `bc2cf670` | 相同实时提交窗口及已修正的查询状态守卫 |

## N21-01 / P1：断流归位失败仍发布“已停止”

位置：S3 `main/boards/lichuang-dev/hutuji_job.cc` 的 `PerformAbortDrainHome`、`StartAbortDrainHomeTask` 与 `Run` 尾段。

断流归位未取得fresh Idle、发送失败、归位完成等待失败或电机释放失败时，`PerformAbortDrainHome` 关闭本次socket、写 `abort_home_done_=false`，返回false。这个返回值表示“没有移交reset worker”，并不表示归位成功；外层只清worker旗标。`Run` 的 `reset_ok` 初始为true，失败判定只看reset owner，而断流路径没有启动owner。因此这些失败最终仍写 `aborted`、释放busy，并通知“已停止”。

宿主探针编译当前真实RequestAbort、worker创建、PerformAbortDrainHome、等待及Run尾段；Pipe和时间为可控替身。成功对照得到 `homed=true / connected=true / state=aborted / notify=已停止并回原点`。四个失败注入均得到 `homed=false / connected=false / busy=false / state=aborted / notify=已停止`。其中第一种连停止前的Idle都未确认，关闭Telnet本身不会清掉Grbl planner，故不能向用户保证已经停稳。

建议让断流worker分别发布运行、成功、失败结果，Run终态消费该结果；失败保留可解释的error与未确认停止提示。不能简单用 `!abort_home_done_` 判所有取消失败，因为预览/下载取消不需要归位；也不能把“未移交reset”当作成功。需补成功、四类失败、Hold移交、重复停止和无需运动收尾的行为回归。

现有11项停止专项仍通过：其中worker替身恒设 `abort_home_done_=true`，并没有覆盖本次失败返回。另观察到Run页循环首部在取消时直接设置aborted，早于尾段等待worker；本轮未单独编译整段Run验证展示时序，记为相邻观察，不另计确证发现。

状态：**已复现，未修复**。没有板上故障注入，不声称已观察到真实机器继续运动。

## N21-02 / P2：Telnet实时命令检查后仍可跨失效会话提交

位置：普通及量产Grbl `Grbl_Esp32/src/Serial.cpp` 的 `clientCheckTask`、会话检查与会话失效函数。

普通仓在 `client_byte_is_current` 内持Serial临界区检查代次，随后解锁，再分类并执行实时命令。量产仓的 `client_session_current` 同样解锁后才调用 `execute_realtime_command`。并发Telnet写失败/换连可以在该间隔递增代次；普通字符入队有第二次同锁比较，实时路径没有。旧会话已读出的 `~` 仍可调用实时执行器；现有执行器会置cycleStart标志。

两仓分别编译真实clientCheckTask和会话函数。在代次1字节检查通过后、实时分类返回前注入 `client_begin_session`，结果当前代次为2，旧字节仍触发1次实时执行。相同旧代次普通字符在普通仓被拒、量产仓写入数为0，是正向隔离对照。探针只证明旧实时字节被提交，未执行运动或板上换连。

建议让会话检查与实时提交共享明确的线性化点，遵守既有Telnet→Serial锁序。不能把完整实时执行器直接塞进Serial自旋临界区：其中状态报告会走网络输出，存在锁序和阻塞风险；需区分命令标志提交与可能阻塞的输出。追加确定性交错回归，不能只在执行前再做一次可被打断的检查。

状态：**普通与量产两仓均已复现，未修复**。先前半行与普通入队隔离修复仍成立，本条是实时提交相邻窗口。

## N21-03 / P2：配网页隐藏后继续提交凭据、切网和认领重试

位置：Hub `draw-portal/weapp/pages/wifi/wifi.js` 的 `onHide`、`_current`、`submitCreds`、`restoreHome` 与 `claimMac`。

onHide只隐藏密码文本，没有作废展示代次、取消TCP或定时器；_current也没有页面可见性判据。用户把小程序切后台后，晚到的TCP连接回调仍可写出凭据，断流回调继续更新页面并排定手机切网；认领失败也会继续POST重试。绑定页已有独立隐藏处理，不能覆盖配网页。

真实页面/真实wifi_transport的可控wx探针：提交后先onHide，再投连接及错误回调，得到后台写入1次、2500ms后connectWifi调用1次、页面进入waiting；认领请求后隐藏并投超时，1500ms后POST数由1变2。测试只记录API调用，未连接设备或微信服务。

建议区分一般后台/离页与当前扫码或系统Wi-Fi设置的明确往返；后台暂停后续控制及重试，已发送结果保持待确认，回屏按原身份核对。protocol的同次系统设置往返保留内存密码规则继续遵守，不借生命周期修补强制清空该场景。需补隐藏前后TCP、延迟切网、认领重试与回屏恢复用例。

状态：**已复现，未修复**。现有wifi/onboarding59项仍通过，隐藏覆盖集中在绑定页与密码显隐，未覆盖这些配网页后续动作。

## N21-04 / P3：失效扫码回调留下_scanning，绑定页回屏跳过恢复

位置：Hub `draw-portal/weapp/pages/bind/bind.js` 的 onShow及onScanBind回调。

onShow在_scanning为true时立即返回，早于登录代次复核；扫码success只有_current通过后才清该标记，fail也只在_current通过时清。若扫码期间登录代次改变，返回的旧扫码回调被正确拒绝，但_scanning永久保留。后续onShow持续绕过登录恢复，当前页面保留过期_authReady，_current一直失败，需重扫或重新进入页面才能恢复。

探针：启动扫码→模拟相机hide→登录代次变化→取消扫码→onShow，得到 `scanning=true / sessionCurrent=false / newRequests=0 / authReady=true`。没有证明正常同账号扫码会失败，普通相机往返回归仍通过；该条只针对回调跨登录代次的条件分支。

建议为扫码操作单独记录所有者，完成/取消时仅结算自身扫码标记，再拒绝旧数据写入；onShow不能因失效操作标记永久跳过账号复核。回归包括success/fail跨登录变化及旧扫码收尾不清新扫码。

状态：**条件分支已复现，未修复**；未做微信真机生命周期验证。

## N21-05 / P3：普通Grbl仍有已在量产仓修正的查询守卫缺陷

位置：普通仓 `Grbl_Esp32/src/Settings.cpp` 的 `notCycleOrHold` 与 `GrblCommand::action`；六项查询注册位于ProcessSettings.cpp。

普通仓仍使用 `state==Cycle && state==Hold`，谓词恒false，受它保护的 `$$ / $+ / $L / $S / $SC / $CMD` 在Cycle/Hold也执行。量产bc2cf670已用OR修正；当日普通仓追加的Web/Telnet修复并没有包含该守卫。

编译两仓真实守卫与action：普通仓Cycle/Hold均未拒绝、查询执行3次（含Idle）；量产两忙态均拒绝、仅Idle执行1次。本条是R20F-03在普通分支的确认，非新的量产回归。未证明运动停顿或重启，不升级严重性，也不以此要求修改运动参数。

状态：**普通仓已复现、未修复；量产已修正**。

## 证据、验证与未执行范围

本机证据 `results/local-only/2026-10-21-review-next/`：weapp-probe.json、s3-drain-tail.json、grbl-probe.json、生成的C++源码/可执行探针、运行日志、source-snapshot.json及validation.json。临时入口在 `tmp/review-20261021-next/`；不含真实凭据或用户数据，未调用付费接口。

当前相关回归：小程序wifi/onboarding **59 passed**；S3 nopaper/停止专项 **11 passed**；普通Grbl Telnet/Web上下文/参数专项 **42 passed**。本轮只改审查文档与状态登记，未运行全套、固件构建、fz、CI、实机或生产验收。文档门禁首次因六处旧引用纯移动失败，按唯一原文重定位并保留首次日志后复跑 **docs overall=pass / exit=0**，278处指纹相符、4处外仓未解析保留；基线差异仅移动键及记录HEAD，没有批量追认改动内容。没有修改产品、正式测试、授权清单或门禁，也未提交、推送、部署、上传或刷机。原发布状态与实体Hold不被这些宿主结果覆盖。

## 五项修复与发布（续作）

N21-01增加AbortDrainPhase结果，任务尾段消费失败并出error/未确认停止提示；页循环取消只退循环，成功终态统一在物理worker收敛之后。重复停止复用worker，失败创建回滚phase，重画复位旧结果；保持归位命令与弹簧沉降顺序。新增真实入口/worker/归位/尾段5项成功与失败回归。原宿主夹具同步新字段，Hold移交源码钉从直接return改核同一移交包装及两处调用，不删除断言。

N21-02两仓在Telnet锁内复核会话并执行回调，执行器保留在Serial层，保持Telnet→Serial锁序及递归输出；新增检查后失效、旧会话、锁内提交和非Telnet4项回归。fz初跑Telnet宿主缺Cmd类型/执行器声明而红，接口改为锁内回调后重验，没有改fz桩或门禁。N21-05普通仓查询守卫改OR，复用真实action忙态拒绝/Idle允许回归。

N21-03配网页隐藏取消TCP及定时器、停止迟到回包续调，回屏先核原身份并由用户显式重试；N21-04扫码所有者按操作/登录代次结算，onShow先核账号再处理相机往返，旧回调不清新操作。新增7项页面交错与系统设置往返回归；既有授权恢复回归改为GET先核、显式重试POST，完整身份和密码不持久化断言保留。小程序0.3.29已由官方CLI上传300659B、明确√ upload，未提交平台审核。

本批证据在 `results/local-only/2026-10-21-n21-release/`，编排脚本在 `tmp/n21-release/`。当前S3全381、普通Grbl48、量产Grbl75宿主通过；两S3与两Grbl真实构建已完成，中途夹具编译缺字段、脚本命令路径与fz旧桩失败保留为过程记录。最终门禁、提交、部署与实机回执另行续记；实体签核不由本批替代。

### 已完成的技术发布与验证

产品提交并推送：S3 `217253d030abe701207ea63607f10803eeac579c`（1.0.44）、普通Grbl `c0ed039a34445157c80ebed6c013238f27005a92`、量产Grbl `e77c3dabb254cdb349897d88044dd65fff11c0b1`。两Grbl的fz standard最终overall=pass；Wokwi月度配额在执行前耗尽、串口为空，按原有门禁为nonblocking失败，芯片云启动未验证；QEMU/产品宿主等通过。首轮缺执行器类型的fz编译失败已由回调接口修复，未改fz。Hub full最终13层通过（MCP1691、e2e PASS），Portal601、小程序347通过；指纹35处唯一原文自动迁移、5处人工核对新路径/范围（窗口谓词2、实时分流1、收尾分支2），未无脑追认。

量产Grbl COM3/MAC `4c:c3:82:cc:8a:9c`在Idle/原点/48项机设核对后单次HTTP OTA，候选SHA `217023f3797f0a941df637e79c58cc1a1e5c75fd1bc97b8061b1361ff1d0c59d`，响应status=3、观察到重启。刷后和控制回归后48项均与刷前逐项相同。回退bc2cf670镜像SHA `7e5144754657a233150a1d556f2329f025f83243fc90e0dab65e990f8fe728eb`本机留存。普通Grbl纸系统镜像仅构建/推送，没有与之匹配的实机，本轮不刷到nopaper硬件。

阿里主与京东热备均发布Freenove **1.0.44**，镜像3091888B、SHA `20391b825e547796b36b481eec301edfab185883d5687a71c844837a98d05cb0`；各自latest/catalog及公网完整下载一致，回退目录 `/root/hutuji-bak-20261021-n21-1.0.44`。Hub本批运行代码只有小程序，无后端部署载荷；后端不作无效重启。主备preflight为25/34、26/34，保留已登记的unit/drop-in/nginx及热备差异，未宣称全项通过。

量产S3 COM6/MAC `b8:1f:3f:c3:70:bc`按生产Portal OTA由1.0.43升1.0.44；首次HTTP504后保留unknown、未重发，随后读回1.0.44、ota ok/100、Idle/原点及连接/就绪/授权/机设检查均true。预览取消、预览pause/resume与错作品确认拒绝、正常小图、实际Run中连续两次停止、暂停Hold后恢复、重画与旧版/坏参数OTA拒绝均有独立回执。停止串口按顺序记录 `$MD`、电机释放确认、断流归位完成与任务aborted。另一台量产S3 `b8:1f:3f:c4:58:2c`未绑云端且当前USB未检测，已请求接入；升级未完成不能以COM6结果代替。

小程序0.3.29已上传300659B；真实微信后台生命周期及屏幕触控未手测，当前为页面宿主回归。停止失败、实时失效窗口属于确定性宿主故障注入，未在运动实机强制断链制造危险。旧S3确切job源码对照4失败/1成功，旧Grbl Serial分流对照2失败/2成功；当前各新增回归通过，源码工作树未为对照回滚。

远端S3交付板CI [39595436177](https://github.com/zhuguang-ZFG/xiaozhi-esp32/actions/runs/39595436177)、量产Grbl [39595446183](https://github.com/zhuguang-ZFG/Grbl_Esp32/actions/runs/39595446183)已completed/success；S3 Build Boards [39595443773](https://github.com/zhuguang-ZFG/xiaozhi-esp32/actions/runs/39595443773)排队，Hub本批推送/CI待后续回执。工厂配对、出货级纸面、老化与交付签核仍Hold。

### 根因与防复发

按trellis-break-loop分析：停止失败属于跨层结果契约与测试盲区，bool表示移交而非成功、stub恒成功使问题漏检；独立phase及真实尾段故障回归已落实。实时失效属于隐含“检查即拥有”的假设，普通入队改了而实时分流未传播；锁内回调给出提交线性化点、两仓同套回归已落实。页面问题属于生命周期传播遗漏，wifi隐藏只改样式、扫码操作状态借用账号代次结算；展示闸及独立扫码所有者已落实。分支查询守卫为变更传播缺口，普通/量产分开编译action核对，不能用某一分支修复概括另一仓。

防复发合同已写protocol末节。本仓没有.trellis，知识落到现有契约与本记录；未创建虚构任务、未引入新的门禁策略。后续审查重点是跨层返回值语义、锁内提交而非锁前检查、一般后台与系统往返的显式区别、同名分支的独立验证。
