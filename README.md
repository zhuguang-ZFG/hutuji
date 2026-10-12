# hutuji — AI 绘图自动换纸写字机

2026-10-12 生图通道修复候选：商汤上游移除 `sensenova-u1-fast` 后，现存 `sensenova-u1.5-lite` 返回 `b64_json`，旧服务在 URL 提取处拒绝，叠加 Agnes 免费额度与 1 分钟限流造成可用性和质量同时下降。现已支持 URL/内联图双形态，商汤独立 75s 单次预算与 10MiB 响应闸，描摹、SSRF、16MiB 图片闸和先到先得对冲策略不变；MCP 1669 项与 e2e 通过。S3 CI 修复提交 `45a0149` 保留 P4 的 IDF6 覆盖、移除其已废弃的 IDF5.5 兼容项。部署、CI 与 Grbl 物理复位阻塞见[本批发布记录](docs/design/three-repo-release-20261012.md)和 readiness。

2026-10-10 三仓PR10修复：串口配网拒绝实时控制保留字符，HTTP保留凭据空格，工厂握手完整消费应答，OTA发送前复核当前版本，产线导出检查放行附件。5项修复与39项新增回归、右括号SSID补修1项见[修复及发布记录](docs/design/three-repo-production-review-20261010.md)。**发布supersede候选状态**：hub ca54562双机Portal已部署，S3 301eb65/1.0.38和量产Grbl b280e525已升级；HTTP参数保真、恢复原SSID及48项参数保持通过。工厂配对/手机矩阵与纸面/老化等实体缺证仍见readiness。

2026-10-10 量产工具本地候选：`scripts/factory/`提供完整镜像打包、逐台身份/参数核验、烧录备份、验收台账与可迁移档案，`production/`保存已批准配方；使用见[工位手册](production/README.md)。双板配对、中文SSID和Portal固件灰度尚未部署；实体首件、手机与交付签核缺项仍见[改进记录](docs/design/production-readiness-20261010.md)及readiness。产线回归须单独跑`python -m pytest -q tests/production`，不由hub门禁代替。

2026-10-09 三仓续审已发布：门户保留已知拒绝原因、S3 1.0.34回收OTA失败资源、量产Grbl修复看门狗计时及实机追加发现的WebSocket生命周期问题。本批空闲态断流/断网恢复通过，Wokwi配额未验；版本、证据与回退见[发布记录](docs/design/three-repo-followup-review-20261009.md)。

2026-10-09 三仓续审修复候选：量产Grbl看门狗计时改为单任务维护，S3 OTA句柄与缓冲统一回收，门户复用共享判错保留已知设备拒绝原因。回归、构建与未部署边界见[续审记录](docs/design/three-repo-followup-review-20261009.md)，当前上线状态只看readiness。

2026-10-08 三仓审查：修正设备忙拒绝被当成发送成功、OTA 丢应答提前解除等待、S3 自愈任务回收与重启接管竞态。门户与 MCP 的设备业务判错共用 `hutuji_mcp.tool_result`，兼容旧固件。源码、测试与构建记录见 [审查修复](docs/design/three-repo-review-20261008.md)，生产和板上状态仍以 readiness 为准。

2026-09-24 多轮复审修复候选：语音按认证用户隔离并串行处理同会话；Portal OTA 原子占位与阶段/代次回写；S3 OTA 与运动共用占位；Grbl 请求上下文按任务隔离；MCP 配置读取不阻塞事件循环，vpype 调参保留在途计数。**用户再次确认量产写字机不自动换纸，交付只用 `Grbl_Esp32-massprod` 的 `massprod/nopaper-1.3a` 分支；不得移植普通分支换纸模块以满足旧门禁。** 详见 [迭代记录](docs/design/review-fixes-20260924.md)，上线和板上状态只看 readiness。

语音说一句话，云端 AI 生成绘图内容，写字机自动画出来、自动换纸。

2026-09-14 纸张与创作候选：每台设备独立保存纸张、方向、留边、XY 交换和已核对的可绘制范围，图片、文字、笔顺、文档及手绘共用一次快照。新产物携内容绑定配置，新 S3 下载前核对配置；选择大纸不扩大旧机型包线。手绘增加镜像、六向、素材与回放，保留留白。契约见 [`protocol §10.4.24`](docs/protocol.md)，实施及发布状态见 [`纸张任务`](docs/design/paper-settings-20260913/implement.md) 与 readiness。

运维入口 `scripts/auto_deploy.py` 从显式提交导出 LF blob、逐文件核 SHA 并保存可重试回滚记录；`scripts/cross_repo_sync.py --gate-all` 包含 Portal 与 fz 检查。队列版本切换必须排空并停止旧 worker。精品图库首批 467 件、20 类的固定来源、许可与逐件预览记录在 `mcp-server/market/`，上线和手机验收以 readiness 为准；发布工具仅对图库白名单开放中文类目目录，文件上限2048。`configure/` 的空白下载工具状态不是量产配方。详见 [`工具审查`](docs/design/production-tools-review-20260914.md) 与 [`图库设计`](docs/design/drawing-market-20260914.md)。

2026-09-13 三仓评审修复：调速改为后台事务并等待三轴实际读回，普通重启保留速度；奎享完成只接受目标机器的连续新广播；云端主备以 `HUTUJI_BRIDGE_ACTIVE` 保持单 bridge。问题、回归与交付证据见 [`评审记录`](docs/design/crossrepo-review-20260913.md)，当前部署状态仍以 release-readiness 为准。

量产机三轴速度（2026-09-13 用户拍板）：X/Y 默认 10000，Z 保留机器现值；小程序分别设置 X/Y/Z，重启保留。单轴写入前检查设备分轴能力，写后查询三轴实际参数，确认选中轴生效再提示成功。设计与验收见 [`三轴调速`](docs/design/axis-speed-20260913.md)，当前板上状态仍以 readiness 为准。

2026-09-13 小程序审查约定：`/api/auth/me.active_device_mac` 用于控制，`binding.device_mac` 仅作打码展示；测试从真实返回形状与正常页面加载开始。状态轮询串行，异步控制按页面/账号/设备隔离；同账号生成跨切页保留结果，录音取消不续发工具。内部隐私核对稿不代表已发布协议或法律签核，实际数据处理与证据见 [`小程序审查`](docs/design/weapp-review-20260913.md)。

量产机任务完成或停止归位后自动释放电机，下一任务重新运动时自动使能。停止请求复用同一次收尾，归位完成后才显示已取消；绘图与暂停时继续保持电机使能。行为与验证边界见 [`protocol §10.4.15`](docs/protocol.md)。

近期评审修复涉及预览资源边界、波纹白区保护、登录回包隔离、字体选择、重试日限与 Tunnel 固件回源。行为契约见 [`docs/protocol.md`](docs/protocol.md)，本地验证及部署状态见 [`docs/release-readiness.md`](docs/release-readiness.md)；小程序回归命令见 [`draw-portal/README.md`](draw-portal/README.md)。

2026-09-12 一次扫码绑定扩展：S3 持久化 P-256 身份与单次 nonce，小程序自动切网/认领/恢复，portal 按真实连接就绪完成绑定并支持一账号多机；新旧版本兼容及回滚边界见 [`docs/design/device-identity-rollout.md`](docs/design/device-identity-rollout.md)。固件源码仍只在独立 fork，本地软件验证不代表生产或手机验收。

## 架构（双芯片）

照片精修的结构线、排线连接和预览/SVG 几何保真约定见 [`docs/design/photo-sketch-quality-20260912.md`](docs/design/photo-sketch-quality-20260912.md)；本机对比与生产、纸面验收分别登记，当前状态仍看 release-readiness。

```
用户语音
   │
   ▼
┌─────────────────────┐   WiFi/HTTPS   ┌──────────────────────┐
│ AI 交互板            │ ◄────────────► │ 小智官方云 + MCP 服务  │
│ 立创实战派 ESP32-S3  │                │ (阿里云主 / 京东云热备) │
│ xiaozhi-esp32 固件   │ ◄─ HTTPS 下载 G-code ─┘               │
└─────────┬───────────┘    (公网 DNS A 直连；Cloudflare Tunnel 退役为回退路径)
          │ WiFi Telnet :23（两板经家用路由器，S3 缓存 IP + 扫网发现写字机）
          ▼
┌─────────────────────┐
│ 运动控制板（ESP32）   │  ← 成熟商业化现役固件
│ Grbl_Esp32 固件      │     含自动换纸系统（M701/M721…）
└─────────────────────┘
```

## 决策记录（2026-07-24，grill-me 逐项确认）

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 芯片架构 | **双芯片**：S3 跑 xiaozhi，独立 ESP32 跑 Grbl；不合并固件 |
| 2 | 板间通讯 | **WiFi Telnet**（方案 E）：写字机 STA 连路由器开 Telnet:23，S3 TCP 直连。USB Host 被 CH334F HUB 堵死、CH9350 仅 HID 不支持 CH340——均否决；拆机 TTL 留作备用 |
| 3 | AI 绘图链路 | **云端生成+转换 G-code**，S3 板只做下载与串口转发（哑管道） |
| 4 | AI 服务 | 小智**官方云**，自定义能力走 MCP 接入点 |
| 5 | MCP 服务部署 | **阿里云主 + 京东云热备**（阿里云 2核2G `47.112.162.80` **现役生产**，2026-08-23 晚割接，DNS A 直连；京东云 2核4G `117.72.118.95` 热备）；2026-08-23 用户反转 2026-07-25「仅京东云单点」锁定——cloud bridge 是单 WSS 长连接，任一时刻只在一个节点跑，切换须先停旧 bridge 再启新 |
| 6 | 换纸机构 | 已有成熟商业化方案，由 Grbl 现役固件控制，**机械/时序/运动/授权不改动**；消息路由、状态字段、WebUI 网络层等不影响商业出图的小加固可接受（2026-07-25 口径调整，清单见 `docs/protocol.md` §9） |
| 7 | 本仓库定位 | **枢纽仓库**：新代码+协议+文档；两 fork 各自独立 |

## 仓库布局

| 仓库 | 路径 | 角色 |
|------|------|------|
| hutuji（本仓库） | `D:/Users/hutuji` | MCP 服务、协议文档、部署脚本、联调 SOP |
| [Grbl_Esp32](https://github.com/zhuguang-ZFG/Grbl_Esp32) | `D:/Users/Grbl_Esp32` | 运动控制固件（现役分支 `fix/panel-hold-after-align-116687e`；实时 HEAD 与板上证据看 `docs/worktree-inventory.md`，不得复制静态 SHA 当当前真值） |
| [xiaozhi-esp32](https://github.com/zhuguang-ZFG/xiaozhi-esp32) | `D:/Users/xiaozhi-esp32` | AI 交互固件（板型 `lichuang-dev` / Waveshare 3.5；实时 HEAD 与板上镜像证据看 `docs/worktree-inventory.md`） |

## 目录

- `docs/agent-handoff.md` — **Agent 防呆页**（误判表 / 默认下一刀；接手先读）
- `docs/agent-anti-drift.md` — **防幻觉 / 禁乱改**铁律
- `docs/agent-constraint-matrix.md` — 三仓 agent 开发约束矩阵
- `docs/agent-edit-allowlist.json` — 改码白名单（机读）
- `docs/worktree-inventory.md` — 跨仓未提交资产清单
- `docs/agent-gate.md` — **本仓门禁定义**（profile / 禁止宣称）
- `docs/foundation-checklist.md` — ① 基础建设闭环勾选
- `scripts/agent_gate.py` — **可跑门禁**（默认 `hub` → `results/hutuji_gate_last.md`）
- `scripts/xiaozhi_console.py` — 小智控制台配置同步 + 官方设备消息 API 的七个 `hutuji.*` 白名单直调（凭据只走环境变量）
- `.cursor/rules/hutuji-agent-rails.mdc` — Cursor 硬轨（防跑偏 / 防幻觉）
- `docs/README.md` — **文档总入口**：新 agent/开发/测试/运维/生产从这里开始
- `docs/release-readiness.md` — 当前真实就绪度与出货阻断项
- `docs/protocol.md` — 板间链路协议与 G-code 文件约定（**v0.5**，②③ 的共同契约）
- `docs/bringup-sop.md` — 阶段 0–9 联调与小批量出货门禁
- `docs/deployment.md` / `docs/operations.md` — 京东云部署与值守
- `docs/templates/` — 逐台测试记录、批次发布清单模板
- `mcp-server/` — MCP 生成服务（③，`hutuji_mcp/redact.py` 为 MCP/桥接/取证脚本共享日志脱敏唯一实现；契约/安全回归、京东云 HTTPS 常驻与真实语音出图均已有证据；当前未闭项见 `docs/release-readiness.md`）
- `firmware/` — S3 板固件补丁说明（②，补丁本体在 xiaozhi-esp32 fork）
- `deploy/` — 京东云 systemd/nginx/环境变量模板（真实凭据不入库）

### BLE 只读诊断辅助面（阶段 A 已实现，默认关闭）

正式 Telnet/G-code 契约仍为 v0.5；BLE 使用独立 `BLE-DIAG schema v1`。阶段 A 已实现为 6 字节公开粗粒度广播，不注册 GATT；阶段 B 才提供需本地确认配对、LE Secure Connections + MITM、单 peer、每秒一次读取上限的 22 字节只读诊断快照；阶段 C 由手机/PC 消费广播并显示 `near/unknown` RSSI 近距提示，S3 不做持续扫描。WiFi Telnet :23 仍是唯一正式板间链路。BLE 不承载 G-code，不直接控制电机、笔、换纸、授权、abort 或 job 生命周期；不以 RSSI 冒充距离/方向/UWB/AoA/Apple Precision Finding。默认镜像保持关闭；阶段 A 异步广播修复已有 COM14 窄范围 HIL，当前 pending event 合并/轮换优先级加固已过 BLE 专测 18/18、全 host 93/93 与默认/启用构建，但当前产物未刷机。共存量化、功耗/温升和阶段 B/C 仍未完成，见 `docs/release-readiness.md`。

### 外部参考证据纪律

涉及社区、GitHub、上游文档、SDK 或硬件资料时，先查官方来源，再查高质量社区；影响设计的资料登记 URL、版本/commit、访问日期、适用板型与范围、本地复核命令/结果、限制和未验证项，落在相关设计段落或明确链接的 findings。外部资料不能覆盖本仓契约/状态真值，且必须区分上游、本地 fork、源码、构建物、板上固件和实机行为；网络不可用时使用本地留档须标明来源层级与快照日期，不能把社区共识写成实机证据。

## 路线图

1. **① 仓库奠基**：协议、文档入口、SOP、生产/部署模板、本仓门禁/硬轨 — ✅ 见 `docs/foundation-checklist.md`
2. **② 哑管道**：S3 设备侧 MCP + 下载校验 + Telnet 流控 — 🟡 实时分支/HEAD 与板上镜像证据以 `docs/worktree-inventory.md` 为准（此处不钉静态 SHA，防腐）；截至 2026-08-28 板上 COM14 现役镜像含唤醒/Grbl 主屏指示（聆听扫光/胶囊变色/说话钮变绿/顶栏三级圆点）与顶栏 z-order 修复，用户照片确认 Grbl 绿点上屏；历史已证：三轮画图 HIL、触摸、七工具、Telnet、授权、fresh-`Idle` 与控制抽屉低层动作的部分 HIL，2026-08-21 新增唱歌双工具（`hutuji.sing`/`hutuji.stop_song`）并语音全链实测通过（云端 `hutuji_sing` → 下载 → 播放 → 喊停退场）。**仍缺**：预览确认/取消与暂停/继续/停止/重画/试笔五个屏幕动作 HIL、纸面画质目视、断连恢复 `paper_active` 窗口 abort 复测；另登记 PSRAM 所有权缺口（下条）。
   - 2026-08-23 全链审计补充阻断：源码审查实测 `hutuji_job.cc` 预取接管路径在上一张成功且预取命中时不释放旧 G-code PSRAM（`AdoptPrefetch` 无所有权交换）——重复「新画一幅」会持续累积 PSRAM；连同 `scripts/xiaozhi_console.py` 白名单缺 `preview_url`（设备 `hutuji.draw` 双参数），两者均待用户批准独立批次修复。
3. **③ 智能端**：MCP 生成服务 + 小智官方云 + 阿里云主/京东云热备 — 🟡 当前生产含 `4af959d` + 2026-08-30 19:04 阿里云 **Redis 队列/worker/PG shadow**（OSS 未开；京东云热备不跑队列）。阿里云 preflight 当场 23/33、京东云数字须按热备口径读；真实语音出图 HIL 未执行，剩余设备预览/控制人机验收、纸面画质与生产签核。

> **生图后端边界（2026-08-20）**：正式 `hutuji_draw` 始终保持「PNG → 中心线描摹 → SVG → G-code」。同题完整 draw 事务由独立任务持有，云端取消只结束 waiter；约 35 秒重试复用原任务、同一个限流名额、同一份产物，不得另起付费生成。位图层另有 10 分钟/16 项中心线缓存与覆盖生图+描摹的 in-flight 合并；未命中时商汤 `sensenova-u1-fast`、Agnes、imagefree、可选硅流（schnell + 可图 Kolors）、可选魔搭 Qwen-Image 与可选 TCB 混元（2026-09-08 起，同为对冲 racer）并发对冲、谁先出图用谁，商汤瞬态 500 快速重试一次，全失败才报错。同一逻辑生成轮仍按既定对冲同时调用已配置后端；取消重试去重不改变该成本/延迟决策。同一 SVG 的 G-code 内容缓存命中时跳过 vpype；禁止切成模型直写 SVG。Pollinations 经 6 个冷 seed + 真实描摹否决（5 次 44–46s，含照片/3D 阴影与纹理，描摹 300s 未收敛），不得加入正式 racer。聊天中出现过的 key 视为泄露，不得使用。

> **2026-10-12 生图后端 supersede**：商汤现役模型改为 `sensenova-u1.5-lite`；旧 `u1-fast` 已被上游移除。u1.5-lite 的 URL/`b64_json` 双响应共用一条严格提取链，内联图不经过 URL/SSRF 路但仍受 16MiB 图片闸并进入同一中心线描摹。该慢通道按实测设 75s 单次预算、同轮不重试；其余后端和 first-past-the-post 对冲不变。

> **本地缓存优先（2026-08-24，设计稿用户批准）**：`hutuji_draw` 事务在付费生图前先查 Quick Draw 本地缓存（`quickdraw.lookup`，中文主题→官方类目映射 v4（402 别名键→298 类）已对账 categories.txt；数据 2026-08-25 C 组合换装精选版每类 50 条（14900 样本，原始 200 条/类本地备份）；质量路由：普通描述走 AI 位图链、仅简笔风描述查 QD）；命中则跳过付费生图与付费限流名额，但服务异步 G-code 转换仍与付费路线共用最多 2 个 vpype 子进程的资源闸，直接复用位图路线的同一 SVG 产物面与 §5 校验（含预览 PNG），未命中/禁用/数据损坏一律回落既有 AI 对冲。数据由 `mcp-server/scripts/quickdraw_prep.py` 离线准备（CC BY 4.0 署名见 `mcp-server/hutuji_mcp/QUICKDRAW-ATTRIBUTION.md`），生产开关 `HUTUJI_QUICKDRAW_DIR`，空值即回纯 AI 链。
4. **④ 小批量出货**：逐台质检、追溯、合规、客户交付 — 🔴 流程已建，须待阶段 0–8 真实证据后放行

实时状态以 `docs/release-readiness.md` 为准。改本仓先跑 `python scripts/agent_gate.py`；改 Grbl 另跑 fz 门禁。
# 2026-10-09 续审修复候选说明

本地候选增加笔控事务会话校验、Grbl超长行整行拒收及按作品地址自动确认；部署须先S3能力、后Portal，旧固件保留预览并转人工确认。未改变运动参数或换纸行为，实际发布状态只看`docs/release-readiness.md`，详见`docs/design/three-repo-next-review-20261009.md`。

2026-10-10 DR10修复候选（软件）：Telnet使用会话代次隔离旧字节；开放网络用`[ESP101=]`写空STA密码；预览阶段拒绝pause/resume；nopaper电机/压纸遥测为unknown；设备OTA严格版本递增并验证下载SHA及镜像版本；token回写比较绑定代次和旧密文；历史flash备份登记缺失拒绝归档。未部署/未刷机，验证与边界见枢纽`docs/design/three-repo-deep-review-20261010.md`修复节和readiness。
