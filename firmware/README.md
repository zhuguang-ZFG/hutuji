# firmware/：S3 哑管道固件补丁资料

2026-10-22 TLS有限等待收口：S3 升 `hutuji.1.0.48`，Disconnect 最多 3×10s，超时放弃 TLS 所有权并泄漏事件组；tracked 补丁表登记 `esp-ml307-tls-socket-lifecycle.patch`。宿主 `test_tls_lifecycle` 5/5。板上与公网状态只看 readiness；不改运动/身份/OTA 校验。详见 `../docs/design/tls-followup-20261022.md`。

2026-10-21 R2停止/试笔收尾修复：S3 fe7120b/1.0.45已主备发布并OTA至现役量产Freenove c3:70:bc，普通创建失败与预启动取消、新试笔失败终态均有宿主回归，S3全389/代表双板构建/Hub full通过。量产Grbl39ecd288同步修补断连会话清理，COM3单次HTTP OTA重启及刷后48项不变。小程序0.3.30已上传；实机预览取消与正常小图通过，后续控制验收因USB双板缺席与消息探活404等待重接，不混记全部HIL通过。详见[本批发布](../docs/design/three-repo-postrelease-review-20261021.md)及readiness；实体Hold继续。

2026-10-11 FR11六项修复准备发布：停止收尾控制隔离、旧会话实时控制、OTA板型路径与异常恢复均沿用已有本地候选。用户明确授权升版/提交推送/部署刷机，S3独立升1.0.40，按量产Grbl→S3顺序执行；准备阶段Grbl离线，尚未刷机或部署，不覆盖现有1.0.39。此前同号候选及宿主369/双板构建只作历史证据，新版本结果另记；`task_verify`未覆盖固件，通知具体文案自动断言减少已披露，实体放行继续Hold。详见`../docs/design/firmware-review-fixes-20261011.md`执行节及readiness。

2026-10-10 PR10修复：配对连接完成只读$I的完整MAC/终止ok事务后才发布连接，分包/截断/错误不遗留应答给正式授权探测。16项新增真实握手及状态机回归通过，S3全套宿主344项通过；**发布supersede候选状态**：301eb65/1.0.38已OTA至固定Freenove，量产Grbl b280e525也已升级；现役factory_paired=false，不能以本次升级代替配对实机验证。构建、SHA和回退见`../docs/design/three-repo-production-review-20261010.md`发布节。

2026-10-09 1.0.34已发布：S3 `00fd3ee`在固定量产Freenove（MAC `b8:1f:3f:c4:58:2c`）OTA启动确认，3080272B/SHA `cea42da2…2818af`。三次真实下载断流均失败回Idle，最后完整同版镜像升级与启动ELF复验通过，公网推荐一致；不再沿用下条1.0.33未发布候选SHA。完整证据和回退见`../docs/design/three-repo-followup-review-20261009.md`。

2026-10-09 OTA回收候选：`main/ota.cc`以RAII统一管理SDK句柄与下载缓冲，读取/写入失败和异常退出均清理，`esp_ota_end`后解除所有权避免重复abort。Freenove/ESP-IDF v6.0.1真实编译通过，app3080272B、SHA `626a92e2…18b4a4`，仅本地候选、仍沿用1.0.33构建版本，不覆盖已发布同号镜像。对应宿主回归与未部署边界见`../docs/design/three-repo-followup-review-20261009.md`。

2026-10-08 续审发布：S3 `f1fffce`已作为`hutuji.1.0.33`部署到量产Freenove（MAC `b8:1f:3f:c4:58:2c`），15:04:52回包确认版本、idle及nopaper链路ready/authorized/settings全true，公网推荐同步。bin SHA `a9979eb2…4022a`；旧1.0.32镜像与推荐指针备份保留。纸面动作未测，发布/回滚记录见`../docs/design/kdraw-runtime-review-20261008.md`。

2026-10-08 奎享参照续审候选：S3停止归位逐行确认并固定会话；丢应答兜底核XYZ、保留迟到错误并在超时先受控停止；正常灌流完成发布Quiesced。29项新增真实函数回归、277项S3全套host与量产Freenove构建通过，现役设备仍为前批1.0.32。完整本地验证与未部署边界见`../docs/design/kdraw-runtime-review-20261008.md`。

2026-10-08 发布结果：S3修复已提交`3b8a116`并升版`hutuji.1.0.32`，量产Freenove 2.8的MAC `b8:1f:3f:c4:58:2c`经OTA后回报新版本/idle，公网推荐同步。镜像3079328B、SHA `f13752b6…8d2ca`；原候选1.0.28的编译证据保留，不能混用哈希。写字机链路在升级前后均未连接，机械动作未测试。具体发布与回滚证据见`../docs/design/three-repo-review-20261008.md`。

2026-10-08 审查候选：S3 `hutuji_job.cc` 将忙、试笔中暂停/继续、换纸中暂停等拒绝改为结构化 `error`，幂等成功保留；自愈任务使用 `vTaskDeleteWithCaps` 回收，最终复核到重启持续持有运动/OTA 发布锁。248 项 host 通过，量产 Freenove 配置编译链接通过，镜像与未部署边界见 `../docs/design/three-repo-review-20261008.md`。未刷机。

> 补丁本体不在本仓库；位于 `D:/Users/xiaozhi-esp32` fork。本文只记录边界、构建入口和验证状态。  
> 全项目接手防呆：`../docs/agent-handoff.md`。状态：`../docs/release-readiness.md`。

## 1.0.18 纸张与跨仓审查候选（2026-09-14）

三板增加 `hutuji.paper`、`status.paper_config`、NVS 配置和下载 `X-Hutuji-Paper` 对照，屏幕设置按既有控件尺寸分页；任务/手动命令锁定连接与 reset 代次。预览失败结束等待，图像、HTTP/TLS 与 PSRAM 所有权按任务收敛；MCP 耗时工具后台运行，保持状态查询可响应。纸张字段与旧 `status.paper` 遥测分离，详见 `../docs/protocol.md` §10.4.24。

当前候选 220 项 host 通过，立创/Freenove/Waveshare 各编译 215 个 CXX。三板镜像与两个Grbl编译结果见 `results/20260913-crossrepo-followup/` 的本机记录及inventory。S3源码 `bde6ba5` 已提交，Freenove量产COM15/MAC `b8:1f:3f:c4:58:2c` 已保留NVS刷入1.0.18，官方状态确认A4配置、完整device_id、idle/ready；其他两板仅有构建证据。刷写前重新核对 MAC、板型和 SHA，保留 NVS，复位后查启动身份。Grbl 本批仅修 Telnet 实际收包长度及关闭旧客户端，量产与换纸分支分别构建、不能互刷。

## 1.0.14 评审修复候选（2026-09-13）

三板共用后台调速与可查询终态，只有无换纸量产机可调；奎享观察端校验已知目标、Seq 与连续新 Idle。174 项 host 回归通过，立创/Freenove/Waveshare 三板各真实编译 215 个 CXX 单元通过。量产目标为 COM15 / MAC `b8:1f:3f:c4:58:2c` 的 Freenove 2.8，镜像 SHA `499bcd44…a09568b`；本段仅记候选，板上部署证据由 `release-readiness.md` 登记。Grbl 换纸分支恢复独立 build 标识 `20211103`，不得刷到量产 `20260910` 机型。

## 一次扫码身份候选（2026-09-12）

独立 fork 新增 `hutuji_bind_identity.{h,cc}`/`hutuji_bind_identity_core.h`，三板共用 `hutuji_draw_bind` worker；P-256 软件私钥、NVS 持久化 nonce、HTTPS challenge+签名 announce，不要求消费者搬运 token。候选 `hutuji.1.0.11` 已完成 lichuang-dev、Freenove 2.8、Waveshare 3.5 的 SDK 构建，仍未由本批刷入设备或发布 OTA 推荐。字节契约、SDK 来源与限制见 `../docs/design/device-identity-rollout.md`；镜像/日志哈希登记于 `../docs/worktree-inventory.md`。保持 Grbl、Telnet、换纸及 GPIO 不变。

## 当前链路与契约

- 板型：`lichuang-dev`（立创实战派 ESP32-S3）。
- 板级原理图/脚位对照：`../docs/hardware/lichuang-szpi-esp32s3-board.md`（**以板级 `config.h` 为准，禁止轻易改脚**）。
- 正式板间链路：**WiFi Telnet :23**，S3 做 TCP 客户端；不使用 USB Host/CH9350。
- 设备侧工具：`hutuji.status/draw/abort/pause/resume/repeat/pen_test` 共七项。
- S3 是哑管道：全量下载 → 长度/CRC32 校验 → 授权门 → 逐行发送/等 `ok`。
- 权威契约：`../docs/protocol.md` **v0.5**；实施清单：`m1-usb-pipe.md` 的 M2 章节。

## 三仓边界

| 内容 | 修改位置 | 本仓留什么证据 |
|------|----------|----------------|
| S3 工具、下载、Telnet 转发 | `D:/Users/xiaozhi-esp32` | commit、构建日志、固件 SHA256、烧录/设备记录 |
| Grbl keepalive/状态路由 | `D:/Users/Grbl_Esp32` | commit、`agent_gate.py` overall pass、实测记录 |
| MCP 服务、协议、部署、SOP | `D:/Users/hutuji` | pytest/e2e/云桥接/发布清单 |

禁止把两个固件 fork 合并进本仓，禁止合并双芯片固件。

## S3 实施状态

| 里程碑 | 状态 | 说明 |
|--------|------|------|
| M1 Telnet 连接、发现、探活 | 🟡 已烧录实测，发现策略 2026-08-22 再收紧 | NVS IP 缓存 + `/24` 扫描，候选须通过 Grbl banner 校验；槽位忙改为 1s 重试缓存、不扫网跳过真 IP（host 119 项钉死）。刷后 COM14 见「扫网后缓存 IP 命中」，槽位忙 1s 快路径未单列取证；不把旧 `05f4563` 链路核验写成已覆盖本条 |
| M2 下载/CRC/授权/逐行流控 | 🟡 关键闭环已实测 | 全量下载、长度/CRC、授权门、逐行 `ok` 配对和 fresh-`Idle` 完成判定已回归 |
| abort/单写者 | 🟡 关键闭环已实测 | 非换纸 abort 从 `streaming/Run,Z=5` 恢复至 `aborted/Idle,Z=0`；普通画线断连后的废纸处理与 PSRAM 从头重发主路径已实测，最终页尾无纸 `error:90`，8.3 尚未完整通过 |
| 硬件冒烟与多页换纸 | 🟡 关键有纸路径已实测 | `13057e8`/`0x2ce890` 已有 S3 自动路径出墨、页尾 `[PaperStatus] 0` + `state=done`；2026-07-30 B-13 已捕获有纸 `Changing=On`。仍缺换纸中 abort、App 真实语音、P2 S3/S4 |

当前源码 HEAD 是 `211d75a`（`feat/hutuji-usb-pipe`，已提交并推送）。该版在既有下载/CRC/授权/窗口流控上叠加 Waveshare Grobot 工作台、预览/控制抽屉、BLE Phase A 与触摸层修复；当前默认镜像已构建并刷 COM14，入口恢复可见且前一版可拖动已有现场反馈，绝对锚定版跟手性和抽屉动作仍待 HIL。2026-08-08 的 `16f2f83` 标准 release/COM12 记录只保留为历史链路证据；当前 HEAD 的下载/CRC/窗口应答配对和纸面回归仍待真实设备 token，2026-08-03 S4 证据使用较早的 `ae4b524`。以下为历史提交链：`c1267ec` WiFi 三修 →
`ed5b132` 净作画时长 + ETA → `15f73c9` `StreamToGrbl` 窗口化流控（window=512 + 换纸行排空）→
`13057e8` 落笔前旁路 `G92 Z0` 校准 + `ok` 静默丢失兜底 → `f1cf066` 默认关闭的 §8.5 abort
取证脚手架（`HUTUJI_AUTO_TEST_ABORT_ON_PAPER`，产品行为不变）。
`15f73c9` 的 8.3 自动触发镜像 `xiaozhi.bin=0x2cdcb0`、SHA256 `090D65AB...8D0E` 已烧录 COM6，
并完成有纸 `state=done` 复测（`results/s3-ed5b132-windowed-flow-eta-paper-draw-evidence-20260729.md`）。
**该镜像属历史**——已被 2026-07-29 20:41 写入的 `0x2ce890`（`13057e8`）覆盖；`0x2ce890` 本身又已被 07-31 的 `0x2cec90` 覆盖，勿当现状。

**`13057e8` 固化了此前未提交的 3 个文件**（`main/boards/lichuang-dev/hutuji_job.cc`、`hutuji_job.h`、
`hutuji_pipe.cc`，+407/−83）：`PreparePenOrigin` 在 fresh `Idle` + 弹簧回位 100ms
（`kPenSpringReturnMs`）后旁路 `G92 Z0`，以及 `ok` 超时按 MPos 状态兜底（`kMaxOkFallback=3`、
`kOkFallbackPosTolMm=0.05`）。**S3-LC-01 在 2026-07-29 烧录的镜像内容即这批改动**（已被 07-31 的 `0x2cec90` 覆盖）——2026-07-29
20:39 构建 `xiaozhi.bin=0x2ce890`（2943120 字节），SHA256
`853179049C21028E62DB6784E20040756DB2866A73ED93F20842EFD7A0A24C3E`，20:41 由 app-flash 脚本
`results/.tmp/flash_autodraw_com6.ps1` 写入 COM6 并通过 esptool 自校验（`Wrote 2943120 bytes`、
`Hash of data verified`，ESP32-S3 MAC `9c:13:9e:8b:ae:f0`）。证据
`results/s3-13057e8-com6-flash-evidence-20260729.md`（原始日志由 IDF 自留于
`build-hutuji-f1000-autodraw/log/idf_py_stdout_output_36852`）。该镜像 autodraw URL 硬编码为
`http://192.168.1.3:8300/files/f1000-line.gcode`，上电即自动下载并画。
**烧录本身已可宣称**；此前落笔只在手动 Telnet 序列（`G92 Z0` →
`G1 Z5.0 F10000`）下纸面证实，S3 自动路径出墨已于 2026-07-30 B-12 归档：同一 `0x2ce890` 镜像下载 409 行/9782 字节/CRC32 `733095ec` 测试件，8 条线全部完整连续，页尾 `M30` 后 `[PaperStatus] 0`、`state=done`。

历史基线（勿当现状）：`3ca9d31` 在 `27eff73` 的受控自动测试入口上修复 `M3/M5` planner-sync `ok`
的 60s 误超时，IDF 6.0.1 默认构建 `xiaozhi.bin=0x2cce30`、app 分区剩余 `0x1231d0`/29%、SHA256
`5D5FE23CE0DF6EE364EBACC2503E8374174781AEAE5E9BFC9BACDF227987BBA6`；其 8.3 自动触发镜像
`xiaozhi.bin=0x2cd070`、剩余 `0x122f90`/29%、SHA256
`98C3B6AF0CB136AF79A0256B5FB8A5279FE0715FE087322419A791D3BD3C122C` 曾烧录 COM6。
该轮复测已看到断连、重连、`Changing=Off`、废纸 `M30`、`[PaperStatus] 0`、PSRAM 从第 1 行重画、
55/55 行完成与 fresh `Idle`；最终页尾自动换纸因无纸返回 `[PaperStatus] 2` + `error:90`，
任务 `state=error`。证据见 `results/bringup-8.3-fault-capture-20260729-010436.md` 与
`results/s3-3ca9d31-8.3-pen-timeout-evidence-20260729.md`；不得扩大为 8.3 完整通过或有纸换纸验收。

## 开发与构建

1. 在 `D:/Users/xiaozhi-esp32` 确认目标分支和基线，保存 `git status --short`。
2. 开工前读该仓自身 `AGENTS.md`，使用已验证的 ESP-IDF **v6.0.1**。
3. 构建入口：`python3 scripts/release.py lichuang-dev`；若上游脚本要求名称参数，以该仓 `--help` 为准。
   8.3 自动触发绘图调试镜像使用：`idf.py -DBOARD_NAME=lichuang-dev -DBOARD_TYPE=lichuang-dev -DHUTUJI_AUTO_TEST_DRAW=ON -DHUTUJI_AUTO_TEST_DRAW_URL='<短时效 G-code URL>' build`；真实 URL/token 不入库。
   bringup §8.5（换纸中调 abort）取证镜像再加 `-DHUTUJI_AUTO_TEST_ABORT_ON_PAPER=ON`；**PowerShell 下 URL 必须整体加单引号**，否则 `http://…` 会被切成新参数（`idf.py` 报 `No such option: -D`）。取证后回刷不带宏的镜像。
4. 产物必须记录：fork commit、是否 dirty、IDF 版本、板型、sdkconfig、构建命令、二进制 SHA256。
5. 烧录前确认 S3 与写字机在同一网络；连接器会先试 NVS 缓存，再扫描本机 `/24`，候选只有通过 Grbl banner 校验后才写回缓存。不得绕过 banner 校验固定错误主机。

## 固件完成判据

- clean commit 可复现构建；编译和烧录日志归档。
- `hutuji.status` 返回 `connected/ready/authorized`，状态查询不破坏单写者。
- 下载长度或 CRC 错误时零字节下发。
- 普通绘图、换纸、断连恢复、abort、缺纸/卡纸均按协议实测。
- 对应逐台记录和发布清单已填写；否则只能称“开发中”，不能称“可出货”。

历史 USB Host/CH9350 否决依据见 `../docs/hardware.md`，不要重新尝试已否决路线。

2026-10-22 C22技术发布：S3 `161174d`/1.0.46试笔领取清旧错误，发送/应答/会话失败先发布本次status原因；两Grbl `a268e05c`/`e5d7199a`完整行按会话短锁领取、锁外执行。量产Grbl单次Web OTA和S3单次HTTP OTA失败后同板USB恢复均有独立回执，48项参数及NVS保持，七项控制最终通过。构建/宿主/CI、首次失败与实体Hold详见`../docs/design/three-repo-review-20261022.md`及readiness，不能据此宣称出货。
# 2026-10-09 笔控与确认入口候选

S3首笔/试笔固定连接与reset banner，并复用Pipe写锁内会话发送；`hutuji.confirm`可带`expected_url`/`expected_preview_url`，在确认锁内匹配作品，`status.confirm_by_url`声明能力。旧无参人工入口保留，三板工具注册同步，SDK只构建量产Freenove。未发布的同号1.0.35候选不得替换线上镜像。设计、验证及部署状态见`../docs/design/three-repo-next-review-20261009.md`和readiness。

2026-10-10 DR10修复候选（软件）：Telnet使用会话代次隔离旧字节；开放网络用`[ESP101=]`写空STA密码；预览阶段拒绝pause/resume；nopaper电机/压纸遥测为unknown；设备OTA严格版本递增并验证下载SHA及镜像版本；token回写比较绑定代次和旧密文；历史flash备份登记缺失拒绝归档。未部署/未刷机，验证与边界见枢纽`docs/design/three-repo-deep-review-20261010.md`修复节和readiness。

2026-10-20 CR20本地候选：用户要求全部修复后，静默Web应答、聊天卡作品身份、激活RAII、文档编码、语音事务回执、归档删除失败分支和会话UTF-8七项已落代码与回归；未提交、未部署、未刷机。契约与验证记录见docs/design/three-repo-review-20261020.md及protocol末节，最终构建/门禁以本批证据为准，实体Hold不变。

2026-10-21 R20F发布执行中：用户要求先修复三项续审问题，再部署刷机验证上传推送。归档恢复/删除事务、S3连续会话分批及量产Grbl忙态查询守卫已实现。新版本计划S3 hutuji.1.0.41、小程序0.3.27；未完成步骤不预记成功。证据在 results/local-only/2026-10-21-release，脚本 tmp/r20f-release；详细阶段见 docs/design/three-repo-release-20261021.md。实体纸面/老化/工厂配对/交付签核仍Hold。

2026-10-21 R20F技术发布完成：Hub eb25bb2五文件主备deploy+verify/生产烟测通过，S3 a8f13b72/1.0.41与量产Grbl bc2cf670均单次OTA、重启/身份与48项参数复核有证；控制回归通过，Run查询守卫实机error8、Hold查询入队延迟导致附加探针未覆盖（宿主action已验），不混记。小程序0.3.27上传295665B；双机Freenove推荐/catalog/latest一致。preflight25/34、26/34已知差异保留，fz仅Wokwi超时失败、本轮QEMU PASS。产品三仓已推送，CI完成情况单独记录；factory_paired=false与纸面/老化/交付签核Hold不变。详见 docs/design/three-repo-release-20261021.md 及本机 results/local-only/2026-10-21-release/verification.json。
