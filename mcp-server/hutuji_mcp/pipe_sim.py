"""PC 版 G-code 转发器：模拟 S3 固件哑管道行为（docs/protocol.md §3）。

TCP 连上（假）Grbl，逐行发送并等 ok：
- 发送前剔除空行与注释（`;` 与 `()`，含行内注释），与 S3 转发约定一致
- 逐行等 "ok"（默认 60s 超时，对齐换纸阻塞期下限）
- 收到 "error:8" 按 §7 原地重发同一行（上限 8 次），其余 "error:" 立即停止并记录行号
- 容忍异步混入的非应答行（[MSG:...]、[PaperStatus]、'<...>' 实时状态行等）
- 'ALARM:' 前缀行是失败信号（F-SIM-10）：真机运动期间软限位触发异步发
  'ALARM:2'（Report.cpp:205-207 report_alarm_message 发 CLIENT_ALL），此后
  ok 永不返回；pipe_sim 识别后结构化失败并携带该 ALARM 行内容，不再挂满
  60s 超时变成非结构化失败。'<...>' 状态行不是失败信号（ok 仍会返回，
  Report.cpp 状态报告与行应答并发），继续容忍吞入 info_lines。
- error:8 行分类已与真机同构（R13-SIM-07 起）：grbl_sim 与真机
  protocol_line_is_motion_gcode_g0_g3 都按「行首 G 字首位数 0-3」判定。

建模边界（R14 登记，勿当缺陷重报）：
- 不建模窗口化流控：真机 S3 在途 ≤512B 字节窗口（无行数上限，R15 勘误——
  此前写「最多 4 行」无固件依据），本仿真逐行等 ok——逐行是
  窗口化的严格子集，能过逐行的文件在窗口化下行为由 S3 固件 host 测试与实机
  取证兜底，e2e 管的是「文件能否按 §3/§7 语义流完」。
- 不发 M30/G92 Z0 旁路行、不查 fresh Idle：那是 S3 固件职责（§4/§5），
  本仿真只覆盖「下载文件逐行下发」这一段；grbl_sim 对 '?' 恒回 Idle 状态行
  （无 status_report_seq 概念），S3 的 ConfirmInFlightDoneByStatus 路径不在
  e2e 面内。
"""
from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

OK_TIMEOUT_S = 60.0
# §7 error:8 重发契约（与 S3 hutuji_job.cc 同值）：换纸期运动行被推迟且该行已被
# empty_line() 丢弃，S3 必须原地重发同一行；其余 error 立即整单终止。
DEFERRED_RETRY_DELAY_S = 2.0
DEFERRED_MAX_RETRIES = 8
_PAREN_COMMENT_RE = re.compile(r"\([^()]*\)")


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


def _clean_gcode_line(line: str) -> str:
    """按 §3 剔除注释：`;` 及其后内容、单层 `(...)` 注释组。返回清洗后的行；
    空行/纯注释返回空串（由调用方过滤）。§5 校验器拒绝含 `(` 的行，合法产物
    不会走到括号分支；此处仅为与 S3 转发行为对齐的防御建模。"""
    s = line.strip()
    if ";" in s:
        s = s.split(";", 1)[0]
    s = _PAREN_COMMENT_RE.sub("", s).strip()
    return s


def _is_error8(resp: str) -> bool:
    """error:8 = 换纸期运动行被推迟（§7）。两种线上形态都认（§3）：
    数字 `error:8` 与 verbose `error: Command requires idle state`。"""
    return resp == "error:8" or resp.startswith("error: Command requires idle state")


def _read_response_line(f) -> str:
    """读一行应答；socket 已设 ok 超时，超时抛 socket.timeout。"""
    data = f.readline()
    if not data:
        raise ConnectionError("对端关闭连接")
    return data.decode(errors="replace").strip()


def stream_gcode(gcode_path: Path, host: str, port: int,
                 ok_timeout_s: float = OK_TIMEOUT_S,
                 deferred_retry_delay_s: float = DEFERRED_RETRY_DELAY_S) -> PipeResult:
    """把 G-code 文件逐行流式发到 Grbl（行协议：发一行等一个 ok；error:8 重发）。"""
    result = PipeResult()
    lines = [
        cleaned
        for l in gcode_path.read_text(encoding="utf-8").splitlines()
        if (cleaned := _clean_gcode_line(l))
    ]
    result.total_lines = len(lines)
    started = time.monotonic()

    with socket.create_connection((host, port), timeout=10) as sock:
        sock.settimeout(ok_timeout_s)
        f = sock.makefile("rb")
        # 读行超时由 socket 决定（上一行 settimeout 已设），makefile 底层即同一
        # socket：_read_response_line 的 f.readline() 直接沿用，超时抛
        # socket.timeout。下面的重绑只是防御性冗余，并非「供 _read_response_line
        # 重算超时」——它从不重算（MCPDetail-F11）。
        f._sock = sock  # type: ignore[attr-defined]

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
            retries = 0
            while True:  # error:8 原地重发同一行（§7），上限 DEFERRED_MAX_RETRIES
                sock.sendall(gcode.encode() + b"\n")
                resp = ""
                # 等本行 ok/error（socket 已设 ok 超时），容忍中间混入的异步信息行
                while True:
                    resp = _read_response_line(f)
                    if resp == "ok":
                        result.ok_lines += 1
                        break
                    if _is_error8(resp):
                        break
                    if resp.startswith("error"):
                        result.failed_line = idx
                        result.error_response = resp
                        result.elapsed_s = time.monotonic() - started
                        return result
                    # F-SIM-10：真机运动期间软限位会异步发 'ALARM:2'
                    # （Report.cpp:205-207 report_alarm_message 发 CLIENT_ALL），
                    # 此后 ok 永不返回——把 ALARM 行吞进 info_lines 继续等会
                    # 挂满 ok_timeout 变成非结构化超时。ALARM: 是失败信号，
                    # 必须结构化终止并携带该行内容。
                    if resp.startswith("ALARM:"):
                        result.failed_line = idx
                        result.error_response = resp
                        result.elapsed_s = time.monotonic() - started
                        return result
                    # '<...>' 实时状态行（'?' 触发或自动报告）不是失败信号，
                    # ok 仍会返回（F-SIM-10 注，Report.cpp 状态报告与应答并发）
                    result.info_lines.append(resp)  # [MSG:...] / [VER:...] 等
                if resp == "ok":
                    break
                retries += 1
                if retries > DEFERRED_MAX_RETRIES:
                    result.failed_line = idx
                    result.error_response = resp
                    result.elapsed_s = time.monotonic() - started
                    return result
                time.sleep(deferred_retry_delay_s)

    result.elapsed_s = time.monotonic() - started
    return result
