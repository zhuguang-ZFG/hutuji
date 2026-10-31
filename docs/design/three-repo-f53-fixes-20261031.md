# F53 三仓复审修复（2026-10-31）

范围：F50/F51 之后到 `68894f5` 的三仓改动。复审报了 9 条，修复时又查出网页 4 个令牌对比度不足。量产 Grbl 的 `59968a47` 也在范围内，复审没有报它的问题。本批不改 Grbl，因此不跑 fz。

其中两条是上一批自己引入的，单独写出来：

- F52 给旧 WebSocket 析构设了「最多两个 `ws_retire` 任务在途」。第三次断连会退回调用方同步析构，最坏要在网络事件路径上等 3×10s。上限挡住了栈的无界增长，卡顿却被原样搬回了调用方。
- 运维降噪批把 failed 扫描收窄到 `hutuji-*`，同时只显式检查 nginx 和 ssh。阿里主节点的 worker 对 redis 只有 `After=`，redis 停了它不会进 failed，只会一直 auto-restart，于是这个状态一条告警都发不出来。

## S3（`0797ee4`，`hutuji.1.0.55`）

- `RetireWebSocketAsync()` 改为单个回收任务加 8 格环形队列。队列和 running 标志由同一把 `std::mutex` 保护，旧对象在锁外析构。只有两种情况退回同步析构：队列满时由调用方析构当前这个对象；`xTaskCreate` 失败时由调用方把队列排空。入队路径不再分配堆，原来的 `new (std::nothrow)` holder 一并删除。
- 量产 Freenove 舞台是暖莓 `#6B4E5E`，emoji 兜底字却沿用主题正文色，对比度只有 1.43:1。现在量产屏改用 `#FFF7ED`，其他板仍用主题正文色。舞台色收为常量 `kProductionStageHex`，原来三处字面量都换成它。
- 宿主测试新增断连风暴、队列满、任务创建失败三例和舞台对比度一例，旧代码上 4 例失败。全套 451/451，需要显式指定 `CXX=D:/zhugu-home/mingw64/mingw64/bin/g++.exe`，并让该 mingw bin 排在 PATH 最前；否则 `test_hutuji_hold_transport` 以 0xC0000139 退出，这是环境问题。

| 板 | 镜像 | 字节 | SHA-256 | 分区余量 |
|---|---|---|---|---|
| Freenove | `xiaozhi-freenove-1.0.55.bin` | 3,106,352 | `d371a1899240835e58f908b26f077f973df400c45aba56c0c8a3b6d5c69b07b6` | 25% |
| 立创 | `xiaozhi-lichuang-1.0.55.bin` | 3,140,752 | `09b0b1fe6d6d84a857658422bf773b5d3d19527ec472becf3e07d212c058dab4` | 24% |

两份镜像在 1.0.53 构建目录里增量编译，目标文件时间晚于源码最后修改时间，镜像内版本串为 `hutuji.1.0.55`。

## Hub

### 运维告警

- `tg-ops-check.sh`：对 redis、postgresql、sing-box 逐个先看 `is-enabled`，已启用而不在 active 就告警。京东没装这三个，跳过；阿里三个都装了，都会检查。
- `tg-relay-server.py`：旧逻辑在首个请求还在发送时，对同一 `request_id` 的重复请求直接回 202。如果首个随后失败，重复方已经当作送达，消息就丢了。现在重复方最多等 17s（`PENDING_WAIT_SECONDS`，低于客户端 `curl --max-time 20`）：首个成功回 202，首个失败由重复方接手重发，等到超时回 503 让客户端重试。
- `tg-notify.sh` 最坏耗时是 3×20s + 2×2s = 64s，而 Portal 和 MCP 调用方的超时都是 15s，常常在重试中途就把子进程杀掉。两处都改为 75s。它们本来就跑在 daemon 线程里，不阻塞请求。
- `test_ops_notifications.py` 用真实 `ThreadingHTTPServer` 起中继、桩掉 subprocess，共 10 例；旧代码上 8 例失败。

### 网页对比度

FH-031 的珊瑚粉 `#f3a3b5`/`#d85f7b` 只能做填充。三套网页原先把它当文字色用，奶油底上只有 1.6–1.8:1。另外 muted、accent-dark、ok、err、warn 这几个令牌在部分奶油面上也不到 4.5:1。

| 令牌 | 旧 | 新 |
|---|---|---|
| muted | `#8f7a84` | `#75626d` |
| soft（后台、知识库） | `#75626d` | `#66535e` |
| accent-dark / 知识库新增 primary-ink | `#d85f7b` | `#b03f5a` |
| ok | `#6ea998`、`#5f927f` | `#3f6a5e` |
| err | `#c85668` | `#ac384a` |
| warn | `#a97919` | `#815c13` |

- Portal 的激活页签、内联摘要、呼图云链接和输入焦点边框，运营后台的 `.badge` 和焦点框，知识库的 `.batch-count`、致谢链接、焦点框和 spinner，都改用深珊瑚。知识库残留的蓝色 `rgba(37,99,235,…)` 换成珊瑚色。
- `test_handraw_web_theme.py` 加了 WCAG 计算：每个文字令牌在每种奶油面上都要 ≥4.5:1，填充色不得出现在 `color:` 里。旧代码上 4 例失败，现在 7/7 通过。

### 小程序 0.3.35

- `handdraw-theme.wxss` 原来是两层：先写一遍墨蓝朱砂，再用奶油色整体覆盖一遍。现在只留奶油这一层，1202 行减到 1001 行，`!important` 从 215 个降到 141 个。被覆盖掉的旧声明全删了，重复的选择器组也合并了。
- 文字对比度修了 30 多处。典型几处：`.gallery-notice` 1.66→5.61，`.msg-failed` 1.97→6.23，录音中的 `.hold-btn.active` 1.03→7.18。还有暗色主题留下的浅色字，比如首页步进值 `#f2f4f8` 1.04，现改为 `#4d3a46`，9.84:1。新增 `--accent-text: #b03f5a`，原先用 `var(--cyan)` 的文字改用它。
- `.cancel-status` 原先被主题里 `.voice-status` 的 `!important` 吞掉，取消录音时颜色和录音中一样。现在由页面的 `.voice-status.cancel-status` 决定颜色。
- `handdraw_theme.test.js` 新增 5 例，里面带一个简化的层叠解析器（主题→app.wxss→页面，含继承色和父级背景），核对 35 个关键文字元素的最终颜色。旧代码上 5 例全失败，全套 376/376。
- 没改的：页面里暗色时代的边框和阴影不承载文字，保留；textarea 占位符 `.ph` 是 3.37:1；测试只算静止态，不含 `:active` 和 `[disabled]`。

### 上游许可

HandRaw Style 钉在 `572dff8`（v1.9.8），许可是 MIT 加署名要求，记在 `weapp-handraw-style-20261030.md`「上游依据」，两份 README 同步补了引用。

## 验证

| 项 | 结果 |
|---|---|
| MCP pytest | 1702 passed |
| Portal pytest | 642 passed |
| 知识库 pytest | 28 passed |
| 小程序 node | 376/376 |
| S3 宿主 | 451/451 |
| S3 IDF 构建 | Freenove、立创各 1 份，见上表 |

没做的：Freenove 舞台底色和 emoji 兜底字没上屏看过；小程序只跑了 node，没在开发者工具和手机上看；网页只有自动化对比度，没有公网实页目视。

## 提交、部署与发布（2026-10-31）

- S3 `0797ee446986caba0141e1ac5ca1593bb62933ee` 已推送 `origin/feat/hutuji-usb-pipe`，Hub `c1e9305acb27df780990deb22c6fbda17049d1bb` 已推送 `origin/main`。
- Hub 代码与静态页：用 `auto_deploy.py` 从 `c1e9305` 的 LF blob 打包，共 5 个文件：`activation_relay.py`、`tg_notify.py` 和三份静态页。京东包 SHA-256 `3e18b18d…0209`，回滚号 `feda07bb9455486992d400c99170cb0a`；阿里包 `3fbd05d1…8ef5`，回滚号 `3d97acef44ae42dcab363a3e417cd498`。两节点带包 verify 均 `PASS services and file hashes`。公网三个入口 HTTP 200，正文都含 `#b03f5a`。
- 阿里第一次部署被拒，代码没有改动。原因是 `hutuji-curated-prep` 处于 auto-restart 循环，`is-active` 返回 `activating`，部署脚本判为状态不可读。这个服务以 hutuji 身份运行，`/opt/hutuji/curated-audit` 的属主却从 08-30 起就是 root，每轮都在写审计文件时报 `Permission denied` 退出，到 20:41 已重启 343 次。按 90s 重试间隔推算，循环大约从 09:47 HandRaw 部署重启它时开始。每轮还会往 journal 写约 1300 行，阿里的 journal 上限是 99M，这时最早只剩 18:23 以后的日志。处理：只执行了 `systemctl stop hutuji-curated-prep`，enabled 保持不变，然后重新部署成功。目录属主没改，灌库也没恢复，这两件要等用户决定。恢复命令是 `systemctl start hutuji-curated-prep`。
- 运维脚本：用 `git show c1e9305:` 导出，CR=0。两机先备份到 `/root/hutuji-bak-20261031-f53-ops/`，再用 `install` 替换。京东替换 `tg-ops-check.sh`、`tg-notify.sh`；阿里另外替换 `tg-relay-server.py`，并重启 `hutuji-tg-relay`（20:49:53 active）。远端 SHA：ops `3c0de341…2831`、notify `87ddfd6a…57cf`、relay `5223f7f7…d066`。两机都按 cron 原参数做了 `bash -x` 实跑，`msg` 为空；阿里检查了 redis、postgresql、sing-box，京东三项未启用，跳过。
- 小程序 0.3.35：微信开发者工具 CLI 上传开发版，exit 0，包体 323,833B，AppID `wxbf3c1e0013b46343`。只上传了开发版，没有设体验版、提审或发布。
- S3 `hutuji.1.0.55`：Freenove 镜像以不可变候选写入两节点 `s3/freenove-esp32s3-display-2.8-lcd/hutuji.1.0.55/`，两节点都返回 `CANDIDATE_OK`；公网下载 3,106,352B，SHA 与上表一致。目标 `b8:1f:3f:c3:70:bc` 绑定探活 HTTP 404，本机只有 COM4、COM5 两个蓝牙口，所以没有 OTA，`latest.json` 仍是 `hutuji.1.0.53`。立创镜像只构建、不发布。
- CI：Hub Tests `41201538910` 1/1；Hutuji S3 `41198134303` 3/3；Build Boards `41198138334` 175/175，都是 success。
- Hub 提交消息第 10 行有两个乱码字节，原文应为「钉住」。为了不强推 main，没有改写。

仍未关闭：
- 1.0.55 还没上屏，也没 OTA。
- 小程序没在手机上看过。
- 网页没有用生产账号逐页目视。

## 遗留项收口（2026-11-01）

提交 `c28793d029881f52c6ffa87ee56771f82473ca47`。

### 灌库服务的循环

两处都改了：`curated_prep.py` 在生图层抛任何异常时按 `UpstreamUnavailable` 中止本轮，不再把剩下的类目逐个送去把各后端打一遍；unit 的自动拉起上限从 `600s/20 次` 改为 `86400s/3 次`。旧值配 `RestartSec=90` 永远触不到上限——600 秒里最多拉起 6 次，门槛是 20 次，所以它只会一直循环而永不进 failed。新值一天最多 3 次，之后停在 failed 等巡检报警。

属主也修了：`/opt/hutuji/curated` 与 `/opt/hutuji/curated-audit` 下原有 12409 项不属 hutuji，`chown -R` 后为 0 项。以 hutuji 身份实跑 `--only cat` 成功写出 manifest（11-01 00:00），没有 Permission denied；该类目已满 52 项，所以没有调用生图接口。

服务保持 stopped + enabled。298 个类目里 60 个未满，启动就会真去请求生图后端，而后端仍是坏的（商汤 `sensenova-u1-fast` 退役、Agnes 403、魔搭 429、混元 success=false），切模型要用户定。恢复命令 `systemctl start hutuji-curated-prep`。

### 巡检盲区

`tg-ops-check.sh` 原来只看 failed 态与显式列出的服务名。`Restart=` 下崩溃的服务会被一直拉起，永远进不了 failed，所以那 343 次循环没有任何告警。新增两条判据：`SubState=auto-restart`（正在等下一次拉起），或累计重启 ≥3 次且最近一次启动在 1 小时内。手动 stop 后 `NRestarts` 不清零，所以按 `ActiveState` 排除 inactive/failed。

在两台机上用桩 `systemctl` 喂已部署脚本的这段逻辑，6 条真值表全对：auto-restart 命中、重启 6 次且刚起命中、已 stop 不命中、已 failed 不命中、健康常驻不命中、重启 2 次不命中。生产机上没有装崩溃循环单元。

### 小程序残留的深空青光

根因是 `styles/weui-page.wxss`：它被 8 个页面 `@import`，加载在 `app.wxss` 的主题之后，同优先级时后者胜出，所以首页和个人页仍是深底青字，按钮还带 `!important`。现在该文件只留间距与排版，颜色、底色、边框、阴影和 hero 外框全部交还主题。

页面层 14 份 wxss 加 `paper-summary.wxss` 的暗色时代取值改为奶油令牌：灰蓝次要字统一 `#735f69`、青色强调改 `#b03f5a`、青绿改 `#3b7564`、深底改 `#fffdf8`/`#fdebdc`、白色半透明细线改 `rgba(77, 58, 70, …)`。被主题 `!important` 盖住的死声明直接删掉，留着会在主题改动时冒出来。顺带把控制页的错误横幅排成一行（文字在左、刷新钮在右）。

`handdraw_theme.test.js` 的层叠解析器补上页面 `@import` 展开（weui-page、paper-summary 按出现位置排在主题之后），新增一项扫描全部页面：页面层不得出现深色底、浅色字、青蓝色相（色相 175–250° 且饱和度 >0.2，薄荷 160° 与薰衣草 263° 不在内）或白色半透明线，自定字色在奶油底上须 ≥4.5:1。主题自身的选中态暗莓底配奶油字是设计真值，不在判定范围内。旧样式报 564 处，新样式 0 处。

微信开发者工具自动化逐页截图复核：首页、个人页、控制页、聊天页、手绘页、历史、商城、纸张、分享、固件、配网、小派设置都是奶油底，暗色卡片和青色头像框都没了；控制页错误横幅已排成一行。

### 提交消息乱码

`c1e9305` 第 10 行的两个坏字节已推送。不改写历史，改用 git note 记正文：`git notes show c1e9305`。

### 验证

| 项 | 结果 |
|---|---|
| MCP pytest | 1706 passed |
| 小程序 node | 377/377 |
| 枢纽门禁 | overall=pass（测试计数行指纹随计数更新，已 `--accept-line-refs` 重登；readiness 顶部插入导致的 2 处纯行号下移用 `--fix-line-refs` 重定位） |
| 运维巡检实跑 | 两机 `msg` 为空、exit 0；循环判定 6/6 |
| 灌库实跑 | 阿里以 hutuji 身份 `--only cat` 成功，无 Permission denied |

部署：两机 `tg-ops-check.sh` 远端 SHA-256 `8c9a798d…a667`，阿里另装 `curated_prep.py`（`83a46efe…346e`）与 unit（`b8841a30…cef1`），`daemon-reload` 后 `StartLimitIntervalUSec=1d`、`StartLimitBurst=3`、`NeedDaemonReload=no`。备份在两机 `/root/hutuji-bak-20261101-f53fu/`。京东没有 curated-prep 服务，只换了巡检脚本。

小程序 0.3.36 上传开发版，321,479B；仍未设体验版、未提审、未发布。

这批没碰：1.0.55 仍未 OTA、未上屏（目标 `b8:1f:3f:c3:70:bc` 探活 404、本机无可用串口）；小程序没在手机上看；网页没有用生产账号登录后逐页目视。
