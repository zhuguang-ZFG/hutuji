# 小派 OTA 固件发版清单（批次）

> 一批一表。与 `release-manifest.md` 一起归档。  
> **nginx 部署**：京东云用仓内 `deploy/nginx-hutuji.conf` 的 `location /firmware/`；**阿里云 gray conf 禁止整份覆盖**，须按 `docs/deployment.md` 双机差异手工插入同口径片段后 `systemctl restart nginx`（非 reload）。

## 批次

| 字段 | 值 |
|------|-----|
| 板型 | |
| 版本（hutuji.x.y.z） | |
| 发版负责人 | |
| 日期 | |
| xiaozhi 上游 commit / 备注 | |

## 发版前（运营机）

- [ ] `xiaozhi.bin` 来自指定 `release.py` 构建，非旧 zip 假绿产物
- [ ] 版本串与编译进固件的 `esp_app_get_description()->version` 一致
- [ ] `python scripts/publish_s3_firmware.py --board … --version … --bin … --catalog … --outdir …` 成功
- [ ] 核对 `manifest.json` / `latest.json` 的 `sha256`、`size`、`url` 与本地 bin 一致
- [ ] catalog 中该 board 的 `relpath` 指向新版本目录

## 静态仓部署

- [ ] 整目录 scp 到生产 `/var/hutuji/firmware/`（京东云 / 阿里云主节点，按现行分发策略）
- [ ] 公网 `GET /firmware/s3/{board}/latest.json` → 200，字段含 `device,board,version,url,sha256,size,notes`
- [ ] 公网 `GET` manifest 与 bin → 200，`Content-Length` 与 manifest 一致
- [ ] `POST /firmware/…` → 405/403（仅 GET/HEAD）
- [ ] portal `DRAW_PORTAL_FIRMWARE_CATALOG_PATH` 已同步新 catalog 并 restart draw-portal

## HIL：正常升级

- [ ] 绑定设备当前版 < 新发版；小程序「检查更新」显示有新版本
- [ ] 用户二次确认后升级；屏显进度；成功后 reboot
- [ ] 刷机/串口：`hutuji.status` 含 `firmware_version":"hutuji.x.y.z"` 与新版本一致
- [ ] **升后联网冒烟**：WiFi 连上、Telnet 可达、语音问「状态」/`hutuji.status` 正常、可完成一次轻量出图或 status 探活

## HIL：故意坏包

### A. 下载/校验失败（不切槽）
- [ ] 准备同 board 的 **损坏/缺失 bin**（截断、篡改或 404），仅 staging；勿长期污染生产 latest
- [ ] 设备触发升级 → 下载/校验失败 → **不切槽**、不 reboot 到新槽
- [ ] `hutuji.status` / 小程序显示明确 **failed** 态，可重试或放弃
- [ ] 旧版本仍可正常联网

### B. 切槽后启动失败回滚（PENDING_VERIFY / F6）
- [ ] 用 `HUTUJI_OTA_POISON_ROLLBACK=ON` 构建临时版本（启动即 `esp_ota_mark_app_invalid_rollback_and_reboot`），**禁止量产发版**
- [ ] 临时将 catalog/latest 指到毒包 → 网升 → 设备离线后再连
- [ ] 复验 `firmware_version` **回到升前稳定版**、可 `ota_check`
- [ ] **立刻**把 latest/catalog 指回稳定版；毒包目录标 WITHDRAWN

## HIL：错板拒升

- [ ] 对 **A 板型设备** 仅发布 **B 板型** manifest（或 catalog 指向错板路径）
- [ ] portal check → `reason: board_mismatch` 或设备侧拒收（board 字段不匹配）
- [ ] 设备 **不得** 开始下载错板 bin

## 回滚

- [ ] catalog / latest 指回上一稳定版本的记录已备
- [ ] 回滚后公网 latest.json 与 portal check 行为已复验

## 签字

| 角色 | 姓名 | 日期 |
|------|------|------|
| 发版 | | |
| HIL | | |
