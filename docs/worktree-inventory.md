# 跨仓工作树清单（2026-08-24 更新）

> **2026-09-15 图库批工作树**：首批467件/20类素材与许可/来源/复核清单在 `mcp-server/market/`；转换器、入库回归、发布白名单与发布回归及对应文档为本批改动。仅暂存明确路径，保留原未跟踪文件及外仓工作树。`results/20260914-market-release/` 保存官方归档、三代候选/最终对照板、LF构建、20项入库/43项发布回归和本机真实加载结果；results被忽略，仅本机留证。本批源码/数据提交 `c4846c906bd3`；后续仅图库LF检出属性与验收台账收尾。双机发布日志 `results/deploy-20260915-082723-3443e594/`、`results/deploy-20260915-083714-76d92e8f/`，服务核验 `results/deploy-20260915-084547-ddbe8deb/`、`results/deploy-20260915-084552-20ab25f7/`；主备数据/配置核验、页面截图和收藏清理在同一图库证据目录。`results/20260915-paper-hil/verification-summary.json` 汇总guards/normal/abort三组串口与远端结果SHA、0/2/1次自动释放、Grbl全套设置前后对照；配置保存与最终官方状态在 `results/20260913-crossrepo-followup/` 当日08:37–08:47日志。失败的两次guards是沙箱连接和探针误读未公开last_error字段，未当作通过；成功组为guards-084259、normal-084417、abort-084509。


## 2026-09-24 笔顺教学美观批（用户「笔顺服务生成的还不够美观漂亮请改进优化」）

`stroke_order.py`：笔画 centripetal Catmull-Rom 平滑（过原控制点、端点原位、≥45° 折点锚定护横折；`_SMOOTH_SUBDIV=6`/`_CORNER_COS`）保留生效；**米字格对角虚线当日回退**——用户实机预览判「看不清楚」（512px 页预览单格约 40px，对角+十字 8 组虚线糊成星爆淹没字形，且抬笔 209 次近翻倍），`_emit_grid` 回田字格（外框+中十字虚线），回归钉 `test_grid_is_tianzige_no_diagonal_dashes`（对角勿再加回）。`test_stroke_order.py` 11 项不变（替换对角断言为田字格断言），混合行辅助同步清理。真数据视觉验证：`tmp/strokes_demo/strokes.jsonl`（chanind/hanzi-writer-data master data/ 逐字拉取 16 字，jsdelivr all.json 与 per-char 路径均 404 弃用）、`tmp/teach_chunmeng_current.png`（米字格病灶复现）→ `tmp/teach_chunmeng_tian.png`（回退后清晰可读，渐显逐格可辨、成形格完整）、`tmp/teach_yong_p0.png`、`tmp/teach_smooth_compare.png`。部署：美观批 14:42 双机部署（见 deployment.md）→ 米字格回退批随后单独重部署。

## 2026-09-23 聊天多轮丢按钮修复 + 预览方案 3（用户「多轮说话后就不出现绘画按钮了，看下日志」+「选择 3」）

生产日志+真 LLM 探针复现：根因=历史剥离出图痕迹致模型鹦鹉学舌（文字链丢 DRAW::、语音链丢 voice.draw）。修复：voice_agent 历史保留工具调用链+整轮裁剪；chat.js 历史回填 DRAW:: 标记。探针修复前 1/6→修复后 6/6。预览：`preview_cache.js` 持久化（内容键+LRU32+登出清空）+ 两页 IntersectionObserver 懒加载（首屏 6 张）。提交 `35de54d`+`2cbd65e`；双机 voice_agent.py 已部署（SHA `71fe4110…` 一致、active、公网 401 冒烟）；备份 `/root/hutuji-bak-20260923-voiceagent/`。node 235/235、portal 427/427。weapp 0.3.17 已上传（`✔ upload`，265298B；CLI 日志在 `results/20260923-weapp-ux-perf/`）。同日追加 `f58e659`：筛选/删卡后重挂预览观察器 + 过期注释改口径。`8499f1d`：DRAW:: 乱位标记升格（0.3.17 上机后重画措辞残余），双机 mcp+bridge 部署 SHA `55b847ef…`，部署后探针 4/4；23:49 用户 0.3.17 实测两轮均出按钮（nginx 日志 172B×2+generate 200），用户确认修复。探针脚本 `tmp/chat_multiturn_probe*.py`、`tmp/voice_multiturn_probe.py`（本机）。

## 2026-09-23 小程序体验/性能优化批（用户「改进优化小程序吧」）

api.request 默认超时 150s→20s + 固件三请求显式超时；phone_gate resolve me 三页同程复用；纸张摘要 60s TTL 缓存 + paper 保存即失效（含离页分支）；control setData 按键 diff（直写一律失效，修设备切换 `canSetSpeed` 漏发回归，RED-GREEN 实证）；profile 昵称 dirty 短路；死代码清理；`version.js` 0.3.15→0.3.16。JS **229/229**，hub 门禁 PASS（pytest 1571、e2e）。提交 `b25a5cd`+`be7df52`，工作树干净；0.3.16 已 CLI 上传（`✔ upload`，包 262116B，日志与 bat 在 `results/20260923-weapp-ux-perf/`）。体验版/提审/发布与 0.3.15 审核处置待用户。白名单键 `weapp_ux_perf_20260923_authorized`。

## 2026-09-23 小程序合规整改（reject.html 核对，用户「同意；并开始整改」）

按《微信小程序平台常见拒绝情形》逐块核 weapp：五块源码干净，服务类目为 09-07 刻意决策（结构性策略风险已如实登记）。整改：`privacy-policy.md` 补「注销与删除」流程+「提审用第三方共享最终文本」；`profile.wxml` 加注销删除指引行；`version.js` 0.3.14→0.3.15（16 个 JS 测试文件全过）；`weapp-smoke-checklist.md` 登记六块核对结论。待用户/MP 后台：隐私填报、42 项真机冒烟、体验版/提审/发布、订阅模板。0.3.15 已上传（19:35 CLI exit 0，包 262367B；首试登录态过期 code 10 失败，日志在 `results/20260923-weapp-compliance/`）。白名单键 `weapp_compliance_remediation_20260923_authorized`。

## 2026-09-23 三仓优化批（breaker/WAL/fz 裁定/取证日志）

用户「继续下一步优化改进三仓」+ 两项拍板（WAL+busy_timeout、取证并刷机）。hub：`breaker.py` 删零调用 `_recent_samples_async` 死代码；`store.py::_connect` 启用 WAL+busy_timeout=30000（portal 425/425 + 6写4读并发冒烟零错误）。量产 Grbl worktree（`9bebc00` 未动）fz standard 复跑 FAIL 9 层与 09-14 基线逐层一致，裁定 nopaper SKU 预期差异（证据 `results/20260923-massprod-hil/fz-massprod-20260923-agent_gate_last.json`）。xiaozhi 提交 **`2312446`**（灌流错误应答取证日志，host 109/109，三板各 215 CXX），量产机已刷 **hutuji.1.0.21**（bin 3074640B SHA `bab9d3d0…b325`），draw+repeat 冒烟通过。hub 门禁 PASS（pytest 1571/1571、e2e PASS）。白名单键 `trirepo_optimize_20260923_authorized`。当日追加：4 文件（breaker/draw_queue/draw_worker/store）经 `d4579d74` LF blob 双机部署（备份 `/root/hutuji-bak-20260923-trirepo/`），阿里 12 服务+京东 2 服务 active、portal/admin/bridge 200，preflight 阿里 25/9、京东 26/8 与 09-14 基线同口径；bridge 连续重试撞 start-limit-hit 一次经 reset-failed 恢复。deployment.md 首条已登记。

**既有在制批已由用户「你搞」收口提交（`2c2848e`）**：队列结算幂等重试——`_FINISH_SCRIPT` 空凭证拒绝/同结果幂等/终态保留领取凭证，`draw_worker.mark_done` 移 try-else 杜绝生成成功写 failed；test_draw_queue 38→43、全套 1571 已在 `8e4bf64` 同步台账。

## 2026-09-23 量产机上电确认、调速复验、三项定向 HIL 与 abort 修复批（xiaozhi `9dd4661`；证据与tmp脚本不提交）

用户告知「量产写字机和量产小派已上电」「备案已通过」。本轮无代码/文档外改动：云端 messaging 链直查 `hutuji.status`（1.0.19/nopaper/idle/指纹通过/原点）+ portal 调速复验（现读 3000/3000/1200，单轴 y 9500↔3000 往返读回，z=3001 越界 422）。随后按用户「按照顺序执行」完成三项定向 HIL：运动中 abort 物理链全对（抬笔/归位/自动 `$MD`/二次停止复用）但揭固件收尾误判——reset owner worker 悬挂 ~53s 后 WaitForAbortReset teardown 自拆 session，误判「abort reset 恢复失败」进 state=error 并播假警报（机制 `hutuji_job.cc:2901-2938`，当日已修复见下段）；error 黏滞经下一正常任务（口+repeat）起步自清、两画两 done；多页真 2 页任务被设备拒「这台机器一次只能画一页哦」零运动（契约行为）；A3 经 portal 设置被 400 拒「需要 X400 × Y277mm」快照不变（物理超行程，拒绝链即验收）。证据 `results/20260923-massprod-hil/`（device-status、speed-\*、abort/normal/multipage 双串口+远端+样本 G-code、a3-reject；results 被忽略仅本机）；辅助脚本 `tmp/hil0923_*.py`、`tmp/run_hil0923*.py` 不提交。readiness 文首同日条目已登记（含 09-08 备案条按用户原话翻 ✅）。

同日追加修复批（用户「同意」授权）：xiaozhi 提交 **`9dd4661`**（5 文件 +107/-3：`hutuji_recovery_core.h` 双谓词 + `hutuji_job.cc` 两处接线 + host 测试 + `CMakeLists` 1.0.20 + `main/idf_component.yml` 钉 esp_video `2.3.*`），2026-09-23 已随批量推送 `origin/feat/hutuji-usb-pipe`。构建阻塞根因=注册表 esp_video 2.5.0 的 esp_h264 依赖引用未定义 Kconfig 符号（净 HEAD 同症，非本批引入），钉版后三板各 215 CXX 通过。量产机已刷 **hutuji.1.0.20**（freenove bin 3074480B SHA `39268268…e814c6`，read-mac 核 `b8:1f:3f:c4:58:2c`，watchdog 复位）；abort 复现案 26.7s 发布 `aborted`（对照事故 53s+error+假警报+拆链），normal 两画两 done、多页拒绝零运动回归同过。host 117/117。证据 `results/20260923-massprod-hil/hutuji.1.0.20/`（flash-record+s3-flash+bin）与同目录 abort/normal/multipage 复跑日志。白名单键 `abort_settle_race_fix_20260923_authorized`（含同日追加的钉版授权）。

## 2026-09-14 配网二维码回归

用户确认长按进入配网后缺二维码。本轮 S3 仅 `main/display/lcd_display.cc`、`CMakeLists.txt` 与新增 `scripts/tests/test_provisioning_qr.py`；本仓维护对应设计、协议、固件说明和交付状态，保留其他未跟踪资产。完整身份配网244B/绑定238B在旧V8上限下实际编码失败，候选V13通过；5项新增回归红→绿，完整S3 host225项通过。真实LVGL两尺寸布局和8张原尺寸截图解码有证；三板各215 CXX通过，格式收尾量产增量1 CXX。S3提交 `923e148`，Freenove已刷1.0.19（3073152B，SHA `119dd764…65734ef`），串口和官方状态均确认idle/ready/指纹通过，NVS与1.0.18回滚保留；新增证据为 `hutuji.1.0.19/`、`device-after-flash.log`、`hub-gate.{json,md}` 和 `acceptance-192730/`。300秒观察未捕获用户进入配网；用户随后明确确认『二维码显示、扫码和关闭都正常』，这三项按用户实机反馈记通过，另存 `user-acceptance.json`。小程序0.3.14上传任务 `confirmation_upload_81aaad08-b50d-4b9e-a4c0-5a85e8271de5` 已由官方查询返回 `success/execution_success`，包263393B；原日志为 `20260913-followup-review/control-193703-993019-polling_task_result.log`，终态副本 `weapp-upload-result.json`，原pending记录同步结清，不重复上传。

本机证据为 `results/20260914-provisioning-qr/`：实际C编码复现、来源SHA、红绿测试、完整host、安装清单、`render/`真实LVGL图像/几何/解码；构建在 `results/20260913-crossrepo-followup/hutuji.1.0.19/`。辅助脚本与工程为 `tmp/probe_provisioning_qr.py`、`tmp/provisioning-qr-probe/`、`tmp/provisioning-qr-fix/`、`tmp/render_provisioning_qr.py`、`tmp/provisioning-qr-render/`、`tmp/run_provisioning_qr_tests.py`、`tmp/run_provisioning_host.py` 和两个安装/准备脚本，均不提交产品仓。根因与验证边界见 [配网二维码回归](design/provisioning-qr-regression-20260914.md)。

此前纸张批已分别提交：hub `a866a94`、S3 `bde6ba5`、普通Grbl `6abedbd`、量产Grbl `9bebc00`。hub25文件已双机部署，备份ID主 `e463ea7b84664be4a3adfecafc3c140e` / 备 `0f6557da2deb46a2a0d50e8b753806bf`；完整出包元数据 `20260913-crossrepo-followup/deployment-packages.json`，部署日志为 `results/deploy-20260914-172423-2bf3f4df/` 与 `results/deploy-20260914-172552-d3df26d7/`。同目录角色更新读回日志 `role-update-172259.log`，最新preflight `ali-preflight-172908-953469.log`（25/9）和 `jd-preflight-182838-121171.log`（26/8），失败条目与09-13基线逐项一致，未报整体通过。

## 2026-09-14 纸张与跨仓追加审查候选

本批依据纸张 PRD 和 `paper_settings_20260913_authorized`、`crossrepo_followup_20260913_authorized`、`production_tools_review_20260914_authorized`。枢纽涉及共享纸张/设备目标/文件元数据、动态 G-code 与文档/文字/笔顺、Redis 恢复、Portal 创作/保存/分享/语音/设置/图库、画板及聊天/控制/收藏体验、运维工具和对应回归。外仓 S3 新增纸张核心/设置/下载标记与会话/预览/MCP 后台/UI 修复；两棵 Grbl 仅 Telnet 实际收包/关闭旧客户端及纯核心测试，普通树原 `.gitignore` 保留。其他未跟踪 `tmp/`、截图、公众号草稿和 `.github/` 等资产不并入本批代码提交。

本机证据（`results/` 忽略，不随 Git 发布）：

- `20260913-followup-review/portal-resume-20260914-142538-504117.log`：Portal 425 项；`weapp-final-20260914-150150-787995.log`：Node 226 项；`release-tools-resume-20260914-144033-523584.log`：发布工具 27 项。`queue-round3-20260914-044308-316666.log` 保留64项真实Redis故障回归。目录内其他更早红/绿日志保留，不以最新通过抹去故障轨迹。
- `20260913-crossrepo-followup/s3-host-081056-153816.{json,log}`：220 项；`hutuji.1.0.18/firmware-builds-20260914-081137-918478.json`：三板各215 CXX。SHA：立创 `ad570cd99b41d40192c69217d3ed037dbf50e8db70103c8cbb72b6beb21f46dd`，Waveshare `dceb01d4e4472e14beaa11080ff26cca13320964d359c0fbcb3fb5a57d0fe71e`，Freenove `4b6dcc054bcf2d6bb9b041b04d7d4d4a671dd06adb94d1859d95d5c005dba388`。
- `20260913-crossrepo-followup/grbl-build-085946-144613/builds.json`：普通197、量产195编译单元，量产DIO=2；两树host各8项。`fz-142953-265594-*`：普通fz完整PASS、201秒、hard0/soft0；量产旧失败与本轮重跑另存，不冒充共同通过。
- `20260913-crossrepo-followup/final-full-hutuji_gate.{json,md}`：枢纽full 13层PASS、MCP1535项/149.69秒、e2e PASS、261处指纹通过。`fz-massprod-151259-298808-*` 量产重跑FAIL/153.59秒；`massprod-baseline-153339-532626/comparison.json` 保存172份HEAD源文件与候选纸路/GPIO同差异结果；`massprod-wokwi-current/` 保存当前DIO应用组成、SHA及Wokwi启动PASS，不代替fz或实机。
- `20260914-drawpad-layout/{comparison.png,validation.json,samples.json}` 及每个样本的SVG/G-code/PNG：四组实际转换包括2000×1200mm自定义，栅格最大距离1像素，仅软件几何证据，未向设备发送。
- `20260913-followup-review/resume-paper-unavailable.jpg` 是真实接口未部署的中文错误页；`paper-ui-sample-{a4,legacy,a2}.jpg` 为禁用设备写入的临时样例，取证后恢复。聊天/画板及后续真实部署证据按本轮结果追加。

本段登记候选和证据，不改变已有设备/上线结论。源码已分别提交：S3 `bde6ba5`，普通Grbl `6abedbd`，量产Grbl `9bebc00`；枢纽提交随后登记。量产S3 COM15/MAC `b8:1f:3f:c4:58:2c` 已刷1.0.18，官方查询确认默认A4配置与完整device_id，证据 `hutuji.1.0.18/{flash-record.json,s3-flash.log,s3-boot-after-flash.log}` 和 `ali-device-163042-694203.log`；NVS已备份，旧1.0.17回滚SHA为 `e72245fca42437058fceb1c628c09f23eb2fce9aa5b8879d686ac3598d451974`。Grbl本轮未刷写；Grbl COM3/MAC `4c:c3:82:cc:8a:9c` 于16:18接回复验为Idle/原点、速度**3000/3000/1200**、`$1=255`（`grbl-reconnected-20260914-161806.log`），此现读值取代本段旧速度记录，未按旧台账回写。刷写前仍须现读MAC。新发布必须基于明确提交的LF blob、逐文件SHA与CR=0；队列升级先暂停入口、排空并停旧worker。精品图库只有程序候选，真实素材和许可集未完成；`configure/` 空工具状态不是量产配方。

| 2026-09-23 | 量产体检加固批 | 代码 | `draw-portal/draw_portal/voice_agent.py`（_sessions LRU 256）、`store.py`（purge_usage_before）、`app.py`（me id + 1h 节流顺带清理 + 装配注入）、`weapp/utils/preview_cache.js`（LRU 128 + cyrb53 哈希名 + 旧命名清扫）、`weapp/app.js`（换账号清缓存）、`mcp-server/hutuji_mcp/activation_relay.py`（MAC 打码 mac[-5:]）、`deploy/README.md` 与 `AGENTS.md`（preflight 33→34）；测试：portal 429/429（+2）、weapp 238/238（+2）；白名单键 `massprod_sweep_hardening_20260923_authorized`；Grbl 双分支 8 提交已推 origin |
<!-- paper-current-count --> MCP 当前收集全套 **1574** 项（45 个文件）；本批完整执行结果另见同批证据，收集数不等于通过数。

## 2026-09-13 公众号引流决策稿（仅文档，未提交）

新增 `docs/design/official-account-plan-20260913.md`：订阅号+与小程序同主体+仅显式授权作品+先落文档，四项用户拍板。注册/认证/运营为线下动作未发生，授权字段与发布 worker 未开发；只改枢纽文档，两个固件仓未动。本条顶部插入推移既有行号引用：`--fix-line-refs` 自动重定位 3 处（findings/28 两处、findings/30 的 app.py 对话上报端点块），空锚点 2 处经人工核对证据行现位置改指 inventory:832（quickdraw 键数）与 inventory:903（fb2b3b5 烧录自证）后 `--accept-line-refs` 重登基线 260 处。

## 2026-09-13 小程序审查（代码已提交、后端已部署、0.3.13已上传）

控制页使用完整目标、读取错误/串行轮询；创作/聊天/历史/资料/画板/固件/小派设置页面补异步边界及体验问题；portal控制目标与业务错误判断、图库备份上传开关和注册通知隐私加固。对应回归、协议、内部隐私核对稿与清单同批修改。产品代码已提交为 `a5fc773df3a2` 与 `ade29f4f5742`，app/archive双机部署核SHA/CR=0；主bridge PID4127741、备0，新增外部上传双机配置/进程均false。追加完成后计时修复已过Node124。组件事件逐轴设置并恢复10000/10000/1200，最终公网/本机done/Idle/原点。0.3.13原生上传任务已查询为success、162480B，`upload-result.json`记录结果，`pending-upload.json`原任务已标success；本批未修改或重刷两个固件。

本机证据根 `results/20260913-weapp-review/` 包含生产故障复现、双机安装/复验/快照、官方法规正文、Node/portal/hub门禁、`component-live.json`、`public-control-after.log`、`control-final.jpg`/`control-status-final.jpg`、`verification-summary.json`和上传终态记录；候选/脚本在tmp，测试用automator仅装在 `tmp/weapp-review-tools/`，不进产品包或提交。全局未跟踪文件与换纸Grbl的`.gitignore`保留；交付状态看readiness。

## 2026-09-13 三轴调速与默认 10000（定向交付已完成）

用户追加要求 X/Y 默认 10000、Z 保留现值，并在小程序分别设置三轴。枢纽改 portal app/machine_speed、control 三文件与定向回归；S3 改 Job/Pipe/速度纯核心/三板工具注册与 host 测试，版本 1.0.17；量产 worktree 只改机头 X/Y 出厂 max-rate。换纸 worktree 本批没有新代码修改，其原 `.gitignore` 脏改保留。

本地 portal351、weapp80、S3 host181、三板各215 CXX、量产 release 与 fz standard 全通过；证据在本机 `results/20260913-crossrepo-review/hutuji.1.0.17/`（Freenove SHA e72245fc…d451974；量产 Grbl e138824a…07eac6）。新脚本/候选/NVS 备份均只留本机 tmp/results，不提交。hub `71250177db60` 已双机部署，S3 `3088794a1287` / 1.0.17 已在 COM15、量产 Grbl `ca9b36bb1ff6` 已在 COM3；最终10000/10000/1200并经重启保留，两轮小样回原点及自动失能通过。weapp0.3.12已上传；57项证据与哈希见同目录 `verification-summary.json`。用户追加的真实页面故障另在本批修复，下方1.0.16历史保留。

> 本文件记录三仓未提交资产。接手时先对清单与 `git status`；清单过期就更新清单，**不是**把别人的改动回滚掉。
> 三仓路径：hutuji `D:\Users\hutuji` ｜ Grbl `D:\Users\Grbl_Esp32` ｜ xiaozhi `D:\Users\xiaozhi-esp32` ｜ fz `D:\Users\zhugu\fz`
> 
> 铁律：**不要假设任何固件源码在本仓库**——运动控制去 Grbl_Esp32 查，AI 交互去 xiaozhi-esp32 查。
> 登记范围含脏树、未推提交与「生产/板上运行态领先 GitHub」；HEAD 每次用命令现取，**不要把本页静态快照当作当前 HEAD 或板上镜像身份。**
> 2026-08-24 合并说明：旧版全文存档于 `tmp/inventory_head.md`；以下各「HEAD 存档并入」小节为旧版证据/决策原样保留（其中日期快照以当时为准）。

---
## 2026-09-13 三仓评审与量产静音交付

- 授权与范围：用户三仓code review、修复验证部署提交，追加量产归位后失能；清单与结果见 `docs/design/crossrepo-review-20260913.md`。
- 枢纽代码 `2d495d1` 已双机部署，备份 `/root/hutuji-bak-20260913-review-2d495d106a65`；主bridge=1 active、备=0 inactive，portal均200。weapp0.3.11上传exit0/144465B；未声称体验版已选择或已发布。
- S3 `b4d267d`（调速/完成观察）→`d72447c`（1.0.15释放）→`99cf4234e41d6c1199d0b99652cbf6c2f318a6f2`（1.0.16停止幂等/终态）。最终179 host、三板各215 CXX，Freenove COM15已刷1.0.16；立创SHA `ed22065b…56af3e`、Waveshare `7f733737…2495ab`、Freenove `0abf5e08…cf9fac2`。其他板未刷。1.0.15停止失败现场留存，最终三项实机回归另存1.0.16目录。
- Grbl换纸分支 `c9a5d68` 恢复build20211103，`001a16c1cbc3ceb21ac29b4d8c5b0566960b0426`修`$MD`参数泄漏；release/fz通过，未刷量产机。量产独立worktree `6221502`→`e68c697fda7a7ca5dcd685a0ecb8736ee8869f9d`，只修参数所有权/回归，经既有Web OTA落COM3，DIO SHA `595d7d39…77b071d`；设置保留、200次失能无累积堆下降。原Grbl `.gitignore`改动保留，旧tmp/untracked资产均不纳入提交。
- fz `e9f81e8`：完整flash装载修复已提交；本次Grbl增量后再跑standard，194.94s、overall pass、observe hard=0/soft=0/block_done=false，Wokwi/QEMU startup通过（不等于纸路、BT或其他硬件放行）。
- 本地证据：`results/20260913-crossrepo-review/`（portal321/weapp68/MCP1453、前段full、双机安装/哈希/preflight、weapp上传、手动消声）；`hutuji.1.0.16/`（179 host、三板bin/sdkconfig/构建、S3刷机/启动、normal/abort两串口与远端日志、样本G-code）；`motor-disable-memory/`（两支真实函数红→绿/两支bin/构建/fz/Web OTA与200次失能）；`verification-summary.json`汇总最终证据SHA。NVS备份可能含身份密钥，仅本机保留，不提交或上传。
- 验证边界：preflight阿里24/33、京东25/33，差异见deployment；手机slider/语音/触屏交互、全幅纸面签核仍未做。三项已测为指定量产机正常绘制、再次绘制、运动中连续停止后的归位失能。

## 2026-09-12 忙批评审补漏（已提交 main 本地，2026-09-23 已推送）：语音画 429 静默 + 阿里云 nginx 片段超时对齐

| 项 | 说明 |
|---|---|
| 授权 | 用户「继续」评审忙批部署改动并处理发现项；`draw-portal/draw_portal/app.py` 在白名单（xiaopai_voice_draw_write_authorized / device_identity_20260912） |
| 改动 | ① `app.py` voice.draw 失败通知加 429 豁免（VoiceJobError status==429 不推「生成失败」，与 /api/draw/generate 同口径：守卫级日限 429 在建任务前已拦，忙批全忙 429 在建任务后漏拦，会发出误导性失败推送）+ `tests/test_voice_api.py` 参数化 2 例（429 静默 / 502 照推）；② `deploy/nginx-draw-portal-snippet.conf` `proxy_read_timeout` 120s→310s——test_contract 钉「模板口径与片段同源」，模板已 310s 而片段未同步（生产阿里云 gray conf 已 310s，见 deployment.md 2026-09-12 提速批+忙批条），按片段重建阿里云 vhost 会把该修复回退成 120s |
| 本地证据 | portal 全套 **291 passed**（289 基线 + 新增 2 例）；hub 门禁 **PASS exit=0**（13/13：mcp pytest 1451/1451、e2e PASS、指纹 259/259，基线重登于 HEAD `e87da7ca`——findings/28 对 inventory 两处引用为纯行移，人工核对 794/798/799 行内容无误后 `--accept-line-refs`） |
| 发现未动 | breaker.py `_recent_samples_async` 全仓零调用且函数体缺 `import asyncio`（潜在 NameError，死代码，非忙批引入；breaker.py 不在白名单，未动，建议删）；store.py `_connect` 无 WAL/busy_timeout（课堂 NAT 并发下读写互斥风险；改库级行为须用户拍板，未动） |


## 2026-09-13 速度下放批收口：金表 10 项 + `hutuji.speed` 工具 + 量产机 1.0.13 落机（🟡 代码+host 测试+双构建+量产机刷机+真机调速度链路+portal 部署+weapp 0.3.10 上传有证；用户端 slider/语音 HIL ☐）

| 项 | 说明 |
|---|---|
| xiaozhi 脏树（本批未提交） | 8 个修改文件：`CMakeLists.txt`（PROJECT_VER→`hutuji.1.0.13`）、`main/boards/freenove-esp32s3-display-2.8-lcd/freenove-esp32s3-display-2.8-lcd.cc`（+`hutuji.speed` AddTool 注册，描述与 lichuang 逐字一致）、`main/boards/lichuang-dev/{hutuji_job.h,hutuji_job.cc}`（+`Job::RequestSpeed`，3000–16000 钳制、settled 态闸、`$110/$111` 写 + WaitResponse 5s、不持久化）、`main/boards/lichuang-dev/hutuji_nopaper_core.h`（金表 12→10 项：删 `$110/$111`，c896ce5c 指纹注释同步）、`main/boards/lichuang-dev/lichuang_dev_board.cc`（lichuang 板同工具注册）、`scripts/tests/{test_hutuji_nopaper_core.py（金表断言 12→10+速度项不得回流）,test_hutuji_recovery_core.py（busy 措辞计数 4→5）}`；既有 untracked tmp 构建残留原样保留 |
| 授权 | 用户原话「速度设置放小程序里，每次刷机太不方便」「量产机啊，只搞量产机」+ 拍板 16000（COM3 `$$` 已证写机） |
| host 测试 | `test_hutuji_nopaper_core.py` 3/3 + `test_hutuji_recovery_core.py` 108/108 OK（CXX=`D:/zhugu-home/mingw64/mingw64/bin/g++.exe`，python3=scoop shim） |
| 构建/刷机 | lichuang 1.0.12 重建（3081968B SHA `50361dbd…`，zip `vhutuji.1.0.12_lichuang-dev.zip`，工具描述修正）**留写字机待插**；量产机 1.0.13 freenove 真构建 215 CXX（`tmp/build_speed_freenove.cmd` HOST_RC=0，`xiaozhi.bin` SHA `370478cb…` 3040736B）→ esptool read-mac 核对 `b8:1f:3f:c4:58:2c` 后 COM15 刷入（app@0x20000+otadata@0xd000，NVS 保身份与绑定）→ 串口复验 `hutuji.1.0.13`/SKU=freenove/**`Grbl 设置指纹通过（10 项）`**/`MCP: Add tool: hutuji.speed`/Idle——量产机 $110/$111=16000 过检、当晚「小程序一直报写字机error」（金表 24000 vs 机器 16000 失配）拒画解除 |
| 事故登记 | 同晚 COM15 实插量产机却刷 lichuang-config bin（MAC 台账教训：刷机前必须 esptool read-mac 核对；`--after=no-reset` 收尾会让 USJ 芯片停留 boot 模式屏黑假象）——已恢复+重刷收口，明细 deployment.md 深夜两条 |
| 已闭环（2026-09-13 凌晨追加） | portal `app.py`（blob `7cc9985a…`）双机部署+restart active+公网 401 冒烟；生产 messaging token lima 现场直推 `hutuji.speed rate=16000` → `{"success":true,"data":{"rate":16000}}`；COM3 `$$` 交叉核对 `$110/$111=16000.000`/`$130/$131=200/200`/`Ver 1.3a Date 20260910`；xiaozhi 提交 `77348e9` 已推 origin，本仓 `239b816`+`a64a72f`；weapp **0.3.10** CLI 上传 exit 0（140.0 KB）。**仍 ☐**：用户端 HIL（小程序 slider/语音口令真机体验）、体验版勾选 |

## 2026-09-12 提速批（xy 轴 3 倍）：云端契约 F24000 + 双固件金标（🟡 已提交 `8dfe00e` + 云端双机部署/生产冒烟/nginx 310s；fleet 固件刷机/机器写入 ☐，bench 机器侧已闭环——量产机 `$110/$111=24000` + freenove S3 换装 1.0.12，见文末「提速批 bench 落机」节）


| 项 | 说明 |
|---|---|
| 授权 | 用户 2026-09-12「写字机需要提速3倍左右太慢了，xy轴运动的」+「继续」；hub `docs/protocol.md` §5 448/469-470 + §6.1 545 |
| 本仓改动 | mcp `{gcode_gen.py（F24000 契约 + G0 原点/换纸闸危险度先判：Z 混入 > 原点 > 进给失配，旧 F8000 注入仍报「自动换纸」而非低危进给提示）,hutuji_gwrite.toml,grbl_sim.py,arc_fit.py,bitmap_svg.py 注释,paper_risk.py 注释,sim_server.py}` + 测试 7 文件 F 值改钉；`AGENTS.md` 105/157/159 行（计数 1451、test_bitmap_busy 条目）；`deploy/nginx-hutuji.conf` `/draw-upload/` 加 `proxy_read_timeout 310s`（忙批 300s 等待轮询须盖过；2026-09-12 双机生产侧 120s→310s 已生效，详见 deployment.md 首条） |
| Grbl_Esp32 脏树 | `Grbl_Esp32/src/Machines/custom_3axis_hr4988.h`：DEFAULT_X/Y_MAX_RATE 12000→**24000**、DEFAULT_X/Y_ACCELERATION 3000→**12000**（+提速注释；Z 不动；G′ 锁拒写故须固件重建，`pio run -e release` 2026-09-12 已构建 `firmware.bin` 1857648B + `firmware_full_0x0.bin`）；既有 `.gitignore`（clangd 行）为用户既有改动原样保留 |
| xiaozhi 脏树 | 金标批已提交 `9c4a218`（2026-09-12 19:54：`hutuji_recovery_core.h`/`hutuji_nopaper_core.h` 双表 $110/$111 24000 + 注释、`test_hutuji_nopaper_core.py` 钉 24000、`PROJECT_VER=hutuji.1.0.12`；工作树现净，仅余 untracked tmp 构建残留）；host 测试 111/111 OK（CXX=`D:/zhugu-home/mingw64/mingw64/bin/g++.exe`；默认 esp-clang 路线本机环境预存在失败，与本批无关，基线对照 git stash 复核） |
| 拒画窗口 | §6.1：金标改动须 Grbl+S3 同批刷机（任一侧先变= `grbl_settings_mismatch` 拒画，fail-closed 预期）；过渡期云端 F24000 被机上 12000 钳到 200mm/s（1.5×），无错误——bench 侧该窗口已解除（2026-09-12 晚三件套对齐，见文末节），fleet MK38 到场前其机上值仍适用 |
| 未验证 | fleet 机器 `$RST=$`/双机刷机/定时盒形冒烟（MK38 到场后回填）；lichuang 1.0.12 已真构建 215 CXX（`releases/vhutuji.1.0.12_lichuang-dev.zip`，`xiaozhi.bin` SHA `ec4b70fc…356764d7`，double `24000.0`×4 取证）待 COM14 刷入；bench 侧已验证项见文末「提速批 bench 落机」节 |

## 2026-09-12 忙批（全忙重试 + 429 链路）：已提交 `c7a43ed` + 双机部署/生产冒烟（🟡 真实后端全忙触发 ☐）

| 项 | 说明 |
|---|---|
| 授权 | 用户 2026-09-12 报「几分钟内连续画就 502 内部错误」后拍板「处理」 |
| 改动 | mcp `{bitmap_svg.py（忙类判定 `_all_errors_busy`/`_generate_bitmap_uncached` 首 fetch sleep+真重试一次）,server.py（/internal/draw BitmapBusyError→429 固定白话）,draw_queue.py（按类名重建 BitmapBusyError）}` + 新增 `tests/test_bitmap_busy.py` **10 项**；portal `app.py`（429 静默不推）+ `tests/test_generate.py`；weapp `chat.js/index.js`（生成两调用 300s）；protocol §10.4.8 |
| 本地证据 | `test_bitmap_busy.py` 10 passed；test_generate 14 passed；portal 289 passed；e2e PASS |
| 未验证 | 真实对冲后端全忙场景的生产触发（单测覆盖；on-node 符号在场冒烟过，见 deployment.md 首条） |

## 2026-09-12 照片精修结构线与几何保真（🟡 已提交 `e9006ea` + 双机部署+生产冒烟；真机纸面 ☐）

| 项 | 说明 |
|---|---|
| 授权/契约 | 用户「图生图服务……质量还不行，有解决方案吗」后连续「继续」；`docs/protocol.md` §10.4.9；设计与证据 `docs/design/photo-sketch-quality-20260912.md` |
| 本批代码 | `mcp-server/hutuji_mcp/sketch_render.py`（结构线、hatch 明暗、实际尾端/整桥白区检查、排线不再 RDP、预览保真）；`bitmap_svg.py`（SVG/预览新增默认 True 的 smooth 关键字）；`draw-portal/draw_portal/pipeline.py` **仅 process_upload_sketch 的 smooth=False hunk**；对应 `mcp-server/tests/test_sketch_render.py` 25 项与 `draw-portal/tests/test_photo_refine.py` 13 项；新增 `mcp-server/scripts/sketch_eval.py` |
| 本地证据 | 完整 hub **overall=pass/exit=0**：MCP **1441/1441**、e2e **PASS**、指纹 **259/259**（同树共享运行 `2026-09-12T10:01:45Z`，HEAD `cdfc6a68`+本批在树）；定向 MCP 75/75、portal 全套 287/287。2 张真实人像×双风格在 MK38/nopaper 均过 §5，3 张随库实拍×双风格过 §5；部署+冒烟证据见 `deployment.md` 2026-09-12 照片精修条 |
| 本机私有资产 | `results/local-only/2026-09-12/sketch-quality-final/`（nopaper 最终回放、原图、SVG、G-code、metrics.json）、`sketch-quality-candidate/`（MK38 转换）、`sketch-quality-reference/`（3 张 scikit-image 0.26.0 CC0 参考图）；`sketch-quality-gates/hub-20260912T100145Z.{json,md}` 为完整门禁原报告，`manifest.json` 登记来源/时间/HEAD/六文件哈希；同目录 `sketch-quality-before/` 与 `sketch-structure-experiment/` 为中间诊断，不代替最终导出证据。图片均不入库 |
| 并发工作树 | 已全部收编：手写抖动批 `cdfc6a6`（write_text/server/weapp 与 pipeline 写字 hunk），照片精修批 `e9006ea`（精修 hunk + `sketch_eval.py` + 设计文档）；运行时 5 文件已双机部署 |
| 未验证 | 用户随手拍 ≥5、纸面 ≥2（双机部署+生产冒烟已完成）；未实现语义主体分割，不宣称所有照片已达艺术家素描质量 |

## 2026-09-12 手写抖动 + weapp 手绘画板（🟡 已提交 `cdfc6a6`；mcp/portal 运行时文件已随照片精修批部署；真机纸面 ☐、MP 0.3.9 体验版 ☐）

| 项 | 说明 |
|----|------|
| 授权 | 用户 2026-09-12 对「手写随机抖动 + 手绘画板」批回复「同意」（白名单 `weapp_drawpad_hand_jitter_20260912_authorized`） |
| 脏树改动（本批） | `mcp-server/hutuji_mcp/write_text.py`（`_HAND_JITTER_*` 常量 + `_jitter_strokes` + `layout_text(handwriting=)`/`layout_text_fit_single` 透传）、`mcp-server/hutuji_mcp/server.py`（`hutuji_write` 开抖动）、`mcp-server/tests/test_write_text.py`（50→55：确定性/默认关/最近点包线 0.115em/分页页界/笔顺路径/fit_single 透传 + 端到端 margin 改抖动预算）、`draw-portal/draw_portal/pipeline.py`（`process_write_text` 开抖动；sketch hunk 由 `e9006ea` 收编）、`draw-portal/weapp/`（新 `pages/drawpad/` 四件 + `app.json` + 创作页三件第 4 格「手绘」+ adopt 回流）、`docs/protocol.md` §10.4.10 抖动行 + §10.4.20 手绘画板、`AGENTS.md` 计数 1441 与功能行、`docs/agent-edit-allowlist.json`、本文件与 `docs/release-readiness.md` 新条目 |
| 并发会话（已收编） | codex 批已随 `e9006ea` 提交并部署：`{bitmap_svg.py,sketch_render.py}`、`test_sketch_render.py(25 项)+test_photo_refine.py`、`pipeline.py` sketch hunk；计数同步已按 2026-09-12 新鲜 collect **1441** 项对齐（含其 +8），commit message 注明 |
| 本地证据 | `test_write_text.py` **55/55**；draw-portal 全套 **287/287**（PYTHONPATH=mcp-server）；mcp 全量 collect **1441**（38 文件）；`node --check` drawpad.js/index.js OK |
| 上传证据 | weapp **0.3.9** CLI upload exit 0、AppID `wxbf3c1e0013b46343`、TOTAL **141802 B**（`tmp/weapp-upload-0.3.9.json`；0.3.7 115128→+26.7KB drawpad 页）；MP 后台选体验版是用户动作 |
| 保留既有改动 | `tmp/weapp_upload_039.bat`、`tmp/make_upload_039.py`、用户杂物（arena-*.png、memory_*.json 等）不动 |

## 2026-09-12 笔顺多字版式「分解行+成形格」（🟡 已提交 `4c1c25e`、双机已部署+生产冒烟；真机纸面待用户）

| 项 | 说明 |
|----|------|
| 授权 | 用户 2026-09-12 实机拍板（多字输入「明天」得到 09-11 成形字帖、无逐笔渐显）+ ask 选「单页混合：分解行+成形格」，supersede 09-11「集中到单张纸」（仅多字路径） |
| 脏树改动 | `mcp-server/hutuji_mcp/stroke_order.py`（删 `_layout_copybook`/`_emit_stroke_numbers`/`_COPYBOOK_*`/`_STROKE_NUM_MM`，新增 `_layout_mixed_rows`）、`mcp-server/hutuji_mcp/server.py`（`hutuji_teach` docstring）、`mcp-server/tests/test_stroke_order.py`（多字测试改钉混合行+折行并入，9 项不变）、`mcp-server/hutuji_mcp/sketch_render.py`（`_normalize_tones` 全局/CLAHE 0.65/0.35 混合 + 排线 mask σ2.2；09-10 评审批遗留未提交）、`mcp-server/tests/test_sketch_render.py`（+主体层次保留回归，配套上述模块改动；本轮随批收编）、`docs/protocol.md` §10.4.11 排版行、`AGENTS.md` test_stroke_order 条目、本文件与 `docs/release-readiness.md` 新条目 |
| 本地证据 | MCP 全量 pytest（见本轮收口输出）+ 视觉复核 `tmp/teach-repro/{mingtian,niLiangYiQi}-mixed-p0.png`；`tmp/agents-head.md` 为 AGENTS 行重建临时对照（可删） |
| 部署证据 | 三文件 HEAD blob SHA 双机逐位相符 CR=0（stroke_order `d2903669…e50`、server `63ee8a97…db7`、sketch_render `33ad02ae…0a6`，部署前与 `4c8296b` 零漂移）；备份 `/root/hutuji-bak-20260912-teach/hutuji_mcp/`（双机）；重启 lima mcp/bridge/portal/admin、jd mcp/portal 全 active；冒烟：双机 `layout_teach("明天")`=1 页 missing=[]、阿里 sketch_render 层次序+18 polys；mcp.env XIAOZHI 四键双机哈希一致 |
| 未验证 | 真机纸面、真机 teach 推送（用户拍板版式以预览+几何断言为准） |

## 2026-09-12 一次扫码身份 v2 与中断恢复（兼容批已双机部署 + 1.0.11 刷入自证有证；手机一次扫码 HIL 待用户）

| 项 | 说明 |
|----|------|
| 授权 | 用户连续要求「扫码、选 WiFi、输入一次密码，剩下的状态确认、认领、重试和恢复全部由系统完成」「开始把固件也改了啊，事情要做完」；联调指定 Freenove 2.8 寸。随后要求查明会话中断并修复、继续原任务 |
| 本仓改动 | `draw-portal` 六个后端模块（含新增 `device_identity.py`）、身份/多设备/页面回归、小程序绑定/配网/设备选择及公共工具；契约 §10.4.0/§10.4.2–4，设计 `docs/design/device-identity-rollout.md`；白名单 `device_identity_20260912_authorized`。并保留登录事故热修 `e05b796` 的旧镜像提升逻辑，冲突时主表所有权优先 |
| 外仓改动 | xiaozhi 独立 fork：P-256 身份与 NVS 会话、签名 worker、三板入口、应用回调和版本 `hutuji.1.0.11`；未并入枢纽。Grbl 既有 `.gitignore` 改动及 fz 未跟踪仿真文件原样保留 |
| 软件证据 | portal `portal-final.xml`：285/285；小程序 `weapp-release.xml`：63/63、exit 0；S3 host `firmware-host-20260912-121822-223914.log`：172/172。完整 hub `hub-pass-before-release.{json,md}` **overall=pass、exit=0**：MCP **1427/1427**（38 文件，`test_contract.py` 656 项）、e2e PASS、指纹 262/262；完整控制台 `hub-20260912-resume.log` |
| 三板真实构建 | `firmware-builds-20260912-121822-223914.json`：lichuang/Freenove/Waveshare 各 215 个 CXX 编译、exit 0；app SHA-256 分别 `2f7e13bd…142f779f` / `17ae2921…db3a3cc7` / `5e2210f8…e6e11dc`。完整哈希与 app/配置/日志在同目录；host PSA 替身不等于芯片密码学实测 |
| 门禁中断修复 | `scripts/agent_gate.py` 改 Windows PID 探活为 `OpenProcess(SYNCHRONIZE)` + 零等待 + `CloseHandle`，无权/未知错误保守保留占有标记；POSIX 仍用信号 0。`pid-probe-red.xml` 1 项失败、`pid-probe-green.xml` 16 项通过；本轮独立隐藏控制台探活 exit 0、signals 为空。中断与旧信号调用相符，但独立旧版探针未复现会话退出，不将同时发生的模型限流武断归于此 |
| 本地证据 | 全部在 `results/20260912-device-identity/`，整目录被 gitignore，证据仅本机；`tmp/probe_pid_liveness_isolated.py` 为隔离诊断辅助。未完成构建目录 `build.identity-incomplete-20260912-111426-299161/` 已于 2026-09-24 随 tmp 清理批删除（见本页 xiaozhi 脏树行）。部署/刷机批新增 tmp 资产：`tmp/deploy-20260912-identity.sh` + `tmp/deploy-20260912-identity/`（六文件暂存+sha256 清单）、`tmp/flash-identity-1011-com15.ps1` + `tmp/flash-identity-1011-com15.log`、`tmp/identity_serial_readonly.py`（只读串口探针，dtr/rts 恒 False、零写入）、`tmp/boot-banner-com15.py` 与 `tmp/boot-banner2-com15.py`（v2 竞抢开端口路线**已禁用**——并发 esptool 疑致端口半死）、`tmp/weapp_upload_038.bat` + `tmp/weapp-upload-0.3.8.json`、串口证据 `serial-COM15-20260912-{160039,160145,160549}.log` |
| hub 单失败根因 | `gate-pytest-diagnostic-20260912-142633.log` 重现 1 fail/1426 pass：队列启动测试继承门禁 `PYTHONIOENCODING=utf-8`，但父进程按 GBK 解码中文 stderr，读取线程异常后得到 None。`test_draw_queue.py` 成对设子输出/父解码 UTF-8；`queue-encoding-green.xml` 在 `PYTHONUTF8=0` 下 1/1，随后完整 hub 通过。生产队列行为未改，不能把独立测试曾通过称为瞬态问题 |
| 升级前实态 | `serial-COM15-20260912-143452.log`：25 秒、零写入、DTR/RTS 均 False，持续 Idle；`production-before-lima.json`：目标 MAC `b8:1f:3f:c4:58:2c`、Freenove 2.8、1.0.10、idle、连接/就绪/授权/指纹全 true，绑定仅在旧镜像表。`production-before-jdcloud.json`：热备绑定空库；双机尚无身份模块。两份预检只返回文件哈希、结构和状态，不含 token |
| 执行结果 | 兼容模式整批发布 ✅（双机六文件 SHA 相符、SQLite `.backup` 备份+迁移保归属实证，见 `deployment.md` 首条）；保留 NVS Freenove 试升级 ✅（仅写 app@0x20000+otadata@0xd000，镜像 SHA `17ae2921…`；15:54 chip-id 复位致 USJ 半死→15:58 用户拔插恢复；messaging `hutuji.status` 自证 `firmware_version=hutuji.1.0.11`、connected/ready/grbl_settings_ok 全 true；探针 404 定案=mcp.env env token 过期，真值在 `/var/lib/hutuji/device_messaging.token` 文件）。**余项**：手机一次扫码 HIL 与 MP 后台选体验版（用户动作）；旧 merged ZIP 不可用作保留 NVS 的刷机输入（本批已按分片写入验证） |

## 2026-09-12 曲库扩容批：fluidsynth 公版旋律 40 首（数据+文档，双机已部署）

| 项 | 说明 |
|----|------|
| 授权 | 用户「唱歌不要只限于儿歌」+ ask 选「全量 40 首一次到位」（2026-09-12）；版权红线（现代流行歌不可上）重申 |
| 内容 | `mcp-server/songs/` 40 ogg + catalog.json 40 条（旧 3 首换装 id 不变）；生成器 `tmp/songs_melodies.py`/`tmp/songs_make.py`/`tmp/render_songs.sh`（工艺脚本不入库维护面，旋律为手写简谱转录） |
| 服务器运行态（不入库） | 双机 `$HUTUJI_OUTPUT_DIR/songs/` 40 ogg+catalog tar 同批落盘，备份 `/tmp/songs-bak-*`；公网 canon/silent 200、穿越 404 |
| 未验证 | 真机点播实听（音色/响度/节奏）；曲名语音召回实机 |

## 2026-09-12 登录事故热修：补 f71d347 隐藏依赖（已提交 `e05b796`，双机已部署）

| 项 | 说明 |
|----|------|
| 授权 | 用户报「现在搞的小程序登录都有问题了」+ `f71d347` 的 app 侧调用点已提交（既有 commit 契约要求 store 方法存在） |
| 提交 | 本仓 `e05b796` 仅 store.py（+94/−2）：`user_device_bindings` DDL + `list/select/_sync_active_binding`（逐字取自 bind-claim WIP）+ select 兜底抄入主表 + `delete_binding_for_mac` HEAD 签名双表清 + users 迁移 `active_device_mac`+回填；用 `git hash-object`+`update-index` 提交，**bind-claim WIP 工作树一字未动** |
| 部署 | 双机 store.py `9d5752b2` LF blob SHA 逐位相符 restart active；lima 真库直调读出真实绑定；nginx `/draw-upload/api/auth/me` 未登录 401（原 500） |
| 遗留 | bind-claim WIP（app.py/models.py 全量 + device_identities/bind_identity_challenges 流）仍在工作树未提交——deploy-from-HEAD 规则下再次全量部署会再断 /auth/me；待用户批次收口提交后重部署 |

## 2026-09-12 小派设置批：音色/模型切换 per-user 克隆 agent（已提交 `8970f4a`，双机已部署+控制台四形状生产探针）

| 项 | 说明 |
|----|------|
| 授权 | 用户原话「可以实现吗（小程序里切换音色和模型）」+ 反转口径「用户不能改所有人的模型和音色啊」（2026-09-12）；此前「一机一 agent 没必要太乱」已被本决策 supersede——克隆仅按需发生，未个性化设备零变化 |
| 提交 | 本仓 `8970f4a`（mcp `xiaozhi_console.py` + server 四内部路由 + `test_xiaozhi_console.py` 16 项；portal `xiaopai.py` + app 三端点 + `test_xiaopai.py` 9 项 + weapp 小派设置页/聊天 ⚙；AGENTS 计数 1413/38 文件） |
| 服务器运行态（不入库） | 双机 mcp 两文件 + portal 三文件 LF blob SHA 逐位相符（server `1cf42018…`/console `8f78aecd…`/app `27de75ff…`/store `6ff83985…`/xiaopai `48548447…`）；jd mcp.env `HUTUJI_USAGE_INGEST_SECRET` 对齐 portal 值（原不一致，热备隐病） |
| 证据 | lima options 200（61 音色）/clone→agent 2348797→config 写入 200/无效码 400/共享 agent 拒改/测试克隆即删；jd options 200；MCP 1413/1413、portal 232/232 |
| 未验证 | 真机绑码后切音色实听、weapp 体验版上传（聊天页 ⚙ 已入库） |

## 2026-09-12 深夜 TG 运维通知面双机上线（已提交系列，双机已部署+端到端投递有证）

| 项 | 说明 |
|----|------|
| 提交 | 本仓 `29d6ea8`→`e069216`→`6095279`→`2fee942`→`ced9292`→`8e93fde`（deploy/tg-notify.sh + tg-relay-server.py + hutuji-tg-relay.service + tg-ops-check/xiaozhi-token-watch 出口收敛 + 注释口径 + readiness/deployment/inventory + 指纹基线） |
| 授权 | 用户原话「做一个 tg 的 bot，我们双机部署有什么异常都能推到我 telegram」（2026-09-12） |
| 服务器运行态（不入库） | lima：`hutuji-tg-relay.service` enabled+active（bind 127.0.0.1:8092）；gray conf 公网 vhost 插保密 location（allow jd IP，备份 /tmp/hutuji-gray.conf.bak-tg）；`/etc/hutuji/tg.env`（TG_BOT_TOKEN/TG_CHAT_ID/TG_PROXY）。jd：`tg.env`（TG_RELAY_URL）。双机 `hutuji-notify@.service` + OnFailure drop-ins、cron `41 * * * * tg-ops-check` + `3 8 * * * --digest`（jd 带 JD_HOT_STANDBY=1） |
| 已清场 | tailnet HTTP 收口（8092 被 tailnet ACL 挡死）、SSH 中继（tailnet :22 握手挂起、公网 :22 jd HIDS 疑似节流 3 连 KEX 挂死）：lima authorized_keys 条目、jd 中继 key、tg-relay-recv.sh 全删 |
| 证据 | lima 直发 ok:true message_id:2；jd 中继自检/digest/unit 模板自启（01:31:39 tg-relay: req）零告警；hub PASS（1397 pytest + e2e PASS + 指纹 262 全对） |
| 未验证 | 通知告警阈值语义化、真实 unit failed 触发复测（模板路径已实测） |

## 2026-09-12 深夜 图库 TG 频道冷备份 PoC + 同日升级召回（已提交 `f89fa25`/`5785c66`/`e19eecb` 系列，lima 已部署+生产召回演练）

| 项 | 说明 |
|----|------|
| 提交 | `f89fa25`（telegram_archive.py 新 + app/store/models + test_telegram_archive 10 项 + privacy-policy 第 4 条 + PoC 设计稿状态翻转）→ `cfe154f`/`ca2cdab`（protocol §10.4.5 契约行 + readiness/deployment 登记 + 指纹重登）→ `5785c66`（worker 不再全局绑定首个 Store——`_Task` 自带 store/user_id，修横库缺陷；新表 `telegram_archive_log` 台账 + 四方法；`restore_user` 分用户召回 + `__main__` CLI；删除端点同步标台账 removed；归档测试 15 项）→ `e19eecb`（补回重写遗漏的 `_json_or_none`——生产 `_upload_document` NameError 实测抓到） |
| 授权 | 用户原话「电报无限图床可以做吗」「无限图床只能用频道吧」+ 频道 `t.me/danqing45`（2026-09-12）；同日风险核实原话「归档 worker 是模块级单例…后续任务可能写入第一个数据库」「要做分用户批量召回照片的功能」 |
| 服务器运行态（不入库） | lima draw-portal.env 增 `TG_CHANNEL_ID=-1004350290412` / `TG_PROXY=http://127.0.0.1:7890` / `HUTUJI_TG_UPLOAD_ENABLED=1`（TG_PROXY 是本批 env 坑：python 直发 curl 无代理 → http=0）；tg.env 增 TG_CHANNEL_ID；jd 不启用（无代理） |
| 证据 | 频道探针 500KB sha256 一致（message_id=4，后撤回）；生产归档：真实预览 `_process` 全链 → message_id=8 → sha256 一致 → `uploaded`+台账入账；**生产召回演练**：删盘上 PNG → CLI `--restore-user` → `{"restored":1}` → 文件回位 sha256 与删除前逐位一致（`e1e4f682…`）；portal **202/202**；本机 scp 工作副本 CRLF 与 blob 不符——发布候选一律 `git cat-file blob` 导出（第二次传） |
| 未验证 | 存量图库回填归档、weapp `tg_backup` 徽标、连发 50 张 429 实测（退避有单测）、trim 重建行生产实测（单测有钉） |

## 2026-09-12 小派人格批：丹青改名 + 无换纸口径（已提交 `94a91cf`，双机已热更，云控制台已 sync）

| 项 | 说明 |
|----|------|
| 提交 | 本仓 `94a91cf`（15 文件：role-prompt/KB/chat.py/portal 5 文件 + test_contract 改钉 + xiaozhi-agent README + readiness/inventory/deployment/protocol/行号基线）；父 `a09aa2d` |
| 授权 | 用户原话「没有换纸功能了呀，这（提示词）还是说自动换页写字机」「继续」（2026-09-12）；改 prompt/KB 属上云文案面 |
| 触及面 | `docs/xiaozhi-agent/role-prompt.md`（丹青自称+删换纸播报+写字单页口径）、`knowledge-base.md`（呼图机半区 10 处换纸承诺→停笔/手动取纸、可画区 190×277、半区统一丹青；奎享厂商半区不动）；mcp `chat.py` 与 portal `voice_agent.py` SYSTEM_PROMPT、portal `app.py` 绑定话术/`voice_jobs.py` 推送话术/`static/index.html` 出图文案；`tests/test_contract.py` 换纸时序测试改钉反向声明 |
| 回归 | portal **183/183**（33s）；mcp test_contract+test_chat **668 passed**（24.5s）；门禁 hub 复跑见本日末行 |
| 部署 | 双机 5 文件（chat.py/app/voice_agent/voice_jobs/index.html）HEAD blob LF 导出 SHA-256 逐位相符（本地 `sha256.txt`：chat `925def30…` app `83799c6e…` voice_agent `00a8ba28…` voice_jobs `e4c4b58e…` index `7439dbcd…`），备份 `/root/hutuji-bak-20260912-persona/`；py_compile 过；重启阿里 mcp+bridge+portal+admin+worker@{1..8} 12 active、京东 mcp+portal active；公网 portal/admin 200、`/mcp` 404、bridge 云 ping 正常（journal 00:31:45 ping/response 2） |
| 云控制台 | JWT 取自 lima `/etc/hutuji/mcp.env` `HUTUJI_XIAOZHI_TOKEN`（仅内存，不落盘不打印）；`sync 2094251` 成功：角色提示 2771 字符更新、KB 新文档 md5 `77d1d824ee76…`（旧 `publish_tmp.md` id=70008 已删）；`recall` 3/3：没纸→停笔条、画完会自动换纸吗→「不支持自动换纸」第 1 命中、丹青能画彩色吗→丹青命名条 |
| 一次性产物 | `tmp/deploy_persona_0912.sh`（ssh/scp 部署脚本）、`tmp/deploy-20260912-persona/`（发布候选）；paramiko 密码路已死（09-10 双机关密码登录），后续部署一律走 ssh 别名/公钥 |
| 未验证 | 真机语音复听（设备自称为丹青、无换纸话术）——下次设备开机复听；KB 语义质量仅召回验证，未过真机端到端 |

## 2026-09-12 AI 写字单页适配 + 设备文案「丹青」批（已提交 `a09aa2d`，双机已部署；weapp 0.3.7 已上传）

| 项 | 说明 |
|----|------|
| 提交 | 本仓 `a09aa2d`（30 文件：write_text/server/test_write_text + portal pipeline/app/voice_jobs + 5 处测试 stub + 新 `tests/test_write_pipeline.py` + weapp 16 文件改名 + AGENTS/protocol/inventory）；领先 origin/main 未推 |
| 授权 | 用户原话「AI 写字的最好也是单张纸」「小程序的文案不要写写字机，起个高大上的名字」（2026-09-11）；设备名「丹青」实施后已告知 |
| 触及面 | mcp `write_text.py`（`layout_text_fit_single` + `replace` import）、`server.py`（`hutuji_write size_mm=0` 哨兵 + `_run_write_job fit_single_page`）；portal `pipeline.py`（`process_write_text fit_single_page`）、`app.py`（`body.size_mm is None` → fit）、`voice_jobs.py`（恒 fit + 推送文案「丹青」）；weapp 全量「写字机」→「丹青」 |
| 回归 | MCP 全套 **1395/1395**（test_write_text 47→50：2 页缩号成 1 页/4mm 地板回落 12 页/单页与地板字号直通）；portal **183/183**（新 `test_write_pipeline.py` 2 项路由 + 5 处 stub 补 kwarg）；`test_test_count_claims_self_consistent` 钉 AGENTS↔inventory 一致（曾 1 红→台账同步后绿） |
| 部署 | 双机 5 文件 HEAD blob SHA-256 逐位相符（明细 `docs/deployment.md` 首条）；备份 `/root/hutuji-bak-20260912-writefit/`（双机）；重启阿里 mcp+bridge+portal+admin+worker@{1..8} 全 active、京东 mcp+portal active；冒烟 bridge-health 200/portal 200/admin 200 |
| 生产冒烟 | 双机 venv+真数据直调：阿里 `layout_teach("中义你我他")`→1 页（**09-11 笔顺批漏重启 portal 的存量问题随之收口**——portal 进程内 import mcp 模块，模块变更必须连 portal 重启）、`layout_text_fit_single` 200 字 2 页→1 页；京东同款 1 页/1 页 |
| 小程序 | weapp **0.3.7** CLI 上传 TOTAL **115128 B**（`tmp/weapp-upload-0.3.7.json`）；16 文件 38 处「写字机」→「丹青」（含转发标题/发送按钮/导航标题/绑定配网话术/项目描述）；`node --check` + JSON parse 全过；**MP 后台选 0.3.7 体验版是用户动作** |
| 一次性产物 | `tmp/deploy-20260912-writefit/`（LF 发布候选）、`tmp/weapp-upload-0.3.7.json`；不入库 |
| 未验证 | 实机纸面（缩号后的字号观感、长文回落多页行为）；真机冒烟清单 42 项未过 |

## 2026-09-10 近期评审七项修复（已提交已推 `5062272`+`a776cc6`+证据回填 `bba1284`，双机已部署；量产机已烧录点亮）

| 项 | 说明 |
|----|------|
| 起点与授权 | `main` / `d40157a`；用户针对七项评审发现回复「解决处理，」。实现与回归路径登记于 `agent-edit-allowlist.json` 的 `recent_review_fixes_20260910_authorized` |
| MCP | `bitmap_svg.py` 限预览栅格单边 2048、先裁线再分配坐标；`sketch_render.py` 蛇形只连紧邻行且全程暗区；`server.py` 日限移入新任务登记，保留取消隔离和同题复用 |
| Portal / 小程序 | `pipeline.py` 显式字体不被标准笔顺覆盖；`app.js` 登录代次作废旧回包，`utils/api.js` 旧 401 不清新会话；创作页默认字体不传 `font` |
| 部署模板 | `nginx-hutuji.conf` 恢复 `:80 /firmware/`，只许 loopback GET/HEAD；本批未部署、未上传小程序、未改两固件仓 |
| 回归 | MCP 当前收集全套 **1453** 项（40 文件；2026-09-13 三仓评审 +2 → 新增 `test_machine_sku.py` 2 项；2026-09-11 读闸热修 +1 → `test_output_store.py` 5 项、笔顺多字单页字帖批 +1 → `test_stroke_order.py` 9 项、AI 写字单页适配批 +3 → `test_write_text.py` 50 项、2026-09-12 TG 通知批 +2 → `test_contract.py` 656 项、2026-09-10 评审批遗留 CLAHE 主体层次回归 +1 → `test_sketch_render.py` 17 项、2026-09-12 并发会话程序化渲染批 +8 → `test_sketch_render.py` 25 项、手写抖动批 +5 → `test_write_text.py` 55 项、2026-09-12 忙批 +10 → 新增 `test_bitmap_busy.py` 10 项、照片精修批计数台账同步）全部通过；新增预览资源边界、白带/行间白缺口、并发/取消/accepted 达日限重试、双入口 OTA 静态契约、合成 gfont 真排版；Node 独立运行登录/字体选择测试。**hub PASS（MCP 1383/1383、e2e PASS）、portal 173/173、Node 19/19**（2026-09-10 评审批时的历史数值，不是当前口径），各套件退出码均为 0 |
| 量产机速度参数（2026-09-11 用户要求「xyz轴速度提起来」） | **分两档**：①指纹白名单 4 项已生效——$112=3000（Z 限速 600→3000，抬笔 10→50mm/s）、$120=$121=1500、$122=300（XY/Z 加速 500/500/60→1500/1500/300）；②$110/$111（XY 最大速率）**被 S3 固件「Grbl 设置指纹防篡改」锁死**（`hutuji_nopaper_core.h kGrblSettingGoldensNopaper` 12 项：$1/$3/$20/$21/$22/$100/$101/$110/$111/$130/$131/$132，$110/$111 金标 5000=c896ce5c 机头默认）——我改成 12000 后指纹不符 → 管线 `ready_=true` 但 `settings_verified_=false` → confirm 返回 `grbl_settings_mismatch`、任务不发（用户报「发送笔顺时卡住」根因；串口实锤 `E HutujiPipe: Grbl 设置指纹不符（$110）`+notify「写字机参数被改动」，抓包 `tmp/hil-redraw-com15.txt` t=12140ms）。已回滚 $110/$111=5000 并实证两次重连指纹重过（t=60080/192670ms）。**教训：量产机改 Grbl 运行参数前必先对 hutuji_nopaper_core.h 金标表**；提速取证 `tmp/grbl-settings-com3-massprod.txt`。坑：COM3 打开即触发板上复位（CH340 DTR，本机电脑侧已知），首次写入撞 boot banner 丢失——稳态脚本（等 `Grbl 1.3a` 提示符+逐条收 ok+`$$` 回读）全过。**XY 顶速（133mm/s）仍锁 83mm/s，解锁须改金标+OTA（待用户拍板）**；笔顺短段为主的任务由加速度主导，4 项已约 3× |
| 本地证据 | `results/review-fixes-20260910/`：最终本仓 hub 为 `hub-final.log` / `hub-final-report.{json,md}`（2026-09-10T14:52:26Z，含 MCP 全量 + e2e），pytest 原始输出为 `mcp-pytest-final.log`；`portal-recheck.log`、`weapp.log` 为独立套件；各 `.exit` 记录退出码，`jobs.json` 记录命令、进程与八份实现文件 SHA-256。前两轮 MCP 仅测试计数台账失配，已按 collect 同步；portal 首轮 172 过 / 1 项临时 SQLite 库损坏，单例复跑过、新临时目录全量复跑 **173 过**，损坏原因未归因；Node **19 过**。失败轮次日志原样保留；`results/` 被忽略，证据只在本机 |
| 保留既有改动 | 开始时 12 份文档的行号调整保留（handoff、daytime、两份 design、六份 kxnx findings、readiness、troubleshooting）；后续门禁重定位单独核对。既有 `tmp/**`、截图、记忆草稿与 `.playwright-mcp/` 保留 |
| 提交 | `5062272`（37 文件：七项修复 + 文档/白名单/指纹基线）+ `a776cc6`（weapp 0.3.3 脚注）；已合远端 PR #9 merge 并推 origin（`d1fcd0e`） |
| 部署 | 双机四文件 SHA-256 逐位相符（备份 `/root/hutuji-bak-20260910-review/`）；京东 `:80 /firmware/` 按 HEAD 模板三处同写并 restart nginx，loopback 200/公网 403；preflight 阿里 25/33、京东 30/33（持平加固批基线）；weapp 0.3.3 CLI 上传 TOTAL 106500 B。明细见 `docs/deployment.md` 首条 |
| 量产机烧录 | **已完成（2026-09-11 凌晨）**：短接 IO0 上电 + `--before no-reset` 烧入 massprod 固件（DIO 版；首刷 QIO 看门狗循环已翻案并重刷）；开机 `[VER:1.3a.20260910:]`、`$RST=$` 后金标指纹 12/12 MATCH；本机 DTR 自动下载坏系电脑侧问题（对照板同症、另一台电脑可烧），板子无罪。详见下方「量产无换纸 SKU 批」刷机阻塞行的解决记录 |

## 2026-09-11 小程序改进批（已提交 `2d81300`，双机已部署；weapp 0.3.4 已上传）

| 项 | 说明 |
|----|------|
| 提交 | 本仓 `2d81300`（portal/weapp 合规件 23 文件，见 `docs/deployment.md`）+ 同日 `a00f1dd`/`93ad7f6`（品牌更名与分享按钮批）+ `d10fe85`（笔顺多字单页字帖批：`stroke_order.py`/`server.py`/`test_stroke_order.py`+AGENTS 计数 1392），全部领先 origin/main 未推 |
| 回归 | MCP 当前收集全套 **1397** 项（38 文件；2026-09-11 读闸热修 +1 → `test_output_store.py` 5 项、笔顺多字单页字帖批 +1 → `test_stroke_order.py` 9 项、AI 写字单页适配批 +3 → `test_write_text.py` 50 项、2026-09-12 TG 通知批 +2 → `test_contract.py` 642 项）全部通过；新增预览资源边界、白带/行间白缺口、并发/取消/accepted 达日限重试、双入口 OTA 静态契约、合成 gfont 真排版；Node 独立运行登录/字体选择测试。**hub PASS（MCP 1383/1383、e2e PASS）、portal 173/173、Node 19/19**（2026-09-10 评审批时的历史数值，不是当前口径），各套件退出码均为 0 |
| 小程序 | weapp 0.3.4 CLI 上传 TOTAL 114342 B（`tmp/weapp-upload-0.3.4.json`）；同日更名批 `a00f1dd`（品牌 `动力巢`→`动力巢科技` + 页脚 v0.3.3→v0.3.5，0.3.4 批漏页脚 bump 用户点名）→ weapp **0.3.5** CLI 上传 TOTAL **114432 B**（`tmp/weapp-upload-0.3.5.json`）；更名批 `93ad7f6`（结果卡可见分享按钮 + 页脚运行时版本根治漏 bump）→ weapp **0.3.6** CLI 上传 TOTAL **115239 B**（`tmp/weapp-upload-0.3.6.json`）；**MP 后台选体验版仍为用户动作（0.3.4–0.3.6 均躺在开发版本列表，用户看到的仍是旧体验版）**；建订阅模板未做 |
| 测试 | portal 187/187（+12 新增：订阅 8 + 分享 4 + 固件 2 对账）；契约 §10.4.16 |
| 一次性产物 | `tmp/deploy-20260911-weapp/`（LF 发布候选）、`tmp/weapp-upload-0.3.4.json`；不入库 |
| 未验证 | 真机冒烟清单 42 项未过（`draw-portal/weapp-smoke-checklist.md`）；订阅消息真实下发（依赖 MP 模板）未验证 |

## 0. 2026-09-09 收口入库（本批提交）

| 项 | 说明 |
|----|------|
| 产品 | weapp LiMa 主题 + 控机疏密至 **0.2.10**；portal send **自动 confirm**；imagefree 默认关；Agnes 400 短 prompt 再试；CJK 笔顺写字链（既有工作树一并入库） |
| 生产 | 双机已热更；备份 `/root/hutuji-bak-20260909-{imagefree,autoconfirm}/` |
| 仍脏/不入库 | `tmp/**`、本地截图/记忆草稿、`.playwright-mcp/`、外仓固件未推项以各仓 status 为准 |

### 0.0 记忆无感 + 小程序 AI 写文（2026-09-09 17:5x，同 worktree）

| 项 | 状态 |
|----|------|
| hub 路径 | `D:\Users\hutuji\.worktrees\xiaopai-ota` / `feat/xiaopai-ota-mass-prod`（未提交改动含 article_llm/portal write/ai、role-prompt、weapp 0.2.15、契约/readiness） |
| xiaozhi | `hutuji_memory.{h,cc}` + `hutuji_memory_core.h`；三板 `InitializeTools` 挂载；`PROJECT_VER=hutuji.1.0.5`；host `test_hutuji_memory_core.py` 绿。**脏树勿覆盖**既有 `hutuji_draw_bind.cc` 等 |
| 生产 | 双机 portal+`article_llm` 已热更；LLM env 已注入；角色 `2094251` 已 sync；weapp **0.2.15** 已 CLI 上传；**catalog=`hutuji.1.0.6`**（sha `6f119063…9b26`）；COM15 板上 **1.0.6**；记忆 messaging 四步 HIL ✅ |
| 未做 | 真人语音记/召口述；AI 写文真机；MP 体验版勾选 |

### 0.1 小派量产 OTA worktree（2026-09-09，`feat/xiaopai-ota-mass-prod`）

| 项 | 说明 |
|----|------|
| 路径 / 分支 | hub worktree `D:\Users\hutuji\.worktrees\xiaopai-ota` / `feat/xiaopai-ota-mass-prod`（相对主工作树隔离；**勿**用主树 `git checkout -- .` 覆盖） |
| 设计 / 计划 | 已批准：`docs/superpowers/specs/2026-09-09-xiaopai-ota-mass-prod-design.md` → `docs/superpowers/plans/2026-09-09-xiaopai-ota-mass-prod.md` |
| hub 已提交链（摘） | `8e052ca`/`acddb83`/`9828d11`/`75d7550` portal catalog+API；`f11e2e6`/`305636c` nginx `/firmware/`+发版脚本；`3a63962`/`427eb9d`/`05c2c6c` protocol OTA 字段与工具；`908e959`/`dc7f2f3` weapp 固件页+onShow 轮询 |
| xiaozhi（`D:\Users\xiaozhi-esp32`） | 量产 OTA 提交：`73257fa` StatusJson+ParseVersion → `9194e60` 空 reason 省略 → `fcaca02` `hutuji.ota_check`/`ota_start`+每天自检+`PROJECT_VER=hutuji.1.0.0`+三板挂工具 → `942fcfb` 日历日 snprintf 钳位。**未推 origin 以现取 `git status`/`git log origin..HEAD` 为准** |
| xiaozhi 脏树 | `main/boards/lichuang-dev/hutuji_draw_bind.cc` 有未提交改动（+22 行，与 OTA 批无关——**禁止覆盖**）；另有本地 `tmp_build_*.{bat,log}` / `pp_*.{bat,txt}` 等不入库——**2026-09-24 已清理**：xiaozhi 仓 38 个未跟踪项全删（tmp_build/tmp_export/tmp-nopaper-build 日志 + pp_out.txt 输出 + tmp_build_*.bat/pp_cmd.bat 一次性构建脚本 + `build.identity-incomplete-20260912-111426-299161/` 未完成构建目录；用户授权措辞为「tmp 日志清理」，.bat 脚本与构建目录超出字面范围但已一并删除、未跟踪文件不可恢复，均为一次性导出/构建包装，重建只需重跑 export.bat+idf.py build），工作树现为 0 行干净 |
| 生产 `/firmware/`（2026-09-09 18:2x） | latest/catalog=`hutuji.1.0.6`（sha `6f119063…9b26`）；COM15 板上同版本；记忆工具 messaging HIL 四步 ok；语音口述未做 |
| 记忆隔离（2026-09-09 17:2x） | 双机已热更 SHA 见 readiness；角色提示已上云 `2094251`。**未**做双设备语音 HIL。产品上「扫码绑机」≠「记忆自动带 MAC」 |
| HIL | 条目 1–6 **本会话未跑＝未验证**（见 `docs/release-readiness.md` 文首 + 状态总览 #18） |
| 白名单 | `docs/agent-edit-allowlist.json` 键 `xiaozhi_ota_mass_prod_authorized` 登记本批授权路径意图 |

### 0.2 小程序写字 P0（2026-09-09，同 worktree `feat/xiaopai-ota-mass-prod`）

| 项 | 说明 |
|----|------|
| 设计 / 计划 | 已批准：`docs/superpowers/specs/2026-09-09-xiaopai-write-ux-design.md` → `docs/superpowers/plans/2026-09-09-xiaopai-write-ux.md` |
| hub 已提交链 | `f79394d` 版式校验 → `819d7df` `process_write_text` → `981c723` `POST /api/draw/write` → `0b7bc95`/`ab31763` 文档 upload 版式 → `c6922f7`/`b663e55` weapp 写字模式+翻页（**v0.2.13**） |
| 触及面 | portal `app.py`/`pipeline.py`/`write_layout_params.py` + 测试 `test_write_*`/`test_doc_upload`；weapp `pages/index/*`、`utils/api.js`、页脚版本 bump |
| HIL | 纸面/真机 **未验证**（见 `docs/release-readiness.md` 文首写字 P0 条） |
| 生产 | 2026-09-09 随笔顺同批双机 portal 热修；weapp 随 **0.2.14** |
| 白名单 | `docs/agent-edit-allowlist.json` 键 `xiaopai_write_ux_authorized` |

### 0.3 小程序笔顺 P0（2026-09-09，同 worktree `feat/xiaopai-ota-mass-prod`）

| 项 | 说明 |
|----|------|
| 设计 / 计划 | 已批准：`docs/superpowers/specs/2026-09-09-xiaopai-teach-ux-design.md` → `docs/superpowers/plans/2026-09-09-xiaopai-teach-ux.md` |
| hub 已提交链 | `07c52e5`/`98b14b1` 抽字 → `8d73f47` `process_teach_text` → `c8fde0a` `POST /api/draw/teach` → `102bc59` weapp 笔顺模式（**v0.2.14**）→ `d71c52a` 文档收口 |
| 触及面 | portal `teach_chars.py`/`pipeline.py`/`app.py` + `test_teach_*`；weapp `pages/index/*` 三段 |
| HIL | 纸面/真机 **未验证**（见 `docs/release-readiness.md` 文首笔顺 P0 条） |
| 生产 | 双机 portal 10 文件热修 ALL_OK；weapp CLI **0.2.14** 已传（80701 B）；MP 选体验版待用户 |
| 白名单 | `docs/agent-edit-allowlist.json` 键 `xiaopai_teach_ux_authorized` |
| 注意 | Task 5 曾误在主工作树落 `ab0be39`——**已于 2026-09-09 将 main tip `reset --hard` 回 `3538f44`**；功能真值在本 worktree `d71c52a` |

### 0.4 小程序控制页进度 P0（2026-09-09，同 worktree `feat/xiaopai-ota-mass-prod`）

| 项 | 说明 |
|----|------|
| 设计 / 计划 | 已批准：`docs/superpowers/specs/2026-09-09-weapp-progress-voice-design.md` §2 → `docs/superpowers/plans/2026-09-09-weapp-progress-p0.md` |
| hub 已提交链（摘） | Task 1–4：`job_progress.js` 纯函数 → `control.js` 自适应轮询 → `control.wxml/wxss` 时间线/页条/ETA → 三页脚注 **v0.2.16**（`832e766` 等，以现取 `git log` 为准） |
| 触及面 | `draw-portal/weapp/utils/job_progress.js`；`draw-portal/weapp/pages/control/*`；`pages/profile/profile.wxml`、`pages/firmware/firmware.wxml` 脚注 |
| HIL | 控制页进度手测 **未验证**（见 `docs/templates/weapp-control-smoke.md` 行 9–12） |
| 生产 | weapp **0.2.16** CLI 上传与 MP 体验版勾选 **待用户** |
| 白名单 | `docs/agent-edit-allowlist.json` 键 `xiaopai_progress_p0_authorized` |

### 0.5 小程序语音聊小派 P1（2026-09-09，同 worktree `feat/xiaopai-ota-mass-prod`）

| 项 | 说明 |
|----|------|
| 设计 / 计划 | 已批准：`docs/superpowers/specs/2026-09-09-weapp-progress-voice-design.md` §3 → `docs/superpowers/plans/2026-09-09-weapp-voice-asr.md` |
| hub 已提交链（摘） | portal `voice_agent.py` + `/api/voice/asr|turn` + 测试；`e9a6e01` weapp chat 页 hold-to-talk + 我的页入口 + 脚注 **v0.2.17** |
| 触及面 | `draw-portal/draw_portal/{voice_agent.py,app.py,tencent_asr.py}`；`draw-portal/tests/test_voice_{agent,api}.py`；`draw-portal/weapp/pages/chat/*`；`draw-portal/weapp/pages/profile/*`；契约 `docs/protocol.md` §10.4.13 |
| 生产 | 腾讯云 ASR + LLM 三键已注入双机 draw-portal.env 并 restart（`ASR_CONFIGURED True`；未登录 asr→401）；weapp 现以 **0.2.19** 为准（见 §0.6） |
| HIL | 语音 ASR/turn 真机 **未验证**（见 `docs/templates/weapp-control-smoke.md` 行 13–15） |
| 白名单 | `docs/agent-edit-allowlist.json` 键 `xiaopai_voice_p1_authorized` |

### 0.6 语音画+写 + 小程序确认（2026-09-09，同 worktree）

| 项 | 说明 |
|----|------|
| 设计 / 计划 | 已批准：`docs/superpowers/specs/2026-09-09-weapp-voice-draw-write-design.md` → `docs/superpowers/plans/2026-09-09-weapp-voice-draw-write.md` |
| 触及面 | `voice_jobs.py`；`voice_agent` 增 `voice.draw`/`voice.write`；`app.py` `/api/voice/confirm|cancel`；weapp chat 预览卡；`tencent_asr` 去 AppId；契约 §10.4.13；脚注 **v0.2.19** |
| 生产 | 双机 portal 已 scp 热更上述模块；CLI upload **0.2.19** TOTAL **94207 B**（`tmp/weapp-upload-0.2.19.json`） |
| HIL | 画/写→预览卡→开始画落笔、取消预览 **未验证**（冒烟行 16–18） |
| 白名单 | `xiaopai_voice_draw_write_authorized` |
| 脏树注意 | 曾误用 PowerShell `Set-Content` 弄坏部分 wxml 编码；已 `git checkout` 恢复 bind/history/index/wifi 后再上传 |

## 1. hutuji 本仓（`D:\Users\hutuji`）
### 照片 i2i 质量批（2026-09-10，已提交 `21699a8` + 双机已部署）

| 项 | 说明 |
|----|------|
| 决策 | ① 提示词 v6：按 Qwen-Image-Edit 官方 EDIT_SYSTEM_PROMPT（`src/examples/tools/prompt_utils.py`）重构——指令直述、人像身份约束前置（「其余保持不变」官方范式）、风格特征压句尾、否定排比保留；v5 实证排线词原样保留只调语序。supersede v5。② 骨架算法 zhang→lee：5 张生产精修样本 A/B（`tmp/ab_lee_vs_zhang.py`，样本拉自 `/var/lib/hutuji/draw-portal/` jobs 5198853e/805fa9b7/0c278f1f/5eebf7f9/6c1f3390）抬笔 138→128、144→144、115→99、94→89、130→133，净 -31（3 胜 1 平 1 微负），点数不降反微升=细节未丢 → **仅精修链切 lee**（证据只覆盖精修照片），t2i/QD/写字/笔顺共用描摹维持 zhang 默认。③ 奎享 SquiggleTab 亮度波纹连续线（抬笔≈0）与 l/c.java 路径拼接 = 数量级杠杆候选，本批只做设计定案外的小步；2511 模型/多图 ControlNet 输入被魔搭余额 429 阻塞 |
| MCP 侧 | `bitmap_svg.trace_bitmap_to_polylines` 加 keyword-only `skeleton_method="zhang"` 透传 skimage；`modelscope_client.py` `I2I_LINE_ART_PROMPT`/`I2I_SPARSE_HATCH_RETRY_PROMPT` v6 |
| portal 侧 | `pipeline.process_upload_png` 加同名透传参数（默认 zhang，Canny 快出段不变）；`app.py` `_refine_photo_job` 精修描摹传 `skeleton_method="lee"` |
| 测试 | mcp `test_contract.py` 631→**632**（+lee 契约：非空折线/量纲 0.5–2×/bogus 方法 fail-closed 收 BitmapError）；当日程序化渲染+设备日限批再 +24（`test_sketch_render.py` 14、`test_device_daily_limit.py` 9、`test_write_text.py` +1 字体列表），全套 **1363** 项全绿；portal `test_photo_refine.py` 假函数补 kwarg（签名适配，无语义变更），全套 **173** 项全绿 |
| 验证 | hub 门禁 PASS（1339 + e2e PASS + 指纹 260：2 处行号纯移动 --fix-line-refs 自动（findings/30 两处）、1 处人工核对后 --accept-line-refs（findings/28 计数行内容随批次更新，引用意图不变）） |
| 部署 | 已完成 02:1x CST：四文件双机 SHA 逐位相符 + 阿里云 mcp/bridge/portal/worker@{1..8} 与京东 mcp/portal restart active + 公网冒烟 + preflight 阿里 24/33·京东 29/33（均符合节点基线，无新 fail 类别；证据 docs/deployment.md 顶部） |
| 待办 | ① ~~真实照片单观察 v6+lee 抬笔数~~（已被程序化渲染批 supersede——AI 精修链退役，v6+lee 指标不再进生产）② **魔搭次日免费额度恢复后**验证 2511/多图输入 + **Qwen 侧 v6/v7 补验**（2026-09-10 用户拍板：魔搭每日有免费额度、当日用完即停、**不充值**；TCB 混元赛道实证：两臂无可测差异，见下行；补验口径 = **每臂同图 ≥3 次取均值、先量噪声地板再判**）③ ~~SquiggleTab 立项拍板~~（已定案：双风格档位同批落地） |
| v7 实证（2026-09-10，TCB 单赛道） | 探针 `tmp/tcb_prompt_ab.py`（节点 /tmp 同名副本）：5 张生产样本 × {v6 否定排比, v7 正向锚定零否定词} 配对 A/B，lee 描摹三指标。首轮配对抬笔 v6 149/128/116/132/148 vs v7 164/185/158/110/162；视觉抽验两张均清晰单线+稀疏修长排线+主体高度可辨，**两臂右下均带混元「AI生成」水印**（生产 `strip_corner_watermark` 已覆盖，探针指标未洗白、两臂同担）。**噪声地板实测推翻配对读数**：0c278f1f 两臂各连跑 3 次——v6 128/204/177（摆幅 76）、v7 185/148/200（摆幅 52），run-to-run 摆幅（52–76）远大于任何配对差（14–57），5 样本配对差全部落在噪声带内。**修正结论：混元赛道 v6/v7 无可测差异，首轮 4/5 是噪声不是效应；维持 v6 属保守惯性（免换提示词重验证），不是实证胜出**。txt2plotter 禁否定句经验在混元上既不证实也不证伪——端点无 seed 控制，单运行配对方法学上限即此；Qwen 侧复验必须先量同法噪声地板再判胜负 |
| 风格目标（已定案） | ~~「排线素描感」与「SquiggleTab 亮度波纹单连续线」互斥待拍板~~ → 2026-09-10 用户拍板 A+B 双风格档位同批落地（hatch 默认 + squiggle 次档），见下行「程序化风格渲染 + 双端日限批」 |

### 程序化风格渲染 + 双端日限批（2026-09-10，用户原话「必须有排线素描感，要有艺术感，这搞的很丑，还有别的方案」「限定文生图每天用户20次」；拍板 A+B 双风格档位 + 限流双端）

| 项 | 说明 |
|----|------|
| 决策 | ① **照片精修链从 AI i2i 切到程序化矢量渲染**（`sketch_render.py`）：位图→骨架化的信息损耗是结构性的，提示词调优已撞噪声地板（见上批 v7 实证行），模型路线到天花板；程序化直出矢量折线，风格是算法本体、零模型费、确定性可复现。② 双风格档位：hatch 排线素描（默认，用户硬约束「必须有排线素描感」）+ squiggle 连续波纹（SquiggleTab 方向同批落地，旧「待拍板」关闭）。③ 文生图限流 20 次/天双端：portal `/api/draw/generate` 既有 `generate_daily_limit` 默认 10→**20**；MCP `hutuji_draw` 新增**单设备每日 20 次**（`HUTUJI_DRAW_PER_DEVICE_DAY`，既有每分钟 6 次不动），内存/Redis 队列 + 进程内回落三路径同行为，UTC+8 自然日键、入队时刻计数、超限白话「今日绘画次数已达上限，明天再来」。④ 魔搭/TCB i2i 客户端模块保留为备用通道但链路不再调用（portal config 四个 i2i 字段已删）；合规面：照片精修不再出服务器 |
| MCP 侧 | 新增 `sketch_render.py`（排线/波纹双渲染器 + RDP 抽稀出口 + `SketchRenderError` fail-closed；产物契约同 `trace_bitmap_to_polylines`，直进 `polylines_to_svg`）；`config.py` +`draw_per_device_day`（默认 20，校验 1..500）；`draw_queue.py` +`DrawDailyLimitError` + `day_stamp_cst()` + 内存/Redis 双路径日键；`server.py` `hutuji_draw` 进程内路径新增日限闸（此前该路径无任何设备限流） |
| portal 侧 | `pipeline.process_upload_sketch`（归一 → 渲染 → SVG 白名单 → vpype → §5）；`app.py` `_refine_photo_job` 整体重写为程序化（删 `_race_i2i`/`_attempt`/抬笔门 PREF/HARD/MAX 与 `_tcb_i2i_ready`/`_race_refine_racers`）；`draw_upload` 新增 `style` 表单字段（白名单外静默回落 hatch）；触发条件从「配 token」改为「是照片即精修」；`config.py` 删 `modelscope_token`/`tcb_image_url`/`tcb_api_key`/`tcb_i2i_enabled` 四字段；`index.html` 上传面板加风格单选（排线素描/连续波纹） |
| 实测 | 5 张生产样本 × 双风格全分辨率 1px 栅格目检通过（排线 = 蚀刻交叉排线、主体可辨；波纹 = 扫描线波形、蛇形并段）；G-code 全部 ≤§5 硬闸（114–298KiB < 512KiB）；排线抬笔 1.9–3.3k/张、波纹 0.4–0.6k/张（蚀刻风固有特性，画图时长显著长于 AI 线稿——已为用户可选档位）；渲染耗时 0.2–0.7s/张。**预览器污染教训已钉进模块 docstring 与 AGENTS.md**（设备预览 0.3×+2px 粗线会糊密排线区，调参只看全分辨率） |
| 测试 | mcp +20（`test_sketch_render.py` 11、`test_device_daily_limit.py` 9），全套 **1363** 项全绿（含计数一致性钉）；portal `test_photo_refine.py` 围绕新链重写 18→11（默认 hatch/squiggle 透传/非法回落/无凭据照跑/fail-open/并发闸），全套 **166** 项全绿 |
| 部署 | 已完成：`27bd254` 八文件双机 SHA 逐位相符 + 备份 `/root/hutuji-bak-20260910-sketch/` + 阿里云 11 单元/京东 2 单元 restart active + 节点真样本冒烟（双风格过 §5）+ preflight 阿里 24/33·京东 29/33（基线同集合）；证据 docs/deployment.md 顶条 |

### 程序化渲染黑卡事故修复批（2026-09-10，用户实机反馈「图生图完全不行」附黑卡截图）

| 项 | 说明 |
|---|---|
| 事故 | 生产首张真实室内人像（逆光、面部 p40-p60 中间调）经程序化排线渲染后预览整卡近纯黑（墨覆盖 ~50%）：`_load_gray` 无色调归一化，裸亮度直接撞 0.78 首层阈值，~90% 页面叠四层交叉排线 |
| 根因定性 | 阈值按「亮背景简单主体」的 5 张样本调参，对任意中间调照片结构性失控；属调参口径错误非链路错误 |
| 修复 | `sketch_render._normalize_tones`：p2/p98 分位拉伸 + `_TONE_GAMMA=0.6` 提亮中间调（p50 0.56→0.71，面部回阈值之上只吃轮廓）；排线层改 (0.55,45°,6px)/(0.42,-45°,4.5px)/(0.30,45°,3.5px)/(0.18,90°,3px) 亮层稀疏；squiggle 暗度地板 0.06→0.30 |
| 验证 | 事故原图（从生产 job 目录取回 `tmp/incident-0910/upload.png`）：hatch 墨覆盖 12.5%、squiggle 20.4%，全分辨率 1px 栅格视觉辨认出戴眼镜微笑女孩、面部留白+轮廓、头发排线纹理非实心；5 张原样本回归 6.7–9.5% 覆盖无过曝；pipeline 级 hatch 305KiB/2756 抬笔、squiggle 165KiB/473 抬笔，§5 余量 ≥213KiB |
| 测试 | `test_sketch_render.py` 12 项（+`test_mid_tone_photo_does_not_flood_page` 事故回归钉墨覆盖 <25%；层叠测试改深灰 40 钉「普通中灰不排线」新语义）；MCP 全套 1361、portal 166 全绿 |
| 教训 | ① 预览器/AA 缩放判读失真第二次咬人（preview mean 255 与「实心黑」并存）——评画风只信全分辨率 1px 栅格，已写进 AGENTS.md 警示；② 阈值调参样本集必须含中间调真实照片，亮背景样本调出的阈值对生产必崩 |
| 部署 | 已完成：`157bd41` 单文件 sketch_render.py 双机 SHA `7f515e469c78` 逐位相符（备份 `…/sketch_render.py.v1`），双机 restart hutuji-draw-portal active；节点冒烟 hatch 129774B/944 抬笔（修复前同样本 231155B/2085，色调修复实证降载） |

### 黑卡二次修复批：CLAHE + 真密度预览（2026-09-10，用户再反馈「还是不太行」附新截图）

| 项 | 说明 |
|---|---|
| 复判定 | 生产 draw.svg 与本地新代码渲染逐点一致（10903 点）→ 新代码确已在线；事故二根因双层：① 全局拉伸+gamma 对**逆光人像**仍不够（面部局部偏暗被整体曲线压住，纸面观感五官糊没、头发近实心）；② 预览器本身失真（0.3×+2px 粗线把密排区并成黑块，用户看到的≠纸面） |
| 修复①渲染+预算闸 | `_normalize_tones` 改三段：近纯色短路（纯白 fail-closed 依赖，CLAHE 对平坦输入会放大数值噪声故必须先闸）→ CLAHE 局部均衡（`equalize_adapthist` clip 0.02，逆光脸局部提亮与暗发分离，照片转线稿标准预处理）→ p2/p98 拉伸 → gamma 0.7；全黑极端测无爆闸风险；另加 `_HATCH_SEGMENT_BUDGET=3500` 段数预算闸（CLAHE 后暗部多的照片 5508 段 → 575KiB 超 §5 闸，段数∝1/层距实测线性，超预算按比放大层距确定性重渲一次） |
| 修复②预览 | `bitmap_svg.polylines_to_preview_png` 重写为真密度渲染：内容分辨率 1px 栅格化 → `resize` 面积平均降采样 → 2.0 增辉保细线可读（curated 跨度测试钉 ink<240「非白存在」语义）；灰度=真墨密度。影响面=全部链（描摹/curated/quickdraw/写字/笔顺预览同函数），无测试钉像素内容（仅 PNG 魔数/字节透传），旧预览对密排区的实心化是事故判读污染源 |
| 验证 | 事故原图：hatch 墨覆盖 19.4%、squiggle 27.5%；纸面观感（277px≈1px/mm 面积平均）视觉评判「能非常清晰认出戴眼镜微笑女孩、五官高辨识度、头发纹理非实心、结构性排线非噪点」；新预览卡尺寸下同一结论；5 样本回归 8.6–12.9% 覆盖；MCP 1361 全绿、portal 166 全绿；§5 预算闸实证：事故原图 hatch 由超闸 575KiB 压回 433KiB（余量 89KiB） |
| 部署 | 已完成：`124ad76` 双文件（sketch_render `f41145e46b6d`、bitmap_svg `234752f71c09`）双机 SHA 逐位相符；阿里云 restart mcp+bridge+portal+worker@{1..8} 11 单元 active、京东 mcp+portal active；节点冒烟 hatch 181843B/1397 抬笔过 §5 |

### A4 单页充分利用批（2026-09-10，用户拍板「暂时先不做刷机…特别单张 A4 纸要充分利用」）

| 项 | 说明 |
|---|---|
| 分析 | 事故 job 真 G-code 实测覆盖 257×146mm（面积利用率 71%）：`gcode_gen.LAYOUT_MARGIN_MM=10` 四边边距是唯一直接损耗（§5 硬边界 X≤277/Y≤190 本身已含 ~20mm 物理余量，版式边距是纯重复保险）；内容-页宽高比错配是理论上限（16:9 内容 ≈82% 顶），旋转内容换覆盖会致人像侧躺，否决 |
| 变更 | `LAYOUT_MARGIN_MM` 10→5（画图/照片/curated/AI 描摹全链自动受益）；文字排版边距 8mm 刻意不动（可读性）；事故照片同链实测 71%→**76%**（X 5..272/Y 19.9..170）；测试 `test_svg_to_gcode_upscales_and_centers_tiny_drawing` 硬编码 170 改为按常量推导 |
| 验证/部署 | MCP 1363、portal 171 全绿；`gcode_gen.py` 双机 SHA `fe65a578e7f9` 逐位相符（备份 `gcode_gen.py.margin10`），阿里云 11 单元/京东 2 单元 restart active |

### 量产无换纸 SKU 批（2026-09-10，用户拍板：量产暂时做不换纸机型；基线 commit c896ce5c「Tune Z motion defaults for spring pen axis」由用户指定）

| 项 | 说明 |
|---|---|
| 固件 | 新分支 `massprod/nopaper-1.3a`（外仓 Grbl_Esp32，已推 origin）：基线 c896ce5c + `GRBL_VERSION_BUILD` 20211103→**20260910**（`[VER:1.3a.20260910:]` 与现役区分）；树内无 Paper* 换纸代码（无需删改）；PlatformIO release 构建 SUCCESS（firmware.bin 1,842,272B，Flash 93.7%）；WiFi/Telnet/WebUI/出厂 AP 代配面完整（§1.5 零接触配网不受影响） |
| 系统影响面 | 云端 MCP/portal/小程序**零改动**（产物契约不变）；小派 S3 侧需「无换纸机型」模式：页尾归位保留、M30 段与换纸等待跳过；多页文章**不做**（2026-09-10 用户拍板：不换纸机型只出单页；写字/笔顺/文档多页功能对该机型不开放）；S3 无换纸模式规格=页尾归位保留、M30 与换纸等待整段跳过、多页任务确认环节拒绝（话术「这台机器一次只能画一页哦」）；契约已立 `docs/protocol.md` §10.4.15：机型识别=VER build 20260910 vs 20211103、S3 读 `$I` 分支、能力标志位（announce 上报 SKU→绑定表→小程序显隐）待联调。**S3 无换纸模式代码已落地**（xiaozhi `eb56138` 已推 feat/hutuji-usb-pipe）：新 header-only `hutuji_nopaper_core.h`（VER build 段精确匹配/多页闸/页尾跳过谓词/无换纸 12 项 $$ 指纹金标——数值=c896ce5c 机头默认，刷机流程须含 $RST=$ 或整片擦写，刷后实机校准）+ pipe VER 解析置机型/banner 复位保守回换纸机/指纹探针分表 + job 提交闸与页尾兜底双拦多页、页尾跳 M30；`recovery_core` 指纹核对器加 Table 形参版（旧调用点零改动）。验证：host 测试新增 3 例全绿（VER 识别陷阱矩阵/谓词矩阵/分表与值钉死）、lichuang-dev release 真构建 2120 单元过（vhutuji.1.0.6）、`xiaozhi.bin` 含拒绝话术字面量。既有 host 套件 170 项中 1 例失败为**用户既有在途改动**（draw_bind kAutoBindMaxAttempts，未 stash 前即红，与本批无关）。**HIL 未验**（刷机仍卡 IO0 物理动作）。外仓工作树含大量用户在途 WIP（freenove 换板等 34 项），本批提交只摘 nopaper hunk（git apply --cached 标记过滤），用户改动原样保留未提交 |
| 刷机阻塞→已解决（2026-09-11 凌晨） | ~~判定「本机 DTR→IO0 单元级故障」~~ **翻案**：另一块 ESP32 板在本机同样进不了下载模式（对照实验），而量产机在另一台电脑用官方 FDT 可烧——根因是**本机（Win11 26200 + CH340 驱动，3.9/4.0 两版同症）对所有 ESP32 板的 DTR 自动下载全灭**（esptool issue #790 同族），两块板子均无问题。实际刷机路径=**短接 IO0（ESP32-S 模组右列下数第 3 焊盘，示意图 `tmp/esp32s-io0-pinout.png`）到 GND 上电 + `--before no-reset`**：esptool v5.3.1 stub 同步成功（MAC `4c:c3:82:cc:8a:9c` 对上原机 BT MAC），三件套写入哈希校验过。**坑：首刷 QIO 看门狗重启循环**（原厂固件 mode:DIO，该机 QIO 读回乱码）→ DIO 重刷一次点亮；massprod 分支已提交 `d09ad28`（flash_mode 改 dio，防下次 pio upload 再踩）。开机验收：`[VER:1.3a.20260910:]`、`[OPT:PHBSW]`、`Custom 3-Axis HR4988`；**NVS 残留盖过机头默认如约出现**（$1=25/$3=6/$100=80/$130=300 等），`$RST=$` 后 12 项金标指纹 **12/12 MATCH**（无换纸表，证据 `tmp/massprod-boot-after-flash.txt`、`tmp/massprod-settings-after-rst.txt`）。WiFi 已由小派侧配上户网（STA `ChinaNet-jJmz`、`192.168.1.66`，Telnet :23 本机实测可连）。**未做**：实画出纸、多页拒绝话术实机。烧录脚本 `tmp/flash_massprod_noreset.py`（no-reset DIO 版，唯一可信路径）；探针 `tmp/probe_*.py`；FDT 驱动 `tmp/fdt_drive_{1,2,3}.py`；刷机包 `tmp/massprod-flash-pack/`（含 dio bootloader） |
| S3 联机（2026-09-11 凌晨） | freenove 小派已 OTA 到 `hutuji.1.0.7`（tip `eb56138`+WIP+版本号 bump，本机真构建 `c0805ffd…c7f4`，双机 catalog 发布公网验证；发布明细 `docs/deployment.md` 首条）。接入新机后 1.0.6 曾把量产机判 `grbl_settings_mismatch`（换纸机表，首失 `$3`）致 `hutuji_job` 拒画 = 用户报「小程序控制不了」的根因；1.0.7 实测 `connected/ready/authorized=true`、`grbl_settings_ok=true`、grbl Idle、MPos 0/0/0（messaging 取证）。**未做**：实画出纸、多页拒绝话术实机、语音全链 |
| 流内中断事故修复批（2026-09-11 凌晨，三次实机「画着中断」收口） | **根因链（S3 日志逐帧取证）**：量产分支 4 月基线缺 §9 Telnet 硬化 + 客户端输入缓冲 256B < 管道 512B 在途窗口 → 解析稍慢缓冲溢出丢字节 → 行粘连（文件头 `G1G90` 幂等对融合）→ `error:21` → 管道中止发 `!` → Hold 态下错误恢复路径发 `[ESP901]` 查纸态 → 无换纸固件无 Paper* 实现静默 → 5s 超时 → 断连丢任务。**修复**：Grbl massprod 分支 `9f26a2b`（§9 Telnet 四件回移植 + 无换纸 [ESP901] 固定应答，Idle 下实测应答）+ `6221502`（InputBuffer 256→2048B 摘自主线 `ed9073b` 单项 + Serial.cpp 规划器饥饿少让出）；S3 侧 `hutuji_job.cc`/`hutuji_pipe.cc` 无换纸机错误恢复跳过 [ESP901] 查询 + `SendAbortReset` 豁免 fresh_paper。**验证与边界（如实）**：串口逐行回放用户原文件 1000+ 行零 error（内容/解析无罪）；加固后回放 294KB 任务 **error:21 消失、冲出多次 stall 推进到 17.5%（2310/13211 行）**——但随后**静默中止**（机器自行 Idle 停在 (60.9,44.5)、页尾归位未执行、无 error/断连日志，S3 串口末 3.5 分钟全静默疑设备自身卡死/重启），**流尾态未修、根因未定性**，待小文件复测+全程抓包；S3 1.0.8 OTA 双机发布（明细 `docs/deployment.md` 首条）。**已知余项**：量产机物理幅面待实测（内容最大 X=272mm vs 该机 $130=200 配置口径）；机器事故级联后曾整体死机需断电（未复现）；多机同网配对方案已定稿待实施（出厂 AP 唯一化/代配记 MAC/绑定表上云） |


### 按机型画幅与提速批（2026-09-11，用户拍板：nopaper 取消转置、按机型画幅、提速）

| 项 | 说明 |
|---|---|
| MCP 侧 | `gcode_gen.py` +`MachineProfile`（frozen dataclass，transpose_xy/max_x_mm/max_y_mm 全 None 默认=现役 MK38）+`PROFILE_NOPAPER=(False,205.0,290.0)`（物理 210×297 减 5mm 边）；`svg_to_gcode`/`validate_gcode` 全链穿 `profile=`；`server.py` 机型选档（`_profile_for_machine`/`_probe_plotter_profile` 60s 含失败缓存/`_resolve_plotter_profile`）+ `/internal/draw` body `machine` 白名单（("", "paper", "nopaper") 外 400）+ `_run_draw_job`/`_run_write_job`/`_run_teach_job` 穿机型 + `_svg_to_gcode_cached` 缓存键含 `_profile_cache_marker` + inflight 键含机型（`_inflight_key_for`）；`draw_queue.py`/`draw_worker.py` payload `machine` 穿参（空=旧键逐字节兼容） |
| portal 侧 | `models.py` DeviceBinding/BindSession +`plotter_sku`/`machine_type`（默认 "paper"）；`store.py` bind_sessions/device_bindings 列迁移（ALTER … DEFAULT 'paper'）+ `set_binding_machine_type`（白名单外 ValueError）+ upsert/行映射随参；`push.py` +`probe_plotter_sku`（hutuji.status 探测，缺席/失败 None）；`app.py` +`_machine_profile_for_user`（探测命中回写库、失败回落库值）+ 生成入口穿 profile（upload/doc/write/teach/refine/sketch）+ AI 生图传 machine；`pipeline.py` 六个 process_* 穿 profile；`generate.py` request_internal_draw +machine（空串不带 key） |
| S3 侧 | `hutuji_job.cc` StatusJson +`plotter_sku` 字段；`hutuji_draw_bind.cc` announce body +`plotter_sku`（`Pipe::GetInstance().IsNopaperMachine()`，+include hutuji_pipe.h）；`hutuji_recovery_core.h` +`kJogEnvelopeNopaperMax{X,Y}Mm=205/290` +`MachineJogEnvelope` +`DecideJog` 显式限幅重载（四参默认档保留）；`hutuji_job.cc` 工具侧预检与 ManualTask 两处点动判定改传机型包线 |
| 1.0.9 构建 | `PROJECT_VER` bump `hutuji.1.0.9`（CMakeLists 已随 1.0.10 批入库 `d0d2c8d`）；双板真编译（各 213 CXX 增量、RELEASE_RC=0）：freenove `xiaozhi.bin` SHA `22111e60…b0d3` 3,033,648B → `releases/vhutuji.1.0.9_freenove-esp32s3-display-2.8-lcd.zip` 4,773,344B；lichuang-dev `bc71568c…303b` 3,067,152B → `releases/vhutuji.1.0.9_lichuang-dev.zip` 2,797,312B；**freenove 已发 OTA**（双机落盘 + 公网 latest.json/catalog.json=1.0.9、bin 真下载 SHA 相符；catalog 备份 `/root/hutuji-bak-20260911-machineprofile-catalog.json` 双机）；lichuang-dev 仅 bench 备用未入 catalog。**实升翻案（同日深夜）**：设备随后自升 1.0.9，探活 404「Device not online」为瞬态（readiness 行 19） |
| 测试 | mcp `test_contract.py` 633→**640**（+nopaper profile 3 + machine 白名单/缓存成分/inflight 隔离/probe 缓存 4）；portal 全套 **173** 项全绿（新增 fakes 补 profile/machine kwarg；`test_bind_mints_messaging_token_when_probe_fails` 曾被 set_device_binding 重写误吞 `ok=True` 修复后回归）；xiaozhi host `test_hutuji_recovery_core.py` 点动测试增 nopaper 包线 6 断言（CXX=mingw g++ 跑通；**esp-clang 被选为 host 编译器会全绿转红——链接 RISC-V libstdc++ `_Unwind_*` 未定义，与本批无关，是 PATH 里 esp-clang 抢占 `find_compiler` 候选**） |
| 契约 | `docs/protocol.md` §10.4.15 扩表：机型词汇 `"nopaper"|"paper"`（未知/缺席=paper）、生成选档、防串图缓存/inflight 成分、portal 学习链、S3 点动包线、状态=代码面绿+HIL 未做 |
| **读闸热修（2026-09-11 晚）** | 用户报「画不了图」根因：本批部署后 nopaper 产物（Y≤290）落盘合法，但 `/files` 读时复核 `output_store._read_verified_gcode_bytes` 只跑默认档（Y≤190）——合法产物判 stale **联动删除文件对**，设备下载 404「文件已过期或不存在」，语音画图全灭（服务器日志多单 `拒绝下发不再满足 §5 契约的产物…Y190.7…`；文件在盘但公网/直连双 404）。修复：读闸两档试探——默认档先验，GcodeError 再用 `PROFILE_NOPAPER` 重验，任一通过放行；两档都不成立仍抛 `StaleOutputStoreError`（默认档报错文案不变）；本地/OSS 两 store 共用同一 helper，一处修复覆盖两路。`tests/test_output_store.py` 4→5 项。排查插曲：样张疑「nopaper 重验被吞」实为合成样张零位移（G0 与 G1 同点、G0 不计入运动闸）——校验器无恙。已双机热更 `output_store.py`（SHA `6126de70…c2310`、CR0、备份 `/root/hutuji-bak-20260911-machineprofile`）+ 重启（阿里 mcp/bridge/portal/worker@1-8、京东 mcp/portal 全 active）。公网复验：`/internal/draw machine=nopaper` 猫图 889 行 23.6KB → 公网 G-code/PNG 双 200，内容 max_X=184.8/max_Y=232.2（默认档必拒、nopaper 档合法）。**旧产物已被读闸删掉，须重新生成**。 |
| 入库状态（2026-09-11 深夜收口） | 本批已全部入库并推 origin：hub 读闸热修 **`2c871b0`** + 文档收口 **`d45edb7`**（7 文件：deployment 首条 1.0.10 闭环、readiness 行 20、inventory 收口行、protocol 速度金标行、指纹基线 4 处纯移动重登 261 处全对、findings 28/30 行号重定位）；xiaozhi **`d0d2c8d`** 已推 `feat/hutuji-usb-pipe`（19 文件 725+/43-：记忆三件套+hutuji_memory_core.h 及 host 测试、abort 修复批（UpgradeFirmware 失败回 Idle/JobIsIdleForOta settled 集合/ota_start failed 自愈）、机型包线+plotter_sku、金标 $110/$111=12000、PROJECT_VER 1.0.10、两条陈旧源文本钉重锚）；host 测试 nopaper 3/3 + recovery 108/108 + memory 1/1；tmp_*/pp_* 构建日志不入库仍脏 |
| **金标提速批收口（2026-09-11 深夜）** | 金标 `$110/$111` 5000→12000 已发 1.0.10 OTA 并设备实升：真构建 213 CXX、bin SHA `46794e20…15e4` 3,033,648B、zip 4,773,334B；双机发布落盘逐位相符（catalog SHA `a9b8b1a2…13be`，备份 `…-catalog.json.pre1010{,-latest}` 双机）；COM3 写 `$110=$111=12000` 回读确认（回退 `5000/5000/600/500/500/60`）；messaging REST 直推 `ota_start` 实升（504 瞬态、下载照常）→ `hutuji.status` 实证 `firmware_version=hutuji.1.0.10` + `grbl_settings_ok=true`（金标与 NVS 同窗对上，指纹拒画锁解除）。portal ota_session 两轮 504 误记 failed 已手动置 ok/100。**$RST=$ 恢复出厂会回落 5000 拒画须重写**。用户画速实测待反馈；明细 `docs/deployment.md` 首条、readiness 行 20 |

### 双机量产安全加固批（2026-09-10，用户原话「把 vps 处理一下，为量产做准备，后续大规模并发，要能扛住冲击和攻击，参考 GitHub 开源项目做法」）

| 项 | 说明 |
|---|---|
| 审计发现 | 双机 sshd PasswordAuthentication yes + PermitRootLogin yes（密码暴破面全开）、fail2ban 缺席、syn_backlog 128/256、mcp/portal/bridge 缺 NoNewPrivileges/PrivateTmp/LimitNOFILE、nginx 无 API 级限速（仅 /files//songs//register）与 HSTS/客户端超时 |
| 实施 | sysctl 四键 + fail2ban 双 jail（sshd 3 次封 1h、nginx-limit-req 10 次封 1h；京东启用即实 ban 2 个在途暴破 IP）+ sshd 五项收紧（reload 后新连接实测可入，首现优先坑：阿里云无 Include 须改写首现行）+ systemd 三件套内联主 unit + nginx API 闸 30r/s burst 90（课堂 NAT 30 生×1.5s 轮询≈20r/s 留头）+ HSTS 86400 + client timeouts |
| 校验 | preflight 阿里 25/33（基线 24，unit 模板一致翻绿）、京东 30/33（基线 29，同翻绿）；公网 /draw-upload/ 200、/mcp 404、HSTS 头实测在；参照系：nginx limit_req/conn 官方模块模式、fail2ban sshd/nginx-limit-req 标准 jail、OpenSSH 生产基线（禁密码+限试次） |
| 仓内同步 | `deploy/prepare-host.sh` 并入全套加固（新节点照搬）；`deploy/nginx-hutuji.conf` 与三 unit 模板对平生产真值（含 live 独有的 SystemCallErrorNumber=ENOSYS） |
| 备份 | 阿里云 `/root/hutuji-bak-20260910-hardening-nginx-gray.conf` + `…-sshd_config`；京东 `/root/hutuji-bak-20260910-hardening-nginx` + `…-sshd_config` |

### 写字字体选择器批（2026-09-10，用户原话「小程序写字写文章可以增加选字体吗」）

| 项 | 说明 |
|---|---|
| 决策 | §10.4.10 「明确不做·选字库」由用户本句 supersede；MCP `hutuji_write` 早有 font_name 参数，portal 链此前硬编码默认库 |
| 后端 | `write_text.list_fonts()`（文件名扫描不解析）；`process_write_text(font_name=)` 透传 `load_font`（具名不存在 → WriteTextError → 400）；portal `WriteBody.font`（>60 字符/含 `/` `\` `..` → 400）+ `GET /api/fonts`（phone_secured，未配置 503） |
| 小程序 | 写字模式「字体」picker（懒加载 /api/fonts，失败静默藏选择器走默认库）；提交带 `font`；脚注 0.3.0/0.2.20 → **v0.3.1**（chat/control/profile/firmware 四处） |
| 契约 | protocol §10.4.10 请求 JSON 加 font 行 + 字体列表行 + 明确不做行 supersede 盖章 |
| 测试 | mcp +1（list_fonts stems/排序/坏目录空表）、portal +5（透传/非法 400 矩阵/未知库 400/列表/503）；MCP 1363、portal 171 全绿 |
| 改名 | 2026-09-10 用户拍板显示名改「动力巢科技系列」：`list_fonts` 回 [{id,name}]，id=文件名词稳定契约、name=`_FONT_DISPLAY_NAMES` 表（9 款全登记：真迹手写（默认）/真迹经典/真迹行书/速写/继世行书/梦万手写/随写/兰亭细黑/符号集，未登记回落「动力巢科技·<词>」）；小程序 picker 展示 name、提交 id |
| 部署 | 双机后端已完成：`2f0e49f` 三文件 SHA 逐位相符（write_text `5513ab560785`、app `8b48724964b3`、pipeline `02b920efa0fd`），阿里云 11 单元/京东 2 单元 restart active，`/api/fonts` 双机 401（路由在场、phone 闸生效）；weapp **0.3.2** 已 CLI 上传（对象形态+品牌名终版，exit 0）；**MP 后台选 0.3.2 体验版是用户动作**；改名后 write_text 双机 SHA `5fdfd061ba6a` 逐位相符、mcp+portal restart active、生产 /api/fonts 实测 9 款品牌名正确 |

### 排线画质/提速优化批（2026-09-10，用户验收「看着还行」后拍板 ①+②）

| 项 | 说明 |
|---|---|
| 决策 | 用户确认二次修复效果可接受，问改进空间；代理提出 ① 轮廓降噪 ② 排线串链提速 ③ 排线避让轮廓 ④ 流向排线，用户拍板 ①+② |
| ① 轮廓降噪 | 小样 4 组合 A/B（事故人像纸面尺度目检）：`_CONTOUR_LEVELS` 4 级 (0.30/0.48/0.66/0.84) → 3 级 (0.32/0.52/0.74)、`_CONTOUR_SMOOTH_SIGMA` 1.2→2.0——旧参数在面部渐变区切密集碎波（「地形图感」之源），新组合碎线显著减少、五官辨识度不降（squiggle 共用 `_iso_contours` 同享降噪） |
| ② 排线串链 | `_chain_hatch_segments`：贪心近邻串链，间距 ≤10px 且连线 1/4、1/2、3/4 三点都在本层 mask 内才落笔并入（连线藏同暗度带，零膨胀）；**最亮层（0.55）刻意不串链**——其边界连线落在面部明调区成可见回折环（全串链版 917 抬笔小样目检否决），暗三层串链后事故人像 3672→**2551 抬笔（-31%）**、G-code 433→336KiB（余量 183KiB）；段序固定+argmin 平局小索引，同输入逐点复现 |
| 部署 | 已完成：`83526fe` 单文件双机 SHA `0878b4671db4` 逐位相符（备份 sketch_render.py.v2），双机 restart hutuji-draw-portal active；节点冒烟 hatch 112765B/875 抬笔过 §5 |
| 测试适配 | 串链打破「2 点段」语义：mask 界内/层叠递增改栅格化口径（防空话化），min_run/预算闸用恒等串链观测段原文，新增 `test_hatch_chaining_collapses_pen_lifts`（200×120 半黑串链后 <40 折线）；`test_sketch_render.py` 14 项全绿，MCP 1362、portal 166 |


### 小派文字聊天 + 小程序缺陷收口批（2026-09-09，代码已测全绿 + weapp 0.3.0 CLI 已传（73.0 KB）；未提交、未部署）

| 项 | 说明 |
|----|------|
| 决策 | 用户拍板：文字聊天走 **A 路径**（云端文字小派，不动固件）、聊天里**能聊能画**（DRAW:: 意图 → 聊内卡片走既有 generate/send/save）、点动方向标注**暂不动**（与固件语音/屏幕面板保持一致，等刷机窗口再三面对齐墙上竖装直觉） |
| MCP 侧 | 新增 `hutuji_mcp/chat.py`（人格 SYSTEM_PROMPT 与 `docs/xiaozhi-agent/role-prompt.md` 同源裁剪；`DRAW::{...}` 意图标记解析，坏标记/乱位一律回落普通回复且剥标记行；20s/400 tokens/64KiB 上限）；`server.py` 新增 `POST /internal/chat`（与 /internal/draw 同闸；`_internal_draw_authorized` 更名 `_internal_api_authorized` 两路共用） |
| portal 侧 | 新增 `draw_portal/chat.py`（msgSecCheck v2 输入+输出双审、access_token 缓存重铸、审核服务不可用 fail-closed 503、输出被拦回落固定话术；`request_internal_chat` 转发与错误映射同 generate.py 口径）；`app.py` `POST /api/chat`（phone_secured + openid 闸 + `wx_chat` 24h 配额 `DRAW_PORTAL_CHAT_DAILY_LIMIT` 默认 50 成功才计数）；`config.py` +2 env |
| weapp 0.3.0 | 新增 `pages/chat/`（气泡对话 + 聊内画图卡：生成/发送/收藏）；tabBar 五 Tab「创作/小派/控制/历史/我的」。缺陷修复：① 收藏断头路——创作页预览卡加「收藏」（此前全端无 `/save` 调用方，历史页永远空）；② 历史页长按删除（DELETE 早已上线无入口）；③ 创作页切 Tab 精修结果恢复（onShow 一次性补查 job，pending 续轮/ready 上屏/failed 标注）；④ 绑定静默重试 30s→60s 带等待秒数上屏；⑤ refreshStatus 失败递归加单次闸；品牌统一「动力巢 · 小派」 |
| 测试 | mcp 新增 `test_chat.py` **28** 项，全套 **1339** 项全绿（2026-09-10 i2i 质量批 +1：lee 骨架契约）（合并分支后：+test_article_llm.py 4、test_contract.py 629→631）（此前 1288；另补登前期漏记 test_device_messaging.py 8、test_i2i_prompt_sketch.py 6、test_write_text.py 45→46、test_curated_server.py 5→6）；`test_contract.py` 链式三测试钉 `HUTUJI_DEVICE_TOKEN_FILE`——本机 `C:\var\lib\hutuji\device_messaging.token` 残留测试值 `fresh-jwt` 曾致 test_draw_chains 假红（文件已删）；portal `test_chat.py` **10** 项，portal 全量 100 过 + test_doc_upload 4 项失败与本批无关（HEAD 基线同挂，未定性） |
| 验证 | hub 门禁：pytest 1332 PASS、e2e PASS、文档层全过；`line_ref_fingerprints` 42 处半漂移 + 3 空锚点 = **xiaozhi 仓 OTA/记忆批进行中**（application.cc 等工作树改动），按规则未重登，待该批收口后人工核对再 --accept-line-refs |
| 文档 | protocol §10.4.10；readiness 顶部条目；AGENTS.md 计数/文件清单 |
| 待办 | ~~① 双机部署~~（已完成：19 文件双机 SHA 对账 + restart + 冒烟 + preflight）~~② weapp 0.3.0 CLI 上传~~（重传 exit 0，TOTAL 100.2 KB）③ MP 设 0.3.0 体验版 ④ 隐私协议补「文字对话传第三方 AI」+ AI 生成标识（MP 后台）⑤ msgSecCheck 真联调 ⑥ 真机 HIL（文字聊/语音/写字/笔顺/固件升级）⑦ test_doc_upload 4 项失败定性（合并后 portal 173 全绿，疑似已被分支修掉，待复核确认） |

### TCB 混元 i2i 通道批（2026-09-08，代码已测未提交未部署；云函数补丁待用户粘贴）

| 项 | 说明 |
|----|------|
| 决策 | 用户指正「混元应该是有图生图 API 的」——此前「TCB t2i only」的判断只看到了函数模板只接 prompt 的表象。官方 `ImageToImage` action + CloudBase AI SDK `generateImage(model=HY-Image-v3.0-I2I-ToB-*)` 均支持底图（librarian 查证 docs.cloudbase.net + cloud.tencent.com，2026-09-08）。拍板：TCB i2i 与魔搭**同等地位**进照片精修链对冲「谁先出图用谁」 |
| 实证 | 网关鉴权：无 key → 401 MISSING_CREDENTIALS、错 key → 401 INVALID_CREDENTIALS（tcloudbasegateway 层拦截，函数内无鉴权码）；函数 URL `https://zhuguang-d7gdwteu083f6fff4.api.tcloudbasegateway.com/v1/functions/generateImage-cqA3Hl`（从阿里云 /etc/hutuji/mcp.env 取证） |
| MCP 侧 | 新增 `hutuji_mcp/tcb_i2i.py`：单次 POST `{prompt,image(b64),mode:"i2i"}` → `{success,imageUrl}` → 复用 `modelscope_client.download_image` 下载（url_guard 唯一 SSRF 闸不开第二路）；入图先缩长边 1024/≤3MiB（网关 body 上限，手机原图 b64 可超 20MB）；提示词与魔搭链同源 `I2I_LINE_ART_PROMPT`；`bitmap_svg._request_tcb_image_url` 注释去「不支持 i2i」过时断言 |
| portal 侧 | `config.py` +3（`HUTUJI_TCB_IMAGE_URL`/`_API_KEY` 与 mcp 同值 + `HUTUJI_TCB_I2I_ENABLED` 显式开关——函数未部署 i2i 分支前必须为 0，旧函数静默忽略 image 退回 t2i 且「成功」）；`app.py` `_race_refine_racers` 对冲（并发发起/败者取消/全败抛首个异常，fail-open 不变）；触发条件 魔搭 token **或** TCB 就绪 |
| 云函数补丁 | `D:/Temp/User/tcb-i2i-index.js`（仓外交付物）：t2i 原样 + i2i 分支（`images:[b64]`、`revise:{value:false}` 钉死提速 ~30s、`HY-Image-v3.0-I2I-ToB-v1.0.1`）；需用户在 TCB 控制台「函数代码」全量粘贴覆盖 index.js 保存部署。**粘贴后须实测**（generateImage 返回字段名 imageUrl/data.imageUrl/url 三兼容已写，若全不中会回 success:false 可再调） |
| 测试 | mcp `test_tcb_i2i.py` **11** 项全新（全套 1276→1287）；portal `test_photo_refine.py` +5（63→68）：TCB 单通道/开关=0 不调 TCB/对冲 TCB 胜取消魔搭/TCB 败回落魔搭/两路全败保 Canny。两侧全量绿（mcp 1287、portal 68） |
| 文档 | protocol §10.4.9 第二段改双通道对冲 + 触发条件；AGENTS env 清单/Important Files/模块数 36/测试数 1287+68；env.draw-portal.example +i2i 段 |
| 待办 | ① 用户粘贴函数补丁 → ② 我用真 key 发 i2i 探针验证端到端 → ③ 双机部署代码 + draw-portal.env 加三键（ENABLED=1）→ restart → ④ 真实照片单观察 TCB 线稿质量（不达标 ENABLED 归 0 回魔搭单通道）；隐私协议条款已含「第三方 AI 服务」覆盖面（魔搭+腾讯云混元同条款，上架前用户 MP 动作不变） |

### SSRF/并发加固 + 文档同步批（2026-09-08，hub `d0db04d` 已推、双机已部署）

| 项 | 说明 |
|----|------|
| 加固 | 新增 `hutuji_mcp/url_guard.py`（公网 https 闸唯一实现，IMG-URL-SSRF-GAP）；`bitmap_svg._validate_image_download_url` 委托它（异常口径不变）；`modelscope_client.download_image` 强制过闸（此前只查 https-scheme，回环/私网 IP 字面量与本地域名可穿） |
| 并发 | portal `app.py` `_refine_slots = asyncio.Semaphore(4)` 包 `_refine_photo_job`——后台 i2i 精修并发上限 4，防批量照片上传同时打魔搭免费层 |
| 小程序 | 历史页受保护预览改走 `api.downloadFile`（x-session 头）——真机直接 `<image src>` 不带会话头会 401；加加载占位 |
| 测试 | mcp `test_modelscope.py` 17→22（全套 1271→1276，SSRF 拒绝矩阵：回环/私网/IPv6 回环/localhost./.internal/非 https）；portal +1 semaphore 用例（62→63） |
| 文档 | AGENTS.md 链路 racer 列表补魔搭/TCB、env 清单 +3、模块数 31→35、portal 测试数 34+7→63；README/CLAUDE racer 列表同步 |
| 待办 | weapp 下一体验版发布（含历史页修复） |

### 图生图（i2i）AI 线稿通道 + 魔搭/TCB t2i racer 批（2026-09-08，hub `8a62f04` 已推、双机已部署）

| 项 | 说明 |
|----|------|
| 决策 | 用户拍板：ModelScope 与商汤日日新/agnes **同等地位**（对冲 racer，含 t2i）；照片 UX = 异步两段（先 Canny 秒回预览，后台 i2i 精修好了结果页轮询换用）；grok/NewAPI 通道排除（new-api xai adaptor 丢 image 字段 + 出境未备案）；TCB 云函数实证 t2i only，作 t2i racer |
| MCP 侧 | 新增 `hutuji_mcp/modelscope_client.py`（httpx 异步 submit（`image_url` 字段，实证 image 会 400）+ poll（`X-ModelScope-Task-Type: image_generation` 头）+ 下载，窄异常 ModelScopeError）；`bitmap_svg.py` 对冲池加 modelscope（Qwen/Qwen-Image）与 tcb（hunyuan-image）两 t2i racer；`config.py` +3 env（`HUTUJI_MODELSCOPE_TOKEN` / `HUTUJI_TCB_IMAGE_URL`+`HUTUJI_TCB_API_KEY` 同配同空、https 强制）；`portal_settings.py` 默认后端 +2 |
| portal 侧 | `pipeline.py`：`normalize_raster_to_png(raw, *, line_art=True)`（False 保留照片中间调供 i2i 输入）+ `is_photo_raster`（近白像素<0.5 启发式）；`app.py`：上传照片且配 token → `_spawn_background(_refine_photo_job)`（后台 i2i → 独立 refined job 走同一描摹/发布链 → 回填；失败仅置 refined=failed 不影响 Canny 结果；CancelledError 置 failed 后再抛保停机语义；`_background_tasks` 强引用集合防 GC 静默回收——asyncio loop 对 task 只持弱引用）；新增 `GET /api/draw/jobs/{id}` 轮询端点；`models.py`/`store.py` + refined_status/refined_job_id 迁移列与 `set_draw_job_refined`；`config.py` + `HUTUJI_MODELSCOPE_TOKEN` |
| 测试 | mcp `test_modelscope.py` **17** 项全新（全套 1254→1271）；portal `test_photo_refine.py` **7** 项全新（全套 55→62）：两段式就绪/无 token·非照片单段/i2i 失败保 Canny/404/照片启发式/line_art=False 保中间调；portal 全套 62/62 绿，mcp 计数钉死测试同步更新 AGENTS.md+本文件 |
| 测试夹具坑 | starlette TestClient 不 `with` 进入则**每个请求一个事件循环**——后台任务挂在上传请求的 loop 上，响应一落盘 loop 关闭任务被销毁（生产 uvicorn 单长循环无此问题）；`test_photo_refine.py` 全部用 `with TestClient(...) as client` |
| 凭据 | 魔搭 token 与 TCB API key 仅内存/600 env，禁入库；TCB key 在 `D:/Temp/User/tcb_apikey.txt`（仓外） |
| 待办 | 小程序结果页轮询 refined + 对比/换用 UI；双机 env 部署 + restart + 冒烟；隐私协议补「照片传第三方 AI（魔搭）生成线稿」条款（上架前用户 MP 后台动作） |

### 微信小程序 + 儿童友好固件批（2026-09-07，hub `9e2c69c`/`f918fb6` + portal `045528e`；xiaozhi `5431a15` 待刷）

| 项 | 说明 |
|----|------|
| 小程序 | AppID `wxbf3c1e0013b46343`；**2026-09-08 备案被驳回，用户拍板改名「动力巢」**（原「小派写字机」；改名申请 12:30 已提交、平台审核中 ≤7 工作日，主体=深圳市动力巢科技有限公司、驳回疑似名称/主体不一致；代码侧 app.json 全局标题/profile 页脚已改，0.1.6 待传——上传私钥缺失，需开发者工具开服务端口或 MP 后台重生成密钥）；0.1.1→0.1.5 已传，0.1.5 修复拍照转换无预览（统一走 AI 预览卡）
| 后端 | portal 55/55：绑定 400 静默重试、强制换绑、拍照 JPEG 修复（魔数闸）、照片 Canny 边缘模式（人像质变，参照 Inkscape CANNY/奎享 EdgeTab）、推送 token 自愈（401/403 自动铸 messaging token 回写重试）——双机已部署 |
| 固件 | xiaozhi `5431a15`：lichuang 链接修复（SetGrobotEyesPaused 移出 WECHAT_MESSAGE_STYLE 守卫）+ 配网 QR 改 URL 形态（微信扫一扫直进小程序）+ bind announce 并入轮询环 + 配网 5 分钟无操作自动退出；lichuang release zip 已建（2.78MB 真编译 2200 单元），waveshare 增量验证中；**待刷机** |
| MP 规则 | 两条扫码规则已上线体验版：`/draw-upload/bind`→绑定页、`/draw-upload/wifi`→配网页（校检文件经 nginx 三路径服务） |
| 待办 | 改名审核生效后：重新提交备案（入口需管理员微信扫码，重新备案表单已进到「验证备案类型」步、待用户传营业执照与负责人核验）；刷机后真机验证扫码绑定+扫码配网；备案号下发后传 0.1.6 提版本审核 |

### kawaii→Eilik 调参批（2026-09-06 晚，xiaozhi `3b88132`；已刷 COM14）

| 项 | 说明 |
|----|------|
| 授权 | 用户截图否决旧色/比例 + 拍板「方案 1」往 Eilik 靠；accent 圈选活泼淡青 |
| 范围 | `kawaii_face/lvgl_kawaii_face.{c,h}`（黑底无眼白、淡青虹膜、铺满父盒）；`lcd_display.cc`（脸盒 312×232、kawaii accent `0xB4F0FF`、`AccentDriftTimerCb` 不再用 π 渐变覆盖）；`scripts/tests/test_kawaii_face.py` + recovery AccentDrift 钉 |
| 证据 | 镜像 SHA-256 `F4CE4FC6…E468`；host kawaii 8/8；刷机 Hash verified + watchdog |
| 待办 | 用户口头确认最终按钮色；情绪/写字冻结完整 HIL |

### TLS 连接复用批（2026-09-06 晚，xiaozhi `531f5b7`；已刷 COM14，多页 HIL 待复验）

| 项 | 说明 |
|----|------|
| 范围 | `hutuji_job.{h,cc}` Acquire/ReleaseFetchClient；`patches/esp-ml307-*.patch` + README；nginx keepalive 1800s 见 hutuji `f360ad0` |
| 证据 | 见 `docs/release-readiness.md` 文首 TLS 条目；启动横幅与两页 HIL 待复验 |

### kawaii 桌宠脸批（2026-09-06 15:0x，xiaozhi `ac819f7` 已推已刷 COM14；supersede 下条 ElectronBot 批；晚间 Eilik 调参见上节）

| 项 | 说明 |
|----|------|
| 授权 | 用户实物否决 ElectronBot 白眼睛 + Eilik 截图指明风格 + 「换啊」；Eilik 闭源不可得已实证告知 |
| 范围 | xiaozhi：`main/boards/lichuang-dev/kawaii_face/`（lvgl_kawaii_face.c/.h vendored d58e1c8 + LICENSE + 三补丁）、`lcd_display.{h,cc}`（kawaii 分支/映射表/暂停钩子/字幕路由复用）、`CMakeLists.txt`（HUTUJI_KAWAII_FACE）、`scripts/tests/test_kawaii_face.py`（7 钉，替掉 test_electronbot_face.py）；electronbot_face* 三文件删除 |
| 证据 | 镜像 3,047,840B；boot `Kawaii face initialized` + 指纹 13 项 + 22s 无 crash；host 160/160；ElectronBot 取证留档 `tmp/electronbot_emoji/` + `tmp/gen_electronbot_face_0906.py` + `tmp/kawaii_face/`（上游原件） |
| 许可 | MIT（上游 LICENSE 已随 vendor 入库）——ElectronBot 的 GPLv3 出货隐患随换脸消除 |
| 待办 | 用户目视验收（各情绪/写字冻结）；晚间 Eilik 调参已落地见上节 |

### ElectronBot 桌宠脸换脸批（2026-09-06 13:35，xiaozhi `ec8c367` 已推已刷 COM14）

| 项 | 说明 |
|----|------|
| 授权 | 用户原话「不用这个眼镜了，在github找桌宠表情吧」+「不用考虑许可，哪个最好」→ 拍板 ElectronBot 表情 |
| 范围 | xiaozhi：新增 `electronbot_face.{h,cc}`（EmojiCollection 子类+21 情绪名映射）、`electronbot_face_assets.h`（8 资产 180KB 嵌入）、`lcd_display.{h,cc}`（SetupUI 换脸分支/SetEmotion 分支/暂停钩子双路径/眨眼定时器/字幕条公共方法）、`CMakeLists.txt`（waveshare-3.5 默认开+可 -D=OFF 回退）、`scripts/tests/test_electronbot_face.py` 7 钉；hutuji：`docs/design/face-electronbot-0906.md`、取证 `tmp/electronbot_emoji/`（原 mp4 + 帧图 + GIF）、再生脚本 `tmp/gen_electronbot_face_0906.py` |
| 证据 | 镜像 SHA-256 `99746921…d137`；boot 日志 ElectronBot face initialized、指纹 13 项、25s 无 crash；host 161/161 |
| 许可风险 | 上游 ElectronBot 仓 GPLv3、素材作者 oooooohmygosh 无独立条款；用户口头豁免，**商业出货前必须在合规签核重新点名** |
| 待办 | 用户目视验收（表情切换/写字冻结/审美）；FaceScout 报告已归档 `tmp/face-scout-0906.md` |

### v10 固件刷机（2026-09-06 12:4x，xiaozhi `8a96608` 已推 origin）

| 项 | 说明 |
|----|------|
| 范围 | 本仓零代码改动；xiaozhi `8a96608`（v9「一直在下载中 5.5 分钟」回归修复：job 活跃期暂停 grobot 眼睛动画 + 非 Speaking 态 drain 等待 3s 封顶）刷入 COM14 |
| 证据 | 镜像 `build-instr-lichuang/xiaozhi.bin` 3,039,568B SHA-256 `d379135e…6d1`；5×Hash verified + watchdog-reset；boot 20s 297 行无 crash、指纹 13 项通过、Grbl Idle；host 153/153 |
| 待办 | 用户重写长文验证下载不再卡死；桌宠表情替换（FaceScout 调研进行中，用户拍板「不用这个眼镜了」） |

### 写字「字叠字+简繁混杂」云端修复批（2026-09-06，用户纸面实证投诉）

| 项 | 说明 |
|----|------|
| 授权 | 用户投诉「写文章有重叠的问题」「字体也是繁体简体混杂，字体也不是很漂亮」+「你要自己检查一下啊」（否决拍照/量尺，走数字侧自查） |
| 根因 | **非**行叠行/页叠页/丢步/洇墨（SVG/G-code 行距 18–19.7mm 正常、nginx 日志单页单次下载）；是同行相邻字墨区相撞：`write_text` 步进拿「国」墨宽当全宽（0.70–0.88em）撑不住实际墨宽；「1932年」另实证「年」左越界 5.2mm 撞前字。简繁混杂源 = 云端 LLM 文本混入繁体（字库简体码位收的就是简体字形，国 72 笔点 vs 國 77 抽查实证），代码原本无归一化 |
| 范围 | `write_text.py`（CJK 逐字槽位 `cjk_slot_units`、拉丁槽墨右缘+越界保底、flush 前瞻边界补偿、`FontMetrics` 增 `cjk_left_overhang`/`latin_left_overhang` 探针字段）；`table_layout.py` 同口径；新模块 `t2s.py` + `t2s_table.tsv`（OpenCC TSCharacters 3222 对 + 加载期传递闭包，Apache-2.0）；`server.py` `hutuji_write` 入口繁→简规范化；`tests/test_write_text.py` 34→40 |
| 本地取证（tmp/overlap_check/，gitignored） | 三字库样张 `final5_*.png`（修复后残余交叠归零审计 + 渲染目检）、`final_*`/`fixed2_*`/`sample_*` 过程件、SVG/G-code 行距审计脚本输出 |
| 验证 | pytest **1240/1240**（计数自洽钉同步）；e2e PASS；三字库宽字压力串残余交叠审计归零 |
| 2026-09-06 12:47 追加 | 默认字体批 `6b0ec59` 双机上线：`_DEFAULT_FONT_PREFERENCE=("blood8真迹_xiongzai",)` 偏好+回落（用户拍板「手写感」），生产冒烟 font=blood8真迹 missing=[]；pytest 1242、preflight 回基线；部署脚本 `tmp/deploy_overlap_0906.py`（不入库） |
| 部署状态 | **已部署（2026-09-06 12:15，`81201a0`，见 `docs/deployment.md` 首条）**：5 文件双机 SHA 逐位相符、CR=0、备份 `/root/hutuji-bak-20260906-overlap/`；阿里云 mcp+bridge+portal+worker@{1..8}、京东云 mcp 全 active；生产冒烟繁体输入 missing=[]、475 行一页、预览目检无叠字；preflight 24/33、28/33 回基线。真机重写验收未执行 |

### 出图速度 HIL 判决实验夜场（2026-09-05 晚，机器时间，结论未最终定案）

| 项 | 说明 |
|----|------|
| 范围 | 本仓零代码改动；xiaozhi 仓未动（HEAD 仍 `5e90cd3`）。只新增取证脚本与证据日志 |
| 新脚本（tmp/，gitignored） | `tmp/cap_bf_serial_0905.py`（COM13 只读 `?` 轮询 6.7Hz 抓 Bf/MPos/FS，dtr/rts=False）、`tmp/cap_speed_com14_0906.py`（COM14 全量日志落盘）、`tmp/gen_s1_s4_0906.py`（S1 共线 200 行/S4 单动 50mm 判决包生成+串口/Telnet 发送器，含 Telnet 抢槽重试环）、`tmp/cat_floor_0906.py`（真实 gcode 运动地板分解，含 G2/G3 弧长与 junction deviation 块链仿真） |
| 证据（results/local-only/2026-09-05/，gitignored） | `hil-speed-com14.log`（COM14 全量，含两次同页灌流与埋点直方图）、`bf-watch-com13-121023.log`（Bf 第一段 12:10–12:42，S1/S4 窗口）、`bf-watch-com13-125528.log`（Bf 第二段 12:55 起，**含 13:08 重画灌流窗 t+757–784 全轨迹**，cap-bf3 退出时正常落盘）、`tmp/cat_124322.{svg,gcode}`（生产 SVG 拉回本地经 svg_to_gcode 确定性重生成，343 行与实流一致） |
| 实测结论（S4/S1/双跑） | S4 串口 50mm 单动 1.03s → A′（$$ 失真）排除；S1 串口 200 行 3.17s（ok 齐 0.66s=3.2ms/行）→ D（Grbl 逐行失速）排除；同一张 343 行猫页 ON 变体（日志压制）灌流两次：发送段 26s/25s；运动地板按真实 gcode 算 25.0s（绘制 15.5+Z 3.4+G0 6.1）——**灌流段贴运动地板，残差 ~1s 级**；页尾段（归位→M30→换纸→done）12–13s 内出现 7–8 次 1000ms 应答超时，**全部落在 `state=paper_change` 之后——等的是换纸机构物理动作 + M30 应答，不是协议空转**（advisory 纠正，此前「纯等待/确定收益」措辞作废）；该段是否含可压缩余量须先以 COM13 侧换纸时序取证机械耗时再定 |
| Bf 轨迹关键读数 | 6.7Hz 采样下 planner 全程未满（Bf 最低 100，多数 200–249）；曾抓到 0.15s 内 Bf 249→174（75 块）——**经 fable Round-5 纠正并本地复核定案：这不是 75 行 ok 爆发（在途上限 32 行，物理不可能），而是一条 G3 弧被 $12=0.002 切成数十根弦逐块入 planner（每弦 plan_buffer_line+recalc ≈2ms）**，「telnet 快路径」证据不成立；稳态 ok 节拍 ~73ms/行（埋点直方图 ok 延迟均 1387–1455ms、在途 17–32 行窗口顶满）。两端 TCP_NODELAY 均已源码确认（xiaozhi `hutuji_pipe.cc` setsockopt / Grbl `TelnetServer.cpp:94` setNoDelay）。**fable 否决延迟 ACK/Nagle 归因**（双端 NODELAY + lwIP quick-ack 每 2 段即 ACK + 双向捎带，互锁不成立）；候选收敛为「ok 在执行时而非入 planner 时送达」的未知队列（Grbl→S3 TX 方向或 fork 内 loopTask 每行写路径） |
| 心跳发现 | S3 的 `?` 探活是**静默触发**（`hutuji_pipe.cc:252` recv 超时分支才发），灌流期间 ok 流不断 → 全程不发 `?` → 今晚数据无灌流期 `?` RTT（fable 指定的 C/D 判别字段）；灌流 25s 内 Grbl 状态报告零到达属预期行为非扣留证据。Bf 第二字段在全部采样点 = 1200（telnet RX 空，但无灌流中采样）。判别改由明日 PC-telnet 对照实验注入 150ms 周期 `?` 取得 |
| 会诊状态 | fable Round-5 已收齐（通道两次挂起后第三次成功）：纠正两处误读（弧爆发/猫页残差≈0）、否决延迟 ACK 归因、给出「?」RTT 判别表（5–30ms→D loopTask 逐行服务器；≈1.4s→C TCP；请求快但回包与 ok 同批→Grbl→S3 TX 方向）、写字页 58ms/行残差若明日复现则锁定「loopTask 每行固定开销」（WiFiClient::write 等可写/Telnet_Server::write 末尾 wait(0)/每行状态报告写），并指出合包吞 ok 旧案更可能是 S3 解析器按 recv 边界计数的 bug 而非协议问题（值得回头查） |
| 明日待办（判决） | ① 先用今天固件状态经 S3 重跑写字页前 1000 行——113ms/行若消失则整套 TCP 议题无回报（fable 预测矩阵：健康≈47s / Grbl 固定开销≈110s）；② PC→Grbl telnet 三文件对照（S1 200 行 + 猫页 343 行 + 写字页 1000 行），PC 侧 TCP_NODELAY + 150ms 周期 `?` + 同字段记录 + Wireshark 抓包，S3 物理离线（含 WebUI 页全关）；③ S3→PC 假 Grbl + Wireshark（零固件改动，可先跑）；④ 页尾段先取证再定论：COM13 侧抓换纸机构机械时序（M30→纸动作→PaperStatus→ok），有机械外余量才谈优化；⑤ **预览二次 HTTPS 卡死（服务端 200 已发、设备 60s 收不到头、内部堆水位 1151B）是最高优先用户可见缺陷** |

### 文档导入（Word/Excel/PPT/CSV/TXT → 写出/画表格）经 draw-portal 传图页（2026-09-04，用户原话「三种全做」）

| 项 | 说明 |
|----|------|
| 授权 | 用户原话：「可以实现导入表格或者word 或者ppt实现写出来或者画出来吗？？」+「三种全做」+ 后续「可以参考奎享逆向」（列宽/合并格/隐藏表口径对齐 `D:/Users/kxnx` 反编译 `excel/c/b.java`，findings/25） |
| 范围 | 新增 `mcp-server/hutuji_mcp/{doc_ingest,table_layout,article_pages}.py`；改 `server.py`（WRITE_PAGE_* 与 `_pages_argument` 下沉 article_pages、保留别名）、`gcode_gen.py`（同步 `svg_to_gcode` 收 `normalize=`）、`write_text.py` 未动；portal 侧 `upload_kinds/pipeline/push/config/app/index.html` + `deploy/env.draw-portal.example` |
| 语义 | docx/pptx/txt → 文章链「写出」（write_text 同参数）；xlsx/csv → 表格链「画出」（格线折线笔画+格内字，同过 §5 闸）；job 主 basename 恒 = 第 1 页，整篇页表存 job_dir `pages.json`（无 DB 迁移）；发送时 `publish_doc_pages` 逐页重发布 + `page_ttl_offsets` 错峰 + `pages_argument` 单源序列化 |
| 测试 | mcp-server `test_doc_ingest.py` 26 项 + `test_table_layout.py` 11 项（含真 vpype normalize=False 端到端）；draw-portal `tests/` 41 项（新增 `test_doc_upload.py` 7 项端点路由/错误映射/整篇发送 pages 参数钉死）；门禁 hub 层不覆盖 portal 测试，单独跑 |
| 部署状态 | **已部署（2026-09-04 19:35，见 `docs/deployment.md` 首条）**：11 文件双机 SHA 逐位相符、双机 draw-portal.env 幂等补 `DRAW_PORTAL_GFONT_DIR`；生产冒烟双机 SMOKE PASS（docx 813 行 / xlsx 表格 334 行 G-code，真字库真 vpype）；preflight 回基线 24/33、28/33。**20:10 追加**：标题/编号批 `4637b56` 双机上线（列表编号还原）。**21:35 再追加**：标题放大居中批 `f3247f9` 双机上线（18mm 放大+落墨居中 95.0 实证，冒烟 SMOKE PASS 双机逐位相同、preflight 回基线）。**21:55 再追加**：Y 方向定案批 `b05f81f` 双机上线（关 vertical_flip，首笔 Y=8.0 原点侧冒烟实证；用户纸面复验待做）。**22:20 Y 限幅批 `cc36459` 当日被实机判定反转**；**22:50 终局**：轴角色反转批 `882956a` 双机上线（X≤277/Y≤190 + vpype --landscape 坑修正，17 字长行单行/横版 layout 不越界冒烟实证、preflight 回基线；xiaozhi 仓互钉已 amend 为 X277/Y190 本地提交未推，板上点动包线仍 190 待重刷）。**2026-09-05 追加**：笔顺教学批 `bde425c` 双机上线（hutuji_teach + strokes.jsonl 数据目录，冒烟逐位相同、preflight 回基线、提示词压缩推云 2837 字符）。真实设备 HIL 未执行 |



### 故障处理审计收口：长词硬断 + 空白段跳过 + 字库 IO 收窄 + breaker 故障隔离（2026-09-04 傍晚，审计发现 + 用户拍板「统一修复」）

| 项 | 说明 |
|----|------|
| 动机 | MCP 服务故障处理全面审计结论：核心路径合适（fail-closed/窄异常/取消语义/资源回收均有测试钉死），排版链 2 缺陷 + 工程面 3 弱点待修；用户拍板统一修复 |
| D1 长词硬断 | docstring 虚称「长词硬断」实测从未实现：单 token（长 URL/英文串）超行宽时独占一行、墨迹顶出 §5 页限（实测 60 个 x 墨至 **288mm**）被 `validate_gcode` 整单拒绝。实现：`adv` 计算后立即逐字切分（`tokens.insert(i+1, tok[1:])` 尾回队），**必须先切再算 overflow**——首版误放 overflow 检查之后，未断长尾先把单字行头冲成「每字一行」病态（实测 60 字排成 3 页 27 行），测试用精确行数钉死防退化。修复后真字库实证：60 字 1 页 2 行、max X=177.8mm |
| D2 空白段跳过 | docstring 称空段跳过，纯空格/制表段实际仍占一整行（空 flush 也增 line_count）；修为段循环头部 `paragraph.strip()` 判空整段 continue，顺带去掉段落枚举冗余与恒真条件 |
| W1 字库 IO | `load_font` 两处 `open(...).read()` 裸抛 OSError 且未用 with 关句柄；抽 `_read_font_bytes()` 独立函数（可注入故障），扫描循环与终选解析统一 `except (GfontError, OSError)`——扫描跳过坏文件继续选最全，终选映射 `WriteTextError`（用户可读白话） |
| W2 | 截断边界 carried 开标点丢弃属预期行为：补注释钉死「truncated=True 如实上报，不为半个标点单独续页」 |
| W3 breaker | ① 状态 JSON 损坏旧静默按 closed → 补 warning（`get_state`/`list_open_backends` 两处）；② **Redis 掉线会砸付费生成主链**（`record_outcome`/`is_open` 裸传 ConnectionError）——store 交互包 try/except 故障隔离：失败只告警、按无闸放行（可用性优先；账本事件独立于熔断存储照常入队） |
| 测试 | `test_write_text.py` 24→28（长词硬断精确行数钉死/空白段跳过/扫描跳过读失败/具名字库读失败映射）、`test_breaker.py` 4→7（损坏 JSON 告警、record_outcome 存储故障存活、enforce 下 is_open fail-open）；计数三处同步（AGENTS.md×2 + 本文件 rollup），全套 **1166** |
| 部署状态 | **已部署（2026-09-04 17:57，见 `docs/deployment.md` 首条，提交 `42b05a5` 登记）**：write_text.py + breaker.py 双机上线（sha 逐位相符、py_compile 过；阿里云 mcp+bridge+worker@{1..8}、京东云 mcp 重启）；冒烟默认字库 3 行页 max X=180.6mm、missing 空；preflight 双机回基线（24/33、28/33） |

### 排版避头并入加 190 上限 + 避尾补齐 + 默认最全字库钉死（2026-09-04 下午，advisory + 用户指令）

| 项 | 说明 |
|----|------|
| 动机 | ① advisory 实锤：避头并入无宽度上限，连续闭标点（。」/！」中文常见）在满行把墨迹顶出 190mm §5 硬线 → `validate_gcode` 整单拒绝（功能性失败非外观）；RED 实测旧代码墨至 **198mm**。② 同轮查实避尾从未实现（旧注释「自然折行已覆盖」不实，RED 实测「《」留守行尾墨至 178）。③ 用户指令「字体库要选最全的」——核查确认 `load_font` 空名本已选 declared_count 最大者，缺的是测试钉死 |
| 修复 | `write_text.py`：避头并入加几何上界（`margin_left + line_width + adv ≤ 190`，超限让位折行——标点行首是外观妥协、越界是整篇写不出）；避尾实现（flush 前弹出暂存行尾连续开标点随新行行首，病态整行开标点不 flush）；模块 docstring 补三条规则声明（避头上限/避尾/空段塌缩不产空行——省纸取向的显式取舍） |
| 字库对账 | 生产与本机同 9 个 .gfont：declared_count 与 ZIP 实际条目数**逐一相等**；`如果梦万国库` 12806 最全（ASCII 62/62、拼音声调字符 25/25、CJK 探针 46/46），空名默认即命中；blood8-2/真迹2/方正兰亭/淘宝/陈继世6 拼音 **0/25**（用这些字库写带调拼音会进 missing_chars 占位）；符号大全 350 仅符号 |
| 测试 | `test_write_text.py` 21→24：避尾镜像构造（《恰满行尾，旧行为墨 178 即红）、避头 190 上界（。」恰满行，旧行为墨 198 即红）、默认选最全字库钉死（退回取第一个即红）；计数不变量三处同步（AGENTS.md×2 + inventory 全套 1156→**1159**） |
| 部署状态 | **已部署（2026-09-04 15:44，见 `docs/deployment.md` 首条）**：write_text.py 单文件双机上线（`8c258ecd…` 三方 sha 一致；双机 mcp + 阿里云 bridge 重启，portal/worker 未涉）；冒烟标点密集文 5 页全页 max X=178.603mm、两机返回 font=如果梦万国库，preflight 回基线 |

### 错峰 TTL 改按页行数估时 + 一次性确认补贴（2026-09-03 晚两轮 advisory 修复，当日 17:57 / 18:31 两批双机部署）

| 项 | 说明 |
|----|------|
| 动机 | advisory 复核实锤：统一 300s/页预算建在 124s/页样本模型上（1021 行短文本页），而 12mm 满页实测 10597 行/1708 抬笔（`twopage_run` 取证），按 8.8 行/s ≈ 20 分钟；设备取第 2 页 ≈t0+1212s，旧窗口 t0+900s 即关——首次多页 HIL 必在第 2 页 404，正是错峰要防的故障 |
| 修复 | `_run_write_job` 错峰从「i×统一预算」改「Σ 前序页估时」：`est_j = (lines_j/8.8 + 8)×2`，模块常量 `WRITE_PAGE_LINES_PER_SECOND/WRITE_PAGE_CHANGE_SECONDS/WRITE_PAGE_TIME_MARGIN`（钉实测证据，不走 env）；第二轮 advisory 追加一次性确认补贴 `WRITE_PAGE_CONFIRM_ALLOWANCE_SECONDS=600`（第四常量）：`t0` 到人按「确认」的等待无上界、与页大小无关，稀疏两页确认延迟 >12 分钟第 2 页必 404；多页各页（含第 1 页——确认延迟卡的是第 1 页首次下载本身）统一前移补贴，只加一次不按页乘；单页文章不动时间戳语义不变；`finalize_job`/OSS 元数据链路未动 |
| 拆除 | `config.py` `page_ttl_budget_seconds` 字段 + 校验项 + env 解析全拆（生产双机 mcp.env 从未设置该值，拆除无运行面影响）；README env 表行、`deploy/env.mcp.example` 同步改为常量说明；AGENTS.md 配置面同步 |
| 测试 | `test_write_text.py` 错峰断言改「补贴+公式」双钉死（第 1 页带补贴断言；按返回页行数复算期望前移量，差值中补贴抵消恰好钉死「只加一次、不按页乘」；退回统一预算立即红）+ 常量安全方向钉死（率上调/余量下调/换纸下调/补贴下调即红）；测试总数不变（改写非新增），全套 **1159** |
| 契约 | `docs/protocol.md` §4.2 错峰条目重写（公式、证据、窗口诚实边界：满密度 20 页末页窗口十余小时 = 物理总时长+余量的必然映射）；readiness 文首新条目 + 09-01 旧条目标记 supersede |
| 速度口径更正 | 12mm 满页文字 ≈20 分钟/页（旧对外口径 125s/页是短文本单页样本，低估约 10×）；3–5 页短文 ≈45–80 分钟物理书写 |
| 部署状态 | **两批均已部署（见 `docs/deployment.md` 首两条）**：17:57 批与 pages_arg 同批双机上线（server.py/config.py/write_text.py 三文件 + 京东云顺带收口 breaker.py 旧漂移）；18:31 批确认补贴 server.py 单文件双机上线（阿里云 mcp+bridge、京东云 mcp，worker/portal 未涉未动）。双机冒烟：第 1 页 mtime=t0+~602（补贴生效）、page2 差值=est₁ 逐位相符（补贴一次性实证）、preflight 双机回基线 |

### 京东云 portal 热备 + 设备 token 看门狗（2026-09-03 下午，双机已部署）

| 项 | 说明 |
|----|------|
| 动机 | 用户拍板补齐故障处理审计发现的两个缺口：#3 draw-portal 只在阿里云、主备切换时网页入口裸奔；#5 `HUTUJI_XIAOZHI_DEVICE_TOKEN` 任何节点无检测，作废（控制台「主题配置」面板每次打开重签并作废旧 token）后链式推送静默失能，热备节点到切换当天才暴露 |
| 看门狗 | 新增 `deploy/xiaozhi-device-token-watch.sh`：读 mcp.env 的 DEVICE_TOKEN，每小时经官方设备消息 API 调**只读** `hutuji.status`（不触 TTS/屏显/运动）；200→ok、401/403→invalid+logger err+exit 1（翻转必报、持续失效 6h 节流）、其余→unknown 不翻转（设备离线是常态不是作废证据）；未配 token 静默退出。**刻意不接自动续期**（设备 token 不走 /auth/login，无程序化重签路径）。双机 cron `23 * * * *` 已装；首跑实证：阿里云/京东云 token 当前均**有效**（exit 0）；坏 token 台架 401 报警文本落 journald 实证 |
| portal 热备 | 京东云新装 `hutuji-draw-portal`：36 文件 HEAD blob 导出 CR=0、SHA-256 逐位相符（首轮校验曾踩两处验证器陷阱——清单未随 scp 上机的假绿、Python `write_text` CRLF 清单污染文件名——均当场识破重验）；env 三密钥（SESSION_SECRET/TOKEN_CIPHER_KEY/PROVIDER_CIPHER_KEY）京东云本地新生成、USAGE_INGEST_SECRET 取自本机 mcp.env（sha256 前缀比对一致）、SMTP 等余项从阿里云 env 管道复制（未显示未落库）；`INTERNAL_ALLOW_CIDRS` 置空。空库（买家账号不复制，切换后需重注册——登记为已知限制）；fresh DB 由 store 自建。探针全绿：health 200 / 未登录 401 / 页 200 / `/mcp`·`/files`·`/register` 回归不变 |
| nginx | 京东云 `sites-enabled/hutuji` 443 块插入 `/draw-upload/` location（片段同源）；`deploy/nginx-hutuji.conf` 模板同步补该 location（否则 preflight 模板一致性层恒红），模板与京东云现役 LF 归一化 md5 对账一致。纯新增 location 不动 limit_req key/zone，reload 合法（非 2026-08-08 陷阱面） |
| 陷阱实证 | 装 portal unit 时的 `systemctl daemon-reload` 把 mcp 的 ExecStartPre 运行记录清成 `[n/a]`，preflight「两个 unit」层由绿转红——restart hutuji-mcp 重建记录后回绿。**京东云运维教训：daemon-reload 后须 restart mcp，否则新鲜度闸误红** |
| preflight | 京东云回基线 **28/33**（5 fail = bridge×4 + 公网真下载 DNS 指阿里云，均热备预期）；阿里云未动运行态（仅 deploy/ 镜像同步） |
| 测试 | `test_contract.py` +2：看门狗形态（探针形状/状态机/节流/刻意无 auto/token 不出现在 logger·echo）+ 模板携带 portal location；620→622，全套 **1159** |

### write 链 pages_arg 透传件（2026-09-03 下午修，当日 17:57 已随错峰批双机部署）

| 项 | 说明 |
|----|------|
| 动机 | 用户点名复查写字链发现的代码级缺陷：设备幂等重入要求**完全相同参数**（protocol §4.2），而链式推送失败走两步回落时，云端 LLM 需从 `hutuji_write` 返回值重组 `pages`——返回的是含 lines/bytes/pen_lifts 多余字段的对象数组，LLM 几乎不可能产出与链式推送逐字节相同的紧凑字符串 → 参数不同 → busy 拒绝，多页回落实际不可用；且 `hutuji_write` docstring 无「不要重复推送」指引 |
| 修复 | ① `_pages_argument()` 序列化单源（`server.py`），链式推送与返回值共用，物理上消灭漂移可能；② 返回值新增 `pages_arg` 字段（未配 token 时也在——正是回落场景）；③ docstring 补「服务端自动推送、不要重复推送、回落时 pages_arg 一字不改透传、missing_chars/truncated 如实告知、确认后 hutuji.confirm」全链指引；④ protocol §4.2 增「两步回落透传」条目；⑤ 顺带修正 `write_text.py` max_pages 注释容量口径（12mm 默认 ≈117 字/页，20 页 ≈2300 字先到页数闸，原文「≈两万字」是旧 8mm 口径） |
| 测试 | `test_write_text.py` +2：端到端钉「返回值 pages_arg == 链式推送实际发出 pages 逐字节一致」（含无 token 时 pages_arg 仍在）；19→21，全套 **1154**；计数自洽测试 RED→GREEN 实证（先红 inventory 1152≠1154，改后绿） |
| 设备侧 | **零改动**——`pages` 参数格式、键序、校验全部未动，纯云端返回值新增字段 |
| 部署状态 | **已部署（2026-09-03 17:57，见 `docs/deployment.md` 首条）**：与错峰估时同批双机上线；生产冒烟实证 pages_arg 与顶层 url 一致 |

### draw-worker 悬挂根因修复（2026-09-03 上午定位修复，午间已部署双机）

| 项 | 说明 |
|----|------|
| 根因 | **解释器终结化（Py_FinalizeEx）遇深度换出堆**：worker 跑过付费描摹后堆 HWM ~300MB（skimage/sknw/numpy），生产机 RAM 1870MB + swap 常驻已用 1.3GB（`vm.swappiness=10`），空闲 45h 的堆被逐页换出；终结化的 GC 遍历/模块拆解要读写整个堆 → 被迫把全部换出页从云盘 swap 逐页换回，8 进程并发换入 + mcp 同时重启自检抢 I/O → 2026-09-02 22:01 重启时 8/8 worker 停机日志后挂 267–298s（全 exit 0、无 SIGKILL） |
| 复现矩阵（生产空闲实例 db15 同构实跑，取证脚本 `tmp/repro_swap_exit*.sh` 不入库） | 未修版压出 162MB → 10–12s（py-spy 主线程 D 态无 Python 帧、maj_flt +21K 爬升、VmSwap 同步回落）；8 进程压出 1.2GB 并发 → 46.8s（并发放大成立）；同状态 SIGKILL → **0.1s**（内核直接丢 swap 槽不读盘，反证耗时全在终结化读页）；**修复版压出 164MB → 3.03s**（≈BRPOP ≤5s 窗口内即退） |
| 判决性验证 | 影子包 `/tmp/pkg0903`（符号链接 + 补丁文件）：`os._exit(7)` 变异实测退出码=**7**，证明补丁路径真执行（首轮验证曾因 `python -m` 把 cwd 排在 PYTHONPATH 前而误跑未修代码，已修正并记录此坑）；`python -m` 的 cwd 优先规则 = 复现实验必须 `cd` 到影子包目录 |
| 修复 | `draw_worker.py` `main()`：`run_until_complete` 干净返回后 `logging.shutdown()` + stdout/stderr flush + `os._exit(0)`，跳过 Py_FinalizeEx。worker 无外部终结化义务：Redis/PG 连接内核回收、job_ledger/usage 线程本就 daemon（自然退出同样被掐断）；异常路径（含缺队列地址 SystemExit）不经过此路，退出码语义不变 |
| 测试 | `test_draw_queue.py` +2（干净路径必须 `os._exit(0)` / 错误路径绝不硬退出），23→25；全套 **1152**；AGENTS.md 两处总数与分项同步 1150→1152 |
| 已排除嫌疑 | job_ledger/usage_report daemon 线程（daemon 不拦退出）、metrics task（取消安全）、系统级 journald/Redis（mcp 秒退证伪）、cgroup 残余（无）、系统级 CPU/IO 争抢（mcp/bridge 秒退证伪） |
| 取证陷阱 | `deploy/worker-hang-watch.sh` 保留在阿里云 cron（deactivating>25s → py-spy dump `/root/worker-hang-dumps/`），待修复版生产 restart 实证秒退后拆除 |
| 部署状态 | **已部署（2026-09-03 午，见 `docs/deployment.md` 首条）**：单文件 `draw_worker.py` 双机落盘 SHA-256 `f5d55e4e…` 与 HEAD blob 逐位相符、py_compile 过、备份 `/root/hutuji-bak-20260903-worker-exit/`；阿里云 restart worker@{1..8}+mcp+bridge，京东云文件+mcp restart；preflight 回基线（阿里云 24/33、京东云 28/33，零新增 fail）。**修复版实证未完成**：本次停的是旧代码内存态进程（~15h 工龄，单实例 10–18s，与换出深度区间一致），新代码进程的老化 stop 才给判决——取证陷阱 cron 保留至彼时拆除 |
| 附带发现 | 商汤生图 API 当前返回 429、imagefree 备用端点 401（复现 job 日志实证；Agnes 主链正常出图，对冲链按设计工作） |

### hutuji_write 生产上线 + 字体分发（2026-09-02 晚，双机已部署，用户拍板「字体你搞」）

| 项 | 说明 |
|----|------|
| 动作 | 6 个代码文件（`30eb2e0`→HEAD delta：`server.py`/`config.py`/`gcode_gen.py`/`output_store.py` + 新 `gfont.py`/`write_text.py`）`git cat-file blob HEAD:` 导出 CR=0、双机 SHA-256 与本地 blob 逐位相符、远端 py_compile 过；9 个 `.gfont` 从本机 kdraw 安装目录 scp 双机 `/opt/hutuji/gfonts/`（本机/阿里云/京东云三方逐文件 SHA-256 全符）；双机 `mcp.env` 追加 `HUTUJI_GFONT_DIR=/opt/hutuji/gfonts`（备份 `.bak.20260902-write`，600 保持）；阿里云 restart mcp+bridge+worker@{1..8}，京东云仅 mcp（bridge 保持 disabled 热备态）；备份 `/root/hutuji-bak-20260902-write/`（双机同名） |
| 冒烟 | 双机本机链（免费零付费，`chain_push=False` 不触设备）：`永国中，你好，写字机。` → 1 页 350 行 7855B 42 抬笔无缺字，自动选「如果梦万国库」，`size_mm=12.0` 新默认生效；阿里云公网真下载 200/7855B 字节级相符，京东云 loopback `/files` 200 逐字节相符 |
| preflight | 阿里云 **24/33**（9 fail 与 nginx 布局基线逐项相符）、京东云 **28/33**（5 fail 全为热备预期分歧）——双机口径不变，零新增 fail |
| 新观察 | 8/8 worker 收 SIGTERM 后消费循环 4s 内退出，但进程又挂 **267–298s** 才消失（全 exit 0「Succeeded」、无 SIGKILL——初报「撑满 300s 被 SIGKILL」有误）。**2026-09-03 上午已定位并修复，见本页顶部「draw-worker 悬挂根因修复」小节** |
| 未做 | 语音/设备链真实 `hutuji.write` 触机 HIL（需用户在 192.168.1.13 装纸在场）；修复版 worker 老化后 stop 实证（新代码已于 2026-09-03 午部署，实证等下次 restart） |
| 证据 | 详见 `docs/deployment.md` 首条（逐文件 SHA-256、MainPID、冒烟返回 JSON 全文） |

### hutuji_write 默认字高 8→12mm（2026-09-02，已提交 `1a7f9d1` 并推 origin）

| 项 | 说明 |
|----|------|
| 动机 | 用户反馈观感「字体太小」：8mm 字高在 190×190 页区占比小、笔尖细（0.5mm 级），且语音场景云端模型基本不显式传 `size_mm`——默认值就是真实出字大小 |
| 改动 | `mcp-server/hutuji_mcp/server.py`：`hutuji_write` 签名默认 `size_mm 8.0→12.0`，docstring「默认 8→12」；`mcp-server/hutuji_mcp/write_text.py`：`WriteLayout.size_mm` 默认同步 12.0（两处默认必须一致，否则工具面与排版面脱节）。边界 2–40 / 行距 1.6 / 字距 0.8 / 边距 8 均不动；调用方显式传参不受影响；行为变更仅此一处 |
| 容量影响 | 字步进 8.8→12.8mm：每行约 19→13 字（首行缩进后约 11）；行高 12.8→19.2mm：每页约 13→9 行。长文章页数约翻倍；错峰 TTL 预算 300s/页与 §5 校验不受影响 |
| 验证状态 | `test_write_text.py` **19/19** 通过（改前已全仓 grep 核实无任何测试钉死 8.0 默认：既有用例全部显式传 10/20/40）；hub 门禁 **overall=pass exit=0**（pytest **1150/1150**、e2e `PASS`、指纹 258/258）。~~新默认仅本机与后续部署生效~~（2026-09-02 晚追记：已随生产上线批双机部署，见本页「hutuji_write 生产上线」小节与 `docs/deployment.md` 首条） |

### env 模板补 `HUTUJI_GFONT_DIR` 注释块 + preflight 新增判据纪律注释（2026-09-01，已提交 `1a7f9d1` 并推 origin）

| 项 | 说明 |
|----|------|
| 动机 | 写字排版批把 `HUTUJI_GFONT_DIR` 落进 `config.py` 与文档，但 `deploy/env.mcp.example` 漏收——模板是双机 mcp.env 的填写底稿，缺项只能靠人记（2026-08-31 批次起即如此，本轮 grep 复核确认模板对该变量零命中） |
| 改动 | 6 个文件全为文档/模板/注释面，零行为变更：① `deploy/env.mcp.example`：curated 块后新增 5 行注释块（空=工具禁用；字体为奎享专有素材不入库、无法随仓 scp 分发；本机取 kdraw 安装目录；生产分发方式待用户拍板；示例 `# HUTUJI_GFONT_DIR=/opt/hutuji/gfonts`）。② `deploy/preflight.sh`：`EXPECTED_CHECKS` 前新增 7 行纪律注释，把「不加 gfont 探针」的理由从 inventory 挪到脚本内（评审 P3：决策只落 inventory，加探针者不会来读）。③ 本页本小节（+9 行）。④ `docs/agent-line-refs-baseline.json` 重登。⑤ `docs/kxnx/findings/28-five-slice-crossrepo-review.md` 1 处人工改指 + 2 处自动重定位。⑥ `docs/kxnx/findings/30-firmware-dual-repo-review-0831.md` 1 处自动重定位 |
| 未决 | ~~字体文件仅存于本机 kdraw 安装目录；生产 `hutuji_write` 启用阻塞在素材授权/分发决策~~（2026-09-02 晚已解：用户拍板「字体你搞」，9 个 `.gfont` 已分发双机 `/opt/hutuji/gfonts/`、env 已配、生产已上线，见本页「hutuji_write 生产上线」小节）。preflight 未加第 34 项探针——无条件探针在双机会永久 fail；若日后要加，须走 Phase-3 条件模式（`GFONT_DIR` 未配即 return 0 计 pass），避免撞 `test_contract.py` 的三方形状对账（`EXPECTED_CHECKS` = `\ncheck "` 条数 = 判据名清单长度）。~~提交未决~~（2026-09-02 追记：用户同意后已随 `1a7f9d1` 入库并推 origin） |
| 指纹 | 本小节插入使 findings #28/#30 对本页的 4 处 `path:line` 引用整体下移，分两轮收口（第二轮因本行所在小节自身增行）：自动重定位 3 处（`--fix-line-refs`），人工改指 1 处——findings #28 的「gitignored 目录内」引用每轮都落到空锚点/歧义，逐条核对后手改。第三轮随 09-02 字高小节插入再移 9 行、第四轮随 09-02 晚生产上线小节再移 11 行，同法收口。当前落点（2026-09-12 再核，含 09-11 小程序批 +11 行与 09-12 写字单页批小节插入）：findings #28 → 本页 `:148`（测试计数行）/`:655`（quickdraw 387 别名键）/`:660`（quickdraw 计数行），findings #30 → `:727`（08-23 fb2b3b5 COM13 烧录自证）；findings #30 另指 `release-readiness.md:220`（08-28 `7f48217` `$1=255` 用户原话进固件条；findings #30 内 R2 行实测引用于此——旧叙述误记为 08-30 指纹门禁条，2026-09-24 按 findings 真实引用改正）。`--accept-line-refs` 每轮明细均只含本次有意编辑所致位移。`deploy/preflight.sh` 无 `path:line` 引用面（全仓 grep `preflight\.sh:\d+` 零命中），注释插入不影响指纹层 |
| 验证状态 | hub 门禁 **overall=pass exit=0**（HEAD `65561660`，preflight 注释插入后复跑）：pytest **1150/1150**、e2e `PASS`、指纹 258/258、外仓引用 243、必备文件 36、治理文档 18/18。~~未执行：任何生产动作~~（2026-09-02 晚追记：双机 mcp.env 已加该变量、字体已分发、生产已上线，见本页顶部小节）；设备侧两页错峰 TTL 真机 HIL 仍未做 |

### 小智控制台登录自动化（2026-08-31，调研→已实施上线阿里云主节点）

| 项 | 说明 |
|----|------|
| 动机 | 用户问「控制台 token 过期能否自动获取、账号能否放 VPS」 |
| 前端证据 | `xiaozhi.me/console` 现行 bundle：`GET /auth/captcha` 返回 **SVG 文本验证码**（svg-captcha 类，cookie `captcha` 键控）；`POST /auth/login` 体 `{username,password,captcha_code}`；登录应答 `data.token`/`data.user`；无 refresh/API key 面 |
| OCR 可行性实测 | 本机 5 样本：SVG→PNG（resvg-py）→ddddocr，3 全对 1 差大小写 1 错 1 字符（单次约 60–80%，重试环可收敛）；证据 `tmp/tmp_cap_{0..3}.{svg,png}`、`tmp/tmp_captcha_probe.{bin,png}`（本机不入库）；临时 venv `tmp/capocr_venv/` |
| 结论 | 技术可行且**已实施**（同日用户拍板全自动，仓 `ebdcce3` 已推已上阿里云）：看门狗 401 → `deploy/xiaozhi-token-auto.py` OCR 登录（重试 ≤5 轮）→ 复用 `refresh-xiaozhi-token.sh` 写回；凭据 `/etc/hutuji/xiaozhi-login.env`（600；用户名已按 JWT telephone claim 预填，**口令待用户人工填**；当日 token 已经浏览器 relay 管道双机写回，探针双过）；部署脚本 `tmp/deploy_token_auto_0831.py`（不入库）；详见 `docs/deployment.md` 首条 |

### 网页「文字生图」入口批（2026-08-31，已提交 `30eb2e0` 并双机部署）

| 项 | 说明 |
|----|------|
| 动机 | 用户拍板「都做」：draw-portal 补「文字→AI 生图」入口 + 传图页体验优化 |
| MCP 侧 | `server.py`：`POST /internal/draw`（loopback + `hmac.compare_digest` 共享密钥，未配/外网/错钥一律 404；400 校验、错误映射 400/429/504/502、nosniff）；`_run_draw_job` 加 `chain_push` 形参（False 时不经全局 token 推设备）；`_draw_inflight` 以 `(description, chain_push)` 分键（`\x00nopush` material）；`draw_queue.py` Redis payload 带 `chain_push`（旧 payload 缺省 True）；契约 `docs/protocol.md` §10.4.8 |
| portal 侧 | 新增 `draw_portal/generate.py`（`request_internal_draw`/`materialize_generated_job`，`_DRAW_FILE_RE` 文件名闸 + 下载限长 512KiB/4MiB）；`app.py` `POST /api/draw/generate`（503 未配密钥 / 400 空描述 / 429 配额 / 400·429 透传 detail、504·502 固定白话）；`config.py` +3 env（`DRAW_PORTAL_MCP_INTERNAL_BASE_URL` loopback 强校验 / `_GENERATE_DAILY_LIMIT` 默认 10 / `_GENERATE_TIMEOUT_SECONDS` 默认 300）；`index.html` sheet 双页签 + 生图表单；传图 UX：>4MB 位图本地 canvas 降压缩、拖拽/粘贴进图、选后本地缩略图、空图库文案 |
| 测试 | mcp `test_draw_queue.py` 20→23、`test_contract.py` 607→617（全套 **1332** 项（2026-09-09 小派文字聊天批 +28：新增 test_chat.py——payload 校验/DRAW:: 标记解析/LLM 窄异常//internal/chat 闸门；另补登前期漏记：test_device_messaging.py 8、test_i2i_prompt_sketch.py 6、test_write_text.py 45→46、test_curated_server.py 5→6）（2026-09-08 量产加固批 +1：test_contract.py 628→629，refresh draw-portal token 互联形状钉死）（2026-09-08 TCB i2i 批 +11：新增 test_tcb_i2i.py 11，载荷/鉴权/下载链/success=false 不下载/401·502·坏 JSON·缺 URL 窄异常/url_guard 拦私网零下载/缩图与输入闸）（2026-09-08 魔搭/TCB 通道批 +17：新增 test_modelscope.py 17，i2i 字段/轮询/错误不回显 + t2i racer 接线 + config 校验）（2026-09-08 SSRF/并发加固批 +5：test_modelscope.py 17→22，url_guard 下载侧 SSRF 拒绝矩阵）（2026-09-07 双仓评审 minor 修复批 +7：test_contract.py 623→628（竖版转置正例互换 + fail-closed 四态拒绝矩阵）；test_write_text.py 43→45（Tab/空格测量同价、拉丁→CJK 边界避让计入行宽测量））（2026-09-06 t2s 韧性批 +1：test_write_text.py 42→43，映射表缺失降级）（2026-09-06 默认字体批 +2：test_write_text.py 40→42，blood8_xiongzai 偏好命中/回落）（2026-09-06 字重叠修复批 +6：test_write_text.py 34→40，步进契约/拉丁半步进+宽字墨右缘/繁简归一/t2s 幂等）（2026-09-05 弧拟合批 +9：新增 test_arc_fit.py，G2/G3 拟合 + 急回折 342° 整圆事故钉死）（2026-09-05 笔顺教学批 +8：新增 test_stroke_order.py）（2026-09-04 Y 方向定案批 +1：test_contract.py 622→623；2026-09-04 标题放大居中批 +2：test_write_text.py 32→34；2026-09-04 文档标题/编号批 +11：test_doc_ingest.py 26→33 + test_write_text.py 28→32；2026-09-04 文档导入批 +37：test_doc_ingest.py 26 + test_table_layout.py 11；2026-09-04 故障收口批 +7：test_write_text.py 24→28——长词硬断/空白段跳过/字库读失败收窄×2；test_breaker.py 4→7——损坏 JSON 告警/record_outcome 存储故障存活/enforce 下 is_open fail-open），2026-09-03 热备/看门狗批 +2：设备 token 看门狗形态钉死 + nginx 模板携带 portal location（test_contract.py 620→622）；2026-09-03 pages_arg 透传批 +2：透传件与链式推送 pages 逐字节一致/无 token 时仍在返回值（test_write_text.py 19→21）；2026-09-03 worker 干净停机批 +2：干净路径 os._exit(0) 跳过终结化/错误路径不硬退出（test_draw_queue.py 23→25）；2026-09-01 错峰 TTL 批 +3：test_output_store.py 2→4、test_write_text.py 18→19；同日多页云端配套批 +2：预览 PNG 可见性/空页兜底、chain_push pages JSON 形状；此前 2026-08-31 写字排版批 +16：test_write_text.py gfont 解析/排版/端到端；再前 +2：refresh 对端钩子 set -u 子壳隔离 + kb-admin token 互联形状钉死）；portal 新增 `tests/test_generate.py` **12 项**（503/配额/透传分级/物化清半成品），portal 全套 32 |
| 本机联调（不入库） | `tmp/stub_internal_draw_0831.py`（stub MCP /internal/draw+/files）、`tmp/strip_prefix_proxy_0831.py`（8400 剥前缀模拟 nginx）、`tmp/smtp_sink_0831.py`（25 端口 SMTP 黑洞）、`tmp/big_noisy_0831.bmp`（15MB 压缩验证件）、`tmp/portal-e2e-0831/`（SQLite+job 产物）。浏览器实测：注册→登录→文字生图 200+预览加载+保存图库✅、SVG 上传转换 14 行✅、15MB BMP 本地压到 1.3MB 绕过 413（噪声图 BitmapError 正常失败）✅ |
| 部署（已完成 01:48 CST） | 脚本 `tmp/deploy_web_generate_0831.py`（不入库）：8 产物 HEAD blob 导出 CR=0、双机 SHA-256 逐位相符、备份 `/root/hutuji-bak-20260831-web-generate/`；阿里云 restart mcp+bridge+worker@{1..8}+portal 全 active，京东云仅 mcp；阿里云本机无钥/错钥 `/internal/draw` 均 404、公网 404、portal 未登录 401；证据条见 `docs/deployment.md` 顶部 |

### kb-admin 发布幂等（2026-08-30 20:09，阿里云已上机）

| 项 | 说明 |
|----|------|
| 现象 | 后台点发布回 failed，「控制台 API 异常退出 code=1」 |
| 根因 | 内容 MD5 未变时小智 create 回 400「文档已存在」；`api()` 用 `sys.exit(1)`，库调用方捕不到真错误 |
| 改动 | `scripts/xiaozhi_console.py`：`_api_lib`/`_api_upload_lib`；「文档已存在」当成功。`kb-admin` 静态页失败 toast 带 `error_summary`。`test_field_tools.py` +1 |
| 生产 | 两文件 scp 阿里云 + restart `hutuji-kb-admin`；复测 SUCCESS。脚本 `tmp/deploy_kb_publish_fix.py`（不入库） |
| Git | `113b1d4` 已 push origin/main |

### bridge proxy ENOSYS 回归（2026-08-30 20:13–20:26，阿里云已修）

| 项 | 说明 |
|----|------|
| 现象 | 用户「画小猪」小派复读「云端生成服务有问题」；`conversation_events` 调 `% hutuji.draw` 非 `hutuji_draw` |
| 根因 | bridge WSS 在线但 `mcp-proxy` 未 spawn（`proxy_connected=false` 自 19:36）；磁盘 unit 缺 `SystemCallErrorNumber=ENOSYS` |
| 生产 | 补 ENOSYS 于 bridge+mcp unit；备份 `/root/hutuji-bak-20260830-bridge-enosys/`；restart bridge → 双 connected；20:28:49 首条 `tools/call` |
| 仓内 | `deploy/hutuji-{cloud-bridge,mcp}.service` + `test_contract.py` 钉 ENOSYS（本批待 push） |
| 未验证 | 修复后完整语音「画 xxx → hutuji_draw」HIL |

### 高并发 Phase-1/2 + Phase-3a + 假负载压测（2026-08-30，**`4af959d` 已双机；阿里云队列 19:04 已启用**）

| 项 | 说明 |
|----|------|
| 动机 | 用户拍板峰值 **10 画/秒** 架构 sizing；Phase-1 Redis 队列 + 独立 worker，Phase-2 OSS 产物 / 单设备限流 / 对冲降档 / 多 worker systemd；Phase-3a Postgres 账本 + breaker shadow + queue_metrics |
| 新增模块 | `mcp-server/hutuji_mcp/{draw_queue,draw_worker,output_store,job_ledger,breaker,queue_metrics}.py` |
| 修改 | `server.py`（队列入队 + ledger/breaker hooks）、`config.py`、`bitmap_svg.py`（`HUTUJI_DRAW_RACER_MAX` + breaker 采样）、`draw_queue.py`/`draw_worker.py`、`requirements.txt`（+`redis`、`oss2`、`psycopg[binary]`） |
| 部署模板 | `deploy/hutuji-draw-worker*.service/target`；`deploy/env.mcp.example` 追加队列/OSS/PG/breaker env；`deploy/preflight.sh` **EXPECTED_CHECKS=33**（+3 条件探针） |
| 压测 | `mcp-server/scripts/bench_draw_load.py`（替身 `_run_draw_job`，不调付费 API）；`tests/test_draw_queue.py` 含 subprocess smoke |
| 测试 | 本机 pytest 收集 **1068** 项（+19）；Phase-3 专测 `test_breaker.py` 3 / `test_job_ledger.py` 5 / `test_queue_metrics.py` 1；`test_draw_queue.py` 4 + `test_output_store.py` 2 全绿；bench `--rps 10 --duration 5 --workers 4 --job-ms 250` memory PASS |
| 本机 bench | `--rps 10 --duration 10 --workers 8 --job-ms 250` → success 100% PASS（memory://，早前一轮） |
| 默认行为 | **未配** `HUTUJI_DRAW_QUEUE_URL` = 进程内 asyncio；**未配** `HUTUJI_PG_DSN` = 账本禁用；**默认** `HUTUJI_BREAKER_ENFORCE=0` = shadow。阿里云主节点 2026-08-30 19:04 已配队列 URL + DSN |
| 推荐上线 env（未验证） | Phase-1/2 同前 + `HUTUJI_PG_DSN=postgresql://…`、`HUTUJI_BREAKER_ENFORCE=0`（3a shadow；3b 稳定 48h 后改 1）、`HUTUJI_METRICS_INTERVAL_SEC=15`；3c 另需 CDN DNS + `HUTUJI_CDN_ENABLED=1` |
| 设计 | `docs/design/high-concurrency-phase3.md`（3a 仓内已落地；3b preflight 已入模板；3c CDN/lifecycle **未生产验证**） |
| 生产部署（2026-08-30 17:07 CST） | 双机代码+preflight 33 项已 scp；备份 `/root/hutuji-bak-20260830-phase3-4af959d/`；当时 **未配** 队列/PG/OSS env，worker 未 enable。冒烟阿里云 8533B 公网 200/CRC `a1ca7ce8`。停京东云误启 bridge 后：阿里云 24/33、京东云 27/33 |
| 生产启用（2026-08-30 19:04 CST，仅阿里云） | Redis + `HUTUJI_DRAW_QUEUE_URL` + worker@1..4；`HUTUJI_PG_DSN` + `HUTUJI_BREAKER_ENFORCE=0`。worker unit 去掉 `SystemCallFilter`（clone3 SIGSYS 实证）。队列冒烟 9799B/431 行/0.9s。京东云仍不跑队列 |
| 生产 bench（2026-08-30 19:22 CST） | 阿里云 Redis 假负载：`10 rps×300s`、job-ms=3000、32 bench workers（停 systemd worker 隔离）→ 2977/2977 PASS；证据 `tmp/bench-redis-20260830.log`（本机不入库） |
| Git | `2710739` 已 push origin/main（worker seccomp 移除 + breaker samples 跳过 + 文档） |
| 未完成 | 3a shadow 满 48h（起算 19:04）、3b enforce、3c CDN、hub 全量门禁在本 HEAD 一次完整跑通（pytest 层曾被环境中断/600s 超时） |

### 写字机 `$1=255`（2026-08-28：NVS → 机型默认已固化并刷 COM13）
| 项 | 说明 |
|----|------|
| 源码 | `Grbl_Esp32/.../Machines/custom_3axis_hr4988.h`：`DEFAULT_STEPPER_IDLE_LOCK_TIME` 25→255（**已提交 `7f48217`**） |
| 构建/门禁 | `pio run -e release` SUCCESS；`firmware.bin` SHA-256 `8FE663BE…80E635`；fz `standard` overall=pass（llvm 22.1.8 前置） |
| 烧录 | COM13 upload `Hash of data verified`；`$$` 仍见 `$1=255`（NVS）；证据 `results/local-only/2026-08-28/grbl-dollar1-default255-com13-verify.log` |
| 早前 NVS | `$1=250`→`$1=255`+`$S`（`grbl-set-dollar1-{250,255}-com13.log`） |
| 注意 | `$RST=$` 现回落 **255**。S3 **无需**为此刷机 |

### Grbl M30/换纸 `$1=255` 失能守卫（2026-08-28 晚，**已提交 `2cecd2d` 已推 origin**）
| 项 | 说明 |
|----|------|
| 源码 | `Grbl_Esp32/src/GCode.cpp`（M30 页尾 2 处 `$1==0xff` 跳过失能）、`PaperSystem.cpp`（`paper_finish_xyz_after_fault()`：abort/Step2 超时/Step6 卡纸 + 换纸成功恢复） |
| 构建 | `pio run -e release` SUCCESS；`firmware.bin` 1858112B SHA-256 `12275654AF0D85462A4FB30403B8E19402406FA184AC95A5A3DF070C146429A5` |
| 烧录 | COM13 upload `Hash of data verified`（2026-08-28 晚） |
| 验证 | 用户确认画完后电机保持使能、出图正常；证据 `results/local-only/2026-08-28/grbl-m30-enable-hil-monitor.log` |
| fz | 本机构建 SUCCESS；fz `standard` 本机 native_sim 环境预存红（mingw/limits.h），不阻断产品结论 |

### draw-portal 买家网页传图（2026-08-29 入库 + 阿里云已部署）

| 项 | 说明 |
|----|------|
| 路径 | `draw-portal/`（FastAPI + SQLite + 静态页） |
| 部署 | `deploy/hutuji-draw-portal.service`、`deploy/env.draw-portal.example`、`deploy/nginx-draw-portal-snippet.conf` |
| 探活脚本 | `scripts/probe_device_messaging_token.py`（token 勿入库） |
| 契约 | `docs/protocol.md` §10.4、`docs/design/draw-portal-bind.md`、`docs/design/conversation-capture.md` |
| 本批（hub `781dcb3` 已推 main） | 对话上报 API + `conversation_events`；usage 埋点 + `usage_events`；`draw-admin` 会话/usage 视图；MCP `usage_report.py`；固件 `hutuji_conversation_report.*`（xiaozhi `0d041c9` 已推，[PR #1](https://github.com/zhuguang-ZFG/xiaozhi-esp32/pull/1)） |
| 生产 MCP（2026-08-30 12:24） | 双机 `server.py`/`config.py`/`usage_report.py`/`bitmap_svg.py`/`gcode_gen.py` LF blob 与 HEAD 逐位相符；阿里云 restart mcp+bridge、京东云 restart mcp；脚本 `tmp/deploy_mcp_delta_0830.py`（未入库） |
| 生产 draw-portal | 阿里云 active；venv 共用 MCP；数据 `/var/lib/hutuji/draw-portal/` |
| 后台 UI 美化（2026-08-30 14:4x 阿里云） | `draw_admin/static/index.html`（dusk 主题 + 手机卡片布局）与 `kb-admin/kb_admin/static/index.html`（prod 草稿入库 + 移动 sheet/横向滚动）已 scp；SHA-256 逐位相符、CR=0；restart `hutuji-draw-admin` + `hutuji-kb-admin`；公网 `/draw-admin/`、`/kb-admin/` **200**，页面含 `viewport-fit=cover`；脚本 `tmp/deploy_admin_ui_0830.py`（未入库） |

### curated 精选库（2026-08-26，代码已合 main / PR #7；灌库未 flip）
| 项 | 说明 |
|----|------|
| 仓内 | `hutuji_mcp/curated*.py`、`scripts/curated_prep.py`、`CURATED-ATTRIBUTION.md`、测试与 `deploy/hutuji-curated-prep.service` 随文档批次入库；gate v3=`1353336` |
| 阿里云运行态 | `hutuji-curated-prep` enabled+active，写 `/opt/hutuji/curated`；暗部署：`HUTUJI_CURATED_DIR`/`REPLACE_QD` 未开。数据与 audit **不入 git** |
| 本机 | `tmp/deploy-20260826-curated-fill/` 部署草稿；`mcp-server/tmp/curated-fill.env` 含生产 env 镜像 → **禁止入库** |
| 未完成 | `manifest.ready`、抽检、京东云同步、REPLACE 探针；xiaozhi 空坐闸已推未刷机 |


### Twemoji 矢量源灌库通道（2026-08-27，已提交 `580bb02`/`639ee36`；阿里云首轮灌库完成）
| 文件 | 说明 |
|------|------|
| `mcp-server/scripts/curated_vector_prep.py` | 矢量源灌库脚本：下载 → vpype 展平 → RDP 自适应压缩（≤780 点、6 档）→ gate v3 → NNN.svg + manifest 重算 |
| `mcp-server/hutuji_mcp/twemoji_categories.json` | 212 类目 → ~250 codepoint 映射（已对账 QD 298 在册，剔 20 个不在册类目） |
| `mcp-server/hutuji_mcp/CURATED-ATTRIBUTION.md` | 追加 Twemoji（jdecked/twemoji，图形 CC-BY 4.0）署名与改动性质说明 |
| `mcp-server/tests/test_curated_vector.py` | +4 项（映射卫生/转换过闸/空路径/量化闸） |
| 阿里云数据面 | `/opt/hutuji/curated`：首轮 accept 456（两轮含 trumpet 补漏）→ 内容哈希去重 226 → cat 52/狗 52/树 52/房 52/鱼 53，**达标类目 142/298**，ready 仍 False；`/opt/hutuji/twemoji-cache/`（下载缓存，~250 SVG）；crab/1f980 单件 805>800 行放弃；prep 服务已恢复 active；**flip 机器侧抽检 142/142 OK**（运行时全链路），视觉样张 8 类目 7 优 1 弱（树偏小偏稀），多条目抬笔 51–88 软告警已登记为闸重钉样本 |
| 本机证据 | `tmp/curated-vector-data/`（230 条干净库）、`tmp/svg_pilot/`（五类转换预览 + 库内猫预览，均本机留档不入库） |

### curated 达标优先路由 + QD 退役（2026-08-27，已提交 `48b7d05` 并双机发布）
| 文件 | 说明 |
|------|------|
| `mcp-server/hutuji_mcp/curated.py` | 新增 `lookup_preferred`（类目 count≥n_min 即出、manifest 现读保鲜）+ `_pick_and_load` 共用件 |
| `mcp-server/hutuji_mcp/server.py` | 路由改 curated-prefer→AI；删 `_QD_STYLE_RE` 与 quickdraw 运行时查询 |
| 双机 env | 追加 `HUTUJI_CURATED_DIR=/opt/hutuji/curated`（京东云无数据前恒落 AI）；`HUTUJI_PAPER_RISK_GATE=0` 保持 shadow |
| 验证 | test_curated_server 5 项 preference 语义（QD 禁查负断言）、test_quickdraw 删 5 死路由用例留 36、全套 1025/1025、hub 门禁 pass；冒烟 `随便画个猫` → curated hit idx=003、3.4s 零付费零 QD |

### curated 预览坐标框修复（2026-08-27，已提交 `cfee84e` 并双机发布）
| 文件 | 说明 |
|------|------|
| `mcp-server/hutuji_mcp/curated.py` | `_preview_from_svg` 按 viewBox 解析源坐标框（原硬写 256 把 1024/36 空间点全裁出画布，小派预览空白实锤） |
| `mcp-server/tests/test_curated.py` | +2 项（1024/36 双空间像素级有墨断言）→ 6 项；全套 1027/1027 |
| 验证 | 冒烟 `画一只猫` → curated hit → 预览 1898B 完整猫（本机回看）；阿里云 196602/196603、京东云 75901 |

### AI 纸面风险闸批（2026-08-27，已提交 `7bf64a8`/`ee5eca0`/`ff718c8`/`1dbd310`/`0717e25` 并双机发布；设计 `docs/superpowers/specs/2026-08-27-ai-bitmap-paper-quality-design.md`）
| 文件 | 说明 |
|------|------|
| `mcp-server/hutuji_mcp/paper_risk.py` | 新模块：五指标纸面闸（short_g1_ratio/pen_lifts/travel_gap_p50/ink_span/scribble_core）+ `enforce_gcode`；阈值钉 08-26 废/好样本中位；Z5 落笔/Z0 抬笔状态机逐字对齐 `hutuji_gwrite.toml` |
| `mcp-server/hutuji_mcp/bitmap_svg.py`（已修改） | `_rdp_polylines` 迭代化（递归 O(n) 深度在地板低 tol 下打栈）+ `_decimate_polylines_with_floor`（逐级减半 tol、下限 0.3px、不回退未抽稀）+ `_suppress_hatch_clusters`（排线填色短段簇整簇丢弃）+ `generate_bitmap(..., attempt=)` 破缓存；trace 顺序 stitch→hatch→decimate |
| `mcp-server/hutuji_mcp/server.py`（已修改） | `_run_draw_job` AI 路径纸面闸重试环（最多 3 轮、Reject 清半成品、`PaperRiskExhaustedError` 无废 URL）；QD/curated 不经闸 |
| `mcp-server/hutuji_mcp/config.py`（已修改） | `paper_risk_gate`（`HUTUJI_PAPER_RISK_GATE`，默认开） |
| `mcp-server/tests/test_paper_risk.py` / `test_bitmap_decimate.py` | 新测试 13 + 8 项 |
| 验证状态 | 全套 1027/1027；hub 门禁 **overall=pass**（指纹 231/231）。闸批已提交至 `06daa95` 并**双机发布 + 转 shadow**（用户拍板：travel_gap 2.0 疑似误杀正常猫，攒样本重钉后再 enforce；`HUTUJI_PAPER_RISK_GATE=0`）。另修：scribble v2 平移不变窗（版本 1→2）+ 拒收缓存逐出 + shadow 指标日志 |

### 未跟踪文件（本次调研新增）
| 文件 | 说明 |
|------|------|
| `tmp/s3-confirm-capture-0829.log` / `tmp/grbl-confirm-capture-0829.log` / `tmp/s3-confirm-tail-0829.log` | 2026-08-29「能预览却画不了」复现抓包（COM14+COM13）；确认→预取复用→streaming→有墨；本机留档不入库 |
| `tmp/cap_confirm_hil_0829.py` / `tmp/diag_nodraw_0829.py` / `tmp/cap_s3_nodraw_0829.py` | 同日只读诊断/抓包脚本（不入库） |
| `lineart-research-report.md` | 简笔图获取方式调研报告（纯调研产物） |
| `docs/design/quickdraw-local-cache.md` | Quick Draw 本地缓存落地设计稿 v0.1（待用户批准，未实施） |
| `USER_MEMORY.md` | 用户记忆备份（跨会话持久化） |
| `memory_user.json`、`.superpowers/sdd/` | 用户记忆 JSON 备份；`.superpowers/sdd/` 为 SDD 工作流本地任务简报/报告/评审 diff（27 个文件）。`.gitignore` 第 44 行注释虽写 “Superpowers”，实际只列 `.worktrees/`，`git check-ignore -v .superpowers/` 实测 exit=1 **未被忽略**，故必须登记；保留本机不入库 |
| `tmp/` | 临时工作目录；2026-08-25 半墨调查新增 `results/local-only/2026-08-25/{tree-redraw-com14,fix-boot-com14,tree-redraw-fixed-com14}.log`（复发抓包 10 次落笔期空窗 230–640ms / 刷机后启动验证 / 修复后复测空窗 max 30ms），gitignored 留本机；**2026-08-25 补登记**构建/取证产物组：`tmp/build-gate-logs/`（19 个 .log/.err）、`tmp/build_ws35.log`、`tmp/build_gate_env.sh`、`tmp/build_instr_{lichuang,ws35}.ps1`、`tmp/build_hil_ws35.ps1`、`tmp/boot_capture.py`、`tmp/zdraw_capture.py`（出图判别取证：出图前 `$#`/`?` + 画线期 `?` 轮询，COM13 只读、dtr/rts=False 铁律），以及 `results/local-only/2026-08-25/grbl-z-baseline-com13-20260825.txt`（COM13 只读 `$`/`$#`/`?` 快照）；**2026-08-29 补登记**门禁/发布暂存组：`tmp/{gate-hub,gate-docs,gate-docs2,gate-docs-2,gate-docs-accept,gate-docs-paper,gate-docs-paper2}.{out,err}`（门禁 stdout/stderr 留档，其中 `gate-hub.{out,err}` 是 2026-08-27 被 `KeyboardInterrupt` 打断的未完成轮，**不可当通过证据**）、`tmp/{fix-refs,fix-refs2,accept-refs,accept-refs2}.{out,err}`（`--fix-line-refs` / `--accept-line-refs` 重登明细留档）、`tmp/{stage-deploy-0829,stage-scripts-0829,deploy-20260829-prompt,deploy-20260829-drift}/`（08-29 两批发布候选暂存）、`tmp/{pubdl_20260829,pubdl_drift_20260829,pub-draw,pub-draw2}.gcode`（公网真下载复核件）、`tmp/{head-deploy-manifest,head-mcp.service,head-bridge.service,node-lima-manifest,node-jdcloud-manifest,head_pkg,prod_pkg}.txt`（双机运行时包逐文件对账清单）、`tmp/dstage/`（`deploy/` 模板暂存副本，含 `.gitignore`/两个 env example/unit/两个安装脚本）、`tmp/inv_head.md`（本页 HEAD 存档另一份副本，与第 8 行提到的 `tmp/inventory_head.md` 同源）、`tmp/gate-hub-final.{out,err}`（提交前末轮 `profile=hub` 完整通过留档，逐轮覆盖同名文件；末轮 exit=0、12 层全 pass、pytest 1043 passed、指纹 238 处全对，精确时间戳以 `results/hutuji_gate_last.json` 为准）、`tmp/gate-backup/hutuji_gate_last.b7f88e30.{json,md}`（重跑前对上一轮 hub 报告的原样备份，防单槽覆盖丢证）、`tmp/preflight-{lima,jdcloud}-0829.txt`（2026-08-29 晚收口后双机 `deploy/preflight.sh` 复跑原文：阿里云 `pass=21 fail=9`（1 项 unit 系 08-23 手调 el8 + 8 项 nginx 判据锁 Debian `sites-*` 布局）、京东云 `pass=25 fail=5`（4 项 bridge 按单 WSS 热备设计不满足 + 1 项公网 `/files` 安全组 hairpin），逐项与部署当时基线相同，无回归）；**2026-08-29 补登记**实机 `$$` 取证组：`tmp/query_grbl_settings.py`（COM13 只读取证脚本，仅发 `$I`/`$$`/`?`，`dtr`/`rts` 恒 `False`，不复位/不运动/不写设置）与 `tmp/grbl-settings-com13-0829.txt`（取证原文落盘：`$130=210.000`/`$131=297.000`/`$132=200`、`$20=$21=$22=0`、固件 `[VER:1.3a.20211103:]`、`[OPT:PHBSW]`、机型 `Custom 3-Axis HR4988`、STA IP、`<Idle|MPos:0.000,0.000,0.000|Bf:249,128|FS:0,0|Pn:0>`），是 `docs/protocol.md` §5 纸面方向定案与 `docs/bringup-sop.md` 第 5 步行程钉死的唯一本机证据；均留本机不入库 |
| `mcp-server/tmp/{telnet_draw_test,two_opt_ab,capture_resilient,motion_profile_analysis}.py` | 喂流/运动实验件（F 提速 A/B 与 2-opt 排除用；2026-08-25 advisory 后已改：`telnet_draw_test.py` 送机前强制过生产 `validate_gcode`（exit 6 拒发），`two_opt_ab.py` 弃手抄步序改复用 `_vpype_command()` 仅注入 `--two-opt`）。**2026-08-29 补登记**同目录三项：`mcp-server/tmp/models.json`（上游模型清单探测快照，仅型号/模态元数据，无凭据）、`mcp-server/tmp/curated-probe/manifest.json`（入库闸干跑清单，`n_target=1` 各类 count=0，非达标证据）、`mcp-server/tmp/curated-vector-data/manifest.json`（第 49 行 230 条干净库的清单文件，库体本身已被窄忽略规则盖住）。均保留本地不入库 |
| `mcp-server/tmp/qd_categories.txt` | QD 类目清单工作副本，保留本地 |
| `mcp-server/tmp/abkit/`（star.svg + draw_f8000/f10000/f12000.gcode） | F 变体实验件。**`draw_f12000.gcode` 违 §5（头行 F12000、全行 F12000>10000 上限）——禁上机**，现由 `telnet_draw_test.py` 的 validate 闸拦截；f8000/f10000 合法仅作对照。保留本地 |
| `mcp-server/tmp/twoopt_star/{cur,opt}.gcode` | 2-opt A/B 输出对照件，保留本地 |
| `mcp-server/tmp/qd_verify/cat.{gcode,jsonl}` | QD 命中验证产物（jsonl 已被 `.gitignore` 窄规则盖住）；保留本地 |
| `tmp/build_jog_ws35.ps1`、`tmp/flash_jog_ws35.ps1` | jog 批构建/烧录脚本（`--after=watchdog-reset` 路径，与既有 flash_hil 同构），保留本地 |
| `mcp-server/tmp/{bench_u15_lite,junction_ab,junction_ab_preview,run_curated_fill}.py`（**2026-09-01 补登记**） | 此前漏登的实验/运维脚本四件：`bench_u15_lite.py`（u1-fast vs u1.5-lite 评测，请求形状复刻 `bitmap_svg._request_sensenova_image_url`，key 只从 `curated-fill.env` 读且不打印）、`junction_ab.py` / `junction_ab_preview.py`（`_continue_junctions`/`_stitch_polylines` 端点缝合 A/B 与预览，读 `results/local-only/2026-08-27/ai-probe.svg`）、`run_curated_fill.py`（离线灌库运行器，载 `curated-fill.env` 后调 `curated_prep`，不打印密钥值）。均保留本地不入库 |
| `mcp-server/tmp/{curated-logs,curated-probe-audit,curated-probe35,curated-probe35-audit}/`（**2026-09-01 补登记**） | 灌库/抽检留档 18 个文件：`curated-logs/`（08-26 两轮 fill-full 的 out/err + pid/meta + 5 类与 35 类探针日志）、`curated-probe-audit/` 与 `curated-probe35-audit/`（各类目 accept 路径清单）、`curated-probe35/{cat,dog,rabbit}/000.svg`（35 类探针样张）。**凭据面已复核**：out 日志只有 `env_loaded=…名单 llm_len=51 sense_len=35` 形式的长度计数，全量扫 `sk-*`/`Bearer *`/≥32 位十六进制串**零命中**；`curated-fill.env` 本身仍按第 110 行「禁止入库」。均保留本地不入库 |
| `tmp/flash_ws35_build_0901.ps1`、`tmp/postflash_boot_com14.py`（**2026-09-01 新增**） | 0901 waveshare 3.5 刷机脚本（`flash_hil_ws35.ps1` 的复制改路径版，镜像指 `build/xiaozhi.bin`）与刷后被动启动抓包（dtr/rts=False 恒不复位）。`flash_ws35_build_0901.ps1` 内 `IDF_TOOLS_PATH='C:\Espressif\tools'` 沿用自原脚本：**仅供跑 bundled esptool，绝不可复制进构建脚本**——真实构建 env 是 `C:\Users\zhugu\.espressif`（见本页构建证据行）。保留本地 |
| `tmp/{twopage_run,twopage_serve,ttl_sim}.py`、`tmp/twopage_pages.json`、`tmp/{twopage_out,ttl_sim_out}/`（**2026-09-01 新增**） | 错峰 TTL 服务端层取证脚本与产物：`twopage_run.py` 真跑 `_run_write_job` 出两页（`chain_push=False`，避免误推设备）、`twopage_serve.py` 起真 uvicorn 取回四条能力 URL、`ttl_sim.py` 六页反事实对照。**`HUTUJI_PUBLIC_BASE_URL` 只能填 loopback HTTP 或 HTTPS:443**（`config.validate_public_base_url` 硬校验），故本机脚本一律用 `127.0.0.1`，LAN 供页需另行改写 URL 而非改 env。产物目录含 gcode/svg/png，保留本地不入库；证据日志见 `results/local-only/2026-09-01/ttl-stagger-http-counterfactual.log` |

### Quick Draw 本地缓存落地（2026-08-24 设计稿用户批准后实施，已提交 `92b00b7`+`2e0b1f2` 并推 origin；本日复核修复 `1952caa` 已部署阿里云生产并同步京东云热备，两机运行时面与 HEAD 一致）

| 文件 | 说明 |
|------|------|
| `mcp-server/hutuji_mcp/quickdraw.py` | 运行时模块：类目映射 + 笔画加载 + 复用位图产物面 |
| `mcp-server/hutuji_mcp/quickdraw_categories.json` | 类目映射 v3（**387 别名键 → 298 官方在册类目**，已对账 categories.txt；手机改指 cell phone）。2026-08-24 实测：去 `_meta` 后 387 键、去重后 298 值，与 `quickdraw-data/` 的 298 个 `.jsonl` 双向零差；本轮补入 14 个多字别名覆盖原单字类目；2026-08-25 v4 批演进 402 键、C 组合双机部署见本页 C 组合节 |
| `mcp-server/hutuji_mcp/QUICKDRAW-ATTRIBUTION.md` | CC BY 4.0 署名文件 |
| `mcp-server/hutuji_mcp/{config.py,server.py}`（已修改） | `HUTUJI_QUICKDRAW_DIR` 配置项 + `_run_draw_job` 命中旁路 |
| `mcp-server/scripts/quickdraw_prep.py` | 数据准备脚本（蓄水池采样、quote 编码、固定种子可复现） |
| `mcp-server/tests/test_quickdraw.py` | **34 项**契约/集成测试（此前 16 项；本轮补入 4 个最长子串碰撞断言 + 14 个单字类目增强短语断言 + fixture 缓存隔离）；2026-08-25 v4 批后演进为 **39 项**（+3 礼貌前缀/循环剥离/NO_STRIP 守卫），全套 **1145** 项（2026-08-31 写字排版批 +16：test_write_text.py；2026-08-31 +2：refresh 对端钩子 set -u 子壳隔离 + kb-admin token 互联形状钉死；2026-08-30 停机宽限批 +1：worker unit TimeoutStopSec 形状钉死；2026-08-30 优雅停机批 +3：test_draw_queue.py 17→20，worker stop 事件迭代边界退出/画途中跑完当前 job/main 接线子进程回归；2026-08-30 评审修复批 +13：test_draw_queue.py 4→17，_decide_attach 决策矩阵 + Redis inflight 残留 replace/NX 冲突重判回归；此前 2026-08-30 高并发 Phase-1/2/3a +19：`test_draw_queue.py` 4、`test_output_store.py` 2、`test_breaker.py` 4、`test_job_ledger.py` 5、`test_queue_metrics.py` 1、`test_portal_settings.py` 4；2026-08-30 +6：`test_draw_limits_config.py` 3、`test_usage_report.py` 2、`test_field_tools.py` 92→93；2026-08-30 晚 +1：`文档已存在` 幂等；余下历史批次见 git 历史） |
| `mcp-server/tmp/quickdraw-data/` | 全量数据产物（2026-08-24 基线 298 类 × 200 条 ≈ 20MiB；2026-08-25 C 组合换装精选版每类 50 条，见 C 组合节）。历史误记「gitignored 目录内」已于 2026-08-24 撤（实测 `git check-ignore` exit=1、`?? mcp-server/tmp/`、`git add -An` 干跑可见 314 文件）。**决策状态（2026-08-24 用户 advisory 点名）**：本页 `edf68e7` 条目「不新增忽略规则」对**整目录忽略 tmp/** 仍成立——advisory 同文否决整目录方案：AGENTS.md 刷机坑段引用根 `tmp/` 工具（flash_hil_ws35.ps1、reset_wdt_capture.py），整目录忽略会把它们变成只在本机的隐形文件；仅对**数据集本体**开窄例外：`.gitignore` 新增 `mcp-server/tmp/quickdraw-data/` + `mcp-server/tmp/**/*.jsonl` 两条——后者兜住 qd_smoke/qd_trial/qd_verify 下 4 个散落 .jsonl（同属 CC BY 4.0 素材，入库即构成再分发）。实测 `check-ignore -v` 双向命中正确、`git add -An` 下数据集不可见；mcp-server/tmp/ 剩余可见面 = 12 个实验/工具件（abkit、twoopt_star、capture_resilient.py 等，干跑实测）**仍未忽略**，故**禁止 `git add .`/`git add -A`**，提交必须点名暂存 |
| `results/local-only/2026-08-24/aliyun-47.112.162.80-inventory.txt` | 阿里云盘点 + Recraft 可达性探测证据 |
| 验证状态 | hub 门禁 **PASS（2026-08-24，13/13）**；全量 pytest **976/976**、e2e `PASS`；docs 指纹 216/216、外仓引用 212/212；Quick Draw 映射 387 键→298 类、14 个单字孤儿类目均有多字别名且跨类子串碰撞为 0；最长优先 4 个真实碰撞断言通过；服务异步 vpype 转换最多 2 个并发且取消/超时回归通过。**已于 2026-08-24 部署阿里云生产**（3 文件 sha256 对账、双服务重启、ExecStartPre 过、hit 冒烟 0.714s 命中 cat、公网下载逐字节一致、/mcp 404）**并同步京东云热备**（6 产物+数据集 298 文件+env，仅重启 mcp、bridge 保持 disabled，preflight 25/30 其 5 fail 均为热备态预期分歧；详见 `deployment.md` 顶部两条）。**生产量化同日完成**：lookup 类目覆盖 23/23（14 修复类目全 HIT）；6 并发 hit 出图全 OK（墙钟 2.29s、同秒 6 产物、付费日志 0 条）。**仍未执行**：真实语音 HIL、真实使用命中率统计；根 `tmp/` 暂存副本已纳入 `scripts/agent_gate.py` `_REF_SKIP_DIRS`，原 basename 假绿根因已修 |
| `docs/design/s3-stream-pacing-investigation.md` | 喂流爬行调查 **已结案（§6+§7 两轮）**：第一轮根因=每 ok 一次 LCD 屏刷阻塞灌流循环，修复=屏显 250ms 节流 + 缺纸预检移除 + Grbl 关省电（§6）；第二轮 lichuang 全脸动画下节流不足致半墨复发，根治=屏显走 `Application::Schedule` 出灌流路径（§7，xiaozhi `e623aec`）。§3.1/§4.B/§4.C 已盖 `[superseded → §6]` 标记。用户未提交的结案编辑连同标记与行号修正已随本批入库（如实登记） |

### C 组合（样本精选 + 质量路由，2026-08-25 晚，已提交 `fdf9874`+`41650e4` 并推 origin/main）
| 文件 | 说明 |
|------|------|
| `mcp-server/scripts/quickdraw_curate.py` | 精选脚本：三因子质量分（平滑 0.4 + 构图 0.3 + 笔画数 0.3），每类留头部 `--keep`。**写盘纪律**（2026-08-25 advisory 后加固）：`--out-dir` 写新目录，缺省原地写但自动留 `.bak`；`kept` 为空跳过写盘并 stderr 标红；报告分列 `bad_lines`（整行解析失败）与 `skipped_empty`，避免整文件解析失败被静默清空且报告看不出 |
| `mcp-server/hutuji_mcp/server.py`（已修改，已提交） | `_QD_STYLE_RE` 质量路由门：默认走 AI 位图链保质量，仅描述含 简笔画/简笔/速写/涂鸦/快速/随便/灵魂 才查 QD 本地缓存 |
| `mcp-server/hutuji_mcp/quickdraw.py`（已修改，已提交） | 风格词并入 `_PREFIX_RE` 剥离链（与路由门解耦，**词表改动两处同步**） |
| `mcp-server/tmp/quickdraw-data/` | **已换装为精选版**：298 类 × 50 = 14900 样本（干净重跑：59600→14900，`bad_lines=0`、`skipped_empty=0`）。原始 200 条/类备份在 `mcp-server/tmp/quickdraw-data-uncurated/`（同属 CC BY 4.0 素材，`.gitignore` 的 `mcp-server/tmp/**/*.jsonl` 已兜住） |
| 目视验证 | 精选头部 top-3 渲染人眼过样（cat/tree/fish/pig/house 各 3 张）：完整猫脸、可识别鱼形与猪脸；对照随机抽样为抽象涂鸦。**顺序为先渲染过样、再换装真数据** |
| 双机部署 | 阿里云 + 京东云各 298 个 `.jsonl`、`.bak` 0 个；**全量清单收口**（2026-08-25 advisory 后）：本地 ↔ 两机按 `basename→sha256` map、`LC_ALL=C` 归一化后逐项 diff **零差异（298/298**，覆盖 `-`/`_`/空格混用类目名，取代此前 4 类目抽样结论）；代码 2 文件两机 sha256 亦相同（`27c8cb31…`/`a097617d…`）。旧数据保留为 `/opt/hutuji/quickdraw.old` |
| 生产冒烟 | 阿里云双链验证：`画一只猫`→AI 链 12.81s / 704 行（日志「位图链路分段：生成+下载 9.3s，描摹+预览 2.4s」）；`简笔画一只猫`→QD 0.80s / 60 行（日志「Quick Draw 命中 category=cat entries=50」，`entries=50` 即精选数据生效）|
| 验证状态 | hub 门禁 **PASS**（`56e2717`：pytest **981/981**、e2e `PASS`、指纹 216/216、外仓引用 212/212）；京东云本批后 preflight **25/30**（5 fail 与 08-24 热备基线同款预期分歧：bridge active/自启/重启 4 项 + 公网 /files 真下 1 项因 DNS 指阿里云）；阿里云本批后 preflight **20/30**（10 fail 为 Alibaba Linux/双 nginx 布局已知环境差异，同 `deployment.md`「30 项为京东云口径、阿里云不适用」口径，非本批回归）；fz `json_feed_hold_tcp` 隔离重放 **3/3 PASS**（同 simulator SHA、`-t 1.0`；standard profile 被 MinGW `limits.h` 头遮蔽致 `AxisMask` 未定义的环境层阻断，非该 case 回归）；COM13 只读快照留档（`$1=25` 未改、`$#` G92 Z=5.000、`?` MPos Z=5.000）。**仍未执行**：真实语音 HIL（C 组合部署后）、真实使用命中率统计 |

### 已修改文件（此前任务遗留，非本调研任务）
| 文件 | 变更类型 | 来源任务 | 说明 |
|------|----------|----------|------|
| `docs/daytime-tuning-runbook.md` | 行号更新 | daytime-tuning | 代码引用行号随提交前移 |
| `docs/design/p2-windowed-flow-control.md` | 行号+逻辑更新 | P2 窗口流控 | 窗口流控设计文档与实现同步 |
| `docs/kxnx/findings/17-reconnect-resend-hazard.md` | 行号更新 | kxnx 调查 | 重连重发 hazard 代码锚点更新 |
| `docs/kxnx/findings/21-transport-watchdog-and-sd-executor.md` | 行号更新 | kxnx 调查 | transport watchdog 代码锚点更新 |
| `docs/protocol.md` | 行号更新 | abort_reset 修复 | abort_reset_owner 行号 +8；~~未提交~~ **已随 2026-08-24 `766cfd1` 入库**（该行引用同批经外仓指纹层校验 212/212） |
| `docs/release-readiness.md` | 日期+摘要更新 | UX 审查收口 | 全链 UX 审查 2026-08-24 结论追加 |
| `docs/troubleshooting.md` | 行号更新 | 多轮调试 | hutuji_job.cc 多处代码行号随提交前移 |

### 证据基线与批次登记（HEAD 存档并入）

- **路径 / 分支**：`D:/Users/hutuji` / `main`
- **当前提交链**：实时用 `git log --oneline origin/main..HEAD` 与 `git rev-parse --short HEAD` 查询；不以本页静态快照代替。
- **生产运行态（2026-08-30 20:26 bridge ENOSYS 修复后）**：**阿里云 `47.112.162.80` 为主**——`hutuji-mcp`+`hutuji-cloud-bridge` 双 active（bridge **cloud/proxy 双 connected**，MainPID 871455/871459 为修复后当场值；不以静态 PID 作长期身份）；代码基线 `2710739` + kb-admin/console 热修 + **bridge unit ENOSYS 热修**（模板本批入库防再丢）。队列/worker/PG shadow 仍仅阿里云（19:04 启用）。**京东云 `117.72.118.95` 热备**：bridge disabled+inactive；回滚 = DNS 指回 CF Tunnel + 启京东云 bridge + 停阿里云 bridge。真实语音 HIL 未执行。
- **本地保留资产**：`results/local-only/2026-08-18/`、`2026-08-19/`、`2026-08-20/` 与 `2026-08-21/` 均保留且被 gitignore；08-21 新增唱歌部署候选与冒烟证据、刷机事故串口原始日志（`2026-08-21-sing-bootloop-raw.log`）、恢复启动日志（`2026-08-21-waveshare-restore-boot.log`）与触摸第三轮开机取证（`2026-08-21-touch-round3-boot.log`，含双门回读 `70 -> 20 peak 60 -> 40`）。**2026-08-22 新增 `results/local-only/2026-08-22/hil-draw-com14.log`**：设备消息 API 触发的五角星全链 HIL 串口证据（生成→预览→确认→下载 CRC32 `586d72ae`→流控出图→归位→M30→error:90 缺纸）。根目录杂散 `COM14` 文件已移入 `results/local-only/2026-08-20/`；`tmp/` 未跟踪保留。不新增忽略规则，避免同类误生成被静默掩盖。
- **2026-08-23 阿里云部署本机资产（gitignored，不删）**：`results/.tmp/deploy-aliyun/` = 灰度节点部署候选——14 模块 + `hutuji_gwrite.toml` + `requirements.txt` 由 `git cat-file blob HEAD` 导出（远端 SHA-256 已逐文件核对相符、CR=0）、`nginx-hutuji-gray{,-bootstrap}.conf`（阿里云直连入口模板，`$binary_remote_addr` 限速、`/mcp` 404，已生效于 `/etc/nginx/conf.d/hutuji-gray.conf`）、`hutuji-{mcp,cloud-bridge}-el8.service`（systemd 239 兼容 unit，已生效于 `/etc/systemd/system/`）；`results/.tmp/songs/` 是京东云歌曲目录副本（已同步到阿里云 output/songs，属主 hutuji）。**会话期 askpass 密码文件已删除**（用完即焚，凭据不出 `D:/Downloads/VPS.txt`）。**独立缺陷登记（不在本轮修）**：复活/禁语扫描面是 `GOVERNANCE_DOCS` 18 份，`results/` 整目录 gitignored 不在扫描面——治理文本若落进 results 下文件门禁看不见；本轮合规来自用户显式决策反转与源头规则下线，不依赖该盲区。
- **工作树（2026-08-22 现取）**：HEAD 以 `git rev-parse --short HEAD` 为准。本轮文档（发现收紧契约/行号、厂商参数快照、售后 KB 厂家信息）随文档提交入库；§9-E′ 契约+白名单+行号基线批次与 bridge-health 加固批次随后入库（见提交链）。原「桥接空闲死链探活」WIP（过期 id 本地消费、禁止覆盖 pending、回包刷新 `_last_traffic`、顶层 JSON-RPC error 立即探活、stdin 锁）**已于 2026-08-23 从 `stash@{0}` pop 并入库 `998e9f2`**：同批并入 review 中等项修复——`_run_proxy_session` spawn 包进 try/finally + shield 等 spawn 落定拿句柄，取消打在 `create_subprocess_exec` 中途不再孤儿 mcp-proxy（新增用例 RED→GREEN 实证：旧实现 `processes` 为空判红）；test_cloud_bridge 106→116、全量 908→918 绿，hub 门禁 PASS。stash 已清空。用户未跟踪资产 `memory_user.json` 与 `tmp/` 不纳入候选。**不要把静态 SHA 当作实时生产/板上身份**。
- **未推判断**：不用静态文字；执行 `git log --oneline origin/main..HEAD`。
- **已入库 `edf68e7`（2026-08-23 稳定性批次 R4，已推 origin）**：`bitmap_svg.py` 生图对冲/下载/描摹分段计时日志 + `server.py` G-code 段计时（只记耗时数字，不落描述/URL）；新增 `scripts/cloud_health_pull.py` 云端取证脚本（ssh BatchMode 拉生产 journalctl 两 unit → 能力名 token 脱敏 → 11 类关键事件计数，退出码 0/1/2，落盘 `results/local-only/<日期>/cloud-health-*`）+ `test_field_tools.py` 4 项新测试。独立 reviewer 审查后修复：脱敏正则尾部 `\b` 在 token 末位为 `-` 时整段漏码（改负前瞻 `(?![A-Za-z0-9_-])` + 三种尾随形态回归测试）、远端 shell 引号 `list2cmdline`→`shlex.quote`。reviewer 曾建议把 `memory_user.json`/`tmp/` 加进 .gitignore，**与本页既有决策（不新增忽略规则，避免同类误生成被静默掩盖）冲突，已回退**。pytest 924/924；`server.py` G-code 计时已随 `c2cdbd4` 部署生产（见下条日志脱敏批次），`bitmap_svg.py` 计时与 `cloud_health_pull.py` 仍为本地取证面未部署。
- **已入库 `5d91ba3`（2026-08-23，已推 origin）**：`scripts/agent_gate.py --fix-line-refs` 只自动处理指纹原文唯一命中的纯行号移动，内容变化/多命中/旧基线无原文等均退 1 交人工；同步 `docs/agent-gate.md`、Cursor 硬轨、带 `source` 的指纹基线与 5 项回归。AGENTS 同批固化 USJ + App 运行中 EN 硬复位根因、`--after=watchdog-reset` 与 WDT 抓包路径；全量 pytest 929/929，hub PASS。`tmp/` 脚本仍按既有决策不入库。
- **已入库 `84aaca0`（2026-08-24 P1-3 hub 批，已推 origin）**：`HUTUJI_XIAOZHI_DEVICE_TOKEN` 配置项；draw 唯一完成点经官方 `/messaging/device/tools/call` 链式调 `hutuji.draw`（双参数 fire-and-forget，失败回落两步），消灭 #1011 漏调「纸不动」；pytest **936/936**（+4 链式契约）+ hub 门禁 PASS（e2e PASS、指纹 178/178）。KB 缺纸问答+画得淡试笔、role-prompt 自动衔接幂等口径同批。**未部署生产；启用前提 = 有效设备消息 token 写入生产 mcp.env（当前无）**；云端 `sync 2094251` 待固件刷机 + token 配置后执行。
- **已部署日志脱敏批次（2026-08-23，提交 `c2cdbd4`）**：新增 `hutuji_mcp/redact.py` 作为 MCP/bridge/取证脚本唯一共享实现；`server.py` 在 lifespan 首动作给 root、uvicorn、httpx/httpcore handler 安装 filter，`cloud_bridge.py` 只迁移 import，JSON-RPC 标签/请求 ID/watchdog 未改；`cloud_health_pull.py` 落盘前复用完整 URL/凭据脱敏。三文件按 Git blob 导出、CR=0、远端哈希逐一相符，双服务重启后 active，`/bridge-health` 200，preflight `30/30`；生产 POST/GET 与 access 探针均实测脱敏，取证文件 `results/local-only/2026-08-23/post-redact-deploy/cloud-health-20260823-212814.log` 已复核无原始 URL/签名值。生产泄露 journal 已 rotate/vacuum，本地取证副本已涂除；删除日志不等于撤销 24h 预签名 URL。本条 supersede R4 条目的笼统“计时日志未部署”：`server.py` G-code 计时已上线，`bitmap_svg.py` 计时仍未上线。用户未跟踪资产 `memory_user.json` 与 `tmp/` 不纳入候选。

---

## 2. Grbl_Esp32（`D:\Users\Grbl_Esp32`）

**工作树干净（2026-08-30 §9-G′ `$` 写保护已提交 `40616de` 已推 origin，[PR #1](https://github.com/zhuguang-ZFG/Grbl_Esp32/pull/1)）** — 见下条。

### §9-G′ Golden `$` 写保护（2026-08-30，**已提交 `40616de` / 已推 origin**）

| 文件 | 说明 |
|------|------|
| `Grbl_Esp32/src/ProtocolDecisionCore.h` | `is_hutuji_locked_setting_key()` |
| `Grbl_Esp32/src/ProcessSettings.cpp` | Telnet/WebUI 写入 golden 项 → `Error::SettingDisabled`(5) |

- **板上（2026-08-30 COM13 刷入）**：`firmware.bin` SHA-256 `7069D20E95DDF58DA8B4C15650FC115D636B8CD2783D2B5DE20956E76405C4E1`；`pio run -e release` + upload `Hash of data verified`。
- **实机**：`$130=200` → `error:5`；`$130` 只读 → `$130=210.000`（脚本 `tmp/test_grbl_write_lock_com13.py`）。
- **fz 门禁**：**未验证**。

**工作树干净（2026-08-28 晚 M30/换纸 `$1=255` 守卫已提交）** — 历史见下条。

### 板上基线与验证边界（HEAD 存档并入）

- **路径 / 分支 / HEAD**：`D:/Users/Grbl_Esp32` / `fix/panel-hold-after-align-116687e` / **`8047d29`**（源码，工作树干净，§9-G′ 已随 `40616de` 提交）；现役 COM13 板上镜像 SHA **`7069D20E…05C4E1`**（2026-08-30 刷，取代 `12275654…429A5`）。
- **有证据的板上行为基线**：`e3b1cad`（2026-08-15 烧录与启动自证）；源码后续 `847b564` 增加 §9-D′ 蓝牙功率，**没有本轮烧录证据，不能把源码 HEAD 写成板上基线**。
- **2026-08-22 换新写字机（COM13，CH340）**：烧前已完整取证并备份——原厂整片镜像 `results/local-only/2026-08-22/new-plotter-factory-backup.bin`（4MB，esptool read_flash）、串口证据 `new-plotter-serial-evidence.log`（烧前 `$I`/`$$`/`$102`/`$130`/`$131`：厂商默认 `$130/$131=200/200`；烧后 `$$` 快照为 fork 机型默认 `$130=210/$131=297`，`$20=0` 软限关闭，云端 190/190 限幅不受影响；X 长边/短边仍待装纸后单轴点动实测量尺）。烧入 app-only `0x10000`（`e3b1cad` 经 worktree 重建 `release`，PlatformIO SUCCESS，esptool `Hash of data verified`），未整片擦写，授权 NVS 保留（启动日志 `[License] Authorized (restored from NVS)`，ChipID `00002C876AE9BFB4`）。注意：本机 scoop mingw 16.1 对 fz 门禁不可用——rt_v14 的 `pthread.h`→`limits.h` 头链把 Grbl `src/limits.h` 经 `include_next` 卷入系统链（`-iquote` 下同样触发，实测复现），且该 winlibs 构建不附 libasan/libubsan（mingw-gcc 无 ASan 属社区已知，msys2/MINGW-packages#3163）；**解法：PATH 前置 scoop llvm `22.1.8/bin`（fz `find_compiler` 原生优先 clang++），fz `standard` 实测 overall=pass**（`wokwi_startup` 可选云层红，用 `.pio` 旧镜像、不含 E′，按仓规不阻断）。故新机用已验证基线 `e3b1cad` 而非当时的 HEAD；E′（`fb2b3b5`）已过 fz standard host SIL，**未烧录、未实机复现物理并发**，板上基线不变。配网走 AP 模式（`GRBL_ESP`，WebUI/命令行），静态 IP 待配网完成后按实读网段写 `.13`。
- **2026-08-23 烧录 `fb2b3b5`（COM13，用户批准后执行）**：`pio run -e release` 重建 SUCCESS（仅重编 PaperSystem.cpp/WebSettings.cpp，与 E′ 触碰面一致；新 bin 1857936 字节，取代 08-15 旧镜像）→ `pio run -t upload --upload-port COM13` app-only `0x10000`，esptool `Hash of data verified`，RTS 硬复位。启动自证 `results/local-only/2026-08-23/fb2b3b5-com13-boot.log`（抓取脚本 `tmp/cap_fb2b3b5_com13.py`）：`Grbl_ESP32 Ver 1.3a`、paper system vM716 ready、`[License] Authorized (restored from NVS)`、连上 `ChinaNet-jJmz` 获 `192.168.1.13`、HTTP/TELNET:23 起来，无崩溃重启。**矛盾待澄清**：本次启动自报 ChipID（efuse MAC）`0000FC7742C63C94`，与 §2 上文 08-22 记录的 `00002C876AE9BFB4` 不一致（本地证据文件中检索不到后者出处），COM 号漂移或多台写字机皆有可能——物理身份需用户确认；可确认的是「今天 COM13 这块板」已跑 `fb2b3b5` 且授权/.13 网络身份正常。物理并发复现（E′ 的 HIL）仍未做。**wokwi 同日登记**：复跑 `run_wokwi_smoke.py`（新镜像已就位）失败根因为 Wokwi 免费版月度 CI 配额耗尽（`API Error: ... Free plan monthly CI minute quota`，serial 0 行），属外部阻塞非固件回归；该可选层本月不可跑，不阻断产品门禁。
- **验证边界**：核心 §9、归位、自动换纸有历史证据；后续源码变化须另跑 fz、构建、烧录与启动身份核验。
- **观察项（2026-08-23，无抓包证据）**：写字机经 `[ESP110]AP`+`[ESP444]RESTART` 切入出厂 AP 后稳定在线约 1 小时（jump1–jump4 窗口串口自证），随后在两次抓包间隙自行回到 STA `192.168.1.13`（jump5 时 S3 缓存 IP 命中并授权通过）。AP 模式是否存在自发回退未定性——同期窗口有人肉拔插 S3 USB 的物理操作，不排除误碰写字机供电；后续若再现需在抓包覆盖下复测。
- **2026-08-31 第二台写字机（新机，COM13，CH340，用户判定已授权免 M800）**：烧前备份 `results/local-only/2026-08-31/new-machine-factory-backup-com13.bin`（4MB read_flash）+ 烧前 `$I`/`$$`（`new-machine-preflash-com13.txt`：原厂 `$1=25`、`$130=$131=$132=200`、`$10=1`）。`pio run -e release`（1858432B，SHA-256 `270d8c84d27e44c6…311b60`，HEAD `8047d29`）+ `pio run -t upload --upload-port COM13` → `Hash of data verified`。`$RST=$` 灌 golden 后 §6.1 指纹全对（`$1=255/$3=7/$20=$21=$22=0/$100=$101=100/$110=$111=12000/$130=210/$131=297/$132=200`，`$Errors/Verbose=Off`，`new-machine-rst-golden-com13.txt`）。授权实证：零位移 `G1G90 X0Y0F1000` → `ok`（非 error:110）；`[ESP901]` → `Changing=Off`（`new-machine-stage012-com13.txt`）。WiFi：热点「vivo X300 PRO」STA 连接失败回落 AP `GRBL_ESP`/192.168.0.1（疑 5GHz-only，ESP32 仅 2.4GHz，未深查），恰为 §1.5 零接触所需出厂态，待小派巡检/工作台「重新配置写字机」代配；串口配网脚本 reset-on-open 竞态同批修复（闭构造 + dtr/rts 恒 False + 清缓冲，本机实证 ESP100–110 全 ok）。逐台记录 `results/local-only/2026-08-31/device-test-record-new-machine.md`；辅助脚本 `tmp/new_machine_rst_golden.py`。
  **结局补记（同日傍晚）**：pxcj2.4 代配曾成功一次（写字机获 `192.168.0.115`，PC 同网 Telnet 实证 banner/`$I`/授权探针全通），但小派子网扫描两轮未命中 .115（扫描单轮 ~55–68s，期间小派被人为多次重启打断，漏检根因未定性）；后按用户决策两台统一收敛到 vivo X300 Pro 热点——写字机经 `grbl_wifi_setup.py` 改配得 `10.181.196.202`，小派自重配同网后**管道接通实证**：COM14 日志 `<- <Idle|MPos:0.000,0.000,0.000|Bf:249,1200|FS:0,0|Pn:0>`、`-> [ESP901]` → `<- Paper=No MotorEn=On PanelHold=Off Changing=Off` + `ok`，PC 侧 10.181.196.202:23 accept 即关旁证槽位被管道独占。vivo 热点 PC↔写字机 TCP 有「banner 后 10053 中止」怪癖（ESP↔ESP 不受影响），未深查。抓包落盘 `results/local-only/2026-08-31/{s3-pipe-watch-com14.log,dual-watch-com13.log,dual2-com13.log,dual2-com14.log}`；逐台记录同目录 `device-test-record-new-machine.md` 已补记。

---

## 3. xiaozhi-esp32（`D:\Users\xiaozhi-esp32`）

### 小派量产远程 OTA（2026-09-09，**已本地提交；推送/刷机/HIL 另批**）

| 文件 / 意图 | 说明 |
|------|------|
| `main/boards/lichuang-dev/hutuji_ota.{h,cc}` | `hutuji.ota_check` / `ota_start`、每天自检（NVS 日戳）、HTTPS host 闸、调度 `UpgradeFirmware` |
| `main/boards/lichuang-dev/hutuji_job.{h,cc}` | `StatusJson`：`firmware_version` / `board` / `ota{state,progress,update_available[,reason]}`；`SetOtaStatus` |
| `main/ota.cc` | `ParseVersion` 跳过非数字段（支持 `hutuji.x.y.z`） |
| `CMakeLists.txt` / `main/CMakeLists.txt` | `PROJECT_VER=hutuji.1.0.0`；链入 `hutuji_ota.cc` |
| 三板 tools 挂点 | `lichuang_dev_board.cc`、`freenove-…2.8-lcd.cc`、`waveshare/…3.5.cc` + `application.cc` 日检钩 |
| 提交 | `73257fa` → `9194e60` → `fcaca02` → `942fcfb`（现取 HEAD 可能含后续无关提交；OTA 范围以上列为准） |
| 脏树 | **`hutuji_draw_bind.cc` 未提交改动须保留**；勿与 OTA 提交混回滚 |
| 板上 / HIL | **未刷含本批 OTA 的量产镜像；HIL 1–6 未验证** |

### Grbl `$` 设置指纹 + 对话上报（2026-08-30，**已提交 `0d041c9` / 已推 origin，[PR #1](https://github.com/zhuguang-ZFG/xiaozhi-esp32/pull/1)**）

| 文件 | 说明 |
|------|------|
| `main/boards/lichuang-dev/hutuji_recovery_core.h` | golden 表 + `ParseGrblSettingLine`（含 `Off/On`） |
| `main/boards/lichuang-dev/hutuji_pipe.{h,cc}` | 授权后 13 项只读指纹；`grbl_settings_ok` / mismatch |
| `main/boards/lichuang-dev/hutuji_job.cc` | confirm/出图/试笔/点动拒画；status JSON 字段 |
| `main/boards/lichuang-dev/hutuji_conversation_report.{h,cc}` | 对话上报（Idle 冲刷） |
| `main/application.cc` | 唤醒词 speaking 期关自打断 |
| `scripts/tests/test_hutuji_recovery_core.py` | +指纹 host 单测 |

- **板上 COM14（2026-08-30）**：`build-hil-ws35/xiaozhi.bin` SHA-256 `1555415246ADF3DF4CBD42ADDE53B53DF9C82627745BD78219972CBA56C1BA62`；抓包 `tmp/settings-fp-fixed-com14.log`：`Grbl 设置指纹通过（13 项）`。
- **首刷 bug**：`Errors/Verbose=Off` 未解析 → 已修并重刷（同 SHA 系增量构建）。

### DRAW-BIND 绑机固件（2026-08-29，**已随 `0d041c9` 入库并推 origin**）

| 文件 | 说明 |
|------|------|
| `main/boards/lichuang-dev/hutuji_draw_bind.{h,cc}` | 绑机 QR/announce；单任务 `hutuji_bind` 栈 8192（修 COM14 `hutuji_bind_pol` 栈溢出） |
| `main/display/lcd_display.{h,cc}` | 维护页「绑定呼图账号」可见性；预览 UI |
| `main/boards/waveshare/esp32-s3-touch-lcd-3.5/esp32-s3-touch-lcd-3.5.cc` | 挂 `StartDrawBind` |
| `main/boards/lichuang-dev/hutuji_job.cc` | （若本批有改动则登记） |
| `main/ota.cc` | OTA 解析 `messaging` 段写 NVS（待刷机验证） |
| 证据 | `tmp/bind-reboot-com14.log`（旧镜像栈溢出）；绑机栈修复后 announce 200 无重启 |

**板上**：绑机+预览+屏上确认出图已 HIL；messaging token 自动写入仍靠运维。**勿与 hub draw-portal 代码提交混为一谈**——固件在外仓单独 commit。

**工作树（2026-08-29）**：DRAW-BIND 绑机固件脏树见上表；历史语音/触摸/稳定性批次见下「批次登记」小节。

### 批次登记与板上镜像（HEAD 存档并入）

- **已入库 `67507c6`（2026-08-28 预取 PSRAM 泄漏修复，已推 origin/feat/hutuji-usb-pipe，未刷机）**：`AdoptPrefetch` 在 `prefetch_mutex_` 锁内、Ready+URL 核验后先 `heap_caps_free` 旧 `buffer_` 再接管——上一张成功留存（`buffer_replayable_`）+ 命中预取的重复新画每次漏 ~512KiB（08-24 审计登记项，§5 xiaozhi 行原「修复待独立批次」就此关闭）；host 138/138 含新增 `test_prefetch_adopt_frees_old_psram_buffer` 钉死释放顺序。刷机随下一联调日（runbook 附 C）。
- **已入库 `5876c13`（2026-08-28 唤醒/Grbl 主屏可视性批，已推 origin/feat/hutuji-usb-pipe，已刷 COM14）**：用户主诉「和小派说话时不知道有没有被唤醒、写字机在不在线」。四文件（`grobot_eyes.cc`/`lcd_display.{h,cc}`/`test_hutuji_recovery_core.py`）四路指示——①Grobot 脸聆听 3s/周 0.90 连续扫光（说话 1.5s、空闲 6s/34% 之间，配合瞳孔放大 18%）；②`SetStatus` 状态胶囊聆听=绿/连接中=柔和色，聆听判定收敛局部布尔（`status_listening_` 缓存）；③`AccentDriftTimerCb` 聆听时说话大圆钮+呼吸光晕整钮切绿（其余 3 钮品牌漂移不变）；④顶栏右图标组最左 Grbl 三级圆点（绿=在线就绪已授权/琥珀=连上未就绪未授权/灰=离线；`EnsureGrblStatusDot` + 500ms timer 读 `Pipe` 原子量，与手动页 HUD 同源；**WeChat/普通双 SetupUI 分支均挂**——Waveshare 编 WeChat 分支、lichuang 编普通分支，漏挂一即某板主屏永久无指示）。host **137/137**（两处旧源码断言改钉新合同 + 新增 `test_grbl_status_dot_on_topbar_three_levels`）；ESP-IDF v6.0.1 `build-waveshare-35/xiaozhi.bin` 3,020,608B SHA-256 `b33e1c3f…0f49`；构建脚本 `tmp/build_ws35_wake_indicators.ps1`（本机不入库；PATH 无 python 是 export.ps1 前置失败的根因，脚本内显式前置 idf6.0_py3.13_env）；clang-format 本机无安装未跑。**刷机**：`tmp/flash_wake_indicators_ws35.ps1`（复制 flash_hil_ws35 改镜像路径/日志名）写 1,793,961B `Hash of data verified` + WDT 复位、COM14 免拔插；复位后 25s 被动抓包 304 行（`results/local-only/2026-08-28/wake-indicators-postflash-com14.log`）无 panic/assert、HutujiPipe fresh `Idle` 且随后正常灌流作画。**屏幕指示观感 HIL 待用户目视**（唤醒绿钮/绿胶囊/聆听扫光辨识度、圆点三级变色）。提交后工作树干净（tmp 脚本/构建目录忽略）。
- **已入库 `c3cf3d7`（2026-08-28 顶栏/状态胶囊 z-order 修复，已推 origin、已刷 COM14，HIL 已关闭）**：`5876c13` 刷机后用户实锤「主屏看不到圆点」——WeChat SetupUI 分支栏先建、`InitializeEmotionUi` 后建 460x300 整屏脸，顶栏/状态胶囊（连网络/电池图标）全被脸压在下；该函数旧注释自称「状态栏独立叠在最前层」从未兑现（普通分支栏在脸后创建所以 lichuang 没事）。修复：脸定尺寸后两栏 `move_foreground` 抬到脸上，顶栏先 `lv_obj_set_parent` 到 screen（z-order 只在兄弟间生效）再抬并补 TOP_MID 对齐。host 137/137（dot 测试 +3 z-order 钉）；镜像 3,020,672B SHA-256 `31e3f3e5…2376` 写入校验 + WDT 复位；抓包 `results/local-only/2026-08-28/wake-indicators-zorder-postflash-com14.log` 无 panic、pipe fresh `Idle`、唤醒状态迁移正常。**用户照片确认（11:51）**：WiFi/电池图标、状态胶囊、Grbl 绿点均上屏；聆听态变色/扫光未留照片证据，用户 2026-08-28 目视验收后指示关闭。
- **已入库 `a1afd2a`（2026-08-24 P1 固件批，已推 origin/feat/hutuji-usb-pipe，经 `git merge-base --is-ancestor a1afd2a origin/feat/hutuji-usb-pipe` 核实）**：pipe 解析 `[ESP901]` 三字段（Paper/MotorEn/PanelHold，与 Changing 同守卫、Unknown fail-open）；`StatusJson` 只读暴露遥测（禁发命令防孤儿 ok 在途漂移，host 源码断言）；遥测刷新走 `Preview()`/出图前预检消费式；**出图前预检**（[ESP901] 消费式遥测 + Changing=On 守卫；Paper 字段仅上云、fail-open，S3 侧无缺纸出图闸——「Paper=No 早退」旧表述 2026-08-25 源码复核撤正）；换纸 90s 补 30s 周期播报；**预览同参数重入幂等**（P1-3 配套，回 `previewing` 非「正忙」）。**幂等谓词 fixup（未提交，工作树 hutuji_job.cc + test）**：判据从 `awaiting_confirmation_.load()`（要等 PNG 下载完才 true，主路径不成立）改为「已在预览流程中」（`state_ == "previewing" || "awaiting_confirmation"`，取快照于 `state_mutex_` 下），host 源码断言钉死。host **135/135**（+4 契约）、ESP-IDF v6.0.1 双板构建通过（fixup 后重建 `ALL DONE`）、clang-format 干净；**未刷机**（板上仍 `93b61d5b…afa9`）。
- **已入库 `12b6751`（2026-08-26 触摸第四轮：拖动跟手性，已推 origin/feat/hutuji-usb-pipe，未刷机）**：`main/display/lcd_display.cc` 单文件双修复——①`SpiLcdDisplay` taskLVGL `priority` 1→4（上游 2025-03 遗留低于组件默认；audio_output(4)/opus_codec(2) 不绑核落核 1 反复插队 PRESSING 事件与重绘）；②`AccentDriftTimerCb` 指针按下期间整帧跳过（`lv_indev` 迭代判定；每 100ms 色相呼吸+阴影脉动常载渲染管线，拖动重绘排动画帧后）。用户体感「不怎么跟随手指」+ 08-21 诊断镜像芯片层排除结论为取证链。host **135/135**、clang-format dry-run 过；ESP-IDF v6.0.1 `build-touch4-ws35/xiaozhi.bin` 3,018,016B SHA-256 `c5bada19d46a2289ddc19e31df4d581e7717fc4cd60467280173be6ff94a76db`；构建脚本 `tmp/build_touch4_ws35.ps1`（复制 build_hil_ws35 改 build 目录名，仓库根 sdkconfig 会被重写为 waveshare 发布态）。**拖动跟手性/灵敏度体感 HIL 待实机**。`0cf01ce` 追加 `lv_obj_set_ext_click_area`（talk 12/角钮 8）；`a7e0495` 角钮 48→56px 合规恢复（PRODUCT.md:55/DESIGN.md:169 三处成文约束，钉死测试同步），最终重构建 SHA-256 `ce5a2d5fabe607c07b88b64f03dda4a2b3712b5cf22089585b82b6c336272b74`（3,018,064B）。`135cecd` 开机 ESP_LOGI 三钮实际几何（NVS 恢复后，命中归因依赖）+ 守卫测试收窄到真实意图；`0ea1cbd` 日志前补 `lv_obj_update_layout(screen)`（LV_SIZE_CONTENT 首帧前未决，同文件 2271 行先例），最终镜像 SHA-256 `e17ac8f878240c9301ac6f53005972d4b70826d91e8ea5b1da5b067efd175bf2`（3,018,464B）。**刷机事故记录**：首次误用 flash_hil_ws35.ps1（第 31 行写死 build-hil-ws35 路径）刷入旧镜像，advisory 抓获后建 `tmp/flash_touch4_ws35.ps1`（路径+日志名已改）；重刷时 COM14 进入 USJ 半死态（PnP Unknown、串口枚举消失），pnputil 无管理员权限拒绝，软件救不回——**用户拔插后看门狗自动重刷成功**（`Hash of data verified` = esptool 芯片侧摘要比对，镜像上板已证；WDT 抓包从 1.37s 加入，Touch registers/geom 两锚点在此前显示/触摸初始化窗口故未捕获——不再开 esptool 轮次补抓，读回验证被否为冗余）。HIL 被动抓包 `tmp/capture_passive_com14.py`（dtr/rts 清零不复位，30 分钟窗）已挂，geom 行折进 HIL 自身复位顺带抓。深度 review 落 `docs/kxnx/findings/29-touch-hit-geometry-review.md`。
- **工作树（2026-08-23 全链审计现取）**：HEAD `72a693a`（`feat/hutuji-usb-pipe`，与 origin 同步、工作树干净；本条为审计复核后的现态指针，下条批次明细与 `b0f29a8`/`4ce8b59` 的零接触配网实现记录保留不删）。含 R22-PIPE-02、点动 1/10mm、`$MD`、HUD/断连告警、发现收紧，以及**设备端零接触配网（协议 §1.5）**：`b0f29a8` 主体实现（新增 `plotter_provision_core.h`、`plotter_provision.{h,cc}`、`test_plotter_provision_core.py`；改 `main/CMakeLists.txt`、`hutuji_pipe.cc`、`lichuang_dev_board.cc`、`esp32-s3-touch-lcd-3.5.cc`、`main/display/lcd_display.{h,cc}`、双语 locale、`test_hutuji_recovery_core.py`），`4ce8b59` HIL 三修复（cfg.ssid 空指针写崩溃 → 指针赋值；全量扫描结果集被 esp-wifi-connect WifiStation 截胡清空 → SSID 过滤扫描防截胡；过滤扫描结果集漏收 → promiscuous 嗅探 beacon/probe response 兜底，IE 解析为 core 纯函数）。**实机 HIL 已通过**（2026-08-23 jump6 跳配闭环双侧日志 + jump5 不打扰路径二次确认，证据 `results/local-only/2026-08-23/hil-jump{5,6}-*.log`；细节与三轮失败现场见 protocol.md §1.5 状态行）。HIL  tooling 在 hub 仓 `tmp/`（`build_hil_ws35.ps1`/`flash_hil_ws35.ps1`（2026-08-23 晚起 `--after=watchdog-reset`：App 运行中 EN 硬复位间歇杀死 USJ 重枚举，RTC WDT 复位不掉 USB；复位后 esptool 关句柄撞重枚举窗口的装饰性 exit=1 由日志分类判成功）/`reset_wdt_capture.py`（免硬复位 boot 抓包，代价错过前 ~1-2s）/`reset_capture_full.py`（**已废弃**：RTS/DTR 脉冲即肇事路径）/`capture_com13.py`/`capture_com14.py`/`hil_probe.py`/`hil_phaseb_plotter.py`）。注意：ESP-IDF v6 `set-target` 会重写仓库根 `sdkconfig`（gitignored），最终根 sdkconfig 由 ws35 构建落回唤醒词状态。**审计新增登记**：源码审查实测 `hutuji_job.cc` 预取接管（`AdoptPrefetch`）在上一张成功留 buffer 且本张预取命中时不释放旧 PSRAM（无所有权交换），且预览下载成功但预取失败/中断时无 `CancelPrefetch` 补偿——重复「新画一幅」累积泄漏 ≤512KB/幅，修复待用户批准独立批次并补 host 测试；`scripts/xiaozhi_console.py` 白名单缺 `preview_url`，CLI 直调 `hutuji.draw` 现会被设备拒。
- **已入库 `72a693a`（2026-08-23 稳定性批次，已推 origin、已刷 COM14 三轮画图 HIL 通过）**：在 `4ce8b59` 之上——双板 `SetPowerSaveLevel` 门控（lichuang-dev 新增 override、waveshare 门控扩为拒绝一切非 PERFORMANCE 回落）+ 双板稳态 LOW_POWER→BALANCED 映射；`hutuji_recovery_core.h` 新增 `kDiscoverFastRetryAttempts`/`NextDiscoverBackoffMs` 纯函数（前 5 次 1s 后翻倍封顶），`hutuji_pipe.cc` 加 `miss_attempts` 计数；`hutuji_job.cc/.h` 两处修复——`WaitForAudioOutputIdle` 等待信号从 `codec output_enabled`（双工 codec 监听期永不关输出，恒吃 30s 上限）改为 speaking 态；G-code 预取从独立 xTaskCreate 任务（内存低谷静默失败，W 日志实锤）改为预览任务尾部内联（删 `PrefetchTaskEntry`）。host 131/131；三轮画图 HIL 证据 `results/local-only/2026-08-23/hil-stable-draw{,2,3}-com14.log`（0 次 LOW_POWER、ok 均值 73/72ms、确认后 10ms 复用预取进 streaming），启动核对 `hil-stable-boot{3,4,5}.log`（稳态 ps type 1），扫描兜底观察 `hil-rescan-com14.log`。**观察项进展（2026-08-23 晚）**：R3 退避已故障注入验证——写字机断电后断开秒级检测（errno 113），重试阶梯 1s×6→2/4/8/16s→30s 封顶（与 `kDiscoverFastRetryAttempts=5` 一致：第 6 次打印仍 1s 是打印先于递增），回电后自动重连 + 版本探活 + 授权探测通过，全程 WiFi 零省电档横跳（证据 `results/local-only/2026-08-23/hil-r3-backoff-com14.log`）；COM14 掉枚举已定性为 App 运行中 EN 硬复位间歇杀死 USJ 重枚举（PnP 实锤 VID_303A&PID_1001；esptool#970 / esp-idf#13287 / arduino-esp32#6762；当日 7/7 次 RTC_SW_CPU_RST 软件复位全程在线对照；`reset_capture_full.py` RTS 脉冲复位 1s 进 boot 即 PORT-LOST 实录于 `hil-stable-boot.log`）；另实测两次刷机**写入中途** `Cannot configure port`，根因未定性（板无电池排除电池掉压，拔插后同参数成功）；boot4 写字机缓存 IP 验证失败一次、子网扫描兜底接管成功（写字机当时是否短暂离网未定性）。同日另实测 S3 重启后写字机侧旧 TCP 僵尸槽位占住 :23（`MAX_TLNT_CLIENTS=1`），约 10s 自动释放并由新连接接管，属预期行为。
- **历史批次（工作树此前干净时登记）**：`7af5cbb`/`5109c9b` 打断进监听与 MQTT 会话清理（goodbye 复核、空通道无副作用、迟到 hello 丢弃）。`70f1c58` 批次 4 文件（板 cc、lcd_display.cc、settings.cc、host 测试）：FT6336 TH_GROUP 70→30（FT5x06 官方寄存器文档 + Linux EDT 20–80 区间）、indev 读定时器 33ms→10ms（`LV_DEF_REFR_PERIOD=33` 实测 + LVGL issue #8152）；复审 P1-1 断连看门狗门控早退后原地续表 + 已恢复连接不进配网、P2-1 `Settings::SetInt` 句柄守卫、P2-2 talk/wifi 点按回弹、P2-3 贴边存档回默认。host 99/99。`37be0e9` 批次 6 文件（lcd_display.{h,cc}、板 cc、双语 locale、host 测试）：「说话」大圆钮 + 「控制」「配网」角落钮、三钮按下跟随拖动 + NVS `hutuji_ui` 布局记忆、二维码「关闭」（Schedule+StopConfigAp 事件链）、断连 120s 看门狗；含实机事故修复（拖动回调 user_data 误传 `this` → LoadProhibited 白屏重启，两轮同 Backtrace `0x42026ca6` 取证，改传 state + 防回退测试钉死）。原「分页抽屉 + taskLVGL 栈 12288 + 触摸/点动调参 + WiFi 省电持有 + 测试 + DESIGN」8 文件批次已提交 `2bc04a6`。Y 限幅收紧批次已提交 `75e83d7`（`hutuji_recovery_core.h` + host 测试 + 文档）。
- **板上镜像**：COM14 当时为 `72a693a` 内容的 HIL 构建（`build-hil-ws35/xiaozhi.bin` SHA-256 `93b61d5b…afa9`）；**2026-08-28 起板上为 `c3cf3d7`**（含触摸第四轮链与主屏可视性，经用户照片确认，见 §3 157-158 行）。含零接触配网全链 + 扫描段三修复 + 省电门控/退避前段/播报等待信号/预取内联。2026-08-23 晚经新 `--after=watchdog-reset` 路径同镜像重刷验证：写入+`Hash of data verified`、WDT 复位后 COM14 无需拔插、App 正常启动并接管写字机（此前 flash 中途掉口两次致 App 区被擦除半砖，由该次重刷恢复）。此前镜像 `bb7ee987…32439`（`4ce8b59`）、`303B7A77…2D54`（`c4e9c1b`）等已被覆盖。
- **当前源码验证与构建**：全 host suite **131/131**（含零接触配网 core 契约、802.11 嗅探 IE 解析、省电门控双板锚点、退避前段序列编译测试）。ESP-IDF v6.0.1 `build-hil-ws35` 已刷 COM14（SHA-256 `93b61d5b…afa9`）；门禁双板编译走 `tmp/build_gate_prov.ps1`（hub 仓）。最近一次 BLE 启用构建仍是 `build-hutuji-ble-enabled/xiaozhi.bin` 3,172,544B、SHA-256 `5a68fafe…80d6`（`CONFIG_HUTUJI_BLE_DIAGNOSTICS=y`，生命周期代码 `f1aa0e7`）。
- **取证脚本已修正**：`results/local-only/2026-08-20/serial_capture.py` 旧版靠 pyserial 默认 open，DTR/RTS 在 open/close 各复位一次板子（S3 原生 USB Serial/JTAG 把它当复位线），曾把工具复位误读成「自发重启」；现改为清 `dtr`/`rts` 后再 `open()`。同目录 `serial_reset_capture.py` 是**有意**用 RTS 发复位脉冲——但 2026-08-23 已定性 RTS（EN）脉冲会在 App 运行中间歇杀死 USJ 重枚举，复位一律改走 WDT 路径（`tmp/reset_wdt_capture.py`），该脚本仅留作历史。
- **BLE 板上窄证据与回滚（生命周期代码 `f1aa0e7`，当前 HEAD 未改该模块）**：启用镜像 `5a68fafe…80d6` 刷入 COM14 后 4 分钟 1,683 行日志无 `BLE_ERR_MEM_CAPACITY`/panic/assert/reboot，free SRAM 稳定约 22.1KiB（`minimal sram: 1647` 低于 24KiB 门槛，缺 BLE-off 基线，不判定回归）；Bleak 30s/15s 均收到目标 UUID、固定 6 字节 payload、seq 10→140、`stale=0`、无 local name/scan response。NVS 回滚读回 byte-identical（`enabled=0`），默认镜像重刷自证，10s 目标 UUID 扫描无匹配。证据在 `results/local-only/2026-08-19/`（本机，不入库）。BLE 仍 🟡：尚未完成共存量化矩阵（含 off 基线）、完整功耗/温升与阶段 B 认证 GATT。

---

## 4. fz（`D:\Users\zhugu\fz`）

### 未跟踪文件
| 文件 | 说明 |
|------|------|
| `native_sim/telnet_server_behavior.cpp` | Telnet 服务器行为 native sim 实现 |
| `native_sim/test_telnet_server_behavior.py` | 对应 pytest 测试 |

### 分支与规则（HEAD 存档并入）

- **路径 / 分支 / HEAD**：`D:/Users/zhugu/fz` / `main` / `c2bf1a3`。
- **工作树**：2 个未跟踪实验文件 `native_sim/telnet_server_behavior.cpp`、`native_sim/test_telnet_server_behavior.py`；属于用户资产，保留、不上传到 hutuji。
- **规则**：改 Grbl 代码后必须在 fz 跑 `scripts/agent_gate.py --profile standard`；本仓 `full` 只做 §9 标记检查，不能替代。

---

## 5. 当前汇总（证据快照，HEAD 存档并入）

| 仓库 | 实时 HEAD 命令 | 已知状态 |
|---|---|---|
| hutuji | `git rev-parse --short HEAD` | 本仓 HEAD 以左列命令现取（08-29 起为 docs-only 证据登记提交，与 `origin/main` 同步；此处不钉 SHA 防自指陈旧）；**生产现役应用基线 `b7f88e3`**（2026-08-29 晚生产漂移收口并复验：运行时包 `hutuji_mcp/` 全 22 文件与 HEAD blob SHA-256 双机逐位相符、CR=0，见 deployment.md 首条；前基线 `befba0a` 为 08-29 上午登记）：冒烟「画一只猫」curated hit 零付费（不注入设备 token 故不推屏），公网真下载 8923B SHA 相符、`/mcp` 404，双服务 active（阿里云 mcp+bridge MainPID 422803/422823，京东云仅 mcp MainPID 359004）。**已关闭的滞后登记**：`preview_url` 白名单 2026-08-24 `55a8d0a` 已修；`c2cdbd4` 之后 13 个服务端 commit 已于 08-28 `34b44d4` 全部部署，两处登记均已关闭。 |
| xiaozhi-esp32 | `git -C D:/Users/xiaozhi-esp32 rev-parse --short HEAD` | **`ac819f7`**（`feat/hutuji-usb-pipe`，已推远端；前基线 `2f06a67` 已推，2026-09-01 多页文章设备侧批 + lichuang-dev 构建假绿修复，见本文尾）。2026-09-04/05 新增三提交均未刷机：`2cf3715` 点动包线 Y190→277 反转（对齐云端 §5）、`bb157b7` 灌流日志降载 + `9262618` in-flight/延迟直方图埋点 + `5e90cd3` 构建期开关 `HUTUJI_QUIET_STREAM_LOG`；host **148/148**；A/B 双镜像已备（ON `16C4D4DE…` / OFF `8E0A0689…`，证据见 release-readiness 2026-09-05 行）；工作树不净（2026-09-06 晚 TLS 复用批未提交，见下文「TLS 连接复用批」节）；两套 release 真编译通过（lichuang-dev zip SHA-256 `04a81463…c3b0`、waveshare 3.5 `4ad35683…b7a69`）。**waveshare 3.5 已于 2026-09-01 傍晚刷入并在板核对**（`build/xiaozhi.bin` = `f01650dd…a2f0`；`verify-flash` digest matched、otadata slot0 `seq=1 state=0x2` 运行 `ota_0`、`esp_app_desc` build `Sep  1 2026 14:33:55` / idf v6.0.1 / `elf_sha256[:32]=bc891553…`；启动日志见本文尾「刷机与在板取证」行），故板上**已含**多页文章设备侧支持；**但带 `pages` 的真机两页 HIL 未做**。lichuang-dev 板未刷（无该板硬件在手）。2026-08-23 全链审计登记的 `hutuji_job.cc` 预取 PSRAM 泄漏已由 `67507c6` 修复（见 §3）。 |
| Grbl_Esp32 | `git -C D:/Users/Grbl_Esp32 rev-parse --short HEAD` | **`8047d29`**（分支 `fix/panel-hold-after-align-116687e`，工作树干净；含 §9-G′ 长名守卫 `8f3e7d6` + 写保护 `40616de`，其前为 §9-F′ M30/换纸 `$1=255` 守卫 `2cecd2d` + 机型默认 `7f48217`）；**在役 COM13 板上镜像 SHA `7069D20E…05C4E1`**（2026-08-30 刷，取代 `12275654…429A5`；详见本页 §2「路径 / 分支 / HEAD」行）。第二台新机（2026-08-31 刷 `8047d29`，`firmware.bin` SHA-256 `270d8c84…311b60`）见本页同段落。用户确认使能/出图正常 |
| fz | `git -C D:/Users/zhugu/fz rev-parse --short HEAD` | `c2bf1a3`；2 个用户实验文件保留；门禁须走 clang 路径（见 §2）；wokwi 可选层 2026-08-23 起受免费 CI 配额阻塞，非固件回归（见 §2） |

---

## 禁令要点（claims_forbidden，绿了也不能说）

- 可出货
- 商业放行
- §9 已完成
- §9 已修好
- 硬件已验证
- 联调完成
- 京东云已上线
- M2 已完成

---

## 同步契约

| 你改了什么 | 必须更新 |
|------------|----------|
| 跨仓脏树 | 本文件 |
| 进度 | `docs/release-readiness.md` |
| 契约 | `docs/protocol.md` |
| 结构/约定 | `AGENTS.md` + `README.md` + `CLAUDE.md` |
| 门禁口径 | `docs/agent-gate.md` + `.cursor/rules/hutuji-agent-rails.mdc` |

> 每次新增/清除工作树后立即更新。本页的静态 SHA 是证据快照，不替代实时命令；源码 HEAD 也不自动等于生产/板上运行态。

---

## 评审修复批：draw_queue inflight 残留（2026-08-30，全面评审发现 #1）

| 文件 | 说明 |
|------|------|
| `mcp-server/hutuji_mcp/draw_queue.py` | 修 single-flight 残留窗：新增 `_decide_attach(existing, status)` 纯函数（attach/create/replace 单源决策，两后端共用）；Redis `attach_or_enqueue` 重写为有界重试环——inflight 指向 failed 或 job_key 已消失（JOB 600 < INFLIGHT 620 的 20s 窗口、worker 崩在 mark_failed 与 clear_inflight 之间）时比较后删残留重新入队，不再把重试钉到死任务上；NX 冲突后重判 winner 死活。TTL 保持 600/620：replace 已兜底残留，反转无害，缩短 inflight 反而引入处理中重复付费生成（注释钉死）。限深/限流首判后只扣一次；「首轮扣配额后仍 attach 到并发 winner」为已记录已知不对称（仅真并发竞争发生，额度软限制） |
| `mcp-server/tests/test_draw_queue.py` | 4→17 项：`_decide_attach` 决策矩阵 8 项参数化 + `_FakeRedis` 桩回归 5 项（残留 replace × 2、存活搭车、NX 冲突重判 × 2）。memory 后端无 TTL 无法复现窗口，Redis 决策环走桩测（advisory 裁决口径） |
| `AGENTS.md`、`docs/worktree-inventory.md` | 计数同步 1070→1083（自洽测试 `test_test_count_claims_self_consistent` 同源钉死） |
| 验证状态 | pytest **1083/1083**（94.02s）；hub 门禁 **PASS exit=0**（2026-08-30，指纹 238/238 重登后，两处变更均对应本批有意编辑）；e2e PASS。**已部署生产**（2026-08-30 22:07 双机，提交 `811426e` 已推 origin）：双机 draw_queue.py 与 HEAD blob SHA-256 `23120277b4a7…50a0c861` 逐位相符、CR=0；阿里云 restart mcp+bridge+draw-worker@{1..8}、京东云仅 mcp；阿里云生产 venv 真 Redis 命名空间冒烟三场景全过（`ATTACH_REPLACE_SMOKE_OK`），bridge-health 双 connected；preflight 阿里云 24/33（9 fail nginx 布局类已知差异）、京东云 28/33（5 fail 热备预期分歧），详见 deployment.md 首条 |
| 本机运维脚本（不入库） | `tmp/deploy_drawqueue_fix_0830.py`（双机部署 + 真 Redis 命名空间冒烟）、`tmp/preflight_both_0830.py`（双机 preflight 运行器）、`tmp/prod_recon_0830.py`（阿里云生产问题排查：journal/队列/对话/埋点）：按既有决策本机解析 `VPS.txt` 凭据，从未 add/commit/push（`git log --all` 双路径为空取证），同 `tmp/deploy_mcp_delta_0830.py` 先例 |

## 优雅停机批：draw_worker SIGTERM 语义（2026-08-30 晚，22:31 recon 发现项闭环）

| 文件 | 说明 |
|------|------|
| `mcp-server/hutuji_mcp/draw_worker.py` | `run_forever(stop=None)` 支持停止事件：循环改「迭代边界观察 stop」，画途中跑完当前付费 job 再退出；`main()` 用 `loop.add_signal_handler(sig, stop.set)` 替代 `loop.stop`（旧路径 `run_until_complete` 抛 RuntimeError → 协程悬挂销毁 → systemd 记 exit=1）。**事故注记**：`0e2fb3d` 首版误删 `_install_secret_redaction()` 与 `loop = asyncio.new_event_loop()` 两行（edit 范围错位），22:57 部署后 worker 全量 NameError 崩溃回环约 4 分钟；`3efec02` 补回并加子进程回归钉死 |
| `mcp-server/tests/test_draw_queue.py` | 17→20 项：停机后干净返回、画途中置 stop 跑完当前 job、main 接线子进程回归（缺队列地址须 SystemExit 文案、不得 Traceback——防同类误删） |
| 验证状态 | pytest **1086/1086**；hub 门禁全绿（指纹重登 2 处均为本批有意计数编辑）。**已部署生产**（2026-08-30 23:01，提交 `3efec02` 已推 origin）：双机 draw_worker.py 与 HEAD blob SHA-256 `93230e02d3b7…fa6ff89f` 逐位相符、CR=0；阿里云 restart worker@{1..8} 全 active，京东云仅同步文件（不跑队列不重启）；**优雅停机生产实证**——二次重启（新码对新码）journal 现「收到停止事件，退出消费循环」→ systemd `Succeeded`/`Result=success`/`ExecMainStatus=0`；备份 `/root/hutuji-bak-20260830-worker-graceful/`（备份先行 + 幂等防覆写），详见 deployment.md 首条 |
| 本机运维脚本（不入库） | `tmp/deploy_worker_graceful_0830.py`（双机部署 + 重启前接线冒烟——空 `HUTUJI_DRAW_QUEUE_URL` 起 `python -m hutuji_mcp.draw_worker` 须见「未配置」且无 Traceback，防 NameError 类事故直达生产）；凭据处理同上节先例 |

## 停机宽限 + 仓库卫生批（2026-08-30 晚，advisory + 评审 findings 裁决）

| 文件 | 说明 |
|------|------|
| `deploy/hutuji-draw-worker{,@}.service` | 补 `TimeoutStopSec=300`：优雅停机只置事件，默认 90s 宽限会让长 job 照样被 SIGKILL；300s 为保守取值（付费全链路上界未实测，当日 5 个生产 job 全为缓存快路径约 2s）且 < JOB_TTL 600s（advisory 指出，原部署只证明了空闲 worker 干净退出） |
| `mcp-server/tests/test_contract.py` | +1 项（606→607）：`test_worker_units_grant_stop_grace_longer_than_paid_job` 钉两 unit 恰好一条 TimeoutStopSec、240≤v<600；全套 1086→**1087** |
| `.gitignore` | 窄规则 `tmp/*password*`（finding #1：`tmp/pg-password-20260830-phase3-rollout.txt` 明文 PG 密码此前未被忽略，`git add -A` 会入库；git 历史为空取证从未入库；`check-ignore`+`add -An` 干跑实测生效）。文件留原位不动——6 个本机 rollout 脚本引用该路径，且与 quickdraw/curated「本机留档+窄规则」先例同口径 |
| `tmp/prod_recon_0830.py`（本机不入库） | finding #2/#3 修复：删除本地手写 token 正则，输出统一走 `hutuji_mcp.redact._redact_secret_text` + 能力 token 打码（同 `scripts/cloud_health_pull.py` 口径）；对话内容同管道过脱敏。实测 `token=abc123`→`<redacted>`、能力文件名打码正常 |
| 验证状态 | pytest **1087/1087**；hub 门禁全绿（指纹重登 2 处均为本批有意计数编辑）。finding #4（paramiko AutoAddPolicy TOFU）维持既有先例记为已知风险不阻塞；#5 登记面 OK 无动作；#7 评审方未读生产内容，不构成生产结论 |

## 固件双仓评审批（2026-08-31 中午，只读评审，无代码改动）

| 文件 | 说明 |
|------|------|
| `docs/kxnx/findings/30-firmware-dual-repo-review-0831.md` | xiaozhi `bf0b295^..0d041c9`（27 提交）+ Grbl `e3b1cad^..40616de`（6 提交）评审结论：**1 项 P2**（§9-G′ 写保护长名绕过面，`$X/MaxTravel=200` 可改 `$130`，须补 `kLockedKeys` 长名——后续固件补丁待用户批准）、1 项 P3 治理台账（`agent-edit-allowlist.json` 未含 `GCode.cpp`/`WifiConfig.cpp`/`custom_3axis_hr4988.h` 三个已授权触面）、5 项 P3 观察项；abort 状态机/指纹门禁 fail-closed/CAS 收口/host 测试钉定性均复核通过 |
| `tmp/review_harvest_{grbl,xiaozhi}review_0831.md`（本机不入库） | 两个评审代理撞 30min 运行上限被杀后的 transcript 分析块收割（131KB/119KB），收尾代理以此为线索复核出报告；主代理逐条裁决记录见 findings 内「裁决推翻/修正」节 |
| 验证状态 | 只读评审无门禁对象改动；findings 入库后 docs profile 门禁复核通过（见本节提交行）。R1（P2）固件修复与 R2 白名单补录为**后续动作**，本批未执行 |

## 门户修复 + 钩子加固批（2026-08-31 下午，本机脚本登记）

| 文件 | 说明 |
|---|---|
| `tmp/deploy_portal_index_0831.py`（本机不入库） | draw-portal `index.html` 发布器：读 VPS.txt 进程内密码（不落盘不落日志）→ 上传 sha256 核验 → 远端备份 → 原子 mv → curl 本机/公网响应 sha256 比对 + 京东云无门户实证。对应 portal 修复 commit `98c9ac5`；凭据处理同 `tmp/deploy_mcp_delta_0830.py` 先例 |
| `tmp/deploy_refresh_hook_fix_0831.py`（本机不入库） | refresh 对端同步钩子双机部署器：`git add` 后取 index blob（LF 纪律）→ 标记断言（防旧版误发）→ 双机备份 + `bash -n` + sha256 相符才替换。本轮复用发布 kb-admin 互联版（标记 `PEER_KEY:-}` 仍在） |
| `tmp/hook_test/`（本机不入库） | refresh 脚本钩子形状台架：`extract_old.py`（HEAD~1 旧钩子提取，RED 对照）+ `extract_kb.py`（现行钩子+互联段提取）从真脚本抽出打桩 ssh/systemctl 的 `hook_old.sh`/`hook_kb.sh`；bash 场景 t1–t5（set -u 旧版 RED / 新版 GREEN）与 k1–k5（kb 互联：unit 缺席只写文件不重启 / restart 失败仅 WARN 不外溢退出码 / 无 XIAOZHI_TOKEN 行则追加）实测 |
| 验证状态 | 钩子 + 互联块形状钉死进 `test_contract.py`（1129 项）；kb 互联双机部署完成；hub 门禁 PASS（见本节提交行） |

## 写字排版链批（2026-08-31 深夜，本仓 `be994ba` 已推 origin）

| 文件 | 说明 |
|---|---|
| `mcp-server/hutuji_mcp/gfont.py`（新） | 奎享 .gfont 解析器：XXTEA-ish 头解密（密钥同 `kx/app/crates/font_gfont` Rust 参照）、v2–v9 头兼容 + 两个实测变体（方正兰亭 v6 明文头回退、u16 preview 计数）、ZIP 十进制码位字形条目、cubic 24 步细分 |
| `mcp-server/hutuji_mcp/write_text.py`（新） | 文章排版：探针实测字库度量（em 高/全宽/基线，v<9 字库 meta 无度量）、段首缩进 2 字/标点避头/CJK 任意折行/拉丁按空格/自动分页（页区 190×190 自排，max_pages=20 防呆）、产物仅 `<path>` 单线 SVG 过 `_validate_svg` |
| `mcp-server/hutuji_mcp/{server,config,gcode_gen}.py` | `hutuji_write` 工具（text/font/size_mm/line_spacing/char_gap_mm → pages[] 产物 URL）；`HUTUJI_GFONT_DIR` 配置（空值禁用；**字体为奎享专有素材不入库**，本机取 kdraw 安装目录）；`svg_to_gcode_async(..., normalize=False)` 跳过 vpype layout——`--fit-to-margins` 实测把内容缩放 1.07× 且随每页内容量漂移，写字链字号保真必须绕开，linemerge/linesort/linesimplify/gwrite/validate_gcode 全量继承 |
| `mcp-server/tests/test_write_text.py`（新） | **16 项**：合成 v2 明文 gfont 夹具（不引专有字体）解析/度量/折行/避头/分页/缺字/端到端过 §5 校验；全套 **1145** 项 |
| 验证状态 | hub 门禁 PASS（1145 + e2e + 指纹 255）；真机 HIL（2026-09-01 凌晨）：本地生成 → scp 阿里云 output → 公网双能力地址 200 → `device-call hutuji.draw` → 屏幕确认 → `开始灌流 lines=1021 xy=719` → 720/720 全发 → 页尾归位 → `<Idle>`；M30 换纸报 `error:90 缺纸/卡纸`（疑纸盒未装纸，待用户确认复测）；证据 `results/local-only/2026-08-31/{write-hil-com14.log,write-hil-draw-com14.log}` + 逐台记录同目录；预览 PNG 首版设备白屏（灰度整页细线被缩盒子采样吞没），RGB 内容裁剪加粗版可见，生成器 `tmp/make_write_preview.py`（探针工件不入库） |
| 后续 | 多页文章的 S3 逐页循环下发已由 xiaozhi `2f06a67` 落地（见本文尾「多页文章设备侧批」，源码已推、已刷 waveshare 3.5）；~~生产部署 `hutuji_write` 未执行~~（2026-09-02 晚已执行：代码 6 文件双机 SHA-256 逐位相符 + 双机 mcp.env 加 `HUTUJI_GFONT_DIR` + 9 个 `.gfont` 字体三方校验分发，冒烟/preflight 全过，见本页顶部「hutuji_write 生产上线」小节） |

## 多页文章云端配套批（2026-09-01，本仓 `f268a0e` 已推 origin）

| 文件 | 说明 |
|---|---|
| `mcp-server/hutuji_mcp/write_text.py` | 新增 `page_to_preview_png()`：每页笔画（mm 折线）直渲 512² RGB PNG（内容包围盒 +10% 留白、2px 笔、空页全白兜底），替换原 SVG 预览——设备 `IsValidDrawCapabilityUrl(preview, ".png")` 硬性只认 PNG |
| `mcp-server/hutuji_mcp/server.py` | `_run_write_job` 每页写 `paths.preview_path`（PNG）+ 返回 `.png` preview_url；`_chain_device_draw` 增可选 `pages` 参数（紧凑 JSON 字符串，xiaozhi MCP Property 无 array 类型）；write 完成点链式推 `pages` 页表（第 1 页同时填 url/preview_url） |
| `docs/protocol.md` | §4 表 `hutuji.draw` 增 `pages`；新增 §4.2 多页文章契约（JSON 字符串形态/fail-closed 校验/`pages[0]`=url·preview_url/页循环/status 增 page·pages/repeat 整篇重来） |
| `mcp-server/tests/test_write_text.py` | 16→**18 项**（+预览 PNG 可见性/空页全白、+chain_push pages 紧凑 JSON 形状钉死）；全套 **1147** 项 |
| 验证状态 | 全套 pytest **1147/1147** 通过（计数自洽测试 RED→GREEN）；hub 门禁 PASS（1147 + e2e + 指纹 255，45 处半漂移=外仓多页改动行号位移，35 处 --fix-line-refs 自动 + 9 处人工核对重定位 + 基线重登）；生产部署与真机两页 HIL 未执行 |

## 多页文章设备侧批 + lichuang-dev 构建假绿修复（2026-09-01，xiaozhi `2f06a67` 已推 `feat/hutuji-usb-pipe`）

| 文件（外仓 `D:/Users/xiaozhi-esp32`） | 说明 |
|---|---|
| `main/boards/lichuang-dev/hutuji_recovery_core.h` | `ParseArticlePages` 纯扫描器（零 cJSON——宿主测试单 TU 编纯头）；`kArticleMaxPages = 20`；键序校验、值内 `\` 拒收、非法输入整体拒收（`json.find` 宽匹配会跨页错配，宁严勿宽） |
| `main/boards/lichuang-dev/hutuji_job.{h,cc}` | 多页成员 `article_pages_`/`article_index_` + `std::atomic<size_t> article_page_/article_total_`（0=非文章模式不出字段——`StatusJson` 是 const 不持 `stream_mutex_`）；`StartDraw` 在 `busy_` 前解析并校验 `pages.front()` 与 url/preview_url 一致；`Run` 在 `ChangePaperAfterDraw` 成功后翻页 + `ReleaseBuffer()` + `repeat_mode_` 复位 + `paper_change_notified_ = false`，翻页播报缓冲 96 字节（48 字节把中文截成半个字）；`RequestRepeat` 对文章禁快路、整篇从第 1 页重来 |
| `main/boards/lichuang-dev/lichuang_dev_board.cc`、`main/boards/waveshare/esp32-s3-touch-lcd-3.5/esp32-s3-touch-lcd-3.5.cc` | 两板型各注册 `Property("pages", kPropertyTypeString, "")` 并把 pages 传进共享 `hutuji::Job` 单例 |
| `main/application.cc` | **构建假绿根因修复**：`boards/lichuang-dev/*.cc` 在 lichuang-dev 上走板级 glob 全量链入（含 `hutuji_conversation_report.cc`），但会话上报的 include/调用点只守 waveshare 宏 → lichuang-dev release 编译报 `'hutuji' has not been declared`。改用 `HUTUJI_CONVERSATION_REPORT_ENABLED` 覆盖两板型；激活码中转仍只守 waveshare（其源只在 waveshare 显式 CMake 列表内） |
| `scripts/tests/test_hutuji_recovery_core.py` | 144→**145 项**：新增 `test_conversation_report_guard_covers_every_board_that_links_it`（守卫宏必须同时覆盖两板型 + 两处调用点都在宏下 + 两板都调 `InitConversationReport`，回退 `application.cc` 到 HEAD 取 RED→GREEN 实测）；此前 3 项文章相关用例保留 |
| 构建证据 | **`release.py` 假绿已定性**：`releases/v2.4.0_<board>.zip` 已存在即跳过 set-target/build/merge-bin 并打 `RELEASE_RC=0`，此前 5.3s「构建成功」实为复用旧 zip、零编译。真实 env 为 `IDF_TOOLS_PATH=C:\Users\zhugu\.espressif`（**不是** `C:\Espressif\tools`——该路径无 `tools.json`），`export.bat` 前须把 `…\python_env\idf6.0_py3.13_env\Scripts` 与 `C:\Program Files\Git\cmd` 前置进 PATH（否则 export 走 `__error_missing_requirements`，且该标签在文件内不存在）。本轮两套真编译：lichuang-dev 1980 个编译单元、`build/xiaozhi.bin` 3,038,096B、zip SHA-256 `04a81463…c3b0`；waveshare/esp32-s3-touch-lcd-3.5 1982 个编译单元、`build/xiaozhi.bin` 3,036,784B SHA-256 `f01650dd…a2f0`、zip SHA-256 `4ad35683…b7a69`（旧 08-20 包改名 `…pre-20260820.zip` 备份）。两次日志均含 `application.cc.obj` 与 `boards/lichuang-dev/hutuji_job.cc.obj`，二进制内可检出多页播报字符串。板型选择经 `release.py waveshare/esp32-s3-touch-lcd-3.5`（manufacturer 子目录必须带前缀，裸板名会「no config.json，skipping」） |
| 刷机与在板取证（2026-09-01 16:41–17:0x） | **已刷 waveshare 3.5 并在板核对**。刷机用 `tmp/flash_ws35_build_0901.ps1`（复制 `flash_hil_ws35.ps1` 改镜像路径为 `build/xiaozhi.bin`——AGENTS.md 铁律：刷机脚本写死路径，复用即刷旧镜像）；日志 `tmp/build-gate-logs/ws35-flash-0901.log` 含 `Wrote 3036784 bytes`/`Hash of data verified.`/`Hard resetting with a watchdog...`，`.err` 的 `Cannot configure port` 是已定性的复位后 USJ 关句柄竞态，脚本守卫按成功归类。**在板独立复核**：`esptool verify-flash 0x20000 build/xiaozhi.bin` → `Verification successful (digest matched)`；`read-flash 0xd000 0x2000` 解析 otadata = slot0 seq=1 state=0x2（ESP_OTA_IMG_VALID）、slot1 全 0xFF，故运行的就是 `ota_0`（=刚写入的 0x20000）。**镜像身份**：`esp_app_desc` project=`xiaozhi` version=`2.4.0` date=`Sep  1 2026` time=`14:33:55` idf=`v6.0.1`、`elf_sha256[:32]=bc891553…e161` 与 `build/xiaozhi.elf` 全量 SHA-256 `bc89155377f8…e161` 前缀一致；`certutil -hashfile build/xiaozhi.bin SHA256` = `f01650dd…a2f0`（与本表构建证据行同值，不再是无依据注释）。**构建日志在场**：`D:/Users/xiaozhi-esp32/build/log/idf_py_stdout_output_9636`（300,616B）含 `[2182/2203] Building CXX object …/application.cc.obj`、211 CXX + 1771 C = **1982** 编译单元、`Generated …/xiaozhi.bin`、`binary size 0x2e5670`（=3,036,784B），排除 `release.py` 假绿。**多页代码在镜像内**：`xtensa-esp-elf-nm -C build/xiaozhi.elf` 可见 `hutuji::ParseArticlePages(...)` 及其内部 lambda 符号 |
| 启动取证（`results/local-only/2026-09-01/ws35-postflash-wdtboot.log`，231 行） | 用 `tmp/reset_wdt_capture.py`（ROM 进入 + `--after=watchdog-reset`，**不脉冲 RTS/DTR**）取；缺前 ~1.3s（已知代价，故无 `rst:`/`Board type` 行）。在场判据：`HutujiPipe: 缓存 IP 命中: 192.168.1.13` → `已连接写字机 Telnet（192.168.1.13:23）` → `Grbl 版本探活成功（[VER:1.3a.20211103:]）` → `授权探测通过（抬笔后 G53 零位移）`，Grbl 侧回 `[MSG:Using machine:Custom 3-Axis HR4988]`、`[MSG:Mode=STA:…IP=192.168.1.13:MAC=94-3C-C6-42-77-FC]`；唤醒词 `xiao pai` 与 AFE 就绪，`free sram` 稳定 22.5KiB。**注意该 MAC/SSID 是老写字机（ChinaNet-jJmz / 192.168.1.13），不是 2026-08-31 新机（vivo X300 Pro / 10.181.196.202 / 94:3C:C6:96:CE:D0）** |
| 验证状态 | 宿主测试 **145/145**（30.6s，`D:/Users/hutuji/mcp-server/.venv/Scripts/python.exe -m pytest scripts/tests/ -q`）；hub 门禁 PASS（1147 + e2e + 指纹 258/258）；**已刷机并在板核对镜像身份与启动**（见上两行）；**真机两页文章 HIL 仍未执行**——未下发过带 `pages` 的 `hutuji.draw`，多页循环、翻页播报与错峰 TTL 的真机时序均无证据 |

## 多页文章错峰 TTL 硬修（2026-09-01，本仓）

| 文件 | 说明 |
|---|---|
| `mcp-server/hutuji_mcp/config.py` | 新增 `page_ttl_budget_seconds: int = 300`（env `HUTUJI_PAGE_TTL_BUDGET_SECONDS`，校验 30..3600：下界=一页灌流+换纸，上界防 20 页末页窗口超 ~15h）；`mcp-server/README.md` 与 `deploy/env.mcp.example` 同步登记该变量 |
| `mcp-server/hutuji_mcp/output_store.py` | `LocalOutputStore.finalize_job` 落地 `created_at`（`None`=旧语义空操作；否则 `os.utime` 改 svg/gcode/png 三件 mtime，OSError 只 warning 不废任务）；`OssOutputStore.finalize_job` 把 `created_at` 写进 `x-oss-meta-hutuji-created`；新增 `_created_meta()` 并让 `cleanup_expired` 对「按 `last_modified` 看已过期」的对象补一次 `head_object` 再判——修掉 OSS 清理只看上传时刻会误删靠后页的既有缺陷 |
| `mcp-server/hutuji_mcp/server.py` | `_run_write_job` 取 `ttl_base = time.time()`，第 i 页（i≥1）`finalize_job(created_at=ttl_base + i*page_ttl_budget_seconds)`；第 1 页恒 `None`（单页语义逐字节不变——传 ttl_base 反而会按生成耗时缩短第 1 页窗口） |
| `docs/protocol.md` | §4.2 新增「错峰 TTL」条目：窗口公式 `output_ttl_seconds + i×page_ttl_budget_seconds`、实测 ≈125s/页依据、本地 mtime 与 OSS 元数据两条判定链、文件名格式不变（设备侧正则硬编码，无需固件配合）、`hutuji_status.latest_ts` 可能显示未来时刻的已知偏差 |
| `mcp-server/tests/test_output_store.py` | 2→**4 项**：本地 `finalize_job(created_at=…)` 三后缀同改 + `None` 保持旧语义；OSS `cleanup_expired` 按 created 元数据判过期（错峰页存活、真过期页删除、未过期对象不多花 head） |
| `mcp-server/tests/test_write_text.py` | 18→**19 项**：多页写字任务错峰断言（第 1 页=写盘时刻、第 i 页差 i×300s、每页三后缀同窗口、末页未来 mtime 仍 `_serve_file` 200） |
| 服务端层取证（2026-09-01 18:00–19:00，不入库脚本） | `tmp/twopage_run.py`：本机真跑 `_run_write_job`（`chain_push=False`，字体「如果梦万国库」，`HUTUJI_GFONT_DIR` = kdraw `gcodeFonts` 9 个 .gfont 在场）→ 2 页产物 page1 `draw_20260901_180048_R8pe1AUXd-yRPUpVR_OY3Q` lines=10597/bytes=240558/pen_lifts=1708/text_lines=13、page2 `draw_20260901_180052_lxoLRmpFouO5lZrM4Aw8fQ` lines=6260/bytes=143467/pen_lifts=1007/text_lines=8，9.1s 出 2 页，`missing_chars=[]`；**错峰实测** page2 三件 mtime = page1 + 294.3s ≈ 1×budget（窗口 894.3s vs 597.6s）。`tmp/twopage_serve.py`（真 uvicorn:8397 起 `server.app`）四条能力 URL 全 200：240558 / 15659 `image/png` / 143467 / 10803。`tmp/ttl_sim.py` 六页反事实（真 `GET /files/`，不打桩 `_serve_file`；第 i 页 mtime 倒推到设备真下载时刻 age=i×125s）：**不错峰组 1–5 页 200、第 6 页 404**（age 625s > TTL 600s），**错峰组 6 页全 200**。证据落 `results/local-only/2026-09-01/ttl-stagger-http-counterfactual.log`；`_serve_file` 只认 22 位 urlsafe token，构造对照文件名必须守此格式（首版用错长度导致 6/6 全 404，已修） |
| 验证状态 | 全量 pytest **1150/1150**（计数自洽测试 RED→GREEN 两轮：先 1147→1149 仍红，实际分项和 1150）；hub 门禁 **overall=pass**（1150 + e2e PASS + 指纹 258/258）；改注释后 `test_write_text.py`+`test_output_store.py` 23/23 复跑通过。**「第 6 页 404」已在服务端 HTTP 层实证**（见上一行），**设备侧真机两页 HIL 仍未做**——设备是否真按 t0+i×125s 节奏下载第 i 页、翻页播报与 `page/pages` 状态字段均无真机证据（待用户在在役写字机 192.168.1.13 装纸并在场；无纸则 `M30` 报 `error:90`）。指纹重登两轮均为本次有意编辑所致的行号位移：第一轮 2 处 `--fix-line-refs` 自动重定位（`docs/protocol.md` 引用因 §4.2 新增条目整体下移 12 行）+ findings #30 的 §9-E′ 引用人工改指 §9 表 E′ 行（原空锚点落在空白行）+ findings #28 的 `AGENTS.md` 计数行指纹随 1147→1150 更新；第二轮 4 处 findings #30 的 readiness 引用因本批新增首条 readiness 记录整体下移 1 行（自动器判「空锚点/多处命中」全交人工，逐条核对目标段落内容一致后重登） |


## 预览/下载 TLS 失速与启动回归根因批（2026-09-06 机器日场，固件 xiaozhi 仓）

| 项 | 内容 |
|---|---|
| 范围 | 本仓零代码改动；xiaozhi 仓提交 `2cddf9a`（feat/hutuji-usb-pipe，2026-09-23 已随批量推送）；现场证据不入库（gitignored），脚本在 `tmp/`（不入库） |
| 用户主诉 | 「点开始画没反应 / 画不了」→「预览好久出不来」→「感觉小派卡住了 / 机启中」→「写出来的字镜像了」 |
| 根因链（全部有现场） | ①**预览/下载失败**：音频会话活跃期内部 SRAM 瞬时低谷（实测 minimal 643B、TLS 握手段 2743B）→ TLS 读记录时 esp-aes 1600B DMA 块分配失败（esp_aes_dma_core.c）→ esp-tls -0x0084 → SSL read fail；判别变量是音频并发时序而非堆块数值（同日 L1984 成功 vs L5632 失败互斥假说否证）。②**60s 干等**（R4）：HttpClient 断连回调不唤醒 GetStatusCode 的事件组等待 → 白等满超时；修复补丁落 `patches/`（managed_components 不入 git，重拉取会丢，升级组件须携带）。③**「机启中」卡死**：构建目录 reconfigure 时 resync 了根 sdkconfig 漂移（WiFi 静态 TX 缓冲 16 块钉死内部 RAM），启动期最大连续块 14.8KB→7.7KB < activation 任务 8KB 栈 → xTaskCreate 静默失败 → OTA/模型/协议全不启动；恢复动态 TX（对齐昨已知良好配置，sdkconfig.h 与 build-quiet0-ws35 零差异验收）后激活 25s 恢复。④HEAP_TASK_TRACKING 取证路线废弃实证：开追踪即 boot 崩溃循环（tracking 自旋锁中断看门狗 + pcTaskGetName 对已删 TCB 取名双诱因，backtrace 钉到 heap_task_info.c:283），代码整段拆除不入库。 |
| 修复落地 | 三处下载（预览/预取/DownloadToPsram）有界重试：只对传输态（Open/Read/截断）重试 ≤3 次，abort/404/CRC 头等确定性失败立即出局；递增退避 1.2s/3s，重试超时 20s；预取是主路径（确认命中即画）此前单发即弃。PaperPresentState::No 对云上报 unknown（对齐用户 08-25「有纸无纸不用机器管」拍板；实机 PanelHold=On 纸在夹持仍被判缺纸的违约修复）。activation 任务创建失败现打 ESP_LOGE+堆八字段。 |
| 取证口径 | SystemInfo::LogHeapNow(phase) 八路指标（internal free/min/largest + DMA free/largest + PSRAM 三项）10s tick + 预览/gcode 下载出入口括号；Bf 轨迹 COM13 6.7Hz 同录。 |
| 实机证据 | v6 镜像（修复后）写诗全链路成功：预览 PNG+gcode 预取双 HTTPS 在音频活跃期成功（谷底 largest 1984B，距失败门槛 1600B 仅 384B）、灌流 835 行 48s（58ms/行，**113ms/行未复现**，in-flight 819/835 满窗口）、页尾归位+M30 换纸 9s PaperStatus 0 done。抓包：results/local-only/2026-09-05/（hil-speed-com14-*.log 多段、bf-watch-com13-125528.log、boot-wdt-v5.log 等） |
| 待办 | ①v7 终版（三重试修订+断言+源码钉）已编译未刷——等用户插电刷入；②「镜像」方向判别待用户「上」字物理观察（短横上下+竖笔左右两 bit 定位；数字链 SVG→G-code 恒等已证，问题只可能在物理映射/观察方位）；③写字页 113ms 判据今日未复现（58ms/行），PC-telnet 对照实验仍待 S3 让槽窗口；④xiaozhi 仓五提交（2cf3715..2cddf9a）待统一推远端 |
| 测试 | xiaozhi host 150/150（新增下载重试契约源码钉 RED→GREEN 实证有效）；本仓 hub 门禁在 `b0a8cd6` PASS（本批本仓零代码改动） |

## TLS 连接复用批（2026-09-06 晚，xiaozhi 仓脏树未提交；页 2 下载死亡修复）

| 文件（外仓 `D:/Users/xiaozhi-esp32`） | 说明 |
|---|---|
| `main/boards/lichuang-dev/hutuji_job.{h,cc}` | `AcquireFetchClient()/ReleaseFetchClient()`（`std::unique_ptr<Http>` + `fetch_mutex_` 串行化）：prefetch/预览/页下载三处共享同一 keep-alive 连接，成功路径不 Close，终态与预览入口释放；并入当日早批的 status<100 传输态重试与播报尾 3s 静置；新增 `page-draw-begin/end` 两条 LogHeapNow（流式期堆对照） |
| `patches/esp-ml307-ssl-rx-task-and-keepalive.patch`（新） | `src/esp/esp_ssl.cc`：收包任务 `xTaskCreate` 失败显式报错（原静默死连接=页 2 死亡直接机制）+ TCP keepalive 120/10/3 |
| `patches/esp-ml307-http1-keepalive-default.patch`（新） | `src/http_client.cc` + `include/http_client.h`：HTTP/1.1 默认持久连接（RFC 7230 §6.3），`response_http_1_1_` HEADERS 识别 + `ResetRequestState` 按响应复位 |
| `patches/README.md` | 三补丁表格登记 + 循环核验记录；路径修正（`src/esp/esp_ssl.cc`、`include/http_client.h`） |
| `scripts/tests/test_hutuji_recovery_core.py` | 配套钉：三处 fetch 的 `status < 100` 传输态重试形状 + DownloadToPsram 重试先于 fatal 分支 + 播报尾静置标记（本批同日早会话产物，随本批一并入库） |
| 验证 | 三补丁「`git apply -R` → 干净树 `--check` → apply 恢复」全循环实测通过；镜像 `build-instr-lichuang/xiaozhi.bin` 3,048,832B SHA-256 `367EEE2412E1B91CEC5BF67A62DAA9F25016875EA4B2B349F02E8436E6810F4F` 已刷 COM14（18:20，`Hash of data verified` + watchdog 复位；启动横幅待下次重启核对）。云端配套：nginx 1800s 双机 + preflight 对账恢复一致（hub 仓 `deploy/nginx-hutuji.conf` 已同步模板） |
| 待办 | ① 用户断电重启后 grep 捕获日志 `Compile time`/`ELF file SHA256` 对 `xiaozhi.elf` 核对镜像身份；② 语音两页文章 HIL：`Reusing existing connection` 日志 + 页 2 下载成功 + 堆日志对比；③ xiaozhi 仓本批提交并推远端 |

## 双仓评审修复批（2026-09-06 深夜，评审后续；两外仓提交 2026-09-07 已推远端）

| 项 | 内容 |
|---|---|
| Grbl `3a6ee4e`（分支 fix/panel-hold-after-align-116687e） | 修复 `371a3c4` release 构建断裂：`src/Limits.h` → 复活为 `src/grbl_limits.h`（声明逐字一致+修复注释），`src/Grbl.h` 改指新名。RED（stash 后 pio release 12+ 处 not-declared）→GREEN（pio release SUCCESS 39.9s，firmware.bin 产出）实证；fz standard 修复前后同态（overall pass；`wokwi_cloud_esp32_startup` fail 为修复前即有的环境性层，同态复现）。`.gitignore` 改动为用户既有工作，未动 |
| xiaozhi `80b0336`（分支 feat/hutuji-usb-pipe） | `sdkconfig.defaults.esp32s3` 钉 `CONFIG_FREERTOS_TASK_CREATE_ALLOW_EXT_MEM=y` + `main/boards/lichuang-dev/hutuji_job.cc` 顶部编译期 `#error` 断言（WiFi TX 双层防漂移同款）。host 165/165 通过；g++ 预处理双态实证断言有效；**未刷板**（旗标本就在生成 sdkconfig 里，重建后行为无变化预期，随下次刷机顺带） |
| 本仓同批改动 | `docs/agent-edit-allowlist.json` extras +2（`Grbl_Esp32/src/Grbl.h`、`Grbl_Esp32/src/grbl_limits.h`，授权=2026-09-06 用户「同意修复」）；`scripts/agent_gate.py` `ALLOWLIST_EXACT` 同步；`AGENTS.md` 硬规则例外清单同步 |
| 验证 | 评审前置 hub 门禁 PASS（pytest 1243、e2e PASS、指纹 260/260、外仓引用 245/245）；本批文档改动后 hub 门禁复跑结果见 readiness 门禁状态行 |
| 待办 | ① 待刷板项累积：TLS 复用批镜像横幅核对 + 本次 EXT_MEM 重建镜像随下次刷机顺带；② 评审遗留 minor/nit 与残留风险清单随评审会话提交信息（30 轮验收前 nginx `limit_conn` 与 paper risk 行级闸为硬项）。三仓推送已于 2026-09-07 完成：hub `90d23e0..457a86c`、xiaozhi `531f5b7..80b0336`、Grbl `8047d29..3a6ee4e` |

## 双仓评审 minor 修复批（2026-09-07，mcp-server 侧；用户「都做」第 ① 项）

| 项 | 内容 |
|---|---|
| `hutuji_mcp/write_text.py` | 三处排版测量/落版错位修复：① `token_advance_mm("\t")` 与空格同价（旧测量 5.5mm vs 落版 5.25mm，满行处折行判决与墨迹错位）；② 拉丁→CJK 边界避让 `cjk_left_overhang×scale` 计入折行测量（旧口径漏算，满行尾交界墨迹越出右边距——「1932年」修复只改了落版侧、测量侧漏网）；③ `load_font` 偏好/回落扫描直接返回已解析 `GfontFile`，消除二次全量解析 |
| `mcp-server/tests/test_contract.py` | 竖版转置直接单测 5 例（623→628）：正例 XY 成对互换且非运动行原样、fail-closed 四态拒绝矩阵（模态 X/Y、同行双 X、行内 X 后无 Y 等 → `GcodeError`） |
| `mcp-server/tests/test_write_text.py` | RED→GREEN 回归钉 2 例（43→45）：Tab/空格同文落版逐点一致 + 单行；拉丁→CJK 边界满行交界不越右边距（stash 旧码复跑双双 RED，预测失败形态逐字命中） |
| 计数同步 | AGENTS.md 1243→1250、test_contract 623→628、test_write_text 43→45；inventory 汇总行同批；`test_test_count_claims_self_consistent` RED→GREEN |
| 验证 | 全量 pytest 1250/1250；hub 门禁 PASS（docs 锚点/复活/禁语/白名单/防漂移/矩阵/行号/指纹 260/260/外仓引用 247/247/pytest/e2e 全绿）；指纹半漂移 2 处人工核对后重登（finding#28 → `AGENTS.md` 第 145-147 行计数行内容合法演进；finding#30 → `test_contract.py` 第 7451 行签名行纯移动 7421→7451） |
| 待办 | ② paper risk 行级闸 ✅（调查批+下午输入面修复：闸改吃塌缩前原文 v2.1，见下节）；③ nginx `limit_conn` ✅（双机部署，见末节）；④ HIL 准备清单 ✅（`docs/hil-checklist-0907.md`）——「都做」四项清零 |

## paper risk 行级闸调查批（2026-09-07，mcp-server 侧；用户「都做」第 ② 项——结论：行级新指标**不加**（塌缩后无信号）；**但发现 v2 输入面漂移**——闸自 08-28 地板起吃塌缩后文本致短段两项脱靶，当日下午修复为吃塌缩前原文（`paper_risk` v2.1，阈值不动），pytest 1253 绿，**当日 12:2x 双机生产已上线**（见 deployment.md 当日条；京东第 6 preflight fail = bridge 热备 recency 机械判红））

| 项 | 内容 |
|---|---|
| 结论 | 「局部画糊被全文件平均稀释逃逸」的行级**新指标**在塌缩后文本上无可抓信号，不加指标、不改阈值（上午结论，四条独立证据）；**下午顾问复核定位根因为输入面漂移并已修复**：闸自 08-28 塌缩地板起吃塌缩后文本，`short_g1_ratio`/`scribble_core` 脱靶，现改吃塌缩前 vpype 原文（v2 标定面），版本 `"2"`→`"2.1"`，同根因的 curated_gate 入库闸一并修为 v4 |
| 证据 ① 结构性 | 本闸评估塌缩后文本：`_collapse_short_pen_down_segments`（1.0mm 地板，08-28 上线）使每笔除末点 flush 外落笔段恒 ≥1.0mm——0.45mm×40 涂鸦笔塌成 13×1.35mm+0.45mm 尾（逐笔 <0.5mm 比 0.071，结构上限 1/N，≥0.8 阈值不可达）；v2 的 `short_g1_ratio`/`scribble_core` 在塌缩后文本上结构性惰性——**该「惰性」即输入面漂移的表象，下午已修复**；修复后五项指标全活（lifts/gap 的真机现行分布见 deployment.md 当日条：阈值陈旧、enforce 开启前须按 30 轮样本重标定） |
| 证据 ② 上游中和 | 最坏情形合成密集涂鸦球（6000 步随机游走 260px 盘 + 干净大圆）走真离线描摹链：实心色块转轮廓环 + 排线簇抑制后仅余 9 笔良性碎片（最大 bbox 14.5mm，15mm 窗墨长 52.7mm），密集墨迹根本到不了 G-code |
| 证据 ③ 无分离带 | 9 张 curated 矢量 + test_star 走现管线（vpype→塌缩→弧拟合）：好侧 15mm 窗墨长最高 332.7mm（violin）、逐笔长/跨比最高 5.0（test_star 凹形轮廓固有 >1）；被中和的涂鸦残留 ~53mm 远在好侧分布之下——任何不冤枉好图的阈值也抓不到残留，阈带为空 |
| 证据 ④ 纹理类有主 | 细密纹理（千级短笔）由活指标 `pen_lifts≤40` 兜住（fur 样本 3500 笔即远超上限） |
| 附带观察 | 400×400px/6px 交叉排线 PNG 的 `trace_bitmap_to_polylines`（sknw 建图）>12 分钟未出结果被杀——生产上这类图会在 draw 期限内 stall 到 DrawTimeoutError，非本批范围，登记备查 |
| 代码改动 | 上午：仅 `paper_risk.py` docstring 增段。下午（`da87440`）：`gcode_gen._read_generated_gcode` 增 `on_raw_text` 回调 + `svg_to_gcode{,_async}` 透传、`server._svg_to_gcode_cached` 返回 `(stats, 原文)` 且缓存随存原文、`_run_draw_job` 闸改吃原文；curated_gate v4（`gate_svg` `_ink_checks` 改吃原文）；stub 迁移 15 处 |
| 证据脚本 | `tmp/calib_prisk_0907.py` + `tmp/calib_prisk_out/`（9 好 + 涂鸦球 SVG/gcode，不入库） |
| 验证 | 上午批 pytest 1250 绿。下午批：新回归 4 例（paper_risk 锯齿原文必拒/终稿不拒对照、test_star 过闸、缓存命中同原文；curated_gate 密集锯齿 RED→GREEN 实证），全量 pytest 1254 绿 + hub 门禁 PASS；生产 shadow 真样本 short=0.06（0.35 阈值下健康） |
| 待办 | ③ nginx `limit_conn` ✅（见末节）；④ HIL 准备清单 ✅（见末节） |
| 待办 | ⑤ limit_conn `/draw-upload/` 放宽 50 ✅（`d68a572`，双机上线，见末节「⑤ 晚补强」）——顾问复检项清零 |
| 待办 | ⑥ paper risk v2.1 + curated_gate v4 输入面修复 ✅ 双机生产（`da87440`/`f4485e3`；shadow 模式不拒收；enforce 硬前提 = 30 轮样本重标定 lifts/gap，见 readiness 定序） |
| 登记 | HIL 日证据（gitignored 本机留存）：`results/local-only/2026-09-07/`——b1-abort-on-paper（§8.5 四判据）、c1-multipage-tls（页 2 单发下载 + 三次握手证复用未生效）、preview-blank-probe（Waveshare 白屏探针 + 预解码修复取证串口全程）；xiaozhi `0898476` 修复已推，板上现役镜像 = HEAD+0898476 |

## ⑤ limit_conn `/draw-upload/` 50 晚补强（2026-09-07 晚，本仓 `d68a572`——✅ 双机已上线）

| 项 | 内容 |
|---|---|
| 起因 | 顾问复检：server 级 20 圈进 `/draw-upload/` 同步长请求（150s AbortController + DrawLimiter 8 并发外排队在途），课堂 NAT 30+ 学生顶破 20 被 503；/files、/register 快请求维持 20 |
| 改动 | 模板 location 内 `limit_conn hutuji_conn 50;` + 注释取值依据；AGENTS.md 137 行补 per-location 口径；inventory 676 行引用改非活指针（基线重登 260） |
| 部署 | 京东三处同写 SHA 相符；阿里 gray conf `/draw-upload/` 块内插一行（+1 行 SHA 相符）；双机 `nginx -t` 绿、restart、worker 0–1s、error.log 晚于 T0 `[emerg]`=0；公网 portal 200、`/mcp` 404 |
| 脚本 | `tmp/deploy_nginx_limitconn_0907b.py`（不入库）；前备 `tmp/nginx_live_0907/`、`{BAK}/jd-b`、`{BAK}/ali-b` |

## nginx `limit_conn` 双机部署批（2026-09-07 午，本仓 `ba0d77f`；用户「都做」第 ③ 项——✅ 已上线）

| 项 | 内容 |
|---|---|
| 改动 | findings/30 R3：keepalive 1800s 后补单 IP 并发连接上限 20（`limit_conn` 只计在途请求连接）。仓内模板 `deploy/nginx-hutuji.conf` 增 `hutuji_conn` zone + 443 块指令；agent-handoff 行号引用 123→132 随插行重登 |
| 部署 | 两节点**不同源分落**：阿里云是多租户 conf.d 机（hutuji vhost = `/etc/nginx/conf.d/hutuji-gray.conf`，`$binary_remote_addr` + `hutuji_gray_*` zone），整份模板覆盖被 advisory 拦下（会替掉生产 vhost），改本地 Python 精确插两行（zone http 上下文第 3 行 + 两个 443 块 keepalive 行后各一 `limit_conn hutuji_gray_conn 20;`）；京东云 `sites-enabled/hutuji` 是普通文件非 symlink（既有差异，nginx 真读它），整份模板三处（/opt tpl + available + enabled）同写、SHA-256 与 HEAD blob 逐位相符 |
| 过程坑 | ① sed 地址 `/zone=hutuji_gray_register/` 误中 server 块内 `limit_req zone=` 使用行→zone 三插进 server 上下文（grep 计数断言拦下，未 restart）；② 转义层出错产 SOH 坏行（`nginx -t` 拦下，未 restart）——两次均从净备份 `/root/hutuji-bak-20260907-nginx-limitconn/ali/` 还原并 `nginx -t` 证绿后重做，运行中 nginx 全程未载坏配置 |
| 验证 | 双机 `nginx -t` 绿 → `systemctl restart nginx`（zone 变更 reload 静默丢弃）→ is-active + 最老 worker 存活数秒 + 近 3 分钟 `[emerg]`=0 → preflight 回基线（阿里云 24/33 九 fail 全为既有布局类、京东云 28/33 热备预期；阿里云公网 443 /files 真下与 /mcp 404 均 pass） |
| 未验证 | 21+ 并发在途连接触发 503 的行为面（需慢请求夹具）；配置级证据 + 标准 ngx_http_limit_conn_module 视为充分 |
| 文档 | deployment.md 首条 + release-readiness 当日条 + AGENTS.md 增「双节点 nginx 布局不同源」段（禁整份覆盖阿里云、sed 地址行首锚定、京东 enabled 普通文件三处同写） |
| 待办 | ④ HIL 准备清单 ✅ `docs/hil-checklist-0907.md`（A 纸面三项 / B abort+paper_active / C 设备复验 / D 速度夜场 / E 30 轮编排 / F 收尾）；「都做」四项全部清零 |

## 奎享守护 §9-H′ + TG 归档审查修复批（2026-09-12 深夜）

| 项 | 内容 |
|---|---|
| Grbl 仓 | `D:/Users/Grbl_Esp32` 新提交 `bdc234a`（基线 `3a6ee4e`）：`WebUI/StatusBeacon.{h,cpp}` 新 + `Config.h` `ENABLE_STATUS_BEACON` 宏 + `WifiServices.cpp` 三挂钩；§9-H′ 登记 protocol.md。fz standard overall pass（qemu 新镜像过；wokwi 可选层 exit 42 预置问题，首轮门禁跑的是 09-06 旧 bin 与本改动无关，重编后已定性）。工作树余 `.gitignore` 用户既有改动（不碰） |
| 本仓 | `a8e62e4`：draw-portal TG 归档审查修复①–④——台账行存在守卫（INSERT…SELECT…EXISTS 单语句原子）、启动 pending 重入队（app.py create_app 内）、CLI `--retry-user`、`_download_document` 下载前删 dest + returncode 校验、PoC 文档 env 名勘误（TG_BOT_TOKEN/TG_CHANNEL_ID/TG_PROXY）+ `deploy/env.draw-portal.example` 归档块；测试 +6 项（竞态三路径/守卫/mark bool/重入队/retry），portal 208/208 |
| 部署 | lima `/opt/hutuji/draw-portal/draw_portal/` 3 文件 HEAD blob LF 导出 SHA-256 逐位相符（app `f2469834…`、store `57538e22…`、telegram_archive `9dab37ee…`），py_compile 过、restart active、`/draw-upload/` 200 |
| S3 侧 | xiaozhi-esp32（基线 `d0d2c8d`）新 `hutuji_kdraw_watcher.{h,cc}` + CMakeLists/board 接线：UDP :2325 收 §9-H′ beacon，Run→`Idle && !Changing` ≥2.5s 判完成 → Alert 屏显+提示音 + MCP notifications/message + `POST /api/device/kdraw/done`；lichuang-dev release 构建过 `d26f52d`（`releases/vhutuji.1.0.10_lichuang-dev.zip` 2799829B，bin sha `C2367ECF…`、214 CXX 全量实编；假绿坑连踩三次——zip 残留致 skip，改名后重编实证） |
| 云端层 | 本仓 `f71d347`：portal `POST /api/device/kdraw/done`（§10.4.17）+ `subscribe_notify` phrase 参数；lima 部署 app `6fbd2028…`/subscribe_notify `91807448…` 逐位相符、坏 MAC 实测 400 |
| 粘合性批 | 本仓粘合性提交：`milestones.py` 纯函数 + `engagement_events` 幂等表 + `_notify_subscribe` 成功事件钩子（里程碑 1/10/50/100/500 + 打卡 3/7/30 天，§10.4.18）；portal 223/223（+9）；白名单 decision_count 9；lima+jd 双节点部署（app `84e45b81…`/store `952109ae…`/milestones `31bcc3d9…`；jd 曾缺 TG 批文件启动失败，补齐后 reset-failed 恢复，健康 200） |

## 量产机「小程序控不动」定案修复批（2026-09-12 晚；固件/云端零代码变更，机器参数 $110/$111=12000 落机）

| 项 | 内容 |
|---|---|
| 本仓 | 零 tracked 变更；deployment.md 首条新增当日晚条目 |
| 机器侧 | COM3（量产无换纸机 ESP32-D0WD-V3，固件 1.3a.20260910 量产分支 6221502）：串口写 `$110=12000`/`$111=12000` 回读核对、`[ESP444]RESTART` 重启 ×2、`$$` 复查；COM15 esptool `read_mac --after hard-reset` 复位 S3 ×2 |
| 证据（tmp，不入库） | `tmp/com15-wiretap2.log`（窗口内仅 Idle 心跳——设备侧工具执行不进串口日志）、`tmp/com15-bootprobe.log`（断连→重连→版本/授权/指纹探针全日志，指纹失配 `（$110）` 实证）、`tmp/com15-wiretap-20260912.log`（首窗口） |
| 排障链 | nginx access_log（hutuji-gray）→ portal journal（`/api/device/control` 200 波）→ 生产文件 token 直推 `hutuji.status`（通）→ 推 `hutuji.manual`（`grbl_settings_mismatch`:"110"）→ 源码 `hutuji_nopaper_core.h`/`hutuji_pipe.cc` 指纹闸定位 → 24000 失配/12000 过检二分 → 端到端 jog 实动验证 |

## 提速批 bench 落机（2026-09-12 晚；freenove S3 1.0.12 + 量产机 $110/$111=24000，全链探针过检）

| 项 | 内容 |
|---|---|
| 本仓 | 零 tracked 变更；deployment.md 首条新增晚条目 + readiness 提速批条目 bench 闭环注记 |
| S3 侧 | xiaozhi HEAD `9c4a218`（金标 24000 + `PROJECT_VER=hutuji.1.0.12`）freenove 目标真构建：host 测试 RC=0、215 CXX 增量、`xiaozhi.bin` SHA `64ed127f…5cc10` 3,038,992 B、zip `vhutuji.1.0.12_freenove-esp32s3-display-2.8-lcd.zip`；COM15 只写 app@0x20000+otadata@0xd000（NVS 保留身份批 P-256），`Hash of data verified` ×2（exit=1 装饰性既定）；1.0.11 原件备份 `results/20260912-device-identity/freenove-esp32s3-display-2.8-lcd-xiaozhi.identity-1011.bak`（SHA `17ae2921…`） |
| 机器侧 | COM3 写 `$110/$111=24000` + `$$` 回读逐项相符（RATE-VERIFIED）；COM15 esptool 复位重探后 `hutuji.status` 文件 token 直推 → `firmware_version:hutuji.1.0.12`、`grbl_settings_ok:true`、Idle 0,0,0；`hutuji.manual jog_x+` → `MPos:10.000` 实动 → `G1 G90 X0 F8000` 归零 |
| 证据（tmp，不入库） | `tmp/build_speed_freenove.cmd`（构建封装）、`results/20260912-device-identity/freenove-esp32s3-display-2.8-lcd-build-20260912-205556-418916.log`（构建日志）、`tmp/flash-speed-1012-com15.ps1` + `tmp/flash-speed-1012-com15.log`（刷机）、`tmp/write_rate_serial.py`（参数写入器）、`tmp/idf-export-speed.log` |
| 未验证 | 真实画图任务 400mm/s 纸面效果（待用户实画）；fleet MK38 到场刷机（2026-09-12 深夜串分支事故收口后：fleet/出货 bin = massprod/nopaper-1.3a@`6221502` 程序 `1f9d30d1…` 1843216B DIO 直出——与量产机烧录前现役 bit-exact，量产机已重刷 SETTINGS-VERIFIED + S3 判「无换纸 SKU」+ 金表 12 项过检；fix 分支构建 `83f44b3f…`/`7e73b32a…` 串分支作废，详见 deployment.md 深夜首条；2026-09-12 用户确认全部机器为不换纸机——nopaper 金表适用，paper 金表无 fleet 对象） |


## 2026-09-13 小程序后续审查与真实客服（本批）

- 授权：用户继续代码审查，要求隐私日期与客服不能虚假；公开邮箱17150303974@163.com，接待微信号17150303974；后台“朱光”管理员出现后用户确认本人、收发尚未验证。
- 本批枢纽代码：portal app/firmware_ota、chat/firmware/profile 页面、版本0.3.14及对应回归；文档为protocol、包README/隐私核对稿/冒烟清单、readiness与`docs/design/weapp-followup-review-20260913.md`。两个固件仓未改。原有公众号决策稿与其工作树记录保留。
- 本地证据：`results/20260913-followup-review/`，包含前段红例、369项portal与134项Node回归、21份语法检查、后台预览和只读查询；本轮补强正常语音回包MAC断言后再次完整通过。前段hub因对话上报旧行号指纹失败，按端点实际范围修正后待最终复验。
- 平台：十类信息的实际用途与邮箱已填入，预览日期2026-09-13，设置页审核中；不能据此宣称用户端已生效。客服已绑定本人，真实消息收发待验证；通用隐私OpenAPI返回48001，采用原生后台证据。
- 交付：后端双机部署、0.3.14候选上传与最终门禁由本轮继续回填。辅助脚本与测试目录仅留tmp/results，不提交。

## weapp 0.3.20 登录合规批 + 三仓审计落盘（2026-09-24 晚）

| 项 | 内容 |
|---|---|
| 本仓 | `d18d374`：weapp 软闸合规批 14 文件 +97/-29（api.js `_reject401` 去 logged_out 强跳、phone_gate.ensurePhone 三态、8 页 onShow 软闸、version 0.3.20、session/market 测试）；0.3.20 CLI 上传 → 用户手动三击提审（18:15:52）→ **同日过审发布，线上版本 0.3.20、发布时间 18:27:45**（MP 页复核，截图 `D:\Temp\User\mp-0320-online-20260924.jpg`）。readiness 首条已改「已发布上线」 |
| 审计落盘 | `results/tri-repo-audit-20260924.md`：四审计 agent findings 汇总（Portal 4 高：OTA 无 TTL 锁死 / `/api/voice/asr` 无上限读内存 / 小派绑定码爆破 / voice 会话竞态；MCP 高 1：breaker redis 无超时冻结事件循环；Grbl 4 项待 nopaper 语义复核；xiaozhi 3 项中低）。排期建议：Portal 4 高为下一刀。原始 payload 随会话失效，以该文件为准 |
| 证据（tmp，不入库） | `D:\Temp\User\omp-sshots-158c082893dae8c4.jpg`（MP 0.3.20 审核中服务端复核截图）、`D:\Temp\User\weapp-0319-diff.txt`（审查用 diff 快照，已过期于最终提交） |
| 待办 | ① 0.3.20 线上版本用户实机体验回归（退出登录后浏览创作/图库/历史不被弹走，用户目视）；② 三仓审计 findings 排期修复（Portal 4 高优先）；③ AuditPortal.WeappAudit 前端审查缺口（子代理被取消，control/firmware/history 等页深度审查未做） |
