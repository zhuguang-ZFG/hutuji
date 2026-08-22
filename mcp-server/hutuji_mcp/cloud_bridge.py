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
_wall_clock = time.time
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
# 判死阈值取 300s = MCP SDK client 的 sse_read_timeout 默认值（SRV-P3a）：
# mcp-proxy 内部（`mcp/client/streamable_http.py:690`）对慢响应自己会等满 300s，
# 桥接看门狗若早于它开火（旧值 120s），长描摹/慢生图的合法 hutuji_draw 会被
# 桥接先掀掉——kill 的是自己人。判死只留给「连 SDK 都放弃了还没动静」之后。
WATCHDOG_STALL_SECONDS = 300
# 主动探活（2026-08-22）：当日事故证明「空闲后 proxy→MCP 链路静默死亡，云端
# tools/call 被本地秒回错误且形态不是 Session terminated」，判死器漏判。
# 双保险：空闲 PROBE_IDLE_SECONDS 后经 proxy 发一次真 tools/list（死链在空闲期暴露）；
# 任何 isError 工具回包后立即探针复核（合法工具错误的探针会健康返回，不重建）。
# 注意「空闲」剔除云端 ping 心跳：ping 由 proxy 进程本地应答，不过 proxy→MCP 链路，
# 且 60s 周期恰好压住本阈值——不剔除则探活永不开火（2026-08-23 生产日志实证）。
PROBE_IDLE_SECONDS = 60
# 探针超时要大于看门狗轮询周期、远小于在途请求判死阈值。
PROBE_TIMEOUT_SECONDS = 25
_PROBE_ID_PREFIX = "hutuji-probe-"

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
    # "Received session ID: <32hex>"（mcp/client/streamable_http.py，uuid4().hex），
    # 经 mcp-proxy 默认 INFO 配置直落 stderr——上面的 header 形态规则匹配不到它，
    # 而它就是可对 loopback 面重放 /mcp 会话的同一个值，按同口径涂掉、保留 key 侧。
    r"|(?<![A-Za-z0-9-])((?:received\s+)?session\s+id[\"']?\s*[:=]\s*[\"']?)([^\"'\s,&}]+)"
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
_STATUS_FILE_ENV = "HUTUJI_BRIDGE_STATUS_FILE"
_DEFAULT_STATUS_FILE = "/run/hutuji/cloud-bridge.status.json"
_HEALTH_FIELDS = frozenset(
    {
        "version",
        "pid",
        "cloud_connected",
        "proxy_connected",
        "proxy_pid",
        "proxy_session_recoveries",
        "reconnect_count",
        "last_event",
        "last_error",
        "updated_at",
    }
)
_health_state: dict[str, object] = {
    "version": 1,
    "pid": os.getpid(),
    "cloud_connected": False,
    "proxy_connected": False,
    "proxy_pid": None,
    "proxy_session_recoveries": 0,
    "reconnect_count": 0,
    "last_event": "stopped",
    "last_error": None,
}


class _McpSessionTerminated(RuntimeError):
    """本机 MCP session 失效；只需重建 mcp-proxy，不应拆 WSS。"""


def _health_path() -> Path:
    return Path(os.environ.get(_STATUS_FILE_ENV, _DEFAULT_STATUS_FILE))


def _write_health_state(**updates: object) -> None:
    """原子写入不含凭据的桥接状态；状态写失败不得掀掉业务链路。"""
    _health_state.update(updates)
    _health_state["pid"] = os.getpid()
    _health_state["updated_at"] = _wall_clock()
    raw_path = os.environ.get(_STATUS_FILE_ENV, "").strip()
    if not raw_path:
        return
    path = Path(raw_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(_health_state, ensure_ascii=True, sort_keys=True),
            encoding="ascii",
        )
        os.replace(temporary, path)
    except OSError as exc:
        logger.warning("桥接健康状态写入失败（%s）", type(exc).__name__)


def read_health_status() -> dict[str, object]:
    """读取本机桥接健康快照，只返回固定安全字段。"""
    status = dict(_health_state)
    try:
        raw = json.loads(_health_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return status
    if isinstance(raw, dict):
        status.update({key: raw[key] for key in _HEALTH_FIELDS if key in raw})
    return status


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
    except (ValueError, RecursionError):
        # ValueError 一族（JSONDecodeError、UnicodeDecodeError 均是其子类）：
        # 除畸形 JSON/UTF-8 外，云端还可下发超过 CPython int↔str 位数上限
        # （默认 4300 位）的整数 id，`json.loads` 抛 ValueError 而非
        # JSONDecodeError（BRG-11-01 实测：`{"id": "9"*5000 …}` 即触发）。
        # RecursionError：云端可下发深嵌套 JSON（实测 `"["*100000` 即触发），
        # `json.loads` 抛的不是 JSONDecodeError。本函数只为打日志算标签，
        # 绝不能让它把整条会话掀掉——不捕会穿到 bridge_once 的 BaseException
        # 汇总处并触发重连。仍用窄捕获元组，不退化成 except Exception。
        pass
    label = "".join(ch if ch.isprintable() else " " for ch in label)
    return _cap_log_line(_redact_secret_text(label))

def _iter_jsonrpc_objects(message: str | bytes) -> list[dict]:
    """把消息解析成 JSON-RPC 对象列表（单条或 batch）；失败返回空列表。

    失败一律 fail-open：解析问题不得武装/解武装看门狗，更不能掀会话。
    与 _message_label 同口径——只捕窄异常。
    """
    try:
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        payload = json.loads(message)
    except (ValueError, RecursionError):
        return []
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [obj for obj in payload if isinstance(obj, dict)]
    return []


def _request_ids(message: str | bytes) -> list[object]:
    """提取消息中带 id 的 JSON-RPC request 的 id 列表。

    只有同时具备「method」与非 null「id」的对象才算 request，才武装看门狗。
    notification 有 method 无 id（按规范不产生响应），response 有 id 无 method
    ——两者都不得计入在途，否则就是 R7-WD-01 的误判。
    notifications/cancelled 自身也是 notification，故同样不武装；它携带的
    params.requestId 由 _cancel_ids 单独处理（结清而非新建）。

    id 类型收窄为 str/int（排除 bool）：JSON-RPC 2.0 规定 id MUST 为
    String/Number/NULL（"MUST contain a String, Number, or NULL"），Array/Object
    即非法；json.loads 会把它们解析成 list/dict，直接做 dict 键会抛
    TypeError: unhashable type，把单条畸形消息升级成整个会话重连（R7-WD-03）。
    bool 是 int 子类（True == 1 同键），显式排除避免 bool/int 歧义。
    """
    ids: list[object] = []
    for obj in _iter_jsonrpc_objects(message):
        method = obj.get("method")
        raw_id = obj.get("id")
        if not isinstance(method, str) or raw_id is None:
            continue
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
            continue  # 非法 id（Array/Object/Boolean）：不武装、不崩溃
        ids.append(raw_id)
    return ids


def _cancel_ids(message: str | bytes) -> list[object]:
    """提取 notifications/cancelled 中被取消的 requestId 列表，用于提前结清 pending。

    requestId 与 pending id 类型不一致（str vs int）时必须双形态匹配（R7-WD-06）：
    MCP Python SDK `mcp/shared/session.py` 的 `_normalize_request_id` 会把 str id
    归一化为 int（"0" vs 0），本仓 pending 用原值做键，故 cancel 侧同时产出
    str/int 两种形态，确保任一形态都能命中。
    """
    ids: list[object] = []
    for obj in _iter_jsonrpc_objects(message):
        if obj.get("method") != "notifications/cancelled":
            continue
        params = obj.get("params")
        if isinstance(params, dict):
            cancel_id = params.get("requestId")
            if cancel_id is None:
                continue
            if isinstance(cancel_id, bool) or not isinstance(cancel_id, (str, int)):
                continue  # 非法 id 不结清
            ids.append(cancel_id)
            # 归一化：str/int 双形态都尝试匹配 pending 键
            if isinstance(cancel_id, str) and cancel_id.isdigit():
                # 超过 int↔str 位数上限（默认 4300 位）的数字串 int() 抛 ValueError；
                # requestId 云端可控，此处掀会话违反 fail-open——保留原 str 形态即可。
                try:
                    ids.append(int(cancel_id))
                except ValueError:
                    pass
            elif isinstance(cancel_id, int):
                ids.append(str(cancel_id))
    return ids


def _response_ids(message: str | bytes) -> list[object] | None:
    """提取消息中 JSON-RPC response 的 id 列表。

    response 有 id 但无 method（区别于 request/notification）。
    返回 None = 「这条消息解析不出任何 JSON-RPC 对象」，调用方须整体结清 pending：
    proxy 确实回了东西、只是我们读不懂，此时宁可解武装也不能凭解析缺口判死会话。
    返回空列表 = 「合法消息但不含 response」（如 proxy 主动发的 notification），
    pending 保持不变。

    id 类型收窄与 _request_ids 同口径（R7-WD-03）：Array/Object/Boolean 一律跳过，
    不结清、不崩溃。
    """
    objs = _iter_jsonrpc_objects(message)
    if not objs:
        return None
    ids: list[object] = []
    for obj in objs:
        raw_id = obj.get("id")
        if obj.get("method") is None and raw_id is not None:
            if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
                continue  # 非法 id 不结清
            ids.append(raw_id)
    return ids


def _ping_request_ids(message: str | bytes) -> set[object]:
    """提取 ping request 的 id 集合（method=="ping" 且 id 合法，口径同 _request_ids）。"""
    ids: set[object] = set()
    for obj in _iter_jsonrpc_objects(message):
        if obj.get("method") != "ping":
            continue
        raw_id = obj.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
            continue
        ids.add(raw_id)
    return ids


def _is_ping_only(message: str | bytes) -> bool:
    """消息里除 ping 外没有任何对象才为 True；解析失败/含其他方法一律 False。

    云端每 60s 的 ping 心跳由 mcp-proxy 进程本地应答（create_proxy_server 未注册
    PingRequest 转发），不经过 proxy→MCP 链路——若让它刷新空闲钟，空闲探活恰好被
    同周期心跳永久压住、从不开火（2026-08-23 生产日志实证：ping 每分钟一条，
    PROBE_IDLE_SECONDS=60 下 idle 永远到不了阈值）。
    """
    objs = _iter_jsonrpc_objects(message)
    return bool(objs) and all(obj.get("method") == "ping" for obj in objs)


def _is_session_terminated_response(message: str | bytes) -> bool:
    """判定 mcp-proxy 回包是否是「本机 /mcp 会话已被服务端终止」的合成错误。

    指纹来源（R10-BRG-01）：MCP SDK client `mcp/client/streamable_http.py`
    对 POST 404 只调 `_send_session_terminated_error()` 合成
    `{"error":{"code":32600,"message":"Session terminated"}}`（code 是上游写的
    正 32600，负值形态一并接受），**不重新 initialize**——hutuji-mcp 重启或
    会话 idle 回收（session_idle_timeout=3600）后，该错误对每条后续请求无限
    重复，回包持续流动使停滞看门狗永不触发。

    两种形态都判死：
    1. 顶层 error.message 精确等于 Session terminated（SDK 原生合成形态）；
    2. mcp-proxy 0.12 的 CallToolResult 包装形态（`proxy_server.py` call_tool
       的 `except Exception` 把 McpError 包成正常 result）：
       `{"result":{"isError":true,"content":[{"type":"text","text":"Session
       terminated"}]}}`——2026-08-18 本机复现（server 重启后 tools/call）实测，
       生产 14 小时 idle 会话回收后三次 tools/call 均此形态，判死曾因此失联。
    形态 2 必须 isError 显式 true 且文本精确匹配，普通工具错误（中文文案、
    isError 缺省/false、相似但非精确文本）不误杀；解析不了一律 False
    （fail-open，与 _message_label 同口径——判死是重手术，宁可漏判交给看门狗）。
    """
    objs = _iter_jsonrpc_objects(message)
    if not objs:
        return False
    for obj in objs:
        error = obj.get("error")
        if (
            isinstance(error, dict)
            and error.get("message") == "Session terminated"
            and error.get("code") in (32600, -32600)
        ):
            return True
        result = obj.get("result")
        if isinstance(result, dict) and result.get("isError") is True:
            for item in result.get("content") or []:
                if (
                    isinstance(item, dict)
                    and item.get("type") == "text"
                    and item.get("text") == "Session terminated"
                ):
                    return True
    return False


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
    """按 JSON-RPC id 跟踪在途请求，供 _watchdog 判死 mcp-proxy。

    刻意不带锁：所有 mark_* 都在同一事件循环的同步区里调用，无 await 点，天然原子。

    为什么必须按 id 归属（R7-WD-01/02，两处都是旧「单对时间戳」模型的产物）：

    1. notification 无 response。旧实现对每条入站消息都 mark_inbound()，而 MCP
       生命周期规定 client 在 initialize 响应后**必发** `notifications/initialized`
       （无 id、按规范不产生响应）；`notifications/cancelled` 同理。于是
       last_inbound 恒晚于 last_outbound，任何随后空闲 120s 的健康会话都被判死并
       重连——正是本模块开头「防重连风暴」要避免的 churn。故只有带 id 的 request
       才武装看门狗。
    2. 单对时间戳无法表达多个在途请求。请求 A 挂起、随后 B 到达并回包时，
       last_outbound 被推到 last_inbound 之后，A 的真实停滞被永久「洗掉」。
       MCP SDK 对每条消息 `tg.start_soon(self._handle_message, ...)`（本机 mcp
       1.28.1，lowlevel/server.py），请求确实并发在途，B 先于 A 完成是可达的。
       故按 id 记各自的转发时刻，取**最久未回包**的那条计时。

    解析失败一律 fail-open（不武装）：日志/解析问题不得升级成掀掉会话。这与
    _message_label 同口径——宁可少判一次死，也不误杀健康会话。
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        # id → 转发给 proxy 的时刻。JSON-RPC id 允许 str/int，用原值做键。
        self._pending: dict[object, float] = {}
        self._last_traffic = self._clock()
        # ping-only 流量的 id 集合：云端心跳由 proxy 本地应答，不刷新空闲钟，
        # 回包侧靠这个集合识别 ping 响应（响应无 method，只能按 id 归属）。
        self._ping_ids: set[object] = set()
        # 探针：只跟踪当前 pending 的一只；id 用独特前缀避免与云端整数 id 冲突。
        self._probe_id: str | None = None
        self._probe_sent_at = 0.0
        self._probe_seq = 0

    def mark_inbound(self, message: str | bytes) -> None:
        """记下这条入站消息里所有带 id 的 request；notification 不武装看门狗。

        notifications/cancelled 携带的 params.requestId 会被提前结清：按 MCP 规范
        receiver 不应对已取消的 request 发送响应，该 id 可以视作「已回包」。

        ping-only 消息不刷新空闲钟（见 `_is_ping_only`），但仍登记 id 跟踪在途。
        """
        now = self._clock()
        if _is_ping_only(message):
            self._ping_ids.update(_ping_request_ids(message))
        else:
            self._last_traffic = now
        for request_id in _request_ids(message):
            # 同 id 重复到达（云端重发）以最早时刻为准：那才是真实等待起点。
            self._pending.setdefault(request_id, now)
        for cancel_id in _cancel_ids(message):
            self._pending.pop(cancel_id, None)

    def mark_outbound(self, message: str | bytes) -> None:
        """按 id 结清已回包的请求；纯 ping 回包（按 id 归属）不刷新空闲钟。"""
        ids = _response_ids(message)
        now = self._clock()
        if ids is None:
            # 解析不出 id 的回包（含 batch 解析失败）：无法归属，只能整体结清，
            # 保守回落到旧语义，避免因解析缺口把健康会话判死。
            self._last_traffic = now
            self._pending.clear()
            return
        saw_non_ping = False
        for request_id in ids:
            self._pending.pop(request_id, None)
            if request_id in self._ping_ids:
                self._ping_ids.discard(request_id)
            else:
                saw_non_ping = True
        if saw_non_ping or not ids:
            # 非 ping 回包或 proxy 主动 notification（无 id）：都真过了 proxy→MCP 链路。
            self._last_traffic = now

    def stall_seconds(self) -> float:
        """返回最久未回包的在途请求已等待的秒数；无在途请求时返回 0。"""
        if not self._pending:
            return 0.0
        return self._clock() - min(self._pending.values())

    def idle_seconds(self) -> float:
        """距最近一次入站/回包的秒数；供看门狗决定何时主动探活。"""
        return self._clock() - self._last_traffic

    def build_probe(self) -> str | None:
        """构造一次经 proxy 的真 tools/list 并记为 pending；同一时间只保留一只探针。

        已有 pending 探针时返回 None，禁止覆盖 id——旧回包否则会落到转发路径，
        污染云端没发过的 id。发送与回包都刷新 ``_last_traffic``，避免探活成功后
        仍被看成「空闲超时」而每轮看门狗再打一次。

        必须用 tools/list 而非 ping：mcp-proxy 的 create_proxy_server 未注册
        PingRequest 转发，ping 由 proxy 进程本地应答，探不到 proxy→MCP 链路；
        tools/list 必然发起到本机 /mcp 的 HTTP POST，且服务端 journal 会留落行，
        可作为「探针确实到达」的运维证据。
        """
        if self._probe_id is not None:
            return None
        self._probe_seq += 1
        self._probe_id = f"{_PROBE_ID_PREFIX}{self._probe_seq}"
        now = self._clock()
        self._probe_sent_at = now
        self._last_traffic = now
        return json.dumps(
            {"jsonrpc": "2.0", "id": self._probe_id, "method": "tools/list"},
            separators=(",", ":"),
        )

    def probe_pending_seconds(self) -> float:
        """当前探针已等待的秒数；无 pending 探针返回 0。"""
        if self._probe_id is None:
            return 0.0
        return self._clock() - self._probe_sent_at

    def resolve_probe(self, message: str | bytes) -> bool | None:
        """探针回包本地消费：True=健康 result；False=错误回包（链路已死）；None=非探针。

        凡 ``hutuji-probe-`` 前缀的回包都本地吃掉（含已覆盖/过期 id），禁止转发云端。
        """
        for obj in _iter_jsonrpc_objects(message):
            raw_id = obj.get("id")
            if (
                not isinstance(raw_id, str)
                or not raw_id.startswith(_PROBE_ID_PREFIX)
                or "method" in obj
            ):
                continue
            self._last_traffic = self._clock()
            if raw_id == self._probe_id:
                self._probe_id = None
            result = obj.get("result")
            if "error" in obj:
                return False
            if isinstance(result, dict) and result.get("isError") is True:
                return False
            return True
        return None


async def _write_proxy_stdin(
    process: asyncio.subprocess.Process,
    payload: str,
    stdin_lock: asyncio.Lock | None = None,
) -> None:
    """把一行 JSON-RPC 写入 mcp-proxy stdin；多任务共用同一把锁。"""
    stdin = process.stdin
    if stdin is None:
        raise RuntimeError("mcp-proxy stdin 已关闭，无法写入")

    async def _write() -> None:
        stdin.write(payload.encode("utf-8") + b"\n")
        await stdin.drain()

    if stdin_lock is None:
        await _write()
        return
    async with stdin_lock:
        await _write()


async def _cloud_to_proxy(
    websocket,
    process: asyncio.subprocess.Process,
    activity: _SessionActivity,
    stdin_lock: asyncio.Lock | None = None,
) -> None:
    async for message in websocket:
        logger.info("云端 MCP 请求: %s", _message_label(message))
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        # R13-BRG-F2：JSON 文本里的原始 \r/\n 只可能是 token 间的无意义空白
        # （字符串内的换行必须转义），但 stdio 是行帧——原样写入会把一帧拆成
        # 多行，子进程逐行 model_validate_json 即炸。换成空格对合法 JSON 零影响。
        message = message.replace("\r", " ").replace("\n", " ")
        await _write_proxy_stdin(process, message, stdin_lock)
        # drain 之后才记：这一刻起 proxy 已收到请求，看门狗开始为它计等待时长。
        activity.mark_inbound(message)


def _is_tool_error_result(message: str | bytes) -> bool:
    """判定回包是否含 isError=true 的 CallToolResult（合法工具错误也算）。"""
    for obj in _iter_jsonrpc_objects(message):
        result = obj.get("result")
        if isinstance(result, dict) and result.get("isError") is True:
            return True
    return False


def _has_jsonrpc_error(message: str | bytes) -> bool:
    """判定回包是否含顶层 JSON-RPC error 对象（非 Session terminated 秒回也算）。"""
    for obj in _iter_jsonrpc_objects(message):
        if isinstance(obj.get("error"), dict):
            return True
    return False


def _needs_liveness_probe(message: str | bytes) -> bool:
    """工具 isError 或顶层 JSON-RPC error 都立即探活；Session terminated 由上层先掀。"""
    return _is_tool_error_result(message) or _has_jsonrpc_error(message)


def _tool_error_shape_label(message: str | bytes) -> str:
    """错误形态标签：只记 error.code 与文本长度等粗粒度形态，**不记任何正文片段**。

    用途：给「未被 Session terminated 指纹匹配」的错误留可复现的形态证据
    （302 号事故的四帧原文不可考，指纹只能靠下次复现对上）。
    正文一律不入日志：能力文件名 token 是 22 位 urlsafe，任何明文前缀都可能
    泄露 token/URL（AGENTS.md 脱敏硬约定），`_redact_secret_text` 只覆盖已知
    模式、不是兜底。长度 + code 已足以区分「秒回的死链错误」与「慢工具错误」。
    """
    for obj in _iter_jsonrpc_objects(message):
        error = obj.get("error")
        if isinstance(error, dict):
            return f"error.code={error.get('code')}"
        result = obj.get("result")
        if isinstance(result, dict) and result.get("isError") is True:
            for item in result.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        return f"isError=true text_len={len(text)}"
            return "isError=true text_len=none"
    return "shape=unknown"


async def _proxy_to_cloud(
    process: asyncio.subprocess.Process,
    websocket,
    activity: _SessionActivity,
    stdin_lock: asyncio.Lock | None = None,
) -> None:
    assert process.stdout is not None
    while line := await process.stdout.readline():
        if not line.endswith(b"\n"):
            # EOF 残行（BRG-P3b）：readline 在流结束处会把未完的半条 JSON 原样
            # 返回，转发只会让云端解析炸。丢弃残行、按进程退出处理；只记长度
            # 不记内容（残行可含工具结果正文）。
            logger.warning("mcp-proxy stdout 在行中途 EOF，丢弃 %d 字节残行", len(line))
            break
        message = line.decode("utf-8").rstrip("\r\n")
        # 探针回包只本地消费：云端没发过这个 id，转发会污染对端会话。
        probe_verdict = activity.resolve_probe(message)
        if probe_verdict is not None:
            if probe_verdict:
                logger.info("mcp-proxy 探活通过")
            else:
                raise _McpSessionTerminated(
                    "mcp-proxy 探活返回错误，本机 MCP 会话已死，重建 mcp-proxy"
                )
            continue
        # 按 id 结清已回包的请求（R7-WD-01/02）；notification（如 progress）不结清
        # ——本机 /mcp 不产生进度通知，该形态今日不可达；若未来引入长任务进度流，
        # 须按 progressToken 归属刷新在途计时，否则 >300s 的慢请求会被看门狗误杀
        # （R13-BRG-F1）。放在 send 前，避免 send 慢时把健康的回包误算成停滞。
        activity.mark_outbound(message)
        logger.info("本机 MCP 响应: %s", _message_label(message))
        await websocket.send(message)
        if _is_session_terminated_response(message):
            # 当前调用已经先转发明确失败；这里只通知上层重建本机 proxy。
            # 云端 WSS 保持不变，避免后续请求撞进 Endpoint 短暂不存在窗口。
            raise _McpSessionTerminated(
                "本机 /mcp 会话已被服务端终止（hutuji-mcp 可能已重启），重建 mcp-proxy"
            )
        # isError 工具回包或顶层 JSON-RPC error 后立即探针复核：合法工具错误
        # 的探针会健康返回，不重建；链路死亡产生的合成错误探针必然失败。
        if _needs_liveness_probe(message):
            logger.warning(
                "工具调用返回错误（%s），立即探活本机 MCP 会话",
                _tool_error_shape_label(message),
            )
            probe = activity.build_probe()
            if probe is not None:
                await _write_proxy_stdin(process, probe, stdin_lock)
    raise RuntimeError("mcp-proxy 已退出")


async def _proxy_stderr(process: asyncio.subprocess.Process) -> None:
    assert process.stderr is not None
    while line := await process.stderr.readline():
        logger.info("mcp-proxy: %s", _safe_stderr_line(line))


async def _watchdog(
    process: asyncio.subprocess.Process,
    activity: _SessionActivity,
    stdin_lock: asyncio.Lock | None = None,
) -> None:
    """周期性检查 mcp-proxy 假死，并刷新 bridge 健康心跳。"""
    while True:
        await asyncio.sleep(WATCHDOG_POLL_SECONDS)
        if process.returncode is not None:
            # 进程已退出，交给 _proxy_to_cloud 的 readline 收尾即可，看门狗退场。
            return
        _write_health_state(last_event="heartbeat", last_error=None)
        if activity.stall_seconds() >= WATCHDOG_STALL_SECONDS:
            raise RuntimeError(
                f"mcp-proxy 对在途请求无响应超过 {WATCHDOG_STALL_SECONDS}s（子进程假死）"
            )
        # 探针超时：经 proxy 的 tools/list 长时间无响应 = 链路静默死亡，只重建 proxy。
        if activity.probe_pending_seconds() >= PROBE_TIMEOUT_SECONDS:
            raise _McpSessionTerminated(
                f"mcp-proxy 探活超过 {PROBE_TIMEOUT_SECONDS}s 无响应，重建本机 MCP 会话"
            )
        # 空闲心跳（方案 2）：无在途请求且超过 PROBE_IDLE_SECONDS 无流量时主动
        # 探活一次，让静默死链在下一次真实调用前暴露。
        if (
            activity.probe_pending_seconds() == 0
            and activity.stall_seconds() == 0
            and activity.idle_seconds() >= PROBE_IDLE_SECONDS
        ):
            probe = activity.build_probe()
            if probe is not None:
                await _write_proxy_stdin(process, probe, stdin_lock)
                logger.info("空闲超过 %ds，主动探活本机 MCP 会话", PROBE_IDLE_SECONDS)


async def _run_proxy_session(websocket, config: BridgeConfig) -> None:
    """运行一个本机 proxy；只把 session 失效交给 WSS 外层处理。"""
    # spawn 的 await 同样是取消点：process/tasks 先落位、spawn 包进 try，
    # 且用 shield 挡住取消等 spawn 落定拿到句柄再抛——否则取消恰好打在
    # create_subprocess_exec 中途时 mcp-proxy 子进程成孤儿，无人 terminate。
    process: asyncio.subprocess.Process | None = None
    tasks: set[asyncio.Task] = set()
    try:
        spawn_task = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *proxy_command(config.local_mcp_url),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MCP_PROXY_STREAM_LIMIT,
            )
        )
        try:
            process = await asyncio.shield(spawn_task)
        except asyncio.CancelledError:
            process = await spawn_task
            raise
        _health_state["proxy_connected"] = True
        _health_state["proxy_pid"] = process.pid
        _write_health_state(last_event="proxy_connected", last_error=None)
        _set_bridge_ready(True)
        activity = _SessionActivity()
        stdin_lock = asyncio.Lock()
        tasks = {
            asyncio.create_task(_cloud_to_proxy(websocket, process, activity, stdin_lock)),
            asyncio.create_task(_proxy_to_cloud(process, websocket, activity, stdin_lock)),
            asyncio.create_task(_proxy_stderr(process)),
            asyncio.create_task(_watchdog(process, activity, stdin_lock)),
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
            if process is not None:
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
            await cleanup_task
            raise
        finally:
            _set_bridge_ready(False)
            _write_health_state(
                proxy_connected=False,
                proxy_pid=None,
                last_event="proxy_disconnected",
            )


async def bridge_once(config: BridgeConfig) -> None:
    """建立一次 WSS；本机 session 失效时只重建 proxy。"""
    logger.info("正在连接小智 MCP 接入点（凭据已隐藏）")
    async with websockets.connect(
        config.endpoint,
        open_timeout=15,
        ping_interval=20,
        ping_timeout=20,
        max_size=2 * 1024 * 1024,
    ) as websocket:
        logger.info("小智 MCP WSS 已连接")
        _write_health_state(
            cloud_connected=True,
            last_event="cloud_connected",
            last_error=None,
        )
        try:
            while True:
                try:
                    await _run_proxy_session(websocket, config)
                    return
                except _McpSessionTerminated as exc:
                    _health_state["proxy_session_recoveries"] = int(
                        _health_state.get("proxy_session_recoveries", 0)
                    ) + 1
                    _write_health_state(
                        last_event="proxy_session_recovery",
                        last_error=type(exc).__name__,
                    )
                    logger.warning("本机 MCP 会话失效，只重建 mcp-proxy（WSS 保持）")
                    await asyncio.sleep(INITIAL_BACKOFF_SECONDS)
        finally:
            _write_health_state(
                cloud_connected=False,
                proxy_connected=False,
                proxy_pid=None,
                last_event="cloud_disconnected",
            )


async def run_forever(config: BridgeConfig) -> None:
    # 异常退出后由 systemd 重启时，旧实例留下的标记不得被新进程复用。
    _set_bridge_ready(False)
    _health_state.update(
        {
            "pid": os.getpid(),
            "cloud_connected": False,
            "proxy_connected": False,
            "proxy_pid": None,
            "proxy_session_recoveries": 0,
            "reconnect_count": 0,
            "last_event": "starting",
            "last_error": None,
        }
    )
    _write_health_state()
    backoff = INITIAL_BACKOFF_SECONDS
    while True:
        started = time.monotonic()
        try:
            await bridge_once(config)
            elapsed = time.monotonic() - started
            if elapsed >= HEALTHY_SESSION_SECONDS:
                backoff = INITIAL_BACKOFF_SECONDS
            logger.warning("WSS 会话已结束（存活 %.1f 秒），%d 秒后重连", elapsed, backoff)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _health_state["reconnect_count"] = int(
                _health_state.get("reconnect_count", 0)
            ) + 1
            _write_health_state(
                cloud_connected=False,
                proxy_connected=False,
                proxy_pid=None,
                last_event="reconnect_scheduled",
                last_error=type(exc).__name__,
            )
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
        # MCPDetail-F7：启动失败带 traceback，运维才能定位配置/网络根因。
        logger.error("桥接启动失败: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
