"""运营后台可覆盖的 MCP 绘图限流键与校验边界。"""
from __future__ import annotations

DRAW_LIMIT_KEYS: tuple[str, ...] = (
    "draw_max_concurrent",
    "draw_per_second",
    "draw_per_minute",
    "vpype_max_concurrent",
)

DRAW_LIMIT_BOUNDS: dict[str, tuple[int, int]] = {
    "draw_max_concurrent": (1, 32),
    "draw_per_second": (1, 32),
    "draw_per_minute": (1, 600),
    "vpype_max_concurrent": (1, 16),
}


def validate_draw_limit_overrides(raw: dict[str, object]) -> dict[str, int]:
    """校验并规范化覆盖项；空 dict 表示全部清除。"""
    out: dict[str, int] = {}
    for key, value in raw.items():
        if key not in DRAW_LIMIT_BOUNDS:
            raise ValueError(f"未知限流键 {key!r}")
        if value is None or value == "":
            continue
        try:
            n = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} 必须是整数") from exc
        lo, hi = DRAW_LIMIT_BOUNDS[key]
        if not lo <= n <= hi:
            raise ValueError(f"{key} 必须在 {lo}..{hi}，当前 {n}")
        out[key] = n
    return out
