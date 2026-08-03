"""hutuji ③ 本地闭环测试（无需硬件、无需 LLM API key）。

链路：grbl_sim（假 Grbl，子进程）
  ← pipe_sim（模拟 S3 转发器）← gcode_gen（vpype 转换 + §5 校验）← tests/test_star.svg

断言：转换成功、合同校验通过、全部行收到 ok、行数 > 0。
退出码：0 = PASS，1 = FAIL。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# 允许从仓库根或 mcp-server/ 直接运行：python scripts/e2e_local.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hutuji_mcp import grbl_sim  # noqa: E402
from hutuji_mcp.gcode_gen import svg_to_gcode  # noqa: E402
from hutuji_mcp.pipe_sim import stream_gcode  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEST_SVG = ROOT / "tests" / "test_star.svg"
HOST = "127.0.0.1"


def _sim_port() -> int:
    raw = os.environ.get("GRBL_SIM_PORT", str(grbl_sim.DEFAULT_PORT))
    try:
        port = int(raw)
    except ValueError:
        raise ValueError(f"GRBL_SIM_PORT 非整数: {raw!r}") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"GRBL_SIM_PORT 越界: {port}")
    return port


def _port_in_use(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def _probe_banner(host: str, port: int, timeout_s: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            sock.settimeout(timeout_s)
            with sock.makefile("rb") as stream:
                for _ in range(8):
                    raw = stream.readline()
                    if not raw:
                        return False
                    if b"Grbl 1.3a" in raw and b"for help" in raw:
                        return True
    except OSError:
        return False
    return False


def _wait_ready(sim: subprocess.Popen[str], host: str, port: int,
                timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if sim.poll() is not None:
            _, stderr = sim.communicate()
            raise RuntimeError(
                f"grbl_sim 子进程提前退出 rc={sim.returncode}; stderr: {stderr.strip() or '(空)'}"
            )
        if _probe_banner(host, port):
            time.sleep(0.05)
            if sim.poll() is None:
                return
        time.sleep(0.2)
    raise TimeoutError(f"grbl_sim 端口 {port} 未就绪")


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
    print("== hutuji e2e 本地闭环 ==")
    try:
        port = _sim_port()
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    if _port_in_use(HOST, port):
        print(f"FAIL: 端口 {port} 已被占用；拒绝借用未知或残留的 grbl_sim", file=sys.stderr)
        return 1

    child_env = os.environ.copy()
    child_env["PYTHONUTF8"] = "1"
    child_env["GRBL_SIM_PORT"] = str(port)
    sim = subprocess.Popen(
        [sys.executable, "-m", "hutuji_mcp.grbl_sim"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        _wait_ready(sim, HOST, port)
        print(f"[1/4] grbl_sim 已启动 (127.0.0.1:{port})")

        with tempfile.TemporaryDirectory() as tmp:
            gcode_path = Path(tmp) / "star.gcode"
            stats = svg_to_gcode(TEST_SVG, gcode_path)
            print(f"[2/4] vpype 转换 + §5 校验通过：{stats.lines} 行 / {stats.bytes} 字节")

            result = stream_gcode(gcode_path, HOST, port)
            print(f"[3/4] pipe_sim 流式发送：{result.ok_lines}/{result.total_lines} 行 ok，"
                  f"耗时 {result.elapsed_s:.2f}s")

        ok = result.success and result.total_lines > 0
        if not ok:
            print(f"     失败：第 {result.failed_line} 行 ← {result.error_response}")
        print(f"[4/4] 断言：{'全部 ok' if ok else '存在失败行'}")
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1
    except (RuntimeError, TimeoutError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        _stop(sim)


if __name__ == "__main__":
    sys.exit(main())
