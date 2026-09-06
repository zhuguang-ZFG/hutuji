# ElectronBot 桌宠脸替换 Grobot 眼睛（2026-09-06）

> **2026-09-06 同日 supersede：本文 ElectronBot GIF 脸方案已由 kawaii 桌宠脸取代**（用户见过 ElectronBot 实物后否决，指明要 Eilik 式大眼+嘴桌宠感；Eilik 本体为 Energize Lab 闭源商业产品、素材不可得，2026-09-06 anysearch 实证）。最终落地 = `lvgl_kawaii_face@d58e1c8`（MIT，程序绘制眼/眉/嘴/腮红、17 情绪、自动眨眼）vendor 进 `main/boards/lichuang-dev/kawaii_face/` + 三本地补丁（`face_animation_set_paused` 暂停 API、画布 SPIRAM 优先、header `extern "C"`），接线面与本文定案相同（保留 LcdDisplay/预览/字幕/暂停钩子语义），xiaozhi `ac819f7`。本文保留作为资产管线与否决路线的证据链（EAF/AAF 转换、EmoteDisplay 否决、GIF 内存账）；ElectronBot 资产（`tmp/electronbot_emoji/`）与再生脚本留档 hutuji 仓 tmp/。
>
> **2026-09-06 晚再调参（方案 1）**：在同一 kawaii 实现上往 Eilik 外观靠——黑底无眼白、淡青虹膜/主题 accent `#B4F0FF`、脸盒铺满 312×232；并修 `AccentDriftTimerCb` 在 KAWAII 下误用 π 品牌渐变覆盖按钮色的问题。证据见 `docs/release-readiness.md` 文首与 `docs/worktree-inventory.md`「kawaii→Eilik 调参批」。

## 背景与授权

- 用户原话：「要不就不用这个眼镜了，在github找桌宠表情吧」→ 选型时豁免许可证（「不用考虑许可，哪个最好」）→ 拍板 **ElectronBot 表情**。
- 触发事故：grobot 程序绘眼 30fps 动画与 TLS 同核互抢致「一直在下载中」（v10 已加 performance-hold 暂停钩子止血）；换脸后暂停钩子保留。
- 许可证事实（用户已豁免，出货前须复核）：ElectronBot 仓整体 GPLv3（`peng-zhihui/ElectronBot@81992701` LICENSE），表情素材作者 oooooohmygosh、仓内无独立许可条款。若商业出货固件需公开源码或换授权，此处须在合规签核时重新点名。

## 证据与调研（FaceScout 2026-09-06，报告 `tmp/face-scout-0906.md`）

- 资产实况：仓 `4.CAD-Model/Emoji/` 6 组（静态/兴奋/难过/愤怒/惊恐/不屑），每组 3 段 mp4（进入/循环/回正）+ 演示；h264 900×900、0.5–1.25s、纯黑白平色眼形。
- 乐鑫官方栈已在本仓 `managed_components`：`esp_emote_assets`（EAF 资产+`emote.json` 情绪映射）、`esp_emote_expression`（整屏框架）、`esp_emote_gfx`（EAF 解码器）、`image_player`（`gif_to_aaf.py` 转换器）。
- **EAF 解码器直接接受 AAF 格式串**（`gfx_eaf_dec.c` `is_valid = memcmp(EAF)||memcmp(AAF)`）——`gif_to_aaf.py` 即官方转换器。
- 否决路线：`emote::EmoteDisplay`（`expression_emote` 组件）整屏接管面板、绕开 LVGL 渲染——`SetPreviewImage` 不支持（`emote_display.cc:202` 明写），接入即毁掉 hutuji 预览确认流。
- 本 fork `LcdDisplay::SetEmotion` 已有 GIF 播放路径（`LvglGif` 控制器 + `emoji_image_`，gif 结束帧回调齐备）；`EmojiCollection` 拥有并 delete 各 `LvglImage*`，别名须多个 wrapper 共享同一只读字节数组（`LvglRawImage` 不拥有数据）。

## 定案架构

**保留 LcdDisplay 全部布局/顶栏/预览/字幕，只把 grobot 画布换成 emoji_image_ 播 GIF。**

- 新 CMake 开关 `HUTUJI_ELECTRONBOT_FACE`（非 Kconfig；waveshare-3.5 板分支默认 ON，`-D=OFF` 回退 grobot，其它板不受影响）：SetupUI 在 grobot 分支内改走「建 EmojiCollection + 显示 emoji_image_」；情绪名走既有 21 名集合（`grobot_eyes.cc kNames` 同源），未知名回落 neutral。
- 资产嵌入固件（C 数组，不入 assets 分区）：mp4 → ffmpeg 256×256@15fps 灰度 GIF，6 循环段 + 眨眼 2 + 静态帧 1 = **240.3KB**（纯黑白平色 LZW 友好）；分区面：partitions/v2/16m.csv 的 ota_0/ota_1 各 0x3f0000（4128KB），本批镜像实测 3,219,552B（余 ~876KB）。
- 情绪映射：兴奋→happy/laughing/funny/loving/delicious/silly/confident/cool；难过→sad/crying；愤怒→angry；惊恐→shocked/surprised；不屑→thinking/confused/kissy/embarrassed/winking；静态→neutral/relaxed/sleepy + 眨眼偶发（blink_once 单段，5s 一拍）。
- 暂停钩子：`SetGrobotEyesPaused` 保持方法名与调用点（hutuji_job 两处）；electronbot 模式下 Pause/Resume 冻结在当前帧（零重分配——neutral/blink 常驻控制器 Stop 倒带不释放，情绪循环段随情绪在 gif_controller_ 上换）；暂停期 SetEmotion 只记录不播放，恢复时按最新情绪重放。
- SetStatus 不动 listening/speaking 的眼睛特化（那是 grobot 专有能力），electronbot 模式下 speaking 由 LLM 情绪流驱动。
- 资产形态修正（转换时发现）：neutral 静态帧也用**单帧 GIF**（3.3KB），不用 PNG——`LvglRawImage` 以 `LV_COLOR_FORMAT_RAW_ALPHA` 交给 LVGL 图片解码器链，PNG 依赖 `LV_USE_PNG/LODEPNG` 开关，而 gifdec（`display/lvgl_display/gif/`）恒定链接。全资产统一 GIF 后无解码器开关面。

## 风险与验证

- GIF 解码 CPU：240² 灰度小帧 + 12fps，远低于 grobot 30fps 全脸重绘；暂停钩子在 job 高压窗口仍生效。
- host 源码钉（scripts/tests）：Kconfig 存在、SetupUI 分支、暂停钩子双路径、情绪映射表完整性（21 名全覆盖）。
- HIL：刷 COM14 后目视各情绪切换 + 写字 job 期间动画冻结 + 下载不再卡死。
