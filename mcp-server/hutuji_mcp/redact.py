"""日志敏感信息脱敏共用实现。"""

from __future__ import annotations

import logging
import re

# mcp-proxy stderr / 第三方库日志可能回显 URL 或凭据；禁止把 token / Bearer /
# api_key / access_token / sk- 裸密钥 / wss 原文写入日志。各形态共用同一个
# 捕获式：<key>=<value>、JSON 里的 "key": "value"、"key":value、token: value
# 以及 Authorization: Bearer <value>，替换时保留 key 侧，方便诊断定位。
_SECRETISH_RE = re.compile(
    # (?i) 全局生效于整个模式。
    r"(?i)(?<![A-Za-z0-9])((?:api[-_]?key|access[-_]?token|token|bearer)[\"']?\s*[=:]\s*[\"']?)([^\"'\s,&}]+)"
    # Authorization 上下文：允许 key 名/分隔侧引号（覆盖 JSON/repr 包裹形态）与
    # 冒号/等号分隔，值不限形态（含纯字母 opaque token）无条件脱敏；左边界排除
    # x-authorization 等前缀。
    r"|(?<![A-Za-z0-9-])((?:authorization|proxy-authorization)[\"']?\s*[=:]\s*[\"']?bearer[ \t]+)([^\s\"',}]+)"
    # mcp-session-id（BRG-P3a）：mcp-proxy 0.12 的 log_request 恒打请求头且只掩
    # authorization/x-api-key/cookie，会话 id 明文进 stderr。它可对 loopback 面
    # 重放已建立的 /mcp 会话，按凭据同等对待（保留 key 供诊断，值涂掉）。
    r"|(?<![A-Za-z0-9-])(mcp-session-id[\"']?\s*[=:]\s*[\"']?)([^\"'\s,&}]+)"
    # 同一会话 id 的叙述形态（BRG-11-02）：MCP SDK client 每次会话建立打
    # "Received session ID: <32hex>"，经 mcp-proxy 默认 INFO 配置直落 stderr——
    # 上面的 header 形态规则匹配不到它，而它就是可对 loopback 面重放 /mcp 会话的
    # 同一个值，按同口径涂掉、保留 key 侧。
    r"|(?<![A-Za-z0-9-])((?:received\s+)?session\s+id[\"']?\s*[:=]\s*[\"']?)([^\"'\s,&}]+)"
    # 裸 Bearer header（非 Authorization 前缀）：值必须像凭据（含数字或 -_./ 符号、
    # 至少 6 位），避免把「bearer token/authentication」这类纯字母普通句子当密钥。
    r"|(?<![A-Za-z0-9])(Bearer[ \t]+)(?![=:]|header\b)((?=[^\s\"',}]*(?:[0-9]|[-_./]))[^\s\"',}]{6,})"
    r"|(?<![A-Za-z0-9])(sk-[A-Za-z0-9_-]{8,})"
)
# URL 单独一条规则、在凭据规则之后跑：凭据规则先剥掉 token=…/Bearer，URL
# 再决定整段 URL 去留。这样 URL 内的能力文件名先经结构规则脱敏后还能被识别放行，
# 保留时间戳+扩展名供诊断；其余 URL 一律整段变 <redacted-url>。
_URL_ONLY_RE = re.compile(r"(?i)(wss://\S+|https?://\S+)")
# 能力文件名自带不可猜 token，等价于 capability URL 的凭据部分。即便不带主机名，
# 裸文件名配上已知公网前缀就能直接下载，故与 token 同等对待：整段 token 变 `*`，
# 保留时间戳前缀与扩展名供诊断。
_CAPABILITY_NAME_RE = re.compile(
    r"(draw_\d{8}_\d{6})_[A-Za-z0-9_-]{22}\.(gcode|svg|png)"
)
_SIGNED_QUERY_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:x-amz-[a-z0-9-]+|x-goog-[a-z0-9-]+|"
    r"awsaccesskeyid|signature|expires|securitytoken)[ \t]*=[ \t]*)([^&\s\"']+)"
)
_MAX_LOG_LINE = 200


def _cap_log_line(text: str) -> str:
    if len(text) <= _MAX_LOG_LINE:
        return text
    # 保留首部和异常末行，既有定位上下文又不让尾部异常信息丢失。
    tail = min(80, _MAX_LOG_LINE - 2)
    return text[: _MAX_LOG_LINE - tail - 1] + "…" + text[-tail:]


def _safe_stack_text(text: str, *, exception: bool = False) -> str:
    """只保留 traceback/stack 标题；异常另保留末行，路径与源码行全部省略。"""
    lines = text.splitlines()
    if not lines:
        return ""
    safe = [lines[0]]
    if exception and len(lines) > 1:
        safe.append(lines[-1])
    return "\n".join(safe)


def _safe_exception_text(exc_info: tuple[object, object, object]) -> str:
    """格式化异常后按与预格式化 ``exc_text`` 相同的无路径规则收窄。"""
    try:
        formatted = logging.Formatter().formatException(exc_info)
    except Exception:
        return "<exception formatting failed>"
    return _safe_stack_text(formatted, exception=True)


def _redact_secret_text(text: str) -> str:
    """去掉文本中的凭据、签名查询参数与完整 URL。"""
    # 外部日志可能通过 JSON 字符串携带 NUL；先剥掉，避免伪造内部放行标记。
    text = text.replace("\x00", "")

    def _sub(match: re.Match[str]) -> str:
        for group in match.groups():
            if group is not None:
                if "://" in group:
                    return "<redacted-url>"
                if group.lower().startswith(("sk-", "wss:")):
                    return "<redacted>"
                return f"{group}<redacted>"
        return "<redacted>"

    # 先按结构去掉能力文件名的 token，并用放行标记替换，再跑凭据/URL 规则。
    # 标记让 URL 规则能保留产物时间戳+扩展名；其余 URL 一律整段脱敏。
    text = _CAPABILITY_NAME_RE.sub(lambda m: f"{m.group(1)}_\x00ALLOW\x00.{m.group(2)}", text)

    def _redact_url(match: re.Match[str]) -> str:
        url = match.group(0)
        return url if "\x00ALLOW\x00" in url else "<redacted-url>"

    text = _SIGNED_QUERY_RE.sub(r"\g<1><redacted>", text)
    text = _URL_ONLY_RE.sub(_redact_url, _SECRETISH_RE.sub(_sub, text))
    return text.replace("_\x00ALLOW\x00.", "_*.")


def _safe_stderr_line(line: str | bytes) -> str:
    """脱敏 mcp-proxy stderr，并截断过长行。"""
    if isinstance(line, bytes):
        text = line.decode("utf-8", errors="replace")
    else:
        text = line
    return _cap_log_line(_redact_secret_text(text.rstrip("\r\n")))


class _RedactSecretsFilter(logging.Filter):
    """在 handler 层脱敏第三方日志的最终文本、异常和栈信息。"""

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn AccessFormatter 不使用普通 message，而是直接解包五元组 args；
        # 只涂字段值并保留形状，否则访问日志 formatter 会在生产报错。
        if record.name == "uvicorn.access" and isinstance(record.args, (tuple, list)) and len(record.args) == 5:
            args = list(record.args)
            for index, value in enumerate(args):
                if isinstance(value, str):
                    args[index] = _redact_secret_text(value)
            record.msg = _redact_secret_text(str(record.msg))
            record.args = tuple(args)
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
            return True

        # 先完成 %-format，再统一脱敏和限长；清空 args/exc_info 防 Formatter 二次泄漏。
        try:
            message = record.getMessage()
        except Exception:
            message = "<log formatting failed>"
        message = _redact_secret_text(message)

        exception = ""
        if record.exc_info is not None:
            exception = _safe_exception_text(record.exc_info)
        elif record.exc_text:
            exception = _safe_stack_text(record.exc_text, exception=True)
        stack = _safe_stack_text(record.stack_info or "")
        suffix = "\n".join(part for part in (exception, stack) if part)
        rendered = _redact_secret_text(message + ("\n" + suffix if suffix else ""))
        rendered = _cap_log_line(rendered)
        record.msg = rendered
        record.args = ()
        record.message = rendered
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def _iter_target_loggers() -> list[logging.Logger]:
    """返回 root、uvicorn、httpx/httpcore 及其子 logger，避免漏掉直连 handler。"""
    prefixes = ("uvicorn", "httpx", "httpcore")
    result: list[logging.Logger] = [logging.getLogger()]
    seen: set[int] = {id(result[0])}
    for name in prefixes:
        logger = logging.getLogger(name)
        if id(logger) not in seen:
            result.append(logger)
            seen.add(id(logger))
    for name, candidate in logging.root.manager.loggerDict.items():
        if not isinstance(candidate, logging.Logger):
            continue
        if not any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes):
            continue
        if id(candidate) not in seen:
            result.append(candidate)
            seen.add(id(candidate))
    return result


def _install_secret_redaction() -> None:
    """把脱敏 filter 挂到目标 logger 的每个 handler，且每个 handler 只挂一次。"""
    seen_handlers: set[int] = set()
    for logger in _iter_target_loggers():
        for handler in logger.handlers:
            marker = id(handler)
            if marker in seen_handlers:
                continue
            seen_handlers.add(marker)
            if not any(isinstance(item, _RedactSecretsFilter) for item in handler.filters):
                handler.addFilter(_RedactSecretsFilter())
