"""小智云桥接配置与安全日志的无网络回归。"""

from __future__ import annotations

import sys

import pytest

from hutuji_mcp.cloud_bridge import (
    _RedactSecretsFilter,
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


def test_redact_secrets_filter_scrubs_third_party_log_records() -> None:
    """websockets 等第三方库的 LogRecord 经 filter 后，token / URL 必须被脱敏。"""
    import logging

    record = logging.LogRecord(
        name="websockets",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="connection failed: wss://api.xiaozhi.me/mcp/?token=top-secret",
        args=None,
        exc_info=None,
    )
    assert _RedactSecretsFilter().filter(record) is True
    assert "top-secret" not in record.msg
    assert "wss://api.xiaozhi.me" not in record.msg


def test_redact_secrets_filter_scrubs_positional_args() -> None:
    """filter 也要脱敏 args 里的字符串（websockets 常以 %s 传 URI）。"""
    import logging

    record = logging.LogRecord(
        name="websockets",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="opening %s",
        args=("wss://h/mcp/?token=abc123",),
        exc_info=None,
    )
    assert _RedactSecretsFilter().filter(record) is True
    assert "abc123" not in record.args[0]

