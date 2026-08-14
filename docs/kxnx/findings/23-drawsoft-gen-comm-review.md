# 23 · 奎享深度 review 全量收口（生成链 + 通信链 × GitHub 社区基线）

> 结论先行：对奎享反编译源码做「G-code 生成链 + Grbl 通信链」双链路深审并对照 GitHub 社区基线后，**hutuji 侧零代码改动**——缺陷/风险类发现逐条实证后，要么是 hutuji 已实现并钉死的契约（ALARM 停发、看门狗、行重组、旁路 `G92 Z0`），要么是既有 findings 已裁决的「有意不对齐」（pollLast 续传、批量 flush、判死阈值）。本轮真实产出：①三条 hutuji 现状表述勘误（看门狗/行重组/G92，见 §7）；②`docs/kxnx/README.md`「`kx/windows_hutuji/decompiled/` 不存在」一行过时——该树存在且是本轮基准树；③厂商「生成在云端闭源服务」这一架构事实的完整证据链（反面印证 hutuji 生成可审计路线）。
>
> **行号基准树**：`D:/Users/kxnx/kx/windows_hutuji/decompiled/`（kxnx 仓第四棵树：Windows 版 kdraw.jar 反编译、清理后 291 文件，**A 混淆体系**，`machine/f/b.java` 为 CommandSender）。实测该树行号比主树 `decompiled_java/` **小 3**（如 `machine/grbl/a.java` 的 `g()=100`：本树 312-314，主树 315-317），与 `decompiled_full/` 同口径——引用前先看本节，三树行号不通用（见 `docs/kxnx/README.md` 反编译源码树表）。
>
> 日期：2026-08-14。方法：3 路并行子代理（生成链 / 通信链 / 社区基线调研），主代理对全部高载证据回源码逐条复核。证据记号：【已复核】= 主代理亲读源码确认；【子代理证据】= 子代理取证、主代理抽核主链后采信。
>
> 与既有 findings 的关系：本文是**全量复审**，逐点细节剖析以专题文档为准——流控窗口 findings/09、传输层 findings/10、路径参数 findings/13、暂停/abort/error findings/19、重连 pollLast 缺陷 findings/17/20、看门狗 findings/21、生成链 findings/07。本文增量 = 生成链云端化证据（§2-H1）+ 逐条分诊表（§9）+ 三条勘误（§7）。

---

## 0. 结论速览

**最重要的一个架构事实**：奎享的 G-code **逐行拼装不在本地**——本地只把几何路径（GZIP 二进制）+ 参数 JSON POST 到云端 `/kvenjoyweb/drawsoftapp`，G-code 文本由闭源云服务生成后流式下载。头尾模板、路径排序/简化、限幅 clamp、页尾处理全部在云端，**本地不可审计**。这从反面印证了 hutuji「生成逻辑必须本地可审计」的路线。

**对 hutuji 的可行动结论**（详见 §4；2026-08-14 经 `protocol.md` 对账修正，原表述两处失实见 §7 勘误）：

1. **断线续传的正确姿势**：奎享只重发队尾一条、窗口计数失真（缺陷，findings/17 已完整剖析），hutuji 保持断连整体重画；其 `WPos==0,0 拒续传` 的位置校验思路已在 findings/20 记录为「禁止对齐」。【已复核】
2. **看门狗阈值对照（调参项，非新功能）**：hutuji 已有 60s 无 `ok`（换纸行 90s）→ `?` 探活 → 任务 error 的应用层看门狗（`protocol.md` §7），叠加 §1.2 三层 keepalive（`TCP_KEEPIDLE=10/KEEPINTVL=3/KEEPCNT=3`，写字机断电 ≈19s 发现）。奎享 5s+1s 更激进——只构成「是否收紧阈值」的对照，不构成「hutuji 缺断链检测」。奎享超时从**最后写入**起算，写路径活跃时不会误判。【已复核】
3. **行重组 = 印证，不是借鉴**：hutuji `protocol.md` §3 已明写「S3 必须重切行，禁止原样透传下载字节」并点名 `ParseLines()`「是契约要求，不是实现细节」。奎享 2048B 缓冲 + 分隔符 split + 残行压回是同一做法的独立实现，双向印证成立。

---

## 1. 架构总览

### 1.1 生成链地图（本地止步于路径，G-code 在云端）

```
b.i（内容→路径编排：单位换算收口 2 位小数 / 对齐 / 网格排版 / 手写随机变形 / 单线字体查找）
  → b.e / b.a / b.g（.gfont 单线字体 = ZIP，按字符懒加载字形 → b.o 路径对象；SoftReference 缓存）
  → b.o（路径模型：段类型 0=move / 1=line / 2=bezier + float[] 坐标；迭代器按 {2,2,6} 取数；
        变换挂迭代钩子不改写数据；n() 二进制序列化）
  → h.c（GZIP 压缩路径流 + PathParameters/CreaterParameters JSON + 路径包围盒
        → POST /kvenjoyweb/drawsoftapp → 流式下载 G-code 文本）
  → machine 层消费：Grbl/Marlin/EBB 仅发送 + b(boolean) 抬落笔指令；
                    KAT 逐行重写注入 F 值 + 文件式打印
```

【已复核】`com/kenjoy/kenjoycnc/h/c.java:155-215`：HttpPost `/kvenjoyweb/drawsoftapp`；`pathParam`/`createrClazzName`/`createrParam`/`pathsBounds{Left,Right,Top,Bottom}` 全量上传；响应 GZIP 流读回 `this.j`。

### 1.2 通信链地图（字符计数窗口 + 事件驱动任务流）

```
machine/b/a（命令发送器：2048B TX/RX 行缓冲 + LinkedBlockingDeque 行队列 + 读线程 + 1s keepalive 定时器）
  → 传输层：b/f（串口 jssc，DTR/RTS 复位）/ b/d（TCP :23，10s 连接超时）+ b/e（128B 网络读线程）
  → 驱动基类 machine/c + grbl/a（握手：Grbl 启动串 → 版本 → 发 $I；
        ok → 滑窗；<...> 状态行 → 解析 state|WPos + 复位看门狗）
  → f/f（窗口队列管理器，按 driver.g()=100 字符在途窗口填充）
  → f/b（字符计数窗口：c 待发 / d 在途，ok 逐个滑动并补窗，批量 flush）
  → f/i / f/l（任务编排：M998/M1100/M1103/paper-ready 事件驱动多页/多份任务流）
```

任务生命周期：连接 → 握手 → 任务开始（0x18 软复位预热 → G92 定零 → 窗口流）→ 完成（抬笔 → `G90G0 X0Y0` 回原点，或 KAT M998 换纸 → `paper ready` 事件 → 下一文件）。异常：无 ok 看门狗 5s+1s → `?` ping → 断线；`error:N` → 清窗中止；KAT 20s 无 `[ESP]` 消息断线。

【已复核】`machine/f/b.java:50-81`（窗口填充 + 批量 flush）、`machine/grbl/a.java:312-314`（`g() { return 100; }`）。

---

## 2. HIGH 发现

### H1. G-code 拼装在云端闭源服务，生成+通信双重网络依赖【已复核】

- 证据：`h/c.java:155`（HttpPost）、`h/c.java:163-166`（createrClazzName/createrParam 上传）、`h/c.java:199-210`（GZIP 流式读回 G-code）。
- 细节：本地生成链止步于 `List<b.o>` 路径 + 参数。头尾模板、排序/简化、限幅、页尾全部服务端实现；服务不可用即全链失效。参数里仅见 `optimizePaths`/`nearDst` 开关（排序在云端做）。
- **反哺 hutuji**：反面印证。hutuji 的 MCP 生成服务代码全量在本仓、可审计、可离线复现（`e2e_local.py`），这是相对厂商的结构性优势，必须保持。厂商的 `b.o.n()` 二进制路径序列化 + GZIP 是紧凑的 wire contract，可作 hutuji 路径传输格式对照（比逐行文本省体积，512KiB 预算下有意义）。

### H2. 本地零行程 clamp / 零负坐标防护，安全防线在不可审计的云端【已复核】

- 证据：`lib/b/i.java:365`（`float f14 = f7 - f6 - f4;` 内容宽=页宽-边距，无保护）、`lib/b/i.java:815-817`（GridData 格宽=(宽-间隙×(列数-1))/列数，间隙过大为负仍继续排版）、`h/c.java:171-175`（仅包围盒上报云端）。
- 细节：布局层假定参数自洽；负格宽 → `Math.min(负,负)` → 负字号 → 镜像翻转字形 + 负坐标笔画，静默无警告。机器若无限位开关即撞机。
- **反哺 hutuji**：直接印证「MCP 侧限幅是唯一安全防线」的必要性，厂商把防线放云端是反例。注：奎享的网格/分栏排版在 hutuji 无对应物（hutuji 走 vpype layout 归一化），「布局参数级校验」一项 N/A；hutuji 的防线是 `validate_gcode()` 指令级限幅 + 负坐标硬拒（findings/17 §对照表已记），已定案。

### H3. 断线重连续传只重发在途队列队尾一条，窗口计数失真【已复核】

- 证据：`machine/f/b.java:83-88`（`d()` = `pollLast()` 只取在途队列**最后一条**重入队）、`machine/f/f.java:149`（收 FirmwareEventReconnected → 置位 → 调 `d()`）、`machine/ui/e/k.java:62`（WPos≠0,0 才发重连事件）【子代理证据：f/f.java、k.java】。
- 细节：重连后窗口内其余已发未 ack 命令既不重发也不清除，`e`（在途字符计数）不修正——后续 ok 会错误弹掉旧条目并误扣计数，导致几何缺行/错位且窗口逐渐失真。`WPos==0,0`（机器被复位）时提示 redraw_failed 不续传。注：autoReconnect 默认 false，触发面有限。
- **反哺 hutuji**：findings/17/20 已完整剖析并定案「有意不对齐」——hutuji 断连后整页重画，正确性严格更优。本条无新增动作。

---

## 3. MEDIUM 发现

### M1. toolOnDelay/toolOffDelay 对 Grbl/Marlin 悬空：全链无 G4【已复核】

- 证据：`model/Settings.java:33`（默认各 0.2s）；`machine/grbl/GrblSettings.java:111`（仅复制进 createrParam 上传云端）；`machine/ebb/a.java:69`（只有 EBB 本地落地，编码进 `SP` 指令毫秒参数，且抬落笔都用 toolOnDelay）【子代理证据：三处均经主代理对 grep 结果抽核——全树 `G4` 字面量零匹配】。
- **反哺 hutuji**：厂商本地不发 G4 与 hutuji「禁 G4」契约同向（§5 允许列表）。若未来需要落笔稳定延时，参考 EBB 做法——把延时编码进抬落笔指令本身，而不是引入 G4。

### M2. 手写随机变形不可复现：全局 Random 无种子【子代理证据】

- 证据：`lib/c/i.java:13`（`this.setSeed(new Random().nextLong())` 类初始化随机播种）；`lib/b/i.java:943`（randomXScale/YScale/Rotate 应用点）；`lib/b/o.java:376`（角度抖动 ±1×angleStrength、长度缩放 0.3..1.0×lengthStrength）。
- 细节：同一文本每次生成路径不同；预览与实际生成不一致；云端重试/限幅复核无法复现。
- **反哺 hutuji**：hutuji 现行生成链（位图描摹）无随机变形环节，本条为厂商缺陷记录，不引入动作。若未来做「手写感」，随机源必须按输入内容播种（如 `sha256(text)[:8]`），保证同一输入 → 同一 G-code。

### M3. Marlin `Resend:` 处理=清空窗口不重发，任务静默截断【已复核（行号纠偏）】

- 证据：`machine/marlin/a.java:23`（`Pattern.compile("Resend: ([0-9]+)")`）、`marlin/a.java:61-64`（解析行号发 FirmwareEventResend）、`machine/f/b.java:149-153`（Resend 分支 `c.clear(); d.clear(); e=0`，**不重发任何命令**）。
- 细节：发送路径只发「命令+\r\n」无行号（无 N 字段、无校验和），Marlin 在无行号模式下通常不触发 Resend——潜伏缺陷，一旦触发即静默丢任务。
- **反哺 hutuji**：findings/15 已定论「resend 是 Marlin 行号重传协议的残留，Grbl 路径用不到，hutuji 不用理」。本条仅补一处行号纠偏（findings/19 §4.3 引 `marlin/a.java:64-67` 为主树行号，本树为 61-64）。

### M4. 暂停/停止语义与缓冲排空风险【子代理证据，主链已复核】

- 证据：`machine/grbl/a.java:296-298`（收尾命令 `G90G0 X0Y0`【已复核】）；`machine/f/f.java:67-74`（暂停=入队抬笔、停止=抬笔+回原点）【子代理证据】；`machine/ui/a.java:583`（任务运行中禁用 jog 面板）【子代理证据】。
- 细节：串口 Grbl 的「暂停」不是发 `!`（feed hold），而是**停止下发并补一条抬笔**——机器会把 planner 缓冲跑完才真正停，期间物理干预（换纸/碰笔）有风险。停止=抬笔+回原点优雅收尾，不发 0x18。ESP32-SD 路径才用 `!`/`~`。
- **反哺 hutuji**：findings/19 已完整剖析（普通 stop 与 reset2 双路径）。印证 hutuji「停止语义 = 等 fresh Idle 再物理干预」：页尾 fresh-`Idle` 后才由 S3 单发 M30 触发换纸。注意反向风险：hutuji profile 故意不以回原点收尾（回本机触发换纸），奎享的 `G90G0 X0Y0` 收尾**不可**照抄（§5 对照表同）。

### M5. 协议面：Telnet 裸流无鉴权，XOR+opcode 分帧仅防误操作【子代理证据】

- 证据：`machine/c/a.java:162`（ESP32 上传走 `http://{ip}/upload`，无鉴权参数）；KAT opcode 体系（99 握手 / 105-106 WiFi 配置 / 913 文件列表 / 914 停止等）经 `lib.a.a(opcode, payload)` 编码，TCP 流明文 + XOR 掩码（掩码值来自 `echo:ID:` UUID）【子代理证据】。
- 另：`lib/a.java:5` 存在硬编码默认口令字段 `esp_admin_pwd`（默认 auth 关闭；**按约定不复述值**）【子代理证据】。
- **反哺 hutuji**：厂商缺陷记录。hutuji 侧对应面已有约束：三仓矩阵禁止「接受未通过 Grbl banner 验证的 :23 候选」，S3 探活链（banner/`$I`/`G1 G53` 授权探针）已在 protocol §2 定案。XOR 弱加密不学。

### M6. 传输健壮性缺口【子代理证据】

- 证据：`machine/b/d.java:43`（write+flush 无 SO_SNDTIMEO/SO_RCVTIMEO）、`machine/b/e.java:22`（读线程 `available()<=0` 时 `continue` 自旋 busy-loop）、串口侧 jssc 8N1 115200 + RTS/DTR 复位（`b/f.java:39,102`）。
- 细节：TCP 半开连接下阻塞写可永久卡死发送线程，断线检测全靠读线程 IOException（不可靠），故厂商用应用层看门狗补偿（串口 5s keepalive / KAT 20s `[ESP]` 静默断线）。无多连接互斥、无重连退避（立即重试）。
- **反哺 hutuji**（2026-08-14 对账修正）：「断链检测盲区」表述收回——S3→写字机方向已有 `protocol.md` §1.2 三层 keepalive（≈19s）与 §7 的 60s/90s 看门狗，云端→S3 方向 cloud_bridge 已有 300s 看门狗（测试钉死）。findings/10 已裁决：奎享读线程自旋**不学**（hutuji 接收侧为独立读线程忙轮询的对应面已记录差异）、批量 flush **暂不改**（lwip 有内部缓冲，瓶颈在逐行 RTT 不在 syscall 数）。本条无新增动作。

---

## 4. INFO 借鉴点（反哺 hutuji 主清单）

### I1. 流控：字符计数在途窗口 = 100 字符 + 批量 flush + singleStep 降级【已复核】

- 证据：`f/b.java:50-81`（窗口条件 `e + len + 1 < g()`，批量后一次 `s().h()` flush）；`grbl/a.java:312-314`（`g()=100`）；`f/f.java:117`（顶层预填同窗）；`f/b.java:66`（singleStep 标志：在途非空即停，退化为逐行等 ok）。
- 对照社区：grbl 官方 character-counting 协议，RX 缓冲 128 字节（[gnea/grbl wiki · Grbl v1.1 Interface](https://github.com/gnea/grbl/wiki/Grbl-v1.1-Interface)）；cncjs 硬编码同协议（[cncjs#252](https://github.com/cncjs/cncjs/issues/252)）；UGS 用 `LinkedBlockingDeque` + sentBufferSize 计数（[UGS AbstractController](https://winder.github.io/ugs_website/dev/backend_development/)）。奎享 100 < 128，留 28 字节余量，属保守正确。
- **反哺 hutuji**：①印证 512B 在途窗口同构（findings/09/17 已记；窗口是否回退 100–256 挂 echo 二分定案，findings/17 §末）；②批量 flush findings/10 已裁决「暂不改」；③singleStep 降级选项与逐行等 ok 模式同义，不新增。

### I2. 看门狗双子：写后无回包 5s+1s，状态行 20s 静默断线【已复核】

- 证据：`machine/b/b.java:19-36`（keepalive TimerTask：写后超 connectTimeout(5s) 无回包 → 先 `driver.f()`（发 `?`）→ 下 tick 仍无 → `b(false)` 断线）；`grbl/a.java:388-392`（socket 传输每秒 `?`，`r>=20` 即 20s 无 `<...>` 状态行 → 强制断开）、`grbl/a.java:79`（收到状态行 `r=0`）。
- **反哺 hutuji**（2026-08-14 对账修正）：原「直接可移植」表述收回——hutuji 已有应用层看门狗（`protocol.md` §7：60s 无 `ok`/换纸行 90s → `?` 探活 → 任务 error），桥接侧 300s 判死阈值与奎享 5s+1s 的差异 findings/17 §对照表已定案「有意不对齐」。本条残留价值仅写法参考：超时从**最后写入**起算而非最后收包，写路径活跃时不误判。

### I3. 实时命令字节直写，绕过窗口计数【已复核】

- 证据：`grbl/a.java:407`（`H()` = `g.a((byte)63)` 发 `?`）、`grbl/a.java:436`（`A()` = `(byte)33` 发 `!`）、`machine/c/a.java:151`（0x18 软复位直写）；`grbl/a.java:304-308`（`$J=G21G91...` jog，版本 ≥1.1 判定）【已复核】。
- 对照社区：grbl 官方——实时命令**不占 RX 缓冲**，随时可发（[Grbl v1.1 Interface](https://github.com/gnea/grbl/wiki/Grbl-v1.1-Interface)）。
- **反哺 hutuji**：印证 hutuji 禁实时字符进文件、旁路单发的决策（§3）。

### I4. 应答处理矩阵：ok 滑窗 / error:120 静默 / error:9 弹窗 $X / 其余清窗中止【子代理证据】

- 证据：`grbl/a.java:47`（`equalsIgnoreCase("ok")` → 滑窗）、`grbl/a.java:89-93`（error:120 静默忽略；error:9（ALARM 锁）弹窗引导 `$X`）、`f/b.java:149-153`（其余 error → 清窗中止，无重试）。
- **反哺 hutuji**（2026-08-14 S3 实证）：原「可补 ALARM 分类处理」表述收回——**S3 已实现且更完整**：`hutuji_pipe.cc` 解析 `ALARM:` 行置 `GrblState::Alarm` + 记录 alarm_code + NotifyCloud；`hutuji_job.cc` 全部五个等待点（流发送/M30 等待/换纸等待/分段等待）判到 Alarm 即置「写字机报警 (ALARM:n)」任务 error 停发；未知 Alarm 不自动 `$X` 解锁（仅 abort reset 自锁可解）。印证成立，无动作。

### I5. 任务边界：0x18 预热 + G92 定零 + `$I` 握手识别【已复核】

- 证据：`grbl/a.java:250-260`（Stepper 首次笔控前发 `G92 Z0`）；`grbl/a.java:246`（连接钩子发 `$I` 读 build info）；`grbl/a.java:60`（`$$` 状态机读 `$N=` 行入 kv）【已复核】；握手识别：grbl 看 `Grbl` 前缀、marlin 看 `Marlin`、kat 看 `K_AT`+UUID【子代理证据】。
- **反哺 hutuji**（2026-08-14 对账修正）：①G92 对比原表述失实，已改——双方都把 `G92` 排除在下载文件外、在会话内校准：奎享由驱动在首次笔控前内嵌发 `G92 Z0`（grbl/a.java:255），hutuji 由 S3 在 fresh `Idle`+100ms 后**旁路**发固定 `G92 Z0`（`protocol.md` §5；文件头两次幂等 `G1G90 Z0.0F10000` **不建立** Z 基准，findings/09 §六早已勘误同口径）。hutuji 的差异优势是校准点显式受控（fresh Idle 门 + 固定 100ms），不是「消除 G92」；**S3 旁路 `G92 Z0` 是安全关键校准，不得删除**。②厂商连接后发 `$I` 读 build info 做固件识别；hutuji S3 已有 `$I` 探活（裸连接授权探测，R10-PIPE-01 有界重试）与 banner 复位语义解析（§2），身份核验面已覆盖，无动作。

### I6. 收尾与换纸：paper-ready 事件流；KAT 进纸序列；awauto 软限位序列【已复核】

- 证据：出图流**无 M30**——收尾=抬笔+`G90G0 X0Y0` 回原点（`grbl/a.java:296-298`【已复核】）；`machine/e/a.java:45-56`（`paper ready` / `remove paper` / `paper removed` 固件事件）；KAT 进纸 `M106 → G0 Y{dist} F6000 → M400 → M107`（`lib/kat/a.java:14-17`【子代理证据】）；awauto 进纸 `$X, $21=1, G91 G0 X-30 Y30(±Z), G91 G0 Y350` + `repeatPaperTime`（默认 20s）定时续纸（`awauto/g.java:55-62`【已复核】）；`Settings.homeDelay` 倍数 N 页回 home【子代理证据】。
- **勘误（主代理复核纠偏）**：子代理称「全库 M30 零匹配」**不成立**——`machine/ui/h/b.java:115` 有 `c2.a("M30 " + file, true)`，但属 SD 卡文件管理面板（邻近 M21/M23/M24，Marlin 语义 `M30 <文件>`=删文件），**不在出图链路**。修正后结论：「奎享出图流不发 M30，用回原点+软件判完成替代」成立。
- **反哺 hutuji**：①hutuji 页尾 M30 push 模型 vs 奎享「回原点 + paper-ready 事件」pull 模型——hutuji 方案对纯 Grbl 无传感器机器更简单（已定案，外部印证）；②awauto `$21` 软限位开关序列与 KAT `M106→G0Y→M400→M107` 进纸时序仅作机构参考存档——hutuji 换纸机械/时序**禁改**（§9 白名单外），不产生动作。

### I7. 生成侧工程细节（值得学的零件）

| 零件 | 证据 | 借鉴点 |
|------|------|--------|
| 坐标统一 2 位小数收口 | `lib/b/i.java:120` `d()` = `round(f*100)/100`；换算全走它【已复核】 | 参照存档；hutuji 输出形状由 profile + 钉死测试锁定，**不为省体积调精度**（改形状=改契约） |
| F 双速分离 + 质量档 | `model/Settings.java:32`（feedRate 1500 / jogSpeed 2500）；`kat/KATSettings.java:252`（质量档映射 High=1500…VeryLow=5000）【子代理证据】 | 参照存档；hutuji 画线空走同 F8000 已定案（真实产物口径，README「三句话结论」第 3 条） |
| 抬落笔五档契约 | `grbl/a.java:263-284`（Servo/Laser=M3S/M5；Stepper=G1G90 Z F；Servo_bachin=M3S 角度；Custom=自定义）【已复核】 | 与 findings/01/06 一致；hutuji 对齐 Stepper 已定案 |
| .gfont 单线字体 | `lib/b/e.java:150-153`（ZIP 按字符懒加载）、`lib/b/g.java:31`（SoftReference 缓存重建）【子代理证据】 | 参照存档：单线字体=路径字典，优于系统字体描边；hutuji 现行走位图描摹路线，无对应环节 |
| 手写变形管线 | `lib/b/o.java:376-380`（拍平贝塞尔→Douglas-Peucker 简化(skeletonStrength)→逐段抖动）【子代理证据】 | 参照存档：一参三用（简化容差+角抖+长缩）、变形必须在拍平后做；`simplePaths=0.02`（`PathParameters.java:20`）与 hutuji `LINESIMPLIFY_TOLERANCE_MM=0.05` 同量级 |
| 路径模型 wire contract | `lib/b/o.java:63,268`（段类型 0/1/2 + float 坐标）、`lib/b/r.java:12`（{2,2,6} 取数表）、连续 move 合并 | 参照存档：「变换挂迭代钩子不改写数据」是干净设计 |
| 自检：bounds 上报 + 笔程统计 | `h/c.java:171-175`（包围盒随参数上传）；KAT 笔程统计输出米数（`kat/b.java:296-303`）【子代理证据】 | 增强候选（bounds 回传/笔程统计），超出本轮「缺陷/风险类」口径，未采纳 |

---

## 5. 与 hutuji 契约总对照表

| hutuji 已定案 | 奎享对应行为 | 结论 |
|---|---|---|
| 512B 在途窗口 Telnet 转发 | 100 字符计数窗口（<128B RX） | **印证**，同构；奎享更保守 |
| `G1G90 Z5.0/Z0.0 F10000` 抬落笔 | Stepper `G1G90 Z{zOn/zOff} F{zSpeed}` | **印证**，句式逐字同构（findings/01 已对齐） |
| 禁 G92 进下载文件，S3 旁路 `G92 Z0` 校准（§5） | 奎享同样不进文件，由驱动会话内嵌 `G92 Z0`（grbl/a.java:255） | **同构印证**：双方均「文件无 G92 + 会话内校准」，注入点不同（S3 fresh Idle 旁路 vs 驱动首笔前内嵌） |
| M30 旁路单发不进文件 | 出图流不发 M30（回原点+软件判完成） | **同向印证**；push/pull 模型差异见 I6 |
| 禁 G4 | 本地全链无 G4（延时语义悬空） | **同向**；EBB 指令内嵌延时是备选 |
| 禁 G0 落 X0Y0 / 不以回原点收尾 | 收尾固定 `G90G0 X0Y0` | **行为相反**——奎享回原点不触发换纸，hutuji 会；各机契约不同，勿互抄 |
| MCP 侧限幅是唯一安全防线 | 限幅在不可审计云端 | **反面印证** hutuji 路线 |
| 云端生成、代码可审计 | 生成在云端且**闭源不可审计** | **反面印证** hutuji 路线 |
| WiFi Telnet :23 | esp32grbl 固定 port 23（ui/esp32grbl/a/c.java:59） | **印证** |
| ALARM 停发 + 不自动解锁 | error:9 弹窗引导 `$X`（人工） | **印证**：hutuji S3 判 Alarm 即任务 error 停发，未知 Alarm 不自动 `$X`（§4-I4 实证） |

---

## 6. GitHub 社区基线对照（调研代理产出，主代理校订）

> 完整参考文献与引文见调研代理原始输出；此处仅留结论与关键链接。调研代理两处错误已校订（见文尾勘误）。

| 主题 | 社区基线 | 奎享 | hutuji |
|---|---|---|---|
| 流控 | character-counting，128B RX（[gnea/grbl wiki](https://github.com/gnea/grbl/wiki/Grbl-v1.1-Interface)）；cncjs 硬编码同协议；UGS Deque 计数 | 100 字符窗口 ✓ 合规范 | 512B 窗口（S3 全缓冲转发，路径不同） |
| 实时命令 | `? ! ~ 0x18 0x85` 不占 RX（同 wiki） | 字节直写绕窗口 ✓ | 禁入文件+旁路 ✓ |
| error/ALARM | `$X` 解锁、`$H` 回零（[v1.1 Commands](https://github.com/gnea/grbl/wiki/Grbl-v1.1-Commands)） | error:9 弹窗引导 `$X` | S3 判 Alarm 停发 + NotifyCloud，不自动解锁 ✓ |
| 抬落笔 | 舵机 M3S/M5（[grbl_pen_servo](https://github.com/bdring/grbl_pen_servo)）、M280（Marlin）、步进 G1 Z | 五档全支持 | 步进 G1 Z（已定案） |
| G-code 模板 | vpype-gcode gwrite profile（document_start/segment_first/line_end…，[vpype-gcode](https://github.com/plottertools/vpype-gcode)） | 模板在云端 | hutuji_gwrite.toml（已定案） |
| Telnet :23 | Grbl_Esp32 Telnet 服务（[bdring/Grbl_Esp32](https://github.com/bdring/Grbl_Esp32)）；hutuji 实机 `MAX_TLNT_CLIENTS=1` | 固定 :23 ✓ | :23 + 单客户端（已定案） |
| M30 | 程序结束+模态复位；`M30 <file>` 在 Marlin=删 SD 文件 | 出图流不用 M30 | S3 页尾旁路单发（已定案） |

---

## 7. 勘误与复核记录

1. **子代理「全库 M30 零匹配」纠偏**：`machine/ui/h/b.java:115` 存在 `M30 <文件名>`，SD 卡管理面板（Marlin 删文件语义），不在出图链路。结论修正为「出图流无 M30」。
2. **调研代理 `0x85` 释义纠偏**：调研代理标「Feed Override Cycle Start」错误；grbl 1.1 中 `0x85` = **Jog Cancel**（[v1.1 Commands](https://github.com/gnea/grbl/wiki/Grbl-v1.1-Commands)），与通信链子代理的代码取证一致。
3. **调研代理「grbl_esp32 多客户端」表述纠偏**：其引用的 5 客户端上限是 WebSocket 语境；hutuji 实机 Telnet 为 `MAX_TLNT_CLIENTS=1`（hutuji 仓实测口径），以此为准。
4. **findings/01 时效备注**：01 中「hutuji 现状=M3+G4」列已过时——hutuji 现行契约是 `G1G90 Z5.0F10000` / `Z0.0F10000`（protocol.md §5），即 01 的「对齐方向」已落地。
5. 主代理亲读复核：`h/c.java:155-215`、`f/b.java:50-98,140-153`、`grbl/a.java:240-320,388-392,407,436`、`b/b.java:19-36`、`marlin/a.java:23,61-64`、`awauto/g.java:46-62`、`esp32grbl/a/c.java:48-62`、`ui/h/b.java:89-118`、`CreaterParameters.java`、`GrblParameters.java`。其余发现为子代理证据 + 抽核。
6. **2026-08-14 protocol.md 对账勘误（外部指正，属实）**：原 §0 两条失实已改——①「hutuji 缺 TX 侧断链检测」不成立：`protocol.md` §7 已定「60s 无 `ok`（换纸行 90s）→ 发 `?` 探活，仍无则任务 error」，§1.2 另有三层 keepalive（≈19s）；奎享 5s+1s 仅为更激进的阈值对照。②「行重组」不是借鉴项：§3 已明写 S3 必须重切行、`ParseLines()` 是契约要求；奎享实现只构成印证。M6/I2 的反哺表述同步修正。
7. **2026-08-14 G92 勘误（外部指正，属实）**：I5 与 §5 对照表原称「hutuji 用文件头两次幂等 Z0 抬笔建基准、消除 G92 状态依赖」失实——`protocol.md` §5 明确两次幂等 `G1G90 Z0.0F10000` **不建立 Z 基准**；Z 基准由 S3 在 fresh `Idle`+100ms 后旁路 `G92 Z0` 建立，`G92` 仅禁止进入下载文件（`docs/kxnx/findings/09-flow-control-deep.md` 同样已勘误双方完整会话都旁路 G92）。已改 I5 反哺①与对照表行；该旁路校准是安全关键路径，任何「优化」不得删除。
8. **2026-08-14 分诊期修正**：I4 原「可补 ALARM 分类」与 §6 表「待补」表述收回——S3 实证已实现（见 §4-I4）；H2 的「布局参数级校验可补强」标 N/A（hutuji 无网格排版）；I7 表两条从「建议」降为「参照存档/增强候选」。

## 8. 审查局限

- 反编译+混淆产物：`a/b/c` 符号职责为推断命名，个别控制流（CFR "aggressive block sorting"）可能与源序不同。
- **云端生成器不可见**：G-code 逐行模板、限幅、排序的真实实现无从审计，相关结论只能标「本地未见」。
- 纯 UI 包（`processTabs/`、`machine/ui/` 除 esp32grbl）未深读；`kenjoycnc/f/**` 批量入口仅抽查。
- 未做任何动态验证（无真机、无云端账号）；全部结论为静态代码证据。

## 9. 分诊总表（2026-08-14 全量修复收口，口径 = 缺陷/风险类）

| 发现 | 裁决 | 依据 |
|------|------|------|
| H1 云端生成不可审计 | 印证·无动作 | findings/13 已录 POST 链；hutuji 生成在本仓可审计 |
| H2 无本地 clamp | 印证·无动作 | `validate_gcode()` 限幅 + 负坐标硬拒已定案；布局级校验 N/A |
| H3 续传缺陷 | 既有裁决·无动作 | findings/17/20「有意不对齐」，hutuji 整页重画 |
| M1 G4 悬空 | 印证 | §5 允许列表禁 G4 |
| M2 随机种子 | 驳回 | hutuji 无随机变形环节；新功能超出口径 |
| M3 Marlin Resend | 驳回 | findings/15：Marlin 残留，Grbl 路径用不到 |
| M4 暂停语义 | 印证·无动作 | findings/19；hutuji fresh Idle 判定已定案 |
| M5 凭证/协议面 | 厂商缺陷记录·无动作 | 矩阵禁未验证 :23 候选；S3 探活链已定案 |
| M6 传输健壮性 | 既有裁决·无动作 | findings/10：自旋不学、flush 暂不改 |
| I1 窗口/flush/singleStep | 印证·无动作 | findings/09/10/17 |
| I2 看门狗 | 印证（勘误后） | §7 + §1.2；findings/17 阈值「有意不对齐」 |
| I3 实时命令 | 印证 | §3 实时旁路 |
| I4 ALARM 分类 | 印证·S3 实证已实现 | `hutuji_pipe.cc` ALARM 解析 + `hutuji_job.cc` 五等待点停发 |
| I5① G92 | 印证（勘误后） | §5 旁路校准，findings/09 同口径 |
| I5② `$I` 身份核验 | 印证·无动作 | R10-PIPE-01 `$I` 探活 + banner 解析已有 |
| I6 换纸序列 | 驳回改动·参照存档 | §9 禁改换纸机械/时序 |
| I7 工程零件 | 驳回/参照存档 | 增强类超出口径；findings/13/14 已录 |

**本轮代码改动：本仓业务 0 行、S3 固件 0 行、Grbl 0 行。** 文档动作：本文件入库 + `docs/kxnx/README.md` 第四棵树登记（「不存在」行已过时）+ 轮次记录。固件观察项新增 0 条（I2 阈值对照、I5② 身份核验均已在既有 findings/矩阵口径内）。
