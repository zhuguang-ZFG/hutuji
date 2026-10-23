"""process_upload 位图归一化回归：JPEG 直传曾撞描摹 PNG 魔数闸（BitmapError）。

2026-09-07 iPhone 拍照上传 422「图片转换失败」实证：旧代码按 content_type
放行 image/jpeg 直接进 process_upload_png，trace_bitmap_to_polylines 开头
校验 PNG 魔数即炸。修复后按魔数判定，非 PNG 一律 normalize_raster_to_png。
"""

from __future__ import annotations

import io

import pytest


def _oriented_raster(fmt: str, orientation: int):
    import numpy as np
    from PIL import Image, ImageOps

    arr = np.full((48, 80, 3), 255, dtype=np.uint8)
    arr[:24, :40] = [0, 40, 80]
    arr[24:, 40:] = [180, 10, 60]
    exif = Image.Exif()
    exif[274] = orientation
    source = io.BytesIO()
    Image.fromarray(arr).save(source, format=fmt, **({"quality": 95} if fmt == "JPEG" else {}), exif=exif)
    raw = source.getvalue()
    with Image.open(io.BytesIO(raw)) as im:
        expected = np.asarray(ImageOps.exif_transpose(im))
    return raw, expected


def _oriented_mode_png(mode: str, orientation: int):
    import numpy as np
    from PIL import Image, ImageOps

    if mode == "L":
        arr = (np.arange(80, dtype=np.uint8) * 3)[None, :].repeat(48, axis=0)
    elif mode == "LA":
        base = (np.arange(80, dtype=np.uint8) * 3)[None, :].repeat(48, axis=0)
        alpha = np.full((48, 80), 255, dtype=np.uint8)
        alpha[:12, :20] = 40
        arr = np.dstack([base, alpha])
    elif mode == "RGBA":
        arr = np.zeros((48, 80, 4), dtype=np.uint8)
        arr[..., 0] = (np.arange(80, dtype=np.uint8) * 3)[None, :]
        arr[..., 1] = 40
        arr[..., 2] = 200
        arr[:12, :20, 3] = 30
    else:  # pragma: no cover
        raise AssertionError(mode)
    exif = Image.Exif()
    exif[274] = orientation
    source = io.BytesIO()
    Image.fromarray(arr).save(source, format="PNG", exif=exif)
    raw = source.getvalue()
    with Image.open(io.BytesIO(raw)) as im:
        expected = np.array(ImageOps.exif_transpose(im))
    return raw, expected


def _indexed_png(orientation: int, *, transparent: bool):
    """非对称 P 模式 PNG（真实编码）：2/4/5/7 镜像方向必须翻像素而非颜色通道。"""
    import numpy as np
    from PIL import Image, ImageOps

    idx = np.zeros((48, 80), dtype=np.uint8)
    idx[:12, :30] = 1
    idx[12:24, 30:] = 2
    idx[24:36, :20] = 3
    idx[36:, 20:60] = 4
    im = Image.new("P", (80, 48))
    im.putdata(idx.flatten().tolist())
    palette = [
        255, 255, 255,
        200, 30, 40,
        10, 120, 220,
        0, 0, 0,
        40, 200, 90,
    ]
    im.putpalette(palette + [0] * (768 - len(palette)))
    exif = Image.Exif()
    exif[274] = orientation
    save_kwargs = {"transparency": 0} if transparent else {}
    source = io.BytesIO()
    im.save(source, format="PNG", exif=exif, **save_kwargs)
    raw = source.getvalue()
    with Image.open(io.BytesIO(raw)) as src:
        oriented = ImageOps.exif_transpose(src)
        mode = "RGBA" if "transparency" in oriented.info else "RGB"
        expected = np.array(oriented.convert(mode))
    return raw, expected


@pytest.mark.parametrize("orientation", range(1, 9))
def test_photo_exif_orientation_consumed_once(orientation):
    """手机显示方向必须与归一化图片相同，镜像/旋转八种方向且重复归一不再旋转。"""
    import numpy as np
    from PIL import Image
    from draw_portal.pipeline import normalize_raster_to_png

    raw, expected = _oriented_raster("JPEG", orientation)
    normalized = normalize_raster_to_png(raw, line_art=False)
    with Image.open(io.BytesIO(normalized)) as im:
        np.testing.assert_array_equal(np.asarray(im), expected)
        assert not im.getexif().get(274), "方向已应用后不应留重复旋转元数据"
    with Image.open(io.BytesIO(normalize_raster_to_png(normalized, line_art=False))) as im:
        np.testing.assert_array_equal(np.asarray(im), expected)


@pytest.mark.parametrize("orientation", range(1, 9))
def test_indexed_png_exif_mirror_flips_pixels_not_channels(orientation):
    """索引 PNG 的镜像方向必须作用在像素坐标：旧 imageio 插件先转 RGB 再按 P
    选轴，2/4/5/7 会把颜色通道左右/上下翻转（形状与元数据都看不出异常）。"""
    import numpy as np
    from PIL import Image
    from draw_portal.pipeline import normalize_raster_to_png

    raw, expected = _indexed_png(orientation, transparent=False)
    normalized = normalize_raster_to_png(raw, line_art=False)
    with Image.open(io.BytesIO(normalized)) as im:
        assert im.mode == "RGB"
        np.testing.assert_array_equal(np.asarray(im), expected)
        assert not im.getexif().get(274)
    with Image.open(io.BytesIO(normalize_raster_to_png(normalized, line_art=False))) as im:
        np.testing.assert_array_equal(np.asarray(im), expected)


@pytest.mark.parametrize("orientation", range(1, 9))
def test_indexed_png_transparency_expands_to_rgba(orientation):
    """P 模式带 transparency 必须保留透明通道，方向仍按像素转置。"""
    import numpy as np
    from PIL import Image
    from draw_portal.pipeline import normalize_raster_to_png

    raw, expected = _indexed_png(orientation, transparent=True)
    normalized = normalize_raster_to_png(raw, line_art=False)
    with Image.open(io.BytesIO(normalized)) as im:
        assert im.mode == "RGBA"
        np.testing.assert_array_equal(np.asarray(im), expected)
        assert not im.getexif().get(274)


@pytest.mark.parametrize("mode", ["L", "LA", "RGBA"])
@pytest.mark.parametrize("orientation", range(1, 9))
def test_gray_and_alpha_modes_keep_orientation_and_shape(mode, orientation):
    """灰度/带 alpha 模式保持原通道数与 dtype，方向消费与 Pillow 有向图一致。"""
    import numpy as np
    from PIL import Image
    from draw_portal.pipeline import normalize_raster_to_png

    raw, expected = _oriented_mode_png(mode, orientation)
    normalized = normalize_raster_to_png(raw, line_art=False)
    with Image.open(io.BytesIO(normalized)) as im:
        assert im.mode == mode
        np.testing.assert_array_equal(np.asarray(im), expected)
        assert not im.getexif().get(274)

@pytest.mark.parametrize("orientation", range(1, 9))
def test_png_exif_orientation_consumed_before_trace(orientation, tmp_path):
    """PNG 魔数直传也必须先消费 EXIF；闸内无方向块时 shrink 仍原样返回。"""
    import numpy as np
    from PIL import Image
    from draw_portal.pipeline import normalize_raster_to_png, process_upload, shrink_oversized_png

    raw, expected = _oriented_raster("PNG", orientation)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    normalized = normalize_raster_to_png(raw, line_art=False)
    with Image.open(io.BytesIO(normalized)) as im:
        np.testing.assert_array_equal(np.asarray(im), expected)
        assert not im.getexif().get(274)
    shrunk = shrink_oversized_png(raw)
    with Image.open(io.BytesIO(shrunk)) as im:
        np.testing.assert_array_equal(np.asarray(im), expected)
    if orientation == 6:
        result = process_upload(raw, content_type="image/png", filename="photo.png", job_dir=tmp_path)
        assert result.lines > 0
        assert result.gcode_path.exists()


@pytest.mark.parametrize("orientation", [2, 6])
def test_indexed_png_shrink_consumes_mirror_orientation(orientation):
    """shrink 闸内的索引 PNG 带方向时重编码：镜像方向同样先转置再展开调色板。"""
    import numpy as np
    from PIL import Image
    from draw_portal.pipeline import shrink_oversized_png

    raw, expected = _indexed_png(orientation, transparent=False)
    shrunk = shrink_oversized_png(raw)
    with Image.open(io.BytesIO(shrunk)) as im:
        np.testing.assert_array_equal(np.asarray(im), expected)
        assert not im.getexif().get(274)


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


def _spy_pixel_load(monkeypatch) -> list[int]:
    """以 Pillow 真正解码像素的 Image.load 为「全量解码」边界（读头只解析 IHDR）。"""
    from PIL import Image

    seen: list[int] = []
    real = Image.Image.load

    def wrapped(self, *args, **kwargs):
        seen.append(1)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "load", wrapped)
    return seen


def test_raster_bomb_rejected_before_decode(monkeypatch):
    """4MB 字节上限内但头部声明 40000×40000（1.6Gpx）的位图：解码前尺寸闸拦下，
    绝不进行像素解码（否则 worker OOM）。旧码无闸 → 像素解码被触发。"""
    from draw_portal import pipeline

    bomb = _png_declaring_size(40000, 40000)
    assert len(bomb) < 1024  # 文件本体几十字节，4MB 上限挡不住
    seen = _spy_pixel_load(monkeypatch)

    with pytest.raises(ValueError, match="像素过多"):
        pipeline.normalize_raster_to_png(bomb)
    assert seen == []  # 守卫在解码前短路


def test_shrink_oversized_rejects_bomb_before_decode(monkeypatch):
    """PNG 描摹路径的首个解码点（shrink_oversized_png）同样在解码前拦像素炸弹。"""
    from draw_portal import pipeline

    bomb = _png_declaring_size(40000, 40000)
    seen = _spy_pixel_load(monkeypatch)

    with pytest.raises(ValueError, match="像素过多"):
        pipeline.shrink_oversized_png(bomb)
    assert seen == []


def test_photo_heuristic_uses_oriented_decode(monkeypatch):
    """照片启发式必须与归一化共用 EXIF 方向消费，避免竖拍被横着判类。"""
    from PIL import ImageOps

    from draw_portal import pipeline

    jpeg, _ = _oriented_raster("JPEG", 6)
    seen: list[int | None] = []
    real = ImageOps.exif_transpose

    def wrapped(im, *args, **kwargs):
        seen.append(im.getexif().get(274))
        return real(im, *args, **kwargs)

    monkeypatch.setattr(ImageOps, "exif_transpose", wrapped)
    pipeline.is_photo_raster(jpeg)
    pipeline.normalize_raster_to_png(jpeg, line_art=False)
    assert seen == [6, 6]


def test_is_photo_raster_bomb_returns_false_without_decode(monkeypatch):
    """照片启发式对像素炸弹走 fail-soft（返回 False），且不触发像素解码。"""
    from draw_portal import pipeline

    bomb = _png_declaring_size(40000, 40000)
    seen = _spy_pixel_load(monkeypatch)

    assert pipeline.is_photo_raster(bomb) is False
    assert seen == []


def test_oversized_side_rejected(monkeypatch):
    """单边超 32768 即便总像素未过阈也拒（对齐 bitmap_svg 单边硬闸，防 nMpx×1 退化图）。"""
    from draw_portal import pipeline

    bomb = _png_declaring_size(40000, 3)
    seen = _spy_pixel_load(monkeypatch)

    with pytest.raises(ValueError, match="单边过大"):
        pipeline.normalize_raster_to_png(bomb)
    assert seen == []


def test_decode_memory_band_rejected_above_pillow_warn(monkeypatch):
    """64–179Mpx 带：Pillow 自带闸只告警不报错、仍会全量解码（OOM 风险），本地
    64Mpx 闸才是真正拦截点——证明守卫带来额外保护，而非只复述 Pillow 自带 DOS 闸。"""
    import warnings

    from PIL import Image

    from draw_portal import pipeline

    band = _png_declaring_size(10000, 10000)  # 100Mpx：> 64Mpx 本地闸、< 179Mpx Pillow 硬闸
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with Image.open(io.BytesIO(band)) as im:
            assert im.size == (10000, 10000)  # 能读到尺寸即说明 Pillow 未硬拦，解码本会放行
    seen = _spy_pixel_load(monkeypatch)

    with pytest.raises(ValueError, match="像素过多"):
        pipeline.normalize_raster_to_png(band)
    assert seen == []


def test_bad_raster_still_rejected_and_probe_false():
    """坏输入维持既有失败面：转换抛错（上层 422），启发式 fail-soft 返回 False。"""
    from draw_portal.pipeline import is_photo_raster, normalize_raster_to_png

    with pytest.raises(OSError):
        normalize_raster_to_png(b"not an image")
    assert is_photo_raster(b"not an image") is False


def test_normal_small_raster_passes_guard(tmp_path):
    """闸内正常图不受影响：合法 JPEG 仍描摹出图（守卫纯增量、不误伤）。"""
    result = process_upload(
        _drawing_jpeg(),
        content_type="image/jpeg",
        filename="ok.jpg",
        job_dir=tmp_path / "job",
    )
    assert result.lines > 0
