"""照片 → 程序化矢量风格渲染：排线素描（hatch）与连续波纹（squiggle）。

为什么存在：AI i2i 位图经骨架化必然碎线糊团，提示词调优 2026-09-10 TCB A/B
实测撞 run-to-run 噪声地板（单臂摆幅 76 > 全部配对差），模型路线到此链路
的天花板。排线/波纹直接产矢量折线——风格是算法本体而非提示词祈祷，零模型费、
零下载、确定性可复现，描摹环节（skeletonize/sknw）整体消失。

产物契约与 trace_bitmap_to_polylines 相同：list[np.ndarray]，每条 (N,2)
float64、列序 (x, y)、像素坐标系 y 向下，可直接进 polylines_to_svg。

调参警示：设备预览器 polylines_to_preview_png 已于 2026-09-10 改为真密度渲染
（全分辨率 1px 栅格化 → 面积平均降采样，灰度=真墨密度）；但任何缩放判读
仍有残余失真——评画风以全分辨率 1px 栅格化或真 G-code 为准，别拿小预览调参
（2026-09-10 两轮被旧预览器（0.3×+2px 粗线）污染的无效评价教训）。
"""
from __future__ import annotations

import io

import imageio.v3 as iio
import numpy as np

# 工作栅格宽：760px ≈ 277mm 页宽下 1px≈0.36mm，排线间距 3px≈1.1mm，
# 落笔精度（亚毫米）内排线互不糊团；再大只是浪费算力。
_WORK_WIDTH_PX = 760

# 排线层：(亮度上限, 角度°, 间距px)。g < 上限的区域叠该层——层数越多越暗，
# 蚀刻素描的经典累积口径。阈值只对「归一化后」的真暗调起线：2026-09-10 生产
# 事故（室内人像 p50=0.54 直接撞旧 0.78 首层 → ~90% 页面叠四层交叉排线糊成
# 实心黑卡）证明裸亮度阈值对中间调照片必然失控，首层必须压在 0.55 以下、
# 亮层用宽间距稀疏过渡。mask 先平滑压照片噪点引起的碎段。
_HATCH_LAYERS: tuple[tuple[float, float, float], ...] = (
    (0.55, 45.0, 6.0),
    (0.42, -45.0, 6.0),
    (0.30, 45.0, 6.0),
    (0.18, -45.0, 6.0),
)
# 四层共享两组格点；暗两层填进同向线的中缝，避免 6/3.5px 同向排线
# 在局部几乎重合。层距按长边比例换算，竖照不能因像素高度大而更密。
# 原理参照 Praun 等 Real-Time Hatching 的嵌套笔画，参数与样本见本轮画质记录。
_WORK_MAX_HEIGHT_PX = 1280
_HATCH_MIN_RUN_PX = 6.0      # 短于 ≈2.2mm 的排线段不画：视觉上只是墨点
_HATCH_SAMPLE_STEP_PX = 1.0  # 沿线采样步长，决定段端点精度
_HATCH_MASK_SIGMA = 1.0      # mask 前平滑，压阈值附近噪点碎段
_HATCH_SEGMENT_BUDGET = 3500  # 段数预算：边际 ≈70B/段 + 固定轮廓/vpype ≈189KiB（事故原图双点校准），3500 段 ≈ 434KiB 留 §5 余量 ~78KiB

# 色调归一化：近纯色闸 → 全局/局部均衡分别拉伸、按 0.65/0.35 混合 → gamma。
# 2026-09-10 两次生产黑卡事故定案：① 裸亮度阈值对中间调照片结构性失控
# （室内人像 p50=0.54 撞 0.78 首层整页铺黑）；② 全局拉伸+gamma 仍救不了
# 逆光人像（面部局部偏暗被整体曲线压住，纸面观感五官糊没）。局部均衡负责
# 补暗部细节，保留全局权重避免背景纹理被独立拉成黑块。CLAHE 对平坦输入会放大
# 数值噪声，故近纯色必须在 CLAHE 之前短路（纯白全靠下游空产出 fail-closed）。
_TONE_STRETCH_LO_PCT = 2.0
_TONE_STRETCH_HI_PCT = 98.0
_TONE_STRETCH_MIN_RANGE = 0.05
_CLAHE_CLIP_LIMIT = 0.02
_TONE_GAMMA = 0.7
# 2026-09-12 两张逆光人像 A/B：0.55 让面部中间调更多留白，五官由结构线
# 保留；旧 0.7 的面颊/眼周排线盖住结构。只影响 hatch，波纹保留原亮度档。
_HATCH_TONE_GAMMA = 0.55
_HATCH_TONE_SIGMA = 2.2

# 排线结构线取原始亮度的双尺度差零交叉，不把均衡后的等亮度边界当五官。
# 工作栅格上 σ2/3.6 压掉衣物细纹；梯度 0.014 拒绝缓慢光照变化（等高线），
# 仍保留 2 张本机人像的镜框/眼睛/嘴边。证据见 2026-09-12 画质对比记录。
_STRUCTURE_SIGMA = 2.0
_STRUCTURE_COARSE_RATIO = 1.8
_STRUCTURE_MIN_CONTRAST = 0.014
# 滞回低阈（Canny 双阈思想）：零交叉段内只要带一个强锚点，弱段作为同一条
# 真实边缘的延续保留——五官/下颌这类低对比轮廓在旧单阈下被齐根切断或整段
# 消失（2026-09-25 用户「五官轮廓和碎边」反馈）。0.006/0.008/0.010 三档本机
# 人像裁切 A/B：0.006 前额碎波明显，0.008 保眉眼/下颌线且前额噪声回落，
# 0.010 更接近旧行为、五官续接变弱；孤立弱段无强锚点照旧丢弃，缓慢光照
# 等高线全程弱梯度仍被拒。
_STRUCTURE_WEAK_CONTRAST = 0.008
# 结构线沿线平滑（2026-10-08 用户「画质要改善」批）：软边缘（下颌阴影、面颊）
# 的零交叉在 σ2/3.6 尺度上蛇行，幅度 >1.2px 故 RDP 留不住；find_contours 出点
# 1pt/px 是密集折线，可沿线高斯平滑（与稀疏排线不同——排线禁止平滑，见
# polylines_to_svg(smooth=False)）。σ3px≈1mm：两张真人像 RDP 顶点 −11%/−10%，
# G-code −7%，画的还是同一组线，只是笔迹不再抖。闭环按环绕、开线端点近似保持。
_STRUCTURE_SMOOTH_SIGMA_PX = 3.0
# 排线禁入带：结构线两侧各留 2 工作像素（≈0.7mm）空白，排线与串链桥都不进。
# 真人像实测（2026-10-08 `halo-ab2/`）镜框/眼睛/鼻梁原被 2–4 层排线整片压住
# 不可读，留白后五官可辨，抬笔 −14%、字节 −11%；半径 2.5 时头发/眉毛排线
# 开始被结构线密集区掏空，故取 2.0。无结构线时禁入带为空，行为不变。
_HATCH_HALO_RADIUS_PX = 2.0

# 轮廓：高斯平滑亮度的等值线（find_contours 直出矢量，无骨架化）。
# 3 级 tone 边界 + 较强平滑：2026-09-10 小样 A/B（事故人像，4 组合目检）
# 定案——旧 4 级/σ1.2 在面部渐变区切出密集碎波（「地形图感」之源）；
# 3 级/σ2.0 杂乱碎线显著减少且五官辨识度不降。
_CONTOUR_LEVELS: tuple[float, ...] = (0.32, 0.52, 0.74)
_CONTOUR_MIN_POINTS = 12     # 短轮廓是噪声斑点，丢弃
_CONTOUR_SMOOTH_SIGMA = 2.0  # 轮廓前平滑，压照片噪点引起的抖线与面部碎波

# 波纹：行距 4px，振幅随暗度（1-g）调制，白区（暗度<_DARK_FLOOR）整行不画——
# 平线在白纸上照样画出墨痕，SquiggleTab 式「全图一根线」的代价是白区爬满
# 直线，故按行分段、暗区起波、亮区断开。
_SQUIGGLE_ROW_SPACING_PX = 4.0
_SQUIGGLE_WAVELENGTH_PX = 7.0
_SQUIGGLE_MAX_AMP_PX = 1.8   # < 行距一半，相邻行波峰永不互撞
_SQUIGGLE_DARK_FLOOR = 0.30  # 暗度低于此值（归一化后亮调）视为白区，行内断开
_SQUIGGLE_MIN_SEG_PX = 8.0
# 主体权重（2026-09-25 用户「照片生图质量还是不行」批）：谱残差显著性
# （Hou & Zhang 2007，纯 numpy FFT，零新依赖）→ 软掩码 [0,1]。低权重
# 背景区暗度按 _BG_DARKNESS_KEEP 比例保留，排线/波纹/结构线同步减墨——
# 「背景与主体同权重排线糊住人脸」是本批点名修的问题（09-12 画质报告
# 自认上限：无主体分割能力）。谱域在 ≤128px 栅格上算：显著性是大尺度量，
# 小图足够且天然平滑；<16px 谱域不可靠、纯色无动态范围，一律回落全 1
# （不抑制），保证纯白/纯黑 fail-closed 与既有测试行为逐点不变。
_SALIENCY_WORK_PX = 128
_SALIENCY_MIN_SIDE_PX = 16
_SALIENCY_SPECTRAL_SIGMA = 3.0  # 对数谱均值滤波尺度，残差 = 局部谱异常
_SALIENCY_MAP_SIGMA = 2.0       # 显著图平滑，压单像素噪斑
_SALIENCY_LO_PCT = 55.0         # 分位窗下沿：低于此视为背景
_SALIENCY_HI_PCT = 92.0         # 分位窗上沿：高于此视为确定主体
_SALIENCY_MIN_RANGE = 0.05      # 归一化显著图 hi-lo 低于此值判平坦 → 全 1
_SALIENCY_BLUR_PX = 6.0         # 权重图软化边界：排线密度渐变，无硬切口
_BG_DARKNESS_KEEP = 0.15        # 背景暗度保留比例；本机基线：hatch 背景墨
                                # 4.3%→≈0.4%、squiggle 14.3%→≈1%，主体不降
_STRUCTURE_SALIENCY_MIN = 0.20  # 结构线 run 平均权重低于此值判背景纹理，丢弃
# 暗部保底：谱残差只点亮主体边界、大色块内部平坦，单靠显著性会把深发/
# 黑衣主体内部也抹掉；归一化暗度 ≥0.64 的区域恒全额保留，0.45 以下才全交
# 显著性裁决。两值按「层叠语义测试」深灰带（归一化 ≈0.36 亮/暗 0.64）必须
# 保住第 1+2 层校准；中间调（0.3–0.45 暗）仍由显著性决定去留。
_DARK_PRIOR_LO = 0.45
_DARK_PRIOR_HI = 0.64


def _subject_keep_weight(saliency_weight: np.ndarray, smooth: np.ndarray) -> np.ndarray:
    """显著性权重 ∪ 暗部保底：返回 [0,1] 最终保留权重。smooth 是归一化
    平滑亮度（1=白）；暗区不看显著性直接保，亮/中间调交显著性裁决。"""
    dark_prior = np.clip(
        ((1.0 - smooth) - _DARK_PRIOR_LO) / (_DARK_PRIOR_HI - _DARK_PRIOR_LO),
        0.0,
        1.0,
    )
    return np.maximum(saliency_weight, dark_prior)


def _apply_background_suppression(smooth: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """按保留权重压低背景暗度（1=白 口径），软边界使密度渐变无硬切口。"""
    return 1.0 - (1.0 - smooth) * (_BG_DARKNESS_KEEP + (1.0 - _BG_DARKNESS_KEEP) * keep)


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
    from skimage.color import rgb2gray, rgba2rgb
    from skimage.util import img_as_float32

    if arr.ndim == 3 and arr.shape[-1] in (3, 4):
        rgb = img_as_float32(arr)
        if rgb.shape[-1] == 4:
            rgb = rgba2rgb(rgb, background=(1, 1, 1))
        gray = rgb2gray(rgb).astype(np.float32)
    elif arr.ndim == 2:
        gray = img_as_float32(arr)
    else:
        raise SketchRenderError("不支持的图片通道数")
    if gray.size == 0:
        raise SketchRenderError("图片为空")
    if not np.isfinite(gray).all():
        raise SketchRenderError("图片亮度包含非有限值")
    h, w = gray.shape
    scale = min(1.0, _WORK_WIDTH_PX / w, _WORK_MAX_HEIGHT_PX / h)
    if scale < 1.0:
        from skimage.transform import resize

        new_h = max(1, round(h * scale))
        new_w = max(1, round(w * scale))
        gray = resize(
            gray, (new_h, new_w), anti_aliasing=True
        ).astype(np.float32)
    return np.clip(gray, 0.0, 1.0)


def _saliency_subject_weight(gray: np.ndarray) -> np.ndarray:
    """谱残差显著性 → 主体权重软掩码 [0,1]，形状与 gray 相同。

    谱残差（Hou & Zhang 2007）：对数谱减均值滤波的局部异常即显著区，
    再取逆变换幅值平方为显著图。分位窗映射成权重并大尺度软化边界，
    使下游排线密度渐变、不出硬切口。平坦显著图（纯色、全黑、均匀渐变）
    与 <16px 小图一律回落全 1（不抑制），不改动既有 fail-closed 行为。
    """
    from skimage.filters import gaussian
    from skimage.transform import resize

    h, w = gray.shape
    if min(h, w) < _SALIENCY_MIN_SIDE_PX:
        return np.ones((h, w), dtype=np.float32)
    scale = min(1.0, _SALIENCY_WORK_PX / max(h, w))
    if scale < 1.0:
        small = resize(
            gray,
            (max(1, round(h * scale)), max(1, round(w * scale))),
            anti_aliasing=True,
        ).astype(np.float32)
    else:
        small = gray.astype(np.float32)
    spectrum = np.fft.fft2(small)
    log_amp = np.log(np.abs(spectrum) + 1e-8)
    residual = log_amp - gaussian(
        log_amp, sigma=_SALIENCY_SPECTRAL_SIGMA, preserve_range=True
    )
    sal = np.abs(np.fft.ifft2(np.exp(residual + 1j * np.angle(spectrum)))) ** 2
    sal = gaussian(sal, sigma=_SALIENCY_MAP_SIGMA, preserve_range=True)
    sal = sal / max(float(sal.max()), 1e-12)
    lo, hi = np.percentile(sal, [_SALIENCY_LO_PCT, _SALIENCY_HI_PCT])
    if hi - lo < _SALIENCY_MIN_RANGE:
        return np.ones((h, w), dtype=np.float32)
    weight = np.clip((sal - lo) / (hi - lo), 0.0, 1.0)
    if scale < 1.0:
        weight = resize(weight, (h, w), anti_aliasing=True).astype(np.float32)
    return gaussian(
        weight, sigma=_SALIENCY_BLUR_PX, preserve_range=True
    ).astype(np.float32)


def _filter_polys_by_weight(
    polys: list[np.ndarray],
    weight: np.ndarray,
    min_mean: float = _STRUCTURE_SALIENCY_MIN,
) -> list[np.ndarray]:
    """按折线顶点平均主体权重过滤：低权重区的纹理/背景轮廓线整条丢弃。

    全 1 权重（平坦图回落）一条不丢，向后兼容。坐标折线是 (x,y) 像素浮点，
    可能因 RDP 前插值出非整数点，取最近像素采样权重并裁剪进界。
    """
    h, w = weight.shape
    out: list[np.ndarray] = []
    for p in polys:
        xs = np.clip(np.round(p[:, 0]).astype(int), 0, w - 1)
        ys = np.clip(np.round(p[:, 1]).astype(int), 0, h - 1)
        if float(weight[ys, xs].mean()) >= min_mean:
            out.append(p)
    return out


def _normalize_tones(gray: np.ndarray, *, gamma: float = _TONE_GAMMA) -> np.ndarray:
    """保留全局层次并混入局部对比；近纯色原样返回，避免放大数值噪声。"""
    lo0, hi0 = np.percentile(gray, [_TONE_STRETCH_LO_PCT, _TONE_STRETCH_HI_PCT])
    if hi0 - lo0 < _TONE_STRETCH_MIN_RANGE:
        return gray
    from skimage.exposure import equalize_adapthist

    global_stretched = np.clip(
        (gray - lo0) / max(hi0 - lo0, 1e-6), 0.0, 1.0
    )
    clahe = equalize_adapthist(gray, clip_limit=_CLAHE_CLIP_LIMIT)
    lo, hi = np.percentile(clahe, [_TONE_STRETCH_LO_PCT, _TONE_STRETCH_HI_PCT])
    local_stretched = np.clip((clahe - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    mixed = 0.65 * global_stretched + 0.35 * local_stretched
    return (mixed ** gamma).astype(np.float32)


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


def _smooth_dense_polys(
    polys: list[np.ndarray], sigma: float = _STRUCTURE_SMOOTH_SIGMA_PX
) -> list[np.ndarray]:
    """密集轮廓折线沿线高斯平滑；闭环环绕、开线端点钉住（nearest）。

    只能用于 find_contours 这类 1pt/px 的密集点列；排线/波纹的稀疏端点经
    平滑会把回折挪进白区（2026-09-12 教训），调用方不得混入。
    """
    from skimage.filters import gaussian

    out: list[np.ndarray] = []
    for p in polys:
        if len(p) < 5 or sigma <= 0:
            out.append(p)
            continue
        if np.allclose(p[0], p[-1]):
            body = gaussian(p[:-1], sigma=(sigma, 0), mode="wrap", preserve_range=True)
            out.append(np.vstack([body, body[:1]]))
        else:
            out.append(gaussian(p, sigma=(sigma, 0), mode="nearest", preserve_range=True))
    return out


def _structure_halo(polys: list[np.ndarray], shape: tuple[int, int], radius: float) -> np.ndarray:
    """结构线栅格化并按半径膨胀 → 排线禁入带（bool，True=禁入）。"""
    from skimage.draw import line as draw_line
    from skimage.morphology import dilation

    h, w = shape
    mask = np.zeros((h, w), dtype=bool)
    for p in polys:
        q = np.rint(p).astype(int)
        for (x0, y0), (x1, y1) in zip(q[:-1], q[1:]):
            rr, cc = draw_line(int(y0), int(x0), int(y1), int(x1))
            ok = (rr >= 0) & (rr < h) & (cc >= 0) & (cc < w)
            mask[rr[ok], cc[ok]] = True
    if radius > 0 and mask.any():
        # 半径随 density_scale 是小数；自建圆盘足迹，不被 disk() 的整数半径放大。
        r = int(np.ceil(radius))
        yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
        mask = dilation(mask, footprint=(xx * xx + yy * yy) <= radius * radius)
    return mask


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
    """排线明暗场用 σ2.2 平滑，压掉阈值附近的照片噪点碎段。"""
    from skimage.filters import gaussian

    return gaussian(gray, sigma=_HATCH_TONE_SIGMA, preserve_range=True)


def _structure_contours(gray: np.ndarray) -> list[np.ndarray]:
    """原始亮度的结构边缘 → 连续矢量线；弱对比区断开，不勾描光照色块。"""
    from skimage.filters import gaussian, sobel
    from skimage.measure import find_contours

    if min(gray.shape) < 2:
        return []
    fine = gaussian(gray, sigma=_STRUCTURE_SIGMA, preserve_range=True)
    coarse = gaussian(
        gray, sigma=_STRUCTURE_SIGMA * _STRUCTURE_COARSE_RATIO, preserve_range=True,
    )
    response = fine - coarse
    contrast = sobel(fine)
    out: list[np.ndarray] = []
    for contour in find_contours(response, level=0):
        if len(contour) < _CONTOUR_MIN_POINTS:
            continue
        pixels = np.rint(contour).astype(int)
        strength = contrast[pixels[:, 0], pixels[:, 1]]
        weak = strength >= _STRUCTURE_WEAK_CONTRAST
        strong = strength >= _STRUCTURE_MIN_CONTRAST
        edges = np.flatnonzero(np.diff(np.r_[False, weak, False].astype(np.int8)))
        spans = edges.reshape(-1, 2)
        runs = [contour[start:end] for start, end in spans]
        anchored = [bool(strong[start:end].any()) for start, end in spans]
        # 闭环的起点是算法任意选的；跨起点的同一条可见边缘须合回，不能断两截。
        if len(runs) > 1 and weak[0] and weak[-1] and np.array_equal(contour[0], contour[-1]):
            runs = [np.vstack((runs[-1], runs[0][1:]))] + runs[1:-1]
            anchored = [anchored[-1] or anchored[0]] + anchored[1:-1]
        out.extend(
            run[:, ::-1].astype(np.float64)
            for run, keep in zip(runs, anchored)
            if keep and len(run) >= _CONTOUR_MIN_POINTS
        )
    return out


def _hatch_layer(
    mask: np.ndarray, angle_deg: float, spacing: float,
    offset: float = 0.0, min_run: float = _HATCH_MIN_RUN_PX,
) -> list[np.ndarray]:
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
    c = np.ceil((c_lo - offset) / spacing) * spacing + offset
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
            if run_len < min_run:
                continue
            out.append(
                np.array(
                    [[xs[start], ys[start]], [xs[end - 1], ys[end - 1]]],
                    dtype=np.float64,
                )
            )
        c += spacing
    return out

# 排线串链：相邻段在暗区边界处首尾仅隔一个层距（≈2-3mm），每段独立起笔
# 是纯浪费（事故人像 3672 抬笔）。贪心近邻串链把「间距 ≤ 上限且连线全程在
# 本层排线 mask 内」的段落笔连成一笔——连线藏在暗区内，视觉上
# 是蚀刻填充经典的边界蛇形回折，不污染白区。2026-09-10 用户拍板的提速项。
_CHAIN_MAX_GAP_PX = 10.0  # ≈3.6mm：串链连线上限


def _chain_hatch_segments(
    segs: list[np.ndarray], dark_mask: np.ndarray
) -> list[np.ndarray]:
    """2 点排线段 → 串链折线。贪心：从段 0 尾端出发，每步取最近未用端点，
    间距 ≤_CHAIN_MAX_GAP_PX 且连线半像素采样全在 dark_mask 内则落笔
    并入当前链（段必要时翻转），否则另起新链（=一次抬笔）。段序固定 +
    argmin 平局取小索引，同输入逐点复现。"""
    if len(segs) < 2:
        return segs
    mask = dark_mask
    mh, mw = mask.shape
    starts = np.array([s[0] for s in segs])
    ends = np.array([s[-1] for s in segs])
    used = np.zeros(len(segs), dtype=bool)

    def linkable(a: np.ndarray, b: np.ndarray) -> bool:
        # 三个分点会漏掉眼镜、衣领等窄留白；连线实际经过的像素也须全暗。
        count = int(np.ceil(np.max(np.abs(b - a)) * 2)) + 1
        xy = np.rint(np.linspace(a, b, count)).astype(int)
        if not ((xy[:, 0] >= 0) & (xy[:, 0] < mw) & (xy[:, 1] >= 0) & (xy[:, 1] < mh)).all():
            return False
        return bool(mask[xy[:, 1], xy[:, 0]].all())

    chains: list[np.ndarray] = []
    # 长链逐段vstack会反复复制已累积顶点；只保留分段引用，在换笔时一次拼接。
    # 近邻选择、翻转、白区校验和顶点顺序均保持，性能改动不改变纸面轨迹。
    cur = [segs[0]]
    used[0] = True
    pos = ends[0]
    for _ in range(len(segs) - 1):
        d_start = np.hypot(starts[:, 0] - pos[0], starts[:, 1] - pos[1])
        d_end = np.hypot(ends[:, 0] - pos[0], ends[:, 1] - pos[1])
        d_start[used] = np.inf
        d_end[used] = np.inf
        j_s = int(np.argmin(d_start))
        j_e = int(np.argmin(d_end))
        if d_start[j_s] <= d_end[j_e]:
            j, dist, nxt = j_s, d_start[j_s], segs[j_s]
        else:
            j, dist, nxt = j_e, d_end[j_e], segs[j_e][::-1]
        if dist <= _CHAIN_MAX_GAP_PX and linkable(pos, nxt[0]):
            cur.append(nxt)
            used[j] = True
            # nxt 可能反向；原段 ends[j] 此时是刚接入的头，不能再当作尾端。
            pos = nxt[-1]
        else:
            chains.append(np.concatenate(cur))
            free = np.flatnonzero(~used)
            j = int(free[np.argmin(np.hypot(starts[free, 0] - pos[0], starts[free, 1] - pos[1]))])
            cur = [segs[j]]
            used[j] = True
            pos = ends[j]
    chains.append(np.concatenate(cur))
    return chains


def render_hatch(image_bytes: bytes) -> tuple[list[np.ndarray], int, int]:
    """排线素描：原图结构线 + 四层累积排线，返回 (polys, w, h)。

    段数预算闸：CLAHE 后暗部多的照片排线段数可撞爆 §5 512KiB（2026-09-10
    事故原图 5508 段 → 575KiB 超闸 422）。段数 ≈ 字节/104（实测线性），超预算
    按段数比线性放大层距重渲一次（段数 ∝ 1/层距已实测），确定性收敛。
    """
    original = _load_gray(image_bytes)
    gray = _normalize_tones(original, gamma=_HATCH_TONE_GAMMA)
    h, w = gray.shape
    # 主体权重软掩码：背景暗度按比例压低，排线在低权重区自然稀疏，
    # 软边界让密度渐变、不出硬切口（2026-09-25 背景糊脸修复）；
    # 显著性 ∪ 暗部保底：深发/暗衣主体不看显著性直接保（首版单靠
    # 显著性把大色块主体内部误抹，保底按层叠语义校准）。
    weight = _saliency_subject_weight(original)
    smooth = _gaussian_for_mask(gray)
    smooth = _apply_background_suppression(smooth, _subject_keep_weight(weight, smooth))
    # 工作像素必须换算到同一长边尺度；720×1280 竖照的 6px 并非原设想的
    # 277mm/760×6≈2.2mm，而只有约 1.3mm。禁止靠调亮缩略图掩盖实际密度。
    density_scale = max(1.0, max(h, w) / _WORK_WIDTH_PX)
    # 结构线先于排线：沿线平滑后抽稀；其栅格膨胀成排线禁入带（五官/镜框
    # 不被排线压住，2026-10-08）。排线 mask 与串链 mask 同扣，桥也不跨结构线。
    polys = _simplify(
        _smooth_dense_polys(_filter_polys_by_weight(_structure_contours(original), weight)),
        _RDP_TOLERANCE_PX,
    )
    halo = _structure_halo(polys, smooth.shape, _HATCH_HALO_RADIUS_PX * density_scale)
    def make_layers(factor: float) -> list[list[np.ndarray]]:
        return [
            _hatch_layer((smooth < th) & ~halo, ang, sp * density_scale * factor,
                         (sp * density_scale * factor / 2) if i >= 2 else 0.0,
                         _HATCH_MIN_RUN_PX * density_scale)
            for i, (th, ang, sp) in enumerate(_HATCH_LAYERS)
        ]
    layers = make_layers(1.0)
    n_seg = sum(len(segs) for segs in layers)
    if n_seg > _HATCH_SEGMENT_BUDGET:
        factor = n_seg / _HATCH_SEGMENT_BUDGET
        layers = make_layers(factor)
    # 最亮层（面部稀疏排线）不串链：连线会落在明调区边缘成可见回折环
    # （2026-09-10 小样目检）；暗三层用本层 mask 串链，连线藏同暗度带内。
    polys.extend(layers[0])
    for (th, _, _), segs in zip(_HATCH_LAYERS[1:], layers[1:]):
        polys.extend(_chain_hatch_segments(segs, (smooth < th) & ~halo))
    if not polys:
        raise SketchRenderError("排线渲染产出为空（图片可能为纯色）")
    # 排线原本就是直线段；串链后再次 RDP 会切过刚校验的留白缺口。
    return polys, w, h


def _squiggle_link_stays_dark(start: np.ndarray, end: np.ndarray, darkness: np.ndarray) -> bool:
    """半像素步长检查整段回折线；只验两端会跨过两行之间的白色缺口。"""
    count = int(np.ceil(np.max(np.abs(end - start)) * 2)) + 1
    xy = np.rint(np.linspace(start, end, count)).astype(int)
    h, w = darkness.shape
    if not ((xy[:, 0] >= 0) & (xy[:, 0] < w) & (xy[:, 1] >= 0) & (xy[:, 1] < h)).all():
        return False
    return bool((darkness[xy[:, 1], xy[:, 0]] > _SQUIGGLE_DARK_FLOOR).all())


def render_squiggle(image_bytes: bytes) -> tuple[list[np.ndarray], int, int]:
    """连续波纹：行扫描线振幅随暗度调制，iso 轮廓补结构。返回 (polys, w, h)。

    波纹用连续暗度调制而非阈值 mask，不需要 mask 平滑；抽稀用更小的波形
    容差保住正弦振幅。
    """
    original = _load_gray(image_bytes)
    gray = _normalize_tones(original)
    h, w = gray.shape
    weight = _saliency_subject_weight(original)
    darkness = 1.0 - _apply_background_suppression(gray, _subject_keep_weight(weight, gray))
    chains: list[list[np.ndarray]] = []
    x = np.arange(w, dtype=np.float64)
    phase = 2.0 * np.pi * x / _SQUIGGLE_WAVELENGTH_PX
    rows = np.arange(_SQUIGGLE_ROW_SPACING_PX / 2, h, _SQUIGGLE_ROW_SPACING_PX)
    serpentine_prev_end: np.ndarray | None = None
    serpentine_prev_row = -2
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
        # 同行不同段之间和空行必须抬笔，回折连线全程也必须在暗区。
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
                and serpentine_prev_row == row_idx - 1
                and abs(seg[0, 1] - serpentine_prev_end[1]) <= _SQUIGGLE_ROW_SPACING_PX * 1.5
                and abs(seg[0, 0] - serpentine_prev_end[0]) <= w * 0.2
                and _squiggle_link_stays_dark(serpentine_prev_end, seg[0], darkness)
            ):
                chains[-1].append(seg)
            else:
                chains.append([seg])
            serpentine_prev_end = seg[-1]
            serpentine_prev_row = row_idx
    # 先收分段再拼接，避免纯暗区数百行蛇形链反复复制整幅顶点。
    polys = [np.concatenate(chain) for chain in chains]
    polys.extend(_filter_polys_by_weight(_iso_contours(gray), weight))
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

    return polylines_to_preview_png(polys, w, h, smooth=False)


def encode_png(gray_or_rgb: np.ndarray) -> bytes:
    """调试/调参用：ndarray → PNG 字节（生产链路不调用）。"""
    buf = io.BytesIO()
    iio.imwrite(buf, gray_or_rgb, extension=".png")
    return buf.getvalue()
