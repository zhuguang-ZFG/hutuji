# 对齐计划：hutuji 笔控改走奎享 Stepper 模式

> 状态：**待实施**。本文件是执行清单，每步做完打勾。
> 依据：`docs/kxnx/findings/01-pen-control.md` 起四份发现文档。

## 背景

实测 `G0 Z5`（直接 Z 指令）能让笔物理落纸，但 `M3`（走 Grbl 的 `USE_M3_M5_AS_PEN_UP_DOWN` 宏）不能。奎享作为这套硬件的**原配上位机**，在现役机器上用 `G1 Z`（非 M3/M5）稳定出图。放弃 M3/M5 宏，对齐奎享 Stepper 模式，是最可靠的修法。

## 目标

- [ ] 出图后**纸上有连续线条**（不再是空走）
- [ ] `hutuji_gwrite.toml` 笔控行与奎享 Stepper 模式逐字一致
- [ ] 不影响既有 §5 契约（无 M30/M2/G0X0Y0）
- [ ] 不引入新卡顿（笔控行数不增加）

## 改动清单

### 1. `mcp-server/hutuji_mcp/hutuji_gwrite.toml`

```toml
document_start = "G21\nG90\nG92 Z0\nF1500\n"
# 先 G1 空走到起点（F2500），再 G1 落笔（F3000，Z=zOn=5）
segment_first = "G1 X{x:.3f} Y{y:.3f} F2500\nG1 G90 Z5.000 F3000\n"
segment       = "G1 X{x:.3f} Y{y:.3f}\n"
line_end      = "G1 G90 Z0.000 F3000\n"
document_end  = "G1 G90 Z0.000 F3000\n"
```

变更点：
- `document_start` 加 `G92 Z0`（每次出图校准 Z 基准，对齐奎享首次笔控前校准）。
- `segment_first` 的 `G0` → `G1`，`M3` → `G1 G90 Z5.000 F3000`，去掉 `G4 P0.2`。
- `segment` 去掉 F（继承 document_start 的 F1500）。
- `line_end` 的 `M5` → `G1 G90 Z0.000 F3000`，去掉 `G4 P0.2`。
- `document_end` 同 line_end。

### 2. 常量化（可选，先硬编码 Z=5/Z=0/F3000）

若以后要调，可在 `gcode_gen.py` 顶部加：
```python
PEN_DOWN_Z_MM = 5.0    # 实测 Z=5 触纸
PEN_UP_Z_MM = 0.0
PEN_Z_SPEED = 3000     # 奎享默认
```
然后模板用 f-string。**第一版先硬编码在 toml**，避免引入 Python 拼接（vpype-gcode 的 toml 模板机制不支持变量注入，得另走 sed 或重写生成器）。先简单，验证通过再考虑参数化。

## 验证步骤

### 2a. 单元测试不破

- [ ] `cd mcp-server && .venv/Scripts/python -m pytest -q` 仍 85 pass
  - 关注 `test_contract.py`：`G0 X0 Y0` 禁令、M30/M2 禁令、字节上限
  - 新模板不引入这些，应继续通过

### 2b. 生成测试 G-code

- [ ] 走真实 `svg_to_gcode()` 生成一张，检查：
  - 头部含 `G21\nG90\nG92 Z0\nF1500`
  - 落笔行是 `G1 G90 Z5.000 F3000`
  - 抬笔行是 `G1 G90 Z0.000 F3000`
  - 无 `M3`/`M5`/`G4`/`G0 X0 Y0`/`M30`
  - 行数与之前 linesimplify 后相当（笔控改方式不影响行数）

### 2c. 串口直发验证（绕开 S3/WiFi）

- [ ] 下载生产 G-code，COM3 串口逐行发
- [ ] 观察：
  - `G92 Z0` 返回 `ok`
  - 落笔行后笔**物理压到纸**
  - 画线期间笔不离纸
  - 抬笔行后笔**物理抬起**
- [ ] **纸上应出现连续线条**（这是核心验收点）

### 2d. 端到端验证

- [ ] 部署到 VPS（scp toml → restart hutuji-mcp → preflight 8/8）
- [ ] 让小智画一张（或走 VPS 直调 hutuji_draw + S3 转发完整链路）
- [ ] 纸上出现完整图案

## 风险与回退

### 风险

1. **`G92 Z0` 校准时机**：要求出图开始时笔处于"弹簧自然抬起位"。若用户出图时笔被人为按下去，Z0 基准会偏，Z5 可能扎不到纸或扎过头。
   - 缓解：首次出图前提示用户"确认笔已自然抬起"；或出图前先发一次抬笔 Z 运动让它回弹（但没基准又绕回来了——chicken-egg，靠弹簧自然位最稳）。

2. **`G1` 空走比 `G0` 慢**：F2500 vs G0 的 rapid（默认最大速率）。空走时间略增。
   - 评估：F2500 = 42mm/s，比 G0 慢但比画线 F1500 快，体感差异小；换来的稳定性值得。

3. **去掉 G4 后落笔不稳**：F3000 落笔到位需要时间，G1 执行完才回 ok，天然同步。G4 本来就是多余的保险。
   - 评估：奎享 Stepper 模式无 G4 且现役可用，证明不需要。

### 回退

- toml 改动是纯文本，`git revert` 即可。
- VPS 部署前会备份当前 toml 到 `/root/hutuji-backup/<ts>/`，回退 = `install` 旧版 + restart。
- Z 轴参数（`$1=255` 等 NVS 设置）不受影响，无需回退固件层。

## 不做的事

- **不改 Grbl 固件**（GCode.cpp 的 M3/M5 宏留着，只是上位机不再发 M3/M5）。
- **不跑 fz 门禁**（本次只改云端 toml，不碰 Grbl 源码；fz 门禁是 Grbl 改动才需要）。
- **不参数化 Z 值**（第一版硬编码，验证通了再说）。

## 完成定义（DoD）

- [ ] 上述 2a/2b/2c 全过，2d 至少串口直发链路出图
- [ ] `docs/release-readiness.md` 写字机行更新："笔控改 G1 Z 对齐奎享，实测落笔出图"
- [ ] 记忆 `m3m5-pen-drop-fix.md` 追加：最终方案是绕开 M3 宏走 G1 Z
- [ ] 提交 + 推送 + 部署 VPS
