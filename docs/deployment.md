# 部署（2026-08-23 起：阿里云主 / 京东云热备；历史章节为京东云口径）
> **2026-08-28 下午 骨架续接 + 短 G1 地板 + 异步生成（默认关）双机发布（本仓 `34b44d4`，含 `6b07506`/`2cbec59` 两笔欠账）**：6 产物（`bitmap_svg.py`/`config.py`/`curated.py`/`gcode_gen.py`/`hutuji_gwrite.toml`/`server.py`）+ `deploy/env.mcp.example` 经 `git cat-file blob HEAD:` 导出、CR=0、双机 SHA-256 逐位相符（`adc3386a…`/`5ff476cb…`/`b1524ef0…`/`41b74230…`/`424b0bbd…`/`80720c01…`）、py_compile 双机过；备份 `/root/hutuji-bak-20260828-34b44d4/`（双机）。阿里云 restart mcp+bridge（MainPID 285369/285371，bridge-health 200），京东云仅 restart mcp（MainPID 207951；bridge 按单 WSS 设计 inactive）。生产实画「一颗五角星」loopback MCP 成功（4305B/7 抬笔），公网 DNS 真下载 200 字节相符、公网 `/mcp` 404。preflight 对账：京东云 25/30——4 项 bridge 检查按热备设计本不满足（桥只在阿里云），1 项公网 /files 系安全组 hairpin（外部实测 404-shape 正常）；阿里云 20/30——8 项 nginx 检查锁 Debian sites-* 布局（阿里云 RHEL 系 conf.d，结构性不适用），unit 模板差为 08-23 割接手调硬化（ENOSYS 变体，刻意，见 inventory），均与本次部署无关。**顺手补缺**：token 看门狗 + 一键换 token 脚本此前只在京东云（08-23 割接漏迁）——已装阿里云并加 */10 cron，首跑 state=ok（现役 token 有效）。

> **2026-08-27 curated 预览坐标框修复双机发布（本仓 `cfee84e`；小派预览空白根因）**：`_preview_from_svg` 硬写 256×256 源框，生成条目（1024 空间）与矢量条目（36 空间）的点全被裁出画布——生产实锤 321B 近空 PNG 上屏（用户照片取证）；改按 SVG viewBox 解析源框（缺省回落 256）。单产物 `curated.py` blob 导出 CR=0、双机 SHA-256 `b1524ef0…dfc2` 逐符、`py_compile` 过；阿里云双服务 restart（MainPID 196602/196603）、京东云仅 mcp（75901）。**冒烟**：`画一只猫` → `curated hit` → 预览 PNG 1898B 完整猫（本机回看确认）；test_curated +2（1024/36 双空间像素级有墨断言），全套 1027/1027。
> **2026-08-27 达标类目 curated 优先路由 + QD 运行时退役双机发布（本仓 `48b7d05`，用户拍板）**：两产物 `server.py`/`curated.py` blob 导出 CR=0、双机 SHA-256 逐符（`a375dce3…b3dd`/`7d7c7079…1e37`）、`py_compile` 过；阿里云双服务 restart（MainPID 190677/190678）、京东云仅 mcp（73165）；两机 env 追加 `HUTUJI_CURATED_DIR=/opt/hutuji/curated`（京东云数据未同步前 `lookup_preferred` 恒 None 落 AI，无害）。路由：类目 manifest count≥n_min(30) 即出 curated（单类目达标，不要求全集 ready，manifest 现读保鲜）→ 未达直接 AI 链；**`_QD_STYLE_RE` 与 quickdraw 运行时查询删除，QD 数据/模块留盘作回滚不再服务**。**冒烟（免费）**：`随便画个猫`（此前必出 QD 涂鸦）→ `curated hit category=cat idx=003.svg`、3.4s、零付费、零 QD——涂鸦体验正式终结。测试：test_curated_server 5 项 preference 语义、test_quickdraw 删 5 死路由用例留 36、全套 1025/1025。回滚：两机 env 删 `HUTUJI_CURATED_DIR` 行 + restart（回 AI/QD 旧路由需还原 server.py/curated.py）。
> **2026-08-27 flip 就绪机器侧抽检（阿里云，142/142 OK；视觉样张 7/8 优 1 弱）**：对全部 142 个达标类目（count≥n_min=30）按「首条+随机一条」跑运行时全链路（`svg_to_gcode_async` + `validate_gcode`），**142/142 OK、0 fail**（`/tmp/flip-audit/`；G-code 抬笔 51–88 软告警 22 次，仅 `PEN_LIFT_WARN` 观察面、gate v3 口径不硬拒）。视觉抽检 8 类目（cat/dog/tree/house/fish/car/flower/airplane 各中位索引 1 张，已拉回本机 `mcp-server/tmp/svg_pilot/`）：猫（坐姿+须）、狗（垂耳幼犬）、房（烟囱+树）、鱼（鲑鱼）、车（轿车）、花（五瓣）、机（螺旋桨）均细节完整可辨；**树样张偏小偏稀（弱）**，非大面积打回。**标定侧证据（喂纸面闸）**：抽检中多条目抬笔 51–88 次——用户认可质量的库内容可超 40 抬笔，佐证 `MAX_PEN_LIFTS=40` 与 travel_gap 同类的偏严嫌疑，纳入 shadow 重钉样本。`manifest.ready` 仍 False（156 类目未满）。~~flip（REPLACE_QD=1）未开、待 ready + 用户拍板~~（superseded：同日达标类目 curated 优先路由上线，QD 退役，全集 flip 被吸收，见上条）。
> **2026-08-27 Twemoji 矢量源灌库通道上线并首轮灌库（本仓 `580bb02`/`639ee36`，CC-BY 4.0 免费提速提质）**：新增 `scripts/curated_vector_prep.py` + `hutuji_mcp/twemoji_categories.json`（212 类目 → ~250 codepoint）；链路 = 下载 → vpype 展平 → 生产同款 RDP 自适应压缩（首档 0.05 单位 ≈ 0.28mm、≤780 点对齐 gate v3 行数闸、6 档）→ gate v3 入库，矢量→矢量免栅格化，天然避开 AI 纹理/排线/实心块病灶。**首轮实测（阿里云，`/opt/hutuji/curated`）**：停 `hutuji-curated-prep` 跑两轮 accept 226+230（含 trumpet 补漏）→ 内容哈希去重 226 条 → cat 50+2=52、树/狗 52、房 52、鱼 53，**达标类目（≥n_min=30）由个位数跃至 142/298**，`manifest.ready` 仍 False（156 类目未满）；crab/1f980 单件因 805>800 行放弃（灌库补）。GitHub raw 从阿里云慢（全程 ~13 分钟）。prep 服务已恢复 active。本机同款证据：`tmp/curated-vector-data`（230 条干净库）+ `tmp/svg_pilot/`（猫狗树房鱼五类转换预览，猫 16 笔/377 点完整猫脸远超 QD 涂鸦）。测试 `test_curated_vector.py` +4（映射卫生/转换过闸/空路径/量化闸），全套 1028/1028。`CURATED-ATTRIBUTION.md` 已追加 Twemoji（jdecked/twemoji，图形 CC-BY 4.0）署名。回滚：删 `/opt/hutuji/curated/<cat>/` 下矢量条目并重算 manifest 即可（产物与生图条目同布局，不混源）。
> **2026-08-27 纸面闸转 shadow 模式双机发布（本仓 `06daa95` + env `HUTUJI_PAPER_RISK_GATE=0`，用户拍板）**：生产冒烟坐实 travel_gap 阈值疑似误杀（正常猫 shadow 实测 7.16mm vs 阈值 2.0，用户两次真画均被三轮耗尽），enforce 暂停改为 shadow 攒废/好样本；单产物 `server.py` blob 导出 CR=0、双机 SHA-256 `241655b4…3057` 逐符、`py_compile` 过；两机 `/etc/hutuji/mcp.env` 追加 `HUTUJI_PAPER_RISK_GATE=0`；阿里云双服务 restart（MainPID 169867/169868）、京东云仅 mcp（53678）。**行为变化**：AI 链不再拒收重试（单轮出图），但照记 `paper_risk shadow short/lifts/gap_p50/ink/scribble/segs` info 行供重钉阈值；gate-off 分支此前零日志，本批补 shadow 行（test +1）。QD/curated 路由不受影响。回滚 enforce：两机 env 改 `=1` + restart。
> **2026-08-27 拒收缓存逐出修复双机发布（本仓 `0717e25`）**：生产实测缺陷——闸 Reject 的位图留在 600s 位图缓存，同题重试三轮全命中缓存、三次同败（journal 16:58:29 实锤）；两产物 `server.py`/`bitmap_svg.py` blob 导出 CR=0、双机 SHA-256 逐符（`313be802…055b`/`411ae9f7…4c14`）、`py_compile` 过；阿里云双服务 restart（MainPID 168335/168336）、京东云仅 mcp（52698）。Reject 分支新增 `evict_bitmap_cache(description, attempt)`，下次同题真重生。test_paper_risk +1，全套 1024/1024、hub 门禁 pass。
> **2026-08-27 纸面闸 v2 scribble 平移不变窗双机发布（本仓 `1dbd310`）**：单产物 `paper_risk.py` blob 导出 CR=0、双机 SHA-256 `8c4438a8…f42e` 逐符、`py_compile` 过；阿里云双服务 restart（MainPID 165168/165169）、京东云仅 mcp（50302）。修正：v1 固定网格桶被骑缝密团劈半逃逸（45 段密团骑 1mm 桶缝时两桶各 ~22 双双低于阈值）且常量偏离 plan 钉值（1mm 窗/45 短段），改对每个短段中点统计其 1mm 盒内短段中点数（桶仅邻域加速），任一 ≥45 即拒；`PAPER_RISK_GATE_VERSION` 1→2。测试 +1（骑缝夹具 + 摊开不误判反向断言），全套 1023/1023、hub 门禁 pass、指纹 231/231 重登。回滚：还原 `paper_risk.py` 至备份 + restart，或 `HUTUJI_PAPER_RISK_GATE=0`。
> **2026-08-27 AI 纸面风险闸双机发布（本仓 `7bf64a8`+`ee5eca0`，enforce 即刻生效）**：四产物 `paper_risk.py`（新）/`bitmap_svg.py`/`server.py`/`config.py` 由 HEAD blob 导出、CR=0，双机 SHA-256 逐一相符（`eabf2cd6…ccd1`/`0afd1571…eeef`/`f51f1779…711d`/`a7d49484…fcc4`）、`py_compile` 过；备份 `/root/hutuji-bak-20260827-paperrisk/`（两机）。阿里云双服务 restart（MainPID 160231/160233）、京东云仅 mcp（MainPID 47297，bridge 保持 inactive）。**冒烟（生产真链，付费 1 次）**：`画一只小猫` → `paper_risk reject reason=travel_gap attempt=0/2` + `pen_lifts attempt=1` → 三轮耗尽 `PaperRiskExhaustedError`（闸按设计工作）；shadow 评测同题真实样本 `pen_lifts=32 short=0.15 travel_gap_p50=7.16 ink_span=170 scribble=0`，travel_gap 远超 2.0 阈值——「图废 vs 闸严」只能纸面目视判定，试画产物 `https://hutuji.donglicao.com/files/draw_20260827_171500_HQpCLbBWMYnWY2wVcWFGdX.gcode`（公网实测 200/17542B/CRC `19b5f4ed`，TTL 600s）已交用户。preflight：阿里云 20/30（10 fail 为 Alibaba 布局已知差异）、京东云 25/30（5 fail 为热备基线同款），均与部署前基线一致。回滚：`HUTUJI_PAPER_RISK_GATE=0` + restart，或还原四文件。**纸面目视未验，不得宣称画质改善或闸标定无误**。
> ~~**2026-08-27 AI 纸面风险闸（代码已合本批工作树，未部署）**~~（superseded：同日已双机发布，见上条；env 模板已补 `HUTUJI_PAPER_RISK_GATE` 注释行）。
> **现役拓扑真值见下方「当前（2026-08-23 晚割接后）」条**。本文标题与「目标机 / 拓扑 / 回滚」等多数章节写成于京东云单点时代，仅「当前」条与本条为现态；引用历史章节前先看日期。
> **2026-08-26 晚 curated 离线灌库迁阿里云（本仓 `1353336` + unit 模板）**：gate **v3**（用户口径：抬笔次数不作入库硬拒；硬闸只拦画不出/会卡死——SVG/vpype/§5、短段比、墨迹、G1 行数）。阿里云已 scp 上机 `curated_gate.py`/`curated.py`/`config.py`/`bitmap_svg.py`（含抽稀）/`server.py`/`scripts/curated_prep.py`，备份 `/root/hutuji-bak-20260826-curated-fill/`；双服务已 restart，bridge 健康。**灌库服务** `hutuji-curated-prep.service`（模板 `deploy/hutuji-curated-prep.service`）：`EnvironmentFile=/etc/hutuji/mcp.env`，写 `/opt/hutuji/curated` + `/opt/hutuji/curated-audit`，`Restart=on-failure`、`Nice=10`、`CPUQuota=50%`、`MemoryMax=1536M`，enabled+active；本机种子约 57 张已并入后续跑。运维：`systemctl status hutuji-curated-prep` / `journalctl -u hutuji-curated-prep -f`。**运行时仍暗部署**：`HUTUJI_CURATED_DIR` 未开、`REPLACE_QD` 未开——出图仍 QD→AI；`manifest.ready` 与抽检通过前禁止 flip。京东云热备本批未同步灌库数据/unit（可后补 rsync）。
> **2026-08-26 curated 精选库部署备忘（仓内代码已合 `main`；PR #7 MERGED）**：环境变量 `HUTUJI_CURATED_DIR`（curated 根；空=关闭）、`HUTUJI_CURATED_REPLACE_QD`（`0` 默认 / `1` 替换 QD；**仅当** `manifest.ready=true` 才激活）。数据根 **`/opt/hutuji/curated/`**（含 `manifest.json` 与 `<category>/*.svg`），构建产物不入 git。**暗部署**：先上代码并设 `HUTUJI_CURATED_REPLACE_QD=0`（或留空 `HUTUJI_CURATED_DIR`），运行时行为与现网 QD→AI 一致。**切换**：双机校验 `manifest.ready` 且抽检无大面积打回后，设 `HUTUJI_CURATED_REPLACE_QD=1` 并 `systemctl restart hutuji-mcp`（京东云热备同步后同样 restart）。**探针**：loopback `hutuji_draw`「画一只老虎」→ journal 须见 `curated hit category=tiger …`，**不得**见 `Quick Draw 命中`。**回滚**：`HUTUJI_CURATED_REPLACE_QD=0`（或清空 `HUTUJI_CURATED_DIR`）→ restart，秒级回到 QD 路径。许可与归因见 `mcp-server/hutuji_mcp/CURATED-ATTRIBUTION.md`。闸门演进：v1 抬笔≤25 → 试过 v2≤35 → **v3 取消抬笔硬拒**（仍严于「画不成」类硬闸）。
> **2026-08-26 晚 描摹实心块→轮廓环修复双机发布（本仓 `e02ff78`）**：用户主诉「AI 生图画的都有问题」（对照：QD 缓存正常）。取证（阿里云生产 venv 双后端实测）：商汤/Agnes 原图均为干净线稿，病灶在描摹环节——生图模型偶发把眼睛/鼻头画成**实心黑块**，骨架化把实心盘变成放射乱麻（纸面涂鸦核心区）；QD 路径是手绘矢量笔画不经过栅格骨架化，故无此病。修复：`trace_bitmap_to_polylines` 在 skeletonize 前按连通域 solidity≥0.82+面积阈值判别实心块并转外环（真实样本两眼各转 1 环，journald 落「实心色块转轮廓环 N 处」）；判据与线稿主体（solidity 0.07-0.18）间隔一个量级。单产物 `bitmap_svg.py` blob 导出 CR=0，SHA-256 `478050b6…6c7a` 两机相符、py_compile 过，备份 `/root/hutuji-bak-20260826-pre-blobfix/`；阿里云重启双服务、京东云仅 mcp，全 active。测试 +1（不规则实心块钉 220px/7 段判别面 vs 朴素 374px/14 段），全套 **986 passed**、hub 门禁 PASS。**同场排查「Z 压不到位」**：QD 与 AI 两路径 G-code 的 Z 指令逐字节同型（同 `G1G90 Z5.0/Z0.0F10000`），软件层无按压差——体感差异与笔画几何相关（AI 长笔画满速 F8000 连贯走线 vs QD 短碎笔低速拐弯，接触时间不同）+大幅面纸面平整度，属机械/装调面，软件无可动项（Z 深度与 F 均 §5 契约锁定）。遗留观察：方版 2048 样本毛发排线更碎（325 段），提示词「无排线无纹理」杠杆待后续 A/B。同日晚补充取证：①XY 覆盖面对比——AI 竖版猫 X 34..156/Y 10..180、QD 猫 X 10..180/Y 16.5..173.5，两路径都能吃满布局边距（10..180mm），「贴死点压不实」与路径无关、只与主体幅面位置有关，定论需实机贴边测试图（机械面）；②商汤单后端隔离 A/B（禁 Agnes/imagefree/硅流 racer），每臂 n=3 同题顺序抽卡：折线数基线 {202,171,252} 中位 202（骨架点数 15.7k..17.3k）vs 加「无毛发排线无纹理」后缀 {154,195,90} 中位 154（11.4k..16.8k）——方向一致偏少约 24%，但两臂数值带重叠、n=3 未达统计显著，**记「未验证」，不足以改 `_BITMAP_STYLE` 常量**（该前缀由 08-09 六连探针钉死，动它需同等强度证据）；若日后排线成确认主诉：先每臂 ≥5 扩样，或走描摹层治理（如最短片段过滤）。

> **2026-08-26 深夜2 形态学闭合+缝合容差 3mm 双机发布（本仓 `a3e216c`）**：用户再主诉「G-code 还是有问题」（17:38 新狗抬笔 55 仍超阈值）。根因再下一层：不是描摹丢线、不是 vpype 拆路径、不是缝合无效——是 **skimage skeletonize 对毛发纹理图天然产海量像素级毛刺短边**（实测 63 边中 31 条 <12px），`_MIN_POLY_PX` 丢弃后断口成虚线；预览渲染器把相接 polyline 连成一片所以看起来连续，G-code 每笔抬笔空移 4.85mm 中位就断成虚线。修复：①`binary = closing(binary, disk(2))` 在 skeletonize 前——形态学闭合填孔连断、不放大线宽（开闭守恒），断边 31→21；②缝合容差 1.9→3mm（`0.016*min(w,h)`）——覆盖 <12px 全部断口，独立特征（眼/鼻/嘴）间距 >3mm 不误并；③blob 腐蚀改 `r.slice` bbox 局部（advisory LOW：原 `comp[comp]` 恒 True 冗余全掩码 + 全幅腐蚀 4Mpx×N，改 bbox 面积×N）；④`closing` 补进 `_import_trace_deps`。生产实证：同 173858 小狗源图 **抬笔 55→15**（-73%，polylines 68→15）。988 passed、hub 门禁 PASS。单产物 `bitmap_svg.py` blob 导出 CR=0、SHA-256 `bad2e274…428` 两机相符、py_compile 过，备份 `/root/hutuji-bak-20260826-pre-closing/`；阿里云双服务、京东云 mcp 全 active。

> **2026-08-26 深夜 描摹端点缝合双机发布（本仓 `3844ac9`）**：用户主诉「刚画的小狗 G-code 有很大问题」。取证链：生产产物 G-code 66 笔、笔间空隙中位 4.85mm（65/65 >1mm）纸面即虚线——根因不是 vpype 拆路径也不是分辨率，而是 **sknw 骨架短边被 `_MIN_POLY_PX=12` 丢弃**：小狗（imagefree 1024 源）的短连接边 0.9-2.2mm 全落在 linemerge 0.3mm 契约阈值之上，整条连通路径碎成 66 段（实测 66 笔全参与 0.3-2mm 近延续对，2mm 合并→1 笔）。修复：`trace_bitmap_to_polylines` 出 polys 后加 `_stitch_polylines`（比例容差 max(6px, 1% 图幅，≈1.9mm）并查集+贪心串接、允许反转、串不出去的残段 fail-open 保留）。生产实证：同复现图 72→52 折线、**抬笔 69→33**（同一 1760×2368 拉布拉多小狗）。+2 测试（合并/内容契约），平行边测试改内容断言（缝合语义下计数字面失效、内容契约保留），全套 **988 passed**、hub 门禁 PASS。单产物 `bitmap_svg.py` blob 导出 CR=0、SHA-256 `ad9ce134…aab` 两机相符、py_compile 过，备份 `/root/hutuji-bak-20260826-pre-stitch/`；阿里云双服务、京东云 mcp 全 active。

> **2026-08-26 复审 P3 修复双机发布（本仓 `b6b314e`）**：运行时差集恰 2 产物 `activation_relay.py`/`quickdraw.py`（bind 400 非 dict JSON→502 对齐 `_list_bound_macs` 口径；lookup 无匹配类目落 info），`git cat-file blob` 导出 CR=0，两机 SHA-256 `6308feb0…fb87f3`/`10f10827…694793` 逐一相符、`py_compile` 过，备份 `/root/hutuji-bak-20260826-pre-p3/`（两机）。阿里云重启双服务（mcp+bridge active、启动日志 URL 已脱敏）、京东云仅重启 mcp（bridge 保持 disabled+inactive，单 WSS 不变量）。冒烟（阿里云）：QD `简笔画一只猫`→BitmapResult cat 50 条、`简笔画一只查无此物的xyz生物`→None 且落 `Quick Draw miss（无匹配类目）` info（新观测面生产首验）；`POST /register` 假码经真实控制台链路 400 invalid code；bridge 云端 ping→proxy→本机 MCP 响应全链在案、探活通过。preflight：京东云 **pass=25 fail=5 expected=30**（与 08-24 热备基线一致）；阿里云 **pass=20 fail=10**——10 项 fail 全为脚本按京东云 Debian 布局写的判据（sites-enabled 符链、专用 hutuji-access.log、:80 loopback ACL、隧道回源兜底、token cron）在阿里云 RHEL 直连双 443 布局（`conf.d/donglicao.conf`+`hutuji-gray.conf`、无 cloudflared/sites-enabled/专用日志/cron）结构性不适用，**非本批回归**；失败项实质逐项替代验证：公网 `/mcp` 404、`/songs` 200 audio/ogg、:80 `/files` 301、假能力 404、本机 `/mcp` 406 活性、双服务 active+ExecStartPre 过。**遗留**：preflight.sh 判据未按主机布局分派，阿里云无全绿口径，后续单独立项。

> **2026-08-25 C 组合（样本精选 + 质量路由）双机发布（本仓 `fdf9874`+`41650e4`）**：代码 2 产物 `server.py`/`quickdraw.py`（`_QD_STYLE_RE` 质量路由 + `_PREFIX_RE` 风格词同步）两机 sha256 `27c8cb31…`/`a097617d…` 逐一相符；数据集换装精选版（298 类 × 50 = 14900，`quickdraw_curate.py` 三因子质量分，`bad_lines=0`），本地 ↔ 两机 `basename→sha256` map `LC_ALL=C` 逐项 diff **零差异 298/298**（覆盖 `-`/`_`/空格混用类目名），旧数据保留 `/opt/hutuji/quickdraw.old`；阿里云重启双服务、京东云仅重启 mcp（bridge 保持 disabled，单 WSS 不变量）。冒烟：阿里云 `画一只猫`→AI 链 12.81s/704 行、`简笔画一只猫`→QD 0.80s/60 行（`entries=50` 精选生效）。本批后 preflight：京东云 **25/30**（5 fail 为热备预期分歧）、阿里云 **20/30**（10 fail 为 Alibaba Linux/双 nginx 布局已知环境差异，非回归）。hub 门禁 PASS（981/981、e2e PASS）。
> **2026-08-24 Quick Draw 复核修复发布（本仓 `1952caa`，阿里云现役生产；运行时面与 HEAD 一致）**：3 产物 `quickdraw.py`/`quickdraw_categories.json`/`gcode_gen.py` 由 `git cat-file blob HEAD:<path>` 导出，CR=0，远端 SHA-256 `c4ceb283…731db`/`28d84719…67d0`/`4390b254…4426`；部署前实测远端 `server.py`/`config.py`/`redact.py`/`cloud_bridge.py` 已与 HEAD 逐一相符，部署差集恰为 3 文件。数据集复核：`/opt/hutuji/quickdraw/` 298 个 `.jsonl` 与本机 `mcp-server/tmp/quickdraw-data/` 按 SHA-256 清单排序比对逐字节一致（首跑 diff 仅为 LC_COLLATE 排序差，非内容差）。备份 `/root/hutuji-bak-20260824-pre-quickdraw-fix/`；远端 `py_compile` 通过，双服务显式 restart，MainPID `4078641/4078642`，两条 ExecStartPre `status=0`，`/bridge-health` 双 connected。**preflight.sh 30 项为 Debian/京东云口径，在阿里云不适用**，按本文回滚 A 节手工判据验收：`tools/list` 恰 7 工具；loopback MCP 真 call `hutuji_draw`「画一只小猫」**0.714s** 返回 111 行/2426B/17 抬笔，journal `Quick Draw 命中 category=cat entries=200`，窗口内无任何付费生图日志（命中不占付费限流名额实证）；公网 `https://hutuji.donglicao.com/files/…gcode` 200，2426B 与远端落盘 sha256 `288733e1…ea4a` 逐字节一致，`x-hutuji-crc32: e5cfec37` 与本地复算一致；公网 `/mcp` 404。同日追加生产量化：类目 lookup 覆盖 **23/23**（14 个修复类目全 HIT + 9 个常规类目）；6 并发 hit 出图（猫/狗/鱼/树/房子/太阳）**全 OK**，墙钟 2.29s、最慢单 call 2.28s（与 vpype≤2 资源闸串行化一致），6 产物同一秒落盘、journal 6 次命中、付费生图日志 0 条。真实语音 HIL 与真实使用命中率统计仍未执行。
> **2026-08-24 京东云热备同步（割接手册步骤⑥）**：热备此前停在 `c2cdbd4` 且无 Quick Draw 面（`quickdraw.py`/`quickdraw_categories.json` 缺失、`server.py a6440db6`/`config.py 8f4193d2` 为旧版、数据集与 env 均无）。同步 6 产物（`server.py`/`config.py`/`gcode_gen.py`/`quickdraw.py`/`quickdraw_categories.json`/`QUICKDRAW-ATTRIBUTION.md`，HEAD blob 导出 CR=0，远端哈希与阿里云在位版本逐一相符）+ 数据集 298 `.jsonl`（SHA-256 清单排序比对逐字节一致）+ `mcp.env` 追加 `HUTUJI_QUICKDRAW_DIR=/opt/hutuji/quickdraw`；仅显式 restart `hutuji-mcp`（MainPID `3777869`，两条 ExecStartPre `status=0`），bridge 保持 disabled+inactive（单 WSS 不变量）。冒烟：生产 venv `quickdraw.lookup('画一只小猫')` HIT、loopback `/mcp` initialize 200。Debian 口径 preflight `pass=25 fail=5 expected=30`——5 fail 全部为热备态预期分歧（bridge active/自启/重启 4 项 + 公网 /files 真下 1 项因 DNS A 已指阿里云），与本批代码无关；回滚副本 `/root/hutuji-bak-20260824-pre-quickdraw-fix/`。**边界：本批只完成代码/产物/数据/env 同步，割接回滚流程（DNS 指回 + 停阿里云 bridge + 启京东云 bridge）未演练——bridge active 态的热备 preflight 从未跑过，回滚可行性是推断非实测；演练须先停阿里云 bridge（单 WSS），属生产中断窗口，待用户点名实机窗口再做。**
> **2026-08-23 日志脱敏发布（本仓 `c2cdbd4`，已部署；supersede 当日 `f984182` 应用基线）**：生产新增 `hutuji_mcp/redact.py`，并更新 `server.py`/`cloud_bridge.py`；候选均由 `git cat-file blob HEAD:<path>` 导出，CR=0，远端 SHA-256 分别为 `redact.py 8000c953…f1e252`、`server.py a6440db6…bda9c4`、`cloud_bridge.py eaca102f…96264`。备份 `/root/hutuji-bak-20260823-pre-c2cdbd4-log-redact/`；远端 `py_compile` 通过，清理缓存后双服务显式 restart，MainPID=`3603181/3603182`，`/bridge-health` 返回 200 且 `cloud_connected=true`/`proxy_connected=true`，当前 preflight **`pass=30 fail=0 expected=30`**。生产真实生成链日志中的 httpx POST/GET URL 均为 `<redacted-url>`，Uvicorn access query 探针保留五元组并将签名值记为 `<redacted>`；health pull 293 行落盘复核无完整 URL、原始探针或未脱敏签名参数。`server.py` 的 G-code 计时已部署；`bitmap_svg.py` 计时与 health pull 脚本仍为本仓取证面，未装入生产服务。前一 `f984182` 的 bridge-health/探活发布及回滚副本仍属有效历史证据，详见 git 历史中的本文上一版。
> **2026-08-21 唱歌目录 + 记忆工具发布（当前应用基线 `fae5eac`）**：两轮 scp。第一轮（唱歌，`2d82051`）：`server.py`（`/songs` 路由 + 云端 `hutuji_sing`）/ `songs/`（`catalog.json` + 三首自合成公版 OGG）/ `nginx-hutuji.conf`（两个 server 块各加 `/songs/` location）逐文件 LF blob 核 SHA-256 与 CR=0，备份 `/root/hutuji-bak-20260821-sing/`；重启三服务全 active，公网 `/songs/twinkle.ogg` 200 `audio/ogg` 152422B、`x-hutuji-crc32: 441a8c9a`，路径穿越与 `.mp3` 均 404 塌缩，`/mcp` 公网仍 404；preflight `pass=29 fail=0 expected=29`（26→29 项，新增 `/songs` 冒烟等判据）。第二轮（记忆 RMW 锁，`fae5eac`）：`memory_store.py` SHA-256 `7dc81b9a…` 两端逐位一致，双服务 restart，preflight 29/29，生产记忆三工具写/读/删实测通过。**运维学习**：① 新增 MCP 工具后云端首轮会话调用可能落空（工具表缓存），失败调用触发自动 `tools/list` 刷新，非代码 bug；② `/songs` 响应 `Cache-Control: max-age=3600`，次轮设备下载可能命中 Cloudflare 边缘缓存不回源——源站 GET 日志不是下载证据，设备串口 `hutuji_music` 日志才是。

> **2026-08-20 取消重试去重发布（当前应用基线 `7eac656`）**：从 Git blob 精确导出 LF 的 `bitmap_svg.py` / `server.py` / `requirements.txt`，生产 SHA-256 `765cc091…1fa` / `ddf2b5c0…f26` / `4200aed6…deb`，三文件 CR=0、生产 Python `py_compile` 通过；回滚副本 `/root/hutuji-bak-20260820-draw-retry-7eac656/`。显式重启 MCP 与共用代码/venv 的 bridge，新 MainPID `2966745/2966746`（04:24:32 CST），两条 ExecStartPre 成功、监听身份相符、preflight `pass=26 fail=0 expected=26`。标准 MCP 取消探针以同一冷题执行三次 call：前两次 2 秒后取消，第三次 8.504 秒接管成功；日志只有一轮生图对冲/一次 Agnes POST。**部署陷阱实证**：本机 `core.autocrlf=true` 时 `git archive` 把 LF blob 导成 CRLF；该候选在哈希/CR 闸失败后未安装，改用逐文件 `git cat-file blob`，故后续不得把 archive 当作 LF 保证。

> **2026-08-17 SPEED-01 + 预览产物窄部署（运行态覆盖提交 `1082400`；本仓实现现已追上并提交于 `155dbff`，本轮尚未重新部署）**：仅将 LF 归一化的 `hutuji_mcp/bitmap_svg.py` / `server.py` 同步生产，远端 SHA-256 分别为 `949d2da7…fff8` / `ac06ee82…e9e`；备份 `/root/hutuji-bak-20260817-speed01/`。只重启 `hutuji-mcp`，bridge/nginx/cloudflared/env/unit 均未改；新 MainPID `2559546` 于 19:11:04 CST 启动，两条 ExecStartPre（含 bitmap self-test）通过，随后 MCP fresh `POST/GET` 返回 200/202。服务进程 `/proc/2559546/maps` 实查 `numpy/scipy/skimage` 三组动态库均已加载，证明 `lifespan` 预热进入真实服务进程而非只在独立 ExecStartPre 进程生效。生产 preflight **`pass=26 fail=0 expected=26`**。**边界**：本轮未再触发付费真实 draw，故只证明预热机制和 PNG 产物代码已部署，不宣称首图实测缩短；生产仍运行 `1082400` 的可执行面，提交 `155dbff` 尚未 scp。


> ~~③ MCP 服务**唯一**生产落点：京东云 VPS~~（2026-08-23 用户反转：阿里云主 + 京东云热备，灰度节点见下条）。模板在 `../deploy/`。真实凭据不入库。
> **当前（2026-08-23 晚割接后）**：生产 = **阿里云 `47.112.162.80`**（Alibaba Cloud Linux 3）——`hutuji-mcp` active（8300，el8 unit 含 `SystemCallErrorNumber=ENOSYS`）、`hutuji-cloud-bridge` active（cloud/proxy 双 connected）、nginx 直双 443 块（`hutuji.donglicao.com` 主 + `aliyun-hutuji.donglicao.com` 灰，分证书）、`PUBLIC_BASE_URL=https://hutuji.donglicao.com`、`/register` 激活中转已验（假码 400 invalid code = 全链通）。京东云 `117.72.118.95` 转**热备**：bridge disabled+inactive，hutuji-mcp active 待命，产物两机已同步（**注意**：CF 代理记录改仅 DNS 后，缓存旧 CF IP 的客户端命中边缘即 1016/530，不会落京东云，同步只对直连 IP 与热备一致有意义）；回滚 = DNS 指回 CF Tunnel 目标 + 启京东云 bridge + 停阿里云 bridge。生产代码基线 `c2cdbd4`（两机一致）；真实语音 HIL 未执行。**🔴 preflight.sh 30 项是 Debian/京东云口径，本机恒红且无 skip——自动化验收面待适配（出货前必修）**。
> **2026-08-23 阿里云灰度节点就绪（决策反转：阿里云主 + 京东云热备；未割接）**：阿里云 `47.112.162.80`（Alibaba Cloud Linux 3，RHEL 系）与既有业务共存（宝塔/nginx/redis/tailscale/JoyCode2Api/dlc-drawing 未动）。与京东云（Debian）路径差异：①系统 Python 仅 3.11——用 uv 装 3.12 到 `/opt/uv-python`，venv 必须 `--link-mode copy`（默认 symlink 指进 `/root`，`ProtectHome=true` 会把服务挡成 203/EXEC）；②systemd 239 不认 `ProtectClock`/`ProtectKernelLogs`/`ProtectProc`/`ProcSubset`，且 `set_mempolicy_home_node` 不在其 syscall 表会让整个 `SystemCallFilter` 解析失败（203/EXEC）——灰度 unit 已删这五行，文件在 `/etc/systemd/system/hutuji-*.service` 与 `/opt/hutuji/deploy/*-el8.service`；③nginx 无 `sites-available` 布局，vhost 直接落 `/etc/nginx/conf.d/hutuji-gray.conf`；④`prepare-host.sh`/`install-from-repo.sh` 是 Debian 口径，本机手工装。节点现状：`hutuji-mcp` enabled+active（`127.0.0.1:8300`，EnvironmentFile `/etc/hutuji/mcp.env`，`HUTUJI_PUBLIC_BASE_URL=https://aliyun-hutuji.donglicao.com`）；`hutuji-cloud-bridge` 已装但 **disabled**（灰度不接小智云，防同 token 对拍）；入口 `aliyun-hutuji.donglicao.com`（DNS only 直连，certbot webroot 证书，`/etc/letsencrypt/live/aliyun-hutuji.donglicao.com/`）。灰度验证：loopback 7 工具、两轮真链出图、公网真下载逐字节一致、`/mcp` 公网 404、`:80` 301。
> **割接手册（按序执行，每步有回退）**：①`systemctl stop hutuji-cloud-bridge`（京东云）→ ②阿里云 `sed -i 's|HUTUJI_PUBLIC_BASE_URL=.*|HUTUJI_PUBLIC_BASE_URL=https://hutuji.donglicao.com|' /etc/hutuji/mcp.env && systemctl restart hutuji-mcp` → ③DNS 把 `hutuji.donglicao.com` 从 CF Tunnel 改 A 记录 `47.112.162.80`（仅 DNS；回退 = 改回 Tunnel 目标）→ ④`systemctl enable --now hutuji-cloud-bridge`（阿里云）验 `curl 127.0.0.1:8300/bridge-health` 200 → ⑤真实语音 HIL → ⑥京东云转热备：`systemctl stop hutuji-mcp` 留档，代码改动照常 scp 同步两机。**注意**：S3 白名单只放行 `hutuji.donglicao.com`，步骤 ②③ 完成前任何灰度子域 URL 设备都拒；bridge 任一时刻只能一台 active。
> **2026-08-17 bridge stale MCP session 运行态恢复（零文件/配置变更）**：Waveshare 真实语音出图时，连续 `hutuji_draw` 先在约 1 秒内报云端断开；生产四服务 `is-active` 全为 active、bridge 每分钟 ping 亦有响应，但 `hutuji-mcp` access log 实锤对应本机 `POST /mcp` 四次均 **404**，即 `mcp-proxy` 持有的 MCP session 已失效，active/ping 未覆盖真实 tools/call。只执行 `systemctl restart hutuji-cloud-bridge.service`；新 PID `2523746` 随后完成 WSS connect → `initialize` → `tools/list` → ping，本机 `/mcp` 恢复 200/202。复测语音 draw：Agnes POST 200、PNG GET 200、bridge 返回 response、S3 对能力 URL GET 200；用户目视完整五角星、回左下原点、自动换纸完成。恢复后 bash `/opt/hutuji/deploy/preflight.sh` **历史记录 `pass=26 fail=0 expected=26`**。未上传产物、未改 env/unit/nginx、未重启 MCP；生产应用基线仍为 `1082400`。stale-session 复发风险保留，不能用 `is-active` 或 ping 代替真实 tools/call。
> **2026-08-16 工具脚本窄部署（本仓 `a7697b9`）**：本批生产适用面仅 `deploy/install-from-repo.sh`；从 HEAD 取 LF 原文同步 `/opt/hutuji/deploy/install-from-repo.sh`，远端 SHA-256 `d8eec7259d363b104da8d01232fd3a983aca3cbb97b73f45c207e1dc01875936` 对账一致。运行时 `hutuji_mcp`、bridge、unit、nginx 均未变，故不做无意义重启；随后生产 preflight `pass=26 fail=0 expected=26`。生产应用基线仍为 `1082400`。
> **2026-08-16 S3 `6004636` 收口核验**：本批变更全在设备侧 S3 与 host 测试，生产 MCP/bridge 无运行时差集，故没有上传或重启服务；远端两服务保持 active，`install-from-repo.sh` SHA-256 仍为 `d8eec725…75936`，生产 preflight `pass=26 fail=0 expected=26`。S3 COM12 已 app-flash，设备证据归 `release-readiness.md`，不提升京东云应用基线。
> **2026-08-14 第十二轮修复发布（本仓 `1082400`，生产 `pass=26 fail=0 expected=26`）**：8 个产物（`bitmap_svg.py`/`cloud_bridge.py`/`gcode_gen.py`/`grbl_sim.py`/`llm_svg.py`/`pipe_sim.py`/`server.py`/`preflight.sh`）LF scp、显式重启两服务（主进程 2017026/2017028，13:57:52 CST，两条 ExecStartPre `status=0`）；preflight 25→26 项，三新判据（is-enabled 开机自启 / 8300 监听者 `ss -lntp` 比对 MainPID / `requirements.txt` 入 mtime 比较集）首次真跑全绿；生产 e2e PASS、真 draw「五角星」228 行/5418B、公网 URL `HTTP=200 bytes=5418`。回滚副本 `/root/hutuji-bak-20260814-135642-pre-1082400/`。
> **2026-08-13 第九+十轮修复发布（本仓 `5104b93`，生产 `pass=25 fail=0 expected=25`）**：11 个产物（8 个 py + `requirements.txt` + 两个 unit）LF scp，`daemon-reload` + 显式重启两服务（13:36:03 CST）。回滚副本 `/root/hutuji-bak-20260813-133527-pre-5104b93/`。
> **2026-08-12 同日四次发布补认回填（`e533f70` 16:51 / 第五轮产物 18:00 / `f3a5b94` 19:01 / `bb2529a` 21:38）**：当日记账只把 `e533f70` 写进本文「当前」行，后三次（第五轮 `requirements.txt`/`install-from-repo.sh`、第六轮 `llm_svg.py`、第七轮 `cloud_bridge.py`+`bitmap_svg.py`）未回填本文——与 2026-08-11 那次同形态的漏登记。证据均在 `release-readiness.md` 文首各 2026-08-12 条；回滚副本 `/root/hutuji-bak-20260812-165011-pre-deploy-HEAD/`、`/root/hutuji-bak-20260812-180044-p2/`、`/root/hutuji-bak-20260812-190109-pre-r6-svg/`、`/root/hutuji-bak-20260812-213817-pre-r7-wd-bmp-cancel/`。
> **历史记录（2026-08-09，已被后续 26 项生产 preflight supersede）**：layout 归一化发布 `3126a84` 当时 preflight 21/21 PASS；生产真 draw「五角星」`lines=19 bytes=364`。当前生产项数与运行态以本文顶部及 `release-readiness.md` 为准。
> **2026-08-09 位图路线发布（本仓 `4be934b`）**：`hutuji_draw` 切到「agnes 文生图 → sknw 中心线描摹 → 单线 SVG」。scp `bitmap_svg.py`（新）/`config.py`/`server.py`/`requirements.txt`（LF），生产 venv 经清华镜像装 `scikit-image 0.26.0`/`networkx 3.6.1`/`sknw 0.15`/`imageio 2.37.4`（**直连 PyPI 会挂起 17 分钟无子进程，镜像 75s 装完**）→ 显式 `restart hutuji-mcp` → preflight **21/21 PASS** → 生产真 draw「一朵五瓣的花」：709 行/16840B/22 抬笔/23.4s，公网 URL 200 且字节数与工具上报一致。回滚副本 `/root/hutuji-bak-20260809-184140-pre-bitmap/`。注意：`/etc/hutuji/mcp.env` 未改（`HUTUJI_IMAGE_MODEL` 默认值即生产值）。
> **2026-08-10 位图自检封闭性收口发布（本仓 `f25d8d1`）**：`bitmap_svg.py`/`preflight.sh` 经 `git show HEAD:` 上传（md5 `cb18fb779084a7ef3d32666bf0772448`/`36e4823dedee94c564d88a3df7062acc` 逐一相符、0 个 CR、`py_compile`/`bash -n` 过）→ 显式 `restart hutuji-mcp`（01:59:14 CST，bridge 与 unit 未改不动）→ 两条 ExecStartPre 均 `status=0`（1024px ⊥ 探针在沙箱内真跑，restart 全程 ~9s，远在 `TimeoutStartSec=30` 内）→ 生产 **22/22 PASS**（改名后的第 3 项「mcp.env 无占位、LLM 完整、image_model 非空白且公网前缀为 HTTPS:443」在真实 mcp.env 下过）→ 公网 `/mcp`/`/files/nope`/`/docs`/随机路径全 404。回滚副本 `/root/hutuji-bak-20260810-015828-pre-f25d8d1/`。生产应用基线现为 `f25d8d1`。
> **2026-08-11 三次部署补认回填（`0db62f5` / `1f8ec57` / `a5ae774`）**：这三次发布当时**只记进了 `deploy/README.md`**，未回填本文与 `release-readiness.md`，导致两处文档长期声称「生产基线仍 `f25d8d1`」而实机已领先。2026-08-11 盘点时用 `git rev-list f25d8d1..HEAD` 逐版计算 LF 归一化 md5、再与生产落盘 md5 映射，才把真实序列还原为 `f25d8d1` → `0db62f5` → `1f8ec57` → `a5ae774`：映射结果为 `server.py`/`bitmap_svg.py` 命中 `b0712a7` 版、`cloud_bridge.py` 命中 `8c2e276` 版、两个 unit 分别命中 `a5ae774`/`2e63b3e` 版，而 `preflight.sh` 仍是 `f25d8d1` era 的 `EXPECTED_CHECKS=22`（该常量的历史为 `f25d8d1`=22、`5261249`=22、`15e30b9`=22、`fb7dff9`=23、`05786ce`=23）。**教训**：部署记录只写在 `deploy/README.md` 一处等于没写——`release-readiness.md` 文首与「京东云生产环境」行是别人判断「要不要发」的唯一入口，漏回填会让下一任把「已部署」当「待部署」重做，或反过来把真实缺口当已发布。发布后三处必须同批更新（本文、`release-readiness.md`、`worktree-inventory.md`），`deploy/README.md` 只记「当前基线 + 项数」这一行。
> **2026-08-11 第二/三轮修复发布（本仓 `05786ce`，生产 23/23 PASS）**：`a5ae774..HEAD` 的可执行差集 5 个，另加盘点新发现的 `install-from-repo.sh` 共 **6 个产物**：`bitmap_svg.py`（仅注释）/`llm_svg.py`（深度守卫 `>` → `>=`，唯一改变拒收面的产物）/`cloud_bridge.py`（`HEALTHY_SESSION_SECONDS=60` 退避门 + `load_config()` 拒 userinfo + `_message_label()` 补捕 `RecursionError`）/`preflight.sh`（22→23 项）/`nginx-hutuji.conf`（两个 server 块加 `server_tokens off`）/`install-from-repo.sh`（生产那份停在 `2a0992f`，收尾指引仍印 `enable --now`，而 `ef67df9` 已改为 `enable` + 显式 `restart`；纯 echo 文案）。流程：`git show HEAD:` → `tr -d '\r'` → `/tmp/hutuji-deploy-05786ce/` → scp，6 处落盘 md5 与 git blob 逐一相符、0 个 CR，三个 `.py` 过 `py_compile`、脚本过 `bash -n`，清 `__pycache__`；`nginx -t` → **reload**（本次未动 `limit_req` 的 key/zone，故不触发 reload 静默丢弃陷阱；仍复核 worker `lstart` 23:51:03 晚于配置 mtime 且无 `[emerg]`）→ `daemon-reload` + 显式 `restart` 两服务（主进程同为 23:51:12 CST）→ **`pass=23 fail=0 expected=23`**（新增第 9 项「bridge 已重启且不早于本次 unit/bridge.env/代码」首次在生产为 pass）。公网复验：`/mcp`、`/docs`、`/redoc`、`/openapi.json`、随机路径全 **404**；`server_tokens off` 双向取证（`:80` 与 `:443` 响应头均只剩裸 `Server: nginx`、正文 `nginx/<版本>` 零命中）；真产物端到端 **200 / 219 字节 / CRC32 `bc057220`**，与盘上文件逐字节一致、正文合 §5。回滚副本 `/root/hutuji-bak-20260811-234834-pre-05786ce/`。**本轮另把可执行面锁成清单证据**：生产 18 个可执行产物（`hutuji_mcp/*.py` 9 份、`hutuji_gwrite.toml`、`requirements.txt`、两个 unit、`nginx-hutuji.conf`、三个 `.sh`、`/etc/nginx/sites-available/hutuji`）逐一比对 HEAD 的 LF-md5，**18/18 相符**。**已知残留**：`/opt/hutuji` 上的文档/测试镜像有 **32 份**与 HEAD 不一致（不被服务加载、不被 preflight 校验、生产无 pytest），其中会误导操作的三份（`deploy/README.md`、`scripts/e2e_local.py`、`scripts/cloud_bridge_smoke.py`）已随本轮同步，其余纯文档镜像未同步。

## 目标机

> 以下为**京东云历史口径**（现役 = 阿里云主 + 京东云热备，见「当前」条）。

| 项 | 值 |
|----|-----|
| 公网 | `117.72.118.95` |
| 规格 | 2 核 4G / 5Mbps |
| 上行实测 | **2026-07-26**：公网拉 512KiB 中位 **1.13s / ~3.72 Mbps**（本机→`http://117.72.118.95/…`，3 轮；测完文件已删） |
| 角色 | 热备待命（原：专机只跑 ③（uvicorn + 小智桥接 + nginx）） |
| 冗余 | 阿里云主 + 本机热备（原：无；单点） |
| 实机业务 | `hutuji-mcp` active（待命）；`hutuji-cloud-bridge` disabled+inactive；`cloudflared` 留作回滚通道；`HUTUJI_PUBLIC_BASE_URL=https://hutuji.donglicao.com`；**2026-08-07 起绘图 LLM = Agnes `agnes-2.5-flash`**（此前 llm7 为历史配置） |

## 拓扑（京东云历史口径；现役见「当前」条与割接手冊）

```
公网客户端 / S3 板
        │ HTTPS（hutuji.donglicao.com）
        ▼
   Cloudflare (Let's Encrypt 证书)
        │ Cloudflare Tunnel（QUIC，出站连接绕过京东云 ICP）
        ▼
   cloudflared (localhost)  ──► nginx :80 /files  ──► 127.0.0.1:8300/files
        │
        ✗  /mcp  nginx 404（禁止暴露）

小智官方云 WSS
        │
        ▼
 hutuji-cloud-bridge（本机）──mcp-proxy──► 127.0.0.1:8300/mcp
                                               │
                                               └─ `LoopbackOnlyMcpGuard` 按 socket 对端复核；非 loopback/未知对端 404
```

> 应用层守卫不替代 unit 的 `--host 127.0.0.1`、安全组不放行 8300、nginx `/mcp` 404：它只兜 `/mcp` 控制面，不能阻止 `/files` 绕开 nginx 限速与 ACL。该守卫已于 2026-08-09 随 `8184860`（守卫本体 `19d1df1`）部署生产，`server.py` md5 `60801395b0ba7d70bf0322fa173d8619`，21 项 preflight `pass=21 fail=0 expected=21`；行为面实测：loopback `initialize` 200；**非 loopback 向量必须在同一份已部署代码的临时实例上取证**——生产 unit 只绑 `127.0.0.1:8300`，非 loopback 对端连不上（`curl` 回 `000`，这是监听面而非守卫的功劳），故用 `--host 0.0.0.0 --port 8399` 起同一个 `server.py`、探完即杀（`ss` 复核 8399 已消失、`0.0.0.0` 监听面仍只有 80/443/22）：同机非 loopback 对端（`curl --interface 172.16.0.4`）伪造 `Host: 127.0.0.1:8399` 打 `/mcp` 得 404 且正文恰为 9 字节 `not found`（守卫路径）、同一对端打 `/files/nope.gcode` 得 404 但正文 `{"detail":"not found"}`（应用路径，未被守卫拦）、公网 HTTPS `/mcp` 仍 404。回滚副本 `/root/hutuji-bak-20260809-020732-pre-8184860/server.py.a939eb67`。

### Cloudflare Tunnel（2026-07-27 上线）

京东云对未备案域名做 HTTP Host + TLS SNI 双重拦截，无法直连 HTTPS。
使用 Cloudflare Tunnel（免费）从 VPS 出站建隧道，完全绕过。

| 项 | 值 |
|----|-----|
| 隧道名 | `hutuji` |
| 域名 | `hutuji.donglicao.com` |
| DNS 记录 | CNAME → `<tunnel-id>.cfargotunnel.com`（已代理） |
| 证书 | Let's Encrypt（Cloudflare 管理），ESP-IDF cert bundle 信任 |
| cloudflared 配置 | `/etc/cloudflared/config.yml`（本地管理模式） |
| 服务 | `systemctl status cloudflared`（enabled，开机自启） |
| 入口规则 | `hutuji.donglicao.com` → `http://localhost:80`；其余 404 |
| Cloudflare 账号 | 域名 `donglicao.com` 托管于 Cloudflare |

> 尝试过的失败方案：Flexible SSL（JD Cloud SNI 拦截）、Origin Rules（付费功能）、Workers（fetch 不允许裸 IP → 1003）。

## 阶段 0：上线前准备（可先做、不启业务）

目的：空出专机、建好目录与用户，**不**把密钥写进 git、**不**对公网开 8300。

1. 京东云控制台安全组：只放行 **22（或 SSH 改口）/ 80 / 443**；确认 **无 8300**。  
2. SSH 用密钥登录（勿把密码写进仓库脚本）。  
3. 将本仓同步到 `/opt/hutuji`。  
4. 执行：`bash /opt/hutuji/deploy/prepare-host.sh`  
5. （若机上有旧杂物）按 `../deploy/README.md`「清理旧环境」停掉无关服务、清空 `output/`。  
6. 勾选本页下方「阶段 0 清单」——勾完只代表**主机坑位就绪**，不代表已上线。

### 阶段 0 清单

- [x] 主机可登录；已建用户 `hutuji`、`/opt/hutuji`、`/etc/hutuji/{mcp,bridge}.env`（600）、`/etc/nginx/ssl/hutuji/`  
- [x] 点名业务已停删（dlc-drawing / MySQL / Redis / qwen-gateway / nginx new-api）；UFW 仅 22/80/443  
- [x] `/opt/hutuji` **本仓代码**就位  
- [x] UFW 仅 22/80/443；8300 未放行（安全组请再复核一眼）  
- [x] `mcp.env` 已填（Agnes `agnes-2.5-flash`；密钥仅在机上 600 文件）；TLS 自签已放 `/etc/nginx/ssl/hutuji/`
- [x] `bridge.env` 已填真实 `MCP_ENDPOINT`（600；禁入库）

### 实机清理（2026-07-26，用户点名后已执行）

已停并删除：

| 项 | 处置 |
|----|------|
| dlc-drawing + `/opt/dlc-drawing` | unit/目录删除；`chat.donglicao.com` nginx 配置删除 |
| qwen-gateway + `/opt/qwen-gateway` | 进程/目录删除；3002 已释放 |
| MySQL / Redis | 包 purge + 数据目录删除；3306/6379 已释放 |
| nginx new-api / newapi / newapi-managed | sites-available 删除；`/root/newapi-backups` 删除 |
| UFW | 仅保留 22/80/443 |

第二轮（建议可删，已执行）：Docker/containerd、lima 残留与 `/root` 杂目录（openclaw/prometheus/dlc-retire 等）。cloudflared 已重新安装用于 Cloudflare Tunnel。

仍保留：nginx、Tailscale、京东云 agent（jdog/ifrit/jcs）、hutuji 业务。

### 阶段 1 进度（2026-07-26）

| 项 | 状态 | 证据 |
|----|------|------|
| 代码 → `/opt/hutuji` | ✅ | `requirements.txt` 在位；venv IMPORT_OK |
| `mcp.env`（Agnes `agnes-2.5-flash`） | ✅ 2026-08-07 | 本地完整链生成 1336B SVG、286 行/6891B G-code；生产真实链在“前两次 15s ReadTimeout、第三次成功”下总耗时 39.87s，生成 296 行/6870B G-code。请求限制 `max_tokens=1200`，网络错误及 `429/500/502/503/504` 最多重试两次（1s/3s）；当前协议消费 SVG 文本，故不用位图模型 |
| Cloudflare Tunnel HTTPS | ✅ | `https://hutuji.donglicao.com/files/…` 经隧道到 nginx；Let's Encrypt 证书 |
| `hutuji-mcp` | ✅ | `systemctl active`；`127.0.0.1:8300` |
| `hutuji-cloud-bridge` | ✅ | WSS 已连接；云端 `initialize` + `tools/list` 已进日志 |
| `preflight.sh` 全 PASS | ✅ 2026-08-09 生产 **21/21**（基线 `8184860`，含 `19d1df1` 的 `/mcp` loopback 守卫） | `pass=21 fail=0 expected=21`。本轮只需发 `server.py` 一个文件（安装后 md5 `60801395b0ba7d70bf0322fa173d8619`、mode 644、0 个 CR），清 `__pycache__` 后显式重启两个应用服务，`ExecStartPre` 自检 `status=0`，`ss -ltnp` 仍只有 `127.0.0.1:8300`；回滚副本 `/root/hutuji-bak-20260809-020732-pre-8184860/server.py.a939eb67`。第 16 项「nginx 活动配置真的在跑」（带 marker 的 loopback 探针必须落进 `hutuji-access.log` 的 `hutuji_client` 自定义格式，行首 `127.0.0.1 via=127.0.0.1 `）与第 17 项「worker 新鲜度」（无早于 `stat -L` 配置 mtime 的 worker；worker 启动后无 `[emerg]`）自 2026-08-08 起在生产通过；同批还首次验真了 `check_units_match_templates` 的 ExecStartPre 成功结果与主进程新鲜度判据。**历史 `pass=21`（2026-08-08 `25ff33d`）/ `pass=19`（2026-08-07）/ 17/17 / 15/15 均已 supersede，不得复用为放行依据**——19 项在「nginx reload 被静默丢弃、旧配置继续服务 37 分钟」时依然全绿。输出格式 `pass=N fail=N expected=N`，放行判据 `PASS==EXPECTED_CHECKS && FAIL==0`（脚本已删 skip 状态，禁止重新引入 `OPTIONAL_SKIP`）。**scp 前必须 LF 归一化**，否则 `preflight.sh` 上机即 `set: pipefail: invalid option name` |
| 小智控制台工具可见 | 🔬 | 控制台刷新后应见 `hutuji_draw` 等 |
| 小智知识库 | ✅ | 用户已上传 `docs/xiaozhi-agent/knowledge-base.md`（2026-07-26）；请确认智能体页已**关联** |

> TLS 已切 Cloudflare Tunnel + Let’s Encrypt（2026-07-27），ESP-IDF cert bundle 信任，S3 无需 `-k`。  
> `install-from-repo.sh` 默认清华 HTTPS PyPI 源（京东云直连 PyPI 易卡住），不再使用 `--trusted-host` 绕过 TLS；私有 CA 场景显式传 `HUTUJI_PIP_CERT`。

> **systemd 生成链边界（2026-08-07 定案，2026-08-08 已部署验真；2026-08-09 扩到位图描摹链，已随 `7aab460` 部署）**：preflight 的 `check_generate_chain` 在服务外验证生产 venv、profile 与 §5 校验器，但不能证明同一程序能穿过 unit 的 seccomp。生产曾出现 preflight 19/19 与真实 vpype `SIGSYS` 同时成立；内核审计定位到 `mbind`。`hutuji-mcp.service` 用 `ExecStartPre=...python -m hutuji_mcp.gcode_gen --self-test` 在服务沙箱内真跑 vpype/profile/§5 自检，`SystemCallFilter` 仅从原 `@resources` 禁用组中放行 `mbind`。**2026-08-09 仓内再加第二条 `ExecStartPre=...python -m hutuji_mcp.bitmap_svg --self-test`**：位图描摹重依赖（skimage/sknw/imageio）是延迟 import，缺失时 import/active/生成链自检全绿而真实描摹才炸（本机拦掉 imageio 实测复现）；自检用纯 stdlib zlib 合成的 T 形探针 PNG 真跑描摹全链，不碰计费生图 API，同时验证 skimage/scipy 数值栈能穿过 seccomp（vpype mbind 同款风险面）。发布后必须 `daemon-reload` 并**显式 restart** 两个应用服务；`enable --now` 对 active 服务不会重启。当前 22 项中的 unit 检查会**逐条**读取两条 ExecStartPre 的实际成功结果（整段子串匹配会让一条绿盖住另一条红），并要求 MCP 主进程不早于已部署 unit、包内 Python 文件和 profile；只 reload 未 restart 会判 fail。gcode 自检部分已随 `25ff33d` 部署（2026-08-08 生产 `pass=21 fail=0 expected=21`）；**bitmap 自检部分已随 `7aab460` 于 2026-08-09 部署**：三产物 md5 与 HEAD blob 逐一相符、0 个 CR，回滚副本 `/root/hutuji-bak-20260809-225123-pre-7aab460/`，显式重启（MCP 主进程 22:52:26）后两条 ExecStartPre 均 `status=0`（bitmap 自检实跑 5 秒，证明 skimage 描摹在沙箱内真执行、无 SIGSYS），生产 `pass=22 fail=0 expected=22`，公网 `/mcp`/`/files/nope`/`/docs`/随机路径复验全 404。

### nginx 站点收敛（2026-07-31 14:51–14:53，已执行）

> 证据：`results/nginx-80-files-deploy-evidence-20260731.md`（`results/` 不入库）。
> **纠错**：此前 `release-readiness.md` / `worktree-inventory.md` 写「nginx :80 /files 反代未部署」是**误判**——
> 该反代自 **07-27 03:06** 起就在 `/etc/nginx/sites-enabled/hutuji` 里生效。

上线前实测三份配置**互不相同**，且 `sites-enabled/hutuji` 是**普通文件而非符号链接**：

| 副本 | 大小 / md5 | 内容 |
|------|-----------|------|
| 线上 `sites-enabled/hutuji`（生效） | 1804B `9f800105…` | 已含 `:80 location /files/`；`server_name` 含隧道域名 |
| 线上 `sites-available/hutuji`（不生效） | 1771B `e9f21b4c…` | 07-27 02:07 旧版，两个 server 块 `server_name` **只有裸 IP** |
| 本仓 `deploy/nginx-hutuji.conf` | LF 归一化 `5a424341…` | 与生效版仅差**两处注释** |

两个方向都是坑：`nginx.conf:65` 只 `include sites-enabled/*`，所以**改 `sites-available` 是静默空操作**；
反之谁 `ln -sf` 建链接，就会把生效版**回退**成缺隧道域名的旧版（`/files` 反代对隧道 Host 失效）。

```bash
# 已执行（可回滚：/root/nginx-hutuji-{enabled,available}.bak.20260731-145120）
scp deploy/nginx-hutuji.conf root@117.72.118.95:/tmp/hutuji-candidate.conf
ssh … 'sed -i "s/\r$//" /tmp/hutuji-candidate.conf
        install -m 644 /tmp/hutuji-candidate.conf /etc/nginx/sites-available/hutuji
        ln -sfn /etc/nginx/sites-available/hutuji /etc/nginx/sites-enabled/hutuji
        nginx -t && systemctl reload nginx'
```

历史复验（reload 前后逐项一致，无回归）：`:80` 两种 Host（裸 IP / `hutuji.donglicao.com`）下
`/files/nope`→404、`POST /files/nope`→403（`limit_except` 生效）、`/mcp`→404、`/`→301；
公网隧道 `/files/nope`→404、`/mcp`→404；`preflight.sh` 先 `pass=9 fail=0`。

**历史 9→14 项记录（已被下方第三轮安全契约 supersede）**：当时新增 nginx 层五项并得到 `pass=14 fail=0`，但“真下载”实际只比较了命令替换后的正文，没有检查 HTTP 状态、`Content-Length` 或 `X-Hutuji-CRC32`；符号链接项也只检查 `-L`，没有确认目标。旧 PASS 只能证明当时的旧判据，不证明当前安全契约。

**首次真 200 端到端**：在 `HUTUJI_OUTPUT_DIR=/opt/hutuji/mcp-server/output` 放一份合法命名产物
（`draw_20260731_145211_<22 位 token>.gcode`，31B），公网隧道与 `:80` 回源路径均 `HTTP=200 bytes=31`
且正文逐字回读，测完立即 `rm`。

### 第三轮传输安全加固(2026-07-31 21:20-21:22,**已部署并复验**)

2026-07-31 code review 发现旧 `:80` server 的 `/files/` 对任意公网来源开放,且 preflight 可能校验
与 systemd 实际生效值不同的首个重复赋值。本仓已修,**并已上线**:

1. `:80 /files/` 加 `allow 127.0.0.1`、`allow ::1`、`deny all`,只给本机 cloudflared 回源;
2. EnvironmentFile 不 `source`/`eval`,支持注释、空行、可选成对引号;重复键与 systemd 一致取最后赋值;
3. `HUTUJI_PUBLIC_BASE_URL` 只允许 HTTPS 默认 443 或显式 `:443`,且拒绝裸 `?`/`#` 与 loopback 主机;`OUTPUT_DIR` 先经 `realpath -m` 归一化,并要求字面路径与解析后的真实路径都位于 `/opt/` 下;
4. enabled 精确链接与 LF 哈希之外,`nginx -T` 还须证明活动配置含 loopback-only `:80 /files` ACL;
5. reload 后 localhost `:80` 活动探针核 GET=404、POST=403、`/mcp`=404;真下载同时核
   HTTP 200、`Content-Length`、IEEE CRC32 头和逐字节正文;解析值只经位置参数传入子 shell。

**部署前置核实**(必须先做,否则 ACL 会切断隧道):`/etc/cloudflared/config.yml` 的
`service: http://localhost:80`,而本机 `getent hosts localhost` **只返回 `::1`**,nginx 又只
`listen 80`(IPv4)。实测公网隧道请求在 access.log 里 `remote_addr=127.0.0.1`——cloudflared 的
Go dialer 在 `::1` 被拒后回落 IPv4,故 loopback ACL 安全;`allow ::1` 仅为冗余保险。

```bash
# 已执行(回滚副本:/root/hutuji-bak-20260731-212012/,含 nginx 两份 + preflight.sh + 三个 .py)
scp deploy/{nginx-hutuji.conf,preflight.sh,env.mcp.example} \
    mcp-server/hutuji_mcp/{cloud_bridge.py,config.py,server.py} root@…:/tmp/hutuji-deploy/
ssh … 'cd /tmp/hutuji-deploy && for f in *; do sed -i "s/\r$//" "$f"; done
        install -m 644 nginx-hutuji.conf /etc/nginx/sites-available/hutuji
        install -m 644 nginx-hutuji.conf /opt/hutuji/deploy/        # 漂移判据的比较基准,别漏
        install -m 755 preflight.sh /opt/hutuji/deploy/
        install -m 644 cloud_bridge.py config.py server.py /opt/hutuji/mcp-server/hutuji_mcp/
        ln -sfn /etc/nginx/sites-available/hutuji /etc/nginx/sites-enabled/hutuji
        nginx -t && systemctl reload nginx
        systemctl restart hutuji-mcp hutuji-cloud-bridge'
```

**复验证据**(2026-07-31 21:22):`preflight.sh` **`pass=14 fail=0`**;LF 归一化 md5 本仓 == 线上
`db3d5daed73c63e7684063efb65d09f2`;`readlink -f sites-enabled/hutuji` == `sites-available/hutuji`;
三服务 `active`。公网面:HTTPS `/mcp` GET/POST **均 404**、直连 `:80 /files/*` **403**、直连 `:80 /mcp` 404、
HTTPS `/files/__nonexistent__` 404。桥接重连后云端真实 `initialize`+`tools/list`+`ping` 已进日志,
且新脱敏 filter 生效(URL 记为 `<redacted-url>`、`mcp-session-id` 截断)。

> 首次部署踩坑:只 `install` 到 `/etc/nginx/sites-available/` 会让第 12 项
> 「线上 nginx conf 与本仓模板一致」**fail**——该项比较基准是 `/opt/hutuji/deploy/nginx-hutuji.conf`,
> 两处都要同步。

> **2026-08-08 `25ff33d` 已部署（supersede 上方全部「仓内未部署」与历史 pass 数）**：实际范围大于本批五修——生产 `server.py`/`gcode_gen.py`/`cloud_bridge.py` 都与 HEAD 有差、`sim_server.py` 缺失，等效 `69c2198` → `25ff33d`。`requirements.txt` 两端相同（`0b4629800c6f4bc7ea51f7f98028e8c6`），未装依赖。备份 `/root/hutuji-bak-20260808-204725-pre-25ff33d/`（`hutuji_mcp/`、`deploy/`、`nginx-hutuji.conf`、`units/`）。8 文件经 `git show HEAD:` 管道进 `/tmp/hutuji-stage/`，md5 与 HEAD blob 逐一相符、0 个 CR、`py_compile` + `bash -n` 通过；装后 md5：`config.py b3d70811cb7d630bc7dd0fc2da246bf2`、`llm_svg.py 1755860ae3c6ebb8d77914ca847b35ae`、`server.py a939eb676716923d47789ca813b4bd4c`、`gcode_gen.py 4211d365f68b25cf7fbed9c64bb4784e`、`cloud_bridge.py e2ab68d37cb03c9bfec026e47d990ed2`、`sim_server.py 35e175329b048154a1aa86d0a9d48201`、`preflight.sh 4f71860303268313bac36a2286be44ec`、nginx `9f71eacd21e85a4f19fcb1474bbfee53`。`daemon-reload` + 显式 restart（`hutuji-mcp` 20:54:41、`hutuji-cloud-bridge` 20:54:44）、ExecStartPre 自检 `status=0`、loopback `/mcp` 406（预期）。**首轮 preflight `pass=19 fail=0 expected=19` 是假绿**：同一时刻的 `nginx reload` 已被 master 静默丢弃（见下条），旧配置又继续服务了 37 分钟。修复后加固到 21 项，复跑 **`pass=21 fail=0 expected=21`**。公网复验 fix #2：真产物 CRC `775b4f31`/243B，HTTPS 200 且逐字节相符，日志 `123.202.***.*** via=127.0.0.1 … "GET /files/draw_….gcode" 200 243`（客户端 IP 末两段脱敏，实测值与 ipify 返回的本机公网 IP 一致）——`$hutuji_client_ip` 是真实公网客户端 IP、`via=$remote_addr` 是 cloudflared loopback。未改固件、未做硬件验证、未提升出货类判据。
>
> **`limit_req` 键变更无法 reload（2026-08-08 实测并已修）**：把 `/files` 限流键从 `$binary_remote_addr` 改成 `$hutuji_client_ip` 后 `nginx -t` 通过、`systemctl reload nginx` 退出 0、`is-active` 仍 active、19 项 preflight 全绿，但 `error.log` 里是 `[emerg] limit_req "hutuji_files" uses the "$hutuji_client_ip" key while previously it used the "$binary_remote_addr" key`——nginx 不能在 reload 时改共享内存 zone 的键，master **丢弃新配置、继续用旧配置服务**。旁证：worker 时间戳仍是前一天 17:31、没有 worker 持有 `hutuji-access.log` 的 fd、探针落进 http 层默认 `access.log`。已在独立 nginx 实例（`/tmp/nginx-repro`，用后清理）复现全部四个特征，含 reload 前后 PID 不变。处置：确认 `:80/:443` 无 established 连接、无在途 `/files`、`nginx -t` 通过、备份在位后 `systemctl restart nginx`（master `1055773`、worker `1055774`/`1055775`，`Sat Aug 8 21:31:57 2026`）。**规则**：改 `limit_req`/`limit_conn` 的键或 zone 名必须 `restart`，不能只 `reload`。新增第 16/17 项就是为了机器化这条：探针必须落进自定义日志格式，且 worker 不得早于 `stat -L` 读出的配置 mtime（`sites-enabled/hutuji` 是 `ln -sfn` 建的符号链接，不加 `-L` 会读到建链时刻——实测 `1786095075`=08-07 17:31:15 vs `1786193641`=08-08 20:54:01，整整差一天）；`[emerg]` 的比较基准取**当前最老 worker 启动时刻**而非配置 mtime，否则已被 restart 解决的历史 `[emerg]` 会永久红。
>
> **当时的仓内 preflight 修复已部署（2026-08-04，supersede 2026-08-01“未部署”状态）**：此前 `f3ef14b`/`8e4b9b4` 引入的变量初始化顺序回归，以及随后补齐的 `[skip]`、`ss` 前置、curl 超时/禁代理、本机 5xx 不算可达等硬化，已随 hutuji `f59c0a0` scp 到 `/opt/hutuji/deploy/preflight.sh`。生产当场复验 `pass=14 fail=0 skip=0`，生产脚本 SHA-256 `7196aa9efa13ccc839bf4b2e59294aee14142e062659efb44fab9a669ae8dc97` 与提交对象一致；回滚副本 `/root/hutuji-bak-20260804-f59c0a0/`。该证据只覆盖当时 14 项版本。
>
> **2026-08-05 `bdbb107` 已部署（supersede “仓内新增检查，尚未部署”）**：先备份到 `/root/hutuji-bak-20260805-bdbb107/`，再经 `/tmp/hutuji-deploy-bdbb107/` 统一 LF、`py_compile`/`bash -n` 后安装 `server.py`、`gcode_gen.py`、`preflight.sh`、`install-from-repo.sh`，重启 `hutuji-mcp` 与 `hutuji-cloud-bridge`。四文件 SHA-256 分别为 `c9fe792d…72ea8`、`9eb9d5d8…941a`、`2f26bd76…77cf`、`61e7bcf0…2327`，与本地候选一致。生产 preflight `pass=15 fail=0 skip=0`；生产 e2e `PASS`（14 行/242 字节）；四服务均 active；bridge 重连后 `initialize`、`tools/list`、`ping` 成功且 URL 脱敏；经运行中 `/mcp` 调用 `hutuji_status` 返回 `output_dir_exists=true`、公网基址正确、LLM 已配置、latest=`*.gcode`。开发机站外复验：HTTPS `/mcp` GET/POST 均 404、直连 `:80 /files` 403、`:8300` 为 `000`/连接超时。未改固件、未做硬件验证。
>
> **2026-08-05 深度审查修复（仓内，未部署）**：`deploy/preflight.sh` 五项硬化——①活动 ACL 判据原用 `grep -Fq 'deny all;'`，分不清 `/files/` 块内的兜底 deny 与 `limit_except GET HEAD { deny all; }` 行内那条；删掉兜底 deny 后 nginx 只剩两条 allow、默认放行、明文 `:80 /files` 对全公网可读，而 grep 命中 limit_except 那行仍报 pass（用脚本原样管道实测复现）。现先剔除 limit_except 行、只认独立成行的兜底 deny，并断言两条 allow 必须排在 deny 之前（nginx allow/deny 按出现顺序匹配，deny 提前会先拒 cloudflared 的 loopback 回源）。②**删除 `OPTIONAL_SKIP(125)` 机制**：放行算术曾是 `(PASS+SKIP)==EXPECTED_CHECKS`，skip 与 pass 完全等价，于是「公网访问 `:80 /files` 被拒」可以从未被验证过、脚本照样打印 15/15 全绿（实测 `pass=14 fail=0 skip=1` 输出 PASS）。取不到非 loopback IPv4 时现在**判 fail**，由人确认，不得由脚本自行豁免。③`HUTUJI_PUBLIC_BASE_URL` 拒 RFC1918 与单标签主机（`https://192.168.1.5`、`https://10.0.0.8`、`https://intranet` 原本全报 pass）。④`/mcp` 可达谓词从 `^[234]` 收紧——MCP 子应用挂载失败时裸 POST 返回 404，原判据会把「端点已不存在」报成 pass。⑤输出格式改为 `pass=N fail=N expected=N`，放行判据 `PASS==EXPECTED_CHECKS && FAIL==0`。另：`nginx-hutuji.conf` 的 `:80` 兜底 301 从 `$host` 改字面量（两个 listen 都无 `default_server`，任意 Host 头都落本块并被回显进 Location，构成开放重定向）；`install-from-repo.sh` 去掉 `bash -lc` 的 `-l` 并 `env -u` 清掉 `PIP_*`/代理变量（root 登录期配置导出的 `PIP_TRUSTED_HOST`/`PIP_EXTRA_INDEX_URL` 会静默盖过脚本只传的 `-i`/`--cert`，推翻「禁止绕过 TLS 校验」的承诺）；两个部署脚本的 chown 顺序改为先收代码树、不动 output 属主；两个 unit 补 `ProtectKernel*`/`RestrictAddressFamilies`/`SystemCallFilter`，bridge 的 `StartLimitIntervalSec`/`StartLimitBurst` 从 `[Service]` 移到 `[Unit]`（原位置被 systemd 静默忽略）；`requirements.txt` 五个无上界依赖补上界。本地 hub `overall=pass`（pytest **262 passed**、e2e `PASS`）；ACL 与 301 两处修复均做过变异验证（删兜底 deny / 改回 `$host` 后 preflight 判据与新增 pytest 断言都转 fail，改完即恢复）。**未部署生产、未改固件、未做硬件验证**——生产仍是 `bdbb107`。
>
> **2026-08-06 `69c2198` 已部署（supersede 下方两条「仓内未部署」）**：**全量对齐**，13 文件一次发齐——生产原为 `bdbb107`，落后一批「已提交未部署」的积压：`preflight.sh`(15→17)、`gcode_gen.py`、`cloud_bridge.py`、`llm_svg.py`、`grbl_sim.py`、`pipe_sim.py`、`hutuji_gwrite.toml`、两个 unit、`nginx-hutuji.conf`、两个安装脚本、`requirements.txt`。profile 的落笔 F1000→F10000 **会改变纸面笔迹**，已获用户授权。流程：备份 `/root/hutuji-bak-20260806-69c2198/`（36 文件）→ `/tmp/hutuji-deploy-69c2198/` 上传（md5 与本地 git blob 逐一相符，字节级复核 0 个 CR）→ `bash -n` ×3 + `py_compile` ×5 → 核对现装依赖满足新上界（未重装）→ `install`（`output` 属主始终未动）→ 装 unit 与 nginx conf → **`daemon-reload`** → `nginx -t` → `reload` + 重启两服务。验证：preflight **17/17**（「生成链」与「unit 一致性」两项**首次在生产通过**）、生产 e2e `PASS`（14 行/243 字节）、四服务 active、`/run/hutuji/cloud-bridge.ready` == MainPID、站外 404/404/403/000、活动 301 目标为字面量域名。已推送 `fa0ce13..69c2198`。**未改固件、未烧录、未做硬件验证**；新 profile 下的纸面笔迹未经目视验证。
>
> **2026-08-05 补修两项（同批，仓内未部署；16→17 项）**：①**§5 字节口径**：`_read_generated_gcode` 用 `read_text()` 读产物，CRLF 被 universal newlines 塌成 LF，每行少算 1 字节；S3 下载到的与 `Content-Length`/CRC32 算的都是磁盘原始字节。实测某 20846 行 CRLF 产物磁盘 524369 字节（超 512KiB 上限 81 字节）被判未超并放行，`bytes` 少报 20846。改为 `read_bytes()` + `validate_gcode(text, total_bytes=len(raw))`；另修一条原本正钉着错口径的既有测试。②**unit 一致性（`check_units_match_templates`）**：`is-active` 不证明跑的是哪一版 unit，加固项可全缺而照样 active；两个 unit 与 nginx 安装方式同构却只有 nginx 有一致性判据。现对两个 unit 各查 LF md5 一致、`NeedDaemonReload=no`、`FragmentPath` 指向 `/etc/systemd/system/`、无 drop-in（glob 判定；`DropInPaths` 在老版 systemd 返回空串，只信它是 fail-open）。**部署提醒**：本项要求 scp 后必须 `systemctl daemon-reload`，否则即便文件一致该项也判 fail——这正是它要抓的场景。`hub overall=pass`（pytest **292 passed**、e2e `PASS`）；变异验证 4/4（字节口径）+ 5/5（unit 判据）。**未部署，生产仍 `bdbb107`。**
>
> **2026-08-05 preflight 生成链盲区修复（仓内，未部署；14→15→16 项）**：此前 15 项**没有任何一条执行过部署的 `hutuji_mcp` 代码**——逐条核过每个 `python3` 调用，只用到 `urlsplit`/`secrets`/`zlib`。下载探针用 `printf "G21\nG90\nF8000\n"` 人工拼产物，而那份内容连 `validate_gcode` 自己都不接受（实测报「第 4 行缺少 §5 文件头 `G1G90 Z0.0F10000`」），所以下载项只证明了「nginx 能把一个文件按字节发出去」。三处实测复现：①把 `hutuji_mcp/hutuji_gwrite.toml` 移走后，`import hutuji_mcp.server` 依然成功、服务保持 active、`/mcp` 与 `/files` 照常响应，而真实 `svg_to_gcode()` 报 `vpype 转换失败（exit 2）: --config … does not exist`——即 `pass=15 fail=0` 与「一画就整单失败」可以同时成立；②把 profile 的 `document_start` 首行 `F8000` 改 `F6000`，生成链 fail-closed 于 `第 3 行不是精确的 §5 文件头 F8000（实际：F6000）`，但同样只在请求期暴露；③`vpype` 是 subprocess、**从不 import**，依赖装坏与 profile 缺失一样逃过 import 与 `systemctl active`。根因是 `_PROFILE_PATH` 只被请求期的 `_vpype_command()` 使用，import 期不加载不校验。修复：新增 `check_generate_chain()`，用**生产 venv 的解释器**（`/opt/hutuji/mcp-server/.venv/bin/python`，不能换系统 `python3`——`_vpype_cmd()` 按 `sys.executable` 同目录定位 vpype，换解释器即换掉被验对象）真跑一次最小 SVG 的 `svg_to_gcode()`，产物落 `mktemp -u`、不污染 `/files`，并要求 `lines>0 && bytes>0`（空产物也是坏产物）；原下载项改为先用同一条链生成**真产物**再验 200/长度/CRC32/正文全等，于是 profile、vpype 依赖、生成器、§5 校验器与下载链一次覆盖。`EXPECTED_CHECKS` 15→16。变异验证：移走 profile 与改 `document_start` 的 `F8000`→`F6000` 两种变异下，探针判据均从 exit=0 转 exit=1，还原后即恢复（profile blob 哈希已与 HEAD 逐字节核对一致）。本地 hub `overall=pass`（pytest **287 passed**、e2e `PASS`）。**未部署生产、未改固件、未做硬件验证**——生产仍是 `bdbb107`（15 项版本，不含生成链项）。


#### 漂移判据（必须 LF 归一化）

本仓 `core.autocrlf` 让工作副本是 CRLF，裸 `md5sum` 永远对不上线上：

```bash
tr -d '\r' < deploy/nginx-hutuji.conf | md5sum
ssh … 'readlink -f /etc/nginx/sites-enabled/hutuji; tr -d "\r" < /etc/nginx/sites-enabled/hutuji | md5sum'
# 目标必须是 /etc/nginx/sites-available/hutuji，两个动态计算的 md5 必须相同。
```

#### SSH 私钥名有坑

可用私钥是 `C:/Users/zhugu/.ssh/jdcloud_ed2519`（**少一个 `5`**）；同目录 `jdcloud_ed25519`
会被服务端拒 `Permission denied (publickey,password)`。`~/.ssh/config` 的 `Host jdcloud` 走密码认证，
非交互脚本请显式 `-i .../jdcloud_ed2519 -o IdentitiesOnly=yes`。

## 首次上线（阶段 1）剩余

1. 小智控制台：确认知识库已**关联到智能体**；刷新 MCP 工具列表；试 `hutuji_status` / 语音「画一只侧脸简笔小猫」。  
2. 角色提示若未贴：`docs/xiaozhi-agent/role-prompt.md`。  
3. 证据写入 `docs/templates/release-manifest.md`。

## 环境变量

| 文件 | 变量 | 说明 |
|------|------|------|
| `mcp.env` | `HUTUJI_HOST/PORT` | **不是**监听面真值（unit 的 `--host/--port` 才是）；`HUTUJI_HOST` 非 loopback 会在 import 期抛错，但 uvicorn CLI 可另绑 `0.0.0.0`。仓内候选另由 `LoopbackOnlyMcpGuard` 按 socket 对端把非 loopback/未知 `/mcp` 请求塌成 404；仍不可省略 unit loopback 监听 |
| `mcp.env` | `HUTUJI_PUBLIC_BASE_URL` | HTTPS 公网前缀（S3 下载） |
| `mcp.env` | `HUTUJI_LLM_*` | LLM |
| `mcp.env` | `HUTUJI_OUTPUT_*` | 产出目录与 TTL |
| `bridge.env` | `MCP_ENDPOINT` | 仅桥接；小智控制台复制 |
| `bridge.env` | `HUTUJI_LOCAL_MCP_URL` | 默认 `http://127.0.0.1:8300/mcp` |

## 回滚

### A. 主机切换回滚（主生产在阿里云时的割接回退）

1. DNS 把 `hutuji.donglicao.com` 从 A 记录 `47.112.162.80` 指回 Cloudflare Tunnel 目标（CF 面板改回 proxied CNAME）
2. 京东云：`systemctl enable --now hutuji-cloud-bridge`，验 `curl 127.0.0.1:8300/bridge-health` 200
3. 阿里云：`systemctl stop hutuji-cloud-bridge && systemctl disable hutuji-cloud-bridge`（防 token 对拍）
4. 判据：公网 `https://hutuji.donglicao.com/files/<TTL 内产物>` 200、`/mcp` 404；bridge 双 connected。**注意 preflight.sh 30 项为 Debian/京东云口径，在阿里云恒红——回滚后（京东云）可直接跑；阿里云侧在该脚本适配 el8/conf.d 前以手工判据代替（`tools/list` 7 工具、真链出图、公网真下载逐字节一致、`/mcp` 404）**
5. 在 `docs/templates/release-manifest.md` 记回滚原因与时间

### B. 单机代码回滚（同一台机内回退应用版本）

1. `systemctl stop hutuji-cloud-bridge hutuji-mcp`  
2. 检出上一发布 commit / 恢复上一 venv  
3. 恢复对应 EnvironmentFile 与 nginx conf；若回滚会改回 `limit_req` 的键或 zone 名，必须 `systemctl restart nginx`，`reload` 会被静默丢弃
4. 再 `start`；京东云跑 `preflight.sh`（30 项），阿里云在脚本适配前用 A 节的手工判据 + 一次 `hutuji_status`
5. 在 `docs/templates/release-manifest.md` 记回滚原因与时间  

详细命令见 `../deploy/README.md`。运维日常见 `operations.md`。
