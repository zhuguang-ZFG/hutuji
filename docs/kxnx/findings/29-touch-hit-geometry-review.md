# 29 · 触摸「点按无反应」深度 review——命中几何定案（2026-08-26）

## 结论

1. **主变量是命中几何，不是按压时长、不是事件丢失**。08-21 诊断日志重算（纠正临时解析器的 ms/µs 误除）：27 次完整按压中，配网钮实测锚点 chip(291,44) 附近的四次——90ms@dist52、170ms@dist47、240ms@dist29 全部「死」，360ms@dist0 唯一命中（UP 后 40ms `EnterWifiConfigMode`，287338 QR、287508 Alert）。48px 钮半径仅 24px：三个「死」按压全在钮外，唯一命中在钮心。日志里**不存在**「短按在钮内却死」的数据点——时长与命中区两个变量中，距离完全解释数据，时长是被距离混淆的伪相关。
2. **轮询漏采只威胁 <20ms 按压**。indev 读周期 10ms 是真实存在的（`70f1c58` 经 `lv_timer_set_period(lv_indev_get_read_timer(...), 10)` 实现，host 测试 `test_waveshare_touch_indev_read_period_is_10ms` 钉源码）；50-90ms 按压跨 5-9 个采样窗，相位漏采数学上不可能。10-20ms 按压跨 1-2 窗、确可漏，但人类有意点按极少低于 40ms。
3. **事件链路端到端健康**：chip（FT6336，诊断镜像证 10ms 极速轻点全捕获）→ esp_lcd_touch 轮询读 → esp_lvgl_port `lvgl_port_touchpad_read`（纯读寄存器，非事件锁存；INT 未接故无 EVENT 模式）→ LVGL 9 indev（`lv_indev.c:132` 读定时器）→ 控件。360ms 命中 → 40ms 内响应，全链无积压。

## 机制链要点（file:line）

- `managed_components/espressif__esp_lvgl_port/src/lvgl9/esp_lvgl_port_touch.c:120-160`：`lvgl_port_touchpad_read` 每次 `esp_lcd_touch_read_data` 读芯片**当前态**；`int_gpio_num == GPIO_NUM_NC` 时不注册中断回调、不设 `LV_INDEV_MODE_EVENT`——纯周期轮询，按压必须活到某次读瞬间。
- `managed_components/lvgl__lvgl/src/indev/lv_indev.c:132`：读定时器周期默认 `LV_DEF_REFR_PERIOD`（sdkconfig=33ms）；本仓在板级代码显式改 10ms。
- 触摸变换（板 cc:304-309）：`swap_xy=1, mirror_x=1, mirror_y=0` → LVGL=(479−chip_y, chip_x)。**按钮位置经拖动存 NVS**（`hutuji_ui` trig/talk/wifi×_x/_y），离线分析不可假设默认矩形——本文配网钮锚点即实测 NVS 位。
- 自定义层（`main/display/lcd_display.cc:816-823` 注释）：24px 拖动阈值区分点按/拖动；`PRESS_LOCK` 锁定首触对象；位置从首个采样起跟手。均不吞短按。

## 外部参照

- **逆向 Java（Kenjoy/kvenjoy，920 文件）**：Java Swing 桌面软件（JTattoo PLAF import 实证），全树无 MotionEvent/onTouch/OnClickListener——鼠标 UI，对 ESP32 触摸问题**不适用**，仅可参照其桌面按钮的最小尺寸惯例（未逐查，不作为依据）。
- **GitHub/LVGL**：LVGL issue #8152（indev 读周期与刷新周期绑定）已在此前批次引用并落地 10ms；`lv_obj_set_ext_click_area`（`lv_obj_pos.h:497`）为 LVGL 官方命中扩展 API，自家 arc 控件用 `LV_DPI_DEF/10`。上游 xiaozhi 主仓无本板 UI（本板 UI 为本仓自研），无对齐基线可引。

## 已实施修复（`12b6751` + 本次 ext_click_area）

1. taskLVGL priority 1→4（拖动跟手，见 08-26 readiness 条目）。
2. 指针按压期间暂停 AccentDrift 动画（渲染管线让路）。
3. **命中区扩展 + 角钮合规恢复**（`0cf01ce` + `a7e0495`）：talk 钮 ext 12px（96→120px 有效，孤立居中无邻接冲突）；两角钮 ext 8px（角钮间距约 20px，12px 会互相越界抢事件）。角钮 48→56px——56px 是本仓成文硬约束（`lcd_display.cc:7` 头注 CONSTRAINTS / `PRODUCT.md:55`「at least 56 px」/ `DESIGN.md:169`，DESIGN.md:45 token 表 control 即 56px），2026-08-20 美观改版降到 48px 属违规漂移，恢复 56 属合规非改设计；56+ext8=有效半径 36px 覆盖 29px 级脱靶，47-52px 级靠更大视觉目标改善瞄准。钉死测试同步 48→56（RED→GREEN）。

## 未验证（待实机 HIL）

- 刷机后：固定钮（维护页）≤90ms 快按、三可拖钮拖动跟手、角钮命中改善体感。
- ext 8px 在 NVS 拖放后按钮贴近时的抢事件风险（理论间距 20px>8+8，实机布局未验证）。
- 47-52px 脱靶是否仍复现（决定是否需要 48→64px 视觉放大）。
