"""process_upload 位图归一化回归：JPEG 直传曾撞描摹 PNG 魔数闸（BitmapError）。

2026-09-07 iPhone 拍照上传 422「图片转换失败」实证：旧代码按 content_type
放行 image/jpeg 直接进 process_upload_png，trace_bitmap_to_polylines 开头
校验 PNG 魔数即炸。修复后按魔数判定，非 PNG 一律 normalize_raster_to_png。
"""

from __future__ import annotations

import io

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
