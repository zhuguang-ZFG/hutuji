# 小智云智能体配置（让呼图机更聪明）

> 目标：小智云负责「听懂 + 选工具」；绘图质量由京东云 MCP + **llm7**（现行）决定。  
> 本目录是**可粘贴到控制台**的文稿，不含密钥。  
> **进度（2026-07-26）**：全部通过脚本自动同步完成。模型 DeepSeek V4；角色提示含 few-shot + 智能补全策略（2459 字符）；知识库含硬件规格/故障排查/创意玩法/对话风格；助手自称已改为「呼图机」。S3 屏幕 UI 文案已定制（待构建烧录）。
> **2026-07-31 事实对齐**：role-prompt 与 knowledge-base 已补「出图两步衔接」（云端 `hutuji_draw` 只返 url，须再调设备 `hutuji.draw`(url) 才落笔；原 KB「自动衔接」为假）、暂停超 10 分钟自动取消、可画区 190×277、断连从开头重画。**本批只改本仓文稿**；云端生效须用户在控制台重取 JWT 后跑 `python scripts/xiaozhi_console.py sync 2094251`（旧 token 已 401 失效，见 `release-readiness.md` #10），sync 前云端仍是旧文稿。

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
```

详见 `scripts/xiaozhi_console.py`。

## 两层脑（勿混）

| 层 | 谁 | 变聪明靠什么 |
|----|-----|--------------|
| 对话/意图 | 小智云（DeepSeek V4） | 角色提示 + 知识库 + few-shot 对话示例 |
| 出图 | 京东云 MCP `llm_svg.py` | `SYSTEM_PROMPT` + **llm7**（现行；FreeModel 可作备用） |

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
