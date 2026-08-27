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

import httpx

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
from hutuji_mcp import bitmap_svg
from hutuji_mcp import gcode_gen
from hutuji_mcp import server


def _settings(
    output_dir: Path,
    *,
    ttl: int = 600,
    xiaozhi_device_token: str = "",
    paper_risk_gate: bool = False,
) -> Settings:
    return Settings(
        llm_base_url="https://example.invalid/v1",
        llm_api_key="",
        llm_model="test",
        output_dir=output_dir,
        public_base_url="https://draw.example",
        host="127.0.0.1",
        port=8300,
        output_ttl_seconds=ttl,
        xiaozhi_device_token=xiaozhi_device_token,
        paper_risk_gate=paper_risk_gate,
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


def test_settings_strips_whitespace_llm_api_key(tmp_path: Path) -> None:
    """纯空白 API key 视同未配置（fail-closed）：不 strip 会让空白 key 过
    llm_configured 的非空闸，全部请求以 401 假性可用、掩盖部署事故。"""
    configured = Settings(
        llm_base_url="https://example.invalid/v1",
        llm_api_key=" \t ",
        llm_model="test",
        output_dir=tmp_path,
        public_base_url="https://draw.example",
        host="127.0.0.1",
        port=8300,
        output_ttl_seconds=600,
    )
    assert configured.llm_api_key == ""
    assert configured.llm_configured is False


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
def _asgi_scope(
    path: str,
    client: tuple[str, int] | None,
    *,
    root_path: str = "",
) -> dict[str, object]:
    scope: dict[str, object] = {
        "type": "http", "path": path, "method": "POST", "headers": [], "client": client,
    }
    if root_path:
        scope["root_path"] = root_path
    return scope


def _run_guard(
    path: str,
    client: tuple[str, int] | None,
    *,
    root_path: str = "",
) -> tuple[bool, list[dict[str, object]]]:
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
    asyncio.run(guard(_asgi_scope(path, client, root_path=root_path), _receive, _send))
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


def _run_guard_ws(path: str, client: tuple[str, int] | None) -> tuple[bool, list[dict[str, object]]]:
    """websocket scope 版驱动：返回（是否进到内层 app，发出的消息）。"""
    reached: list[bool] = []

    async def _inner(scope: dict[str, object], receive: object, send: object) -> None:
        reached.append(True)

    sent: list[dict[str, object]] = []

    async def _send(message: dict[str, object]) -> None:
        sent.append(message)

    async def _receive() -> dict[str, object]:
        return {"type": "websocket.connect"}

    guard = server.LoopbackOnlyMcpGuard(_inner)
    scope = {"type": "websocket", "path": path, "headers": [], "client": client}
    asyncio.run(guard(scope, _receive, _send))
    return bool(reached), sent


@pytest.mark.parametrize("client", [("117.72.118.95", 40000), None])
def test_mcp_guard_blocks_nonloopback_websocket_scope(
    client: tuple[str, int] | None,
) -> None:
    """websocket scope 的 /mcp 同样必须拦（SRV-P3c）。

    守卫旧实现只对 `type == "http"` 生效，websocket scope 直接透传——当前
    Starlette 栈对 /mcp 没挂 ws 端点所以拿不到东西，但守卫的存在意义就是
    「下层怎么变都兜得住」；透传等于把这层保证悄悄挖空。fail-closed：
    非 loopback（或对端未知）一律握手前 close。
    """
    reached, sent = _run_guard_ws("/mcp", client)
    assert reached is False, "非 loopback websocket 对端不得进到 MCP 应用"
    assert sent and sent[0]["type"] == "websocket.close"


def test_mcp_guard_allows_loopback_websocket_scope() -> None:
    reached, _ = _run_guard_ws("/mcp", ("127.0.0.1", 50000))
    assert reached is True


def test_files_route_registers_head_method() -> None:
    """`GET /files/{name}` 必须同时受理 HEAD（SRV-P3b）。

    FastAPI 的 @app.get 不自动注册 HEAD；nginx 反代白名单与文档口径都写着
    `GET/HEAD /files/`，S3 侧用 HEAD 预检 Content-Length/CRC32 时应用层却回
    404 方法不存在。"""
    for route in server.app.routes:
        if getattr(route, "path", "") == "/files/{name}":
            assert "GET" in route.methods
            assert "HEAD" in route.methods, "/files 路由缺 HEAD"
            break
    else:
        raise AssertionError("找不到 /files/{name} 路由")


def test_mcp_session_idle_timeout_armed() -> None:
    """streamable HTTP 会话必须配 idle 超时（R10-BRG-02）。

    FastMCP 1.28.1 构造 StreamableHTTPSessionManager 时不透传
    session_idle_timeout（恒 None）：桥接/mcp-proxy 每次重连都留下一个再也
    无人 DELETE 的旧会话，其 run_server 任务与内存永不回收——慢泄漏直到
    重启。构造后补设该属性即可生效（manager 在请求期读 self 属性）。
    取 3600s：远大于看门狗 300s 与最慢合法绘图，只回收真正的孤儿会话；
    孤儿被回收后 mcp-proxy 若还拿旧 id 来，会得到 Session terminated，
    由桥接的判死重连自愈（见 test_cloud_bridge 第十轮）。
    """
    assert server.mcp.session_manager.session_idle_timeout == 3600

def test_draw_transaction_deadline_rejects_late_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """付费生成完成但事务已过预算时，不发布 G-code/preview。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(server, "settings", _settings(tmp_path, ttl=600))
    monkeypatch.setattr(server, "DRAW_TRANSACTION_DEADLINE_SECONDS", -0.01)

    async def late_bitmap(_description: str, **_kw) -> bitmap_svg.BitmapResult:
        return bitmap_svg.BitmapResult(svg="<svg/>", preview_png=b"png")

    monkeypatch.setattr(server, "generate_bitmap", late_bitmap)
    with pytest.raises(server.DrawTimeoutError, match="绘图生成超时"):
        asyncio.run(server._run_draw_job("超时产物", "https://draw.example"))
    assert list(tmp_path.iterdir()) == []
def test_bridge_health_endpoint_is_loopback_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        server,
        "read_health_status",
        lambda: {
            "cloud_connected": True,
            "proxy_connected": True,
            "updated_at": server.time.time(),
            "version": 1,
        },
    )

    local = asyncio.run(
        server._bridge_health(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")))
    )
    assert local.status_code == 200
    assert b'"cloud_connected":true' in local.body

    external = asyncio.run(
        server._bridge_health(SimpleNamespace(client=SimpleNamespace(host="203.0.113.10")))
    )
    assert external.status_code == 404
    assert external.body == b"not found"

def test_bridge_health_endpoint_rejects_stale_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        server,
        "read_health_status",
        lambda: {
            "cloud_connected": True,
            "proxy_connected": True,
            "updated_at": 1.0,
            "version": 1,
        },
    )
    monkeypatch.setattr(server.time, "time", lambda: 100.0)

    response = asyncio.run(
        server._bridge_health(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")))
    )

    assert response.status_code == 503

def test_bridge_health_endpoint_collapses_snapshot_read_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读快照抛非预期异常时按 503 塌缩（探针语义），不得回 500。"""
    from types import SimpleNamespace

    def _raise() -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "read_health_status", _raise)

    response = asyncio.run(
        server._bridge_health(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")))
    )

    assert response.status_code == 503
    assert b'"cloud_connected":false' in response.body

def test_bridge_health_fastapi_route_accepts_loopback_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 FastAPI 路由必须把 Request 注入，而非解析成查询参数。"""
    import httpx

    monkeypatch.setattr(
        server,
        "read_health_status",
        lambda: {
            "cloud_connected": True,
            "proxy_connected": True,
            "updated_at": server.time.time(),
            "version": 1,
        },
    )

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=server.app, client=("127.0.0.1", 12345))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/bridge-health")

    response = asyncio.run(request())
    assert response.status_code == 200
    assert response.json()["proxy_connected"] is True


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


def test_mcp_guard_respects_root_path_prefix() -> None:
    """uvicorn --root-path /x 下守卫必须剥 root_path 再比对 /mcp（SC-02）。

    ASGI 规范里 scope["path"] 是含 root_path 的完整路径（uvicorn --root-path
    时 path=/x/mcp、root_path=/x），Starlette 剥掉 root_path 后才路由到 /mcp
    子应用。守卫若按裸 path 前缀匹配，`/x/mcp` 会透传守卫再被剥前缀命中控制
    面——当前生产 unit 钉死 --host 127.0.0.1 且无 --root-path，今天不可达，
    属守卫承诺的纵深缺口。非 loopback 的 /x/mcp 必须拦，loopback 必须放行；
    相邻路径（/xmcpx、/x/mcpx）不得被误剥误拦。
    """
    reached, sent = _run_guard("/x/mcp", ("9.9.9.9", 1234), root_path="/x")
    assert reached is False, "root_path 前缀下的 /mcp 同样只接受 loopback 对端"
    assert sent[0]["status"] == 404
    reached, _ = _run_guard("/x/mcp", ("127.0.0.1", 50000), root_path="/x")
    assert reached is True, "root_path 前缀下 loopback 对端必须放行，否则桥接器打不通"
    # 剥 root_path 必须按路径段：/xmcpx 不是 /x 的子路径，/x/mcpx 剥后也不是
    # /mcp 端点，都不能被误拦。
    reached, _ = _run_guard("/xmcpx", ("9.9.9.9", 1234), root_path="/x")
    assert reached is True
    reached, _ = _run_guard("/x/mcpx", ("9.9.9.9", 1234), root_path="/x")
    assert reached is True


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


def test_settings_rejects_incomplete_sensenova_image_config(tmp_path: Path) -> None:
    """商汤 key 一旦启用，模型名不得留空；部分配置不能等到计费请求才报错。"""
    with pytest.raises(ValueError, match="HUTUJI_SENSENOVA_IMAGE_MODEL"):
        Settings(
            llm_base_url="https://example.invalid/v1",
            llm_api_key="agnes-key",
            llm_model="test",
            output_dir=tmp_path,
            public_base_url="https://draw.example",
            host="127.0.0.1",
            port=8300,
            output_ttl_seconds=600,
            sensenova_api_key="sense-key",
            sensenova_image_model="  ",
        )



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
            paper_risk_gate=False,
        ),
    )

    async def fake_generate_svg(_description: str, **_kw) -> SimpleNamespace:
        return SimpleNamespace(svg="<svg/>", preview_png=b"png")

    async def fake_svg_to_gcode(_svg_path: Path, gcode_path: Path) -> SimpleNamespace:
        gcode_path.write_text(_minimal_gcode(), encoding="utf-8")
        return SimpleNamespace(lines=6, bytes=gcode_path.stat().st_size, pen_lifts=0)

    monkeypatch.setattr(server, "generate_bitmap", fake_generate_svg)
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


def test_draw_result_includes_ttl_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CloudUX-F1：返回 JSON 必须带 ttl_seconds，调用方才能预告链接有效期。"""
    import json
    from types import SimpleNamespace

    monkeypatch.setattr(server, "settings", _settings(tmp_path, ttl=600))

    async def fake_bitmap(_desc: str, **_kw) -> SimpleNamespace:
        return SimpleNamespace(svg="<svg/>", preview_png=b"png")

    async def fake_convert(_svg_path: Path, gcode_path: Path) -> SimpleNamespace:
        gcode_path.write_text(_minimal_gcode(), encoding="utf-8")
        return SimpleNamespace(lines=6, bytes=gcode_path.stat().st_size, pen_lifts=0)

    monkeypatch.setattr(server, "generate_bitmap", fake_bitmap)
    monkeypatch.setattr(
        server,
        "_new_basename",
        lambda: "draw_20260731_120000_abcdefghijklmnopqrstuv",
    )
    monkeypatch.setattr(server, "svg_to_gcode_async", fake_convert)
    monkeypatch.setattr(
        server,
        "_draw_limiter",
        server.DrawLimiter(per_second=100, per_minute=100, max_concurrent=2),
    )

    result = json.loads(asyncio.run(server.hutuji_draw("test")))
    assert result["ttl_seconds"] == 600
def test_draw_waiter_has_deadline_before_bridge_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单次云端 waiter 必须在 bridge watchdog 前返回，后台任务仍可复用。"""
    import asyncio

    monkeypatch.setattr(server, "settings", _settings(tmp_path, ttl=600))
    monkeypatch.setattr(server, "DRAW_WAITER_DEADLINE_SECONDS", 0.01)

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_job(_description: str, _public_base_url: str) -> str:
        started.set()
        await release.wait()
        return "{}"

    monkeypatch.setattr(server, "_run_draw_job", slow_job)

    async def exercise() -> None:
        task = asyncio.create_task(server.hutuji_draw("超时复用测试"))
        await started.wait()
        with pytest.raises(server.DrawTimeoutError, match="绘图生成超时"):
            await task
        work = server._draw_inflight["超时复用测试"]
        assert not work.done()
        release.set()
        await work

    asyncio.run(exercise())
    server._draw_inflight.clear()

def test_draw_returns_matching_png_preview_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一次描摹必须产出可显示的 PNG 预览，并与 G-code 共用 capability stem。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(server, "settings", _settings(tmp_path, ttl=600))

    async def fake_bitmap(_desc: str, **_kw) -> bitmap_svg.BitmapResult:
        return bitmap_svg.BitmapResult(svg="<svg/>", preview_png=b"\x89PNG\r\n\x1a\npreview")

    async def fake_convert(_svg_path: Path, gcode_path: Path) -> SimpleNamespace:
        gcode_path.write_text(_minimal_gcode(), encoding="utf-8")
        return SimpleNamespace(lines=6, bytes=gcode_path.stat().st_size, pen_lifts=0)

    stem = "draw_20260731_120000_abcdefghijklmnopqrstuv"
    monkeypatch.setattr(server, "generate_bitmap", fake_bitmap)
    monkeypatch.setattr(server, "_new_basename", lambda: stem)
    monkeypatch.setattr(server, "svg_to_gcode_async", fake_convert)
    monkeypatch.setattr(
        server,
        "_draw_limiter",
        server.DrawLimiter(per_second=100, per_minute=100, max_concurrent=2),
    )

    result = json.loads(asyncio.run(server.hutuji_draw("五角星")))

    assert result["url"] == f"https://draw.example/files/{stem}.gcode"
    assert result["preview_url"] == f"https://draw.example/files/{stem}.png"
    assert (tmp_path / f"{stem}.png").read_bytes() == b"\x89PNG\r\n\x1a\npreview"



def _mock_device_api(monkeypatch, handler):
    """把 server 里的 httpx.AsyncClient 换成 MockTransport 版（同 activation_relay 夹具）。"""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def _factory(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("hutuji_mcp.server.httpx.AsyncClient", _factory)
    return transport


def _stub_draw_pipeline(monkeypatch, stem: str) -> None:
    async def fake_bitmap(_desc: str, **_kw) -> bitmap_svg.BitmapResult:
        return bitmap_svg.BitmapResult(svg="<svg/>", preview_png=b"\x89PNG\r\n\x1a\npreview")

    async def fake_convert(_svg_path: Path, gcode_path: Path) -> SimpleNamespace:
        gcode_path.write_text(_minimal_gcode(), encoding="utf-8")
        return SimpleNamespace(lines=6, bytes=gcode_path.stat().st_size, pen_lifts=0)

    monkeypatch.setattr(server, "generate_bitmap", fake_bitmap)
    monkeypatch.setattr(server, "_new_basename", lambda: stem)
    monkeypatch.setattr(server, "svg_to_gcode_async", fake_convert)
    monkeypatch.setattr(
        server,
        "_draw_limiter",
        server.DrawLimiter(per_second=100, per_minute=100, max_concurrent=2),
    )


def test_draw_chains_device_draw_when_token_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-3：token 配置时，draw 唯一完成点必须链式调用设备 hutuji.draw（双参数齐全、
    Bearer 头正确、端点为官方 /messaging/device/tools/call）。"""
    stem = "draw_20260824_120000_aaaaaaaaaaaaaaaaaaaaaa"
    monkeypatch.setattr(server, "settings", _settings(tmp_path, xiaozhi_device_token="dev-token"))
    _stub_draw_pipeline(monkeypatch, stem)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"success": True})

    _mock_device_api(monkeypatch, handler)

    async def run() -> dict:
        result = json.loads(await server.hutuji_draw("五角星"))
        await asyncio.sleep(0.05)
        return result

    result = asyncio.run(run())

    assert result["url"] == f"https://draw.example/files/{stem}.gcode"
    assert len(calls) == 1
    body = json.loads(calls[0].content)
    assert body["name"] == "hutuji.draw"
    assert body["arguments"] == {"url": result["url"], "preview_url": result["preview_url"]}
    assert calls[0].headers["authorization"] == "Bearer dev-token"
    assert calls[0].url.path == "/api/messaging/device/tools/call"


def test_draw_skips_chain_when_token_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-3：token 为空 = 链式禁用，不得发起任何设备 API 调用（云端两步是合法回落）。"""
    stem = "draw_20260824_120001_bbbbbbbbbbbbbbbbbbbbbb"
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    _stub_draw_pipeline(monkeypatch, stem)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"success": True})

    _mock_device_api(monkeypatch, handler)

    async def run() -> dict:
        result = json.loads(await server.hutuji_draw("五角星"))
        await asyncio.sleep(0.05)
        return result

    result = asyncio.run(run())

    assert result["url"].endswith(".gcode")
    assert calls == []


def test_draw_chain_failure_does_not_break_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-3：链式失败（设备 API 500）只记日志，draw 结果与产物不受任何影响。"""
    stem = "draw_20260824_120002_cccccccccccccccccccccc"
    monkeypatch.setattr(server, "settings", _settings(tmp_path, xiaozhi_device_token="dev-token"))
    _stub_draw_pipeline(monkeypatch, stem)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _mock_device_api(monkeypatch, handler)

    async def run() -> dict:
        result = json.loads(await server.hutuji_draw("五角星"))
        await asyncio.sleep(0.05)
        return result

    result = asyncio.run(run())

    assert result["url"] == f"https://draw.example/files/{stem}.gcode"
    assert (tmp_path / f"{stem}.gcode").is_file()


def test_draw_retry_same_description_chains_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-3 幂等：同题两个 waiter 复用同一任务（single-flight），链式只触发一次。"""
    stem = "draw_20260824_120003_dddddddddddddddddddddd"
    monkeypatch.setattr(server, "settings", _settings(tmp_path, xiaozhi_device_token="dev-token"))
    _stub_draw_pipeline(monkeypatch, stem)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"success": True})

    _mock_device_api(monkeypatch, handler)

    async def run() -> tuple[str, str]:
        first = asyncio.create_task(server.hutuji_draw("五角星"))
        second = asyncio.create_task(server.hutuji_draw("五角星"))
        ra, rb = await asyncio.gather(first, second)
        await asyncio.sleep(0.05)
        return ra, rb

    ra, rb = asyncio.run(run())

    assert json.loads(ra)["url"] == json.loads(rb)["url"]
    assert len(calls) == 1


def test_draw_reuses_gcode_content_for_same_svg(

    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一 SVG 的 vpype 结果是确定内容：命中缓存时跳过子进程，仍写新 capability 文件。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(server, "settings", _settings(tmp_path, ttl=600))
    server._gcode_cache.clear()

    async def fake_bitmap(_desc: str, **_kw) -> bitmap_svg.BitmapResult:
        return bitmap_svg.BitmapResult(svg="<svg><line x1='1' y1='1' x2='2' y2='2'/></svg>", preview_png=b"png")

    stems = iter(["draw_20260819_120000_aaaaaaaaaaaaaaaaaaaaaa",
                  "draw_20260819_120001_bbbbbbbbbbbbbbbbbbbbbb"])
    converts = 0

    async def fake_convert(_svg_path: Path, gcode_path: Path) -> SimpleNamespace:
        nonlocal converts
        converts += 1
        gcode_path.write_text(_minimal_gcode(), encoding="utf-8")
        return SimpleNamespace(lines=6, bytes=gcode_path.stat().st_size, pen_lifts=0)

    monkeypatch.setattr(server, "generate_bitmap", fake_bitmap)
    monkeypatch.setattr(server, "_new_basename", lambda: next(stems))
    monkeypatch.setattr(server, "svg_to_gcode_async", fake_convert)
    monkeypatch.setattr(
        server,
        "_draw_limiter",
        server.DrawLimiter(per_second=100, per_minute=100, max_concurrent=2),
    )

    first = json.loads(asyncio.run(server.hutuji_draw("同题缓存兔")))
    second = json.loads(asyncio.run(server.hutuji_draw("同题缓存兔")))
    assert converts == 1, "同一 SVG 第二次不得再起 vpype 子进程"
    assert first["lines"] == second["lines"] and first["bytes"] == second["bytes"]
    assert first["url"] != second["url"], "每次仍须发放独立随机 capability 名"
    for result in (first, second):
        name = result["url"].rsplit("/", 1)[-1]
        assert (tmp_path / name).read_text(encoding="utf-8") == _minimal_gcode()
    server._gcode_cache.clear()


def test_draw_does_not_cache_failed_gcode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """vpype/校验失败不得写 G-code 内容缓存；下一单必须真重跑转换。"""
    from hutuji_mcp import bitmap_svg, gcode_gen

    monkeypatch.setattr(server, "settings", _settings(tmp_path, ttl=600))
    server._gcode_cache.clear()

    async def fake_bitmap(_desc: str, **_kw) -> bitmap_svg.BitmapResult:
        return bitmap_svg.BitmapResult(svg="<svg/>", preview_png=b"png")

    stems = iter(["draw_20260819_120002_cccccccccccccccccccccc",
                  "draw_20260819_120003_dddddddddddddddddddddd"])
    converts = 0

    async def fake_convert(_svg_path: Path, gcode_path: Path) -> SimpleNamespace:
        nonlocal converts
        converts += 1
        if converts == 1:
            raise gcode_gen.GcodeError("vpype 瞬态失败")
        gcode_path.write_text(_minimal_gcode(), encoding="utf-8")
        return SimpleNamespace(lines=6, bytes=gcode_path.stat().st_size, pen_lifts=0)

    monkeypatch.setattr(server, "generate_bitmap", fake_bitmap)
    monkeypatch.setattr(server, "_new_basename", lambda: next(stems))
    monkeypatch.setattr(server, "svg_to_gcode_async", fake_convert)
    monkeypatch.setattr(
        server,
        "_draw_limiter",
        server.DrawLimiter(per_second=100, per_minute=100, max_concurrent=2),
    )

    with pytest.raises(gcode_gen.GcodeError, match="vpype 瞬态失败"):
        asyncio.run(server.hutuji_draw("失败不缓存鸟"))
    ok = json.loads(asyncio.run(server.hutuji_draw("失败不缓存鸟")))
    assert converts == 2
    assert ok["lines"] == 6
    server._gcode_cache.clear()

def test_serve_png_preview_with_image_media_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = "draw_20260731_120000_abcdefghijklmnopqrstuv.png"
    data = b"\x89PNG\r\n\x1a\npreview"
    (tmp_path / name).write_bytes(data)

    response = asyncio.run(server._serve_file(name))

    assert response.media_type == "image/png"
    assert response.body == data
    assert response.headers["x-hutuji-crc32"] == f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"


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
        "G1 X10 Y10 F8000\n"
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
    motion = "G1 X10 Y10 F8000\n"
    base = _minimal_gcode(motion)
    text = _minimal_gcode(motion + _size_filler(MAX_BYTES - len(base.encode("utf-8"))))
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
    motion = "G1 X10 Y10 F8000\n"
    base = _minimal_gcode(motion)
    text = _minimal_gcode(motion + _size_filler(MAX_BYTES - len(base.encode("utf-8"))))
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
    raw = _minimal_gcode("G1 X10 Y10 F8000\n").replace("\n", "\r\n").encode("ascii")
    p = tmp_path / "ok_crlf.gcode"
    p.write_bytes(raw)
    stats = _read_generated_gcode(p)
    assert stats.bytes == len(raw) == p.stat().st_size
    # 塌陷计数确实更小，证明这条断言不是恒真。
    assert len(p.read_text(encoding="utf-8").encode("utf-8")) < len(raw)


def test_validate_gcode_accepts_explicit_total_bytes_override() -> None:
    """`total_bytes` 显式传入时优先于文本推算，且缺省行为不变（纯文本场景）。"""
    text = _minimal_gcode("G1 X10 Y10 F8000\n")
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
    validate_gcode(_minimal_gcode("G1 X10 Y10 F8000\n" + ok_line + "\n"))

    # 255 字节 payload：必须拒。这一条是本次修复的核心——旧口径 `> 255` 会放行。
    bad_line = ";" + "x" * MAX_LINE_PAYLOAD_BYTES
    assert len(bad_line.encode("ascii")) == MAX_LINE_PAYLOAD_BYTES + 1
    with pytest.raises(GcodeError, match=r"255 字节超上限 254"):
        validate_gcode(_minimal_gcode("G1 X10 Y10 F8000\n" + bad_line + "\n"))

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
    text = _minimal_gcode(
        "G1 X10 Y10 F8000\n" + "G1G90 Z0.0F10000\n" * (PEN_LIFT_WARN_THRESHOLD + 1)
    )
    with caplog.at_level(logging.WARNING):
        stats = validate_gcode(text)
    assert stats.pen_lifts == PEN_LIFT_WARN_THRESHOLD + 4
    assert "仍放行" in caplog.text


def test_capability_download_has_integrity_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = f"{server._new_basename()}.gcode"
    data = _minimal_gcode("G1 X10 Y10 F8000\n").encode("ascii")
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
    gcode.write_text(_minimal_gcode(), encoding="utf-8")
    svg.write_text("<svg/>", encoding="utf-8")
    old = time.time() - 601
    os.utime(gcode, (old, old))
    os.utime(svg, (old, old))

    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(gcode.name))

    assert caught.value.status_code == 404
    assert not gcode.exists()
    assert not svg.exists()


def test_stale_gcode_over_y_limit_returns_404_and_deletes_triplet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """服务时复核：限幅收紧前落盘、TTL 未到的旧产物（Y=200 > 190）必须按过期
    同处置——统一 404 并删除同 stem 三件套；重试同一 URL 不得再拿到越界内容。"""
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    base = server._new_basename()
    gcode = tmp_path / f"{base}.gcode"
    svg = tmp_path / f"{base}.svg"
    png = tmp_path / f"{base}.png"
    gcode.write_text(_minimal_gcode("G0 X100 Y200 F8000\n"), encoding="utf-8")
    svg.write_text("<svg/>", encoding="utf-8")
    png.write_bytes(b"\x89PNG")

    with caplog.at_level(logging.WARNING, logger="hutuji_mcp.server"):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(server._serve_file(gcode.name))

    assert caught.value.status_code == 404
    assert not gcode.exists()
    assert not svg.exists()
    assert not png.exists()
    # 脱敏硬约定：warning 只允许出现 token 掩码名，不得泄露完整 capability 名。
    assert any("_*.gcode" in record.getMessage() for record in caplog.records)
    assert all(base not in record.getMessage() for record in caplog.records)


def test_stale_gcode_non_utf8_returns_404_and_deletes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """落盘后被改写成非 UTF-8 字节：解码失败按陈旧产物处理，404 并删除。"""
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = f"{server._new_basename()}.gcode"
    path = tmp_path / name
    path.write_bytes(b"\xff\xfe\x00binary-garbage")

    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(name))

    assert caught.value.status_code == 404
    assert not path.exists()


def test_valid_gcode_passes_serve_time_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """合法产物照常下发：复核只拦违约内容，不改变正常下载的体与完整性头。"""
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = f"{server._new_basename()}.gcode"
    data = _minimal_gcode("G1 X10 Y10 F8000\n").encode("ascii")
    (tmp_path / name).write_bytes(data)

    resp = asyncio.run(server._serve_file(name))

    assert resp.body == data
    assert resp.headers["x-hutuji-crc32"] == f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"


def test_stale_delete_skipped_when_path_swapped_after_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """读取后、删除前路径被换成新对象（mtime 变化）：跳过删除以免误删新产物，
    但本次响应仍是 404——旧内容绝不下发。"""
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    base = server._new_basename()
    gcode = tmp_path / f"{base}.gcode"
    svg = tmp_path / f"{base}.svg"
    gcode.write_text(_minimal_gcode("G0 X100 Y200 F8000\n"), encoding="utf-8")
    svg.write_text("<svg/>", encoding="utf-8")

    real_read = server._read_regular_file

    def _read_then_swap(path: Path) -> tuple[bytes, float]:
        result = real_read(path)
        # 模拟并发替换：换入一份新的合法产物，并把 mtime 显式错开。
        replacement = tmp_path / f"{base}.gcode"
        replacement.write_text(_minimal_gcode("G0 X10 Y10 F8000\n"), encoding="utf-8")
        os.utime(replacement, (result[1] + 5, result[1] + 5))
        return result

    monkeypatch.setattr(server, "_read_regular_file", _read_then_swap)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(gcode.name))

    assert caught.value.status_code == 404
    assert gcode.exists(), "路径已换成新对象时不得误删"
    assert svg.exists(), "路径已换成新对象时整对删除都应跳过"


def test_capability_expiring_exactly_at_ttl_is_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R14-TG-03：`_is_expired` 边界是 `>=`——age 恰等于 ttl 的文件必须按过期
    处置；若退化成 `>`，边界文件会被错误续命一个判定周期。"""
    monkeypatch.setattr(server, "settings", _settings(tmp_path, ttl=600))
    base = server._new_basename()
    gcode = tmp_path / f"{base}.gcode"
    gcode.write_text("G21\n", encoding="utf-8")
    now = time.time()
    os.utime(gcode, (now - 600, now - 600))
    assert server._is_expired(gcode, now=now) is True
    os.utime(gcode, (now - 599, now - 599))
    assert server._is_expired(gcode, now=now) is False


def test_draw_limiter_rejects_rate_and_concurrency() -> None:
    now = [0.0]
    limiter = server.DrawLimiter(clock=lambda: now[0])

    asyncio.run(limiter.enter())
    with pytest.raises(server.DrawLimitError, match="绘图服务正忙"):
        asyncio.run(limiter.enter())

    now[0] = 1.1
    asyncio.run(limiter.enter())
    now[0] = 2.2
    with pytest.raises(server.DrawLimitError, match="绘图服务正忙"):
        asyncio.run(limiter.enter())

    asyncio.run(limiter.leave())
    asyncio.run(limiter.leave())

def test_draw_limiter_message_is_user_facing_and_logs_counts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CloudUX-F4：限流文案用户向（无技术数字、无 每秒/并发/每分钟 字样），
    技术计数进日志——不因文案去技术化而丢失运维定位信息。"""
    limiter = server.DrawLimiter(per_second=1, per_minute=100, max_concurrent=2)
    asyncio.run(limiter.enter())
    with caplog.at_level(logging.INFO, logger="hutuji_mcp.server"):
        with pytest.raises(server.DrawLimitError) as exc_info:
            asyncio.run(limiter.enter())
    msg = str(exc_info.value)
    assert msg == "绘图服务正忙，请稍候再试"
    assert not any(ch.isdigit() for ch in msg), "文案不得带技术数字"
    assert all(word not in msg for word in ("每秒", "并发", "每分钟"))
    assert any("每秒请求数达上限 1" in record.getMessage() for record in caplog.records)

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

    async def fake_generate_svg(_description: str, **_kw) -> SimpleNamespace:
        return SimpleNamespace(svg="<svg/>", preview_png=b"png")

    async def exercise() -> None:
        started = 0
        both_started = asyncio.Event()
        release = asyncio.Event()

        async def fake_convert(_svg_path: Path, gcode_path: Path) -> SimpleNamespace:
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await release.wait()
            gcode_path.write_text(_minimal_gcode(), encoding="utf-8")
            return SimpleNamespace(lines=6, bytes=gcode_path.stat().st_size, pen_lifts=0)

        monkeypatch.setattr(server, "generate_bitmap", fake_generate_svg)
        monkeypatch.setattr(server, "svg_to_gcode_async", fake_convert)
        tasks = [
            asyncio.create_task(server.hutuji_draw("first")),
            asyncio.create_task(server.hutuji_draw("second")),
        ]
        await asyncio.wait_for(both_started.wait(), timeout=1)
        try:
            with pytest.raises(server.DrawLimitError, match="绘图服务正忙"):
                await server.hutuji_draw("third")
            assert started == 2
        finally:
            release.set()
            await asyncio.gather(*tasks)

    asyncio.run(exercise())


def test_draw_cancelled_retries_share_one_job_and_limiter_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """云端约 35s 取消并重试时，同题三次调用只能拥有一个后台生成作业。

    取消的是等待该结果的 JSON-RPC 调用，不是已计费的生成事务。事务必须独立持有
    DrawLimiter 名额；重试只搭车，不能另占名额、另调生图或另起 vpype。
    """
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    monkeypatch.setattr(
        server,
        "_draw_limiter",
        server.DrawLimiter(per_second=100, per_minute=100, max_concurrent=1),
    )
    getattr(server, "_draw_inflight", {}).clear()
    server._gcode_cache.clear()

    started = asyncio.Event()
    release = asyncio.Event()
    bitmap_calls = 0
    convert_calls = 0

    async def fake_bitmap(description: str, **_kw) -> SimpleNamespace:
        nonlocal bitmap_calls
        bitmap_calls += 1
        assert description.strip() == "重试猫"
        started.set()
        await release.wait()
        return SimpleNamespace(svg="<svg/>", preview_png=b"png")

    async def fake_convert(_svg_path: Path, gcode_path: Path) -> SimpleNamespace:
        nonlocal convert_calls
        convert_calls += 1
        gcode_path.write_text(_minimal_gcode(), encoding="utf-8")
        return SimpleNamespace(lines=6, bytes=gcode_path.stat().st_size, pen_lifts=0)

    monkeypatch.setattr(server, "generate_bitmap", fake_bitmap)
    monkeypatch.setattr(server, "svg_to_gcode_async", fake_convert)
    monkeypatch.setattr(
        server,
        "_new_basename",
        lambda: "draw_20260820_120000_abcdefghijklmnopqrstuv",
    )

    async def exercise() -> None:
        callers: list[asyncio.Task] = []
        try:
            first = asyncio.create_task(server.hutuji_draw("  重试猫  "))
            callers.append(first)
            await asyncio.wait_for(started.wait(), timeout=1)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(first, timeout=1)

            assert server._draw_limiter._active == 1, "生成事务而非已取消 waiter 持有名额"

            second = asyncio.create_task(server.hutuji_draw("重试猫"))
            callers.append(second)
            for _ in range(3):
                await asyncio.sleep(0)
            assert bitmap_calls == 1
            assert server._draw_limiter._active == 1
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(second, timeout=1)

            third = asyncio.create_task(server.hutuji_draw("重试猫"))
            callers.append(third)
            for _ in range(3):
                await asyncio.sleep(0)
            assert bitmap_calls == 1
            assert server._draw_limiter._active == 1

            release.set()
            result = json.loads(await asyncio.wait_for(third, timeout=1))
            assert result["lines"] == 6
            assert bitmap_calls == 1
            assert convert_calls == 1
            assert server._draw_limiter._active == 0
            assert not server._draw_inflight
        finally:
            release.set()
            for caller in callers:
                if not caller.done():
                    caller.cancel()
            await asyncio.gather(*callers, return_exceptions=True)
            jobs = list(getattr(server, "_draw_inflight", {}).values())
            for job in jobs:
                if not job.done():
                    job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            getattr(server, "_draw_inflight", {}).clear()

    asyncio.run(exercise())
    server._gcode_cache.clear()


def test_draw_cancellation_removes_partial_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    monkeypatch.setattr(
        server,
        "_draw_limiter",
        server.DrawLimiter(per_second=100, per_minute=100, max_concurrent=2),
    )

    server._gcode_cache.clear()

    async def fake_generate_svg(_description: str, **_kw) -> SimpleNamespace:
        return SimpleNamespace(svg="<svg/>", preview_png=b"png")

    async def exercise() -> None:
        started = asyncio.Event()

        async def fake_convert(_svg_path: Path, gcode_path: Path) -> None:
            gcode_path.write_text("partial", encoding="utf-8")
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(server, "generate_bitmap", fake_generate_svg)
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

    async def fake_bitmap(_desc: str, **_kw) -> SimpleNamespace:
        called.append("bitmap")
        return SimpleNamespace(svg="<svg/>", preview_png=b"png")

    async def fake_llm(_desc: str) -> str:
        raise AssertionError("draw 不得走 LLM 直写路线")

    async def fake_convert(_svg_path: Path, gcode_path: Path) -> SimpleNamespace:
        gcode_path.write_text(_minimal_gcode(), encoding="utf-8")
        return SimpleNamespace(lines=6, bytes=gcode_path.stat().st_size, pen_lifts=0)

    monkeypatch.setattr(server, "generate_bitmap", fake_bitmap)
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


def _irregular_solid_blob_png() -> bytes:
    """白底 200x200：左上不规则实心块（模拟生图把眼睛画成实心）+ 右下细横线对照。"""
    import io
    import struct
    import zlib

    import imageio.v3 as iio
    import numpy as np

    rng = np.random.default_rng(7)
    w = h = 200
    img = np.full((h, w), 255, dtype=np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt((yy - 70) ** 2 + (xx - 70) ** 2 / 1.6)
    wobble = rng.uniform(0.85, 1.15, size=(h, w))
    img[r * wobble <= 30] = 0
    img[150:152, 120:180] = 0
    buf = io.BytesIO()
    iio.imwrite(buf, img, extension=".png")
    assert struct.unpack(">I", buf.getvalue()[33:37])  # PNG 长度字段自检防呆
    _ = zlib.crc32  # 保持 zlib 引用（未来改手写编码时不静默丢依赖）
    return buf.getvalue()


def test_bitmap_trace_solid_blob_becomes_ring() -> None:
    """实心色块→轮廓环（2026-08-26 用户主诉「AI 画的都有问题」）：生图模型偶发
    把眼睛/鼻头画成实心块，骨架化会把实心盘变成放射乱麻；描摹前须转成外环。
    判别面实测：朴素 14 段/374px 乱麻 vs 修复 7 段/220px 弧（本仓 rng=7 夹具）。"""
    from hutuji_mcp import bitmap_svg

    polys, _w, _h = bitmap_svg.trace_bitmap_to_polylines(_irregular_solid_blob_png())
    total = sum(
        float((((p[1:] - p[:-1]) ** 2).sum(axis=1) ** 0.5).sum()) for p in polys
    )
    assert len(polys) <= 9, polys
    assert total <= 300, total
    # 对照细线必须原样保留（清理不得吃掉正常笔画）
    longest = max(
        float((((p[1:] - p[:-1]) ** 2).sum(axis=1) ** 0.5).sum()) for p in polys
    )
    assert longest >= 55, longest


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


def test_bitmap_trace_rejects_single_side_overflow() -> None:
    """单边超过 _MAX_IMAGE_SIDE 即拒：sknw int16 坐标域溢出会静默回绕成负值。

    3×40000 总像素仅 12 万（远低于 5 Mpx），但宽度超 32767 后 sknw idx2rc
    的 int16 回绕产负坐标，画面静默扭曲而不报错——fit-to-margins 会把负坐标
    归一化回页内，validate_gcode 照样绿。故单边闸必须独立于总像素闸。
    """
    from hutuji_mcp import bitmap_svg

    with pytest.raises(bitmap_svg.BitmapError, match=r"单边过大.*int16"):
        bitmap_svg.trace_bitmap_to_polylines(_valid_ihdr_png(3, 40000))
    with pytest.raises(bitmap_svg.BitmapError, match=r"单边过大.*int16"):
        bitmap_svg.trace_bitmap_to_polylines(_valid_ihdr_png(40000, 3))


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


def _circle_with_chord_png_bytes() -> bytes:
    """200×200 圆环+水平弦：弦把圆切成两条平行弧。

    两个交叉节点之间存在 3 条边（上弧、下弧、弦），是「同一对节点间多重边」
    的最小构图——太阳+光芒、气球+绳、眼镜、轮子+轴线都属此形态。
    """
    import io
    import math as _math

    import imageio.v3 as iio
    import numpy as np

    img = np.full((200, 200), 255, dtype=np.uint8)
    cx = cy = 100
    radius = 70
    for t in np.linspace(0.0, 2.0 * _math.pi, 20000):
        xi = int(cx + radius * _math.cos(t))
        yi = int(cy + radius * _math.sin(t))
        img[yi, xi] = 0
    img[cy, cx - radius : cx + radius] = 0
    buf = io.BytesIO()
    iio.imwrite(buf, img, extension=".png")
    return buf.getvalue()


def _pt_poly_dist(pt: tuple, poly) -> float:
    """点到折线最近线段距离（线段距离，非点到点——抽稀后长弦段内无中间顶点，
    点到点度量会把合法内容判成「缺线」）。"""
    import numpy as np

    p = np.asarray(pt, dtype=float)
    a = poly[:-1]
    b = poly[1:]
    ab = b - a
    denom = np.einsum("ij,ij->i", ab, ab)
    t = np.zeros(len(ab))
    nz = denom > 1e-18
    t[nz] = np.clip(np.einsum("ij,ij->i", p - a[nz], ab[nz]) / denom[nz], 0, 1)
    proj = a + t[:, None] * ab
    return float(np.min(np.hypot(proj[:, 0] - p[0], proj[:, 1] - p[1])))


def test_bitmap_trace_keeps_parallel_edges_between_same_nodes() -> None:
    """同节点对间的平行边必须逐条保留到描摹产物（R10-BMP-02 + 缝合后语义）。

    sknw 默认 multi=False 返回 nx.Graph，同一对节点间重复 add_edge 后写覆盖
    前写：圆环+弦的三条路径只剩一条，实测丢 63% 线条——SVG 合法、过白名单、
    过 §5，纸面直接缺线且零告警。必须以 MultiGraph 建图。
    2026-08-26 端点缝合上线后：节点相接的边合并成更少折线（抬笔更少、缺线
    反而为零），断言面从「折线数」改为「总点数内容保留」。
    2026-08-27 重标定（用户批准）：08-26 抽稀上线后总点数不再代表内容——
    本 fixture 528 点抽稀至 134 仍全部内容在位，旧 ≥500 点断言与抽稀数学上
    不可共存（HEAD 基线同红，a089288 遗留）。改覆盖率断言：圆+弦采样点到
    最近折线**线段**距离 ≤4px（实测最差 1.8px）。断言面不变：三路径缺任
    一采样即超阈。"""
    import math

    import numpy as np

    from hutuji_mcp import bitmap_svg

    polys, _w, _h = bitmap_svg.trace_bitmap_to_polylines(_circle_with_chord_png_bytes())
    samples = [
        (100 + 70 * math.cos(t), 100 + 70 * math.sin(t))
        for t in np.linspace(0, 2 * math.pi, 400)
    ]
    samples += [(x, 100) for x in range(30, 171, 5)]
    worst = max(min(_pt_poly_dist(s, p) for p in polys) for s in samples)
    assert worst <= 4.0, f"平行边内容被丢（最差覆盖距离 {worst:.2f}px）"


def test_stitch_polylines_merges_near_continuations() -> None:
    """端点缝合（2026-08-26 小狗虚线根因修复）：_MIN_POLY_PX 丢掉的短连接边
    让一条连通路径碎成 0.3-2mm 缺口的碎片（实测 66 段、2mm 合并后只剩 1 段），
    vpype linemerge 0.3mm 阈值够不着。缝合必须允许反转、不得误并远特征。"""
    import numpy as np

    from hutuji_mcp import bitmap_svg

    a = np.array([[0, 0], [5, 0], [10, 0]], dtype=float)
    b = np.array([[15, 0], [12, 0]], dtype=float)  # 反向续接 (10,0)→(12,0)
    c = np.array([[16, 0], [20, 0]], dtype=float)  # 正向续接 (15,0)→(16,0)
    d = np.array([[0, 60], [10, 60]], dtype=float)  # 远端独立特征

    out = bitmap_svg._stitch_polylines([a, b, c, d], 5.0)
    assert len(out) == 2, out
    merged = max(out, key=len)
    assert len(merged) == len(a) + len(b) + len(c) - 2
    lone = [p for p in out if len(p) == len(d)][0]
    assert np.array_equal(lone, d)


def test_stitch_polylines_preserves_all_content() -> None:
    """缝合不得丢任何输入点（拼接处只去重复尾点）。"""
    import numpy as np

    from hutuji_mcp import bitmap_svg
    a = np.array([[0, 0], [4, 3], [9, 6]], dtype=float)
    b = np.array([[10, 6], [14, 9]], dtype=float)
    out = bitmap_svg._stitch_polylines([a, b], 3.0)
    assert len(out) == 1
    merged = out[0]
    # 全链覆盖两端、内容点数 = 总点数 - 拼接去重（每接点丢 1 个重复尾点）
    assert np.array_equal(merged[0], a[0])
    assert np.array_equal(merged[-1], b[-1])
    assert len(merged) == len(a) + len(b) - 1


def test_decimate_polylines_collapses_pixel_staircase() -> None:
    """骨架像素阶梯必须抽稀：否则 layout 后 G1 中位 ~0.8mm，F8000 下 70% 段
    <10ms → planner 空档失能抬笔（2026-08-26 小路 829 段卡顿/没线）。

    不改锁定的 linesimplify=0.05（楼梯偏差常 >0.05mm 会保留阶梯点）；在像素
    域 Douglas-Peucker 把阶梯压掉。端点必须保留。"""
    import numpy as np

    from hutuji_mcp import bitmap_svg

    # 100 点水平阶梯：每步 (1, ±1) 锯齿，弦长偏差 ~0.7px，生产 tol=1.5 应收成两端附近
    xs = np.arange(100, dtype=float)
    ys = (xs % 2).astype(float)
    stair = np.column_stack([xs, ys])
    out = bitmap_svg._rdp_polylines([stair], 1.5)
    assert len(out) == 1
    dec = out[0]
    assert len(dec) <= 8, f"阶梯未抽稀：{len(dec)} 点"
    assert np.allclose(dec[0], stair[0])
    assert np.allclose(dec[-1], stair[-1])


def test_decimate_polylines_keeps_short_and_corners() -> None:
    """短折线不动；有真实拐角的折线不得被抽成直线。"""
    import numpy as np

    from hutuji_mcp import bitmap_svg

    short = np.array([[0, 0], [3, 0]], dtype=float)
    corner = np.array([[0, 0], [10, 0], [10, 10], [20, 10]], dtype=float)
    out = bitmap_svg._rdp_polylines([short, corner], 1.5)
    assert np.array_equal(out[0], short)
    assert len(out[1]) >= 3, "拐角被抹成直线"
    assert np.allclose(out[1][0], corner[0])
    assert np.allclose(out[1][-1], corner[-1])


def test_decimate_polylines_keeps_fine_features_at_production_tol() -> None:
    """生产 tol≈1.5px 不得抹掉老虎类细弯（半圆齿）——5.3px 曾把细节抽成折棍。"""
    import numpy as np

    from hutuji_mcp import bitmap_svg

    # 半径 8px 的半圆像素阶梯：细特征，tol=1.5 应保留多点；tol=5.3 会塌成弦
    n = 40
    ang = np.linspace(0, np.pi, n)
    arc = np.column_stack([50 + 8 * np.cos(ang), 50 + 8 * np.sin(ang)])
    arc = np.round(arc).astype(float)
    mild = bitmap_svg._rdp_polylines([arc], 1.5)[0]
    harsh = bitmap_svg._rdp_polylines([arc], 5.3)[0]
    assert len(mild) >= 5, f"生产 tol 抹细弯：{len(mild)} 点"
    assert len(harsh) <= 4, f"对照 tol=5.3 应更狠：{len(harsh)} 点"
    assert len(mild) > len(harsh)


def test_bitmap_trace_rejects_skeleton_over_sknw_trace_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """骨架总像素超过 sknw trace() 缓冲预算必须显式拒绝（R10-BMP-01）。

    sknw 的 trace() 用固定 np.zeros(131072) 缓冲且 numba nopython 无边界检查，
    单条长边越界写堆内存，实测 0xC0000005 直接杀死整个进程——asyncio.to_thread
    线程里的 C 层崩溃无法被 Python 捕获。缩小预算常量证明闸在 build_sknw
    之前、且以常量为准（真造 13 万像素骨架会太慢）。
    """
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(bitmap_svg, "_SKNW_TRACE_BUDGET_PX", 10)
    with pytest.raises(bitmap_svg.BitmapError, match="骨架过长"):
        bitmap_svg.trace_bitmap_to_polylines(_star_png_bytes())


def test_bitmap_sknw_trace_budget_pinned_to_buffer_size() -> None:
    """预算钉死 131071 = sknw trace() 的 131072 缓冲 - 1。

    这不是画质参数：抬它必须先证明 sknw 的缓冲同步变大（或上游加了边界检查），
    否则「预算 > 缓冲」= 恢复进程级崩溃。
    """
    from hutuji_mcp import bitmap_svg

    assert bitmap_svg._SKNW_TRACE_BUDGET_PX == 131071


def test_bitmap_trace_wraps_numeric_stack_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """描摹计算段的非预期异常必须包成 BitmapError（调用方只准备接 BitmapError）。

    gaussian/otsu/skeletonize/build_sknw 此前不在任何 try 内，numba 运行期
    TypingError 等罕见异常会裸穿模块契约变成 500。
    """
    import sknw as _sknw

    from hutuji_mcp import bitmap_svg

    def boom(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("numba 内部错误")

    monkeypatch.setattr(_sknw, "build_sknw", boom)
    with pytest.raises(bitmap_svg.BitmapError, match=r"位图描摹失败.*RuntimeError"):
        bitmap_svg.trace_bitmap_to_polylines(_star_png_bytes())


def test_bitmap_generate_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(llm_api_key="", llm_base_url="https://x.invalid/v1", image_model="m"),
    )
    with pytest.raises(bitmap_svg.BitmapError, match="绘图服务未就绪"):
        asyncio.run(bitmap_svg.generate_bitmap_svg("test"))


def test_bitmap_generate_rejects_blank_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空白主题在进入计费请求前必须被拒（MCPDetail-F2，位图路线）。

    两条生成路线共用 _sanitize_description，这里钉位图路线的行为；LlmError
    从位图路径冒出可接受（文案用户向）。"""
    from hutuji_mcp import bitmap_svg
    from hutuji_mcp.llm_svg import LlmError

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="m"
        ),
    )
    with pytest.raises(LlmError, match="描述为空"):
        asyncio.run(bitmap_svg.generate_bitmap_svg(" \u3000 "))


def test_bitmap_fetch_rejects_blank_image_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """HUTUJI_IMAGE_MODEL 空白必须在本地拦下：空 model 名不该发进计费请求。
    R21 起用户面消息统一「绘图服务未就绪」，环境变量名挪进 warning 日志。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="  "
        ),
    )
    with pytest.raises(bitmap_svg.BitmapError, match="绘图服务未就绪"):
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



def _sensenova_settings() -> SimpleNamespace:
    return SimpleNamespace(
        llm_api_key="agnes-key",
        llm_base_url="https://agnes.invalid/v1",
        image_model="agnes-image",
        image_fallback_url="",
        sensenova_api_key="sense-key",
        sensenova_base_url="https://token.sensenova.invalid/v1",
        sensenova_image_model="sensenova-u1-fast",
    )


def test_bitmap_fetch_uses_sensenova_official_image_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对冲下商汤快于 Agnes 时必须用商汤结果，且 U1 Fast 请求字段逐字钉死。"""
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(bitmap_svg, "settings", _sensenova_settings())
    calls: list[dict] = []
    agnes_calls = 0

    async def handler(request):  # noqa: ANN001, ANN202
        nonlocal agnes_calls
        if request.url.host == "token.sensenova.invalid":
            calls.append(json.loads(request.content))
            assert request.headers["authorization"] == "Bearer sense-key"
            return _httpx.Response(200, json={"data": [{"url": "https://cdn.example/u1.png"}]})
        if request.url.host == "agnes.invalid":
            agnes_calls += 1
            # 对冲赛跑：Agnes 慢一拍，商汤必赢；赢家不得等输家出图。
            await asyncio.sleep(0.2)
            return _httpx.Response(200, json={"data": [{"url": "https://cdn.example/a.png"}]})
        # 两侧下载内容不同，赢家可由最终 body 判定。
        if request.url.path.endswith("/u1.png"):
            return _httpx.Response(200, content=_png_stub())
        return _httpx.Response(200, content=b"AGNES-WON")

    body = asyncio.run(
        bitmap_svg._fetch_image_png("五角星", transport=_httpx.MockTransport(handler))
    )
    assert body == _png_stub()
    assert calls == [
        {
            "model": "sensenova-u1-fast",
            "prompt": bitmap_svg._BITMAP_STYLE + "五角星",
            "size": "1760x2368",
            "n": 1,
            "watermark": False,
        }
    ]
    # 对冲并发发起 Agnes；商汤赢时 Agnes 被取消，其请求至多一次。
    assert agnes_calls <= 1

def test_bitmap_fetch_sensenova_failure_falls_back_to_agnes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对冲中商汤失败时必须用 Agnes 结果；商汤单次尝试不重试（低延迟优先）。"""
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(bitmap_svg, "settings", _sensenova_settings())
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    calls = {"sense": 0, "agnes": 0}

    def handler(request):  # noqa: ANN001, ANN202
        if request.url.host == "token.sensenova.invalid":
            calls["sense"] += 1
            return _httpx.Response(500)
        if request.url.host == "agnes.invalid":
            calls["agnes"] += 1
            return _httpx.Response(200, json={"data": [{"url": "https://cdn.example/a.png"}]})
        return _httpx.Response(200, content=_png_stub())

    body = asyncio.run(
        bitmap_svg._fetch_image_png("城堡", transport=_httpx.MockTransport(handler))
    )
    assert body == _png_stub()
    assert calls == {"sense": 1, "agnes": 1}


def test_bitmap_fetch_hedge_agnes_wins_when_sensenova_slow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """商汤慢于 Agnes 时必须用 Agnes 结果——并发对冲判据（串行实现会等商汤）。"""
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(bitmap_svg, "settings", _sensenova_settings())
    calls = {"sense": 0, "agnes": 0}

    async def handler(request):  # noqa: ANN001, ANN202
        if request.url.host == "token.sensenova.invalid":
            calls["sense"] += 1
            await asyncio.sleep(0.3)
            return _httpx.Response(200, json={"data": [{"url": "https://cdn.example/u1.png"}]})
        if request.url.host == "agnes.invalid":
            calls["agnes"] += 1
            return _httpx.Response(200, json={"data": [{"url": "https://cdn.example/a.png"}]})
        if request.url.path.endswith("/a.png"):
            return _httpx.Response(200, content=_png_stub())
        return _httpx.Response(200, content=b"SENSE-WON")

    body = asyncio.run(
        bitmap_svg._fetch_image_png("城堡", transport=_httpx.MockTransport(handler))
    )
    assert body == _png_stub(), "Agnes 先出图时必须用 Agnes 结果并取消商汤"
    assert calls == {"sense": 1, "agnes": 1}, "对冲必须并发发起两侧"


def test_bitmap_fetch_hedge_both_fail_report_both_sides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对冲双失败错误须带两侧原因（顺序无关）；备用端点置空时直接抛出。"""
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(bitmap_svg, "settings", _sensenova_settings())
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))

    def handler(request):  # noqa: ANN001, ANN202
        return _httpx.Response(500)

    with pytest.raises(bitmap_svg.BitmapError) as exc_info:
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=_httpx.MockTransport(handler)))
    msg = str(exc_info.value)
    assert "商汤生图失败" in msg
    assert "Agnes 同样失败" in msg


def _sensenova_fallback_settings() -> SimpleNamespace:
    """商汤三件套 + Agnes 三件套 + imagefree 备用 URL（三路对冲全开）。"""
    return SimpleNamespace(
        llm_api_key="agnes-key",
        llm_base_url="https://agnes.invalid/v1",
        image_model="agnes-image",
        image_fallback_url="https://fb.invalid/v1/generate",
        sensenova_api_key="sense-key",
        sensenova_base_url="https://token.sensenova.invalid/v1",
        sensenova_image_model="sensenova-u1-fast",
    )


def test_sensenova_retries_one_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """商汤单次 500 是实测瞬态故障：快速重试一次，第二次成功即返回 URL。"""
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(bitmap_svg, "settings", _sensenova_settings())
    monkeypatch.setattr(bitmap_svg, "_SENSENOVA_RETRY_DELAYS_SECONDS", (0.0,))
    calls = 0

    def handler(request):  # noqa: ANN001, ANN202
        nonlocal calls
        calls += 1
        if calls == 1:
            return _httpx.Response(500)
        return _httpx.Response(200, json={"data": [{"url": "https://cdn.example/u1.png"}]})

    async def run() -> str:
        async with _httpx.AsyncClient(transport=_httpx.MockTransport(handler)) as client:
            return await bitmap_svg._request_sensenova_image_url(client, "五角星")

    assert asyncio.run(run()) == "https://cdn.example/u1.png"
    assert calls == 2


def test_bitmap_generate_caches_completed_result_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一规范化主题 10 分钟内复用完整中心线结果，不重复计费请求或描摹。"""
    from hutuji_mcp import bitmap_svg

    bitmap_svg._bitmap_cache.clear()
    now = [100.0]
    monkeypatch.setattr(bitmap_svg, "_bitmap_cache_clock", lambda: now[0])
    calls = 0

    async def fake_fetch(_desc: str) -> bytes:
        nonlocal calls
        calls += 1
        return _star_png_bytes()

    monkeypatch.setattr(bitmap_svg, "_fetch_image_png", fake_fetch)
    first = asyncio.run(bitmap_svg.generate_bitmap("  五角星  "))
    second = asyncio.run(bitmap_svg.generate_bitmap("五角星"))
    assert first == second
    assert calls == 1

    now[0] += bitmap_svg._BITMAP_CACHE_TTL_SECONDS + 0.001
    third = asyncio.run(bitmap_svg.generate_bitmap("五角星"))
    assert third == first
    assert calls == 2, "TTL 到期必须重新生成，不能永久复用能力内容"
    bitmap_svg._bitmap_cache.clear()


def test_bitmap_generate_does_not_cache_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上游失败不得写缓存；下一次同主题必须真实重试。"""
    from hutuji_mcp import bitmap_svg

    bitmap_svg._bitmap_cache.clear()
    calls = 0

    async def fake_fetch(_desc: str) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise bitmap_svg.BitmapError("上游瞬态失败")
        return _star_png_bytes()

    monkeypatch.setattr(bitmap_svg, "_fetch_image_png", fake_fetch)
    with pytest.raises(bitmap_svg.BitmapError, match="上游瞬态失败"):
        asyncio.run(bitmap_svg.generate_bitmap("小猫"))
    result = asyncio.run(bitmap_svg.generate_bitmap("小猫"))
    assert result.svg.startswith("<svg")
    assert calls == 2
    bitmap_svg._bitmap_cache.clear()


def test_bitmap_generate_coalesces_concurrent_identical_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同题并发只发一次生图：语音端重试不得重复计费；两调用方各拿完整结果。"""
    from hutuji_mcp import bitmap_svg

    bitmap_svg._bitmap_cache.clear()
    bitmap_svg._bitmap_inflight.clear()
    calls = 0
    gate = asyncio.Event()

    async def fake_fetch(_desc: str) -> bytes:
        nonlocal calls
        calls += 1
        await asyncio.wait_for(gate.wait(), timeout=5)
        return _star_png_bytes()

    monkeypatch.setattr(bitmap_svg, "_fetch_image_png", fake_fetch)

    async def scenario() -> tuple:
        first = asyncio.create_task(bitmap_svg.generate_bitmap("并发合并猫"))
        await asyncio.sleep(0)
        second = asyncio.create_task(bitmap_svg.generate_bitmap("并发合并猫"))
        gate.set()
        return await asyncio.gather(first, second)

    first, second = asyncio.run(scenario())
    assert first == second
    assert calls == 1, "同题在途时第二个请求必须搭车，不得再次调生图后端"
    bitmap_svg._bitmap_cache.clear()


def test_bitmap_generate_cancel_keeps_shared_work_until_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """云端取消不能丢掉已付费的同题生成；重试必须复用完整结果。

    小智云会在慢 ``tools/call`` 尚未返回时取消/重试。旧实现只 shield PNG 下载：
    取消方立即退出并释放 DrawLimiter，后台下载完成后又立刻清掉在途表，没人负责
    描摹或写结果缓存；稍晚到达的同题重试会再次付费生图。契约：取消方必须等同题
    完整 ``BitmapResult`` 进入缓存后才退出，随后重试零额外生图。
    """
    from hutuji_mcp import bitmap_svg

    bitmap_svg._bitmap_cache.clear()
    bitmap_svg._bitmap_inflight.clear()
    calls = 0

    async def scenario() -> None:
        nonlocal calls
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_fetch(_desc: str) -> bytes:
            nonlocal calls
            calls += 1
            started.set()
            await asyncio.wait_for(release.wait(), timeout=5)
            return _star_png_bytes()

        monkeypatch.setattr(bitmap_svg, "_fetch_image_png", fake_fetch)
        first = asyncio.create_task(bitmap_svg.generate_bitmap("取消后重试猫"))
        await asyncio.wait_for(started.wait(), timeout=1)
        first.cancel()
        for _ in range(3):
            await asyncio.sleep(0)
        try:
            assert not first.done(), "取消方不得在共享付费生成完成前释放名额"
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await first

        second = await bitmap_svg.generate_bitmap("取消后重试猫")
        assert second.svg.startswith("<svg")
        assert calls == 1, "取消后的同题重试必须命中完整结果，不得再次付费生图"

    asyncio.run(scenario())
    bitmap_svg._bitmap_cache.clear()
    bitmap_svg._bitmap_inflight.clear()


def test_bitmap_generate_does_not_coalesce_different_topics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不同主题必须各自生成；合并键不能只按「是否在途」粗暴塌缩。"""
    from hutuji_mcp import bitmap_svg

    bitmap_svg._bitmap_cache.clear()


    bitmap_svg._bitmap_inflight.clear()
    calls = 0

    async def fake_fetch(_desc: str) -> bytes:
        nonlocal calls
        calls += 1
        return _star_png_bytes()

    monkeypatch.setattr(bitmap_svg, "_fetch_image_png", fake_fetch)

    async def scenario() -> None:
        await asyncio.gather(
            bitmap_svg.generate_bitmap("合并甲主题"),
            bitmap_svg.generate_bitmap("合并乙主题"),
        )

    asyncio.run(scenario())
    assert calls == 2
    bitmap_svg._bitmap_cache.clear()


def test_bitmap_generate_shared_fetch_failure_retries_next_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """共享生图失败对所有等待者报错；结束后必须清出在途记录，下次真实重试。"""
    from hutuji_mcp import bitmap_svg

    bitmap_svg._bitmap_cache.clear()
    bitmap_svg._bitmap_inflight.clear()
    calls = 0

    async def fake_fetch(_desc: str) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise bitmap_svg.BitmapError("共享上游失败")
        return _star_png_bytes()

    monkeypatch.setattr(bitmap_svg, "_fetch_image_png", fake_fetch)

    async def scenario() -> list[str]:
        results = await asyncio.gather(
            bitmap_svg.generate_bitmap("共享失败鱼"),
            bitmap_svg.generate_bitmap("共享失败鱼"),
            return_exceptions=True,
        )
        return [type(r).__name__ for r in results]

    assert asyncio.run(scenario()) == ["BitmapError", "BitmapError"]
    ok = asyncio.run(bitmap_svg.generate_bitmap("共享失败鱼"))
    assert ok.svg.startswith("<svg")
    assert calls == 2
    bitmap_svg._bitmap_cache.clear()


def _full_race_sf_settings() -> SimpleNamespace:
    """商汤 + Agnes + imagefree + SiliconFlow key（五路全开）。"""
    return SimpleNamespace(
        llm_api_key="agnes-key",
        llm_base_url="https://agnes.invalid/v1",
        image_model="agnes-image",
        image_fallback_url="https://fb.invalid/v1/generate",
        sensenova_api_key="sense-key",
        sensenova_base_url="https://token.sensenova.invalid/v1",
        sensenova_image_model="sensenova-u1-fast",
        siliconflow_api_key="sf-key",
    )


def test_siliconflow_racers_request_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """硅流双 racer 请求字段逐字钉死：schnell 走默认 4 步，可图钉 12 步/guidance 5。"""
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(bitmap_svg, "settings", _full_race_sf_settings())
    calls: list[dict] = []

    def handler(request):  # noqa: ANN001, ANN202
        assert request.url.host == "api.siliconflow.cn"
        assert request.headers["authorization"] == "Bearer sf-key"
        calls.append(json.loads(request.content))
        return _httpx.Response(200, json={"images": [{"url": "https://cdn.example/sf.png"}]})

    async def run() -> list[str]:
        async with _httpx.AsyncClient(transport=_httpx.MockTransport(handler)) as client:
            a = await bitmap_svg._request_siliconflow_schnell_image_url(client, "五角星")
            b = await bitmap_svg._request_siliconflow_kolors_image_url(client, "五角星")
            return [a, b]

    assert asyncio.run(run()) == ["https://cdn.example/sf.png"] * 2
    assert calls == [
        {
            "model": "black-forest-labs/FLUX.1-schnell",
            "prompt": bitmap_svg._BITMAP_STYLE + "五角星",
            "image_size": "1024x1024",
            "batch_size": 1,
        },
        {
            "model": "Kwai-Kolors/Kolors",
            "prompt": bitmap_svg._BITMAP_STYLE + "五角星",
            "image_size": "1024x1024",
            "batch_size": 1,
            "num_inference_steps": 12,
            "guidance_scale": 5.0,
        },
    ]


def test_siliconflow_wins_race_when_fastest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """硅流 key 在场时双 racer 必须并发加入对冲；最快者胜出，付费输家被取消。"""
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(bitmap_svg, "settings", _full_race_sf_settings())
    calls = {"sense": 0, "agnes": 0, "fb": 0, "sf": 0}

    async def handler(request):  # noqa: ANN001, ANN202
        host = request.url.host
        if host == "api.siliconflow.cn":
            calls["sf"] += 1
            if "Kolors" in str(request.content):
                await asyncio.sleep(0.3)  # 可图慢于 schnell
            return _httpx.Response(200, json={"images": [{"url": "https://cdn.example/sf.png"}]})
        if request.url.path.endswith("/sf.png"):
            return _httpx.Response(200, content=_png_stub())
        if host == "token.sensenova.invalid":
            calls["sense"] += 1
        elif host == "agnes.invalid":
            calls["agnes"] += 1
        elif host == "fb.invalid":
            calls["fb"] += 1
        await asyncio.sleep(0.3)
        return _httpx.Response(200, json={"data": [{"url": "https://cdn.example/x.png"}]})

    body = asyncio.run(
        bitmap_svg._fetch_image_png("五角星", transport=_httpx.MockTransport(handler))
    )
    assert body == _png_stub()
    assert calls["sf"] == 2, "schnell 与可图必须并发发起"
    assert calls["sense"] == 1 and calls["agnes"] == 1 and calls["fb"] == 1


def test_siliconflow_absent_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未配置硅流 key 时不得发起任何硅流请求（配置即禁用，无需开关）。"""
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(bitmap_svg, "settings", _sensenova_fallback_settings())
    seen: list[str] = []

    def handler(request):  # noqa: ANN001, ANN202
        seen.append(request.url.host)
        assert request.url.host != "api.siliconflow.cn"
        if request.url.host == "token.sensenova.invalid":
            return _httpx.Response(200, json={"data": [{"url": "https://cdn.example/u1.png"}]})
        return _httpx.Response(200, content=_png_stub())

    body = asyncio.run(
        bitmap_svg._fetch_image_png("城堡", transport=_httpx.MockTransport(handler))
    )
    assert body == _png_stub()


def test_siliconflow_failure_reports_without_key_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """硅流双 racer 失败进聚合错误，文案不得回显 key 或上游正文。"""
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(bitmap_svg, "settings", _full_race_sf_settings())
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))

    def handler(request):  # noqa: ANN001, ANN202
        return _httpx.Response(500)

    with pytest.raises(bitmap_svg.BitmapError) as exc_info:
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=_httpx.MockTransport(handler)))
    msg = str(exc_info.value)
    assert "硅流 Schnell 同样失败" in msg
    assert "硅流可图 同样失败" in msg
    assert "sf-key" not in msg


def test_bitmap_fetch_hedge_free_endpoint_wins_when_fastest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """imagefree 免 key 渠道参与对冲：它最快时必须用它的结果且取消两路付费。"""
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(bitmap_svg, "settings", _sensenova_fallback_settings())
    calls = {"sense": 0, "agnes": 0, "fb": 0}

    async def handler(request):  # noqa: ANN001, ANN202
        if request.url.host == "token.sensenova.invalid":
            calls["sense"] += 1
            await asyncio.sleep(0.3)
            return _httpx.Response(200, json={"data": [{"url": "https://cdn.example/u1.png"}]})
        if request.url.host == "agnes.invalid":
            calls["agnes"] += 1
            await asyncio.sleep(0.3)
            return _httpx.Response(200, json={"data": [{"url": "https://cdn.example/a.png"}]})
        if request.url.host == "fb.invalid":
            calls["fb"] += 1
            return _httpx.Response(
                200,
                json={"status": "completed", "image_url": "https://cdn.example/fb.png", "error": None},
            )
        if request.url.path.endswith("/fb.png"):
            return _httpx.Response(200, content=_png_stub())
        return _httpx.Response(200, content=b"PAID-WON")

    body = asyncio.run(
        bitmap_svg._fetch_image_png("五角星", transport=_httpx.MockTransport(handler))
    )
    assert body == _png_stub(), "imagefree 最快时必须用它的结果"
    assert calls == {"sense": 1, "agnes": 1, "fb": 1}, "三路必须并发发起"


def test_bitmap_fetch_hedge_all_three_fail_report_all_sides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """三路全灭时报三方原因（含免 key 渠道），且不再有第二层兜底。"""
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(bitmap_svg, "settings", _sensenova_fallback_settings())
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))

    def handler(request):  # noqa: ANN001, ANN202
        return _httpx.Response(500)

    with pytest.raises(bitmap_svg.BitmapError) as exc_info:
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=_httpx.MockTransport(handler)))
    msg = str(exc_info.value)
    assert "商汤生图失败" in msg
    assert "Agnes 同样失败" in msg
    assert "备用端点同样失败" in msg




def test_bitmap_fetch_sensenova_download_failure_does_not_regenerate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """赢家已生成 URL 后 CDN 失败只重试 GET；不得再计费生成或换另一侧重出。"""
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(bitmap_svg, "settings", _sensenova_settings())
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    calls = {"sense": 0, "agnes": 0}

    def handler(request):  # noqa: ANN001, ANN202
        if request.url.host == "token.sensenova.invalid":
            calls["sense"] += 1
            return _httpx.Response(200, json={"data": [{"url": "https://cdn.example/bad.png"}]})
        if request.url.host == "agnes.invalid":
            calls["agnes"] += 1
            return _httpx.Response(200, json={"data": [{"url": "https://cdn.example/a.png"}]})
        return _httpx.Response(500)

    with pytest.raises(bitmap_svg.BitmapError, match="图片下载返回 500"):
        asyncio.run(bitmap_svg._fetch_image_png("小猫", transport=_httpx.MockTransport(handler)))
    # 下载失败不得触发任何第二次生成；对冲并发发起故 Agnes 至多一次。
    assert calls["sense"] == 1
    assert calls["agnes"] <= 1


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


def test_sensenova_image_attempt_has_twenty_second_tail_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """U1 Fast 每次尝试仍钉 20s；瞬态 500 仅快速重试一次，不改单 attempt 尾延闸。"""
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(bitmap_svg, "settings", _sensenova_settings())
    seen: list[float] = []

    class _Timeout:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN202
            return False

    def fake_timeout(seconds: float):
        seen.append(seconds)
        return _Timeout()

    monkeypatch.setattr(bitmap_svg.asyncio, "timeout", fake_timeout)

    def handler(request):  # noqa: ANN001, ANN202
        return _httpx.Response(500)

    async def run() -> None:
        async with _httpx.AsyncClient(transport=_httpx.MockTransport(handler)) as client:
            with pytest.raises(bitmap_svg.BitmapError, match="返回 500"):
                await bitmap_svg._request_sensenova_image_url(client, "五角星")

    asyncio.run(run())
    assert seen == [20.0, 20.0]


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


def test_bitmap_generation_response_capped_at_read_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生图 POST 的 200 正文超上限必须拦成 BitmapError（R10-BMP-03）。

    与 llm_svg.MAX_LLM_RESPONSE_BYTES 同口径：上限在网络读取阶段生效，不等
    httpx 全量缓冲完再检查——恶意/故障上游在超时窗口内可推送 GB 级正文打爆
    VPS 内存。正常响应只含一条图片 URL 的 JSON，64KiB 已极宽裕。
    """
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="m"
        ),
    )
    import httpx as _httpx

    big = (
        b'{"data": [{"url": "https://cdn.example/'
        + b"a" * bitmap_svg._IMAGE_API_MAX_BYTES
        + b'.png"}]}'
    )
    transport = _httpx.MockTransport(lambda request: _httpx.Response(200, content=big))
    with pytest.raises(bitmap_svg.BitmapError, match="响应超过读取上限"):
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=transport))

def _fallback_settings() -> SimpleNamespace:
    """带备用端点的 settings：agnes 三件套 + image_fallback_url。"""
    return SimpleNamespace(
        llm_api_key="k",
        llm_base_url="https://x.invalid/v1",
        image_model="m",
        image_fallback_url="https://fb.invalid/v1/generate",
    )


def test_bitmap_fetch_falls_back_to_free_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """agnes 持续 500 → 备用端点（扁平 image_url）出图；备用请求形状钉死。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(bitmap_svg, "settings", _fallback_settings())
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    import httpx as _httpx

    agnes_calls = 0
    fallback_payloads: list[dict] = []

    def handler(request):  # noqa: ANN001, ANN202
        nonlocal agnes_calls
        if request.url.path.endswith("/images/generations"):
            agnes_calls += 1
            return _httpx.Response(500)
        if request.url.path.endswith("/v1/generate"):
            fallback_payloads.append(json.loads(request.content))
            return _httpx.Response(
                200,
                json={
                    "status": "completed",
                    "image_url": "https://cdn.example/fb.png",
                    "error": None,
                },
            )
        return _httpx.Response(200, content=_png_stub())

    body = asyncio.run(
        bitmap_svg._fetch_image_png("五角星", transport=_httpx.MockTransport(handler))
    )
    assert body == _png_stub()
    assert agnes_calls == 3, "主链两次瞬态重试耗尽后才降级"
    assert len(fallback_payloads) == 1
    assert "线稿" in fallback_payloads[0]["prompt"]
    assert "五角星" in fallback_payloads[0]["prompt"]
    assert fallback_payloads[0]["aspect_ratio"] == "1:1"
    assert "model" not in fallback_payloads[0], "备用端点请求不带 model 字段"


def test_bitmap_fetch_fallback_business_error_not_echoed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """备用端点 200+status=error：error 自由文本（内容安全原因等）不得回显。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(bitmap_svg, "settings", _fallback_settings())
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    import httpx as _httpx

    def handler(request):  # noqa: ANN001, ANN202
        if request.url.path.endswith("/images/generations"):
            return _httpx.Response(500)
        return _httpx.Response(
            200,
            json={"status": "error", "error": "内容安全拦截：血腥", "image_url": None},
        )

    with pytest.raises(bitmap_svg.BitmapError, match="备用生图端点未出图") as exc_info:
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=_httpx.MockTransport(handler)))
    assert "血腥" not in str(exc_info.value), "上游 error 自由文本不得回显"


def test_bitmap_fetch_fallback_completed_without_url_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """备用端点 completed 但缺 image_url = 结构异常，fail-closed。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(bitmap_svg, "settings", _fallback_settings())
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    import httpx as _httpx

    def handler(request):  # noqa: ANN001, ANN202
        if request.url.path.endswith("/images/generations"):
            return _httpx.Response(500)
        return _httpx.Response(200, json={"status": "completed", "error": None})

    with pytest.raises(bitmap_svg.BitmapError, match="备用生图端点未返回有效的"):
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=_httpx.MockTransport(handler)))


def test_bitmap_fetch_fallback_failure_reports_both_sides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主备都失败时错误须带两侧原因（均不含 URL/凭据）。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(bitmap_svg, "settings", _fallback_settings())
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    import httpx as _httpx

    def handler(request):  # noqa: ANN001, ANN202
        if request.url.path.endswith("/images/generations"):
            return _httpx.Response(500)
        return _httpx.Response(503)

    with pytest.raises(bitmap_svg.BitmapError) as exc_info:
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=_httpx.MockTransport(handler)))
    msg = str(exc_info.value)
    assert "生图 API 返回 500" in msg
    assert "备用生图端点 返回 503" in msg


def test_bitmap_fetch_config_gate_never_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 key 是部署事故，不走备用链——防止失效凭据被免费端点长期掩盖。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="",
            llm_base_url="https://x.invalid/v1",
            image_model="m",
            image_fallback_url="https://fb.invalid/v1/generate",
        ),
    )
    with pytest.raises(bitmap_svg.BitmapError, match="绘图服务未就绪"):
        asyncio.run(bitmap_svg._fetch_image_png("x"))


def test_bitmap_config_gate_messages_are_user_facing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """CloudUX-F5：位图路线配置类错误（缺 key / 空 model）文案用户向，
    环境变量名只进日志，不直达用户耳朵。"""
    from hutuji_mcp import bitmap_svg

    def settings_with(**overrides: str) -> SimpleNamespace:
        base = dict(
            llm_api_key="",
            llm_base_url="https://x.invalid/v1",
            image_model="m",
            image_fallback_url="https://fb.invalid/v1/generate",
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    monkeypatch.setattr(bitmap_svg, "settings", settings_with(llm_api_key=""))
    with caplog.at_level(logging.WARNING, logger="hutuji_mcp.bitmap_svg"):
        with pytest.raises(bitmap_svg.BitmapError) as exc_info:
            asyncio.run(bitmap_svg._fetch_image_png("x"))
    assert str(exc_info.value) == "绘图服务未就绪，请稍后再试"
    assert any("HUTUJI_LLM_API_KEY" in record.getMessage() for record in caplog.records)

    caplog.clear()
    monkeypatch.setattr(
        bitmap_svg, "settings", settings_with(llm_api_key="k", image_model="  ")
    )
    with caplog.at_level(logging.WARNING, logger="hutuji_mcp.bitmap_svg"):
        with pytest.raises(bitmap_svg.BitmapError) as exc_info:
            asyncio.run(bitmap_svg._fetch_image_png("x"))
    assert str(exc_info.value) == "绘图服务未就绪，请稍后再试"
    assert any("HUTUJI_IMAGE_MODEL" in record.getMessage() for record in caplog.records)


def test_settings_image_fallback_url_requires_https_or_blank(tmp_path: Path) -> None:
    """备用端点只放行 https；空白归一为空串（= 禁用）。"""
    base = dict(
        llm_base_url="https://example.invalid/v1",
        llm_api_key="",
        llm_model="test",
        output_dir=tmp_path,
        public_base_url="https://draw.example",
        host="127.0.0.1",
        port=8300,
        output_ttl_seconds=600,
    )
    with pytest.raises(ValueError, match="HUTUJI_IMAGE_FALLBACK_URL"):
        Settings(**base, image_fallback_url="http://fb.example/v1/generate")
    assert Settings(**base, image_fallback_url="  ").image_fallback_url == ""


def test_settings_image_fallback_url_rejects_bare_https(tmp_path: Path) -> None:
    """MCPDetail-F8：裸 `https://`（无主机名）必须被拒——旧 startswith 前缀
    校验会放过它；与 validate_llm_base_url 的 urlsplit 口径对齐。"""
    base = dict(
        llm_base_url="https://example.invalid/v1",
        llm_api_key="",
        llm_model="test",
        output_dir=tmp_path,
        public_base_url="https://draw.example",
        host="127.0.0.1",
        port=8300,
        output_ttl_seconds=600,
    )
    with pytest.raises(ValueError, match="HUTUJI_IMAGE_FALLBACK_URL"):
        Settings(**base, image_fallback_url="https://")
    # 带主机名的 https（含路径，生产默认值形状）仍放行。
    assert (
        Settings(**base, image_fallback_url="https://fb.example/v1/generate").image_fallback_url
        == "https://fb.example/v1/generate"
    )

def test_bitmap_fetch_fallback_single_attempt_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """备用链单次尝试（retry_delays=()）：429 也不重试——桥接看门狗 300s 内
    必须出结果（主链最坏 135s + 备用 45s + 下载 60s ≈ 240s），备用重试三轮会
    越界被撞死，响应丢在重连里。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(bitmap_svg, "settings", _fallback_settings())
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    import httpx as _httpx

    fallback_calls = 0

    def handler(request):  # noqa: ANN001, ANN202
        nonlocal fallback_calls
        if request.url.path.endswith("/images/generations"):
            return _httpx.Response(500)
        fallback_calls += 1
        return _httpx.Response(429)  # 瞬态码，但备用链不许重试

    with pytest.raises(bitmap_svg.BitmapError) as exc_info:
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=_httpx.MockTransport(handler)))
    assert fallback_calls == 1, "备用链只允许单次尝试"
    assert "备用生图端点 返回 429" in str(exc_info.value)


def test_bitmap_download_failure_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主链出图成功、CDN 下载失败时不降级：「主链好+CDN 挂」与「主链挂」
    是两类故障；降级等于免费端点再赌一次画风不同的图。"""
    from hutuji_mcp import bitmap_svg

    monkeypatch.setattr(bitmap_svg, "settings", _fallback_settings())
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    import httpx as _httpx

    fallback_calls = 0

    def handler(request):  # noqa: ANN001, ANN202
        nonlocal fallback_calls
        if request.url.path.endswith("/images/generations"):
            return _httpx.Response(200, json={"data": [{"url": "https://cdn.example/x.png"}]})
        if request.url.path.endswith("/v1/generate"):
            fallback_calls += 1
            return _httpx.Response(200, json={"status": "completed", "image_url": "https://cdn.example/fb.png"})
        return _httpx.Response(500)  # CDN 持续 500

    with pytest.raises(bitmap_svg.BitmapError, match="图片下载返回 500"):
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=_httpx.MockTransport(handler)))
    assert fallback_calls == 0, "下载阶段失败不得触发降级"


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


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        # 非 https：沿用原有「有效的 https 图片地址」口径
        ("http://cdn.example/ok.png", r"有效的 https"),
        ("https:///ok.png", r"有效的 https"),
        ("https://cdn.example:abc/ok.png", r"有效的 https"),  # 非数字端口
        ("https://2001:4860:4860::8888/ok.png", r"有效的 https"),  # 未加方括号 IPv6
        # IP 字面量非 global（is_global False 即拒）：loopback/private/link-local/
        # reserved/shared 全覆盖，含 IPv4-mapped 与结尾点（DNS 绝对名）形态
        ("https://127.0.0.1/ok.png", r"内网|保留"),
        ("https://127.0.0.1./ok.png", r"内网|保留"),
        ("https://[::1]/ok.png", r"内网|保留"),
        ("https://[::ffff:127.0.0.1]/ok.png", r"内网|保留"),
        ("https://10.0.0.8/ok.png", r"内网|保留"),
        ("https://192.168.1.13/ok.png", r"内网|保留"),
        ("https://172.16.5.5/ok.png", r"内网|保留"),
        ("https://100.64.0.1/ok.png", r"内网|保留"),  # CGNAT shared 段
        ("https://169.254.169.254/latest/meta-data", r"内网|保留"),
        ("https://0.0.0.0/ok.png", r"内网|保留"),
        ("https://[fe80::1]/ok.png", r"内网|保留"),
        # 本地主机名：localhost 大小写不敏感、剥结尾点，及保留本地 TLD
        ("https://localhost/ok.png", r"本地主机名"),
        ("https://LOCALHOST/ok.png", r"本地主机名"),
        ("https://localhost./ok.png", r"本地主机名"),
        ("https://x.localhost/ok.png", r"本地主机名"),
        ("https://x.local/ok.png", r"本地主机名"),
        ("https://x.internal/ok.png", r"本地主机名"),
        # userinfo 里的主机名不算数：判据取 hostname
        ("https://cdn.example@127.0.0.1/ok.png", r"内网|保留"),
    ],
)
def test_bitmap_fetch_rejects_non_public_image_urls(
    url: str, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """生图 API 返回的图片地址必须指向公网 https（IMG-URL-SSRF-GAP）。

    上游 images API 的响应受 prompt 影响，可能返回内网 https 地址；服务器直接
    GET 该地址等于把内网当代理（SSRF）。拒收必须发生在下载**之前**（不发内网
    GET）；DNS-rebinding 形态（域名解析到内网）不在本层防御面——响应还须过
    PNG magic，且本模块从不回显下载内容，见放行对照测试。
    """
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="m"
        ),
    )
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    get_calls: list[str] = []

    def handler(request):  # noqa: ANN001, ANN202
        if request.url.path.endswith("/images/generations"):
            return _httpx.Response(200, json={"data": [{"url": url}]})
        get_calls.append(str(request.url))
        return _httpx.Response(200, content=_png_stub())

    transport = _httpx.MockTransport(handler)
    with pytest.raises(bitmap_svg.BitmapError, match=reason):
        asyncio.run(bitmap_svg._fetch_image_png("x", transport=transport))
    assert not get_calls, f"拒收必须发生在下载之前，不得发起 GET：{url}"


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.example/ok.png",
        "https://example.com./ok.png",  # 公网域名的结尾点绝对名形态
        "https://8.8.8.8/ok.png",  # 公网 IP 字面量
        "https://[2001:4860:4860::8888]/ok.png",  # 公网 IPv6 字面量
        # DNS-rebinding 形态（域名可先解析到内网）：不在本层防御面——
        # 响应还须过 PNG magic，且无外带通道，域名形态必须放行。
        "https://dns-rebinding.example/ok.png",
    ],
)
def test_bitmap_fetch_allows_public_image_urls(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """合法公网地址必须照常下载（IMG-URL-SSRF-GAP 的放行对照）。

    防止 SSRF 闸收得过狠废掉唯一现行生成路线；同时钉死本层防御边界：
    域名形态一律放行（含可解析到内网的 DNS-rebinding 域名），纵深由
    PNG magic + 无回显通道承担。
    """
    from hutuji_mcp import bitmap_svg
    import httpx as _httpx

    monkeypatch.setattr(
        bitmap_svg,
        "settings",
        SimpleNamespace(
            llm_api_key="k", llm_base_url="https://x.invalid/v1", image_model="m"
        ),
    )
    monkeypatch.setattr(bitmap_svg, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))

    def handler(request):  # noqa: ANN001, ANN202
        if request.url.path.endswith("/images/generations"):
            return _httpx.Response(200, json={"data": [{"url": url}]})
        return _httpx.Response(200, content=_png_stub())

    transport = _httpx.MockTransport(handler)
    body = asyncio.run(bitmap_svg._fetch_image_png("x", transport=transport))
    assert body == _png_stub()

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


def test_bitmap_generate_cancel_waits_for_trace_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3 并发漂移：取消 generate_bitmap_svg 必须等描摹线程真正跑完再放行。

    描摹跑在线程池，Python 线程不可强制取消。旧实现直接
    `await asyncio.to_thread(trace_bitmap_to_polylines, png)`：请求被取消时 await
    立刻抛 CancelledError，server.DrawLimiter.slot() 的 finally 随即 leave() 释放
    并发名额，而描摹线程仍在后台烧 CPU——并发实数超过 max_concurrent=2（可用性
    DoS 面）。契约：被取消的协程要到线程置位 finished 之后才结束，slot().leave()
    因此永远晚于线程收尾。
    """
    import threading

    import numpy as np

    from hutuji_mcp import bitmap_svg

    async def fake_fetch(_desc: str) -> bytes:
        return b"png-bytes"

    started = threading.Event()
    finished = threading.Event()

    def slow_trace(_png: bytes):
        started.set()
        time.sleep(0.2)
        finished.set()
        return ([np.array([[0.0, 0.0], [1.0, 1.0]])], 10, 10)

    monkeypatch.setattr(bitmap_svg, "_fetch_image_png", fake_fetch)
    monkeypatch.setattr(bitmap_svg, "trace_bitmap_to_polylines", slow_trace)

    async def scenario() -> None:
        task = asyncio.create_task(bitmap_svg.generate_bitmap_svg("单次取消线程猫"))
        # 等线程进场后再取消，确保取消命中 shield 的 await 点而非更早处。
        await asyncio.to_thread(started.wait, 1.0)
        assert started.is_set()
        assert not finished.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # 关键断言：协程结束时线程必须已跑完。旧实现此处 finished 尚未置位。
        assert finished.is_set()

    asyncio.run(scenario())


def test_bitmap_generate_survives_second_cancel_during_trace_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R7-CANCEL-01：取消恢复路径必须抗**二次**取消，仍等描摹线程跑完。

    上一条只证了单次取消。asyncio.shield 只解耦第一次传播——恢复分支里若裸
    `await trace`，第二次取消会把它打断，协程在线程收尾前就返回，
    「DrawLimiter.leave() 永远晚于线程结束」这条不变量随即失效。
    二次取消是可达的：客户端先发 notifications/cancelled（第一次），随后断连让
    MCP SDK 的 tg.cancel_scope.cancel() 取消全部在途 handler（第二次）。
    契约：无论被取消几次，出口恒为 CancelledError，且线程必须已 finished。
    """
    import threading

    import numpy as np

    from hutuji_mcp import bitmap_svg

    async def fake_fetch(_desc: str) -> bytes:
        return b"png-bytes"

    started = threading.Event()
    finished = threading.Event()

    def slow_trace(_png: bytes):
        started.set()
        time.sleep(0.3)
        finished.set()
        return ([np.array([[0.0, 0.0], [1.0, 1.0]])], 10, 10)

    monkeypatch.setattr(bitmap_svg, "_fetch_image_png", fake_fetch)
    monkeypatch.setattr(bitmap_svg, "trace_bitmap_to_polylines", slow_trace)

    async def scenario() -> None:
        task = asyncio.create_task(bitmap_svg.generate_bitmap_svg("二次取消线程猫"))
        await asyncio.to_thread(started.wait, 1.0)
        assert started.is_set()
        assert not finished.is_set()
        # 第一次取消：命中 shield 的 await 点，协程转入恢复分支继续等线程。
        task.cancel()
        # 让出若干次，确保恢复分支已进入并挂在第二个 await 上。
        for _ in range(5):
            await asyncio.sleep(0)
        assert not finished.is_set(), "线程不应这么快跑完，用例前提失效"
        # 第二次取消：旧实现在此被打断并提前返回。
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(scenario())


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

def test_status_latest_ts_reflects_latest_file_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CloudUX-F6：latest_ts 是 latest 文件的 mtime（float epoch）；无文件为 None。"""
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    import json

    data = json.loads(asyncio.run(server.hutuji_status()))
    assert data["latest_ts"] is None

    base = server._new_basename()
    name = f"{base}.gcode"
    (tmp_path / name).write_text("G21\nG90\n", encoding="utf-8")
    mtime = (tmp_path / name).stat().st_mtime
    data = json.loads(asyncio.run(server.hutuji_status()))
    assert data["latest_ts"] == mtime

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


def test_cleanup_skips_symlink_capability_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """清理路径与下载路径同口径：命名合法的 symlink 不算产物，不得被删除。

    `Path.is_file()` 跟随链接会把指向普通文件的 symlink 当成产物，而下载侧
    `_read_regular_file`（O_NOFOLLOW + lstat）拒之——两处口径必须一致。
    """
    name = f"{server._new_basename()}.gcode"
    target = tmp_path / "keep.bin"
    target.write_bytes(b"keep")
    link = tmp_path / name
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        # Windows 上建 symlink 需管理员/开发者模式，无权限时跳过并说明原因。
        pytest.skip(f"当前平台无权限创建 symlink：{exc}")
    monkeypatch.setattr(server, "settings", _settings(tmp_path))

    # now 远超前强制「已过期」：旧实现（is_file 跟随）会把链接当产物删掉
    assert server._cleanup_expired_outputs(now=time.time() + 1000) == 0
    assert link.exists(), "symlink 不是本服务产物，不得被清理删除"


def test_status_ignores_symlink_capability_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """状态统计与下载路径同口径：命名合法的 symlink 不计入产物。"""
    name = f"{server._new_basename()}.gcode"
    target = tmp_path / "keep.bin"
    target.write_bytes(b"keep")
    link = tmp_path / name
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"当前平台无权限创建 symlink：{exc}")
    monkeypatch.setattr(server, "settings", _settings(tmp_path))

    data = json.loads(asyncio.run(server.hutuji_status()))
    assert data["gcode_files"] == 0
    assert data["latest"] is None
    assert link.exists(), "status 清理不得误删 symlink"


def test_status_survives_stat_permission_error_on_sort_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_safe_mtime 遇任意 OSError（如 PermissionError）不得让 status 变 500（SC-01）。

    与同文件 _cleanup_expired_outputs（捕 OSError 记 warning）和 _serve_file
    （塌 404）口径一致：status 是健康查询，单个文件在排序取 mtime 时 stat 失败
    应按 mtime=0.0 处理、继续返回 JSON。断言用排序可观察行为验证 0.0 生效：
    被拒 stat 的文件 mtime 归零后排在更老文件之前，latest 指向另一份。
    """
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    older = f"{server._new_basename()}.gcode"
    denied = f"{server._new_basename()}.gcode"
    (tmp_path / older).write_text("G21\nG90\n", encoding="utf-8")
    (tmp_path / denied).write_text("G21\nG90\n", encoding="utf-8")
    now = time.time()
    # older 早 10s；denied 若 stat 正常会是较新者（latest 应为它），
    # 归零后反而排最前，latest 应落到 older。
    os.utime(tmp_path / older, (now - 10, now - 10))

    # 只让 denied 在「排序取 mtime」那次 stat 失败，其余 stat（清理 is_file、
    # _is_expired、status 列表 is_file）照常：stat 调用序列按文件统计为
    # 1 cleanup is_file → 2 cleanup _is_expired → 3 status is_file → 4 排序 key。
    stat_calls: dict[str, int] = {}
    real_stat = Path.stat

    def denied_stat(self: Path, *_args: object, **_kwargs: object) -> object:
        key = str(self)
        stat_calls[key] = stat_calls.get(key, 0) + 1
        if key == str(tmp_path / denied) and stat_calls[key] >= 4:
            raise PermissionError("stat denied")
        return real_stat(self)

    monkeypatch.setattr(Path, "stat", denied_stat)
    import json

    data = json.loads(asyncio.run(server.hutuji_status()))
    assert data["gcode_files"] == 2, "stat 失败的文件仍应被计入产物数"
    assert data["latest"] == server._redact_capability_name(older), (
        "denied 文件 mtime 归零后应排最前，latest 指向较老文件"
    )


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
    with pytest.raises(server.DrawLimitError, match="绘图服务正忙"):
        asyncio.run(limiter.enter())


# ---- 2026-07-26 安全审计修复回归 ----


def test_serve_file_returns_response_not_file_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1 TOCTOU 修复：_serve_file 返回 Response（内存发送），不再是 FileResponse。"""
    from fastapi.responses import Response as _Resp

    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = f"{server._new_basename()}.gcode"
    body = _minimal_gcode("G1 X10 Y10 F8000\n").encode("ascii")
    (tmp_path / name).write_bytes(body)
    resp = asyncio.run(server._serve_file(name))
    assert isinstance(resp, _Resp)
    assert resp.body == body
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
    original = _minimal_gcode().encode("ascii")
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


def test_serve_file_read_oserror_collapses_to_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R14-TG-02：f.read() 中途抛 OSError（盘故障/权限竞态）必须塌成 404——
    不得 500，也不得多漏一条内部细节。"""
    import os as real_os

    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    name = f"{server._new_basename()}.gcode"
    real = tmp_path / name
    real.write_bytes(b"G21\n")

    class _BoomFile:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, *args):
            raise OSError("simulated mid-read failure")

    class FakeOS:
        O_RDONLY = real_os.O_RDONLY
        O_BINARY = getattr(real_os, "O_BINARY", 0)

        @staticmethod
        def stat(path, follow_symlinks=True):
            return real_os.stat(path, follow_symlinks=follow_symlinks)

        @staticmethod
        def fstat(fd):
            return real_os.fstat(fd)

        @staticmethod
        def fdopen(fd, mode):
            real_os.close(fd)  # 所有权名义上移交给文件对象；此处直接收掉防漏
            return _BoomFile()

        @staticmethod
        def close(fd):
            return real_os.close(fd)

        @staticmethod
        def open(path, flags, *args, **kwargs):
            return real_os.open(path, flags, *args, **kwargs)

    monkeypatch.setattr(server, "os", FakeOS)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(name))
    assert caught.value.status_code == 404

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



def test_svg_to_gcode_async_limits_subprocess_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不同 SVG 的并发转换最多启动两个 vpype，Quick Draw 命中也不能绕开资源界。"""
    active = 0
    peak = 0
    two_started = asyncio.Event()
    release = asyncio.Event()

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            nonlocal active
            await release.wait()
            active -= 1
            return b"", b""

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active >= 2:
            two_started.set()
        return FakeProcess()

    stats = gcode_gen.GcodeStats(lines=1, bytes=1, pen_lifts=0)
    monkeypatch.setattr(gcode_gen.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(gcode_gen, "_read_generated_gcode", lambda _path: stats)

    async def exercise() -> None:
        tasks = [
            asyncio.create_task(
                gcode_gen.svg_to_gcode_async(tmp_path / f"{i}.svg", tmp_path / f"{i}.gcode")
            )
            for i in range(3)
        ]
        await asyncio.wait_for(two_started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert peak == 2
        release.set()
        assert await asyncio.gather(*tasks) == [stats, stats, stats]

    asyncio.run(exercise())



def test_gate_repo_index_skips_tmp_staging_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """根 tmp 的部署/发布副本不得破坏本仓源码 basename 唯一解析。"""
    gate = _load_agent_gate()
    source = tmp_path / "mcp-server" / "pkg" / "server.py"
    staging = tmp_path / "tmp" / "deploy-copy" / "pkg" / "server.py"
    source.parent.mkdir(parents=True)
    staging.parent.mkdir(parents=True)
    source.write_text("source\n", encoding="utf-8")
    staging.write_text("copy\n", encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    index = gate._repo_file_index()

    assert index["server.py"] == [source]
    assert gate._resolve_doc_ref("server.py", index) == source

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
        Path(cmd[-1]).write_text(_minimal_gcode("G1 X10 Y10 F8000\n"), encoding="utf-8")
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
        Path(cmd[-1]).write_text(_minimal_gcode("G1 X10 Y10 F8000\n"), encoding="utf-8")
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
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 190 190">'
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
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 190 190">'
        '<line x1="10" y1="100" x2="180" y2="100" stroke="black"/></svg>',
        encoding="utf-8",
    )
    gcode = tmp_path / "hline.gcode"
    gcode_gen.svg_to_gcode(svg, gcode)

    min_x, min_y, max_x, max_y = _gcode_xy_bbox(gcode)
    assert (min_y + max_y) / 2 == pytest.approx(gcode_gen.MAX_Y_MM / 2, abs=1.0)



def test_svg_to_gcode_layout_handles_vertical_line(tmp_path: Path) -> None:
    """R13-GC-01：零宽度图形（一条竖线）生产链可达，不得让归一化除零崩溃，
    且水平居中——零跨度缩放比依赖 numpy min(inf, 有限)=有限 语义，必须有钉。"""
    from hutuji_mcp import gcode_gen

    svg = tmp_path / "vline.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 190 190">'
        '<line x1="100" y1="10" x2="100" y2="180" stroke="black"/></svg>',
        encoding="utf-8",
    )
    gcode = tmp_path / "vline.gcode"
    gcode_gen.svg_to_gcode(svg, gcode)

    min_x, min_y, max_x, max_y = _gcode_xy_bbox(gcode)
    assert (min_x + max_x) / 2 == pytest.approx(gcode_gen.MAX_X_MM / 2, abs=1.0)
    assert max_y - min_y == pytest.approx(170.0, abs=2.0), "竖线必须被拉满到边距内"


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


def test_gwrite_profile_head_and_segments_accepted_by_validator() -> None:
    """R13-GC-03：toml 与校验器必须互为事实源——只改 toml 的 F 值（不碰
    gcode_gen.py）时，真实产物会在 draw 时整单自拒且 preflight 全绿（与
    hutuji_gwrite.toml 漏传同形）。用 toml 自己的模板拼最小产物喂校验器，
    任一侧漂移都在这里红。"""
    import tomllib

    from hutuji_mcp import gcode_gen

    toml_path = Path(gcode_gen.__file__).parent / "hutuji_gwrite.toml"
    profile = tomllib.loads(toml_path.read_text(encoding="utf-8"))["gwrite"]["hutuji"]

    text = (
        profile["document_start"]
        + profile["segment_first"].format(x=95.0, y=175.0)
        + profile["segment"].format(x=100.0, y=100.0)
        + profile["line_end"]
        + profile["document_end"]
    )
    stats = gcode_gen.validate_gcode(text)
    assert stats.lines >= 5
    assert stats.pen_lifts >= 1


def test_firmware_jog_envelope_matches_cloud_limits() -> None:
    """R17-XREPO-01：固件点动包线与云端 §5 限幅互为事实源。

    xiaozhi `hutuji_recovery_core.h` 的 `kJogEnvelopeMax{X,Y}Mm` 是无限位机器上
    点动的唯一防线（2026-08-20 `DecideJog` fail-closed 加固）。只改云端
    `MAX_X_MM`/`MAX_Y_MM` 或只改固件常量，两侧限幅就漂移——固件比云端松是
    撞机风险，比云端紧则云端合法图样的边缘位置点不动。字面量断言两侧互为
    事实源（R13-GC-01 同款）；外仓缺失时跳过（生产 venv 无 pytest，本机
    门禁 hub profile 必跑到），路径解析与 agent_gate 外仓层同口径。"""
    import os

    from hutuji_mcp import gcode_gen

    # 云端侧钉死 §5 硬边界（行为测试之外的显式锚点）。
    assert gcode_gen.MAX_X_MM == 190.0
    assert gcode_gen.MAX_Y_MM == 190.0

    root = Path(os.environ.get("XIAOZHI_ROOT", "D:/Users/xiaozhi-esp32"))
    header = root / "main/boards/lichuang-dev/hutuji_recovery_core.h"
    if not header.is_file():
        pytest.skip(f"外仓固件头缺失：{header}")

    text = header.read_text(encoding="utf-8")
    assert f"kJogEnvelopeMaxXMm = {gcode_gen.MAX_X_MM:.1f}f" in text
    assert f"kJogEnvelopeMaxYMm = {gcode_gen.MAX_Y_MM:.1f}f" in text
    # 1mm 固定步进是固件侧独有概念（云端从不点动），逐字对齐奎享 `$J=` 序列。
    assert "kJogStepMm = 1.0f" in text


def test_arc_full_circle_epsilon_boundary_is_inclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R13-GC-02：整圆判定阈值含等号，与固件逐字一致（MotionControl.cpp
    CCW `<= eps` / CW `>= -eps`）。sweep 恰等于 eps 时必须按整圆取包围盒；
    严格 `<` 口径下同一输入包围盒退化成两端点。"""
    import math

    from hutuji_mcp import gcode_gen

    # 圆心原点、半径 1：起点 (1,0)，终点取一个可复算角度；两侧用同一组
    # atan2 计算 sweep，monkeypatch 把阈值钉到恰等于它——等号归属立判。
    a1 = 0.37
    x0, y0 = 1.0, 0.0
    x1, y1 = math.cos(a1), math.sin(a1)
    sweep = (math.atan2(y1, x1) - math.atan2(y0, x0)) % (2.0 * math.pi)
    monkeypatch.setattr(gcode_gen, "_ARC_FULL_CIRCLE_EPSILON_RAD", sweep)

    min_x, min_y, max_x, max_y = gcode_gen._arc_bbox(
        x0, y0, x1, y1, -1.0, 0.0, None, False
    )
    assert min_x == pytest.approx(-1.0)
    assert max_x == pytest.approx(1.0)
    assert min_y == pytest.approx(-1.0)
    assert max_y == pytest.approx(1.0)


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


def test_gcode_rejects_arc_without_feed() -> None:
    """R14-TG-01：G2/G3 有坐标有圆弧参数但缺 F 必须拒（`feed is None` 闸）——
    既有用例只覆盖缺 X/Y 与缺 I/J/R，这条分支此前零测试。"""
    text = _minimal_gcode("G0 X100 Y100 F8000\nG2 X120 Y100 I10 J0\n")
    with pytest.raises(GcodeError, match="进给"):
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
    # 前置一条运动行满足零运动闸（纯注释参数否则过不了），不影响各参数本身的判定
    validate_gcode(_minimal_gcode("G1 X10 Y10 F8000\n" + line + "\n"))


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
    """两端点都在界内，但 G2（顺时针）走上半圈，中途凸到 Y=195 > 190。
    只看端点会放行这条弧，实机就是撞机。"""
    text = _minimal_gcode("G0 X100 Y185 F8000\nG2 X120 Y185 I10 J0 F8000\n")
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


def test_arc_full_circle_epsilon_matches_grbl_firmware() -> None:
    """扫角落在 (1e-9, 5e-7) 的微小弧必须按整圆算包围盒（R10-ARC-01）。

    Grbl `Config.h` 的 ARC_ANGULAR_TRAVEL_EPSILON=5e-7：`MotionControl.cpp` 对
    |angular_travel| ≤ 5e-7 的 G2/G3 一律补 2π 当整圆画。旧校验器用 1e-9 判
    整圆，(1e-9, 5e-7) 区间的弧被当「原地微动」放行（包围盒退化成端点），
    固件却画出整圆——圆心 (100,100) 半径 99 的整圆 X 凸到 199 > 190 限幅，
    真实后果是撞出画幅（本机无限位开关，无第二道防线）。
    构造：起点 (1,100)，终点沿 CW 转 1e-7 rad ≈ (1, 100.0000099)。
    """
    text = _minimal_gcode(
        "G0 X1 Y100 F8000\nG2 X1 Y100.0000099 I99 J0 F8000\n"
    )
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(text)


def test_arc_full_circle_epsilon_constant_pinned_to_grbl() -> None:
    """整圆判定阈值钉死 5e-7 = Grbl `Config.h` ARC_ANGULAR_TRAVEL_EPSILON。

    这不是可调精度参数：比固件小则漏放「固件当整圆」的弧（撞机方向），
    比固件大则误拒合法微小弧。改它必须先证明固件侧同步改了。
    """
    assert gcode_gen._ARC_FULL_CIRCLE_EPSILON_RAD == 5e-7


def test_gcode_rejects_product_without_xy_motion() -> None:
    """零 XY 运动的产物必须拒（GC-P3b）：合法头尾 + 纯注释也一样。

    上游（描摹丢线/vpype linesimplify 滤光几何）产出空产物时，放行等于
    白白消耗一张纸和一轮换纸周期，且用户拿到「成功」却是空白纸。
    """
    with pytest.raises(GcodeError, match="XY 运动"):
        validate_gcode(_minimal_gcode())
    with pytest.raises(GcodeError, match="XY 运动"):
        validate_gcode(_minimal_gcode("; comment only\n"))


def test_gcode_rejects_zero_travel_ij_arc() -> None:
    """零行程 IJ 弧不得满足运动闸（GC-01）：终点 == 起点，画不出任何东西。

    旧闸「本行带坐标即算运动」把 `G2 X100 Y100 I0 J0`（定位到同点后的原地
    圆弧）也计入，放行即画白纸。G0 定位是抬笔态空走（hutuji_gwrite.toml 的
    segment_first 语义，空走不落墨），同样不计入——整份文件只剩空走 + 零行程
    时按空产物拒收。仅外部 G-code 可达该路径（vpype 不产 G2/G3、read 丢零长段）。
    """
    text = _minimal_gcode("G0 X100 Y100 F8000\nG2 X100 Y100 I0 J0 F8000\n")
    with pytest.raises(GcodeError, match="XY 运动"):
        validate_gcode(text)


def test_gcode_rejects_zero_displacement_g1() -> None:
    """先定位到同点后的 G1 同点必须不计入运动闸（GC-01）。

    与零行程 IJ 弧同理：G0 空走到 (100,100)（不落墨）+ G1 原地不动，整份文件
    实际位移为零，放行即画白纸。G1 若真的从当前位置移开（哪怕只移一个轴），
    仍算运动——见放行对照测试。
    """
    text = _minimal_gcode("G0 X100 Y100 F8000\nG1 X100 Y100 F8000\n")
    with pytest.raises(GcodeError, match="XY 运动"):
        validate_gcode(text)


def test_gcode_motion_gate_still_passes_real_drawings() -> None:
    """运动闸收紧后合法图必须照常放行（GC-01 放行对照）。

    三种真实形态：单个 G1 画线（首个定位前 cur 未知也视为位移）、G0 空走 +
    G1 落笔画线（生产 profile 的 segment_first/segment 形态）、以及真位移的
    G2/G3 圆弧。它们都有实际位移，不能被零行程闸误伤。
    """
    stats = validate_gcode(_minimal_gcode("G1 X10 Y10 F8000\n"))
    assert stats.lines == 7
    stats = validate_gcode(
        _minimal_gcode(
            "G0 X100 Y100 F8000\nG1G90 Z5.0F10000\n"
            "G1 X110 Y100 F8000\nG1G90 Z0.0F10000\n"
        )
    )
    assert stats.lines == 10
    stats = validate_gcode(_minimal_gcode("G0 X100 Y100 F8000\nG2 X120 Y100 R20 F8000\n"))
    assert stats.lines == 8


def test_gcode_rejects_formfeed_line_splitting_bypass() -> None:
    """行内 \\x0c 不得当行分隔符吞掉（GC-P3c）。

    splitlines() 会按 \\x0c/\\x0b/\\x1c-\\x1e 等切行：一条 270 字节的设备行被
    校验器拆成两条「合法」短行，分隔符本身消失——设备却按原样收到超长行，
    error:11 静默吃行。控制字符必须显式拒绝。
    """
    long_comment = ";" + "x" * 250
    text = _minimal_gcode(f"G1 X10 Y10 F8000\x0c{long_comment}\n")
    with pytest.raises(GcodeError, match="控制字符"):
        validate_gcode(text)


def test_gcode_rejects_unicode_line_separator_bypass() -> None:
    """U+2028/U+0085 也是 splitlines() 的分隔符（GC-P3c 非 ASCII 变体）。

    旧实现里它们作为分隔符直接消失，两侧内容各自过 ASCII 检查；设备收到的
    却是含多字节 UTF-8 的一条长行。改按 \\n 切行后它们留在行内，非 ASCII
    闸必须拦住。
    """
    text = _minimal_gcode("G1 X10 Y10 F8000\u2028; hidden\n")
    with pytest.raises(GcodeError, match="非 ASCII|控制字符"):
        validate_gcode(text)


def test_gcode_crlf_product_still_accepted() -> None:
    """CRLF 产物仍必须放行：生产链在 Windows 检出下真实产出过 CRLF 文件，
    §3 已约定 S3 重切行。控制字符闸只拦「行中」控制符，不拦行尾 \\r。"""
    text = _minimal_gcode("G1 X10 Y10 F8000\n").replace("\n", "\r\n")
    stats = validate_gcode(text)
    assert stats.lines == 7


def test_vpype_cmd_fallback_module_is_importable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """兜底命令必须指向真实可 `-m` 执行的模块（GC-P3a）。

    vpype 包没有 `__main__.py`，旧兜底 `python -m vpype` 是死路径——真走到
    时报 "No module named vpype.__main__"，诊断指向错误方向。console script
    入口在 vpype_cli 包，它才有 `__main__.py`。
    """
    import importlib.util

    monkeypatch.setattr(gcode_gen.shutil, "which", lambda _name: None)
    monkeypatch.setattr(gcode_gen.Path, "is_file", lambda _self: False)
    cmd = gcode_gen._vpype_cmd()
    assert cmd == [gcode_gen.sys.executable, "-m", "vpype_cli"]
    assert importlib.util.find_spec("vpype_cli.__main__") is not None


def test_coordinate_upper_bound_uses_eps_not_1mm() -> None:
    """#9 容差对称：190.5mm 不应放行（旧 +1.0mm 容差会放行）。"""
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(_minimal_gcode("G1 X190.5 Y5 F8000\n"))


def test_coordinate_at_boundary_passes() -> None:
    """X=190 和 Y=190 恰好在各自界内。"""
    validate_gcode(_minimal_gcode("G1 X190 Y190 F8000\n"))


@pytest.mark.parametrize(
    "body",
    [
        "G1 X190.0001 Y5 F8000\n",
        "G1 X5 Y190.0001 F8000\n",
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


def test_y_over_190mm_rejected() -> None:
    """Y=190.5 超出 Y 轴限幅 190mm。"""
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(_minimal_gcode("G1 X5 Y190.5 F8000\n"))


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

    故 `0~190/0~190` 的绝对限幅**只在全程 G90 时成立**，`G91` 是限幅的整体旁路，
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
    """首个 G2 半径 10mm 合法；裸轴字继承 G2 后半径 25mm 凸到 Y=195，必须拒。"""
    text = _minimal_gcode(
        "G0 X100 Y170 F8000\nG2 X120 Y170 I10 J0 F8000\nX170 Y170 I25 J0 F8000\n"
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
    # DEP-05：is-active 只证「在跑」，证不到「重启后还会回来」。install-from-repo.sh
    # 刻意不自动 enable（首次上线流程第 4 步才手动 enable），漏 enable 时重启后服务
    # 永久丢失而 preflight 全绿，故三条服务必须显式验 is-enabled。
    assert "systemctl is-enabled --quiet hutuji-mcp" in preflight
    assert "systemctl is-enabled --quiet hutuji-cloud-bridge" in preflight
    assert "systemctl is-enabled --quiet nginx" in preflight
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
    assert preflight.count('\ncheck "') == 30
    # /register（激活码中转）两项必须在列：本机 400/503 判据 + 公网只放 POST 判据。
    assert '/register 拒绝坏形状' in preflight
    assert '/register 只放 POST' in preflight
    # token 看门狗 + 一键换 token 必须在列（JWT 标称 90 天、事件性作废的运维口径）。
    assert 'token 看门狗 cron 与一键换 token 脚本已安装' in preflight
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
    `unbound variable` 退出，29 项一个都不会跑——脚本连体检结果都产不出。用行序钉死。"""
    root = Path(__file__).resolve().parents[2]
    lines = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8").splitlines()

    init_last = max(
        i
        for i, line in enumerate(lines)
        if line.startswith(("TUNNEL_HOST=", "OUTPUT_DIR=", "BRIDGE_ENDPOINT="))
    )
    checks = [i for i, line in enumerate(lines) if line.startswith('check "')]
    assert len(checks) == 30
    assert min(checks) > init_last


def test_preflight_check_callsites_are_real_checks() -> None:
    """DEP-02：条数与函数定义的钉死防不住「死函数定义 + 恒真补位」。

    实测复现：把某条真实检查的调用行换成 `check "生成链可用" true`（函数定义
    保留、条数不变），旧的全部形状断言仍绿——preflight 打全绿但该判据从未执行。
    故必须把调用点本身钉死：每条 check 的命令不得是恒真命令；`check_*` 裸函数
    调用必须有同名的真实函数定义；三条链式正例检查逐字钉调用点。
    """
    root = Path(__file__).resolve().parents[2]
    preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")
    preflight_code = "\n".join(
        line for line in preflight.splitlines() if not line.lstrip().startswith("#")
    )

    callsites = [
        line for line in preflight_code.splitlines() if line.startswith('check "')
    ]
    expected = re.search(r"^EXPECTED_CHECKS=(\d+)$", preflight, re.MULTILINE)
    assert expected is not None
    assert len(callsites) == int(expected.group(1))
    for line in callsites:
        m = re.match(r'check "([^"]+)" (.*)$', line)
        assert m is not None, line
        cmd = m.group(2).strip()
        # 恒真补位（含带参数的变体）一律判红。
        assert not re.fullmatch(r"(true|:|/bin/true)( .*)?", cmd), line
        fname = cmd.split()[0]
        if re.fullmatch(r"check_[a-z_0-9]+", fname):
            # 裸函数调用必须存在真实定义（`fname() {` 或子壳形态 `fname() (`），
            # 防「调用指向不存在/残留定义」与「定义在但调用被换」两个方向。
            assert re.search(
                rf"^{re.escape(fname)}\(\) [({{]", preflight_code, re.MULTILINE
            ), line

    # 三条链式正例（生成链/位图链/下载契约）逐字钉调用点：名字像不算，
    # 必须确实把流量打进真实函数。
    assert (
        'check "生产 venv 真实生成链可用（vpype + profile + §5 校验）" check_generate_chain'
        in preflight_code
    )
    assert (
        'check "生产 venv 真实位图描摹链可用（skimage + sknw + imageio）" check_bitmap_chain'
        in preflight_code
    )
    assert (
        'check "localhost :80 /files 真下 G-code 200" check_download local'
        in preflight_code
    )
    # DEP-05：开机自启判据必须逐字钉调用点——install-from-repo.sh 刻意不自动 enable，
    # 漏 enable 时重启后服务永久丢失而 preflight 全绿，故 is-enabled 三连必须存在。
    assert (
        'check "hutuji-mcp / hutuji-cloud-bridge / nginx 开机自启（is-enabled）" bash -c '
        "'systemctl is-enabled --quiet hutuji-mcp && systemctl is-enabled --quiet "
        "hutuji-cloud-bridge && systemctl is-enabled --quiet nginx'"
        in preflight_code
    )


def test_preflight_check_echoes_failure_criteria_tail() -> None:
    """DEP-UX-01：check() 必须捕获判据输出、失败时回显末尾行。

    约半数判据（裸 `bash -c '[[ … ]]'`、systemctl 探活、curl 状态码比对等）失败时
    本身零输出，不捕获则 29 项里多几项红时操作员定位不到是哪一步、为什么红
    （download 失败连 code/len/crc 都看不到）。形态必须是 `out=$(...) || rc=$?`——
    `if ! out=$(...)` 下 `$?` 恒为 0（见 check_bitmap_chain 注释），全仓禁止。
    成功时丢弃输出，[pass]/[fail] 行与 PASS/FAIL 计数语义不得改变。
    """
    root = Path(__file__).resolve().parents[2]
    preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")
    preflight_code = "\n".join(
        line for line in preflight.splitlines() if not line.lstrip().startswith("#")
    )

    body = preflight_code.split("check() {", 1)[1].split("\n}", 1)[0]
    assert 'out=$("$@" 2>&1) || rc=$?' in body, "判据输出必须被捕获且保住真实退出码"
    assert "tail -2" in body, "失败时必须回显判据输出的末尾行（供操作员定位）"
    assert 'if ! out="$(' not in body, "`if ! out=$(...)` 形态下 $? 恒为 0，不得复活"
    assert 'echo "[pass] ${name}"' in body, "[pass] 输出行不许改版"
    assert 'echo "[fail] ${name}"' in body, "[fail] 输出行不许改版"
    assert "PASS=$((PASS + 1))" in body, "PASS 计数语义不许改"
    assert "FAIL=$((FAIL + 1))" in body, "FAIL 计数语义不许改"


def test_install_warns_missing_env_and_nginx_fail_guidance() -> None:
    """DEP-UX-02：任一 env 缺失必须显式警告先跑 prepare-host.sh；nginx -t
    失败必须提示 unit 已装未启、修复配置后可重跑。"""
    root = Path(__file__).resolve().parents[2]
    install = (root / "deploy" / "install-from-repo.sh").read_text(encoding="utf-8")

    assert "[[ ! -f /etc/hutuji/mcp.env || ! -f /etc/hutuji/bridge.env ]]" in install
    assert "prepare-host.sh" in install
    assert "nginx -t" in install
    assert "已安装未启动" in install
    assert "重跑本脚本" in install



def test_install_warns_when_either_environment_file_is_missing() -> None:
    """只缺一个 EnvironmentFile 也必须提示，否则安装后服务会缺配置且无诊断。"""
    root = Path(__file__).resolve().parents[2]
    install = (root / "deploy" / "install-from-repo.sh").read_text(encoding="utf-8")
    code = "\n".join(line for line in install.splitlines() if not line.lstrip().startswith("#"))
    assert "||" in code.split("if [[ ! -f /etc/hutuji/mcp.env", 1)[1].split("; then", 1)[0]

def test_preflight_verifies_8300_listener_identity() -> None:
    """DEP-03：「8300 有监听」证不到「监听者是本次部署的 uvicorn」。

    SO_REUSEADDR 允许残留旧进程与新 uvicorn 并存监听同一 loopback:8300，探针
    可能命中旧进程内存中的旧代码而假绿（2026-07-29 本机实测，docs/troubleshooting.md
    「本地临时测试服务（8300）」）。必须 ss -lntp 验监听者归属 hutuji-mcp 的
    MainPID，且旧的「只认监听存在」弱形式不得复活。
    """
    root = Path(__file__).resolve().parents[2]
    preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")
    preflight_code = "\n".join(
        line for line in preflight.splitlines() if not line.lstrip().startswith("#")
    )

    assert "check_8300_listener_owned_by_mcp() {" in preflight_code
    assert (
        'check "8300 仅本机监听且监听者是 hutuji-mcp 主进程" check_8300_listener_owned_by_mcp'
        in preflight_code
    )
    body = preflight_code.split("check_8300_listener_owned_by_mcp() {", 1)[1].split(
        "\n}", 1
    )[0]
    # 必须读进程身份（-p）并比对 MainPID；ss 缺位与「无监听」都是 fail-closed。
    assert "ss -lntp" in body
    assert "systemctl show hutuji-mcp -p MainPID --value" in body
    assert 'pid=$main_pid,' in body
    assert "command -v ss >/dev/null || return 1" in body
    # 旧的弱形式（只验监听存在、不验身份）不得复活。
    assert 'ss -lnt | grep -q "127.0.0.1:8300"' not in preflight_code


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
        # DEP-01：依赖约束（范围钉、无 lockfile）变更漏 restart 时，进程内旧树与
        # 磁盘新 venv 分叉而探针全绿；requirements.txt 必须在比较集内。
        "/opt/hutuji/mcp-server/requirements.txt",
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
    # DEP-04：error.log 缺失/不可读/路径被改时 awk 会静默扫空，[emerg] 判据假绿；
    # error_log 指令在 nginx.conf（模板 md5 判据覆盖不到），故读不到必须显式判红。
    assert "[[ -r /var/log/nginx/error.log ]]" in preflight_code


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
    # junction 续接（2026-08-27 上线）：⊥ 交叉处横臂左右两半按最直通配对续成
    # 一条通笔，竖臂成短枝——3 边变 2 笔、抬笔少一次，覆盖断言不变。
    assert len(polys) == 2
    # 2026-08-27 重标定（用户批准）：08-26 抽稀上线后轴对齐探针任何 tol 都塌成
    # 2-3 点，「每条 ≥_MIN_POLY_PX 点」与抽稀数学上不可共存（HEAD 基线同红，
    # a089288 遗留）。改覆盖率断言：两臂臂体（避开端部 ~20px 骨架收圆区）
    # 采样点到最近折线线段距离 ≤4px（实测最差 0px）。
    import numpy as np

    _samples = [(x, 520) for x in range(148, 876, 20)]
    _samples += [(520, y) for y in range(148, 511, 20)]
    _worst = max(min(_pt_poly_dist(s, p) for p in polys) for s in _samples)
    assert _worst <= 4.0, f"探针臂体覆盖缺口（最差 {_worst:.2f}px）"
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


def test_bitmap_warm_trace_deps_preloads_heavy_modules() -> None:
    """SPEED-01：重依赖必须能被显式预热，且预热的正是描摹真正用到的那几个模块。

    冷启 import（skimage/sknw/imageio/scipy）本机实测 ~3.0s，而它写在
    `trace_bitmap_to_polylines` 内部——`ExecStartPre` 自检是**独立进程**，预热不到
    服务进程，于是这 3s 每次重启后都落在**第一次真实出图**上（生产日志
    14:04:59 下完 PNG → 14:05:04 才出应答）。
    """
    import sys

    from hutuji_mcp import bitmap_svg

    bitmap_svg.warm_trace_deps()
    # 钉住「预热的是描摹真正用到的模块」，而不是随便 import 点什么充数
    for module in ("imageio.v3", "sknw", "skimage.color", "skimage.filters", "skimage.morphology"):
        assert module in sys.modules, f"{module} 未被预热"
    # 幂等：第二次调用不得抛
    bitmap_svg.warm_trace_deps()


def test_bitmap_warm_trace_deps_reports_same_actionable_diagnosis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预热失败必须复用描摹那条唯一可行动的诊断，而不是自造一句新错。

    预热与描摹分成两份 import 块时会漂移（一边升了依赖名、另一边没跟），
    「预热成功但真实描摹炸」正是本仓已被咬过一次的 fail-open 形状。
    """
    import builtins

    from hutuji_mcp import bitmap_svg

    real_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("sknw"):
            raise ImportError("simulated sknw missing")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with pytest.raises(bitmap_svg.BitmapError) as caught:
        bitmap_svg.warm_trace_deps()
    message = str(caught.value)
    assert "simulated sknw missing" in message
    assert "requirements.txt" in message


def test_lifespan_warms_generation_deps_without_blocking_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SPEED-01：`lifespan` 必须真的触发预热，且预热不得阻塞/拖垮启动。

    预热只有挂在服务进程的启动路径上才有意义；同时依赖坏掉是 ExecStartPre 与
    preflight 的职责，这里必须 fail-open（记日志、照常起服务）。
    """
    import contextlib as _contextlib

    from hutuji_mcp import server as srv

    calls: list[str] = []

    @_contextlib.asynccontextmanager
    async def fake_run():
        calls.append("session_manager")
        yield

    monkeypatch.setattr(
        srv, "mcp", SimpleNamespace(session_manager=SimpleNamespace(run=fake_run))
    )

    def boom() -> None:
        calls.append("warm")
        raise srv.BitmapError("simulated broken deps")

    monkeypatch.setattr(srv, "warm_trace_deps", boom)

    async def drive() -> None:
        async with srv.lifespan(None):
            calls.append("serving")

    asyncio.run(drive())
    assert "warm" in calls, "lifespan 未触发重依赖预热"
    assert "serving" in calls, "预热失败把启动拖垮了（必须 fail-open）"
    # 预热在后台线程跑，不得排在 session manager 之前把启动卡住
    assert calls.index("session_manager") < calls.index("serving")


def test_lifespan_installs_log_redaction_before_starting_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uvicorn 配好 handler 后进入 lifespan；脱敏必须先于预热和 MCP 会话启动。"""
    import contextlib as _contextlib

    from hutuji_mcp import server as srv

    calls: list[str] = []

    @_contextlib.asynccontextmanager
    async def fake_run():
        calls.append("session_manager")
        yield

    monkeypatch.setattr(
        srv, "mcp", SimpleNamespace(session_manager=SimpleNamespace(run=fake_run))
    )
    monkeypatch.setattr(srv, "_install_secret_redaction", lambda: calls.append("redact"))
    monkeypatch.setattr(srv, "warm_trace_deps", lambda: calls.append("warm"))

    async def drive() -> None:
        async with srv.lifespan(None):
            calls.append("serving")

    asyncio.run(drive())
    assert calls[0] == "redact"
    assert calls.index("redact") < calls.index("session_manager")


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

    # R5-INST-1：`-u` 只清**环境变量**层；pip 还读**配置文件**层（/etc/pip.conf、
    # $HOME/.pip/pip.conf、.venv/pip.conf、PIP_CONFIG_FILE 指向的任意文件），其中
    # extra-index-url / trusted-host 不在 -u 覆盖面内。`--isolated` 让 pip 忽略一切
    # 配置文件与环境变量，只认命令行 -i/--cert，把投毒面收进命令行参数，fail-closed。
    assert re.search(r'pip_args=\(-i "[^"]*" --isolated\)', install_code), (
        "pip_args 必须带 --isolated 封死配置文件层"
    )

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



def test_gate_accepts_explicit_non_adoption_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「不是 USB Host」是明确否定，不应与「当前采用 USB Host」混淆。"""
    gate = _load_agent_gate()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "agent-handoff.md").write_text(
        "板间通讯 = WiFi Telnet（不是 USB Host，不是 CH9350）。", encoding="utf-8"
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


def test_gate_forbidden_claims_catches_zero_width_joined_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R13-SIM-01：零宽字符（U+200B 等）曾切断模糊匹配让禁语逃逸——渲染不可见、
    正则不可见。扫描前剥离零宽字符后必须照常判红（剥离不动行号，方向 fail-closed）。"""
    gate = _load_agent_gate()
    (tmp_path / "README.md").write_text(
        "本轮验收完毕，可\u200b出\u200b货。", encoding="utf-8"
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    result = gate.check_forbidden_claims()

    assert result["status"] == "fail", result
    assert "README.md" in result["detail"]


def test_gate_repo_index_skips_tmp_staging_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """根 tmp 的部署/发布副本不得破坏本仓源码 basename 唯一解析。"""
    gate = _load_agent_gate()
    source = tmp_path / "mcp-server" / "pkg" / "server.py"
    staging = tmp_path / "tmp" / "deploy-copy" / "pkg" / "server.py"
    source.parent.mkdir(parents=True)
    staging.parent.mkdir(parents=True)
    source.write_text("source\n", encoding="utf-8")
    staging.write_text("copy\n", encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    index = gate._repo_file_index()

    assert index["server.py"] == [source]
    assert gate._resolve_doc_ref("server.py", index) == source

def test_gate_doc_line_refs_flags_fullwidth_colon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R13-SIM-02：全角冒号的 path：line 引用不进任何校验层（越界/外仓/指纹
    全跳过），必须判红而非静默漏网。"""
    gate = _load_agent_gate()
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "x.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (tmp_path / "docs" / "agent-handoff.md").write_text(
        "证据见 scripts/x.py：1\n", encoding="utf-8"
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    result = gate.check_doc_line_refs()

    assert result["status"] == "fail", result
    assert "全角冒号" in result["detail"]


def test_gate_doc_line_refs_zero_width_inside_ref_stays_checkable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R13-SIM-01：行号里塞零宽字符（行号里夹 U+200B 零宽空格）曾让整条引用不匹配、三层
    全跳过；剥离后按 235 校验，越界即红。"""
    gate = _load_agent_gate()
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "x.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (tmp_path / "docs" / "agent-handoff.md").write_text(
        "证据见 scripts/x.py:2\u200b35\n", encoding="utf-8"
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    result = gate.check_doc_line_refs()

    assert result["status"] == "fail", result
    assert "235" in result["detail"]


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
        "本机不可断电，但硬件已验证。",
        "version=1，本机硬件已验证。",
        "本机现已可<!-- -->出货。",
        "本机现已可**出**货。",
        "## 禁止删除日志\n\n- 硬件已验证",
    ],
)
def test_gate_forbidden_claims_rejects_unrelated_exemptions_and_markup(
    body: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """否定词/等号只约束自身短语；Markdown 排版不得切断禁止宣称。"""
    gate = _load_agent_gate()
    (tmp_path / "README.md").write_text(body, encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "GOVERNANCE_DOCS", ["README.md"])

    assert gate.check_forbidden_claims()["status"] == "fail", body


@pytest.mark.parametrize(
    "body",
    [
        "当前采用 USB Host 并禁止串口旁路。",
        "板间链路当前采用\nUSB Host。",
        "板间链路采用 USB **Host**。",
        "板间通讯采用原生\nCH<!-- -->9350。",
    ],
)
def test_gate_revived_decisions_rejects_unrelated_rejection_and_markup(
    body: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """命中短语自身在采纳否决项时，不得借无关禁令或 Markdown 排版逃逸。"""
    gate = _load_agent_gate()
    (tmp_path / "README.md").write_text(body, encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "GOVERNANCE_DOCS", ["README.md"])

    assert gate.check_revived_decisions()["status"] == "fail", body



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
        (".cursor/rules/hutuji-agent-rails.mdc", "板间通讯采用原生 USB Host。"),
    ],
)
def test_gate_revived_scan_covers_all_governance_docs(
    rel: str, body: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """原三条 REVIVED_PATTERNS 各带一份手写文件清单，合计只覆盖 5 个文件，漏掉的
    恰是优先级最高的几份：CLAUDE.md（对 agent 是 OVERRIDE 级指令）、.cursor 硬轨
    （alwaysApply）、release-readiness.md（进度真值）；`ch9350` 规则连 protocol.md
    都不扫。实测把「当前采用 USB Host，已改用 CH9350，生产落点阿里云」写进
    CLAUDE.md，门禁全绿。现在各规则共用 GOVERNANCE_DOCS。
    （2026-08-23 用户决策反转后，aliyun_as_prod 规则与 allowlist 同步下线，
    本参数化同步移除阿里云用例；反转记录见 release-readiness.md。）"""
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


def test_gate_doc_ref_parent_segments_cannot_escape_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`../` 必须按规范化后的真实路径判仓界，不能把仓外文件登记成本仓证据。"""
    gate = _load_agent_gate()
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "target.py").write_text("inside\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "foreign.cc").write_text("outside\n", encoding="utf-8")
    (repo / "docs" / "refs.md").write_text(
        "有效: target.py:1\n仓外: ../outside/foreign.cc:1\n", encoding="utf-8"
    )
    monkeypatch.setattr(gate, "ROOT", repo)

    assert gate._repo_file("../outside/foreign.cc") is None
    result = gate.check_doc_line_refs()
    assert result["status"] == "pass", result
    assert "跳过 1 处" in result["detail"], result



def test_gate_rejects_weakened_edit_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """白名单是精确安全契约；通配路径、空恢复清单、漏禁符号均须判红。"""
    gate = _load_agent_gate()
    docs = tmp_path / "docs"
    docs.mkdir()
    weakened = {
        "version": 999,
        "description": "test",
        "decision_count": 7,
        "grbl_section9_only": ["**/*"],
        "grbl_core_headers_restore_only": [],
        "grbl_forbidden_symbols": ["report_realtime_status"],
        "grbl_forbidden_intents": [],
        "hub_safe_doc_globs": ["**/*"],
        "claims_forbidden": gate.CLAIMS_FORBIDDEN,
        "rejected_current_link": gate.ALLOWLIST_EXACT["rejected_current_link"],
    }
    (docs / "agent-edit-allowlist.json").write_text(
        json.dumps(weakened), encoding="utf-8"
    )
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    result = gate.check_edit_allowlist()
    assert result["status"] == "fail", result
    assert "不一致" in result["detail"]



def test_gate_main_malformed_layer_result_collapses_to_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """层返回坏结构与层抛异常同属执行失败：必须落红报告并清 tombstone。"""
    gate = _load_agent_gate()
    _gate_main_sandbox(gate, tmp_path, monkeypatch)
    monkeypatch.setattr(gate, "profile_layers", lambda profile: [("probe", lambda: {})])
    monkeypatch.setattr(gate, "_git_head", lambda: "deadbeef" * 5)

    assert gate.main(["--profile", "docs"]) == 1
    assert not gate.RUNNING_MARK.exists()
    report = json.loads(gate.REPORT_JSON.read_text(encoding="utf-8"))
    layer = report["layers"][0]
    assert layer["id"] == "probe"
    assert layer["status"] == "fail"
    assert "返回结构不合法" in layer["detail"]


def test_gate_main_rejects_concurrent_run_without_clearing_owner_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一结果目录只允许一个门禁；二次启动不得覆盖报告或删除首轮 tombstone。"""
    gate = _load_agent_gate()
    _gate_main_sandbox(gate, tmp_path, monkeypatch)
    observed: dict[str, object] = {}

    def probe() -> dict:
        owner_mark = gate.RUNNING_MARK.read_text(encoding="utf-8")
        observed["nested_exit"] = gate.main(["--profile", "docs"])
        observed["mark_unchanged"] = gate.RUNNING_MARK.read_text(encoding="utf-8") == owner_mark
        return gate._layer("probe", "探针", "pass")

    calls = {"n": 0}

    def layers(_profile: str):
        calls["n"] += 1
        return [("probe", probe)] if calls["n"] == 1 else [("nested", lambda: gate._layer("nested", "嵌套", "pass"))]

    monkeypatch.setattr(gate, "profile_layers", layers)
    monkeypatch.setattr(gate, "_git_head", lambda: "deadbeef" * 5)

    assert gate.main(["--profile", "docs"]) == 0
    assert observed == {"nested_exit": 1, "mark_unchanged": True}
    report = json.loads(gate.REPORT_JSON.read_text(encoding="utf-8"))
    assert [layer["id"] for layer in report["layers"]] == ["probe"]


def test_gate_pid_permission_error_is_treated_as_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """无权探测 PID 时必须保守认为仍存活，禁止覆盖其他运行者的 tombstone。"""
    gate = _load_agent_gate()

    def deny(_pid, _sig):
        raise PermissionError("access denied")

    monkeypatch.setattr(gate.os, "kill", deny)
    assert gate._pid_is_alive(12345) is True


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


def test_test_count_claims_self_consistent() -> None:
    """计数漂移机器钉死：AGENTS.md 总数 == 分项和 == 实际 collect；inventory 全套同源。

    本会话手抄计数漂了三轮（976→979→981）；不变量本该是派生断言（先例：
    `required_file_set` 现算并集），不靠人记得同步。collect 只跑
    `--collect-only -q`，不执行测试体，成本数秒。历史批次记录（如 08-24 批
    976/976）不在断言面内，只钉「当前共/当前收集/全套」三处当前态表述。
    """
    import re
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    m_total = re.search(r"当前收集 \*\*(\d+) 项\*\*", agents)
    m_sum = re.search(r"当前共 \*\*(\d+) 项\*\*", agents)
    assert m_total and m_sum, "AGENTS.md 计数行格式漂移"
    declared = int(m_total.group(1))
    assert int(m_sum.group(1)) == declared, "AGENTS.md 两处总数不一致"
    breakdown = {
        name: int(n)
        for name, n in re.findall(r"`(test_\w+\.py)` \*\*(\d+)\*\* 项", agents)
    }
    assert sum(breakdown.values()) == declared, (
        f"分项和 {sum(breakdown.values())} != 总数 {declared}: {breakdown}"
    )

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
        cwd=root / "mcp-server",
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    collected: dict = {}
    for line in proc.stdout.splitlines():
        m = re.match(r"(tests/)?(test_\w+\.py)::", line.strip())
        if m:
            collected[m.group(2)] = collected.get(m.group(2), 0) + 1
    assert collected == breakdown, f"实际 collect 与 AGENTS 分项漂移: {collected}"
    assert sum(collected.values()) == declared

    inventory = (root / "docs" / "worktree-inventory.md").read_text(encoding="utf-8")
    m_inv = re.search(r"全套 \*\*(\d+)\*\* 项", inventory)
    assert m_inv, "inventory 全套计数行格式漂移"
    assert int(m_inv.group(1)) == declared, "inventory 全套计数与 AGENTS 总数不一致"


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
        "version": gate.ALLOWLIST_EXACT["version"],
        "description": "test",
        "decision_count": gate.ALLOWLIST_EXACT["decision_count"],
        "grbl_section9_only": gate.ALLOWLIST_EXACT["grbl_section9_only"],
        "grbl_core_headers_restore_only": gate.ALLOWLIST_EXACT["grbl_core_headers_restore_only"],
        "grbl_forbidden_symbols": gate.ALLOWLIST_EXACT["grbl_forbidden_symbols"],
        "grbl_forbidden_intents": gate.ALLOWLIST_EXACT["grbl_forbidden_intents"],
        "hub_safe_doc_globs": gate.ALLOWLIST_EXACT["hub_safe_doc_globs"],
        "claims_forbidden": ["stale"],
        "rejected_current_link": gate.ALLOWLIST_EXACT["rejected_current_link"],
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

@pytest.mark.parametrize(
    "func,gov_rel",
    [
        ("check_decision_anchors", "README.md"),
        ("check_anti_drift_doc", "docs/agent-anti-drift.md"),
        ("check_constraint_matrix", "docs/agent-constraint-matrix.md"),
    ],
)
def test_gate_layer_unreadable_governance_doc_returns_fail(
    func: str, gov_rel: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R5-GATE-01：这三个层的 read_text 治理文档不可读时必须塌成本层 fail。

    旧代码这三处 read_text 没有 OSError 守卫（对比 doc_line_refs /
    forbidden_claims 均有守卫），治理文档被删/不可读时异常穿透 main() 层循环。
    加守卫后应返回结构化 fail 而非抛 traceback——层循环的兜底即使失效，
    本层自身的 fail-closed 也成立（双保险）。"""
    gate = _load_agent_gate()
    gov = tmp_path / gov_rel
    gov.parent.mkdir(parents=True, exist_ok=True)
    gov.write_text("x", encoding="utf-8")
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args, **kwargs):  # noqa: ANN002, ANN003
        if path == gov:
            raise PermissionError("denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    result = getattr(gate, func)()

    assert result["status"] == "fail"
    assert "PermissionError" in result["detail"]


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


def test_gate_main_layer_exception_collapses_to_fail_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5-GATE-01：层执行抛出未捕获异常必须塌成「该层判红」并正常收尾。

    此前（test_gate_main_crash_leaves_running_mark 钉死的旧契约）异常会穿透
    main() 让解释器裸退：报告两个文件不落盘、tombstone 留盘误导「上次没跑完」、
    读报告的人看到的仍是上一轮旧绿——门禁「没有结论」而不是判红。fail-closed
    的正确形状是：异常塌成对应层 fail → overall=fail、exit 1 → 报告落盘 →
    tombstone 正常清除。退出码是真值的承诺不变。"""
    gate = _load_agent_gate()
    _gate_main_sandbox(gate, tmp_path, monkeypatch)

    def boom() -> dict:
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(gate, "profile_layers", lambda profile: [("boom", boom)])
    monkeypatch.setattr(gate, "_git_head", lambda: "deadbeef" * 5)

    code = gate.main(["--profile", "docs"])

    assert code == 1, "层异常必须塌成 overall=fail / exit 1，而不是穿透裸退"
    assert not gate.RUNNING_MARK.exists(), "异常收尾必须清除 tombstone"
    report = json.loads(gate.REPORT_JSON.read_text(encoding="utf-8"))
    assert report["overall_status"] == "fail"
    boom_layer = [L for L in report["layers"] if L["id"] == "boom"][0]
    assert boom_layer["status"] == "fail"
    assert "未捕获异常" in boom_layer["detail"]
    assert "RuntimeError" in boom_layer["detail"]

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
    "hutuji.repeat", "hutuji.pen_test", "hutuji.stop_song",
])
def test_console_device_no_argument_tools_are_whitelisted(tool_name) -> None:  # noqa: ANN001
    console = _load_root_script(
        f"hutuji_console_device_{tool_name.replace('.', '_')}_test",
        "xiaozhi_console.py",
    )
    assert console._parse_device_arguments(tool_name, None) == {}
    with pytest.raises(ValueError, match="参数必须严格"):
        console._parse_device_arguments(tool_name, '{"unexpected":1}')


def test_console_device_draw_requires_exact_url_and_preview_url_arguments() -> None:
    """hutuji.draw 双参数缺一不可（2026-08-24 fixup：白名单补 preview_url——设备侧
    IsValidDrawCapabilityUrl 双校验，单参数调用设备也拒，CLI 必须先拦下）。"""
    console = _load_root_script("hutuji_console_device_draw_test", "xiaozhi_console.py")
    url = "https://hutuji.donglicao.com/files/draw_20260807_120000_abcdefghijklmnopqrstuv.gcode"
    preview = "https://hutuji.donglicao.com/files/draw_20260807_120000_abcdefghijklmnopqrstuv.png"

    assert console._parse_device_arguments(
        "hutuji.draw", json.dumps({"url": url, "preview_url": preview})
    ) == {"url": url, "preview_url": preview}
    for raw in (None, "[]", "{}", '{"url":""}', '{"url":"x","extra":1}',
                json.dumps({"url": url}), json.dumps({"preview_url": preview}),
                '{"url":"x","preview_url":""}'):
        with pytest.raises(ValueError):
            console._parse_device_arguments("hutuji.draw", raw)
    with pytest.raises(ValueError, match="不在白名单"):
        console._parse_device_arguments("self.system.reboot", None)


def test_console_device_sing_requires_exact_url_and_title_arguments() -> None:
    console = _load_root_script("hutuji_console_device_sing_test", "xiaozhi_console.py")
    url = "https://hutuji.donglicao.com/songs/twinkle.ogg"

    assert console._parse_device_arguments(
        "hutuji.sing", json.dumps({"url": url, "title": "小星星"})
    ) == {"url": url, "title": "小星星"}
    for raw in (None, "[]", "{}", '{"url":"x"}', '{"title":"x"}',
                '{"url":"x","title":"y","extra":1}'):
        with pytest.raises(ValueError):
            console._parse_device_arguments("hutuji.sing", raw)


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

    if runner == "run_e2e":
        # run_e2e 已从 subprocess.run 改为 Popen+communicate（R11-E2E-02 整树杀），
        # 启动失败点移到 Popen() 构造期，mock 目标随之切换；run_pytest 仍是 run。
        monkeypatch.setattr(gate.subprocess, "Popen", _boom)
    else:
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
    # DEP-01：bridge 与 mcp 共用同一 venv，requirements 变更漏 restart 同样判红
    assert "/opt/hutuji/mcp-server/requirements.txt" in body
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

def test_preflight_probes_public_https_service_path() -> None:
    """R16：S3 真实走的 HTTPS:443 路径必须有机器判据。

    旧版全脚本 0 处 443 探针——`check_download` 只 curl `http://127.0.0.1/files/`
    带 Host 头模拟隧道回源，nginx 层其余探针同样都在 :80/:8300。于是证书过期或未续、
    隧道 down、公网 DNS 被改、边缘路由改错，全都不会让体检红，而设备侧必然下载失败：
    「23 项全绿 + 一画就整单失败」可以同时成立，与 hutuji_gwrite.toml 漏传那次同形。

    唯一的「验证」曾是末行给人看的 `再验：HTTPS /files 经 nginx`。提示语不是判据：
    没人跑它时与不存在等价，本脚本已有先例（旧 OPTIONAL_SKIP 让「明文 :80 /files
    须 403」从未被验证却打印全绿）。
    """
    root = Path(__file__).resolve().parents[2]
    preflight = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8")
    preflight_code = "\n".join(
        line for line in preflight.splitlines() if not line.lstrip().startswith("#")
    )

    # 两条 check 都必须注册，且下载探针必须按请求面参数化调用（漏参数会因 mode
    # 白名单判红，不会静默退化成 local 面）。
    assert "check_download local" in preflight_code
    assert "check_download public" in preflight_code
    assert 'check "公网 HTTPS:443 /files 真下 G-code 200' in preflight
    assert 'check "公网 HTTPS:443 /mcp 返回 404' in preflight

    body = preflight.split("check_download() (", 1)[1].split("\n)", 1)[0]
    # 旗标类断言必须只看代码行：函数内注释解释了「刻意不加 -k / --resolve」，
    # 连注释一起扫会被自己的说明文字判红（本测试首轮实测如此）。
    body_code = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )
    assert '[[ "$mode" == "local" || "$mode" == "public" ]] || exit 1' in body_code
    # 公网面必须真打 https://$TUNNEL_HOST，且沿用同一套 200/长度/CRC32/cmp 判据。
    assert '"https://$h/files/$f"' in body_code
    # 证书链与公网 DNS 都是 S3 下载的前提，绕过任一项就把本探针退化成 local 复读。
    assert " -k " not in body_code and "--insecure" not in body_code
    assert "--resolve" not in body_code
    # 边缘 gzip 会让 Content-Length/CRC32 比对的不是产物字节：两面都须禁压缩。
    assert body_code.count("Accept-Encoding: identity") == 2

    # /mcp 公网判据只认 404：000（DNS/TLS/隧道坏）意味着 /files 同样不可达，
    # 拿故障当合规就是本脚本禁 skip 的那条毛病。
    mcp_body = preflight.split("check_public_mcp_blocked() {", 1)[1].split("\n}", 1)[0]
    assert '"https://${TUNNEL_HOST}/mcp"' in mcp_body
    assert '[[ "$code" != "404" ]]' in mcp_body
    assert "|| return 1" in mcp_body

    # 判据落地后，末行提示语不得再把这两项列为「待人工再验」。
    tail = preflight.rstrip().splitlines()[-1]
    assert "HTTPS /files" not in tail
    assert "/mcp" not in tail

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


# ---------- 第九轮修复：config loopback 口径统一 / 空白 OUTPUT_DIR fail-closed ----------


def test_config_loopback_127_8_accepted_as_loopback() -> None:
    """R9-CFG-01：loopback 判据与 LoopbackOnlyMcpGuard 同口径（整段 127/8 + ::1）。
    旧实现用 3 字符串集合，`127.0.0.2` 被误当公网拒为「必须 HTTPS」。"""
    import importlib

    import hutuji_mcp.config as config

    assert config._is_loopback_host("127.0.0.2") is True
    assert config._is_loopback_host("127.8.8.8") is True
    assert config._is_loopback_host("::1") is True
    assert config._is_loopback_host("localhost") is True
    assert config._is_loopback_host("198.18.0.1") is False
    assert config._is_loopback_host("hutuji.donglicao.com") is False
    # 行为面：127.0.0.2 的 loopback HTTP 配置合法（旧实现此处抛 ValueError）
    assert config.validate_public_base_url("http://127.0.0.2:8300") == "http://127.0.0.2:8300"
    assert config.validate_llm_base_url("http://127.8.8.8:11434") == "http://127.8.8.8:11434"


def test_config_non_loopback_still_requires_https() -> None:
    """口径统一不得放宽公网边界：非 loopback 的 http 仍拒。"""
    import hutuji_mcp.config as config

    for bad in ("http://198.18.0.1:8300", "http://example.com"):
        with pytest.raises(ValueError, match="HTTPS"):
            config.validate_public_base_url(bad)
    with pytest.raises(ValueError, match="HTTPS"):
        config.validate_llm_base_url("http://example.com/v1")


def test_config_blank_output_dir_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """R9-CFG-02：显式空白 HUTUJI_OUTPUT_DIR 不得静默解析为进程 CWD。"""
    import importlib

    import hutuji_mcp.config as config

    importlib.reload(config)
    monkeypatch.setenv("HUTUJI_OUTPUT_DIR", "")
    with pytest.raises(ValueError, match="OUTPUT_DIR 不得为空白"):
        config.load_settings()
    monkeypatch.setenv("HUTUJI_OUTPUT_DIR", "   ")
    with pytest.raises(ValueError, match="OUTPUT_DIR 不得为空白"):
        config.load_settings()
    monkeypatch.delenv("HUTUJI_OUTPUT_DIR")
    assert config.load_settings().output_dir == config.Path("./output").resolve()


def test_config_output_dir_strips_surrounding_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCPDetail-F15：HUTUJI_OUTPUT_DIR 首尾空白先 strip 再建 Path——
    不得在文件系统里造出带空格的目录名。"""
    import importlib

    import hutuji_mcp.config as config

    importlib.reload(config)
    target = config.Path("sub").resolve()
    monkeypatch.setenv("HUTUJI_OUTPUT_DIR", f"  {target}  ")
    assert config.load_settings().output_dir == target


# ---------- 第九轮修复：hutuji-mcp.service StartLimit ----------


def test_mcp_unit_has_startlimit_matching_bridge() -> None:
    """R9-DEP-01：坏 env 时 RestartSec=5 会每 5s 无限重启（systemd 默认限流
    10s/5 次永远触发不到）；bridge unit 已带同款防护，mcp unit 不得不对称遗漏。"""
    root = Path(__file__).resolve().parents[2]
    mcp = (root / "deploy" / "hutuji-mcp.service").read_text(encoding="utf-8")
    bridge = (root / "deploy" / "hutuji-cloud-bridge.service").read_text(encoding="utf-8")
    for line in ("StartLimitIntervalSec=300", "StartLimitBurst=5"):
        assert line in mcp, f"hutuji-mcp.service 缺 {line}"
        assert line in bridge
    # 必须位于 [Unit] 段：写在 [Service] 里会被 systemd 静默忽略。
    # 注意注释里就出现「[Service]」字样，不能拿 split("[Service]") 切段。
    in_unit = False
    seen: set[str] = set()
    for raw in mcp.splitlines():
        stripped = raw.strip()
        if stripped == "[Unit]":
            in_unit = True
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_unit = False
        if in_unit and stripped.startswith("StartLimit"):
            seen.add(stripped)
    assert seen == {"StartLimitIntervalSec=300", "StartLimitBurst=5"}


# ---------- 第九轮修复：console JWT 形状脱敏 / 成功路径解析守卫 / 退出码 ----------


def test_console_redacts_jwt_under_nonstandard_field_names() -> None:
    """R9-CON-02：JWT 以非白名单字段名回显（"jwt"/"credential"/裸 data）时，
    形状级兜底必须把 eyJ...x.y.z 整体涂红。"""
    console = _load_root_script("hutuji_console_jwt_shape_test", "xiaozhi_console.py")
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )
    for text in (
        f'{{"jwt":"{jwt}"}}',
        f'{{"credential":"{jwt}"}}',
        f'{{"data":"{jwt}"}}',
        f"raw token: {jwt}",
    ):
        redacted = console._redact_text(text)
        assert jwt not in redacted, f"JWT 泄漏：{redacted}"
        assert "eyJ" not in redacted, f"JWT 头部残留：{redacted}"


def test_console_api_garbage_200_body_is_handled_not_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R9-CON-01：2xx 但正文非 JSON（网关 WAF 页标 200 常见）必须收成一行
    退出 1，不得裸抛 JSONDecodeError traceback。"""
    import io

    class _GarbageOpener:
        def open(self, req, timeout=None):  # noqa: ANN001, ANN202, ARG002
            return io.BytesIO(b"<html>WAF interstitial</html>")

    console = _load_root_script("hutuji_console_garbage200_test", "xiaozhi_console.py")
    console._URL_OPENER = _GarbageOpener()
    monkeypatch.setenv("XIAOZHI_TOKEN", "probe-token")

    captured = _capture_stderr(lambda: console.api("GET", "/agents"))

    assert "API 响应不可解析" in captured
    assert "Traceback" not in captured


def test_console_recall_rejects_non_integer_kb_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """R9-CON-01：int(kb_id) 对非数字参数必须收成 exit 1，不得裸抛 ValueError。"""
    console = _load_root_script("hutuji_console_kb_int_test", "xiaozhi_console.py")
    monkeypatch.setenv("XIAOZHI_TOKEN", "probe-token")

    captured = _capture_stderr(lambda: console.cmd_recall("abc", "query"))

    assert "kb_id 必须是整数" in captured
    assert "Traceback" not in captured


def test_console_show_not_found_exits_nonzero() -> None:
    """R9-CON-03：未找到对象必须退出 1（与 sync/update-* 同口径），链式脚本
    `show && sync` 不得把「不存在」当成功推进。"""
    import io

    class _EmptyAgentOpener:
        def open(self, req, timeout=None):  # noqa: ANN001, ANN202, ARG002
            return io.BytesIO(b'{"data":{}}')

    console = _load_root_script("hutuji_console_show_404_test", "xiaozhi_console.py")
    console._URL_OPENER = _EmptyAgentOpener()

    with pytest.raises(SystemExit) as excinfo:
        console.cmd_show("9999")
    assert excinfo.value.code == 1


# ---------------------------------------------------------------------------
# 激活码中转（activation_relay + POST /register）
# ---------------------------------------------------------------------------


def _relay_settings(tmp_path: Path, *, token: str = "tok", agent_id: str = "2094251") -> Settings:
    return Settings(
        llm_base_url="https://example.invalid/v1",
        llm_api_key="",
        llm_model="test",
        output_dir=tmp_path,
        public_base_url="https://draw.example",
        host="127.0.0.1",
        port=8300,
        output_ttl_seconds=600,
        xiaozhi_console_token=token,
        xiaozhi_agent_id=agent_id,
    )


def _mock_async_client(monkeypatch, handler):
    """把 activation_relay 里的 httpx.AsyncClient 换成 MockTransport 版。"""
    import httpx as _httpx

    transport = _httpx.MockTransport(handler)
    real_client = _httpx.AsyncClient  # patch 的是全局 httpx 属性，先存真类防递归

    def _factory(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("hutuji_mcp.activation_relay.httpx.AsyncClient", _factory)
    return transport


def test_relay_validate_mac_and_code() -> None:
    from hutuji_mcp import activation_relay as ar

    assert ar.validate_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    assert ar.validate_code("123456") == "123456"
    for bad in ("zz:00:00:00:00:00", "aabbccddeeff", "aa:bb:cc:dd:ee", "", None, 123):
        with pytest.raises(ValueError):
            ar.validate_mac(bad)  # type: ignore[arg-type]
    for bad in ("12345a", "123", "123456789", "", None, 123456):
        with pytest.raises(ValueError):
            ar.validate_code(bad)  # type: ignore[arg-type]


def test_relay_config_requires_token_and_agent_together(tmp_path: Path) -> None:
    """token 与 agent_id 只配一半必须在 import 期就炸，不得以 503 假性禁用。"""
    for token, agent in (("tok", ""), ("", "2094251")):
        with pytest.raises(ValueError, match="同时配置"):
            _relay_settings(tmp_path, token=token, agent_id=agent)
    with pytest.raises(ValueError, match="HUTUJI_XIAOZHI_API_BASE"):
        Settings(
            llm_base_url="https://example.invalid/v1",
            llm_api_key="",
            llm_model="test",
            output_dir=tmp_path,
            public_base_url="https://draw.example",
            host="127.0.0.1",
            port=8300,
            output_ttl_seconds=600,
            xiaozhi_api_base="http://xiaozhi.me/api",
        )


def test_relay_disabled_raises(tmp_path: Path) -> None:
    from hutuji_mcp import activation_relay as ar

    s = _relay_settings(tmp_path, token="", agent_id="")
    with pytest.raises(ar.RelayDisabledError):
        asyncio.run(ar.bind_device(s, "aa:bb:cc:dd:ee:ff", "123456"))


def test_relay_already_bound_skips_post(tmp_path: Path, monkeypatch) -> None:
    from hutuji_mcp import activation_relay as ar

    calls = []

    def handler(request):  # noqa: ANN001, ANN202
        calls.append(request.method)
        assert request.method == "GET"
        return __import__("httpx").Response(
            200, json={"data": [{"mac_address": "aa:bb:cc:dd:ee:ff"}]}
        )

    _mock_async_client(monkeypatch, handler)
    ar.invalidate_device_list_cache()
    s = _relay_settings(tmp_path)
    assert asyncio.run(ar.bind_device(s, "AA:BB:CC:DD:EE:FF", "123456")) == "already_bound"
    assert calls == ["GET"]  # 幂等命中后不得再发 POST
    ar.invalidate_device_list_cache()


def test_relay_bind_success(tmp_path: Path, monkeypatch) -> None:
    from hutuji_mcp import activation_relay as ar
    import httpx as _httpx

    posted = []

    def handler(request):  # noqa: ANN001, ANN202
        if request.method == "GET":
            return _httpx.Response(200, json={"data": []})
        posted.append(request)
        return _httpx.Response(200, json={"success": True, "data": {}})

    _mock_async_client(monkeypatch, handler)
    ar.invalidate_device_list_cache()
    s = _relay_settings(tmp_path)
    assert asyncio.run(ar.bind_device(s, "aa:bb:cc:dd:ee:ff", "123456")) == "bound"
    assert len(posted) == 1
    body = __import__("json").loads(posted[0].content)
    assert body == {"mac_address": "aa:bb:cc:dd:ee:ff", "verificationCode": "123456"}
    assert posted[0].headers["Authorization"] == "Bearer tok"
    ar.invalidate_device_list_cache()


def test_relay_invalid_code_mapped(tmp_path: Path, monkeypatch) -> None:
    from hutuji_mcp import activation_relay as ar
    import httpx as _httpx

    def handler(request):  # noqa: ANN001, ANN202
        if request.method == "GET":
            return _httpx.Response(200, json={"data": []})
        return _httpx.Response(
            400, json={"success": False, "code": "INVALID_VERIFICATION_CODE"}
        )

    _mock_async_client(monkeypatch, handler)
    ar.invalidate_device_list_cache()
    s = _relay_settings(tmp_path)
    with pytest.raises(ar.InvalidCodeError):
        asyncio.run(ar.bind_device(s, "aa:bb:cc:dd:ee:ff", "000000"))
    ar.invalidate_device_list_cache()


def test_relay_400_non_dict_json_is_upstream(tmp_path: Path, monkeypatch) -> None:
    """400 响应体为合法 JSON 但非 dict（list/str/null）时按形状不符走 UpstreamError
    (→502)，不得让 AttributeError 裸奔成 500（2026-08-26 复审 P3-1）。"""
    from hutuji_mcp import activation_relay as ar
    import httpx as _httpx

    for bad_body in ('["意外形状"]', '"纯字符串"', "null"):
        def handler(request, _b=bad_body):  # noqa: ANN001, ANN202
            if request.method == "GET":
                return _httpx.Response(200, json={"data": []})
            return _httpx.Response(400, content=_b.encode("utf-8"))

        _mock_async_client(monkeypatch, handler)
        ar.invalidate_device_list_cache()
        s = _relay_settings(tmp_path)
        with pytest.raises(ar.UpstreamError):
            asyncio.run(ar.bind_device(s, "aa:bb:cc:dd:ee:ff", "123456"))
        ar.invalidate_device_list_cache()


def test_relay_upstream_failures_mapped(tmp_path: Path, monkeypatch) -> None:
    from hutuji_mcp import activation_relay as ar
    import httpx as _httpx

    # token 被拒（401）与其他 4xx/5xx 统一 UpstreamError
    for status in (400, 401, 500):
        def handler(request, _s=status):  # noqa: ANN001, ANN202
            if request.method == "GET":
                return _httpx.Response(200, json={"data": []})
            return _httpx.Response(_s, json={"success": False, "code": "OTHER"})

        _mock_async_client(monkeypatch, handler)
        ar.invalidate_device_list_cache()
        s = _relay_settings(tmp_path)
        with pytest.raises(ar.UpstreamError):
            asyncio.run(ar.bind_device(s, "aa:bb:cc:dd:ee:ff", "123456"))
    # 网络异常也只报 UpstreamError
    def boom(request):  # noqa: ANN001, ANN202
        raise _httpx.ConnectError("nope", request=request)

    _mock_async_client(monkeypatch, boom)
    ar.invalidate_device_list_cache()
    with pytest.raises(ar.UpstreamError):
        asyncio.run(ar.bind_device(s, "aa:bb:cc:dd:ee:ff", "123456"))
    ar.invalidate_device_list_cache()


def test_relay_never_logs_code(tmp_path: Path, monkeypatch, caplog) -> None:
    """脱敏硬约定：无论成功/失败/异常，日志里绝不出现激活码。"""
    from hutuji_mcp import activation_relay as ar
    import httpx as _httpx
    import logging

    def handler(request):  # noqa: ANN001, ANN202
        if request.method == "GET":
            return _httpx.Response(200, json={"data": []})
        return _httpx.Response(200, json={"success": True, "data": {}})

    _mock_async_client(monkeypatch, handler)
    ar.invalidate_device_list_cache()
    s = _relay_settings(tmp_path)
    with caplog.at_level(logging.INFO):
        asyncio.run(ar.bind_device(s, "aa:bb:cc:dd:ee:ff", "918273"))
    assert "918273" not in caplog.text
    ar.invalidate_device_list_cache()


def test_register_route_maps_errors(tmp_path: Path, monkeypatch) -> None:
    """路由层窄异常→HTTP 码：503 禁用 / 400 形状 / 400 无效码 / 502 上游 / 200 成功。"""
    from hutuji_mcp import server
    from hutuji_mcp import activation_relay as ar

    async def _call(body, settings_obj):
        monkeypatch.setattr(server, "settings", settings_obj)
        return await server._activate_relay(body)

    s_disabled = _relay_settings(tmp_path, token="", agent_id="")
    with pytest.raises(server.HTTPException) as e1:
        asyncio.run(_call({"mac": "aa:bb:cc:dd:ee:ff", "code": "123456"}, s_disabled))
    assert e1.value.status_code == 503

    s = _relay_settings(tmp_path)
    with pytest.raises(server.HTTPException) as e2:
        asyncio.run(_call({"mac": "zz", "code": "123456"}, s))
    assert e2.value.status_code == 400

    def _raise_invalid(settings, mac, code):  # noqa: ANN001, ANN202, ARG001
        raise ar.InvalidCodeError()

    monkeypatch.setattr(server, "bind_device", _raise_invalid)
    with pytest.raises(server.HTTPException) as e3:
        asyncio.run(_call({"mac": "aa:bb:cc:dd:ee:ff", "code": "123456"}, s))
    assert e3.value.status_code == 400

    def _raise_upstream(settings, mac, code):  # noqa: ANN001, ANN202, ARG001
        raise ar.UpstreamError("x")

    monkeypatch.setattr(server, "bind_device", _raise_upstream)
    with pytest.raises(server.HTTPException) as e4:
        asyncio.run(_call({"mac": "aa:bb:cc:dd:ee:ff", "code": "123456"}, s))
    assert e4.value.status_code == 502

    async def _ok(settings, mac, code):  # noqa: ANN001, ANN202, ARG001
        return "bound"

    monkeypatch.setattr(server, "bind_device", _ok)
    result = asyncio.run(_call({"mac": "aa:bb:cc:dd:ee:ff", "code": "123456"}, s))
    assert result == {"status": "bound"}


# ----- 小派记忆与唱歌（memory_store / hutuji_sing / /songs / make_songs） -----

from hutuji_mcp import memory_store


def _patch_memory_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """memory_store 与 server 各自持有 settings 引用（模块级单例惯例），两边都要换。"""
    patched = _settings(tmp_path)
    monkeypatch.setattr(memory_store, "settings", patched)
    monkeypatch.setattr(server, "settings", patched)


def test_memory_remember_recall_forget_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_memory_settings(tmp_path, monkeypatch)
    assert "已记住" in asyncio.run(server.hutuji_remember("称呼", "叫我小明"))
    got = json.loads(asyncio.run(server.hutuji_recall("称呼")))
    assert got["memories"] == {"称呼": "叫我小明"}
    # 全量回忆
    all_got = json.loads(asyncio.run(server.hutuji_recall("")))
    assert all_got["total"] == 1 and all_got["memories"]["称呼"] == "叫我小明"
    # 精确删除后查不到
    assert "已忘掉" in asyncio.run(server.hutuji_forget("称呼"))
    assert "没有记住" in asyncio.run(server.hutuji_recall("称呼"))


def test_memory_same_key_overwrites_and_lru_evicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_memory_settings(tmp_path, monkeypatch)
    asyncio.run(server.hutuji_remember("画猫偏好", "橘猫"))
    asyncio.run(server.hutuji_remember("画猫偏好", "黑猫"))
    got = json.loads(asyncio.run(server.hutuji_recall("画猫偏好")))
    assert got["memories"] == {"画猫偏好": "黑猫"}
    # 写满 200 条后再写 2 条：最久未触碰的两条被逐出，总数恒 200
    for i in range(memory_store.MEMORY_MAX_ENTRIES):
        asyncio.run(server.hutuji_remember(f"k{i:03d}", "v"))
    asyncio.run(server.hutuji_remember("extra1", "v"))
    asyncio.run(server.hutuji_remember("extra2", "v"))
    all_got = json.loads(asyncio.run(server.hutuji_recall("")))
    assert all_got["total"] == memory_store.MEMORY_MAX_ENTRIES
    # k000 是最早写入且未被触碰的，应已被逐出；extra2 必在
    assert "k000" not in all_got["memories"]
    assert "extra2" in all_got["memories"]


def test_memory_truncates_and_strips_control_chars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_memory_settings(tmp_path, monkeypatch)
    long_key = "题" * (memory_store.MEMORY_KEY_MAX_CHARS + 20)
    long_value = "值" * (memory_store.MEMORY_VALUE_MAX_CHARS + 20) + "\x00\x1f"
    asyncio.run(server.hutuji_remember(long_key + "\x07", long_value))
    got = json.loads(asyncio.run(server.hutuji_recall("")))
    (key, value), = got["memories"].items()
    assert len(key) == memory_store.MEMORY_KEY_MAX_CHARS
    assert len(value) == memory_store.MEMORY_VALUE_MAX_CHARS
    assert "\x00" not in value and "\x1f" not in value


def test_memory_corrupt_file_fails_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """memory.json 损坏/结构不对一律从空开始，不得让工具 500。"""
    _patch_memory_settings(tmp_path, monkeypatch)
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / memory_store.MEMORY_FILE_NAME).write_text("{broken", encoding="utf-8")
    got = json.loads(asyncio.run(server.hutuji_recall("")))
    assert got["total"] == 0
    # 损坏后仍可正常写入并覆盖坏文件
    assert "已记住" in asyncio.run(server.hutuji_remember("a", "b"))
    assert json.loads(asyncio.run(server.hutuji_recall("a")))["memories"] == {"a": "b"}
    # 结构不对（合法 JSON 但非 dict[str,str]）同样从空开始
    (tmp_path / memory_store.MEMORY_FILE_NAME).write_text("[1,2,3]", encoding="utf-8")
    assert json.loads(asyncio.run(server.hutuji_recall("")))["total"] == 0


def test_memory_forget_star_clears_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_memory_settings(tmp_path, monkeypatch)
    asyncio.run(server.hutuji_remember("a", "1"))
    asyncio.run(server.hutuji_remember("b", "2"))
    assert "全部忘掉" in asyncio.run(server.hutuji_forget("*"))
    assert json.loads(asyncio.run(server.hutuji_recall("")))["total"] == 0


def test_memory_file_not_downloadable_and_not_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """记忆文件必须同时躲过 /files 闸门与 TTL 清理（位置安全的两条活判据）。"""
    _patch_memory_settings(tmp_path, monkeypatch)
    asyncio.run(server.hutuji_remember("称呼", "小明"))
    # /files 名字闸门拒绝 memory.json
    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_file(memory_store.MEMORY_FILE_NAME))
    assert caught.value.status_code == 404
    # TTL 清理只清 draw_* 产物，memory.json 过期语义不适用
    old = time.time() - 86400
    os.utime(tmp_path / memory_store.MEMORY_FILE_NAME, (old, old))
    server._cleanup_expired_outputs()
    assert (tmp_path / memory_store.MEMORY_FILE_NAME).is_file()


def _write_song_catalog(tmp_path: Path, entries: list[dict]) -> Path:
    songs_dir = tmp_path / "songs"
    songs_dir.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        (songs_dir / entry["file"]).write_bytes(b"OggS" + b"\x00" * 32)
    (songs_dir / "catalog.json").write_text(
        json.dumps({"songs": entries}, ensure_ascii=False), encoding="utf-8"
    )
    return songs_dir


def test_sing_empty_query_returns_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_memory_settings(tmp_path, monkeypatch)
    _write_song_catalog(tmp_path, [
        {"id": "twinkle", "title": "小星星", "file": "twinkle.ogg", "duration_seconds": 25.2},
        {"id": "tigers", "title": "两只老虎", "file": "tigers.ogg", "duration_seconds": 17.0},
    ])
    got = json.loads(asyncio.run(server.hutuji_sing("")))
    assert got["found"] is False
    assert [e["title"] for e in got["catalog"]] == ["小星星", "两只老虎"]


def test_sing_hits_by_id_and_title_substring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_memory_settings(tmp_path, monkeypatch)
    _write_song_catalog(tmp_path, [
        {"id": "twinkle", "title": "小星星", "file": "twinkle.ogg", "duration_seconds": 25.2},
        {"id": "birthday", "title": "生日快乐", "file": "birthday.ogg", "duration_seconds": 13.4},
    ])
    by_id = json.loads(asyncio.run(server.hutuji_sing("twinkle")))
    assert by_id["found"] is True
    assert by_id["url"] == "https://draw.example/songs/twinkle.ogg"
    assert by_id["title"] == "小星星" and by_id["duration_seconds"] == 25.2
    by_title = json.loads(asyncio.run(server.hutuji_sing("生日")))
    assert by_title["found"] is True and by_title["url"].endswith("/songs/birthday.ogg")


def test_sing_miss_returns_catalog_and_never_invents_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_memory_settings(tmp_path, monkeypatch)
    _write_song_catalog(tmp_path, [
        {"id": "twinkle", "title": "小星星", "file": "twinkle.ogg", "duration_seconds": 25.2},
    ])
    got = json.loads(asyncio.run(server.hutuji_sing("孤勇者")))
    assert got["found"] is False
    assert "url" not in got
    assert [e["id"] for e in got["catalog"]] == ["twinkle"]


def test_sing_catalog_missing_or_drifted_fails_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """catalog.json 缺失 → 空清单；条目指向不存在的文件 → 被剔除（不给设备发死链）。"""
    _patch_memory_settings(tmp_path, monkeypatch)
    assert json.loads(asyncio.run(server.hutuji_sing("")) )["catalog"] == []
    songs_dir = tmp_path / "songs"
    songs_dir.mkdir(parents=True, exist_ok=True)
    (songs_dir / "twinkle.ogg").write_bytes(b"OggS")
    (songs_dir / "catalog.json").write_text(json.dumps({"songs": [
        {"id": "twinkle", "title": "小星星", "file": "twinkle.ogg", "duration_seconds": 25.2},
        {"id": "ghost", "title": "不存在", "file": "ghost.ogg", "duration_seconds": 1},
        {"id": "bad", "title": "坏名字", "file": "../evil.ogg", "duration_seconds": 1},
        {"id": "nonstr", "title": "坏结构", "file": 42, "duration_seconds": 1},
    ]}, ensure_ascii=False), encoding="utf-8")
    got = json.loads(asyncio.run(server.hutuji_sing("")))
    assert [e["id"] for e in got["catalog"]] == ["twinkle"]


def test_serve_song_ok_and_headers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    songs_dir = tmp_path / "songs"
    songs_dir.mkdir()
    payload = b"OggS" + bytes(range(64))
    (songs_dir / "twinkle.ogg").write_bytes(payload)
    resp = asyncio.run(server._serve_song("twinkle.ogg"))
    assert resp.body == payload
    assert resp.media_type == "audio/ogg"
    assert resp.headers["X-Hutuji-CRC32"] == format(zlib.crc32(payload) & 0xFFFFFFFF, "08x")


@pytest.mark.parametrize(
    "name",
    [
        "../evil.ogg",       # 目录穿越
        "a/b.ogg",           # 子路径
        "UPPER.ogg",         # 大写不在白名单
        "twinkle.mp3",       # 非 ogg
        "twinkle.oggx",      # 后缀钓鱼
        ".ogg",              # 空 id
        "x" * 65 + ".ogg",   # 超长
        "memory.json",       # 记忆文件不可经 /songs 下载
    ],
)
def test_serve_song_rejects_bad_names(name: str) -> None:
    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_song(name))
    assert caught.value.status_code == 404


def test_serve_song_missing_file_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "settings", _settings(tmp_path))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(server._serve_song("twinkle.ogg"))
    assert caught.value.status_code == 404


def _load_make_songs():
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "make_songs.py"
    spec = importlib.util.spec_from_file_location("hutuji_make_songs_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_make_songs_generates_valid_ogg_and_catalog(tmp_path: Path) -> None:
    """合成产物必须是 ffmpeg 可编码的 OGG，且 catalog 与 server 白名单口径一致。"""
    make_songs = _load_make_songs()
    catalog = make_songs.build_songs(tmp_path)
    assert len(catalog) == 3
    for entry in catalog:
        assert server._SONG_FILE_RE.fullmatch(entry["file"])
        assert entry["duration_seconds"] > 0
        blob = (tmp_path / entry["file"]).read_bytes()
        assert blob[:4] == b"OggS"
        assert len(blob) > 1000  # 不是空壳
    on_disk = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert on_disk["songs"] == catalog
