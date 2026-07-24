# firmware/（② 待建）

S3 板固件补丁说明。补丁本体在 `D:/Users/xiaozhi-esp32`（fork 内新分支）实现：

- 设备侧 MCP 工具：`hutuji.draw(url)` / `hutuji.status()` / `hutuji.abort()`
- HTTP 下载 + G-code 校验 + USB Host（`usb_host_ch34x_vcp`，枚举写字机 CH340）流控转发

契约见 `../docs/protocol.md`（v0.2）。此目录放补丁清单、接线图、烧录与联调 SOP。

## ② 实施要点（2026-07-24 代码勘察结论）

- **挂点**：`main/boards/lichuang-dev/lichuang_dev_board.cc` 的 `InitializeTools()` 注册三个工具；参照 `main/boards/common/press_to_talk_mcp_tool.cc` 做成独立工具类。**板级行为不进核心模块**。
- **异步**：MCP 回调在主任务同步执行（`mcp_server.cc:550-559`）——回调立即返回 `"started"`，`xTaskCreate` 独立任务跑下载+转发；结果经 `Application::SendMcpMessage` 主动推送。
- **HTTP**：用 `Board::GetNetwork()->CreateHttp()` + `Read()` 流式循环（模板 `main/ota.cc:299-367`），不要用 `esp_http_client`。
- **USB Host**：`main/idf_component.yml` 加 `espressif/usb_host_ch34x_vcp`；参考 `main/boards/common/rndis_board.cc:46-55` 的 usbh 安装方式。GPIO19/20 空闲（控制台 UART0），octal PSRAM + 16MB flash 余量充足。
- **构建**：`python3 scripts/release.py lichuang-dev --name lichuang-dev`（或 `idf.py set-target esp32s3` + menuconfig 选 `BOARD_TYPE_LICHUANG_DEV_S3`）。IDF ≥ 5.5.2，仓 AGENTS.md 要求 v6.0.2。
- **Grbl 侧行为真值表**（转发状态机分支依据）：`Protocol.cpp:147-224`（ok/error/deferred）、`PaperSystem.cpp:722-1008`（换纸阻塞 + `[PaperStatus]`）。
