# 绑定、独立记忆与量产XY运动复审（2026-09-25）

用户要求继续三仓检查，重点绑定、独立记忆和运动丝滑度，明确异响出现在XY画线/快速移动。量产交付仅 `massprod/nopaper-1.3a`；保留全部限幅、几何容差、Z约定和绑定所有权闸。

本轮起点：hub e2e3e6f、S3 48cabce（板上1.0.22）、量产Grbl 0e3cf1a；hub原用户deployment前部修改保留，两个交付固件树无已跟踪改动。板上前轮读取XY4500mm/min、Z1200、XYZ加速度3000mm/s²、junction deviation 0.01mm。XY75mm/s上限会限制F24000，不能将文件F视作实际速度。

待验证修复：绑定探活HTTP200错误/错设备被当在线；云端记忆冒号转下划线造成不同标识共桶、筛选召回绕过预算；S3中文按字节截断和不完整JSON接受；量产Grbl未同步普通分支现有的WiFi省电关闭设置。源码差异不等于已确定异响根因，需量化喂流和用户听感对照；不借机改运动内核或提高几何容差。

验证顺序：先失败回归→精确修复→各层测试与构建/门禁→窄范围实机。噪声没有声学测量设备，不能用状态Idle或没有ALARM冒充静音/无失步证据。fz既有Wokwi/QEMU启动失败继续登记，不能靠扩大豁免消除。

## 已复现与修复

- 绑定探活原先将 HTTP 200 当作在线；现在解析真实工具状态、拒绝错误信封，v2 签名绑定核对返回的完整设备 MAC。S3 在线而 Grbl 离线仍允许绑定。失败返回 pending，不消费扫码证明；真实 probe 集成回归验证原证明重试成功。
- 云端非 MAC 标识原来用冒号转下划线作为路径，`device:a`/`device_a`、MAC/下划线串会共桶；Windows 大小写路径也会碰撞。非 MAC 改用原标识 SHA-256，MAC 规范为小写并保留原小写 MAC 文件路径。旧非 MAC 歧义桶和非规范大写 MAC 文件保留、不自动读取迁移；需要核实归属后人工处理，不能把混存内容自动分给任一设备。分桶不构成对模型传入 MAC 的身份认证。
- 筛选记忆原先绕过全量回忆的条数和字符预算；现统一最近优先预算，并返回 total/returned。
- S3 记忆原先按字节截断中文，合法长中文变坏 UTF-8；现按 Unicode 码点限 key 50/value 500，非法 UTF-8 拒绝整项。JSON 恢复正确处理 Unicode 转义/代理对，拒绝尾随内容、尾随逗号、非法转义和裸控制字节；重复主题最后一次覆盖，最多保留80条。筛选回忆也遵守40条/4000码点预算，NVS仍按3500字节压缩逐出。
- S3 记忆原先借通用 Settings 写入，NVS 写入/提交错误触发 ESP_ERROR_CHECK 重启。记忆层现独立检查返回值、关闭句柄，读取失败不写空记忆，提交成功才返回成功，无变化不重复写闪存；不改全局 Settings。
- 量产 Grbl 漏掉普通仓既有 WiFi PS_NONE 策略，已在 WiFi.begin 后、关联前补齐，设置失败有告警。本轮只改网络策略，未改规划器/步进/行程/换纸。

## 验证与现役边界

软件：绑定针对性69项通过；Portal全套465项通过；MCP全套1588项通过、e2e PASS；S3 host230项通过，其中记忆真实源码编译注入覆盖打开/两阶段读取/写入/提交失败；量产Grbl host15项通过。中文新增用例先红后绿，云端隔离新增用例先4失败/1通过后全过。新增测试计数与文档行号指纹已同步。

量产 Grbl 镜像1844096B，SHA `bfd573e2c16b8d32d504c270ffdc03843e34ca90c0c508f41b49a4922bb7a2fb`，COM7核MAC `4c:c3:82:cc:8a:9c`。用户要求停止备份后立即停止，直接仅写应用0x10000；写入及独立verify通过，刷前未复位，刷后按用户追加许可复位。启动读回仍1.3a.20260910、原点Idle、原NVS参数保留。`grbl-flash/result.json` 明确标记备份未完成，不得把部分读取算完整备份。

fz量产standard已重新执行：协议、硬件层通过，但Wokwi就绪超时和QEMU启动失败仍在，overall FAIL。属于既有芯片仿真缺口，用户明确授权板上网络/运动诊断；不宣称完整门禁放行。hub full误用量产路径时会使普通纸路仓的历史行号/AGENTS身份不匹配，保留失败报告；按引用实际目标普通仓复验，随后修正文档行号漂移。量产实际镜像来源由独立构建、fz产品档与板上摘要约束。

运动诊断均为抬笔Z0、完整文件过X/Y≤60mm额外包线，通过既有S3下载/确认/收尾链路。4500下727行短段圆出现中途Idle，19行等价圆弧没有采样到中途Idle；6000圆弧正常完成归位，用户反馈原文「提速后感觉还有点静的感觉」，暂按略安静记录，非声学认证。6000短段仍有多次中途Idle，不能宣称WiFi修复已解决异响。XY已逐轴读回6000，Z仍1200，加速度仍3000；新增S3完成时耗时汇总用于区分发送/收应答/UI入队，既有512B窗口和逐行F契约不变。

本机证据根 `results/20260925-binding-memory-motion/`：软件日志、fz失败摘要、`grbl-flash/`、各组轨迹/双串口原始采样/analysis。S3构建证据在 `results/20260924-review-fixes/s3-builds/hutuji.1.0.23/`；构建与刷后结果以最新manifest及后续条目为准，不把首个候选当成最终板上版本。

## 继续定位短线段卡顿

1.0.23最终镜像为3077280B、SHA `2fbbdbe6ed2626fe085510d4ccd1d4895d949534fb6382a80e8951eb5b91cc40`，已刷COM15并核版本/指纹。6000短段对照：`segments-6000-012522`采到83个圆中途Idle点，首末Run跨度15.209s，发送累计12280ms/单次1990ms，收应答累计1690ms/单次130ms，UI入队最大0ms。取消云端状态轮询的`segments-6000-012934-quiet`仍有137个中途Idle点，Run跨度22.603s，发送19650ms/单次2460ms。quiet脚本人为等30秒，确认到done的外部耗时不能当作运动耗时。

1.0.24仅加慢发送分项诊断，发送策略不变。量产板真实215个CXX编译、host230项通过，镜像3077696B、SHA `c93aa5f1bf3c9f4f1fb9f109e22c5466b6b0c6c1bcf7f0bbe79def704d35956d`；COM15写入哈希及启动1.0.24/Grbl指纹复验通过。`segments-6000-014235-quiet`仍有38个中途Idle采样点、Run跨度9.492s。三次慢发送1060/1310/1220ms，socket锁等待均0、无慢写锁日志；send单次调用最大30ms、EAGAIN重试60/70/67次，feedhold让步0。直接原因已证实为TCP背压导致短段供给停住；底层为何背压仍需队列快照，不能宣称已解决。各次波动明显，禁止仅按一组较快结果认定优化有效。Grbl只读网络诊断signal94%约-53dBm，单次信号值不能排除丢包或干扰。

本地官方SDK依据（访问2026-09-25）：[esp-idf](https://github.com/espressif/esp-idf/tree/8c19b156084a0753687347cca1f5355782893533)本地HEAD `8c19b156084a0753687347cca1f5355782893533`，lwIP子模块[esp-lwip](https://github.com/espressif/esp-lwip/tree/fd432e4ee2cfb7f7f1c7eb7227e0173412e7b84e)为`fd432e4ee2cfb7f7f1c7eb7227e0173412e7b84e`。范围仅本机ESP32-S3/IDF v6.0.1构建，不代表Grbl旧SDK。`git rev-parse HEAD`及读取`api_msg.c:lwip_netconn_do_writemore`复核：非阻塞tcp_write遇ERR_MEM返回ERR_WOULDBLOCK；`opt.h`默认TCP_SND_QUEUELEN按发送字节预算/MSS推导，现5760/1440对应16；`lwipopts.h`设LWIP_NETIF_TX_SINGLE_PBUF=1且`tcp_out.c:tcp_pbuf_prealloc`在此分支按max_length分配。上述只能支持候选机制，源码推论不等于板上耗尽或重传实证，未改SDK或缓冲参数。

绑定/记忆产品提交：hub `f7463e3d1e6a3bfcea44aa0b7e000a3d9dfdd114`已先京东后阿里部署三文件并逐项verify，回滚备份ID分别`9f1fca4a80e546a0a654c207701c342b`、`be100482aee04211a38bb2f4b29f8831`。生产probe正确设备通过、错MAC拒绝；真实设备记忆召回合法JSON且1条，仅核结构未输出正文。S3记忆和首版流耗时提交`cf39af5`，量产Grbl网络提交`e87e97e`。hub full已PASS；fz仿真缺口仍按上文记录。

### Grbl NODELAY初始化顺序缺陷

量产`Telnet_Server::begin()`此前先`setNoDelay(true)`再`WiFiServer::begin()`。实际PIO构建日志确定依赖Arduino ESP32 **1.0.4**（包`framework-arduinoespressif32@3.10004.210126`）；本地该包`libraries/WiFi/src/WiFiServer.cpp`显示`begin()`末尾无条件`_noDelay=false`，`available()`把该值传给新连接TCP_NODELAY。因此原代码的关闭Nagle意图未生效，不能仅凭出现`setNoDelay(true)`就认为已生效。

官方来源：[arduino-esp32 1.0.4 WiFiServer.cpp](https://github.com/espressif/arduino-esp32/blob/1.0.4/libraries/WiFi/src/WiFiServer.cpp)，访问2026-09-25，依据为本地官方包快照与`package.json`/PIO依赖版本复核；仅适用于当前Grbl旧Arduino SDK，不能外推到S3 IDF sockets路径。真实服务初始化函数编译回归模拟SDK启动清零/新连接继承语义，旧源码在`available().no_delay`断言失败，移动设置到`begin()`之后通过；覆盖首次启动、重开与服务禁用。改动仅量产Telnet初始化顺序，运动/换纸/行程/参数不变；真实卡顿改善仍须刷后对照，源码缺陷成立不等于它解释全部秒级停顿。证据`nodelay-{red,green}.log`、`grbl-build.log`、`grbl-host.log`（16项通过）。

1.0.25加入TCP/IP线程内队列快照（仅诊断，无SDK写入），SHA `603dc51c18afc7ad210d85861bb6518e073a1838d5859a20ad20f0b4d83e872b`、3077984B；Grbl NODELAY修正版SHA `4e0b13b807216fe8f7d4f89328a17deb0f11885ce1122ed9c79d43b04e4e53db`、1844096B已在用户再次手动BOOT后直接刷入，独立verify与刷后复位通过，原6000/6000/1200及加速度/行程读回不变。`segments-6000-015736-quiet`仍有69个中途Idle采样点，Run跨度14.9s。8次慢发送快照均`queue=16/16 unacked=16 unsent=0 sndbuf=5344`，对端窗口4470–5744B，内部堆约23–25KiB/最大连续9216B。由此确认发送段数耗尽，并排除这些快照中的字节发送缓冲耗尽/对端零窗口；`nrtx=0`只说明采样时尚无当前RTO重传，不能据此断言无丢包，也不能反过来宣布已证明重传根因。1.0.26候选增加8条在途上限作单变量验证，保留512B上限/逐行发送/错误与停止链，不把8称为官方推荐最优值。

### 外部参考补充与适用边界（2026-09-25在线复核）

用户要求不能靠猜。已用Context7检索官方文档并直接下载原文，摘要保存在`reference-context7.json`，下载URL/访问日/文件SHA登记于`references/manifest.json`。GitHub搜索连接器鉴权失败，因此没有引用任何未成功读取的社区issue。固定版本/提交原文优先于master搜索摘要。

- [Grbl v1.1 Interface](https://github.com/gnea/grbl/wiki/Grbl-v1.1-Interface)，2026-09-25的wiki快照SHA `7906361953242e7ec7584f36dd1fbf2d1c638492f847bdc8a9ae8ee10216f475`，说明短段/高速度/通信延迟导致planner starvation和start-stop motion，并解释字符计数FIFO。此文是AVR上游，不是本地ESP32量产fork；不照搬其128B/16规划块常量、不引入文中的G4同步建议，不替代本仓512B/fresh Idle契约。
- [lwIP 2.1.x TCP raw API](https://www.nongnu.org/lwip/2_1_x/group__tcp__raw.html)，下载SHA `794dc0afb4732f6f0f09dd3f6418b1193c34f9d3600451e0b1a60ce4078744f8`；tcp_write明确同时受字节预算和发送段队列上限限制，ERR_MEM后等待对端收到已有数据再重试。上游机制与本地esp-lwip `tcp_out.c`/`api_msg.c`交叉复核一致；应用实际用socket API，不直接从任务调用raw TCP函数。
- [ESP-IDF固定提交WiFi缓冲说明](https://github.com/espressif/esp-idf/blob/8c19b156084a0753687347cca1f5355782893533/docs/en/api-guides/wifi-driver/overview.rst)，在线SHA `974bb85ba30ecdf5027750d9e747d9a191ea2b380511d4707e9933c4216b6535`；官方不建议AMPDU开启时静态RX少于6，本机为3、动态RX为6，低于建议的静态数量。`esp_wifi/Kconfig`亦建议静态RX至少达到RX_BA_WIN以改善突发兼容性。这里只登记潜在ACK接收风险；尚无射频抓包证明丢ACK，不能把该推荐当作已确认根因，不能无内存预算依据直接调大全部缓存。
- 在线Arduino1.0.4原文SHA `6a925b747f9493b1b446a57234547f6d361a70053299365d7c7b6eba89952c1d`，再次核对begin清零及available继承，与前述本地PIO SDK结论一致。

## 最终交付与复验（2026-09-25）

S3源码提交`83ed8a7a8bddcce7e8ccb007227b583130f1476d`，版本**hutuji.1.0.27**，量产Freenove镜像**3077712B**、SHA **0939e802c0ba9c1b667bebc7d1bffc7d0731f6e633e2ea1d276b8b61d7b1f472**。已移除临时lwIP私有PCB遍历，仅保留超过250ms的慢发送分项记录；最终源通过231项host回归，量产板真实编译215个CXX，COM15刷写两段哈希及启动版本/指纹通过。Grbl源码提交`14c83080d7a1188be994df24930d57deac2216e7`，量产镜像与前述`4e0b13b8…e53db`一致，16项host回归/PIO构建/COM7写入与独立verify/参数读回通过。两者均只更新指定量产设备；S3共享代码在其他板型的本轮镜像未重新构建或刷验。固件镜像在提交前从相同源码构建，板上以版本和完整SHA辨识，不以编译时旧Git基线字段冒充新提交号。

同一727行/18812B/Z0轨迹，XY=6000、Z=1200及加速度保持：

| 组别 | 首末Run跨度（含起步、归位） | 圆中途Idle采样数 | 最长SendLine |
| --- | --- | --- | --- |
| 1.0.25，只有512B预算 | 14.900s | 69 | 1440ms |
| 1.0.26，512B+8条，第1轮 | 4.674s | 0 | 40ms |
| 1.0.26，第2轮 | 5.073s | 0 | 40ms |
| 最终1.0.27 | 4.757s | 0 | 40ms |

对应`segments-6000-{015736,020737,020917,022143}-quiet`。最终版仅被动监听PC串口且运行中不调用云端status的`passive-6000-021825`，板上灌流4040ms、发送累计620ms/最长40ms，无慢发送告警并完成归位；该组没有高频运动状态采样，不将其写成“未出现任何短暂停顿”。`control-6000-021944`以7207行/187292B、同60mm抬笔包线验证Run中暂停、Hold后两次位置稳定、恢复Run、连续两次停止（第二次明确“停止处理中”），最终aborted/原点且仅一次$MD。`repeat-6000-022034`验证停止后的新任务与重画，两次done/原点、各一次$MD。`final-control-summary.json`核定释放次数。用户实机反馈原文：**“明显改善，基本连贯”**。

结果限定：本次复现的秒级发送背压已通过在途条数限制消除；根因链证据止于TCP发送段队列满→发送停住→Grbl在短段中途Idle，ACK为何未及时到达未做射频/TCP抓包定因。8条是本机验证有效的预算，不称通用最优值。WiFi静态RX=3、动态RX=6、RX_BA_WIN=3；静态数满足该BA_WIN但低于官方AMPDU通用建议6，仍是待评估项，本轮未盲加内存预算。没有声学测量/编码器/长时老化，不宣布静音、无失步或整机量产放行。

旁路诊断边界：`segments-6000-020702-quiet`在首次速度只读阶段失败，未发送轨迹；日志显示PC主动实时查询期间`$112=1200.000`与`<Idle...>`出现在同一行，S3未确认设置并重建连接。只读复验恢复后再开任务。最终控制/被动对照已改为PC串口只监听，保留该失败样本；多来源诊断输出原子性仍待独立修复，不把该样本算成功运动。

Grbl最终修改后fz standard再次完整运行249s，协议/硬件等层通过，但Wokwi就绪超时与QEMU PHY启动失败仍在、overall FAIL，证据`fz.log`及`fz-nodelay-reports/`，不扩大豁免。hub此前full通过后，本轮按改动范围运行S3/Grbl回归与文档门禁；43处纯移动行号及4处人工语义锚点已核对，最终263处指纹通过。云端绑定/记忆本轮没有重复部署，沿用f7463e3的双机上线结果。
