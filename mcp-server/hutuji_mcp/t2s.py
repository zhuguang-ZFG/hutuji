"""繁体→简体单字规范化（hutuji_write 云端文本专用）。

云端 LLM 生成的文章会零星混入繁体字（2026-09-06 用户纸面实证「繁体简体
混杂」）；奎享字库简繁字形俱全，不归一就照单全收写到纸上。映射表见
t2s_table.tsv（OpenCC TSCharacters 首选简化字，Apache-2.0）。

只覆盖单字映射，不做词汇级转换（「乾」一律→「干」这类边角接受误伤——
写字机文章的繁体混入本就是噪声，不是用户有意写繁体）。文档导入链
（doc_ingest/table_layout）刻意不经过这里：用户自己的文档保真优先。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_TABLE_PATH = Path(__file__).with_name("t2s_table.tsv")
_t2s: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _t2s
    if _t2s is None:
        table: dict[str, str] = {}
        try:
            with open(_TABLE_PATH, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("#") or "\t" not in line:
                        continue
                    t, s = line.rstrip("\n").split("\t", 1)
                    if t and s:
                        table[t] = s
        except OSError:
            # 表缺失/读坏（部署漏传 tsv）降级为不归一：写字工具照常可用、
            # 繁体照原文写——不能把整条 hutuji_write 炸掉（preflight 新鲜度
            # 只看 *.py+toml 的盲区兜底，见 deploy/preflight.sh watched 集）
            logger.warning("t2s 映射表不可用（%s），写字将不做繁→简归一", _TABLE_PATH)
            _t2s = {}
            return _t2s
        # 传递闭包：表里存在 薴→苧 而 苧→苎，逐字映射一遍会留下中间态繁体。
        # 步数封顶防表内成环（当前数据无环，封顶只为不让坏数据挂死服务）。
        for key, value in list(table.items()):
            for _ in range(4):
                nxt = table.get(value, value)
                if nxt == value:
                    break
                value = nxt
            table[key] = value
        _t2s = table
    return _t2s


def to_simplified(text: str) -> str:
    """逐字繁→简；无映射的字原样保留。"""
    table = _load()
    if not any(ch in table for ch in text):
        return text
    return "".join(table.get(ch, ch) for ch in text)
