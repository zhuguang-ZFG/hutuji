"""照片 → 程序化矢量风格渲染：排线素描（hatch）与连续波纹（squiggle）。

为什么存在：AI i2i 位图经骨架化必然碎线糊团，提示词调优 2026-09-10 TCB A/B
实测撞 run-to-run 噪声地板（单臂摆幅 76 > 全部配对差），模型路线到此链路
的天花板。排线/波纹直接产矢量折线——风格是算法本体而非提示词祈祷，零模型费、
零下载、确定性可复现，描摹环节（skeletonize/sknw）整体消失。

产物契约与 trace_bitmap_to_polylines 相同：list[np.ndarray]，每条 (N,2)
float64、列序 (x, y)、像素坐标系 y 向下，可直接进 polylines_to_svg。

调参警示：portal 设备预览器（polylines_to_preview_png）把 760px 网格缩到
约 1/3 再以 2px 粗线无抗锯齿重采样，密排线区在预览里必然糊成实心块——
评画风必须用全分辨率栅格化（1px 线）或真 G-code，别拿设备预览图调参
（2026-09-10 两轮被预览器污染的无效评价教训）。
"""
from __future__ import annotations

import io

import imageio.v3 as iio
import numpy as np

# 工作栅格宽：760px ≈ 277mm 页宽下 1px≈0.36mm，排线间距 3px≈1.1mm，
# 落笔精度（亚毫米）内排线互不糊团；再大只是浪费算力。
_WORK_WIDTH_PX = 760

# 排线层：(亮度上限, 角度°)。g < 上限的区域叠该层——层数越多越暗，蚀刻素描
# 的经典累积口径。0.78 起手让中间调（人脸皮肤 ~0.7-0.8）也有稀疏排线过渡，
# 暗部四层方向交叉自然近实。mask 先平滑压照片噪点引起的碎段。
_HATCH_LAYERS: tuple[tuple[float, float], ...] = (
    (0.78, 45.0),
    (0.62, -45.0),
    (0.46, 45.0),
    (0.30, 90.0),
)
_HATCH_SPACING_PX = 3.0      # 排线间距（≈1.1mm 纸上）
_HATCH_MIN_RUN_PX = 6.0      # 短于 ≈2.2mm 的排线段不画：视觉上只是墨点
_HATCH_SAMPLE_STEP_PX = 1.0  # 沿线采样步长，决定段端点精度
_HATCH_MASK_SIGMA = 1.0      # mask 前平滑，压阈值附近噪点碎段

# 轮廓：高斯平滑亮度的等值线（find_contours 直出矢量，无骨架化）。
# 选 4 级 tone 边界：外形 + 三大调分界，多一级就碎、少一级丢结构。
_CONTOUR_LEVELS: tuple[float, ...] = (0.30, 0.48, 0.66, 0.84)
_CONTOUR_MIN_POINTS = 12     # 短轮廓是噪声斑点，丢弃
_CONTOUR_SMOOTH_SIGMA = 1.2  # 轮廓前平滑，压照片噪点引起的抖线

# 波纹：行距 4px，振幅随暗度（1-g）调制，白区（暗度<_DARK_FLOOR）整行不画——
# 平线在白纸上照样画出墨痕，SquiggleTab 式「全图一根线」的代价是白区爬满
# 直线，故按行分段、暗区起波、亮区断开。
_SQUIGGLE_ROW_SPACING_PX = 4.0
_SQUIGGLE_WAVELENGTH_PX = 7.0
_SQUIGGLE_MAX_AMP_PX = 1.8   # < 行距一半，相邻行波峰永不互撞
_SQUIGGLE_DARK_FLOOR = 0.06  # 暗度低于此值视为白区，行内断开
_SQUIGGLE_MIN_SEG_PX = 8.0

# RDP 抽稀：find_contours/波形按像素步进出点（1pt/px），直接进 G-code 会撑爆
# §5 的 512KiB 硬闸（实测 0c278f1f 排线 81k 点，抽稀后 10.5k 点/298KiB）。
# 轮廓 tolerance 1.2px≈0.44mm 不可辨；波形振幅 1.8px 必须用更小的 0.6px，
# 否则正弦被抽成直线。
_RDP_TOLERANCE_PX = 1.2
_RDP_TOLERANCE_WAVE_PX = 0.6


class SketchRenderError(Exception):
    """照片解码失败或产出为空——portal 据此整单 422，不重试不降级。"""


def _load_gray(image_bytes: bytes) -> np.ndarray:
    """解码任意位图 → float32 灰度 [0,1]（1=白），宽压到 _WORK_WIDTH_PX。"""
    try:
        arr = iio.imread(image_bytes)
    except Exception as exc:
        raise SketchRenderError("图片解码失败") from exc
    if arr.ndim == 3:
        rgb = arr[..., :3].astype(np.float32)
        gray = rgb.mean(axis=-1)
    elif arr.ndim == 2:
        gray = arr.astype(np.float32)
    else:
        raise SketchRenderError("不支持的图片通道数")
    if gray.size == 0:
        raise SketchRenderError("图片为空")
    max_val = float(gray.max())
    if max_val > 1.0:
        gray /= max_val if max_val <= 255.0 else 65535.0
    h, w = gray.shape
    if w > _WORK_WIDTH_PX:
        from skimage.transform import resize

        new_h = max(1, round(h * _WORK_WIDTH_PX / w))
        gray = resize(
            gray, (new_h, _WORK_WIDTH_PX), anti_aliasing=True
        ).astype(np.float32)
    return np.clip(gray, 0.0, 1.0)


def _simplify(polys: list[np.ndarray], tolerance: float) -> list[np.ndarray]:
    """RDP 抽稀多点折线；两点排线段不动。"""
    from skimage.measure import approximate_polygon

    out: list[np.ndarray] = []
    for p in polys:
        if len(p) > 2:
            q = approximate_polygon(p, tolerance=tolerance)
            if len(q) >= 2:
                out.append(q)
        else:
            out.append(p)
    return out


def _iso_contours(gray: np.ndarray) -> list[np.ndarray]:
    """亮度等值线 → 轮廓折线；find_contours 出 (row,col)，转 (x,y)。"""
    from skimage.filters import gaussian
    from skimage.measure import find_contours

    smooth = gaussian(gray, sigma=_CONTOUR_SMOOTH_SIGMA, preserve_range=True)
    out: list[np.ndarray] = []
    for level in _CONTOUR_LEVELS:
        for contour in find_contours(smooth, level=level):
            if len(contour) < _CONTOUR_MIN_POINTS:
                continue
            out.append(contour[:, ::-1].astype(np.float64))  # (row,col)→(x,y)
    return out


def _gaussian_for_mask(gray: np.ndarray) -> np.ndarray:
    """排线 mask 前的轻平滑：照片噪点在阈值附近抖出碎段，1px 高斯压掉。"""
    from skimage.filters import gaussian

    return gaussian(gray, sigma=_HATCH_MASK_SIGMA, preserve_range=True)


def _hatch_layer(mask: np.ndarray, angle_deg: float, spacing: float) -> list[np.ndarray]:
    """一组平行线裁剪进 mask：逐线沿方向采样，True 连续 run 成段。

    采样而不是逐像素扫描的原因：线段端点只需亚像素级位置，1px 步长采样
    足够，且全程 numpy fancy-index，千条线秒级完成。
    """
    h, w = mask.shape
    theta = np.deg2rad(angle_deg)
    ux, uy = np.cos(theta), np.sin(theta)          # 线方向
    nx, ny = -np.sin(theta), np.cos(theta)         # 法向（截距轴）
    corners = np.array([[0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1]])
    projs = corners[:, 0] * nx + corners[:, 1] * ny
    c_lo, c_hi = float(projs.min()), float(projs.max())
    # t 区间取角点在方向轴上的投影：法向足点常在 bbox 外，弦可能整段
    # 落在负 t（如 45° 时 c<0 的线），只扫 [0,∞) 会整线漏画。
    t_projs = corners[:, 0] * ux + corners[:, 1] * uy
    t_lo, t_hi = float(t_projs.min()), float(t_projs.max())

    out: list[np.ndarray] = []
    c = np.ceil(c_lo / spacing) * spacing
    while c <= c_hi:
        t = np.arange(t_lo, t_hi + _HATCH_SAMPLE_STEP_PX, _HATCH_SAMPLE_STEP_PX)
        # 起点：c 处法向足点（相对原点）；参数平移不改变直线本身
        px, py = nx * c, ny * c
        xs = px + ux * t
        ys = py + uy * t
        xi = np.round(xs).astype(np.int64)
        yi = np.round(ys).astype(np.int64)
        inside = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
        hits = np.zeros(len(t), dtype=bool)
        hits[inside] = mask[yi[inside], xi[inside]]
        # True 连续 run → 段
        padded = np.concatenate(([False], hits, [False]))
        edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
        for start, end in edges.reshape(-1, 2):
            run_len = t[end - 1] - t[start]
            if run_len < _HATCH_MIN_RUN_PX:
                continue
            out.append(
                np.array(
                    [[xs[start], ys[start]], [xs[end - 1], ys[end - 1]]],
                    dtype=np.float64,
                )
            )
        c += spacing
    return out


def render_hatch(image_bytes: bytes) -> tuple[list[np.ndarray], int, int]:
    """排线素描：iso 轮廓 + 四层累积排线，RDP 抽稀后返回 (polys, w, h)。"""
    gray = _load_gray(image_bytes)
    h, w = gray.shape
    polys = _iso_contours(gray)
    smooth = _gaussian_for_mask(gray)
    for threshold, angle in _HATCH_LAYERS:
        polys.extend(_hatch_layer(smooth < threshold, angle, _HATCH_SPACING_PX))
    if not polys:
        raise SketchRenderError("排线渲染产出为空（图片可能为纯色）")
    return _simplify(polys, _RDP_TOLERANCE_PX), w, h


def render_squiggle(image_bytes: bytes) -> tuple[list[np.ndarray], int, int]:
    """连续波纹：行扫描线振幅随暗度调制，iso 轮廓补结构。返回 (polys, w, h)。

    波纹用连续暗度调制而非阈值 mask，不需要 mask 平滑；抽稀用更小的波形
    容差保住正弦振幅。
    """
    gray = _load_gray(image_bytes)
    h, w = gray.shape
    darkness = 1.0 - gray
    polys: list[np.ndarray] = []
    x = np.arange(w, dtype=np.float64)
    phase = 2.0 * np.pi * x / _SQUIGGLE_WAVELENGTH_PX
    rows = np.arange(_SQUIGGLE_ROW_SPACING_PX / 2, h, _SQUIGGLE_ROW_SPACING_PX)
    serpentine_prev_end: np.ndarray | None = None
    for row_idx, y0 in enumerate(rows):
        yi = int(round(y0))
        amp = np.clip(darkness[yi] - _SQUIGGLE_DARK_FLOOR, 0.0, None)
        amp = amp / max(1.0 - _SQUIGGLE_DARK_FLOOR, 1e-6) * _SQUIGGLE_MAX_AMP_PX
        live = amp > 1e-3
        if not live.any():
            continue
        wave_y = y0 + amp * np.sin(phase)
        # 行内按 live 分段；蛇形连接相邻行段减少抬笔：偶数行左→右、
        # 奇数行右→左，行尾到下行行首近邻则用竖直连线并入同一条折线，
        # 白捡一次免抬笔；断开即新折线。
        padded = np.concatenate(([False], live, [False]))
        edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
        segs: list[np.ndarray] = []
        for start, end in edges.reshape(-1, 2):
            if x[end - 1] - x[start] < _SQUIGGLE_MIN_SEG_PX:
                continue
            seg = np.column_stack([x[start:end], wave_y[start:end]])
            segs.append(seg)
        if not segs:
            continue
        if row_idx % 2 == 1:
            segs = [seg[::-1] for seg in segs][::-1]
        for seg in segs:
            if (
                serpentine_prev_end is not None
                and abs(seg[0, 1] - serpentine_prev_end[1]) <= _SQUIGGLE_ROW_SPACING_PX * 1.5
                and abs(seg[0, 0] - serpentine_prev_end[0]) <= w * 0.2
            ):
                polys[-1] = np.vstack([polys[-1], seg])
            else:
                polys.append(seg)
            serpentine_prev_end = seg[-1]
    polys.extend(_iso_contours(gray))
    if not polys:
        raise SketchRenderError("波纹渲染产出为空（图片可能为纯白）")
    return _simplify(polys, _RDP_TOLERANCE_WAVE_PX), w, h


_STYLES = {"hatch": render_hatch, "squiggle": render_squiggle}


def render_sketch_polylines(
    image_bytes: bytes, *, style: str = "hatch"
) -> tuple[list[np.ndarray], int, int]:
    """风格分发入口；未知风格 fail-closed（portal 参数校验漏网的最后防线）。"""
    renderer = _STYLES.get(style)
    if renderer is None:
        raise SketchRenderError(f"未知风格 {style!r}（可选：{sorted(_STYLES)}）")
    return renderer(image_bytes)


def render_sketch_preview(polys: list[np.ndarray], w: int, h: int) -> bytes:
    """复用描摹链同款预览渲染，保持产物面一致（RGB PNG，黑线白底）。"""
    from hutuji_mcp.bitmap_svg import polylines_to_preview_png

    return polylines_to_preview_png(polys, w, h)


def encode_png(gray_or_rgb: np.ndarray) -> bytes:
    """调试/调参用：ndarray → PNG 字节（生产链路不调用）。"""
    buf = io.BytesIO()
    iio.imwrite(buf, gray_or_rgb, extension=".png")
    return buf.getvalue()
