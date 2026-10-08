# 2026-10-08 奎享参照的运行时续审

用户：「grbl已上线，继续排查bug，参考奎享逆向文件」。沿用本会话修复授权，先复现再更改。基线：hub `e6b8ed7`、量产Grbl `d723f4c`、S3 `3b8a116`（已部署1.0.32），三仓受跟踪文件起点无改动。普通Grbl既有`.gitignore`保留。

## 参照与范围

奎享只作互操作参照：`D:/Users/kxnx`提交`65bdcac`，固定`kx/tools/dump_agent/decompiled_full/`的A命名体系，读取`com/kenjoy/kenjoycnc/machine/f/b.java`、`machine/f/f.java`和`machine/grbl/a.java`。前者为FIFO命令/应答队列，中者为暂停/停止编排，后者明确Stepper用Z轴G1。2026-10-08本地读取；仅代码事实，不声称在奎享运行验证；不复制厂商实现、不修改授权或换纸机构。

已经排除：奎享stop把抬笔/归位追加队尾，不等于实时急停；重连只救最后在途行的缺陷不移植。量产Grbl现有Telnet锁及网络重连只读核对，保持8行/512B窗口、文件F值和机械包线。

## 已确认并修复的缺陷

| 编号 | 级别 | 源码路径与后果 | 修复依据 |
|---|---|---|---|
| K08-01 | P1 | `HomeAfterAbort`连续两次SendLine，逐行模式第二次清掉首个ok/error；可能误超时或坐标声明失败后仍归位 | §4.1停止收尾；逐行命令逐个确认，固定连接/横幅后才能发归位 |
| K08-02 | P2 | `ConfirmInFlightDoneByStatus`只查XY，纯Z行无条件放行，笔控未到位也能释放窗口 | §3新鲜有限坐标与目标一致；Stepper的Z也是运动目标 |
| K08-03 | P1 | 状态兜底成功后`DrainResponses`丢弃查询期间到达的error，错误行被当作完成 | §3/§7任何窗口error先受控停机，不能作为迟到ok清理 |
| K08-04 | P1 | 无法确认到位的应答超时仅return false，未走已有hold/reset收敛，软件可先报error并释放busy | §3物理收敛先于终态；复用既有错误停止事务，连接变更仍走断连分流 |
| K08-05 | P1 | `StreamToGrbl`正常成功出口未调用`MarkQuiesced`，RAII仍发布Failed；页尾停止等不到已排空的窗口，可能阻塞归位/reset | §4.1既有静止性闸；正常成功才发布Quiesced，异常早退仍Failed |

实现清单限S3 `hutuji_job.cc`与必要声明/回归、本仓契约和台账；不改Grbl运动、授权、参数、GPIO、换纸，不改逆向源码。当前无本批硬件注入或部署证据。

## 回归与实现

K08-01将坐标声明和归位改为两次独立的“同会话发送→本行应答确认”，首行拒绝/超时即停止，不再发归位；失败只关闭原连接，等待期间不持有stream锁。原有Hold坐标、`G92 X/Y Z0`、`G1G90 X0Y0F8000`、fresh Idle和nopaper末尾`$MD`保持。

K08-02倒序分别寻找最后X/Y/Z目标，纯Z与XY后的Z均核对0.05mm既定容差。K08-03状态查询结束后逐条检查积压响应，迟到error/Deferred及超出在途数量的ok均先停止；只有合法迟到ok可被结清。K08-04不能确认完成的超时复用已有hold→Quiesced→受控reset，不先发布error/busy=false；查询期间连接变化交回原断连处理，不操作新连接。K08-05只在正常流循环完成时增加`MarkQuiesced`，异常早退仍由RAII发布Failed。

新增两份host测试直接编译真实归位函数、Pipe逐行发送器、XYZ确认、超时分支、WindowGuard及FinishStream；不以改写的算法副本作验证。旧实现归位9例中8败、兜底18例中11败，正常结束另1例败；修复后针对性29例通过。S3全套host **277 tests OK**（63.742s）。量产Freenove/ESP-IDF v6.0.1构建、链接与分区校验通过，候选app 3080192B、SHA-256 `a9c1674cb404f62afded3b55c06ec86a28c3dcb5bf0f61245478c000ecfab81d`；clang-format修改段与git diff --check通过。枢纽hub门禁12层PASS，MCP 1611 passed（164.61s）、e2e PASS；268处指纹与255处外仓行号核对通过。

完整SDK编译曾发现新增调用使用了Pipe的私有检查函数，已改用公开只读状态字段，并在新测试替身保持该方法私有以覆盖访问边界。既有归位源码测试同步发送辅助入口名称，仍钉死G92先于固定G1归位；不删除机械/顺序断言。

## 来源与运行态边界

参考入口：[奎享基准树](https://github.com/zhuguang-ZFG/kxnx/tree/65bdcac/kx/tools/dump_agent/decompiled_full/com/kenjoy/kenjoycnc/machine)，本地`git ls-files`确认三文件受跟踪。本轮只以其协议/排队行为核对自有实现；不以反编译正确性覆盖本仓锁定决策。

本地原文件SHA-256：`f/b.java`=`05e7cab6087b7947fe63be3e14b4a3cfb638f78dc7a2369732e120dcf135d7c8`；`f/f.java`=`bec770388e79f237b699a9f1dc46e4aba13a50ad1a731c5a751ad530b43d3ff9`；`grbl/a.java`=`c21ad76249faabf0bc85c626eda7cc2b8adb48b8a6fac3f01e0c7600d4a63250`。范围为A命名体系的Stepper、主机行流及stop/reset接口；未验证奎享运行态，也不照搬其pollLast重连或软停止设计。

只读设备探针确认现役仍为1.0.32、MAC `b8:1f:3f:c4:58:2c`、connected/ready/authorized/grbl_settings_ok全true、nopaper/Idle；这是前批上线状态，不是本批补丁的板上验证。新补丁没有发往实机，未制造Grbl错误或运动。证据目录`results/local-only/2026-10-08-kdraw-review/`（本机、被忽略），含RED/GREEN、构建、源差异和参考文件哈希；临时脚本在`tmp/kdraw-review-20261008/`。

本地候选仍沿用1.0.32构建版本号，归档名`xiaozhi-unpublished-review.bin`；不能覆盖线上同号镜像，后续发布须新版本号重新构建。参考仓读到`kx/windows_hutuji/README.md`有修改，本轮未触碰；三份已引用Java源没有本轮改动。

## 1.0.33发布续作

用户追加授权「提交、部署」。提交上述S3修复与本仓契约/台账，S3升版1.0.33重新构建；先不可变候选上传和公网完整哈希校验，再核对固定量产MAC/板型/空闲状态后单次OTA，版本启动确认后切latest/catalog。云端服务代码与量产Grbl代码无本批变化。发布结果及回滚位置待执行后回填。

**发布完成（2026-10-08，supersede 本文前述未提交/未部署状态）**：

- S3提交`f1fffcec799ff8204ee1cf06c165db48d9633588`与hub审查记录`4228a9a45c72a3ce25d33720c0be5914e3b029af`已推送各自原分支。S3六文件包括升版1.0.33及五个修复/测试文件；Grbl和云端服务代码没有本批修改。
- 从固定提交构建量产Freenove/ESP32-S3/ESP-IDF v6.0.1，`esptool image-info`确认App version=1.0.33、校验和及验证hash有效。bin为3080192B，SHA-256 `a9979eb204504c910192e6cd53bfe1303281b6db6649551af4efc869a384022a`，ELF SHA `72efaa06e94fd23ef7bc1eebeadfcf78f133da9f0efe45b868f211780dafc816`。
- 候选目录为主节点`/var/hutuji/firmware/s3/freenove-esp32s3-display-2.8-lcd/hutuji.1.0.33/`；公网完整下载200且大小/SHA一致后，向已核对MAC `b8:1f:3f:c4:58:2c`、同板型且Idle的设备发送一次OTA。首应答与第一次轮询为HTTP 504，未重复下发；15:04:52回包确认1.0.33、idle、connected/ready/authorized/grbl_settings_ok全true、nopaper/Idle。
- 启动确认后发布latest/catalog，公网两份元数据均验证为1.0.33与同SHA。旧1.0.32镜像保留，推荐指针备份`/root/hutuji-bak-20261008-ota-1.0.33/`；回退时使用备份指针和保留的旧版本镜像，不能覆盖已有同号bin。
- 云端MCP/portal/bridge仍active；本批没有重启这些服务或发布新云端代码。现役云端服务保持先前已验版本。本次设备验收为OTA版本启动和只读Grbl链路状态，未跑纸面绘图或故障注入。

发布证据目录`results/local-only/2026-10-08-kdraw-release/`：SDK构建日志、image-info、公网bin、原/新catalog、单次OTA日志、设备状态时间线、确认回包与`release-summary.json`。审查期1.0.32候选证据独立保留，不作为本次发布镜像。
