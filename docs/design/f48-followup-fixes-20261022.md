# F48 三项修复（2026-10-22）

用户原话「同意修复，刷机部署验证，上传推送」。承接 [1.0.48 续审](three-repo-followup-review-20261022.md) 的 F48-01/02/03。Grbl 无产品 delta；另一 S3 排除。不改机械、运动、换纸、身份或 §5 闸。

## 范围

- S3 升 `hutuji.1.0.49`：放弃接收线程后泄漏整个 `EspSsl`，`Connect` 见 `receive_abandoned_` 直接失败。tracked 补丁 `esp-ml307-tls-socket-lifecycle.patch` 增拒绝重用；新增字母序最后的 `esp-ml307-z-tls-abandon-leak.patch`（`tcp.h` / `http_client.h` / `http_client.cc`）在 Close/Open 替换/析构路径 `tcp_.release()`。宿主 `test_tls_lifecycle.py` 源码断言覆盖 leak 与拒绝重用。
- Hub：PNG 魔数直传先 `normalize_raster_to_png(..., line_art=False)`；闸内带 EXIF 的 PNG 按显示方向重编码、禁止按 4.8Mpx 放大。OTA 600s 过期仅在设备可达、已离开 upgrading、版本未对上目标时生效；查询失败或仍 upgrading 不放锁。出图闸先 `get_ota_status` 再 `is_upgrading`。小程序 `0.3.33`。
- 双机精确发布 `pipeline.py` / `firmware_ota.py` / `app.py`。目标 Freenove MAC `b8:1f:3f:c3:70:bc` 单次 HTTP OTA；504 不重发。

## 验证（软件，本机）

- Portal `test_pipeline_raster.py` + `test_firmware_ota.py`：73 passed。
- S3 `scripts/tests/test_tls_lifecycle.py`：6 passed（含 leak 源码断言）。
- 补丁在隔离树相对 vendor-probe（已含 wakeup）叠 keepalive/ssl-rx/lifecycle/z-patch 后与活树五文件逐字节相符；每文件均有 `diff --git` 头。

部署、刷机、上传、门禁与 CI 回执写入 `docs/release-readiness.md` 与本机 `results/local-only/2026-10-22-f48-followup/`。未完成项写未验证。

## 未宣称

泄漏 EspSsl/事件组是刻意 fail-closed，不是回收完成。源码修复不等于历史 OTA 失败唯一根因，也不等于已上板构造出 UAF。PNG EXIF 回归是合成夹具，不是手机实拍纸面。`factory_paired`、纸面、老化、交付签核继续 Hold。
