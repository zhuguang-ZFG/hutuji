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
