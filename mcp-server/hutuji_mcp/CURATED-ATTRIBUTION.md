# 精选单线 SVG 库署名

本产品 **curated** 精选库中的 SVG 资产由 **hutuji 离线流水线**生成并入库
（`mcp-server/scripts/curated_prep.py`：生图 → 描摹/缝合 → 自动闸门 →
`manifest.json`），**不是** Google **Quick, Draw!** 数据集或其衍生笔画。

- 类目映射复用 `quickdraw_categories.json`（中文/别名 → 英文类名），仅作查找语义对齐；
  curated 目录下的 `.svg` 为独立定稿资产，不伪装成 QD ndjson。
- 生成阶段遵守所用 **生图后端服务条款**（如 Agnes / 商汤 / imagefree / 硅流等
  配置面所指向的后端）；入库样本已通过自动闸门，人工抽检为质量复核而非许可来源变更。
- 当 `HUTUJI_CURATED_REPLACE_QD=1` 且 `manifest.ready=true` 时，**已覆盖类目**
  的运行时出图走 curated 路径，**不再分发** 对应主题的 Quick, Draw! 涂鸦笔画；
  QD 包若仍驻留磁盘（如 `/opt/hutuji/quickdraw/`）仅作回滚备用，不构成对用户侧的 QD 再分发。

Quick, Draw! 数据集本身的 CC BY 4.0 署名见同目录 `QUICKDRAW-ATTRIBUTION.md`。
