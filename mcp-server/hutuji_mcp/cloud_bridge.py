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
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import websockets

logger = logging.getLogger("HUTUJI_MCP_BRIDGE")

INITIAL_BACKOFF_SECONDS = 1
MAX_BACKOFF_SECONDS = 60
# 会话「算健康」的时长门槛：只有存活至少这么久的会话才复位退避。
# 云端干净关闭（1000/1001：维护、会话被顶、空闲回收）会让 `bridge_once` 正常返回；
# 若不看时长一律复位，握手后立刻被关就退化成 1 秒/次、无上限的重连风暴，反复冲击
# 厂商接入点（该端点不属于我们，被限流/封禁的代价由整机承担）。实测：不看时长时
# 6 轮重连间隔恒为 1s（累计 6s），看时长后回到 1/2/4/8/16/32（累计 63s）。
HEALTHY_SESSION_SECONDS = 60

# R4 假死看门狗：mcp-proxy 子进程死锁（例如本机 /mcp 卡住、proxy 内部阻塞）时，
# 进程仍存活（returncode 为 None）、WSS 也健康（websockets 的 ping/pong 独立维持），
# 于是 _proxy_to_cloud 的 readline 与 _cloud_to_proxy 的 async-for 都不返回，
# asyncio.wait(FIRST_COMPLETED) 永不完成——桥接「在线却全哑」且不会重连（fail-open）。
# 判据刻意只看「在途请求」而非「一段时间无输出」：纯 idle 会话（云端长时间不发消息）
# 本就无输出，用输出静默判死会误杀合法空闲连接。改为「有一条已转发给 proxy 的入站
# 请求，超过 WATCHDOG_STALL_SECONDS 仍无任何回包」才判死——idle 时 last_inbound
# 不晚于 last_outbound，永不触发。命中即抛错，走 bridge_once 既有的清理 + 重连路径。
WATCHDOG_POLL_SECONDS = 20
WATCHDOG_STALL_SECONDS = 120

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
    # 裸 Bearer header（非 Authorization 前缀）：值必须像凭据（含数字或 -_./ 符号、
    # 至少 6 位），避免把「bearer token/authentication」这类纯字母普通句子当密钥。
    r"|(?<![A-Za-z0-9])(Bearer[ \t]+)(?![=:]|header\b)((?=[^\s\"',}]*(?:[0-9]|[-_./]))[^\s\"',}]{6,})"
    r"|(?<![A-Za-z0-9])(sk-[A-Za-z0-9_-]{8,})"
)
# URL 单独一条规则、在凭据规则之后跑：凭据规则先剥掉 token=…/Bearer，URL 规则
# 再决定整段 URL 去留。这样 URL 内的能力文件名先经结构规则脱敏后还能被识别放行，
# 保留时间戳+扩展名供诊断；其余 URL 一律整段变 <redacted-url>。
_URL_ONLY_RE = re.compile(r"(?i)(wss://\S+|https?://\S+)")
# 能力文件名自带不可猜 token，等价于 capability URL 的凭据部分。即便不带主机名，
# 裸文件名配上已知公网前缀就能直接下载，故与 token 同等对待：整段 token 变 `*`，
# 保留时间戳前缀与扩展名供诊断。与 server._redact_capability_name 同口径。
_CAPABILITY_NAME_RE = re.compile(
    r"(draw_\d{8}_\d{6})_[A-Za-z0-9_-]{22}\.(gcode|svg)"
)
MCP_PROXY_STREAM_LIMIT = 2 * 1024 * 1024
_MAX_LOG_LINE = 200
_READY_FILE_ENV = "HUTUJI_BRIDGE_READY_FILE"


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
    if parsed_local.username or parsed_local.password:
        # CB-01：上面那条注释自称与 config.validate_public_base_url「同口径」，但只抄了
        # 裸 ?/# 那半条——userinfo 从未校验。config.py 的两个同族谓词
        # （validate_llm_base_url / validate_public_base_url）都显式拒 username/password。
        # 缺这条的后果不是行为不可预期，而是泄密：hostname 取的是 `@` 之后的部分，
        # 故 `http://user:pass@127.0.0.1:8300/mcp` 完整通过 loopback 判据，随后整串
        # 进 proxy_command() 的 argv，本机任何用户 `ps` 即可读到明文凭据——而本仓
        # 上方 check_bridge_env 特意让 endpoint 走 stdin 就是为了避免这件事。
        # 报错文本不回显 URL 本身。
        raise RuntimeError("HUTUJI_LOCAL_MCP_URL 不允许携带用户名/密码")
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
    防止超长标签绕过日志限长或注入换行伪造日志行。返回值再经
    _redact_secret_text 去 token/URL，防 method/id 内嵌敏感串原样入日志。"""
    label = "unknown"
    try:
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        payload = json.loads(message)
        if isinstance(payload, dict):
            method = payload.get("method")
            if method:
                label = str(method)
            else:
                # 响应无 method。id 由云端可控且可为任意 JSON 值（长字符串、
                # 嵌套结构），原样拼进标签等于把云端可控数据写进日志。只在 id
                # 是整数（JSON-RPC 常态）时保留其数值用于配对请求，其余一律不记。
                raw_id = payload.get("id")
                label = (
                    f"response:{raw_id}"
                    if isinstance(raw_id, int) and not isinstance(raw_id, bool)
                    else "response"
                )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        # RecursionError：云端可下发深嵌套 JSON（实测 `"["*100000` 即触发），
        # `json.loads` 抛的不是 JSONDecodeError。本函数只为打日志算标签，
        # 绝不能让它把整条会话掀掉——不捕会穿到 bridge_once 的 BaseException
        # 汇总处并触发重连。仍用窄捕获元组，不退化成 except Exception。
        pass
    label = "".join(ch if ch.isprintable() else " " for ch in label)
    return _cap_log_line(_redact_secret_text(label))


def _redact_secret_text(text: str) -> str:
    """去掉文本中的 token / Bearer / api_key / sk- 裸密钥与 URL，保留其余诊断上下文。"""
    # 先剥 \x00：能力 URL 的放行标记是 `_\x00ALLOW\x00.`（见下），外部文本
    # （mcp-proxy stderr 可带云端可控数据，NUL 可经 JSON 字符串转义进入）若
    # 原样携带该序列即可复刻标记，让带凭据的 URL 绕过脱敏。正常诊断文本不含
    # NUL，剥除无副作用；_message_label 路径另有 isprintable 清洗，不经过这里。
    text = text.replace("\x00", "")

    def _sub(match: re.Match[str]) -> str:
        # 各 key 形态（token= / api_key= / "access_token": / Authorization: Bearer 等）
        # 保留 key 侧；sk-/wss: 前缀大小写不敏感（`SK-`/`WSS:` 同样整段替换）。
        for group in match.groups():
            if group is not None:
                if "://" in group:
                    return "<redacted-url>"
                if group.lower().startswith(("sk-", "wss:")):
                    return "<redacted>"
                return f"{group}<redacted>"
        return "<redacted>"

    # 先按结构去掉能力文件名的 token，并用放行标记替换，再跑凭据/URL 规则。
    # 标记让 URL 规则能识别「这是已脱敏的能力产物 URL」而保留其路径（时间戳+
    # 扩展名 = 哪个产物出问题），其余 URL 一律整段变 <redacted-url>；最后还原标记。
    # 不能回查 _CAPABILITY_NAME_RE：脱敏后的名字含 `*`，已不匹配 22 位 token 模式。
    text = _CAPABILITY_NAME_RE.sub(lambda m: f"{m.group(1)}_\x00ALLOW\x00.{m.group(2)}", text)

    def _redact_url(match: re.Match[str]) -> str:
        url = match.group(0)
        return url if "\x00ALLOW\x00" in url else "<redacted-url>"

    text = _URL_ONLY_RE.sub(_redact_url, _SECRETISH_RE.sub(_sub, text))
    return text.replace("_\x00ALLOW\x00.", "_*.")


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


def _set_bridge_ready(ready: bool) -> None:
    """仅在 systemd 配置的运行时目录内暴露当前 WSS 会话是否已建立。"""
    raw_path = os.environ.get(_READY_FILE_ENV, "").strip()
    if not raw_path:
        return
    path = Path(raw_path)
    if ready:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(f"{os.getpid()}\n", encoding="ascii")
        os.replace(temporary, path)
        return
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


class _SessionActivity:
    """一次会话内 cloud→proxy 与 proxy→cloud 的最近时刻，供 _watchdog 判死。

    刻意不带锁：两个 mark_* 都在同一事件循环的同步区里调用，无 await 点，天然原子。
    初值取会话建立时刻，避免握手瞬间被误判为「有在途请求且久无回包」。
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        now = clock()
        self.last_inbound = now
        self.last_outbound = now

    def mark_inbound(self) -> None:
        self.last_inbound = self._clock()

    def mark_outbound(self) -> None:
        self.last_outbound = self._clock()

    def stall_seconds(self) -> float:
        """有未回应的入站请求时返回它已等待的秒数；无在途请求时返回 0。"""
        if self.last_inbound <= self.last_outbound:
            return 0.0
        return self._clock() - self.last_inbound


async def _cloud_to_proxy(
    websocket, process: asyncio.subprocess.Process, activity: _SessionActivity
) -> None:
    assert process.stdin is not None
    async for message in websocket:
        logger.info("云端 MCP 请求: %s", _message_label(message))
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        process.stdin.write(message.encode("utf-8") + b"\n")
        await process.stdin.drain()
        # drain 之后才记：这一刻起 proxy 已收到请求，看门狗开始为它计等待时长。
        activity.mark_inbound()


async def _proxy_to_cloud(
    process: asyncio.subprocess.Process, websocket, activity: _SessionActivity
) -> None:
    assert process.stdout is not None
    while line := await process.stdout.readline():
        # 任何回包都算 proxy 仍在响应：清掉在途计时。放在 send 前，避免 send
        # 慢时把健康的回包误算成停滞。
        activity.mark_outbound()
        message = line.decode("utf-8").rstrip("\r\n")
        logger.info("本机 MCP 响应: %s", _message_label(message))
        await websocket.send(message)
    raise RuntimeError("mcp-proxy 已退出")


async def _proxy_stderr(process: asyncio.subprocess.Process) -> None:
    assert process.stderr is not None
    while line := await process.stderr.readline():
        logger.info("mcp-proxy: %s", _safe_stderr_line(line))


async def _watchdog(
    process: asyncio.subprocess.Process, activity: _SessionActivity
) -> None:
    """周期性检查 mcp-proxy 是否对在途请求假死；命中抛错触发 bridge_once 重连。"""
    while True:
        await asyncio.sleep(WATCHDOG_POLL_SECONDS)
        if process.returncode is not None:
            # 进程已退出，交给 _proxy_to_cloud 的 readline 收尾即可，看门狗退场。
            return
        if activity.stall_seconds() >= WATCHDOG_STALL_SECONDS:
            raise RuntimeError(
                f"mcp-proxy 对在途请求无响应超过 {WATCHDOG_STALL_SECONDS}s（子进程假死）"
            )


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
            limit=MCP_PROXY_STREAM_LIMIT,
        )
        # ready 文件写入与 task 创建都必须在 try 之内：一旦它们抛错（运行时目录缺失、
        # 权限不足、创建 task 时被取消），finally 才能回收刚 spawn 的 mcp-proxy。
        # 否则 run_forever 每次重连都会再 spawn 一个,泄漏进程无上限累积。
        tasks: set[asyncio.Task] = set()
        try:
            _set_bridge_ready(True)
            activity = _SessionActivity()
            tasks = {
                asyncio.create_task(_cloud_to_proxy(websocket, process, activity)),
                asyncio.create_task(_proxy_to_cloud(process, websocket, activity)),
                asyncio.create_task(_proxy_stderr(process)),
                asyncio.create_task(_watchdog(process, activity)),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            first_error: BaseException | None = None
            for task in done:
                try:
                    task.result()
                except BaseException as exc:  # noqa: BLE001 -- preserve session cancellation/error
                    if first_error is None:
                        first_error = exc
            for task in pending:
                task.cancel()
            if first_error is not None:
                raise first_error
        finally:
            async def cleanup() -> None:
                for task in tasks:
                    task.cancel()
                if process.stdin and not process.stdin.is_closing():
                    process.stdin.close()
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        if process.returncode is None:
                            with contextlib.suppress(ProcessLookupError):
                                process.kill()
                        await process.wait()
                await asyncio.gather(*tasks, return_exceptions=True)

            cleanup_task = asyncio.create_task(cleanup())
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                # 外层取消也必须等子进程回收完成，再把取消继续向上传递。
                await cleanup_task
                raise
            finally:
                _set_bridge_ready(False)


async def run_forever(config: BridgeConfig) -> None:
    # 异常退出后由 systemd 重启时，旧实例留下的标记不得被新进程复用。
    _set_bridge_ready(False)
    backoff = INITIAL_BACKOFF_SECONDS
    while True:
        started = time.monotonic()
        try:
            await bridge_once(config)
            # 会话正常结束（云端干净关闭走这条路）。只有真正撑过
            # HEALTHY_SESSION_SECONDS 的会话才复位退避——原意是「长连接断开后不该
            # 从累积的高值起步」，不是「任何正常返回都从 1 秒起步」。
            elapsed = time.monotonic() - started
            if elapsed >= HEALTHY_SESSION_SECONDS:
                backoff = INITIAL_BACKOFF_SECONDS
            logger.warning("WSS 会话已结束（存活 %.1f 秒），%d 秒后重连", elapsed, backoff)
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
