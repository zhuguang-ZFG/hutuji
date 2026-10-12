# 配额修复与三仓 CI / 发布（2026-10-11）

用户明确要求“修复改进优化部署刷机，推送github，ci变绿三仓”。范围为已证实的 Portal 配额并发问题、三仓实际交付分支自动验证、通过后的精确提交推送与主备部署、固定量产双板刷新/控制复验。NR12-GRBL-01 已证伪，不因此修改固件。运动、换纸、GPIO、机设与 NVS 身份保持原契约。

## 实施清单

- Portal `store.py` / 新增 `quota.py` / `app.py` / 对应 tests：SQLite `BEGIN IMMEDIATE` 原子预占，网页与语音生图共享预算，聊天与小派设置各自预算；成功事件与预占结算同事务，失败释放。滚动24小时成功计数保持不变。
- 请求取消后后台操作仍运行时，预占保留到操作真正结束；进程退出等无法判断结果的在途项保守保留最多24小时，不自动重试付费请求。此项不计成功 usage，但临时占用剩余额度。
- 三仓 GitHub Actions：hub运行MCP/Portal/产线独立测试；量产Grbl运行宿主回归与DIO构建；S3交付分支运行宿主回归及Freenove/立创构建。保留已有上游工作流，不借跳过失败或改换纸行为做绿。
- 通过后点名提交推送、核GitHub对应提交检查、主备Portal精确LF/SHA部署；固件使用已测候选与当前身份/空闲闸，已有同版本落板优先核验，不把CI配置变更冒充新固件行为。

## 当前状态

## 2026-10-12 收口

Hub 配额修复已提交 `8d2525b` 并推送 `origin/main`；Hub Tests run `38213965954` 为 success。Portal 从该提交打包部署到京东云热备和阿里云主节点，逐文件哈希与服务检查均通过；回退备份号分别为 `d901af36b9e2475492d459e1ffa890b2`（jdcloud）和 `bc38ebc55a3b4f118135c121f34ec626`（aliyun）。

S3 交付工作流修复提交 `2ef6b9f`、`39da6bf` 已推送 `feat/hutuji-usb-pipe`；专用 Hutuji S3 run `38215880648` 为 success，宿主测试及 Freenove、立创两板构建均通过。量产 Grbl 交付分支保持 `539e3f9b` 的绿色 Hutuji Mass Production run `38136408594`；固定设备此前已刷入 `8586cc04`，当前只读核验为 S3 `hutuji.1.0.40`、Grbl `1.3a.20260910`、48 项参数不变、Idle 原点。因 S3 提交只改 CI，不生成新的产品固件，未重复刷同版 OTA。

S3 仓库原有覆盖全部历史板型的 `Build Boards` 工作流仍会因无关板型和兼容矩阵失败；本批交付判据采用专用 Hutuji S3 工作流，不以跳过这些板型换取绿色。

已确认三仓真实交付分支原先没有覆盖push的Actions：hub无工作流，S3现有build仅匹配main/ci分支（现存PR #1另触发main的多板检查），Grbl近期绿色是另一分支。不能以这些历史绿色代替本次验证。

配额已实现，Portal 574项/282.79秒、产线78项/13.74秒通过；新增7项覆盖跨进程、真实认证路由并发、取消期间预占及结算失败保守保留。原产线首次77过/1失败为未指定IDF Python，记录`production.xml`保留，修正环境后的`production-idf.xml`为本轮结果。S3 unittest宿主366项、量产Grbl69项通过；S3 pytest入口云端仍在修复Linux编译器调用及cJSON依赖。

Grbl `539e3f9b` 已推送，GitHub run `38136408594` 宿主与DIO构建成功。S3 `8cccaa8` 已推送，交付构建及旧PR检查尚未全绿。三个宿主测试将固定Windows头文件路径改成检出路径，测试断言不变。证据在`results/local-only/2026-10-11-quota-ci-release/`，尚未部署或刷机。
