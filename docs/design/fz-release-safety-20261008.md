# 2026-10-08 发布签核与门禁报告修复

用户在七项问题清单后明确「同意完全修复」。本批基线为fz `a5fc388`、hub `884b269`。范围限发布签核、门禁报告生命周期、硬件用例跳过统计及对应测试；不改Grbl/S3/云端产品代码，不执行真机运动。原fz两个未跟踪文本资产与普通Grbl的既有修改保留。

## 执行契约

1. 签核必须检查当前工作树（含暂存和未跟踪文件），不能只看HEAD及门禁时的dirty。显式签核时身份缺失、未知或脏树均阻断；开发检查保留有说明的降级。声明的源码SHA必须非空且与报告一致。
2. 显式scope缺失、空白、结构错误、错误类型或未知布尔值均阻断，不静默取消HIL。YAML使用安全解析器，缺解析依赖时明确失败；JSON可无额外依赖读取。`--allow-pending-hil`只能放行明确的待验状态，不能放宽scope/身份错误。
3. 复用G3/G4的必测项集合与结构化验证。签核另要求操作者、日期、版本、固件SHA等身份以及必测项的非空可读取证据文件；空文件、模板、示例、失败/跳过项和缺失日志不能作为已验。结果报告保存逐项原因。
4. Wokwi非阻断失败/跳过必须传递为“启动未验证”警告，原始层仍保留fail/skip；阻断失败不能因overall被写成pass而遗漏。
5. 门禁在风险操作前发布本轮running状态，异常/中断留下本轮失败报告，进程强停至少留下非pass。报告通过临时文件原子替换；共享输出目录互斥，第二个进程不能破坏活动报告。运行标识贯穿开始与完成。
6. 硬件用例新增显式skipped字段，跳过时passed为null；生产/JSON转换、重复运行、退出码、observe汇总与失败复跑消费者保持一致，旧报告按明确的skip标记兼容。passed/failed/skipped分别计数。
7. GRBL_ROOT缺席时product_profile/product_host均明确记录skip与原因；机型未知仍fail，不借缺文件豁免产品验证。

## 验证计划

先用隔离夹具复现签核四项、门禁异常和跳过统计；修复后覆盖合法签核、非法输入、脏树/身份读取失败、云启动警告、原子写失败、运行互斥、异常/中断、缺仓以及旧报告兼容。执行相关G3/G4、签核、报告、观察、硬件测试和真实PC快速用例，再串行执行量产nopaper `standard`，阅读observe；最后跑hub文档门禁。所有结果按实际输出回填，不冒充硬件验收。

本机证据目录为`results/local-only/2026-10-08-fz-release-safety/`，暂存补丁为`tmp/fz-release-safety-20261008/`，两者仅本机。修改清单共15文件，安装前核对目标SHA，未覆盖既有未跟踪资产。

## 实现

`release_honesty.py`将当前Git状态、scope结构、G3/G4结果及云启动未验信息纳入最终裁决，任何blocker统一判blocked。新增`release_inputs.py`安全解析JSON/YAML并拒绝重复键；签核检查必测项、操作者/日期/源码SHA及非空证据文件，模板和示例不再有通过特权。`g3_evidence.py`/`g4_ota.py`改用同一安全入口，原必测项集合保留。YAML依赖写入`requirements-validation.txt`，测试环境已装PyYAML 6.0.3。

`gate_report.py`提供内核文件锁、原子JSON写入和运行状态记录。`agent_gate.py`在读Git和导入产品模块前进入本轮running状态，异常/中断留下同run_id失败，强停释放锁且保留非pass。无Grbl时明确发布product_profile/product_host两层skip。`run_hw_sim.py`共用原子写入，并在重复运行缺报告时判失败。

`case_runner.py`和`run_hw_sim.py`为跳过返回`skipped=true/ passed=null`；`sim_common/case_result.py`统一新旧报告的三态判定和计数。`agent_observe.py`显示通过/失败/跳过各自数量，running报告阻止完成声明，已跳过层的旧硬件失败保持历史提示。

## 回归过程

新增32项测试：签核17、报告生命周期8、跳过及观察7；初始RED覆盖旧签核放行、早期异常残留旧pass及跳过返回true。补充真实临时Git仓的未暂存/暂存/未跟踪三态，以及子进程强停后的非pass和锁回收。与原G3/G4、签核、observe、硬件和MPos回归合计83项通过；旧QEMU/芯片报告判据11项通过，共94项专项测试。末次另补非空列表形式的损坏身份回归：先复现AttributeError，再改为结构化阻断，83项重跑通过。

真实PC快速用例`json_feed_hold_tcp`在time_factor=0下显示SKIP，连同session元检查为0通过/0失败/2跳过；没有把未执行的暂停验证计成通过，也没有真机运动。结果见`fast-case-report.json`与`fast-case.log`。

首轮standard退出0（159.37s，26 pass/8 skip/Wokwi非阻断quota一项），observe hard=0；19个运动/日志用例全通过，QEMU、产品host、7/7引脚与6/6注入通过。本轮Wokwi串口零行、配额不足，仍未验证。真实签核CLI读取该报告后给出ready_for_dev，同时明确列出Wokwi未验和当前fz脏树警告；不能用作正式签核。第一轮报告与日志保留为`gate-first.json`、`standard-first.log`。最终源码的完整门禁复核结果在下节回填。

## 最终结果

最终源码standard退出0、overall=pass，run_id=`48941cbaf56f414286375113c6deaebf`，耗时167.92s。26层pass、8层按SKU/profile跳过，Wokwi为非阻断quota（串口0行）；observe hard=0/soft=2、block_done_claim=false，硬件计数19 passed/0 failed/0 skipped。最终源码15文件SHA已冻结，94项专项回归通过，compileall与diff空白检查通过。

本轮未提交、未推送或部署，未触发OTA及真机运动。正式签核仍须提交后的匹配门禁与有效实机证据；开发模式的ready_for_dev和Wokwi非阻断不构成出货或硬件验收。枢纽文档门禁结果随最终日志归档。

## 提交与部署验证续作

用户追加「提交推送部署验证」。本节覆盖上述未提交/未推送状态，提交前报告继续作为历史证据保留。

- fz提交`e36c1259994d821ed4a7f2d9aead309849b54822`已推送main；15文件与上轮通过的源码SHA一致。独立从GitHub浅克隆后核对HEAD及15份Git blob，并与本机工作文件按LF归一逐字节比对通过。本机`D:/Users/zhugu/fz`已应用并运行新版本，未新增云端服务或固件载荷。
- 提交后的量产nopaper standard再次通过：run_id=`d67b0314c83040c2aa8c69a016d1c7b0`、耗时160.57s，报告fz SHA等于本次提交、受跟踪工作树dirty=false。26 pass/8 skip/Wokwi非阻断quota一项，observe hard=0、block_done_claim=false，19项运动/日志通过。原fz两份未跟踪文本仍保留；正式签核的当前工作树检查会如实提示它们，不删除资产绕过检查。
- 双机现役4服务文件SHA/LF与已部署`859a46f`一致，真实导入冒烟通过。按`auto_deploy.py`实际unit名核验：阿里`hutuji-mcp`/`hutuji-draw-portal`/`hutuji-cloud-bridge`及8个`hutuji-draw-worker@`均active；京东mcp/portal active、bridge inactive。最初检查漏写hutuji前缀造成inactive假象，已按实际名称复核，没有重启或修改服务。
- 公网健康接口返回status=ok；1.0.33固件重新完整下载3080192B，SHA-256 `a9979eb204504c910192e6cd53bfe1303281b6db6649551af4efc869a384022a`，与latest/catalog一致。固定量产MAC `b8:1f:3f:c4:58:2c`只读复查为Freenove/1.0.33/nopaper/idle，Grbl Idle、connected/ready/authorized/grbl_settings_ok全true。未重新OTA、未跑纸面运动。

发布证据在本批`release/`子目录；远端下载哈希另存父目录`fz-release-download.json`，枢纽下载记录随提交后生成。Wokwi启动、无线与纸面仍未验证；本次完成的是软件发布及部署状态核验。
