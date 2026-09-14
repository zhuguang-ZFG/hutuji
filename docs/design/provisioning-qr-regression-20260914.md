# 配网二维码消失回归（2026-09-14）

用户确认：长按进入配网模式后，屏幕上应有的二维码消失。目标板为 Freenove 2.8 量产小派，COM15、MAC `b8:1f:3f:c4:58:2c`，反馈时已运行 `hutuji.1.0.18` / S3 `bde6ba5`。本次先恢复该路径，再继续纸张实机验收。

## 1. 根因与证据

分类：**变更传播遗漏 + 测试覆盖缺口**。身份 v2 加长二维码时，显示端仍沿用旧编码容量；编码失败分支直接隐藏整个二维码层，用户只看到配网提示或原界面。

- `git blame`：`3355a492` 引入显示端 `max_qrcode_version=8`；`197e7b6b` 为配网与绑定追加 `v/d/k/n` 身份字段。`bde6ba5` 的首页/抽屉调整没有修改这个容量或配网事件钩子。
- 使用本地固件实际 `components/hutuji_qrcode/{esp_qrcode_main,esp_qrcode_wrapper,qrcodegen}.c`，配合实际 `BuildOpenHotspotWifiQrPayload()` / `BindIdentityQuery()` 和公开 P-256 测试点，复现正常配网码 **244B**、手动绑定码 **238B**：上限 V8 与 V10 均 `ESP_FAIL`，V11 起成功，输出 61×61 模块。
- 32 字节 SSID 全部 URL 转义后的 **328B** 配网码，需要 V13，输出 69×69 模块。原小屏 150px 预算只能给这种码每模块 1px。
- 45 秒被动串口观察只看到在线 Idle，未捕获用户此前的配网进入事件。上述根因由真实编码器复现确认，不把这段观察日志写成现场报错截图。

## 2. 为什么原有回归未挡住

此前覆盖了身份字段、签名/NVS、板级配网钩子和显示方法是否存在，却未把完整身份载荷交给固件实际编码器。编译与这些测试通过，不能证明该载荷能生成二维码。首页/抽屉的 LVGL 渲染也未覆盖配网层。

本轮先复现编码失败，再修显示端；没有删除公钥、设备 ID 或 nonce，没有回退 MAC-only 绑定。原身份契约仍见 `../protocol.md` §10.4.2。

## 3. 修复与防复发

| 优先级 | 机制 | 实际改动与验证 |
|---|---|---|
| P0 | 编码 | 上限改为 V13，保留 ECC Medium；小屏预算 160px、大屏 220px，至少每模块 2px，保留每侧 4 模块静区 |
| P0 | 失败处理 | 清掉旧图片，保留可见遮罩、中文失败提示和既有关闭回调；成功重试恢复图片，关闭恢复原入口 |
| P0 | 实际代码回归 | 新 `scripts/tests/test_provisioning_qr.py` 编译实际 C 编码器与实际显示函数，覆盖完整配网/绑定、最长转义 SSID、逐像素静区/整数缩放、空载荷/过长载荷/分配失败和显隐恢复；修复前 5 项失败、候选 5 项通过 |
| P1 | 真实 LVGL | 320×240 / 480×320 两尺寸运行实际布局、二维码函数、图片描述构造和设备 20px 字体，检查关闭按钮 ≥56px、图片/提示/按钮边界与相互不遮挡；点击关闭回调及失败后恢复通过 |
| P1 | 解码 | 两尺寸的配网、绑定、最长 SSID、失败后重试共 8 张原尺寸完整截图，经 OpenCV 5.0.0 `QRCodeDetectorAruco` 解码与输入逐字相符 |

解码限制保留：OpenCV 旧 `QRCodeDetector` 对部分完整截图未检出，裁出二维码并整数放大能解码；没有为追求其通过改变有效载荷或遮掉屏幕内容。Aruco 检测路径成功属于软件图片证据，手机相机、微信识别和实体屏幕仍要实测。

## 4. 同类范围与知识落点

配网与手动绑定共用 `LcdDisplay::ShowProvisioningQr()`，此次同时覆盖。身份生成失败是上游单独分支，本次没有捕获到此故障，不将其当作已复现根因。二维码字段、URL 编码或提示布局以后有改动，必须重复实际编码、像素边界与配网/绑定两入口验证，不能只做字符串存在性检查。

本仓没有 `.trellis/spec` 和配套模板，本记录与现有身份设计、协议共同维护；不新建工作流。产品改动仅 S3 显示文件、版本号和回归；Grbl 未修改。

## 5. 验证与交付台账

- 完整 S3 host：**225 项通过 / 58.634s**，`results/20260914-provisioning-qr/firmware-host.log`。
- 原编码器复现、红绿测试、安装文件 SHA：同目录 `encoder-repro.log`、`encoder-source-sha.json`、`host-before.log`、`host-after.log`、`install-manifest.json`。
- 实际 LVGL 截图、几何与解码：同目录 `render/{320x240,480x320}/`、`render/decode.json`、`render/source-sha.json`。
- 构建与提交：三板各215 CXX通过；格式收尾后量产板再增量编译1 CXX并重新打包，未覆盖此前三板证据。S3提交 `923e148fb16db5e37e5b92ef855ea79c74f6754d`，版本 `hutuji.1.0.19`，量产应用3073152B、SHA `119dd76475b3e5eaa4e869ffdb238a5971bc3f4461e2b205503a1e49d65734ef`。同目录 `hutuji.1.0.19/final-build.json` 钉最终来源与旧三板包；1.0.18回滚SHA为 `4b6dcc054bcf2d6bb9b041b04d7d4d4a671dd06adb94d1859d95d5c005dba388`。
- 量产安装：现读COM15的MAC、读旧应用前4096字节并对照1.0.18、备份NVS/otadata，再仅写otadata与app；两区Hash校验通过。watchdog复位后的USJ重枚举433按既有窄条件识别；串口 `Current version: hutuji.1.0.19`、10项Grbl指纹通过，官方设备状态再次确认1.0.19/idle/ready。记录为同目录 `hutuji.1.0.19/{flash-record.json,s3-flash.log,s3-boot-after-flash.log}` 和 `device-after-flash.log`。
- 本仓hub12层PASS，1535项MCP/146.80秒、e2e PASS、261处指纹，`hub-gate.{json,md}`。
- 现场边界：`acceptance-192730/` 被动观察300.1秒，未出现进入配网/QR ready/QR failed事件，无panic；用户长按显码、微信扫码和关闭回网的反馈仍待确认，不能把这段Idle日志当作二维码通过。

所有 `results/` 为本机证据，不随 Git 发布；脚本和渲染临时工程位于 `tmp/`，不混入固件发布包。
