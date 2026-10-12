# 三仓续审与技术发布（2026-10-12）

用户明确要求“继续 codereview 三仓，修复改进部署刷机，验证，推送，ci 变绿”。承接 R7 文档导入修复及上一批发布记录，保持量产 nopaper、运动参数、NVS 身份、引脚与 OTA 校验契约。

## 审查与修复

| 范围 | 证据与处置 |
|---|---|
| Hub R7 | UTF-16 编码绕过 ASCII DTD 预检；公共 XML 解析器在内部子集及正文前拒绝 DOCTYPE。R7 当时 MCP 1660 项、门户文档相关 16 项及 e2e 通过；本批生图回归加入后当前总数为 1669。完整根因与兼容性边界见三仓续审 R7。 |
| 生图质量与可用性 | 期望：已配置的对冲池至少有两条可用图像通道，返回图进入同一中心线描摹。实际：商汤旧模型 `sensenova-u1-fast` 已从聚合器移除而持续 404；魔搭因余额不足 429，TCB 上游 429；Agnes 免费层达到日额度后又受 1 分钟一次限制。最小复现显示 u1.5-lite 返回 `data[0].b64_json`，旧 `_extract_agnes_image_url` 在首个分歧边界只接受 URL。修复支持 URL 优先或严格 base64 内联图，商汤 75s 单次尝试、同轮零重试、专用 10MiB JSON 闸，解码图仍受 16MiB 上限；静态与运营后台动态 racer 共用。PNG→中心线→SVG→G-code、URL SSRF 闸、先到先得对冲和其余端点 64KiB 上限均不变。 |
| 画质样张 | 同题“小猫”本地真调用：u1.5-lite 生成稀疏线稿，中心线 32 条/3913 点、SVG 约 50.1KiB；Agnes 2.5-flash 8.3s 出图，中心线 20 条/571 点、SVG 约 7.2KiB。两者均可描摹，前者是立即恢复的国内备用通道，后者是否切默认及升级 Token Plan 涉及付费决策，本批不代选。 |
| S3 CI | 前一提交 `9752e0b` 已使所有产品板构建通过；剩余普通 P4 因板包 `esp32_p4_function_ev_board@5.2.3` 约束 `esp_video ~2.0` 与仓内产品锁 `2.3.*` 冲突，IDF5.5 P4 又命中 Espressif 已关闭的链接器问题。提交 `45a0149` 只在普通 IDF6 P4 的 CI 工作副本临时降为 `2.0.*`，不改仓内产品锁；兼容矩阵仅保留仍有文档依据的 xmini-c3，不再声称 P4 支持 IDF5.5。P4 板目录和 IDF6 构建覆盖保留。 |
| S3 运行路径 | 复查 OTA 主机/端口/板型路径、版本单调、SHA、预约与调度失败释放；本轮未新增确定性产品缺陷。`RunOtaCheck` 的 ReadAll 另有底层 8KiB 队列背压，不能只见一次性读取就宣称无界 OOM。 |
| 量产 Grbl | 当前候选 `539e3f9b` 含 JSON 空指针守卫及 pushError/WebSocket 生命周期修复。一次 OTA 期间设备进入“Web 存活、串口/Telnet/restart 均无响应”的半挂状态；该状态属于旧 WebSocket 锁死故障类，禁止再次发送 OTA 或探测 :81。后续刷写与 48 项设置复核必须等待用户物理断电重启。 |

## 验证

- 生图回归先以 source-only 暂存恢复旧实现，7 项在旧代码失败；修复后位图链定向 57 项通过。计数与商汤边界复验 18 项通过。
- 当前枢纽全套 `1669 passed`，`scripts/e2e_local.py` 输出 `PASS`。动态 SenseNova racer 独立冒烟返回内联 PNG 字节且未发下载 GET。
- S3 workflow 策略断言修前显示 `regular_p4_override=False / compat_idf5_has_p4=True` 并失败，修改后反转为期望值；YAML 解析通过，`VersionTests` 2/2 通过。宿主全套 360 项另有 38 fail/40 error，集中在本机 ESP clang 链接与既有夹具，不计为本次 workflow 通过，也未为其改产品代码。
- `task_verify` 未运行配置检查，返回 `unverified: partial-project-coverage`；不能以此代替上述项目门禁。

## 发布状态与边界

- Hub 生图修复尚待从明确提交 LF blob 双机部署；生产 env 只计划把商汤模型名改为 `sensenova-u1.5-lite`。本批不切 Agnes 2.5、不升级付费计划、不充值魔搭/TCB，也不启用 breaker enforce。
- S3 CI 修复 `45a0149` 已推 `feat/hutuji-usb-pipe`，并推 `ci/p4-build-fix-20261012` 触发云端 Build Boards；最终绿灯仍以该 run 结果为准。
- Grbl 刷机和发布后 48 项回归阻塞于物理断电重启；当前不可执行，不得写成已刷。
- 本机证据在 `results/local-only/2026-10-12-image-quality/` 与 `results/local-only/2026-10-12-utf16-review/`；临时脚本在 `mcp-server/tmp/`、`tmp/review-utf16-20261012/`，均不入发布提交。
- 纸面尺量、4小时/30次老化、工厂配对和交付签核仍缺证，实体放行保持 Hold。
