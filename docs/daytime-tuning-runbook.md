# 白天联调 Runbook（单日取证流程）

> 用途：把**已就绪但待真机/真环境验证**的多项能力，编排进**一次装纸 + 一次 SSH + 一次重取 token**
> 的白天窗口里串起来验完。本文是**操作手册**，不是能力定义——每个能力的通过判据以
> `bringup-sop.md` 对应阶段为准（本文只引用、不复制）；取证记录填 `templates/device-test-record.md`，
> 批次结论填 `templates/release-manifest.md`，状态勾选回 `release-readiness.md`。
>
> 铁律（与 `agent-anti-drift.md` 同）：每步不过就停，**不得假装过**；证据缺一项就写「未验证」；
> 取证镜像用完**必须回刷**默认镜像；凭据（token / MCP_ENDPOINT）只进当前终端环境变量，不入库。
>
> 适用基线（2026-07-31）：本仓 HEAD 含审查修复批（nginx :80 /files 反代、§9-C′ 治理、暂停 soft reset
> 治理、语音容错文稿、review P1 脱敏修复）；Grbl 待烧 `12f26ac`；S3 待烧 `255f133`（默认镜像）+
> `f1cf066`（仅 8.5 取证用，用完回刷）。**烧录前板上基线见 `release-readiness.md`，以当时自证为准。**

---

## 0. 开工前自检（不碰真机，5 分钟）

目的：确认白天要烧/要部署的东西都已提交、本仓绿，避免带着脏树或漏提交上真机。

```powershell
# 三仓干净 + 本轮 commit 在位
git -C D:\Users\hutuji        status --short      # 期望空
git -C D:\Users\Grbl_Esp32    status --short      # 期望空
git -C D:\Users\xiaozhi-esp32 status --short      # 期望仅 4 个 build-* 未跟踪目录（用户资产，不删）
git -C D:\Users\hutuji        log --oneline -8    # 应见 f02f12a/8e4923f/a5d0d77/814b1c9/0cfa35d/d599b3b
git -C D:\Users\Grbl_Esp32    log --oneline -1    # 应见 12f26ac
git -C D:\Users\xiaozhi-esp32 log --oneline -2    # 应见 255f133 与 f1cf066

# 本仓门禁
cd D:\Users\hutuji
python scripts\agent_gate.py --profile hub        # 期望 overall=pass、pytest 全绿、e2e PASS
```

**判据**：三仓无未提交源码改动；hub `overall=pass`。
**失败回退**：有未提交改动 → 先确认是不是用户资产（对照 `worktree-inventory.md`），是则保留、不是则提交或丢弃；门禁红 → 先修红，**不带着红上真机**。

---

## 1. 重取小智控制台 token（用户在浏览器，1 分钟）

目的：旧 JWT 已被服务端作废（`/api/agents` 返回 401，见 `release-readiness.md` 第 10 条），
sync 与云端下发都依赖新 token。

1. 浏览器打开 `xiaozhi.me` 控制台 → F12 控制台输入 `localStorage.getItem('token')` → 复制整串。
2. 当前终端注入（**只在本窗口**，不写文件）：
   ```powershell
   $env:XIAOZHI_TOKEN="<粘贴>"
   python scripts\xiaozhi_console.py list        # 期望：列出智能体，含 ID 2094251，不报 401
   ```

**判据**：`list` 返回智能体列表、无 401。
**失败回退**：仍 401 → token 复制不全或未登录，重取；`list` 报网络错 → 本机能否访问 xiaozhi.me，查代理/网络。**token 绝不写进仓库/.env/提交说明。**

---

## 2. 同步提示词 + 知识库到云端（2 分钟）

目的：把本仓 `docs/xiaozhi-agent/` 的最新文稿（两步衔接、暂停时限、可画区 190×277、断连重画、
听不清先确认、误识别纠错表）推到智能体 2094251 / 知识库 71153。

```powershell
python scripts\xiaozhi_console.py sync 2094251     # 角色提示 + 知识库一次同步
# 召回自测（验证知识库真的关联上、能命中）
python scripts\xiaozhi_console.py recall 71153 "画一只猫"
python scripts\xiaozhi_console.py recall 71153 "能画彩色吗"
python scripts\xiaozhi_console.py recall 71153 "纸卡住了"
```

**判据**：sync 无报错；三条 recall 分别命中「绘图意图 / 单色线条说明 / 卡纸处置」相关内容。
**失败回退**：sync 报 401 → 回第 1 步重取；recall 命中不到 → 控制台确认知识库 71153 已**关联**到智能体 2094251（`deployment.md` 阶段 6 第 1 条）。

---

## 3. SSH 京东云：修 nginx 隧道坏环（10 分钟，含取证）

目的：部署本轮 `deploy/nginx-hutuji.conf` 的 :80 `/files/` 反代，修 Cloudflare Tunnel 回源 301 自指坏环。

⚠️ **reload 前必须先取证 :80 拓扑**（上一轮 code review 发现的点）：本 conf 的 :80 `server_name` 是裸 IP
`117.72.118.95`，而隧道回源透传的 Host 是域名 `hutuji.donglicao.com`。若 VPS 上 :80 还有别的 server 块，
Host=域名 可能落不到我们的块 → /files 反代不生效。

```bash
ssh <user>@117.72.118.95
# 3a 取证 :80 现有 server 块（看 server_name 都有谁）
nginx -T 2>/dev/null | grep -B1 -A3 'listen 80'
# 3b 同步 conf（symlink 已建，不重建；只覆盖 sites-available 那份）
cp /opt/hutuji/deploy/nginx-hutuji.conf /etc/nginx/sites-available/hutuji
# 3c 语法检查（不过绝不 reload）
nginx -t
# 3d reload
systemctl reload nginx
# 3e 公网复验（在本机或 VPS 皆可）
curl -sI https://hutuji.donglicao.com/files/__nonexistent__   # 期望：nginx 404 或应用 JSON 404，不是 301 自指
curl -sI https://hutuji.donglicao.com/mcp                      # 期望：404
# 3f 真 200 正例（需先有一张云端产物；若暂无，留到第 7 步语音出图后回来补这一条）
curl -sI https://hutuji.donglicao.com/files/<真实token>.gcode  # 期望：200 + X-Hutuji-CRC32
```

**判据**：`nginx -t` 成功；`/files/<不存在>` 不再 301 自指；`/mcp` 404；有产物时 `/files/<token>` 200。
**失败回退**：
- 3a 发现 :80 另有 server 块且其 `server_name` 含域名/`_`（默认）→ 我们的块抢不到域名 Host。
  修法：把本 conf :80 的 `server_name` 加成 `117.72.118.95 hutuji.donglicao.com;`（安全：`/` 仍 301、
  `/files/` 走 loopback 反代），改完重跑 3c/3d。**这是唯一允许现场改 conf 的情形，且改完要 commit 回本仓。**
- `nginx -t` 失败 → **不 reload**，看报错（多半是 `limit_req_zone` 上下文或证书路径），修本仓 conf 重传。
- 公网 `:8300` 应不可达（安全组未放行）：`curl -v http://117.72.118.95:8300` 期望连接被拒/超时。

---

## 4. 烧录 Grbl `12f26ac`（COM3，10 分钟）

目的：落地 §9-C′（underflow 恢复置 `State::Cycle`，避免恢复期误报 Idle 致 S3 提前 M30）。

```powershell
cd D:\Users\Grbl_Esp32
# PlatformIO release 构建（按该仓既有流程；构建完烧录）
# 烧录后串口核验（COM3，115200）：发 $$ 与 ?
```

**判据**（串口自证，不凭记忆）：esptool 各分区 `Hash of data verified`；`?` 回 `<Idle|...>`；
`[ESP901]` 回 `Paper=... Changing=Off`；`$130=210`、`$131=297`；授权保留。
**失败回退**：烧录失败 → 看 esptool 报错（COM 口被占？换口/关占用进程）；启动 panic → 回退上一已验基线，**不带着 panic 进第 6 步**。
**取证**：构建/烧录日志 + 串口核验截图记入 `device-test-record.md` 的 Grbl 行。

---

## 5. 烧录 S3 默认镜像 `255f133`（COM6，15 分钟）

目的：落地暂停超时 soft reset 冲 planner（避免 Grbl 锁 Hold）。**这一步烧的是默认镜像，不含 8.5 脚手架。**

```powershell
cd D:\Users\xiaozhi-esp32
# IDF v6.0.1 构建 lichuang-dev 默认配置 → app-flash COM6
# 烧录后看 COM9（CH340K 日志口）启动日志
```

**判据**：esptool `Hash of data verified`；启动日志见七工具注册、Telnet ready/authorized、持续 fresh `Idle`，
无 panic/assert/backtrace。
**失败回退**：构建 OOM → 加 `--cpus=4` 之类限并行（参考 stackchan-mcp 的 LVGL 编译坑）；启动连不上写字机 → 回第 0 步核对 IP/路由器。
**取证**：记入 `device-test-record.md` 的 S3 行（镜像大小/SHA256/COM/烧录时间，以本次自证为准）。

---

## 6. 装纸 + 预检（2 分钟）

```
装纸 → CH340 串口发 [ESP901]（或 HTTP /command?plain=[ESP901]）
期望：Paper=OK ... Changing=Off
```

**判据**：`Paper=OK` 且 `Changing=Off`。
**失败回退**：`Paper=No` → 纸没装好/传感器没压到，重装；`Changing=On` 卡住 → 等换纸完成或断电取纸重来，**不在此状态进第 7 步**。

---

## 7. 语音端到端（bringup §阶段 7，15 分钟）

目的：验 App 语音 → 小智云 → MCP → S3 → Grbl 全链，**重点看两步衔接是否让纸动**
（上一轮标 [INFERENCE] 的隐藏根因，本轮 prompt 已修，此处证实或证伪）。

1. 对设备说「画一只简笔小猫」（吐字清晰、30cm–1m、见文末「语音现场调优」）。
2. **CH340 串口全程看日志**（Telnet 只有 ok/error/?/banner，看不到全貌）。
3. 观察链：唤醒 → ASR 文本 → LLM 是否**先调云端 `hutuji_draw` 再调设备 `hutuji.draw(url)`**（两步）→ S3 下载/CRC/授权 → 逐行 ok → 页尾 fresh Idle → S3 单发 M30 → 换纸 → `[PaperStatus] 0` → fresh Idle → 完成推送。

**判据**（bringup §阶段 7 通过判据）：纸上出现完整图；换纸自动完成；云端收到完成推送；
**且**日志可见两步工具调用（云端 draw 返回 url 后，设备 draw 被调用）。
**失败回退 / 分支判定**：
- 纸不动、日志只有云端 draw 没有设备 draw → 两步衔接仍漏（prompt 没生效？回第 2 步确认 sync 成功 + recall 命中）；这是本轮要证实/证伪的核心，**如实记录，不美化**。
- ASR 听错 → 看是否触发「听不清先确认」反问；若直接画错 → 纠错表没召回，回第 2 步；若是声学问题 → 文末「语音现场调优」。
- 出图但换纸失败 → 进第 8/9 步前先看 `[PaperStatus]` 码（§阶段 8.8）。
**取证**：填 `device-test-record.md`（语音原文 / ASR 文本 / 两步调用是否齐 / 出图照片 / 换纸结果 / 串口日志片段）。

---

## 8. 暂停超时验（关联 §9-C′ + 暂停 soft reset，10 分钟 + 等待）

目的：验暂停 >10 分钟后 Grbl 不锁 Hold、后续能开新画（本轮 S3 `255f133` 的修复点）。

1. 语音/工具起一幅画，进入 streaming 后说「暂停」。
2. 等 >10 分钟（或临时把 `kMaxPauseMs` 调小重烧以加速——**调小重烧只用于本验，验完回默认**）。
3. 观察：超时后日志应见 soft reset 序列（`!` → 等 Hold → `0x18`），Grbl 回 Idle。
4. 再起一幅新画，验能正常出图（证明没锁 Hold）。

**判据**：超时后 Grbl 回 Idle；新画能起、能出图。
**失败回退**：新画起不来、`?` 一直 Hold → 修复未生效，记 bug，**不宣称修好**；回退排查 `WaitWhilePaused` 超时分支。
**取证**：记暂停起止时间、reset 序列日志、新画结果。

---

## 9. 换纸中 abort（bringup §阶段 8.5，20 分钟，含回刷）

目的：验换纸态里 abort 不发 reset/M30、不续跑。**需先烧取证镜像 `f1cf066`。**

⚠️ 顺序铁律：**先备份第 5 步的默认镜像 `xiaozhi.bin`** → 烧 `f1cf066` → 取证 → **回刷默认镜像**。

1. 备份当前默认 `xiaozhi.bin`。
2. 构建/烧 `f1cf066`（含 `HUTUJI_AUTO_TEST_ABORT_ON_PAPER`，默认关闭宏在本构建打开）。
3. 装纸，PC 起本机静态服务托**小**测试件（件越小越快到页尾换纸窗，§8.5 用过 658B / CRC32 `2290095f`）。
4. 复位 S3 触发出图 → 脚手架在换纸窗一开即本地调 `RequestAbort()` → 看 abort 前后 `status` 打印。
5. **判据**（bringup §8.5 四条）：`Changing=On` 被识别；abort 不叠加 reset/M30；换纸结束后任务不续跑；诚实回报「换纸中无法即停」。
6. **回刷第 5 步备份的默认镜像**，串口确认脚手架字符串已不在、行为回默认。

**失败回退**：取证中发了 reset/M30 → 记 bug；**回刷不能忘**，否则板上留着取证宏污染后续。
**取证**：abort 前后 status 日志 + 回刷后串口自证，记入 `device-test-record.md`。

---

## 10. P2 S4 有纸出图（bringup §阶段 7.0，10 分钟）

目的：0.05mm 真实图样出图，关闭 S4 证据缺口。可与第 7/9 共用同一次装纸。

1. 用真管线产物覆盖被服务文件（**不改文件名**，URL 是常量）：
   `test_rose_s4.svg` → `linesimplify 0.05mm` → 551 行 / 13,804 B / CRC32 `ae1b25b2`（已过 `validate_gcode`，>512B 压满窗口）。
2. 起静态服务（只暴露该文件，`/mcp` 404），复位 S3 触发 autodraw。
3. 出图结束**立刻停 8300**（防空载复位压笔）。

**判据**（bringup §7.0）：551 行全部出墨且曲线连续无停顿；页尾 M30 后 `[PaperStatus] 0` + `state=done`，全程无 `error:90`。
**失败回退**：曲线有棱角/停顿 → 0.05mm 在该图仍不够或窗口反压未做（`hutuji_job.cc:1319-1320` TODO 自适应 `Bf`），记入 P2 待办，**不宣称 S4 过**。
**取证**：出图照片 + 行/CRC/状态记入 `device-test-record.md`。

---

## 11. 收尾（10 分钟）

1. 填 `templates/device-test-record.md`（每台/每轮一行：固件 commit、镜像 SHA256、COM、各项判据勾选、照片/日志路径）。
2. 填 `templates/release-manifest.md`（本批次结论）。
3. 按实测结果勾 `release-readiness.md`：第 7 条（断连重发回归）、第 8/8a 条（换纸/换纸中 abort）、第 10 条（语音端到端）、P2 S4 行。**只勾有证据的，没过的留 🟡/。**
4. 更新 `docs/agent-handoff.md` §0 快照 + §6（哪些已闭、下一刀变什么）。
5. 更新 `docs/worktree-inventory.md`（若取证产生新未提交资产/回刷记录）。
6. 跑门禁：本仓 `python scripts\agent_gate.py --profile hub`；**本轮若动了 Grbl 源码**（第 4 步只是烧已提交 commit 则不用）另跑 fz `standard`。
7. 三仓 `git status` 干净后 commit + push 文档同步。

**判据**：record/manifest 填完；readiness 勾选与证据一致；门禁 pass；三仓干净。

---

## 附 A：语音识别现场调优（用户反馈「听不准」时当场做）

分三层，**先做不需要重烧的**，仍不行再动固件 Kconfig。

**A.1 使用/硬件层（立即，不重烧）**
- MIC 朝向用户；说话距离 30cm–1m（太近爆音、太远信噪比塌）。
- 远离风扇/空调/电视等突发噪声源（AFE 对稳态噪声抑制好，对突发噪声有限）。
- 说话时别让机器正播 TTS——验硬件 AEC 参考回路（ES8311 播放→ES7210 参考）是否接对；
  若 AEC 参考没接对，机器自己的播报会被当成用户输入。
- 语速适中、吐字清晰；含糊时模型应触发「听不清先确认」反问（见知识库纠错表）。

**A.2 云端 LLM 容错（已写入文稿，第 2 步 sync 后生效）**
- role-prompt「听不清先确认」+ knowledge-base「语音识别纠错表」：ASR 认错字时 LLM 据映射表猜回意图或反问。

**A.3 固件 Kconfig（需重构建重烧，**最后**才动）**
- `CUSTOM_WAKE_WORD_THRESHOLD`（默认 20%）：调低更灵敏但误触多，调高更难唤醒但更准。
- `WAKE_WORD_DETECTION_IN_LISTENING`（默认 n）：改 y 允许说话中途用唤醒词打断。
- 唤醒词选择：若已从默认「小土豆」改「呼图机」，注意 `hu tu ji` 声学区分度低于 `xiao tu dou`
  （前两字韵母同），漏触/误触多时可换回或选声母更分散的词。
- **多数「听不准」是 ASR/环境，不是唤醒阈值**——先做完 A.1/A.2 再考虑 A.3。

---

## 附 B：本 runbook 与 bringup-sop 的分工

| | bringup-sop.md | 本 runbook |
|---|---|---|
| 回答 | 单个能力**怎么验、通过判据是什么** | 多个已就绪能力**怎么在一天内串起来验完** |
| 粒度 | 阶段 0–9，每阶段判据 + 观测点 | 单日 11 步，每步 命令/期望/失败回退/取证落点 |
| 关系 | 判据真值 | 编排 + 引用 bringup 判据（不复制）+ 填 templates |

> 本文不定义契约、不宣布出货；判据以 bringup-sop 为准，状态以 release-readiness 为准。
> 任何一步「未验证」就如实写，不得为凑绿删判据或跳步。
