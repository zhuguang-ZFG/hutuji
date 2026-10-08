# 2026-10-08 fz 验证漏洞修复

用户在剩余问题清单后要求「继续」，并明确「先完成软件修复」。本批限位置变化断言、trace数量检查和金样来源匹配，零真机运动。基线fz `d176b09`、hub `1ffe27e`，受跟踪文件起点无改动；保留既有未跟踪资产。

## 执行契约

1. `expect_mpos_delta`为三个有限有符号毫米数，比较结束位置减起始位置，显式零向量仍检查。`eps_mm`默认0.6且必须有限非负。整例基准取setup完成后、inject/steps前；步级基准取该send前。两端都要求同一完整Idle帧中的有限XYZ，不用wait_idle可能返回的陈旧坐标。步级断言仅允许send步骤，其他形态明确失败，不静默跳过；携带断言的async步骤也等运动完成后检查。
2. trace必须是逐项对象列表，长度等于expect及输入序列；少行、多行、坏形状返回结构化失败。结构错误不交给只针对字段差异的缩减器，避免IndexError或生成错误金样。
3. 金样来源按非空case名称或文件stem精确匹配，拒绝子串/空名称兜底；多候选明确报歧义且不录制。正常精确匹配仍保留原setup。CLI的--only筛选用途保持，不把筛选规则当身份选择规则。

新增针对性失败回归，再修实现；已有移动用例增加位置断言以经真实host模拟器执行。金样测试改用自己的report夹具，避免依赖上次门禁残留。必跑fz standard并读observe；已登记Wokwi启动限制保持，不修改产品固件、不开宽泛豁免换绿。

## 验证

新增35项回归已通过（位置差15、trace结构9、金样匹配11）；与旧金样/allowlist八项合跑共43项通过，其中依赖last_report的用例已改为自带报告夹具。RED日志保留旧实现对漏行、方向错误、空名称及歧义来源的失败结果。金样最外层也拒绝缺失/空白/非字符串报告名称，不再先替换为case绕过精确匹配。

真实PC模拟器验证：`hardware_sim/run_hw_sim.py --start-sim --json-only --only json_move_x10_step_window`退出0，步级与整例均记录expected/actual为`[10,0,0]`，结束MPos为`[10,0,0]`。原StepOracle检查同时保留通过。该次run_id为`20261008T073053Z-8052-7fb2e4`，仅host模拟器，无真机运动。

完整standard及observe结果见末节；使用显式`GRBL_ROOT=D:/Users/Grbl_Esp32-massprod`，不移植换纸模块或改变Wokwi豁免。源码改动不代表Wokwi、射频、板上运动或出货验证。本机证据集中于`results/local-only/2026-10-08-fz-validation/`，暂存及复现脚本在`tmp/fz-validation-20261008/`。

第一轮standard为26 pass/8 skip/1 fail，仅Wokwi启动超时；observe的block_done_claim=true。`sim_rerun --from-last`返回3（无协议/运动失败可选），随后完整复跑。第二轮standard为25 pass/8 skip/2 fail，增加一次`json_feed_hold_tcp`在3s内未观察到Hold；未改参数或放宽超时。该用例没有新增位置断言，其send/inject/status实现本批未改；目前只登记间歇失败，不据此判断产品固件有新问题。按observe执行`sim_rerun --from-last`，该单项PC模拟器复跑退出0，run_id=`20261008T074621Z-13724-84f373`。第三轮完整standard结果待结束后回填。

## 验证期间发现的配额报告误判

第三轮协议/运动全部通过，Wokwi返回真实`quota`（串口0行、云端启动前资源不可用），runner按既有规则给出blocking=false，因此gate overall=pass；但`agent_observe._wokwi_startup_findings`只列unauthorized/transport，漏quota，反而把派生的ready_timeout升级为固件启动失败，block_done_claim=true。此为验证链中新确认的软件遗漏，追加修复observe分类，不改变runner的阻断判据。

新增3项回归先2败，修复后observe全套19项通过；测试从runner的PRE_START_CLOUD_ERRORS读取分类，避免两端再漂移，并验证blocking=true/缺失、未知timeout仍为hard。启动前配额不可用只记soft且明确startup not verified，不冒充Wokwi通过；前两轮真实启动超时仍保留hard。第四轮最终源码standard结果待回填。

## 最终结果（2026-10-08 15:59）

- 最终源码的standard退出0、overall=pass，run_id=`ae74a6659b71483cab2e3d756794f497`；26层pass、8层按nopaper/profile不适用skip，Wokwi一层fail但blocking=false（quota、串口0行）。QEMU、协议、19个运动/日志用例、产品host、7/7引脚和6/6缺陷注入检查通过。
- 同轮observe：hard=0、soft=2、`agent_should_block_done_claim=false`。两条soft均明确云启动不可用，不能声称Wokwi/射频已验证；以前真实启动超时的限制未解除。
- 本批新增38项回归，加既有金样/allowlist与observe回归合计62项专项测试通过；暂停用例第二轮的单次失败、定向复跑及第三/四轮完整通过均保留，不伪装成从未失败。
- fz改动共11个明确文件（实现/测试/规范），hub仅文档/白名单与行号同步；未提交，没有部署、烧录或真机运动。用户指定的“先完成软件修复”范围保持。

## 提交推送与下载核验续作

用户追加「提交推送部署下载验证」，随后要求继续。以下结果覆盖前述未提交状态；本批仅host验证器改动，没有新的产品固件或云端服务载荷。

- fz已提交并推送`a5fc3883250c340a7c212011992851bccc7dc948`至`origin/main`。SSH连接被关闭后改用同仓HTTPS推送成功，未修改remote配置。提交前11文件原始SHA-256与最终门禁的`source-sha256.json`全部相符；沿用已通过的62项专项回归与standard证据，不把提交后的HEAD冒记为原门禁身份。
- 从GitHub独立浅克隆`main`至本机暂存目录，远端HEAD等于上述提交，11份下载Git blob与本地提交逐字节相同，并与工作文件按LF归一后相同。记录为`release-source-verification.json`与`download-verification.json`。
- 公网重新完整下载1.0.33固件，3080192B，SHA-256 `a9979eb204504c910192e6cd53bfe1303281b6db6649551af4efc869a384022a`，与已发布镜像、`latest.json`、`catalog.json`一致。正确目录接口是`/firmware/catalog.json`；错误的`/firmware/s3/catalog.json`返回404不代表发布目录故障。现役1.0.33镜像及推荐指针没有改动。
- 阿里主节点与京东热备各自核验`tool_result.py`、`device_messaging.py`、`mcp_result.py`、`firmware_ota.py`：实际文件SHA与已部署提交`859a46f`一致、CR=0；共享判错入口与嵌套拒绝/成功应答导入冒烟通过。未重复发布或重启服务。
- 量产MAC `b8:1f:3f:c4:58:2c`只读复查为Freenove/1.0.33/nopaper/idle，Grbl为Idle，connected/ready/authorized/grbl_settings_ok均true，OTA空闲。本轮没有OTA下发、机械动作或纸面验收。

上述新增证据仍保存于本批本机证据目录：`recheck-latest.json`、`recheck-catalog.json`、`recheck-xiaozhi-1.0.33.bin`、双机`*-smoke.log`、`recheck-device.json`及脱敏状态日志。枢纽台账随本次文档提交收口；原fz未跟踪资产及普通Grbl既有`.gitignore`保留。Wokwi启动仍未验证。
