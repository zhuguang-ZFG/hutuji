# 工作树盘点

> 登记所有脏树、未推提交与“生产/板上运行态领先 GitHub”的情况。HEAD 每次用命令现取；本页只保存经证据绑定的基线和必须保留的本地资产。**不要把本页静态快照当作当前 HEAD 或板上镜像身份。**

## 1. hutuji（枢纽仓）

- **路径 / 分支**：`D:/Users/hutuji` / `main`
- **当前提交链**：实时用 `git log --oneline origin/main..HEAD` 与 `git rev-parse --short HEAD` 查询；不以本页静态快照代替。
- **生产运行态**：提交基线仍为 `1082400`；2026-08-19 最终三路对冲 + 商汤快速重试 + 同题 single-flight 合并 + 中心线/G-code 双缓存版 `bitmap_svg.py` / `config.py`（`server.py` 同批 md5 `65adea296c38f4ab07788bdd1edf7ca0`）LF scp 后本地/生产 md5 `d6f064ccbc1d51474a2c93e4d595e9b3` / `183aa425da1c603bf80e4e00386254f2`，双服务 active，preflight 26/26；真实同题小松鼠首轮 22.278s、双缓存命中第二轮 0.002s，两次 G-code 均为 941 行/22,159B/50 次抬笔。硅流双 racer 代码保留但生产 key 已注释停用（用户决定不充值；停用后真实出图无硅流请求）。bridge/nginx/cloudflared/env 结构未改。
- **本地保留资产**：原 12 个会话记忆、临时脚本和串口转储在 `results/local-only/2026-08-18/`；`.pi-memory-consolidated.md`、两份临时记忆整理计划及 Pollinations 六张候选图/冷 seed 延迟证据在 `results/local-only/2026-08-19/`；Waveshare 触摸修复的 `drag-scroll-visible-boot.log` 与 `drag-responsive-boot.log` 在 `results/local-only/2026-08-20/`。三处均只在本机、未删除、未上传，也不纳入产品提交。
- **未推判断**：不用静态文字；执行 `git log --oneline origin/main..HEAD`。

## 2. Grbl_Esp32（运动控制）

- **路径 / 分支 / HEAD**：`D:/Users/Grbl_Esp32` / `fix/panel-hold-after-align-116687e` / `847b564`，与远端同名分支对齐；工作树干净。
- **有证据的板上行为基线**：`e3b1cad`（2026-08-15 烧录与启动自证）；源码后续 `847b564` 增加 §9-D′ 蓝牙功率，**没有本轮烧录证据，不能把源码 HEAD 写成板上基线**。
- **验证边界**：核心 §9、归位、自动换纸有历史证据；后续源码变化须另跑 fz、构建、烧录与启动身份核验。

## 3. xiaozhi-esp32（S3 AI 交互）

- **路径 / 分支 / HEAD**：`D:/Users/xiaozhi-esp32` / `feat/hutuji-usb-pipe` / `211d75a`，已推 `origin/feat/hutuji-usb-pipe`；touch 拖动修复提交 `211d75a`，UI/设计系统提交 `2dc2769`，BLE 生命周期提交 `f1aa0e7`，工作树干净。
- **板上默认 UI 镜像**：COM14 当前运行 `211d75a` 默认镜像 `966bdf3d…a1adf`（2,974,896B）；esptool 写入哈希校验通过，随后约 60 秒持续收到 Grbl `Idle`，最低 SRAM 27,803B，无 panic/assert/watchdog。用户已确认入口恢复可见且前一版可拖动；当前绝对锚定版慢拖/快拖跟手性仍待现场复核，预览确认/取消与抽屉机器动作 HIL 未验。
- **当前源码验证与构建**：全 host suite 93/93；相关 C++ clang-format dry-run 与界面 detector 通过；ESP-IDF v6.0.1 默认构建 `build-waveshare-35/xiaozhi.bin` 2,974,896B、SHA-256 `966bdf3d…a1adf`。最近一次 BLE 启用构建仍是 `build-hutuji-ble-enabled/xiaozhi.bin` 3,172,544B、SHA-256 `5a68fafe…80d6`（`CONFIG_HUTUJI_BLE_DIAGNOSTICS=y`，生命周期代码 `f1aa0e7`）。
- **BLE 板上窄证据与回滚（生命周期代码 `f1aa0e7`，当前 HEAD 未改该模块）**：启用镜像 `5a68fafe…80d6` 刷入 COM14 后 4 分钟 1,683 行日志无 `BLE_ERR_MEM_CAPACITY`/panic/assert/reboot，free SRAM 稳定约 22.1KiB（`minimal sram: 1647` 低于 24KiB 门槛，缺 BLE-off 基线，不判定回归）；Bleak 30s/15s 均收到目标 UUID、固定 6 字节 payload、seq 10→140、`stale=0`、无 local name/scan response。NVS 回滚读回 byte-identical（`enabled=0`），默认镜像重刷自证，10s 目标 UUID 扫描无匹配。证据在 `results/local-only/2026-08-19/`（本机，不入库）。BLE 仍 🟡：尚未完成共存量化矩阵（含 off 基线）、完整功耗/温升与阶段 B 认证 GATT。

## 4. fz（Grbl 门禁）

- **路径 / 分支 / HEAD**：`D:/Users/zhugu/fz` / `main` / `c2bf1a3`。
- **工作树**：2 个未跟踪实验文件 `native_sim/telnet_server_behavior.cpp`、`native_sim/test_telnet_server_behavior.py`；属于用户资产，保留、不上传到 hutuji。
- **规则**：改 Grbl 代码后必须在 fz 跑 `scripts/agent_gate.py --profile standard`；本仓 `full` 只做 §9 标记检查，不能替代。

## 当前汇总

| 仓库 | 实时 HEAD 命令 | 已知状态 |
|---|---|---|
| hutuji | `git rev-parse --short HEAD` | 本次 tracked 文档随提交收口；12 个 local-only untracked 保留 |
| Grbl_Esp32 | `git -C D:/Users/Grbl_Esp32 rev-parse --short HEAD` | `847b564`，干净；板上证据止于 `e3b1cad` |
| xiaozhi-esp32 | `git -C D:/Users/xiaozhi-esp32 rev-parse --short HEAD` | `211d75a`，已推且干净；默认镜像已刷 COM14并通过窄启动检查，绝对锚定拖动手感待现场复核 |
| fz | `git -C D:/Users/zhugu/fz rev-parse --short HEAD` | `c2bf1a3`；2 个用户实验文件保留 |

> 每次新增/清除工作树后立即更新。本页的静态 SHA 是证据快照，不替代实时命令；源码 HEAD 也不自动等于生产/板上运行态。
