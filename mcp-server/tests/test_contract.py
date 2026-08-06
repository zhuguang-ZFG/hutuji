"""protocol v0.5 与公网安全基础契约的无硬件回归。"""

from __future__ import annotations

import asyncio
import logging
import re
import os
import time
import zlib
from pathlib import Path

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

    monkeypatch.setattr(server, "generate_svg", fake_generate_svg)
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

        monkeypatch.setattr(server, "generate_svg", fake_generate_svg)
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

        monkeypatch.setattr(server, "generate_svg", fake_generate_svg)
        monkeypatch.setattr(server, "svg_to_gcode_async", fake_convert)
        task = asyncio.create_task(server.hutuji_draw("cancel"))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert list(tmp_path.iterdir()) == []


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


def test_y_over_277mm_rejected() -> None:
    """Y=277.5 超出 Y 轴限幅 277mm。"""
    with pytest.raises(GcodeError, match="越界"):
        validate_gcode(_minimal_gcode("G1 X5 Y277.5 F8000\n"))


def test_mid_file_g91_is_rejected() -> None:
    """位置跟踪假定 G90 绝对坐标；中途切 G91 会让此后所有圆弧起点失真。"""
    with pytest.raises(GcodeError, match="G91"):
        validate_gcode(_minimal_gcode("G0 X10 Y10 F8000\nG91\nG1 X5 Y5 F8000\n"))


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
    assert preflight.count('\ncheck "') == 18
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
    `unbound variable` 退出，15 项一个都不会跑——脚本连体检结果都产不出。用行序钉死。"""
    root = Path(__file__).resolve().parents[2]
    lines = (root / "deploy" / "preflight.sh").read_text(encoding="utf-8").splitlines()

    init_last = max(
        i
        for i, line in enumerate(lines)
        if line.startswith(("TUNNEL_HOST=", "OUTPUT_DIR=", "BRIDGE_ENDPOINT="))
    )
    checks = [i for i, line in enumerate(lines) if line.startswith('check "')]
    assert len(checks) == 18
    assert min(checks) > init_last


def test_preflight_exercises_real_generation_chain() -> None:
    """第 15/16 项必须真跑生成链，不能用人工拼的 G-code 文本冒充。

    旧版 check_download 用 `printf "G21\\nG90\\nF8000\\n"` 造产物——那份内容连
    validate_gcode 自己都不接受（实测「第 4 行缺少 §5 文件头」），所以只证明了
    nginx 能按字节发文件，完全没碰生成链。已实测复现：把 hutuji_gwrite.toml 移走后
    `import hutuji_mcp.server` 仍成功、服务保持 active、/mcp 与 /files 照常响应，
    而真实 svg_to_gcode() 报「vpype 转换失败（exit 2）: --config … does not exist」，
    即 `pass=15 fail=0` 与「一画就整单失败」可以同时成立。根因是 _PROFILE_PATH 只在
    请求期由 _vpype_command() 使用，import 期不加载不校验，systemd active 抓不到。
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
    assert preflight_code.count('[[ -x "$VENV_PY" ]] || exit 1') == 2

    # 下载契约必须验的是真产物：printf 拼 G-code 的老写法不得复活。
    assert 'printf "G21' not in preflight_code
    assert "G1G90" not in preflight_code
    for marker in ('"$code" == "200"', "cmp -s", "zlib.crc32"):
        assert marker in preflight, marker


def test_preflight_pins_running_systemd_units_to_templates() -> None:
    """`is-active` 不证明跑的是哪一版 unit——加固项可全部缺失而照样 active。

    此前只有「运行中 nginx conf 与模板一致」这一项，两个 unit 却没有对应判据，
    而它们的安装方式完全同构（`install-from-repo.sh` 从 `/opt/hutuji/deploy/`
    复制到 `/etc/systemd/system/`）。unit 里的 `--host 127.0.0.1`、
    `ProtectSystem=strict`、`SystemCallFilter` 等指令一旦漂移，监听面还有第 5/6
    项兜着，加固项则无人验证。

    四条判据缺一不可：md5 一致（堵手改/漏同步）、`NeedDaemonReload=no`（systemd
    用加载时快照，忘了 reload 时磁盘一致而在跑的是旧配置）、`FragmentPath` 指向
    `/etc/systemd/system`（排除 `/lib` 残留）、无 drop-in（drop-in 能静默覆盖
    ExecStart 与全部加固项，存在时前三条什么都证明不了）。
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
    ):
        assert must in gate.GOVERNANCE_DOCS, must


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
