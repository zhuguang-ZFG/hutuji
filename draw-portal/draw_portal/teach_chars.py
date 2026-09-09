"""笔顺模式：从自由文本按序抽出 CJK 汉字。"""

from __future__ import annotations

import re

TEACH_MAX_CHARS = 20

_CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")


def extract_teach_chars(text: str) -> tuple[str, bool]:
    """按出现顺序抽出 CJK，最多 ``TEACH_MAX_CHARS`` 个。

    返回 ``(chars, input_truncated)``；无汉字时 ``chars`` 为空串。
    """
    found = _CJK_RE.findall(text)
    input_truncated = len(found) > TEACH_MAX_CHARS
    return "".join(found[:TEACH_MAX_CHARS]), input_truncated
