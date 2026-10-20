# 三仓代码审查（2026-10-20）

本轮口径沿用 `deep_review_20261010_readonly` 先例：**只读产品源码**，不改产品代码/测试/配置，不跑固件构建，不连设备与生产服务。续作运行了本地 Python 输入与交错探针，网络均替换为内存函数；只新增审查记录和本机证据。

基线：hub `4006a39`；S3 `2b997d4`（板上产品 `3d94f8f` / `hutuji.1.0.40`）；量产 Grbl `massprod/nopaper-1.3a` @ `539e3f9b`（普通树参考分支 `fix/panel-hold-after-align` @ `7398a264`）。

近期已修复且带回归钉死的批次不重报：DR10-01..08、PR10-01..05、NR09-01..03、FR11、2026-10-13 breaker 批（`8c843d9`/`e6eb326`）。10-13 批的 `breaker.py`/`bitmap_svg.py`/`image_racers.py` 已由 1673 项回归与真 Redis 交错回归钉死，本轮不重审。

## 本轮结论

新增 **7 项待修复：P1 一项，P2 六项**。下面的编号表示不同根因，不把已修批次重复计入。固件项基于真实调用链和目标 SDK 源码，未在板上触发；Python 项的本地复现范围逐项注明。

| 编号 | 级别 | 所属 | 问题 | 证据层级 |
|---|---|---|---|---|
| CR20-01 | P1 | 量产 Grbl | 静默 Web 查询将空应答指针交给列表/设置读取函数 | 源码调用链 |
| CR20-02 | P2 | Portal / S3 接口 | 聊天旧预览卡只绑定设备，能确认同设备的新作品 | 真实 Portal 路由 + 前后端源码；设备 RPC 替身 |
| CR20-03 | P2 | S3 | 激活中转任务自删时跳过 HTTP 对象析构 | 固件 + 实际 HTTP 实现源码 |
| CR20-04 | P2 | MCP / Portal | 4096 字节采样切断字符，误拒合法 UTF-8 文档 | 真实嗅探与完整解析函数对照 |
| CR20-05 | P2 | Portal | 工具成功后 LLM 失败，整轮丢失预览回执与工具历史 | 真实语音编排函数 + 内存替身 |
| CR20-06 | P2 | Portal | 归档校验失败与删除交错，远端副本无撤回也无台账 | 真实 worker / SQLite Store + 内存网络替身 |
| CR20-07 | P2 | S3 / Portal 接口 | 会话内容按字节截断成非法 UTF-8，整个批次无法受理 | 固件/cJSON 源码 + 字节算法探针 |

## CR20-01：静默 Web 查询可空指针解引用

涉及量产树 `Grbl_Esp32/src/WebUI/WebServer.cpp` 的 `_handle_web_command`，`WebSettings.cpp` 的 `listSettings` / `listAPs`，以及 `ProcessSettings.cpp` 的 `show_setting`。

`/command_silent` 调用 `_handle_web_command(true)`，ESP 分支显式构造 `ESPResponseStream* espresponse = NULL`。该指针沿 `system_execute_line` → `do_command_or_setting` → `WebCommand::action` 原样传递，`WebCommandContext` 不补应答对象。`[ESP400]` 进入 `listSettings` 后立即执行 `espresponse->client()`；`[ESP410]` 的 `listAPs` 与 `[ESP100]` 设置读取也有同类解引用。`ESPResponseStream::client()` 读取实例字段，不能在空对象上调用。

`ESP400` 使用 `anyState`，该函数返回 false，表示不拒绝任何状态；因此忙状态不能挡住此路径。当前量产 `Config.h` 未启用 `ENABLE_AUTHENTICATION`，对应 `is_authenticated` 返回 admin；即便开启认证，合法登录用户仍能触发。局域网调用此入口可能使控制器异常重启并中断任务，故列 P1。**未发实际 HTTP 请求，不宣称实机已重启。**

建议在静默入口提供有效的丢弃输出对象，或完整地使所有查询处理器支持无输出；需要覆盖列表、单设置读取与普通非静默查询。只在一个列表函数里加空检查会留下其他入口。

## CR20-02：旧预览卡可以确认同一设备的新作品

涉及 `draw-portal/weapp/pages/chat/chat.js` 的 `_pushMessage` / `onConfirmPreview`，`draw-portal/draw_portal/app.py` 的 `voice_confirm` / `_voice_device_tool` / `_device_control`，S3 `hutuji_job.cc` 的 `RequestConfirmForPreview`。

聊天卡保存 `jobId` 和图片，但点击开始画仅提交 `{device_mac}`。服务端不查卡片对应任务，也不传 `expected_url` / `expected_preview_url`，最终 RPC 是 `hutuji.confirm {}`。S3 只有两地址至少一个非空时才检查作品身份；无参兼容路径直接确认当前待确认作品。

具体时序：卡片 A 仍显示待确认 → 用户在设备屏幕取消 A（聊天页不因此作废该卡）→ 同一设备收到 B 并进入待确认 → 点击 A 卡的开始画。设备/所有权检查全部成立，确认落在 B。此处不假设忙状态能被新 draw 覆盖；A 先取消收尾再接收 B 即可成立。

本地真实登录/绑定/`/api/voice/confirm` 路由返回 HTTP 200，捕获的设备 RPC 参数确为 `{}`。设备侧只做源码核对，未模拟板上开画。NR09-03 已给自动确认链加作品地址，此卡片链未接入；原本针对当前设备状态的屏幕/口头无参确认语义可以保留。

建议卡片确认提交任务身份，服务端按用户/设备查对应产物，并将两个地址交给 S3 同锁比较；旧卡不能降级成无参确认。卡片取消也只传设备，应另明确“取消本卡”与“全局停止”的语义。

## CR20-03：激活中转任务跳过 HTTP 析构

涉及 S3 `main/boards/lichuang-dev/hutuji_activation_relay.cc` 的 `RelayTask`，实际网络实现 `managed_components/78__esp-ml307/src/esp/esp_network.cc` 和 `src/http_client.cc`。

`CreateHttp(3)` 返回 `std::unique_ptr<Http>`；成功与 `Open` 失败分支最终都在它仍存活时执行 `vTaskDelete(nullptr)`。FreeRTOS 自删任务不展开 C++ 栈，`unique_ptr` 析构不执行。显式 `http->Close()` 只断开 TCP，并不删除 `HttpClient`；其 `~HttpClient` 才删除构造时创建的 event group，字符串、容器与成员对象也依赖析构回收。

`application.cc` 激活轮询可以多轮调用 `ReportActivationCode`，每轮创建一个一次性任务，故失败重试期间泄漏会累积。该钩子包含现役 Freenove 量产板。已有 `test_activation_relay_wiring_and_redaction` 只检查接线/脱敏和 `vTaskDelete` 字符串存在，不能证明析构执行。未量测单次泄漏量或设备堆下降。

建议采用 `RunRelay` 正常返回后由薄任务入口自删的结构，或在自删前显式结束所有 RAII 作用域；需要验证成功、HTTP 打开失败、资源创建失败各出口的析构次数。

## CR20-04：合法中文文档在编码采样边界被拒

涉及 `mcp-server/hutuji_mcp/doc_ingest.py` 的 `sniff_doc_kind`，上游真实入口 `draw-portal/draw_portal/upload_kinds.py` 的 `detect_upload_kind`。

文本分支对 `raw[:4096]` 调用严格 `_decode_text`。若第 4096 字节是中文 UTF-8 字符的首字节，样本不是完整编码串；回落 GBK 也可能同样以残缺首字节结束，最终错误宣称文件编码不支持。

两个真实函数对照均命中：TXT 为 4125 字节/4105 字符，CSV 为 4128 字节/4108 原始字符。二者都可完整 UTF-8 解码；绕过嗅探而直接 `parse_document` 分别得到 4105 与 3912 有效内容字符，均低于 5000，CSV 也在行列限制内。然而真实上传种类判别均抛“无法识别文本编码（仅支持 UTF-8 / GBK）”。不是文件超长或非法编码。

建议用非终结式增量解码验证样本，或在已有上传字节预算内解码完整文本；不能用忽略编码错误隐藏文件正文损坏。补边界前后、UTF-8/GBK、TXT/CSV 和真实坏编码对照。

## CR20-05：语音工具已完成，后续 LLM 失败抹掉回执

涉及 `draw-portal/draw_portal/voice_agent.py` 的 `_chat`，以及 `app.py` 的 `voice_turn`。

工具成功后，预览只暂存在局部 `last_preview`，工具链也只放在局部 `messages`。下一轮 `_post_chat` 若超时或回 503，异常直接退出；位于循环后的 `history.extend` 与返回预览都不执行。API 将此转成整轮失败，但此前 `voice.draw` 已经产生付费结果并推送设备预览，不能被回滚。

本地注入“首轮要求 voice.draw、工具成功、第二轮 LLM 503”：真实编排调用工具一次，随后抛错，持久会话历史长度为 0，调用方收不到预览。用户看到失败后重说可能再生成；已在设备待确认的任务也缺少本轮聊天卡入口。未调用付费模型或设备。

建议在工具结果产生后保留已完成事务与预览；后续语言润色失败可返回固定说明和现有预览。回归应覆盖有副作用工具成功后失败，以及零工具成功的普通 LLM 失败，避免把两者都当成功。

## CR20-06：归档校验失败时漏撤回已删除作品

涉及 `draw-portal/draw_portal/telegram_archive.py` 的 `_process`，`store.py` 的 `mark_preview_telegram`。

上传已拿到 `file_id/message_id` 后，`_verify_download` 网络失败或 SHA 不符会进入失败分支。该分支调用 `mark_preview_telegram` 后无条件返回，忽略其“行已删除”返回值；成功分支才检查返回值并撤回副本。

确定性交错：上传成功 → 校验期间用户删除本地行 → 删除动作看到的旧行还没有 `tg_message_id`，不能发撤回 → 校验失败 → UPDATE 找不到行 → 返回。远端副本仍在，台账也没有它，后续按用户追踪无法找到。真实 Store/worker 探针得到本地行不存在、撤回调用 0、有效台账行 0。网络均为替身，**本轮没有上传或删除任何真实第三方消息**。该问题只适用于启用冷备份的部署。

建议失败分支同样消费 UPDATE 结果，丢失行时撤回已上传消息；回归同时覆盖校验 false 与抛异常。09-12 已修的“校验成功时删除竞态”不覆盖此分支。

## CR20-07：会话截断破坏 UTF-8，丢掉整个上报批次

涉及 S3 `hutuji_conversation_report.cc` 的 `PushTurn` / `PostBatch`，目标 cJSON 的 `print_string_ptr`，Portal `/api/device/conversation/report`。

`PushTurn` 对 `strlen(content)` 按 500 字节截断并直接 `memcpy`；没有退回完整码点边界。167 个“中”占 501 字节，截断后留下 166 个完整字符和末尾两个字节。实际 cJSON 实现把这些非控制字节原样复制入 JSON，不验证或修复 UTF-8。接收端 JSON 解码因此失败，路由不能受理；批次在 POST 前已从队列弹出，按现行失败即丢契约不会重试，一条长中文消息可连带丢掉同批其他正常事件。

本地按该字节算法构造 JSON，Python 解码得到 `UnicodeDecodeError`。这是算法探针与真实序列化源码对照，未编译或运行固件，未做设备 POST。500 字节预算和失败即丢策略本身不是本条缺陷。

建议保持字节预算但只在完整 UTF-8 边界截断；补纯中文、中英混排和 emoji 的边界样本，并检查整个批次 JSON 可解析。

## 排除项与覆盖边界

- 量产 `WebSettings.cpp` 已具备第十个参数拒收和按任务 `WebCommandContext` 隔离，普通树 `f62ccf52` / `7398a264` 对应修复不是本轮遗漏。Telnet 会话代次与 `Pipe::SendLineForSession` / `ShutdownSocket` 只做定向复读，没有重跑旧交错测试。
- 曾怀疑会话上报静态栈仅有 3 KiB；目标 ESP-IDF v6.0.1 Xtensa `portmacro.h` 实际定义 `StackType_t = uint8_t`，`12288 / sizeof(StackType_t)` 仍是 12288，**排除此项**。不能把通用 FreeRTOS 的类型假设套到目标 SDK。
- S3 复读了确认/试笔/调速/自愈关键入口，以及 memory、bind_identity、draw_bind、kdraw 和会话/激活上报；未重新完成 BLE、全部显示/音乐/OTA 路径的逐函数审计。现有 host366/CI 成功是先前批次证据，本轮未复跑。
- Hub 补读了文档嗅探、curated 运行时、gfont 字形加载、单页缩字号、sketch 解码/波纹连接边界，以及语音、冷备份、身份与小派设置入口；字库是服务端可信资产，不把任意恶意字库上传当作现有可达攻击面。没有重新出图做画质结论。
- 原文“待 scout 回报”只是未完成占位；本次由主代理直接续审，没有虚构子代理结论。本仓无 `.trellis/scripts/get_context.py`，按仓库交接文档恢复上下文。
- 七项均未修复、未部署、未刷机；不改变 readiness 的纸面、老化、配对和交付 Hold。此次范围不是三仓无剩余缺陷的证明。

## 本地证据与验证

- `tmp/review_20261020_probes.py`：真实上传种类判别/完整解析对照、语音工具后失败、UTF-8 字节截断、归档删除交错。以 `mcp-server/.venv/Scripts/python.exe -B` 执行，断言命中、exit 0。
- `tmp/review_20261020_voice_card_probe.py`：真实 Portal 注册/登录/绑定/确认路由，邮件与设备 RPC 替身；HTTP 200、`hutuji.confirm` 参数 `{}`，exit 0。
- 证据目录 `results/local-only/2026-10-20-review/`，主要文件 `probe-results.json`、`voice-card/result.json`；SQLite 与预览占位文件仅本机隔离目录。两脚本不写产品源码，不读取生产凭据。
- 不运行产品全套 pytest、固件构建、付费 API 或实机探针；只对本批文档跑 `python scripts/agent_gate.py --profile docs`，最终回执保存在上述证据目录的 `docs-gate-final.md`。首次因页首新增状态记录挤动六处历史行号引用而失败，随后将新增记录移至文尾恢复原行号，不重登或放宽指纹；初次回执保留为 `docs-gate-initial.md`。

## 2026-10-20 修复续作（supersede 上方“七项待修复”状态）

用户逐项引用本报告并要求“全部修复”。七项产品改动已落本地工作树，未提交/部署/刷机，固件仍使用原版本号，仅作 NOT_FOR_RELEASE 编译验证；物理放行 Hold 不变。

| 问题 | 实现 | 回归要点 |
|---|---|---|
| CR20-01 | 静默入口使用有效默认应答对象，CLIENT_INPUT 丢弃输出 | 真实 HTTP handler 宿主编译；静默列表、单设置与普通应答；原 bounds/credentials 保持 |
| CR20-02 | 卡片提交 job_id；服务端复核用户/ready任务/设备快照/固件能力，并传两地址同锁确认 | 缺字段、伪造任务、跨用户/设备、缺快照、旧固件、异作品拒绝、同作品成功；小程序旧卡拒发 |
| CR20-03 | RunRelay 正常返回后任务入口自删，载荷与 HTTP 均 RAII | 以不展开栈的 longjmp 模拟自删；成功、Open失败、无网络、JSON失败、HTTP创建失败均检查析构 |
| CR20-04 | 去掉截断样本，按完整文本严格验码 | UTF-8/GBK、4094/4095/4096边界、TXT/CSV、真实坏编码与末尾残字符 |
| CR20-05 | 后续模型失败时用固定说明收尾，保留 messages/preview | 上游异常和坏回包后预览与工具历史仍在；下一轮不重复执行；未执行工具失败仍抛错 |
| CR20-06 | 校验失败的 UPDATE 返回 false 时撤回消息 | 校验 false/异常与删除交错；成功分支与本地仍在的 failed 语义保持 |
| CR20-07 | 截断长度回退到 UTF-8 起始字节前 | 真实 PushTurn 编译输出；中文、emoji、ASCII混排、短文本与500字节上限 |

旧码区分证据：Hub 首批定向 10 failed / 13 passed；卡片身份 10 failed；S3 两项分别表现为非法 UTF-8 和 HTTP live 计数不归零；Grbl 静默入口空应答断言失败。修后首批 Python 三文件 104 passed，S3 两项与 Grbl 定向宿主通过；全套/构建/最终门禁结果以本批最终验证记录为准。

根因与防复发：这批问题集中于“边界丢失”——字符串截断丢码点、卡片调用丢作品身份、任务自删丢析构、LLM失败丢已完成事务、失败分支丢删除意愿。回归必须检查真实入口和最终副作用，不能只断言源代码含某个函数名；Grbl旧测试桩允许空指针、S3旧测试只数自删调用，都是已有盲区，本批已补行为断言。相关执行契约已追加到 protocol，不改变运动、换纸、引脚或旧门禁判据。

本批暂存 `tmp/cr20-fixes/`，最终证据归档 `results/local-only/2026-10-20-fixes/`。历史行号纯移动用门禁重定位；重复引用人工对照符号后登记，不能用删除检查项求绿。

## CR20 最终本地验证（2026-10-20）

七项补丁和对应回归均已落本地工作树。未提交、部署或刷机。

| 验证面 | 实测结果 |
|---|---|
| MCP / 枢纽 full | 1691 passed；e2e PASS；full 13层通过。其历史§9源码引用普通Grbl仓，量产由下列独立构建/fz覆盖 |
| Portal | 使用枢纽venv执行独立 `draw-portal/tests`：595 passed；两条既有依赖/测试样本警告 |
| 小程序 | `node --test draw-portal/tests/weapp/*.test.js`：325 passed |
| S3宿主 | x86_64 g++、unittest discover：368 passed；改动行 clang-format检查通过 |
| S3真构建 | Freenove与LICHUANG_DEV_S3均成功，日志确认两修改模块参与编译；app分别3088400/3126656字节 |
| 量产Grbl | x86_64 g++宿主70 passed；PlatformIO release成功 |
| fz standard | product_host、protocol、hardware仿真通过；overall=fail，仅wokwi_startup/qemu_startup失败。Wokwi为ready_timeout/CLI wall timeout，QEMU为Unhandled debug exception；observe继续阻断完整放行声明 |
| fz失败项复跑 | 已按要求读observe/triage，`sim_rerun --from-last`提示Nothing to rerun并退出1；无失败协议/硬件用例可复跑，不能记作通过 |

首轮枢纽full为1690 passed/1 failed。门禁只保留汇总，随后单跑计数用例明确复现“AGENTS两处总数1691/1673不一致”；当前收集/当前共/inventory三锚和doc_ingest分项全部同步后，最终full1691全过。未改计数断言或门禁阈值。首次单跑漏设PYTHONPATH导致的导入错误也保留在日志，不计产品回归。

`results/local-only/2026-10-20-fixes/verification.json` 保存三仓源码摘要、最终full回执、构建大小与SHA-256；三个镜像均命名NOT_FOR_RELEASE，原版本号未提高，禁止当作可部署同号正式包。fz报告绑定当前量产539e3f9b脏树，未更改其源码/判据；普通Grbl既有.gitignore不动。纸面、老化、工厂配对、交付签核与芯片启动层未验证边界保持。

2026-10-20 CR20提交推送授权：用户要求提交推送、继续审查并使CI通过。S3提交34dc2ca、量产Grbl提交99d14a13均已推送；枢纽随本批提交，Hub CI两份外仓快照同步到这两个精确提交。继续审查与远端CI结果另行登记；未部署或刷机，候选镜像NOT_FOR_RELEASE和实体Hold不变。
