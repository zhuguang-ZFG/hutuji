# 工作树盘点

> 登记所有脏树、未推提交与“生产/板上运行态领先 GitHub”的情况。HEAD 每次用命令现取；本页只保存经证据绑定的基线和必须保留的本地资产。**不要把本页静态快照当作当前 HEAD 或板上镜像身份。**

## 1. hutuji（枢纽仓）

- **路径 / 分支**：`D:/Users/hutuji` / `main`
- **当前提交链**：实时用 `git log --oneline origin/main..HEAD` 与 `git rev-parse --short HEAD` 查询；不以本页静态快照代替。
- **生产运行态**：应用基线 `4d9e49a`（2026-08-21 服务时 §5 复核）。复核轮（`4d9e49a`）：`server.py` 单文件 LF blob SHA-256 `7aa29c0e…1699` 两端一致、CR=0，备份 `/root/hutuji-bak-20260821-stalecheck/`，双服务 restart 后 active，preflight 29/29；生产探针实测越界旧名 `.gcode` 经 `/files` 404 + 删除 + 日志脱敏。唱歌轮（`2d82051`）：`server.py`（`/songs` 路由 + 云端 `hutuji_sing`）/ `songs/`（catalog + 三首自合成公版 OGG）/ `nginx-hutuji.conf`（`/songs` location）逐文件 LF blob 核 SHA-256/CR=0，备份 `/root/hutuji-bak-20260821-sing/`，三服务 active，公网 `/songs/twinkle.ogg` 200 `audio/ogg` 152422B + `x-hutuji-crc32: 441a8c9a`，preflight 29/29。记忆轮（`fae5eac`）：`memory_store.py` SHA-256 `7dc81b9a…` 两端一致，双服务 restart，preflight 29/29，记忆三工具生产写/读/删实测通过。现行链仍含 2026-08-20 取消重试去重、08-19 三路对冲、商汤快速重试、中心线/G-code 双缓存；硅流 key 按用户决定保持停用。
- **本地保留资产**：`results/local-only/2026-08-18/`、`2026-08-19/`、`2026-08-20/` 与 `2026-08-21/` 均保留且被 gitignore；08-21 新增唱歌部署候选与冒烟证据、刷机事故串口原始日志（`2026-08-21-sing-bootloop-raw.log`）、恢复启动日志（`2026-08-21-waveshare-restore-boot.log`）与触摸第三轮开机取证（`2026-08-21-touch-round3-boot.log`，含双门回读 `70 -> 20 peak 60 -> 40`）。根目录杂散 `COM14` 文件已移入 `results/local-only/2026-08-20/`；`tmp/` 未跟踪保留。不新增忽略规则，避免同类误生成被静默掩盖。
- **工作树**：干净（仅未跟踪 `tmp/` 保留）。本轮 code review follow-up 已恢复 `_serve_file` 的 `Content-Disposition: attachment` 回归断言；定向下载契约 2 项通过，hub 门禁 overall=pass（pytest 900 + e2e PASS），随当前提交落账。2026-08-21 服务时复核批次已提交 `4d9e49a`（`server.py` 下载时 §5 复核 `_read_verified_output`/`StaleOutputError` + `test_contract.py` 新增 4 项回归与 4 处夹具换合法内容；pytest 900 绿、e2e PASS、hub 门禁 pass）。对应 readiness 文首 2026-08-21「服务时 G-code 复核」条目；**当日已部署生产**（基线 `4d9e49a`，preflight 29/29）。
- **未推判断**：不用静态文字；执行 `git log --oneline origin/main..HEAD`。

## 2. Grbl_Esp32（运动控制）

- **路径 / 分支 / HEAD**：`D:/Users/Grbl_Esp32` / `fix/panel-hold-after-align-116687e` / `847b564`，与远端同名分支对齐；工作树干净。
- **有证据的板上行为基线**：`e3b1cad`（2026-08-15 烧录与启动自证）；源码后续 `847b564` 增加 §9-D′ 蓝牙功率，**没有本轮烧录证据，不能把源码 HEAD 写成板上基线**。
- **验证边界**：核心 §9、归位、自动换纸有历史证据；后续源码变化须另跑 fz、构建、烧录与启动身份核验。

## 3. xiaozhi-esp32（S3 AI 交互）

- **工作树**：干净。2026-08-21 触摸第三轮批次已提交并推送 `4d5d4e0`（板 cc 双检测门齐调 `TH_GROUP` 30→20 + `THPEAK` 60→40、host 断言同步、板 README 调参历史更新；host 102/102；ESP-IDF v6.0.1 构建 `build-waveshare-35/xiaozhi.bin` 0x2da630、SHA-256 `d1d2f5f6…e7d5`）。**已刷 COM14**：esptool 哈希校验通过，开机日志实测 `Touch threshold tuned: 70 -> 20 peak 60 -> 40` 双门回读确认、25s 无 panic/assert、minimal sram 21,207B、HutujiPipe 持续 `<Idle|…>`、点动事件正常上报（取证 `results/local-only/2026-08-21-touch-round3-boot.log`）。`git merge-base --is-ancestor` 实测 `75e83d7`/`5e1a91b`/`09a9764` 均为 `4d5d4e0` 祖先——设备侧点动包线 Y≤190 与唱歌双工具随本镜像一并上板，readiness「固件 Y 限幅缺口」已 supersede。触摸灵敏度体感仍待用户现场 HIL。此前文档收口批次 `f98d42f`（`DESIGN.md` 与板 README 点动包线口径）。
- **历史批次（工作树此前干净时登记）**：`70f1c58` 批次 4 文件（板 cc、lcd_display.cc、settings.cc、host 测试）：FT6336 TH_GROUP 70→30（FT5x06 官方寄存器文档 + Linux EDT 20–80 区间）、indev 读定时器 33ms→10ms（`LV_DEF_REFR_PERIOD=33` 实测 + LVGL issue #8152）；复审 P1-1 断连看门狗门控早退后原地续表 + 已恢复连接不进配网、P2-1 `Settings::SetInt` 句柄守卫、P2-2 talk/wifi 点按回弹、P2-3 贴边存档回默认。host 99/99。`37be0e9` 批次 6 文件（lcd_display.{h,cc}、板 cc、双语 locale、host 测试）：「说话」大圆钮 + 「控制」「配网」角落钮、三钮按下跟随拖动 + NVS `hutuji_ui` 布局记忆、二维码「关闭」（Schedule+StopConfigAp 事件链）、断连 120s 看门狗；含实机事故修复（拖动回调 user_data 误传 `this` → LoadProhibited 白屏重启，两轮同 Backtrace `0x42026ca6` 取证，改传 state + 防回退测试钉死）。原「分页抽屉 + taskLVGL 栈 12288 + 触摸/点动调参 + WiFi 省电持有 + 测试 + DESIGN」8 文件批次已提交 `2bc04a6`。Y 限幅收紧批次已提交 `75e83d7`（`hutuji_recovery_core.h` + host 测试 + 文档）。
- **板上镜像**：COM14 当前运行 `4d5d4e0` 的 Waveshare 3.5 构建（`build-waveshare-35/xiaozhi.bin` 0x2da630，SHA-256 `d1d2f5f6…e7d5`；含触摸双门齐调、点动包线 Y≤190、唱歌双工具）：esptool `Hash of data verified`，开机 25s 无崩溃、AFE/编解码/PMIC 正常、HutujiPipe 持续收 `<Idle|…>`；唱歌语音全链实测为 `5e1a91b` 代际取证（「唱一首小星星」→ 云端 `CallToolRequest` → 下载 152,422B 命中 Cloudflare 缓存 → 播放 10.7s → 喊停 → `hutuji.stop_song` 干净退场）。**刷机事故留档**：同日曾误把 lichuang-dev 板型镜像刷入本板，`I2cDevice::WriteReg` 0x108 崩溃循环，先回刷 `build-hutuji-ble-enabled` 止血再以 HEAD 重建 Waveshare 镜像恢复；已知好镜像备份在 `build-waveshare-35-good/`（`70f1c58` 代际，app `6b844555`）。未验证：触摸灵敏度体感与误触回归现场 HIL，说话/配网/关闭/断连自动显码/布局记忆重启恢复的现场 HIL。
- **当前源码验证与构建**：全 host suite **102/102**（`scripts/tests`，含唱歌 `hutuji_music` 核心套件 + 触摸双门/读周期/看门狗/布局记忆断言）。ESP-IDF v6.0.1 最新构建 `build-waveshare-35/xiaozhi.bin` 0x2da630、SHA-256 `d1d2f5f6…e7d5`（28% 空闲，触摸第三轮 `4d5d4e0`），**已刷 COM14 并通过开机取证**。最近一次 BLE 启用构建仍是 `build-hutuji-ble-enabled/xiaozhi.bin` 3,172,544B、SHA-256 `5a68fafe…80d6`（`CONFIG_HUTUJI_BLE_DIAGNOSTICS=y`，生命周期代码 `f1aa0e7`）。
- **取证脚本已修正**：`results/local-only/2026-08-20/serial_capture.py` 旧版靠 pyserial 默认 open，DTR/RTS 在 open/close 各复位一次板子（S3 原生 USB Serial/JTAG 把它当复位线），曾把工具复位误读成「自发重启」；现改为清 `dtr`/`rts` 后再 `open()`。同目录 `serial_reset_capture.py` 是**有意**用 RTS 发复位脉冲，不要照此修改。
- **BLE 板上窄证据与回滚（生命周期代码 `f1aa0e7`，当前 HEAD 未改该模块）**：启用镜像 `5a68fafe…80d6` 刷入 COM14 后 4 分钟 1,683 行日志无 `BLE_ERR_MEM_CAPACITY`/panic/assert/reboot，free SRAM 稳定约 22.1KiB（`minimal sram: 1647` 低于 24KiB 门槛，缺 BLE-off 基线，不判定回归）；Bleak 30s/15s 均收到目标 UUID、固定 6 字节 payload、seq 10→140、`stale=0`、无 local name/scan response。NVS 回滚读回 byte-identical（`enabled=0`），默认镜像重刷自证，10s 目标 UUID 扫描无匹配。证据在 `results/local-only/2026-08-19/`（本机，不入库）。BLE 仍 🟡：尚未完成共存量化矩阵（含 off 基线）、完整功耗/温升与阶段 B 认证 GATT。

## 4. fz（Grbl 门禁）

- **路径 / 分支 / HEAD**：`D:/Users/zhugu/fz` / `main` / `c2bf1a3`。
- **工作树**：2 个未跟踪实验文件 `native_sim/telnet_server_behavior.cpp`、`native_sim/test_telnet_server_behavior.py`；属于用户资产，保留、不上传到 hutuji。
- **规则**：改 Grbl 代码后必须在 fz 跑 `scripts/agent_gate.py --profile standard`；本仓 `full` 只做 §9 标记检查，不能替代。

## 当前汇总

| 仓库 | 实时 HEAD 命令 | 已知状态 |
|---|---|---|
| hutuji | `git rev-parse --short HEAD` | `4d9e49a`（服务时 §5 复核）；工作树干净 |
| xiaozhi-esp32 | `git -C D:/Users/xiaozhi-esp32 rev-parse --short HEAD` | `f98d42f`（文档收口）；工作树干净；唱歌镜像 `5e1a91b` 构建已刷 COM14 并语音实测通过；**板上不含 `75e83d7`，点动包线设备侧仍为 Y≤277** |
| fz | `git -C D:/Users/zhugu/fz rev-parse --short HEAD` | `c2bf1a3`；2 个用户实验文件保留 |

> 每次新增/清除工作树后立即更新。本页的静态 SHA 是证据快照，不替代实时命令；源码 HEAD 也不自动等于生产/板上运行态。
