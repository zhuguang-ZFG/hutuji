"""小智云桥接配置与安全日志的无网络回归。"""

from __future__ import annotations

import sys

import pytest

from hutuji_mcp.cloud_bridge import (
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


def test_proxy_stderr_redacts_token_and_wss_urls() -> None:
    raw = "connecting wss://api.xiaozhi.me/mcp/?token=super-secret-value ok"
    safe = _safe_stderr_line(raw)
    assert "super-secret-value" not in safe
    assert "token=" not in safe.lower() or "redacted" in safe.lower()
    assert "wss://api.xiaozhi.me" not in safe


def test_proxy_stderr_keeps_benign_short_lines() -> None:
    assert _safe_stderr_line("listening on stdio") == "listening on stdio"


def test_redact_filter_unit_scrubs_msg_tuple_and_mapping_args() -> None:
    """单元测：filter 对 msg / tuple args / Mapping args 都脱敏且保持容器契约。

    注意这只验函数逻辑；真正「挂对位置」由下面的集成测钉死。"""
    import logging

    f = _RedactSecretsFilter()
    rec = logging.LogRecord(
        "websockets.client", logging.INFO, __file__, 1,
        "open %s", ("wss://h/mcp/?token=tuple-leak",), None,
    )
    assert f.filter(rec) is True
    assert "tuple-leak" not in rec.msg and "tuple-leak" not in rec.args[0]
    assert isinstance(rec.args, tuple)

    rec2 = logging.LogRecord(
        "websockets.client", logging.INFO, __file__, 1, "uri=%(u)s", None, None,
    )
    # 直接赋 dict：LogRecord.__init__ 对「单元素 tuple 包 dict」才会解包成 dict-args，
    # 直接传 dict 会在 __init__ 里 args[0] 取键 0 抛 KeyError，故构造后赋值。
    rec2.args = {"u": "wss://h/mcp/?token=dict-leak"}
    assert f.filter(rec2) is True
    assert isinstance(rec2.args, dict), "dict args 必须保持 dict，否则破坏 % 格式化"
    assert "dict-leak" not in rec2.args["u"]

    from collections import UserDict
    from collections.abc import Mapping

    rec3 = logging.LogRecord(
        "websockets.client", logging.INFO, __file__, 1, "uri=%(u)s", None, None,
    )
    rec3.args = UserDict({"u": "wss://h/mcp/?token=userdict-leak"})
    assert f.filter(rec3) is True
    assert isinstance(rec3.args, Mapping), "mapping args 必须保持 Mapping 兼容"
    formatted = rec3.getMessage()
    assert "userdict-leak" not in formatted
    assert "wss://h" not in formatted

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

        out = stream.getvalue()
        assert out, "子 logger 记录必须 propagate 到受控 root handler"
        assert "real-path-leak" not in out
        assert "wss://api.xiaozhi.me" not in out
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



