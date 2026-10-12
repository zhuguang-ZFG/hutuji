from __future__ import annotations

import io
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from draw_portal.upload_kinds import detect_upload_kind


# 解码前按文件头拦「解码即 OOM」级像素炸弹。app.py 的 _MAX_UPLOAD_BYTES=4MB 字节上限
# 挡不住高压缩比图幅：纯色/低熵 JPEG/PNG/WebP 可 4MB 文件头声明数亿像素，imageio/skimage
# 全量解码会在 Pillow 自带 ~179Mpx 闸触发前就吃垮 worker 内存（RGB 解码 + rgb2gray float64
# 峰值 ~11 字节/像素）。门户 /api/draw/upload 直收 JPEG/WebP/GIF/BMP 原件，用 Pillow 惰性
# 读头（只解析尺寸、不解码像素）在 iio.imread 之前拦下。
#
# 阈值刻意远高于 bitmap_svg 的 5Mpx 描摹闸：门户的 shrink_oversized_png 会把 5–5.9Mpx 手机
# 照片**等比缩进**描摹闸（2026-09-08 生产修复：客户端按 4MB 字节压缩、不按像素，12MP 照片压完
# 仍 5.9Mpx），若在此按 5Mpx 拒就把合法上传 400 掉、UX 回退。本闸只负责 OOM 级别的巨幅拒绝，
# 5Mpx 以下/以上但仍可解码的图继续走既有缩放链。合法 4MB 图经验上 ≤~40Mpx，64Mpx 闸干净分离
# 合法上传与数百 Mpx~Gpx 炸弹；单边 32768 另拦细长退化图（如 40000×3，总像素虽小但单行巨量分配）。
_MAX_RASTER_PIXELS = 64_000_000  # 64Mpx：解码峰值 ~700MB，仍远低于合法图上界之上的炸弹区
_MAX_RASTER_SIDE = 32768         # 对齐 hutuji_mcp.bitmap_svg._MAX_IMAGE_SIDE


def _guard_raster_dimensions(raw: bytes) -> None:
    """位图解码前按文件头校验尺寸；确认超限才抛 ValueError（上层映射 400）。

    读不出尺寸（非图片/头部损坏）一律静默返回，交由后续解码链按原逻辑报错，
    不新增 fail 面、不改既有「图片转换失败」路径的状态码。
    """
    from PIL import Image

    try:
        with Image.open(io.BytesIO(raw)) as im:
            width, height = im.size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        # Pillow 自带闸（警告 ~89Mpx / 报错 2×=179Mpx）在读头时判巨幅——直接视作炸弹
        # 拒绝，不回落解码（否则 iio.imread 同样炸且已占满内存）。警告仅在调用方把
        # warnings 升为 error 时才成异常，一并兜住。
        raise ValueError("图片像素过多（超出安全解码上限，疑似解压炸弹）") from None
    except Exception:
        # 读不出尺寸（非图片/头部损坏）：交后续解码链按原逻辑报错，不新增 fail 面。
        return
    if width >= _MAX_RASTER_SIDE or height >= _MAX_RASTER_SIDE:
        raise ValueError(
            f"图片单边过大（{width}x{height}，单边上限 {_MAX_RASTER_SIDE}）"
        )
    if width * height > _MAX_RASTER_PIXELS:
        raise ValueError(
            f"图片像素过多（{width}x{height}="
            f"{width * height / 1e6:.1f}Mpx，上限 {_MAX_RASTER_PIXELS / 1e6:.1f}Mpx）"
        )


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


def normalize_raster_to_png(raw: bytes, *, line_art: bool = True) -> bytes:
    """WebP/GIF/BMP/PNG/JPEG 统一为 PNG 字节。

    照片类栅格（近白像素占比 <0.5）走 Canny 边缘提取转单线稿，
    人像/场景相似度远优于亮度阈值+骨架化（2026-09-07 用户人像实证：
    阈值链输出不成形轮廓，Canny σ2.2 输出可辨识人脸+口罩+帽子）。
    参照 Inkscape trace bitmap CANNY 模式与奎享 EdgeTab 的行业共识。

    line_art=False 只做格式归一、跳过 Canny——两段式精修要把「原始照片」
    喂给 i2i 模型，不是把 Canny 结果喂进去（2026-09-08 拍板 UX）。
    """
    import imageio.v3 as iio

    _guard_raster_dimensions(raw)
    arr = iio.imread(raw)
    if line_art and _looks_like_photo(arr):
        arr = _photo_to_line_art(arr)
    buf = io.BytesIO()
    iio.imwrite(buf, arr, extension=".png")
    return buf.getvalue()


def is_photo_raster(raw: bytes) -> bool:
    """上传原件是否「照片」（近白像素 <0.5）；与 normalize 的照片启发式同源。

    两段式精修用它决定要不要起 i2i 后台任务。非栅格（SVG 字节）/解码失败
    一律 False——判不出就维持现状（只有 Canny 结果），不为可用性特性加
    fail-closed 面。
    """
    import imageio.v3 as iio

    try:
        _guard_raster_dimensions(raw)
        arr = iio.imread(raw)
        return _looks_like_photo(arr)
    except Exception:
        return False


def _looks_like_photo(arr) -> bool:
    """照片 vs 线稿：线稿近白像素占比高（>0.5），照片充满中间调。"""
    import numpy as np
    from skimage import color

    rgb = arr[..., :3] if arr.ndim == 3 else np.stack([arr] * 3, axis=-1)
    gray = color.rgb2gray(rgb)
    return float((gray > 0.9).mean()) < 0.5


def _photo_to_line_art(arr):
    """照片 → 黑线白底 PNG 图（uint8）：CLAHE 对比度归一 → Canny → 去斑点。"""
    import numpy as np
    from skimage import color, exposure, feature, morphology, util

    rgb = arr[..., :3] if arr.ndim == 3 else np.stack([arr] * 3, axis=-1)
    gray = color.rgb2gray(rgb)
    g = exposure.equalize_adapthist(gray, clip_limit=0.02)
    edges = feature.canny(g, sigma=2.2, low_threshold=0.10, high_threshold=0.20)
    edges = morphology.remove_small_objects(edges, max_size=16)
    return util.img_as_ubyte(1.0 - edges.astype(float))


def _materialize_laid_pages(
    laid,
    job_dir: Path,
    *,
    kind: str,
    mode: str,
    profile=None,
) -> DocPipelineResult:
    """排版结果 → 每页 SVG/G-code/预览 + pages.json + 第 1 页兼容名。"""
    import json as _json

    from hutuji_mcp import write_text
    from hutuji_mcp.gcode_gen import svg_to_gcode, validate_gcode
    from hutuji_mcp.llm_svg import _validate_svg

    job_dir.mkdir(parents=True, exist_ok=True)
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
        stats = svg_to_gcode(svg_path, gcode_path, normalize=False, profile=profile)
        gcode_raw = gcode_path.read_bytes()
        validate_gcode(
            gcode_raw.decode("ascii"), total_bytes=len(gcode_raw), profile=profile
        )
        preview_path.write_bytes(write_text.page_to_preview_png(page))
        page_lines.append(stats.lines)
        total_lines += stats.lines
        total_bytes += stats.bytes

    first_svg = pages_dir / "page_001.svg"
    shutil.copyfile(first_svg, job_dir / "draw.svg")
    shutil.copyfile(pages_dir / "page_001.gcode", job_dir / "draw.gcode")
    from hutuji_mcp.paper_artifacts import read_paper_metadata, write_paper_metadata

    write_paper_metadata(job_dir / "draw.gcode", read_paper_metadata(pages_dir / "page_001.gcode"))
    shutil.copyfile(pages_dir / "page_001.png", job_dir / "preview.png")

    meta = {
        "kind": kind,
        "mode": mode,
        "page_count": len(laid.pages),
        "truncated": laid.truncated,
        "missing_chars": laid.missing_chars,
        "page_lines": page_lines,
    }
    (job_dir / "pages.json").write_text(
        _json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    return DocPipelineResult(
        kind=kind,
        mode=mode,
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


def process_write_text(
    text: str,
    job_dir: Path,
    *,
    gfont_dir: str,
    layout,
    stroke_dir: str = "",
    font_name: str | None = None,
    profile=None,
    fit_single_page: bool = False,
) -> DocPipelineResult:
    """纯文本 → 文章排版 → 多页产物（kind=write，供 /api/draw/write 调用）。
    font_name 为空走默认偏好（blood8 真迹），具名不存在由 load_font 抛
    WriteTextError → app 层 400。显式选字体时中文字形也由该字库决定；
    仅未选字体时沿用标准笔顺覆盖。fit_single_page：未显式传字号时启用
    （2026-09-11 用户拍板「AI 写字最好也单张纸」）——超页逐轮缩字号凑单页，
    4mm 地板仍超则回落原字号多页。
    """
    from hutuji_mcp import write_text
    from hutuji_mcp.article_pages import ARTICLE_MAX_PAGES

    assert gfont_dir
    if not text.strip():
        raise write_text.WriteTextError("文本为空")
    font = write_text.load_font(gfont_dir, font_name or None)
    metrics = write_text.measure_font(font)
    stroke_data = None
    if stroke_dir and not font_name:
        from hutuji_mcp import stroke_order

        try:
            stroke_data = stroke_order.StrokeData.load(stroke_dir)
        except stroke_order.StrokeOrderError:
            stroke_data = None
    layout_fn = (
        write_text.layout_text_fit_single if fit_single_page else write_text.layout_text
    )
    if profile is not None:
        layout = replace(layout, page_width_mm=profile.page_width_mm, page_height_mm=profile.page_height_mm)
    laid = layout_fn(
        text,
        font,
        metrics,
        layout,
        max_pages=ARTICLE_MAX_PAGES,
        stroke_data=stroke_data,
        handwriting=True,  # 手写随机抖动：手输写字链默认开（奎享 randomX/YScale/Rotate 口径）
    )
    return _materialize_laid_pages(
        laid, job_dir, kind="write", mode="article", profile=profile
    )

def process_teach_text(
    chars: str,
    job_dir: Path,
    *,
    stroke_dir: str,
    profile=None,
) -> DocPipelineResult:
    """笔顺教学 → 田字格多页产物（kind=teach，供 /api/draw/teach 调用）。"""
    from hutuji_mcp import stroke_order
    from hutuji_mcp.article_pages import ARTICLE_MAX_PAGES

    assert stroke_dir, "stroke_dir 为空应先在 app 层 503"
    if not [c for c in chars if not c.isspace()]:
        raise stroke_order.StrokeOrderError("没有可教的汉字")
    data = stroke_order.StrokeData.load(stroke_dir)
    dimensions = {} if profile is None else {
        "page_width_mm": profile.page_width_mm, "page_height_mm": profile.page_height_mm,
    }
    laid = stroke_order.layout_teach(chars, data, ARTICLE_MAX_PAGES, **dimensions)
    return _materialize_laid_pages(
        laid, job_dir, kind="teach", mode="article", profile=profile
    )


def process_upload_doc(
    raw: bytes,
    kind: str,
    job_dir: Path,
    *,
    gfont_dir: str,
    stroke_dir: str = "",
    size_mm: float | None = None,
    line_spacing: float | None = None,
    profile=None,
) -> DocPipelineResult:
    """文档 → 解析 → 排版 → 每页 SVG/G-code/预览（多页，pages/ 目录 + pages.json）。

    版式全部自排（normalize=False 跳过 vpype layout）：文章走 write_text
    （与语音 hutuji_write 同链），表格走 table_layout（格线+格内字同页笔画）。
    两种模式的每页产物都过同一条 §5 闸（_validate_svg + validate_gcode）。
    gfont_dir 由 app 层在路由前验空（未配置 = 503），此处断言即可。
    stroke_dir 可选：配置则文章 CJK 按标准笔顺逐笔抬笔（与 hutuji_write 同）。
    """
    from draw_portal.write_layout_params import resolve_article_layout
    from hutuji_mcp import doc_ingest, table_layout, write_text
    from hutuji_mcp.article_pages import ARTICLE_MAX_PAGES

    assert gfont_dir, "gfont_dir 为空应先在 app 层 503"
    doc = doc_ingest.parse_document(raw, kind)

    font = write_text.load_font(gfont_dir, None)
    metrics = write_text.measure_font(font)
    stroke_data = None
    if stroke_dir:
        from hutuji_mcp import stroke_order

        try:
            stroke_data = stroke_order.StrokeData.load(stroke_dir)
        except stroke_order.StrokeOrderError:
            stroke_data = None
    if doc.mode == "article":
        layout = resolve_article_layout(size_mm=size_mm, line_spacing=line_spacing)
        if profile is not None:
            layout = replace(layout, page_width_mm=profile.page_width_mm, page_height_mm=profile.page_height_mm)
        laid = write_text.layout_text(
            "\n".join(doc.paragraphs),
            font,
            metrics,
            layout,
            max_pages=ARTICLE_MAX_PAGES,
            headings=doc.headings or None,
            stroke_data=stroke_data,
        )
    else:
        layout = write_text.WriteLayout(size_mm=8.0, line_spacing=1.25, char_gap_mm=0.5)
        if profile is not None:
            layout = replace(layout, page_width_mm=profile.page_width_mm, page_height_mm=profile.page_height_mm)
        laid = table_layout.layout_tables(
            doc.tables,
            font,
            metrics,
            layout,
            max_pages=ARTICLE_MAX_PAGES,
        )
    return _materialize_laid_pages(
        laid, job_dir, kind=doc.kind, mode=doc.mode, profile=profile
    )


def process_upload_svg(
    svg_bytes: bytes, job_dir: Path, *, profile=None, preserve_canvas: bool = False,
) -> PipelineResult:
    import numpy as np
    import vpype

    from hutuji_mcp.bitmap_svg import polylines_to_preview_png
    from hutuji_mcp.gcode_gen import svg_to_gcode, validate_gcode
    from hutuji_mcp.llm_svg import _validate_svg

    job_dir.mkdir(parents=True, exist_ok=True)
    text = svg_bytes.decode("utf-8")
    _validate_svg(text)

    svg_path = job_dir / "draw.svg"
    gcode_path = job_dir / "draw.gcode"
    preview_path = job_dir / "preview.png"
    svg_path.write_text(text, encoding="utf-8")
    layout_path = job_dir / "layout-preview.svg"
    try:
        stats = svg_to_gcode(svg_path, gcode_path, profile=profile,
                             preserve_canvas=preserve_canvas, preview_svg_path=layout_path)
        raw = gcode_path.read_bytes()
        validate_gcode(raw.decode("ascii"), total_bytes=len(raw), profile=profile)
        # 同一管线导出排版后路径，避免预览保留原构图、纸面却按笔迹放大的错位。
        # 手绘保留画布；普通上传仍按笔迹构图，毫米容差和 5mm 内边距均不变。
        lines, width, height = vpype.read_svg(layout_path, quantization=1.0)
        polys = [np.column_stack((line.real, line.imag)) for line in lines]
        preview_path.write_bytes(polylines_to_preview_png(polys, width, height, smooth=False))
    finally:
        layout_path.unlink(missing_ok=True)

    return PipelineResult(
        lines=stats.lines,
        bytes=stats.bytes,
        pen_lifts=stats.pen_lifts,
        svg_path=svg_path,
        gcode_path=gcode_path,
        preview_path=preview_path,
    )


def strip_corner_watermark(png_bytes: bytes) -> bytes:
    """洗白右下角「AI生成」类角标水印，供描摹输入（2026-09-08 实证 TCB 混元
    i2i 输出右下角带半透明灰字「AI生成」，不洗会随描摹变成纸面笔画）。

    判定只动角区（x≥78%, y≥88%）的中灰像素（灰度 >100 洗白）：水印是半透灰字，
    线稿笔画近黑（<100）与角区人物线条都不受影响。数字存档 upload.png 由调用方
    保留未洗版本（合规留痕），本函数产物只进描摹/G-code。
    """
    import imageio.v3 as iio
    import numpy as np

    try:
        arr = iio.imread(png_bytes).copy()
    except Exception:
        return png_bytes  # 判不出就不动：水印洗白是可用性增强，不为它加 fail-closed 面
    h, w = arr.shape[:2]
    y0, x0 = int(h * 0.88), int(w * 0.78)
    zone = arr[y0:, x0:]
    gray = zone if zone.ndim == 2 else zone[..., :3].mean(axis=-1)
    zone[gray > 100] = 255
    buf = io.BytesIO()
    iio.imwrite(buf, arr, extension=".png")
    return buf.getvalue()


def shrink_oversized_png(png_bytes: bytes, *, max_mpx: float = 4.8) -> bytes:
    """超过描摹内存闸（bitmap_svg 5.0 Mpx 硬闸）的 PNG 先等比缩到闸内。

    2026-09-08 生产实证：小程序客户端压缩按文件字节（4MB）不按像素，
    12MP 手机照片压完仍是 3072×1920=5.9Mpx，直撞描摹闸整单 422
    「图片转换失败」。像素高于闸对写字机本就无收益（落笔精度亚毫米），
    缩到 4.8 Mpx 留边际；闸内图片原样返回零开销。
    """
    import imageio.v3 as iio

    _guard_raster_dimensions(png_bytes)
    arr = iio.imread(png_bytes)
    h, w = arr.shape[:2]
    mpx = (h * w) / 1e6
    if mpx <= max_mpx:
        return png_bytes
    from skimage import transform

    scale = (max_mpx / mpx) ** 0.5
    new_hw = (max(1, round(h * scale)), max(1, round(w * scale)))
    arr = transform.resize(
        arr, new_hw + arr.shape[2:], anti_aliasing=True, preserve_range=True
    ).astype(arr.dtype)
    buf = io.BytesIO()
    iio.imwrite(buf, arr, extension=".png")
    return buf.getvalue()


def process_upload_png(
    png_bytes: bytes, job_dir: Path, *, skeleton_method: str = "zhang", profile=None
) -> PipelineResult:
    """PNG → 描摹 → SVG 校验 → vpype G-code → validate_gcode。

    skeleton_method 透传 trace_bitmap_to_polylines；照片 i2i 精修链传 lee
    （生产样本 A/B 抬笔净降，2026-09-10），Canny 快出段维持 zhang 不动。
    """
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

    polys, w, h = trace_bitmap_to_polylines(
        shrink_oversized_png(png_bytes), skeleton_method=skeleton_method
    )
    svg = polylines_to_svg(polys, w, h)
    _validate_svg(svg)
    preview = polylines_to_preview_png(polys, w, h)

    svg_path = job_dir / "draw.svg"
    gcode_path = job_dir / "draw.gcode"
    preview_path = job_dir / "preview.png"
    svg_path.write_text(svg, encoding="utf-8")
    preview_path.write_bytes(preview)

    stats = svg_to_gcode(svg_path, gcode_path, profile=profile)
    raw = gcode_path.read_bytes()
    validate_gcode(raw.decode("ascii"), total_bytes=len(raw), profile=profile)

    return PipelineResult(
        lines=stats.lines,
        bytes=stats.bytes,
        pen_lifts=stats.pen_lifts,
        svg_path=svg_path,
        gcode_path=gcode_path,
        preview_path=preview_path,
    )


def process_upload_sketch(
    raw: bytes, job_dir: Path, *, style: str = "hatch", profile=None
) -> PipelineResult:
    """照片 → 程序化风格渲染（排线/波纹）→ SVG 校验 → vpype G-code → §5。

    2026-09-10 拍板替换 AI i2i 精修链：位图经骨架化必然碎线糊团（提示词调优
    撞 run-to-run 噪声地板），程序化渲染直出矢量折线、零模型费、确定性可复现。
    输入先归一成 PNG（render 内部自缩到 760px 工作栅格，无需 shrink_oversized）。
    """
    from hutuji_mcp.bitmap_svg import polylines_to_preview_png, polylines_to_svg
    from hutuji_mcp.gcode_gen import svg_to_gcode, validate_gcode
    from hutuji_mcp.llm_svg import _validate_svg
    from hutuji_mcp.sketch_render import render_sketch_polylines

    job_dir.mkdir(parents=True, exist_ok=True)
    plain_png = normalize_raster_to_png(raw, line_art=False)
    (job_dir / "upload.png").write_bytes(plain_png)

    polys, w, h = render_sketch_polylines(plain_png, style=style)
    # 渲染器已经给出稀疏矢量拐点；描摹链的均值平滑会挪走回折和五官。
    svg = polylines_to_svg(polys, w, h, smooth=False)
    _validate_svg(svg)
    preview = polylines_to_preview_png(polys, w, h, smooth=False)

    svg_path = job_dir / "draw.svg"
    gcode_path = job_dir / "draw.gcode"
    preview_path = job_dir / "preview.png"
    svg_path.write_text(svg, encoding="utf-8")
    preview_path.write_bytes(preview)

    stats = svg_to_gcode(svg_path, gcode_path, profile=profile)
    gcode_raw = gcode_path.read_bytes()
    validate_gcode(
        gcode_raw.decode("ascii"), total_bytes=len(gcode_raw), profile=profile
    )

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
    profile=None,
    preserve_canvas: bool = False,
) -> PipelineResult:
    kind = detect_upload_kind(raw, content_type, filename)
    if kind == "svg":
        return process_upload_svg(raw, job_dir, profile=profile, preserve_canvas=preserve_canvas)
    if preserve_canvas:
        raise ValueError("保留画布仅适用于 SVG 画稿")
    # 描摹只吃 PNG 魔数（trace_bitmap_to_polylines 开头硬闸）；JPEG 直传会在闸上炸
    # BitmapError（2026-09-07 iPhone 拍照 422「图片转换失败」实证）。按魔数判定，
    # 不信客户端 content_type。
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return process_upload_png(raw, job_dir, profile=profile)
    return process_upload_png(normalize_raster_to_png(raw), job_dir, profile=profile)


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
