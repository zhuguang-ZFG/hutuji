# Quick, Draw! 数据集署名（CC BY 4.0）

本产品简笔画本地缓存基于 **Quick, Draw! dataset by Google, Inc.**，许可
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。

- 官方仓库：https://github.com/googlecreativelab/quickdraw-dataset
- 数据获取：官方简化版 ndjson（`storage.googleapis.com/quickdraw_dataset/full/simplified/`）
- 本地处理（`mcp-server/scripts/quickdraw_prep.py`，同参数同产出可复现）：
  仅保留官方 `recognized=true` 条目、每类固定种子蓄水池采样、丢弃笔画时间通道，
  未对笔迹几何做任何修改。
- 类目映射（中文语音主题 → 官方类目）为本项目自编，见
  `quickdraw_categories.json`。
