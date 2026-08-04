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
import json
import os
import re
import secrets
import stat
import time
import zlib
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from mcp.server.fastmcp import FastMCP

from .config import settings
from .config import validate_public_base_url
from .gcode_gen import svg_to_gcode
from .llm_svg import generate_svg

# 保持默认 streamable_http_path="/mcp"，子应用挂到根（/files 先注册优先匹配），
# 避免 Mount("/mcp") 对裸 /mcp 产生 307 跳转（POST 桥接器不一定跟随重定向）
mcp = FastMCP("hutuji")

_DRAW_FILE_RE = re.compile(
    r"^draw_\d{8}_\d{6}_[A-Za-z0-9_-]{22}\.(?:gcode|svg)$"
)

# 同一个式子按结构分组，供脱敏使用：token 用的是 URL-safe base64 字母表，
# **本身可能含 `_`**，所以绝不能靠「最后一个下划线」切分（会把 token 前缀
# 当成时间戳留在输出里；实测约 29% 的 token 会这样泄露）。
_DRAW_FILE_PARTS_RE = re.compile(
    r"^(draw_\d{8}_\d{6})_(?P<token>[A-Za-z0-9_-]{22})\.(?P<ext>gcode|svg)$"
)


class DrawLimitError(RuntimeError):
    """LLM 生成入口超过本进程安全限额。"""


class DrawLimiter:
    """单进程全局限流：1 次/秒、30 次/分钟、最多 2 个并发。"""

    def __init__(
        self,
        *,
        per_second: int = 1,
        per_minute: int = 30,
        max_concurrent: int = 2,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.per_second = per_second
        self.per_minute = per_minute
        self.max_concurrent = max_concurrent
        self._clock = clock
        self._starts: deque[float] = deque()
        self._active = 0
        self._lock = asyncio.Lock()

    async def enter(self) -> None:
        """占用一个生成名额；超限立即报错，不排队等待。"""
        async with self._lock:
            now = self._clock()
            while self._starts and now - self._starts[0] >= 60.0:
                self._starts.popleft()

            if self._active >= self.max_concurrent:
                raise DrawLimitError(
                    f"生成任务并发已达上限 {self.max_concurrent}，请稍后重试"
                )
            recent_second = sum(now - started < 1.0 for started in self._starts)
            if recent_second >= self.per_second:
                raise DrawLimitError(
                    f"生成请求超过每秒 {self.per_second} 次限制，请稍后重试"
                )
            if len(self._starts) >= self.per_minute:
                raise DrawLimitError(
                    f"生成请求超过每分钟 {self.per_minute} 次限制，请稍后重试"
                )

            self._starts.append(now)
            self._active += 1

    async def leave(self) -> None:
        """释放并发名额；速率时间戳保留到滑动窗口自然过期。"""
        async with self._lock:
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
    """删除同一产物的 SVG/G-code；文件本就可能只生成了一半。"""
    for suffix in (".svg", ".gcode"):
        (settings.output_dir / f"{stem}{suffix}").unlink(missing_ok=True)


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


def _cleanup_expired_outputs(*, now: float | None = None) -> int:
    """清除超过 TTL 的 draw_*.svg/gcode，返回删除的文件数。"""
    if not settings.output_dir.is_dir():
        return 0
    now = time.time() if now is None else now
    expired_stems: set[str] = set()
    for path in settings.output_dir.iterdir():
        if path.is_file() and _DRAW_FILE_RE.fullmatch(path.name):
            try:
                if _is_expired(path, now=now):
                    expired_stems.add(path.stem)
            except FileNotFoundError:
                continue

    deleted = 0
    for stem in expired_stems:
        for suffix in (".svg", ".gcode"):
            path = settings.output_dir / f"{stem}{suffix}"
            try:
                path.unlink()
                deleted += 1
            except FileNotFoundError:
                pass
    return deleted


async def _generate_svg_limited(description: str) -> str:
    """所有会调用付费 LLM 的工具共用同一个进程内限流器。"""
    async with _draw_limiter.slot():
        return await generate_svg(description)


@mcp.tool()
async def hutuji_draw(description: str) -> str:
    """当用户要求「画/绘/写一幅…」时调用：把自然语言变成写字机可执行的单线笔画。

    参数 description：简短中文主题，建议 10～40 字，带「单线/简笔」更稳。
    好例子：「单线简笔侧脸小猫」「站在树枝上的小鸟轮廓」。
    坏例子：长篇故事、要求填色/照片写实、无主题的空话。

    返回 JSON：url（设备 hutuji.draw 用的 G-code 下载地址）、lines/bytes/pen_lifts。
    不要编造 url；失败时根据错误白话说明。"""
    # 即使测试或运行时注入绕过 Settings，也不能返回明文公网能力 URL。
    public_base_url = validate_public_base_url(settings.public_base_url)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_cleanup_expired_outputs)
    svg = await _generate_svg_limited(description)

    base = _new_basename()
    svg_path = settings.output_dir / f"{base}.svg"
    gcode_path = settings.output_dir / f"{base}.gcode"
    svg_path.write_text(svg, encoding="utf-8")

    # vpype 转换是阻塞子进程，扔到线程池避免卡住事件循环
    try:
        stats = await asyncio.to_thread(svg_to_gcode, svg_path, gcode_path)
    except Exception:
        svg_path.unlink(missing_ok=True)
        gcode_path.unlink(missing_ok=True)
        raise

    url = f"{public_base_url}/files/{gcode_path.name}"
    return json.dumps({
        "url": url,
        "lines": stats.lines,
        "bytes": stats.bytes,
        "pen_lifts": stats.pen_lifts,
    }, ensure_ascii=False)


@mcp.tool()
async def hutuji_status() -> str:
    """查询云端「绘图生成服务」是否就绪：LLM 是否配置、产出目录、最近 G-code 数量。

    在用户问「能不能画了」「服务正常吗」「刚才生成成功没」时使用。
    这不是写字机笔尖实时位置；设备连线请用设备侧 hutuji.status。"""
    await asyncio.to_thread(_cleanup_expired_outputs)
    out = settings.output_dir
    def _safe_mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except FileNotFoundError:
            return 0.0
    files = sorted(
        [p for p in out.glob("*.gcode") if p.is_file()],
        key=_safe_mtime,
    ) if out.is_dir() else []
    return json.dumps({
        "output_dir_exists": out.is_dir(),
        "public_base_url": settings.public_base_url,
        "llm_configured": settings.llm_configured,
        "llm_model": settings.llm_model,
        "gcode_files": len(files),
        "latest": _redact_capability_name(files[-1].name) if files else None,
    }, ensure_ascii=False)


@mcp.tool()
async def hutuji_preview_svg(description: str) -> str:
    """只预览矢量草稿、不生成 G-code、不驱动写字机。调试构图或用户说「先看看别出纸」时用。

    参数 description 同 hutuji_draw。返回 SVG 文本截断（前 2000 字符）。"""
    svg = await _generate_svg_limited(description)
    return svg[:2000]


# ----- FastAPI 应用 -----

_mcp_app = mcp.streamable_http_app()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # 挂载的子应用 lifespan 不会自动运行，必须显式拉起 MCP session manager
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="hutuji MCP 生成服务", lifespan=lifespan)

# 静态文件源：自定义路由（非裸 StaticFiles），以带 protocol §5.1 完整性头
# Content-Length + X-Hutuji-CRC32。S3 侧下完先比长度再比 CRC32 才开画（见 mcp-server README 组 0.2）。
@app.get("/files/{name}")
async def _serve_file(name: str):
    # 只接受带 128-bit 随机 token 的 capability 文件名；旧时间戳名不再公开。
    if not _DRAW_FILE_RE.fullmatch(name):
        raise HTTPException(status_code=404, detail="not found")
    path = settings.output_dir / name
    try:
        # 打开句柄后做 fstat 校验：拒 symlink / 硬链接 / 非普通文件，竞态替换
        # 不会让请求读到新路径的目标；TTL 用已打开对象的 mtime 判断。
        data, mtime = await asyncio.to_thread(_read_regular_file, path)
    except OSError:
        # detail 与「不存在」不可区分：拒绝原因（symlink/硬链接/竞态）回显会
        # 泄漏目录内文件属性，统一 404。
        raise HTTPException(status_code=404, detail="not found") from None
    if time.time() - mtime >= settings.output_ttl_seconds:
        _delete_output_pair(path.stem)
        # detail 与「不存在」不可区分：过期与否是时序信息，回显会泄漏
        # 「该 token 曾有效」的可探测面（128-bit token 下价值为零，但
        # 统一口径成本更低，也避免攻击者拿响应差异做枚举侧信道）。
        raise HTTPException(status_code=404, detail="not found")
    crc = format(zlib.crc32(data) & 0xFFFFFFFF, "08x")  # zlib/IEEE 变体，须与 S3 侧一致
    media = "image/svg+xml" if name.endswith(".svg") else "application/octet-stream"
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

app.mount("/", _mcp_app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
