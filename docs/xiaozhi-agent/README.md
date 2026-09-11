# 小智云智能体配置（让呼图机更聪明）

> 目标：小智云负责「听懂 + 选工具」；绘图质量由阿里云主节点 MCP（京东云热备）+ 当前生产图像/精选库路线决定，图片链路包含 curated SVG 或 PNG 中心线描摹（禁止模型直写 SVG）。
> 本目录是**可粘贴到控制台**的文稿，不含密钥。  
> **进度（2026-07-26）**：全部通过脚本自动同步完成。模型 DeepSeek V4；角色提示含 few-shot + 智能补全策略（2459 字符）；知识库含硬件规格/故障排查/创意玩法/对话风格；助手自称已改为「呼图机」。S3 屏幕 UI 文案已定制（待构建烧录）。
> **2026-07-31 事实对齐**：role-prompt 与 knowledge-base 已补「出图两步衔接」（云端 `hutuji_draw` 只返 url，须再调设备 `hutuji.draw`(url) 才落笔；原 KB「自动衔接」为假）、暂停超 10 分钟自动取消、可画区 190×277、断连从开头重画。**本批只改本仓文稿**；云端生效须用户在控制台重取 JWT 后跑 `python scripts/xiaozhi_console.py sync 2094251`（旧 token 已 401 失效，见 `release-readiness.md` #10），sync 前云端仍是旧文稿。
> **2026-08-07 云端已真同步（本条 supersede 上条「sync 前云端仍是旧文稿」）**：用户重取 JWT 后跑 `sync 2094251` 成功——云端 `character` 与仓库 `role-prompt.md` 逐字节一致（2443 字符，md5 `d1ef811fba1c`），KB 换成新文档 `65569`（旧 `63334` 已删），召回测三问各 5 命中并返回新 few-shot 内容。**过程暴露一个此前从未触发的硬限**：云端 `character` 上限 **2000 tokens**，超限 `POST /agents/{id}/config` 返回 400 `CHARACTER_LENGTH_LIMIT`；07-31 那批修正把 role-prompt 写到 3675 字符，**当时 token 已 401，从未真正试过同步，故该限制一直没暴露**。现按「行为规则留角色提示、参考材料下沉知识库」重排：few-shot 对话示例（1085 字符）移入 `knowledge-base.md`（KB 无 token 限制），三条行为规则（两步衔接 / 暂停超 10 分钟自动取消 / 断连从开头重画）全部保留在角色提示内。**改 `role-prompt.md` 前先算上传字符数**（`cmd_update_role` 只剥第一行 `# ` 标题，其余全部上传），2443 字符可过、3675 必拒。
> **2026-08-07 设备 MCP 官方消息入口补查**：此前只探测 11 个猜测路由并据其 404 断言「PC 无设备工具入口」，该全称结论错误。上游维护者在 `78/xiaozhi-esp32#1956` 给出官方入口 `POST /api/messaging/device/tools/list` 与 `POST /api/messaging/device/tools/call`，并指向 `78/xiaozhi-assets-generator` 的现役调用代码；本机无认证请求已得到 `No token provided`，证明路由存在。该入口使用设备消息 token（官方示例从自定义主题 URL 取得），当前终端未持有，故认证后的七工具列表与 `hutuji.draw` 调用仍待验证；不要再用那 11 个 404 推导「不存在入口」。
> **2026-08-07 直调客户端已落地**：`scripts/xiaozhi_console.py` 新增 `device-tools` / `device-call`，使用独立 `XIAOZHI_DEVICE_TOKEN`，只允许白名单内 `hutuji.*` 工具（当时七个，现含 `confirm`/`sing`/`stop_song` 共九个；2026-08-23 审计发现 `hutuji.draw` 白名单仍缺 `preview_url`，当前直调会被设备拒），严格校验参数并脱敏能力 URL、Bearer、token 与 API key。定向 9 项通过；完整 hub `339 passed` + e2e PASS；假 token 在线探针准确返回 401 `Invalid token`。真实 token 的 `tools/list` 与运动调用仍未验证。
> **2026-08-08 知识库契约纠错（仅本地）**：`knowledge-base.md` 原称坐标越界会被云端裁掉、画面会自动适配纸张，且没有把「漏调设备 `hutuji.draw(url)`」列入纸不动原因，换纸停止语义也不完整；现已对齐 `protocol.md`：越界整幅拒绝并说明原因，不裁剪、不静默缩放；生成成功后必须执行设备侧第二步；换纸中停止请求要等当前换纸完成后生效。云端现役 KB 文档 `65569` 尚未同步这组纠错；当前进程/User/Machine 三个环境作用域均无 `XIAOZHI_TOKEN`，取得控制台 JWT 后须重新执行 `python scripts/xiaozhi_console.py sync 2094251` 并复核召回结果。
> **2026-08-08 慢 MCP 根因确认（只读社区复核 + 生产实测）**：小智维护者在 `78/xiaozhi-esp32#964` 明确说明官方云不支持 10 秒级慢 MCP，请求其他大模型应走异步 A2A；当前尚不支持 A2A，建议 MCP 先快速返回“进行中”，后续再查状态。`#1011` 还记录了同轮多设备工具会概率漏调，故角色提示不能保证第二步。生产同提示实测：`agnes-2.0-flash` 2/2 在三轮 15 秒尝试后失败；`agnes-2.5-flash` 首个新请求 39.50 秒，完全相同提示的缓存命中才是 0.30 秒。现有证据支持“生成成功但纸不动”主因是长调用越过云端实时窗口，而非 S3/Grbl 不在线。临时验收可用官方设备消息 API 直调；产品级修复需异步生成完成后再通过该 API 调设备 `hutuji.draw`，仍待设备消息 token 与 MCP 代码改动授权。
> **2026-08-09 08-08 那组纠错已真同步上云（本条 supersede 上条「云端现役 KB 文档 `65569` 尚未同步这组纠错」）**：用户续期控制台 JWT 后 `GET /api/agents` 回 200（智能体 `2094251`，设备 1/1 在线，`knowledge_base_ids=[71153]`），`sync 2094251` 成功。**同步前**云端现役 KB 文档时间戳仍是 `2026-08-07 11:32:37 +0800`，早于 `ee2ba4e`（强调两步衔接的 few-shot）与 `ef67df9`（换纸 ≥60 秒/90 秒上限、越界整幅拒绝、「纸不动」排查顺序），即 08-08 那组纠错确实一直没上云。**同步后**三问召回逐条命中标记：越界 → `拒绝整幅` HIT、`不会裁剪` HIT；纸不动 → `漏调` HIT、`hutuji.draw` HIT；换纸停止 → `60 秒`、`90 秒`、`等当前换纸完成` 全 HIT。`cmd_update_kb` 是**先上传新文档、成功后才删旧文档**，故同步失败不会清空云端 KB；也因为「删旧上新」，文档 id 每次都变（`63334` → `65569` → 本轮新号，**本轮未单独留证新号**），而 `71153` 是**知识库容器** id（`kb_id`，`update-kb`/`recall` 的参数），二者勿混。另记一次**未定因异常**：首轮 `recall` 三问全回 `401 Invalid token`，紧随其后的 `list` 回 200、重跑 `recall` 即成功 → 不是 token 失效，疑为新建文档后 `recall-test` 的瞬时拒绝，**未确认**。本条只证云端文案与仓库一致且可召回，**不证** App 真实语音两步衔接（仍缺证据），**不证**纸面画质。
> **2026-08-09 笔架归位提示已同步上云**：`role-prompt.md` 新增「开机后第一次出图前：提醒推笔架」（推到左下角机械死点定 XY 原点，连续出图不重复提醒，Z 机器自校准），`knowledge-base.md` 新增「出图前准备（笔架归位）」节。`sync 2094251` 成功（role-prompt 2604 字符，低于 2000 tokens 上限；KB 先上传新文档成功后再删旧 `65972`）。召回验证：「笔架要推到哪里」→ 新条目**第 1 命中**（distance 0.626）。依据 `findings/03`：无限位 `$H` 用不了，奎享同款「手动推死点归 XY 零」。
> **2026-08-09 KB 扩写已同步上云**：新增边界告知四条（生成耗时 10~40s、同主题两次不一样、自动居中留边、「太复杂/生成失败」两种形态）与故障排查三条（画偏→推笔架、与想象不符→AI 理解偏差、生成失败→稍后重试+`hutuji_status`），并把「文字书法」措辞从「折线笔画风格」改为「单线书法风格；福字实测稳定」（位图路线证据）。`sync 2094251` 成功（KB 旧文档 `66029` 已替换）；召回验证：「说完画之后要等多久」→ 新耗时条目**第 1 命中**，「画偏了怎么办」→ 两条新条目分居第 1/3 命中。耗时数字依据：生产真 draw 实测 23.4s（位图路线）与 39.87s（LLM 路线含重试）。
> **2026-08-23 全链审计修订（仅本地文稿，云端须重新 sync）**：`hutuji.draw` 双参数（`url`+`preview_url`，缺一不可）与「预览 → `hutuji.confirm` 才落笔」链路已按 xiaozhi `72a693a` 设备实现重写 `role-prompt.md`/`knowledge-base.md`；此前「只传 url 即落笔」的表述会培养出不会确认、也不会传预览地址的助手。**当前终端无控制台 JWT，云端仍为旧文稿；取得 token 后须执行 `sync 2094251` 并复核召回**。另审计登记：`scripts/xiaozhi_console.py` 白名单缺 `preview_url`（下条「七个工具」计数也已过时，实际九个），待用户批准独立批次修复。
> **2026-08-28 知识库增补（仅本地文稿，云端须重新 sync）**：`knowledge-base.md` 新增「进阶专题」6 条（速度与画质档、导入 .gcode/word/pdf 与批量出图含安全注意、方向镜像根因、wordx 换机、共享机器/记住密码安全、软件升级），素材来自 `docs/kxnx/findings/` 13/22/24/25/26 的奎享 3.9.9 产品行为实证，覆盖《售后知识点.xlsx》44 条未答的问题；附注来源边界与「奎享专属」索引同步更新。同日另逐条核对用户持有的 xlsx（序号 1–45 跳 16，共 44 条）与入库版完全一致，无新增内容。**云端 KB 仍停在 08-22 版**（08-23/08-24 修订 + 本批均未上云），取得控制台 JWT 后执行 `sync 2094251` 并做召回验证。
> **2026-08-28 三批积压已全部上云（本条 supersede #15/#16 的「云端须重新 sync」）**：经 OMP Browser Relay 从用户已登录控制台标签页直读 JWT（免 F12 复制；token 不落盘不入库；该枚 08-09 签发、11-07 到期仍有效）执行 `sync 2094251`：role-prompt 2017 字符更新成功，KB 新文档建立（md5 `098ff6629f90…`，旧 `69214` 已删）。召回验证 6/6：画一只猫→few-shot；能画彩色吗→单色条；没纸了怎么办→缺纸条（08-24 批）；画得太淡→试笔条（08-24 批）；**画得太慢怎么调速度→进阶专题第 1 命中**（08-28 批）；画出来是反的→方向根因条（08-28 批）。**relay 取 token 路径自此可用**：`omp browser-relay install` 已装、`browser.relay=true` 已配，扩展在 `C:\\Users\\zhugu\\.omp\\browser-relay\\extension`。
> **2026-08-28 二轮 xlsx 逐条复核纠错（仅本地文稿，云端须重新 sync）**：上条 #16 的「逐条核对用户持有的 xlsx…与入库版完全一致，无新增内容」**不成立**。本轮以 xlsx 规范化转储（`tmp/xlsx_norm.txt`）为真值，机器 token/短语扫描 + 逐条人工复核 44 条官方答复：20 条相符、24 条有缺陷——15 条缺失官方处置步骤/口径（8 条全缺：#9 基准线 100+更新、#17 word 纯文字限制、#24 btwrite、#26 齿轮入口、#27 先点停止、#29 自动跳页语义、#42 改名拼音、#43 相反数/固件第三项；7 条官方多步只保留部分：#4 混合行数两注意点、#5 优先级三用法、#15 com1/驱动/杀毒、#22 别点三按钮+断电 10 分钟、#28 仅文字模式、#31 精简内容、#33 打印背景+打印机缩放），4 条入库版答案与官方答复不相干（#30 拍照识别表格、#37 模板输内容、#38 杀毒/数据线、#39 云备份录字），5 条与官方口径相悖（#3 行间隔根因、#20 字体很小三查、#23 「可走网络连」、#41 只能换电脑/重装系统、#45 国产系统三层解法被写成 Windows 解法），15+4+5=24。已全部在 `knowledge-base.md` 对应条目以「官方口径/官方答复（手册）」标记补正，来源边界附注同步登记；#14/#43 的固件参数试法按链路差异保留并加注呼图机警示。逐条明细见 `results/kb-xlsx-fidelity-audit.md`（本机证据）。**云端 KB 是 08-28 上午版（md5 `098ff6629f90…`），本轮 24 条补正未上云**，取得控制台 JWT 后执行 `sync 2094251` 并做召回验证。

> **2026-08-29 小派提示词与二轮 FAQ 修订已同步上云（本条 supersede #18）**：按云端 `accepted`/同步回落两种返回及设备 `awaiting_confirmation` 契约，更新 `role-prompt.md`：异步受理不重复调 `hutuji.draw`/`hutuji.confirm`，预览前取消不虚报，`last_draw_error` 不单独触发重画，`hutuji.manual` 仅按用户主动要求且设备空闲时使用；新增奎享软件与呼图机语音链路分流。经 OMP Browser Relay 从已登录控制台标签页直读 JWT 执行 `sync 2094251` 成功：role-prompt 2559 字符，KB 新文档 md5 `2ec8e5afad57…`，旧文档 `69856` 已删。召回验证 3/3：`调行间隔`、`国产系统安装后找不到图标怎么办`、`写字机能不能网络连接` 均返回 5 条且目标问答进入第 1 命中；网络问同时召回两条链路差异说明。token 未落盘。
> **2026-09-12 设备改名「丹青」+ 无换纸口径批（仅本地文稿，云端须重新 sync）**：用户口径量产机没有换纸功能（massprod 分支，S3 页尾 M30 整段跳过、多页拒绝），小程序文案已改名「丹青」。本轮：① `role-prompt.md` 自称「自动换纸写字机（呼图机）」→「丹青 AI 绘图机」，删换纸播报条，写字链改「默认自动排成一页，一页放不下如实告知（不支持连写多页）」，链路分流呼图机→丹青；② `knowledge-base.md` 呼图机半区全部自动换纸承诺改「画完自动停笔、手动取纸继续」（没纸/repeat/正在停/连续画/硬件规格/纸怎么放/儿童安全/画完不回起点共 10 处），可画区 190×190→190×277（X≤277 沿长边，2026-09-04 定案），呼图机半区「写字机」统一改称丹青、奎享厂商问答半区不动（P100-自动换纸型是厂商产品线事实）；③ mcp `chat.py` 与 portal `voice_agent.py` 两处 SYSTEM_PROMPT 同源同步（同源裁剪纪律），portal 绑定/推送话术与网页「写字机正在出图」一并改丹青；④ `test_contract.py` 旧「换纸时序」契约测试改钉新真值（KB 不得承诺自动换纸、必须反向声明）改名 `test_xiaozhi_knowledge_base_has_no_auto_paper_change_claim`。云端角色提示/KB 均未同步，取得 JWT 后执行 `sync 2094251` 并召回验证。
## 当前配置快照

| 项 | 值 |
|----|-----|
| 智能体 ID | 2094251 |
| 知识库 ID | 71153 |
| 对话模型 | DeepSeek V4（性格丰富） |
| 助手自称 | 丹青（内部代号呼图机/hutuji；2026-09-12 前=呼图机） |
| 语音 | zh_female_gaolengyujie_moon_bigtts |
| 记忆 | SHORT_TERM（控制台可手动改 LONG_TERM） |
| MCP 端点 | 已关联（hutuji_draw / hutuji_status / hutuji_preview_svg） |

## 控制台手动步骤（仅首次或脚本不可用时）

1. 打开 [xiaozhi.me 控制台](https://xiaozhi.me/) → 对应智能体 → **配置角色**。  
2. 将 [`role-prompt.md`](./role-prompt.md) 全文粘贴进角色/系统提示 → 保存。  
3. **知识库**：将 [`knowledge-base.md`](./knowledge-base.md) 上传 → 智能体页确认 **已关联** → 召回测：`画一只猫` / `能画彩色吗` / `纸卡住了`。  
4. **MCP 接入点**：✅ 已写入京东云并启桥接。  
5. **模型与记忆**：选 DeepSeek V4；记忆可开 LONG_TERM（API 暂不支持改此项）。  
6. 语音试一句：「画一只小猫」→ 应调云端 `hutuji_draw`，AI 回应类似「好嘞，我来画一只小猫！」

## 脚本自动化（推荐）

修改 `role-prompt.md` 或 `knowledge-base.md` 后，一条命令同步到小智云：

```bash
# 1. 获取 token（一次性）：浏览器控制台输入 localStorage.getItem('token')
set XIAOZHI_TOKEN=eyJhbG...

# 2. 一键同步（角色提示 + 知识库文档替换）
python scripts/xiaozhi_console.py sync 2094251

# 也可单独操作
python scripts/xiaozhi_console.py update-role 2094251   # 仅角色提示
python scripts/xiaozhi_console.py update-kb 71153       # 仅知识库
python scripts/xiaozhi_console.py recall 71153 "画一只猫"  # 召回测试

# 3. 设备消息 token 来自设备“自定义主题”URL 的 token 参数，不是上面的控制台 JWT
$env:XIAOZHI_DEVICE_TOKEN='...'
python scripts/xiaozhi_console.py device-tools
python scripts/xiaozhi_console.py device-call hutuji.status
python scripts/xiaozhi_console.py device-call hutuji.draw '{"url":"https://hutuji.donglicao.com/files/<capability>.gcode","preview_url":"https://hutuji.donglicao.com/files/<capability>.png"}'  # 注意：CLI 白名单缺 preview_url，修复前直调会报参数错（2026-08-23 审计登记）
```

详见 `scripts/xiaozhi_console.py`。

## 两层脑（勿混）

| 层 | 谁 | 变聪明靠什么 |
|----|-----|--------------|
| 对话/意图 | 小智云（DeepSeek V4） | 角色提示 + 知识库 + few-shot 对话示例 |
| 出图 | 阿里云主节点 MCP `hutuji_draw`（京东云热备） | 当前生产图片链路 + `SYSTEM_PROMPT`；异步完成后有设备消息 token 时由服务端自动推送预览 |

## 验收话术

| 你说 | 期望行为 | 期望回话风格 |
|------|----------|-------------|
| 你好 | 自我介绍 | 「我是小派，你的 AI 绘图小助手！告诉我想画什么~」 |
| 画一只小猫 | 调 `hutuji_draw`，description 补全为线描风格 | 「好嘞，我来画一只小猫！」 |
| 画朵向日葵 | 调 `hutuji_draw` | 「没问题，向日葵来啦！」 |
| 画个写实油画 | 拒绝或改写成线描 | 「写实油画我画不了，但线条风格的也很好看哦！」 |
| 丹青连上了吗 | 调设备 `hutuji.status` | 白话报告连接状态 |
| 别画了 / 停下 | 调 `hutuji.abort` | 诚实说明中止后这一幅不会续画 |
| 能画彩色吗 | 知识库召回回答 | 「目前只支持单色线条，换个笔芯可以换颜色~」 |
| 再来一张 | 调 `hutuji_draw`（新 description 或同主题变体） | 「好，再来一张！」 |
