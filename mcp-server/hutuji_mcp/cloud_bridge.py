"""小智官方云 WSS ↔ 本机 FastMCP streamable HTTP 桥接。

凭据只从 ``MCP_ENDPOINT`` 环境变量读取，绝不写文件、绝不打印完整 URL。
本模块启动 ``mcp-proxy`` 子进程，把云端 WebSocket 文本帧与其 stdio 双向转发。
契约依据：小智 MCP 接入点飞书文档及官方示例 78/mcp-calculator/mcp_pipe.py。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

import websockets

logger = logging.getLogger("HUTUJI_MCP_BRIDGE")

INITIAL_BACKOFF_SECONDS = 1
MAX_BACKOFF_SECONDS = 60

# mcp-proxy stderr 可能回显 URL；禁止把 token / wss 原文写入日志。
_SECRETISH_RE = re.compile(
    r"(?i)(token=)([^&\s]+)|(wss://\S+)|(https?://\S+)"
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


@dataclass(frozen=True)
class BridgeConfig:
    endpoint: str
    local_mcp_url: str


def load_config() -> BridgeConfig:
    """读取并校验桥接配置；错误信息不包含 endpoint/token。"""
    endpoint = os.environ.get("MCP_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError("未设置 MCP_ENDPOINT（请从小智控制台复制 MCP 接入点）")
    endpoint_url = urlparse(endpoint)
    if endpoint_url.scheme != "wss" or not endpoint_url.hostname:
        raise RuntimeError("MCP_ENDPOINT 必须是合法的 wss:// 地址")

    local_url = os.environ.get(
        "HUTUJI_LOCAL_MCP_URL", "http://127.0.0.1:8300/mcp"
    ).strip()
    parsed_local = urlparse(local_url)
    if parsed_local.scheme != "http" or parsed_local.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise RuntimeError("HUTUJI_LOCAL_MCP_URL 必须是本机 http:// loopback 地址")
    if "?" in local_url or "#" in local_url:
        # 与 config.validate_public_base_url 对裸 ?/# 的显式拒绝同口径：query/
        # fragment（含空 query 的裸 `?`，urlparse 会解析出空字符串而漏过）会
        # 被原样传给 mcp-proxy，行为不可预期，直接拒。
        raise RuntimeError("HUTUJI_LOCAL_MCP_URL 不允许 query/fragment")
    if parsed_local.path.rstrip("/") != "/mcp":
        raise RuntimeError("HUTUJI_LOCAL_MCP_URL 路径必须是 /mcp")
    return BridgeConfig(endpoint=endpoint, local_mcp_url=local_url)


def proxy_command(local_mcp_url: str) -> list[str]:
    """固定使用当前虚拟环境里的 mcp-proxy，避免 PATH/解释器漂移。"""
    return [
        sys.executable,
        "-m",
        "mcp_proxy",
        "--transport",
        "streamablehttp",
        local_mcp_url,
    ]


def _message_label(message: str | bytes) -> str:
    """只提取 JSON-RPC 方法名用于安全日志，不记录参数或响应正文。

    method/id 由云端可控：截断到 _MAX_LOG_LINE 并把控制字符替换为空格，
    防止超长标签绕过日志限长或注入换行伪造日志行。"""
    label = "unknown"
    try:
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        payload = json.loads(message)
        if isinstance(payload, dict):
            label = str(payload.get("method") or f"response:{payload.get('id', '?')}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    label = "".join(ch if ch.isprintable() else " " for ch in label)
    return _cap_log_line(label)


def _redact_secret_text(text: str) -> str:
    """去掉文本中的 token 与 URL，同时保留其余诊断上下文。"""

    def _sub(match: re.Match[str]) -> str:
        if match.group(1):
            return f"{match.group(1)}<redacted>"
        return "<redacted-url>"

    return _SECRETISH_RE.sub(_sub, text)


def _safe_stderr_line(line: str | bytes) -> str:
    """脱敏 mcp-proxy stderr：去掉 token=… / wss|http(s) URL，并截断过长行。"""
    if isinstance(line, bytes):
        text = line.decode("utf-8", errors="replace")
    else:
        text = line
    return _cap_log_line(_redact_secret_text(text.rstrip("\r\n")))


class _RedactSecretsFilter(logging.Filter):
    """脱敏第三方库（如 websockets）日志记录里的 token / URL。

    websockets 在握手/连接失败时会经 root logger 打出含 ``?token=…`` 的完整 URI；
    本模块自身的 ``_safe_stderr_line`` / ``_message_label`` 只覆盖自家调用，管不到
    第三方 logger，故在此对记录文本统一脱敏。

    注意：本 filter 必须挂在 **handler** 上（见 ``_install_secret_redaction``），不能挂在
    logger 上——logger 级 filter 只对该 logger 自己 emit 的记录生效，子 logger
    （如 ``websockets.client``）经 propagate 上抛时父 logger 只贡献 handler、不跑父
    logger 的 filter，挂 logger 上等于死代码。"""

    def filter(self, record: logging.LogRecord) -> bool:
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


def _install_secret_redaction() -> None:
    """把脱敏 filter 挂到 root 的每个 handler（handler 级）。

    handler 级 filter 对一切经该 handler 的记录生效——含 ``websockets.client`` 等子
    logger 经 propagate 上来的记录。必须在 ``logging.basicConfig`` 之后调用（root 才有
    handler）。对已自行脱敏的本模块日志是幂等 no-op。"""
    redact = _RedactSecretsFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(redact)


async def _cloud_to_proxy(websocket, process: asyncio.subprocess.Process) -> None:
    assert process.stdin is not None
    async for message in websocket:
        logger.info("云端 MCP 请求: %s", _message_label(message))
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        process.stdin.write(message.encode("utf-8") + b"\n")
        await process.stdin.drain()


async def _proxy_to_cloud(process: asyncio.subprocess.Process, websocket) -> None:
    assert process.stdout is not None
    while line := await process.stdout.readline():
        message = line.decode("utf-8").rstrip("\r\n")
        logger.info("本机 MCP 响应: %s", _message_label(message))
        await websocket.send(message)
    raise RuntimeError("mcp-proxy 已退出")


async def _proxy_stderr(process: asyncio.subprocess.Process) -> None:
    assert process.stderr is not None
    while line := await process.stderr.readline():
        logger.info("mcp-proxy: %s", _safe_stderr_line(line))


async def bridge_once(config: BridgeConfig) -> None:
    """建立一次 WSS 会话；连接断开时清理子进程并交由外层重连。"""
    logger.info("正在连接小智 MCP 接入点（凭据已隐藏）")
    async with websockets.connect(
        config.endpoint,
        open_timeout=15,
        ping_interval=20,
        ping_timeout=20,
        max_size=2 * 1024 * 1024,
    ) as websocket:
        logger.info("小智 MCP WSS 已连接")
        process = await asyncio.create_subprocess_exec(
            *proxy_command(config.local_mcp_url),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stderr_task = asyncio.create_task(_proxy_stderr(process))
        tasks = {
            asyncio.create_task(_cloud_to_proxy(websocket, process)),
            asyncio.create_task(_proxy_to_cloud(process, websocket)),
        }
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            for task in pending:
                task.cancel()
        finally:
            for task in tasks:
                task.cancel()
            stderr_task.cancel()
            if process.stdin and not process.stdin.is_closing():
                process.stdin.close()
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task


async def run_forever(config: BridgeConfig) -> None:
    backoff = INITIAL_BACKOFF_SECONDS
    while True:
        try:
            await bridge_once(config)
            # 连接曾建立并正常结束：复位 backoff，避免长连接断开后重连延迟
            # 从上次失败累积的高值起步（ponytail: 无 jitter，单实例无惊群风险）。
            logger.warning("WSS 会话已结束，%d 秒后重连", INITIAL_BACKOFF_SECONDS)
            backoff = INITIAL_BACKOFF_SECONDS
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 某些网络库异常可能携带请求 URI；只记类型，避免 query token 进日志。
            logger.warning("桥接断开（%s），%d 秒后重连", type(exc).__name__, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    # 脱敏第三方库（websockets 等）经 root handler 打出的带 token URI。必须挂 handler
    # 级（见 _install_secret_redaction 注释），且须在 basicConfig 之后（root 才有 handler）。
    _install_secret_redaction()
    try:
        config = load_config()
        asyncio.run(run_forever(config))
    except KeyboardInterrupt:
        logger.info("收到退出信号")
        return 0
    except Exception as exc:
        logger.error("桥接启动失败: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
