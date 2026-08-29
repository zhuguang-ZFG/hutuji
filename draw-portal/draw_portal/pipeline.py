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


def normalize_raster_to_png(raw: bytes) -> bytes:
    """WebP/GIF/BMP/PNG/JPEG 统一为 PNG 字节。"""
    import imageio.v3 as iio

    arr = iio.imread(raw)
    buf = io.BytesIO()
    iio.imwrite(buf, arr, extension=".png")
    return buf.getvalue()


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
