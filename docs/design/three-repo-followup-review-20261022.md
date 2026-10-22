# 1.0.48 发布后只读续审（2026-10-22）

用户要求「继续深入codereview」。本轮产品源码和正式测试只读，承接 TLS/EXIF 1.0.48 发布后的相邻路径；临时探针与审查文档不进入产品。未调用设备、付费接口或生产服务，未部署、刷机、上传、提交或推送。

| 仓库 | 当前审查提交 | 覆盖范围 |
|---|---|---|
| Hub | `bd97821` | `pipeline.py` 有向解码之后的 PNG 直传/缩图；`firmware_ota` 504/uncertain 占位与 600s 过期 |
| S3 | 产品 `0141de4` / CI 补丁 `66f2cc5`，1.0.48 | `EspSsl` 放弃所有权之后的 HttpClient 析构与连接替换 |
| 量产 Grbl | `a268e05c` | C22 完整行领取是否仍在；`client_command_owners` 是否被消费 |
| 普通 Grbl（对照） | `e5d7199a` | 同款领取点，无新 delta |

接手四个产品仓已跟踪文件均无未提交修改；既有未跟踪资产保留。设备现态与实体 Hold 仍只看 readiness，本轮没有重新探活。

## F48-01 / P1：放弃接收线程后仍销毁 EspSsl，宿主测没覆盖这条

位置：xiaozhi `managed_components/78__esp-ml307/src/esp/esp_ssl.cc` 的 `Disconnect` 放弃分支与析构；`http_client.cc` 的 `Open`/`Close`/`~HttpClient`；`hutuji_job.cc` 的 `ReleaseFetchClient`。

1.0.48 把无限 `WaitBits` 收成最多 3×10s；超时置 `receive_abandoned_`、把 `tls_client_` 置空且析构不删事件组，避免对仍在跑的接收线程 `esp_tls_conn_destroy`。这只保护了 TLS 句柄和事件组堆对象。`EspSsl` 这个 C++ 对象本身仍由 `HttpClient::tcp_`（`unique_ptr<Tcp>`）持有。

生产释放顺序是固定的：

1. `Job::ReleaseFetchClient` 先 `Close()` 再 `fetch_http_.reset()`。
2. `HttpClient::Close` 调 `tcp_->Disconnect()`。放弃路径在这里返回，接收任务和 `disconnect_callback_` 仍指向活着的 `this`。
3. `reset()` 进入 `~HttpClient`：此时 `connected_` 已是 false，不再 `Close`，随后成员 `unique_ptr` 析构 `EspSsl`。
4. `~EspSsl` 见 `tls_client_ == nullptr` 与 `receive_abandoned_ == true`，跳过事件组删除后释放对象。
5. 接收任务稍后从 `esp_tls_conn_read` 返回，访问已释放的 `connected_` / `disconnect_callback_` / `event_group_`。`HttpClient::Open` 在不可复用时执行 `tcp_ = CreateSsl(...)` 是同一条销毁路径。

宿主 `scripts/tests/test_tls_lifecycle.py` mode=4 只断言「三次等待、零 destroy、原 fd 仍属本连接」，栈上的 `EspSsl ssl` 一直活到进程结束。它证明了「不要 destroy TLS」，没有证明「不要 delete EspSsl」。因此 1.0.48 的 fail-closed 在真实 HTTP 所有权模型下不成立。

建议：放弃后必须泄漏整个 `EspSsl`（`tcp_.release()` 且不再 `Connect` 该对象），`Connect` 见 `receive_abandoned_` 直接失败；宿主增 mode：放弃后销毁对象而接收任务仍视为存活，旧码必须红。不要在放弃对象上重建连接并 `ClearBits` 同一事件组，否则旧任务的 EXIT 会让新连接误判接收已退出。

**已从源码顺序复现，未上板构造 30s 卡住。** 不是已发布 1.0.48 的实机事故记录。

## F48-02 / P2：PNG 魔数直传仍不消费 EXIF 方向

位置：Hub `draw-portal/draw_portal/pipeline.py` 的 `process_upload` PNG 魔数分支、`shrink_oversized_png`、`strip_corner_watermark`。

1.0.48 只让 `normalize_raster_to_png` 与 `is_photo_raster` 共用 `_imread_oriented(..., rotate=True)`。`process_upload` 见 PNG 魔数就跳过 normalize，直接 `process_upload_png` → `shrink_oversized_png` → 默认 `iio.imread`。

本机合成 EXIF Orientation=6 的 48×80 PNG（魔数合法、带方向块）：

| 入口 | 形状 | 与 `ImageOps.exif_transpose` |
|---|---|---|
| `_imread_oriented` / `normalize_raster_to_png` | 80×48 | 相符 |
| `shrink_oversized_png` | 48×80 | 不相符，等于未转置原图像素 |
| `process_upload` 是否跳过 normalize | 是 | — |

因此 JPEG 照片已按显示方向归一，同方向的 PNG 仍按文件像素描摹。`is_photo_raster` 现已有向，启发式与描摹像素会再分叉。`strip_corner_watermark` 同样无 `rotate=True`，当前生产调用只在测试里，不升为独立缺陷。

建议：所有进描摹/缩图的栅格先走 `_imread_oriented`，或 PNG 也先 normalize 成无 EXIF PNG；回归补 Orientation 1–8 的 PNG 直传，且重复 normalize 不再旋转。未宣称手机实拍 PNG 已出纸错误。

## F48-03 / P2：uncertain OTA 在设备离线刷写时 600s 后放开门户锁

位置：`firmware_ota.py` 的 `_OTA_STALE_SECONDS = 600`、`get_ota_status`、`is_upgrading`；`store.expire_firmware_ota_session`。

504/读超时保持 `unknown`/`uncertain` 是已证实设计，本轮 1.0.48 单次 OTA 也走过这条。过期把 `starting/uncertain/upgrading` 写成 `failed/status_timeout`。`is_upgrading` 只认前三个状态，过期后出图闸与再次 `start_upgrade` 都放开。设备刷写中常离线，`get_ota_status` 刷新不了 `updated_at`，十分钟后门户不再视为升级中。版本对账仍能把 `status_timeout` 收成 ok，所以成功升级不会永久卡死；空窗里的二次 `ota_start` 或出图依赖设备占用/忙拒绝，门户不再互斥。

建议：过期不要单凭墙钟；目标版本未对上且最近一次设备查询失败时延长占位，或过期后仍按 target 拒绝出图直到对账/人工解除。本轮未改、未复测生产 504。

## 已核对无新缺陷

- 量产/普通 Grbl 完整行仍在 Eol 后用 `ClientCommandOwner` 领取，C22 提交点还在。`client_command_owners` 只做加减，固件无人读，属记账残留，不单独立项。
- JPEG/`is_photo_raster` 有向解码与 1.0.48 宿主 mode=4「不 destroy TLS」仍成立；本轮不回退它们。
- `strip_corner_watermark` 无生产调用点。

## 用户授权修复

用户原话「同意修复，刷机部署验证，上传推送」。三项均已落代码与宿主回归，版本 1.0.49 / 小程序 0.3.33。实现、边界与发布回执见 [F48 修复](f48-followup-fixes-20261022.md)。本审查文档保留发现时的只读结论，不把源码修复写成审查当时已上板。
