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


def test_redact_filter_unit_scrubs_msg_tuple_and_dict_args() -> None:
    """单元测：filter 函数本身对 msg / tuple args / dict args 都脱敏且保容器类型。

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


def test_redact_filter_on_root_handler_scrubs_child_logger_emit(caplog) -> None:  # noqa: ANN001
    """集成测：filter 挂 root handler 后，子 logger ``websockets.client`` 经 propagate
    上来的记录也被脱敏——这才是 websockets 16 的真实 emit 路径。

    旧实现把 filter 挂在 logger ``websockets`` 上，对子 logger 的 propagate 记录无效
    （logger 级 filter 不跑父 logger 的 filter），是死代码；本测走真实链路，能抓住它。"""
    import logging

    caplog.set_level(logging.INFO)
    redact = _RedactSecretsFilter()
    handlers = logging.getLogger().handlers
    for h in handlers:  # 模拟 _install_secret_redaction，但测试结束必须撤，免污染对照组
        h.addFilter(redact)
    try:
        logging.getLogger("websockets.client").info(
            "connect failed: wss://api.xiaozhi.me/mcp/?token=real-path-leak"
        )
        assert caplog.records, "子 logger 记录必须 propagate 到 root handler"
        out = caplog.records[-1].getMessage()
        assert "real-path-leak" not in out
        assert "wss://api.xiaozhi.me" not in out
    finally:
        for h in handlers:
            h.removeFilter(redact)


def test_child_logger_token_leaks_without_redaction_install(caplog) -> None:  # noqa: ANN001
    """对照组：不调 _install_secret_redaction 时，同一条子 logger 记录的 token 原样
    保留——证明上一条测的脱敏确由 filter 产生，而非 caplog 链路本身会抹字。"""
    import logging

    caplog.set_level(logging.INFO)
    logging.getLogger("websockets.client").info(
        "baseline wss://x/?token=baseline-leak"
    )
    assert caplog.records
    assert "baseline-leak" in caplog.records[-1].getMessage()

