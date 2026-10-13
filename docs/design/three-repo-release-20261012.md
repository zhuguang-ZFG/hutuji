# 三仓续审与技术发布（2026-10-12）

用户明确要求“继续 codereview 三仓，修复改进部署刷机，验证，推送，ci 变绿”。承接 R7 文档导入修复及上一批发布记录，保持量产 nopaper、运动参数、NVS 身份、引脚与 OTA 校验契约。

## 审查与修复

| 范围 | 证据与处置 |
|---|---|
| Hub R7 | UTF-16 编码绕过 ASCII DTD 预检；公共 XML 解析器在内部子集及正文前拒绝 DOCTYPE。R7 当时 MCP 1660 项、门户文档相关 16 项及 e2e 通过；生图响应修复加入后为 1669，本批 breaker 两项回归加入后为 1671。完整根因与兼容性边界见三仓续审 R7。 |
| 生图质量与可用性 | 期望：已配置的对冲池至少有两条可用图像通道，返回图进入同一中心线描摹。实际：商汤旧模型 `sensenova-u1-fast` 已从聚合器移除而持续 404；魔搭因余额不足 429，TCB 上游 429；Agnes 免费层达到日额度后又受 1 分钟一次限制。最小复现显示 u1.5-lite 返回 `data[0].b64_json`，旧 `_extract_agnes_image_url` 在首个分歧边界只接受 URL。修复支持 URL 优先或严格 base64 内联图，商汤 75s 单次尝试、同轮零重试、专用 10MiB JSON 闸，解码图仍受 16MiB 上限；静态与运营后台动态 racer 共用。PNG→中心线→SVG→G-code、URL SSRF 闸、先到先得对冲和其余端点 64KiB 上限均不变。 |
| 熔断恢复 | 期望：open 冷却后跨 worker 只放一个 half-open 探针；实际：旧 `should_skip_backend()` 把 half-open 继续当 open，通道无法自愈。首个分歧边界在 Redis probe claim：旧实现只接受已写好的 half-open，open→half-open 与占锁不原子。`8c843d9` 改为 `BackendPermit`，Lua 原子迁移并以 `SET NX EX` 占唯一探针，状态 TTL 长于探针锁；动态/静态对冲在创建 task 前认领，取消、输家和 cap 裁剪均释放 permit，Redis 故障仍 fail-open。生产 `HUTUJI_BREAKER_ENFORCE=0` 保持 shadow，本批未开启过滤。 |
| 画质样张 | 同题“小猫”本地真调用：u1.5-lite 生成稀疏线稿，中心线 32 条/3913 点、SVG 约 50.1KiB；Agnes 2.5-flash 8.3s 出图，中心线 20 条/571 点、SVG 约 7.2KiB。两者均可描摹；这些是本地样张/描摹链证据，不是当前生产 live draw。u1.5-lite 仅作为待轮换 key 后可验证的候选备用通道，Agnes 是否切默认及升级 Token Plan 涉及付费决策，本批不代选。 |
| S3 CI 与宿主回归 | `2b997d4` 给 release matrix 增加配置中的真实 `target`，对全部 `esp32p4` 变体仅在 CI 工作副本临时改 `esp_video 2.0.*`；S3/C3 与仓内产品锁 `2.3.*` 不变。Build Boards run `38275272261` completed/success。宿主全套以默认命中的 Espressif riscv clang 在链接前报 `_Unwind_*`，同一代码显式使用 x86_64 MinGW g++ 后 `366/366`，故归类为宿主编译器环境问题，未改产品/测试求绿。 |
| S3 运行路径 | 复查 OTA 主机/端口/板型路径、版本单调、SHA、预约与调度失败释放；本轮未新增确定性产品缺陷。现役量产 S3 只读状态为 `hutuji.1.0.40`、connected/ready/authorized/settings 全 true、nopaper/Idle；未刷 S3 新镜像。 |
| 量产 Grbl | COM7 物理恢复后按原 HTTP `/updatefw` 单次刷入 `539e3f9b`，候选 `firmware.bin` SHA-256 `f21b07d9432ead0220756294849c6c4ba773464752b8aac6377c2cc4f6fe235e`；回执 status `3`、观察到重启。刷前/刷后 48 项设置逐项相同，随后只读 `$I`/`$$`/`?` 复核 VER `1.3a.20260910`、MAC `4C:C3:82:CC:8A:9C`、Idle/原点、速度 6000/6000/1200、加速度 3000/3000/3000。未探测 WebSocket `:81`，未重复 OTA。 |

## 验证

- 生图响应回归先以 source-only 暂存恢复旧实现，7 项在旧代码失败；修复后位图链定向 57 项通过。计数与商汤边界复验 18 项为测试桩/边界验证，不是生产上游调用。
- breaker 最小复现：旧实现中过期 open 的真实 Redis 原子 claim 返回 false；修复后真实 Redis 并发双认领恰好一条成功，half-open 状态 TTL 长于 probe 锁，释放后续探针同样保持该不变量。合并回归 `124 passed, 570 deselected`，全套 `1671 passed`；`task_verify` 仍因配置范围只覆盖 weapp 返回 `unverified`，不能替代项目测试。
- 部署后的主节点真实 Redis、热备内存 store 冒烟均为 `claims=1`、state=half_open、enforce=false；没有调用任何生图上游。两节点 breaker/bitmap 文件带包 verify 与服务检查通过。preflight 主 25/34、备 26/34，失败仍是登记的 unit/drop-in/nginx/热备角色差异。
- S3 Build Boards run `38275272261` 对 `2b997d4` completed/success；宿主全套指定 x86_64 MinGW g++ 后 `366/366`。默认 Espressif riscv clang 的旧红发生在 `_Unwind_*` 链接边界，不是纸张产品回归。
- Hub 最终 `python scripts/agent_gate.py --profile hub` 在 `3dcff84` 通过：pytest `1671 passed`，`e2e_local` 输出 PASS；该门禁是本批 Hub 代码与文档的最终验证。
- fz `standard` 以要求的 x86_64 MinGW `CXX` 重跑后 `product_host` PASS；overall 仍为 fail，仅剩 Wokwi `ready_timeout`（24 行 UART，停在笔控初始化，无 panic/restart）与实验 QEMU `Unhandled debug exception/BREAK instr` 重启循环。两层输入的 bootloader、partition、app SHA 一致，app 即本次 OTA 候选 `f21b07d9432ead0220756294849c6c4ba773464752b8aac6377c2cc4f6fe235e`；因此只登记芯片启动层未验证，不据此改固件、ready oracle 或产品测试求绿。

## 发布状态与边界

- Hub 生图响应修复 `f28477a` 已双机部署；breaker 自愈修复 `8c843d9` 的 `breaker.py`/`bitmap_svg.py` 也已从提交 LF blob 双机 deploy+verify。breaker 包 SHA-256 主 `ec0abd9a25bada5110ea0004904b40adde7782f017a49fdc82c4346f9472c63e`、备 `b2f9d670ca52d7aeb145a62c11d6706477aa6fdbe911c9ba54e94012754c6c49`；回滚号主 `b86fd551af69454db360b2c3fb250133`、备 `9ad56ecf8ef842c58bf0f90b97eb008d`。生产保持 `HUTUJI_BREAKER_ENFORCE=0`，仅验证修复代码与状态机，不启用过滤。
- SenseNova 生产 env 仍为旧 `sensenova-u1-fast`；泄露 key 必须先由授权路径轮换，再切 `sensenova-u1.5-lite` 并执行付费 live draw。Agnes 2.5、Token Plan、ModelScope/TCB 充值均属用户付费决策，本批不代选。
- S3 CI 修复 `2b997d4` 已推 `feat/hutuji-usb-pipe` 与 `ci/p4-build-fix-20261012`，云端 run `38275272261` 成功；量产设备现役 S3 仍为 `hutuji.1.0.40`。
- 量产 Grbl `539e3f9b` 已完成 HTTP Web OTA、重启与 48 项参数不变复核；回退镜像 `grbl-rollback-8586cc04.bin` SHA-256 `2f07ed1a3d26ccb8859f4e48d8a8d0c24a4523e9e2e9bd1eab9bfb695143b3df` 保留。本轮不含运动/纸面测试。
- 纸面尺量、4小时/30次老化、工厂配对（当前 `factory_paired=false`）、真实 SN/操作员/测量照片、交付与法规签核仍缺证，实体放行保持 Hold。
