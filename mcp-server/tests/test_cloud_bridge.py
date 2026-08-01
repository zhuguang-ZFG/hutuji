"""小智云桥接配置与安全日志的无网络回归。"""

from __future__ import annotations

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


def test_proxy_stderr_redacts_token_and_wss_urls() -> None:
    raw = "connecting wss://api.xiaozhi.me/mcp/?token=super-secret-value ok"
    safe = _safe_stderr_line(raw)
    assert "super-secret-value" not in safe
    assert "token=" not in safe.lower() or "redacted" in safe.lower()
    assert "wss://api.xiaozhi.me" not in safe


def test_proxy_stderr_keeps_benign_short_lines() -> None:
    assert _safe_stderr_line("listening on stdio") == "listening on stdio"


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
