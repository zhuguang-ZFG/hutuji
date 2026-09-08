from __future__ import annotations

import re

VERSION_RE = re.compile(r"^hutuji\.(\d+)\.(\d+)\.(\d+)$")


def parse_hutuji_version(s: str) -> tuple[int, int, int] | None:
    m = VERSION_RE.match((s or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_hutuji_version(candidate), parse_hutuji_version(current)
    if a is None or b is None:
        return False
    return a > b
