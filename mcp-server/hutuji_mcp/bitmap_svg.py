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
import sys
import zlib

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
        # exc.name 只在「找不到模块」时有值；依赖自身 __init__ 里抛裸 ImportError
        # （numpy/scipy 的 ABI 不匹配守卫正是这么写的）时它是 None，必须回落到异常
        # 文本，否则开机失败只剩「依赖缺失（None）」，唯一的线索被丢干净。
        text = str(exc).strip()
        detail = exc.name or (text.splitlines()[0][:120] if text else type(exc).__name__)
        raise BitmapError(
            f"位图描摹依赖缺失（{detail}），生产 venv 需安装 requirements.txt"
        ) from exc

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



# ---- ExecStartPre 沙箱内自检 ----------------------------------------------
#
# 为什么位图路线必须有独立自检：描摹重依赖（skimage/sknw/imageio）是
# `trace_bitmap_to_polylines` 里的**延迟 import**，缺失时 `import
# hutuji_mcp.server` 照样成功、systemd 照样 active、gcode_gen 的自检只碰
# vpype 一半，于是「服务 active + preflight 全绿 + 每次 hutuji_draw 都在
# 描摹期炸」三者可同时成立——正是 deploy/preflight.sh 开头记的那类
# fail-open。2026-08-09 本机实测复现：拦掉 imageio 后 server 可 import、
# `gcode_gen --self-test` 退 0，只有真实描摹抛 BitmapError。
#
# 自检**不碰网络**：生图是按次计费的外部依赖，开机自检不得调它。探针 PNG
# 由下面的纯 stdlib 编码器现造，故「依赖缺失」与「探针造不出来」不会混淆。
#
# 放在 systemd ExecStartPre（沙箱内）而不是只放 preflight：preflight 以
# root 在沙箱外跑，验不到 SystemCallFilter。skimage/scipy 的数值栈比 vpype
# 更宽，2026-08-07 已被 vpype 的 mbind/SIGSYS（exit -31）咬过一次，
# 同一类风险只有沙箱内的真实调用才能暴露。

_PROBE_SIZE_PX = 64
# 探针画 T 形：水平臂 + 垂直臂交于中部。轴对齐所以栅格化精确；T 形交叉点
# 会逼 sknw 做分支簇聚合（选它的原因就是这个），产出 3 条边、每条都远长于
# _MIN_POLY_PX，且 ≥_SMOOTH_WINDOW 故平滑分支也被覆盖。
_PROBE_STROKE_PX = 3


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return (
        len(payload).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")
    )


def _encode_probe_png() -> bytes:
    """造一张 8bit 灰度 T 形线稿 PNG。只用 zlib，不依赖 imageio。"""
    n = _PROBE_SIZE_PX
    rows = [bytearray(b"\xff" * n) for _ in range(n)]
    lo, hi = n // 8, n - n // 8            # 线条留白边，避免贴边被骨架化削掉
    bar = n // 2
    for r in range(bar, bar + _PROBE_STROKE_PX):        # 水平臂
        for c in range(lo, hi):
            rows[r][c] = 0
    for r in range(lo, bar + _PROBE_STROKE_PX):         # 垂直臂（落在水平臂中部）
        for c in range(bar, bar + _PROBE_STROKE_PX):
            rows[r][c] = 0

    raw = b"".join(b"\x00" + bytes(row) for row in rows)   # 每行前置 filter=0
    ihdr = n.to_bytes(4, "big") + n.to_bytes(4, "big") + b"\x08\x00\x00\x00\x00"
    return (
        _PNG_MAGIC
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


def _run_self_test() -> None:
    """在 systemd ExecStartPre 沙箱内真跑一次描摹链：依赖 + 数值栈 + 白名单。"""
    png = _encode_probe_png()
    polys, w, h = trace_bitmap_to_polylines(png)
    if w != _PROBE_SIZE_PX or h != _PROBE_SIZE_PX:
        raise BitmapError(f"描摹自检解码尺寸异常：{w}x{h}")
    svg = polylines_to_svg(polys, w, h)
    # 机器产物也过同一道白名单闸（模块不变量），顺带证明 llm_svg 校验器可用。
    _validate_svg(svg)
    if "<polyline" not in svg:
        raise BitmapError("描摹自检没有产出任何 polyline")


def _main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ["--self-test"]:
        print("usage: python -m hutuji_mcp.bitmap_svg --self-test", file=sys.stderr)
        return 2
    _run_self_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())