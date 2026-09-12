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

只改上表与必要测试、状态记录、行号指纹；fz 仅修复验证脚本装载完整 flash。Grbl 不改运动、换纸、授权、GPIO 或协议状态字。

## 验证与交付

- 先保存可复现失败，再运行修复后的定向回归、portal 全量、小程序 Node 回归、hub/full 门禁。
- S3 运行 host 行为测试及三板 SDK 构建；Grbl 运行 release 构建和 fz standard 门禁。
- 云端逐文件从提交对象导出 LF，核对部署前状态、备份、双机文件哈希、服务与 preflight。
- 量产 S3 部署前核对 MAC/board/固件；速度实测只写已授权范围内参数，不发送运动指令。离线换纸板的构建结果不写成板上验证。
- 提交仅包含本次审查并核实的改动，保留原工作树其他资产；最终状态与证据同步 readiness、deployment、worktree-inventory。

## 外部接口依据

ESP-IDF v6.0.1 官方本地 SDK 快照：`components/freertos/FreeRTOS-Kernel/include/freertos/task.h`，xTaskCreateStatic 的 ulStackDepth 明确为 NUMBER OF BYTES；适用于本仓 S3 使用的 IDF，非通用 FreeRTOS 字长语义。官方来源：https://github.com/espressif/esp-idf/blob/v6.0.1/components/freertos/FreeRTOS-Kernel/include/freertos/task.h 。访问/本地核对日期 2026-09-13；SDK 注释与本地头文件已核对，板上栈余量需另留运行证据。

**已排除的怀疑**：进一步核对同 SDK 的 `portable/xtensa/include/freertos/portmacro.h`，`StackType_t=uint8_t`，旧表达式实际仍为 6144 字节，不能算栈缩小缺陷。观察端仅将命名改为字节并用 `sizeof` 明示单位，分配量不变。上游对应来源：https://github.com/espressif/esp-idf/blob/v6.0.1/components/freertos/FreeRTOS-Kernel/portable/xtensa/include/freertos/portmacro.h 。

## 当前结果

- portal 全量 321 passed；小程序 Node 68 passed；S3 host 174 项通过。
- S3 1.0.14 三板均真实编译 215 CXX：立创 b0931b63…872ab / Waveshare 7b314bbb…53c9b / Freenove 499bcd44…9568b。
- Grbl release 通过；fz standard 第二轮 exit=0，188.44 秒；observe hard=0、block_done=false，Wokwi startup pass。
- fz 首轮 app-only 装载导致 12 次复位；同一应用配完整 flash 后单次到 Grbl ready。修正验证脚本，新增 3 项离线回归拒绝旧合并应用、旧分区及缺少完整镜像。未改产品以适配模拟器。
- hub/full、提交与部署仍在进行；本段不代表已部署或手机/纸面通过。

Wokwi 官方依据：https://docs.wokwi.com/vscode/project-config ，访问 2026-09-13；CLI v0.26.1 / API 1.0.0-20260907-g3f4fedff。适用于本地 Arduino ESP32/Grbl fork：`flasher_args.json` 从 0x0 装载项目完整 flash；app SHA `d7d7d8ff…f17620` 在对照中不变。离线命令 `python -m unittest discover -s chip_sim -p test_*.py -q`；真实对照与 gate/observe 归档在 `results/20260913-crossrepo-review/`。Wokwi ready 不证明纸路、BT、OTA 或板上启动。
