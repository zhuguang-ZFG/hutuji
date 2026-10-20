# 三仓提交推送与续审（2026-10-20）

承接 `three-repo-review-20261020.md` 的七项修复。用户本轮要求“提交推送，继续code review，ci变绿”。本轮续审只读产品源码，新增发现与上一批七项分开登记；没有部署、刷机或更改量产运动/换纸行为。

## 提交与 CI 范围

| 仓 | 产品提交 | 推送分支 |
|---|---|---|
| Hub | `4437dad` | `main` |
| S3 | `34dc2ca` | `feat/hutuji-usb-pipe`；另有无源码变化的 CI 触发提交 `46aa527` |
| 量产 Grbl | `99d14a13` | `massprod/nopaper-1.3a` |

Hub CI 外仓快照更新为 S3 `34dc2ca41ce5ff2312cf761217f31d52a93d2280` 与量产 Grbl `99d14a13f42ef2de01fe804b099702cec4e134b8`，防止测试本批时仍引用旧固件树。普通 Grbl 的既有参考分支不变。

运行入口：[Hub产品提交 CI](https://github.com/zhuguang-ZFG/hutuji/actions/runs/39423472340)、[量产Grbl CI](https://github.com/zhuguang-ZFG/Grbl_Esp32/actions/runs/39423038484)、[S3交付板 CI](https://github.com/zhuguang-ZFG/xiaozhi-esp32/actions/runs/39424801925)、[S3 PR矩阵](https://github.com/zhuguang-ZFG/xiaozhi-esp32/actions/runs/39424809566)。本地最终快照存 `results/local-only/2026-10-20-followup/ci-final.json`，每项须绑定实际提交与作业结论，不引用旧批绿色状态代替。

S3 首次推送 TLS EOF，重试成功；但新提交没有 Actions 检查。权限 API 显示 `enabled=true`，工作流显示 active，dispatch 却回 422 “Actions has been disabled for this repository”。重申启用没有解决；用户随后在 GitHub 页面明确回复“已启用”。增加空提交后，分支 Hutuji S3 与现有 PR 的 Build Boards 两套检查均触发。没有放宽工作流令牌权限、allowed_actions、测试断言或构建矩阵。

## R20F-01 / P2：台账快照使删除中的作品被恢复

位置：`draw-portal/draw_portal/telegram_archive.py` 的 `restore_user` / `_restore_one`，`draw-portal/draw_portal/store.py` 的 `restore_saved_preview`。

`restore_user` 先取整份未删除台账快照；逐项下载/回写时不再确认 `tg_removed_at`。若用户在快照之后删除作品，后续 `_restore_one` 查到行已不存在，就把它当成“被自动清理但允许召回”的记录。异步频道撤回尚未完成或失败、远端文件仍可读取时，`INSERT OR REPLACE` 会重新建立用户刚删除的图库行。

确定性本地探针使用真实 Store 和恢复函数：获取快照 → 删除行并标记台账 removed → 恢复继续，得到 `recreated=1`、图库行与文件重新存在，而有效台账查询仍为空。网络下载是内存替身，未访问真实第三方服务。此问题在后台人工执行恢复 CLI 与用户删图重叠时触发；不是 CR20-06 的上传校验失败分支。

建议在恢复落盘/建行事务中再次核对台账归属与删除标记，并使用户删除与恢复提交有明确的先后关系；单纯在下载前检查一次仍有竞态。回归须覆盖下载前、下载中和提交前删除，以及允许的自动清理后恢复。

状态：**待修复**。

## R20F-02 / P2：会话批次把后续事件归到第一个 session

位置：S3 `main/boards/lichuang-dev/hutuji_conversation_report.cc` 的 `PopBatch` / `PostBatch`；Portal `/api/device/conversation/report`。

每个 `PendingTurn` 保存自己的 session_id，但 `PopBatch` 只按数量取前8项，不按会话分组。`PostBatch` 只在根对象写 `turns[0].session_id`，事件本身不含会话字段。上游 MQTT/WebSocket 新 hello 可以更新会话号，旧事件又会在非 Idle 时留队，因此恢复空闲后的队列可能同时含 A、B 两个会话；B 事件将按 A 入库并参与错误的会话去重/检索。

宿主编译真实 `PopBatch`，构造两条不同 session 的队列，输出 `2 session-A session-B`；随后源码核对根 JSON 仅使用第一项 session。仅证明批次混装与序列化契约不匹配，未在设备上触发换会话，也不把它扩大为跨账号泄露结论。UTF-8 修复 CR20-07 不解决此类归属错误。

建议批次只消费连续同 session 事件，或在协议中显式携带每事件会话号并在接收端使用它；保持队列8项预算与既定失败即丢策略。回归涵盖 A→B、A→B→A、队列回绕和一次消费后的剩余项。

状态：**待修复**。

## R20F-03 / P3：Grbl 查询忙态守卫恒为 false

位置：量产 `Grbl_Esp32/src/Settings.cpp` 的 `notCycleOrHold`，`ProcessSettings.cpp` 的六个查询命令注册点，`GrblCommand::action`。

守卫写成 `sys.state == State::Cycle && sys.state == State::Hold`，同一枚举状态不可能同时等于两个不同值，故永远不拒绝。`$$`、`$+`、`$L`、`$S`、`$SC`、`$CMD` 依赖此函数，而 `GrblCommand::action` 仅在返回 true 时回 IdleError。

编译真实守卫的宿主探针对 Idle/Cycle/Hold 输出 `0 0 0`。目前确认的是查询状态限制失效；未证明它导致运动停顿、串口配对损坏或控制器重启，故列 P3，不与 CR20-01 的空指针崩溃混报。

建议在独立授权的设置命令状态守卫修复中校正谓词，并编译实际 action 路径验证 Cycle/Hold 拒绝、Idle正常读取；不改变运动参数或为仿真调整机械常量。

状态：**待修复**。

## 证据与边界

- 探针 `tmp/cr20-push/review_followup.py`；实测 exit 0，三个判别断言均命中。
- 本机证据 `results/local-only/2026-10-20-followup/probe-results.json` 与 `probe-*` 目录中的隔离 SQLite、图片占位文件、编译的宿主探针；不含真实用户数据或凭据。
- 本次新增三项没有改产品代码。此前七项继续以已提交修复为准，不将“新问题待修复”写成“七项尚未修复”。
- 云端 CI 不运行 fz 的 Wokwi/QEMU 启动层；GitHub检查通过不能消除本地 fz overall=fail，也不证明纸面/老化/配对/交付放行。NOT_FOR_RELEASE 与实体 Hold 不变。
