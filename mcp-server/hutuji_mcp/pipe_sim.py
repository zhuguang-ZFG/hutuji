"""PC 版 G-code 转发器：模拟 S3 固件哑管道行为（docs/protocol.md §3）。

TCP 连上（假）Grbl，逐行发送并等 ok：
- 发送前剔除空行与注释（`;` 与 `()`），与 S3 转发约定一致
- 逐行等 "ok"（默认 60s 超时，对齐换纸阻塞期下限）
- 收到 "error:" 立即停止并记录行号
- 容忍异步混入的非应答行（[MSG:...]、[PaperStatus] 等）
"""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

OK_TIMEOUT_S = 60.0


@dataclass
class PipeResult:
    total_lines: int = 0          # 实际下发的 G-code 行数（剔除空行/注释后）
    ok_lines: int = 0
    failed_line: int = 0          # 1-based；0 = 无失败
    error_response: str = ""
    elapsed_s: float = 0.0
    info_lines: list[str] = field(default_factory=list)  # 异步信息行（[MSG:] 等）

    @property
    def success(self) -> bool:
        return self.failed_line == 0 and self.ok_lines == self.total_lines


def _is_gcode_line(line: str) -> bool:
    """剔除空行与注释行（; 开头或整行 () 注释）。"""
    s = line.strip()
    return bool(s) and not s.startswith(";") and not (s.startswith("(") and s.endswith(")"))


def _read_response_line(f) -> str:
    """读一行应答；socket 已设 ok 超时，超时抛 socket.timeout。"""
    data = f.readline()
    if not data:
        raise ConnectionError("对端关闭连接")
    return data.decode(errors="replace").strip()


def stream_gcode(gcode_path: Path, host: str, port: int,
                 ok_timeout_s: float = OK_TIMEOUT_S) -> PipeResult:
    """把 G-code 文件逐行流式发到 Grbl（行协议：发一行等一个 ok）。"""
    result = PipeResult()
    lines = [l.strip() for l in gcode_path.read_text(encoding="utf-8").splitlines()
             if _is_gcode_line(l)]
    result.total_lines = len(lines)
    started = time.monotonic()

    with socket.create_connection((host, port), timeout=10) as sock:
        sock.settimeout(ok_timeout_s)
        f = sock.makefile("rb")
        f._sock = sock  # type: ignore[attr-defined]  # 供 _read_response_line 重算超时

        # 读 banner（"Grbl ... ['$' for help]" 行）
        while True:
            raw = f.readline()
            # EOF 判定只能看 readline 原始返回值：b"" 才是对端关闭；banner
            # 以 \r\n 开头，首行 strip 后是空串但 raw 非空，不得误判为 EOF
            # （否则对正常 banner 抛 ConnectionError）。
            if not raw:
                # socket 超时会抛 socket.timeout 而不是返回 b""，故走到这里
                # 必是对端关闭（EOF）。旧代码 continue 会以 100% CPU 忙等，
                # 须与主循环一样抛 ConnectionError 终止。
                raise ConnectionError("对端在 banner 阶段关闭连接")
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            result.info_lines.append(line)
            if "for help" in line:
                break

        for idx, gcode in enumerate(lines, start=1):
            sock.sendall(gcode.encode() + b"\n")
            # 等本行 ok/error（socket 已设 ok 超时），容忍中间混入的异步信息行
            while True:
                resp = _read_response_line(f)
                if resp == "ok":
                    result.ok_lines += 1
                    break
                if resp.startswith("error"):
                    result.failed_line = idx
                    result.error_response = resp
                    result.elapsed_s = time.monotonic() - started
                    return result
                result.info_lines.append(resp)  # [MSG:...] / [VER:...] 等

    result.elapsed_s = time.monotonic() - started
    return result
