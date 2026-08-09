# 小智云智能体配置（让呼图机更聪明）

> 目标：小智云负责「听懂 + 选工具」；绘图质量由京东云 MCP + **Agnes `agnes-2.5-flash`**（现行；llm7 为历史配置）决定。
> 本目录是**可粘贴到控制台**的文稿，不含密钥。  
> **进度（2026-07-26）**：全部通过脚本自动同步完成。模型 DeepSeek V4；角色提示含 few-shot + 智能补全策略（2459 字符）；知识库含硬件规格/故障排查/创意玩法/对话风格；助手自称已改为「呼图机」。S3 屏幕 UI 文案已定制（待构建烧录）。
> **2026-07-31 事实对齐**：role-prompt 与 knowledge-base 已补「出图两步衔接」（云端 `hutuji_draw` 只返 url，须再调设备 `hutuji.draw`(url) 才落笔；原 KB「自动衔接」为假）、暂停超 10 分钟自动取消、可画区 190×277、断连从开头重画。**本批只改本仓文稿**；云端生效须用户在控制台重取 JWT 后跑 `python scripts/xiaozhi_console.py sync 2094251`（旧 token 已 401 失效，见 `release-readiness.md` #10），sync 前云端仍是旧文稿。
> **2026-08-07 云端已真同步（本条 supersede 上条「sync 前云端仍是旧文稿」）**：用户重取 JWT 后跑 `sync 2094251` 成功——云端 `character` 与仓库 `role-prompt.md` 逐字节一致（2443 字符，md5 `d1ef811fba1c`），KB 换成新文档 `65569`（旧 `63334` 已删），召回测三问各 5 命中并返回新 few-shot 内容。**过程暴露一个此前从未触发的硬限**：云端 `character` 上限 **2000 tokens**，超限 `POST /agents/{id}/config` 返回 400 `CHARACTER_LENGTH_LIMIT`；07-31 那批修正把 role-prompt 写到 3675 字符，**当时 token 已 401，从未真正试过同步，故该限制一直没暴露**。现按「行为规则留角色提示、参考材料下沉知识库」重排：few-shot 对话示例（1085 字符）移入 `knowledge-base.md`（KB 无 token 限制），三条行为规则（两步衔接 / 暂停超 10 分钟自动取消 / 断连从开头重画）全部保留在角色提示内。**改 `role-prompt.md` 前先算上传字符数**（`cmd_update_role` 只剥第一行 `# ` 标题，其余全部上传），2443 字符可过、3675 必拒。
> **2026-08-07 设备 MCP 官方消息入口补查**：此前只探测 11 个猜测路由并据其 404 断言「PC 无设备工具入口」，该全称结论错误。上游维护者在 `78/xiaozhi-esp32#1956` 给出官方入口 `POST /api/messaging/device/tools/list` 与 `POST /api/messaging/device/tools/call`，并指向 `78/xiaozhi-assets-generator` 的现役调用代码；本机无认证请求已得到 `No token provided`，证明路由存在。该入口使用设备消息 token（官方示例从自定义主题 URL 取得），当前终端未持有，故认证后的七工具列表与 `hutuji.draw` 调用仍待验证；不要再用那 11 个 404 推导「不存在入口」。
> **2026-08-07 直调客户端已落地**：`scripts/xiaozhi_console.py` 新增 `device-tools` / `device-call`，使用独立 `XIAOZHI_DEVICE_TOKEN`，只允许七个 `hutuji.*` 工具，严格校验参数并脱敏能力 URL、Bearer、token 与 API key。定向 9 项通过；完整 hub `339 passed` + e2e PASS；假 token 在线探针准确返回 401 `Invalid token`。真实 token 的 `tools/list` 与运动调用仍未验证。
> **2026-08-08 知识库契约纠错（仅本地）**：`knowledge-base.md` 原称坐标越界会被云端裁掉、画面会自动适配纸张，且没有把「漏调设备 `hutuji.draw(url)`」列入纸不动原因，换纸停止语义也不完整；现已对齐 `protocol.md`：越界整幅拒绝并说明原因，不裁剪、不静默缩放；生成成功后必须执行设备侧第二步；换纸中停止请求要等当前换纸完成后生效。云端现役 KB 文档 `65569` 尚未同步这组纠错；当前进程/User/Machine 三个环境作用域均无 `XIAOZHI_TOKEN`，取得控制台 JWT 后须重新执行 `python scripts/xiaozhi_console.py sync 2094251` 并复核召回结果。
> **2026-08-08 慢 MCP 根因确认（只读社区复核 + 生产实测）**：小智维护者在 `78/xiaozhi-esp32#964` 明确说明官方云不支持 10 秒级慢 MCP，请求其他大模型应走异步 A2A；当前尚不支持 A2A，建议 MCP 先快速返回“进行中”，后续再查状态。`#1011` 还记录了同轮多设备工具会概率漏调，故角色提示不能保证第二步。生产同提示实测：`agnes-2.0-flash` 2/2 在三轮 15 秒尝试后失败；`agnes-2.5-flash` 首个新请求 39.50 秒，完全相同提示的缓存命中才是 0.30 秒。现有证据支持“生成成功但纸不动”主因是长调用越过云端实时窗口，而非 S3/Grbl 不在线。临时验收可用官方设备消息 API 直调；产品级修复需异步生成完成后再通过该 API 调设备 `hutuji.draw`，仍待设备消息 token 与 MCP 代码改动授权。
> **2026-08-09 08-08 那组纠错已真同步上云（本条 supersede 上条「云端现役 KB 文档 `65569` 尚未同步这组纠错」）**：用户续期控制台 JWT 后 `GET /api/agents` 回 200（智能体 `2094251`，设备 1/1 在线，`knowledge_base_ids=[71153]`），`sync 2094251` 成功。**同步前**云端现役 KB 文档时间戳仍是 `2026-08-07 11:32:37 +0800`，早于 `ee2ba4e`（强调两步衔接的 few-shot）与 `ef67df9`（换纸 ≥60 秒/90 秒上限、越界整幅拒绝、「纸不动」排查顺序），即 08-08 那组纠错确实一直没上云。**同步后**三问召回逐条命中标记：越界 → `拒绝整幅` HIT、`不会裁剪` HIT；纸不动 → `漏调` HIT、`hutuji.draw` HIT；换纸停止 → `60 秒`、`90 秒`、`等当前换纸完成` 全 HIT。`cmd_update_kb` 是**先上传新文档、成功后才删旧文档**，故同步失败不会清空云端 KB；也因为「删旧上新」，文档 id 每次都变（`63334` → `65569` → 本轮新号，**本轮未单独留证新号**），而 `71153` 是**知识库容器** id（`kb_id`，`update-kb`/`recall` 的参数），二者勿混。另记一次**未定因异常**：首轮 `recall` 三问全回 `401 Invalid token`，紧随其后的 `list` 回 200、重跑 `recall` 即成功 → 不是 token 失效，疑为新建文档后 `recall-test` 的瞬时拒绝，**未确认**。本条只证云端文案与仓库一致且可召回，**不证** App 真实语音两步衔接（仍缺证据），**不证**纸面画质。
> **2026-08-09 笔架归位提示已同步上云**：`role-prompt.md` 新增「开机后第一次出图前：提醒推笔架」（推到左下角机械死点定 XY 原点，连续出图不重复提醒，Z 机器自校准），`knowledge-base.md` 新增「出图前准备（笔架归位）」节。`sync 2094251` 成功（role-prompt 2604 字符，低于 2000 tokens 上限；KB 先上传新文档成功后再删旧 `65972`）。召回验证：「笔架要推到哪里」→ 新条目**第 1 命中**（distance 0.626）。依据 `findings/03`：无限位 `$H` 用不了，奎享同款「手动推死点归 XY 零」。

## 当前配置快照

| 项 | 值 |
|----|-----|
| 智能体 ID | 2094251 |
| 知识库 ID | 71153 |
| 对话模型 | DeepSeek V4（性格丰富） |
| 助手自称 | 呼图机 |
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
python scripts/xiaozhi_console.py device-call hutuji.draw '{"url":"https://hutuji.donglicao.com/files/<capability>.gcode"}'
```

详见 `scripts/xiaozhi_console.py`。

## 两层脑（勿混）

| 层 | 谁 | 变聪明靠什么 |
|----|-----|--------------|
| 对话/意图 | 小智云（DeepSeek V4） | 角色提示 + 知识库 + few-shot 对话示例 |
| 出图 | 京东云 MCP `llm_svg.py` | `SYSTEM_PROMPT` + Agnes `agnes-2.5-flash`（现行；llm7 为历史配置） |

## 验收话术

| 你说 | 期望行为 | 期望回话风格 |
|------|----------|-------------|
| 你好 | 自我介绍 | 「我是呼图机，你的 AI 绘图小助手！告诉我想画什么~」 |
| 画一只小猫 | 调 `hutuji_draw`，description 补全为线描风格 | 「好嘞，我来画一只小猫！」 |
| 画朵向日葵 | 调 `hutuji_draw` | 「没问题，向日葵来啦！」 |
| 画个写实油画 | 拒绝或改写成线描 | 「写实油画我画不了，但线条风格的也很好看哦！」 |
| 写字机连上了吗 | 调设备 `hutuji.status` | 白话报告连接状态 |
| 别画了 / 停下 | 调 `hutuji.abort` | 诚实说明换纸中可能停不了 |
| 能画彩色吗 | 知识库召回回答 | 「目前只支持单色线条，换个笔芯可以换颜色~」 |
| 再来一张 | 调 `hutuji_draw`（新 description 或同主题变体） | 「好，再来一张！」 |
