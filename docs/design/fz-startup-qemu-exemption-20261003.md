# fz 启动层收口：NVS radio-off 夹具 + QEMU 首轮 Guru 窄豁免（2026-10-03）

> 用户拍板落地形态 **B（QEMU 窄豁免）**（三选项：A 全阻断登记 / B 窄豁免 / C Config.h 仿真宏）。
> 本批零 Grbl 产品代码改动；全部改动在 fz 仓（仿真基础设施）。Wokwi 层按拍板保持阻断。


> **证据边界**：本批全部结论来自 PC 仿真与源码符号化，**未做任何实机验证**；「QEMU Guru / Wokwi 楔死属仿真保真缺口而非固件缺陷」是推断（依据：指纹与旧基线同款、符号化落在仿真弱项路径、历史板上启动记录无同类签名），如需定论须补真机对照。
## 1. 根因链（最终版，supersede 早前单因表述）

fz `wokwi_startup` + `qemu_startup` 长期 fail 是**三个互相独立的仿真保真缺口**叠加，不是一个 bug：

1. **BT 控制器时钟源不可仿真**（QEMU 主因）：fix 分支默认射频=蓝牙（Config.h 的 `DEFAULT_RADIO_MODE=ESP_BT`，因 `ENABLE_WIFI`+`ENABLE_BLUETOOTH` 同定义且未设 `CONNECT_TO_SSID`），启动即 `esp_bt_controller_init` 在 `bt.c` 断言（`select_src_ret && set_div_ret`），abort → SW_CPU_RESET crash-loop。massprod 分支编译期禁 BT（实机 `[MSG:No BT]`）故不崩。
2. **QEMU 多核 IPC 仿真缺口**（第二层）：每次 qemu 启动的首轮 POWERON，应用入口 `ipc_task → xQueueGenericReceive → esp_crosscore_int_send_yield → vTaskExitCritical` 触发一次性 `Guru Meditation / BREAK instr`（PC 符号化 = `_DoubleExceptionVector`，xtensa_vectors.S），SW_CPU_RESET 后 boot2 干净。该指纹在默认空 NVS 旧日志同款存在（既有基线），与 NVS 内容、首启写回均无因果（零写回轮仍复现）；本批未做实机对照，「仿真缺口」定性为推断（依据见文首证据边界）。
3. **Wokwi WiFi 初始化段保真缺口**（Wokwi 主因）：即使射频全关（radio-off NVS），wokwi 仍在 `[MSG:No spindle]` 之后（下一步是 `wifi_config.begin()` 内调用链）楔死，boot1 另伴 TG0WDT；30s 观察窗无 ready。要绕过须跳过 WiFi 初始化代码段 = 产品构建配置改动（拍板不做）。

## 2. 判别实验（2026-10-03，证据 `fz/results/local-only/2026-10-03-startup-radio-off/`）

方法：NVS v1 镜像（IDF v6.0.1 `nvs_partition_gen`）写入合法产品运行时配置
namespace `Grbl_ESP32` / key `Radio/Mode` = 0（ESP_RADIO_OFF），注入整片 0x9000
（分区表实测 nvs@0x9000/0x5000）。`BTConfig::begin()` 按 `wifi_radio_mode==ESP_BT`
门控、`WiFiConfig::begin()` 对 mode 0 走 `WiFi.mode(WIFI_OFF)`，故零产品改动即可跳过全部射频初始化。

| 实验 | 结果 |
|---|---|
| QEMU + radio-off（3 轮一致） | BT 断言消失；boot2 完整到 `Grbl 1.3a ['$' for help]` + `[MSG:No Wifi]`/`[MSG:No BT]`，`$I`/`$$` 真应答；残留=首轮一次性 Guru（缺口 2），oracle fail |
| QEMU 同镜像复跑/预提交 NVS 复跑 | 零写回仍首轮 Guru → 与写回/内容无因果，逐 qemu 启动确定性一次 |
| 首启写回 diff | 唯一写回扇区 0x9000；写入=Grbl 坐标系默认值（G54-G59/G28/G30），与 Guru 无因果 |
| Wokwi + radio-off（1 轮，云） | boot1 TG0WDT、boot2 停 `[MSG:No spindle]`，无 ready → 缺口 3，radio-off 治不了 |
| 默认 BT 镜像（对照，既有报告） | QEMU crash-loop（bt.c 断言）/ Wokwi 停同一点，均 fail |

测量教训：smoke 退出码经管道（`| tail`）会丢——两轮「EXIT=0」实为 tail 的退出码，真实 oracle 均 fail；后续所有验证均不管道取码。

## 3. 落地形态（fz 仓，全部显式 opt-in）

- `chip_sim/fixtures/nvs_radio_off.{csv,bin}`：钉死夹具（bin SHA-256 `6475c964…27e6`，0x5000 字节），`chip_sim/radio_off_fixture.py` 钉身份常量与限制声明；`TestRadioOffFixtureIntegrity` 钉死「换夹具必须连常量一起改」。
- `chip_sim/build_flash_image.py --nvs-fixture PATH`：显式 opt-in 注入；NVS 偏移从分区表二进制解析（fail-closed，不猜 0x9000）；sidecar 记录 offset/bytes/SHA/limits，**仅当夹具 SHA == 钉死常量才标 `radio_off: true`**（任意自定义 NVS 照常注入但不开豁免）。不传参 = 原始擦除态，旧调用语义不变。
- `chip_sim/startup_log_oracle.py`：`qemu_ipc_guru_exemption` 参数（默认 False）。判据**全部**成立才把首轮 Guru 记为仿真 artifact（`artifact_exemptions` 落报告）：单行同事件（guru+panic 同行）∧ 事件行在首个 `[MSG:` 前 ∧ `boot_count == 2` ∧ 无 restart_loop ∧ 无其它 fatal ∧ **强 ready**（`Grbl … ['$' for help]` 提示符行，弱标记 "Grbl" 会被横幅骗过，已钉回归）。
- `chip_sim/run_qemu_smoke.py`：`radio_off_fixture_ok()` 三重绑定——sidecar 声明 + SHA==钉死常量 + flash 分区区域实测 SHA 一致；**在跑 QEMU 前判定**（guest 首启会写回 NVS）。报告增 `sim_radio_off_fixture` / `artifact_exemptions`，`panic_exemptions_applied` 由豁免记录驱动。Wokwi 侧不传豁免，零容忍不变。
- `scripts/agent_gate.py`：QEMU/Wokwi 两处 build 调用显式传 `--nvs-fixture`（统一「仿真射频全关」配置；Wokwi 仍按其自身缺口判 fail）。

## 4. 验证矩阵

- 新增回归：oracle 豁免矩阵 10 项（合法形态过；横幅后 Guru/两次 Guru/无 ready/横幅无提示符/boot_count=1/伴其它 fatal/BT crash-loop/无 panic 词同行/默认关 全拒）；夹具绑定 6 项（钉死夹具过；自造 NVS/无 sidecar/radio_off=false/区域 SHA 不符/文件缺失 全拒）；builder 7 项（分区解析/缺 NVS 条目/垃圾输入/注入与 sidecar/钉死标记/无参不注入/超容与缺失 fail-closed）+ 夹具完整性 1 项。
- 端到端（门禁同款命令，不管道）：build+fixture → smoke `--require-protocol` → **exit 0 / PASS**，oracle pass、boots=2、豁免记录在场、协议真应答、镜像新鲜、机型身份 match。
- 反向证据保留：默认 BT 镜像报告（BT crash-loop fail）已用默认流程重跑恢复 `results/qemu/qemu_smoke_report.json`；豁免对默认镜像不生效（无夹具绑定）。

## 5. 明确不做 / 剩余项

- **Wokwi 层保持 fail（阻断）**：缺口 3 需跳过 WiFi 初始化代码段（产品构建配置改动），拍板不做；登记为 known-limitation，待 Wokwi 上游改善或用户另授权。
- radio-off 镜像只验证**非射频启动**；BT/WiFi 未在仿真中验证（实机证据：量产机 `[MSG:No BT]` + STA Telnet 板上链路）。
- 射频初始化路径的启动回归覆盖 = 本批代价，由实机 HIL 承担。
- `test_golden_record.test_from_last_pass_writes` 依赖 `protocol_sim/results/last_report.json` 前置状态（需含 smoke_ok pass 用例），与本批无代码路径交集，属既有测试隔离弱点；定向复跑通过、全套 discovery 内受前置状态影响。
