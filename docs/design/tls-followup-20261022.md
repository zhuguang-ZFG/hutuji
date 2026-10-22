# TLS 有限等待与 EXIF 启发式收口（2026-10-22）

用户原话「修复完全，部署验证刷机，上传推送」。承接审查：C22 之后 Hub `7278971` / S3 `f772706` / 1.0.47 已提交，但 EspSsl `Disconnect` 在接收线程不退出时无限 `WaitBits`；补丁表仍写三份；`is_photo_raster` 未走 EXIF 方向；readiness 仍写未提交。本批只修这四项并发布验证。

## 范围

- S3：`EspSsl::Disconnect` 最多 3×10s；超时置 `receive_abandoned_`、放弃 `tls_client_` 所有权且析构不删事件组，避免挂死或对仍在跑的接收线程 UAF。tracked 补丁 `esp-ml307-tls-socket-lifecycle.patch` 同步 header/cc，README 表登记为第四份。版本 `hutuji.1.0.48`。宿主 `test_tls_lifecycle.py` 增 mode=4（超时三次、零 destroy、原 fd 仍属本连接）。
- Hub：`pipeline._imread_oriented` 为 normalize 与照片启发式共用 `plugin=pillow, rotate=True`。小程序无产品 diff，上传沿用已提交 0.3.32。
- 双机只发 `draw-portal/draw_portal/pipeline.py`（EXIF 启发式）。Grbl 无 delta。目标板 Freenove MAC `b8:1f:3f:c3:70:bc`；另一 S3 排除。
- 不改机械、运动、换纸、身份、OTA 校验、§5 安全闸。

## 未宣称

有限等待后的 socket/事件组泄漏是刻意 fail-closed，不是回收完成。源码修复不等于历史 OTA 失败唯一根因。部署/刷机/上传证据写 readiness，未跑的项写未验证。`factory_paired`、纸面、老化、交付签核继续 Hold。

## 发布验证

- Hub `f487258` 只发 `draw-portal/draw_portal/pipeline.py`（blob SHA-256 `57f959cf5ea879cbda7529bfc3f81ede92630dd0`）；主备 EXIF 烟测 PASS，定向形状 `[80,48,3]`。preflight 主 25/34、备 26/34，已知差异保留。
- S3 产品 `0141de4`/1.0.48。CI glob `git apply --recount` 把无 `diff --git` 的双文件补丁下一文件头 `--- a/` 吞成删行，宿主/交付板均 fail。跟进 `66f2cc5` 只改 tracked 补丁头与 hunk 边界；Hutuji S3 宿主与 Freenove/立创交付板均 success。不升版本、不重刷。完整 Build Boards 仍排队，不以 queued 当绿。
- 目标 Freenove `b8:1f:3f:c3:70:bc` 单次 HTTP OTA：受理回 HTTP 504 unknown，未重发；后探 `ok/100`、`hutuji.1.0.48`、Idle/原点。镜像 3092672B SHA-256 `8e4e0cb995388fc72ab78a1f010091712cd2206f8ef178afb7782d0559e71a17`，主备 catalog/latest 与公网下载一致。
- 量产 Grbl 无产品 delta。COM7 刷前/刷后 48 项 PASS（脚本仍打印 COM3 标签）。
- 小程序 0.3.32 上传 303445B，AppID `wxbf3c1e0013b46343`；上传不等于平台发布。
- 本批未做运动控制 HIL。证据 `results/local-only/2026-10-22-tls-followup/verification.json`。
