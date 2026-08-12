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
import json
import logging
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
# 单次尝试的完整上限：agnes 实测 10~22s/张，45s 仍留 >2× 余量。不许无限挂，
# 也不给 90s：占槽时长 = 3×本值 + 2×下载（DrawLimiter 并发只有 2），90s 时
# 最坏 ~334s，两个慢请求即堵死生成入口；45s 把最坏压到 ~199s。
_IMAGE_ATTEMPT_TIMEOUT_SECONDS = 45
# 下载单独给更紧的上限：≤16MB 的 PNG 走 30s 已极宽裕。下载不像生图——
# 重试不再计费，慢连接不值得占住 DrawLimiter 名额久等（并发只有 2）。
_IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 30
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
# 解码前的像素数上限。_IMAGE_DOWNLOAD_MAX_BYTES 限的是**压缩**字节，管不住像素数：
# 全白 PNG 压缩比实测 440~925:1，16MB 上限可携带上万 Mpx。而本模块的预处理链把
# 像素放大到远超裸图——2026-08-11 本机实测（results 探针，用后即删）：
#   灰度 4000² (16 Mpx)：压缩 20.6 KiB → gaussian 产 float64 128 MB → 峰值 272 MB
#   RGBA 4000² (16 Mpx)：压缩 67.7 KiB → rgba2rgb 产 3 通道 float64 384 MB → 峰值 1728 MB
# RGBA 是最坏路径（≈108 字节/像素峰值）。Pillow 自带的 DecompressionBomb 闸在
# 2×MAX_IMAGE_PIXELS=178,956,970 px 才报错，那之下的 170 Mpx RGBA 按实测系数已达
# ~18 GB，且 unit 没有 MemoryMax=，越界的是内核 OOM killer——它可能杀 nginx 或
# sshd 而不是本服务。故必须在 iio.imread 之前按 IHDR 自行拦。
# 取 5 Mpx：现行请求固定 1024²=1.05 Mpx，留 4.8× 余量并容纳 2048²=4.19 Mpx；
# 5 Mpx 的 RGBA 峰值 ≈540 MB，小 VPS 可承受。换更高分辨率生图模型必须显式抬这个
# 常量——4096² RGBA 在本链上真实要 ~1.8 GB，那是应当被迫做的决策，不该静默通过。
_MAX_IMAGE_PIXELS = 5_000_000
# 单边上限，独立于总像素上限。触发链（2026-08-12 本机实测，R7-BMP-01）：
# sknw 0.15 的 `idx2rc()` 用 `np.zeros(..., dtype=np.int16)` 存行/列坐标（GitHub
# 上游 Image-Py/sknw 同样是 int16），任一坐标超过 32767 即静默回绕成负数。
# 3×40000 的图总像素只有 12 万、远在 _MAX_IMAGE_PIXELS 之下，却在真实
# `build_sknw` 后产出 7232 个负坐标（范围 -32768..32767）——描摹结果被打乱，
# 而负坐标 SVG 既过 _validate_svg（白名单不查数值域），又被 vpype
# --fit-to-margins 归一化回页内，validate_gcode 照样 PASS。即：坐标域溢出不会
# 越界、不会报错，只会**静默画错**。故在解码前按 IHDR 单独拦，让它变成显式
# BitmapError。取 32768 是 int16 的正半域边界，不是可调画质参数。
_MAX_IMAGE_SIDE = 32768


def _reject_oversized_png(png_bytes: bytes) -> None:
    """按 PNG IHDR 的宽高在解码前拦下超大图，避免预处理链吃垮进程内存。

    只读头 24 字节，不解码：PNG 规范要求 IHDR 必须是第一个块，故宽高就在
    magic(8) + length(4) + type(4) 之后。调用方已校验过 magic。
    """
    if len(png_bytes) < 24:
        raise BitmapError("PNG 头不完整（不足 24 字节，读不到 IHDR）")
    if png_bytes[12:16] != b"IHDR":
        # 规范要求 IHDR 排第一；不在第一位说明文件畸形或不是真 PNG。
        # 这里 fail-closed，不去容错扫后续块——放过就等于放弃本闸。
        raise BitmapError("PNG 首块不是 IHDR（文件畸形）")
    width = int.from_bytes(png_bytes[16:20], "big")
    height = int.from_bytes(png_bytes[20:24], "big")
    if width <= 0 or height <= 0:
        raise BitmapError(f"PNG 宽高非法（{width}x{height}）")
    if width >= _MAX_IMAGE_SIDE or height >= _MAX_IMAGE_SIDE:
        # 必须排在总像素闸之前：细长图（如 3x40000）总像素远未超限，只有单边
        # 判据能拦，且诊断要指向真正的成因（坐标域）而不是内存。
        raise BitmapError(
            f"位图单边过大（{width}x{height}，单边上限 {_MAX_IMAGE_SIDE}）："
            "描摹坐标会溢出 sknw 的 int16 域并静默回绕成负值"
        )
    if width * height > _MAX_IMAGE_PIXELS:
        raise BitmapError(
            f"位图过大（{width}x{height}={width * height / 1e6:.1f} Mpx，"
            f"上限 {_MAX_IMAGE_PIXELS / 1e6:.1f} Mpx）：预处理会吃垮内存"
        )


def trace_bitmap_to_polylines(png_bytes: bytes) -> tuple[list[np.ndarray], int, int]:
    """PNG 字节 → 中心线折线列表（像素坐标）。纯函数，不碰网络/磁盘。

    延迟 import 重依赖（skimage/sknw）：模块被 import 的场合（如只跑
    LLM 路线的测试/生产旧调用面）不为用不到的功能付加载成本，缺失时报
    清晰的 BitmapError 而不是 ImportError 穿透。
    """
    try:
        import imageio.v3 as iio
        import sknw
        from skimage.color import rgb2gray, rgba2rgb
        from skimage.filters import gaussian, threshold_otsu
        from skimage.morphology import skeletonize
    except Exception as exc:  # noqa: BLE001
        # 刻意宽 catch：本块唯一的语句就是 import，不存在吞掉业务异常的风险，而
        # 依赖装坏并不只走 ImportError——numpy/scipy 的 ABI 失配经典报错是
        # `ValueError: numpy.dtype size changed`，C 扩展初始化失败还会抛
        # RuntimeError/SystemError/OSError。只 catch ImportError 时这些会裸穿出
        # 本模块，调用方（只准备接 BitmapError）变成 500，本 hunk 存在的全部意义
        # ——保住「装 requirements.txt」这句唯一可行动的诊断——就丢了一半入口。
        #
        # exc.name 只在「找不到模块」时有值；依赖自身 __init__ 里抛裸异常时它是
        # None 或不存在，必须回落到异常文本，否则只剩「装坏（None）」。文本优先
        # 取前三行拼接而不是首行截 120 字：numpy 的 ABI 报错可行动的部分常在
        # 第二三行，而消费者是 journald 与 preflight 的 `tail -1`，不必省字节。
        text = "; ".join(line.strip() for line in str(exc).strip().splitlines()[:3] if line.strip())
        detail = text or getattr(exc, "name", None) or "无异常文本"
        raise BitmapError(
            f"位图描摹依赖不可用（{type(exc).__name__}: {detail}）："
            "生产 venv 需安装 requirements.txt，若为 ABI 失配需重装 numpy/scipy 系依赖"
        ) from exc

    if not png_bytes.startswith(_PNG_MAGIC):
        raise BitmapError("生图 API 返回的不是 PNG")
    _reject_oversized_png(png_bytes)
    try:
        img = iio.imread(png_bytes)
        if img.ndim == 3 and img.shape[2] == 3:
            img = (rgb2gray(img) * 255).astype(np.uint8)
        elif img.ndim == 3 and img.shape[2] == 4:
            # RGBA 必须先按 alpha 合成再转灰，不能丢 alpha 通道：imageio 会把
            # palette+tRNS 解码成 RGBA，而线稿语义里透明即「无墨」，透明区的
            # 垃圾 RGB 直接当颜色用会被描成墨线。rgba2rgb 默认合成到白底，
            # 正合这个语义。
            img = (rgb2gray(rgba2rgb(img)) * 255).astype(np.uint8)
        elif img.ndim == 3:
            # LA（2 通道）等非常规通道数：rgb2gray 只接受 3 通道，会裸抛
            # ValueError，在本地拦成可诊断的 BitmapError。
            raise BitmapError(f"不支持的位图通道数：{img.shape}")
        elif img.ndim != 2:
            raise BitmapError(f"无法解码的位图形状：{img.shape}")
    except BitmapError:
        raise
    except Exception as exc:  # noqa: BLE001
        # 刻意宽 catch：imageio 各后端插件的解码异常类型不统一（签名通过但正文
        # 损坏时裸穿出插件自有异常，实测 OSError/ValueError 都有），而调用方只
        # 准备接 BitmapError。正文来自外部 CDN，异常文本可能回显字节内容，只记
        # 类型名。
        raise BitmapError(f"位图解码失败（{type(exc).__name__}）") from None
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
    if not settings.image_model.strip():
        # 与上面的 api_key 闸同理：空 model 名会原样发进 payload 被端点拒绝，
        # 错误要等一次计费请求才暴露；在本地拦下，诊断才指向环境变量本身。
        raise BitmapError("HUTUJI_IMAGE_MODEL 为空：生图请求会被端点拒绝")
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
                    try:
                        data = resp.json()
                    except json.JSONDecodeError:
                        # 200 但正文不是 JSON（如 CDN/网关的 HTML 错误页）：
                        # json.JSONDecodeError 是 ValueError 而非 httpx 异常，
                        # 不拦会裸穿出本模块；重试同一份垃圾正文无意义，直接失败。
                        raise BitmapError("生图 API 响应不是合法 JSON") from None
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

        # 下载给一次（且仅一次）重试：重新 GET 不再计费（重试 POST 才会），
        # CDN 瞬态断连值得一试；延迟沿用 _RETRY_DELAYS_SECONDS 首段，不另起常量。
        # 错误处理与生图 POST 同口径：httpx 异常只记类型名，不碰可能回显的 URL。
        #
        # 必须流式读：非流式 GET 返回时 body 已被 httpx 全量缓冲，事后再按
        # len(body) 判上限拦不住内存暴涨（CDN/上游异常返超大正文即打爆 VPS
        # 内存）。流式下 Content-Length 声明闸真正省流量，逐块累计上限兜住
        # 缺头（chunked）或头部畸形——两道闸都在数据进内存前生效。
        retry_download = False
        for attempt in range(2):
            try:
                # 必须把 asyncio.timeout 包在 stream 外：httpx timeout= 只管两次 chunk
                # 间静默间隔，不管总时长；慢速攻击（1KB/s 持续流）可让下载挂 30 分钟。
                # 总超时必须覆盖 stream 建立 + 整个 aiter_bytes 循环 + body 累加。
                async with asyncio.timeout(_IMAGE_DOWNLOAD_TIMEOUT_SECONDS):
                    async with client.stream("GET", image_url) as dl:
                        if dl.status_code == 200:
                            declared = dl.headers.get("content-length")
                            if (
                                declared
                                and declared.isdigit()
                                and int(declared) > _IMAGE_DOWNLOAD_MAX_BYTES
                            ):
                                raise BitmapError("图片超过下载上限")
                            body = bytearray()
                            async for chunk in dl.aiter_bytes():
                                if len(body) + len(chunk) > _IMAGE_DOWNLOAD_MAX_BYTES:
                                    raise BitmapError("图片超过下载上限")
                                body.extend(chunk)
                            return bytes(body)
                        if dl.status_code in _TRANSIENT_HTTP_STATUSES and attempt == 0:
                            retry_download = True
                        else:
                            raise BitmapError(f"图片下载返回 {dl.status_code}")
            except (httpx.HTTPError, TimeoutError) as exc:
                if attempt == 0:
                    retry_download = True
                else:
                    raise BitmapError(f"图片下载网络错误（{type(exc).__name__}）") from None
            if retry_download:
                retry_download = False
                await asyncio.sleep(_RETRY_DELAYS_SECONDS[0])


async def generate_bitmap_svg(description: str) -> str:
    """hutuji_draw 的现行生成入口：description → 单线 SVG（已过白名单校验）。"""
    safe_desc = _sanitize_description(description)
    png = await _fetch_image_png(safe_desc)
    # R3 并发漂移：描摹跑在线程池里，Python 线程无法被强制取消。若直接
    # `await asyncio.to_thread(...)`，请求被取消时 await 立刻抛 CancelledError，
    # server.DrawLimiter.slot() 的 finally 随即 leave() 释放并发名额，而描摹线程
    # 仍在后台烧 CPU——并发实数超过 max_concurrent=2（可用性 DoS 面）。用
    # asyncio.shield 把线程完成与外层取消解耦：取消时先等线程真正跑完再把
    # CancelledError 上抛，于是 leave() 永远晚于线程结束，并发计数与在跑线程数
    # 始终一致。代价：取消方最多多等一次描摹时长（本机实测 ~73ms）。
    trace = asyncio.ensure_future(asyncio.to_thread(trace_bitmap_to_polylines, png))
    try:
        polys, w, h = await asyncio.shield(trace)
    except asyncio.CancelledError:
        # shield 只中断了本协程的等待，未取消 trace 本身；等线程自然收尾
        # （连同它可能抛出的 BitmapError 一并吞掉），再让取消继续传播。
        #
        # R7-CANCEL-01：这里必须能抗**二次**取消。shield 只解耦第一次传播，
        # 裸 `await trace` 会被第二次取消打断，于是本协程在线程收尾前就返回，
        # 上面那句「leave() 永远晚于线程结束」不再成立。二次取消是可达的：
        # 客户端先发 notifications/cancelled（第一次），随后断连触发 MCP SDK
        # 的 tg.cancel_scope.cancel() 取消全部在途 handler（第二次）。
        # 故循环等待并对每次取消调 uncancel()——不 uncancel 时 Task 的取消计数
        # 不归零，后续 await 会被持续重抛（CPython #93453 同型）。取消语义不丢：
        # 无论中途被取消几次，出口恒为 CancelledError。
        current = asyncio.current_task()
        while not trace.done():
            try:
                await asyncio.shield(trace)
            except asyncio.CancelledError:
                if current is not None:
                    current.uncancel()
            except BaseException:  # noqa: BLE001
                # 线程自身的异常（如 BitmapError）在取消路径上无消费者，
                # 吞掉即可；trace 已 done，循环随即退出。
                break
        raise
    svg = polylines_to_svg(polys, w, h)
    if len(svg.encode("utf-8")) > _SVG_MAX_BYTES:
        raise BitmapError("描摹 SVG 超过 256KiB 上限（图太复杂）")
    _validate_svg(svg)
    return svg


# ---- ExecStartPre 沙箱内自检 ----------------------------------------------
#
# 为什么位图路线必须有独立自检：描摹重依赖（skimage/sknw/imageio）是
# `trace_bitmap_to_polylines` 里的**延迟 import**，缺失时 `import
# hutuji_mcp.server` 照样成功、systemd 照样 active、gcode_gen 的自检只覆盖生成链
# 依赖（vpype 系）、完全不碰描摹依赖，于是「服务 active + preflight 全绿 + 每次
# hutuji_draw 都在描摹期炸」三者可同时成立——正是 deploy/preflight.sh 开头记的那类
# fail-open。2026-08-09 本机实测复现：拦掉 imageio 后 server 可 import、
# `gcode_gen --self-test` 退 0，只有真实描摹抛 BitmapError。
#
# 自检**不碰网络**：生图是按次计费的外部依赖，开机自检不得调它。探针 PNG
# 由下面的纯 stdlib 编码器现造，故「依赖缺失」与「探针造不出来」不会混淆。
#
# 放在 systemd ExecStartPre（沙箱内）而不是只放 preflight：preflight 以
# root 在沙箱外跑，验不到 SystemCallFilter。skimage/scipy 的数值栈比 vpype
# 更宽，2026-08-07 已被 vpype 的 mbind/SIGSYS（exit -31）咬过一次，
# 同一类风险只有沙箱内的真实调用才能暴露。探针刻意取 _IMAGE_SIZE 的边长（见
# _PROBE_SIZE_PX），故数值栈处理的数组量级与真实请求一致——小探针（64px）几乎
# 走不到 scipy/OpenMP 的并行分支，对「沙箱漏项」的覆盖只是碰运气。
#
# 代价要写明：本条把「缺描摹依赖只坏位图路线」升级为「缺描摹依赖整个 MCP 服务
# 起不来」，是拿可用性换确定性。这是有意的——位图路线是 hutuji_draw 的唯一现行
# 生成路线（llm_svg 只留给 hutuji_preview_svg 调试），带病 active 的服务对用户
# 表现为「每次画都失败」，与不起来等价，但会骗过一切「服务在跑」式的检查。

# 探针边长取 _IMAGE_SIZE（1024）的边长：与真实生图输出同量级，让自检真的经过
# 大数组/多线程路径。实测描摹本体仅 ~73ms（冷启重依赖 import 才是 ~4.5s，与探针
# 尺寸无关），故这个尺寸不构成启动预算问题。
_PROBE_SIZE_PX = 1024
# 探针画 ⊥ 形（竖臂自横臂中点向上）：水平臂 + 垂直臂交于中部。轴对齐所以栅格化
# 精确；交叉点会逼 sknw 做分支簇聚合（选它的原因就是这个），产出 3 条边、每条都
# 远长于 _MIN_POLY_PX（实测 368/383/385，30× 余量，依赖升级动阈值也不会误红），
# 且 ≥_SMOOTH_WINDOW 故平滑分支也被覆盖。
_PROBE_STROKE_PX = 17


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    """按 PNG 规范拼一个 chunk：长度（数据段、4 字节大端）+ 类型 + 数据 + CRC32。

    CRC 的覆盖范围是**类型 + 数据**，不含长度字段——这是最易写错的一处。
    """
    body = kind + payload
    return (
        len(payload).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")
    )


def _encode_probe_png() -> bytes:
    """造一张 8bit 灰度 ⊥ 形线稿 PNG。只用 zlib，不依赖 imageio。"""
    # _PNG_MAGIC 的主用途是 startswith 嗅探，那里 4 字节 `\x89PNG` 也够用；当签名
    # 写时少了 `\r\n\x1a\n`，多数解码器直接拒收，且失败会伪装成「依赖装坏」。
    if len(_PNG_MAGIC) != 8:
        raise BitmapError(f"_PNG_MAGIC 必须是完整 8 字节 PNG 签名，当前 {len(_PNG_MAGIC)}")
    n = _PROBE_SIZE_PX
    rows = [bytearray(b"\xff" * n) for _ in range(n)]
    lo, hi = n // 8, n - n // 8            # 线条留白边，避免贴边被骨架化削掉
    bar = n // 2                           # 水平臂上沿所在行
    v_end = bar + _PROBE_STROKE_PX         # 竖臂末端与水平臂下沿齐平（形成 ⊥ 交叉）
    for r in range(bar, bar + _PROBE_STROKE_PX):        # 水平臂
        for c in range(lo, hi):
            rows[r][c] = 0
    for r in range(lo, v_end):                          # 垂直臂（落在水平臂中部）
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
    """在 systemd ExecStartPre 沙箱内真跑一次描摹链：依赖 + 数值栈 + 白名单。

    自检必须保持**封闭**（不碰网络、不读环境配置）：同一个 `--self-test` 入口被
    两处复用——unit 的 ExecStartPre（有 EnvironmentFile 喂环境）与 preflight 的
    check_bitmap_chain（root 直跑，刻意不 source mcp.env，该约束由
    test_contract.py 钉死）。自检一旦读 settings，preflight 在无凭据的 root 环境
    里必红，健康机器也过不了体检。凭据存在性由 preflight 的 check_mcp_env 覆盖
    （mcp.env 无占位 + LLM 三件套非空），模型名空白由 _fetch_image_png 的本地闸
    与 check_mcp_env 的 image_model 检查双层兜底——都不需要自检代劳。

    断言刻意「松于单测、严于无」：生产启动路径不该因 skimage 小版本让骨架多出一
    条短枝就把服务拖死（脆性压到 CI 的 test_contract.py 去），但必须钉住与实现
    无关的三条不变量——尺寸、⊥ 形必然分支（≥2 条）、前景极性。
    """
    png = _encode_probe_png()
    polys, w, h = trace_bitmap_to_polylines(png)
    if w != _PROBE_SIZE_PX or h != _PROBE_SIZE_PX:
        raise BitmapError(f"描摹自检解码尺寸异常：{w}x{h}")
    # polys 直接查，不靠 "<polyline" in svg 反推：⊥ 形交叉必然分支，只出一条链
    # 说明数值栈坏了但没崩（退化态），那是假绿。留 ≥2 而不是 ==3 给版本余量。
    if len(polys) < 2:
        raise BitmapError(f"描摹自检只得到 {len(polys)} 条线：⊥ 形交叉必然分支，数值栈可疑")
    # 极性断言：探针是白底黑线，前景判据一旦反向，背景会变成前景、骨架化出一堆
    # 垃圾却仍能产出 polyline（实测反极性得 1 条 272 点的链、包围盒溢出到 7,7）。
    # 所有点必须落在线条的画布范围内，这是最廉价的方向性钉子。
    lo, hi = _PROBE_SIZE_PX // 8, _PROBE_SIZE_PX - _PROBE_SIZE_PX // 8
    margin = _PROBE_STROKE_PX
    for poly in polys:
        if (
            poly[:, 0].min() < lo - margin or poly[:, 0].max() > hi + margin
            or poly[:, 1].min() < lo - margin or poly[:, 1].max() > hi + margin
        ):
            raise BitmapError(
                "描摹自检包围盒溢出探针线条范围：前景/背景极性可能反了"
            )
    svg = polylines_to_svg(polys, w, h)
    # 机器产物也过同一道白名单闸（模块不变量），顺带证明 llm_svg 校验器可用。
    _validate_svg(svg)
    if "<polyline" not in svg:
        raise BitmapError("描摹自检没有产出任何 polyline")
    # 打印被执行代码的真实位置：preflight 走 PYTHONPATH 的源码树、systemd 走 unit
    # 里的解释器，两者若解析到不同副本（venv 里另有 site-packages 装的 hutuji_mcp），
    # 「真跑当前落盘的代码」这个卖点就是空的。preflight 拿这行断言路径。
    print(f"bitmap self-test OK module={__file__}")


def _main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ["--self-test"]:
        print("usage: python -m hutuji_mcp.bitmap_svg --self-test", file=sys.stderr)
        return 2
    try:
        _run_self_test()
    except BitmapError as exc:
        # 刻意压成单行 stderr 而不是让 BitmapError 裸抛 traceback：preflight 的
        # check_bitmap_chain 只取输出最后一行当诊断，多行 traceback 会让最有用的
        # 那句话被淹掉。改这里必须同步那条 check（勿改成多行输出）。
        print(f"bitmap self-test FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
