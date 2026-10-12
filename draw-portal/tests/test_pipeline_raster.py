"""process_upload 位图归一化回归：JPEG 直传曾撞描摹 PNG 魔数闸（BitmapError）。

2026-09-07 iPhone 拍照上传 422「图片转换失败」实证：旧代码按 content_type
放行 image/jpeg 直接进 process_upload_png，trace_bitmap_to_polylines 开头
校验 PNG 魔数即炸。修复后按魔数判定，非 PNG 一律 normalize_raster_to_png。
"""

from __future__ import annotations

import io

import pytest

from draw_portal.pipeline import process_upload


def _drawing_jpeg() -> bytes:
    """合成简单线稿 JPEG（白底黑框），走真实 JPEG 编码。"""
    import imageio.v3 as iio
    import numpy as np

    arr = np.full((128, 128), 255, dtype=np.uint8)
    arr[20:24, 20:108] = 0
    arr[104:108, 20:108] = 0
    arr[20:108, 20:24] = 0
    arr[20:108, 104:108] = 0
    buf = io.BytesIO()
    iio.imwrite(buf, arr, extension=".jpeg")
    raw = buf.getvalue()
    assert raw[:2] == b"\xff\xd8"  # 确为 JPEG 魔数，防止测试静默变质
    return raw


def test_jpeg_upload_traces_through(tmp_path):
    result = process_upload(
        _drawing_jpeg(),
        content_type="image/jpeg",
        filename="photo.jpg",
        job_dir=tmp_path,
    )
    assert result.lines > 0
    assert result.gcode_path.exists()


def test_oversized_png_shrinks_under_trace_gate(tmp_path):
    """超描摹 5.0 Mpx 闸的 PNG 先等比缩到闸内（2026-09-08 生产实证：
    客户端压缩按字节不按像素，3072×1920=5.9Mpx 手机照片整单 422）。"""
    import imageio.v3 as iio
    import numpy as np

    from draw_portal.pipeline import shrink_oversized_png

    # 3072×1920=5.9Mpx 白底黑框线稿，真实 PNG 编码
    arr = np.full((1920, 3072), 255, dtype=np.uint8)
    arr[400:450, 400:2600] = 0
    arr[1400:1450, 400:2600] = 0
    buf = io.BytesIO()
    iio.imwrite(buf, arr, extension=".png")
    big = buf.getvalue()

    out = shrink_oversized_png(big)
    shrunk = iio.imread(out)
    h, w = shrunk.shape[:2]
    assert (h, w) != (1920, 3072)  # 尺寸必变（重编码发生）
    assert (h * w) / 1e6 <= 4.8
    assert abs((w / h) - (3072 / 1920)) < 0.02  # 等比
    assert out[:8] == b"\x89PNG\r\n\x1a\n"  # 仍是 PNG


def test_small_png_passes_shrink_untouched():
    """闸内图片原样返回（零重编码零质量损失）。"""
    import imageio.v3 as iio
    import numpy as np

    from draw_portal.pipeline import shrink_oversized_png

    arr = np.full((64, 64), 255, dtype=np.uint8)
    buf = io.BytesIO()
    iio.imwrite(buf, arr, extension=".png")
    raw = buf.getvalue()
    assert shrink_oversized_png(raw) is raw


def test_corner_watermark_stripped_but_dark_strokes_survive():
    """i2i 角标「AI生成」半透灰字洗白进描摹，近黑线稿笔画与非角区内容不动
    （2026-09-08 实证 TCB 混元输出右下角水印会上纸成笔画）。"""
    import imageio.v3 as iio
    import numpy as np

    from draw_portal.pipeline import strip_corner_watermark

    arr = np.full((200, 200), 255, dtype=np.uint8)
    arr[180:190, 160:190] = 180  # 角区水印（半透中灰）
    arr[176:179, 150:195] = 20   # 角区线稿笔画（近黑）
    arr[50:60, 50:60] = 150      # 非角区中灰内容（不许动）
    buf = io.BytesIO()
    iio.imwrite(buf, arr, extension=".png")
    out = iio.imread(strip_corner_watermark(buf.getvalue()))
    assert (out[180:190, 160:190] == 255).all()  # 水印洗白
    assert (out[176:179, 150:195] == 20).all()   # 近黑笔画保留
    assert (out[50:60, 50:60] == 150).all()      # 非角区不动


def _png_declaring_size(width: int, height: int) -> bytes:
    """构造几十字节的 PNG：IHDR 真实声明 width×height，IDAT 不足以表达该图幅。

    Pillow 的 `Image.open(...).size` 只解析 IHDR（不解码像素），故能拿到声明尺寸；
    用它触发解码前尺寸闸，而无需在测试里真的分配数亿像素。
    """
    import struct
    import zlib

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk("IHDR".encode(), struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    idat = _chunk("IDAT".encode(), zlib.compress(b"\x00" * 16))
    iend = _chunk("IEND".encode(), b"")
    return sig + ihdr + idat + iend


def test_raster_bomb_rejected_before_decode(monkeypatch):
    """4MB 字节上限内但头部声明 40000×40000（1.6Gpx）的位图：解码前尺寸闸拦下，
    绝不进 iio.imread 全量解码（否则 worker OOM）。旧码无闸 → imread 被调用。"""
    import imageio.v3 as iio

    from draw_portal import pipeline

    bomb = _png_declaring_size(40000, 40000)
    assert len(bomb) < 1024  # 文件本体几十字节，4MB 上限挡不住
    seen: list[int] = []
    real = iio.imread
    monkeypatch.setattr(iio, "imread", lambda *a, **k: (seen.append(1), real(*a, **k))[1])

    with pytest.raises(ValueError, match="像素过多"):
        pipeline.normalize_raster_to_png(bomb)
    assert seen == []  # 守卫在解码前短路


def test_shrink_oversized_rejects_bomb_before_decode(monkeypatch):
    """PNG 描摹路径的首个解码点（shrink_oversized_png）同样在解码前拦像素炸弹。"""
    import imageio.v3 as iio

    from draw_portal import pipeline

    bomb = _png_declaring_size(40000, 40000)
    seen: list[int] = []
    real = iio.imread
    monkeypatch.setattr(iio, "imread", lambda *a, **k: (seen.append(1), real(*a, **k))[1])

    with pytest.raises(ValueError, match="像素过多"):
        pipeline.shrink_oversized_png(bomb)
    assert seen == []


def test_is_photo_raster_bomb_returns_false_without_decode(monkeypatch):
    """照片启发式对像素炸弹走 fail-soft（返回 False），且不触发全量解码。"""
    import imageio.v3 as iio

    from draw_portal import pipeline

    bomb = _png_declaring_size(40000, 40000)
    seen: list[int] = []
    real = iio.imread
    monkeypatch.setattr(iio, "imread", lambda *a, **k: (seen.append(1), real(*a, **k))[1])

    assert pipeline.is_photo_raster(bomb) is False
    assert seen == []


def test_oversized_side_rejected(monkeypatch):
    """单边超 32768 即便总像素未过阈也拒（对齐 bitmap_svg 单边硬闸，防 nMpx×1 退化图）。"""
    import imageio.v3 as iio

    from draw_portal import pipeline

    bomb = _png_declaring_size(40000, 3)
    seen: list[int] = []
    real = iio.imread
    monkeypatch.setattr(iio, "imread", lambda *a, **k: (seen.append(1), real(*a, **k))[1])

    with pytest.raises(ValueError, match="单边过大"):
        pipeline.normalize_raster_to_png(bomb)
    assert seen == []


def test_decode_memory_band_rejected_above_pillow_warn(monkeypatch):
    """64–179Mpx 带：Pillow 自带闸只告警不报错、仍会全量解码（OOM 风险），本地
    64Mpx 闸才是真正拦截点——证明守卫带来额外保护，而非只复述 Pillow 自带 DOS 闸。"""
    import warnings

    import imageio.v3 as iio
    from PIL import Image

    from draw_portal import pipeline

    band = _png_declaring_size(10000, 10000)  # 100Mpx：> 64Mpx 本地闸、< 179Mpx Pillow 硬闸
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with Image.open(io.BytesIO(band)) as im:
            assert im.size == (10000, 10000)  # 能读到尺寸即说明 Pillow 未硬拦，解码本会放行
    seen: list[int] = []
    real = iio.imread
    monkeypatch.setattr(iio, "imread", lambda *a, **k: (seen.append(1), real(*a, **k))[1])

    with pytest.raises(ValueError, match="像素过多"):
        pipeline.normalize_raster_to_png(band)
    assert seen == []


def test_normal_small_raster_passes_guard(tmp_path):
    """闸内正常图不受影响：合法 JPEG 仍描摹出图（守卫纯增量、不误伤）。"""
    result = process_upload(
        _drawing_jpeg(),
        content_type="image/jpeg",
        filename="ok.jpg",
        job_dir=tmp_path / "job",
    )
    assert result.lines > 0
