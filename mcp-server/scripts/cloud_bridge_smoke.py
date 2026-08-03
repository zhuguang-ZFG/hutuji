"""小智云桥接冒烟：临时启动本机服务，验证 initialize → tools/list。

运行前只在当前终端设置 ``MCP_ENDPOINT``。脚本不打印、不落盘该变量，
约 12 秒后自动终止两个子进程。退出码 0 表示云端已成功发现本机工具列表。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORT = 8300


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _port_in_use(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def _wait_port(timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
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


def main() -> int:
    if not os.environ.get("MCP_ENDPOINT"):
        print("FAIL: 请先在当前终端设置 MCP_ENDPOINT", file=sys.stderr)
        return 2
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
        _wait_port()
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
        time.sleep(12)
        _stop(bridge)
        _, bridge_log = bridge.communicate(timeout=2)

        required = ("小智 MCP WSS 已连接", "云端 MCP 请求: initialize", "云端 MCP 请求: tools/list")
        missing = [marker for marker in required if marker not in bridge_log]
        if missing:
            print("FAIL: 桥接未完成工具发现；缺少 " + ", ".join(missing))
            # cloud_bridge 保证隐藏 endpoint 且网络异常只记类型，可安全用于诊断。
            for line in bridge_log.splitlines():
                if "HUTUJI_MCP_BRIDGE" in line:
                    encoding = sys.stdout.encoding or "utf-8"
                    print(line.encode(encoding, errors="replace").decode(encoding))
            return 1
        print("PASS: WSS 鉴权、MCP initialize、tools/list 全部完成")
        return 0
    finally:
        if bridge is not None:
            _stop(bridge)
        _stop(server)


if __name__ == "__main__":
    raise SystemExit(main())
