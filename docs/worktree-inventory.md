# 工作树盘点

> 登记所有脏树、未推提交与“生产/板上运行态领先 GitHub”的情况。HEAD 每次用命令现取；本页只保存经证据绑定的基线和必须保留的本地资产。**不要把本页静态快照当作当前 HEAD 或板上镜像身份。**

## 1. hutuji（枢纽仓）

- **路径 / 分支**：`D:/Users/hutuji` / `main`
- **当前提交链**：实时用 `git log --oneline origin/main..HEAD` 与 `git rev-parse --short HEAD` 查询；不以本页静态快照代替。
- **生产运行态**：提交基线仍为 `1082400`；2026-08-17 窄部署的 `bitmap_svg.py` / `server.py` 以 SHA-256 `949d2da7…fff8` / `ac06ee82…e9e` 绑定，preflight 26/26。GitHub 源码已由 `155dbff` 追上该运行态及其配置/测试，但未在本轮重新部署。
- **本地保留资产**：原 12 个会话记忆、临时脚本和串口转储已移入 `results/local-only/2026-08-18/`，保留在本机、未删除、未上传；当前仓库根另有用户/工具生成的未跟踪 `.pi-memory-consolidated.md`，同样保留、不删除、不纳入本次提交。归档文件清单：`pi-hermes-memory-consolidation.md`、`COM13-COM14_Terminal.txt`、`memory-consolidation-ws35-pmic.md`、`memory-consolidation-ws36.md`、`tmp-audit-summary.md`、`tmp-excel-read.py`、`tmp-excel-read2.py`、`tmp-memory-consolidation-plan.md`、`memory-consolidated-v1.md`、`memory-consolidation-plan-v2.md`、`memory-consolidation-plan.md`、`memory_add`。
- **未推判断**：不用静态文字；执行 `git log --oneline origin/main..HEAD`。

## 2. Grbl_Esp32（运动控制）

- **路径 / 分支 / HEAD**：`D:/Users/Grbl_Esp32` / `fix/panel-hold-after-align-116687e` / `847b564`，与远端同名分支对齐；工作树干净。
- **有证据的板上行为基线**：`e3b1cad`（2026-08-15 烧录与启动自证）；源码后续 `847b564` 增加 §9-D′ 蓝牙功率，**没有本轮烧录证据，不能把源码 HEAD 写成板上基线**。
- **验证边界**：核心 §9、归位、自动换纸有历史证据；后续源码变化须另跑 fz、构建、烧录与启动身份核验。

## 3. xiaozhi-esp32（S3 AI 交互）

- **路径 / 分支 / HEAD**：`D:/Users/xiaozhi-esp32` / `feat/hutuji-usb-pipe` / `dcbec6f`；已推 `origin/feat/hutuji-usb-pipe`，工作树干净。
- **板上源码身份**：COM14 启动横幅 ELF `031cafd51...` 与提交前同一工作树构建的 ELF `031cafd5…aada` 对齐；该工作树已原样提交为 `dcbec6f`。
- **部分 HIL**：触摸、七工具、`HutujiPipe`、WiFi、Grbl 授权、Idle，以及控制抽屉 `jog_y+`×2 / `jog_x-` / `pen_down` 已有证据；预览确认/取消与暂停/继续/停止/重画/试笔五个屏幕动作仍未验证。
- **验证**：host 67/67；固件构建和 app-flash hash 证据见 `release-readiness.md` 文首。真实语音出图/归位/自动换纸是 2026-08-17 历史证据，不替代当前新增 UI HIL。

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
