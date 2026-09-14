# 新增运维工具审查

用户要求另一批新增功能一并检查处理，并改善量产配置、稳定性和故障恢复。范围是 `scripts/auto_deploy.py`、`cross_repo_sync.py`、`protocol_version_check.py`，不改 Grbl 运动、换纸或 eFuse。修改清单见 `production_tools_review_20260914_authorized`。

## 已观察问题

- 部署工具读取可变工作树，却用 HEAD 标注版本；遗漏 TOML 和静态资源，未重启 Portal。共用远端临时目录、信任包内服务名/路径、备份失败被忽略、回滚选择不可靠，可能污染另一节点或掩盖失败。`curl -s` 可把 HTTP 404 当健康。
- 跨仓状态漏 staged 文件，部署计划用 basename 匹配完整路径，JSON 输出混入人类日志；缺仓与仅存在 `release.py` 不能算门禁或构建通过。
- 版本检查硬编码盘符、Grbl 与 S3 源码层级错误、把应用版本与板间协议混淆、报告对外仓 `relative_to()` 抛错；宽正则跨行把其他版本当作协议版本，时间戳固定。
- `configure/esp32/` 是下载工具的空白本机配置：无镜像、串口与 MAC 记录，`after=no_reset`，不是可直接交给产线的配方。不得根据这几份文件推断 S3 身份、NVS 保留或安全启动状态。
- 新 `.github/workflows/cross-repo-gate.yml` 与仓内无新增 CI 的规则冲突，当前保留未跟踪资产，不提交启用。

## 修复与验证要求

部署候选来自显式 commit 的 blob，保留完整相对路径和逐文件 SHA，文本 CR=0。包外固定节点决定服务，备机 bridge 必须 inactive；部署前验证候选、运行文件和备份，失败可按明确备份回滚，不能猜最新目录。队列格式升级仍必须先暂停入口、排空旧队列并停止旧 worker，不能混跑。真实 SSH 发布前先以本地受控文件系统验证异常路径。

状态工具读取完整 Git 状态，包括暂存、重命名和未跟踪文件；缺必需仓库或门禁失败返回非零。只记录实际执行的门禁，构建另取编译日志与产物哈希。版本工具只检查本仓存在的协议声明，不为让检查通过向固件新塞无依据常量；不存在的声明记未声明，不能冒充实现兼容。

最终测试、部署和产线/实机结论分别登记在 readiness 与 inventory。本文件目前是审查与实施记录，不是上线或量产放行证明。

## 已完成的候选（2026-09-14）

`auto_deploy.py` 从指定提交的 blob 构造发布包，覆盖允许的完整相对路径、TOML 与静态资源，校验文本 CR=0 和逐文件 SHA。包中的服务不能覆盖固定节点策略；备机恢复也不能引入 bridge/worker 或任意服务。备份记录保存被替换文件内容、mode/owner、原服务清单与实际 Redis URL 的 SHA，不存队列凭据。

安装先停入口，排空队列，再停旧 worker。队列检查用 AST 读取已安装 `draw_queue.py` 的字面量键名，兼容旧版缺 pending 索引，故障应用无法 import 时仍能恢复。失败执行 `rollback_files()`；文件恢复前后均核对，磁盘恢复失败记 `rollback_failed`，不启动混合文件版本，允许用同一备份重试。手动回滚在服务已停时只读 `/etc/hutuji/mcp.env` 并核对备份中的队列摘要，不执行 env 内容；没有原服务快照的旧备份拒绝自动猜测。SSH 清理阶段断连不覆盖已取得的远端执行结果。

`cross_repo_sync.py --gate-all` 明确执行 Portal pytest 与 fz standard（即使 Grbl 工作树干净），同时保留其他检查；缺仓或任何必需门禁失败返回非零。状态读取包含 staged/rename，JSON 输出不混入文字。`protocol_version_check.py` 只校对明确协议声明，未声明与失败分开，不用应用版本推测兼容性，也不自动修改固件。

验证：`mcp-server/tests/test_release_tools.py` **27 项通过**，包含真实隔离 Redis、路径/包边界、Git LF 对象、备份完整性、服务已停时回滚、磁盘恢复中断后续跑与断连清理。日志 `results/20260913-followup-review/release-tools-resume-20260914-144033-523584.log`；遗漏门禁的红例 `all-gates-coverage-red-20260914-143812-973111.log`，恢复接口的前置红例 `deployment-recovery-red-20260914-143317-181472.log`。后者证明旧候选没有恢复接口，不冒充完整运行时故障注入记录。

操作入口：`python scripts/auto_deploy.py --build --target aliyun --commit <完整提交>` 生成可审查包；`--deploy --target <节点> --package <包>` 执行该包；`--rollback --target <节点> --backup-id <本次备份ID>` 恢复指定备份。部署/回滚只在候选与目标核对后执行，结果另外登记。`configure/esp32/{utility,spi_download,security}.conf` 已逐份检查：未选择镜像/串口，MAC 保存关闭，结束方式 no_reset，安全启动/加密开关关闭；空配置不提交为量产烧录配方。
