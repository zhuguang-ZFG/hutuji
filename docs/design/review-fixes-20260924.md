# 三仓审查修复与复审迭代（2026-09-24）

用户先要求“三仓深度 code review”，随后授权“修复以上 code review 出来的问题”，再要求修复后多轮复审，查逻辑问题与冗余设计。本批以可复现的问题为修复依据，不把测试全绿写成无缺陷保证。

用户最新裁决：**量产写字机无自动换纸，交付目标固定 `Grbl_Esp32-massprod` 的 `massprod/nopaper-1.3a`**。普通树只保留先前已完成的同步修复及历史参考，不作为量产固件来源。

## 范围与基线

- 枢纽 `D:/Users/hutuji`：基线 `7202758`；保留用户已有 `docs/deployment.md` 改动。
- S3 `D:/Users/xiaozhi-esp32`：基线 `2312446`。
- Grbl 普通树 `D:/Users/Grbl_Esp32`：基线 `f62ccf5`；保留已有 `.gitignore` 改动。
- 同一 Grbl 仓量产树 `D:/Users/Grbl_Esp32-massprod`：基线 `5b7e3bc`，同步网络层修复。

源码修复未提交、未部署、未刷机；商业机械/运动/纸路/授权行为不变。原审查证据为 `results/20260924-review-continue/review.md`，修复/复审证据为 `results/20260924-review-fixes/`；两目录被忽略，只在本机，已登记 inventory。

## 第一轮：六项已复现问题

| 编号 | 原缺陷 | 修复及回归 |
|---|---|---|
| R24C-01，P1 | 仅按客户端 sid 缓存语音历史，知道另一账号 sid 即可复用上下文 | API 注入已认证用户身份，历史键为 `(user_id, session_id)`；跨账号同 sid 历史隔离回归。普通退出已有清 sid，不声称正常登出就串历史 |
| R24C-02，P1 | S3 OTA 只检查旧空闲快照，调度前不占任务，可与绘图/另一升级并发 | stream/state 锁内原子占用 Job busy；回调确认所有权；异常/升级失败释放；成功持有到 reboot；停止/暂停/继续拒抢 OTA。提取真实方法/回调编译测试 |
| R24C-03，P1 | Grbl 应答流裸指针和参数为文件全局，任务交错后串路由或访问已释放应答 | 调用栈 RAII 上下文，按当前 RTOS task 查找，兼容同任务嵌套；微小临界区保护链表。真实 action/webPrint 并发及嵌套 C++ 回归；普通/量产同补丁 |
| R24C-04，P2 | 两个升级请求都过检查，拒绝者覆盖已受理会话 | SQLite 条件插入/更新在 I/O 前领取；每轮唯一 session_id，所有回写比对会话代次；并发请求只有一个发送者、旧回包不能终结新会话 |
| R24C-05，P2 | 配置缓存未命中时同步 urllib 阻塞 MCP 事件循环 | DrawLimiter、racer 和 vpype 配置读取移到线程；取消未占名额；下方第三轮继续收紧配置回写 |
| R24C-06，P2 | 语音同会话并发时，按变化后的 history 长度切片，丢用户和工具链 | 完整轮次非阻塞互斥，重叠回 409；活动会话禁止 LRU 逐出；异常释放；保留完整用户/assistant tool_calls/tool 回包 |

## 第二轮：修复后交错复审

1. **OTA 领取阶段误消费旧状态（P2）**：占位后但尚未下发时，另一终端轮询读到旧的 ota.failed，把新会话终结。新增用例在目标查出前和命令应答前分别轮询，原实现失败（`portal-223144-232443.log`）。修复为内部 starting 阶段，对外仍 upgrading；命令应答后再转入状态对账，CAS 阻止阶段跨越的旧轮询回写。
2. **vpype 调参重建并发闸（P2）**：已有两个转换时从 2 缩到 1，旧实现新建信号量并立即放进第三个（`mcp-223155-718384.log`）。改为每 loop 一个可调整闸，缩容等现有任务退出，扩容唤醒等待者；取消等待者不占名额。回归同时验证缩容、扩容及取消。

## 第三轮：线程边界与设计收敛

配置 I/O 改为线程后，复审发现旧缓存锁只保护查值/写值，中间网络刷新可并发放大并由慢旧响应覆盖新快照。刷新合并到同一锁内，TTL 从完成起算；所有生产异步入口在线程执行。删除没有读取者的 `_cache_revision`，避免维护一份无用途的状态。

DrawLimiter 工作线程只返回三个限制值，事件循环在无 await 的计数临界区前整体应用；取消的刷新不再继续修改限流对象。保留无 await 的名额释放路径。新增并发缓存刷新回归，并加强取消后配置不变断言。

最终人工复核覆盖：语音鉴权到历史/工具回写、OTA SQLite 迁移/领取/受理/超时/CAS、S3 stream/state 锁顺序与失败释放、Grbl 嵌套上下文生命期、vpype 调参与取消。未发现需以新增框架或全局大锁解决的问题，维持局部实现。

## 第四轮：送达不确定和终态单向收敛

504/读超时可能已送达设备；旧设备失败若被直接消费，会提前释放刚领取的升级忙闸。新增内部 `uncertain`，先观察到设备进入 upgrading 后才消费失败；目标版本匹配仍可直接确认成功。starting/uncertain/upgrading 共用 600s 过期与出图拦截。终态不被后续失败回退，只有 status_timeout 保留版本对账恢复路径。新增回归覆盖这些交错，Portal 最终全套449项通过。

## 验证与未完成项

- Portal 全套 **449 passed**（`portal-all-224835-418046.log`）；唯一提示为 Starlette/httpx 弃用警告。
- S3 host 全套 **228 passed**；已有停止用例补充 OTA 占位断言，新 OTA 真实核心/回调 2 项通过。
- Grbl 普通/量产 release 真编译通过：普通 1,859,856B，SHA256 `09a48e7ccc2a24299406143571e85ded2f59c8fa45d19583b2225d7629b63c63`；量产 1,843,984B，SHA256 `651681fa4b9dea96ca202aae87f41caa985ad84af060ea33fedf01e883c3ac26`。两树请求上下文及参数解析回归分别留证。
- 本仓 full **PASS**：MCP **1583 passed**、e2e PASS、263处指纹及250处外仓引用通过（`full-224426-213164.log`）；此 full 的外仓历史引用仍以普通 Grbl 树核对。量产 Grbl host **15 passed**，S3三板各215个 CXX真实编译通过，镜像摘要见下文。
- fz 普通首次 standard 失败：Wokwi 连接关闭/ready timeout、plant_feed_hold 未观察到 Hold；定向暂停复跑仍失败，未修改产品运动行为迎合模拟器。量产完整运行时相同暂停用例通过，说明结果存在时序差异，普通完整复跑另留证。
- fz 量产适配前 standard 失败：缺 `ProtocolDecisionCore.h`/纸路纯核心、引脚契约及完整刷写产物，影响 native_units/protocol_scenarios/machine_pin_erc/machine_pin_mutations/wokwi_startup/qemu_startup/paper_contract/native_product；报告 fail=8。不能用普通树结果替代量产树，也不能为过门禁恢复量产已裁剪的换纸模块。
- 手机多端并发、板上 OTA 与运动互斥、Grbl 长时间并发、纸面验收和生产部署未执行。fz现已增加无换纸 SKU 分流（见下文），不据纸路检查失败判定产品缺功能；量产启动、网络和运动检查保留。启动门禁和板上验收闭合前，不声明整体交付验收完成。

复跑入口：`mcp-server/.venv/Scripts/python.exe tmp/review_20260924_verify.py`（portal-all/mcp-all/s3-all/grbl-context/s3-ota）；`tmp/review_gates.py`（full/fz/fz-massprod/build-grbl/build-massprod）。临时脚本不作为产品接口，日志先过共享脱敏函数。

### 构建与复跑补记

S3 三板各真实编译215个 CXX：lichuang-dev 3,114,112B/SHA `44a0402a…e73ab`；waveshare 3.5 3,116,256B/SHA `3bceef74…424d7`；交付目标 freenove 2.8 3,075,568B/SHA `93ee3f24bbf55581bf3f70ff3201ee7eb06295b1ca86b8ee53309206a8f86a7a`。所有镜像仅本机构建。

fz本轮改进前，普通树完整复跑 PASS（`fz-223830-001930.log`），当时hard=0、block_done_claim=false；该结果属于旧镜像选择/启动判据，不能作为改进后最终验收，也不替代量产树验证。枢纽文档中的历史外仓行号仍指普通树，对该参考树跑的 full 标记检查只证明引用/既有契约，不代表量产具备自动换纸。


## fz 多轮复审改进（用户追加授权）

用户原话「fz仿真你如果觉得质量有问题，你可以修改修正改进」。修复明细与外部参考纪律记录在 `D:/Users/zhugu/fz/docs/specs/2026-09-24-nopaper-gate-review.md`，精确外仓文件已登记 allowlist。

- 增加严格SKU身份识别：nopaper固定版本戳/DIO和独立7引脚契约；只有已知SKU可将纸路纯核心记为不适用。两个SKU均必跑产品网络/电机源码回归、协议/运动、引脚ERC和6类缺陷注入。
- 修复跨工作树/构建环境拾取旧镜像；每次从本次release重建整片，Wokwi逐字节对齐当前应用/分区并清除旧ELF。QEMU不再优先残留qemu镜像，合并失败必须fail。
- 修复启动报告误判：仅启动前且无串口的云鉴权/连接故障可软化；所有失败层都有提示，不再输出“No hard failures”。报告缺席或过时保持严格失败。
- 进一步复审发现Wokwi“Grbl”标题命中即提前结束，早于WiFi初始化。改为完整30秒模拟观察窗、CLI专用完成码43、`['$' for help]`主循环就绪标记和全日志异常检测；清空本轮串口日志，CLI墙钟最多90秒。仅标题、晚到panic、提前0退出、旧日志均不能通过。该修复使早期标题pass不再能冒充初始化通过。

专项回归全部通过：18项观察/门禁、4项机型身份、1项引脚缺陷注入、35项芯片工具（`fz-focused-231544.log`）。量产完整standard最终 **FAIL**（`fz-massprod-231611-074556.log`，253.89秒）；产品host15项、协议、19项运动、引脚7/7和缺陷注入6/6通过，纸路核心显式不适用。两项启动仍未闭合：

1. **Wokwi**：完整观察时未出现主循环就绪，串口到电机/笔控制初始化后停止，CLI达到90秒墙钟上限；不是启动前网络故障，不软化。较早 `fz-massprod-231030-653960.log` 的Wokwi pass只证明标题出现，已被本次更完整判据替代。
2. **QEMU**：当前release来源/机器身份匹配，app SHA `651681fa…ac26`；`LoadStorePIFAddrError` 地址 `0x60033c00`，量产ELF定位至 `register_chipv7_phy` → `esp_phy_rf_init` → WiFi初始化。官方源码快照存在未实现外设，但未与本机QEMU发布commit匹配，尚不足以将本故障归为已证明的模拟器限制；保留hard失败，不扩大panic白名单。

已读observe：量产hard=3（两失败层+一条Wokwi详情）、block_done_claim=true。`sim_rerun --from-last` 仅支持协议/运动用例，本次返回3（无此类失败），已改用完整standard复跑；不把复跑入口的3误写成通过。CLI/模拟器不是实机，板上OTA与运动互斥、并发长期运行和纸面验收仍待独立取证。本批未提交、未部署、未刷机。

普通SKU改进后最终standard也已完整复跑（`fz-232053-669047.log`，262.54秒）：纸路协议场景/源码核心/模型检查/fuzz、28引脚契约、6类缺陷注入、产品host、协议与19项运动均通过；Wokwi同样90秒未就绪，整体FAIL，observe hard=2、block_done_claim=true。QEMU按既有实验性判据pass不等于应用稳定或板上通过。该运行证实SKU分流未移除普通机型的纸路检查；先前旧门禁PASS仅保留为历史，不覆盖当前失败。最后枢纽docs门禁PASS；所有仓库差异检查无空白错误。


## 2026-09-25 复审补记：移除崩溃豁免

再次审查发现普通SKU QEMU曾把“已知panic”判作实验性通过；已完全取消该豁免，指纹只供诊断，`panic_exemptions_applied=false`。协议活性必须独立完整 `[VER:...]` 行和独立 `ok` 行同时出现，普通日志的 ok 不算；standard明确启用 `--require-protocol`。观察建议按失败层路由：启动失败完整重跑standard，只有协议/运动失败可用sim_rerun；nopaper跳过的coverage不消费旧paper报告、硬件skip不消费其他运行的失败。

专项64项通过（`fz-focused-233519.log`：观察/门禁21、SKU4、引脚1、芯片38）。改进后两个SKU串行完整复跑：量产 `fz-massprod-233546-504518.log`、普通 `fz-234242-939829.log`，均 **FAIL**，observe均hard=3、block_done_claim=true；Wokwi仍未就绪，QEMU崩溃现在如实失败。两SKU产品host/协议/19项运动/引脚与缺陷注入通过，普通纸路专属检查也保留通过。此结果取代前文普通QEMU“既有实验性pass”的最终状态，不意味着新增产品崩溃回归。

已取得与本机安装tag一致的官方源码：<https://raw.githubusercontent.com/espressif/qemu/esp-develop-9.2.2-20260417/hw/xtensa/esp32.c>，访问2026-09-24，本地 `tmp/qemu-esp32-9.2.2-20260417.c`，SHA256 `dcbdb0c67e2398e73e0d17b2f51d0f300161829b7bffd1c34b3114b168846d3f`。analog/RMT等仍为未实现外设，适用classic ESP32；尚未完成故障地址到模型寄存器的充分因果验证，不将本故障写成已解决的模拟器限制。

本轮继续修复三张网页与小程序网络入口，详见 [网页量产复审](web-production-review-20260925.md)。本地软件回归不替代板上或生产运行证据。
