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
import typing
from pathlib import Path

# 允许从仓库根或 mcp-server/ 直接运行：python scripts/e2e_local.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hutuji_mcp import grbl_sim  # noqa: E402
from hutuji_mcp.gcode_gen import GcodeError, svg_to_gcode  # noqa: E402
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
                timeout_s: float = 10.0,
                stderr_log: typing.TextIO | None = None) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if sim.poll() is not None:
            if stderr_log is not None:
                # stderr 已重定向到临时文件（见 main）：文件偏移与子进程共享，
                # 子进程写完后 seek 回 0 读全量，早退诊断靠这条路径。
                stderr_log.seek(0)
                diagnostic = stderr_log.read()
            else:
                _, diagnostic = sim.communicate()
            raise RuntimeError(
                f"grbl_sim 子进程提前退出 rc={sim.returncode}; stderr: {diagnostic.strip() or '(空)'}"
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
    # grbl_sim 对每条 G-code 行 logger.info（grbl_sim.py:185）；stderr=PIPE 而成功路径
    # 无人排空的话，夹具增至数百行即写满 Windows 4KB 匿名管道 → grbl_sim 阻塞不应答 →
    # 真 PASS 误 FAIL（R11-E2E-01）。改重定向到临时文件：文件无背压，写多少都行；
    # 早退诊断从该文件读（_wait_ready 是唯一需要 stderr 文本的场景）。
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as sim_log:
        sim = subprocess.Popen(
            [sys.executable, "-m", "hutuji_mcp.grbl_sim"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=sim_log,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            _wait_ready(sim, HOST, port, stderr_log=sim_log)
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
        except (RuntimeError, TimeoutError, ConnectionError, socket.timeout,
                OSError, GcodeError) as exc:
            # GcodeError<:RuntimeError、socket.timeout≡TimeoutError(3.10+)，显式列出防层级
            # 漂移；ConnectionError 是真实缺口（pipe_sim 在对端关闭/EOF 时抛，见 pipe_sim.py）。
            # 裸 OSError 必须单独列出：WinError 10038（WSAENOTSOCK）等不是 ConnectionError
            # 子类，漏掉会打原始 traceback 而非统一 FAIL: 文案（R11-E2E-03）。
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            return 130
        finally:
            _stop(sim)


if __name__ == "__main__":
    sys.exit(main())
