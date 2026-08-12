"""小智云桥接配置与安全日志的无网络回归。"""

from __future__ import annotations

import contextlib
import sys

import pytest

from hutuji_mcp.cloud_bridge import (
    _MAX_LOG_LINE,
    _RedactSecretsFilter,
    _install_secret_redaction,
    _message_label,
    _safe_stderr_line,
    load_config,
    proxy_command,
)


def test_bridge_config_requires_secret_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_ENDPOINT", raising=False)
    with pytest.raises(RuntimeError, match="MCP_ENDPOINT"):
        load_config()


def test_bridge_only_accepts_loopback_mcp_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ENDPOINT", "wss://api.example.invalid/mcp/?token=secret")
    monkeypatch.setenv("HUTUJI_LOCAL_MCP_URL", "http://203.0.113.1:8300/mcp")
    with pytest.raises(RuntimeError, match="loopback"):
        load_config()


def test_bridge_rejects_query_and_fragment_in_local_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """query/fragment 会被原样传给 mcp-proxy，行为不可预期，必须拒。"""
    monkeypatch.setenv("MCP_ENDPOINT", "wss://api.example.invalid/mcp/?token=secret")
    for bad in (
        "http://127.0.0.1:8300/mcp?x=1",
        "http://127.0.0.1:8300/mcp#frag",
    ):
        monkeypatch.setenv("HUTUJI_LOCAL_MCP_URL", bad)
        with pytest.raises(RuntimeError, match="query/fragment"):
            load_config()


def test_proxy_command_uses_current_interpreter() -> None:
    assert proxy_command("http://127.0.0.1:8300/mcp") == [
        sys.executable,
        "-m",
        "mcp_proxy",
        "--transport",
        "streamablehttp",
        "http://127.0.0.1:8300/mcp",
    ]


def test_message_log_never_contains_params() -> None:
    message = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"token":"secret"}}'
    assert _message_label(message) == "tools/call"


def test_message_label_caps_length_and_control_chars() -> None:
    long_method = "tools/" + "x" * 500
    assert len(_message_label(f'{{"method":"{long_method}"}}')) <= 200
    assert "x" * 200 not in _message_label(f'{{"method":"{long_method}"}}')
    # 云端可控标签里的换行不得注入日志行（内容本身保留，仅控制字符替换）。
    assert "\n" not in _message_label('{"method":"evil\\nINJECTED"}')
    assert _message_label('{"method":"evil\\nINJECTED"}') == "evil INJECTED"
    assert _message_label('{"id":123}').startswith("response:")

def test_message_label_never_logs_cloud_controlled_response_id() -> None:
    # 整数 id 是 JSON-RPC 常态且不可承载 secret；字符串/结构可由云端塞任意内容，
    # 不得进入日志。bool 是 int 子类但不是合法的安全数值标签。
    assert _message_label('{"jsonrpc":"2.0","id":42,"result":{}}') == "response:42"
    for raw in (
        '{"id":"attacker-controlled\\nsecret"}',
        '{"id":{"token":"secret"}}',
        '{"id":["secret"]}',
        '{"id":true}',
        '{"id":null}',
    ):
        assert _message_label(raw) == "response"


def test_proxy_stderr_redacts_bare_capability_filenames() -> None:
    token = "AbCdEf_GhIjKlMnOpQrStU"
    assert len(token) == 22
    for ext in ("gcode", "svg"):
        safe = _safe_stderr_line(
            f"failed artifact draw_20260808_120000_{token}.{ext}: denied"
        )
        assert token not in safe
        assert f"draw_20260808_120000_*.{ext}" in safe
        assert "failed artifact" in safe

def test_capability_url_keeps_artifact_path_but_drops_token() -> None:
    """完整 capability URL：token 必删，但产物路径（时间戳+扩展名）要保留供诊断。

    与无关 URL 整段吞成 <redacted-url> 不同——能力 URL 的凭据全在文件名 token 里，
    token 删掉后路径本身不再敏感。这是 F-5 的核心诉求：日志里仍看得出「哪个产物」。"""
    token = "AbCdEf_GhIjKlMnOpQrStU"
    safe = _safe_stderr_line(
        f"GET https://hutuji.donglicao.com/files/draw_20260808_120000_{token}.gcode 200"
    )
    assert token not in safe
    assert "draw_20260808_120000_*.gcode" in safe
    assert "<redacted-url>" not in safe  # 能力 URL 不整段吞
    # 无关 URL 仍整段脱敏
    assert _safe_stderr_line("see https://example.com/private/path") == "see <redacted-url>"


def test_proxy_stderr_redacts_token_and_wss_urls() -> None:
    raw = "connecting wss://api.xiaozhi.me/mcp/?token=super-secret-value ok"
    safe = _safe_stderr_line(raw)
    assert "super-secret-value" not in safe
    assert "token=" not in safe.lower() or "redacted" in safe.lower()
    assert "wss://api.xiaozhi.me" not in safe


def test_redact_cannot_be_bypassed_by_injected_allow_marker() -> None:
    """能力 URL 的放行标记（见 _redact_secret_text）由脱敏函数内部生成；外部文本
    若能原样携带该序列（stderr 路径不剥控制字符，NUL 可经 JSON 字符串转义进入），
    就能复刻标记、让带凭据的 URL 绕过整段脱敏。输入侧先剥 NUL，复刻必须失效。"""
    marker = "_" + chr(0) + "ALLOW" + chr(0) + "."
    crafted = f"https://cdn.example/draw_20260810_010101{marker}gcode/path-secret-9"
    safe = _safe_stderr_line(crafted)
    assert "path-secret-9" not in safe


def test_proxy_stderr_keeps_benign_short_lines() -> None:
    assert _safe_stderr_line("listening on stdio") == "listening on stdio"


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig", "eyJhbGciOiJIUzI1NiJ9.payload.sig"),
        ("Authorization: Bearer abcdefghijklmnop", "abcdefghijklmnop"),
        ('{"headers":{"Authorization":"Bearer abcdefghijklmnop"}}', "abcdefghijklmnop"),
        ("'Authorization': 'Bearer abcdefghijklmnop'", "abcdefghijklmnop"),
        ("authorization: bearer=deadbeefcafe", "deadbeefcafe"),
        ("Authorization=Bearer abcdefghijklmnop", "abcdefghijklmnop"),
        ("proxy-authorization: Bearer xyz", "xyz"),
        ("upstream replied Bearer abcdef123456", "abcdef123456"),
        ("loaded SK-PROJ-ABCDEF123456 ok", "SK-PROJ-ABCDEF123456"),
        ("loaded sk-proj-abcdef123456 ok", "sk-proj-abcdef123456"),
        ("bearer = abc123", "abc123"),
        ("bearer=abc123", "abc123"),
        ("x-api-key: zz9secret", "zz9secret"),
        ("api_key=AKIA1234567890 rejected", "AKIA1234567890"),
        ("apikey=zz9secret rejected", "zz9secret"),
        ("access_token=tok_value_9 expired", "tok_value_9"),
        ('{"api_key": "sk-abcdef1234567890"}', "sk-abcdef1234567890"),
        ('{"access_token":"tok_value_9"}', "tok_value_9"),
        ("token: abcdef123456 more", "abcdef123456"),
        ('{"token": "xyz987654"}', "xyz987654"),
        ("loaded key sk-proj-abcdef1234567890 ok", "sk-proj-abcdef1234567890"),
    ],
)
def test_redact_scrubs_bearer_and_assignment_forms(raw: str, secret: str) -> None:
    """Bearer / key=value / JSON \"key\": value / token: value / 裸 sk- 都不得入日志。"""
    safe = _safe_stderr_line(raw)
    assert secret not in safe
    assert "<redacted>" in safe


@pytest.mark.parametrize(
    "benign",
    [
        "listening on stdio",
        "status code 401, retry in 3s",
        "tokenizer ready",
        "monkey: banana",
        "socket: closed",
        "no bearer header found",
        "no bearer token found",
        "bearer authentication disabled",
        "notbearer=value",
        "x-authorization: Bearer abcdefghijklmnop",
        "Bearer\nsecret",
    ],
)
def test_redact_keeps_benign_diagnostics(benign: str) -> None:
    """普通诊断不得被误吞：含 token 子串的普通词、键值小段落原样保留。"""
    assert _safe_stderr_line(benign) == benign


def test_redact_bearer_assignment_does_not_leak_secret() -> None:
    safe = _safe_stderr_line("bearer = abc123")
    assert "abc123" not in safe
    assert "<redacted>" in safe



def test_redact_filter_scrubs_dict_and_list_args() -> None:
    """dict/list args 经 getMessage 渲染后的最终文本过同一脱敏函数。"""
    import logging

    f = _RedactSecretsFilter()
    rec = logging.LogRecord(
        "websockets.client", logging.INFO, __file__, 1,
        "reply=%s", ({"api_key": "sk-dictleak123456", "n": 1},), None,
    )
    assert f.filter(rec) is True
    assert "sk-dictleak123456" not in rec.getMessage()
    assert "<redacted>" in rec.getMessage()

    rec2 = logging.LogRecord(
        "websockets.client", logging.INFO, __file__, 1,
        "replies=%s", ([{"access_token": "listleak99"}, 3],), None,
    )
    assert f.filter(rec2) is True
    assert "listleak99" not in rec2.getMessage()

    rec3 = logging.LogRecord(
        "websockets.client", logging.INFO, __file__, 1,
        "auth=%s", ("Bearer tuplebearer123",), None,
    )
    assert f.filter(rec3) is True
    assert "tuplebearer123" not in rec3.getMessage()
    assert "Bearer <redacted>" in rec3.getMessage()


def test_redact_filter_unit_scrubs_final_message_and_exception_paths() -> None:
    """单元测：先保持 logging 插值语义，再覆盖异常参数与 traceback 独立通道。

    注意这只验函数逻辑；真正「挂对位置」由下面的集成测钉死。"""
    import logging

    f = _RedactSecretsFilter()
    rec = logging.LogRecord(
        "websockets.client", logging.INFO, __file__, 1,
        "open %s attempt=%03d", ("wss://h/mcp/?token=tuple-leak", 7), None,
    )
    assert f.filter(rec) is True
    assert rec.getMessage() == "open <redacted-url> attempt=007"
    assert rec.args == ()

    rec2 = logging.LogRecord(
        "websockets.client", logging.INFO, __file__, 1,
        "uri=%(u)s retry=%(n)02d", None, None,
    )
    # 直接赋 dict：LogRecord.__init__ 对「单元素 tuple 包 dict」才会解包成 dict-args，
    # 直接传 dict 会在 __init__ 里 args[0] 取键 0 抛 KeyError，故构造后赋值。
    rec2.args = {"u": "wss://h/mcp/?token=dict-leak", "n": 5}
    assert f.filter(rec2) is True
    assert rec2.getMessage() == "uri=<redacted-url> retry=05"

    from collections import UserDict

    rec3 = logging.LogRecord(
        "websockets.client", logging.INFO, __file__, 1,
        "uri=%(u)s retry=%(n)02d", None, None,
    )
    rec3.args = UserDict({"u": "wss://h/mcp/?token=userdict-leak", "n": 5})
    assert f.filter(rec3) is True
    assert rec3.getMessage() == "uri=<redacted-url> retry=05"

    rec4 = logging.LogRecord(
        "websockets.client", logging.ERROR, __file__, 1, "connect failed: %s",
        (RuntimeError("dial wss://h/mcp/?token=object-leak"),), None,
    )
    assert f.filter(rec4) is True
    assert rec4.getMessage() == "connect failed: dial <redacted-url>"

    try:
        raise RuntimeError("handshake wss://h/mcp/?token=traceback-leak")
    except RuntimeError:
        rec5 = logging.LogRecord(
            "websockets.client", logging.ERROR, __file__, 1,
            "websocket failure", (), sys.exc_info(),
        )
    assert f.filter(rec5) is True
    assert rec5.exc_info is None
    formatted = logging.Formatter("%(levelname)s %(message)s").format(rec5)
    assert "traceback-leak" not in formatted
    assert "wss://h" not in formatted
    assert "Traceback (most recent call last):" in formatted
    assert "RuntimeError: handshake <redacted-url>" in formatted
    assert __file__ not in formatted
    assert "raise RuntimeError" not in formatted


def test_redact_filter_on_root_handler_scrubs_child_logger_emit() -> None:
    """集成测：真实安装器能脱敏子 logger 经 root handler 格式化的输出。"""
    import logging
    from io import StringIO

    root = logging.getLogger()
    child = logging.getLogger("websockets.client")
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    handler.setLevel(logging.INFO)
    root_level = root.level
    child_level = child.level
    child_propagate = child.propagate
    child_disabled = child.disabled
    root.addHandler(handler)
    handler_filters = [(item, tuple(item.filters)) for item in root.handlers]
    try:
        root.setLevel(logging.INFO)
        child.setLevel(logging.NOTSET)
        child.propagate = True
        child.disabled = False
        _install_secret_redaction()
        child.info("connect failed: wss://api.xiaozhi.me/mcp/?token=real-path-leak")
        child.error(
            "exception arg: %s",
            RuntimeError("dial wss://api.xiaozhi.me/mcp/?token=object-path-leak"),
        )
        try:
            raise RuntimeError(
                "handshake wss://api.xiaozhi.me/mcp/?token=traceback-path-leak"
            )
        except RuntimeError:
            child.exception("websocket crashed")

        out = stream.getvalue()
        assert out, "子 logger 记录必须 propagate 到受控 root handler"
        assert "real-path-leak" not in out
        assert "object-path-leak" not in out
        assert "traceback-path-leak" not in out
        assert "wss://api.xiaozhi.me" not in out
        assert "exception arg: dial <redacted-url>" in out
        assert "Traceback (most recent call last):" in out
        assert "RuntimeError: handshake <redacted-url>" in out
    finally:
        for item, filters in handler_filters:
            item.filters[:] = filters
        root.removeHandler(handler)
        root.setLevel(root_level)
        child.setLevel(child_level)
        child.propagate = child_propagate
        child.disabled = child_disabled


def test_child_logger_token_leaks_without_redaction_install() -> None:
    """对照组：隔离 handler 未安装 filter 时仍会原样格式化 token。"""
    import logging
    from io import StringIO

    child = logging.getLogger("websockets.client.no_install_control")
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(logging.INFO)
    child_level = child.level
    child_propagate = child.propagate
    child_disabled = child.disabled
    child.addHandler(handler)
    try:
        child.setLevel(logging.INFO)
        child.propagate = False
        child.disabled = False
        child.info("baseline wss://x/?token=baseline-leak")
        assert "baseline-leak" in stream.getvalue()
    finally:
        child.removeHandler(handler)
        child.setLevel(child_level)
        child.propagate = child_propagate
        child.disabled = child_disabled


def test_redact_filter_caps_oversized_formatted_and_exception_records() -> None:
    import logging

    filt = _RedactSecretsFilter()
    rec = logging.LogRecord(
        "websockets.client", logging.ERROR, __file__, 1,
        "payload=%s", ("wss://h/mcp/?token=" + "secret" * 100,), None,
    )
    assert filt.filter(rec) is True
    assert len(rec.getMessage()) <= _MAX_LOG_LINE
    assert "secret" not in rec.getMessage()
    try:
        raise RuntimeError("traceback wss://h/mcp/?token=" + "exception-secret" * 100)
    except RuntimeError:
        exc_rec = logging.LogRecord(
            "websockets.client", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )
    assert filt.filter(exc_rec) is True
    assert len(exc_rec.getMessage()) <= _MAX_LOG_LINE
    assert exc_rec.exc_info is None
    assert "exception-secret" not in exc_rec.getMessage()
    formatted_exc = "Traceback (most recent call last):\n  File \"adjacent.py\", line 9, in boom\n    raise RuntimeError(\"source-adjacent-secret\")\nRuntimeError: exception-only"
    exc_text_rec = logging.LogRecord(
        "websockets.client", logging.ERROR, __file__, 1, "failed", (), None
    )
    exc_text_rec.exc_text = formatted_exc
    assert filt.filter(exc_text_rec) is True
    assert "Traceback (most recent call last):" in exc_text_rec.getMessage()
    assert "adjacent.py" not in exc_text_rec.getMessage()
    assert "source-adjacent-secret" not in exc_text_rec.getMessage()
    assert "RuntimeError: exception-only" in exc_text_rec.getMessage()

    stack_rec = logging.LogRecord(
        "websockets.client", logging.ERROR, __file__, 1, "stack", (), None
    )
    stack_rec.stack_info = (
        "Stack (most recent call last):\n"
        "  File \"stack.py\", line 4, in run\n"
        "    marker = \"stack-adjacent-secret\"\n"
    )
    assert filt.filter(stack_rec) is True
    assert "stack.py" not in stack_rec.getMessage()
    assert "stack-adjacent-secret" not in stack_rec.getMessage()


class _BlockingWebSocket:
    def __aiter__(self):
        return self

    async def __anext__(self):
        await __import__("asyncio").Event().wait()

    async def send(self, message: str) -> None:
        del message


class _WebSocketContext:
    async def __aenter__(self):
        return _BlockingWebSocket()

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False


def _capture_proxy_process(monkeypatch: pytest.MonkeyPatch, code: str):
    import asyncio
    from hutuji_mcp import cloud_bridge

    processes = []
    create = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):  # noqa: ANN002, ANN003
        process = await create(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(cloud_bridge.websockets, "connect", lambda *args, **kwargs: _WebSocketContext())
    monkeypatch.setattr(cloud_bridge, "proxy_command", lambda url: [sys.executable, "-c", code])
    monkeypatch.setattr(cloud_bridge.asyncio, "create_subprocess_exec", capture)
    return cloud_bridge, processes


def _run_with_reaped_children(coro_factory, processes, *, timeout: float = 5.0) -> None:
    """跑一段 async 用例，无论结果如何都杀掉测试自己 spawn 的真子进程。

    这些用例故意起 `time.sleep(60)` 来验证回收路径。若被测取消/清理逻辑回归
    （例如 CancelledError 在内部被吞），旧写法 `asyncio.run(exercise())` 会从
    「判红」退化成「永久挂起」——无人值守时表现为整轮超时而非某项失败，且
    sleep(60) 子进程留在机器上。故：①整段套 wait_for；②finally 无条件收尸。
    """
    import asyncio

    async def _bounded() -> None:
        await asyncio.wait_for(coro_factory(), timeout=timeout)

    try:
        asyncio.run(_bounded())
    finally:
        for process in processes[:]:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()


def test_bridge_reaps_proxy_when_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    cloud_bridge, processes = _capture_proxy_process(
        monkeypatch, "import time; time.sleep(60)"
    )

    async def exercise() -> None:
        task = asyncio.create_task(cloud_bridge.bridge_once(cloud_bridge.BridgeConfig(
            endpoint="wss://example.invalid/mcp?token=secret",
            local_mcp_url="http://127.0.0.1:8300/mcp",
        )))
        # 等 spawn 完成，但不能裸自旋：spawn 若因回归始终不发生，`while not
        # processes` 会把用例挂死在这一行。给它一个与整体超时同量级的上界。
        deadline = asyncio.get_running_loop().time() + 3.0
        while not processes:
            assert asyncio.get_running_loop().time() < deadline, "mcp-proxy 未被 spawn"
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run_with_reaped_children(exercise, processes)
    assert processes, "未捕获到 spawn 的子进程"
    assert processes[0].returncode is not None


def test_bridge_reaps_proxy_when_stderr_line_exceeds_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    cloud_bridge, processes = _capture_proxy_process(
        monkeypatch,
        "import sys,time; sys.stderr.write('x'*512); sys.stderr.flush(); time.sleep(60)",
    )
    monkeypatch.setattr(cloud_bridge, "MCP_PROXY_STREAM_LIMIT", 128)

    async def exercise() -> None:
        with pytest.raises(ValueError):
            await asyncio.wait_for(
                cloud_bridge.bridge_once(cloud_bridge.BridgeConfig(
                    endpoint="wss://example.invalid/mcp?token=secret",
                    local_mcp_url="http://127.0.0.1:8300/mcp",
                )),
                timeout=1,
            )

    asyncio.run(exercise())
    assert processes[0].returncode is not None


def test_bridge_reaps_proxy_when_ready_marker_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ready 文件写失败也必须回收刚 spawn 的 mcp-proxy。

    `_set_bridge_ready(True)` 若留在 try 之外，运行时目录缺失/权限不足就会在
    spawn 之后、finally 生效之前抛错 → mcp-proxy 变孤儿；`run_forever` 每次
    重连再 spawn 一个，泄漏无上限累积。
    """
    import asyncio

    cloud_bridge, processes = _capture_proxy_process(
        monkeypatch, "import time; time.sleep(60)"
    )

    def exploding_ready(ready: bool) -> None:
        if ready:
            raise OSError("运行时目录不可写")

    monkeypatch.setattr(cloud_bridge, "_set_bridge_ready", exploding_ready)

    async def exercise() -> None:
        with pytest.raises(OSError, match="运行时目录不可写"):
            await cloud_bridge.bridge_once(cloud_bridge.BridgeConfig(
                endpoint="wss://example.invalid/mcp?token=secret",
                local_mcp_url="http://127.0.0.1:8300/mcp",
            ))

    asyncio.run(exercise())
    assert processes, "应已 spawn mcp-proxy 才能验证回收"
    assert processes[0].returncode is not None



class _StopProbe(BaseException):
    """BaseException 派生：绕过 `run_forever` 的 `except Exception`，用于终止无限循环。"""


def _backoff_sequence(
    monkeypatch: pytest.MonkeyPatch, *, session_seconds: float, fail: bool, rounds: int
) -> list[float]:
    """跑 `rounds` 轮 `run_forever`，返回每轮实际睡眠的重连间隔。

    时钟与 sleep 全部替换为假实现：`fake_sleep` 推进假时钟，`fake_bridge_once`
    按 `session_seconds` 声明本次会话存活多久，故不依赖真实时间。
    """
    import asyncio
    from types import SimpleNamespace

    from hutuji_mcp import cloud_bridge

    delays: list[float] = []
    now = {"t": 0.0}
    calls = {"n": 0}

    async def fake_bridge_once(_config) -> None:  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] > rounds:
            raise _StopProbe
        now["t"] += session_seconds
        if fail:
            raise OSError("dial failed")

    async def fake_sleep(delay: float, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        del args, kwargs
        delays.append(delay)
        now["t"] += delay

    monkeypatch.setattr(cloud_bridge, "bridge_once", fake_bridge_once)
    monkeypatch.setattr(cloud_bridge, "time", SimpleNamespace(monotonic=lambda: now["t"]))
    monkeypatch.setattr(cloud_bridge.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(cloud_bridge, "_set_bridge_ready", lambda ready: None)

    async def exercise() -> None:
        with pytest.raises(_StopProbe):
            await cloud_bridge.run_forever(
                cloud_bridge.BridgeConfig(
                    endpoint="wss://example.invalid/mcp?token=secret",
                    local_mcp_url="http://127.0.0.1:8300/mcp",
                )
            )

    asyncio.run(exercise())
    return delays


def test_backoff_grows_when_clean_close_arrives_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """云端握手后立刻干净关闭（1000/1001）不得复位退避。

    `async for message in websocket` 对 1000/1001 是**正常退出**（实测；1008 才抛
    `ConnectionClosedError`），故 `bridge_once` 正常返回。旧实现在这条路上无条件把
    backoff 复位成 `INITIAL_BACKOFF_SECONDS`，于是维护/会话被顶/空闲回收时退化成
    1 秒/次、无上限的重连风暴，反复冲击不属于我们的厂商接入点。
    """
    from hutuji_mcp import cloud_bridge

    delays = _backoff_sequence(
        monkeypatch,
        session_seconds=cloud_bridge.HEALTHY_SESSION_SECONDS / 60.0,
        fail=False,
        rounds=6,
    )
    assert delays == [1, 2, 4, 8, 16, 32], f"短命干净关闭必须指数退避，实际 {delays}"


def test_backoff_resets_after_healthy_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """撑过时长门槛的会话断开后，退避复位到初值（原意保留）。"""
    from hutuji_mcp import cloud_bridge

    delays = _backoff_sequence(
        monkeypatch,
        session_seconds=cloud_bridge.HEALTHY_SESSION_SECONDS,
        fail=False,
        rounds=4,
    )
    assert delays == [
        cloud_bridge.INITIAL_BACKOFF_SECONDS
    ] * 4, f"健康会话须复位退避，实际 {delays}"


def test_backoff_still_grows_and_caps_on_error_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """异常路径（连不上/协议错）行为不变，且封顶在 MAX_BACKOFF_SECONDS。"""
    from hutuji_mcp import cloud_bridge

    delays = _backoff_sequence(monkeypatch, session_seconds=0.0, fail=True, rounds=9)
    assert delays == [1, 2, 4, 8, 16, 32, 60, 60, 60], f"实际 {delays}"
    assert max(delays) == cloud_bridge.MAX_BACKOFF_SECONDS


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pass@127.0.0.1:8300/mcp",
        "http://user@127.0.0.1:8300/mcp",
        "http://:pass@localhost:8300/mcp",
    ],
)
def test_bridge_local_mcp_url_rejects_userinfo(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CB-01：HUTUJI_LOCAL_MCP_URL 不得携带凭据。

    load_config 校验了 scheme、hostname、裸 `?`/`#`、path，独缺 userinfo。
    `hostname` 取的是 `@` 之后的部分，故 `http://user:pass@127.0.0.1:8300/mcp` 完整
    通过 loopback 判据，随后整串进 proxy_command() 的 argv——本机任何用户 `ps` 即可
    读到明文凭据，而 deploy/preflight.sh 的 check_bridge_env 特意让 endpoint 走 stdin
    就是为了避免这件事。config.py 的两个同族谓词（validate_llm_base_url /
    validate_public_base_url）都显式拒 username/password，本函数此前独缺。
    """
    monkeypatch.setenv("MCP_ENDPOINT", "wss://example.invalid/mcp")
    monkeypatch.setenv("HUTUJI_LOCAL_MCP_URL", url)

    with pytest.raises(RuntimeError, match="不允许携带用户名/密码"):
        load_config()


def test_bridge_local_mcp_url_error_does_not_echo_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报错文本不得回显 URL 本身：异常会进日志，回显等于把凭据搬进日志。"""
    monkeypatch.setenv("MCP_ENDPOINT", "wss://example.invalid/mcp")
    monkeypatch.setenv("HUTUJI_LOCAL_MCP_URL", "http://alice:s3cr3t@127.0.0.1:8300/mcp")

    with pytest.raises(RuntimeError) as excinfo:
        load_config()

    message = str(excinfo.value)
    assert "s3cr3t" not in message
    assert "alice" not in message


def test_bridge_local_mcp_url_still_accepts_plain_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无凭据的 loopback URL 不得被 CB-01 误伤。"""
    monkeypatch.setenv("MCP_ENDPOINT", "wss://example.invalid/mcp")
    monkeypatch.setenv("HUTUJI_LOCAL_MCP_URL", "http://127.0.0.1:8300/mcp")

    config = load_config()

    assert config.local_mcp_url == "http://127.0.0.1:8300/mcp"

def test_message_label_survives_deeply_nested_cloud_json() -> None:
    """云端可下发深嵌套 JSON；`json.loads` 抛的是 RecursionError 而非
    JSONDecodeError，旧捕获元组漏了它。

    可达链：云端消息 → `logger.info("云端 MCP 请求: %s", _message_label(msg))`
    → RecursionError 穿出 → bridge_once 的 BaseException 汇总 → 重抛 → 重连。
    一个只负责打日志的辅助函数不该有能力掀掉数据通路。
    """
    payload = "[" * 100_000 + "]" * 100_000
    assert _message_label(payload) == "unknown"


def test_message_label_still_rejects_non_json_and_bad_utf8() -> None:
    """补 RecursionError 不能顺手放宽既有两类：仍走 unknown 而非抛错。"""
    assert _message_label("not json at all") == "unknown"
    assert _message_label(b"\xff\xfe not utf8") == "unknown"


def test_session_activity_only_counts_inflight_requests() -> None:
    """R4：stall_seconds 必须只在「有已转发、未回应的请求」时计时。

    纯 idle 会话（云端长时间不发消息）last_inbound<=last_outbound，返回 0——
    这是不误杀合法空闲连接的关键，看门狗只据此判死。
    """
    from hutuji_mcp.cloud_bridge import _SessionActivity

    clock = {"t": 1000.0}
    activity = _SessionActivity(clock=lambda: clock["t"])

    # 握手初值：无在途请求。
    clock["t"] = 2000.0
    assert activity.stall_seconds() == 0.0

    # 一条请求转发出去、尚无回包：开始计时。
    activity.mark_inbound()
    clock["t"] = 2005.0
    assert activity.stall_seconds() == pytest.approx(5.0)

    # 收到任意回包即清零，哪怕时间继续走。
    activity.mark_outbound()
    clock["t"] = 9999.0
    assert activity.stall_seconds() == 0.0


def test_watchdog_raises_on_stalled_inflight_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有在途请求且超过 WATCHDOG_STALL_SECONDS 无回包 → 抛错触发重连。"""
    import asyncio
    from types import SimpleNamespace

    from hutuji_mcp import cloud_bridge

    clock = {"t": 1000.0}
    activity = cloud_bridge._SessionActivity(clock=lambda: clock["t"])
    # 先推进时钟再标入站，令 last_inbound 严格晚于握手初值 last_outbound——
    # 否则二者相等，stall_seconds 恒为 0，看门狗永不触发（会把用例挂死）。
    clock["t"] = 1001.0
    activity.mark_inbound()  # 一条未回应的请求
    process = SimpleNamespace(returncode=None)

    async def fake_sleep(_seconds: float) -> None:
        clock["t"] += cloud_bridge.WATCHDOG_STALL_SECONDS

    monkeypatch.setattr(cloud_bridge.asyncio, "sleep", fake_sleep)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="假死"):
            # 不套 wait_for：fake_sleep 不让出事件循环，超时定时器无从触发；
            # 逻辑正确时看门狗首轮即抛错。若回归成不终止，靠 pytest 全局超时兜底。
            await cloud_bridge._watchdog(process, activity)

    asyncio.run(run())


def test_watchdog_never_trips_idle_session_and_exits_on_proxy_death(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """纯 idle（零入站）即便时钟狂跳也永不判死；proxy 退出后看门狗干净退场。"""
    import asyncio
    from types import SimpleNamespace

    from hutuji_mcp import cloud_bridge

    clock = {"t": 1000.0}
    activity = cloud_bridge._SessionActivity(clock=lambda: clock["t"])  # 从不 mark_inbound
    process = SimpleNamespace(returncode=None)
    calls = {"n": 0}

    async def fake_sleep(_seconds: float) -> None:
        clock["t"] += cloud_bridge.WATCHDOG_STALL_SECONDS * 10
        calls["n"] += 1
        if calls["n"] >= 3:
            process.returncode = 0  # 模拟 proxy 退出，让看门狗按 returncode 分支返回

    monkeypatch.setattr(cloud_bridge.asyncio, "sleep", fake_sleep)

    async def run() -> None:
        # 不得抛：idle 会话不判死；proxy 退出后必须返回而非空转。
        await asyncio.wait_for(cloud_bridge._watchdog(process, activity), timeout=2)

    asyncio.run(run())
    assert calls["n"] == 3


def test_bridge_once_watchdog_failure_reaps_proxy_and_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """看门狗必须真被 bridge_once 编入 task 集：其 RuntimeError 要作为 first_error
    重抛（→ run_forever 重连），且刚 spawn 的 mcp-proxy 必须被回收。"""
    import asyncio

    cloud_bridge, processes = _capture_proxy_process(
        monkeypatch, "import time; time.sleep(60)"
    )

    async def boom(process, activity):  # noqa: ANN001
        del process, activity
        raise RuntimeError("mcp-proxy 假死 probe")

    monkeypatch.setattr(cloud_bridge, "_watchdog", boom)

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="假死 probe"):
            await cloud_bridge.bridge_once(cloud_bridge.BridgeConfig(
                endpoint="wss://example.invalid/mcp?token=secret",
                local_mcp_url="http://127.0.0.1:8300/mcp",
            ))

    _run_with_reaped_children(exercise, processes)
    assert processes, "未捕获到 spawn 的子进程"
    assert processes[0].returncode is not None
