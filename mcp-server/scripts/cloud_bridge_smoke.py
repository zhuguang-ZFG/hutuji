"""小智云桥接冒烟：临时启动本机服务，验证 initialize → tools/list。

运行前只在当前终端设置 ``MCP_ENDPOINT``（scheme 必须 wss，启动前预校验，
取值绝不回显）。脚本轮询 bridge 日志出现就绪 marker 后短等兜底，随后自动
终止两个子进程。退出码 0 表示云端已成功发现本机工具列表。
"""

from __future__ import annotations

import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORT = 8300
# 就绪轮询上限 = cloud_bridge.open_timeout（cloud_bridge.py:564）的 15s：原固定
# sleep(12) 短于 WSS 连接自身预算，慢接入点 13~15s 握手会被误判 FAIL；轮询放宽到
# 与 open 预算一致后，一次 open 周期内必见「小智 MCP WSS 已连接」或判失败。
BRIDGE_READY_TIMEOUT_S = 15.0
# 就绪后短等兜底：云端 SDK 握手成功后毫秒级发 initialize、随后 tools/list；2s 覆盖
# 桥接进程调度抖动与云端首包延迟，之后仍未齐才判 FAIL。总预算 17s = 15(open) + 2。
BRIDGE_SETTLE_S = 2.0
READY_MARKER = "小智 MCP WSS 已连接"


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _port_in_use(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def _wait_port(process: subprocess.Popen[str] | None = None, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"本机 MCP 服务提前退出（exit={process.returncode}）")
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"本机 MCP 端口 {PORT} 未就绪")


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _validate_endpoint() -> str | None:
    """MCP_ENDPOINT 形态预校验：非空、scheme 必须 wss、必须带主机名。

    失败返回诊断文案（指向环境变量本身），**绝不回显取值**：MCP_ENDPOINT 的
    query/fragment 可能携带云端 token，回显即泄密（脱敏是硬约定）。格式错误在
    bridge 启动前被拒，把「端点本身写错」与「网络/鉴权失败」分开，诊断指向根因。
    """
    raw = os.environ.get("MCP_ENDPOINT", "")
    if not raw:
        return "请先在当前终端设置 MCP_ENDPOINT"
    parsed = urlparse(raw)
    if parsed.scheme != "wss":
        return "MCP_ENDPOINT 的 scheme 必须是 wss（请检查环境变量取值，此处不回显）"
    if not parsed.netloc:
        return "MCP_ENDPOINT 缺少主机名（应为 scheme://host:port 形态，此处不回显）"
    return None


def _wait_bridge_ready(bridge: subprocess.Popen[str],
                       log_lines: queue.Queue[str],
                       timeout_s: float = BRIDGE_READY_TIMEOUT_S,
                       poll_s: float = 0.2) -> list[str]:
    """轮询 bridge 日志找就绪 marker，替代固定 sleep(12)（R11-BR-01）。

    就绪 marker = bridge 连上云端后的日志行「小智 MCP WSS 已连接」
    （cloud_bridge.py:569；该模块对自家日志脱敏，只记类型不记 endpoint）。
    守护线程已把 stderr 逐行放入队列，这里边轮询边消费：marker 出现即提前返回
    （快接入点不等满固定 sleep），慢接入点给足 timeout_s；bridge 提前退出立即收场。
    返回已消费的全部日志行，供后续缺 marker 诊断。"""
    deadline = time.monotonic() + timeout_s
    seen: list[str] = []
    while time.monotonic() < deadline:
        while not log_lines.empty():
            seen.append(log_lines.get_nowait())
        if any(READY_MARKER in line for line in seen):
            return seen
        if bridge.poll() is not None:
            break
        time.sleep(poll_s)
    return seen


def _print_bridge_diag(bridge_log: str) -> None:
    """打印 bridge 侧诊断行（仅带 HUTUJI_MCP_BRIDGE 标签的行）。

    cloud_bridge 保证隐藏 endpoint 且网络异常只记类型，可安全用于诊断；
    Windows 控制台可能是 GBK，逐行按当前编码 errors=replace 转码防 UnicodeEncodeError。
    """
    for line in bridge_log.splitlines():
        if "HUTUJI_MCP_BRIDGE" in line:
            encoding = sys.stdout.encoding or "utf-8"
            print(line.encode(encoding, errors="replace").decode(encoding))


def main() -> int:
    endpoint_error = _validate_endpoint()
    if endpoint_error is not None:
        print(f"FAIL: {endpoint_error}", file=sys.stderr)
        return 2  # 环境配置类错误，与「缺 MCP_ENDPOINT」同退出码语义
    if _port_in_use(HOST, PORT):
        # 8300 是固定生产口：被别的进程占着时 uvicorn 绑定失败但端口探测成功，
        # 冒烟会拿别人的服务当真 PASS。绑定前先拒未知监听者，与 e2e_local 同口径。
        print(f"FAIL: 端口 {PORT} 已被占用；拒绝借用未知或残留的监听者", file=sys.stderr)
        return 1

    flags = _creation_flags()
    child_env = os.environ.copy()
    child_env["PYTHONUTF8"] = "1"
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "hutuji_mcp.server:app",
            "--host",
            HOST,
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env,
        creationflags=flags,
    )
    bridge: subprocess.Popen[str] | None = None
    try:
        try:
            _wait_port(server)
        except (TimeoutError, RuntimeError) as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
        bridge = subprocess.Popen(
            [sys.executable, "-m", "hutuji_mcp.cloud_bridge"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
            creationflags=flags,
        )
        # bridge stderr 是 PIPE：起守护线程逐行排空，既供就绪轮询判 marker，
        # 又避免长时间运行日志写满匿名管道（同 e2e_local 的背压教训）。
        log_lines: queue.Queue[str] = queue.Queue()

        def _drain() -> None:
            for line in iter(bridge.stderr.readline, ""):  # type: ignore[arg-type]
                log_lines.put(line.rstrip("\r\n"))

        drain = threading.Thread(target=_drain, daemon=True, name="bridge-stderr-drain")
        drain.start()
        try:
            seen = _wait_bridge_ready(bridge, log_lines)
            ready = any(READY_MARKER in line for line in seen)
            if ready:
                # 短等兜底：让云端 initialize / tools/list 请求落日志（见 BRIDGE_SETTLE_S）
                time.sleep(BRIDGE_SETTLE_S)
            _stop(bridge)
            drain.join(timeout=3)
            while not log_lines.empty():
                seen.append(log_lines.get_nowait())
            bridge_log = "\n".join(seen)

            if not ready:
                print(
                    f"FAIL: 桥接未在 {BRIDGE_READY_TIMEOUT_S:.0f}s 内连上云端"
                    f"（缺「{READY_MARKER}」marker）",
                    file=sys.stderr,
                )
                _print_bridge_diag(bridge_log)
                return 1

            required = (READY_MARKER, "云端 MCP 请求: initialize", "云端 MCP 请求: tools/list")
            missing = [marker for marker in required if marker not in bridge_log]
            if missing:
                print("FAIL: 桥接未完成工具发现；缺少 " + ", ".join(missing))
                _print_bridge_diag(bridge_log)
                return 1
            print("PASS: WSS 鉴权、MCP initialize、tools/list 全部完成")
            return 0
        finally:
            drain.join(timeout=1)
    finally:
        if bridge is not None:
            _stop(bridge)
        _stop(server)


if __name__ == "__main__":
    raise SystemExit(main())
