# 2026-10-08 三仓代码审查与修复

用户初始授权：「三仓code review；找出问题；并完全修复」。审查阶段只处理有源码路径和回归证据的缺陷。2026-10-08追加授权「提交部署」：提交双仓修复，发布云端主备服务并升版量产S3；部署与设备结果另记下方发布续作节。

## 基线与范围

- hutuji：`6f8eee1`，已跟踪文件无改动；保留既有未跟踪资产。
- xiaozhi-esp32：`8a9ec02`，已跟踪文件无改动。
- Grbl_Esp32-massprod：`d723f4c`，分支 `massprod/nopaper-1.3a`，已跟踪文件无改动。
- 普通 Grbl 的既有 `.gitignore` 改动保留，不将换纸模块移入量产分支。

审查面：生成/推送与纸张身份链、Portal OTA 事务、设备 MCP 应答、S3 自愈和运动/OTA 互斥、量产 Grbl Telnet/STA 网络恢复与 Web 请求上下文。没有 `.trellis` 项目配置，按仓内协议和门禁执行。

## 已确认问题与改动清单

| 编号 | 等级 | 触发与影响 | 修复路径与依据 |
|---|---|---|---|
| R08-01 | P1 | 设备忙时 `StartDraw` 返回成功字符串；Portal 将拒绝当成功，并可能自动确认另一张预览 | S3 `hutuji_job.cc` 与 Portal `mcp_result.py`、对应回归；§4/§10.4.5/§10.4.21 |
| R08-02 | P2 | OTA 传输层返回 `网络 TimeoutError`，业务层仅匹配小写 timeout，错误解除升级等待 | `firmware_ota.py` 与 OTA HTTP 回归；§10.4.22 |
| R08-03 | P2 | 自愈任务由 WithCaps 创建，放弃重启时普通删除不能释放自行分配的栈/TCB | S3 `hutuji_job.cc` 与真实 C++ 回归；§4 自愈生命周期 |
| R08-04 | P1 | 自愈最终检查与 restart 间未持有任务发布锁，可能与新运动/OTA 接管交错；新任务清零失败计数后旧自愈也未重新核对 | S3 `hutuji_job.cc` 与确定性交错回归；§4 单任务互斥与自愈放弃规则 |
| R08-05 | P1 | `success/data/{isError,text}` 解包直接越过中间层，显式失败被正文的 started 遮掉 | 共享 `hutuji_mcp/tool_result.py` 逐层判错、Portal 保留原导出路径；嵌套三种错误旗标回归 |
| R08-06 | P2 | MCP 备用链式推送仅看 HTTP 状态，设备业务失败和 token 自愈后的拒绝均被记为受理 | `device_messaging.py` 接共享判据，保留真实 HTTP 状态和无敏感值的失败摘要；直发/重铸两路径共6个回归 |

先新增失败回归，再修复实现。外仓写入已通过执行环境的目录权限审批；应用前核对原文件 SHA，未覆盖并发改动。S3 原有“必须有4个 JsonString 忙返回”断言同步为五个入口保留中文拒绝原因且不再用成功字符串；没有删除原测试。

## 驳回的疑点

- Grbl Telnet 环形缓冲 push/read 数据竞争：`clientCheckTask` 顺序执行 `getClientChar` 和 WiFi handle，两者同任务；不能仅凭缓冲字段没有锁就判并发缺陷。
- S3 新绘图清零 TLS 连续失败计数：§4 明确约定，且单次预览/下载重试内已经能达到阈值，不改成跨任务累计。

## 验证与交付

基线：Portal OTA 26 passed；量产 Grbl host 16 tests OK。

- RED：Portal 原新增矩阵10 fail/5 pass，嵌套信封3 fail；MCP直发/重铸6 fail；S3自愈8 fail/1 pass，绘图/暂停/继续5 fail/3 pass。均调用真实 Python 路径或编译真实 C++ 函数，硬件接口为替身。
- GREEN：Portal完整 `pytest -q` 487 passed；小程序 `node --test tests/weapp/*.test.js` 254 passed；S3 `unittest discover -s scripts/tests -v` 248 tests OK；量产 Grbl同命令16 tests OK；MCP设备推送定向14 passed。
- 枢纽 `hub` 门禁12/12 PASS；MCP 1611 passed（161.91s）、e2e PASS；268处行号指纹与255处外仓行号校验通过。MCP实际collect为1611项/46文件，当前总数及14项设备推送分项自洽。
- SDK构建：既有量产 Freenove 2.8/ESP32-S3配置，ESP-IDF v6.0.1，真实重编译 `hutuji_job.cc`、链接和分区检查通过；app 3079328B，应用分区余量25%，SHA-256 `510d31ba33a6425419c1ea314507dc6f4a659c22fa0b66afdca2c7296c0b0de4`。第一次构建因Python虚拟环境 C/D 别名不一致被IDF拒绝；改回缓存记录的C盘入口后通过，没有 fullclean 或修改板型。
- 固件版本仍为 `hutuji.1.0.28` 的本地审查构建；未覆盖已归档发布包，不能当作已发布新版本。发布前须按独立发布流程升版和验证。
- 首轮枢纽检查1610 passed/1 failed：多页写字测试的HTTP替身只有状态码、缺少真实应答的text字段；补齐成功信封并保留pages/目标一致性断言，定向通过后复跑门禁。另一个后台推送替身同步补齐，避免异步异常被测试等待逻辑遮掉。
- 文档行号由 `--fix-line-refs` 按原内容唯一匹配重定位，格式化后复核；一处落到空行的引用人工确认应指回“进度屏显250ms节流”原注释，保留其原指纹，未盲目重登。

## SDK依据与限制

来源：Espressif官方 ESP-IDF [idf_additions.h](https://github.com/espressif/esp-idf/blob/v6.0.1/components/freertos/esp_additions/include/freertos/idf_additions.h) 的 WithCaps 创建/删除配对契约。本地SDK提交 `8c19b156084a0753687347cca1f5355782893533`（v6.0.1），2026-10-08读取本机 `components/freertos/esp_additions/include/freertos/idf_additions.h` 和 `idf_additions.c` 复核：WithCaps 使用独立分配的栈与内部TCB，普通删除不承担其回收。适用于本次ESP32-S3/PSRAM配置；回归使用替身记录配对释放，实际芯片的内存回收/任务接管时间窗尚未板测。

证据存于 `results/local-only/2026-10-08-three-repo-review/`（本机、被忽略），包含RED/GREEN日志、SDK编译日志、候选镜像及SHA、工作树差异与验证摘要。没有生产部署、OTA、串口或机械动作；没有修改量产Grbl源码、GPIO、速度、运动或换纸行为。本次只覆盖上述审查面，不作“全仓绝无其他缺陷”的保证。

## 发布续作（用户追加授权「提交部署」）

按现有 `auto_deploy.py` 从明确提交的LF blob打包，仅发布4个服务文件：共享`tool_result.py`、MCP `device_messaging.py`、Portal `mcp_result.py`与`firmware_ota.py`。主备逐节点发布、保留回滚号、核对哈希和健康；备机bridge保持inactive。量产S3使用新版本`hutuji.1.0.32`，避开先前1.0.29–31诊断镜像号；当前线上推荐版为1.0.28。实际提交、部署及OTA结果待执行后回填，不沿用审查镜像哈希作为升版产物。

**执行结果（14:15–14:22 CST，supersede 上段待执行状态）**：

- 源码已提交并推送：hub `859a46f2c2c10c78a8c3fc1118a105edefdfa02e`，S3 `3b8a1166d36758fb6d0612b72283a2e342d73a74`。量产Grbl无本批变更。
- 云端先热备后主机发布4文件，服务与文件哈希verify双过，生产venv导入与拒绝/嵌套旗标冒烟双过，LF无CR。京东备份号`6eedfa87c7584153a505019ba6c80794`，阿里备份号`29a3ecc15de3407091ea7f4be5685f7d`。回滚使用`auto_deploy.py --rollback --target <节点> --backup-id <对应号>`。
- 包SHA：阿里`a39bfceaed072b80f2d37932e32d195f9eb664bde941bf9aedc4e8ccf75bcf9d`；京东`1af790153ee90895940e6cfb09870a105ad016d0d4c87dd15550cf5f92b9a70f`。发布/verify日志在`results/deploy-20261008-141530-d999256b`、`-141644-ec678617`、`-141711-4f4ef38e`、`-141822-e676c753`。
- 生产preflight阿里25/34、京东26/34，与已登记基线一致；失败仍为主备角色差异、现役systemd drop-in、nginx布局及DNS回源与旧模板不一致。主节点公网HTTPS文件真下载、双机生成与描摹链均通过；未据此把preflight标成全绿，也未改动既定主备架构以迁就模板。
- S3在固定提交上升版构建：ESP32-S3/Freenove/ESP-IDF v6.0.1，`esptool image-info`验证版本、校验和与镜像hash有效；3079328B，SHA-256 `f13752b6609385b204dbe375bf7c9606be98b84f398d72a90dead86e7dc8d2ca`，ELF SHA `e6720de373206f5eef49e8c4dcc2632c727aa76ede3d27a5d483fb76d0e47fc1`。先上传不可变候选版本，公网完整下载200且SHA逐位相符，再向MAC `b8:1f:3f:c4:58:2c`下发一次OTA。
- OTA命令回504，重启窗口首查404；未重复下发。14:21:12的只读状态确认相同MAC/板型、`firmware_version=hutuji.1.0.32`、`state=idle`、`ota.state=idle`、heap字段在线。随后promote并验证公网latest/catalog均为1.0.32与同SHA；原推荐1.0.28的指针备份在主节点`/root/hutuji-bak-20261008-ota-1.0.32/`，旧版本镜像保留。
- 初次升级前后写字机链路为`connected=false/ready=false/grbl_state=Unknown`，离线时`plotter_sku=paper`为保守默认。14:24:59再查：同设备仍为1.0.32，connected/ready/authorized/grbl_settings_ok全true，plotter_sku=nopaper、grbl_state=Idle，链路已恢复。没有串口、归位、试笔或真实绘图；本次板上证据覆盖身份、OTA版本启动与只读链路状态。

发布证据集中于`results/local-only/2026-10-08-release/`，包含公网候选bin、catalog、脱敏状态时间线、双机导入哈希、preflight与升版构建记录；不覆盖审查阶段`three-repo-review`证据目录中的1.0.28候选镜像。
