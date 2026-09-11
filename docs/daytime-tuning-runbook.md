# 白天联调 Runbook（单日取证流程）

> 用途：把**已就绪但待真机/真环境验证**的多项能力，编排进**一次装纸 + 一次 SSH + 一次重取 token**
> 的白天窗口里串起来验完。本文是**操作手册**，不是能力定义——每个能力的通过判据以
> `bringup-sop.md` 对应阶段为准（本文只引用、不复制）；取证记录填 `templates/device-test-record.md`，
> 批次结论填 `templates/release-manifest.md`，状态勾选回 `release-readiness.md`。
>
> 铁律（与 `agent-anti-drift.md` 同）：每步不过就停，**不得假装过**；证据缺一项就写「未验证」；
> 取证镜像用完**必须回刷**默认镜像；凭据（token / MCP_ENDPOINT）只进当前终端环境变量，不入库。
>
> 适用基线（**2026-08-18 治理复核**）：本仓与三仓 HEAD 均不在此钉死——开工用 `git log --oneline -1`
> / `git -C <repo> rev-parse --short HEAD` 现取，并与 `release-readiness.md`、`worktree-inventory.md` 对齐。
> 当前有证据的板上身份：Grbl 至少为 `e3b1cad` 烧录版；S3 启动 ELF `031cafd51...` 对齐已推源码
> `dcbec6f` 的本轮构建。Grbl 源码 `847b564` 含后续蓝牙功率改动，不能反推板上已刷；以串口/烧录证据为准。
> 历史待烧目标 `12f26ac` / `255f133` / `f1cf066` 均是当前分支祖先，禁止照旧文回退烧录。
> nginx `:80 /files` 站点收敛已有历史证据；正常联调日不从零重做部署。每次烧录前仍须重新核对
> 当前源码、目标镜像 SHA 与板上启动横幅，不凭本段静态文字。

---

## 0. 开工前自检（不碰真机，5 分钟）

目的：确认白天要烧/要部署的东西都已提交、本仓绿，避免带着脏树或漏提交上真机。

```powershell
# 三仓干净 + 本轮 commit 在位
git -C D:\Users\hutuji        status --short      # 期望空
git -C D:\Users\Grbl_Esp32    status --short      # 期望空
git -C D:\Users\xiaozhi-esp32 status --short      # 期望仅 4 个 build-* 未跟踪目录（用户资产，不删）
git -C D:\Users\hutuji        log --oneline -1    # 记下 SHA，与 release-readiness.md 最新回填条目对齐
git -C D:\Users\Grbl_Esp32    status -sb          # 期望首行 ...origin/<branch> 且无 ahead/behind
git -C D:\Users\xiaozhi-esp32 status -sb          # 期望首行 ...origin/<branch> 且无 ahead/behind

# COM 号每次上电都会变（板载 CH340K 也枚举为 CH340），先认口再烧，别照抄步骤里的口号
[System.IO.Ports.SerialPort]::GetPortNames()

# 本仓门禁
cd D:\Users\hutuji
python scripts\agent_gate.py --profile hub        # 期望 overall=pass、pytest 全绿、e2e PASS
```

**判据**：hutuji 本仓允许保留本次文档治理改动；Grbl_Esp32、xiaozhi-esp32、fz 三个外仓工作树无未提交源码改动；hub `overall=pass`。
**失败回退**：外仓有未提交改动 → 先确认是不是用户资产（对照 `worktree-inventory.md`），是则保留并登记；门禁红 → 先修红，**不带着红上真机**。

---

## 1. 重取小智控制台 token（用户在浏览器，1 分钟）

> **2026-08-09 已执行并通过**：`list` 回 200，列出智能体 `2094251` 呼图机（设备 1/1 在线，模型 deepseek，`knowledge_base_ids=[71153]`）。
> **本步按看门狗报警执行，不再硬性每日重做**——2026-08-20 实测 JWT 标称寿命 90 天（iat 2026-07-04 → exp 2026-10-02），作废是事件性的（别处重登/改密/登出）。京东云 `xiaozhi-token-watch.sh` cron 每 10 分钟探测，401/403 经 journald 报警；联调日前先看 `journalctl -t hutuji-token-watch -n 5`，无报警即直接用现有 token；有报警则换新值后 `refresh-xiaozhi-token.sh` 一键写回。仍**不是**一次性完成项，勿照第 3/4/5 步那样标「跳过」。
> **口径**：`list` 输出的「知识库」列是**容器 id**（`71153`，即 `update-kb`/`recall` 的参数），**不是**文档 id；文档 id 每次 sync 都变（`63334` → `65569` → …），二者勿混。

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

> **2026-08-09 已执行并通过**：`sync 2094251` 成功。**同步前**云端现役 KB 文档时间戳仍是 `2026-08-07 11:32:37 +0800`，早于 `ee2ba4e`（两步衔接 few-shot）与 `ef67df9`（换纸 ≥60 秒/90 秒上限、越界整幅拒绝、「纸不动」排查顺序）→ 08-08 那组契约纠错此前一直没上云，本轮才真正生效（详见 `xiaozhi-agent/README.md` 2026-08-09 条）。
> **失败不会清空云端**：`cmd_update_kb` 是先上传新文档、成功后才删旧文档。
> **建议在下面三条通用问法之外，再跑三条针对性召回**（直接验 08-08 那组纠正能否召回，比通用问法更强）：
> ```powershell
> python scripts\xiaozhi_console.py recall 71153 "画超出纸张会怎样"   # 期望命中 拒绝整幅 / 不会裁剪
> python scripts\xiaozhi_console.py recall 71153 "说了画但纸没动"     # 期望命中 漏调 / hutuji.draw
> python scripts\xiaozhi_console.py recall 71153 "换纸时能停止吗"     # 期望命中 60 秒 / 90 秒 / 等当前换纸完成
> ```
> 2026-08-09 用越界／纸不动／换纸停止三类问法复核，**逐条命中标记**（原始问法未逐字留证，上面是等效示例）。
> **未定因异常（遇到先重跑一次，再回第 1 步）**：本轮首轮 `recall` 三问全回 `401 Invalid token`，紧随其后的 `list` 回 200、重跑 `recall` 即成功 → 不是 token 失效，疑为新建文档后 `recall-test` 的瞬时拒绝，**未确认**。

目的：把本仓 `docs/xiaozhi-agent/` 的最新文稿（两步衔接、暂停时限、可画区 190×190、断连重画、
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

## 3. SSH 京东云：nginx 站点收敛 ✅ 已于 2026-07-31 14:51–14:53 执行

> **本步已完成，正常联调日跳过**（只在换机/重建 nginx 时回来照做）。
> 证据：`results/nginx-80-files-deploy-evidence-20260731.md`；固化记录见 `deployment.md`「nginx 站点收敛」。
> **两处原文写错，已纠正**：
> ① 「部署 :80 `/files/` 反代」——该反代**自 07-27 03:06 起就已在线**，不是本轮才部署；
> ② 「symlink 已建，不重建；只覆盖 sites-available 那份」——`sites-enabled/hutuji` 当时是**普通文件**，
>    照此只改 `sites-available` 是**静默空操作**（`nginx.conf:65` 只 include `sites-enabled/*`）。

实际执行与结论（细节见 `deployment.md`）：三份配置曾互不相同 → 已 `install` 本仓候选到 `sites-available`
+ `ln -sfn` 重建 `sites-enabled` 符号链接 + `nginx -t` + `reload`；reload 前后行为逐项一致（两种 Host 下
`/files/nope`→404、`POST`→403、`/mcp`→404、`/`→301），并首次取到真 200（公网隧道与 `:80` 回源均
`HTTP=200 bytes=31`，正文逐字回读）；`preflight.sh` 先 `pass=9 fail=0`，扩到 14 项后 `pass=14 fail=0`。备份
`/root/nginx-hutuji-{enabled,available}.bak.20260731-145120`。

**换机/重建时照此做**（`-i` 私钥名少一个 `5`，见 `deployment.md`「SSH 私钥名有坑」）：

```bash
ssh -i ~/.ssh/jdcloud_ed2519 -o IdentitiesOnly=yes root@117.72.118.95
# 3a 取证 :80 现有 server 块 + 当前生效副本到底是文件还是链接
nginx -T 2>/dev/null | grep -B1 -A3 'listen 80'
ls -l /etc/nginx/sites-enabled/hutuji
# 3b 同步 conf 并让 sites-enabled 一定是指向 sites-available 的链接（CRLF 必须先剥掉）
sed -i 's/\r$//' /opt/hutuji/deploy/nginx-hutuji.conf
install -m 644 /opt/hutuji/deploy/nginx-hutuji.conf /etc/nginx/sites-available/hutuji
ln -sfn /etc/nginx/sites-available/hutuji /etc/nginx/sites-enabled/hutuji
# 3c 语法检查（不过绝不 reload）
nginx -t
# 3d reload
systemctl reload nginx
# 3e 复验：两种 Host 都要试，裸 IP 过了不代表隧道 Host 过
for h in 117.72.118.95 hutuji.donglicao.com; do
  curl -s -o /dev/null -w "$h /files/nope=%{http_code} " -H "Host: $h" http://127.0.0.1/files/nope
  curl -s -o /dev/null -w "POST=%{http_code}\n" -X POST -H "Host: $h" http://127.0.0.1/files/nope
done                                                            # 期望 404 / 403
curl -sI https://hutuji.donglicao.com/files/__nonexistent__      # 期望 404，不是 301 自指
curl -sI https://hutuji.donglicao.com/mcp                        # 期望 404
# 3f 真 200 正例（可自造：合法命名 + TTL 600s，测完删）
curl -s -o /dev/null -w "%{http_code} %{size_download}\n" https://hutuji.donglicao.com/files/<真实token>.gcode
```

**判据**：`sites-enabled/hutuji` 是符号链接；`nginx -t` 成功；两种 Host 下 `/files/<不存在>` 都 404（不是 301）、
`POST` 403；`/mcp` 404；有产物时 `/files/<token>` 200 且字节数对得上。
**失败回退**：
- 复验发现 Host=域名 落不到我们的块 → conf 两个 server 块的 `server_name` 必须同时含
  `117.72.118.95 hutuji.donglicao.com`（现行 conf 已含；安全：`/` 仍 301、`/files/` 走 loopback 反代）。
- `nginx -t` 失败 → **不 reload**，看报错（多半是 `limit_req_zone` 上下文或证书路径），修本仓 conf 重传。
- reload 后行为回归 → `cp -a /root/nginx-hutuji-enabled.bak.<ts> /etc/nginx/sites-enabled/hutuji`（先删链接）后 `nginx -t && reload`。
- 公网 `:8300` 应不可达（安全组未放行）：`curl -v http://117.72.118.95:8300` 期望连接被拒/超时。

---

## 4. 烧录 Grbl：正常联调日默认跳过，换板/明确升级才执行

> 有烧录证据的板上基线至少为 `e3b1cad`；实时源码 HEAD 当前为 `847b564`，后者含 §9-D′ 蓝牙功率改动，
> **不得**由源码 HEAD 反推板上已刷。正常联调只需现状探活，不为追 HEAD 重烧；确需升级时先跑 fz 门禁、
> 记录产物 SHA，再以启动/串口证据提升板上基线。`12f26ac` 是历史祖先，禁止按旧文回退。

**换板 / 需要回退基线时才照此做**：

```powershell
cd D:\Users\Grbl_Esp32
# PlatformIO release 构建（按该仓既有流程；构建完烧录）；COM 号先按第 0 步认口
# 烧录后串口核验（115200）：发 $$ 与 ?
```

**判据**（串口自证，不凭记忆）：esptool 各分区 `Hash of data verified`；`?` 回 `<Idle|...>`；
`[ESP901]` 回 `Paper=... Changing=Off`；`$130=210`、`$131=297`；授权保留（`[License] Authorized`）。
**失败回退**：烧录失败 → 看 esptool 报错（COM 口被占？换口/关占用进程）；启动 panic → 回退上一已验基线，**不带着 panic 进第 6 步**。
**取证**：构建/烧录日志 + 串口核验记入 `device-test-record.md` 的 Grbl 行。

---

## 5. 烧录 S3 默认镜像：当前 `dcbec6f` 已推且板上身份已绑定，正常联调日跳过

> 当前板上启动横幅 ELF `031cafd51...` 与本轮构建 ELF `031cafd5…aada` 对齐，触摸、七工具、Telnet、
> 授权和 Idle 已自证；源码已固定并推送为 `dcbec6f`。正常联调不重刷。换板或生成新镜像时，必须重新记录
> bin/ELF SHA、app-flash hash 校验与启动横幅；`255f133` 是历史祖先，禁止按旧文回退。

**换板 / 需要回退基线时才照此做**（烧的是默认镜像，**不含 8.5 脚手架**）：

```powershell
cd D:\Users\xiaozhi-esp32
# IDF v6.0.1 构建 lichuang-dev 默认配置 → app-flash（烧录口与 CH340K 日志口都按第 0 步认口，别照抄口号）
python3 scripts\release.py lichuang-dev
```

**判据**：esptool `Hash of data verified`；启动日志见七工具注册、Telnet ready/authorized、持续 fresh `Idle`，
无 panic/assert/backtrace。
**失败回退**：构建 OOM → 加 `--cpus=4` 之类限并行（参考 stackchan-mcp 的 LVGL 编译坑）；启动连不上写字机 → 回第 0 步核对 IP/路由器。
**取证**：记入 `device-test-record.md` 的 S3 行（镜像大小/SHA256/COM/烧录时间，以本次自证为准）。

---

## 6. 装纸 + 预检（2 分钟）

```
装纸 → CH340 串口发 [ESP901]（或 HTTP /command?plain=[ESP901]）
期望：Changing=Off 且（Paper=OK 或 PanelHold=On）
```

**判据**：`Changing=Off`，且 `Paper=OK` **或** `PanelHold=On` 二者之一成立。
**为什么不是只看 `Paper=OK`**：`Paper=OK` 要求纸恰好压住台面传感器；**`M30` 自动换纸成功的
终态是 `Paper=No` + `PanelHold=On`**（第 8 步末对位把尾缘再次带过传感器，2026-08-07 COM10
两次实测）。`PanelHold=On` 由 `paper_enter_panel_low_hold()` 置位，它只在换纸成功收尾处被调用
（`PaperSystem.cpp:1037`，唯一置位点），任何 `paper_disable_drivers()` 都会清零——是「这张纸正被
面板夹持」的可靠判据。
**失败回退**：两者都不成立 → 纸没装好/未换纸/驱动已失能，重装或发 `M30` 送一张；`Changing=On`
卡住 → 等换纸完成或断电取纸重来，**不在此状态进第 7 步**。

---

## 7. 语音端到端（bringup §阶段 7，15 分钟）

目的：验 App 语音 → 小智云 → MCP → S3 → Grbl 全链，**重点看异步受理、完成推送和预览确认是否让纸动**
（同步模式无设备消息 token 时，再验证助手是否把两个地址传给设备）。

1. 对设备说「画一只简笔小猫」（吐字清晰、30cm–1m、见文末「语音现场调优」）。
2. **CH340 串口全程看日志**（Telnet 只有 ok/error/?/banner，看不到全貌）。
3. **双源观测**（单一 CH340 串口看不到全链，见 §7.1 信号表）：
   - **源 A = CH340 串口**（S3 日志 + Grbl 的 `<...>`/`ok`/`[MSG:[PaperStatus]`）：看 S3 是否进入
     下载→校验→流控→页尾→换纸→完成。
   - **源 B = 阿里云主节点 `journalctl -u hutuji-mcp` + 小智云对话日志**：看云端 `hutuji_draw` 是否被调用，以及异步完成点是否触发设备消息推送（京东云只作为热备节点，切换时才观察）。
   - 两步衔接 = 异步时源 B 见 accepted 后见服务端设备链式调用且源 A 随后见预览/下载完成；无 token 的同步回落才由助手把 `url` 与 `preview_url` 原样传给设备侧 `hutuji.draw`。

**判据**（bringup §阶段 7 通过判据）：纸上出现完整图；换纸自动完成（源 A 见 `[PaperStatus] 0`）；
源 A 见 `notify: 出图完成` 或 `任务结束 state=done`（= 完成推送已发）；源 B 见云端 `hutuji_draw` 被调用。两步衔接按 §7.1 间接判定（同步无 token 时另核对助手手动传双地址）。
**失败回退 / 分支判定**：
- 纸不动 + 源 A 无 `下载完成` → 设备 `hutuji.draw` 没被调（两步衔接漏）：回第 2 步确认 sync + recall；
  若 sync 已生效仍漏 → 记 bug，**如实记录不美化**。
- 纸不动 + 源 A 有 `下载完成` 但无逐行 `ok` → 卡在授权/流控，看源 A 是否 `未授权`/`等 ok 超时，队首`。
- 源 B 无云端 draw 记录 → LLM 没调云端工具（prompt/知识库没召回），回第 2 步。
- ASR 听错 → 看是否触发「听不清先确认」反问；直接画错 → 纠错表没召回，回第 2 步；声学问题 → 文末调优。
- 出图但换纸失败 → 进第 8/9 步前先看 `[PaperStatus]` 码（§阶段 8.8）。
**取证**：填 `device-test-record.md`（语音原文 / ASR 文本 / 源 B 云端 draw 是否出现 / 源 A 关键子串按序截图 /
出图照片 / 换纸结果）。源 A 抓一段、按 §7.1 grep 清单核对。

### 7.1 两步衔接观测：真实信号表 + 可观测性缺口

⚠️ **诚实前提**：当前固件/服务**没有为「两步衔接」留直接观测点**——云端 `hutuji_draw` 返回的 url
只存在于源 B，而 S3 的 `DownloadToPsram` **不把下载的 url 打进日志**（`hutuji_job.cc:2389` 只打字节数+crc）。
所以「LLM 调云端 draw 拿到的 url == 设备 draw 下载的 url」**无法用一行日志直接串起来**，只能间接证。
白天取证按间接法；要直接证需「观测性增强」（见下表末，**非本 runbook 强制，记待办**）。

**源 A（CH340 串口）真实子串，正常画线按序应出现**（字面量取自 `hutuji_job.cc`；**「出处」列行号仅供定位、固件一改即漂，一律以「子串」列为准做 grep**，符合本仓「行号易过期、不确定别写行号」的告诫）：

| 序 | 子串（grep 用） | 出处 | 含义 |
|----|----------------|------|------|
| 1 | `下载完成` | `:809` | 设备 draw 已起 + 全量下载完（**设备侧第一个可见信号**） |
| 2 | *（预期无）* `CRC expect=` | `:817` | 出现 = CRC 不符 = 校验失败；正常应**静默通过、无此行** |
| 3 | *（预期无）* `未授权` | `:641` | 出现 = 授权门没过；正常应**静默通过、无此行** |
| 4 | *（正常无）* `error:8，` | `:1454` | 出现 = 流控重发；正常画线不应出现 |
| 5 | *（偶发可接受）* `状态报告确认在途` | `:1601` | ok 超时但状态兜底释放，≤3 次正常；频繁 = 链路差 |
| 6 | `[PaperStatus] 0` | Grbl→S3 | 页尾 M30 换纸成功 |
| 7 | `notify: 出图完成` 或 `state=done` | `:139` / `:717` | 完成推送已发 / 任务收尾 |

**源 A 取证 grep 清单**（抓语音触发后整段日志，正常画线应满足）：
```
# 应出现（按序）
grep -E "下载完成|PaperStatus\] 0|notify: 出图完成|state=done" <串口日志>
# 应不出现（出现即异常，逐个查）
grep -E "CRC expect=|未授权|等 ok 超时，队首|error:8，" <串口日志>   # 期望空
```

**源 B 取证**（时间窗与源 A 的「下载完成」对齐）：
```
journalctl -u hutuji-mcp --since "<语音时刻-10s>" | grep -iE "hutuji_draw|tools/call"
# 期望：语音后先有一条云端 hutuji_draw 调用且返回 200，时间早于源 A 的「下载完成」
```

**两步衔接间接判定**：源 B 见云端 draw 200 **且** 源 A 在其后见 `下载完成 N 字节` **且** 纸动 **且**
源 A 全程无 `CRC expect=`/`未授权` → 判两步衔接成立（设备 draw 被调、且下载了云端刚生成的产物）。
若源 B 有云端 draw 但源 A 无 `下载完成` → 设备 draw 漏调（衔接断）；若源 A 有 `下载完成` 但源 B 无
云端 draw → 设备 draw 用了别的 url（autodraw 残留？核对镜像是否默认镜像、`HUTUJI_AUTO_TEST_DRAW` 是否关）。

**观测性增强待办**（要直接证两步衔接，二选一加一行日志，**改代码，白天/后续做，不在本 runbook 强制**）：
- S3 `StartDraw`/`DownloadToPsram` 打 url 的 host+path 末段（`ESP_LOGI(TAG,"draw url=%s",…)`，注意只打 path
  末段 token，**不打 query/凭据**）；或
- 云端 `hutuji_draw` 返回前打返回 url 末段。两端末段 token 一致 = 直接证据。
  加完须补一条脱敏回归（token 不进敏感字段、不泄全 URL），并过 `test_injection`/`test_cloud_bridge` 口径。

---

## 8. 暂停超时验（关联 §9-C′ + 暂停 soft reset，10 分钟 + 等待）

目的：验暂停 >10 分钟后 Grbl 不锁 Hold、后续能开新画（修复已包含于当前 `dcbec6f` 源码；是否通过仍看本轮实机日志）。

1. 语音/工具起一幅画，进入 streaming 后说「暂停」。
2. 等 >10 分钟（或临时把 `kMaxPauseMs` 调小重烧以加速——**调小重烧只用于本验，验完回默认**）。
3. 观察：超时后日志应见 soft reset 序列（`!` → 等 Hold → `0x18`），Grbl 回 Idle。
4. 再起一幅新画，验能正常出图（证明没锁 Hold）。

**判据**：超时后 Grbl 回 Idle；新画能起、能出图。
**失败回退**：新画起不来、`?` 一直 Hold → 修复未生效，记 bug，**不宣称修好**；回退排查 `WaitWhilePaused` 超时分支。
**取证**：记暂停起止时间、reset 序列日志、新画结果。

---

## 9. 换纸中 abort ✅ 已于 2026-07-31 取证，正常联调日跳过

> **本步已完成，不作为默认待办重做**（AGENTS.md「默认下一刀」明令禁止）。
> 证据：`release-readiness.md` 第 8a 条 + `agent-handoff.md` §6 第 1 项——2026-07-31 21:49–21:50，
> 带宏镜像 `build-hutuji-8_5-abort/xiaozhi.bin`（`0x2cfc40`）app-flash 后，AutoTest 五角星画完
> `state=paper_change` → 脚手架命中窗口调 `RequestAbort()` → 回「换纸中无法即停，完成后即停」→
> 页尾 `M30` → `[PaperStatus] 0` → `state=aborted` 不续跑，**bringup §8.5 四判据全过**。
>
> ⚠️ 历史 `f1cf066` 是当前 `dcbec6f` 的祖先，烧它会回退；取证脚手架是 `main/CMakeLists.txt`
> 的构建期开关 `HUTUJI_AUTO_TEST_ABORT_ON_PAPER`，不是某个旧 commit 的固有行为。
> 要复测就从当前 HEAD 带开关构建，不要 checkout 旧 commit。

**需要复测时照此做**（顺序铁律：**先备份默认镜像** → 烧带宏镜像 → 取证 → **回刷默认镜像**）：

1. 备份当前默认 `build-waveshare-35/xiaozhi.bin`，记录 SHA256 与板上启动 ELF。
2. 从**当前 HEAD** 带开关构建：`idf.py -DHUTUJI_AUTO_TEST_ABORT_ON_PAPER=ON build` → app-flash（COM 号按第 0 步认口）。
3. 装纸，PC 起本机静态服务托**小**测试件（件越小越快到页尾换纸窗，§8.5 用过 658B / CRC32 `2290095f`）。
4. 复位 S3 触发出图 → 脚手架在换纸窗一开即本地调 `RequestAbort()` → 看 abort 前后 `status` 打印。
5. **判据**（bringup §8.5 四条）：`Changing=On` 被识别；abort 不叠加 reset/M30；换纸结束后任务不续跑；诚实回报「换纸中无法即停」。
6. **回刷第 1 步备份的默认镜像**，串口确认脚手架字符串已不在、行为回默认。

**失败回退**：取证中发了 reset/M30 → 记 bug；**回刷不能忘**，否则板上留着取证宏污染后续。
**取证**：abort 前后 status 日志 + 回刷后串口自证，记入 `device-test-record.md`。

---

## 10. P2 S4 有纸出图 ✅ 链路已于 2026-08-03 取证，**只剩画质人工目视**

> **出图链路本身已通过，不重做**：2026-08-03 同一 551 行 / 13,804 B / CRC32 `ae1b25b2` 玫瑰件实测——
> 全量下发无丢行（G1 547 / G0 1 / 旁路 `G92 Z0` 与页尾 `M30` 各 1 / `ok` 559）、进度 `124→475→547/551`、
> 页尾 `M30` 触发自动换纸八步全绿（`Paper found at step 7764`、`[PaperStatus] 0`）→ `state=done replayable=1`、
> `pbuf_free`/`Guru Meditation`/`assert failed` 两侧 0 命中（`agent-handoff.md` §6 第 3 项、§0 2026-08-03 条）。
>
> **本步唯一未验证项 = 纸面实物画质人工目视**（`linesimplify 0.05mm` 在该图是否够、曲线有无棱角/停顿），
> 这也是 AGENTS.md「默认下一刀」的第 ① 项。**目视需人在机前，无法由代理代劳。**

目视做法：可与第 6/7 步共用同一次装纸；若只为看画质，用真管线产物覆盖被服务文件（**不改文件名**，URL 是常量）：
`test_rose_s4.svg` → `linesimplify 0.05mm` → 551 行 / 13,804 B / CRC32 `ae1b25b2`（已过 `validate_gcode`），
起静态服务（只暴露该文件，`/mcp` 404），复位 S3 触发 autodraw；出图结束**立刻停 8300**（防空载复位压笔）。

**判据**（bringup §7.0，画质部分靠肉眼）：551 行全部出墨且**曲线连续无棱角、无停顿**；页尾 M30 后
`[PaperStatus] 0` + `state=done`，全程无 `error:90`。
**失败回退**：曲线有棱角/停顿 → 0.05mm 在该图仍不够或窗口反压未做（`hutuji_job.cc` 自适应 `Bf` TODO），
记入 P2 待办，**不宣称 S4 过**。
**取证**：出图照片（画质结论必须配照片）+ 行/CRC/状态记入 `device-test-record.md`。

---

## 11. 收尾（10 分钟）

1. 填 `templates/device-test-record.md`（每台/每轮一行：固件 commit、镜像 SHA256、COM、各项判据勾选、照片/日志路径）。
2. 填 `templates/release-manifest.md`（本批次结论）。
3. 按实测结果勾 `release-readiness.md`。当前开放项：Waveshare 预览确认/取消与五个屏幕动作、纸面画质目视、
   断连恢复 `paper_active` 窗口 abort、生产/合规签核；当前源码 BLE pending event 加固（`f1aa0e7`）已通过 host 93/93、BLE 专测 18/18、默认/启用构建，
   且启用镜像已刷 COM14 完成窄范围复验（4 分钟稳定 + 两轮 6 字节广播 + NVS 逐字节回滚，证据在 `results/local-only/2026-08-19/`）；
   但共存量化、功耗/温升仍未过，`minimal sram: 1647` 疑点未闭环，不得写成共存通过。真实语音出图、归位、自动换纸、正常换纸中 abort
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

---

## 附 C：下一轮联调日——屏幕 HIL 一日清单（2026-08-28 增补）

前提：板上 `c3cf3d7` 已含触摸第四轮链、主屏可视性与配网修复，**无需新镜像**（仅第 C4 步需带构建期开关的专用构建）。每步如实记录，判据仍归 bringup-sop。

1. **触摸体感复核**（判别顺序钉死，勿先调参）：先对不可拖固定钮（维护页「配网」/「重新配置写字机」）做 ≤90ms 快按 → 有反应则点按链路健康，残留体感归布局/跟手；再拖动三个可拖钮验 round-4 效果。**不开第四轮阈值/周期调参**，除非固定钮快按也哑。取证：录像 + `results/local-only/<date>/touch-hil-*.log`。
2. **屏幕 UI 全链 HIL**：预览确认出图 / 预览取消；streaming、paused、idle 三态下分别暂停、继续、停止、重画、试笔；息屏/亮屏（抬腕 + 轻触/双击 + 30s 空闲）各一轮。必须真触摸走完全映射链，**禁直接调 `Job::Request*`** 代替（会绕过触摸映射、置灰门控与回调接线）。取证：逐态录像 + 串口日志。
3. **配网闭环**：进入配网 → 二维码 → 「关闭」钮（08-26 修复已在板上）→ 完整配网到出图一轮。期望：关闭钮可点、抽屉不遮 QR、配网成功出图。
4. **断连恢复 `paper_active` 窗口 abort 复测**：从当前 HEAD 带 `-DHUTUJI_AUTO_TEST_ABORT_ON_PAPER=ON` 构建（构建期开关，**禁 checkout 旧 commit**）；期望 abort 不追加 reset/M30，与 2026-07-31 正常换纸 abort 分流。取证：双串口抓包（`bringup_fault_capture.py`）。
5. **机设复核**：实机 `$$` 读 `$130`/`$131`，核对「X 长边/Y 宽边」口径是否应对调为 `$130=297/$131=210`；若口径坐实，顺手取 X 上限抬 277 的实机证据。取证：`$$` 全文拍照/抄录进 templates。
6. **全链冒烟**：语音画一张 QD 命中类（秒出）+ 一张 AI 链（观察生成段 10–40s 体验）；AI 虚实交替抽查一次即可（已有 ✅，不做重复取证）。

C1–C3 全过 → readiness 8.7.7 可勾 ✅；C4 过 → 默认下一刀 ② 关闭。
