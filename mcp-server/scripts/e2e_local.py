"""hutuji ③ 本地闭环测试（无需硬件、无需 LLM API key）。

链路：grbl_sim（假 Grbl，子进程）
  ← pipe_sim（模拟 S3 转发器）← gcode_gen（vpype 转换 + §5 校验）← tests/test_star.svg

断言：转换成功、合同校验通过、全部行收到 ok、行数 > 0。
退出码：0 = PASS，1 = FAIL。
"""
from __future__ import annotations

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
HOST, PORT = "127.0.0.1", grbl_sim.DEFAULT_PORT


def wait_port(host: str, port: int, timeout_s: float = 10.0) -> None:
    """等 grbl_sim 端口就绪。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"grbl_sim 端口 {port} 未就绪")


def main() -> int:
    print("== hutuji e2e 本地闭环 ==")
    sim = subprocess.Popen(
        [sys.executable, "-m", "hutuji_mcp.grbl_sim"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_port(HOST, PORT)
        print(f"[1/4] grbl_sim 已启动 (127.0.0.1:{PORT})")

        with tempfile.TemporaryDirectory() as tmp:
            gcode_path = Path(tmp) / "star.gcode"
            stats = svg_to_gcode(TEST_SVG, gcode_path)
            print(f"[2/4] vpype 转换 + §5 校验通过：{stats.lines} 行 / {stats.bytes} 字节")

            result = stream_gcode(gcode_path, HOST, PORT)
            print(f"[3/4] pipe_sim 流式发送：{result.ok_lines}/{result.total_lines} 行 ok，"
                  f"耗时 {result.elapsed_s:.2f}s")

        ok = result.success and result.total_lines > 0
        if not ok:
            print(f"     失败：第 {result.failed_line} 行 ← {result.error_response}")
        print(f"[4/4] 断言：{'全部 ok' if ok else '存在失败行'}")
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        sim.terminate()
        sim.wait(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
