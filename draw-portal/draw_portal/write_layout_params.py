from __future__ import annotations

from hutuji_mcp.write_text import WriteLayout, WriteTextError


class WriteLayoutParamsError(ValueError):
    pass


def parse_optional_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError as exc:
        raise WriteLayoutParamsError("版式参数无效") from exc


def resolve_article_layout(
    *, size_mm: float | None = None, line_spacing: float | None = None
) -> WriteLayout:
    kwargs: dict = {}
    if size_mm is not None:
        kwargs["size_mm"] = float(size_mm)
    if line_spacing is not None:
        kwargs["line_spacing"] = float(line_spacing)
    try:
        layout = WriteLayout(**kwargs)
        layout.validate()
        return layout
    except WriteTextError as exc:
        msg = str(exc)
        if "字号" in msg:
            raise WriteLayoutParamsError(msg) from exc
        if "行距" in msg:
            raise WriteLayoutParamsError(msg) from exc
        raise WriteLayoutParamsError(msg) from exc
