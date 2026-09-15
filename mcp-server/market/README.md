# 精品图库数据根

契约：`docs/protocol.md` §10.4.23/§10.4.24。设计与部署记录：`docs/design/drawing-market-20260914.md`、`docs/release-readiness.md`。

首批 **467件、20类，每类20–27件**。本目录是Git真值，双机运行目录为 `/opt/hutuji/mcp-server/market`，由 `DRAW_PORTAL_MARKET_DIR` 指定；运行时只读，清单mtime变化触发重载。

## 内容与溯源

- `manifest.json`：运行时清单，逐件SVG/G-code/PNG/许可SHA与 `reviewed=true`。
- `sources.json`：官方固定提交、访问日期、许可来源、原始SVG/许可哈希及适用限制。
- `review.json`：500候选中7件硬闸拒收、26件画质/重复排除、467件最终预览逐件复核，含对照板哈希和标题修正。
- `svg/<类目>/<内容标识>.svg`、`preview/<类目>/<内容标识>.png`：可见单线轮廓与预览。
- `gcode/<类目>/<内容标识>.gcode`：入库时真实vpype和§5闸证据。发送与收藏按当前设备纸张重新转换，不直接发送此文件。
- `licenses/<source>.txt`：Twemoji图形CC-BY-4.0、Tabler MIT、MDI Apache-2.0许可全文；MDI不含品牌/标志。

## 入库与发布

外源只按 `sources.json` 的官方提交取SVG/元数据/许可，不执行来源代码。每件须过许可白名单、来源/哈希、SVG注入、curated_gate v4及最终画质复核。稀疏轮廓禁五点平滑，孔洞/遮挡/描边按实际可见区域处理；隐藏节点不回流，半透明拒收。SVG及预览视口覆盖右下边。文本产物先规范成LF再算SHA，原许可另留原字节哈希；本目录 `.gitattributes` 保证Windows检出也保留LF。

通过 `scripts/auto_deploy.py` 从明确Git提交的LF对象出包，支持既定中文类目和最多2048文件；逐文件SHA检查、备份与回滚。先验证数据完整，再设置目录并重启Portal。源码和本地预览复核不等于生产页面验收或用户纸面签核。
