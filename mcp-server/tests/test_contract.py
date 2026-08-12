"""protocol v0.5 与公网安全基础契约的无硬件回归。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import os
import time
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from hutuji_mcp.config import (
    Settings,
    load_settings,
    validate_llm_base_url,
    validate_public_base_url,
)
from hutuji_mcp.gcode_gen import (
    MAX_BYTES,
    MAX_LINE_PAYLOAD_BYTES,
    PEN_LIFT_WARN_THRESHOLD,
    GcodeError,
    _read_generated_gcode,
    validate_gcode,
)
from hutuji_mcp import gcode_gen
from hutuji_mcp import server


def _settings(output_dir: Path, *, ttl: int = 600) -> Settings:
    return Settings(
        llm_base_url="https://example.invalid/v1",
        llm_api_key="",
        llm_model="test",
        output_dir=output_dir,
        public_base_url="https://draw.example",
        host="127.0.0.1",
        port=8300,
        output_ttl_seconds=ttl,
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://public.example/files",
        "https://public.example:8300",
        "https://public.example:8443",
    ],
)
def test_public_capability_url_rejects_path_or_non_https_port(value: str) -> None:
    with pytest.raises(ValueError):
        validate_public_base_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "https://public.example?",
        "https://public.example#",
        "https://public.example?x=1",
        "https://public.example#frag",
    ],
)
def test_public_capability_url_rejects_raw_delimiter(value: str) -> None:
    with pytest.raises(ValueError):
        validate_public_base_url(value)


def test_loopback_http_public_url_remains_valid() -> None:
    assert validate_public_base_url("http://127.0.0.1:8300") == "http://127.0.0.1:8300"
    assert validate_public_base_url("https://hutuji.donglicao.com") == "https://hutuji.donglicao.com"


@pytest.mark.parametrize(
    "value",
    [
        "http://api.example.com/v1",      # 公网明文 http：API key 裸奔
        "ftp://127.0.0.1:11434/v1",       # loopback 也限 http/https
        "https://api.example.com:99999/v1",  # 坏端口
        "https://api.example.com/v1?x=1", # query
        "https://api.example.com/v1#f",   # fragment
        "https://user:pass@api.example.com/v1",  # 凭据
        "",                               # 空
    ],
)
def test_llm_base_url_rejects_bad_values(value: str) -> None:
    with pytest.raises(ValueError):
        validate_llm_base_url(value)


def test_llm_base_url_accepts_https_and_loopback_http() -> None:
    assert validate_llm_base_url("https://api.freemodel.dev/v1") == "https://api.freemodel.dev/v1"
    assert validate_llm_base_url("https://api.example.com/v1/") == "https://api.example.com/v1"
    assert validate_llm_base_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"


def test_settings_normalizes_public_origin(tmp_path: Path) -> None:
    configured = Settings(
        llm_base_url="https://example.invalid/v1",
        llm_api_key="",
        llm_model="test",
        output_dir=tmp_path,
        public_base_url="  https://draw.example:443/  ",
        host="127.0.0.1",
        port=8300,
        output_ttl_seconds=600,
    )

    assert configured.public_base_url == "https://draw.example:443"


def test_settings_rejects_insecure_public_origin(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Settings(
            llm_base_url="https://example.invalid/v1", llm_api_key="", llm_model="test",
            output_dir=tmp_path, public_base_url="http://public.example", host="127.0.0.1",
            port=8300, output_ttl_seconds=600,
        )

def test_load_settings_rejects_insecure_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUTUJI_PUBLIC_BASE_URL", "http://public.example")
    with pytest.raises(ValueError):
        load_settings()


@pytest.mark.parametrize("port", [0, 65536])
def test_settings_rejects_invalid_port(tmp_path: Path, port: int) -> None:
    with pytest.raises(ValueError, match="port.*1.*65535"):
        Settings(
            llm_base_url="https://example.invalid/v1", llm_api_key="", llm_model="test",
            output_dir=tmp_path, public_base_url="https://draw.example", host="127.0.0.1",
            port=port, output_ttl_seconds=600,
        )


# 锁定决策：MCP 服务绝不直曝，`/mcp` 只本机可达、公网由 nginx 404。生产 unit 用
# `--host 127.0.0.1` 钉死，`-m uvicorn` 根本不读 HUTUJI_HOST——于是把它设成
# 0.0.0.0 在生产是**静默无效**，在开发直跑 `python -m hutuji_mcp.server` 时却是
# 真裸曝。静默无效的配置比报错更危险：它让人以为改了监听面。此处让非 loopback
# 取值直接拒绝，两条路径口径一致。
@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.168.1.13", "117.72.118.95", "example.com"],
)
def test_settings_rejects_non_loopback_host(tmp_path: Path, host: str) -> None:
    with pytest.raises(ValueError, match="HUTUJI_HOST"):
        Settings(
            llm_base_url="https://example.invalid/v1", llm_api_key="", llm_model="test",
            output_dir=tmp_path, public_base_url="https://draw.example", host=host,
            port=8300, output_ttl_seconds=600,
        )


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_settings_accepts_loopback_host(tmp_path: Path, host: str) -> None:
    configured = Settings(
        llm_base_url="https://example.invalid/v1", llm_api_key="", llm_model="test",
        output_dir=tmp_path, public_base_url="https://draw.example", host=host,
        port=8300, output_ttl_seconds=600,
    )
    assert configured.host == host


def test_load_settings_rejects_non_loopback_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUTUJI_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="HUTUJI_HOST"):
        load_settings()


# 上一条只堵住 HUTUJI_HOST 这一条路。`python -m uvicorn hutuji_mcp.server:app
# --host 0.0.0.0` 完全绕开 settings.host：监听面由 CLI 参数决定，配置校验根本没
# 被问过（HUTUJI_HOST 可以是合法的 127.0.0.1 而进程照样绑 0.0.0.0）。`/mcp` 是
# **无鉴权控制面**，能驱动写字机出图，生产却只有两层网络防线：nginx 显式 404 +
# 安全组不放行 8300。任一层被绕（直跑 uvicorn、安全组改宽、换反代）控制面即裸曝。
# 所以应用层必须自己再兜一刀，且判据只能是「对端地址」——它不可伪造。
# SDK 自带的 DNS-rebinding 防护挡不住：它校验 `Host` 请求头，而请求头由攻击方
# 控制。2026-08-09 实测（绑 0.0.0.0，从非 loopback 对端裸 socket）：`Host:
# 127.0.0.1:<port>` 被 SDK 放行，`initialize` 回 200 与真实 result。
def _asgi_scope(path: str, client: tuple[str, int] | None) -> dict[str, object]:
    return {"type": "http", "path": path, "method": "POST", "headers": [], "client": client}


def _run_guard(path: str, client: tuple[str, int] | None) -> tuple[bool, list[dict[str, object]]]:
    """用裸 ASGI scope 驱动守卫，返回（是否进到内层 app，发出的消息）。

    不用 TestClient：本仓测试一律直调被测对象 + monkeypatch 模块全局。
    """
    reached: list[bool] = []

    async def _inner(scope: dict[str, object], receive: object, send: object) -> None:
        reached.append(True)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    sent: list[dict[str, object]] = []

    async def _send(message: dict[str, object]) -> None:
        sent.append(message)

    async def _receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    guard = server.LoopbackOnlyMcpGuard(_inner)
    asyncio.run(guard(_asgi_scope(path, client), _receive, _send))
    return bool(reached), sent


@pytest.mark.parametrize(
    "client",
    [
        ("117.72.118.95", 40000),   # 生产公网地址
        ("192.168.1.13", 40000),    # 同网段写字机
        ("0.0.0.0", 40000),
        ("::ffff:9.9.9.9", 40000),  # IPv4-mapped 公网，不能当 loopback 放行
    ],
)
def test_mcp_rejects_non_loopback_peer(client: tuple[str, int]) -> None:
    reached, sent = _run_guard("/mcp", client)
    assert reached is False, "非 loopback 对端不得进到 MCP 应用"
    start = sent[0]
    assert start["status"] == 404, "口径与 nginx 公网一致：不回显端点是否存在"


@pytest.mark.parametrize(
    "client",
    [
        ("127.0.0.1", 50000),
        ("127.0.0.2", 50000),        # 整个 127/8 都是本机
        ("::1", 50000),
        ("::ffff:127.0.0.1", 50000),  # 双栈 socket 上 uvicorn 会这样报
    ],
)
def test_mcp_allows_loopback_peer(client: tuple[str, int]) -> None:
    reached, _ = _run_guard("/mcp", client)
    assert reached is True, f"loopback 对端 {client[0]} 必须放行，否则桥接器打不通"


@pytest.mark.parametrize("client", [None, ("", 0), ("not-an-ip", 1), ("127.0.0.1.5", 1)])
def test_mcp_guard_fails_closed_on_unknown_peer(client: tuple[str, int] | None) -> None:
    """对端取不到或解析不了时按拒绝处理：宁可打不通，不可静默放行控制面。"""
    reached, sent = _run_guard("/mcp", client)
    assert reached is False
    assert sent[0]["status"] == 404


def _run_guard_under_proxy_headers(
    trusted: str, peer: tuple[str, int], xff: bytes | None
) -> bool:
    """把守卫压在 uvicorn 的 ProxyHeadersMiddleware 下驱动，返回是否放行。

    上面那批测试直接调守卫、手搓 scope，**从不经过生产里压在它之上的这层
    中间件**——而那层正是唯一能改写守卫判据的组件，故隔离测试对本类缺陷
    永远绿。这里复刻真实叠放顺序：uvicorn 的 Config.load() 把
    ProxyHeadersMiddleware 包在最外层，改写发生在守卫看到 scope 之前。
    """
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    reached: list[bool] = []

    async def _inner(scope: dict[str, object], receive: object, send: object) -> None:
        reached.append(True)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    headers: list[tuple[bytes, bytes]] = [(b"host", b"127.0.0.1:8300")]
    if xff is not None:
        headers.append((b"x-forwarded-for", xff))
    scope = {
        "type": "http",
        "path": "/mcp",
        "method": "POST",
        "client": peer,
        "headers": headers,
        "scheme": "http",
    }

    async def _send(message: dict[str, object]) -> None:
        return None

    async def _receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    app = ProxyHeadersMiddleware(server.LoopbackOnlyMcpGuard(_inner), trusted_hosts=trusted)
    asyncio.run(app(scope, _receive, _send))
    return bool(reached)


def test_mcp_guard_bypassed_when_forwarded_headers_are_trusted_widely() -> None:
    """钉死本类缺陷的机理：头信任面放宽后，攻击方自带的头就变成守卫的判据。

    这不是「建议」而是实测（2026-08-11）：uvicorn 的 ProxyHeadersMiddleware 用
    X-Forwarded-For 改写 ASGI scope["client"]，而守卫读的正是它。本例断言的是
    **上游行为**，故它绿不代表我们安全——它存在的意义是：一旦哪天 uvicorn 改了
    这个行为，下面那条真正的防线测试就该被重新审视。
    """
    assert _run_guard_under_proxy_headers("*", ("203.0.113.7", 51234), b"127.0.0.1") is True, (
        "上游行为变了：trusted_hosts='*' 时 X-Forwarded-For 不再改写 scope['client']"
    )
    # 默认信任面下同一请求必须挡住：证明危险只来自信任面被放宽，而非头本身。
    assert _run_guard_under_proxy_headers("127.0.0.1", ("203.0.113.7", 51234), b"127.0.0.1") is False


def test_mcp_unit_disables_proxy_headers_so_peer_cannot_be_forged() -> None:
    """生产 ExecStart 必须带 --no-proxy-headers：这是守卫判据不可伪造的前提。

    uvicorn 的 forwarded_allow_ips 默认值读环境变量
    （`os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")`），而 unit 带
    EnvironmentFile=/etc/hutuji/mcp.env——只要那个文件里出现
    FORWARDED_ALLOW_IPS=*，头信任面就静默放宽，命令行压不住。关掉
    proxy_headers 后 Config.load() 的 `if self.proxy_headers:` 干脆不装中间件，
    环境变量再设也无效。unit 已为 --host 防过同一类坑（见其行内注释），本条把
    高一层的同款坑也钉住。
    """
    root = Path(__file__).resolve().parents[2]
    unit = (root / "deploy" / "hutuji-mcp.service").read_text(encoding="utf-8")
    execstarts = [l for l in unit.splitlines() if l.startswith("ExecStart=")]
    assert len(execstarts) == 1, f"ExecStart 必须恰好一条，当前 {len(execstarts)}"
    line = execstarts[0]
    assert "--no-proxy-headers" in line, (
        "ExecStart 缺 --no-proxy-headers：uvicorn 默认信任 X-Forwarded-For 并用它"
        "改写 scope['client']，LoopbackOnlyMcpGuard 的判据即可被伪造"
    )
    # --host 的既有防线一并守住，避免本次编辑把它挤掉。
    assert "--host 127.0.0.1" in line


def test_mcp_guard_docstring_does_not_overclaim_unforgeable_peer() -> None:
    """守卫 docstring 声称判据「来自 socket，不可伪造」——该断言有前提，必须写明。

    没有 --no-proxy-headers 时这句话是假的（见上面的机理测试）。防止后人读了
    docstring 就以为无条件成立，从而放心去放宽 FORWARDED_ALLOW_IPS。
    """
    doc = server.LoopbackOnlyMcpGuard.__doc__ or ""
    assert "no-proxy-headers" in doc, "docstring 必须点明「不可伪造」依赖关闭转发头信任"


@pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/mcp/messages"])
def test_mcp_guard_covers_endpoint_and_subpaths(path: str) -> None:
    reached, _ = _run_guard(path, ("9.9.9.9", 1234))
    assert reached is False, f"{path} 属于 MCP 端点，必须受守卫"


@pytest.mark.parametrize("path", ["/files/draw_20260808_120000_" + "A" * 22 + ".gcode", "/mcpx", "/"])
def test_guard_leaves_public_capability_path_alone(path: str) -> None:
    """`/files` 是设计上的公网下载面（经 nginx 反代），不能被这刀带走；
    前缀也必须按路径段匹配，`/mcpx` 不是 MCP 端点。"""
    reached, _ = _run_guard(path, ("9.9.9.9", 1234))
    assert reached is True


def test_guard_passes_through_non_http_scopes() -> None:
    """lifespan 等非 http scope 必须原样透传，否则服务起不来。"""
    reached: list[bool] = []

    async def _inner(scope: dict[str, object], receive: object, send: object) -> None:
        reached.append(True)

    async def _noop(*_args: object) -> dict[str, object]:
        return {"type": "lifespan.startup"}

    guard = server.LoopbackOnlyMcpGuard(_inner)
    asyncio.run(guard({"type": "lifespan"}, _noop, _noop))
    assert reached == [True]


def test_app_registers_loopback_guard() -> None:
    """光有类没挂上等于死代码——把接线本身钉死。"""
    assert any(
        entry.cls is server.LoopbackOnlyMcpGuard for entry in server.app.user_middleware
    ), "LoopbackOnlyMcpGuard 必须挂在 app 上"


@pytest.mark.parametrize("ttl", [0, -1])
def test_settings_rejects_nonpositive_ttl(tmp_path: Path, ttl: int) -> None:
    with pytest.raises(ValueError, match="output_ttl_seconds.*> 0"):
        Settings(
            llm_base_url="https://example.invalid/v1", llm_api_key="", llm_model="test",
            output_dir=tmp_path, public_base_url="https://draw.example", host="127.0.0.1",
            port=8300, output_ttl_seconds=ttl,
        )


@pytest.mark.parametrize("name", ["HUTUJI_PORT", "HUTUJI_OUTPUT_TTL_SECONDS"])
def test_load_settings_rejects_noninteger_numbers(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "not-an-int")
    with pytest.raises(ValueError, match=name):
        load_settings()



def test_draw_rejects_injected_insecure_settings_before_capability_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            llm_base_url="https://example.invalid/v1", llm_api_key="", llm_model="test",
            output_dir=tmp_path, public_base_url="http://public.example", host="127.0.0.1",
            port=8300, output_ttl_seconds=600,
        ),
    )
    with pytest.raises(ValueError):
        asyncio.run(server.hutuji_draw("test"))


def test_draw_uses_normalized_injected_public_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    from types import SimpleNamespace

    monkeypatch.setattr(
        server,
        "settings",
        SimpleNamespace(
            output_dir=tmp_path,
            public_base_url="  https://draw.example:443/  ",
            output_ttl_seconds=600,
        ),
    )

    async def fake_generate_svg(_description: str) -> str:
        return "<svg/>"

    async def fake_svg_to_gcode(_svg_path: Path, _gcode_path: Path) -> SimpleNamespace:
        return SimpleNamespace(lines=1, bytes=1, pen_lifts=0)

    monkeypatch.setattr(server, "generate_bitmap_svg", fake_generate_svg)
    monkeypatch.setattr(server, "_new_basename", lambda: "draw_20260731_120000_abcdefghijklmnopqrstuv")
    monkeypatch.setattr(server, "svg_to_gcode_async", fake_svg_to_gcode)
    monkeypatch.setattr(
        server,
        "_draw_limiter",
        server.DrawLimiter(per_second=100, per_minute=100, max_concurrent=2),
    )

    result = json.loads(asyncio.run(server.hutuji_draw("test")))
    assert result["url"] == (
        "https://draw.example:443/files/draw_20260731_120000_abcdefghijklmnopqrstuv.gcode"
    )


def _minimal_gcode(extra: str = "") -> str:
    """构造符合 §5 固定头尾的最小生产文件，在中间插入待测代码。"""
    if extra and not extra.endswith("\n"):
        extra += "\n"
    return (
        "G21\nG90\nF8000\n"
        "G1G90 Z0.0F10000\nG1G90 Z0.0F10000\n"
        f"{extra}"
        "G1G90 Z0.0F10000\n"
    )


@pytest.mark.parametrize(
    "text",
    [
        "G90\nG21\nF8000\nG1G90 Z0.0F10000\nG1G90 Z0.0F10000\nG1G90 Z0.0F10000\n",
        "G21\nG90\nF1\nG1G90 Z0.0F10000\nG1G90 Z0.0F10000\nG1G90 Z0.0F10000\n",
        "G21\nG90\nF8000\nG1G90 Z0.0F10000\nG1 X1Y1F8000\nG1G90 Z0.0F10000\n",
        "G21\nG90\nF8000\nG1G90 Z0.0F10000\nG1G90 Z5.0F10000\nG1G90 Z0.0F10000\n",
    ],
)
def test_gcode_rejects_nonproduction_header(text: str) -> None:
    with pytest.raises(GcodeError, match="§5 文件头|文件头不足"):
        validate_gcode(text)


def test_gcode_rejects_pen_down_ending() -> None:
    with pytest.raises(GcodeError, match="文件末尾.*Z0"):
        validate_gcode(
            "G21\nG90\nF8000\n"
            "G1G90 Z0.0F10000\nG1G90 Z0.0F10000\n"
            "G1G90 Z5.0F10000\n"
        )


def test_gcode_accepts_spaced_production_header() -> None:
    validate_gcode(
        "G21\nG90\nF8000\n"
        "G1 G90 Z0.0 F10000\nG1 G90 Z0.0 F10000\n"
        "G1 G90 Z0.0 F10000\n"
    )


@pytest.mark.parametrize(
    "ending",
    [
        "G1 X10Y10 Z0 F8000",
        "G1G90 Z0.0F9999",
        "G1G90 Z0F10000",
        "G1G90 Z0.00F10000",
    ],
)
def test_gcode_requires_exact_production_footer(ending: str) -> None:
    text = (
        "G21\nG90\nF8000\n"
        "G1G90 Z0.0F10000\nG1G90 Z0.0F10000\n"
        f"{ending}\n"
    )
    with pytest.raises(GcodeError, match="文件末尾.*G1G90 Z0.0F10000"):
        validate_gcode(text)


def test_gcode_rejects_more_than_512_kib() -> None:
    text = _minimal_gcode(";" + "x" * MAX_BYTES)
    with pytest.raises(GcodeError, match=r"总大小 .*实际|总大小 .*字节"):
        validate_gcode(text)


def _size_filler(remaining: int) -> str:
    """凑出恰好 `remaining` 字节的注释行填充。

    每行 payload 取满 MAX_LINE_PAYLOAD_BYTES（不含换行），故整行是 payload+1 字节。
    不能一条长行填满——单行超 payload 上限会先撞上行长检查。
    """
    per_line = MAX_LINE_PAYLOAD_BYTES + 1
    full_lines, tail_bytes = divmod(remaining, per_line)
    filler = (";" + "x" * (MAX_LINE_PAYLOAD_BYTES - 1) + "\n") * full_lines
    if tail_bytes:
        assert tail_bytes >= 2
        filler += ";" + "x" * (tail_bytes - 2) + "\n"
    return filler


def test_gcode_accepts_exactly_512_kib() -> None:
    """§5「≤ 512KB 含」：恰好 MAX_BYTES 放行，多一字节才拒。"""
    base = _minimal_gcode()
    text = _minimal_gcode(_size_filler(MAX_BYTES - len(base.encode("utf-8"))))
    assert len(text.encode("utf-8")) == MAX_BYTES
    stats = validate_gcode(text)
    assert stats.bytes == MAX_BYTES


def test_gcode_size_limit_counts_disk_bytes_not_collapsed_newlines(tmp_path) -> None:
    """CRLF 产物必须按磁盘真实字节判上限，不能按 universal newlines 塌陷后的字符数。

    `read_text()` 把 CRLF 读成单个 LF，于是每行少算 1 字节。而 S3 经 HTTP 下载到的
    是磁盘原始字节，`Content-Length` 与 CRC32 也按原始字节算——两边量的不是同一个
    东西。实测复现：某 20846 行 CRLF 产物磁盘 524369 字节（超 512KiB 上限 81 字节），
    旧算法数出 503523、判为未超并放行，且回给 S3 的 `bytes` 少报 20846。512KiB 是
    S3 侧 PSRAM 缓冲的硬约束，少算即等于把越限产物放给设备。
    """
    base = _minimal_gcode()
    text = _minimal_gcode(_size_filler(MAX_BYTES - len(base.encode("utf-8"))))
    # LF 形态恰好等于上限；换成 CRLF 后磁盘字节必然超限（每行 +1）。
    assert len(text.encode("utf-8")) == MAX_BYTES

    crlf = text.replace("\n", "\r\n").encode("ascii")
    assert len(crlf) > MAX_BYTES
    p = tmp_path / "crlf.gcode"
    p.write_bytes(crlf)
    # 塌陷后仍是 MAX_BYTES，即旧算法会判「未超」。
    assert len(p.read_text(encoding="utf-8").encode("utf-8")) == MAX_BYTES

    with pytest.raises(GcodeError, match=f"总大小 {len(crlf)} 字节超上限"):
        _read_generated_gcode(p)


def test_gcode_stats_bytes_equals_real_file_size_for_crlf(tmp_path) -> None:
    """回给 S3 的 `bytes` 必须等于磁盘真实字节，否则与 Content-Length / CRC32 不同源。"""
    raw = _minimal_gcode().replace("\n", "\r\n").encode("ascii")
    p = tmp_path / "ok_crlf.gcode"
    p.write_bytes(raw)
    stats = _read_generated_gcode(p)
    assert stats.bytes == len(raw) == p.stat().st_size
    # 塌陷计数确实更小，证明这条断言不是恒真。
    assert len(p.read_text(encoding="utf-8").encode("utf-8")) < len(raw)


def test_validate_gcode_accepts_explicit_total_bytes_override() -> None:
    """`total_bytes` 显式传入时优先于文本推算，且缺省行为不变（纯文本场景）。"""
    text = _minimal_gcode()
    assert validate_gcode(text).bytes == len(text.encode("utf-8"))
    assert validate_gcode(text, total_bytes=12345).bytes == 12345
    with pytest.raises(GcodeError, match="总大小"):
        validate_gcode(text, total_bytes=MAX_BYTES + 1)


def test_generated_gcode_is_read_as_bytes_not_text() -> None:
    """钉住实现形态：产物必须按字节读并显式传 total_bytes。

    只断言行为不够——`_read_generated_gcode` 一旦被改回 `read_text()`，上面两条
    行为测试会红，但重构时容易被误当成「测试过时」而放宽。这里把根因钉在源码上。
    """
    import ast

    src = Path(gcode_gen.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_read_generated_gcode"
    )
    # 用 AST 取语句、剔掉 docstring：函数注释与 docstring 里会正面写出被禁的写法
    # （「必须按字节读，不能用 read_text()」），按文本 grep 会把说明当成实现。
    stmts = [n for n in fn.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))]
    body_code = "\n".join(ast.unparse(n) for n in stmts)

    assert "read_bytes()" in body_code
    assert "read_text(" not in body_code
    assert "total_bytes=len(raw)" in body_code


def test_gcode_line_payload_limit_is_254_not_255() -> None:
    """行长上限是 **254** 字节 payload，不是 255。

    Grbl_Esp32 `Protocol.h:34` 定义 `LINE_BUFFER_SIZE 256`，但 `Protocol.cpp:68` 的
    溢出判定排在换行判定**之前**：第 255 个 payload 字节能存进缓冲（len 变 255），
    紧随的换行符进来时先撞上 `len == LINE_BUFFER_SIZE - 1` → 回 `error:11`，且
    `Protocol.cpp:218` 的 `empty_line()` 把整行丢掉——该行**从未进入 execute_line()**。
    即 255 字节的行不是「勉强执行」而是「整行被吃」，校验器放行等于让设备静默丢行。
    上游 grblHAL 把 LINE_BUFFER_SIZE 定为 257（`protocol.h:36`）正是为绕开这个 off-by-one。
    """
    assert MAX_LINE_PAYLOAD_BYTES == 254

    # 254 字节 payload：放行（含换行整行 255 字节，设备侧 len 走到 254 后收到 EOL）
    ok_line = ";" + "x" * (MAX_LINE_PAYLOAD_BYTES - 1)
    assert len(ok_line.encode("ascii")) == MAX_LINE_PAYLOAD_BYTES
    validate_gcode(_minimal_gcode(ok_line + "\n"))

    # 255 字节 payload：必须拒。这一条是本次修复的核心——旧口径 `> 255` 会放行。
    bad_line = ";" + "x" * MAX_LINE_PAYLOAD_BYTES
    assert len(bad_line.encode("ascii")) == MAX_LINE_PAYLOAD_BYTES + 1
    with pytest.raises(GcodeError, match=r"255 字节超上限 254"):
        validate_gcode(_minimal_gcode(bad_line + "\n"))

    # 明显超长仍拒（回归保护，旧口径也能拦住这一条）
    with pytest.raises(GcodeError, match=r"超上限 254"):
        validate_gcode(_minimal_gcode(";" + "x" * 300 + "\n"))


def test_grbl_line_buffer_offbyone_reproduced_from_firmware_source() -> None:
    """转写固件收行逻辑，证明 254/255 的分界不是读码猜测。

    忠实复现 `Grbl_Esp32/src/Protocol.cpp:57-79`（add_char_to_line）+ `:149-225`
    （protocol_poll_client）的判定顺序与 empty_line 时机。若上游把溢出判定挪到 EOL
    判定之后、或改用 grblHAL 的 257，本测试会红——那时才允许调整 MAX_LINE_PAYLOAD_BYTES。
    """
    LINE_BUFFER_SIZE = 256  # Protocol.h:34

    def feed(payload: str, eol: str) -> tuple[list[str], list[int]]:
        """返回（设备应答序列, 真正进 execute_line 的各行长度）。"""
        resp: list[str] = []
        executed: list[int] = []
        ln, buf = 0, []
        for c in payload + eol:
            if ln == (LINE_BUFFER_SIZE - 1):  # :68 溢出判定在 EOL 判定之前
                resp.append("error:11")
                ln, buf = 0, []  # :218 empty_line()，整行丢弃
                continue
            if c in "\r\n":  # :71
                executed.append(len(buf))  # :163 line 指向 buffer，此时未清
                resp.append("ok")
                ln, buf = 0, []  # :210 empty_line()
                continue
            buf.append(c)
            ln += 1
        return resp, executed

    def payload(n: int) -> str:
        return "G" + "1" * (n - 1)

    # 254 字节：正常执行
    assert feed(payload(254), "\n") == (["ok"], [254])
    # 255 字节：整行被 error:11 吃掉，execute_line 一次都没进
    assert feed(payload(255), "\n") == (["error:11"], [])
    # 256 字节：溢出后残段继续攒，换行才落地成空行
    assert feed(payload(256), "\n") == (["error:11", "ok"], [0])

    # 边界即 MAX_LINE_PAYLOAD_BYTES：恰好放行的最大值、以及多一字节就被吃
    assert feed(payload(MAX_LINE_PAYLOAD_BYTES), "\n")[1] == [MAX_LINE_PAYLOAD_BYTES]
    assert feed(payload(MAX_LINE_PAYLOAD_BYTES + 1), "\n")[1] == []


def test_crlf_makes_grbl_answer_twice_per_line() -> None:
    """CRLF 行尾会让设备每行回 **两个** ok —— S3 必须重切行，禁止原样透传磁盘字节。

    `\\r` 触发一次 EOL（执行真实行），紧随的 `\\n` 又触发一次 EOL，`execute_line` 对
    `line[0] == 0` 直接返回 `Error::Ok`（`Protocol.cpp:84-86`）于是再回一个 ok。
    奎享真实缓存产物就是 CRLF（946 CRLF / 0 裸 LF），Windows 上 vpype gwrite 走
    `click.File("w")` 文本模式同样产 CRLF —— 这不是假设形态。

    当前 S3 安全，靠的是 `hutuji_job.cc:1496-1502` 的 ParseLines() 自己按 `\\n`/`\\r`
    切行并跳过空行、SendLine() 只补一个 `\\n`。一旦有人为省内存改成 zero-copy 直发
    PSRAM 字节，每行多一个 ok 会让 `c_line` 窗口计数（`hutuji_job.cc:1744`）持续正向
    漂移，表现为「越画越超发」。该不变量此前只存在于实现里，这里连同 §5 一起钉住。
    """
    LINE_BUFFER_SIZE = 256

    def answers(payload: str, eol: str) -> list[str]:
        resp: list[str] = []
        ln, buf = 0, []
        for c in payload + eol:
            if ln == (LINE_BUFFER_SIZE - 1):
                resp.append("error:11")
                ln, buf = 0, []
                continue
            if c in "\r\n":
                resp.append("ok")
                ln, buf = 0, []
                continue
            buf.append(c)
            ln += 1
        return resp

    assert answers("G1 X1Y1F8000", "\n") == ["ok"]
    assert answers("G1 X1Y1F8000", "\r\n") == ["ok", "ok"]


def test_gcode_rejects_non_ascii_even_when_character_count_fits() -> None:
    line = "G1 X1 Y1;" + chr(0x6C49) * 100
    assert len(line) <= 255
    assert len(line.encode("utf-8")) > 255
    with pytest.raises(GcodeError, match="非 ASCII"):
        validate_gcode(_minimal_gcode(line))


def test_gcode_rejects_paren_comment_line() -> None:
    """§5 注释口径：`(...)` 括号注释暂不解析，含 `(` 的行一律整行拒。"""
    for line in ("(G28 X0)", "; ok\n(G90)"):
        with pytest.raises(GcodeError, match="无法解析或不允许"):
            validate_gcode(_minimal_gcode(line + "\n"))


def test_pen_lifts_are_reported_but_not_rejected(caplog: pytest.LogCaptureFixture) -> None:
    # 抬笔 = Z 回 0 的运动（对齐奎享 Stepper 产物，本 profile 不产 M5）
    text = _minimal_gcode("G1G90 Z0.0F10000\n" * (PEN_LIFT_WARN_THRESHOLD + 1))
    with caplog.at_level(logging.WARNING):
        stats = validate_gcode(text)
    assert stats.pen_lifts == PEN_LIFT_WARN_THRESHOLD + 4
    assert "仍放行" in caplog.text


def test_capability_download_has_integrity_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = f"{server._new_basename()}.gcode"
    data = b"G21\nG90\n"
    (tmp_path / name).write_bytes(data)

    response = asyncio.run(server._serve_file(name))

    assert response.headers["content-length"] == str(len(data))
    assert response.headers["x-hutuji-crc32"] == f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"
    assert response.headers["cache-control"] == "private, no-store"


def test_fastapi_auto_docs_endpoints_are_disabled() -> None:
    """`/docs`、`/redoc`、`/openapi.json`、`/docs/oauth2-redirect` 必须在应用层就不存在。

    FastAPI 默认自动挂这四条，实测原本全回 200。公网访问不到，但那份安全**完全来自
    nginx 兜底**（443 的 `location / { return 404; }`），应用层零防御——`location /`
    一旦为加健康检查之类放开、或有人给 :8300 开直连调试，攻击面即静默打开，而
    preflight 与 pytest 原本都没有任何判据会红。对比 `/mcp` 有三层保护（nginx 显式
    404 + 安全组不放行 8300 + preflight 专项探针），这四条原本只有第一层。

    断言用路由表而非 TestClient 状态码：`app.mount("/", _mcp_app)` 会把未注册路径
    交给 MCP 子应用，其返回也是 404——即「端点已关掉」与「端点还在但被子应用兜住」
    在状态码上不可区分。只认路由表才能真正钉住 `docs_url=None` 三参数没被去掉。
    """
    paths = {getattr(route, "path", None) for route in server.app.routes}
    for forbidden in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert forbidden not in paths, f"{forbidden} 仍在路由表内：FastAPI 自动文档端点未关"

    # 该关的关掉了，该留的必须还在——否则本测试可被「删掉整个 app」满足。
    assert "/files/{name}" in paths
    # FastAPI 实例上的三个开关本身也钉住，避免有人只删路由不改构造参数。
    assert server.app.docs_url is None
    assert server.app.redoc_url is None
    assert server.app.openapi_url is None


def test_preflight_probes_docs_endpoints_directly_on_8300() -> None:
    """preflight 必须直连 :8300 验四条自动文档端点已关，且判据只认 404。

    这一项本身也需要判据钉住，否则它可以被静默删掉而没有任何东西会红——本仓已有
    先例：「明文 :80 /files 须 403」曾在 preflight 与 pytest 两层同时形同虚设。

    三条要求各有其对应的假绿形态：
    - **必须探 127.0.0.1:8300**：经 nginx 探只能证明 nginx 在拦，证不到应用层是否已关，
      而应用层零防御正是本项要覆盖的盲区。
    - **判据必须是 `!= "404"` 即 fail**，不能写成 `=~ ^[45]` 之类：那样 000（服务没起来、
      连不上）也落在范围外→报 pass，「探不到」就能蒙成绿。
    - **四条路径一条不少**：`/docs/oauth2-redirect` 最容易漏——它不受 `docs_url=None`
      之外的开关控制，实测原本同样回 200。
    """
    root = Path(__file__).resolve().parents[2]
    preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")

    body_start = preflight.index("check_docs_endpoints_disabled() {")
    body = preflight[body_start : preflight.index("\ncheck \"", body_start)]

    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert path in body, f"preflight 未探 {path}"
    # 直连应用端口，不经 nginx
    assert "http://127.0.0.1:8300${p}" in body
    # 只认 404；任何其它码（含 000 连不上）都判 fail
    assert '[[ "$code" != "404" ]]' in body
    assert "return 1" in body
    # 该项确实被注册成一条 check（否则函数写了也不跑）
    assert 'check "应用层已关 FastAPI 自动文档端点' in preflight


def test_legacy_or_guessable_filename_is_not_public(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = "draw_20260725_120000.gcode"
    (tmp_path / name).write_text("G21\nG90\n", encoding="utf-8")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(name))
    assert caught.value.status_code == 404


def test_expired_capability_returns_404_and_deletes_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path, ttl=600))
    base = server._new_basename()
    gcode = tmp_path / f"{base}.gcode"
    svg = tmp_path / f"{base}.svg"
    gcode.write_text("G21\nG90\n", encoding="utf-8")
    svg.write_text("<svg/>", encoding="utf-8")
    old = time.time() - 601
    os.utime(gcode, (old, old))
    os.utime(svg, (old, old))

    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(gcode.name))

    assert caught.value.status_code == 404
    assert not gcode.exists()
    assert not svg.exists()


def test_draw_limiter_rejects_rate_and_concurrency() -> None:
    now = [0.0]
    limiter = server.DrawLimiter(clock=lambda: now[0])

    asyncio.run(limiter.enter())
    with pytest.raises(server.DrawLimitError, match="每秒"):
        asyncio.run(limiter.enter())

    now[0] = 1.1
    asyncio.run(limiter.enter())
    now[0] = 2.2
    with pytest.raises(server.DrawLimitError, match="并发"):
        asyncio.run(limiter.enter())

    asyncio.run(limiter.leave())
    asyncio.run(limiter.leave())

def test_draw_limiter_slot_releases_under_cancellation() -> None:
    """取消路径必须把并发名额还回来：slot 的 finally 里不许有可挂起点，
    否则取消可能打断名额释放，让 max_concurrent 永久缩水（生成入口随之瘫痪）。"""

    async def exercise() -> None:
        limiter = server.DrawLimiter(per_second=100, per_minute=100, max_concurrent=1)
        entered = asyncio.Event()

        async def hold() -> None:
            async with limiter.slot():
                entered.set()
                await asyncio.sleep(60)

        task = asyncio.create_task(hold())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert limiter._active == 0, "名额泄漏会让后续生成全部被拒"
        # 名额真的可用，不只是计数归零
        await limiter.enter()
        await limiter.leave()

    asyncio.run(exercise())


def test_draw_limit_covers_vpype_conversion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两个转换占满名额后，第 3 个 draw 必须立即拒绝，不能继续堆 vpype。"""
    from types import SimpleNamespace

    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    monkeypatch.setattr(
        server,
        "_draw_limiter",
        server.DrawLimiter(per_second=100, per_minute=100, max_concurrent=2),
    )

    async def fake_generate_svg(_description: str) -> str:
        return "<svg/>"

    async def exercise() -> None:
        started = 0
        both_started = asyncio.Event()
        release = asyncio.Event()

        async def fake_convert(_svg_path: Path, _gcode_path: Path) -> SimpleNamespace:
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await release.wait()
            return SimpleNamespace(lines=1, bytes=1, pen_lifts=0)

        monkeypatch.setattr(server, "generate_bitmap_svg", fake_generate_svg)
        monkeypatch.setattr(server, "svg_to_gcode_async", fake_convert)
        tasks = [
            asyncio.create_task(server.hutuji_draw("first")),
            asyncio.create_task(server.hutuji_draw("second")),
        ]
        await asyncio.wait_for(both_started.wait(), timeout=1)
        try:
            with pytest.raises(server.DrawLimitError, match="并发"):
                await server.hutuji_draw("third")
            assert started == 2
        finally:
            release.set()
            await asyncio.gather(*tasks)

    asyncio.run(exercise())


def test_draw_cancellation_removes_partial_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    monkeypatch.setattr(
        server,
        "_draw_limiter",
        server.DrawLimiter(per_second=100, per_minute=100, max_concurrent=2),
    )

    async def fake_generate_svg(_description: str) -> str:
        return "<svg/>"

    async def exercise() -> None:
        started = asyncio.Event()

        async def fake_convert(_svg_path: Path, gcode_path: Path) -> None:
            gcode_path.write_text("partial", encoding="utf-8")
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(server, "generate_bitmap_svg", fake_generate_svg)
        monkeypatch.setattr(server, "svg_to_gcode_async", fake_convert)
        task = asyncio.create_task(server.hutuji_draw("cancel"))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert list(tmp_path.iterdir()) == []



# ---------------------------------------------------------------------------
# 位图生成路线（bitmap_svg）
# ---------------------------------------------------------------------------


def _star_png_bytes() -> bytes:
    """合成 1024×1024 五角星线稿 PNG（确定性，无网络无模型）。"""
    import io
    import math as _math

    import imageio.v3 as iio
    import numpy as np

    img = np.full((1024, 1024), 255, dtype=np.uint8)
    cx = cy = 512.0
    outer, inner = 400.0, 160.0
    verts = []
    for i in range(10):
        r = outer if i % 2 == 0 else inner
        ang = -_math.pi / 2 + i * _math.pi / 5
        verts.append((cx + r * _math.cos(ang), cy + r * _math.sin(ang)))
    verts.append(verts[0])
    for (x0, y0), (x1, y1) in zip(verts, verts[1:]):
        steps = int(max(abs(x1 - x0), abs(y1 - y0))) * 2 + 1
        for t in np.linspace(0, 1, steps):
            x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            xi, yi = int(x), int(y)
            img[yi - 2 : yi + 3, xi - 2 : xi + 3] = 0  # 5px 粗线
    buf = io.BytesIO()
    iio.imwrite(buf, img, extension=".png")
    return buf.getvalue()


def _star_rgba_png_bytes() -> bytes:
    """五角星的 RGBA 变体：背景透明黑（RGB=0 且 alpha=0），线条不透明黑。

    透明区的 RGB 刻意填 0：若实现丢 alpha 通道而不是按白底合成，整张图会被
    看成纯黑、描不出任何线条，用本夹具的用例随之变红——借此钉死「透明即
    无墨」的白底合成语义（imageio 会把 palette+tRNS 的 PNG 解码成 RGBA，
    生图 API 完全可能返回这类文件）。
    """
    import io

    import imageio.v3 as iio
    import numpy as np

    gray = iio.imread(_star_png_bytes())
    rgba = np.zeros((*gray.shape, 4), dtype=np.uint8)
    rgba[:, :, 3] = np.where(gray < 128, 255, 0).astype(np.uint8)
    buf = io.BytesIO()
    iio.imwrite(buf, rgba, extension=".png")
    return buf.getvalue()


def test_hutuji_draw_uses_bitmap_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hutuji_draw 必须走位图路线；接回 LLM 直写（旧路线）要红。"""
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    monkeypatch.setattr(
        server,
        "_draw_limiter",
        server.DrawLimiter(per_second=100, per_minute=100, max_concurrent=2),
    )
    called: list[str] = []

    async def fake_bitmap(_desc: str) -> str:
        called.append("bitmap")
        return "<svg/>"

    async def fake_llm(_desc: str) -> str:
        raise AssertionError("draw 不得走 LLM 直写路线")

    async def fake_convert(_svg_path: Path, _gcode_path: Path) -> SimpleNamespace:
        return SimpleNamespace(lines=1, bytes=1, pen_lifts=0)

    monkeypatch.setattr(server, "generate_bitmap_svg", fake_bitmap)
    monkeypatch.setattr(server, "generate_svg", fake_llm)
    monkeypatch.setattr(server, "svg_to_gcode_async", fake_convert)

    asyncio.run(server.hutuji_draw("test"))
    assert called == ["bitmap"]


def test_bitmap_trace_synthetic_star_full_chain(tmp_path: Path) -> None:
    """合成星稿 → 描摹 → SVG 过白名单 → 真 vpype 过 §5，全链无模型参与。"""
    from hutuji_mcp import bitmap_svg, gcode_gen, llm_svg

    polys, w, h = bitmap_svg.trace_bitmap_to_polylines(_star_png_bytes())
    assert polys, "一条折线都没描出来"
    assert sum(len(p) for p in polys) > 500
    svg = bitmap_svg.polylines_to_svg(polys, w, h)
    llm_svg._validate_svg(svg)  # 不抛即过白名单

    svg_path = tmp_path / "in.svg"
    gcode_path = tmp_path / "out.gcode"
    svg_path.write_text(svg, encoding="utf-8")
    stats = gcode_gen.svg_to_gcode(svg_path, gcode_path)
    assert stats.lines > 5


def test_bitmap_trace_rejects_non_png() -> None:
    from hutuji_mcp import bitmap_svg

    with pytest.raises(bitmap_svg.BitmapError, match="不是 PNG"):
        bitmap_svg.trace_bitmap_to_polylines(b"definitely not a png")


def test_bitmap_trace_rgba_png_composites_transparent_over_white() -> None:
    """RGBA PNG 必须按白底合成后再描：透明 = 无墨，不得裸抛 ValueError。"""
    from hutuji_mcp import bitmap_svg

    polys, w, h = bitmap_svg.trace_bitmap_to_polylines(_star_rgba_png_bytes())
    assert (w, h) == (1024, 1024)
    # 夹具的透明区 RGB 是 0：丢 alpha 的错误实现会看到纯黑图、一条线都描不出。
    assert sum(len(p) for p in polys) > 500


def test_bitmap_trace_rejects_odd_channel_count() -> None:
    """LA（2 通道）等非常规通道数必须拦成 BitmapError，不能裸抛 ValueError。"""
    import io

    import imageio.v3 as iio
    import numpy as np
    from hutuji_mcp import bitmap_svg

    la = np.full((64, 64, 2), 255, dtype=np.uint8)
    buf = io.BytesIO()
    iio.imwrite(buf, la, extension=".png")
    with pytest.raises(bitmap_svg.BitmapError, match="不支持的位图通道数"):
        bitmap_svg.trace_bitmap_to_polylines(buf.getvalue())


def _valid_ihdr_png(width: int, height: int) -> bytes:
    """造一个 IHDR 合法（宽高可控）但正文缺 IDAT/IEND 的 PNG。

    用途有二：喂过 _reject_oversized_png 这道头部闸去测更深的解码失败路径；
    以及只用 24 字节头就能测超大图拦截，不必真造出上百 MB 的图。
    """
    from hutuji_mcp import bitmap_svg

    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes([8, 0, 0, 0, 0])  # 8bit 灰度，无交错
    )
    return bitmap_svg._PNG_MAGIC + bitmap_svg._png_chunk(b"IHDR", ihdr)


def test_bitmap_trace_corrupt_png_raises_bitmap_error() -> None:
    """PNG 签名 + IHDR 完好但正文损坏时，插件异常必须包成 BitmapError（实测 OSError）。

    夹具刻意带合法 IHDR：否则会被 _reject_oversized_png 的头部闸先拦掉，
    本测试就测不到「imageio 插件异常被包窄」这个原本要守的契约了。
    """
    from hutuji_mcp import bitmap_svg

    with pytest.raises(bitmap_svg.BitmapError, match="位图解码失败"):
        bitmap_svg.trace_bitmap_to_polylines(_valid_ihdr_png(64, 64) + b"0" * 32)


def test_bitmap_trace_rejects_oversized_png_before_decode() -> None:
    """超过 _MAX_IMAGE_PIXELS 的图必须在解码前按 IHDR 拒掉。

    动机是实测的内存放大：RGBA 4000²（16 Mpx）压缩仅 67.7 KiB，rgba2rgb 产
    3 通道 float64 384 MB、峰值 1728 MB（24944×）。_IMAGE_DOWNLOAD_MAX_BYTES
    限的是压缩字节，对像素数完全无效；Pillow 自带的炸弹闸要到 178,956,970 px
    才报错，那之下已足够打爆小 VPS，而 unit 没有 MemoryMax= 兜底。
    本例只给 24 字节头，正文一字节没有——能红/能绿就证明闸在解码之前生效。
    """
    from hutuji_mcp import bitmap_svg

    side = 4000  # 16 Mpx > 5 Mpx 上限
    with pytest.raises(bitmap_svg.BitmapError, match="位图过大"):
        bitmap_svg.trace_bitmap_to_polylines(_valid_ihdr_png(side, side))


def test_bitmap_trace_accepts_current_generation_size() -> None:
    """现行生图尺寸（_IMAGE_SIZE=1024x1024）必须能过头部闸。

    这是上一条的反向钉子：上限收得过狠会静默废掉唯一现行生成路线。
    只验头部闸放行（正文缺失故仍会在解码期失败），不重复描摹链的覆盖。
    """
    from hutuji_mcp import bitmap_svg

    side = int(bitmap_svg._IMAGE_SIZE.split("x")[0])
    assert side * side <= bitmap_svg._MAX_IMAGE_PIXELS
    with pytest.raises(bitmap_svg.BitmapError, match="位图解码失败"):
        bitmap_svg.trace_bitmap_to_polylines(_valid_ihdr_png(side, side) + b"0" * 32)


def test_bitmap_trace_rejects_png_without_leading_ihdr() -> None:
    """IHDR 不在首块即判畸形：容错去扫后续块等于放弃这道闸。"""
    from hutuji_mcp import bitmap_svg

    with pytest.raises(bitmap_svg.BitmapError, match="首块不是 IHDR"):
        bitmap_svg.trace_bitmap_to_polylines(bitmap_svg._PNG_MAGIC + b"0" * 32)


def test_bitmap_trace_rejects_truncated_png_header() -> None:
    """不足 24 字节读不到 IHDR，必须报头不完整而不是切片出空值放行。"""
    from hutuji_mcp import bitmap_svg

    with pytest.raises(bitmap_svg.BitmapError, match="PNG 头不完整"):
        bitmap_svg.trace_bitmap_to_polylines(bitmap_svg._PNG_MAGIC + b"0" * 4)


def test_bitmap_trace_rejects_zero_dimension_png() -> None:
    """宽或高为 0 的 PNG：w*h=0 会溜过上限比较，必须单独拦。"""
    from hutuji_mcp import bitmap_svg

    with pytest.raises(bitmap_svg.BitmapError, match="宽高非法"):
        bitmap_svg.trace_bitmap_to_polylines(_valid_ihdr_png(0, 1024))


def test_bitmap_generate_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(llm_api_key="", llm_base_url="https://x.invalid/v1", image_model="m"),
    )
    with pytest.raises(bitmap_svg.BitmapError, match="HUTUJI_LLM_API_KEY"):
        asyncio.run(bitmap_svg.generate_bitmap_svg("test"))


def test_bitmap_fetch_rejects_blank_image_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """HUTUJI_IMAGE_MODEL 空白必须在本地拦下：空 model 名不该发进计费请求。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="  "
        ),
    )
    with pytest.raises(bitmap_svg.BitmapError, match="HUTUJI_IMAGE_MODEL"):
        asyncio.run(bitmap_svg._fetch_image_png("x"))


def _png_stub() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"0" * 32


def test_bitmap_fetch_retries_transient_then_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """429→429→200 必须重试成功；prompt 带线稿风格与用户主题，model 走配置。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="img-m"
        ),
    )
    calls: list[dict] = []
    image_url = "https://cdn.example/ok.png"

    def handler(request):  # noqa: ANN001, ANN202
        import httpx as _httpx

        if request.url.path.endswith("/images/generations"):
            calls.append(json.loads(request.content))
            if len(calls) < 3:
                return _httpx.Response(429)
            return _httpx.Response(200, json={"data": [{"url": image_url}]})
        return _httpx.Response(200, content=_png_stub())

    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    import httpx as _httpx

    transport = _httpx.MockTransport(handler)
    body = asyncio.run(bitmap_svg._fetch_image_png("五角星", transport=transport))
    assert body == _png_stub()
    assert len(calls) == 3, "瞬态 429 必须重试"
    assert calls[0]["model"] == "img-m"
    assert "五角星" in calls[0]["prompt"]
    assert "线稿" in calls[0]["prompt"]



def test_bitmap_fetch_persistent_500_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="m"
        ),
    )
    import httpx as _httpx

    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    transport = _httpx.MockTransport(lambda request: _httpx.Response(500))
    with pytest.raises(bitmap_svg.BitmapError, match="生图 API 返回 500"):
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=transport))


def test_bitmap_fetch_rejects_bad_response_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="m"
        ),
    )
    import httpx as _httpx

    transport = _httpx.MockTransport(lambda request: _httpx.Response(200, json={}))
    with pytest.raises(bitmap_svg.BitmapError, match="响应结构异常"):
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=transport))


def test_bitmap_fetch_rejects_non_json_200_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """200 但正文不是 JSON（如网关 HTML 错误页）必须拦成 BitmapError。

    json.JSONDecodeError 是 ValueError 而非 httpx 异常，不拦会裸穿出模块。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="m"
        ),
    )
    import httpx as _httpx

    transport = _httpx.MockTransport(
        lambda request: _httpx.Response(200, content=b"<html>Bad Gateway</html>")
    )
    with pytest.raises(bitmap_svg.BitmapError, match="不是合法 JSON"):
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=transport))


def _generations_ok_transport(get_handler):  # noqa: ANN001, ANN202
    """生图 POST 恒 200 并返回图片 URL；图片 GET 的行为由 get_handler 注入。"""
    import httpx as _httpx

    def handler(request):  # noqa: ANN001, ANN202
        if request.url.path.endswith("/images/generations"):
            return _httpx.Response(
                200, json={"data": [{"url": "https://cdn.example/ok.png"}]}
            )
        return get_handler(request)

    return _httpx.MockTransport(handler)


def test_bitmap_download_network_error_raises_bitmap_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """图片 GET 的 httpx 异常必须包成 BitmapError（只记类型名），不能裸穿出模块。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="m"
        ),
    )
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    import httpx as _httpx

    def broken_get(request):  # noqa: ANN001, ANN202
        raise _httpx.ConnectError("simulated CDN outage", request=request)

    transport = _generations_ok_transport(broken_get)
    with pytest.raises(
        bitmap_svg.BitmapError, match="图片下载网络错误（ConnectError）"
    ):
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=transport))


def test_bitmap_download_retries_once_then_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """图片 GET 首次瞬态断连必须重试一次并成功（重新 GET 不再计费）。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="m"
        ),
    )
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    import httpx as _httpx

    get_calls = 0

    def flaky_get(request):  # noqa: ANN001, ANN202
        nonlocal get_calls
        get_calls += 1
        if get_calls == 1:
            raise _httpx.ConnectError("simulated CDN blip", request=request)
        return _httpx.Response(200, content=_png_stub())

    transport = _generations_ok_transport(flaky_get)
    body = asyncio.run(bitmap_svg._fetch_image_png("x", transport=transport))
    assert body == _png_stub()
    assert get_calls == 2, "图片下载必须重试一次"


def test_bitmap_download_rejects_oversize_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content-Length 声明超限必须在读 body 前就拒：小 body 大声明也要拒。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="m"
        ),
    )
    import httpx as _httpx

    def oversize_get(request):  # noqa: ANN001, ANN202
        # 实际 body 很小但声明超限：只在缓冲后按 len(body) 检查的实现会漏过本用例。
        return _httpx.Response(
            200,
            content=_png_stub(),
            headers={"content-length": str(bitmap_svg._IMAGE_DOWNLOAD_MAX_BYTES + 1)},
        )

    transport = _generations_ok_transport(oversize_get)
    with pytest.raises(bitmap_svg.BitmapError, match="图片超过下载上限"):
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=transport))

def test_bitmap_download_rejects_oversize_chunked_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无 Content-Length（chunked）的超大正文必须被流式累计上限拦下。

    声明级闸对缺头响应不生效，逐块累计是唯一兜底；且流式实现保证拦截发生在
    全量进内存之前（非流式 GET 先缓冲再判长，拦不住内存暴涨）。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="m"
        ),
    )
    import httpx as _httpx

    def chunked_oversize_get(request):  # noqa: ANN001, ANN202
        # 显式 Transfer-Encoding: chunked 让 httpx 不自动补 Content-Length，
        # 声明级闸因此跳过，只剩流式累计上限能拦。
        return _httpx.Response(
            200,
            headers={"transfer-encoding": "chunked"},
            content=b"x" * (bitmap_svg._IMAGE_DOWNLOAD_MAX_BYTES + 1),
        )

    transport = _generations_ok_transport(chunked_oversize_get)
    with pytest.raises(bitmap_svg.BitmapError, match="图片超过下载上限"):
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=transport))


def test_bitmap_download_total_timeout_covers_stream_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """慢速滴流（每块间隔短、总时长长）必须被墙钟总超时拦下。

    httpx 的 `timeout=` 只管两次 chunk 之间的静默间隔，不管总时长——上游文档：
    超时只在「对端在超时窗口内没发完整消息」时触发。故 CDN 慢速攻击（如 1KB/s
    持续流，每块都按时到达）在 httpx 层永不超时，能把一个 worker 挂住数十分钟，
    并发名额随之耗尽。唯一防线是把 `asyncio.timeout` 包在 `client.stream`
    **之外**，覆盖 stream 建立 + 整个 `aiter_bytes` 循环 + body 累加。

    本例把总超时压到 0.3s，滴流总需 ~2.5s（50 块 × 0.05s），每块间隔 0.05s 远
    小于 httpx 的读超时——即「httpx 视角完全健康、只有总时长超标」。若
    `asyncio.timeout` 退回只包 stream 建立，本例会跑满滴流全程且不抛超时。
    """
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="m"
        ),
    )
    monkeypatch.setattr(bitmap_svg, "_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    import httpx as _httpx

    drip_chunks = 50
    drip_gap = 0.05
    delivered = {"n": 0}

    async def slow_drip():  # noqa: ANN202
        for _ in range(drip_chunks):
            await asyncio.sleep(drip_gap)
            delivered["n"] += 1
            yield b"x"

    def dripping_get(request):  # noqa: ANN001, ANN202
        # 无 Content-Length：声明级闸跳过；每块 1 字节，累计上限也拦不到。
        # 唯一能终止它的就是总超时。
        return _httpx.Response(
            200, headers={"transfer-encoding": "chunked"}, content=slow_drip()
        )

    transport = _generations_ok_transport(dripping_get)
    started = time.monotonic()
    with pytest.raises(bitmap_svg.BitmapError, match="图片下载网络错误（TimeoutError）"):
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=transport))
    elapsed = time.monotonic() - started

    # 两次尝试（首次超时后重试一次）各 0.3s，故上界给 2×超时 + 余量，
    # 且必须远小于滴流全程 drip_chunks*drip_gap = 2.5s。
    assert elapsed < drip_chunks * drip_gap, (
        f"总超时未生效：耗时 {elapsed:.2f}s 已跑满滴流全程"
    )
    assert delivered["n"] < drip_chunks, (
        f"必须在滴流读完前中断，实际已收 {delivered['n']}/{drip_chunks} 块"
    )

def test_bitmap_generate_end_to_end_mocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mock 生图下载，描摹+校验全真：返回合法单线 SVG。"""
    from hutuji_mcp import bitmap_svg

    async def fake_fetch(_desc: str) -> bytes:
        return _star_png_bytes()

    monkeypatch.setattr(bitmap_svg, "_fetch_image_png", fake_fetch)
    svg = asyncio.run(bitmap_svg.generate_bitmap_svg("五角星"))
    assert "<polyline" in svg
    assert svg.startswith("<svg")


def test_load_settings_image_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUTUJI_IMAGE_MODEL", "img-x")
    from hutuji_mcp.config import load_settings

    assert load_settings().image_model == "img-x"


def test_delete_output_pair_logs_unlink_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path))

    def denied(*_args, **_kwargs) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "unlink", denied)
    server._delete_output_pair("draw_20260805_120000_abcdefghijklmnopqrstuv")
    assert "删除过期产物失败" in caplog.text
    assert "denied" in caplog.text


def test_cleanup_warning_logs_never_leak_capability_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """产物清理路径的 warning 必须走 `_redact_capability_name`。

    这些 warning 会进 journald。`_is_expired` 抛错跳过与失败绘制清理两条路径上，
    token 可能**仍在 TTL 窗口内有效**，直接记原名等于把 capability URL 写进日志。
    """
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    token = "AbCdEf_GhIjKlMnOpQrStU"
    assert len(token) == 22
    stem = f"draw_20260805_120000_{token}"
    (tmp_path / f"{stem}.gcode").write_text("G21\nG90\n", encoding="utf-8")

    def denied(*_args, **_kwargs) -> None:
        raise PermissionError("denied")

    with caplog.at_level(logging.WARNING):
        # 站点一：_delete_output_pair 的 unlink 失败
        monkeypatch.setattr(Path, "unlink", denied)
        server._delete_output_pair(stem)
        monkeypatch.undo()
        monkeypatch.setattr(server, "settings", _settings(tmp_path))

        # 站点二：_is_expired 抛 OSError → 跳过该项
        monkeypatch.setattr(
            server, "_is_expired", lambda *_a, **_k: (_ for _ in ()).throw(PermissionError("stat"))
        )
        server._cleanup_expired_outputs()
        monkeypatch.undo()
        monkeypatch.setattr(server, "settings", _settings(tmp_path))

        # 站点三：已过期但 unlink 失败
        monkeypatch.setattr(server, "_is_expired", lambda *_a, **_k: True)
        monkeypatch.setattr(Path, "unlink", denied)
        server._cleanup_expired_outputs()

    assert "删除过期产物失败" in caplog.text
    assert "跳过清理" in caplog.text
    assert token not in caplog.text
    # 结构化脱敏形态：保留时间戳前缀，token 整段变 `*`
    assert f"draw_20260805_120000_*.gcode" in caplog.text


def test_status_latest_filename_is_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    base = server._new_basename()
    name = f"{base}.gcode"
    (tmp_path / name).write_text("G21\nG90\n", encoding="utf-8")
    # basename 含 token；status 不得回显完整 capability 名。
    # token 是 URL-safe base64，本身可能含 `_`，故按结构取而不是 rsplit("_")
    # ——旧写法会在约 29% 的随机 token 上取到残缺 token，让本断言时好时坏。
    token = server._DRAW_FILE_PARTS_RE.fullmatch(name)["token"]
    assert len(token) == 22

    payload = asyncio.run(server.hutuji_status())
    import json

    data = json.loads(payload)
    assert data["gcode_files"] == 1
    assert data["latest"] is not None
    assert token not in data["latest"]
    assert data["latest"].startswith("draw_")
    assert data["latest"].endswith(".gcode")
    assert "*" in data["latest"]

def test_status_ignores_foreign_gcode_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """output_dir 里手工放的外来 .gcode 不是本服务产物，计入 gcode_files/latest
    会让 status 反映不出真实的生成活动（混入即失真）。"""
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    (tmp_path / "handmade.gcode").write_text("G21\nG90\n", encoding="utf-8")
    import json

    data = json.loads(asyncio.run(server.hutuji_status()))
    assert data["gcode_files"] == 0
    assert data["latest"] is None


def test_draw_limiter_rejects_minute_window() -> None:
    now = [0.0]
    limiter = server.DrawLimiter(
        per_second=100,
        per_minute=2,
        max_concurrent=100,
        clock=lambda: now[0],
    )
    asyncio.run(limiter.enter())
    asyncio.run(limiter.enter())
    with pytest.raises(server.DrawLimitError, match="每分钟"):
        asyncio.run(limiter.enter())


# ---- 2026-07-26 安全审计修复回归 ----


def test_serve_file_returns_response_not_file_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1 TOCTOU 修复：_serve_file 返回 Response（内存发送），不再是 FileResponse。"""
    from fastapi.responses import Response as _Resp

    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = f"{server._new_basename()}.gcode"
    (tmp_path / name).write_bytes(b"G21\nG90\n")
    resp = asyncio.run(server._serve_file(name))
    assert isinstance(resp, _Resp)
    assert resp.body == b"G21\nG90\n"
    assert resp.headers.get("content-disposition") == "attachment"


def test_serve_file_concurrent_delete_returns_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1 TOCTOU：文件在 is_file() 检查后、read_bytes() 前被删除，应返回 404。"""
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = f"{server._new_basename()}.gcode"
    path = tmp_path / name
    path.write_bytes(b"G21\nG90\n")
    path.unlink()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(name))
    assert caught.value.status_code == 404


def test_serve_file_rejects_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1：output_dir 内命名合法的 symlink 不得被跟随读取（防借链接外泄任意文件）。"""
    name = f"{server._new_basename()}.gcode"
    target = tmp_path / "secret.bin"
    target.write_bytes(b"must-not-leak")
    try:
        os.symlink(target, tmp_path / name)
    except (OSError, NotImplementedError) as exc:
        # Windows 上建 symlink 需管理员/开发者模式，无权限时跳过并说明原因。
        pytest.skip(f"当前平台无权限创建 symlink：{exc}")

    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(name))
    assert caught.value.status_code == 404


def test_serve_file_rejects_hardlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """命名合法的硬链接（st_nlink != 1）不得被服务：capability URL 只能指向
    本服务生成的单链接普通文件，别名入口会让配对删除留下可访问副本。"""
    name = f"{server._new_basename()}.gcode"
    target = tmp_path / "secret.bin"
    target.write_bytes(b"must-not-leak")
    os.link(target, tmp_path / name)

    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(name))
    assert caught.value.status_code == 404


def test_serve_file_does_not_follow_swapped_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """路径在打开前被换成 symlink 也不得跟随：TOCTOU 竞态只能拿到 404。"""
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = f"{server._new_basename()}.gcode"
    real = tmp_path / name
    real.write_bytes(b"G21\nG90\n")
    secret = tmp_path / "secret.bin"
    secret.write_bytes(b"must-not-leak")

    real_open = server.os.open

    def _swap_then_open(path, flags, *args, **kwargs):
        # 模拟「检查通过后、打开前」路径被替换为指向敏感文件的 symlink。
        if Path(path) == real and real.exists():
            real.unlink()
            try:
                os.symlink(secret, real)
            except (OSError, NotImplementedError) as exc:
                # Windows 上建 symlink 需管理员/开发者模式，无权限时跳过。
                pytest.skip(f"当前平台无权限创建 symlink：{exc}")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(server.os, "open", _swap_then_open)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(name))
    assert caught.value.status_code == 404


def test_serve_file_never_reads_path_swapped_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """打开前路径被换成同名新文件（Windows 下打开新对象 / Linux 下身份校验
    失败）：响应要么 404，要么内容仍是打开时锁定的原对象，绝不返回替换内容。"""
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = f"{server._new_basename()}.gcode"
    real = tmp_path / name
    original = b"G21\nG90\n"
    real.write_bytes(original)

    real_open = server.os.open

    def _swap_then_open(path, flags, *args, **kwargs):
        if Path(path) == real and real.exists():
            replacement = tmp_path / "replacement.tmp"
            replacement.write_bytes(b"must-not-leak")
            os.replace(replacement, real)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(server.os, "open", _swap_then_open)
    try:
        resp = asyncio.run(server._serve_file(name))
    except HTTPException as caught:
        assert caught.status_code == 404
    else:
        assert resp.body == original
        assert b"must-not-leak" not in resp.body


def test_read_regular_file_rejects_rename_swap_with_nofollow_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """强制走带 ``O_NOFOLLOW`` 的分支语义，rename-swap 不得读到替换内容。

    Windows 上 ``os.replace`` 后 ``os.open`` 可能仍拿到旧句柄，因此用 fake ``os``
    固定控制流；该用例钉住的是「open 后必须复核路径身份」这一生产契约。"""
    name = f"{server._new_basename()}.gcode"
    real = tmp_path / name
    real.write_bytes(b"old")

    import os as real_os

    class FakeOS:
        O_RDONLY = real_os.O_RDONLY
        O_BINARY = getattr(real_os, "O_BINARY", 0)
        O_NOFOLLOW = 0x40000

        @staticmethod
        def stat(path, follow_symlinks=True):
            return real_os.stat(path, follow_symlinks=follow_symlinks)

        @staticmethod
        def fstat(fd):
            return real_os.fstat(fd)

        @staticmethod
        def fdopen(fd, mode):
            return real_os.fdopen(fd, mode)

        @staticmethod
        def close(fd):
            return real_os.close(fd)

        @staticmethod
        def open(path, flags, *args, **kwargs):
            flags &= ~FakeOS.O_NOFOLLOW
            target = Path(path)
            if target == real and target.exists():
                replacement = tmp_path / "replacement.tmp"
                replacement.write_bytes(b"must-not-leak")
                real_os.replace(replacement, target)
            return real_os.open(path, flags, *args, **kwargs)

    monkeypatch.setattr(server, "os", FakeOS)
    with pytest.raises(OSError, match="目标在读取期间被替换"):
        server._read_regular_file(real)

def test_status_does_not_leak_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#5：status 不泄露服务端绝对路径。"""
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    import json as _json

    data = _json.loads(asyncio.run(server.hutuji_status()))
    assert "output_dir" not in data
    assert "output_dir_exists" in data


def test_svg_size_limit() -> None:
    """#2：SVG 超过 256KB 应被拒绝。"""
    from hutuji_mcp.llm_svg import LlmError, MAX_SVG_BYTES, _extract_svg

    giant = '<svg xmlns="http://www.w3.org/2000/svg">' + "x" * (MAX_SVG_BYTES + 1000) + "</svg>"
    with pytest.raises(LlmError, match="256KB"):
        _extract_svg(giant)


def test_svg_deep_nesting_rejected() -> None:
    """#7：深嵌套 SVG 应被拒绝，不得触发 RecursionError。"""
    from hutuji_mcp.llm_svg import LlmError, _extract_svg

    layers = '<g fill="none">' * 100
    close = "</g>" * 100
    svg = f'<svg xmlns="http://www.w3.org/2000/svg">{layers}{close}</svg>'
    with pytest.raises(LlmError, match="嵌套深度"):
        _extract_svg(svg)


def test_vpype_stderr_not_in_error_message(tmp_path: Path) -> None:
    """#4：vpype 失败时 stderr 不应泄露给调用方——消息只含 exit code。"""
    from hutuji_mcp.gcode_gen import GcodeError, svg_to_gcode

    bad_svg = tmp_path / "bad.svg"
    bad_svg.write_text("<not-svg/>", encoding="utf-8")
    out_gcode = tmp_path / "out.gcode"
    with pytest.raises(GcodeError, match=r"exit \d+") as caught:
        svg_to_gcode(bad_svg, out_gcode)
    msg = str(caught.value)
    assert "/" not in msg and "\\" not in msg, f"stderr 泄露了路径信息：{msg}"


def test_svg_to_gcode_wraps_timeout_as_gcode_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """vpype 超时（subprocess.TimeoutExpired）必须塌成 GcodeError。

    TimeoutExpired 不是 GcodeError，不包会穿透 svg_to_gcode 变成 500，
    违背 §5「越界即整单拒绝 + 明确告知」的窄异常契约。"""
    import subprocess as _sp

    from hutuji_mcp import gcode_gen

    def fake_timeout(cmd, **kwargs):
        raise _sp.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(gcode_gen.subprocess, "run", fake_timeout)
    svg = tmp_path / "in.svg"
    svg.write_text("<svg/>", encoding="utf-8")
    with pytest.raises(GcodeError, match="超时"):
        gcode_gen.svg_to_gcode(svg, tmp_path / "out.gcode")


def test_svg_to_gcode_wraps_missing_output_as_gcode_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """vpype exit 0 却不写产物（FileNotFoundError）必须塌成 GcodeError。"""
    import subprocess as _sp

    from hutuji_mcp import gcode_gen

    def fake_run_no_output(cmd, **kwargs):
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gcode_gen.subprocess, "run", fake_run_no_output)
    svg = tmp_path / "in.svg"
    svg.write_text("<svg/>", encoding="utf-8")
    with pytest.raises(GcodeError, match="未产出"):
        gcode_gen.svg_to_gcode(svg, tmp_path / "out.gcode")


def test_svg_to_gcode_async_terminates_child_on_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hutuji_mcp import gcode_gen

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = None
            self.started = asyncio.Event()
            self.terminated = False

        async def communicate(self):
            self.started.set()
            await asyncio.Event().wait()

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode or 0

    process = FakeProcess()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return process

    monkeypatch.setattr(gcode_gen.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    async def exercise() -> None:
        task = asyncio.create_task(
            gcode_gen.svg_to_gcode_async(tmp_path / "in.svg", tmp_path / "out.gcode")
        )
        await asyncio.wait_for(process.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert process.terminated is True
    assert process.returncode == -15


def test_svg_to_gcode_async_terminates_child_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hutuji_mcp import gcode_gen

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = None
            self.terminated = False

        async def communicate(self):
            raise asyncio.TimeoutError

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode or 0

    process = FakeProcess()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return process

    monkeypatch.setattr(gcode_gen.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(GcodeError, match="超时"):
        asyncio.run(
            gcode_gen.svg_to_gcode_async(tmp_path / "in.svg", tmp_path / "out.gcode")
        )

    assert process.terminated is True
    assert process.returncode == -15


def test_svg_to_gcode_async_runs_real_vpype_pipeline(tmp_path: Path) -> None:
    """异步生产入口必须能启动真实 vpype，并产出通过 §5 校验的文件。"""
    from hutuji_mcp import gcode_gen

    svg = Path(__file__).with_name("test_star.svg")
    gcode = tmp_path / "star.gcode"

    stats = asyncio.run(gcode_gen.svg_to_gcode_async(svg, gcode))

    raw = gcode.read_bytes()
    generated = raw.decode("ascii")
    # `stats.bytes` 必须是磁盘真实字节，不是 universal newlines 塌陷后的字符数。
    # 本断言原写作 `len(generated.encode("ascii"))`，而 `generated` 来自
    # `read_text()`——在 Windows 上 vpype 产出 CRLF，塌陷后每行少 1 字节（实测
    # 243 vs 真实 257），于是测试自己钉住了错的口径。S3 下载到的、
    # `Content-Length` 与 CRC32 算的都是磁盘原始字节，必须同源。
    assert stats.bytes == len(raw) == gcode.stat().st_size
    assert stats.lines > 5
    # 逐行契约与文本一致（校验器只按文本判形状），字节数另由上面钉死。
    assert stats == gcode_gen.validate_gcode(generated, total_bytes=len(raw))


def test_vpype_pipeline_keeps_simplify_and_merge(tmp_path: Path, monkeypatch) -> None:
    """回归：管线必须含 linemerge + linesimplify，且顺序在 gwrite 之前。

    这两步是「笔走停停」修复的实体（commit 2aead01）：SVG 曲线离散成 0.12mm
    步长的密点，逐行流控下每行执行 0.9ms（0.12mm @ 生产 F8000=133mm/s）
    < Telnet 往返 ~25ms，电机干等。
    若有人清理管线时删掉这两行，其余测试照样全绿，卡顿静默回归。
    """
    import subprocess as _sp

    from hutuji_mcp import gcode_gen

    captured: dict = {}

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
        captured["cmd"] = cmd
        # 伪造 vpype 成功：往输出路径写一份最小合法 G-code
        Path(cmd[-1]).write_text(_minimal_gcode(), encoding="utf-8")
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gcode_gen.subprocess, "run", fake_run)
    svg = tmp_path / "in.svg"
    svg.write_text("<svg/>", encoding="utf-8")
    gcode_gen.svg_to_gcode(svg, tmp_path / "out.gcode")

    cmd = captured["cmd"]
    for step in ("linemerge", "linesort", "linesimplify", "gwrite"):
        assert step in cmd, f"vpype 管线缺少 {step}"
    order = [cmd.index(s) for s in ("linemerge", "linesort", "linesimplify", "gwrite")]
    assert order == sorted(order), f"管线顺序被破坏：{cmd}"
    # 容差必须显式给出（默认容差未必等于已验证的 0.05/0.3mm）
    assert cmd[cmd.index("linesimplify") + 1] == "--tolerance"
    assert cmd[cmd.index("linemerge") + 1] == "--tolerance"
    # 数值也要锁：只断言「有 --tolerance」的话，把容差改成 5mm 照样全绿
    assert cmd[cmd.index("linesimplify") + 2] == f"{gcode_gen.LINESIMPLIFY_TOLERANCE_MM}mm"
    assert cmd[cmd.index("linemerge") + 2] == f"{gcode_gen.LINEMERGE_TOLERANCE_MM}mm"


def test_tolerances_are_pinned_to_verified_values() -> None:
    """容差直接决定行数与画质，改动必须是显式决策而非顺手调参。

    0.05mm：S3 已落地 512 字节窗口化流控（`15f73c9`），往返不再逐行串行，
    故从 0.1mm 回降，向奎享 0.02mm 画质靠拢。0.3mm：对齐奎享 nearDst。
    """
    from hutuji_mcp import gcode_gen

    assert gcode_gen.LINESIMPLIFY_TOLERANCE_MM == 0.05
    assert gcode_gen.LINEMERGE_TOLERANCE_MM == 0.3



def _gcode_xy_bbox(gcode_path: Path) -> tuple[float, float, float, float]:
    """产物 G-code 里全部 X/Y 坐标的包围盒（G0 空走端点即笔画端点，同 extent）。"""
    from hutuji_mcp import gcode_gen

    xs: list[float] = []
    ys: list[float] = []
    for line in gcode_path.read_text(encoding="ascii").splitlines():
        for letter, value in gcode_gen._WORD_RE.findall(line):
            if letter == "X":
                xs.append(float(value))
            elif letter == "Y":
                ys.append(float(value))
    assert xs and ys, "产物里一个坐标都没有"
    return min(xs), min(ys), max(xs), max(ys)


def test_vpype_pipeline_normalizes_layout(tmp_path: Path, monkeypatch) -> None:
    """管线必须在 read 之后、容差步骤之前做 layout 归一化（居中 + 等比缩放到边距内）。

    2026-08-09 画质探针实测：LLM 无视 prompt 的「居中留约 10 边距」，
    六组题材 bbox 占画布 1.8%~85.5% 乱飞，还有顶到 X=190/Y=277 硬边的。
    归一化放在 linemerge/linesimplify 之前，0.3/0.05mm 容差才是最终毫米
    几何上的绝对值；放在其后会把容差随缩放倍数放大。
    """
    import subprocess as _sp

    from hutuji_mcp import gcode_gen

    captured: dict = {}

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN202
        captured["cmd"] = cmd
        Path(cmd[-1]).write_text(_minimal_gcode(), encoding="utf-8")
        return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gcode_gen.subprocess, "run", fake_run)
    svg = tmp_path / "in.svg"
    svg.write_text("<svg/>", encoding="utf-8")
    gcode_gen.svg_to_gcode(svg, tmp_path / "out.gcode")

    cmd = captured["cmd"]
    assert "layout" in cmd, f"vpype 管线缺少 layout 归一化：{cmd}"
    assert cmd.index("read") < cmd.index("layout") < cmd.index("linemerge"), cmd
    assert cmd[cmd.index("layout") + 1] == "--fit-to-margins"
    # 边距与画布必须钉死：只断言「有 layout」的话，改成 0 边距照样全绿
    assert cmd[cmd.index("layout") + 2] == f"{gcode_gen.LAYOUT_MARGIN_MM:.0f}mm"
    assert cmd[cmd.index("layout") + 3] == (
        f"{gcode_gen.MAX_X_MM:.0f}x{gcode_gen.MAX_Y_MM:.0f}mm"
    )


def test_svg_to_gcode_upscales_and_centers_tiny_drawing(tmp_path: Path) -> None:
    """角落小图必须被等比放大并居中：10mm 方块 → 宽 170mm、X 边距 10/180、垂直居中。"""
    from hutuji_mcp import gcode_gen

    svg = tmp_path / "tiny.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 190 277">'
        '<path d="M5 5 L15 5 L15 15 L5 15 Z" fill="none" stroke="black"/></svg>',
        encoding="utf-8",
    )
    gcode = tmp_path / "tiny.gcode"
    gcode_gen.svg_to_gcode(svg, gcode)

    min_x, min_y, max_x, max_y = _gcode_xy_bbox(gcode)
    assert min_x == pytest.approx(gcode_gen.LAYOUT_MARGIN_MM, abs=0.5)
    assert max_x == pytest.approx(gcode_gen.MAX_X_MM - gcode_gen.LAYOUT_MARGIN_MM, abs=0.5)
    assert max_x - min_x == pytest.approx(170.0, abs=1.0), "小图必须被放大"
    assert (min_y + max_y) / 2 == pytest.approx(gcode_gen.MAX_Y_MM / 2, abs=1.0)


def test_svg_to_gcode_downscales_oversized_drawing(tmp_path: Path) -> None:
    """超出边距的大图必须被等比缩小：200×270 矩形 → 收进 10mm 边距。"""
    from hutuji_mcp import gcode_gen

    svg = tmp_path / "big.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 270">'
        '<path d="M0 0 L200 0 L200 270 L0 270 Z" fill="none" stroke="black"/></svg>',
        encoding="utf-8",
    )
    gcode = tmp_path / "big.gcode"
    gcode_gen.svg_to_gcode(svg, gcode)

    min_x, min_y, max_x, max_y = _gcode_xy_bbox(gcode)
    assert max_x <= gcode_gen.MAX_X_MM - gcode_gen.LAYOUT_MARGIN_MM + 0.5
    assert max_y <= gcode_gen.MAX_Y_MM - gcode_gen.LAYOUT_MARGIN_MM + 0.5
    assert min_x >= gcode_gen.LAYOUT_MARGIN_MM - 0.5
    assert min_y >= gcode_gen.LAYOUT_MARGIN_MM - 0.5


def test_svg_to_gcode_layout_handles_degenerate_line(tmp_path: Path) -> None:
    """零高度图形（一条横线）不得让归一化除零崩溃，且垂直居中。"""
    from hutuji_mcp import gcode_gen

    svg = tmp_path / "hline.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 190 277">'
        '<line x1="10" y1="100" x2="180" y2="100" stroke="black"/></svg>',
        encoding="utf-8",
    )
    gcode = tmp_path / "hline.gcode"
    gcode_gen.svg_to_gcode(svg, gcode)

    min_x, min_y, max_x, max_y = _gcode_xy_bbox(gcode)
    assert (min_y + max_y) / 2 == pytest.approx(gcode_gen.MAX_Y_MM / 2, abs=1.0)


def test_gwrite_profile_matches_production_shape() -> None:
    """生产 profile 形状锁定（docs/protocol.md §5，逐字对齐奎享唯一产物）。

    上一条管线回归把 subprocess.run 整个 mock 掉，假产物的头是随手写的最小
    合法 G-code，锁不住真实 profile；真 vpype 只在 scripts/e2e_local.py 跑，
    且只断言「有行数」。故这里直接对 hutuji_gwrite.toml 做字面量断言。
    """
    import tomllib

    from hutuji_mcp import gcode_gen

    toml_path = Path(gcode_gen.__file__).parent / "hutuji_gwrite.toml"
    profile = tomllib.loads(toml_path.read_text(encoding="utf-8"))["gwrite"]["hutuji"]

    assert profile["unit"] == "mm"
    assert profile["vertical_flip"] is True
    # 头：G21/G90/F8000 + 两次幂等抬笔（不建立 Z 基准，G92 由 S3 旁路发送）
    assert profile["document_start"] == "G21\nG90\nF8000\nG1G90 Z0.0F10000\nG1G90 Z0.0F10000\n"
    assert profile["document_start"].count("G1G90 Z0.0F10000") == 2
    # 空走 G0 F8000 → 落笔 Z5；画线 G1 F8000；抬笔 Z0
    assert profile["segment_first"] == "G0 X{x:.3f}Y{y:.3f}F8000\nG1G90 Z5.0F10000\n"
    assert profile["segment"] == "G1 X{x:.3f}Y{y:.3f}F8000\n"
    assert profile["line_end"] == "G1G90 Z0.0F10000\n"
    # 收尾只抬笔：奎享的 G90G0 X0Y0 在本机会被判为换纸触发，禁止照抄
    assert profile["document_end"] == "G1G90 Z0.0F10000\n"
    assert "G0" not in profile["document_end"], "收尾不得有 G0 空走"
    for key in ("document_start", "segment_first", "segment", "line_end", "document_end"):
        assert "G92" not in profile[key], f"{key} 不得含 G92（校验器会拒）"
        assert "M" not in profile[key], f"{key} 不得含 M 码（笔控纯 Z 运动）"
        assert "X0Y0" not in profile[key].replace(" ", ""), f"{key} 不得回原点"


# ---- §5 校验器绕过回归（2026-07-26 深度评审）----
# 这些写法曾全部被放行：指令识别依赖空格切分，坐标正则不认 `1e3` / `.5`,
# 且 G2/G3 圆弧不限幅。校验器是下发前最后一道闸，不能只对 vpype 当前输出成立。


@pytest.mark.parametrize(
    "line, reason",
    [
        ("G0 X0 Y0 F8000", "自动换纸"),
        ("G0X0Y0F8000", "自动换纸"),          # 无空格：整行曾是一个 token
        ("G90 G0 X0 Y0 F8000", "自动换纸"),    # 模态前缀：首个 token 曾是 G90
        ("g0x0y0f8000", "自动换纸"),          # 小写 + 无空格
        ("G0X0Y0Z0F8000", "笔控必须"),
        ("G28X1", "G28/G30"),
        ("G1 X1e3 Y5", "无法解析"),        # Grbl 不支持科学计数法，必须在云端拒绝
        ("G1 X.5e3 Y5", "无法解析"),       # 前导小数点 + 指数同样不准下发
        ("G1 X1e-1 Y5", "无法解析"),       # 旧校验器会当成 0.1，Grbl 会报 unused word
        ("G1 X.5e1 Y5", "无法解析"),       # 旧校验器会当成 5，Grbl 实际按 .5 解析
        ("G2 X999 Y999 I1 J1 F8000", "越界"),  # 圆弧曾完全不限幅
        ("G3 X5 Y999 I1 J1 F8000", "越界"),
        ("G1 X-5 Y5 F8000", "越界"),
        # 以下为 2026-07-26 二次评审新增（都是我引入 _WORD_RE 时的遗留缺陷）：
        ("G1E400 X5 Y5", "无法解析"),      # Grbl 不支持指数，必须拒绝而非漏出 OverflowError
        ("M1E400", "无法解析"),
        ("G1 X1E400 Y5", "无法解析"),
        ("G1 XNAN Y5", "无法解析"),       # NAN 曾让该轴坐标被静默丢弃 → 限幅失效
        ("G1 XINF Y5", "无法解析"),
        ("G1 X1.5.5 Y5", "无法解析"),     # 残缺写法曾被截成 1.5 静默放行
        ("G1 X Y5", "无法解析"),          # 缺值
        # 生产文件采用允许列表：单位/坐标系/停顿/笔控/机器坐标均拒绝
        ("G20", "允许 G0"),                # 英寸模式会把合法数值放大 25.4 倍
        ("G92 X10 Y10", "允许 G0"),        # 改变工件坐标系
        ("G53 G0 X10 Y10", "允许 G0"),     # 机器坐标覆盖
        ("G4 P0.2", "不允许"),             # G4/P 都不属于生产模板
        ("M3", "不允许 M 码"),             # 当前固件会驱动 Z 落笔
        ("M5", "不允许 M 码"),             # 当前固件会驱动 Z 抬笔
        ("!", "无法解析"),                  # 实时 hold 不得混入下载文件
        ("~", "无法解析"),                  # 实时 resume
        ("?", "无法解析"),                  # 实时 status
        ("%", "无法解析"),                  # 程序分隔符
        ("G1 ZNAN", "无法解析"),            # Z 非数值不得静默忽略
        ("F1E400", "无法解析"),             # Grbl 不支持指数进给
        ("G1 Z999", "生产笔位"),            # 笔轴只允许 Z0/Z5
        ("F10001", "进给范围"),             # 当前生产上限 Z 轴 F10000
        ("F0", "进给范围"),                 # F=0 不合法，§5 要求 0<F≤10000
        ("F-1", "进给范围"),                # 负进给不合法
        ("G1 X10 Y10 F0", "进给范围"),      # F0 在运动指令中
        # 换纸/程序结束 M 指令必须拦截
        ("M30", "换纸/结束"),             # 程序结束触发自动换纸
        ("M2", "换纸/结束"),              # 程序结束
        ("M701", "换纸/结束"),            # 换纸系统指令
        ("M704", "换纸/结束"),
        ("M711", "换纸/结束"),
        ("M712", "换纸/结束"),
        ("M713", "换纸/结束"),
        ("M721", "换纸/结束"),
        ("G1 X50 Y50 M30", "换纸/结束"), # M30 粘在运动指令后面
    ],
)
def test_gcode_rejects_bypass_variants(line: str, reason: str) -> None:
    with pytest.raises(GcodeError, match=reason):
        validate_gcode(_minimal_gcode(line + "\n"))

@pytest.mark.parametrize(
    "line",
    [
        "G0 X0 Y0 X5 Y5",  # Grbl error:25；last-wins 会绕过 G0 原点禁令
        "G1 G1 X5 Y5",     # 同一 modal group 重复命令，Grbl error:20
        "G1 X5 Y5 Z0 Z5",  # 同一 value word 重复，Grbl error:25
    ],
)
def test_gcode_rejects_words_repeated_in_one_block(line: str) -> None:
    with pytest.raises(GcodeError, match="重复"):
        validate_gcode(_minimal_gcode(line + "\n"))


@pytest.mark.parametrize(
    "line",
    [
        "G1 X5 Y5 I0 J0",
        "G0 X5 Y5 R5",
        "G90 I1",
    ],
)
def test_gcode_rejects_arc_words_outside_arc_motion(line: str) -> None:
    with pytest.raises(GcodeError, match="圆弧参数"):
        validate_gcode(_minimal_gcode(line + "\n"))


@pytest.mark.parametrize(
    "line, reason",
    [
        ("G2 X120 Y100", "圆弧参数"),
        ("G2 I10 J0", "X/Y"),
    ],
)
def test_gcode_rejects_incomplete_arc_blocks(line: str, reason: str) -> None:
    text = _minimal_gcode("G0 X100 Y100 F8000\n" + line + "\n")
    with pytest.raises(GcodeError, match=reason):
        validate_gcode(text)


def test_gcode_rejects_mixed_radius_and_offset_arc_words() -> None:
    text = _minimal_gcode("G0 X100 Y100 F8000\nG2 X120 Y100 R10 I10 J0 F8000\n")
    with pytest.raises(GcodeError, match="R.*I/J|I/J.*R"):
        validate_gcode(text)


def test_gcode_rejects_degenerate_radius_mode_arc() -> None:
    """正 R 近整圆弧（起终点近乎重合）必须拒：它会把包围盒退化成弦点而绕过限幅。

    实证缺陷：I/J 模式下 `G2 X100.0001 Y5 I0 J95` 被包围盒判据拦住（X 凸到 195），
    但 R 模式 `G2 X100.0001 Y5 R95` 因解出的圆心贴在弦中点旁、正 R 取 minor 一侧
    圆心，包围盒退化成弦点 (100,5) 而被放行。vpype 生产管线实测 0 条真实圆弧，
    这种几何歧义没有合法来源，按 fail-closed 拒收（弦长 < _ARC_MIN_CHORD_MM）。
    """
    text = _minimal_gcode("G0 X100 Y5 F8000\nG2 X100.0001 Y5 R95 F8000\n")
    with pytest.raises(GcodeError, match="近乎重合|近整圆"):
        validate_gcode(text)


def test_gcode_accepts_legitimate_radius_mode_arc() -> None:
    """合法 R 模式圆弧（弦长充足、在界内）必须放行：负 R 绕远路一侧也在界内。

    钉死 _ARC_MIN_CHORD_MM 不误伤正常 R 弧。弦长 20mm 远大于 0.5mm 容差。
    """
    # 正 R minor
    stats = validate_gcode(_minimal_gcode("G0 X100 Y100 F8000\nG2 X120 Y100 R20 F8000\n"))
    assert stats.lines == 8
    # 负 R major（绕远路一侧），圆心仍在界内
    stats = validate_gcode(_minimal_gcode("G0 X100 Y100 F8000\nG2 X120 Y100 R-20 F8000\n"))
    assert stats.lines == 8


def test_gcode_rejects_radius_mode_arc_out_of_bounds() -> None:
    """负 R 绕远路大弧凸出限幅必须拒（弦长充足、但几何凸到界外）。


    补住 R 模式 major 弧的包围盒限幅路径——这条路径此前零测试。弦长 5mm 充足，
    负 R=40 让圆心落到弦左侧、major 弧向右凸到 X=222.5，越过 190 限幅。
    """
    text = _minimal_gcode("G0 X185 Y100 F8000\nG2 X180 Y100 R-40 F8000\n")
    with pytest.raises(GcodeError, match="包围盒"):
        validate_gcode(text)


def test_malformed_gcode_never_raises_non_gcode_error() -> None:
    """闸门只应抛 GcodeError；任何畸形输入都不得漏出 OverflowError/ValueError 等，
    否则会穿透 svg_to_gcode 变成 hutuji_draw 的未捕获 500。"""
    import re as _re
    for line in ["G1E400", "M9E999", "G1 X1E400 Y1E400", "G9999999999999999999 X5 Y5"]:
        try:
            validate_gcode(_minimal_gcode(line + "\n"))
        except GcodeError:
            pass  # 预期内的拒绝
        except Exception as exc:  # noqa: BLE001 —— 正是要证明这里抓不到东西
            raise AssertionError(f"{line!r} 漏出非 GcodeError 异常：{type(exc).__name__}: {exc}")


@pytest.mark.parametrize(
    "line",
    [
        # 生产 profile 的真实形状：X 与 Y 之间无空格、每行都带 F8000
        # （hutuji_gwrite.toml segment_first/segment；早先这两行写的
        #  F2500/F1500 是 profile 定稿前的旧口径，锁不住真实产物）
        "G0 X95.000Y175.000F8000",
        "G1 X100.000Y100.000F8000",
        "G0 X95.000 Y175.000 F8000",  # 带空格的等价写法同样必须放行
        "G1 X.5 Y.5 F8000",           # 前导小数点但在范围内
        "; comment",
        "; generated by vpype-gcode",  # 注释含 Y 字母，不得误拒
        "G1 X50 Y50 F8000 ; move to XY",  # 行内注释含 X/Y
    ],
)
def test_gcode_accepts_legit_lines(line: str) -> None:
    validate_gcode(_minimal_gcode(line + "\n"))


@pytest.mark.parametrize(
    "line",
    [
        "G0 X10 Y10 Z5 F8000",
        "G1 X10 Y10 Z5 F8000",
        "G0 X10 Y10",
        "G1 X10 Y10 F10000",
        "G1G90 Z5.0F8000",
        "Z0",
    ],
)
def test_gcode_rejects_nonproduction_motion_shape(line: str) -> None:
    with pytest.raises(GcodeError, match="笔控必须|XY 运动必须"):
        validate_gcode(_minimal_gcode(line + "\n"))


# 圆弧：端点在界内不代表整条弧在界内——弧中途会凸到象限点。
# 每例都先用 G0 给出起点，否则校验器会以「起点未知」拒绝。
def test_arc_bulge_outside_limit_is_rejected() -> None:
    """两端点都在界内，但 G2（顺时针）走上半圈，中途凸到 Y=280 > 277。
    只看端点会放行这条弧，实机就是撞机。"""
    text = _minimal_gcode("G0 X100 Y270 F8000\nG2 X120 Y270 I10 J0 F8000\n")
    with pytest.raises(GcodeError, match="圆弧 [XY] 包围盒"):
        validate_gcode(text)


def test_arc_fully_inside_limit_passes() -> None:
    # 同一条弧整体下移：凸点 Y=110，稳在界内
    text = _minimal_gcode("G0 X100 Y100 F8000\nG2 X120 Y100 I10 J0 F8000\n")
    stats = validate_gcode(text)
    assert stats.lines == 8


def test_arc_opposite_direction_bulges_the_other_way() -> None:
    """同样的起终点与 I/J，G3 走下半圈——凸向 Y=175，仍在界内。
    方向决定凸向，故限幅必须按实际扫过的角度区间算，不能取外接正方形。"""
    stats = validate_gcode(_minimal_gcode("G0 X100 Y185 F8000\nG3 X120 Y185 I10 J0 F8000\n"))
    assert stats.lines == 8


def test_arc_without_known_start_is_rejected() -> None:
    """圆弧的包围盒依赖起点；前面没有定位指令时必须拒，不能猜 (0,0)。"""
    with pytest.raises(GcodeError, match="缺少已知起点"):
        validate_gcode(_minimal_gcode("G2 X50 Y50 I5 J5 F8000\n"))


def test_arc_with_inconsistent_radius_is_rejected() -> None:
    """I/J 给出的圆心到起点、终点半径不一致 —— Grbl 自身也会报 invalid target
    （`GCode.cpp` delta_r > 0.5mm）。校验器须在落笔前拦住，否则包围盒本身就失真。

    起点不能用 (0,0)：那会先撞上 G0 X0 Y0 换纸禁令，测不到圆弧这条。"""
    with pytest.raises(GcodeError, match="半径不自洽"):
        validate_gcode(_minimal_gcode("G0 X10 Y10 F8000\nG2 X100 Y100 I1 J1 F8000\n"))


def test_near_full_circle_arc_checks_all_quadrants() -> None:
    """#8 浮点精度：起终点几乎重合的圆弧应按整圆算包围盒，不能退化为点。
    圆心 (100,100)，半径 95 → X 最大 195 > 190（X 限幅），必须拦住。"""
    text = _minimal_gcode("G0 X100 Y5 F8000\nG2 X100.0001 Y5 I0 J95 F8000\n")
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(text)


def test_coordinate_upper_bound_uses_eps_not_1mm() -> None:
    """#9 容差对称：190.5mm 不应放行（旧 +1.0mm 容差会放行）。"""
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(_minimal_gcode("G1 X190.5 Y5 F8000\n"))


def test_coordinate_at_boundary_passes() -> None:
    """X=190 和 Y=277 恰好在各自界内。"""
    validate_gcode(_minimal_gcode("G1 X190 Y277 F8000\n"))


@pytest.mark.parametrize(
    "body",
    [
        "G1 X190.0001 Y5 F8000\n",
        "G1 X5 Y277.0001 F8000\n",
        "G1 X-0.0001 Y5 F8000\n",
        "G1 X5 Y-0.0001 F8000\n",
    ],
)
def test_explicit_coordinate_outside_hard_boundary_is_rejected(body: str) -> None:
    """§5 的显式 X/Y 是硬边界；几何浮点容差不得放大可下发画幅。"""
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(_minimal_gcode(body))


@pytest.mark.parametrize("z", ["0.0001", "-0.0001", "4.9999", "5.0001"])
def test_explicit_z_requires_exact_production_pen_position(z: str) -> None:
    """Z 只允许精确 0/5；容差会把非生产笔位送给 Grbl。"""
    with pytest.raises(GcodeError, match="只允许 Z0/Z5"):
        validate_gcode(_minimal_gcode(f"G1G90 Z{z}F10000\n"))


def test_y_over_277mm_rejected() -> None:
    """Y=277.5 超出 Y 轴限幅 277mm。"""
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(_minimal_gcode("G1 X5 Y277.5 F8000\n"))


@pytest.mark.parametrize(
    ("form", "body"),
    [
        ("独立行", "G0 X10 Y10 F8000\nG91\nG1 X5 Y5 F8000\n"),
        ("小写", "G0 X10 Y10 F8000\ng91\nG1 X5 Y5 F8000\n"),
        ("同行混排", "G0 X10 Y10 F8000\nG1G91 X5 Y5 F8000\n"),
    ],
)
def test_mid_file_g91_is_rejected(form: str, body: str) -> None:
    """相对模式必须整类拒绝——理由是**撞机**，不只是圆弧起点失真。

    本机 `custom_3axis_hr4988.h` 把 `X/Y/Z_LIMIT_PIN` 三个全部注释掉，且
    `$20=0`（软限位关）、`$22=0`（无归零）——Grbl 完全不知道任何轴的物理边界。
    相对模式下更没有「绝对目标」可供限幅比较：`G91` + `G1 X10` 反复执行会让 X
    每轮 +10mm 一路顶到 `$130=210` 的机械死点。2026-08-07 用户在手动串口片段上
    实测到这一现象（`MPos.X` 从 0 走到 10.000，反复跑即累积）。

    故 `0~190/0~277` 的绝对限幅**只在全程 G90 时成立**，`G91` 是限幅的整体旁路，
    必须由指令白名单在文件层拒死。三种绕过形态都要钉住：小写与同行混排若漏，
    白名单形同虚设。
    """
    with pytest.raises(GcodeError, match="G91"):
        validate_gcode(_minimal_gcode(body))


@pytest.mark.parametrize(
    "text",
    [
        "; G21\nG20\nG90\nG1 X8 Y5\n",   # 注释伪造毫米模式，实际英寸
        "G210\nG900\nG1 X10 Y10\n",       # 子串伪造 G21/G90
        "G21.1\nG90\nG1 X10 Y10\n",      # 非整数码不得截断成 G21
        "G21\nG90.1\nG1 X10 Y10\n",      # 非整数码不得截断成 G90
        "G21\nX10 Y10\nG90\n",          # 运动前尚未建立绝对坐标模式
    ],
)
def test_gcode_requires_exact_g21_g90_before_motion(text: str) -> None:
    with pytest.raises(GcodeError, match="§5 文件头"):
        validate_gcode(text)


def test_g0_with_unknown_modal_axis_is_rejected() -> None:
    """首个 G0 只给 X 时 Y 未知，不能证明实际终点不是触发换纸的 (0,0)。"""
    with pytest.raises(GcodeError, match="完整 XY|自动换纸"):
        validate_gcode(_minimal_gcode("G0 X0 F8000\n"))


def test_production_modal_axis_only_line_is_limited() -> None:
    """固定头已建立 G1；后续裸轴字仍必须执行画幅限幅。"""
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(_minimal_gcode("X500 Y5 F8000\n"))


def test_production_header_establishes_g1_before_axis_only_line() -> None:
    """固定头的两次 G1 抬笔已把模态切到 G1；裸 X0Y0 不走 G0 换纸路径。"""
    validate_gcode(_minimal_gcode("X0 Y0 F8000\n"))


def test_explicit_g1_to_origin_is_still_allowed() -> None:
    """只拦 G0 语义的 (0,0)：显式 G1 走到 (0,0) 是普通画线，不走换纸路径。"""
    validate_gcode(_minimal_gcode("G1 X0 Y0 F8000\n"))


def test_inherited_modal_g1_axis_only_line_is_limited() -> None:
    """显式 G1 后的裸轴字仍是 G1，不能因本行没有 G 字而绕过限幅。"""
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(_minimal_gcode("G1 X100 Y100 F8000\nX500 Y500 F8000\n"))


def test_inherited_modal_g0_origin_is_rejected() -> None:
    """G0 X0 继承上一行 Y=0，实际落点是 (0,0)，会触发固件自动换纸。"""
    with pytest.raises(GcodeError, match="自动换纸"):
        validate_gcode(_minimal_gcode("G1 X50 Y0 F8000\nG0 X0 F8000\n"))


def test_inherited_modal_arc_checks_bounding_box() -> None:
    """首个 G2 半径 10mm 合法；裸轴字继承 G2 后半径 20mm 凸到 Y=280，必须拒。"""
    text = _minimal_gcode(
        "G0 X100 Y260 F8000\nG2 X120 Y260 I10 J0 F8000\nX160 Y260 I20 J0 F8000\n"
    )
    with pytest.raises(GcodeError, match="圆弧 [XY] 包围盒"):
        validate_gcode(text)


def test_pen_lift_counts_glued_and_padded_forms() -> None:
    # 抬笔 = Z 回 0：紧凑与带空格的纯 G1G90 Z0F10000 都要计数；
    # 落笔 Z5 与不含 Z 的画线行不算。生产 profile 不用 G0 Z0：G0 实际终点
    # 会继承未知 XY，无法排除固件回原点换纸触发。
    stats = validate_gcode(
        _minimal_gcode(
            "G1G90 Z0.0F10000\nG1 G90 Z0.000 F10000\nG1G90 Z0F10000\n"
            "G1G90 Z5.0F10000\nG1 X10.000Y10.000F8000\n"
        )
    )
    assert stats.pen_lifts == 6


@pytest.mark.parametrize(
    "token",
    [
        "xDNlk73FAJubla7zE_HDGg",   # 下划线在尾部
        "_iIT-11Xvigmh8ODcPG20Q",   # 下划线在首位
        "w2PMOJF_LKJ6XT-EB6DVxg",   # 下划线在中间
        "SlIdJ_-X4lLi25a4F1oR2g",   # 下划线紧跟连字符
    ],
)
def test_redaction_never_leaks_token_containing_underscore(token: str) -> None:
    """token 用 URL-safe base64，含 `_` 是常态（约 29%）。
    脱敏必须按结构切，不能靠最后一个下划线，否则 status 会回显 token 前缀。"""
    name = f"draw_20260726_010101_{token}.gcode"
    redacted = server._redact_capability_name(name)
    assert redacted == "draw_20260726_010101_*.gcode"
    assert token not in redacted
    # 逐段确认没有残留任何非平凡 token 片段
    for size in range(4, len(token) + 1):
        assert token[:size] not in redacted


def test_redaction_preserves_extension_and_rejects_unknown_names() -> None:
    base = "draw_20260726_010101_" + "a" * 22
    assert server._redact_capability_name(f"{base}.svg").endswith("_*.svg")
    assert server._redact_capability_name(f"{base}.gcode").endswith("_*.gcode")
    # 不符合 capability 命名的一律退化为通配，不回显原名
    for bad in ("../../etc/passwd", "draw_1_2_short.gcode", "random.gcode"):
        assert server._redact_capability_name(bad) == "*.gcode"


def test_public_download_template_requires_https_and_loopback_http_origin() -> None:
    root = Path(__file__).resolve().parents[2]
    env_template = (root / "deploy" / "env.mcp.example").read_text(encoding="utf-8")
    nginx = (root / "deploy" / "nginx-hutuji.conf").read_text(encoding="utf-8")

    assert "HUTUJI_PUBLIC_BASE_URL=https://" in env_template
    assert "HUTUJI_PUBLIC_BASE_URL=http://" not in env_template

    port_80 = nginx[nginx.index("    listen 80;") :]
    files_location = port_80[
        port_80.index("    location /files/ {") : port_80.index("    location / {")
    ]

    # `deny all;` 在本块内出现两次：ACL 的兜底 deny，和 `limit_except GET HEAD { deny
    # all; }` 行内那条。旧断言写成 `"deny all;" in files_location`，删掉兜底 deny 后
    # limit_except 那行仍能满足它——此时 nginx 只剩两条 allow、默认放行，明文 :80
    # /files 对全公网可读，而本测试照样绿（已实测：删该行后这两个测试 2 passed）。
    # 故必须剔除 limit_except 行，只在纯 ACL 语句里找独立成行的兜底 deny。
    acl_lines = [
        line.strip()
        for line in files_location.splitlines()
        if "limit_except" not in line and line.strip()
    ]
    assert "deny all;" in acl_lines, acl_lines

    # nginx allow/deny 按出现顺序匹配：兜底 deny 排到 allow 之前，cloudflared 的
    # loopback 回源会被先拒，公网经隧道下载全挂。顺序也要钉。
    deny_at = acl_lines.index("deny all;")
    allow_v4_at = acl_lines.index("allow 127.0.0.1;")
    allow_v6_at = acl_lines.index("allow ::1;")
    assert allow_v4_at < deny_at, acl_lines
    assert allow_v6_at < deny_at, acl_lines


def test_nginx_tunnel_rate_limit_and_logs_use_real_client_ip() -> None:
    """Tunnel 回源都来自 127.0.0.1，不能让全体公网用户共享一个限速桶。

    `CF-Connecting-IP` 只可信于 cloudflared 的 loopback 回源；公网直连 :80 可伪造
    任意请求头，故必须先按连接源分流。头缺失时回退 `$remote_addr`，保持非空 key，
    不能让 nginx 按「空 key 不计数」规则静默关闭限速。
    """
    root = Path(__file__).resolve().parents[2]
    nginx = (root / "deploy" / "nginx-hutuji.conf").read_text(encoding="utf-8")

    assert "map $http_cf_connecting_ip $hutuji_cf_client_ip {" in nginx
    assert re.search(
        r"map \$http_cf_connecting_ip \$hutuji_cf_client_ip \{\s*"
        r'"" \$remote_addr;\s*default \$http_cf_connecting_ip;\s*\}',
        nginx,
    ), "CF 头缺失必须回退连接源，不能产生不计数的空 key"
    assert re.search(
        r"map \$remote_addr \$hutuji_client_ip \{\s*"
        r"default \$remote_addr;\s*127\.0\.0\.1 \$hutuji_cf_client_ip;\s*"
        r"::1 \$hutuji_cf_client_ip;\s*\}",
        nginx,
    ), "只有 loopback cloudflared 回源可信 CF-Connecting-IP"

    assert "limit_req_zone $hutuji_client_ip zone=hutuji_files:10m rate=10r/s;" in nginx
    assert "limit_req_zone $binary_remote_addr zone=hutuji_files:" not in nginx
    assert "proxy_set_header X-Real-IP $hutuji_client_ip;" in nginx
    assert "proxy_set_header X-Forwarded-For $hutuji_client_ip;" in nginx
    assert "log_format hutuji_client '$hutuji_client_ip " in nginx
    assert nginx.count("access_log /var/log/nginx/hutuji-access.log hutuji_client;") == 2


def test_nginx_http_redirect_target_is_not_attacker_controlled() -> None:
    """:80 的兜底 301 若用 `$host`，而两个 listen 都没有 default_server，任意 Host 头都
    落到本 server 块并被原样回显进 Location——变成开放重定向。目标必须是字面量。"""
    root = Path(__file__).resolve().parents[2]
    nginx = (root / "deploy" / "nginx-hutuji.conf").read_text(encoding="utf-8")

    redirects = [
        line.strip()
        for line in nginx.splitlines()
        if "return 301" in line and not line.lstrip().startswith("#")
    ]
    assert redirects
    for line in redirects:
        assert "$host" not in line, line
        assert "$http_host" not in line, line
        assert re.search(r"return 301 https://[A-Za-z0-9.-]+\$request_uri;", line), line


def test_preflight_checks_active_nginx_and_complete_download_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")

    assert "env_value()" in preflight
    assert "while IFS= read -r line" in preflight
    assert "source /etc/hutuji/mcp.env" not in preflight
    assert "eval " not in preflight
    # TUNNEL_HOST 的取值仍用大小写混合类；check_public_base_url 先 ${1,,} 转小写再匹配。
    assert "^https://([A-Za-z0-9-]+\\.)*[A-Za-z0-9-]+(:443)?/?$" in preflight
    assert "^https://([a-z0-9-]+\\.)*[a-z0-9-]+(:443)?/?$" in preflight
    assert "[[ -f /etc/hutuji/mcp.env ]]" in preflight
    assert "check_bridge_env()" in preflight
    assert "env_value /etc/hutuji/bridge.env MCP_ENDPOINT" in preflight
    assert "MCP_ENDPOINT 为 wss://" in preflight
    assert "from urllib.parse import urlsplit" in preflight
    assert "parsed.username is None" in preflight
    assert "parsed.password is None" in preflight
    assert "systemctl is-active --quiet hutuji-cloud-bridge" in preflight
    # 部署模板必须来自固定安装目录；从任意位置调用 preflight 都应比较同一份模板。
    assert "SCRIPT_DIR" not in preflight
    assert "/opt/hutuji/deploy/nginx-hutuji.conf" in preflight
    assert '"$d/nginx-hutuji.conf"' not in preflight
    assert '"$d/preflight.sh"' not in preflight
    assert "check_no_placeholder()" in preflight
    assert "check_public_base_url()" in preflight
    assert "safe_output_dir()" in preflight
    assert preflight.count('safe_output_dir "$OUTPUT_DIR"') == 1
    # 不锚固定条数，锚不变量：每个把 OUTPUT_DIR 取进 $d 的子壳都必须先过 safe_output_dir，
    # 否则该子壳会在未校验的路径上写文件/删文件。
    assert preflight.count('d="$OUTPUT_DIR"') >= 1
    assert preflight.count('safe_output_dir "$d"') == preflight.count('d="$OUTPUT_DIR"')
    assert "EXPECTED_CHECKS=" in preflight

    # 只有 pass / fail 两种状态。曾有 OPTIONAL_SKIP(125)：判据缺前提时返回它、计为
    # skip，而放行算术是 (PASS+SKIP)==EXPECTED_CHECKS——skip 与 pass 完全等价，于是
    # 「公网访问 :80 /files 被拒」可以从未被验证过、脚本照样打印 15/15 全绿（实测
    # pass=14 fail=0 skip=1 输出 PASS）。计数器与放行算术里都不得再出现 skip。
    counters = re.findall(r"^([A-Z][A-Z0-9_]*)=\d+$", preflight, re.MULTILINE)
    assert not [name for name in counters if "SKIP" in name], counters
    # 只查代码行：脚本顶部保留了一段解释 OPTIONAL_SKIP 为何被删的注释，那是防复发的
    # 说明，不是机制本身。
    preflight_code = "\n".join(
        line for line in preflight.splitlines() if not line.lstrip().startswith("#")
    )
    assert "SKIP" not in preflight_code
    assert re.search(r'"?\$\{?PASS\}?"?\s+-eq\s+"?\$\{?EXPECTED_CHECKS\}?"?', preflight)
    assert re.search(r'"?\$\{?FAIL\}?"?\s+-eq\s+0', preflight)

    assert "nginx -T" in preflight
    assert '"$(readlink -f /etc/nginx/sites-enabled/hutuji)" == "/etc/nginx/sites-available/hutuji"' in preflight
    assert '"%08x" % (zlib.crc32' in preflight
    assert "print(f" not in preflight
    # 行为不变量，不钉转义风格：ACL 判据必须是具名函数 + 探针，且 denied 子判据必须汇入总判据。
    assert "check_acl_response()" in preflight
    assert "probe_non_loopback_ip()" in preflight
    assert 'check_acl_response "$TUNNEL_HOST"' in preflight
    assert '"$get" == "404" && "$post" == "403" && "$mcp" == "404"' in preflight
    assert '"$denied" == "403"' in preflight
    # 兜底行为必须验证状态码和明文分支的精确 HTTPS 目标，不能只凭 301 状态报绿。
    assert 'location="$(printf' in preflight
    assert '"$location" != "https://${TUNNEL_HOST}/zzz-preflight-probe"' in preflight
    assert 'Location ${location}' in preflight
    # loopback 三项必须先 fail-closed；取不到非 loopback IPv4 时整个判据 fail（不得跳过，
    # 见上方 skip 注释），而不是只跳过后半个 denied 探针。
    loopback_result = preflight.index(
        '[[ "$get" == "404" && "$post" == "403" && "$mcp" == "404" ]] || return 1'
    )
    optional_probe = preflight.index('ip="$(probe_non_loopback_ip || true)"')
    fail_probe = preflight.index("判 fail（不得跳过）")
    assert loopback_result < optional_probe < fail_probe
    assert '"$code" == "200"' in preflight
    # 两条探针路径（hostname -I / 默认路由接口地址）都必须滤掉 loopback，否则 denied
    # 探到 127.x 会被 `allow 127.0.0.1` 放行，把「非 loopback 拒绝」验成假绿。
    assert "/^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$/" in preflight
    assert preflight.count("!~ /^127\\./") == 2
    assert preflight.count('\ncheck "') == 23
    # EXPECTED_CHECKS 必须与真实 check 条数一致：放行判据是 PASS==EXPECTED_CHECKS，
    # 两者对不上时要么永远不可能放行，要么少验几项也能算全绿。
    expected = re.search(r"^EXPECTED_CHECKS=(\d+)$", preflight, re.MULTILINE)
    assert expected is not None
    assert int(expected.group(1)) == preflight.count('\ncheck "')

    # 每条 curl 都必须绕代理 + 带超时：环境里有 http_proxy 会让 loopback 探针走代理假红，
    # 而 fallback 选中的接口地址若被 DROP 式防火墙吃掉，默认超时约 300s 会把冒烟挂死。
    # 只认命令替换里的真调用，末尾提示行里的「公网 curl /mcp 须失败」不是调用。
    curls = [line for line in preflight.splitlines() if "$(curl " in line]
    assert len(curls) >= 6
    for line in curls:
        assert '--noproxy "*"' in line, line
        assert "--max-time" in line, line


def test_preflight_invokes_every_check_after_env_init() -> None:
    """顶层 check 若在 TUNNEL_HOST/OUTPUT_DIR 赋值之前调用，`set -u` 会在第一条语句就
    `unbound variable` 退出，23 项一个都不会跑——脚本连体检结果都产不出。用行序钉死。"""
    root = Path(__file__).resolve().parents[2]
    lines = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8").splitlines()

    init_last = max(
        i
        for i, line in enumerate(lines)
        if line.startswith(("TUNNEL_HOST=", "OUTPUT_DIR=", "BRIDGE_ENDPOINT="))
    )
    checks = [i for i, line in enumerate(lines) if line.startswith('check "')]
    assert len(checks) == 23
    assert min(checks) > init_last


def test_preflight_exercises_real_generation_chain() -> None:
    """生成链与位图描摹链都必须真跑，不能用人工拼的 G-code 文本冒充。

    旧版 check_download 用 `printf "G21\\nG90\\nF8000\\n"` 造产物——那份内容连
    validate_gcode 自己都不接受（实测「第 4 行缺少 §5 文件头」），所以只证明了
    nginx 能按字节发文件，完全没碰生成链。已实测复现：把 hutuji_gwrite.toml 移走后
    `import hutuji_mcp.server` 仍成功、服务保持 active、/mcp 与 /files 照常响应，
    而真实 svg_to_gcode() 报「vpype 转换失败（exit 2）: --config … does not exist」，
    即「全绿」与「一画就整单失败」可以同时成立。位图路线同理：skimage/sknw/imageio
    是延迟 import，缺失只能在真实 trace 时暴露，故必须有独立的离线 bitmap self-test。
    """
    root = Path(__file__).resolve().parents[2]
    preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")
    preflight_code = "\n".join(
        line for line in preflight.splitlines() if not line.lstrip().startswith("#")
    )

    # 生成链必须被真的调用，且经过 §5 校验器（svg_to_gcode 内部即 validate_gcode）。
    assert "check_generate_chain()" in preflight
    assert "check_generate_chain" in preflight_code
    assert preflight_code.count("from hutuji_mcp.gcode_gen import svg_to_gcode") == 2
    assert preflight_code.count("svg_to_gcode(Path(sys.argv[1]), Path(sys.argv[2]))") == 2
    # 空产物不算通过：行数/字节必须为正。
    assert preflight_code.count("stats.lines > 0 and stats.bytes > 0") == 2

    # 必须用生产 venv 的解释器：vpype/vpype-gcode 只装在 venv，且 _vpype_cmd() 按
    # sys.executable 同目录定位 vpype——换成系统 python3 就换掉了被验对象。
    assert "VENV_PY=/opt/hutuji/mcp-server/.venv/bin/python" in preflight_code
    assert preflight_code.count('[[ -x "$VENV_PY" ]] || exit 1') == 3

    # 下载契约必须验的是真产物：printf 拼 G-code 的老写法不得复活。
    assert 'printf "G21' not in preflight_code
    assert "G1G90" not in preflight_code
    for marker in ('"$code" == "200"', "cmp -s", "zlib.crc32"):
        assert marker in preflight, marker

def test_preflight_exercises_real_bitmap_trace_chain() -> None:
    """preflight 必须走与 unit 相同的 bitmap_svg --self-test 入口。"""
    root = Path(__file__).resolve().parents[2]
    preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")
    preflight_code = "\n".join(
        line for line in preflight.splitlines() if not line.lstrip().startswith("#")
    )

    assert "check_bitmap_chain()" in preflight
    assert "check_bitmap_chain" in preflight_code
    assert (
        'PYTHONPATH=/opt/hutuji/mcp-server "$VENV_PY" '
        "-m hutuji_mcp.bitmap_svg --self-test"
    ) in preflight_code
    # 退出码必须真实回显：`if ! out="$(...)"` 形态下 `!` 已消费命令替换的退出状态，
    # 其后 `$?` 恒为 0（本机 bash 5 实测，探针在 results/_rc_probe.sh）——「回显
    # 真实退出码」会永远打印 exit=0，等于没回显。该形态不得复活。
    assert 'if ! out="$(' not in preflight_code
    assert "|| rc=$?" in preflight_code
    # 数值栈卡死（死锁、OOM 前长时间挂住）时 preflight 不得无限等；124=超时靠
    # timeout 提供，退出码单独成行先打就是为了让这三类失败可区分。
    assert "timeout 60 env" in preflight_code
    assert 'printf \'%s\\n\' "$out" | tail -1' in preflight_code
    # 自检成功行里的模块路径必须断言落在安装目录：venv 若另有一份 site-packages
    # 装的 hutuji_mcp，preflight 的 PYTHONPATH 版与 unit 开机走的就是两份代码，
    # 「真跑当前落盘的代码」失效。
    assert "^bitmap self-test OK module=" in preflight_code
    assert "/opt/hutuji/mcp-server/*" in preflight_code


def test_preflight_chain_subprocesses_all_run_under_timeout() -> None:
    """三条真跑子进程的链（生成链 / 位图描摹链 / 下载探针造产物）必须同构套 timeout 60。

    f25d8d1 只给 check_bitmap_chain 加了 timeout 与真实退出码回显；生成链与下载
    探针造产物这两处同样真跑 vpype，挂死时（被沙箱 SIGSYS 咬死是已实测出现过的
    形状）preflight 会无限等。三条链必须逐一钉死同一形态：
    `out="$(timeout 60 …)" || rc=$?`，失败时退出码单独成行先打
    （124=超时 / 128+N=被信号杀死）——`if ! out=$(...)` 形态下 `$?` 恒为 0，
    退出码回显永久失真，全仓禁止该形态。
    """
    root = Path(__file__).resolve().parents[2]
    preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")
    preflight_code = "\n".join(
        line for line in preflight.splitlines() if not line.lstrip().startswith("#")
    )

    for func in ("check_generate_chain() (", "check_bitmap_chain() (", "check_download() ("):
        start = preflight.index(func)
        body = preflight[start : preflight.index("\n)", start)]
        assert "timeout 60" in body, func
        assert "|| rc=$?" in body, func
        assert "rc=0" in body, func
        assert "if (( rc != 0 ))" in body, func
        assert "124=超时" in body, func
        assert "128+N=被信号杀死" in body, func
        assert 'printf \'%s\\n\' "$out" | tail -1' in body, func

    # 全脚本再钉一次：`if ! out=$(...)` 不得在任何一处复活（不只这三条链）。
    assert 'if ! out="$(' not in preflight_code


def test_preflight_mcp_env_rejects_blank_image_model() -> None:
    """HUTUJI_IMAGE_MODEL 显式置空是 fail-open：闸必须落在有 env 可读的 preflight 层。

    自检保持封闭、不读环境（bitmap_svg._run_self_test 的 docstring 解释了为什么不能
    由自检代劳：preflight 以 root 直跑且刻意不 source mcp.env，自检读 settings 会让
    健康机器过不了体检）。凭据类检查全部归 check_mcp_env：键缺省放行（config.py 有
    内置默认值），显式出现而空白才判红。
    """
    root = Path(__file__).resolve().parents[2]
    preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")
    preflight_code = "\n".join(
        line for line in preflight.splitlines() if not line.lstrip().startswith("#")
    )

    assert "check_image_model_shape()" in preflight_code
    assert "env_value /etc/hutuji/mcp.env HUTUJI_IMAGE_MODEL" in preflight_code
    # 闸必须串进 check_mcp_env，否则函数写了也不跑。
    assert "check_image_model_shape &&" in preflight_code
    # 缺省不算缺、显式空白才红：判据必须是「键出现 → 值非空白」，不是「值必须存在」。
    assert "HUTUJI_IMAGE_MODEL[[:space:]]*=" in preflight_code


def test_preflight_pins_running_systemd_units_to_templates() -> None:
    """`is-active` 不证明跑的是哪一版 unit——加固项可全部缺失而照样 active。

    此前只有「运行中 nginx conf 与模板一致」这一项，两个 unit 却没有对应判据，
    而它们的安装方式完全同构（`install-from-repo.sh` 从 `/opt/hutuji/deploy/`
    复制到 `/etc/systemd/system/`）。unit 里的 `--host 127.0.0.1`、
    `ProtectSystem=strict`、`SystemCallFilter` 等指令一旦漂移，监听面还有第 5/6
    项兜着，加固项则无人验证。

    静态四条判据缺一不可：md5 一致（堵手改/漏同步）、`NeedDaemonReload=no`（systemd
    用加载时快照，忘了 reload 时磁盘一致而在跑的是旧配置）、`FragmentPath` 指向
    `/etc/systemd/system`（排除 `/lib` 残留）、无 drop-in（drop-in 能静默覆盖
    ExecStart 与全部加固项，存在时前三条什么都证明不了）。此外必须验证 MCP 的
    ExecStartPre 实际成功，并拒绝早于本次部署文件的旧主进程。
    """
    root = Path(__file__).resolve().parents[2]
    preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")
    preflight_code = "\n".join(
        line for line in preflight.splitlines() if not line.lstrip().startswith("#")
    )

    assert "check_unit_matches_template()" in preflight
    assert "check_units_match_templates" in preflight_code
    # 两个 unit 都必须被覆盖，漏一个等于半边无判据。
    for name in ("hutuji-mcp", "hutuji-cloud-bridge"):
        assert f"check_unit_matches_template {name}" in preflight_code, name

    # 模板路径固定在安装目录，与 nginx 那项同源。
    assert '/opt/hutuji/deploy/${name}.service' in preflight_code
    assert '/etc/systemd/system/${name}.service' in preflight_code
    # 行尾归一化后比 md5：scp 带进 CRLF 不该判红。
    assert preflight_code.count('tr -d "\\r"') >= 3
    # md5 一致这条判据本身必须在（只断言 `tr -d` 与路径不够：删掉比较行后
    # 两者都还在，实测该变异未被捕获）。nginx 那项与 unit 这项各一条。
    assert preflight_code.count('[[ -n "$a" && "$a" == "$b" ]]') == 2

    # 四条判据逐条钉死，任何一条被删都必须让本测试红。
    assert '[[ "$need" == "no" ]] || return 1' in preflight_code
    assert "NeedDaemonReload" in preflight_code
    assert '[[ "$frag" == "$inst" ]] || return 1' in preflight_code
    assert "FragmentPath" in preflight_code
    # drop-in 用文件系统 glob 判定：`DropInPaths` 在老版 systemd 上返回空串，
    # 只信它就是 fail-open。
    assert 'compgen -G "$dir/${name}.service.d/*.conf"' in preflight_code
    assert "DropInPaths" not in preflight_code
    # drop-in 搜索面必须覆盖 systemd 真实读取的四个层级。
    for d in ("/etc/systemd/system", "/run/systemd/system", "/usr/lib/systemd/system"):
        assert d in preflight_code, d

    # daemon-reload 与 enable --now 都不会重启已 active 的服务。必须读取运行时
    # ExecStartPre 结果，并用主进程启动时间覆盖本次 unit/代码/profile 的 mtime。
    assert "check_mcp_prestart_and_restart()" in preflight
    assert "systemctl show hutuji-mcp -p ExecStartPre --value" in preflight_code
    assert 'while IFS= read -r line' in preflight_code
    assert 'saw_gcode=0 saw_bitmap=0' in preflight_code
    assert '*"hutuji_mcp.gcode_gen --self-test"*)' in preflight_code
    assert '*"hutuji_mcp.bitmap_svg --self-test"*)' in preflight_code
    assert '[[ "$line" == *"code=exited"* && "$line" == *"status=0"* ]]' in preflight_code
    assert '(( saw_gcode == 1 ))' in preflight_code
    assert '(( saw_bitmap == 1 ))' in preflight_code
    assert "ExecMainStartTimestamp --value" in preflight_code
    assert 'date -d "$started" +%s' in preflight_code
    # PREFLIGHT-P2-01：mtime 比较必须解引用符号链接。裸 `stat -c %Y` 取到的是
    # `ln -sfn` 的时刻而非配置/代码本体，故 `/etc/hutuji/mcp.env` 或 unit 是链接时，
    # 轮换 LLM_API_KEY 却不 restart 也能过「主进程不早于本次部署」。同文件的 nginx
    # 项 2026-08-08 已用 -L，本项此前没跟上。两侧都钉：新写法在、旧写法不在。
    assert 'stat -L -c %Y "$path"' in preflight_code
    assert 'stat -c %Y "$path"' not in preflight_code
    # deployed_files 数组结构检查：不能只判路径字符串是否出现在文件中（会被注释
    # 或其他用途骗过），必须验证数组声明的完整结构。
    import re
    array_match = re.search(
        r'local -a deployed_files=\(\s*(.*?)\s*\)',
        preflight_code,
        re.DOTALL
    )
    assert array_match, "deployed_files 数组声明未找到"
    array_content = array_match.group(1)
    for deployed in (
        "/etc/systemd/system/hutuji-mcp.service",
        "/opt/hutuji/deploy/hutuji-mcp.service",
        "/etc/hutuji/mcp.env",
        "/opt/hutuji/mcp-server/hutuji_mcp/hutuji_gwrite.toml",
    ):
        assert deployed in array_content, f"{deployed} 不在 deployed_files 数组内"
    # PREFLIGHT-P2-02：.py 不再写进数组字面量，改 find 递归收集。bash 的 `*.py`
    # 只展开一层，新增子包（hutuji_mcp/routes/*.py）的 mtime 就完全不参与比较。
    assert "/opt/hutuji/mcp-server/hutuji_mcp/*.py" not in array_content
    assert "collect_deployed_py" in preflight_code
    assert 'deployed_files+=("${DEPLOYED_PY[@]}")' in preflight_code
    assert "-type f -name '*.py' -print0" in preflight_code
    assert "(( start_epoch < newest ))" in preflight_code
    assert "check_mcp_prestart_and_restart" in preflight_code

    # 条数也必须是显式判据：systemd 的多条目单行渲染是内部格式而非契约，若某版把
    # 两条 ExecStartPre 拼到同一行，逐条判就退化成整段子串匹配（那行同时含
    # code=exited 与 status=0 即全绿）。格式漂移必须是红不是绿。
    assert "records=$(( records + 1 ))" in preflight_code
    assert "(( records == 2 ))" in preflight_code
    # 顺序钉死：位图自检先于生成链自检出现，说明 unit 两条被对调、模板已漂移。
    assert "ExecStartPre 顺序与模板不一致" in preflight_code


def test_preflight_pins_running_nginx_to_deployed_config() -> None:
    """「配置文件与模板一致」证不到「在跑的 nginx 读了它」。

    nginx 的 reload 有一条对三道常规检查全隐形的静默失败路径：`nginx -t` 起的是新
    进程、没有继承的共享内存区，故必过；`systemctl reload` 走 `nginx -s reload`，
    只发信号不等受理，恒 `status=0/SUCCESS`；master 拒绝新配置时只在 error.log 写
    `[emerg]` 并**继续用旧配置服务**，`is-active` 照样 active。2026-08-08 生产实测：
    限速 key 从 `$binary_remote_addr` 改为 `$hutuji_client_ip` 后 reload 被
    `[emerg] limit_req "hutuji_files" uses the "$hutuji_client_ip" key while
    previously it used the "$binary_remote_addr" key` 拒绝，旧配置又服务了 37 分钟，
    而当时 19 项 preflight **全绿**——其余判据在旧配置下同样成立。

    故必须同时有行为判据与进程判据：行为判据（标记探针落进 `hutuji_client` 专用
    日志）在「旧配置恰好也含同名 access_log」时会假绿，进程判据补住；而进程判据的
    mtime 可被 `touch` 干扰，故不能只留一条。
    """
    root = Path(__file__).resolve().parents[2]
    preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")
    preflight_code = "\n".join(
        line for line in preflight.splitlines() if not line.lstrip().startswith("#")
    )

    # 两项都必须存在且被注册成 check，否则写了也不跑。
    assert "check_nginx_running_config_is_live()" in preflight
    assert "check_nginx_workers_fresh()" in preflight
    assert 'check "在跑的 nginx 确实是本次配置' in preflight
    assert 'check "nginx worker 不早于配置 mtime 且启动后无 [emerg]"' in preflight

    # 行为判据：loopback 探针带唯一标记，且必须在专用日志里找到它。
    assert "/var/log/nginx/hutuji-access.log" in preflight_code
    assert 'marker="hutuji-preflight-live-$$-$(date +%s)"' in preflight_code
    assert '-A "$marker" http://127.0.0.1/files/__nonexistent__' in preflight_code
    assert 'grep -F "$marker" "$log"' in preflight_code
    # 读不到日志必须判 fail，不得当作「无法验证」放过。
    assert '[[ ! -r "$log" ]]' in preflight_code
    # 行首必须是限速身份 + via=$remote_addr：缺 via= 说明跑的不是这一版日志格式。
    assert '"$line" != "127.0.0.1 via=127.0.0.1 "*' in preflight_code

    # 进程判据：worker 不得早于配置 mtime。stat 必须 -L——sites-enabled/hutuji 是
    # `ln -sfn` 建的符号链接，不解引用会读到建链时刻（实测差整整一天）。
    assert 'stat -L -c %Y "$conf"' in preflight_code
    assert "stat -c %Y /etc/nginx/sites-enabled/hutuji" not in preflight_code
    # master 不换、worker 才换，故必须排除 master 进程。
    assert "grep -qs 'master process' \"/proc/$pid/cmdline\" && continue" in preflight_code
    assert "(( epoch + 1 < mtime ))" in preflight_code
    # 找不到 worker 是 fail，不是 pass。
    assert '[[ -z "$oldest" ]]' in preflight_code

    # [emerg] 基准必须取最老 worker 启动时刻：用配置 mtime 会让已被 restart 修好的
    # 历史 [emerg] 永久判红。
    assert 'since="$(date -d "@$oldest"' in preflight_code
    assert '/\\[emerg\\]/ {ts = $1 " " $2; if (ts >= s) print}' in preflight_code
    assert "/var/log/nginx/error.log" in preflight_code


def test_mcp_unit_self_tests_vpype_inside_sandbox_and_only_allows_mbind() -> None:
    """服务启动必须在真实 seccomp 下跑 vpype，且 @resources 只放行其必需的 mbind。"""
    root = Path(__file__).resolve().parents[2]
    unit = (root / "deploy" / "hutuji-mcp.service").read_text(encoding="utf-8")

    prestarts = [line for line in unit.splitlines() if line.startswith("ExecStartPre=")]
    assert prestarts == [
        "ExecStartPre=/opt/hutuji/mcp-server/.venv/bin/python -m hutuji_mcp.gcode_gen --self-test",
        "ExecStartPre=/opt/hutuji/mcp-server/.venv/bin/python -m hutuji_mcp.bitmap_svg --self-test",
    ]
    assert "SystemCallFilter=~@privileged @resources @obsolete" not in unit
    assert "SystemCallFilter=~@privileged @obsolete" in unit
    denied_resources = next(
        line for line in unit.splitlines()
        if line.startswith("SystemCallFilter=~ioprio_set ")
    ).split("=~", 1)[1].split()
    assert "mbind" not in denied_resources
    assert set(denied_resources) == {
        "ioprio_set", "migrate_pages", "move_pages", "nice", "sched_setaffinity",
        "sched_setattr", "sched_setparam", "sched_setscheduler", "set_mempolicy",
        "set_mempolicy_home_node", "setpriority", "setrlimit",
    }


def test_mcp_unit_caps_memory_so_oom_stays_in_this_cgroup() -> None:
    """必须设 MemoryMax：无上限时内存尖峰交由内核 OOM killer 全机选受害者。

    生产实测（2026-08-11）：总内存 3911MB、**无 swap**，本服务 cgroup 历史峰值
    174.5MB，原 MemoryMax=infinity。没有本条时一次内存尖峰可能被内核以
    oom_score 判给 nginx / cloud-bridge / sshd，把「一次生成失败」放大成
    「机器失联」。本条只钉「有上限且量级合理」，不钉具体数值以外的策略——
    真正的防线是 bitmap_svg._reject_oversized_png，本条是最后一道。
    """
    root = Path(__file__).resolve().parents[2]
    unit = (root / "deploy" / "hutuji-mcp.service").read_text(encoding="utf-8")
    caps = [l for l in unit.splitlines() if l.startswith("MemoryMax=")]
    assert len(caps) == 1, f"MemoryMax 必须恰好一条，当前 {len(caps)}"
    value = caps[0].split("=", 1)[1].strip()
    assert value != "infinity", "MemoryMax=infinity 等于没设"
    # 只接受带单位的显式量级；裸数字（字节）极易少写几个零而静默把服务锁死。
    assert value.endswith(("M", "G")), f"MemoryMax 必须带 M/G 单位，当前 {value!r}"
    mib = int(value[:-1]) * (1024 if value.endswith("G") else 1)
    # 下界：必须显著高于实测历史峰值 174.5MB，否则正常出图会被 cgroup 杀掉。
    assert mib >= 512, f"MemoryMax={value} 低于实测峰值的安全倍数，正常出图会被杀"
    # 上界：超过整机 3911MB 的一半就失去「给 nginx/sshd 留余量」的意义。
    assert mib <= 1946, f"MemoryMax={value} 超过整机一半，兜底失去意义"


def test_bridge_unit_caps_memory_and_keeps_hardening() -> None:
    """bridge unit 的内存兜底与加固键必须在场。

    此前本 unit **完全没有形状测试**：2026-08-11 给它补的 ProcSubset=pid 当时
    也没留钉子，删掉不会红。本条与 MCP 侧的 MemoryMax 判据同口径。

    取值依据（生产实测 2026-08-11）：整机 3911MB 无 swap，本服务 cgroup 峰值
    59.7MB（只中转 JSON-RPC，曲线平坦），进程树含 spawn 的 mcp-proxy。
    要防的是 mcp-proxy 子进程僵死/堆积（0.12.0 有社区稳定性报告）在无上限时
    挤垮整机，而内核按 oom_score 选受害者——可能是 nginx 或 sshd 而非肇事者。
    """
    root = Path(__file__).resolve().parents[2]
    unit = (root / "deploy" / "hutuji-cloud-bridge.service").read_text(encoding="utf-8")

    caps = [l for l in unit.splitlines() if l.startswith("MemoryMax=")]
    assert len(caps) == 1, f"MemoryMax 必须恰好一条，当前 {len(caps)}"
    value = caps[0].split("=", 1)[1].strip()
    assert value != "infinity", "MemoryMax=infinity 等于没设"
    assert value.endswith(("M", "G")), f"MemoryMax 必须带 M/G 单位，当前 {value!r}"
    mib = int(value[:-1]) * (1024 if value.endswith("G") else 1)
    # 下界：须显著高于实测峰值 59.7MB，并容得下重连期两个 mcp-proxy 短暂并存
    # （约 114MB + bridge 约 30MB ≈ 144MB），否则正常重连会被 cgroup 杀掉。
    assert mib >= 192, f"MemoryMax={value} 容不下 mcp-proxy 重连期并存，会误杀"
    # 上界：本服务峰值仅 60MB，给太宽就抓不住泄漏——它是本条唯一要防的东西。
    assert mib <= 512, f"MemoryMax={value} 远超本服务需要，抓不住 mcp-proxy 泄漏"

    # OOM 被杀后必须能自愈，且不得无限重启刷日志（这两键分属不同段，易写错段）。
    assert "Restart=on-failure" in unit
    assert "StartLimitIntervalSec=300" in unit
    assert "StartLimitBurst=5" in unit
    # 与 MCP 侧同口径的加固键：此前无任何测试覆盖，删掉不会红。
    for key in (
        "ProtectProc=invisible",
        "ProcSubset=pid",
        "ProtectSystem=strict",
        "CapabilityBoundingSet=",
        "SystemCallFilter=~@privileged @resources @obsolete",
    ):
        assert key in unit, f"bridge unit 缺加固键 {key}"


def test_gcode_module_self_test_runs_real_generation_chain() -> None:
    gcode_gen._run_self_test()
    assert gcode_gen._main(["--self-test"]) == 0
    assert gcode_gen._main([]) == 2
def test_bitmap_module_self_test_runs_offline_trace_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self-test 不得调用计费生图 API，且必须真走 PNG→描摹→SVG 闸门。"""
    from hutuji_mcp import bitmap_svg

    async def forbidden_network_call(*_args, **_kwargs):
        raise AssertionError("bitmap self-test 不得调用生图 API")

    monkeypatch.setattr(bitmap_svg, "_fetch_image_png", forbidden_network_call)
    polys, width, height = bitmap_svg.trace_bitmap_to_polylines(
        bitmap_svg._encode_probe_png()
    )
    # 探针边长必须与生图输出同量级（_IMAGE_SIZE）：小探针几乎走不到大数组/多线程
    # 路径，对沙箱漏项（seccomp 放行缺失）的覆盖只是碰运气。
    probe_edge = int(bitmap_svg._IMAGE_SIZE.split("x", 1)[0])
    assert (width, height) == (probe_edge, probe_edge)
    assert len(polys) == 3
    assert all(len(poly) >= bitmap_svg._MIN_POLY_PX for poly in polys)
    assert all(len(poly) >= bitmap_svg._SMOOTH_WINDOW for poly in polys)
    assert bitmap_svg._main(["--self-test"]) == 0
    assert bitmap_svg._main([]) == 2


def test_bitmap_self_test_never_reads_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """自检必须保持封闭：把 settings 换成「任何属性访问都炸」的对象仍须退 0。

    回归钉（7aab460）：自检曾读 settings.llm_api_key，而 preflight 的
    check_bitmap_chain 以 root 直跑、刻意不 source mcp.env，健康机器因此
    过不了体检。本钉保证自检路径完全不碰配置。
    """
    from hutuji_mcp import bitmap_svg

    class _ExplodingSettings:
        def __getattr__(self, name: str) -> None:
            raise AssertionError(f"bitmap self-test 不得读 settings.{name}")

    monkeypatch.setattr(bitmap_svg, "settings", _ExplodingSettings())
    assert bitmap_svg._main(["--self-test"]) == 0


def test_bitmap_self_test_prints_preflight_contract_line(
    capsys: pytest.CaptureFixture,
) -> None:
    """成功行是生产契约：preflight 的 check_bitmap_chain 按
    `^bitmap self-test OK module=` grep 它，格式一改生产体检必红。"""
    from hutuji_mcp import bitmap_svg

    assert bitmap_svg._main(["--self-test"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("bitmap self-test OK module=")


def test_bitmap_missing_dependency_error_keeps_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """依赖自身抛裸 ImportError 时，错误不能退化成「依赖缺失（None）」。"""
    import builtins

    from hutuji_mcp import bitmap_svg

    real_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("imageio"):
            raise ImportError("simulated imageio ABI failure")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with pytest.raises(bitmap_svg.BitmapError) as caught:
        bitmap_svg.trace_bitmap_to_polylines(b"")
    message = str(caught.value)
    assert "simulated imageio ABI failure" in message
    assert "依赖缺失（None）" not in message


def test_bitmap_missing_dependency_abi_value_error_keeps_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ABI 失配的经典报错是 ValueError 而非 ImportError，宽 catch 必须同样兜住。

    旧代码只 catch ImportError 时，`ValueError: numpy.dtype size changed` 会
    裸穿出模块（调用方只准备接 BitmapError，变成 500），「装 requirements.txt」
    这句唯一可行动的诊断随之丢失。上面那条用例只抛 ImportError，旧代码也能过。
    """
    import builtins

    from hutuji_mcp import bitmap_svg

    real_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("skimage"):
            raise ValueError(
                "numpy.dtype size changed, may indicate binary incompatibility. "
                "Expected 96 from C header, got 88 from PyObject"
            )
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with pytest.raises(bitmap_svg.BitmapError) as caught:
        bitmap_svg.trace_bitmap_to_polylines(b"")
    message = str(caught.value)
    assert "ValueError" in message
    assert "numpy.dtype size changed" in message


def test_deploy_guidance_explicitly_restarts_active_application_services() -> None:
    root = Path(__file__).resolve().parents[2]
    install = (root / "deploy" / "install-from-repo.sh").read_text(encoding="utf-8")
    readme = (root / "deploy" / "README.md").read_text(encoding="utf-8")

    restart = "systemctl restart hutuji-mcp hutuji-cloud-bridge"
    assert restart in install
    assert restart in readme
    assert "enable --now hutuji-mcp hutuji-cloud-bridge nginx" not in install
    assert "enable --now hutuji-mcp hutuji-cloud-bridge nginx" not in readme


def test_xiaozhi_knowledge_base_uses_protocol_paper_change_timing() -> None:
    root = Path(__file__).resolve().parents[2]
    knowledge = (root / "docs" / "xiaozhi-agent" / "knowledge-base.md").read_text(
        encoding="utf-8"
    )

    assert "换纸（约 10 秒）" not in knowledge
    assert "通常需要 60 秒以上" in knowledge
    assert "90 秒换纸应答上限" in knowledge


def test_install_script_keeps_package_index_tls_verification() -> None:
    root = Path(__file__).resolve().parents[2]
    install = (root / "deploy" / "install-from-repo.sh").read_text(encoding="utf-8")

    # 注释里会正面写出被禁的写法（如「不用 `bash -lc`」），断言一律只看代码行。
    install_code = "\n".join(
        line for line in install.splitlines() if not line.lstrip().startswith("#")
    )

    assert "--trusted-host" not in install_code
    assert "HUTUJI_PIP_CERT" in install
    assert re.search(r'pip_args\+=\(--cert "\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"\)', install_code)

    # 不得用 `bash -lc`：`-l` 会 source root 的登录期配置，其中导出的 PIP_TRUSTED_HOST /
    # PIP_EXTRA_INDEX_URL / PIP_INDEX_URL 静默生效，而脚本只传 -i 与可选 --cert，覆盖
    # 不了它们——上面「禁止绕过 TLS 校验」的承诺被环境推翻，PIP_EXTRA_INDEX_URL 更直接
    # 打开依赖投毒面。安装行为必须只由本脚本参数决定，故显式清掉 PIP_* 与代理变量。
    assert not re.search(r"bash\s+-[a-z]*l[a-z]*c", install_code)
    for cleared in (
        "PIP_TRUSTED_HOST",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_CONFIG_FILE",
        "PIP_NO_INDEX",
        "PIP_FIND_LINKS",
        "https_proxy",
        "HTTPS_PROXY",
    ):
        assert f"-u {cleared}" in install_code, cleared

def _load_agent_gate():
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "scripts" / "agent_gate.py"
    spec = importlib.util.spec_from_file_location("hutuji_agent_gate_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "text",
    [
        "当前采用 USB Host 作为板间链路：CH9350 已否决。",
        "当前采用 USB Host 作为板间链路（CH9350 已否决）。",
        "当前采用 USB Host 作为板间链路——CH9350 已否决。",
        "当前采用 USB Host 作为板间链路、CH9350 已否决。",
    ],
)
def test_gate_rejection_clause_does_not_borrow_another_options_rejection(text: str) -> None:
    """另一选项的否决语境不能掩盖当前 USB Host 复活。"""
    gate = _load_agent_gate()
    match = gate.REVIVED_PATTERNS[0][1].search(text)
    assert match is not None

    clause = gate._rejection_clause(text, match)

    assert "USB Host" in clause
    assert "CH9350" not in clause
    assert "已否决" not in clause


def test_gate_prior_rejection_does_not_mask_later_revival(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _load_agent_gate()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "agent-handoff.md").write_text(
        "USB Host 已否决，当前采用 USB Host 作为板间链路。", encoding="utf-8"
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    assert gate.check_revived_decisions()["status"] == "fail"


def test_gate_accepts_same_clause_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _load_agent_gate()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "agent-handoff.md").write_text(
        "当前禁止采用 USB Host 作为板间链路。", encoding="utf-8"
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    assert gate.check_revived_decisions()["status"] == "pass"

def test_gate_accepts_forbidden_list_heading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _load_agent_gate()
    (tmp_path / "AGENTS.md").write_text(
        "- **禁止重开**：合并固件 / USB Host / CH9350 / 阿里云部署。", encoding="utf-8"
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    assert gate.check_revived_decisions()["status"] == "pass"


@pytest.mark.parametrize(
    "rel",
    [
        "README.md",
        "AGENTS.md",
        "CLAUDE.md",
        "docs/agent-handoff.md",
        "docs/release-readiness.md",
        "docs/worktree-inventory.md",
        ".cursor/rules/hutuji-agent-rails.mdc",
    ],
)
def test_gate_forbidden_claims_scans_every_governance_doc(
    rel: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLAIMS_FORBIDDEN 此前只用于「与白名单比对是否漂移」和打印报告末尾那句
    「仍受 claims_forbidden 约束」，**没有任何一层扫过文档正文里是否使用了禁语**。
    实测在 README.md 追加「硬件已验证，联调完成，现已可出货」，docs 层七项全绿并
    照常打印那句约束提示——门禁列出禁语却不执行。逐份钉死每个治理文档都被扫。"""
    gate = _load_agent_gate()
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("本机已完成全部验证，现已可出货。", encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    result = gate.check_forbidden_claims()

    assert result["status"] == "fail", result
    assert rel in result["detail"]


@pytest.mark.parametrize(
    "body",
    [
        # 免责句：否定词与禁语隔着书名号并列项
        "本条**不支持**「联调完成」「可出货」「硬件已验证」任何一项宣称。",
        # ≠ 链式：否定语义只由符号承载
        "写了说明 ≠ 代码已改 ≠ 硬件已验 ≠ 可出货。",
        "勾选 ≠ 可出货；出货仍看 release-readiness.md。",
        # 「不证明：」+ 顿号并列
        "不证明：§9 已烧录、M2 完成、硬件联调、京东云上线、可出货。",
        # inline code 引述（规则条文自身）
        "禁止宣称：`可出货` / `商业放行` / `硬件已验证`。",
        # 表格定义列
        "| 可出货 / 商业放行 | bringup 0–8 有设备证据 + 生产/合规签核 |",
        # Markdown 标题 + 空行 + 并列清单（.cursor 硬轨的排版）
        "## 禁止宣称（除非 release-readiness 对应行已 ✅）\n\n可出货 / §9 已完成 / 硬件已验证",
        # 重叠窗口：禁语 `可出货` 是 `不可出货` 的子串，否定词 `不可` 与它共享一个字。
        # 若按「禁语起点之前」切窗会把 `不可` 拦腰截断，本例即误判判红。
        "> **不可出货**",
        # 「不提升/不升级 X 状态」= release-readiness 每条实机记录的收尾免责句式。
        # 缺这两词时，凡在免责句里写全禁语字面量的条目一律误判判红——2026-08-07 实测
        # 「本条不提升 M2、联调、硬件已验证或可出货状态」被判红两处，逼作者改写措辞绕开。
        "本条不提升 M2、联调、硬件已验证或可出货状态。",
        # 只含「不升级」：不得再带「不构成」等词表已有的否定词，否则本例会被它们
        # 放行而钉不住「不升级」本身（实测原写法在无修复时照样 pass = 弱测试）。
        "本条不升级 硬件已验证 状态。",
        # 反引号举例：元语境导语在 `:` 之前，`_claim_sentence` 遇 `:` 截断把导语切掉。
        # 须取「行首到命中处 + claim_sentence」才能覆盖（2026-08-07 E5 实测修复）。
        "滥用形态举例：`现已可出货，后续不升级。`",
        "2026-08-07 实测 `硬件已验证、可出货` 在整句口径下 pass：",
    ],
)
def test_gate_forbidden_claims_allows_quoting_the_rule(
    body: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """禁语出现在「讲规则」「免责」「引述」语境里必须放行，否则门禁会把自己的
    规则条文判红，逼人删掉免责句——那是比漏报更坏的结果。"""
    gate = _load_agent_gate()
    (tmp_path / "README.md").write_text(body, encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    assert gate.check_forbidden_claims()["status"] == "pass", body


@pytest.mark.parametrize(
    "body",
    [
        # 免责词落在禁语**后面**：前半句已经把话说出去了，不得被后半句的否定词挡箭。
        "现已可出货，后续不支持。",
        "硬件已验证、可出货，本条不构成 M2 结论。",
        "现已可出货，后续不升级。",
        "硬件已验证、可出货，本条不提升 M2 状态。",
    ],
)
def test_gate_forbidden_claims_rejects_trailing_disclaimer(
    body: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """否定词只在禁语**之前**才算免责语境。

    整句口径下，`_CLAIM_QUOTE_RE` 会被反过来当挡箭牌：句尾补一个 `不支持`／`不升级`
    就能让前半句的裸宣称整句放行。2026-08-07 实测四例在整句口径下全部 pass。
    收紧为「只看禁语之前的半句」后全部判红，且当时的 17 份治理文档零新误报
    （真实免责句式的否定词一律在禁语之前）。
    """
    gate = _load_agent_gate()
    (tmp_path / "README.md").write_text(body, encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    assert gate.check_forbidden_claims()["status"] == "fail", body



@pytest.mark.parametrize(
    "body",
    [
        # AG-F1: 空格分隔
        "本机验证通过，现已 可 出货。",
        "§9  已  完成",
        "硬件 已 验证",
        # 换行分隔
        "联调\n完成",
        "京东云已\n上线",
        # 制表符
        "M2\t已\t完成",
        # 混合空白
        "商业  \n\t 放行",
    ],
)
def test_gate_forbidden_claims_catches_whitespace_bypass(
    body: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""AG-F1 收口：精确匹配可被 `可 出货` / `可\n出货` 绕过。

    修复后先删掉禁语自身的空白，再在字符间插 `\s*` 构造模糊模式。
    这样 `§9 已\n完成` 与 `§9  已  完成` 都会命中 `§9 已完成`。
    """
    gate = _load_agent_gate()
    (tmp_path / "README.md").write_text(body, encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    result = gate.check_forbidden_claims()

    assert result["status"] == "fail", f"应拦截空白分隔的禁语: {body!r}"
    assert "README.md" in result["detail"]


def test_gate_forbidden_claims_whitespace_respects_quote_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""AG-F1 模糊模式不得误判引述语境。

    `禁止宣称：\`可\n出货\`` 里的命中跨行，反引号奇偶必须按段落计数才数得对；
    按行计数会漏掉命中前几行的反引号，把这句判据表述自己判红。
    """
    gate = _load_agent_gate()
    body = "禁止宣称：`可\n出货` / `硬件 已 验证`。"
    (tmp_path / "README.md").write_text(body, encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    assert gate.check_forbidden_claims()["status"] == "pass", body


@pytest.mark.parametrize(
    "body",
    [
        "结论：`硬件已验证`，可以发货。",
        "- 当前状态 `联调完成`",
        "本机实测：`可出货`。",
    ],
)
def test_gate_forbidden_claims_backtick_still_needs_meta_context(
    body: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反引号本身不是豁免：裸宣称套反引号仍须判红（2026-08-07 加固）。

    AG-F1 把反引号奇偶计数的起点从行首改到段首以支持跨行命中，这里钉死该改动
    **没有**顺手把元语境要求一起去掉——反引号在本仓是「引用术语」的常规排版，
    不含否定语义，仅靠它逃逸等于把整层还原成无条件放行。
    """
    gate = _load_agent_gate()
    (tmp_path / "README.md").write_text(body, encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    assert gate.check_forbidden_claims()["status"] == "fail", body

@pytest.mark.parametrize(
    ("rel", "body"),
    [
        ("CLAUDE.md", "板间链路当前采用 USB Host 直连。"),
        ("docs/protocol.md", "现行板间方案改用 CH9350 桥接。"),
        ("docs/release-readiness.md", "生产部署落点为阿里云 ECS。"),
        (".cursor/rules/hutuji-agent-rails.mdc", "板间通讯采用原生 USB Host。"),
    ],
)
def test_gate_revived_scan_covers_all_governance_docs(
    rel: str, body: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """三条 REVIVED_PATTERNS 原各带一份手写文件清单，合计只覆盖 5 个文件，漏掉的
    恰是优先级最高的几份：CLAUDE.md（对 agent 是 OVERRIDE 级指令）、.cursor 硬轨
    （alwaysApply）、release-readiness.md（进度真值）；`ch9350` 规则连 protocol.md
    都不扫。实测把「当前采用 USB Host，已改用 CH9350，生产落点阿里云」写进
    CLAUDE.md，门禁全绿。现在三条规则共用 GOVERNANCE_DOCS。"""
    gate = _load_agent_gate()
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    assert gate.check_revived_decisions()["status"] == "fail", (rel, body)


@pytest.mark.parametrize(
    ("layer", "body"),
    [
        # 同一行里前半句的禁令，管的是它自己的宾语，管不到冒号后另起的主张。
        ("revived", "禁止在出图中发 M 命令：板间链路改用 USB Host 直连。"),
        ("claims", "禁止跳过 bringup-sop：本机硬件已验证。"),
        # 禁令清单只掩护「列举否决项」的并列项，掩护不了混进清单里的采纳句。
        ("revived", "## 禁止重开\n\n- 阿里云\n- 蓝牙数据链路\n- 现已改用 USB Host 直连"),
        # 标题名自带否决词，其下清单却在讲采纳。
        ("revived", "## 已否决方案的替代实现\n\n- 采用 CH9350 桥接替代"),
        # 引用块不是引述形态：`>` 里照样能做主张。
        ("revived", "> 板间链路采用 USB Host。"),
    ],
)
def test_gate_rejection_heading_does_not_shield_adoption_claims(
    layer: str,
    body: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """否决语境豁免不得沦为「附近有禁令词就全绿」。

    `_has_rejection_heading` 为放行仓内真实的并列免责清单而放宽过两次：先允许任意
    禁令词 + 冒号豁免整行余下内容，又让句窗跨过冒号去借否定词。这两处放宽实测各开出
    假绿——本参数化的 5 例当时全部全绿。判据的正确形状是「命中短语自身是否在做主张」
    （`_ADOPTION_RE`）加「否决词必须紧贴冒号、中间只容粗体与括号旁注」，而不是
    「上下文出现过否决词」。任何再次放宽都必须先让这 5 例保持判红。
    """
    gate = _load_agent_gate()
    target = tmp_path / "docs" / "agent-handoff.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body + "\n", encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "GOVERNANCE_DOCS", ["docs/agent-handoff.md"])
    checker = gate.check_forbidden_claims if layer == "claims" else gate.check_revived_decisions
    assert checker()["status"] == "fail", body


def test_gate_governance_docs_cover_repo_governance_surface() -> None:
    """新增治理文档若忘了加进 GOVERNANCE_DOCS，禁语与复活扫描都会绕过它——
    这正是本轮发现的原始缺陷形态。用必备文件清单交叉校验覆盖面。"""
    gate = _load_agent_gate()
    root = Path(__file__).resolve().parents[2]

    # GOVERNANCE_DOCS 里的每一项都必须真实存在，否则是笔误导致的静默跳过
    for rel in gate.GOVERNANCE_DOCS:
        assert (root / rel).is_file(), rel

    # 三条复活规则不再各带清单，一律走 GOVERNANCE_DOCS
    for _kid, _pattern, files in gate.REVIVED_PATTERNS:
        assert files is None, _kid

    # 关键治理文档不得漏（这几份是 agent 行为的直接约束源）
    for must in (
        "CLAUDE.md",
        "AGENTS.md",
        "README.md",
        ".cursor/rules/hutuji-agent-rails.mdc",
        "docs/agent-handoff.md",
        "docs/release-readiness.md",
        "docs/protocol.md",
        # runbook 决定「哪几步真机验证可以跳过」，是 agent 行为的直接约束源：
        # 它一旦写出「已完成/可跳过」，后续代理就不会再上真机复验。此前它不在
        # GOVERNANCE_DOCS，故禁语与复活扫描都绕过它——同一形态的原始缺陷。
        "docs/daytime-tuning-runbook.md",
    ):
        assert must in gate.GOVERNANCE_DOCS, must


def test_gate_doc_scan_surface_covers_every_governance_doc() -> None:
    """治理文档必须全部落在行号三层的扫描源里，否则在其中写过期行号无人可查。

    2026-08-11 实测：三层各自内联同一份 glob（`*.md` + `docs/**/*.md` +
    `*/README.md`），GOVERNANCE_DOCS 18 份里 `.cursor/rules/hutuji-agent-rails.mdc`
    恰好一份都不匹配——它是 Cursor 侧硬轨、且在 hub_safe_doc_globs 内可被 agent
    改写，却是三层唯一的盲区。用派生断言而非静态清单，管住下一次新增。
    """
    gate = _load_agent_gate()
    root = Path(__file__).resolve().parents[2]
    scanned = {path.relative_to(root).as_posix() for path in gate._doc_scan_files()}
    missing = [rel for rel in gate.GOVERNANCE_DOCS if rel not in scanned]
    assert not missing, f"治理文档不在行号扫描源内: {missing}"


def test_gate_doc_ref_normalize_preserves_leading_dot() -> None:
    """`lstrip("./")` 按字符集剥离，会吃掉 `.cursor/...` 的前导点；必须用 removeprefix。

    路径被改写后引用永远解析不到，只计入 skipped 而不判红——过期行号照样全绿。
    """
    gate = _load_agent_gate()
    assert (
        gate._normalize_doc_ref(".cursor/rules/hutuji-agent-rails.mdc")
        == ".cursor/rules/hutuji-agent-rails.mdc"
    )
    # `./` 前缀仍要剥掉：与旧行为一致，不能因为修 bug 把已登记的 key 全改一遍
    assert gate._normalize_doc_ref("./docs/protocol.md") == "docs/protocol.md"
    assert gate._normalize_doc_ref("docs\\protocol.md") == "docs/protocol.md"


def test_gate_doc_line_refs_judges_refs_written_in_cursor_rails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.cursor/rules/*.mdc` 里写的过期行号必须判红，不能因为它不在扫描源而漏掉。

    同时放一条有效引用，确保失败不是「一条都没解析到」那条兜底红。
    """
    gate = _load_agent_gate()
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "docs" / "refs.md").write_text("见 target.py:1\n", encoding="utf-8")
    (tmp_path / "target.py").write_text("only\n", encoding="utf-8")
    rails = tmp_path / ".cursor" / "rules"
    rails.mkdir(parents=True)
    (rails / "hutuji-agent-rails.mdc").write_text("见 target.py:99\n", encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    result = gate.check_doc_line_refs()
    assert result["status"] == "fail"
    assert "target.py:99" in result["detail"]
    assert ".cursor/rules/hutuji-agent-rails.mdc" in result["detail"]


def test_gate_doc_line_refs_judges_dot_prefixed_ref_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """引用目标本身以点开头（`.cursor/rules/x.mdc:99`）时必须判红而非静默跳过。"""
    gate = _load_agent_gate()
    (tmp_path / "docs").mkdir(parents=True)
    rails = tmp_path / ".cursor" / "rules"
    rails.mkdir(parents=True)
    (rails / "rails.mdc").write_text("单行\n", encoding="utf-8")
    (tmp_path / "target.py").write_text("only\n", encoding="utf-8")
    (tmp_path / "docs" / "refs.md").write_text(
        "有效: target.py:1\n过期: .cursor/rules/rails.mdc:99\n", encoding="utf-8"
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    result = gate.check_doc_line_refs()
    assert result["status"] == "fail"
    assert ".cursor/rules/rails.mdc:99" in result["detail"]


def test_gate_doc_ref_absolute_path_cannot_escape_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文档写绝对路径引用时，解析不得逃出本仓——否则整个门禁被 ValueError 打死。

    _DOC_REF_RE 的字符集不含 `:`，故 `D:/Users/xiaozhi-esp32/x.cc:45` 捕获到的是
    `/Users/xiaozhi-esp32/x.cc`；Windows 上 `ROOT / "/Users/…"` 保留盘符替换路径，
    该文件真实存在则被当成本仓命中，随后 relative_to(ROOT) 抛 ValueError——结果是
    「没有结论」而不是判红，比假绿更糟。本仓 docs/kxnx/findings/ 里确有这样一处引用。
    """
    gate = _load_agent_gate()
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "target.py").write_text("only\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "foreign.cc").write_text("a\nb\n", encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", repo)

    # 用与 repo 同根的绝对路径（同盘符），复现驱动器相对逃逸
    escape = "/" + outside.relative_to(outside.anchor).as_posix() + "/foreign.cc"
    assert gate._repo_file(escape) is None, "仓外路径不得被当成本仓命中"

    (repo / "docs" / "refs.md").write_text(
        f"有效: target.py:1\n仓外: {escape}:999\n", encoding="utf-8"
    )
    # 不得抛异常；仓外引用归「跳过」，有效引用照常校验
    result = gate.check_doc_line_refs()
    assert result["status"] == "pass", result
    assert "跳过 1 处" in result["detail"], result


def test_gate_required_files_derives_governance_docs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`check_required_files` 必须**在调用时**把 GOVERNANCE_DOCS 并进必备文件面。

    静态断言「两张清单包含关系成立」只能证明当下没漂，管不住下一次新增：加一份治理
    文档时若忘了同步 REQUIRED_FILES，那份文档在 `docs` 层（不跑 pytest）被删/改名后，
    禁语与复活两层只会 `continue` 静默跳过，而 forbidden_claims 的 pass 文案照打
    `len(GOVERNANCE_DOCS)`（2026-08-09 实测印「19 份均未做出」而实际只扫 18）。
    故并集必须在 `check_required_files` 里现算，让「治理文档必存在」成为结构不变量，
    而不是一条要靠人记得同步的清单。
    """
    gate = _load_agent_gate()
    monkeypatch.setattr(
        gate, "GOVERNANCE_DOCS", [*gate.GOVERNANCE_DOCS, "docs/brand-new-governance.md"]
    )

    result = gate.check_required_files()

    assert result["status"] == "fail", result
    assert "docs/brand-new-governance.md" in result["detail"], result


def test_gate_section9_rejects_marker_files_outside_grbl_repo(tmp_path: Path) -> None:
    gate = _load_agent_gate()
    for rel, needles, _ in gate.SECTION9_MARKERS:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(needles), encoding="utf-8")

    result = gate.check_section9(tmp_path)

    assert result["status"] == "fail"
    assert "AGENTS.md" in result["detail"]


def test_gate_rejects_claims_list_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _load_agent_gate()
    docs = tmp_path / "docs"
    docs.mkdir()
    allowlist = {
        "version": 1,
        "grbl_section9_only": ["x"],
        "grbl_core_headers_restore_only": ["x"],
        "grbl_forbidden_symbols": ["report_realtime_status"],
        "claims_forbidden": ["stale"],
    }
    (docs / "agent-edit-allowlist.json").write_text(
        __import__("json").dumps(allowlist), encoding="utf-8"
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    result = gate.check_edit_allowlist()

    assert result["status"] == "fail"
    assert "claims_forbidden" in result["detail"]


def test_gate_required_files_rejects_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = _load_agent_gate()
    target = tmp_path / "target.md"
    target.write_text("real", encoding="utf-8")
    link = tmp_path / "required.md"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"当前平台无权限创建 symlink：{exc}")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "REQUIRED_FILES", ["required.md"])

    result = gate.check_required_files()

    assert result["status"] == "fail"
    assert "required.md" in result["detail"]


def test_gate_external_refs_skip_unreadable_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """full 门禁不能把 pytest 缓存当治理文档读取并在权限错误时崩溃。"""
    gate = _load_agent_gate()
    cache = tmp_path / ".pytest_cache"
    cache.mkdir()
    cache_readme = cache / "README.md"
    cache_readme.write_text("cache", encoding="utf-8")
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):  # noqa: ANN002, ANN003
        if path == cache_readme:
            raise PermissionError("cache belongs to another account")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "_external_index", lambda: ({}, ["test-repo"], []))
    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    result = gate.check_external_doc_line_refs()

    assert result["status"] == "fail"
    assert "一条外仓引用都没解析到" in result["detail"]


def test_gate_external_refs_report_unreadable_governance_doc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真正的治理文档不可读时应返回结构化 fail，而不是抛出 traceback。"""
    gate = _load_agent_gate()
    docs = tmp_path / "docs"
    docs.mkdir()
    unreadable = docs / "bad.md"
    unreadable.write_text("Protocol.cpp:1", encoding="utf-8")
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):  # noqa: ANN002, ANN003
        if path == unreadable:
            raise PermissionError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "_external_index", lambda: ({}, ["test-repo"], []))
    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    result = gate.check_external_doc_line_refs()

    assert result["status"] == "fail"
    assert "无法读取治理文档 docs/bad.md（PermissionError）" in result["detail"]


def test_gate_doc_refs_report_unreadable_governance_doc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docs/hub 门禁同样要把治理文档读错收敛为结构化 fail。"""
    gate = _load_agent_gate()
    docs = tmp_path / "docs"
    docs.mkdir()
    unreadable = docs / "bad.md"
    unreadable.write_text("gcode_gen.py:1", encoding="utf-8")
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):  # noqa: ANN002, ANN003
        if path == unreadable:
            raise PermissionError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    result = gate.check_doc_line_refs()

    assert result["status"] == "fail"
    assert "无法读取治理文档 docs/bad.md（PermissionError）" in result["detail"]


def test_gate_doc_refs_report_unreadable_reference_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """治理文档可读、但被引用的本仓目标不可读时，也必须是结构化 fail。

    这一分支的 detail 要走 target.relative_to(ROOT)（外仓那条不能这么写，
    会 ValueError），路径表达式最容易写错，所以单独钉住。"""
    gate = _load_agent_gate()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "refs.md").write_text("gcode_gen.py:1", encoding="utf-8")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    target = pkg / "gcode_gen.py"
    target.write_text("line", encoding="utf-8")
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):  # noqa: ANN002, ANN003
        if path == target:
            raise PermissionError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    result = gate.check_doc_line_refs()

    assert result["status"] == "fail"
    assert "无法读取引用目标 pkg/gcode_gen.py（PermissionError）" in result["detail"]


def test_gate_external_refs_report_unreadable_reference_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """full 门禁必须把不可读的外仓引用目标收敛为结构化 fail。"""
    gate = _load_agent_gate()
    docs = tmp_path / "docs"
    docs.mkdir()
    governance_doc = docs / "refs.md"
    governance_doc.write_text("Protocol.cpp:1", encoding="utf-8")
    # 落点放在 tmp_path 内：`_external_index` 已被 mock，走外仓分支只取决于
    # ref 字符串在本仓解析不到（ROOT/Protocol.cpp 不存在），与 target 真实位置无关。
    # 用 tmp_path.parent 会在 session 级共享目录留固定名副作用，重复执行即 FileExistsError。
    external = tmp_path / "external"
    external.mkdir()
    target = external / "Protocol.cpp"
    target.write_text("line", encoding="utf-8")
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):  # noqa: ANN002, ANN003
        if path == target:
            raise PermissionError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(
        gate,
        "_external_index",
        lambda: ({"Protocol.cpp": [target]}, ["test-repo"], []),
    )
    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    result = gate.check_external_doc_line_refs()

    assert result["status"] == "fail"
    assert "无法读取外仓引用目标 Protocol.cpp（PermissionError）" in result["detail"]



# ---------------------------------------------------------------------------
# 门禁防旧绿（tombstone + HEAD 绑定）与行号内容指纹
# ---------------------------------------------------------------------------


def _gate_main_sandbox(
    gate, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """把 main() 的产物面（报告 + tombstone）整体指到 tmp，互不污染真仓。"""
    results = tmp_path / "results"
    monkeypatch.setattr(gate, "RESULTS", results)
    monkeypatch.setattr(gate, "REPORT_JSON", results / "hutuji_gate_last.json")
    monkeypatch.setattr(gate, "REPORT_MD", results / "hutuji_gate_last.md")
    monkeypatch.setattr(gate, "RUNNING_MARK", results / ".hutuji_gate_running")
    return results


def test_gate_main_running_mark_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tombstone 契约：运行期间必须存在且带计划层清单，正常结束必须清除。

    崩溃后磁盘上只剩上一轮报告是「旧绿」的根源；git index.lock / dpkg
    两阶段状态的社区解法是在场即「没跑完」。同时钉死报告绑定 git HEAD，
    让肉眼能分辨报告属于哪一版代码。"""
    gate = _load_agent_gate()
    _gate_main_sandbox(gate, tmp_path, monkeypatch)
    seen: dict = {}

    def probe() -> dict:
        seen["mark_exists_during_run"] = gate.RUNNING_MARK.is_file()
        if gate.RUNNING_MARK.is_file():
            seen["mark"] = json.loads(gate.RUNNING_MARK.read_text(encoding="utf-8"))
        return gate._layer("probe", "探针", "pass")

    monkeypatch.setattr(gate, "profile_layers", lambda profile: [("probe", probe)])
    monkeypatch.setattr(gate, "_git_head", lambda: "deadbeef" * 5)

    code = gate.main(["--profile", "docs"])

    assert code == 0
    assert seen["mark_exists_during_run"] is True, "运行期间 tombstone 必须在场"
    assert seen["mark"]["plan"] == ["probe"]
    assert seen["mark"]["profile"] == "docs"
    assert not gate.RUNNING_MARK.exists(), "正常结束后 tombstone 必须清除"
    report = json.loads(gate.REPORT_JSON.read_text(encoding="utf-8"))
    assert report["git_head"] == "deadbeef" * 5
    assert "deadbeef" in gate.REPORT_MD.read_text(encoding="utf-8")


def test_gate_main_crash_leaves_running_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """层执行中途崩溃：tombstone 必须留在盘上，且不产生新报告。

    这样下一个 agent 看到 `.hutuji_gate_running` 在场即知上次没跑完，
    旁边的旧报告不得当作本轮结果。"""
    gate = _load_agent_gate()
    _gate_main_sandbox(gate, tmp_path, monkeypatch)

    def boom() -> dict:
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(gate, "profile_layers", lambda profile: [("boom", boom)])

    with pytest.raises(RuntimeError, match="simulated crash"):
        gate.main(["--profile", "docs"])

    assert gate.RUNNING_MARK.is_file(), "崩溃后 tombstone 必须留盘"
    mark = json.loads(gate.RUNNING_MARK.read_text(encoding="utf-8"))
    assert mark["plan"] == ["boom"]
    assert not gate.REPORT_JSON.exists(), "崩溃路径不得写出新报告"

@pytest.mark.parametrize("mode", ["no_git_binary", "not_a_repo"])
def test_gate_git_head_degrades_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """git 不存在 / 非 git 仓时返回 None，不得炸掉整个门禁。

    不能用「tmp_path 不是 git 仓」当真：门禁把 pytest basetemp 放在仓内
    results/.tmp/ 下，`git rev-parse` 会从 tmp_path 一路上溯到真仓 .git
    并返回真实 HEAD——该写法在直跑 pytest 时绿、在门禁里红（2026-08-09
    实测）。故打桩 subprocess.run 覆盖两条失败路径。"""
    gate = _load_agent_gate()
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    if mode == "no_git_binary":

        def no_git(*args, **kwargs):  # noqa: ANN002, ANN003
            raise FileNotFoundError("git not installed")

        monkeypatch.setattr(gate.subprocess, "run", no_git)
    else:

        class _Done:
            returncode = 128
            stdout = ""

        monkeypatch.setattr(gate.subprocess, "run", lambda *a, **k: _Done())

    assert gate._git_head() is None


def _finger_sandbox(
    gate,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    docs: dict[str, str],
    files: dict[str, str],
) -> None:
    """搭一个最小仓：docs/ 下放引用文档，files 下放被引用的代码。"""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for name, text in docs.items():
        (docs_dir / name).write_text(text, encoding="utf-8")
    for name, text in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    # 本组测试只关心本仓解析；外仓索引置空（全部缺席）
    monkeypatch.setattr(gate, "_external_index", lambda: ({}, [], ["none"]))


def test_gate_line_ref_fingerprint_detects_half_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """行号在界内但指向内容已变的「半漂移」，行号校验查不出，指纹必须判红。"""
    gate = _load_agent_gate()
    _finger_sandbox(
        gate,
        tmp_path,
        monkeypatch,
        docs={"refs.md": "见 target.py:2\n"},
        files={"target.py": "first\nsecond\nthird\n"},
    )
    assert gate.accept_line_refs() == 0
    assert gate.check_line_ref_fingerprints()["status"] == "pass"

    # 行数不变、第二行内容变：check_doc_line_refs 必然全绿的那种漂移
    (tmp_path / "target.py").write_text(
        "first\nSECOND-CHANGED\nthird\n", encoding="utf-8"
    )

    result = gate.check_line_ref_fingerprints()
    assert result["status"] == "fail"
    assert "半漂移" in result["detail"]


def test_gate_line_ref_fingerprint_rejects_unregistered_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文档新增 path:line 引用却不登记基线 = 证据未经核对，必须判红。"""
    gate = _load_agent_gate()
    _finger_sandbox(
        gate,
        tmp_path,
        monkeypatch,
        docs={"refs.md": "见 target.py:1\n"},
        files={"target.py": "only\n"},
    )
    baseline = tmp_path / "docs" / "agent-line-refs-baseline.json"
    baseline.write_text(
        json.dumps({"version": 1, "refs": {}}), encoding="utf-8"
    )

    result = gate.check_line_ref_fingerprints()
    assert result["status"] == "fail"
    assert "未登记" in result["detail"]


def test_gate_line_ref_fingerprint_flags_stale_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """基线里残留文档已不再包含的引用条目 = 登记面与文档脱钩，必须判红。"""
    gate = _load_agent_gate()
    _finger_sandbox(
        gate,
        tmp_path,
        monkeypatch,
        docs={"refs.md": "没有任何行号引用\n"},
        files={"target.py": "only\n"},
    )
    baseline = tmp_path / "docs" / "agent-line-refs-baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "version": 1,
                "refs": {
                    "docs/refs.md -> target.py:1": {
                        "target": "target.py",
                        "sha8": "00000000",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = gate.check_line_ref_fingerprints()
    assert result["status"] == "fail"
    assert "陈旧" in result["detail"]


def test_gate_line_ref_fingerprint_keeps_unresolvable_external_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """外仓缺席时其引用解析不到：基线条目必须保留且不判陈旧。

    否则在没有 fork 的机器上跑一次 accept 就会把全部外仓条目冲掉，
    回到有 fork 的机器整层假绿。"""
    gate = _load_agent_gate()
    _finger_sandbox(
        gate,
        tmp_path,
        monkeypatch,
        docs={"refs.md": "外仓证据见 ghost_ext.py:1\n"},
        files={},
    )
    baseline = tmp_path / "docs" / "agent-line-refs-baseline.json"
    # target 必须是**绝对路径**：_iter_doc_line_refs 对外仓命中写 `target.as_posix()`
    # （_external_index 的候选都从绝对 repo root 拼出），只有本仓命中才写
    # `relative_to(ROOT)`。GATE-P2-06 正是靠这个形态差把「fork 缺席，须容忍」与
    # 「本仓文件被删/改名，须判红」分开，故本夹具必须复刻真实序列化格式——
    # 早先这里写的是相对路径 `Grbl_Esp32/src/ghost_ext.py`，那种形态在真实基线里
    # 从未出现过（实测 171 条 = 151 外仓绝对 + 20 本仓相对）。
    external_target = "D:/Users/Grbl_Esp32/Grbl_Esp32/src/ghost_ext.py"
    assert gate._is_external_target(external_target), "夹具必须是外仓形态，否则测的是 P2-06 判红路径"
    baseline.write_text(
        json.dumps(
            {
                "version": 1,
                "refs": {
                    "docs/refs.md -> ghost_ext.py:1": {
                        "target": external_target,
                        "sha8": "11111111",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert gate.check_line_ref_fingerprints()["status"] == "pass"
    # accept 也不得冲掉当前解析不到但文档仍引用的条目
    assert gate.accept_line_refs() == 0
    kept = json.loads(baseline.read_text(encoding="utf-8"))
    assert "docs/refs.md -> ghost_ext.py:1" in kept["refs"]


def test_gate_line_ref_fingerprint_range_ref_hashes_all_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """区间引用 `:2-3` 的指纹必须覆盖整段，只动区间中间一行也要判红。"""
    gate = _load_agent_gate()
    _finger_sandbox(
        gate,
        tmp_path,
        monkeypatch,
        docs={"refs.md": "见 target.py:2-3\n"},
        files={"target.py": "a\nb\nc\nd\n"},
    )
    assert gate.accept_line_refs() == 0
    (tmp_path / "target.py").write_text("a\nb\nC\nd\n", encoding="utf-8")

    result = gate.check_line_ref_fingerprints()
    assert result["status"] == "fail"
    assert "半漂移" in result["detail"]


def test_gate_line_ref_fingerprint_rejects_blank_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """引用指向空白行 = 指不到任何证据，必须判红，且不得靠登记洗成绿。

    空串的 sha256 前 8 位恒为 e3b0c442：登记之后这条锚点永远与基线相符、
    永远不报漂移，却被计进「指纹 N 处全对」。本仓实测有过 5 条这样的锚点，
    全是行号已漂 1-2 行的陈旧指针（真证据就在邻行）——指纹层不但没抓住漂移，
    还替它盖了章（2026-08-11 review AG-F2）。"""
    gate = _load_agent_gate()
    _finger_sandbox(
        gate,
        tmp_path,
        monkeypatch,
        docs={"refs.md": "见 target.py:2\n"},
        files={"target.py": "first\n\nthird\n"},
    )
    # 前提：空串指纹确实是那个恒定值，本测试的前提才成立
    assert gate._sha8([""]) == "e3b0c442"

    baseline = tmp_path / "docs" / "agent-line-refs-baseline.json"
    baseline.write_text(json.dumps({"version": 1, "refs": {}}), encoding="utf-8")

    result = gate.check_line_ref_fingerprints()
    assert result["status"] == "fail"
    assert "空锚点" in result["detail"]


def test_gate_accept_line_refs_refuses_blank_and_broken_anchors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """accept 必须拒绝空锚点与越界引用，且一个字节都不写基线。

    否则 --accept-line-refs 就是「把坏引用洗成永久绿」的一键开关，
    正是 AGENTS.md 警告的「无脑重登等于把指纹层退化成自动追认」
    （2026-08-11 review AGR-003）。"""
    gate = _load_agent_gate()
    _finger_sandbox(
        gate,
        tmp_path,
        monkeypatch,
        docs={"refs.md": "空 target.py:2\n越界 target.py:99\n好 target.py:3\n"},
        files={"target.py": "first\n\nthird\n"},
    )
    baseline = tmp_path / "docs" / "agent-line-refs-baseline.json"

    assert gate.accept_line_refs() == 1
    assert not baseline.exists(), "被拒的 accept 不得留下任何基线"

    # 行号改对之后同一命令必须能正常登记，证明拒绝是针对坏引用而非整条路径
    (tmp_path / "docs" / "refs.md").write_text("好 target.py:3\n", encoding="utf-8")
    assert gate.accept_line_refs() == 0
    refs = json.loads(baseline.read_text(encoding="utf-8"))["refs"]
    assert sorted(refs) == ["docs/refs.md -> target.py:3"]


def test_gate_line_ref_fingerprint_flags_broken_ref_left_in_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """基线里残留「行号已越界」的条目 = 上一次 accept 洗白过，必须判红。

    行号层会独立报越界，但基线若仍登记着它，重跑 accept 就会把它再抄一遍；
    指纹层必须自己也认这笔账（2026-08-11 review AGR-003）。"""
    gate = _load_agent_gate()
    _finger_sandbox(
        gate,
        tmp_path,
        monkeypatch,
        docs={"refs.md": "越界 target.py:99\n"},
        files={"target.py": "only\n"},
    )
    baseline = tmp_path / "docs" / "agent-line-refs-baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "version": 1,
                "refs": {
                    "docs/refs.md -> target.py:99": {
                        "target": "target.py",
                        "sha8": "deadbeef",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = gate.check_line_ref_fingerprints()
    assert result["status"] == "fail"
    assert "越界残留" in result["detail"]


def test_gate_line_ref_fingerprint_pass_reports_baseline_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pass 文案必须打出基线登记时的 HEAD，并在 HEAD 已前进时明说。

    基线一直写 git_head 却从没被读过：读报告的人无法判断这批指纹是本轮
    人工核对的，还是几十个提交前登记后一路顺延的（2026-08-11 review
    AGR-001）。不判红——HEAD 每次提交都变，判红等于要求每次提交重登基线。"""
    gate = _load_agent_gate()
    _finger_sandbox(
        gate,
        tmp_path,
        monkeypatch,
        docs={"refs.md": "见 target.py:1\n"},
        files={"target.py": "only\n"},
    )
    monkeypatch.setattr(gate, "_git_head", lambda: "a" * 40)
    assert gate.accept_line_refs() == 0

    same = gate.check_line_ref_fingerprints()
    assert same["status"] == "pass"
    assert "aaaaaaaa" in same["detail"]
    assert "已前进" not in same["detail"]

    monkeypatch.setattr(gate, "_git_head", lambda: "b" * 40)
    moved = gate.check_line_ref_fingerprints()
    assert moved["status"] == "pass", "HEAD 前进不判红"
    assert "已前进" in moved["detail"]
    assert "aaaaaaaa" in moved["detail"] and "bbbbbbbb" in moved["detail"]


def test_gate_main_records_stale_running_mark_in_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上一轮门禁未跑完（tombstone 在场）必须进报告 JSON 与 markdown。

    只 print 到终端是不够的：报告才是下一个 agent 唯一会读的东西，
    「上次崩过」不进报告就等于在报告面上不存在（2026-08-11 review AGR-002）。
    本轮跑完会覆盖报告，故不判红，只留痕。"""
    gate = _load_agent_gate()
    results = _gate_main_sandbox(gate, tmp_path, monkeypatch)
    results.mkdir(parents=True, exist_ok=True)
    gate.RUNNING_MARK.write_text(
        json.dumps({"profile": "hub", "started": "2026-08-11T00:00:00Z", "git_head": "cafe" * 10}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        gate, "profile_layers", lambda profile: [("probe", lambda: gate._layer("probe", "探针", "pass"))]
    )

    assert gate.main(["--profile", "docs"]) == 0, "陈旧 mark 不判红"

    report = json.loads(gate.REPORT_JSON.read_text(encoding="utf-8"))
    assert report["stale_run_mark"]["profile"] == "hub"
    assert report["stale_run_mark"]["git_head"] == "cafe" * 10
    md = gate.REPORT_MD.read_text(encoding="utf-8")
    assert "上一轮门禁未跑完" in md
    assert "2026-08-11T00:00:00Z" in md
    assert not gate.RUNNING_MARK.exists(), "本轮正常收尾仍须清除 mark"


def test_gate_main_omits_stale_mark_when_previous_run_was_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上轮干净收尾时 stale_run_mark 必须是 None，告警段不得出现。

    否则告警变成每轮都印的噪音，读报告的人会学会忽略它。"""
    gate = _load_agent_gate()
    _gate_main_sandbox(gate, tmp_path, monkeypatch)
    monkeypatch.setattr(
        gate, "profile_layers", lambda profile: [("probe", lambda: gate._layer("probe", "探针", "pass"))]
    )

    assert gate.main(["--profile", "docs"]) == 0

    report = json.loads(gate.REPORT_JSON.read_text(encoding="utf-8"))
    assert report["stale_run_mark"] is None
    assert "上一轮门禁未跑完" not in gate.REPORT_MD.read_text(encoding="utf-8")


def _load_cloud_bridge_smoke():
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "cloud_bridge_smoke.py"
    spec = importlib.util.spec_from_file_location("hutuji_cloud_bridge_smoke_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cloud_bridge_smoke_reports_server_early_exit() -> None:
    class ExitedProcess:
        returncode = 7

        @staticmethod
        def poll():
            return 7

    with pytest.raises(RuntimeError, match="提前退出.*exit=7"):
        _load_cloud_bridge_smoke()._wait_port(ExitedProcess(), timeout=0.01)


def _load_root_script(module_name: str, name: str):
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "scripts" / name
    spec = importlib.util.spec_from_file_location(module_name, script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fault_capture_rejects_nonpositive_and_negative_timings() -> None:
    capture = _load_root_script("hutuji_fault_capture_test", "bringup_fault_capture.py")
    with pytest.raises(Exception, match="duration.*> 0"):
        capture._positive_duration("0")
    with pytest.raises(Exception, match="秒数.*>= 0"):
        capture._nonnegative_seconds("-1")

def test_fault_capture_queues_wifi_restore_after_interruption() -> None:
    import queue

    capture = _load_root_script("hutuji_fault_capture_restore_test", "bringup_fault_capture.py")
    commands: queue.Queue[str] = queue.Queue()

    assert capture._queue_wifi_restore(True, False, commands) is True
    assert commands.get_nowait() == "[ESP115]ON"
    assert capture._queue_wifi_restore(False, False, commands) is False
    assert capture._queue_wifi_restore(True, True, commands) is True
    assert commands.empty()


def test_console_endpoint_display_drops_credentials_path_and_token() -> None:
    console = _load_root_script("hutuji_console_test", "xiaozhi_console.py")
    url = "wss://user:password@mcp.example:443/private?token=must-not-leak#secret"

    shown = console._endpoint_origin(url)

    assert shown == "wss://mcp.example:443"
    assert "password" not in shown
    assert "must-not-leak" not in shown

def test_console_endpoint_display_preserves_ipv6_origin_syntax() -> None:
    console = _load_root_script("hutuji_console_ipv6_test", "xiaozhi_console.py")

    assert console._endpoint_origin("wss://[2001:db8::1]:8443/private?token=secret") == (
        "wss://[2001:db8::1]:8443"
    )


def test_console_rejects_insecure_public_api_base() -> None:
    console = _load_root_script("hutuji_console_api_base_test", "xiaozhi_console.py")

    with pytest.raises(ValueError, match="HTTPS"):
        console._validate_api_base("http://api.example.com/api")
    assert console._validate_api_base("http://127.0.0.1:8000/api/") == (
        "http://127.0.0.1:8000/api"
    )


def test_console_redirect_strips_auth_only_when_origin_changes() -> None:
    from email.message import Message
    from urllib.request import Request

    console = _load_root_script("hutuji_console_redirect_test", "xiaozhi_console.py")
    handler = console._SafeRedirectHandler()
    headers = Message()

    cross_origin = handler.redirect_request(
        Request(
            "https://xiaozhi.me/api/agents",
            headers={
                "Authorization": "Bearer secret",
                "Proxy-Authorization": "Basic secret",
                "Cookie": "session=secret",
            },
        ),
        None,
        302,
        "Found",
        headers,
        "https://attacker.example/collect",
    )
    assert cross_origin is not None
    assert cross_origin.get_header("Authorization") is None
    assert cross_origin.get_header("Proxy-Authorization") is None
    assert cross_origin.get_header("Cookie") is None

    same_origin = handler.redirect_request(
        Request(
            "https://xiaozhi.me/api/agents",
            headers={"Authorization": "Bearer secret"},
        ),
        None,
        302,
        "Found",
        headers,
        "https://xiaozhi.me/api/agents/",
    )
    assert same_origin is not None
    assert same_origin.get_header("Authorization") == "Bearer secret"


def test_console_device_api_uses_separate_token_and_official_path(monkeypatch) -> None:
    console = _load_root_script("hutuji_console_device_api_test", "xiaozhi_console.py")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"success":true,"data":{"accepted":true}}'

    class FakeOpener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(console, "get_device_token", lambda: "device-only-secret")
    monkeypatch.setattr(console, "_URL_OPENER", FakeOpener())

    result = console.device_api("POST", "/messaging/device/tools/call", {
        "name": "hutuji.status",
        "arguments": {},
    })

    request = captured["request"]
    assert request.full_url == "https://xiaozhi.me/api/messaging/device/tools/call"
    assert request.get_header("Authorization") == "Bearer device-only-secret"
    assert json.loads(request.data) == {"name": "hutuji.status", "arguments": {}}
    assert captured["timeout"] == 30
    assert result["data"]["accepted"] is True


@pytest.mark.parametrize("tool_name", [
    "hutuji.status", "hutuji.abort", "hutuji.pause", "hutuji.resume",
    "hutuji.repeat", "hutuji.pen_test",
])
def test_console_device_no_argument_tools_are_whitelisted(tool_name) -> None:  # noqa: ANN001
    console = _load_root_script(
        f"hutuji_console_device_{tool_name.replace('.', '_')}_test",
        "xiaozhi_console.py",
    )
    assert console._parse_device_arguments(tool_name, None) == {}
    with pytest.raises(ValueError, match="参数必须严格"):
        console._parse_device_arguments(tool_name, '{"unexpected":1}')


def test_console_device_draw_requires_exact_url_argument() -> None:
    console = _load_root_script("hutuji_console_device_draw_test", "xiaozhi_console.py")
    url = "https://hutuji.donglicao.com/files/draw_20260807_120000_abcdefghijklmnopqrstuv.gcode"

    assert console._parse_device_arguments("hutuji.draw", json.dumps({"url": url})) == {
        "url": url,
    }
    for raw in (None, "[]", "{}", '{"url":""}', '{"url":"x","extra":1}'):
        with pytest.raises(ValueError):
            console._parse_device_arguments("hutuji.draw", raw)
    with pytest.raises(ValueError, match="不在白名单"):
        console._parse_device_arguments("self.system.reboot", None)


def test_console_output_redacts_capability_and_credentials() -> None:
    console = _load_root_script("hutuji_console_device_redact_test", "xiaozhi_console.py")
    text = (
        '{"token":"device-secret","authorization":"Bearer bearer-secret",'
        '"api_key":"sk-examplelongsecret",'
        '"url":"https://hutuji.example/files/'
        'draw_20260807_120000_abcdefghijklmnopqrstuv.gcode"}'
    )

    redacted = console._redact_text(text)

    assert "device-secret" not in redacted
    assert "bearer-secret" not in redacted
    assert "sk-examplelongsecret" not in redacted
    assert "abcdefghijklmnopqrstuv" not in redacted
    assert redacted.count("<redacted>") == 4


def _console_with_failing_opener(tag: str, exc: BaseException):  # noqa: ANN202
    """加载控制台脚本并把 opener 换成恒抛 exc 的假实现。"""
    console = _load_root_script(tag, "xiaozhi_console.py")

    class _FailingOpener:
        def open(self, req, timeout=None):  # noqa: ANN001, ANN202, ARG002
            raise exc

    console._URL_OPENER = _FailingOpener()
    return console


def _capture_stderr(fn) -> str:  # noqa: ANN001
    """跑 fn 并返回其 stderr；吞掉 SystemExit（这些 CLI 路径以 exit(1) 收场）。"""
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.suppress(SystemExit):
        fn()
    return buf.getvalue()


def test_console_api_redacts_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """控制台 JWT 路径的 HTTP 错误正文必须脱敏后再打印。

    上游网关/WAF 的错误页常把请求头或 query 回显进正文。控制台 JWT 属敏感凭据
    （仓规禁止入库 小智控制台token.txt），裸打印会把它落进终端回滚缓冲与日志采集。
    孪生的 device_api 一直脱敏，api() 曾漏——实测同一正文下 api() 打印明文 JWT、
    device_api() 输出 <redacted>，故本例钉住两者同口径。
    """
    import io
    from urllib.error import HTTPError

    leaky = (
        '{"error":"unauthorized","echo":{"Authorization":'
        '"Bearer eyJhbGciOiJIUzI1NiJ9.LEAKED-CONSOLE-JWT.sig"},'
        '"token":"LEAKED-QUERY-TOKEN"}'
    )
    err = HTTPError(
        url="https://xiaozhi.me/api/agents",
        code=401,
        msg="Unauthorized",
        hdrs=None,
        fp=io.BytesIO(leaky.encode()),
    )
    console = _console_with_failing_opener("hutuji_console_api_redact_test", err)
    monkeypatch.setenv("XIAOZHI_TOKEN", "probe-token")

    # 只调一次：HTTPError.fp 首次 read() 即耗尽，二次调用拿到空正文会假绿。
    captured = _capture_stderr(lambda: console.api("GET", "/agents"))

    assert "LEAKED-CONSOLE-JWT" not in captured, f"JWT 泄漏进 stderr：{captured}"
    assert "LEAKED-QUERY-TOKEN" not in captured, f"query token 泄漏：{captured}"
    assert "<redacted>" in captured
    assert "API 错误 401" in captured


def test_console_api_handles_network_error_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """网络错误必须收成一行「只含 reason 类型名」的消息，不得裸抛 traceback。"""
    from urllib.error import URLError

    console = _console_with_failing_opener(
        "hutuji_console_api_urlerror_test",
        URLError(OSError(101, "Network is unreachable")),
    )
    monkeypatch.setenv("XIAOZHI_TOKEN", "probe-token")

    captured = _capture_stderr(lambda: console.api("GET", "/agents"))

    assert "API 网络错误: OSError" in captured
    assert "Network is unreachable" not in captured, "不得回显 reason 正文"


def test_console_upload_redacts_error_body_and_network_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """api_upload 的两条失败分支必须与 api/device_api 同口径。

    知识库文档替换走 multipart 上传，上游 401/413 的错误页同样会回显 Authorization
    头；该分支曾裸 print(e.read()) 且完全没有 URLError 分支——网络不通即裸抛
    traceback。三个 HTTP 入口共用一个 _URL_OPENER，脱敏口径必须一致。
    """
    import io
    from urllib.error import HTTPError, URLError

    doc = tmp_path / "kb.md"
    doc.write_text("# 知识库", encoding="utf-8")
    monkeypatch.setenv("XIAOZHI_TOKEN", "probe-token")

    leaky = (
        '{"error":"payload too large","echo":{"Authorization":'
        '"Bearer eyJhbGciOiJIUzI1NiJ9.LEAKED-UPLOAD-JWT.sig"}}'
    )
    http_console = _console_with_failing_opener(
        "hutuji_console_upload_redact_test",
        HTTPError(
            url="https://xiaozhi.me/api/knowledge/upload",
            code=413,
            msg="Payload Too Large",
            hdrs=None,
            fp=io.BytesIO(leaky.encode()),
        ),
    )
    # 只调一次：HTTPError.fp 首次 read() 即耗尽，二次调用拿到空正文会假绿。
    captured = _capture_stderr(
        lambda: http_console.api_upload("/knowledge/upload", str(doc))
    )
    assert "LEAKED-UPLOAD-JWT" not in captured, f"JWT 泄漏进 stderr：{captured}"
    # _BEARER_RE 先把值收成 `Bearer <redacted>`，_TOKEN_FIELD_RE 再把整个
    # "authorization" 的值替成 "<redacted>"——比只脱 Bearer 段更强，钉住终态。
    assert '"<redacted>"' in captured
    assert "Bearer eyJ" not in captured
    assert "上传错误 413" in captured

    net_console = _console_with_failing_opener(
        "hutuji_console_upload_urlerror_test",
        URLError(OSError(111, "Connection refused")),
    )
    captured = _capture_stderr(
        lambda: net_console.api_upload("/knowledge/upload", str(doc))
    )
    assert "上传网络错误: OSError" in captured
    assert "Connection refused" not in captured, "不得回显 reason 正文"


def _load_e2e_local():
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "e2e_local.py"
    spec = importlib.util.spec_from_file_location("hutuji_e2e_local_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_e2e_rejects_occupied_simulator_port(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """残留/未知监听者不能被本地 e2e 借用，否则 child bind 失败仍可能测到旧 sim 而假 PASS。"""
    import socket

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    monkeypatch.setenv("GRBL_SIM_PORT", str(srv.getsockname()[1]))
    try:
        assert _load_e2e_local().main() == 1
        assert "已被占用" in capsys.readouterr().err
    finally:
        srv.close()


def test_e2e_stop_reaps_child() -> None:
    """终止不响应的模拟器时必须升级 kill，finally 不得遗留下一次 e2e 的假绿来源。"""
    import subprocess
    import sys

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    _load_e2e_local()._stop(process)
    assert process.poll() is not None


def test_pipe_sim_banner_eof_raises_connection_error(tmp_path: Path) -> None:
    """回归：banner 阶段对端连上即断（EOF）必须抛 ConnectionError。

    旧代码 `if not line: continue` 会以 100% CPU 忙等（readline 返回 b""
    只发生在 EOF，socket 超时走 socket.timeout 不会到这里）；修复后抛
    ConnectionError。EOF 判定须用 readline 原始返回值：banner 以 \r\n
    开头时首行 strip 后是空串但 raw 非空，不得误判。"""
    import socket
    import threading

    from hutuji_mcp.pipe_sim import stream_gcode

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _accept_and_close() -> None:
        conn, _ = srv.accept()
        conn.close()

    t = threading.Thread(target=_accept_and_close, daemon=True)
    t.start()
    gcode = tmp_path / "in.gcode"
    gcode.write_text("G21\nG90\nF8000\n", encoding="utf-8")
    try:
        with pytest.raises(ConnectionError, match="banner"):
            stream_gcode(gcode, "127.0.0.1", port)
    finally:
        srv.close()
        t.join(timeout=2)



def test_gate_forbidden_claims_reports_actually_scanned_count() -> None:
    """GATE-P2-01：pass 文案里的份数必须是实测扫描数，不是清单长度。

    读不到的治理文档只 `continue`，而旧文案直接打 `len(GOVERNANCE_DOCS)`：
    2026-08-09 实测往清单塞一个不存在的路径，该层仍 pass 且印「19 份均未做出」而
    实际只扫 18——报告数字与真实扫描面脱钩。存在性仍由 required_files 层判红
    （那层三个 profile 都跑，且 required_file_set() 现算并集），本层只负责别撒谎。
    """
    gate = _load_agent_gate()
    real = list(gate.GOVERNANCE_DOCS)
    original = gate.check_forbidden_claims()
    assert original["status"] == "pass", original
    assert f"{len(real)}/{len(real)} 份" in original["detail"], original["detail"]

    # 清单里多一个不存在的路径：扫描面仍是 real，文案的分子不得跟着膨胀
    gate.GOVERNANCE_DOCS = [*real, "docs/does-not-exist-ever.md"]
    try:
        polluted = gate.check_forbidden_claims()
    finally:
        gate.GOVERNANCE_DOCS = real
    assert polluted["status"] == "pass", polluted
    assert f"{len(real)}/{len(real) + 1} 份" in polluted["detail"], polluted["detail"]
    assert "required_files" in polluted["detail"]


def test_gate_decision_anchors_reports_soft_missing_anchors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GATE-P2-02：软锚点缺失必须真的报出来。

    旧文案是 `[a for a in missing if a in protocol or a in text]`，而 missing 的构造
    条件恰是「既不在 text 也不在 protocol」——该推导式恒为空列表，缺多少条都印
    `soft_absent_in_readme_only=[]`。不判红是刻意的（软锚点），但不说出来不是。
    """
    gate = _load_agent_gate()
    docs = tmp_path / "docs"
    docs.mkdir()
    # 硬锚点齐全（否则先撞 fail 分支），软锚点故意全缺
    (tmp_path / "README.md").write_text(
        "双芯片 / WiFi Telnet / 117.72.118.95 / 哑管道\n", encoding="utf-8"
    )
    (docs / "protocol.md").write_text("## 9. Grbl 小改清单\n", encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "DECISION_ANCHORS", ["独角兽锚点甲", "独角兽锚点乙"])

    result = gate.check_decision_anchors()

    assert result["status"] == "pass", result
    assert "独角兽锚点甲" in result["detail"], result["detail"]
    assert "独角兽锚点乙" in result["detail"], result["detail"]
    assert "soft_absent=[]" not in result["detail"]


@pytest.mark.parametrize(
    ("ref", "why"),
    [
        ("deploy/nginx-hutuji.conf:52", "nginx 配置引用"),
        ("deploy/hutuji-mcp.service:12", "systemd unit 引用"),
        ("Grbl_Esp32.ino:28", "Arduino 主文件引用"),
    ],
)
def test_gate_doc_ref_regex_covers_deploy_and_ino_extensions(ref: str, why: str) -> None:
    """GATE-P2-03a：conf/service/ino 都能解析到真实文件，必须进校验面。

    白名单原先只有 py/toml/c/cc/cpp/h/hpp/sh/json/mdc/md，于是
    `docs/agent-handoff.md` 引 nginx-hutuji.conf 的行号、`docs/protocol.md` 引
    Grbl_Esp32.ino 的行号这两条**真活指针**，从未进入三层校验中的任何一层
    （2026-08-11 实测）。
    """
    gate = _load_agent_gate()
    match = gate._DOC_REF_RE.search(f"见 {ref} 那行")
    assert match is not None, why
    assert match.group(0) == ref


@pytest.mark.parametrize(
    "ext",
    ["txt", "log"],
)
def test_gate_doc_ref_regex_still_excludes_volatile_extensions(ext: str) -> None:
    """txt/log 刻意不收：本仓这两类命中全落在 gitignore 的 results/，
    纳入会让门禁结论随机器上有没有残留产物而变。"""
    gate = _load_agent_gate()
    assert gate._DOC_REF_RE.search(f"见 results/whatever.{ext}:12 那行") is None


@pytest.mark.parametrize(
    ("ref", "reason"),
    [
        ("target.py:3-2", "区间倒序"),
        ("target.py:0", "行号从 0 起"),
        ("target.py:0-2", "行号从 0 起"),
    ],
)
def test_gate_doc_line_refs_rejects_malformed_ranges(
    ref: str, reason: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GATE-P2-03b：区间语义错必须在越界层就判红。

    旧实现只算 `max(start, end)` 再判越界，故 `:3-2` 的较大端在范围内即整条放行；
    而指纹层用 start/end 原值切片，`start > end` 得空 body、`start < 1` 触发负索引，
    两者都进 broken 并判红。同一条引用在越界层绿、指纹层红，读报告的人只看到
    「越界残留」却在越界层查不出任何越界。
    """
    gate = _load_agent_gate()
    _finger_sandbox(
        gate,
        tmp_path,
        monkeypatch,
        docs={"refs.md": f"见 {ref}\n"},
        files={"target.py": "a\nb\nc\n"},
    )

    result = gate.check_doc_line_refs()

    assert result["status"] == "fail", result
    assert reason in result["detail"], result["detail"]


def test_gate_doc_line_refs_still_passes_valid_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正序区间与单行引用不得被 P2-03b 的新判据误伤。"""
    gate = _load_agent_gate()
    _finger_sandbox(
        gate,
        tmp_path,
        monkeypatch,
        docs={"refs.md": "见 target.py:1-3 与 target.py:2\n"},
        files={"target.py": "a\nb\nc\n"},
    )

    result = gate.check_doc_line_refs()

    assert result["status"] == "pass", result
    assert "校验 2 处" in result["detail"], result["detail"]


def test_gate_line_ref_fingerprint_flags_vanished_internal_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GATE-P2-06：基线登记过仓相对路径而现在解析不到 = 本仓文件被删/改名，判红。

    此前三层对这种情况全部只表现为「计数变小」：doc_line_refs 记 skipped、外仓层
    记 skipped、指纹层把它算进 kept。没有任何一层会红，于是一条曾经真实校验过的
    证据引用可以静默失去全部校验。基线里 target 的形态（仓相对 vs 绝对）是唯一能
    区分「fork 缺席，须容忍」与「本仓文件没了，须判红」的凭据。
    """
    gate = _load_agent_gate()
    _finger_sandbox(
        gate,
        tmp_path,
        monkeypatch,
        docs={"refs.md": "见 target.py:1\n"},
        files={"target.py": "only\n"},
    )
    assert gate.accept_line_refs() == 0
    assert gate.check_line_ref_fingerprints()["status"] == "pass"

    # 文档仍引用 target.py，但文件改名 —— 引用从此解析不到
    (tmp_path / "target.py").rename(tmp_path / "renamed.py")

    result = gate.check_line_ref_fingerprints()

    assert result["status"] == "fail", result
    assert "目标消失" in result["detail"], result["detail"]
    # accept 不得把它洗白：基线保留该条目，故重跑仍红
    assert gate.accept_line_refs() == 0
    assert gate.check_line_ref_fingerprints()["status"] == "fail"


def test_gate_line_ref_fingerprint_tolerates_absent_external_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """外仓缺席仍须容忍：GATE-P2-06 的判红只针对本仓相对路径。

    fork 可能整个不在这台机器上，那时把它判红等于要求每台机器都克隆三个仓。
    基线里外仓条目的 target 是绝对路径，据此分流。
    """
    gate = _load_agent_gate()
    _finger_sandbox(
        gate,
        tmp_path,
        monkeypatch,
        docs={"refs.md": "见 Foreign.cpp:1\n"},
        files={"target.py": "only\n"},
    )
    baseline = tmp_path / "docs" / "agent-line-refs-baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "version": 1,
                "refs": {
                    "docs/refs.md -> Foreign.cpp:1": {
                        "target": "D:/Users/Grbl_Esp32/Grbl_Esp32/Foreign.cpp",
                        "sha8": "deadbeef",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = gate.check_line_ref_fingerprints()

    assert result["status"] == "pass", result
    assert "保留 1 处" in result["detail"], result["detail"]


@pytest.mark.parametrize(
    "target",
    [
        "/opt/hutuji/mcp-server/hutuji_mcp/server.py",
        "D:/Users/Grbl_Esp32/Grbl_Esp32/Serial.cpp",
        "d:\\Users\\Grbl_Esp32\\x.h",
    ],
)
def test_gate_is_external_target_recognizes_absolute_paths(target: str) -> None:
    """绝对路径（含 Windows 盘符与反斜杠形态）必须判为外仓。"""
    gate = _load_agent_gate()
    assert gate._is_external_target(target) is True


@pytest.mark.parametrize(
    "target",
    ["scripts/agent_gate.py", "docs/protocol.md", "deploy/preflight.sh"],
)
def test_gate_is_external_target_recognizes_repo_relative_paths(target: str) -> None:
    """仓相对路径必须判为本仓，否则 P2-06 的判红会被整体豁免掉。"""
    gate = _load_agent_gate()
    assert gate._is_external_target(target) is False


@pytest.mark.parametrize(
    ("runner", "layer_id"),
    [("run_pytest", "pytest"), ("run_e2e", "e2e_local")],
)
def test_gate_runners_turn_launch_failure_into_layer_fail(
    runner: str, layer_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GATE-P2-04：subprocess 启动失败必须是本层判红，不能穿透到 main()。

    `subprocess.run` 除 TimeoutExpired 还会抛 OSError（解释器被删、venv 换架构、
    权限拒绝、ENOEXEC）。旧代码只捕 Timeout，异常一路穿过 main() 的层循环（那里
    没有 try），于是报告两个文件都不落盘、tombstone 留在盘上、终端只剩 traceback。
    结果是门禁「没有结论」而不是判红——读报告的人看到的是上一轮的旧报告。
    """
    import subprocess as sp

    gate = _load_agent_gate()

    def _boom(*_args, **_kwargs):
        raise OSError(8, "Exec format error")

    monkeypatch.setattr(gate.subprocess, "run", _boom)
    # _mcp_python() 必须有返回值，否则先撞「缺少 venv」分支而测不到本条
    monkeypatch.setattr(gate, "_mcp_python", lambda: Path(sp.__file__))

    result = getattr(gate, runner)()

    assert result["id"] == layer_id
    assert result["status"] == "fail", result
    assert "无法启动" in result["detail"], result["detail"]
    assert "OSError" in result["detail"], result["detail"]


def test_preflight_pins_bridge_restart_recency() -> None:
    """PREFLIGHT-P2-03：bridge 必须有「重启晚于本次部署」判据。

    此前 bridge 唯一的判据是 check_bridge_ready，而它只比对 MainPID 与 ready 文件
    里的 PID——证明「当前主进程写过 ready 文件」，证不到它读的是本次部署的
    endpoint。cloud_bridge.load_config() 只在启动期读一次 MCP_ENDPOINT，进程内重连
    复用旧值；check_bridge_env 校验的却是磁盘上的 bridge.env。于是 scp 了新
    bridge.env 却漏 restart 时全绿，跑着的仍是旧 endpoint。
    """
    root = Path(__file__).resolve().parents[2]
    preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")

    assert "check_bridge_restart_recency() {" in preflight
    assert 'check "hutuji-cloud-bridge 已重启且不早于本次 unit/bridge.env/代码"' in preflight
    body = preflight.split("check_bridge_restart_recency() {", 1)[1].split("\n}", 1)[0]
    # bridge.env 此前不在任何 mtime 比较集里，必须进来
    assert "/etc/hutuji/bridge.env" in body
    assert "/etc/systemd/system/hutuji-cloud-bridge.service" in body
    assert "/opt/hutuji/deploy/hutuji-cloud-bridge.service" in body
    # 代码面与 mcp 同源（cloud_bridge.py 在 hutuji_mcp 包内），且走同一个递归收集器
    assert "collect_deployed_py" in body
    assert 'watched+=("${DEPLOYED_PY[@]}")' in body
    # 判据本体：主进程启动时刻不得早于比较集里最新的 mtime
    assert "ExecMainStartTimestamp" in body
    assert "(( start_epoch < NEWEST_MTIME ))" in body
    # ready 判据仍在（本条是补充而非替换）
    assert 'check "hutuji-cloud-bridge active 且当前 WSS 已连接" check_bridge_ready' in preflight


def test_preflight_generate_chain_uses_real_temp_file() -> None:
    """PREFLIGHT-P2-04：dst 必须是真 mktemp，不能是 `mktemp -u` 的预测名。

    `-u` 只预测一个尚不存在的路径名、不创建文件、不确立属主，而 EXIT trap 随后
    无条件以 root `rm -f -- "$dst"`。脚本以 root 跑在 /tmp（sticky 对 root 无效），
    预测名与真建名之间存在窗口：任何本机用户都能在窗口内把那个名字建成自己的文件
    或指向别处的符号链接，trap 就替他删。同文件 check_download 用的是真 mktemp。
    """
    root = Path(__file__).resolve().parents[2]
    preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")

    body = preflight.split("check_generate_chain() (", 1)[1].split("\n)", 1)[0]
    assert 'dst="$(mktemp)"' in body
    assert "mktemp -u" not in body
    # trap 仍须回收两个文件，否则改成真文件反而留垃圾
    assert 'trap \'rm -f -- "$svg" "$dst"\' EXIT' in body
    # 全脚本范围内 `mktemp -u` 只允许出现在解释性注释里，不得再有调用
    calls = [
        line
        for line in preflight.splitlines()
        if "mktemp -u" in line and not line.lstrip().startswith("#")
    ]
    assert calls == [], calls

def test_preflight_permission_checks_dereference_symlinks() -> None:
    """env 权限判定必须 `stat -L`，与 mtime 侧同口径。

    2026-08-11 第三轮 review：2026-08-11 上一轮把 mtime 比较改成了 `stat -L -c %Y`
    （只声称 mtime，逐字准确），但同一份脚本里两条 env 权限判定 `%a` 仍是裸 `stat`，
    两类判定对 symlink 的口径由此分裂。

    方向上裸 `stat` 是 fail-closed（对 symlink 取到 777 → 判红，不会假绿），
    故这不是安全漏洞；真实危害是 env 被 symlink 化（轮换 API key 的常见做法）时
    体检会报「权限 777、应为 600」这种误导结论，让人去改链接权限而非看目标本体。
    """
    preflight_code = (Path(__file__).resolve().parents[2] / "deploy" / "preflight.sh").read_text(
        encoding="utf-8"
    )
    for env_path in ("/etc/hutuji/mcp.env", "/etc/hutuji/bridge.env"):
        assert f"stat -L -c %a {env_path}" in preflight_code, env_path
        assert f"stat -c %a {env_path}" not in preflight_code, env_path


def test_preflight_has_no_bare_stat_format_calls() -> None:
    """整份脚本不得再出现裸 `stat -c`：mtime 与权限两类判定都必须解引用。

    用正则扫全文而非逐个路径比对——逐个比对只能防住今天已知的那几处，
    新增一处裸 `stat -c` 仍会静默通过（`%a` 正是这样在 `%Y` 改掉后被留下的）。
    """
    import re

    preflight_code = (Path(__file__).resolve().parents[2] / "deploy" / "preflight.sh").read_text(
        encoding="utf-8"
    )
    code_lines = [
        line
        for line in preflight_code.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    bare = [line.strip() for line in code_lines if re.search(r"stat\s+-c\s+%", line)]
    assert bare == [], f"发现裸 stat -c（应为 stat -L -c）：{bare}"

def test_nginx_config_disables_server_tokens_in_both_blocks() -> None:
    """两个 server 块都必须关 server_tokens。

    默认 `on` 会让每个响应（含兜底 404）带 `nginx/1.x.y` 精确版本号，等于
    给 CVE 库筛版本。公网面虽只暴露 /files/ 与兜底 404，版本号仍无条件外泄。
    两个块各写一次而非提到 http 级：本仓只管这一份 site 配置。
    """
    conf = (Path(__file__).resolve().parents[2] / "deploy" / "nginx-hutuji.conf").read_text(
        encoding="utf-8"
    )
    assert conf.count("server_tokens off;") == 2, conf.count("server_tokens off;")
    # 必须落在 server 块内，不能只写在文件头部注释里
    for listen in ("listen 443 ssl http2;", "listen 80;"):
        idx = conf.index(listen)
        window = conf[idx : idx + 400]
        assert "server_tokens off;" in window, listen
