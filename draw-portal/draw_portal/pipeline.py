from __future__ import annotations

import io
import shutil
from dataclasses import dataclass
from pathlib import Path

from draw_portal.upload_kinds import detect_upload_kind


@dataclass(frozen=True)
class PipelineResult:
    lines: int
    bytes: int
    pen_lifts: int
    svg_path: Path
    gcode_path: Path
    preview_path: Path


@dataclass(frozen=True)
class DocPipelineResult:
    """文档导入（docx/xlsx/pptx/csv/txt）的多页产物；单页字段 = 第 1 页兼容值。"""

    kind: str
    mode: str  # article | tables
    page_count: int
    truncated: bool  # 达到页数上限截断（protocol §4.2 设备上限 20 页）
    missing_chars: list[str]
    lines: int  # 全部页合计
    bytes: int
    page_lines: list[int]  # 每页 G-code 行数（发送端错峰 TTL 估时输入）
    svg_path: Path  # 第 1 页（draw.svg/draw.gcode/preview.png 兼容旧保存链）
    gcode_path: Path
    preview_path: Path


def normalize_raster_to_png(raw: bytes) -> bytes:
    """WebP/GIF/BMP/PNG/JPEG 统一为 PNG 字节。"""
    import imageio.v3 as iio

    arr = iio.imread(raw)
    buf = io.BytesIO()
    iio.imwrite(buf, arr, extension=".png")
    return buf.getvalue()


def process_upload_doc(raw: bytes, kind: str, job_dir: Path, *, gfont_dir: str) -> DocPipelineResult:
    """文档 → 解析 → 排版 → 每页 SVG/G-code/预览（多页，pages/ 目录 + pages.json）。

    版式全部自排（normalize=False 跳过 vpype layout）：文章走 write_text
    （与语音 hutuji_write 同链），表格走 table_layout（格线+格内字同页笔画）。
    两种模式的每页产物都过同一条 §5 闸（_validate_svg + validate_gcode）。
    gfont_dir 由 app 层在路由前验空（未配置 = 503），此处断言即可。
    """
    import json as _json

    from hutuji_mcp import doc_ingest, table_layout, write_text
    from hutuji_mcp.article_pages import ARTICLE_MAX_PAGES
    from hutuji_mcp.gcode_gen import svg_to_gcode, validate_gcode
    from hutuji_mcp.llm_svg import _validate_svg

    assert gfont_dir, "gfont_dir 为空应先在 app 层 503"
    job_dir.mkdir(parents=True, exist_ok=True)
    doc = doc_ingest.parse_document(raw, kind)

    font = write_text.load_font(gfont_dir, None)
    metrics = write_text.measure_font(font)
    if doc.mode == "article":
        # 与语音写字链同参数（WriteLayout 默认值即生产值）
        laid = write_text.layout_text(
            "\n".join(doc.paragraphs), font, metrics, write_text.WriteLayout(),
            max_pages=ARTICLE_MAX_PAGES,
        )
    else:
        # 表格：8mm 字号/1.25 行距/0.5 字距——190mm 页区塞下常见成绩表的
        # 经验下限（更小到 3.5mm 地板由 table_layout 自收紧，再不行报错）
        laid = table_layout.layout_tables(
            doc.tables, font, metrics,
            write_text.WriteLayout(size_mm=8.0, line_spacing=1.25, char_gap_mm=0.5),
            max_pages=ARTICLE_MAX_PAGES,
        )

    pages_dir = job_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    page_lines: list[int] = []
    total_lines = 0
    total_bytes = 0
    for i, page in enumerate(laid.pages, start=1):
        svg = write_text.page_to_svg(page)
        _validate_svg(svg)
        svg_path = pages_dir / f"page_{i:03d}.svg"
        gcode_path = pages_dir / f"page_{i:03d}.gcode"
        preview_path = pages_dir / f"page_{i:03d}.png"
        svg_path.write_text(svg, encoding="utf-8")
        stats = svg_to_gcode(svg_path, gcode_path, normalize=False)
        gcode_raw = gcode_path.read_bytes()
        validate_gcode(gcode_raw.decode("ascii"), total_bytes=len(gcode_raw))
        preview_path.write_bytes(write_text.page_to_preview_png(page))
        page_lines.append(stats.lines)
        total_lines += stats.lines
        total_bytes += stats.bytes

    # 第 1 页复制为单页兼容名：saved_preview 保存链与后台 job 展示只认
    # draw.gcode/preview.png；多页整篇发送走 pages.json，不碰这条旧链。
    first_svg = pages_dir / "page_001.svg"
    shutil.copyfile(first_svg, job_dir / "draw.svg")
    shutil.copyfile(pages_dir / "page_001.gcode", job_dir / "draw.gcode")
    shutil.copyfile(pages_dir / "page_001.png", job_dir / "preview.png")

    meta = {
        "kind": doc.kind,
        "mode": doc.mode,
        "page_count": len(laid.pages),
        "truncated": laid.truncated,
        "missing_chars": laid.missing_chars,
        "page_lines": page_lines,
    }
    (job_dir / "pages.json").write_text(
        _json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    return DocPipelineResult(
        kind=doc.kind,
        mode=doc.mode,
        page_count=len(laid.pages),
        truncated=laid.truncated,
        missing_chars=laid.missing_chars,
        lines=total_lines,
        bytes=total_bytes,
        page_lines=page_lines,
        svg_path=job_dir / "draw.svg",
        gcode_path=job_dir / "draw.gcode",
        preview_path=job_dir / "preview.png",
    )


def process_upload_svg(svg_bytes: bytes, job_dir: Path) -> PipelineResult:
    from hutuji_mcp.curated import _preview_from_svg
    from hutuji_mcp.gcode_gen import svg_to_gcode, validate_gcode
    from hutuji_mcp.llm_svg import _validate_svg

    job_dir.mkdir(parents=True, exist_ok=True)
    text = svg_bytes.decode("utf-8")
    _validate_svg(text)

    svg_path = job_dir / "draw.svg"
    gcode_path = job_dir / "draw.gcode"
    preview_path = job_dir / "preview.png"
    svg_path.write_text(text, encoding="utf-8")
    preview_path.write_bytes(_preview_from_svg(text))

    stats = svg_to_gcode(svg_path, gcode_path)
    raw = gcode_path.read_bytes()
    validate_gcode(raw.decode("ascii"), total_bytes=len(raw))

    return PipelineResult(
        lines=stats.lines,
        bytes=stats.bytes,
        pen_lifts=stats.pen_lifts,
        svg_path=svg_path,
        gcode_path=gcode_path,
        preview_path=preview_path,
    )


def process_upload_png(png_bytes: bytes, job_dir: Path) -> PipelineResult:
    """PNG → 描摹 → SVG 校验 → vpype G-code → validate_gcode。"""
    from hutuji_mcp.bitmap_svg import (
        polylines_to_preview_png,
        polylines_to_svg,
        trace_bitmap_to_polylines,
    )
    from hutuji_mcp.gcode_gen import svg_to_gcode, validate_gcode
    from hutuji_mcp.llm_svg import _validate_svg

    job_dir.mkdir(parents=True, exist_ok=True)
    upload_path = job_dir / "upload.png"
    upload_path.write_bytes(png_bytes)

    polys, w, h = trace_bitmap_to_polylines(png_bytes)
    svg = polylines_to_svg(polys, w, h)
    _validate_svg(svg)
    preview = polylines_to_preview_png(polys, w, h)

    svg_path = job_dir / "draw.svg"
    gcode_path = job_dir / "draw.gcode"
    preview_path = job_dir / "preview.png"
    svg_path.write_text(svg, encoding="utf-8")
    preview_path.write_bytes(preview)

    stats = svg_to_gcode(svg_path, gcode_path)
    raw = gcode_path.read_bytes()
    validate_gcode(raw.decode("ascii"), total_bytes=len(raw))

    return PipelineResult(
        lines=stats.lines,
        bytes=stats.bytes,
        pen_lifts=stats.pen_lifts,
        svg_path=svg_path,
        gcode_path=gcode_path,
        preview_path=preview_path,
    )


def process_upload(
    raw: bytes,
    *,
    content_type: str,
    filename: str,
    job_dir: Path,
) -> PipelineResult:
    kind = detect_upload_kind(raw, content_type, filename)
    if kind == "svg":
        return process_upload_svg(raw, job_dir)
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in {"image/png", "image/jpeg", "image/jpg"}:
        return process_upload_png(raw, job_dir)
    return process_upload_png(normalize_raster_to_png(raw), job_dir)


def copy_saved_preview(
    *,
    job_dir: Path,
    saved_dir: Path,
    saved_id: str,
) -> Path:
    """把 job 预览复制到用户 saved_previews 域（无 TTL）。"""
    saved_dir.mkdir(parents=True, exist_ok=True)
    dst = saved_dir / f"{saved_id}.png"
    shutil.copy2(job_dir / "preview.png", dst)
    return dst
