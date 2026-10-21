# 三仓续审修复与技术发布（2026-10-21）

用户明确选择先修复 R20F-01 至03，再连同CR20七项执行部署、刷机、验证、小程序上传与Git推送。范围由 agent-edit-allowlist.json 的 r20f_release_authorized 登记。

## 本次修复

1. 归档召回：主动删除与台账作废原子提交；召回在数据库写锁内复核台账身份和删除标记，原子写文件并按需重建行。自动清理的恢复资格保留。
2. 会话上报：批次只含连续同会话事件，其余留队；队列预算与失败即丢策略保持。
3. 量产查询：Cycle/Hold任一状态均拒绝受守卫限制的查询，Idle允许。只改Settings.cpp谓词。

## 已验证与执行记录

- 旧码归档5项失败；新码最初29项（新5+既有24）通过，追加真实双线程交错后新6项通过。
- 真实S3 PopBatch与GrblCommand::action宿主回归分别旧码失败、新码通过。
- S3全宿主369项、量产Grbl71项、小程序325项通过；量产release构建成功。
- Portal全套600项通过后追加第6项并发回归，新增6项独立运行通过。枢纽full13层通过（MCP1691、e2e PASS）。Freenove/LICHUANG_DEV_S3 1.0.41真构建成功，版本及板型配置已核对；镜像SHA见本机verification.json。
- fz standard本轮overall=fail，仅Wokwi ready_timeout/CLI wall timeout失败；产品宿主、协议和硬件层通过。本轮QEMU未列失败，不能复述历史QEMU失败为本次结果。已读observe/triage；sim_rerun提示Nothing to rerun、exit1，无失败协议/硬件样例可复跑。
- 固件提交S3 a8f13b72、量产Grbl bc2cf670已推送。Hub CI引用同步到这两个精确提交，未减测试矩阵。
- 发布目标仅量产nopaper机：S3 MAC b8:1f:3f:c4:58:2c / Freenove，Grbl MAC 4c:c3:82:cc:8a:9c。刷前必须当前身份与48项参数快照；S3新版本1.0.41，不发布旧号验证镜像。
- 当前S3只读在线为1.0.40、Idle、connected/ready/authorized/grbl_settings_ok均true，factory_paired=false。用户已回复USB已接好并准备测试纸。

## 边界

尚未完成的部署、刷机、上传、推送与实机结果不能从本地测试推断。fz Wokwi/QEMU启动层历史失败需以本次回执记录；不改固件行为或门禁求绿。纸面尺量、4小时/30次老化、工厂配对及交付签核不由本次控制回归替代，实体放行继续Hold。

## 2026-10-21 技术发布完成

**远端CI最终复核补充**：Hub产品eb25bb2与文档09ffa4f的Tests、量产Grbl bc2cf670的Hutuji Mass Production、S3 a8f13b72的Hutuji S3（3项）与Build Boards完整矩阵（175项）均completed/success。精确运行链接、提交和完整作业列表在本机`results/local-only/2026-10-21-release/ci-final.json`。本节末尾的运行/排队描述为发布当时快照，现由本补充覆盖；本地fz及实体Hold边界不变。

产品提交及推送：Hub `eb25bb2`、S3 `a8f13b72`、量产Grbl `bc2cf670`。服务器从Hub提交的精确LF blob投放五文件（doc_ingest、app、voice_agent、telegram_archive、store），主备各自deploy和带包verify通过、逐文件SHA相符；回退号阿里 `34e0e15aae284220910c09a54f9bbb43`、京东 `c4d3393987724a3f9bbc34da15a35d1d`。生产venv使用临时SQLite/固定字节验证删图与恢复交错、允许自动trim后恢复、UTF-8完整文本识别，三项双机PASS，未调用归档或付费生图服务。preflight阿里25/34、京东26/34，已登记unit/drop-in/nginx与热备差异保留。

量产Grbl经COM3核MAC/48项/原点后HTTP单次OTA，响应status=3、观察到重启，刷后及控制回归后48项与刷前逐项相同。镜像1848240B、SHA `7e5144754657a233150a1d556f2329f025f83243fc90e0dab65e990f8fe728eb`；回退539e3f9b镜像SHA `f21b07d9432ead0220756294849c6c4ba773464752b8aac6377c2cc4f6fe235e`。两个静默HTTP只读查询 `[ESP400]`/`[ESP110]` 均200/ok，未重启。

Freenove S3单次OTA从1.0.40升级到 **1.0.41**，首次请求504后未重发；旧文件消息凭据404，服务器重铸并核身份后恢复，随后同步热备，两节点只读探活均确认目标1.0.41、连接/就绪/授权/参数检查为true。Freenove镜像3088320B、SHA `ab5ca61a2b07b9cb3fd21a74d2ef8111c01ed503baaf35db4d13ed2324bf7d6d`；立创3126720B、SHA `3263b9a35d949a5af792262b3bed1602a4978cf22370160ebba53fc162e1bf29`，仅构建未刷该板。两节点Freenove推荐指针已promote，公网完整镜像/catalog/latest一致；推荐指针回退备份均 `/root/hutuji-bak-20261021-r20f-1.0.41`。

控制回归：预览取消、预览期pause/resume拒绝、错作品confirm拒绝、正常小图、暂停恢复、重画、实际Run中连续两次停止（第二次“停止处理中”）、试笔、同版/降级及坏OTA参数拒绝均通过。normal/pause/repeat/stop的S3串口有 `$MD`/释放确认，最终Grbl Idle/XYZ原点。Run期间串口 `$$` 得error:8，覆盖忙态查询拒绝；**Hold附加守卫HIL未覆盖**：命令在Hold入队、resume后的Idle才执行，附加探针断言失败保留，不写成Hold守卫实机PASS；实际action的Cycle/Hold拒绝由宿主回归证明，暂停恢复控制本身有独立成功回执。新会话混批与激活析构为宿主证据，未模拟真实语音换会话/重复激活。

小程序 **0.3.27 已上传**，295665B、工具明确“√ upload”。首次code10登录过期，用户重新扫码；第二次IDE端口未就绪，islogin=true后重试成功。全部原始回执保留，未提交微信平台审核。

本机总证据 `results/local-only/2026-10-21-release/verification.json`，包含源码提交、三镜像摘要、主备回退号、OTA/参数与控制回执；`ci-current.json` 独立保存远端作业状态。Hub产品CI与S3交付板CI已成功，量产Grbl和S3完整板型矩阵仍运行/排队，尚不宣称本批全部CI通过。fz本轮只有Wokwi启动超时失败，QEMU实验启动PASS，不能用CI或控制回归覆盖实体Hold。factory_paired=false，纸面尺量、4小时/30次老化、真实SN/操作员/照片和交付签核仍缺证。

## 2026-10-21 普通Grbl/S3/Hub续审（本地候选，刷机阻塞）

本节是后续独立批次：用户指定普通 `Grbl_Esp32`、S3及Hub，后追加“刷机部署验证”；不把上面的量产仓发布、旧S3板或既有CI回执算入本轮。基线普通Grbl `7398a264`、S3 `a8f13b72`、Hub `09ffa4f`；没有本轮提交或推送。预存17份脏文件在文档同步前逐字节保留，S3版本仍沿用既有候选1.0.43，不发布同号验证件。

### 源码与回归

- 普通Grbl `WebSettings.cpp` 的 `WebCommand::action` 在空输出时创建现有丢弃流，修补静默HTTP帮助格式化死循环与设置查询空指针；真实函数旧码8项中3失败（超时/0xc0000005），修后全宿主22项通过，release真实构建通过。
- S3 `hutuji_job.cc` 的 `RequestAbort` 将取消发布、worker占位和创建失败回滚置于同一段stream锁，防止Run先发布aborted/释放busy而未启动归位收尾。真实入口、worker创建和Run尾段的确定性交错两项旧码失败；修后11专项、373全宿主通过。预览/下载/校验/换纸/试笔排除与重复停止复用保留，不改机械命令序列。
- **普通Grbl两项追加修补落地（仅源码与宿主验证）**：连接A半行`$System/`可与替换连接B的`IP`拼接执行；256字节超长行溢出后同一物理行尾`$I`被执行。行为回归先让旧码12项失败，最小修补后专项28/28、全量宿主43/43通过，release真实构建成功；会话代次接口按共享fz harness既有`client_begin_session`签名对齐，fz native_sim 35项通过。用户明确“同意修复”，仅允许必要的Telnet/Serial/Protocol会话与输入边界修补及行为回归，未改运动/换纸/授权/GPIO。fz standard当时为fail（Wokwi云启动层）；16:09因云配额未执行Wokwi、按门禁既有口径复跑通过（见下节），仍不得宣称修好或可发布，也不得把量产仓历史修补当成普通仓已修好。
- Hub指定身份/重试链在声明的有限审查覆盖内无新增确证问题，不代表全仓无缺陷；本轮未改后端产品源码。Portal601、小程序340、MCP1691及本地e2e通过。小程序外层runner在打印成功日志时遇GBK编码错误，Node实际退出0；两个结果分别留证。

### 构建、门禁与设备边界

Freenove与LICHUANG_DEV_S3各真实增量编译1个CXX单元（本轮job文件）并重链接，IDF6.0.1，自动起画/换纸abort测试宏均OFF。Freenove3091760B、SHA `171d30ec03457de7eb59bee7082d55f7803d9eb365a2bd40636d3a664dd2ab09`；立创3126400B、SHA `27c96e0f15b632729028dcf73189882b11b37415453a61ca3f72ffae5b98bbf5`。普通Grbl1859968B、SHA `c33f259adbdb2cdf1f0f5f575de6b9d5aeff57deaea9e4de3ed8a46f152d30c9`。两项边界补丁后普通Grbl重新真实构建为1860640B、SHA `65945a82643b955a15e8e7ec312b24d3378542d443fb6c87568a7625e1f7a562`，full镜像4194304B、SHA `bbb3ebfc56d709a362b56672b941e230564c91e070fec1d09317c34e545a679c`；中间构建1860608B/`464c2b58…`的构建与fz日志仍保留。首个S3构建wrapper因MSYS参数转换只启动cmd提示符，退出0不计构建；只有修正后的实际编译日志计入。

fz standard绑定普通仓/dirty，overall=fail，唯一失败层Wokwi ready_timeout/CLI wall timeout；串口仿真可见一次TG0WDT复位后再启动，不能据此认定板上故障或忽略失败。product_host、QEMU、协议和硬件模拟层通过；observe/triage仍要求阻止完成宣称。Hub full首次只因36处引用指纹漂移失败，测试/e2e/§9标记通过；32处唯一原文自动迁移，余4处人工核对：两处S3历史分支合并到现存换纸分支，两处Web预检/成功判据移动1行。新增验证记录另引起6处文档行号移动及1处E′行内引用指纹变化，均按原文核对修正；随后docs门禁通过，279处指纹一致，4处外仓缺席等未解析项保留。未无脑重登或修改门禁，未重跑full，不把docs通过说成full通过。两项边界修补后，fz native_units曾因共享harness桩名不同而编译失败；普通仓接口对齐既有`client_begin_session`签名后，native_sim 35项与release构建通过，fz standard复跑仍fail，唯一失败层Wokwi云启动（CLI wall timeout、ready marker `['$' for help]` 未出现；串口日志显示启动到“No spindle”，无panic/restart记录）；未改fz或门禁。Hub docs经人工核对14处旧行号与13处新引用后重登基线并通过（278处指纹全对、4处外仓未解析保留），随后hub与full均通过（pytest 1691、e2e、§9标记）；Hub通过不等于fz或实机通过。

本轮实机截至16:05仅先置DTR/RTS=false的被动串口观察，以及COM3单次实时`?`。COM3当时无应答，未发送$I/$$，无法证明身份或Idle；COM6 USB MAC匹配新板`b8:1f:3f:c3:70:bc`，但采样未捕获版本/状态。指定MAC的Portal会话凭据缺席，不回退用可能属于旧板的token。**当时运行验收BLOCKED；未复位、刷机、运动、占用Telnet或生产部署**；16:2x新S3刷机与COM3身份核对见下节。普通镜像不适用未知/nopaper硬件；新板状态与fz条件未满足，三镜像均存为NOT_FOR_RELEASE。本轮无新增Hub后端发布载荷，未上传原有小程序候选。

证据均在 `results/local-only/2026-10-21-codereview/`：`artifacts.json`绑定源码/镜像摘要与运行边界，红绿、全套、真实编译、fz/Hub原始报告和`passive-serial.json`/`grbl-readonly.json`保留；这不替代实体屏幕、停止归位、电机释放、纸面或老化验收。两项边界修补后的普通Grbl镜像另存为`NOT_FOR_RELEASE-ordinary-grbl-session-api.bin`（1860640B、SHA `65945a…`）与`NOT_FOR_RELEASE-ordinary-grbl-session-api-full.bin`（4194304B、SHA `bbb3eb…`），源码摘要见`grbl-source-sha-after-boundary.txt`；`artifacts.json`保留15:01快照未覆盖。

### 16:05 Wokwi 配额诊断与 fz standard 重跑

60秒诊断（`fz-wokwi-60s.log`、`fz-wokwi-quota-report.json`）显示 wokwi-cli 连接后直接报 `API Error: You have used up your Free plan monthly CI minute quota`，`cloud_error=quota`，串口日志0行、`boot_count=0`：本轮固件未被执行，配额不可当作启动通过或失败证据。15:47的90秒挂起与串口停在`[MSG:No spindle]`也可能由配额中途切断引起，不能据此认定固件启动缺陷或板上故障；Wokwi芯片启动仍未验证。

按 fz 门禁既有口径（启动前配额/鉴权/连接故障且串口为空 → 不阻断 host SIL），16:09重跑`standard`：`overall: pass exit=0`（`fz-standard-after-quota.log`、`fz-agent_gate_after_quota.json`），其余层通过，`wokwi_startup`为fail/`blocking=False`，`mcp_stdio`、`native_coverage`、`release_smoke`、`g0`按依赖或profile SKIP。该pass不包含芯片云启动、射频、OTA、纸路或实机结论，`NOT_FOR_RELEASE`与刷机阻塞不变。

16:05实机只读复查（`readonly-check-160529.json`）：COM3在DTR/RTS=false后发一次`?`，0字节回包；COM6被动监听2秒0字节。身份/Idle仍未证明，未发送`$I/$$`、未复位、未刷机、未占用Telnet。

### 16:2x 实机身份核对与 S3 1.0.43 刷机

COM3受控复位后确认是ESP32 Grbl：`$I`为`Paixi Writer Firmware [VER:1.3a.20260910]`、`PAIXI_WRITER_3AXIS/PX-WRITER-3A/paixi_serial_v1`，MAC `4C:C3:82:CC:8A:9C`与量产记录一致，`$$` 48项与发布记录逐项一致，状态`<Idle|MPos:0,0,0>`。该板为massprod nopaper SKU（无纸系统、步进使能GPIO25）；普通仓release是纸系统且GPIO25为纸系统DAC、步进使能GPIO4，故未刷普通镜像，也未用web OTA跨越SKU。普通Grbl两项修补仍只有宿主/构建/fz证据，无实机验证。

COM6为ESP32-S3 rev v0.2、16MB、Freenove（应用内含`freenove-esp32s3-display-2.8-lcd`），MAC `B8:1F:3F:C3:70:BC`，原`hutuji.1.0.42`。已回读原应用3145728B、SHA `0cd95e951855e77237291117ed0196bdef743f145e207e83da250b31e5c41100`作回滚；经USB `write_flash`写ota_0 `hutuji.1.0.43`（3091760B、SHA `171d30ec03457de7eb59bee7082d55f7803d9eb365a2bd40636d3a664dd2ab09`），esptool报告Hash of data verified、退出0。写后回读版本头为`hutuji.1.0.43`；启动日志显示`Ota: Current version: hutuji.1.0.43`、WiFi IP 192.168.1.10、ARP匹配`b8-1f-3f-c3-70-bc`，并通过Telnet连接192.168.1.13:23的Grbl（PAIXI 20260910），Grbl保持Idle；观察窗内无panic/重启。

边界：未执行任何运动/作业/换纸/试笔，未做Hub生产部署，未提交/推送；S3 1.0.43仍为NOT_FOR_RELEASE候选（版本串不唯一标识脏工作树源码，以镜像SHA为准），未做屏幕/UI人工验证。证据：`flash-deploy-20261021.json`、`com3-boot-capture.json`、`com3-identity.json`、`s3-flash-1.0.43.log`、`s3-after-flash-serial.json`、`s3-after-flash-serial2.json`、`rollback-freenove-1.0.42-app.bin`。


### 16:4x 量产仓（第四仓）复核：两项修补已在 HEAD，无需移植刷机

用户确认COM3即量产不换纸（PAIXI nopaper）系统并同意继续后，按追加授权复核 `D:/Users/Grbl_Esp32-massprod`（HEAD `bc2cf670`，工作树干净）。`git merge-base --is-ancestor` 确认两项修补 `e3054175`（Telnet换连会话隔离）与 `2cd03f20`（超长物理行丢弃至行尾）均已在本轮之前的量产HEAD内，属于既有提交而非本轮移植。

宿主验证：massprod全套71项通过（`massprod-host-suite2.log`）。首跑7项`test_websocket_lifecycle`失败系本机MinGW运行时缺DLL入口（退出码0xC0000139），在PATH加入`/d/zhugu-home/mingw64/mingw64/bin`后复跑全绿；属环境问题，未改源码或断言。本地产物`.pio/build/release/firmware.bin`为1848240B、SHA `7e5144754657a233150a1d556f2329f025f83243fc90e0dab65e990f8fe728eb`，与发布记录中经COM3 HTTP OTA刷入的镜像逐字节一致，说明板上运行的即含这两项修补的固件。

实机overflow复核（`com3-overflow-hil.json`）：Idle下发送255个`A`加`$I
`，得到`error:11`且未执行行尾`$I`；随后显式`$I`正常返回`[VER:1.3a.20260910:]`，`suffix_executed=false`，判定PASS。**未改massprod任何源码，也未重刷**；未执行运动/换纸/试笔。Telnet换连边界仅有宿主`test_telnet_session.py`证据——该S3长期独占Grbl唯一Telnet槽位，按计划不抢占，故未做实机换连验证，不得宣称实机通过。


### 16:5x 推送三仓与 1.0.43 指针发布（本轮收口，用户授权「刷机部署验证推送」）

- 普通Grbl推送 `fix/panel-hold-after-align-116687e`：`f632f587`（WebCommand 空输出丢弃流）、`4a654d36`（Telnet 换连会话代次隔离与超长行 overflow 丢弃）、`f9719a15`（.gitignore）。该仓 GitHub 只有转换器 workflow，无固件门禁；两项修补仍无实机证据（见上节边界）。
- S3推送 `feat/hutuji-usb-pipe`：`3e2d836`（RequestAbort 取消/占位/回滚同锁）、`03854cf`（1.0.43 版本+屏幕布局+配网 QR 回归）。
- **干净树可复现**：提交后 HEAD `03854cf` 工作树为空，重建 Freenove 镜像 3091760B、SHA `171d30ec…`，与已刷 COM6 并验证的镜像及 NOT_FOR_RELEASE 候选件逐字节一致，未重刷；1.0.43 的 NOT_FOR_RELEASE 保留标记就此解除（版本串现在唯一对应已提交源码）。
- **1.0.43 指针发布**：lima（阿里主）与 jdcloud（京东热备）先把 1.0.41 现役 `catalog.json`/`latest.json` 备份至 `/root/hutuji-bak-20261021-promote-1.0.43/`，再以 `scripts/publish_s3_firmware.py`（脚本 SHA `8981ff51…`，装至两节点 `/opt/hutuji/scripts/`）发布 `hutuji.1.0.43`，notes 注明源码提交。两节点 latest/catalog 与公网下载三向核对：公网 latest 指向 1.0.43，完整镜像 HTTP 200、3091760B、SHA `171d30ec…` 逐位一致。回执 `results/local-only/2026-10-21-codereview/s3-promote-1.0.43.json`。**未强制任何设备 OTA**，机群在下次检查时自行拉取；立创变体不发指针；Hub 后端产品文件未变、未重部署。
- Hub推送 `main`：`09a24e1`（本轮与历史文档/治理记录）、`374c650`（weapp 0.3.28 配网与绑定调用链）、`4c379ed`（onboarding/screen-polish/公众号规划设计与配网回归用例）。**0.3.28 未上传微信平台**。scratch 未入库（arena 图片、USER_MEMORY、build 目录、tmp 日志等）。
- CI：push 触发 Hub Tests（`39571927099`）与 S3 Hutuji S3（`39571466242`）；Build Boards PR 矩阵（`39571472192`）在队，完成前不宣称通过。


#### CI 结果补充（快照，Build Boards 终态见证据 JSON）

- Hub Tests 对最终提交 `87053c3`（run `39572011496`）completed success；S3 Hutuji S3 对 `03854cf`（run `39571466242`）completed success，host/Freenove/立创三作业全绿。上节运行/排队描述为当时快照，由本节覆盖。
- Build Boards PR 矩阵（run `39571472192`）运行中（66 绿/20 跑/89 队，前次同矩阵 1h3m 绿）；终态结论补录 `results/local-only/2026-10-21-codereview/ci-final-20261021.json`，不另行修改本文。普通 Grbl 仓 push 无固件 CI。


#### 17:1x 生产 OTA 路径实机验证（1.0.42→1.0.43，PASS）

绑定表已切至新 S3 `b8:1f:3f:c3:70:bc`（16:39 绑定，machine_type=nopaper），老量产板 `b8:1f:3f:c4:58:2c` 已不在绑定表、无法云端驱动。为验证真实 OTA 链路：COM6 经 USB 刷回 1.0.42 回滚件（Hash verified，NVS 未动，`com6-1.0.42-boot.log`）→ 云端探得 1.0.42/Idle 且 `update_available: true` → Portal `start_upgrade`（首次响应 504 与 1.0.41 发布同型，会话保留 upgrading，设备实际已开工）→ 串口实录设备从公网 URL 下载 3,091,760/3,091,760 字节（`com6-ota-live.log`）→ 从 **ota_1 分区**（OTA 槽位，非 USB 所写的 ota_0）以 1.0.43 启动并 `Marking firmware as valid`（`com6-ota-reboot.log`）→ `ota_status` 终态 `ok/100`，终探 1.0.43、connected/ready/authorized/grbl_settings_ok、Grbl Idle、plan up_to_date。回执 `results/local-only/2026-10-21-codereview/ota-verify-1.0.43.json`。下载字节数与 promote 镜像一致；镜像即提交 `03854cf` 干净树重建件。未做运动/作业/屏幕人工验证；老板自身升级机制未验证。
