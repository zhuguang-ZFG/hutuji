"""hutuji ③ MCP 生成服务入口（FastAPI + FastMCP，端口 8300）。

端点：
- GET /files/{name}   G-code 静态文件源（output 目录，S3 板后续从这里下载）
- /mcp                MCP 工具端点（streamable-http，接小智云需经桥接器，见 README）

工具面（对齐 plotter-studio-mcp 的 draw/status 划分，代码全自研）：
- hutuji_draw(description)        自然语言 → SVG → G-code（§5 校验）→ URL
- hutuji_status()                 服务状态
- hutuji_preview_svg(description) 只生成 SVG（调试 LLM 用）
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import re
import secrets
import stat
import time
import hashlib
import zlib
from collections import deque
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Callable
import httpx

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from mcp.server.fastmcp import FastMCP
from .cloud_bridge import WATCHDOG_POLL_SECONDS, read_health_status

from .config import settings
from .config import validate_public_base_url
from .usage_report import record_tool_usage
from .gcode_gen import GcodeError, svg_to_gcode_async, validate_gcode
from .bitmap_svg import BitmapError, generate_bitmap, warm_trace_deps, evict_bitmap_cache
from . import paper_risk
from . import t2s
from .redact import _install_secret_redaction
from .llm_svg import LlmError, _CONTROL_CHAR_RE, _sanitize_description, generate_svg
from .memory_store import (
    forget as _memory_forget,
    recall as _memory_recall,
    remember as _memory_remember,
)
from .activation_relay import (
    InvalidCodeError,
    RelayDisabledError,
    UpstreamError as RelayUpstreamError,
    bind_device,
)
from .article_pages import (
    ARTICLE_MAX_PAGES,
    WRITE_PAGE_CHANGE_SECONDS,
    WRITE_PAGE_CONFIRM_ALLOWANCE_SECONDS,
    WRITE_PAGE_LINES_PER_SECOND,
    WRITE_PAGE_TIME_MARGIN,
    pages_argument as _pages_argument,
)

# 保持默认 streamable_http_path="/mcp"，子应用挂到根（/files 先注册优先匹配），
# 避免 Mount("/mcp") 对裸 /mcp 产生 307 跳转（POST 桥接器不一定跟随重定向）
mcp = FastMCP("hutuji")

_DRAW_FILE_RE = re.compile(
    r"^draw_\d{8}_\d{6}_[A-Za-z0-9_-]{22}\.(?:gcode|svg|png)$"
)

# 同一个式子按结构分组，供脱敏使用：token 用的是 URL-safe base64 字母表，
# **本身可能含 `_`**，所以绝不能靠「最后一个下划线」切分（会把 token 前缀
# 当成时间戳留在输出里；实测约 29% 的 token 会这样泄露）。
_DRAW_FILE_PARTS_RE = re.compile(
    r"^(draw_\d{8}_\d{6})_(?P<token>[A-Za-z0-9_-]{22})\.(?P<ext>gcode|svg|png)$"
)

# 歌曲文件名白名单：小写 id + .ogg，无路径分隔符与点号段，天然免疫目录穿越。
_SONG_FILE_RE = re.compile(r"[a-z0-9_-]{1,64}\.ogg")
logger = logging.getLogger(__name__)


def _report_mcp_tool(name: str, *, ok: bool = True, detail: str | None = None) -> None:
    record_tool_usage(
        ingest_url=getattr(settings, "usage_ingest_url", "") or "",
        ingest_secret=getattr(settings, "usage_ingest_secret", "") or "",
        event_type=f"tool_{name}",
        ok=ok,
        detail=detail,
    )


class DrawLimitError(RuntimeError):
    """LLM 生成入口超过本进程安全限额。"""


_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9:_-]{1,64}$")


def _sanitize_device_id(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if not _DEVICE_ID_RE.fullmatch(value):
        raise ValueError("device_id 只允许 1..64 字 ASCII 字母数字及 :_-")
    return value
# 云端 MCP bridge 与 mcp-proxy 在 300s 时放弃慢响应；完整事务在 290s 后不再发布
# 产物，waiter 在 295s 返回。两道边界之间留给云端重试和 bridge 清理。
DRAW_TRANSACTION_DEADLINE_SECONDS = 290
DRAW_WAITER_DEADLINE_SECONDS = 295
# 健康快照连续 3 个 watchdog 心跳周期未刷新，视为 bridge 已停止或失联。
BRIDGE_HEALTH_MAX_AGE_SECONDS = 3 * WATCHDOG_POLL_SECONDS


class DrawTimeoutError(RuntimeError):
    """完整绘图事务超过云端桥接预算；后台共享任务仍可能完成。"""


class PaperRiskExhaustedError(RuntimeError):
    """纸面风险闸连续拒收（默认 3 轮整轮重生图耗尽）；不发布废 URL。"""


# 纸面闸重试上界（2026-08-27）：1 初试 + 2 重跑；每轮 attempt 破缓存真重跑整轮
# 生图。宁多等一轮也不交废图。
MAX_PAPER_RISK_ATTEMPTS = 3


class DrawLimiter:
    """单进程全局限流；默认对齐课堂级 settings（可用构造参数覆盖）。"""

    def __init__(
        self,
        *,
        per_second: int | None = None,
        per_minute: int | None = None,
        max_concurrent: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        from .config import settings as _settings

        self._env_max_concurrent = (
            _settings.draw_max_concurrent if max_concurrent is None else max_concurrent
        )
        self._env_per_second = (
            _settings.draw_per_second if per_second is None else per_second
        )
        self._env_per_minute = (
            _settings.draw_per_minute if per_minute is None else per_minute
        )
        self.max_concurrent = self._env_max_concurrent
        self.per_second = self._env_per_second
        self.per_minute = self._env_per_minute
        self._clock = clock
        self._starts: deque[float] = deque()
        self._active = 0
        # 刻意不带锁：enter/leave 的临界区内没有任何 await 点，单事件循环里
        # 同步代码天然原子。曾用 asyncio.Lock，但 slot() 的 finally 里
        # `await self.leave()` 要抢锁——任务被二次取消时锁等待会被再打断，
        # `_active` 永不递减，并发名额永久漏掉一个直到进程重启。无 await 的
        # async 方法被 await 时同步执行到底，取消只在挂起点注入，泄漏面消除。

    def _refresh_from_portal(self) -> None:
        from .config import settings as _settings
        from .portal_settings import effective_draw_limit

        self.max_concurrent = effective_draw_limit(
            _settings, "draw_max_concurrent", self._env_max_concurrent
        )
        self.per_second = effective_draw_limit(
            _settings, "draw_per_second", self._env_per_second
        )
        self.per_minute = effective_draw_limit(
            _settings, "draw_per_minute", self._env_per_minute
        )

    async def enter(self) -> None:
        """占用一个生成名额；超限立即报错，不排队等待。"""
        self._refresh_from_portal()
        now = self._clock()
        while self._starts and now - self._starts[0] >= 60.0:
            self._starts.popleft()

        if self._active >= self.max_concurrent:
            # CloudUX-F4：文案用户向（不带技术数字）；计数进日志供运维定位。
            logger.info("绘图服务忙：并发已达上限 %d", self.max_concurrent)
            raise DrawLimitError("绘图服务正忙，请稍候再试")
        recent_second = sum(now - started < 1.0 for started in self._starts)
        if recent_second >= self.per_second:
            logger.info("绘图服务忙：每秒请求数达上限 %d", self.per_second)
            raise DrawLimitError("绘图服务正忙，请稍候再试")
        if len(self._starts) >= self.per_minute:
            logger.info("绘图服务忙：每分钟请求数达上限 %d", self.per_minute)
            raise DrawLimitError("绘图服务正忙，请稍候再试")

        self._starts.append(now)
        self._active += 1

    async def leave(self) -> None:
        """释放并发名额；速率时间戳保留到滑动窗口自然过期。"""
        if self._active > 0:
            self._active -= 1

    @contextlib.asynccontextmanager
    async def slot(self):
        await self.enter()
        try:
            yield
        finally:
            await self.leave()


_draw_limiter = DrawLimiter()


def _new_basename() -> str:
    """文件名带不可猜随机 token（capability URL，见 protocol §5.1 + mcp-server README 组 3.2）。
    格式：draw_<时间戳>_<token>。token 让 URL 本身即凭证，防时间戳枚举。"""
    return "draw_{}_{}".format(
        datetime.now().strftime("%Y%m%d_%H%M%S"),
        secrets.token_urlsafe(16),
    )


def _redact_capability_name(name: str) -> str:
    """status 对外展示时隐藏 token，避免经 MCP 泄露 capability URL。"""
    m = _DRAW_FILE_PARTS_RE.fullmatch(name)
    if not m:
        return "*.gcode"
    # draw_YYYYMMDD_HHMMSS_<token>.ext → draw_YYYYMMDD_HHMMSS_*.ext
    # 用结构化分组去掉整个 token（含其中的下划线），不做字符串切分。
    return f"{m.group(1)}_*.{m.group('ext')}"


def _delete_output_pair(stem: str) -> None:
    """删除同一产物的 SVG/G-code/PNG；文件本就可能只生成了一半。"""
    from .output_store import get_output_store

    get_output_store().delete_stem(stem)


def _delete_stale_output_pair(stem: str, read_mtime: float) -> None:
    """契约复核失败时删除整组产物；路径已被并发替换则跳过以免误删新对象。"""
    gcode_path = settings.output_dir / f"{stem}.gcode"
    try:
        current_mtime = gcode_path.stat().st_mtime
    except OSError:
        return
    if abs(current_mtime - read_mtime) > 1e-3:
        return
    _delete_output_pair(stem)


def _is_expired(path: Path, *, now: float | None = None) -> bool:
    """按 mtime 判断 capability URL 是否已超过公开窗口。"""
    now = time.time() if now is None else now
    return now - path.stat().st_mtime >= settings.output_ttl_seconds


def _read_regular_file(path: Path) -> tuple[bytes, float]:
    """一次打开并基于句柄校验后读取，返回 ``(内容, fstat 的 mtime)``。

    只允许普通文件：拒绝 symlink 与 ``st_nlink != 1`` 的硬链接。打开前后都对
    路径做 ``lstat`` 并与句柄 ``fstat`` 比对身份（dev/ino），路径在检查/打开间
    被 ``rename``/``replace``/换 symlink 时不会读到新对象。任何竞态/拒绝/OSError
    一律由调用方塌成 404。TTL 判断必须使用返回的 mtime（同一对象）。
    """
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    before = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise OSError("目标不是单链接普通文件")
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise OSError("目标不是单链接普通文件")
        after = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(after.st_mode) or after.st_nlink != 1:
            raise OSError("目标在读取期间被替换")
        # dev/ino 在 Linux 与 Windows 均可用于确认打开对象仍是当前路径对象。
        if (before.st_dev, before.st_ino) != (st.st_dev, st.st_ino):
            raise OSError("目标在读取期间被替换")
        if (after.st_dev, after.st_ino) != (st.st_dev, st.st_ino):
            raise OSError("目标在读取期间被替换")
        f = os.fdopen(fd, "rb")
        fd = -1  # 所有权移交文件对象，异常路径不再手动 close
        with f:
            return f.read(), st.st_mtime
    except BaseException:
        if fd >= 0:
            os.close(fd)
        raise


class StaleOutputError(RuntimeError):
    """已落盘的 `.gcode` 不再满足 §5 契约：必须按过期产物处理，不得下发。

    出现的两条真实路径：① 限幅/契约常量收紧后（如 Y 上限 277→190mm），
    收紧前生成、TTL 未到的旧产物仍留在 output_dir，仍可凭 capability URL
    下载并被设备执行；② 落盘后文件在磁盘上被改写。两者生成期校验都已通过，
    只有服务时复核能挡住。携带读取时的 mtime，供调用方在删除前确认路径
    对象未被并发替换。
    """

    def __init__(self, message: str, mtime: float) -> None:
        super().__init__(message)
        self.mtime = mtime


def _read_verified_output(path: Path) -> tuple[bytes, float]:
    """读取产物；`.gcode` 再过一遍 §5 校验（服务时复核）。

    复核与读取在同一次 `asyncio.to_thread` 内完成，用的就是已校验句柄读回的
    字节，不再二次打开文件——否则「读到的」与「校验的」可能不是同一个对象。
    `total_bytes` 传 `len(data)`（磁盘原始字节，与 `_read_generated_gcode`
    同口径），CRLF 产物不会少算 512KiB 上限。
    SVG/PNG 不复核：契约面只在 G-code（§5 是设备执行的唯一闸门）。
    """
    data, mtime = _read_regular_file(path)
    if path.suffix != ".gcode":
        return data, mtime
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise StaleOutputError("G-code 无法按 UTF-8 解码", mtime) from None
    try:
        validate_gcode(text, total_bytes=len(data))
    except GcodeError as exc:
        raise StaleOutputError(str(exc), mtime) from None
    return data, mtime


def _is_regular_capability_file(path: Path) -> bool:
    """lstat 判普通文件 + 能力命名，与 `_read_regular_file` 拒 symlink 同口径。

    `Path.is_file()` 会跟随链接：output_dir 里的 symlink 指向普通文件时会被它当成
    产物（实测 is_file()=True 而 lstat S_ISREG=False），而下载侧 O_NOFOLLOW 拒之。
    清理/统计若跟随，两处口径就不一致。恒不跟随：链接（含 Windows junction）
    一律不算本服务产物。
    """
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and _DRAW_FILE_RE.fullmatch(path.name)


def _cleanup_expired_outputs(*, now: float | None = None) -> int:
    """清除超过 TTL 的 draw_*.svg/gcode/png，返回删除的文件数。"""
    from .output_store import get_output_store

    return get_output_store().cleanup_expired(now=now)


async def _generate_svg_limited(description: str) -> str:
    """所有会调用付费 LLM 的工具共用同一个进程内限流器。"""
    async with _draw_limiter.slot():
        return await generate_svg(description)


# G-code 内容缓存：同一 SVG 的 vpype 输出是确定内容，重复主题只需把上次
# 字节写进新的随机 capability 文件，跳过 ~0.7s 子进程。键是 SVG 内容哈希，
# 与 _new_basename 解耦；TTL/容量与位图结果缓存同口径。只缓存校验通过
# 后的字节，失败不落表。
_GCODE_CACHE_TTL_SECONDS = 600.0
_GCODE_CACHE_MAX_ENTRIES = 16
_gcode_cache: OrderedDict[str, tuple[float, bytes, object]] = OrderedDict()
_gcode_cache_clock = time.monotonic


async def _svg_to_gcode_cached(svg: str, svg_path: Path, gcode_path: Path):
    key = hashlib.sha256(svg.encode("utf-8")).hexdigest()
    now = _gcode_cache_clock()
    hit = _gcode_cache.get(key)
    if hit is not None:
        created, payload, stats = hit
        if now - created < _GCODE_CACHE_TTL_SECONDS:
            _gcode_cache.move_to_end(key)
            logger.info("G-code 内容缓存命中")
            gcode_path.write_bytes(payload)
            return stats
        del _gcode_cache[key]
    stats = await svg_to_gcode_async(svg_path, gcode_path)
    _gcode_cache[key] = (_gcode_cache_clock(), gcode_path.read_bytes(), stats)
    _gcode_cache.move_to_end(key)
    while len(_gcode_cache) > _GCODE_CACHE_MAX_ENTRIES:
        _gcode_cache.popitem(last=False)
    return stats


# 同题云端重试的完整 draw 事务表。小智云约 35s 会放弃慢调用并重试；若事务归
# waiter 所有，取消会提前释放 DrawLimiter，下一次请求又发一轮付费生图。任务表
# 覆盖位图、vpype、产物与返回 JSON；waiter 取消只分离自己，事务仍持有一个名额。
_draw_inflight: dict[str, asyncio.Task[str]] = {}

# 2026-08-28 异步生成失败可见性：记最近一次 _run_draw_job 异常的类型名与描述主题
# 成功后清零，供 hutuji_status 暴露 last_draw_error（不记异常细节，脱敏硬约定）。
_draw_last_error: dict[str, object] | None = None


def _forget_draw_inflight(key: str, done: asyncio.Task[str]) -> None:
    """只清掉自己登记的任务，并取走无人等待时的异常。"""
    if _draw_inflight.get(key) is done:
        del _draw_inflight[key]
    if not done.cancelled():
        done.exception()


# `_pages_argument` 序列化单源已下沉到 article_pages.py（portal 文档导入共用，
# portal 进程没有 mcp 依赖、import 不了本模块）——本文件经顶部 import 别名持有。


async def _chain_device_draw(
    url: str, preview_url: str, pages: list[dict] | None = None
) -> None:
    """生成完成后经官方设备消息 API 主动调 `hutuji.draw`（2026-08-24 P1-3）。

    消灭云端第二步漏调（78/xiaozhi-esp32#1011 多工具漏调：LLM 调完 draw 后
    第二步 hutuji.draw 可能永远不到，纸不动）。尽力而为：任何失败只记日志
    （不记 token、URL 经共享脱敏），draw 结果不受影响——云端两步衔接仍是
    合法回落路径。token 为空 = 禁用。

    pages 非空 = 多页文章（hutuji_write）：设备 MCP Property 只支持
    boolean/integer/string，故 pages 以紧凑 JSON 字符串传（设备侧
    `hutuji::ParseArticlePages` 纯扫描解析，键序 url→preview_url 固定）。
    """
    token = getattr(settings, "xiaozhi_device_token", "")
    if not token:
        return
    arguments: dict[str, str] = {"url": url, "preview_url": preview_url}
    if pages:
        arguments["pages"] = _pages_argument(pages)
    payload = {"name": "hutuji.draw", "arguments": arguments}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{settings.xiaozhi_api_base}/messaging/device/tools/call",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code != 200:
            logger.warning("设备链式调用失败 status=%d（回落云端两步）", resp.status_code)
            _report_mcp_tool("draw_push", ok=False, detail=f"http_{resp.status_code}")
        else:
            logger.info("设备链式调用 hutuji.draw 已受理")
            _report_mcp_tool("draw_push", ok=True)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("设备链式调用异常（%s，回落云端两步）", type(exc).__name__)
        _report_mcp_tool("draw_push", ok=False, detail=type(exc).__name__)


async def _run_draw_job(
    description: str, public_base_url: str, *, job_id: str | None = None,
    chain_push: bool = True,
) -> str:
    """完整生成事务；生命周期独立于任一 JSON-RPC waiter。"""
    global _draw_last_error
    from . import job_ledger
    from .output_store import JobPaths, get_output_store

    token = job_ledger.bind_job_id(job_id) if job_id else None

    store = get_output_store()
    artifact_base: str | None = None
    stats = None
    active_paths: JobPaths | None = None
    deadline = time.monotonic() + DRAW_TRANSACTION_DEADLINE_SECONDS

    async def _write_bitmap_artifacts(bitmap, base: str) -> tuple[JobPaths, object]:
        with store.job_workspace(base) as paths:
            paths.svg_path.write_text(bitmap.svg, encoding="utf-8")
            paths.preview_path.write_bytes(bitmap.preview_png)
            gcode_t0 = time.monotonic()
            job_stats = await _svg_to_gcode_cached(
                bitmap.svg, paths.svg_path, paths.gcode_path
            )
            logger.info("G-code 段耗时 %.1fs", time.monotonic() - gcode_t0)
            # OSS 模式须在 with 块内 finalize；local 模式 finalize 为空操作。
            store.finalize_job(paths)
            return paths, job_stats

    try:
        store.ensure_ready()
        await asyncio.to_thread(_cleanup_expired_outputs)
        from . import curated as curated_mod

        bitmap = await asyncio.to_thread(curated_mod.lookup_preferred, description)
        if bitmap is not None:
            if job_id:
                job_ledger.schedule_route(job_id=job_id, route="curated")
            base = _new_basename()
            active_paths, stats = await _write_bitmap_artifacts(bitmap, base)
            artifact_base = active_paths.base
        else:
            if job_id:
                job_ledger.schedule_route(job_id=job_id, route="ai")
            async with _draw_limiter.slot():
                last_reject: paper_risk.PaperRiskReject | None = None
                for attempt in range(MAX_PAPER_RISK_ATTEMPTS):
                    if time.monotonic() >= deadline:
                        raise DrawTimeoutError("绘图生成超时，请稍后重试")
                    bitmap = await generate_bitmap(description, attempt=attempt)
                    if time.monotonic() >= deadline:
                        raise DrawTimeoutError("绘图生成超时，请稍后重试")
                    base = _new_basename()
                    with store.job_workspace(base) as paths:
                        paths.svg_path.write_text(bitmap.svg, encoding="utf-8")
                        paths.preview_path.write_bytes(bitmap.preview_png)
                        gcode_t0 = time.monotonic()
                        stats = await _svg_to_gcode_cached(
                            bitmap.svg, paths.svg_path, paths.gcode_path
                        )
                        logger.info("G-code 段耗时 %.1fs", time.monotonic() - gcode_t0)
                        gcode_text = paths.gcode_path.read_text(encoding="utf-8")
                        if not getattr(settings, "paper_risk_gate", True):
                            report = paper_risk.evaluate_gcode(
                                gcode_text, pen_lifts=stats.pen_lifts
                            )
                            logger.info(
                                "paper_risk shadow short=%.2f lifts=%d gap_p50=%.2f "
                                "ink=%.1f scribble=%d segs=%d",
                                report.short_g1_ratio,
                                report.pen_lifts,
                                report.travel_gap_p50_mm,
                                report.ink_span_mm,
                                report.scribble_core_hits,
                                report.g1_xy_segments,
                            )
                            store.finalize_job(paths)
                            active_paths = paths
                            artifact_base = paths.base
                            break
                        try:
                            paper_risk.enforce_gcode(
                                gcode_text, pen_lifts=stats.pen_lifts
                            )
                            logger.info(
                                "paper_risk pass attempt=%d pen_lifts=%d",
                                attempt,
                                stats.pen_lifts,
                            )
                            store.finalize_job(paths)
                            active_paths = paths
                            artifact_base = paths.base
                            break
                        except paper_risk.PaperRiskReject as exc:
                            last_reject = exc
                            logger.warning(
                                "paper_risk reject reason=%s attempt=%d",
                                exc.reason,
                                attempt,
                            )
                            evict_bitmap_cache(description, attempt)
                            store.abort_job(paths)
                else:
                    raise PaperRiskExhaustedError(
                        "这张线稿太碎，纸上会画糊，请换个说法再试"
                    ) from last_reject
        if time.monotonic() >= deadline:
            raise DrawTimeoutError("绘图生成超时，请稍后重试")
    except BaseException as exc:
        if isinstance(exc, Exception):
            _draw_last_error = {
                "description": description,
                "error_type": type(exc).__name__,
                "ts": time.time(),
            }
        if active_paths is not None:
            store.abort_job(active_paths)
        raise
    finally:
        if token is not None:
            job_ledger.reset_job_id(token)

    assert artifact_base is not None
    assert stats is not None
    gcode_name = f"{artifact_base}.gcode"
    preview_name = f"{artifact_base}.png"
    _draw_last_error = None
    if chain_push and getattr(settings, "xiaozhi_device_token", ""):
        asyncio.create_task(
            _chain_device_draw(
                f"{public_base_url}/files/{gcode_name}",
                f"{public_base_url}/files/{preview_name}",
            )
        )
    return json.dumps({
        "url": f"{public_base_url}/files/{gcode_name}",
        "preview_url": f"{public_base_url}/files/{preview_name}",
        "lines": stats.lines,
        "bytes": stats.bytes,
        "pen_lifts": stats.pen_lifts,
        "ttl_seconds": settings.output_ttl_seconds,
    }, ensure_ascii=False)


async def _hutuji_draw_via_queue(
    safe_description: str, public_base_url: str, device_id: str, *,
    force_sync: bool = False, chain_push: bool = True,
) -> str:
    """Redis 队列模式：MCP 只入队/等待，实际生成由 draw-worker 执行。

    force_sync=True 供本机内部调用（draw-portal 网页生图）：即使生产开了
    draw_async 也等待完整结果，不回 accepted；chain_push=False 时完成不推
    演示设备预览（推送由 portal 页面显式触发）。
    """
    from .draw_queue import (
        DrawQueueBusyError,
        attach_or_enqueue,
        queue_enabled,
        wait_result,
    )

    if not queue_enabled():
        raise RuntimeError("队列模式未启用")
    try:
        job_id = await attach_or_enqueue(
            safe_description, public_base_url, device_id=device_id,
            chain_push=chain_push,
        )
    except DrawQueueBusyError as exc:
        raise DrawLimitError(str(exc)) from exc

    if not force_sync and getattr(settings, "draw_async", False) and getattr(settings, "xiaozhi_device_token", ""):
        return json.dumps({
            "accepted": True,
            "description": safe_description,
            "job_id": job_id,
            "eta_seconds": 45,
            "note": "生成完成会自动推送到设备预览，无需重复调用",
        }, ensure_ascii=False)
    if not force_sync and getattr(settings, "draw_async", False):
        logger.warning("HUTUJI_DRAW_ASYNC 已启用但未配置 HUTUJI_XIAOZHI_DEVICE_TOKEN，回落同步模式")

    try:
        return await wait_result(job_id, timeout=DRAW_WAITER_DEADLINE_SECONDS)
    except asyncio.TimeoutError:
        from . import job_ledger

        job_ledger.schedule_timed_out(job_id=job_id)
        logger.warning("绘图生成超时（queue waiter=%ss）", DRAW_WAITER_DEADLINE_SECONDS)
        raise DrawTimeoutError("绘图生成超时，请稍后重试") from None


@mcp.tool()
async def hutuji_draw(description: str, device_id: str = "") -> str:
    """当用户要求「画/绘/写一幅…」时调用：把自然语言变成写字机可执行的单线笔画。

    结果分两种。启用 `HUTUJI_DRAW_ASYNC=1` 且配置设备消息 token 时，立即返回
    `accepted: true`，服务端后台生成完成后自动推送到设备预览（调用设备侧 `hutuji.draw`）。
    收到 accepted 后不要重复调用本工具或设备侧 `hutuji.draw`；预览出现后且用户明确
    同意开始画，再调用设备侧 `hutuji.confirm`。同步模式下，工具等待完成并返回 `url`
    和 `preview_url`；若已配置设备消息 token，服务端在唯一完成点也会尝试自动调用
    `hutuji.draw`，调用方不要重复推送，否则才把两个地址原样传给设备。用户明确确认后
    再调用 `hutuji.confirm`。地址有效期以 `ttl_seconds` 为准，拿到后立即传递。
    生图通常耗时约 15～45 秒。参数 description 建议为 10～40 字的简短中文主题，最长 200 字。
    可选 device_id（设备 MAC/序列号，≤64 字）用于单设备限流，省略则只走全局限流。
    好例子：「一只坐着的小猫」「带烟囱的小房子」「写个福字」；坏例子是长篇故事、填色/写实要求或空话。
    失败时根据错误白话说明，不要编造 URL。"""

    _report_mcp_tool("draw")
    public_base_url = validate_public_base_url(settings.public_base_url)
    safe_description = _sanitize_description(description).strip()
    safe_device_id = _sanitize_device_id(device_id)

    from .draw_queue import queue_enabled

    if queue_enabled():
        return await _hutuji_draw_via_queue(
            safe_description, public_base_url, safe_device_id
        )

    work = _draw_inprocess(safe_description, public_base_url, chain_push=True)

    # 2026-08-28 异步生成+完成推送（默认关闭）：双真立即 accepted，任务由
    # _draw_inflight 持有生命周期；缺 token 则回落同步并 warning。
    if getattr(settings, "draw_async", False) and getattr(settings, "xiaozhi_device_token", ""):
        return json.dumps({
            "accepted": True,
            "description": safe_description,
            "eta_seconds": 45,
            "note": "生成完成会自动推送到设备预览，无需重复调用",
        }, ensure_ascii=False)
    if getattr(settings, "draw_async", False):
        logger.warning("HUTUJI_DRAW_ASYNC 已启用但未配置 HUTUJI_XIAOZHI_DEVICE_TOKEN，回落同步模式")

    return await _await_draw_work(work, log_label="mcp")


def _inflight_key_for(safe_description: str, chain_push: bool) -> str:
    """进程内去重键：chain_push 不同的同名任务不得互相搭车（理由同 draw_queue）。

    语音（True）搭上网页（False）的任务完成时不推设备预览，语音用户干等；
    反向则网页任务串推到演示设备。与 draw_queue._inflight_material 同口径。
    """
    return safe_description if chain_push else safe_description + "\x00nopush"


def _draw_inprocess(
    safe_description: str, public_base_url: str, *, chain_push: bool
) -> "asyncio.Task[str]":
    """进程内（无队列）共享任务表登记；同题 waiter 搭同一事务。"""
    key = _inflight_key_for(safe_description, chain_push)
    work = _draw_inflight.get(key)
    if work is not None and work.get_loop() is not asyncio.get_running_loop():
        # 测试/嵌入调用可能轮换事件循环；生产单 loop 不会走此分支。
        if work.done() and not work.cancelled():
            work.exception()
        if _draw_inflight.get(key) is work:
            del _draw_inflight[key]
        work = None
    if work is None:
        work = asyncio.create_task(
            _run_draw_job(safe_description, public_base_url, chain_push=chain_push)
        )
        _draw_inflight[key] = work
        work.add_done_callback(
            lambda done, k=key: _forget_draw_inflight(k, done)
        )
    return work


async def _await_draw_work(work: "asyncio.Task[str]", *, log_label: str) -> str:
    # shield 只保护完整事务不被本次 waiter 的取消连带杀死；waiter 在 bridge
    # watchdog 之前明确超时，后续同题重试仍搭同一个任务。
    try:
        return await asyncio.wait_for(
            asyncio.shield(work), timeout=DRAW_WAITER_DEADLINE_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning("绘图生成超时（%s waiter=%ss）", log_label, DRAW_WAITER_DEADLINE_SECONDS)
        raise DrawTimeoutError("绘图生成超时，请稍后重试") from None


async def _draw_internal_sync(description: str) -> str:
    """draw-portal 网页生图的本机内部入口（/internal/draw）。

    与语音路径同一条生成链，但两点刻意不同：
    - 强制同步等待完整结果（生产 draw_async 的 accepted 分支不适用于网页：
      页面要自己拿 preview_url 展示，推送也由买家显式触发）；
    - chain_push=False：完成时不链式推演示设备（运营 demo 机不该收到买家
      网页生图的预览）。
    不计 _report_mcp_tool：portal 侧以 web_generate 自行记账，避免双计。
    """
    public_base_url = validate_public_base_url(settings.public_base_url)
    safe_description = _sanitize_description(description).strip()

    from .draw_queue import queue_enabled

    if queue_enabled():
        return await _hutuji_draw_via_queue(
            safe_description, public_base_url, "",
            force_sync=True, chain_push=False,
        )
    work = _draw_inprocess(safe_description, public_base_url, chain_push=False)
    return await _await_draw_work(work, log_label="internal")


@mcp.tool()
async def hutuji_status() -> str:
    """查询云端「绘图生成服务」是否就绪：LLM 是否配置、产出目录、最近 G-code 数量。

    在用户问「能不能画了」「服务正常吗」「刚才生成成功没」时使用。
    这不是写字机笔尖实时位置；设备连线请用设备侧 hutuji.status。"""
    _report_mcp_tool("status")
    await asyncio.to_thread(_cleanup_expired_outputs)
    out = settings.output_dir
    def _safe_mtime(p: Path) -> float:
        # 与 _cleanup_expired_outputs（捕 OSError 记 warning）和 _serve_file（塌 404）
        # 同口径（SC-01）：stat 的任意 OSError（权限/竞态/目录消失）都按 0.0 处理，
        # 不让健康查询因单个文件读不到 stat 而变 500。
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0
    # R13-SRV-01：glob + 逐文件 stat 是 IO，与同函数上一行的 cleanup 同口径
    # 卸载到线程，避免目录大/慢存储时状态查询卡住事件循环。
    def _collect() -> tuple[bool, list[Path]]:
        exists = out.is_dir()
        # 只统计能力命名的产物：手工放进目录的外来 .gcode 不属于本服务产出，
        # 混入会让 gcode_files/latest 反映不出真实的生成活动。
        files = sorted(
            [p for p in out.glob("*.gcode") if _is_regular_capability_file(p)],
            key=_safe_mtime,
        ) if exists else []
        return exists, files
    dir_exists, files = await asyncio.to_thread(_collect)
    return json.dumps({
        "output_dir_exists": dir_exists,
        "image_model": settings.image_model,
        "public_base_url": settings.public_base_url,
        "llm_configured": settings.llm_configured,
        "llm_model": settings.llm_model,
        "gcode_files": len(files),
        "latest": _redact_capability_name(files[-1].name) if files else None,
        # CloudUX-F6：显式 mtime 时间戳，让「刚才生成成功没」不必靠 LLM 解析文件名。
        "latest_ts": _safe_mtime(files[-1]) if files else None,
        "last_draw_error": _draw_last_error,
    }, ensure_ascii=False)


@mcp.tool()
async def hutuji_preview_svg(description: str) -> str:
    """只预览矢量草稿、不生成 G-code、不驱动写字机。调试构图或用户说「先看看别出纸」时用。

    走的是 LLM 直写 SVG 旧路线（调试用）；hutuji_draw 出图走位图路线，两者观感不同。
    参数 description 同 hutuji_draw。返回 SVG 文本截断（前 2000 字符）。"""
    _report_mcp_tool("preview_svg")
    svg = await _generate_svg_limited(description)
    return svg[:2000]


_WRITE_TEXT_MAX_CHARS = 5000

# WRITE_PAGE_* 错峰 TTL 常量与 `pages_argument` 同源在 article_pages.py（实测
# 证据注释随常量在那里）；本模块顶部 import 持有同名别名，test_write_text 的
# `server.WRITE_PAGE_*` 引用不变。

# 字库解析缓存：单文件最大 ~10MB（42MB 目录全量），解析一次进程内复用；
# key 是 (目录, 名字)，目录切换自然隔离。不设 LRU——字库数量以个位数计。
_gfont_cache: dict[tuple[str, str], object] = {}
_gfont_metrics_cache: dict[tuple[str, str], object] = {}


async def _run_write_job(
    text: str, font_name: str, size_mm: float, line_spacing: float,
    char_gap_mm: float, public_base_url: str, *, chain_push: bool = True,
) -> str:
    """写字排版事务：奎享单线字库 → 分页 SVG+PNG → 无版式 vpype → §5 校验 → 产物。

    不经 DrawLimiter（本地计算，无付费后端）；vpype 并发仍由 gcode_gen 信号量收口。
    多页时返回 pages 数组，并经 `_chain_device_draw` 把整份 pages 一次推给设备
    （设备侧逐页下载→灌流→M30 换纸→下一页；见 protocol §10.2）。
    """
    from . import write_text
    from .llm_svg import _validate_svg
    from .output_store import get_output_store

    cache_key = (settings.gfont_dir, font_name)
    font = _gfont_cache.get(cache_key)
    if font is None:
        font = await asyncio.to_thread(write_text.load_font, settings.gfont_dir, font_name or None)
        _gfont_cache[cache_key] = font
    metrics = _gfont_metrics_cache.get(cache_key)
    if metrics is None:
        metrics = await asyncio.to_thread(write_text.measure_font, font)
        _gfont_metrics_cache[cache_key] = metrics

    layout = write_text.WriteLayout(
        size_mm=size_mm, line_spacing=line_spacing, char_gap_mm=char_gap_mm,
    )
    result = await asyncio.to_thread(write_text.layout_text, text, font, metrics, layout, max_pages=ARTICLE_MAX_PAGES)

    store = get_output_store()
    store.ensure_ready()
    # 错峰 TTL 基准（protocol §4.2）：设备逐页「下载→灌流→换纸」串行推进，第 i 页
    # 要到 t0 + Σ(前序页实际耗时) 才被下载。页耗时按行数折算：2026-09-01 真机实测
    # 1021 行灌流 116.3s ≈ 8.8 行/s、换纸 8s；12mm 满页实测 10597 行 ≈ 20 分钟。
    # 旧版统一 300s/页预算建在 124s/页模型上，满页文章第 2 页必 404
    # （2026-09-03 advisory 复核数字实锤）。故第 i 页产物 mtime 前移到
    # t0 + 确认补贴 + Σ_{j<i} est_j（est_j = lines_j/8.8 + 8，再乘 2 倍余量覆盖
    # 设备重试、用户暂停与单点实测外推方差——窗口过宽只是 capability URL 活得久，
    # 过紧就是第 i 页 404）；确认补贴是一次性常量：t0 到人按「确认」开画的等待
    # 无上界、与页大小无关，稀疏页单靠 2× 余量只有几分钟绝对量（est₁≈248s 时
    # 确认延迟 >12 分钟第 2 页必 404）。判定链只读 mtime。
    ttl_base = time.time()
    multi_page = len(result.pages) > 1
    cumulative = WRITE_PAGE_CONFIRM_ALLOWANCE_SECONDS  # 一次性，不按页乘
    pages_out: list[dict] = []
    for page_index, page in enumerate(result.pages):
        base = _new_basename()
        svg = write_text.page_to_svg(page)
        _validate_svg(svg)  # 机器产物同 draw 链口径：一切进管线的 SVG 必过白名单
        # 预览必须是 PNG：设备侧 IsValidDrawCapabilityUrl 硬校验 .png，且
        # LvglAllocatedImage 只解 PNG（.svg 会在 StartDraw 就被拒，纸都不会动）。
        preview_png = await asyncio.to_thread(write_text.page_to_preview_png, page)
        with store.job_workspace(base) as paths:
            paths.svg_path.write_text(svg, encoding="utf-8")
            paths.preview_path.write_bytes(preview_png)
            # normalize=False：写字链版式自排，layout 会让字号随内容量漂移
            stats = await svg_to_gcode_async(
                paths.svg_path, paths.gcode_path, normalize=False
            )
            # 单页保持写盘时刻语义（既有行为）；多页连第 1 页也带确认补贴——
            # 确认延迟卡住的是第 1 页的首次下载本身，只补贴后续页等于没建模。
            store.finalize_job(
                paths,
                created_at=ttl_base + cumulative if multi_page else None,
            )
        cumulative += (
            stats.lines / WRITE_PAGE_LINES_PER_SECOND + WRITE_PAGE_CHANGE_SECONDS
        ) * WRITE_PAGE_TIME_MARGIN
        pages_out.append({
            "page": page_index + 1,
            "url": f"{public_base_url}/files/{base}.gcode",
            "preview_url": f"{public_base_url}/files/{base}.png",
            "lines": stats.lines,
            "bytes": stats.bytes,
            "pen_lifts": stats.pen_lifts,
            "text_lines": page.line_count,
        })

    if chain_push and getattr(settings, "xiaozhi_device_token", ""):
        # 与 draw 链同口径：链式推送是尽力而为，失败只记日志，返回值不受影响。
        # pages 恒传（含单页）——设备侧 page[0] 必须与 url/preview_url 一致，
        # 单页也走同一条解析路径，避免两套语义。
        asyncio.create_task(
            _chain_device_draw(
                pages_out[0]["url"], pages_out[0]["preview_url"], pages_out,
            )
        )
    return json.dumps({
        "pages": pages_out,
        "total_pages": len(pages_out),
        "url": pages_out[0]["url"],
        "preview_url": pages_out[0]["preview_url"],
        # 两步回落透传件：链式推送未配置/失败时，调用方若要自调设备侧
        # hutuji.draw，必须把本字段原样（一字不改）作为 pages 参数传入——
        # 设备幂等谓词要求「完全相同参数」，与链式推送同一序列化点产出，
        # 任何重组都会变成不同参数而被 busy 拒绝（多页回落失效根因）。
        "pages_arg": _pages_argument(pages_out),
        "missing_chars": result.missing_chars,
        "truncated": result.truncated,
        "font": font.meta.display_name,
        "size_mm": layout.size_mm,
        "ttl_seconds": settings.output_ttl_seconds,
    }, ensure_ascii=False)


@mcp.tool()
async def hutuji_write(
    text: str, font: str = "", size_mm: float = 12.0,
    line_spacing: float = 1.6, char_gap_mm: float = 0.8,
) -> str:
    """用奎享手写字库把整段文字写成字（钢笔单线手写体，非画图）。

    用户说「写一封信/写一段话/抄一篇文章」时用；要画图用 hutuji_draw。
    text 是要写的内容（\\n 分段，段首自动缩进两格，自动折行/分页/标点避头）。
    font 字库名（留空默认字数最多的手写字库）；size_mm 字高（2–40，默认 12）；
    line_spacing 行距倍数（默认 1.6）；char_gap_mm 字距（默认 0.8）。
    返回分页产物 URL 数组（多页时 S3 逐页下发）；missing_chars 列缺字，
    truncated=true 表示超长被截断——两者都要如实告知用户。写入前文本统一
    繁→简规范化（云端文章常混入繁体，纸面简繁混杂难看），不必自己转换。
    服务端在完成点会自动链式调用设备侧 hutuji.draw 推送预览（含全部页），
    调用方不要重复推送；仅在自动推送未生效时才自调设备侧 hutuji.draw，且
    多页必须把返回里的 pages_arg 字段一字不改地作为 pages 参数透传（设备按
    完全相同参数判幂等，自己重组 JSON 会被 busy 拒绝）。用户明确确认后再调
    hutuji.confirm。"""
    _report_mcp_tool("write")
    from .write_text import WriteTextError

    safe_text = t2s.to_simplified(text.strip())
    if not safe_text:
        raise WriteTextError("文本为空")
    if len(safe_text) > _WRITE_TEXT_MAX_CHARS:
        raise WriteTextError(f"文本超长：{len(safe_text)} 字符（上限 {_WRITE_TEXT_MAX_CHARS}）")
    public_base_url = validate_public_base_url(settings.public_base_url)
    return await _run_write_job(
        safe_text, font.strip(), size_mm, line_spacing, char_gap_mm, public_base_url,
    )

# 笔顺数据模块级缓存（同 _gfont_cache 口径：import 期不探测目录，首次调用惰性加载）
_stroke_data_cache: dict[str, object] = {}

# 一次教学的汉字上限：教学页每字一页起，小孩注意力/纸张消耗都有限
_TEACH_MAX_CHARS = 20


async def _run_teach_job(
    text: str, public_base_url: str, *, chain_push: bool = True,
) -> str:
    """笔顺教学事务：标准笔顺中心线 → 渐显面板页 → 无版式 vpype → §5 校验 → 产物。

    与 _run_write_job 同一产物面/错峰 TTL/链式推送口径；不经 DrawLimiter
    （本地数据，无付费后端）；vpype 并发仍由 gcode_gen 信号量收口。
    """
    from . import stroke_order, write_text
    from .llm_svg import _validate_svg
    from .output_store import get_output_store

    if not settings.stroke_dir:
        raise stroke_order.StrokeOrderError("笔顺数据未配置（HUTUJI_STROKE_DIR 为空）")
    data = _stroke_data_cache.get(settings.stroke_dir)
    if data is None:
        data = await asyncio.to_thread(stroke_order.StrokeData.load, settings.stroke_dir)
        _stroke_data_cache[settings.stroke_dir] = data

    result = await asyncio.to_thread(
        stroke_order.layout_teach, text, data, ARTICLE_MAX_PAGES,
    )

    store = get_output_store()
    store.ensure_ready()
    ttl_base = time.time()
    multi_page = len(result.pages) > 1
    cumulative = WRITE_PAGE_CONFIRM_ALLOWANCE_SECONDS
    pages_out: list[dict] = []
    for page_index, page in enumerate(result.pages):
        base = _new_basename()
        svg = write_text.page_to_svg(page)
        _validate_svg(svg)  # 机器产物同 draw 链口径：一切进管线的 SVG 必过白名单
        preview_png = await asyncio.to_thread(write_text.page_to_preview_png, page)
        with store.job_workspace(base) as paths:
            paths.svg_path.write_text(svg, encoding="utf-8")
            paths.preview_path.write_bytes(preview_png)
            stats = await svg_to_gcode_async(
                paths.svg_path, paths.gcode_path, normalize=False
            )
            store.finalize_job(
                paths,
                created_at=ttl_base + cumulative if multi_page else None,
            )
        cumulative += (
            stats.lines / WRITE_PAGE_LINES_PER_SECOND + WRITE_PAGE_CHANGE_SECONDS
        ) * WRITE_PAGE_TIME_MARGIN
        pages_out.append({
            "page": page_index + 1,
            "url": f"{public_base_url}/files/{base}.gcode",
            "preview_url": f"{public_base_url}/files/{base}.png",
            "lines": stats.lines,
            "bytes": stats.bytes,
            "pen_lifts": stats.pen_lifts,
        })

    if chain_push and getattr(settings, "xiaozhi_device_token", ""):
        asyncio.create_task(
            _chain_device_draw(
                pages_out[0]["url"], pages_out[0]["preview_url"], pages_out,
            )
        )
    return json.dumps({
        "pages": pages_out,
        "total_pages": len(pages_out),
        "url": pages_out[0]["url"],
        "preview_url": pages_out[0]["preview_url"],
        "pages_arg": _pages_argument(pages_out),
        "missing_chars": result.missing_chars,
        "truncated": result.truncated,
        "ttl_seconds": settings.output_ttl_seconds,
    }, ensure_ascii=False)


@mcp.tool()
async def hutuji_teach(text: str) -> str:
    """教小孩写汉字：按国家规范笔顺把汉字一笔一笔分解写出来（田字格渐显面板）。

    用户说「教小孩写字/笔顺/这个字怎么写/分解笔画/写字课」时用；写整段文章用
    hutuji_write，画图用 hutuji_draw。text 是要教的汉字（空格忽略，一次最多 20 字，
    每字一页：首行成形大字 + 逐笔渐显面板，第 k 格 = 前 k 笔）。
    返回分页产物 URL 数组；missing_chars 列缺字（不在笔顺数据内），
    truncated=true 表示超页截断——两者都要如实告知用户。服务端在完成点会自动
    链式调用设备侧 hutuji.draw 推送预览（含全部页），调用方不要重复推送；
    仅在自动推送未生效时才自调设备侧 hutuji.draw，且多页必须把返回里的
    pages_arg 字段一字不改地作为 pages 参数透传。用户明确确认后再调 hutuji.confirm。"""
    _report_mcp_tool("teach")
    from .stroke_order import StrokeOrderError

    safe_text = text.strip()
    if not safe_text:
        raise StrokeOrderError("文本为空")
    if len(safe_text) > _TEACH_MAX_CHARS:
        raise StrokeOrderError(f"一次最多教 {_TEACH_MAX_CHARS} 个字（收到 {len(safe_text)}）")
    public_base_url = validate_public_base_url(settings.public_base_url)
    return await _run_teach_job(safe_text, public_base_url)

@mcp.tool()
async def hutuji_remember(key: str, value: str) -> str:
    """记住用户告诉你的事情（称呼、偏好、约定等）。

    key 是简短主题（≤50 字），value 是要记住的内容（≤500 字），同主题会覆盖旧的。
    用户说「记住…」「以后都…」时用；密码、验证码等敏感信息不要记。
    重启后记忆仍在，除非用户说忘掉。"""
    _report_mcp_tool("remember")
    return await asyncio.to_thread(_memory_remember, key, value)


@mcp.tool()
async def hutuji_recall(key: str = "") -> str:
    """回忆之前记住的事情。

    key 留空返回全部记忆（很多时只回最近的并注明总数）；给 key 按主题匹配。
    用户问「我说过…吗」「你记得…吗」「我喜欢什么」时先查它再回答，查不到就老实说没记住，不要编。"""
    _report_mcp_tool("recall")
    return await asyncio.to_thread(_memory_recall, key)


@mcp.tool()
async def hutuji_forget(key: str) -> str:
    """忘掉记住的事情：key 精确删除；传 * 清空全部（须用户明确要求全部忘掉）。

    用户说「忘掉…」「别记了」时用。"""
    _report_mcp_tool("forget")
    return await asyncio.to_thread(_memory_forget, key)


def _songs_dir() -> Path:
    return settings.output_dir / "songs"


def _load_song_catalog() -> list[dict]:
    """读曲库；catalog.json 缺失/损坏一律空清单——唱歌是娱乐面，fail-open 不可 500。

    条目必须 id/title/file 齐全、file 过名字白名单且文件真实存在才列出：
    catalog 与磁盘漂移时宁可少列，也不能给设备发必然 404 的 URL。
    """
    try:
        raw = json.loads((_songs_dir() / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = raw.get("songs") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return []
    catalog: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        song_id, title, file = entry.get("id"), entry.get("title"), entry.get("file")
        if not all(isinstance(x, str) and x for x in (song_id, title, file)):
            continue
        if not _SONG_FILE_RE.fullmatch(file):
            continue
        if not (_songs_dir() / file).is_file():
            continue
        duration = entry.get("duration_seconds")
        catalog.append({
            "id": song_id,
            "title": title,
            "file": file,
            "duration_seconds": duration if isinstance(duration, (int, float)) else None,
        })
    return catalog


@mcp.tool()
async def hutuji_sing(song: str = "") -> str:
    """点歌：song 为歌名，可留空查看曲库。

    返回 JSON：found=true 时含 url/title/duration_seconds，须把 url 和 title 交给
    设备端工具 hutuji.sing 播放；found=false 时附 catalog 可点歌曲清单，把歌名念给用户挑。
    曲库里没有的歌不要假装会唱。"""
    _report_mcp_tool("sing")
    public_base_url = validate_public_base_url(settings.public_base_url)
    catalog = await asyncio.to_thread(_load_song_catalog)
    query = _CONTROL_CHAR_RE.sub("", song or "").strip()[:50]
    hit = None
    if query:
        # id 精确优先（助手可能直接引用 catalog 的 id），再 title 子串（用户口语）。
        hit = next((e for e in catalog if e["id"] == query), None)
        if hit is None:
            hit = next((e for e in catalog if query in e["title"]), None)
    if hit is None:
        return json.dumps({
            "found": False,
            "catalog": [
                {"id": e["id"], "title": e["title"], "duration_seconds": e["duration_seconds"]}
                for e in catalog
            ],
        }, ensure_ascii=False)
    return json.dumps({
        "found": True,
        "url": f"{public_base_url}/songs/{hit['file']}",
        "title": hit["title"],
        "duration_seconds": hit["duration_seconds"],
    }, ensure_ascii=False)


# ----- FastAPI 应用 -----

_mcp_app = mcp.streamable_http_app()

# 会话 idle 回收（R10-BRG-02）：FastMCP 1.28.1 构造 StreamableHTTPSessionManager
# 时不透传 session_idle_timeout（恒 None）——桥接/mcp-proxy 每次重连都留下一个
# 再也无人 DELETE 的旧会话，其 run_server 任务与内存永不回收，慢泄漏直到重启。
# manager 在会话创建/请求期读 self.session_idle_timeout（streamable_http_manager
# 第 259/294 行），构造后补设属性即完整生效。取 3600s：远大于桥接看门狗 300s
# 与最慢合法绘图，只回收真正的孤儿；孤儿被回收后 mcp-proxy 若还拿旧 id 来会得
# 到 Session terminated，由桥接判死重连自愈（cloud_bridge R10-BRG-01）——代价是
# 静默超 1 小时后的第一次调用失败一次，随后自动恢复。
_MCP_SESSION_IDLE_TIMEOUT_SECONDS = 3600
mcp.session_manager.session_idle_timeout = _MCP_SESSION_IDLE_TIMEOUT_SECONDS


_MCP_PATH = mcp.settings.streamable_http_path.rstrip("/") or "/mcp"


class LoopbackOnlyMcpGuard:
    """应用层兜底：`/mcp` 只接受 loopback 对端连接。

    `settings.host` 只管 `python -m hutuji_mcp.server` 这一条启动路径；生产 unit 与
    开发常用的 `python -m uvicorn hutuji_mcp.server:app --host …` 由 CLI 决定监听面，
    根本不读该字段——HUTUJI_HOST 合法为 127.0.0.1 而进程绑 0.0.0.0 是可达状态。
    `/mcp` 是无鉴权控制面（能驱动写字机出图），此前防线全在应用之外：nginx 显式
    404 + 安全组不放 8300。任一层被绕（直跑 uvicorn、安全组放宽、换反代）即裸曝。

    判据只用 ASGI `scope["client"]` 的对端地址。**该地址不可伪造有前提**：生产
    unit 的 ExecStart 必须带 `--no-proxy-headers`。uvicorn 默认
    `proxy_headers=True`，会把 `ProxyHeadersMiddleware` 包在最外层，并用
    `X-Forwarded-For` **改写 scope["client"]**——即改写本刀的唯一判据；其信任面
    `forwarded_allow_ips` 默认值又取自环境变量
    （`os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")`），而 unit 带
    `EnvironmentFile=`，于是 env 文件里一行 `FORWARDED_ALLOW_IPS=*` 就能静默放宽。
    2026-08-11 实测：放宽后从公网对端发 `X-Forwarded-For: 127.0.0.1` 即被本刀放行
    （默认信任面下同一请求 404）。关掉 proxy_headers 后中间件根本不装，
    scope["client"] 恒为真实 socket 对端，这句「不可伪造」才成立。
    SDK 自带的 DNS-rebinding 防护（FastMCP 因 host 是 loopback 而自动开启，
    `allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"]`）**不能替代本刀**：
    它校验的是 `Host` 请求头，而请求头由攻击方完全控制。2026-08-09 实测：绑
    0.0.0.0 后从非 loopback 对端裸 socket 发 `Host: 127.0.0.1:<port>`，SDK 放行、
    `initialize` 回 200 与真实 result（换成不带端口的 `Host: localhost` 才 421——
    allowed_hosts 模式要求端口后缀，绕过门槛低到「补上端口」）。同理
    `X-Forwarded-For`/`CF-Connecting-IP` 也不可信。
    对端缺失或解析不了一律拒绝（fail-closed）——宁可桥接打不通被立刻发现，
    也不能静默放行控制面。回 404 而非 403，与 nginx 公网口径一致：不回显端点存在。
    """

    def __init__(self, app: Callable) -> None:
        self._app = app

    @staticmethod
    def _is_loopback(host: str) -> bool:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            # uvicorn 只会给出数值地址；非法值按未知处理。
            return False
        # 双栈 socket 上 IPv4 对端会以 ::ffff:127.0.0.1 形式出现，
        # ip_address().is_loopback 对它已返回 True，无需另行拆解。
        return ip.is_loopback

    async def __call__(self, scope, receive, send) -> None:
        scope_type = scope.get("type")
        # websocket 也必须拦（SRV-P3c）：当前 Starlette 栈对 /mcp 没挂 ws 端点，
        # 但守卫的意义就是「下层怎么变都兜得住」——只认 http 等于把 ws 面的
        # 这层保证悄悄挖空。lifespan 等非连接 scope 照常透传。
        if scope_type not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return
        path = scope.get("path", "")
        # root_path 归一（SC-02）：uvicorn --root-path /x 下 scope["path"] 是完整
        # 路径（带 /x 前缀）、scope["root_path"]="/x"，Starlette 剥掉 root_path
        # 后才路由到 /mcp 子应用。守卫若按裸 path 前缀匹配，`/x/mcp` 会透传守卫
        # 再被 Starlette 剥前缀命中控制面。当前生产 unit 钉死 --host 127.0.0.1
        # 且无 --root-path，今天不可达；这是守卫承诺的纵深缺口，按 root_path 剥
        # 前缀再比对。剥法按路径段："/x" 只剥自身与 "/x/..."，不误伤 "/xmcpx"。
        root_path = scope.get("root_path") or ""
        if root_path:
            root = root_path.rstrip("/")
            if path == root:
                path = "/"
            elif path.startswith(root + "/"):
                path = path[len(root):]
        # 按路径段匹配：`/mcpx` 不是 MCP 端点，不能连带拦掉。
        if path != _MCP_PATH and not path.startswith(f"{_MCP_PATH}/"):
            await self._app(scope, receive, send)
            return
        client = scope.get("client")
        host = client[0] if client else ""
        if not host or not self._is_loopback(host):
            # MCPDetail-F6：带对端数值 IP，区分「外部攻击源」与「代理头配置错误」。
            logger.warning("拒绝非 loopback 对端访问 MCP 控制面（对端 %s）", host or "无 client 信息")
            if scope_type == "websocket":
                # 握手前 close：ASGI 服务器会以 403 拒绝升级，不回显端点信息。
                await send({"type": "websocket.close", "code": 1008})
                return
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [
                        (b"content-length", b"9"),
                        (b"content-type", b"text/plain; charset=utf-8"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b"not found"})
            return
        await self._app(scope, receive, send)


async def _warm_generation_deps() -> None:
    """后台预热位图描摹的重依赖；失败只记日志，绝不拖垮启动。

    依赖是否可用由 unit 的 ExecStartPre（沙箱内 fail-closed）与 preflight 判红——
    那两处才是判据。这里唯一的职责是把 ~3s 冷启 import 从「第一次真实出图」的
    用户等待里挪到启动阶段，所以必须 fail-open。
    """
    try:
        await asyncio.to_thread(warm_trace_deps)
    except BitmapError as exc:
        logger.warning("位图描摹依赖预热失败（服务照常启动）：%s", exc)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # uvicorn 在 import 应用后才配置自身 handler；进入 lifespan 时安装才能覆盖
    # root、uvicorn、httpx/httpcore 的真实运行态 handler，避免预签名 URL 进 journald。
    _install_secret_redaction()
    # 挂载的子应用 lifespan 不会自动运行，必须显式拉起 MCP session manager
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    warm = asyncio.create_task(_warm_generation_deps())
    from .draw_queue import queue_enabled
    from . import queue_metrics

    metrics_stop = asyncio.Event()
    metrics_task: asyncio.Task | None = None
    if queue_enabled():
        metrics_task = asyncio.create_task(
            queue_metrics.metrics_loop(metrics_stop, role="mcp")
        )
    try:
        async with mcp.session_manager.run():
            yield
    finally:
        metrics_stop.set()
        if metrics_task is not None:
            metrics_task.cancel()
            try:
                await metrics_task
            except asyncio.CancelledError:
                pass
        # `asyncio.to_thread` 起的线程本就取消不掉（cancel 只是不再等它、让异常
        # 无人接管），await 才是诚实收尾。预热 ~3s，只有「刚启动就停机」会等到它。
        await warm


# 三个 URL 必须显式关成 None：FastAPI 默认自动挂 `/docs`、`/redoc`、
# `/openapi.json`（外加 `/docs/oauth2-redirect`），实测四条全回 200。公网当前访问
# 不到，但那份安全**完全来自 nginx 兜底**（443 的 `location / { return 404; }`），
# 应用层零防御：`location /` 一旦为加健康检查之类放开、或有人给 :8300 开直连调试，
# 攻击面即静默打开。对比 `/mcp` 是「nginx 显式 404 + 安全组不放行 8300 + preflight
# 专项探针」三层，这四条原本只有第一层，且 preflight 与 pytest 都没有任何判据会红。
# 与「公网只经 nginx 暴露 /files/」的既定口径对齐，在应用层也拒掉。
# 注：`/mcp` 走 app.mount，本就不进 OpenAPI schema，关掉不影响 MCP 自身。
app = FastAPI(
    title="hutuji MCP 生成服务",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(LoopbackOnlyMcpGuard)

@app.get("/bridge-health")
async def _bridge_health(request: Request) -> Response:
    """仅供本机运维探针读取 bridge 的云端与 proxy 状态。"""
    client = request.client
    host = client.host if client is not None else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is None or not ip.is_loopback:
        return Response(content=b"not found", status_code=404, media_type="text/plain")
    try:
        status = read_health_status()
    except Exception as exc:  # 探针语义：读快照失败 = 不健康，按 503 塌缩不回 500
        logger.warning("bridge-health 读取快照失败（%s）", type(exc).__name__)
        return Response(
            content=b'{"cloud_connected":false,"proxy_connected":false}',
            status_code=503,
            media_type="application/json",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
    now = time.time()
    updated_at = status.get("updated_at")
    fresh = (
        isinstance(updated_at, (int, float))
        and 0 <= now - updated_at <= BRIDGE_HEALTH_MAX_AGE_SECONDS
    )
    healthy = bool(
        fresh and status.get("cloud_connected") and status.get("proxy_connected")
    )
    return Response(
        content=json.dumps(status, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        status_code=200 if healthy else 503,
        media_type="application/json",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )

# ---- /internal/draw：draw-portal 网页生图的本机内部入口（2026-08-31）----
# 防线与 /mcp 同口径：loopback 对端（nginx 不公网反代本路径）+ 共享密钥头。
# 密钥复用 HUTUJI_USAGE_INGEST_SECRET（本机 portal 已持有同值），不新增第三套凭据。
# 未配置密钥时 fail-closed（404），避免「忘了配密钥=门户大开」。
_INTERNAL_DRAW_MAX_BODY_BYTES = 4 * 1024


def _internal_draw_authorized(request: Request) -> bool:
    client = request.client
    host = client.host if client is not None else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if not ip.is_loopback:
        return False
    secret = (getattr(settings, "usage_ingest_secret", "") or "").strip()
    if not secret:
        return False
    provided = request.headers.get("x-hutuji-usage-secret", "")
    return secrets.compare_digest(provided, secret)


@app.post("/internal/draw")
async def _internal_draw(request: Request) -> Response:
    """本机 portal 的同步生图入口；鉴权失败一律 404（不回显端点存在）。"""
    if not _internal_draw_authorized(request):
        return Response(content=b"not found", status_code=404, media_type="text/plain")
    body = await request.body()
    if len(body) > _INTERNAL_DRAW_MAX_BODY_BYTES:
        raise HTTPException(status_code=400, detail="请求体过大")
    try:
        payload = json.loads(body.decode("utf-8")) if body else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON") from None
    description = payload.get("description") if isinstance(payload, dict) else None
    if not isinstance(description, str) or not description.strip():
        raise HTTPException(status_code=400, detail="description 必填")
    try:
        result = await _draw_internal_sync(description)
    except LlmError as exc:
        # 输入类白话（空描述/超长/控制字符），回 400 让页面向用户展示
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except DrawLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None
    except DrawTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from None
    except (BitmapError, PaperRiskExhaustedError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    return Response(
        content=result,
        status_code=200,
        media_type="application/json",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )

# 静态文件源：自定义路由（非裸 StaticFiles），以带 protocol §5.1 完整性头
# Content-Length + X-Hutuji-CRC32。S3 侧下完先比长度再比 CRC32 才开画（见 mcp-server README 组 0.2）。
# HEAD 必须显式注册（SRV-P3b）：FastAPI @app.get 不自动带 HEAD，而 nginx 反代
# 白名单与文档口径都是 `GET/HEAD /files/`——S3 侧用 HEAD 预检长度/CRC32 时应用
# 层不能回 405/404。uvicorn 对 HEAD 响应按 HTTP 规范只发头不发体，处理函数无需
# 区分方法。
@app.api_route("/files/{name}", methods=["GET", "HEAD"])
async def _serve_file(name: str):
    if not _DRAW_FILE_RE.fullmatch(name):
        raise HTTPException(status_code=404, detail="not found")
    from .output_store import OutputNotFoundError, StaleOutputStoreError, get_output_store

    store = get_output_store()
    try:
        data, mtime = await asyncio.to_thread(store.read_verified, name)
    except OutputNotFoundError:
        raise HTTPException(status_code=404, detail="not found") from None
    except StaleOutputStoreError as exc:
        logger.warning(
            "拒绝下发不再满足 §5 契约的产物 %s：%s",
            _redact_capability_name(name),
            exc,
        )
        _delete_stale_output_pair(Path(name).stem, exc.mtime)
        raise HTTPException(status_code=404, detail="not found") from None
    if time.time() - mtime >= settings.output_ttl_seconds:
        _delete_output_pair(Path(name).stem)
        raise HTTPException(status_code=404, detail="not found")
    crc = format(zlib.crc32(data) & 0xFFFFFFFF, "08x")  # zlib/IEEE 变体，须与 S3 侧一致
    media = (
        "image/svg+xml" if name.endswith(".svg")
        else "image/png" if name.endswith(".png")
        else "application/octet-stream"
    )
    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Length": str(len(data)),
            "X-Hutuji-CRC32": crc,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment",
        },
    )

# 歌曲下载：与 _serve_file 同口径（名字白名单 + fstat 拒链接 + 一切失败塌 404），
# 差异仅三条——无 TTL（曲库是静态内容，过期语义只针对 draw 产物）、
# 可缓存（同一首歌人人相同）、不带 Content-Disposition（设备直接流式消费）。
@app.api_route("/songs/{name}", methods=["GET", "HEAD"])
async def _serve_song(name: str):
    if not _SONG_FILE_RE.fullmatch(name):
        raise HTTPException(status_code=404, detail="not found")
    path = _songs_dir() / name
    try:
        data, _mtime = await asyncio.to_thread(_read_regular_file, path)
    except OSError:
        # 拒绝原因（symlink/竞态/不存在）回显会泄漏目录内文件属性，统一 404。
        raise HTTPException(status_code=404, detail="not found") from None
    crc = format(zlib.crc32(data) & 0xFFFFFFFF, "08x")
    return Response(
        content=data,
        media_type="audio/ogg",
        headers={
            "Content-Length": str(len(data)),
            "X-Hutuji-CRC32": crc,
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )

# 激活码中转（2026-08-20 用户决策，京东云中转方案）：新设备把小智 OTA 下发的
# 激活码 POST 上来，服务器持控制台 token 代绑到呼图机 agent，用户联网后无感
# 完成绑定；本端失败/禁用不影响设备走「屏显激活码人工输入」原兜底。公网入口
# 由 nginx 单独放行 POST /register（限速），/mcp 维持 404；本路由只接受
# application/json 的 {mac, code}，体超限由 uvicorn/nginx 双层挡。
@app.post("/register", status_code=200)
async def _activate_relay(request: dict):
    try:
        status = await bind_device(settings, request.get("mac", ""), request.get("code", ""))
    except RelayDisabledError:
        raise HTTPException(status_code=503, detail="relay disabled") from None
    except ValueError:
        raise HTTPException(status_code=400, detail="bad request") from None
    except InvalidCodeError:
        raise HTTPException(status_code=400, detail="invalid code") from None
    except RelayUpstreamError:
        raise HTTPException(status_code=502, detail="upstream error") from None
    return {"status": status}

app.mount("/", _mcp_app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
