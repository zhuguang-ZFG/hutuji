# 繁→简映射表署名（Apache License 2.0）

`hutuji_write` 云端文本繁→简规范化的单字映射表（`t2s_table.tsv`，3222 对）
派生自 **OpenCC（Open Chinese Convert）** 项目，许可
[Apache License 2.0](https://github.com/BYVoid/OpenCC/blob/master/LICENSE)。

- 官方仓库：https://github.com/BYVoid/OpenCC
- 上游文件：`data/dictionary/TSCharacters.txt`（繁→简单字首选映射）
- 版本：OpenCC master（访问/导出日期 2026-09-06，经 GitHub raw 取得）
- 本地处理：仅抽出单字符对（多字词组未收），未改动映射内容；加载期在
  `t2s.py` 做传递闭包（上游表内 薴→苧 而 苧→苎 这类链式映射一次到底）。
- 用途：`hutuji_write` 云端文章繁简混杂归一；文档导入链刻意保真不使用。
