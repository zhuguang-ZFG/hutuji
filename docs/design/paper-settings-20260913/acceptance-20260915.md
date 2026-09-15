# 2026-09-15 量产纸张实机验收

本轮只验量产机：S3 COM15/MAC `b8:1f:3f:c4:58:2c`、提交 `923e148`、hutuji.1.0.19；Grbl COM3/MAC `4c:c3:82:cc:8a:9c`、VER1.3a.20260910、STA192.168.1.66。未重新刷固件。09-15上午首次探针发现写字机断连/COM3缺席，任何写入前停止；用户接电后重新确认ready/Idle/原点再执行。

## 设置与拦截

- Portal `/api/settings/paper` 对精确设备A4留边10→12，回包、随后GET与设备paper_config marker一致；最终恢复10。旧expected409，A3/A2与交换轴超出X205/Y290，在Portal400与S3均拒绝，未确认或扩大行程。证据 `results/20260913-crossrepo-followup/paper-settings-hil-20260915-083718.log`。
- 非默认留边12下，分别发送旧A4 marker产物与无marker旧文件，预览确认后下载阶段error；COM15两次明确“作品纸张与设备配置不一致”，COM3整窗无Run、未启动G92 Z0。
- 恢复原配置后打开匹配预览，改留边返回“请先结束任务或取消预览，再修改纸张”；取消返回“预览已取消”，aborted/Idle/原点，整窗无Run/无$MD。
- 首轮探针误以为status公开last_error而断言失败；实际失败原因在串口，已恢复配置并修正探针。保留失败证据，正式通过组为 `guards-084259`，不以泛化error代替原因验证。

## 运动与收尾

使用生产 `_run_write_job(..., chain_push=False, machine="nopaper", paper=<设备快照>, device_id=<完整MAC>)`，公网上取G-code并核X-Hutuji-Paper，完整G-code另过X/Y≤60mm包线（含弧扫掠），然后设备预览/确认。

| 组 | 样本 | 实机结果 |
|---|---|---|
| normal-084417 | 8mm小「口」，24行/468B，X26.338–32.613、Y9.376–15.007 | 完成及再次绘制各一次；done/Idle/原点；2次自动$MD及成功确认 |
| abort-084509 | 九个小「口」，168行/3639B，X26.474–50.414、Y9.132–40.556 | Run且X28.09/Y14.40时停止，第二次返回“停止处理中”；同一收尾结束后aborted/Idle/原点；1次自动$MD及成功确认 |

COM3捕获仅发实时 `?`，COM15只听；DTR/RTS恒False，不占PC Telnet。总结与每个证据SHA在 `results/20260915-paper-hil/verification-summary.json`。最终官方状态 `ali-device-084619-999498.log`：aborted、ready/authorized、Idle/原点，默认A4 marker、3000/3000/1200。COM3只读 `$I/$$/?` 对09-14接回记录全套设置逐项一致（`grbl-reconnected-20260915-084705.log`），包括$1=255、$3=4、$130/$131=200/$132=20。

## 边界

以上为设备API和串口的窄范围实机证据，不代表大尺寸纸面或视觉质量签核。手机手势、设备实屏其余动作、大行程纸面、量产fz既有失败及生产/合规签核仍分别缺证。用户已确认1.0.19长按配网的显码/微信扫码/关闭正常，本轮保留该结论且未重复索取。
