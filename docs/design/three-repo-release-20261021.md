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
