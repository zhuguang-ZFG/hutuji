# 工作树盘点

> 登记所有脏树、未推提交与“生产/板上运行态领先 GitHub”的情况。HEAD 每次用命令现取；本页只保存经证据绑定的基线和必须保留的本地资产。**不要把本页静态快照当作当前 HEAD 或板上镜像身份。**

## 1. hutuji（枢纽仓）

- **路径 / 分支**：`D:/Users/hutuji` / `main`
- **当前提交链**：实时用 `git log --oneline origin/main..HEAD` 与 `git rev-parse --short HEAD` 查询；不以本页静态快照代替。
- **生产运行态**：提交基线仍为 `1082400`；2026-08-17 窄部署的 `bitmap_svg.py` / `server.py` 以 SHA-256 `949d2da7…fff8` / `ac06ee82…e9e` 绑定，preflight 26/26。GitHub 源码已由 `155dbff` 追上该运行态及其配置/测试，但未在本轮重新部署。
- **本地保留资产**：原 12 个会话记忆、临时脚本和串口转储在 `results/local-only/2026-08-18/`；本轮新增 `.pi-memory-consolidated.md`、`memory-consolidation-plan-v2.md`、`tmp-memory-consolidation-plan.md` 已移入 `results/local-only/2026-08-19/`。两处均只在本机、未删除、未上传，也不纳入产品提交。
- **未推判断**：不用静态文字；执行 `git log --oneline origin/main..HEAD`。

## 2. Grbl_Esp32（运动控制）

- **路径 / 分支 / HEAD**：`D:/Users/Grbl_Esp32` / `fix/panel-hold-after-align-116687e` / `847b564`，与远端同名分支对齐；工作树干净。
- **有证据的板上行为基线**：`e3b1cad`（2026-08-15 烧录与启动自证）；源码后续 `847b564` 增加 §9-D′ 蓝牙功率，**没有本轮烧录证据，不能把源码 HEAD 写成板上基线**。
- **验证边界**：核心 §9、归位、自动换纸有历史证据；后续源码变化须另跑 fz、构建、烧录与启动身份核验。

## 3. xiaozhi-esp32（S3 AI 交互）

- **路径 / 分支 / HEAD**：`D:/Users/xiaozhi-esp32` / `feat/hutuji-usb-pipe` / `f1aa0e7`；UI/设计系统提交 `2dc2769`，BLE 生命周期提交 `f1aa0e7`，工作树已清理。是否已推远端须实时执行 `git log --oneline origin/feat/hutuji-usb-pipe..HEAD`，不以本页静态文字代替。
- **板上默认 UI 镜像**：COM14 当前运行的默认镜像 `xiaozhi.bin` 2,974,336B、SHA-256 `ee084c20…b2419`，启动横幅 ELF `d4c8fd315...`；触摸初始化成功、`GrobotEyes initialized: 460x300`、WiFi/Telnet/`idle` 均自证，未见 panic/assert/reboot。UI 实际画面目视与预览/五按钮触控仍未验证。
- **当前源码验证与构建**：`python3 -m unittest scripts.tests.test_hutuji_ble_diag_core -v` 为 18/18，全 host suite 为 93/93；ESP-IDF v6.0.1 当前默认构建 `build-waveshare-35/xiaozhi.bin` 2,974,336B、SHA-256 `58defc01…63b2`，当前 BLE 启用构建 3,172,544B、SHA-256 `5a68fafe…80d6`，`CONFIG_HUTUJI_BLE_DIAGNOSTICS=y`。两套构建通过，但当前产物均未刷机；不得把当前构建身份写成板上身份。
- **BLE 板上窄证据与回滚**：启用镜像 `1a3b1777…c8475b` 在运行时 `enabled=1` 后 COM14 稳定约 4 分钟、约 240 次快照刷新，无 `BLE_ERR_MEM_CAPACITY`、panic、assert、reboot；20s/15s Bleak 扫描均收到目标 UUID、固定 6 字节 payload，seq 递增、`stale=0`、无 local name/scan response。随后写 NVS `ble_diag/enabled=0`，默认镜像恢复并启动自证；10s 目标 UUID 扫描为空。该 HIL 早于当前 pending event 合并/轮换优先级加固，不能证明当前 `5a68fafe…80d6` 已刷机。证据：`results/ble-phase-a-fixed-20260819.raw.log`、`results/ble-phase-a-fixed-scan-20260819.json`、`results/ble-phase-a-rollback-scan-20260819.json`（本机，不入库）。BLE 仍保持 🟡：尚未完成当前源码板上复验、WiFi/Telnet 共存量化门槛、完整功耗/温升与阶段 B 认证 GATT。

## 4. fz（Grbl 门禁）

- **路径 / 分支 / HEAD**：`D:/Users/zhugu/fz` / `main` / `c2bf1a3`。
- **工作树**：2 个未跟踪实验文件 `native_sim/telnet_server_behavior.cpp`、`native_sim/test_telnet_server_behavior.py`；属于用户资产，保留、不上传到 hutuji。
- **规则**：改 Grbl 代码后必须在 fz 跑 `scripts/agent_gate.py --profile standard`；本仓 `full` 只做 §9 标记检查，不能替代。

## 当前汇总

| 仓库 | 实时 HEAD 命令 | 已知状态 |
|---|---|---|
| hutuji | `git rev-parse --short HEAD` | 本次 tracked 文档随提交收口；12 个 local-only untracked 保留 |
| Grbl_Esp32 | `git -C D:/Users/Grbl_Esp32 rev-parse --short HEAD` | `847b564`，干净；板上证据止于 `e3b1cad` |
| xiaozhi-esp32 | `git -C D:/Users/xiaozhi-esp32 rev-parse --short HEAD` | `dcbec6f`，已推且干净；部分 HIL |
| fz | `git -C D:/Users/zhugu/fz rev-parse --short HEAD` | `c2bf1a3`；2 个用户实验文件保留 |

> 每次新增/清除工作树后立即更新。本页的静态 SHA 是证据快照，不替代实时命令；源码 HEAD 也不自动等于生产/板上运行态。
