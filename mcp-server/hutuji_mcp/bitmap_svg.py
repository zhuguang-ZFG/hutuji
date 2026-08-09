"""位图生成路线：文生图 → 中心线描摹 → 单线 SVG（hutuji_draw 的现行路线）。

为什么取代 LLM 直写 SVG：2026-08-09 双路线实测（results/ 探针，证据登记在
docs/worktree-inventory.md）——LLM 直写 5/6 可辨认且方差大（语义质量无解，
构图散布已由管线 layout 归一化兜底）；agnes-image-2.1-flash + 线稿 prompt
6/6 可辨认，描摹后全部通过 §5 校验。

描摹为什么用 skimage skeletonize + sknw 建图而不是 potrace/手写走查：
potrace 系描的是色块轮廓（双线，写字机会描两遍）；手写逐像素走查会被
Guo-Hall 骨架的 2×2 块干掉（51% 像素度数≠2 被误判为交叉 → 虚线化）。
sknw 把多像素交叉簇正确聚成图节点，是本路径的成熟组件。

本模块生成的 SVG 是机器产物，但仍过 `_validate_svg` 白名单——「一切进
vpype 管线的 SVG 都过同一道闸」是不变量，不因来源可信而豁免。
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx
import numpy as np

from .config import settings
from .llm_svg import _sanitize_description, _validate_svg

logger = logging.getLogger(__name__)


class BitmapError(RuntimeError):
    """位图路线生成失败（生图 API、下载、描摹任一环节）。"""


# 生图 prompt 前缀：把用户主题约束成可描摹的线稿。2026-08-09 探针实测
# 这套措辞在 agnes-image-2.1-flash 上 6/6 出干净 coloring-book 线稿。
_BITMAP_STYLE = (
    "单色线稿，纯黑线条纯白背景，coloring book line art，单线简笔画风格，"
    "线条清晰连贯，无阴影无灰度无填色无色块，构图居中，主体占画面大部分，主题："
)

_IMAGE_SIZE = "1024x1024"
# 单次尝试的完整上限：agnes 实测 10~22s/张，留足余量但不许无限挂。
_IMAGE_ATTEMPT_TIMEOUT_SECONDS = 90
_IMAGE_DOWNLOAD_MAX_BYTES = 16 * 1024 * 1024
# 与 LLM 路线同一口径：瞬态状态码重试两次（1s/3s），其余立即失败。
_TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
_RETRY_DELAYS_SECONDS = (1.0, 3.0)
# 描摹 SVG 上限：与 LLM 路线的 256KiB 同口径（_validate_svg 不管大小，
# 上限一直是本模块自己的职责；最重的自行车实测 ~200KB，离顶还有余量）。
_SVG_MAX_BYTES = 256 * 1024
# 骨架链短于此视为噪点；滑动平均窗口压骨架阶梯抖动（探针调参定稿值）。
_MIN_POLY_PX = 12
_SMOOTH_WINDOW = 5

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def trace_bitmap_to_polylines(png_bytes: bytes) -> tuple[list[np.ndarray], int, int]:
    """PNG 字节 → 中心线折线列表（像素坐标）。纯函数，不碰网络/磁盘。

    延迟 import 重依赖（skimage/sknw）：模块被 import 的场合（如只跑
    LLM 路线的测试/生产旧调用面）不为用不到的功能付加载成本，缺失时报
    清晰的 BitmapError 而不是 ImportError 穿透。
    """
    try:
        import imageio.v3 as iio
        import sknw
        from skimage.color import rgb2gray
        from skimage.filters import gaussian, threshold_otsu
        from skimage.morphology import skeletonize
    except ImportError as exc:
        raise BitmapError(f"位图描摹依赖缺失（{exc.name}），生产 venv 需安装 requirements.txt") from exc

    if not png_bytes.startswith(_PNG_MAGIC):
        raise BitmapError("生图 API 返回的不是 PNG")
    img = iio.imread(png_bytes)
    if img.ndim == 3:
        img = (rgb2gray(img) * 255).astype(np.uint8)
    elif img.ndim != 2:
        raise BitmapError(f"无法解码的位图形状：{img.shape}")
    h, w = img.shape

    blur = gaussian(img, sigma=1.0, preserve_range=True)
    binary = blur < threshold_otsu(blur)  # 线条为 True
    skel = skeletonize(binary)
    graph = sknw.build_sknw(skel.astype(np.uint16))
    polys: list[np.ndarray] = []
    for _u, _v, data in graph.edges(data=True):
        pts = data["pts"]  # (N, 2)，(row, col)
        if len(pts) < _MIN_POLY_PX:
            continue
        polys.append(np.array([(x, y) for y, x in pts], dtype=float))
    if not polys:
        raise BitmapError("位图描摹没有得到任何线条（生图可能不是线稿）")
    return polys, w, h


def polylines_to_svg(polys: list[np.ndarray], width: int, height: int) -> str:
    """折线列表 → 单线 SVG 文本（元素只含 svg/polyline，过 _validate_svg）。"""
    parts = []
    for pts in polys:
        if len(pts) >= _SMOOTH_WINDOW:
            kernel = np.ones(_SMOOTH_WINDOW) / _SMOOTH_WINDOW
            xs = np.convolve(pts[:, 0], kernel, mode="same")
            ys = np.convolve(pts[:, 1], kernel, mode="same")
            xs[:2], ys[:2] = pts[:2, 0], pts[:2, 1]
            xs[-2:], ys[-2:] = pts[-2:, 0], pts[-2:, 1]
            pts = np.stack([xs, ys], axis=1)
        str_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        parts.append(f'<polyline points="{str_pts}"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="190mm" height="277mm" '
        f'viewBox="0 0 {width} {height}" fill="none" stroke="black" stroke-width="1">'
        + "".join(parts)
        + "</svg>"
    )


async def _fetch_image_png(description: str, *, transport: httpx.AsyncBaseTransport | None = None) -> bytes:
    """调 OpenAI 兼容 images/generations 并下载 PNG。transport 仅供测试注入。"""
    if not settings.llm_api_key:
        raise BitmapError(
            "未配置 LLM API key：请设置环境变量 HUTUJI_LLM_API_KEY"
            "（位图路线与生图共用同一端点凭据）"
        )
    url = f"{settings.llm_base_url.rstrip('/')}/images/generations"
    payload = {
        "model": settings.image_model,
        "prompt": _BITMAP_STYLE + description,
        "size": _IMAGE_SIZE,
        "n": 1,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}

    async with httpx.AsyncClient(
        timeout=_IMAGE_ATTEMPT_TIMEOUT_SECONDS, transport=transport
    ) as client:
        data = None
        for attempt in range(len(_RETRY_DELAYS_SECONDS) + 1):
            try:
                async with asyncio.timeout(_IMAGE_ATTEMPT_TIMEOUT_SECONDS):
                    resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    break
                # 上游错误正文可能回显凭据；不读取不记录，只记状态码。
                if (
                    resp.status_code in _TRANSIENT_HTTP_STATUSES
                    and attempt < len(_RETRY_DELAYS_SECONDS)
                ):
                    await asyncio.sleep(_RETRY_DELAYS_SECONDS[attempt])
                    continue
                raise BitmapError(f"生图 API 返回 {resp.status_code}")
            except (httpx.HTTPError, TimeoutError) as exc:
                if attempt < len(_RETRY_DELAYS_SECONDS):
                    await asyncio.sleep(_RETRY_DELAYS_SECONDS[attempt])
                    continue
                raise BitmapError(f"生图 API 网络错误（{type(exc).__name__}）") from None
        if data is None:
            raise BitmapError("生图 API 未返回数据")
        try:
            image_url = data["data"][0]["url"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BitmapError(f"生图 API 响应结构异常（{type(exc).__name__}）") from exc
        if not isinstance(image_url, str) or not image_url.startswith("https://"):
            raise BitmapError("生图 API 未返回有效的 https 图片地址")

        dl = await client.get(image_url)
        if dl.status_code != 200:
            raise BitmapError(f"图片下载返回 {dl.status_code}")
        body = dl.content
        if len(body) > _IMAGE_DOWNLOAD_MAX_BYTES:
            raise BitmapError("图片超过下载上限")
        return body


async def generate_bitmap_svg(description: str) -> str:
    """hutuji_draw 的现行生成入口：description → 单线 SVG（已过白名单校验）。"""
    safe_desc = _sanitize_description(description)
    png = await _fetch_image_png(safe_desc)
    polys, w, h = await asyncio.to_thread(trace_bitmap_to_polylines, png)
    svg = polylines_to_svg(polys, w, h)
    if len(svg.encode("utf-8")) > _SVG_MAX_BYTES:
        raise BitmapError("描摹 SVG 超过 256KiB 上限（图太复杂）")
    _validate_svg(svg)
    return svg
