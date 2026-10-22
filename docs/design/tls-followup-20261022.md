# TLS 有限等待与 EXIF 启发式收口（2026-10-22）

用户原话「修复完全，部署验证刷机，上传推送」。承接审查：C22 之后 Hub `7278971` / S3 `f772706` / 1.0.47 已提交，但 EspSsl `Disconnect` 在接收线程不退出时无限 `WaitBits`；补丁表仍写三份；`is_photo_raster` 未走 EXIF 方向；readiness 仍写未提交。本批只修这四项并发布验证。

## 范围

- S3：`EspSsl::Disconnect` 最多 3×10s；超时置 `receive_abandoned_`、放弃 `tls_client_` 所有权且析构不删事件组，避免挂死或对仍在跑的接收线程 UAF。tracked 补丁 `esp-ml307-tls-socket-lifecycle.patch` 同步 header/cc，README 表登记为第四份。版本 `hutuji.1.0.48`。宿主 `test_tls_lifecycle.py` 增 mode=4（超时三次、零 destroy、原 fd 仍属本连接）。
- Hub：`pipeline._imread_oriented` 为 normalize 与照片启发式共用 `plugin=pillow, rotate=True`。小程序无产品 diff，上传沿用已提交 0.3.32。
- 双机只发 `draw-portal/draw_portal/pipeline.py`（EXIF 启发式）。Grbl 无 delta。目标板 Freenove MAC `b8:1f:3f:c3:70:bc`；另一 S3 排除。
- 不改机械、运动、换纸、身份、OTA 校验、§5 安全闸。

## 未宣称

有限等待后的 socket/事件组泄漏是刻意 fail-closed，不是回收完成。源码修复不等于历史 OTA 失败唯一根因。部署/刷机/上传证据写 readiness，未跑的项写未验证。`factory_paired`、纸面、老化、交付签核继续 Hold。
