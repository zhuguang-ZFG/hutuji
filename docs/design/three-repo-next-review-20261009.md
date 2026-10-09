# 三仓发布后续审（2026-10-09）

用户「继续改进 codereview」。审查基线：hub `4b9f3fe`、S3 `220048d`（1.0.35）、量产Grbl `9f860f5`（`massprod/nopaper-1.3a`）。三仓受跟踪文件开始时无改动。本轮只读产品源码，编译真实函数与运行本地Portal路由故障注入；未连接设备、部署、刷机或修改产品代码。

## 已确认问题

### NR09-01 / P1：首笔校准与试笔仍可跨失效会话发送

位置：S3 `hutuji_job.cc` 的 `PreparePenOrigin`、`RequestPenTest`；最终进入 `hutuji_pipe.cc` 的通用`SendLine`。上一批修复的是归位和调速消费者，这两个入口仍先检查连接/ready，再调用未绑定身份的发送。

`PreparePenOrigin`等到fresh Idle和100ms沉降后，检查连接代次，随后调用`SendLine("G92 Z0")`。检查与取得Pipe写锁之间发生重连或reset时，通用SendLine仍把校准写入新会话；它没有在锁内重新检查连接/banner/授权/设置。试笔同样在预检后用两个通用SendLine发送Z5/Z0，前一条等待写锁期间失效，后两条仍可写出。最终返回error发生在命令发送之后。

7个真实函数场景：校准正常1条/成功；写锁前重连与reset各写出1条失效后的G92，最终返回false；应答等待期间reset并收到迟到ok时，校准返回true，尽管ready已false；试笔正常3条/完成；Z5写锁前重连与reset各写出2条失效后的Z运动，最终error。探针直接提取当前完整校准、试笔、Idle等待和Pipe发送函数；只替换RTOS调度、时钟、外部收发及写锁调度钩子，重连/reset同时清ready/authorized/settings。

风险：旧任务改变新会话Z基准或在新会话执行试笔动作。应答成功后缺少会话复核还会误报校准通过；本轮未把它夸大成完整出图绕过所有后续闸门，也未实际驱动笔头。建议固定整个笔控事务的连接/banner身份，使用锁内身份校验发送，并在消费应答和等待完成后复核同一身份；覆盖普通出图、重画和试笔消费者，保留原Z值/速度/时序。

### NR09-02 / P1：Grbl超长行被拒后仍执行同一行的后半段

位置：量产`Protocol.cpp`的`add_char_to_line`和`protocol_main_loop`内`Error::Overflow`分支。缓冲满时返回Overflow，主循环调用`empty_line(client)`立即清空后继续读，却没有“丢弃至本行换行”的状态。后续字符因此从新行开头重新累计。

真实`LINE_BUFFER_SIZE=256`。输入**256个A紧接`G1 X10 F100\n`**，没有中间换行：报告一次Overflow，但最终`execute_line`仍将尾部`G1 X10 F100`提交给`gc_execute_line`。后面再跟一条独立合法行时，两条都被提交。正常短行对照无错误；255字节后立即换行的对照报错且下一行独立执行，说明现有254字节契约没有改变。

4个场景编译当前真实`add_char_to_line`、`empty_line`、`execute_line`及主循环的完整客户端取字/分派段，仅在`gc_execute_line`/设置执行边界记录收到的命令，没有仿写溢出分支或执行真实运动。

风险入口为原始Telnet/串口输入。MCP生成校验器已限制254字节，正常云端产物有前置保护；Web入口的256B入队缓冲也不能直接等同于原始Telnet入口。因此不宣称当前合法云端出图都会触发。但固件对一条已拒绝输入继续执行其尾部，与契约§3/§7“该行从未执行”的口径矛盾。建议每个客户端独立锁存溢出并丢弃至CR/LF，完整拒绝该物理行，保持实时字符与下一独立行语义。该修复涉及协议解析，须明确限定授权意图后再改，不能顺便调整运动或换纸逻辑。

### NR09-03 / P1：绘图受理回包不明时仍自动确认旧预览

位置：Portal `push.py`的`push_tool_call`、`push_draw_to_device`、`push_draw_self_heal`与`auto_confirm_after_preview`。HTTP200且未识别到明确错误就被当成draw成功；`push_draw_to_device`立即丢掉原正文，随后自动确认只看设备是否处于awaiting_confirmation，无法证明是本次新预览。

真实推送链的外部HTTP替身保持设备旧预览为awaiting_confirmation；新draw返回空正文、HTML或截断JSON三种情形，均观察到调用序列`draw → status → confirm`且最终返回成功。相同返回也穿过真实注册/绑定/控制路由，被API标为`status=ok`。明确拒绝对照只调用draw并返回失败，明确previewing成功对照照常确认。

另测多编码一层的错误正文：`data=json.dumps(json.dumps({error:已知拒绝}))`被共享`tool_result._tool_result`在解出字符串时提前返回false，绕过拒绝判据并进入相同自动确认序列。此形状属于兼容解析缺口，**没有现网网关必然发送该形状的证据**，不另计为一个生产事故或独立P1。主要问题由空/坏正文三种对照独立成立，且上一批OTA明确受理修复没有覆盖draw消费者。

风险：本次draw没有可信受理结果时，自动confirm可能启动机器上保留的另一张预览。建议draw具备自己的明确受理形状判据；未知结果不得自动确认或重新发送，保留用户可见的不确定结果。自动确认与本次任务身份的关联需要同时设计，不能只在通用HTTP函数中统一“失败/成功”二分。当前测试证明调用链会发confirm，未向真实设备注入坏网关回包。

## 证据、覆盖与排除

| 探针 | 场景数 | 本机结果 |
|---|---:|---|
| S3首笔校准/试笔会话 | 7 | `s3-pen-session.json`及真实函数C++/编译日志 |
| Grbl超长物理行 | 4 | `grbl-overlong-line.json`及C++/编译日志 |
| Portal控制路由与自动确认 | 6 | `portal-unconfirmed-draw.json`及隔离临时数据库 |

合计17个正常/故障对照。脚本位于`tmp/trirepo-next-review-20261009/`，结果在`results/local-only/2026-10-09-trirepo-next-review/`。`review-verification.json`固定本次审查源码SHA、提交与场景数；产品文件无修改，故本轮没有重跑完整固件构建或服务全套测试。文档独立跑docs门禁。

运行：本仓MCP venv分别执行该暂存目录中的`probe_s3.py`、`probe_grbl.py`、`probe_portal.py`。C++使用现有MinGW GCC16.1.0，不访问实机。S3首次脚手架误沿用旧沉降钩子，使写锁钩子未触发；修正为纯时钟延时后再跑7项，产品函数未改。

已检查但未作为新结论：WebSocket日志大块写入的缓冲风险尚未找到当前生产可达的超1200字节调用来源；Telnet重连接管时的残留输入仍需完整接收与协议缓冲复现，不能凭清理缺席直接断言实机串行；多编码错误正文属于条件性兼容缺口。此前已完成的归位/调速修复、1.0.35发布与小路径HIL仍有效，本轮揭示的是相邻消费者和其他输入形状。

建议修复次序：NR09-01与NR09-03优先，NR09-02随后以窄解析修复处理；每项先把本次故障探针转为正式行为回归。当前三项**已复现、尚未修复**，未更改部署或设备状态。Wokwi配额、完整触屏及纸面画质仍沿用readiness的未验证边界。

## 用户授权后的修复（本地候选）

用户随后明确“开始修复改进”，本节覆盖上文待修状态。机读白名单`trirepo_next_fixes_20261009_authorized`限定S3笔控/确认入口与三板工具参数、Grbl超长行解析、共享判错/Portal推送及正式测试。外仓先在hub暂存并核对原SHA，再经目录写权限应用；不改运动参数、换纸逻辑或引脚，不部署/刷机。

- **NR09-01**：Run与试笔受理捕获`stream_banner_seq_`，恢复成功同步更新。首笔校准及试笔所有Z发送使用`SendLineForSession`，Idle等待固定同一连接/banner，应答后复核身份；试笔持有Pipe任务会话并在正常/失败/任务创建失败收尾释放。新`test_hutuji_pen_session.py`含10项真实函数回归：正常校准/试笔、首条/第二条写锁前重连/reset、迟到ok遇reset；旧源码9失败→候选10通过。
- **NR09-02**：`client_line_t`新增独立`discard_until_eol`；首次Overflow后丢弃至CR/LF，backspace不能解除，换行本身溢出不吞下一物理行；初始化复位时清除所有客户端拒收状态。正式`test_protocol_overflow.py`9项覆盖长尾、下一行、255字节+换行、CR/CRLF、backspace、三倍长度、254字节合法边界、客户端隔离和reset。旧源码4失败→候选9通过。实时字节处理仍在Serial，未改阈值或执行器。
- **NR09-03**：draw只接受明确`previewing`；未知受理直接返回可见提示且零后续confirm/重发。共享判错继续解开编码字符串中的对象。自动确认的状态必须可解析，confirm只接受`started`，不回显坏正文。S3新增`RequestConfirmForPreview`，在原确认锁内核对两个作品地址；三板MCP confirm都透传可选参数，状态声明`confirm_by_url=true`。门户给自动确认传本次地址，旧设备无能力声明时保留预览、要求人工确认。原无参屏幕/语音入口保留；已有纸张/秒确认/取消宿主回归补同作品成功、异作品/缺一地址拒绝且不消费预览断言。

Portal新`test_draw_acceptance.py`23项覆盖10种未知/拒绝正文、4种合法受理信封、能力声明/精确参数、真实绑定控制路由的多编码拒绝、损坏状态、预览替换后不重试及未知confirm不泄露正文；MCP新增3项多编码拒绝。首轮新增Portal13+MCP3在旧代码全部失败，修复及边界扩展通过。初次Portal候选缺少模块级json导入已修正，不把调试失败冒充通过证据。

本批不升级版本号：S3候选仍标1.0.35，只保存在本地，禁止覆盖已发布同号镜像。未来发布须另升版本，先设备后Portal；三板注册一致但真实SDK只构建量产Freenove，不能称其他两板构建/实机通过。证据位于`results/local-only/2026-10-09-trirepo-next-fixes/`，暂存/执行脚本在`tmp/trirepo-next-fixes-20261009/`；审查前失败证据保留。

### 最终软件验证与候选身份

| 验证 | 结果 |
|---|---|
| S3完整host | 326项通过，84.19秒；差异行格式化后笔控10项及纸张/确认5项再次通过 |
| 量产Grbl完整host | 54项通过，8.78秒；fz内复跑54项通过 |
| Portal完整pytest | 最终533项通过，125.73秒；1条既有Starlette/httpx弃用警告 |
| MCP/e2e | 1643项通过，202.39秒；e2e PASS；计数总和与47个文件一致 |
| 枢纽full | 13层PASS，外仓行号与263处指纹通过 |
| 量产fz standard | PASS，180.9秒；Grbl基线9f860f5、dirty=true为本批候选，fz e36c125未改 |
| S3真实构建 | 量产Freenove、IDF6.0.1成功，3082240B，应用分区余量25% |
| Grbl真实构建 | PlatformIO release成功，28.35秒，1846944B |

S3候选SHA-256为`3e5bbb2e63544e529c8a18ccf928f02cb4e483824b3eb3022ff615461b97c84a`，Grbl为`20cdd471e132229f1ce7de7c612f62d418bb2db0c7c234863500e98b5a036dab`。`fix-verification.json`冻结13份产品/测试源与两镜像，候选副本`s3-candidate.bin`、`grbl-candidate.bin`均仅本机。

新增45项正式回归，另在既有确认测试内增加同地址成功、错误地址/缺半对参数拒绝断言。旧代码RED为S3 9失败、Grbl4失败、Portal13/MCP3失败；其余扩展边界在修复后验证。S3新增宿主模板原引用本机头文件路径已改为按目标仓解析，未把临时绝对路径作为测试依赖。暂存独立跑旧纸张套件缺头文件的环境失败保留，最终实际仓全套通过。

差异行clang-format检查通过，未重排历史代码。源码插行的30处引用自动按原文唯一匹配同步；3处歧义或行为变化引用人工核对后改为具体函数名（RequestConfirmForPreview、ReturnHomeAfterDraw/ChangePaperAfterDraw、RecoverDisconnectedDraw），不自动追认新正文。台账及规则插行的纯移动引用继续同法同步。

Wokwi重新连接服务后仍返回月度CI quota，非阻断但**未完成启动验证**；没有真机笔控、超长行或预览替换注入。旧固件需人工确认的兼容行为有宿主/Portal测试，尚无上线证据。当前只完成代码、测试、构建和本地门禁，未提交、部署或刷机；下一发布必须另升S3版本，不能复用现役1.0.35目录。

## 发布续作（用户「提交部署」）

用户授权本批三仓提交推送、固件与服务部署。S3另升1.0.36并重新构建，先核对固定Freenove身份/空闲态/回退镜像，单次OTA后确认`confirm_by_url`能力，再更新Portal与共享判错；量产Grbl按MAC/原点/全参数保护升级。发布与验证证据保存在`results/local-only/2026-10-09-trirepo-next-release/`，暂存脚本位于`tmp/trirepo-next-release-20261009/`。原有未提交审查记录随本批提交，其他未跟踪资产保留。
