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

from collections.abc import Callable, Coroutine
from collections import OrderedDict
import asyncio
import base64
import binascii
import json
import logging
import math
import time
import sys
import zlib
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import anyio
import httpx
import numpy as np

from .config import settings
from .llm_svg import _sanitize_description, _validate_svg
from .url_guard import UrlGuardError, validate_public_https_url

logger = logging.getLogger(__name__)


class BitmapError(RuntimeError):
    """位图路线生成失败（生图 API、下载、描摹任一环节）。"""


class BitmapBusyError(BitmapError):
    """位图路线「瞬态全忙」：所有对冲通道同一轮都因限流/5xx/超时失败。

    与普通 BitmapError 区分开是给两端用的：/internal/draw 把它映射成
    429+固定白话（「通道忙」而不是「服务不可用」），生成链对它做一次
    短延迟真重跑；内容审核类 400 属永久失败，不进这个类。队列模式按
    类名重建异常，故 draw_queue._exception_from_job 须同步登记这个名字。
    """


@dataclass(frozen=True)
class BitmapResult:
    """同一次中心线描摹的机械 SVG 与设备屏幕 PNG 预览。"""

    svg: str
    preview_png: bytes


_PREVIEW_MAX_WIDTH = 320
_PREVIEW_MAX_HEIGHT = 220
_PREVIEW_PADDING = 6
# 2048² float32 栅格最多 16MiB；保留现役 1024/2048 生图的 1px 墨密度，
# 用户 SVG 的 viewBox 可任意大，超出时只缩预览坐标，不能按声明尺寸分配。
_PREVIEW_RASTER_MAX_SIDE = 2048

# 生图 prompt 前缀：把用户主题约束成适合中心线描摹和笔式绘图仪落纸的线稿。
# 2026-08-29：在既有纸面风险闸之外前移辅助约束；不把 prompt 当确定性闸门。
_BITMAP_STYLE = (
    "适合笔式绘图仪描摹（plotter-friendly）的单色线稿，纯黑线条纯白背景，coloring book line art，"
    "简洁的稀疏连续的轮廓线，线宽均匀，主体完整居中并占画面大部分；不要双轮廓，不要排线，不要纹理，"
    "不要阴影、灰度、填色或色块，不要无语义的孤立小线段和密集碎线；必要的结构细节要与主体轮廓相连；主题："
)

_IMAGE_SIZE = "1024x1024"
_SENSENOVA_IMAGE_SIZE = "1760x2368"
# 2026-10-12：上游聚合器下架 u1-fast，商汤路仅剩 sensenova-u1.5-lite——实测
# 29~64s/张（1760×2368 与 1024² 各测两轮，1024² 反而更慢）且响应无 url、只有
# b64_json。75s 覆盖 ≥1.2× 最坏实测值；单次尝试不重试——u1.5-lite 出图慢，同端点
# 重试会把 75s 翻倍并撞穿桥接看门狗 300s，对冲轮的「全忙重跑」本身就是它的重试。
# 旧值 20s/(0.5,) 是 u1-fast 7~8s 时代的口径（2026-08-28 bench.json）。
_SENSENOVA_ATTEMPT_TIMEOUT_SECONDS = 75.0
_SENSENOVA_RETRY_DELAYS_SECONDS: tuple[float, ...] = ()
# b64_json 内联图：实测 1760×2368 响应 JSON ~6.2MB；10MiB 留 ~1.5× 余量，且解码
# 结果 ≤7.5MB 仍在 16MiB 下载上限内。仅商汤路放宽；其余端点维持 64KiB JSON 闸
# （R10-BMP-03 的流式上限口径不变）。
_SENSENOVA_RESPONSE_MAX_BYTES = 10 * 1024 * 1024
# SiliconFlow 双 racer：schnell（4 步，速度标杆）与可图 Kolors（12 步，已备案
# Beijing-KeTu-202404280003，中文提示词原生）同 key 并发对冲。单次尝试不重试——
# 其他 racer 就是它的重试。官方 OpenAPI：POST /v1/images/generations，响应
# images[0].url（URL 有效期 1 小时，本链拿到即下载，兼容）。
_SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
_SILICONFLOW_SCHNELL_MODEL = "black-forest-labs/FLUX.1-schnell"
_SILICONFLOW_KOLORS_MODEL = "Kwai-Kolors/Kolors"
_SILICONFLOW_KOLORS_STEPS = 12
_SILICONFLOW_KOLORS_GUIDANCE = 5.0
_SILICONFLOW_ATTEMPT_TIMEOUT_SECONDS = 20.0
# 相同规范化主题的完整中心线结果缓存：设备/用户常重复「五角星」「小猫」，命中
# 后只需 vpype（生产实测 ~0.7s），不重复付费生图、下载和描摹。TTL 与能力 URL
# 同为 600s；缓存仅存内存中的 SVG/预览，不延长任何落盘 capability 的有效期。
_BITMAP_CACHE_TTL_SECONDS = 600.0
_BITMAP_CACHE_MAX_ENTRIES = 16
_bitmap_cache: OrderedDict[str, tuple[float, BitmapResult]] = OrderedDict()
# 同题在途合并覆盖完整 BitmapResult，而非只覆盖付费 PNG 请求。客户端取消后，
# 生成、描摹与缓存仍由这张任务负责收尾；随后重试直接搭车或命中缓存。
_bitmap_inflight: dict[str, asyncio.Task[BitmapResult]] = {}
_bitmap_cache_clock = time.monotonic

# 单次尝试的完整上限：agnes 实测 10~22s/张，45s 仍留 >2× 余量。不许无限挂，
# 也不给 90s：主链最坏 3×45+2×30 下载 ≈ 195s；含备用单次尝试 ≈ 240s，仍低于
# 桥接看门狗 300s（WATCHDOG_STALL_SECONDS，超时判死重连会丢掉在途 draw 应答）。
# 商汤路另按 u1.5-lite 慢通道单列 75s 单次尝试（见其常量注释）：一轮对冲最坏
# 75+2.5+75≈152.5s，与主链同口径低于 300s。
_IMAGE_ATTEMPT_TIMEOUT_SECONDS = 45
# 下载单独给更紧的上限：≤16MB 的 PNG 走 30s 已极宽裕。下载不像生图——
# 重试不再计费，慢连接不值得占住 DrawLimiter 名额久等（课堂级默认并发 8）。
_IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 30
_IMAGE_DOWNLOAD_MAX_BYTES = 16 * 1024 * 1024
# 与 LLM 路线同一口径：瞬态状态码重试两次（1s/3s），其余立即失败。
_TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
_RETRY_DELAYS_SECONDS = (1.0, 3.0)
# 备用生图端点（imagefree 等免鉴权公共服务）的画幅：1:1 = 1024x1024，与 _IMAGE_SIZE
# 对齐。2026-09-09：阿里云出口 IP 被 imagefree 风控 403（AUTH.003），默认 URL 已清空。
# 同尺寸（其文档 /v1/meta 可查）；改 _IMAGE_SIZE 宽高比时同步改这里。该端点文档
# 典型耗时 20~45s，与 _IMAGE_ATTEMPT_TIMEOUT_SECONDS 同量级——降级链比主链更容易
# 撞超时，属预期（降级宁慢勿错）。
_FALLBACK_ASPECT_RATIO = "1:1"
# 描摹 SVG 上限：与 LLM 路线的 256KiB 同口径（_validate_svg 不管大小，
# 上限一直是本模块自己的职责；最重的自行车实测 ~200KB，离顶还有余量）。
_SVG_MAX_BYTES = 256 * 1024
# 骨架链短于此视为噪点；滑动平均窗口压骨架阶梯抖动（探针调参定稿值）。
_MIN_POLY_PX = 12
_SMOOTH_WINDOW = 5
# 像素域 Douglas-Peucker 容差（2026-08-26 小路卡顿/没线）：骨架折线是 1px 阶梯，
# layout 后 G1 中位 ~0.8mm，F24000（400mm/s，2026-09-12 提速批）下 70% 段运动
# <3ms → planner 空档、$1=25 失能弹簧抬笔，提速后空窗矛盾更尖锐。vpype
# 容差只压像素阶梯（弦偏差 ~0.7px），勿按图幅抬到 3–5px：1760 源 tol=5.3
# 曾把老虎 25508→1006 点（~96%），五官/条纹被抹成折棍（2026-08-26 用户反馈）。
# 1024→tol=1.5px≈0.28mm；1760→1.8px≈0.19mm——阶梯仍塌、曲率特征保留。
_DECIMATE_TOL_MIN_PX = 1.5
_DECIMATE_TOL_FRAC = 0.001
# 抽稀地板（2026-08-27 纸面闸批）：tol 把整幅抽到 <80 点或 <8% 时逐级减半 tol
# 重抽（下限 0.3px），仍不足则保留最温和一档的抽稀结果。全量回退未抽稀会复活
# 1px 阶梯致密 G1 病理（test_decimate_polylines_collapses_pixel_staircase 钉死）。
_DECIMATE_MIN_POINTS_AFTER = 80
_DECIMATE_MIN_KEEP_RATIO = 0.08
_DECIMATE_TOL_FLOOR_PX = 0.3
# 排线簇抑制（2026-08-27）：AI 模型偶发用近同向并排短段「排线填色」，纸面即高
# 密度乱涂。判据：弧长 ≤40px 的短段按弦向 30° 分桶 + 6px 网格链距并查集成簇，
# 簇 ≥8 条整簇丢弃；长笔画与独立特征不受影响。QD 路径不经本函数。
_HATCH_MAX_LEN_PX = 40.0
_HATCH_ANGLE_BUCKETS = 12
_HATCH_MID_GAP_PX = 6.0
_HATCH_MIN_CLUSTER = 8

# 实心色块→轮廓环（2026-08-26 用户主诉「AI 画的都有问题」取证）：生图模型偶发
# 把眼睛/鼻头画成实心黑块，骨架化会把实心盘变成乱麻团（纸面=涂鸦核心区）。判据
# 用连通域 solidity（实心块 ≥0.82 vs 线稿主体 0.07-0.18，实测间隔一个量级）+
# 面积下限（滤掉笔画交点处的小实心三角）。命中后只保留腐蚀出的外环，骨架化即
# 得单线圈——与手绘眼睛同形态。QD 路径不经过本函数，不受影响。
_BLOB_AREA_MIN_PX = 600
_BLOB_AREA_MIN_FRAC = 0.0002
_BLOB_SOLIDITY_MIN = 0.82
_BLOB_RING_PX = 3
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _validate_image_download_url(url: str) -> None:
    """生图 API 返回的下载地址必须指向公网 https（IMG-URL-SSRF-GAP）。

    判定逻辑唯一实现在 url_guard.validate_public_https_url（modelscope_client
    结果图下载共用）；此处只把异常包装成本模块窄类型 BitmapError。
    """
    try:
        validate_public_https_url(url)
    except UrlGuardError as exc:
        raise BitmapError(str(exc)) from None
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
# 骨架总像素预算（R10-BMP-01，2026-08-13 本机崩溃复现）：sknw 的 trace() 用固定
# `np.zeros(131072)` 缓冲存单条边的像素索引，且 @jit(nopython=True) 下 numba 默认
# 不做边界检查——任一**无交叉的单条曲线**骨架长度超过缓冲即越界写堆内存。实测
# 1500² 螺线（骨架 244257 px）令进程直接 0xC0000005（ACCESS_VIOLATION）退出：
# 崩溃发生在 asyncio.to_thread 工作线程的 C 层，Python 捕获不到，整个 uvicorn
# 进程死亡，并发请求与 /files 一起陪葬。解码后无法预知「单条边」会有多长，骨架
# 总像素是唯一保守上界（单边长度 ≤ 总和）。1024² 正常线稿骨架实测 ~1 万 px 量级，
# 余量充足；「画个蚊香/螺旋/漩涡」这类主题会自然产出 15 万~26 万 px 的长单线，
# 必须显式拒绝而不是崩进程。131071 = 缓冲大小 - 1，不是可调画质参数。
_SKNW_TRACE_BUDGET_PX = 131071
# 生图 POST 响应读取上限（R10-BMP-03）：与 llm_svg.MAX_LLM_RESPONSE_BYTES 同口径，
# 必须在网络读取阶段生效——非流式 post() 返回时正文已被 httpx 全量缓冲，事后
# len() 检查拦不住内存暴涨。正常响应只含一条图片 URL 的 JSON，64KiB 已极宽裕。
_IMAGE_API_MAX_BYTES = 64 * 1024


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


def _import_trace_deps() -> tuple:
    """一次性取齐描摹链的重依赖；单一出处，供描摹与预热共用。

    延迟 import 重依赖（skimage/sknw）：模块被 import 的场合（如只跑
    pytest 收集）不付 numpy/skimage 的加载代价，拿到的是装坏时的
    清晰的 BitmapError 而不是 ImportError 穿透。
    """
    try:
        import imageio.v3 as iio
        import sknw
        from skimage.color import rgb2gray, rgba2rgb
        from skimage.filters import gaussian, threshold_otsu
        from skimage.morphology import skeletonize, erosion, disk, closing
        from skimage import measure
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
    return iio, sknw, rgb2gray, rgba2rgb, gaussian, threshold_otsu, skeletonize, erosion, disk, measure, closing

def warm_trace_deps() -> None:
    """预热描摹链：装重依赖 + 用探针 PNG 真描一次（幂等，不碰网络/磁盘）。

    冷启 import 本机实测 ~3.0s（skimage/sknw/imageio 连带 scipy/numba），而
    `_import_trace_deps` 是**函数内**延迟 import，systemd 的 `ExecStartPre` 自检又跑在
    **独立进程**里——预热不到服务进程。于是每次重启后这 3s 都落在第一次真实出图上
    （2026-08-17 生产日志：14:04:59 PNG 下完 → 14:05:04 才出应答）。服务启动时调它，
    把这笔一次性开销从用户等待里挪走；依赖是否可用仍由 ExecStartPre / preflight 判红。

    只 import 还不够：skimage 过滤器/骨架化首调另有 ~2.4s 一次性初始化（2026-08-19
    本机实测：import 后首次 trace 2.4s、第二次 0.0s）。用自检测绘同款的探针 PNG
    真描一遍，首单才能稳态速度。
    """
    _import_trace_deps()
    trace_bitmap_to_polylines(_encode_probe_png())


def _continue_junctions(polys: list[np.ndarray], tangent_px: int = 5) -> list[np.ndarray]:
    """骨架交叉点续接：把被交叉点拆散的同一笔画按最直通原则接回长路径。

    动机（2026-08-27 纸面实锤）：sknw 在度≥3 节点把笔画拆成碎段（机器人咖啡
    实测 274 折线 / 84 抬笔），高频抬落笔下机械接触误差逐次累积，纸面大面积
    丢墨；奎享 Inkscape 链出闭合长轮廓故无此问题。只拼接既有折线（点集不变、
    不增删内容），配对规则：
    - 度=2 共点：唯一伙伴，直接续（现行 `_stitch_polylines` 只从最长段单向
    -   串尾，链中段的头侧伙伴永远接不上，这里走配对表双向建链补齐）；
    - 度≥3 交叉：按端点切线夹角偏离 180° 最小贪心两两配对（最平滑续行），
    -   每半边限配一次，奇数余一个短枝原样保留。
    """
    n = len(polys)
    if n < 2:
        return polys
    ends: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for i, p in enumerate(polys):
        if len(p) < 2:
            continue
        key0 = (int(round(float(p[0][0]))), int(round(float(p[0][1]))))
        key1 = (int(round(float(p[-1][0]))), int(round(float(p[-1][1]))))
        ends.setdefault(key0, []).append((i, 0))  # 0=头端
        ends.setdefault(key1, []).append((i, 1))  # 1=尾端

    def tangent(i: int, end: int) -> np.ndarray:
        p = polys[i]
        k = min(tangent_px, len(p) - 1)
        v = (p[-1] - p[-1 - k]) if end == 1 else (p[0] - p[k])
        norm = float(np.linalg.norm(v))
        return v / norm if norm > 1e-9 else np.zeros(2)

    pair: dict[tuple[int, int], tuple[int, int]] = {}
    for members in ends.values():
        if len(members) == 2:
            (i, ei), (j, ej) = members
            if i == j:
                continue  # 自闭环（同一折线头尾共点）：两端天然连续，无需配对
            pair[(i, ei)] = (j, ej)
            pair[(j, ej)] = (i, ei)
            continue
        if len(members) < 3:
            continue
        cands = []
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                (i, ei), (j, ej) = members[a], members[b]
                if i == j:
                    continue
                cos = float(np.dot(tangent(i, ei), tangent(j, ej)))
                cands.append((cos, (i, ei), (j, ej)))  # cos 越接近 -1 越直通
        cands.sort(key=lambda c: c[0])
        used: set[tuple[int, int]] = set()
        for _cos, ha, hb in cands:
            if ha in used or hb in used:
                continue
            pair[ha] = hb
            pair[hb] = ha
            used.add(ha)
            used.add(hb)
    if not pair:
        return polys
    visited: set[int] = set()
    out: list[np.ndarray] = []
    for i in range(n):
        if i in visited or len(polys[i]) < 2:
            continue
        chain: list[tuple[int, bool]] = [(i, False)]  # (折线号, 是否反转)
        visited.add(i)
        while True:  # 从链尾向前扩展
            ci, rev = chain[-1]
            nxt = pair.get((ci, 0 if rev else 1))
            if nxt is None or nxt[0] in visited:
                break
            chain.append((nxt[0], nxt[1] == 1))
            visited.add(nxt[0])
        while True:  # 从链头向后扩展
            fi, rev = chain[0]
            prv = pair.get((fi, 1 if rev else 0))
            if prv is None or prv[0] in visited:
                break
            chain.insert(0, (prv[0], prv[1] == 0))
            visited.add(prv[0])
        if len(chain) == 1:
            out.append(polys[i])
            continue
        parts = [polys[idx][::-1] if rev else polys[idx] for idx, rev in chain]
        out.append(np.concatenate([c[:-1] for c in parts[:-1]] + [parts[-1]]))
    return out

def _stitch_polylines(polys: list[np.ndarray], tol_px: float) -> list[np.ndarray]:
    """端点距离 ≤ tol_px 的折线互连成连续路径（允许反转），降抬笔次数。

    骨架短边被 _MIN_POLY_PX 过滤后，长边之间留下亚 2mm 缺口；vpype linemerge
    的 0.3mm 阈值（契约钉死）够不着，纸面表现为虚线。并查集分组 + 组内贪心
    串接；串接不出去的剩余段原样保留（fail-open，不因缝合引入丢线）。
    """
    n = len(polys)
    if n < 2:
        return polys
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    ends = []
    for i, p in enumerate(polys):
        ends.append((i, tuple(p[0])))
        ends.append((i, tuple(p[-1])))
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            if find(ends[i][0]) == find(ends[j][0]):
                continue
            d = float(np.linalg.norm(np.array(ends[i][1]) - np.array(ends[j][1])))
            if d <= tol_px:
                a, b = find(ends[i][0]), find(ends[j][0])
                if a != b:
                    parent[a] = b

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    out: list[np.ndarray] = []
    for idxs in groups.values():
        if len(idxs) == 1:
            out.append(polys[idxs[0]])
            continue
        pool = {i: polys[i] for i in idxs}
        start_i = max(pool, key=lambda i: len(polys[i]))
        chain = [pool.pop(start_i)]
        tail = chain[0][-1]
        while pool:
            best, bd, rev = None, tol_px + 1e-9, False
            for i, p in pool.items():
                d0 = float(np.linalg.norm(p[0] - tail))
                d1 = float(np.linalg.norm(p[-1] - tail))
                if d0 <= bd:
                    best, bd, rev = i, d0, False
                if d1 <= bd:
                    best, bd, rev = i, d1, True
            if best is None:
                out.extend(pool.values())
                break
            p = pool.pop(best)
            chain.append(p[::-1] if rev else p)
            tail = chain[-1][-1]
        merged = np.concatenate([c[:-1] for c in chain[:-1]] + [chain[-1]])
        out.append(merged)
    return out


def _rdp_polylines(polys: list[np.ndarray], tol_px: float) -> list[np.ndarray]:
    """纯 Douglas-Peucker 抽稀（像素坐标），压掉 1px 阶梯致密 G1。

    只用 stdlib 几何（不引 skimage.measure）；端点保留；≤2 点原样。无地板——
    地板语义在 `_decimate_polylines_with_floor`，本函数供其逐级调用与契约单测。
    """
    if tol_px <= 0:
        return polys

    def _rdp(pts: np.ndarray, eps: float) -> np.ndarray:
        # 迭代版 Douglas-Peucker：递归版深度 O(n)，地板把 tol 压到 0.3px 时
        # 长折线（数千点）会打穿解释器栈（RecursionError 不是 BitmapError，
        # 会裸穿描摹契约）。显式栈等价递归，语义不变（argmax 取首个最远点）。
        n = len(pts)
        if n <= 2:
            return pts
        eps2 = eps * eps
        keep = np.zeros(n, dtype=bool)
        keep[0] = keep[-1] = True
        stack = [(0, n - 1)]
        while stack:
            lo, hi = stack.pop()
            if hi - lo < 2:
                continue
            start, end = pts[lo], pts[hi]
            seg = end - start
            seg_len2 = float(seg[0] * seg[0] + seg[1] * seg[1])
            sub = pts[lo + 1:hi]
            if seg_len2 < 1e-18:
                d2 = np.sum((sub - start) ** 2, axis=1)
            else:
                rel = sub - start
                cross = np.abs(rel[:, 0] * seg[1] - rel[:, 1] * seg[0])
                d2 = (cross * cross) / seg_len2
            idx = int(np.argmax(d2))
            if float(d2[idx]) <= eps2:
                continue
            mid = lo + 1 + idx
            keep[mid] = True
            stack.append((lo, mid))
            stack.append((mid, hi))
        return pts[keep]

    out: list[np.ndarray] = []
    for p in polys:
        if len(p) <= 2:
            out.append(p)
            continue
        out.append(_rdp(p.astype(float, copy=False), float(tol_px)))
    return out


def _decimate_polylines_with_floor(polys: list[np.ndarray], tol_px: float) -> list[np.ndarray]:
    """带地板的抽稀：整幅抽到 <80 点或 <8% 原点数时逐级减半 tol 重抽（下限
    0.3px），仍不足保留最温和一档结果。全量回退未抽稀会复活阶梯致密 G1
    病理（test_decimate_polylines_collapses_pixel_staircase 钉死）。
    """
    if tol_px <= 0:
        return polys
    before = sum(len(p) for p in polys)
    min_after = max(_DECIMATE_MIN_POINTS_AFTER, int(before * _DECIMATE_MIN_KEEP_RATIO))
    out = _rdp_polylines(polys, float(tol_px))
    eps = float(tol_px)
    while sum(len(p) for p in out) < min_after and eps > _DECIMATE_TOL_FLOOR_PX:
        eps = max(eps / 2.0, _DECIMATE_TOL_FLOOR_PX)
        out = _rdp_polylines(polys, eps)
    after = sum(len(p) for p in out)
    if after < min_after:
        logger.warning(
            "位图抽稀撞地板：%d→%d 点（min_after=%d），保留最温和抽稀结果",
            before, after, min_after,
        )
    return out


def _suppress_hatch_clusters(polys: list[np.ndarray]) -> list[np.ndarray]:
    """丢弃「排线填色」短段簇：近同向、并排密集的短段整簇移除。

    AI 模型偶发用近同向并排短段填色（纸面=高密度乱涂）。判据：弧长 ≤40px 的
    短段按弦向 30° 分桶（无向，0-180°）+ 中点 6px 网格链距并查集成簇，簇
    ≥8 条整簇丢弃。长笔画（弧长 >40px）与独立短特征（眼/鼻）不进簇、原样
    保留——fail-open 只删排线形态。
    """
    n = len(polys)
    short_idx: list[int] = []
    key: list[tuple[int, int, int]] = []
    mids: list[tuple[float, float]] = []
    for i, p in enumerate(polys):
        arc = float(np.sum(np.linalg.norm(np.diff(p, axis=0), axis=1)))
        if arc > _HATCH_MAX_LEN_PX or len(p) < 2:
            continue
        dx = float(p[-1][0] - p[0][0])
        dy = float(p[-1][1] - p[0][1])
        if dx == 0.0 and dy == 0.0:
            continue
        ang = math.degrees(math.atan2(dy, dx)) % 180.0
        bucket = int(ang // (180.0 / _HATCH_ANGLE_BUCKETS)) % _HATCH_ANGLE_BUCKETS
        mx = float(p[:, 0].mean())
        my = float(p[:, 1].mean())
        short_idx.append(i)
        key.append((bucket, int(mx // _HATCH_MID_GAP_PX), int(my // _HATCH_MID_GAP_PX)))
        mids.append((mx, my))
    if len(short_idx) < _HATCH_MIN_CLUSTER:
        return polys
    parent = list(range(len(short_idx)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    cells: dict[tuple[int, int, int], list[int]] = {}
    for pos, k in enumerate(key):
        cells.setdefault(k, []).append(pos)
    for pos, (bucket, cx, cy) in enumerate(key):
        for nb in (bucket, (bucket + 1) % _HATCH_ANGLE_BUCKETS):
            for nx in (cx - 1, cx, cx + 1):
                for ny in (cy - 1, cy, cy + 1):
                    for other in cells.get((nb, nx, ny), ()):
                        if other <= pos:
                            continue
                        if (key[other][0] == bucket or key[other][0] == (bucket + 1) % _HATCH_ANGLE_BUCKETS):
                            a, b = find(pos), find(other)
                            if a != b:
                                parent[a] = b
    sizes: dict[int, int] = {}
    for pos in range(len(short_idx)):
        sizes[find(pos)] = sizes.get(find(pos), 0) + 1
    drop: set[int] = set()
    for pos in range(len(short_idx)):
        if sizes[find(pos)] >= _HATCH_MIN_CLUSTER:
            drop.add(short_idx[pos])
    if not drop:
        return polys
    logger.info("位图描摹：排线簇抑制丢弃 %d 条短段", len(drop))
    return [p for i, p in enumerate(polys) if i not in drop]


def trace_bitmap_to_polylines(
    png_bytes: bytes, *, skeleton_method: str = "zhang"
) -> tuple[list[np.ndarray], int, int]:
    """PNG 字节 → 中心线折线列表（像素坐标）。纯函数，不碰网络/磁盘。

    skeleton_method 透传 skimage skeletonize：默认 zhang 维持存量路径行为；
    照片 i2i 精修链传 lee——2026-09-10 五张生产真实精修样本 A/B 实测
    抬笔 -31 净（-14%/0/-5%/-7%/+2%）、点数略增（细节不丢），txt2plotter
    社区同结论（Lee 更平滑、连通更好）。lee 用 3D 场计算更吃内存，
    输入已被 shrink_oversized_png 限幅，风险可控。
    """
    (
        iio, sknw, rgb2gray, rgba2rgb, gaussian, threshold_otsu,
        skeletonize, erosion, disk, measure, closing,
    ) = _import_trace_deps()
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

    try:
        blur = gaussian(img, sigma=1.0, preserve_range=True)
        binary = blur < threshold_otsu(blur)  # 线条为 True
        # 形态学闭合填孔连断：skimage skeletonize 对毛发纹理图天然产生海量
        # 像素级毛刺短边（实测 63 边中 31 条 <12px），_MIN_POLY_PX 丢弃后断口
        # 成虚线。disk(2) 闭合填小孔+连断口、不放大线宽（开闭运算守恒），
        # 断边 31→21；剩余断口由下游端点缝合（容差从 1.9mm 提到 3mm）兜底。
        binary = closing(binary, disk(2))
        # 实心色块→轮廓环：见 _BLOB_* 常量注释。放在 skeletonize 之前——既消
        # 眼睛乱麻团，也让 R10-BMP-01 的骨架预算按清理后的骨架计。label+regionprops
        # 在 4Mpx 图上实测 <100ms（描摹段本身 ~1s），不另开线程。
        lab = measure.label(binary)
        blob_hits = 0
        for r in measure.regionprops(lab):
            if r.area < _BLOB_AREA_MIN_PX or r.area < _BLOB_AREA_MIN_FRAC * h * w:
                continue
            if r.solidity < _BLOB_SOLIDITY_MIN:
                continue
            comp = lab == r.label
            # bbox 局部腐蚀替代全幅腐蚀（advisory LOW：原 `comp[comp]` 恒为
            # 全 True 冗余全掩码，且 erosion 每个 blob 都跑 4Mpx 全图）。按
            # regionprops 的 bbox 切片跑，语义等价、计算量从 4Mpx×N 降到
            # bbox 面积×N。
            r_slice = r.slice
            comp_local = comp[r_slice]
            ring_local = comp_local & ~erosion(comp_local, disk(_BLOB_RING_PX))
            binary[r_slice] = np.where(comp_local, ring_local, binary[r_slice])
            blob_hits += 1
        if blob_hits:
            logger.info("位图描摹：实心色块转轮廓环 %d 处", blob_hits)
        skel = skeletonize(binary, method=skeleton_method)
        skel_px = int(skel.sum())
        if skel_px > _SKNW_TRACE_BUDGET_PX:
            # R10-BMP-01：必须拦在 build_sknw 之前——sknw 的越界写不是异常而是
            # 进程级崩溃，下面的宽 catch 兜不住它。
            raise BitmapError(
                f"骨架过长（{skel_px} px，上限 {_SKNW_TRACE_BUDGET_PX}）："
                "超过 sknw trace() 的固定缓冲，越界写会直接崩掉进程"
                "（图太密，如螺旋/密集纹理主题）"
            )
        # R10-BMP-02：sknw 默认 multi=False 返回 nx.Graph，同一对节点间的多条弧
        # （圆环被弦穿过 = 上弧/下弧/弦共 3 条路径）会被 add_edge 后写覆盖前写、
        # 静默丢线（本机实测圆环+弦丢 63.1%，太阳+光芒/气球+绳/眼镜均属此形态）。
        # MultiGraph 逐条保留平行边与同节点双自环（8 字形）。iso/ring/full 显式
        # 写死 0.15 的默认值，把行为从「库默认」变成「本仓契约」——旧版 sknw
        # 无这些参数、孤立闭环（纯圆/字母 O）会整体丢失，requirements.txt 已同步
        # 抬下界到 >=0.15。
        graph = sknw.build_sknw(
            skel.astype(np.uint16), multi=True, iso=True, ring=True, full=True
        )
    except BitmapError:
        raise
    except Exception as exc:  # noqa: BLE001
        # 与解码段同款宽 catch：numba 运行期 TypingError、scipy/skimage 内部错误
        # 等罕见异常不该裸穿模块契约（调用方只准备接 BitmapError）；只记类型名，
        # 不回显可能含外部数据的异常文本。
        logger.warning("位图描摹失败（%s）", type(exc).__name__)
        raise BitmapError(f"位图描摹失败（{type(exc).__name__}）") from None
    polys: list[np.ndarray] = []
    for _u, _v, data in graph.edges(data=True):
        pts = data["pts"]  # (N, 2)，(row, col)
        if len(pts) < _MIN_POLY_PX:
            continue
        polys.append(np.array([(x, y) for y, x in pts], dtype=float))
    if not polys:
        logger.warning("位图描摹没有得到任何线条（生图可能不是线稿）")
        raise BitmapError("位图描摹没有得到任何线条（生图可能不是线稿）")
    # 交叉点续接在距离缝合之前：sknw 交叉点端点精确共坐标，结构性续接不依赖
    # 容差猜测；续完再由距离缝合兜底亚 3mm 断口。2026-08-27 纸面实锤：机器人
    # 咖啡 274 折线/84 抬笔高频 Z 循环丢墨，续接把同一笔画接回长路径。
    polys = _continue_junctions(polys)
    # 端点缝合：_MIN_POLY_PX 丢掉的短连接边在长边之间留 0.3-2mm 缺口，恰在
    # vpype linemerge 0.3mm 阈值之上——2026-08-26 小狗实测 66 笔全是被拆散的
    # 同一连通路径（2mm 容差合并后只剩 1 笔）。按图幅比例容差在描摹侧缝合
    # （允许反转），抬笔次数降一个量级；对 1760px 源约 17px ≈ 1.9mm。
    # 容差从 1.9mm 提到 3mm：skimage 骨架毛刺断口实测 3px（@190mm/1024 =
    # 0.56mm）到 12px（2.2mm）都有，1.9mm 只能接部分。3mm 覆盖 <12px 全部
    # 断口，实测同图抬笔 55→15；独立特征（眼/鼻/嘴）间距通常 >3mm 不误并。
    polys = _stitch_polylines(polys, max(6.0, min(w, h) * 0.016))
    # 排线簇抑制在缝合之后、抽稀之前：缝合先把真断口接回长路径，排线短段
    # 互不共端点不会被缝合；抽稀前短段形态最完整，判据最准。
    polys = _suppress_hatch_clusters(polys)
    # 抽稀必须在缝合之后：先把碎段接回长路径，再压阶梯，避免短段被 tol
    # 直接收成两点后丢曲率。QD 路径不经本函数，不受影响。
    dec_tol = max(_DECIMATE_TOL_MIN_PX, min(w, h) * _DECIMATE_TOL_FRAC)
    before = sum(len(p) for p in polys)
    polys = _decimate_polylines_with_floor(polys, dec_tol)
    after = sum(len(p) for p in polys)
    if before > after:
        logger.info(
            "位图描摹：折线抽稀 %d→%d 点（tol=%.1fpx）", before, after, dec_tol
        )
    return polys, w, h


def _smooth_polyline(pts: np.ndarray) -> np.ndarray:
    if len(pts) < _SMOOTH_WINDOW:
        return pts
    kernel = np.ones(_SMOOTH_WINDOW) / _SMOOTH_WINDOW
    xs = np.convolve(pts[:, 0], kernel, mode="same")
    ys = np.convolve(pts[:, 1], kernel, mode="same")
    xs[:2], ys[:2] = pts[:2, 0], pts[:2, 1]
    xs[-2:], ys[-2:] = pts[-2:, 0], pts[-2:, 1]
    return np.stack([xs, ys], axis=1)


def polylines_to_svg(
    polys: list[np.ndarray], width: int, height: int, *, smooth: bool = True,
) -> str:
    """折线列表 → 单线 SVG；程序化稀疏折线须 smooth=False，保留回折位置。"""
    parts = []
    for pts in polys:
        smoothed = _smooth_polyline(pts) if smooth else pts
        str_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in smoothed)
        parts.append(f'<polyline points="{str_pts}"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="190mm" height="190mm" '
        f'viewBox="0 0 {width} {height}" fill="none" stroke="black" stroke-width="1">'
        + "".join(parts)
        + "</svg>"
    )


def _clip_preview_segment(
    start: np.ndarray, end: np.ndarray, max_x: float, max_y: float
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """先裁到视口再调用 line()；事后过滤像素挡不住越界长线的坐标数组分配。"""
    x0, y0 = map(float, start)
    x1, y1 = map(float, end)
    dx, dy = x1 - x0, y1 - y0
    if not math.isfinite(dx) or not math.isfinite(dy):
        raise BitmapError("预览线段坐标超出数值范围")
    enter, leave = 0.0, 1.0
    for p, q in ((-dx, x0), (dx, max_x - x0), (-dy, y0), (dy, max_y - y0)):
        if p == 0:
            if q < 0:
                return None
        elif p < 0:
            enter = max(enter, q / p)
        else:
            leave = min(leave, q / p)
        if enter > leave:
            return None

    def point(t: float) -> tuple[float, float]:
        # 浮点求交可有舍入误差，回到视口内再转整数像素。
        return min(max(x0 + t * dx, 0.0), max_x), min(max(y0 + t * dy, 0.0), max_y)

    return point(enter), point(leave)


def polylines_to_preview_png(
    polys: list[np.ndarray], width: int, height: int, *, smooth: bool = True,
) -> bytes:
    """把同一组中心线栅格成适合 480×320 设备屏幕的紧凑 PNG。

    2026-09-10 起改为真密度渲染：旧实现在 ~308px 目标尺寸直接画 1px+右下
    加粗线，密排线区（程序化排线/波纹）全部并成实心黑块，用户预览与纸面
    真相严重不符（两次黑卡事故的判读污染源）。现按内容分辨率 1px 栅格化
    后面积平均降采样：灰度 = 真实墨密度，疏区细线仍可读（轻度增辉曲线），
    密区呈灰而非死黑。源尺寸超过 2048 时等比缩入有界栅格；先裁线再画，
    防止用户 SVG 的巨大 viewBox 或越界端点耗尽进程内存。smooth 必须与 SVG
    导出一致；程序化排线/波纹已是稀疏路径，均值平滑会改变笔迹而非降噪。
    """
    try:
        import imageio.v3 as iio
        from skimage.draw import line
        from skimage.transform import resize
    except Exception as exc:  # noqa: BLE001
        raise BitmapError(f"预览图依赖不可用（{type(exc).__name__}）") from None

    try:
        valid_size = all(math.isfinite(side) and side > 0 for side in (width, height))
    except (OverflowError, TypeError, ValueError):
        valid_size = False
    if not valid_size:
        raise BitmapError("预览尺寸必须是有限正数")

    usable_w = _PREVIEW_MAX_WIDTH - 2 * _PREVIEW_PADDING
    usable_h = _PREVIEW_MAX_HEIGHT - 2 * _PREVIEW_PADDING
    scale = min(usable_w / width, usable_h / height)
    content_w = max(1, int(round(width * scale)))
    content_h = max(1, int(round(height * scale)))
    out_w = content_w + 2 * _PREVIEW_PADDING
    out_h = content_h + 2 * _PREVIEW_PADDING

    raster_scale = min(1.0, _PREVIEW_RASTER_MAX_SIDE / width, _PREVIEW_RASTER_MAX_SIDE / height)
    raster_w = max(1, int(round(width * raster_scale)))
    raster_h = max(1, int(round(height * raster_scale)))
    # 以像素中心映射端点，极窄视口缩到 1px 时整条线仍能保留；先在源视口
    # 裁剪，避免把本应在视口外的平行线压到这一像素上。
    scale_xy = (
        (raster_w - 1) / (width - 1) if width > 1 else 1.0,
        (raster_h - 1) / (height - 1) if height > 1 else 1.0,
    )
    hi = np.full((raster_h, raster_w), 255.0, dtype=np.float32)
    for pts in polys:
        smoothed = _smooth_polyline(pts) if smooth else pts
        if not np.isfinite(smoothed).all():
            raise BitmapError("预览线段坐标必须是有限数")
        for start, end in zip(smoothed[:-1], smoothed[1:]):
            clipped = _clip_preview_segment(start, end, max(0, width - 1), max(0, height - 1))
            if clipped is None:
                continue
            (x0, y0), (x1, y1) = np.rint(np.asarray(clipped) * scale_xy).astype(int)
            rr, cc = line(y0, x0, y1, x1)
            hi[rr, cc] = 0.0
    small = resize(hi, (content_h, content_w), anti_aliasing=True)
    # 增辉：单根细线降采样后约 70-85% 亮（1024 坐标系 0.3 缩放下占空 0.3），
    # 乘 2.0 把细线拉回 <128 深灰保持可读（curated 预览跨度测试钉 ink<128）；密区仍近黑
    boosted = 255.0 - (255.0 - small) * 2.0
    canvas = np.full((out_h, out_w), 255, dtype=np.uint8)
    canvas[
        _PREVIEW_PADDING : _PREVIEW_PADDING + content_h,
        _PREVIEW_PADDING : _PREVIEW_PADDING + content_w,
    ] = np.clip(boosted, 0, 255).astype(np.uint8)
    output = BytesIO()
    iio.imwrite(output, canvas, extension=".png")
    return output.getvalue()


def _image_fallback_url() -> str:
    """agnes 主链失败时的免鉴权备用生图端点（imagefree）；空值禁用。

    getattr 兜底兼容测试注入的窄 settings（SimpleNamespace 无此字段 = 禁用）。
    """
    return getattr(settings, "image_fallback_url", "").strip()




async def _post_image_request(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    headers: dict[str, str] | None,
    provider: str,
    retry_delays: tuple[float, ...] | None = None,
    attempt_timeout_seconds: float = _IMAGE_ATTEMPT_TIMEOUT_SECONDS,
    max_bytes: int = _IMAGE_API_MAX_BYTES,
) -> dict:
    """两个生图端点共用的 POST + 瞬态重试 + 流式上限闸；返回解析后的 JSON object。

    provider 只进错误文案：agnes 侧沿用「生图 API」（既有测试钉死），备用侧
    「备用生图端点」。两侧都不回显 URL 与上游错误正文（可能含凭据/自由文本）。
    retry_delays=None 用全局 _RETRY_DELAYS_SECONDS；备用链传 () 做单次尝试——
    桥接看门狗 WATCHDOG_STALL_SECONDS=300 内必须出结果：主链最坏 3×45=135s +
    备用 45s + 下载 60s ≈ 240s；备用若也重试三轮，最坏 ~334s 会撞上看门狗被判死、
    响应丢在重连里。备用是锦上添花，不为它赌上整单。
    """
    delays = _RETRY_DELAYS_SECONDS if retry_delays is None else retry_delays
    data = None
    for attempt in range(len(delays) + 1):
        try:
            # R10-BMP-03：POST 与下面的图片 GET 同口径改流式——非流式 post()
            # 返回时正文已被 httpx 全量缓冲，事后检查拦不住内存暴涨；恶意/
            # 故障上游在超时窗口内可推送 GB 级正文。声明闸省流量，逐块累计
            # 闸兜住 chunked/畸形头，两道闸都在数据进内存前生效。
            # 每次请求覆盖 httpx 超时：共享 client 默认 45s 读超时会把慢通道
            # （u1.5-lite 实测 29~64s 无首字节）误杀；外层 asyncio.timeout 仍是硬闸。
            async with asyncio.timeout(attempt_timeout_seconds):
                async with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
                    timeout=attempt_timeout_seconds,
                ) as resp:
                    status = resp.status_code
                    raw = bytearray()
                    if status == 200:
                        declared = resp.headers.get("content-length")
                        if (
                            declared
                            and declared.isdigit()
                            and int(declared) > max_bytes
                        ):
                            raise BitmapError(f"{provider} 响应超过读取上限")
                        async for chunk in resp.aiter_bytes():
                            if len(raw) + len(chunk) > max_bytes:
                                raise BitmapError(f"{provider} 响应超过读取上限")
                            raw.extend(chunk)
            if status == 200:
                try:
                    data = json.loads(bytes(raw))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # 200 但正文不是 JSON（如 CDN/网关的 HTML 错误页）：
                    # JSONDecodeError/UnicodeDecodeError 是 ValueError 而非
                    # httpx 异常，不拦会裸穿出本模块；重试同一份垃圾正文
                    # 无意义，直接失败。
                    raise BitmapError(f"{provider} 响应不是合法 JSON") from None
                break
            # 上游错误正文可能回显凭据；不读取不记录，只记状态码。
            if status in _TRANSIENT_HTTP_STATUSES and attempt < len(delays):
                await asyncio.sleep(delays[attempt])
                continue
            logger.warning("%s 返回 %d", provider, status)
            raise BitmapError(f"{provider} 返回 {status}")
        except (httpx.HTTPError, TimeoutError) as exc:
            if attempt < len(delays):
                await asyncio.sleep(delays[attempt])
                continue
            logger.warning("%s 网络错误（%s）", provider, type(exc).__name__)
            raise BitmapError(f"{provider} 网络错误（{type(exc).__name__}）") from None
    if data is None:
        raise BitmapError(f"{provider} 未返回数据")
    if not isinstance(data, dict):
        raise BitmapError(f"{provider} 响应结构异常（TypeError）")
    return data


def _extract_agnes_image_url(data: dict) -> str | bytes:
    """OpenAI 兼容 images/generations 的取链：data[0].url；无 url 时解 data[0].b64_json。

    2026-10-12：商汤聚合器下架 u1-fast 后仅剩的 u1.5-lite 不带 url，图片以
    base64 内联在计费响应里（实测 ~6MB JSON）。解出字节直接交给描摹链，
    省一跳 CDN；有 url 的既有端点行为逐字不变。
    """
    try:
        item = data["data"][0]
        image_url = item["url"]
    except KeyError:
        return _decode_inline_b64_image(data)
    except (IndexError, TypeError) as exc:
        raise BitmapError(f"生图 API 响应结构异常（{type(exc).__name__}）") from exc
    if not isinstance(image_url, str):
        raise BitmapError("生图 API 未返回有效的 https 图片地址")
    return image_url


def _decode_inline_b64_image(data: dict) -> bytes:
    try:
        b64 = data["data"][0]["b64_json"]
    except (KeyError, IndexError, TypeError) as exc:
        raise BitmapError(f"生图 API 响应结构异常（{type(exc).__name__}）") from exc
    if not isinstance(b64, str) or not b64:
        raise BitmapError("生图 API 未返回有效的 https 图片地址")
    try:
        raw = base64.b64decode(b64, validate=True)
    except (ValueError, binascii.Error):
        raise BitmapError("生图 API 的 b64_json 不是合法 base64") from None
    if not raw:
        raise BitmapError("生图 API 未返回有效的 https 图片地址")
    return raw

async def _request_agnes_image_url(
    client: httpx.AsyncClient, description: str
) -> str | bytes:
    url = f"{settings.llm_base_url.rstrip('/')}/images/generations"
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    styled = {
        "model": settings.image_model,
        "prompt": _BITMAP_STYLE + description,
        "size": _IMAGE_SIZE,
        "n": 1,
    }
    try:
        data = await _post_image_request(client, url, styled, headers, "生图 API")
    except BitmapError as exc:
        # 2026-09-09：Agnes 偶发 400（内容策略/过长前缀），短主题裸 prompt 再试一次；
        # 其它错误不重试，避免拖慢对冲。
        if "返回 400" not in str(exc):
            raise
        plain = {
            "model": settings.image_model,
            "prompt": description.strip() or "simple line art icon",
            "size": _IMAGE_SIZE,
            "n": 1,
        }
        logger.info("Agnes 400，改用短 prompt 重试一次")
        data = await _post_image_request(
            client, url, plain, headers, "生图 API", retry_delays=()
        )
    return _extract_agnes_image_url(data)


async def _request_sensenova_image_url(
    client: httpx.AsyncClient, description: str
) -> str | bytes:
    """调用商汤聚合器 PNG 接口（现仅剩 u1.5-lite：b64_json 内联出图）；单次尝试 75s。"""
    url = f"{settings.sensenova_base_url.rstrip('/')}/images/generations"
    payload = {
        "model": settings.sensenova_image_model,
        "prompt": _BITMAP_STYLE + description,
        "size": _SENSENOVA_IMAGE_SIZE,
        "n": 1,
        "watermark": False,
    }
    headers = {"Authorization": f"Bearer {settings.sensenova_api_key}"}
    data = await _post_image_request(
        client,
        url,
        payload,
        headers,
        "商汤生图 API",
        retry_delays=_SENSENOVA_RETRY_DELAYS_SECONDS,
        attempt_timeout_seconds=_SENSENOVA_ATTEMPT_TIMEOUT_SECONDS,
        max_bytes=_SENSENOVA_RESPONSE_MAX_BYTES,
    )
    return _extract_agnes_image_url(data)


async def _request_fallback_image_url(client: httpx.AsyncClient, description: str) -> str:
    """免鉴权备用端点（imagefree /v1/generate）：扁平 image_url，status 判成败。

    其文档状态机：200+completed 出图 / 200+error 业务失败 / 202 排队超窗（含
    Location 轮询地址）/ 429 队列满 / 422 参数错。202 不再叠轮询——降级链快进
    快出，按「返回 202」失败；429 走共有瞬态重试。
    """
    payload = {
        "prompt": _BITMAP_STYLE + description,
        "aspect_ratio": _FALLBACK_ASPECT_RATIO,
    }
    data = await _post_image_request(
        client, _image_fallback_url(), payload, None, "备用生图端点", retry_delays=()
    )
    status = data.get("status")
    if status != "completed":
        # error 字段是上游自由文本（内容安全拦截原因等），不回显，只记状态。
        logger.warning("备用生图端点未出图（status=%r）", status)
        raise BitmapError(f"备用生图端点未出图（status={status!r}）")
    image_url = data.get("image_url")
    if not isinstance(image_url, str):
        raise BitmapError("备用生图端点未返回有效的 https 图片地址")
    return image_url


def _siliconflow_api_key() -> str:
    """SiliconFlow key；空值禁用。Settings 可能缺该字段（测试直造），同 getattr 口径。"""
    return getattr(settings, "siliconflow_api_key", "").strip()


def _sensenova_api_key() -> str:
    return getattr(settings, "sensenova_api_key", "").strip()


def _image_backend_enabled(name: str) -> bool:
    from .portal_settings import image_backend_enabled

    return image_backend_enabled(settings, name)


async def _request_siliconflow_image_url(
    client: httpx.AsyncClient,
    description: str,
    *,
    model: str,
    provider: str,
    extra: dict,
) -> str:
    """SiliconFlow OpenAI 风格生图；响应取 images[0].url（与 Agnes 的 data[0].url 不同构）。"""
    payload = {
        "model": model,
        "prompt": _BITMAP_STYLE + description,
        "image_size": _IMAGE_SIZE,
        "batch_size": 1,
        **extra,
    }
    headers = {"Authorization": f"Bearer {_siliconflow_api_key()}"}
    data = await _post_image_request(
        client,
        f"{_SILICONFLOW_BASE_URL}/images/generations",
        payload,
        headers,
        provider,
        retry_delays=(),
        attempt_timeout_seconds=_SILICONFLOW_ATTEMPT_TIMEOUT_SECONDS,
    )
    try:
        image_url = data["images"][0]["url"]
    except (KeyError, IndexError, TypeError) as exc:
        raise BitmapError(f"{provider} 响应结构异常（{type(exc).__name__}）") from exc
    if not isinstance(image_url, str):
        raise BitmapError(f"{provider} 未返回有效的 https 图片地址")
    return image_url


async def _request_siliconflow_schnell_image_url(
    client: httpx.AsyncClient, description: str
) -> str:
    return await _request_siliconflow_image_url(
        client, description,
        model=_SILICONFLOW_SCHNELL_MODEL, provider="硅流 Schnell", extra={},
    )


async def _request_siliconflow_kolors_image_url(
    client: httpx.AsyncClient, description: str
) -> str:
    return await _request_siliconflow_image_url(
        client, description,
        model=_SILICONFLOW_KOLORS_MODEL, provider="硅流可图",
        extra={
            "num_inference_steps": _SILICONFLOW_KOLORS_STEPS,
            "guidance_scale": _SILICONFLOW_KOLORS_GUIDANCE,
        },
    )

def _modelscope_token() -> str:
    """魔搭 token；空值禁用。Settings 可能缺该字段（测试直造），同 getattr 口径。"""
    return getattr(settings, "modelscope_token", "").strip()


def _tcb_image_url() -> str:
    return getattr(settings, "tcb_image_url", "").strip()


def _tcb_api_key() -> str:
    return getattr(settings, "tcb_api_key", "").strip()


async def _request_modelscope_image_url(
    client: httpx.AsyncClient, description: str
) -> str:
    """魔搭 t2i 对冲 racer：异步任务制（提交+轮询 output_images[0]）。

    免费层排队实测可达分钟级，轮询上限用 modelscope_client.T2I_TIMEOUT_SECONDS
    （120s）而非对冲池 45s 单次闸——它是「有机会赢」的成员，不为它拖住整单；
    输给快通道时被 race 取消，已提交的任务在魔搭侧自然完结（免费配额代价）。
    """
    from . import modelscope_client

    try:
        return await modelscope_client.generate_image_url(
            client, _modelscope_token(), prompt=_BITMAP_STYLE + description
        )
    except modelscope_client.ModelScopeError as exc:
        raise BitmapError(str(exc)) from None


async def _request_tcb_image_url(client: httpx.AsyncClient, description: str) -> str:
    """腾讯云开发混元 t2i（generateImage 云函数原生 invoke）：单次 POST ~15s，
    {prompt, model} 进 {success, imageUrl} 出。i2i 由独立模块 tcb_i2i 承担
    （portal 照片精修链），本 racer 只进 t2i 对冲池。"""
    payload = {"prompt": _BITMAP_STYLE + description, "model": "hunyuan-image"}
    headers = {"Authorization": f"Bearer {_tcb_api_key()}"}
    data = await _post_image_request(
        client, _tcb_image_url(), payload, headers, "混元生图", retry_delays=()
    )
    if data.get("success") is not True:
        # error/message 是云函数自由文本，不回显，只记 success 值。
        logger.warning("混元生图未出图（success=%r）", data.get("success"))
        raise BitmapError("混元生图未出图（success=false）")
    image_url = data.get("imageUrl")
    if not isinstance(image_url, str) or not image_url:
        raise BitmapError("混元生图未返回有效的 https 图片地址")
    return image_url


# 瞬态「忙」判别：job_ledger.classify_bitmap_error 的类别里，429/5xx/timeout
# 代表上游容量或网络瞬态，重跑一轮通常就好；400（内容审核/参数）与其余
# error 属描述相关或配置类失败，重试大概率白花一次付费调用，判非忙。
_BUSY_ERROR_CLASSES = frozenset({"http_429", "http_5xx", "timeout"})
# 全忙重试延迟（2026-09-12 诊断：郭富城/日本连败时商汤+魔搭同轮 429 而
# Agnes 单飞可成；对全忙瞬态停 2.5s 真重跑一轮，吃掉限流窗口。不取更长：
# 共享任务全程持有 DrawLimiter 名额，延迟过长会让后续同题搭车者空等）。
_BUSY_RETRY_DELAY_SECONDS = 2.5


def _all_errors_busy(errors: dict[str, BitmapError]) -> bool:
    """判别对冲全灭是否属「全忙」：至少有一条错误，且全部落在忙类。"""
    if not errors:
        return False
    from . import job_ledger

    return all(
        job_ledger.classify_bitmap_error(exc) in _BUSY_ERROR_CLASSES
        for exc in errors.values()
    )


async def _race_dynamic_image_racers(
    client: httpx.AsyncClient, description: str, racers: list[dict[str, Any]]
) -> str | bytes:
    from . import breaker, job_ledger
    from .image_racers import request_racer_image_url

    cap = getattr(settings, "draw_racer_max", 0) or 0
    if cap > 0:
        racers = racers[:cap]
    filtered: list[dict[str, Any]] = []
    for racer in racers:
        backend_id = job_ledger.backend_id_from_label(
            str(racer.get("name") or racer.get("id") or "racer"),
            racer=racer,
        )
        if await asyncio.to_thread(breaker.should_skip_backend, backend_id):
            logger.info("生图对冲：跳过熔断通道 %s", backend_id)
            continue
        filtered.append(racer)
    racers = filtered
    if not racers:
        raise BitmapError("生图通道暂不可用，请稍后再试")
    task_meta: dict[asyncio.Task, tuple[str, str]] = {}
    for racer in racers:
        label = str(racer.get("name") or racer.get("id"))
        backend_id = job_ledger.backend_id_from_label(label, racer=racer)
        task = asyncio.create_task(request_racer_image_url(client, description, racer))
        task_meta[task] = (label, backend_id)
    errors: dict[str, BitmapError] = {}
    pending: set[asyncio.Task] = set(task_meta)
    winner: str | bytes | None = None
    try:
        while winner is None and pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                label, backend_id = task_meta[task]
                started = time.monotonic()
                try:
                    image_url = task.result()
                except BitmapError as exc:
                    latency_ms = int((time.monotonic() - started) * 1000)
                    await asyncio.to_thread(breaker.record_outcome,
                        backend_id,
                        job_ledger.classify_bitmap_error(exc),
                        latency_ms=latency_ms,
                    )
                    logger.info("生图对冲：%s 失败：%s", label, exc)
                    errors[label] = exc
                    continue
                latency_ms = int((time.monotonic() - started) * 1000)
                await asyncio.to_thread(breaker.record_outcome, backend_id, "ok", latency_ms=latency_ms)
                winner = image_url
                for other in done:
                    if other is not task and other.done() and not other.cancelled():
                        other.exception()
                break
    finally:
        rest = [t for t in task_meta if not t.done()]
        for task in rest:
            task.cancel()
        if rest:
            await asyncio.gather(*rest, return_exceptions=True)
    if winner is None:
        parts = [f"{name} 失败：{exc}" for name, exc in errors.items()]
        message = "；".join(parts) if parts else "全部动态生图后端均失败"
        if _all_errors_busy(errors):
            raise BitmapBusyError(message)
        raise BitmapError(message)
    return winner


async def _race_image_backends(
    client: httpx.AsyncClient, description: str
) -> str | bytes:
    """商汤 / Agnes / imagefree 并发对冲；优先运营后台 image_racers，空则 break-glass env。"""
    from . import breaker, job_ledger
    from .portal_settings import portal_image_racers

    dynamic = await asyncio.to_thread(portal_image_racers, settings)
    if dynamic:
        return await _race_dynamic_image_racers(client, description, dynamic)

    racers: list[
        tuple[str, str, Callable[[httpx.AsyncClient, str], Coroutine[None, None, str | bytes]]]
    ] = []
    enabled = await asyncio.to_thread(lambda: {
        name: _image_backend_enabled(name)
        for name in ("sensenova", "agnes", "siliconflow", "modelscope", "tcb", "fallback")
    })
    if enabled["sensenova"] and _sensenova_api_key():
        racers.append(("商汤生图", "sensenova", _request_sensenova_image_url))
    if enabled["agnes"]:
        racers.append(("Agnes", "agnes", _request_agnes_image_url))
    if enabled["siliconflow"] and _siliconflow_api_key():
        racers.append(("硅流 Schnell", "siliconflow:schnell", _request_siliconflow_schnell_image_url))
        racers.append(("硅流可图", "siliconflow:kolors", _request_siliconflow_kolors_image_url))
    if enabled["modelscope"] and _modelscope_token():
        racers.append(("魔搭生图", "modelscope", _request_modelscope_image_url))
    if enabled["tcb"] and _tcb_image_url() and _tcb_api_key():
        racers.append(("混元生图", "tcb", _request_tcb_image_url))
    if enabled["fallback"] and _image_fallback_url():
        racers.append(("备用端点", "imagefree", _request_fallback_image_url))
    if not racers:
        raise BitmapError("未启用任何生图后端（请在运营后台或环境变量中配置）")
    racers = [
        (name, backend_id, fn)
        for name, backend_id, fn in racers
        if not await asyncio.to_thread(breaker.should_skip_backend, backend_id)
    ]
    if not racers:
        raise BitmapError("生图通道暂不可用，请稍后再试")
    cap = getattr(settings, "draw_racer_max", 0) or 0
    if cap > 0:
        racers = racers[:cap]
    task_meta: dict[asyncio.Task, tuple[str, str]] = {
        asyncio.create_task(fn(client, description)): (name, backend_id)
        for name, backend_id, fn in racers
    }
    race_t0 = time.monotonic()
    errors: dict[str, BitmapError] = {}
    pending: set[asyncio.Task] = set(task_meta)
    winner: str | bytes | None = None
    try:
        while winner is None and pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                name, backend_id = task_meta[task]
                try:
                    image_url = task.result()
                except BitmapError as exc:
                    latency_ms = int((time.monotonic() - race_t0) * 1000)
                    await asyncio.to_thread(breaker.record_outcome,
                        backend_id,
                        job_ledger.classify_bitmap_error(exc),
                        latency_ms=latency_ms,
                    )
                    # 对冲期单侧失败属正常波动，记 info；全灭才升级。
                    logger.info("生图对冲：%s 失败：%s", name, exc)
                    errors[name] = exc
                    continue
                await asyncio.to_thread(breaker.record_outcome,
                    backend_id,
                    "ok",
                    latency_ms=int((time.monotonic() - race_t0) * 1000),
                )
                winner = image_url
                logger.info(
                    "生图对冲：%s 先出图（%.1fs）", name, time.monotonic() - race_t0
                )
                # 同批醒来的其余已完成任务若带异常，标记已取——否则 asyncio
                # 在 GC 时打 "Task exception was never retrieved" 噪音。
                for other in done:
                    if other is not task and other.done() and not other.cancelled():
                        other.exception()
                break
    finally:
        rest = [t for t in task_meta if not t.done()]
        for task in rest:
            task.cancel()
        if rest:
            await asyncio.gather(*rest, return_exceptions=True)
    if winner is None:
        parts: list[str] = []
        if "商汤生图" in errors:
            parts.append(f"商汤生图失败：{errors['商汤生图']}")
        if "Agnes" in errors:
            parts.append(f"Agnes 同样失败：{errors['Agnes']}")
        for sf_name in ("硅流 Schnell", "硅流可图"):
            if sf_name in errors:
                parts.append(f"{sf_name} 同样失败：{errors[sf_name]}")
        for extra_name in ("魔搭生图", "混元生图"):
            if extra_name in errors:
                parts.append(f"{extra_name}同样失败：{errors[extra_name]}")
        if "备用端点" in errors:
            parts.append(f"备用端点同样失败：{errors['备用端点']}")
        message = "；".join(parts) if parts else "全部生图后端均失败"
        if _all_errors_busy(errors):
            raise BitmapBusyError(message)
        raise BitmapError(message)
    return winner


async def _fetch_image_png(description: str, *, transport: httpx.AsyncBaseTransport | None = None) -> bytes:
    """生图并下载 PNG：商汤 / Agnes / imagefree 三路并发对冲 → 描摹链不变。"""
    if not settings.llm_api_key:
        # CloudUX-F5：Agnes 仍是商汤失败时的受控回退，故凭据必须始终可用。
        logger.warning(
            "位图路线未配置：请设置环境变量 HUTUJI_LLM_API_KEY"
            "（位图路线与 Agnes 回退共用同一端点凭据）"
        )
        raise BitmapError("绘图服务未就绪，请稍后再试")
    if not settings.image_model.strip():
        logger.warning("HUTUJI_IMAGE_MODEL 为空：生图请求会被端点拒绝")
        raise BitmapError("绘图服务未就绪，请稍后再试")

    # 显式决策（R10 复审）：不跟随重定向（httpx 默认 follow_redirects=False），
    # 3xx 一律按「图片下载返回 3xx」失败。盲开 follow_redirects=True 会打开
    # https→内网/明文 的 SSRF 降级面（下面的 https 前缀闸只验首跳 URL）。
    async with httpx.AsyncClient(
        timeout=_IMAGE_ATTEMPT_TIMEOUT_SECONDS, transport=transport
    ) as client:
        image = await _race_image_backends(client, description)
        if isinstance(image, bytes):
            # b64_json 内联图（商汤 u1.5-lite）：图已在计费响应里，无 URL 可跳，
            # 也不存在 SSRF 面；字节闸与下载路同口径。PNG magic 校验留给描摹链
            # （与 URL 路一致）。
            if len(image) > _IMAGE_DOWNLOAD_MAX_BYTES:
                raise BitmapError("图片超过下载上限")
            logger.info("生图内联返回：%d 字节", len(image))
            return image
        image_url = image
        # 只验 https 前缀挡不住内网 https 地址（SSRF，见 IMG-URL-SSRF-GAP）。
        _validate_image_download_url(image_url)

        # 下载阶段失败**不降级**（R16 登记，测试钉死）：此刻主链已计费出图成功，
        # 只是 CDN 取图失败——降级等于免费端点再赌一次画风不同的图，且
        # 「主链好+CDN 挂」与「主链挂」是不可同药的两类故障。

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
            dl_t0 = time.monotonic()
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
                            logger.info(
                                "图片下载完成：%d 字节 %.1fs",
                                len(body),
                                time.monotonic() - dl_t0,
                            )
                            return bytes(body)
                        if dl.status_code in _TRANSIENT_HTTP_STATUSES and attempt == 0:
                            retry_download = True
                        else:
                            logger.warning("图片下载返回 %d", dl.status_code)
                            raise BitmapError(f"图片下载返回 {dl.status_code}")
            except (httpx.HTTPError, TimeoutError) as exc:
                if attempt == 0:
                    retry_download = True
                else:
                    logger.warning("图片下载网络错误（%s）", type(exc).__name__)
                    raise BitmapError(f"图片下载网络错误（{type(exc).__name__}）") from None
            if retry_download:
                retry_download = False
                await asyncio.sleep(_RETRY_DELAYS_SECONDS[0])


def _get_cached_bitmap(description: str) -> BitmapResult | None:
    now = _bitmap_cache_clock()
    cached = _bitmap_cache.get(description)
    if cached is None:
        return None
    created_at, result = cached
    if now - created_at >= _BITMAP_CACHE_TTL_SECONDS:
        del _bitmap_cache[description]
        return None
    _bitmap_cache.move_to_end(description)
    return result


def _cache_bitmap(description: str, result: BitmapResult) -> None:
    _bitmap_cache[description] = (_bitmap_cache_clock(), result)
    _bitmap_cache.move_to_end(description)
    while len(_bitmap_cache) > _BITMAP_CACHE_MAX_ENTRIES:
        _bitmap_cache.popitem(last=False)


def _forget_bitmap_inflight(key: str, done: asyncio.Task[BitmapResult]) -> None:
    """只清掉自己登记的任务，避免完成回调误删同键后继任务。"""
    if _bitmap_inflight.get(key) is done:
        del _bitmap_inflight[key]


async def _generate_bitmap_uncached(description: str, cache_key: str) -> BitmapResult:
    """跑完整付费生成链并在任务结束前写入结果缓存。"""
    fetch_t0 = time.monotonic()
    try:
        png = await _fetch_image_png(description)
    except BitmapBusyError:
        # 全忙瞬态（全对冲通道 429/5xx/超时）：停一个短窗后真重跑整轮生图，
        # 不复用第一轮任何产物（内容缓存在成功前不写，重试天然绕开）。只
        # 重试一次：第二轮仍忙则把 BitmapBusyError 原样上抛——/internal/draw
        # 映射成 429「通道忙」白话，语音路径按既有 BitmapError 白话回落。
        logger.warning("生图通道瞬态全忙，%.1fs 后重试一次", _BUSY_RETRY_DELAY_SECONDS)
        await asyncio.sleep(_BUSY_RETRY_DELAY_SECONDS)
        png = await _fetch_image_png(description)
    fetch_s = time.monotonic() - fetch_t0

    def build() -> BitmapResult:
        polys, w, h = trace_bitmap_to_polylines(png)
        svg = polylines_to_svg(polys, w, h)
        if len(svg.encode("utf-8")) > _SVG_MAX_BYTES:
            raise BitmapError("描摹 SVG 超过 256KiB 上限（图太复杂）")
        _validate_svg(svg)
        return BitmapResult(svg=svg, preview_png=polylines_to_preview_png(polys, w, h))

    build_t0 = time.monotonic()
    result = await asyncio.to_thread(build)
    # 分段计时是「链路慢」的定位数据源（2026-08-23 用户主诉取证）：只记
    # 阶段名与秒数，不含描述文本/URL（脱敏硬约定）。
    logger.info(
        "位图链路分段：生成+下载 %.1fs，描摹+预览 %.1fs",
        fetch_s,
        time.monotonic() - build_t0,
    )
    _cache_bitmap(cache_key, result)
    return result


async def generate_bitmap(description: str, *, attempt: int = 0) -> BitmapResult:
    """description → 同源单线 SVG + 设备屏幕 PNG 预览。

    `attempt`（纸面闸重试轮次）：>0 时缓存键追加 `::attempt=N` 破缓存——
    重试必须真重跑整轮生图，不得拿上一轮被拒的同一 PNG 再描一遍。
    """
    safe_desc = _sanitize_description(description)
    cache_key = safe_desc.strip()
    if attempt:
        cache_key = f"{cache_key}::attempt={attempt}"
    cached = _get_cached_bitmap(cache_key)
    if cached is not None:
        logger.info("位图结果缓存命中")
        return cached

    work = _bitmap_inflight.get(cache_key)
    if work is not None and work.get_loop() is not asyncio.get_running_loop():
        # 测试/嵌入调用可能轮换事件循环；生产单 loop 不会走此分支。旧 loop 的
        # 任务不能跨 loop await，且若已完成只需取其异常避免未取异常噪音。
        if work.done() and not work.cancelled():
            work.exception()
        _bitmap_inflight.pop(cache_key, None)
        work = None
    if work is None:
        work = asyncio.create_task(_generate_bitmap_uncached(safe_desc, cache_key))
        _bitmap_inflight[cache_key] = work
        work.add_done_callback(
            lambda done, key=cache_key: _forget_bitmap_inflight(key, done)
        )

    # 同题完整任务由所有调用方共享。shield 防止单个云端取消杀掉付费生成；取消方
    # 仍须等任务真正结束才退出，保证 DrawLimiter 不会在后台网络/CPU 工作仍活着时
    # 提前释放名额。任务先写缓存、后标 done，重试不会落入「PNG 已付费却被丢弃」窗口。
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        current = asyncio.current_task()
        # FastMCP wraps each request in anyio.CancelScope. Once that scope is
        # cancelled, every checkpoint in the same scope is cancelled again;
        # asyncio.shield alone protects the child task, not this waiter.
        # A nested AnyIO shield lets the shared job finish without spinning the
        # event loop, while the original CancelledError is re-raised below.
        with anyio.CancelScope(shield=True):
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    if current is not None:
                        current.uncancel()
                except BaseException:  # noqa: BLE001 -- observe shared failure, preserve cancellation
                    break
        if work.done() and not work.cancelled():
            work.exception()
        raise


async def generate_bitmap_svg(description: str, *, attempt: int = 0) -> str:
    """兼容调试/自检入口；生产 draw 使用 generate_bitmap 同时取得预览。"""
    return (await generate_bitmap(description, attempt=attempt)).svg


def evict_bitmap_cache(description: str, attempt: int) -> None:
    """纸面闸拒收后逐出该 attempt 的位图缓存：拒收内容不得在 TTL 内再次被
    服务——否则同题重试必然复演同一拒绝（生产实测：三轮全命中缓存、三次
    同败），重试环形同虚设。"""
    key = description.strip()
    if attempt:
        key = f"{key}::attempt={attempt}"
    _bitmap_cache.pop(key, None)


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
    # 方向性钉子（R10 复审补）：上面的包围盒对 x/y 转置与上下翻转**不敏感**
    # （⊥ 探针的 x、y 极值同域），行列序写反自检照样绿。⊥ 的竖臂是唯一
    # 「y 跨度远大于 x 跨度」的成分，且必须整体落在画面上半部（y 均值 < 中线）：
    # 转置后它变成横臂（找不到竖臂）、y 镜像后掉到下半部，两类坐标事故都判红。
    mid = _PROBE_SIZE_PX // 2
    vertical = [
        poly
        for poly in polys
        if (poly[:, 1].max() - poly[:, 1].min())
        > 2 * (poly[:, 0].max() - poly[:, 0].min())
    ]
    if not vertical:
        raise BitmapError("描摹自检未找到竖臂：坐标轴可能被转置（行列序写反）")
    if any(float(np.mean(poly[:, 1])) >= mid for poly in vertical):
        raise BitmapError("描摹自检竖臂落在下半画面：y 轴可能被上下翻转")
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
