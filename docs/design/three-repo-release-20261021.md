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

产品提交及推送：Hub `eb25bb2`、S3 `a8f13b72`、量产Grbl `bc2cf670`。服务器从Hub提交的精确LF blob投放五文件（doc_ingest、app、voice_agent、telegram_archive、store），主备各自deploy和带包verify通过、逐文件SHA相符；回退号阿里 `34e0e15aae284220910c09a54f9bbb43`、京东 `c4d3393987724a3f9bbc34da15a35d1d`。生产venv使用临时SQLite/固定字节验证删图与恢复交错、允许自动trim后恢复、UTF-8完整文本识别，三项双机PASS，未调用归档或付费生图服务。preflight阿里25/34、京东26/34，已登记unit/drop-in/nginx与热备差异保留。

量产Grbl经COM3核MAC/48项/原点后HTTP单次OTA，响应status=3、观察到重启，刷后及控制回归后48项与刷前逐项相同。镜像1848240B、SHA `7e5144754657a233150a1d556f2329f025f83243fc90e0dab65e990f8fe728eb`；回退539e3f9b镜像SHA `f21b07d9432ead0220756294849c6c4ba773464752b8aac6377c2cc4f6fe235e`。两个静默HTTP只读查询 `[ESP400]`/`[ESP110]` 均200/ok，未重启。

Freenove S3单次OTA从1.0.40升级到 **1.0.41**，首次请求504后未重发；旧文件消息凭据404，服务器重铸并核身份后恢复，随后同步热备，两节点只读探活均确认目标1.0.41、连接/就绪/授权/参数检查为true。Freenove镜像3088320B、SHA `ab5ca61a2b07b9cb3fd21a74d2ef8111c01ed503baaf35db4d13ed2324bf7d6d`；立创3126720B、SHA `3263b9a35d949a5af792262b3bed1602a4978cf22370160ebba53fc162e1bf29`，仅构建未刷该板。两节点Freenove推荐指针已promote，公网完整镜像/catalog/latest一致；推荐指针回退备份均 `/root/hutuji-bak-20261021-r20f-1.0.41`。

控制回归：预览取消、预览期pause/resume拒绝、错作品confirm拒绝、正常小图、暂停恢复、重画、实际Run中连续两次停止（第二次“停止处理中”）、试笔、同版/降级及坏OTA参数拒绝均通过。normal/pause/repeat/stop的S3串口有 `$MD`/释放确认，最终Grbl Idle/XYZ原点。Run期间串口 `$$` 得error:8，覆盖忙态查询拒绝；**Hold附加守卫HIL未覆盖**：命令在Hold入队、resume后的Idle才执行，附加探针断言失败保留，不写成Hold守卫实机PASS；实际action的Cycle/Hold拒绝由宿主回归证明，暂停恢复控制本身有独立成功回执。新会话混批与激活析构为宿主证据，未模拟真实语音换会话/重复激活。

小程序 **0.3.27 已上传**，295665B、工具明确“√ upload”。首次code10登录过期，用户重新扫码；第二次IDE端口未就绪，islogin=true后重试成功。全部原始回执保留，未提交微信平台审核。

本机总证据 `results/local-only/2026-10-21-release/verification.json`，包含源码提交、三镜像摘要、主备回退号、OTA/参数与控制回执；`ci-current.json` 独立保存远端作业状态。Hub产品CI与S3交付板CI已成功，量产Grbl和S3完整板型矩阵仍运行/排队，尚不宣称本批全部CI通过。fz本轮只有Wokwi启动超时失败，QEMU实验启动PASS，不能用CI或控制回归覆盖实体Hold。factory_paired=false，纸面尺量、4小时/30次老化、真实SN/操作员/照片和交付签核仍缺证。
