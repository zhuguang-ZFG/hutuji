# 2026-09-13 三仓评审与修复

用户任务：code review 三仓代码；找出问题并修复、验证、部署、提交。
本仓没有 `.trellis/`，使用本仓 README、protocol v0.5、三仓 AGENTS 与门禁作为执行规范。

## 范围与基线

- 枢纽仓：`8b543426`，现有速度设置改动及相关文档未提交；旧临时文件不纳入提交。
- S3：`feat/hutuji-usb-pipe@9c4a218`，速度金表、工具与 1.0.13 已有未提交改动。
- Grbl：换纸分支 `fix/panel-hold-after-align-116687e@d06dd45`；实际量产机来自独立 worktree `massprod/nopaper-1.3a@6221502`。两者不得混刷。
- 起始 hub 门禁：2026-09-12T16:03:40Z，profile=hub，overall=pass；只作为修复前基线。

## 已确认问题与改动清单

| 问题 | 证据与后果 | 修复面 / 契约 |
|---|---|---|
| P1 换纸分支冒用无换纸机型标识 | Grbl.h 的 build=20260910，与 S3 的无换纸识别字面量相同；机型包线与页尾行为会选错 | Grbl.h 恢复换纸分支标识；跨仓机型回归；§10.4.15 |
| P1 速度工具阻塞主事件循环 | MCP dispatcher 在 Application::Schedule 内执行工具；RequestSpeed 同步等两个 5s 应答 | 板级 Job 改后台事务，status 发布可核对的结果，portal 等待实际完成；§3/§10.4.21 |
| P2 速度拒绝被报成功 | portal 只判断 HTTP 传输成功，设备 error / busy 正文仍返回 status=ok；小程序显示已调整 | app.py / mcp_result.py / push.py / control.js 与回归；§10.4.21 |
| P2 速度生命周期说明错误 | 两 Grbl 分支 FloatSetting::setStringValue 均写 NVS，普通重启不会恢复默认；默认值也须按机型读取 | 工具描述、控制页与协议改为重启保留、恢复出厂回各机型默认；不改 Grbl 参数存储逻辑 |
| P2 奎享完成误报 | Idle 后收包超时仍触发完成；未核对来源、Seq、Changing 完整性 | 纯状态机回归覆盖失联/乱序/跨机器/换纸，观察端仅用已验证目标的新包推进；§9-H′/§10.4.17 |
| P1 主备同时连接 cloud bridge | 部署前阿里和京东均 cloud/proxy connected；京东 disabled 但 active，MCP restart 经 PartOf 拉起热备 | bridge.env 显式主 1/备 0，unit 默认 0 并以 ExecCondition 拦截；运维重启与主备切换复验 |

只改上表、下述用户追加需求与必要测试、状态记录、行号指纹；fz 仅修复验证脚本装载完整 flash。换纸 Grbl 不改运动、换纸、授权、GPIO 或协议状态字；量产分支按下述用户追加要求只改 X/Y 出厂 max-rate。

## 用户追加：量产机归位后释放电机

2026-09-13 用户报告停下后持续沙沙声，随后明确「画完画回原点后，停止后失能」「开始任务时再使能」。COM3 只读取证为量产 `1.3a.20260910`、Idle/FS=0、`$1=255`；未测音频/电流，不将源码推断当作声源实测。

改动清单：S3 Job 在整单末尾或停止后的归位成功后，仅对 nopaper 发送现有 `$MD`。正常页尾补显式抬笔、fresh Idle 与弹簧沉降，再走原 G1 归位；暂停/绘图/换纸期不释放。`$1=255` 保留，不反复写 NVS；新任务仍先 `G92 Z0`，Grbl `st_wake_up()` 在下一次运动前自动使能。发送必须在 Pipe 单写者锁内核对同一连接、reset 横幅、fresh Idle、机型、授权与设置通过；失败/超时不声称释放。释放提交后不再接受能重新运动的暂停/停止编排，直至旧任务释放 busy。手动控制继续使用原维护面「关闭电机」；本需求只覆盖绘图任务生命周期。

本地量产源码依据：`massprod/nopaper-1.3a@6221502` 的 `ProcessSettings.cpp` 注册 `$MD` 为 idleOrAlarm 命令，`Motors.cpp` GPIO25 共用使能，`Stepper.cpp` 的 `st_wake_up()` 恢复使能；不采用 `$SLP`。2026-09-13 一次受控串口测试：身份核对、两次 fresh Idle/MPos=0 后 `$MD` 回 ok，随后仍 Idle/MPos=0；日志 `results/20260913-crossrepo-review/grbl-idle-motor-release-probe.log`。**用户随后确认声音「已停止」**，支持本次声音与停机持流相关；自动收尾与下一任务的实机验证另记。

实机复验追加两项修复：① 1.0.15 正常绘制与重画后均自动释放成功，但停止在归位 worker 完成前就发布 `aborted`；再次停止创建第二个 worker，串口出现重复抬笔、归位与 `$MD`。1.0.16 将停止请求幂等化、worker 原子抢占，并把终态延至 worker 收敛后发布，新增编译真实函数的回归。② 两个 Grbl 分支的 `motor_disable()` 对 `strdup` 参数均未释放，包括无参数 `$MD`；自动每任务调用会累积泄漏。仅以 RAII 保留原始分配地址，覆盖 `trim` 偏移和轴名解析早退，不改变掩码与错误语义；新增真实函数分配/释放计数回归。构建、部署与后续实机结果在下方登记。

## 用户追加：默认 10000 与三轴独立调速

用户要求 X/Y 默认 10000、Z 保留实机现值，小程序 X/Y/Z 独立设置。实现与验收记录单列在 [三轴设计](axis-speed-20260913.md)：增加只读查询、写前能力检查（防旧设备忽略 axis）、单轴写入与三轴读回，量产机头只改 X/Y 出厂值。下方 1.0.16/16000 结果属于前段静音交付，不作为本次最终速度；本次实际版本和默认值以 readiness 新条目为准。

## 验证与交付

- 先保存可复现失败，再运行修复后的定向回归、portal 全量、小程序 Node 回归、hub/full 门禁。
- S3 运行 host 行为测试及三板 SDK 构建；Grbl 运行 release 构建和 fz standard 门禁。
- 云端逐文件从提交对象导出 LF，核对部署前状态、备份、双机文件哈希、服务与 preflight。
- 量产 S3 部署前核对 MAC/board/固件；速度实测只写已授权范围内参数；追加静音验证使用既有写字链生成并额外限幅的小字样本。离线换纸板的构建结果不写成板上验证。
- 提交仅包含本次审查并核实的改动，保留原工作树其他资产；最终状态与证据同步 readiness、deployment、worktree-inventory。

## 外部接口依据

ESP-IDF v6.0.1 官方本地 SDK 快照：`components/freertos/FreeRTOS-Kernel/include/freertos/task.h`，xTaskCreateStatic 的 ulStackDepth 明确为 NUMBER OF BYTES；适用于本仓 S3 使用的 IDF，非通用 FreeRTOS 字长语义。官方来源：https://github.com/espressif/esp-idf/blob/v6.0.1/components/freertos/FreeRTOS-Kernel/include/freertos/task.h 。访问/本地核对日期 2026-09-13；SDK 注释与本地头文件已核对，板上栈余量需另留运行证据。

**已排除的怀疑**：进一步核对同 SDK 的 `portable/xtensa/include/freertos/portmacro.h`，`StackType_t=uint8_t`，旧表达式实际仍为 6144 字节，不能算栈缩小缺陷。观察端仅将命名改为字节并用 `sizeof` 明示单位，分配量不变。上游对应来源：https://github.com/espressif/esp-idf/blob/v6.0.1/components/freertos/FreeRTOS-Kernel/portable/xtensa/include/freertos/portmacro.h 。

## 前段静音交付结果与证据边界（1.0.16）

| 面 | 结果与证据 |
|---|---|
| 云端代码 | hub `2d495d106a65aa513d9f9cc039413187a95478ba`，双机8文件从commit blob导出LF部署、SHA一致/CR=0；备份 `/root/hutuji-bak-20260913-review-2d495d106a65`。主bridge active、备inactive且显式start受ExecCondition拦截，双机portal200 |
| 回归 | portal321、weapp68、MCP1453通过；本轮前段hub/full13层与e2e PASS。S3最终179 host、三板各215 CXX；两支Grbl release、各10000次真实`motor_disable`参数调用无泄漏并保留掩码/错误语义；fz最终standard194.94s/exit0/observe hard0 soft0 block_done=false |
| S3提交/落机 | `b4d267d`→`d72447c`→`99cf4234e41d6c1199d0b99652cbf6c2f318a6f2`。COM15 Freenove `b8:1f:3f:c4:58:2c` 已运行1.0.16，app3044688B/SHA `0abf5e08…cf9fac2`；只写应用/otadata，NVS保留；串口和云status独立核对版本与就绪 |
| Grbl提交/落机 | 换纸分支 `c9a5d68`→`001a16c`，未刷量产机。量产分支 `e68c697fda7a7ca5dcd685a0ecb8736ee8869f9d` 经已有Web OTA更新COM3 `4c:c3:82:cc:8a:9c`；DIO1843312B/SHA `595d7d39…77b071d`，OTA返回3、身份/设置不变、最终原点Idle |
| 实机失能/重启 | 用户确认手动`$MD`后沙沙声停止。最终1.0.16+e68c697：小字“口”（24行、468B，X26.338–32.613/Y9.376–15.007）正常完成与repeat均经过真实Run、原点Idle和独立的释放确认；9字样本（168行、3639B，X≤50.414/Y≤40.556）运动中连续两次abort，只执行一轮归位与一次`$MD`，随后aborted/Idle/MPos0。完整文件额外经60×60mm包线校验，含弧扫掠 |
| 内存实测 | 量产OTA后200次`$MD`全ok；堆样本before82.46/83.92/83.83KB、after85.38/85.74/85.38KB，未见累积下降。HTTP/WiFi分配有抖动，零泄漏结论以真实函数10000次分配/释放精确计数为据，实机只报该采样观察 |
| 速度链路 | 生产portal请求8000→16000，两轴`$$`交叉核对，最终16000；每步仅在已就绪/Idle设备执行。结果见`ali-portal-speed-*.log`与`grbl-speed-*.log` |
| 小程序 | 0.3.11上传exit0，144465B/141.1KB，描述「优化速度调节结果反馈与连接异常处理」。未代选体验版、提交审核或线上发布 |
| 生产巡检 | 阿里24 pass/9 fail、京东25 pass/8 fail。既有unit drop-in；阿里nginx多租户/专用日志/:80旧Tunnel口径等8项；京东停bridge相关4项、unit/nginx差异以及公网文件走阿里。公网HTTPS G-code下载、MCP404与生成链通过。未为了全绿覆盖生产nginx模板 |

1.0.15的停止失败记录保留在`hutuji.1.0.15/abort-*.log`；1.0.16在旧源码上先复现两个回归失败，再验证修复与实机连点。最终样本、双串口日志、构建与哈希见`results/20260913-crossrepo-review/verification-summary.json`、`hutuji.1.0.16/`和`motor-disable-memory/`。S3其他两板仅有构建证据，Grbl换纸板未刷；本批不替代手机slider、语音/触屏交互、全幅纸面或出货签核。未全车队发布S3 OTA。

Wokwi 官方依据：https://docs.wokwi.com/vscode/project-config ，访问 2026-09-13；CLI v0.26.1 / API 1.0.0-20260907-g3f4fedff。适用于本地 Arduino ESP32/Grbl fork：`flasher_args.json` 从 0x0 装载项目完整 flash；app SHA `d7d7d8ff…f17620` 在对照中不变。离线命令 `python -m unittest discover -s chip_sim -p test_*.py -q`；真实对照与 gate/observe 归档在 `results/20260913-crossrepo-review/`。Wokwi ready 不证明纸路、BT、OTA 或板上启动。
