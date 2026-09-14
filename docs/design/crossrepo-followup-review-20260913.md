# 三仓追加迭代：命令收包与状态会话

用户原话：「三仓要多轮迭代找出隐藏问题；并修复」。本批接续小程序体验审查，三仓分别验证、分别提交；依据 `protocol.md` §4/§9-A2 和手动控制契约。不改变运动/换纸/授权/G-code 包线。机读清单为 `crossrepo_followup_20260913_authorized`。

## 迭代与判据

第一轮沿着「小程序控制 → S3 命令 → Grbl 收包 → 状态回传」寻找异常边界，以真实 C++ 函数编译测试注入短读、重连、复位和停止。第二轮检查相邻的会话结束、错误展示、页面切换与迟到回包；第三轮跑三仓完整回归、真编译与发布前核对。

| 范围 | 已观察问题 / 待验证假设 | 验证与最小改动 |
|---|---|---|
| Grbl Telnet 收包 | `handle()` 忽略 `read()` 实际长度；零/负返回与短读仍按预期长度入队 | 真函数故障注入；仅将实际正长度入队，保留 1024B 栈缓冲和 ring 容量上限 |
| Grbl 服务关闭 | `end()` 仅删除监听者，已接入客户端仍存活 | 真函数关闭/重开测试；在原递归锁内停止旧客户端 |
| S3 新鲜状态 | 等待过程中未核对连接/启动横幅代次；重连回包可能使旧查询返回成功 | 真函数注入；查询前锁定会话，等待中和返回前复核 |
| S3 完成与停止 | `WaitForIdle()` 最后看到 Idle 时未复核停止和链路 | 同时到达停止/Idle、重连/Idle；保留恢复阶段忽略 abort 的既有语义 |

## 证据与发布状态

证据目录 `results/20260913-crossrepo-followup/`（只在本机）。以上先复现再修复；测试、构建、板上和生产状态分别登记，不以源码推断设备已运行。外仓写入由具体补丁执行，保留 Grbl 原 `.gitignore` 修改。任何 Grbl C++ 改动后必须跑 fz `standard` 与枢纽 `full`。

## 后续轮次（2026-09-14）

- S3 手动动作与命令应答也锁定连接/reset 会话，旧 Idle、旧 ok 和迟到回包不能结清新动作。耗时 MCP 工具在后台执行，错误文本经 JSON 转义，不阻塞状态查询。预览请求失败清除等待并收敛任务，图像/PSRAM/TLS/decoder 各持有者按任务释放；沿用前轮 2000 次接管/释放证据。
- Redis enqueue、payload SHA、claim token、pending 恢复与终态防复活完成真实 Redis 故障回归。`queue-round3-20260914-044308-316666.log` 64 项通过；新旧 payload 不可混跑，发布必须排空旧队列并停旧 worker。
- 纸张状态使用 `status.paper_config`，保留 `status.paper` 遥测；动态配置与目标随生成事务冻结，文件/设备双侧核对。手绘四组真实转换与栅格对照见 `paper-settings-20260913/design.md`，不当作大行程硬件验证。

候选构建：S3 `hutuji.1.0.18` host **220** 项，三板各 **215 CXX**，结果 `hutuji.1.0.18/firmware-builds-20260914-081137-918478.json`。普通 Grbl **197 编译单元/1859264B**，SHA `637d43501923a26d9a576729851d927d14bed0f90061fbfabd4ec21a4b1bce46`；量产 Grbl **195 编译单元/1843392B**，DIO=2，SHA `c517f6eac21d8985d208e97cfd6780f47ddea2e09484ffe3fb8eb6a740bd5afd`，两棵 host 各 **8** 项通过。完整编译记录在 `grbl-build-085946-144613/builds.json`，supersede 更早「只复用旧镜像」的阶段判断。

普通 Grbl 本轮 fz standard **201 秒、PASS、hard=0/soft=0、block_done_claim=False**，归档 `fz-142953-265594-*`；早先 `fz-224035-297158` 的 QEMU 失败已由此替代。量产树重跑 `fz-massprod-151259-298808-*` **153.59秒、FAIL**，QEMU已通过，剩余纸路/GPIO/core不适用项与Wokwi缺完整镜像。`massprod-baseline-153339-532626/` 从量产原始HEAD `ca9b36bb1ff6e6249480f1e1aaebf4f671f7fd5d` 导出172份源码，同一纸路/GPIO校验的失败内容与候选一致，`ProtocolDecisionCore.h`两者均缺席；受跟踪产品源码仅变更TelnetServer.cpp。用已验证的当前量产DIO应用、对应SDK启动程序和分区表组成完整模拟镜像后，`massprod-wokwi-current/` **Wokwi启动PASS**；完整镜像仅用于模拟，不写实体NVS。量产fz仍不写成通过。

最终枢纽full **13层PASS**，MCP **1535 passed / 149.69秒**、e2e PASS、261处行号指纹通过，报告 `final-full-hutuji_gate.{json,md}`。2026-09-14 16:18用户接回量产写字机后COM3只读复验，MAC与台账相符、Idle/原点、速度3000/3000/1200、`$1=255`，没有改设置（`grbl-reconnected-20260914-161806.log`）。随后分别提交S3 `bde6ba5`、普通Grbl `6abedbd`、量产Grbl `9bebc00`。S3仅将已核SHA的Freenove应用和OTA选择刷入COM15，NVS备份及旧应用描述核对通过；官方状态已确认1.0.18、默认A4配置、完整device_id和idle/ready（`ali-device-163042-694203.log`）。两个Grbl候选未刷写，后端和已上传小程序0.3.13尚未切换。
