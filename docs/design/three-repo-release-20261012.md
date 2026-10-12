# 三仓续审与技术发布（2026-10-12）

用户明确要求“继续 codereview 三仓，修复改进部署刷机，验证，推送，ci 变绿”。承接 R7 文档导入修复及上一批发布记录，保持量产 nopaper、运动参数、NVS 身份、引脚与 OTA 校验契约。

## 审查与修复

| 范围 | 证据与处置 |
|---|---|
| Hub | UTF-16 编码绕过 ASCII DTD 预检；公共 XML 解析器在内部子集及正文前拒绝 DOCTYPE。修前六项失败，修后文档解析 49 项、门户文档相关 16 项、MCP 1660 项及 e2e 通过，hub 门禁通过。完整根因与兼容性边界见三仓续审 R7。 |
| S3 CI | `Build Boards` 的 atom-echos3r、IDF5 xmini-c3、IDF5 P4 失败日志均明确报 `espressif/cjson` 缺少 `.component_hash` 或 `CHECKSUMS.json`，尚未进入产品编译。CI 把宿主用的裸 cJSON 检出也带进固件任务，导致组件管理器拒绝。修复为仅宿主测试保留该检出，固件和兼容任务让 IDF 下载完整组件；保留全部板型和测试矩阵。已推 `9752e0b`，交付与通用工作流正在验证。此前把这些失败笼统归为“无关板型/兼容矩阵”的解释不再作为本问题结论。 |
| S3 运行路径 | 复查 OTA 主机/端口/板型路径、版本单调、SHA、预约与调度失败释放；本轮未新增确定性产品缺陷。`RunOtaCheck` 的 ReadAll 另有底层 8KiB 队列背压，不能只见一次性读取就宣称无界 OOM。源码最近差异为 CI 与测试移植，不凭此新增固件版本。 |
| 量产 Grbl | 从上次登记板上 `8586cc04` 到当前 `539e3f9b` 的产品差异是 `2a7350c9`：JSON 空指针守卫及 pushError 与 WebSocket 析构共锁。复核补丁与当前 Serial2Socket 分块写/同锁生命周期，69 项宿主及当前 DIO 构建通过；准备将包含两项修复的当前固件升级到固定量产板。 |

## 验证与发布状态

- 当前本机证据目录：`results/local-only/2026-10-12-utf16-review/`；临时执行脚本：`tmp/review-utf16-20261012/`。首轮门户测试因错误工作目录不能导入包，首轮 hub 因计数/行号漂移失败，均保留，不计成功。
- S3 只读实机核验：MAC `b8:1f:3f:c4:58:2c`、Freenove、`hutuji.1.0.40`，aborted/Grbl Idle、XYZ 原点，connected/ready/authorized/grbl_settings_ok 均为 true；`factory_paired=false`。
- Hub 双机部署、Grbl 刷机、发布后回归、GitHub 最终提交检查尚在执行；不能把上述本地通过写成已经部署或已经刷机。
- 纸面尺量、4小时/30次老化、工厂配对和交付签核仍缺证，实体放行保持 Hold。
