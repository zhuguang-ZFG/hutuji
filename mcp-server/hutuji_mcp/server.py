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
import zlib
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from mcp.server.fastmcp import FastMCP

from .config import settings
from .config import validate_public_base_url
from .gcode_gen import svg_to_gcode_async
from .bitmap_svg import generate_bitmap_svg
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

logger = logging.getLogger(__name__)


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
        # 刻意不带锁：enter/leave 的临界区内没有任何 await 点，单事件循环里
        # 同步代码天然原子。曾用 asyncio.Lock，但 slot() 的 finally 里
        # `await self.leave()` 要抢锁——任务被二次取消时锁等待会被再打断，
        # `_active` 永不递减，并发名额永久漏掉一个直到进程重启。无 await 的
        # async 方法被 await 时同步执行到底，取消只在挂起点注入，泄漏面消除。

    async def enter(self) -> None:
        """占用一个生成名额；超限立即报错，不排队等待。"""
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
    """删除同一产物的 SVG/G-code；文件本就可能只生成了一半。

    删除失败（权限/占用）只记录 warning：过期 URL 无论如何统一 404，
    删除是尽力而为的清理，不能反过来把下载路径变成 500。
    """
    for suffix in (".svg", ".gcode"):
        try:
            (settings.output_dir / f"{stem}{suffix}").unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "删除过期产物失败 %s：%s",
                _redact_capability_name(f"{stem}{suffix}"),
                exc,
            )


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
    """清除超过 TTL 的 draw_*.svg/gcode，返回删除的文件数。

    清理是尽力而为的运维动作：目录不可读、mtime 拿不到、删除被拒
    （权限/占用/竞态）都只记录 warning 并跳过该项，绝不让 status/draw
    因清理失败变成 500。
    """
    if not settings.output_dir.is_dir():
        return 0
    now = time.time() if now is None else now
    expired_stems: set[str] = set()
    try:
        entries = list(settings.output_dir.iterdir())
    except OSError as exc:
        logger.warning("清理输出目录失败（iterdir）：%s", exc)
        return 0
    for path in entries:
        if path.is_file() and _DRAW_FILE_RE.fullmatch(path.name):
            try:
                if _is_expired(path, now=now):
                    expired_stems.add(path.stem)
            except OSError as exc:
                # mtime 拿不到（权限/竞态）→ 跳过该项，不穿透
                logger.warning("跳过清理 %s：%s", _redact_capability_name(path.name), exc)
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
            except OSError as exc:
                logger.warning(
                    "删除过期产物失败 %s：%s",
                    _redact_capability_name(path.name),
                    exc,
                )
    return deleted


async def _generate_svg_limited(description: str) -> str:
    """所有会调用付费 LLM 的工具共用同一个进程内限流器。"""
    async with _draw_limiter.slot():
        return await generate_svg(description)


@mcp.tool()
async def hutuji_draw(description: str) -> str:
    """当用户要求「画/绘/写一幅…」时调用：把自然语言变成写字机可执行的单线笔画。

    生成耗时约 15～45 秒，调用前请先告知用户需要等待。
    参数 description：简短中文主题，建议 10～40 字；最长 200 字，超出会被截断。
    好例子：「一只坐着的小猫」「带烟囱的小房子」「写个福字」。
    坏例子：长篇故事、要求填色/照片写实、无主题的空话。

    返回 JSON：url（设备 hutuji.draw 用的 G-code 下载地址，有有效期，以返回的
    ttl_seconds 秒数为准，拿到后请立即开始绘图，过期需重新生成）、lines/bytes/pen_lifts/ttl_seconds。
    不要编造 url；失败时根据错误白话说明。"""
    # 即使测试或运行时注入绕过 Settings，也不能返回明文公网能力 URL。
    public_base_url = validate_public_base_url(settings.public_base_url)
    svg_path: Path | None = None
    gcode_path: Path | None = None
    try:
        # R13-SRV-02：清理是 IO 运维动作、不是受控的付费生成工作，不占并发名额
        # （hutuji_status 同样在 slot 外跑同一清理，并发重复执行幂等无害）。
        # 名额只覆盖付费 LLM 与 CPU 密集的 vpype，不能在 SVG 返回后放任转换堆积。
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(_cleanup_expired_outputs)
        async with _draw_limiter.slot():
            # 位图路线（2026-08-09 起为现行路线）：agnes 文生图 → 中心线描摹 →
            # 单线 SVG。LLM 直写 SVG 只留给 hutuji_preview_svg 调试用。
            # 两条路线的实测对比见 docs/worktree-inventory.md 当日探针行。
            svg = await generate_bitmap_svg(description)

            base = _new_basename()
            svg_path = settings.output_dir / f"{base}.svg"
            gcode_path = settings.output_dir / f"{base}.gcode"
            # SVG 已被限制到 256KiB；同步落盘很短，并避免取消 to_thread 后后台迟写残留文件。
            svg_path.write_text(svg, encoding="utf-8")
            stats = await svg_to_gcode_async(svg_path, gcode_path)
    except BaseException:
        # 取消也必须清理 capability 产物；清理失败只能记日志，不能覆盖原异常。
        for path in (svg_path, gcode_path):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning(
                        "清理失败产物失败 %s：%s",
                        _redact_capability_name(path.name),
                        exc,
                    )
        raise

    assert gcode_path is not None
    url = f"{public_base_url}/files/{gcode_path.name}"
    return json.dumps({
        "url": url,
        "lines": stats.lines,
        "bytes": stats.bytes,
        "pen_lifts": stats.pen_lifts,
        # 让调用方（云端 LLM）能把「约 10 分钟有效」转述给用户，而非等到 404
        # 才由设备侧猜测原因（CloudUX-F1）。
        "ttl_seconds": settings.output_ttl_seconds,
    }, ensure_ascii=False)


@mcp.tool()
async def hutuji_status() -> str:
    """查询云端「绘图生成服务」是否就绪：LLM 是否配置、产出目录、最近 G-code 数量。

    在用户问「能不能画了」「服务正常吗」「刚才生成成功没」时使用。
    这不是写字机笔尖实时位置；设备连线请用设备侧 hutuji.status。"""
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
            [p for p in out.glob("*.gcode") if p.is_file() and _DRAW_FILE_RE.fullmatch(p.name)],
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
    }, ensure_ascii=False)


@mcp.tool()
async def hutuji_preview_svg(description: str) -> str:
    """只预览矢量草稿、不生成 G-code、不驱动写字机。调试构图或用户说「先看看别出纸」时用。

    走的是 LLM 直写 SVG 旧路线（调试用）；hutuji_draw 出图走位图路线，两者观感不同。
    参数 description 同 hutuji_draw。返回 SVG 文本截断（前 2000 字符）。"""
    svg = await _generate_svg_limited(description)
    return svg[:2000]


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


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # 挂载的子应用 lifespan 不会自动运行，必须显式拉起 MCP session manager
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    async with mcp.session_manager.run():
        yield


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

# 静态文件源：自定义路由（非裸 StaticFiles），以带 protocol §5.1 完整性头
# Content-Length + X-Hutuji-CRC32。S3 侧下完先比长度再比 CRC32 才开画（见 mcp-server README 组 0.2）。
# HEAD 必须显式注册（SRV-P3b）：FastAPI @app.get 不自动带 HEAD，而 nginx 反代
# 白名单与文档口径都是 `GET/HEAD /files/`——S3 侧用 HEAD 预检长度/CRC32 时应用
# 层不能回 405/404。uvicorn 对 HEAD 响应按 HTTP 规范只发头不发体，处理函数无需
# 区分方法。
@app.api_route("/files/{name}", methods=["GET", "HEAD"])
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
        # 删除前复核：_read_regular_file 锁定的是打开时的旧对象，期间同 stem
        # 路径可能被换成新文件（mtime 变了）。路径已换则跳过删除（删了会误删
        # 新对象）；mtime 拿不到（已被并发删除）也跳过。删除本身失败由
        # _delete_output_pair 收窄为 warning。无论哪种情况，统一 404：
        # detail 与「不存在」不可区分，过期与否是时序信息，回显会泄漏
        # 「该 token 曾有效」的可探测面（128-bit token 下价值为零，但
        # 统一口径成本更低，也避免攻击者拿响应差异做枚举侧信道）。
        try:
            still_expired = _is_expired(path)
        except OSError:
            still_expired = False
        if still_expired:
            _delete_output_pair(path.stem)
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
