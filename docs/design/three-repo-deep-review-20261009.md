# 三仓深度审查（2026-10-09）

用户要求继续深度code review。审查起点：hub `939d9fa`、S3 `00fd3ee`、量产Grbl `548c669`（`massprod/nopaper-1.3a`）；三仓受跟踪文件起点无改动。量产Grbl无独立AGENTS，遵循枢纽与普通Grbl入口，不引入普通分支换纸模块。

本批只审查与复现，产品代码保持不变；不连接实机、不部署或刷机。使用真实C++函数与真实Portal路由，替换时钟、网络、RTOS等外部依赖。临时探针在 `tmp/trirepo-deep-review-20261009/`，输出在 `results/local-only/2026-10-09-trirepo-deep-review/`，均为本机证据。

## 已确认问题

### DR09-01 / P1：归位任务在连接或reset代次变化后仍发送运动命令

位置：S3 `main/boards/lichuang-dev/hutuji_job.cc` 的 `ReturnHomeAfterDraw`（抬笔、100ms沉降、归位）和 `PerformAbortDrainHome`（停止排空归位）；发送落到 `hutuji_pipe.cc` 的通用 `SendLine`。

两个路径都在抬笔确认后等待100ms，再调用不带连接/banner代次的 `SendLine("G1G90 X0Y0F8000")`。等待期间若写字机重连或出现reset banner，S3已经把ready/authorized/settings校验置为false，但 `SendLine` 只检查connected，最终 `SendRawLocked` 只要求socket有效，旧任务仍可向新会话写出归位命令。后续 `WaitForIdle` 或电机释放检查报错发生在发送之后，不能撤回已经发出的运动。

复现使用真实两条归位函数、真实 `WaitForIdle`、真实 `SendLine/SendLineLocked` 与既有 `SendLineForSession`，仅替换RTOS延时/收发。按真实reset处理同步清除ready/authorized/settings，在100ms沉降点注入连接代次变化或同连接banner变化：**两条路径均写出1条失效后的XY归位命令**，最终正常归位返回false，停止归位的完成标志为false；未变化正常对照完成，持续断开对照不发送。相同条件调用既有 `SendLineForSession` 拒绝发送。

影响：停止或收尾动作可能越过已经失效的机器会话，涉及实际运动。证明的是软件写出命令，不宣称实机已经发生位移或撞机。建议整个归位事务固定连接+banner身份，各发送点在Pipe写锁内核对；等待、错误收口也沿用该身份，不能每一步重新接受当前连接。

### DR09-02 / P1：256行Web命令使处理循环无法终止

位置：量产Grbl `Grbl_Esp32/src/WebUI/WebServer.cpp` 的 `_handle_web_command`，拆行循环使用 `uint8_t sindex`。

请求有至少256个连续非空行时，处理索引255后 `sindex++` 回绕到0，再从第一行重复。输入队列满时 `Serial2Socket.push` 返回false只置 `hasError`，不能退出循环。该HTTP handler由 `Serial.cpp` 的 `clientCheckTask` 同步执行，卡在其中时不再轮询Telnet/串口的实时指令；目前还持有WebSocket生命周期锁，不能指望别的日志路径解开它。

真实handler与 `get_Splited_Value` 宿主复现：1行/255行均正常返回；256行/257行在**第513次push仍没有HTTP响应**，由探针预算主动截停。使用的是逐行`?`，不包含运动；128次后让替身push返回false，仍不断重入，证明并非依赖输入缓冲无限接收。量产配置开启HTTP与Serial2Socket，认证宏关闭；这是本地Web入口风险，不能说S3常规Telnet灌流必然触发。

建议把索引改为不回绕的长度类型，同时限定请求总字节/行数，满队列拒绝或停止消费；重构拆行时保持实时字符语义。未在板上发送该请求，未把潜在看门狗重启写成实测事实。

### DR09-03 / P2：超长Web ESP命令缺少NUL终止符

位置：同一 `_handle_web_command` 的 `[ESP...]` 分支：`char line[256]` 后仅 `strncpy(line, cmd.c_str(), 255)`，没有补终止符，也没有拒绝长度超限。

当trim后命令至少255字节，255个位置全被复制，最后1字节未初始化；下游 `ProcessSettings.cpp` 的 `system_execute_line` 使用 `strrchr/strchr` 等C字符串扫描，可越过本地数组边界。

真实HTTP handler以GCC `-ftrivial-auto-var-init=pattern` 为未初始化栈槽填非零值，仅在进入解析器的边界检查256字节内是否存在NUL：20/254字节有终止符，**255/300字节都没有**。此编译选项只是确定性模拟栈残留；没有改写被审查函数。探针未执行实际越界读取，不声称已复现实机崩溃或信息泄露。

建议先按字节数拒绝超长ESP命令，且拷贝后显式NUL；只补NUL会把本来不同的长命令静默截断，仍需明确拒收策略。

### DR09-04 / P2：损坏的OTA成功HTTP回包被当作明确受理，随后误消费旧失败

位置：Portal `draw_portal/firmware_ota.py` 的 `_start_upgrade_claimed`，与 `push.py` / 共享 `tool_result.py` 的逐层判错衔接。

`push_tool_call` 只认明确业务失败，HTTP200的半截JSON、空正文或HTML正文可作为 `ok=True` 返回；OTA解析失败回落 `{}`，最终仍返回 `started`，会话进入 `upgrading`。下一次状态查询若仍是设备上一轮的failed，它就直接终结当前会话、解除忙闸，允许再次发升级命令。此前针对 `IncompleteRead` 的修复不能覆盖“HTTP读取完成、正文结构却损坏”的情形。

复现穿过真实注册/绑定/升级/状态路由，只替换设备HTTP响应：上述3类正文均得到 `started → failed(download_failed)`，再次提交后 **ota_start共下发2次**；明确传输截断 `IncompleteRead` 对照得到 `unknown → upgrading`，再次请求busy，只有1次下发。

建议对OTA受理结果做明确形状校验；无法判断送达或受理时保持uncertain，不允许未知正文变成明确成功，也不应直接当明确拒绝释放会话。继续保留旧失败隔离与版本核对。

### DR09-05 / P2：调速预检与写入之间重连，旧请求仍改新会话参数

位置：S3 `hutuji_job.cc` 的 `RunSpeedUpdate` / `exchange`。它先调用 `same_session()`，然后使用通用 `pipe.SendLine(line)`；两者间没有原子会话校验，也没绑定reset banner。

注入发生在取得Pipe写锁前，真实 `RunSpeedUpdate`、`WriteMachineSpeed` 与 `SendLine` 共同执行：正常对照把XY改到7000并正确读回；换连接或同连接reset对照均最终报 `error`，但已经向变化后的会话写出 `$110=7000`，X=7000、Y仍=10000。状态报错没有阻止首轴设置生效。这里与DR09-01共用底层缺口，但调速有独立消费者及持久参数影响。

建议为调速事务捕获连接+banner，并使用写锁内校验的会话发送接口；不能只增加发送后的同会话检查。重连仍按失败处理，不自动把半个XY事务补写到新会话。

## 验证与证据

| 探针 | 场景 | 结果文件 |
|---|---:|---|
| 真实S3两条归位函数及Pipe发送/等待 | 8 | `s3-home-session.json` |
| 真实S3调速函数及速度核心 | 3 | `s3-speed-session.json` |
| 真实Grbl Web handler/拆行 | 8 | `grbl-web-input.json` |
| 真实Portal路由与HTTP替身 | 4 | `portal-ota-malformed.json` |

合计23个正常/故障对照场景，当前源码的上述问题均可重复观察。运行命令（在hub根目录，用现有MCP venv的Python）：

```powershell
& mcp-server/.venv/Scripts/python.exe tmp/trirepo-deep-review-20261009/probe_firmware.py
& mcp-server/.venv/Scripts/python.exe tmp/trirepo-deep-review-20261009/probe_speed.py
& mcp-server/.venv/Scripts/python.exe tmp/trirepo-deep-review-20261009/probe_portal.py
```

宿主编译器MinGW GCC16.1.0；探针直接提取当前函数，未把函数重写成“等价实现”。生成的C++、编译日志、逐场景JSON、独立Portal临时数据库均保存在本批证据目录。源码SHA和23场景计数另由`review-verification.json`固定，便于修复前复核。

审查范围还包含：确认/停止/重画占位、OTA占位与代次CAS、共享判错、预览/调速任务、纸张快照、WebSocket生命周期及网络客户端调用链。没有把最初“Web生命周期锁导致所有阻塞”的疑点单列为新bug：HTTP本来就在同一个clientCheckTask执行，需区分既有同步结构与本次已证实的无限循环。`RequestConfirm`的50ms释放窗口仍保持busy且abort由自身收口，没有证据支持它允许另一预览直接抢入。本轮未把这两个未成立疑点混入清单。

## 边界与建议顺序

优先DR09-01和DR09-02，再处理DR09-03/04/05；DR09-01与05宜共同核对发送入口，但各自保留行为回归。只审查和复现，不改产品代码；未执行固件构建、生产部署、刷机或设备注入，也没有重跑无改动的全套服务测试。Wokwi仍按用户既定选择保留quota未验，0.3.26手机分享仍按用户反馈待验证；均不是本次新发现。

## 用户授权后的完整修复（软件验证完成）

用户明确「开始完全修复」，授权本节对应5项代码与回归、构建和门禁。上文未修复是审查快照，以本节完成记录为准。外仓代码先在hub暂存，核对原SHA后应用，保留无关改动。

设计：归位和调速固定连接/banner身份，发送时在Pipe写锁内校验；归位的fresh Idle等待也使用同一身份，失败只拆原连接，暂停/停止不改动作和速度。Web同步请求上限4096字节、256行（约束解析工作量，不扩展256B接收队列），超限整单拒绝，队列满即停止追加；ESP命令允许至255字节并显式NUL，256及以上拒绝。OTA仅明确`ok:true`设备结果视为受理，其余不明正文保留uncertain，与现有逐层判错/版本对账衔接。

正式回归从原23个对照场景扩展至首条发送、沉降、等待、写锁、旧应答和输入边界。完成后运行S3与Grbl全host、两固件真实构建、Portal/MCP全量测试、枢纽full和量产fz standard。Wokwi配额按既定决定保留未验；本节不启动生产更新或实机动作。

### 已应用修复

- **DR09-01/05**：正常归位、停止归位的普通命令复用`SendLineForSession`；新增`WaitForIdleForSession`接受调用方固定的连接与banner，旧两参入口仍保留原行为。停止退出Hold使用只发送`~`的`SendResumeForSession`，同一写锁内核对身份；超时/拒绝只关闭原连接，正常归位遇到用户abort不抢走停止worker的连接。调速在受理时保存`speed_banner_seq_`，所有轴写入和读回均用固定身份发送，连接变化不补写半个XY事务。
- **DR09-02/03**：Web入口4KiB字节预算与256行预检，超限413且零入队；拆行索引不再使用uint8，队列满即返回既有Error语义。ESP命令256字节起拒收，合法255字节连NUL一起复制，不静默截断。保留既有空行与实时命令语义，不改WebSocket生命周期锁、运动、换纸、引脚或授权。
- **DR09-04**：有界解包设备受理正文，只有严格布尔`ok:true`才进入明确upgrading；空/损坏JSON、HTML、标量、缺确认及非布尔ok保留uncertain。reason/detail异常类型不会抛错释放占位，旧failed继续隔离，后续设备开始或版本相符才对账；明确拒绝仍由既有逐层判错处理。

### 软件与构建结果

原代码52项正式回归有37失败、15通过（S3 19失败、Grbl7失败、Portal11失败）。应用修复后新增55项全部通过，其中3项为新安全恢复接口的成功/重连/复位对照；没有通过删断言或放宽安全阈值做绿。

| 检查 | 结果 |
|---|---|
| S3完整host | 316/316，72.10秒 |
| 量产Grbl完整host | 45/45，7.36秒 |
| Portal完整pytest | 510/510，111.60秒；1条既有Starlette/httpx弃用警告 |
| S3 Freenove / ESP-IDF6.0.1构建 | 成功，app3080784B，分区余量25% |
| Grbl PlatformIO release | 成功，23.09秒，bin1846832B；RAM26.3%、Flash93.9% |

固件SHA：S3 `36b78b8f8576db21afed23298a3d2c9206e52e6582595b3622ba4451a1c11ffa`；Grbl `35aa138f301d603a89652bfb4ee856baa3f05e061d2fec3a6e27d42d752bbc0d`。13份源码SHA与候选复制保存在`results/local-only/2026-10-09-trirepo-deep-fixes/fix-verification.json`及`*-candidate.bin`；构建与测试日志在同目录。S3版本常量仍是1.0.34，候选仅用于本地验证，没有替换已发布同号镜像。

格式检查只处理4份S3文件的本批差异行。外仓应用前逐文件核对原SHA；完整宿主回归覆盖既有恢复与电机释放契约。引用同步自动重定位27处纯行号移动；另人工核对2处空锚点，重画IsReady改指当前判断，历史播报等待表原误指预取注释，已改指真实函数入口并保留历史实测口径。未盲目追认失效引用。

### 最终门禁与交付边界

枢纽full **13层PASS / exit0**，含MCP **1640 passed（159.16秒）**、`e2e_local PASS`与266处行号指纹。量产fz standard **overall_status=pass / exit0**（166.21秒），报告绑定`massprod/nopaper-1.3a`、基线`548c669`及`dirty=true`候选。fz代码无修改；普通Grbl既有`.gitignore`改动保留。枢纽full的§9源码标记仍按既定普通Grbl入口检查，量产实际源码另由其真实构建、45项host及fz standard覆盖。

Wokwi本轮由standard调用后返回 **TLS连接失败**，`wokwi_startup fail / blocking=false`；此前quota是背景限制，本轮不把连接失败误记成新配额结论，也不把非阻断PASS说成Wokwi启动成功。未进行实机复位、断网、运动或纸面验证。

两份门禁原始JSON/Markdown另存 `hub-hutuji_gate_last.*` 与 `fz-agent_gate_last.*`，日志为`hub-all.log/fz-all.log`。门禁后再核对13份源码SHA和两镜像SHA保持一致，S3差异行clang-format dry-run通过。状态页回填后再跑docs门禁，避免让更新后的台账沿用旧检查结果。

**5项源码修复与本地验证已完成，尚未提交、部署或刷机。** 本批没有改小程序，无需为本修复上传新小程序版本；现役S3/Grbl仍是之前已发布的基线。上文“尚未修复”为审查历史，不能用于判断当前源码；源文件及候选身份以`fix-verification.json`为准。

## 发布与实机验证续作

用户点名处理未提交、部署、刷机及验证，授权本批三仓提交推送、Portal双机部署、S3升1.0.35及量产Grbl升级。先保存当前镜像、目录和参数，确认MAC/板型/空闲状态，再执行单次升级并核对回执；结果不明先查原状态，不重发。

实机范围：量产Web输入边界、升级与参数保持、S3正常预览/收尾和调速读回、可控失败后的恢复。100ms或写锁内的精确竞态主要由已完成的真实函数注入证明，现场证据按实际覆盖范围登记，不以一般冒烟代替精确窗口注入。Wokwi重新尝试仍独立登记，不影响其他已授权工作。

证据目录 `results/local-only/2026-10-09-trirepo-deep-release/`，发布脚本暂存于 `tmp/trirepo-deep-release-20261009/`。以下回填覆盖上文未提交/未部署状态。
