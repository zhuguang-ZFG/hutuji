"""采集 bringup 故障注入证据。

默认只监听串口并写入 results/ 下的 Markdown 记录；不会向设备发送任何命令。

示例：
  python scripts/bringup_fault_capture.py --s3-port COM9 --grbl-port COM3 --duration 180

8.3 普通画线行断连注入（需先确认正在出图，且 Grbl 串口为 COM3）：
  python scripts/bringup_fault_capture.py --s3-port COM9 --grbl-port COM3 --duration 240 ^
    --inject-grbl-wifi-cycle-at 20 --wifi-off-seconds 8
"""

from __future__ import annotations

import argparse
import datetime as dt
import queue
import sys
import threading
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS = REPO_ROOT / "results"


S3_MARKERS = [
    "写字机连接断开",
    "绘图会话重连",
    "重连后查询换纸状态",
    "Changing=Off",
    "断连恢复完成，从 PSRAM 第 1 行重画",
    "写字机已恢复，正在从头重画",
    "断连废纸处理失败",
    "断连发生在换纸窗口",
]

GRBL_MARKERS = [
    "[MSG:WiFi Off]",
    "[MSG:Client Started]",
    "[MSG:Connected",
    "M30",
    "[PaperStatus]",
    "error:90",
]

# 串口写超时（秒）。注入命令只有十来个字节，115200bps 下写入本是毫秒级；2.0s 已给足
# USB 转串口驱动重试与操作系统缓冲的余量，同时把「写阻塞」压成有界失败——不设上限时
# ser.write 可能长时间挂住，finally 里补排的 [ESP115]ON 既发不出去也不报错。
SERIAL_WRITE_TIMEOUT_S = 2.0

# 线程回收预算（秒）。WiFi 恢复命令是在 finally 里补排的，预算必须够一次有界写走完，
# 否则线程被丢下时 [ESP115]ON 还没写出去（旧值 1.5s 小于写超时，实测会漏掉恢复）。
JOIN_TIMEOUT_S = SERIAL_WRITE_TIMEOUT_S + 1.0

# WiFi 恢复命令。排入队列与发送确认判定共用同一常量，免得两边悄悄漂开。
WIFI_RESTORE_COMMAND = "[ESP115]ON"

# 串口侧故障标记：任一标记出现在日志任一行 → 退出码 1（AGENTS.md 钉死 1=串口报错）。
# 集中一处是为了新增故障文案时不漏改退出码判定——旧实现只认 "ERROR opening/reading"，
# 写失败与「恢复未确认」都以退出码 0 收场，事故对操作员完全隐形。
SERIAL_READ_ERROR = "ERROR opening/reading"
SERIAL_WRITE_ERROR = "ERROR writing to"
RESTORE_UNCONFIRMED_ERROR = "ERROR wifi-restore-unconfirmed"
SERIAL_ERROR_MARKERS = (SERIAL_READ_ERROR, SERIAL_WRITE_ERROR, RESTORE_UNCONFIRMED_ERROR)


def _load_serial_module():
    try:
        import serial  # type: ignore
    except ImportError as exc:
        raise RuntimeError("缺少 pyserial：请先安装 pyserial 后再采集串口") from exc
    return serial


def _timestamp() -> str:
    return dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _serial_worker(
    name: str,
    port: str,
    baud: int,
    stop: threading.Event,
    out: queue.Queue[str],
    commands: queue.Queue[str] | None = None,
) -> None:
    serial = _load_serial_module()
    try:
        with serial.Serial(port, baud, timeout=0.2, write_timeout=SERIAL_WRITE_TIMEOUT_S) as ser:
            out.put(f"{_timestamp()} [{name}] opened {port} @ {baud}")
            pending = bytearray()
            # stop 后仍排空命令队列：Ctrl-C 若发生在 [ESP115]OFF/ON 之间，
            # 主线程会先补排 ON 再置 stop，不能因退出循环把设备留在 WiFi 关态。
            while not stop.is_set() or (commands is not None and not commands.empty()):
                if commands is not None:
                    while True:
                        try:
                            command = commands.get_nowait()
                        except queue.Empty:
                            break
                        payload = (command.rstrip() + "\r\n").encode("ascii")
                        try:
                            ser.write(payload)
                            ser.flush()
                        except Exception as exc:
                            # 与「打开/读取」失败分开报：端口开着、读循环还活着，只是这条
                            # 命令没出去（可能就是 WiFi 恢复），处置是人工补发而非重连串口。
                            out.put(
                                f"{_timestamp()} [inject] {SERIAL_WRITE_ERROR} {port}: {command}: {exc}"
                            )
                            continue
                        # write 与 flush 都返回了才落这行，故它就是「命令已送出」的确认，
                        # WiFi 恢复判定（_restore_failure_line）据此认账。
                        out.put(f"{_timestamp()} [inject] sent to {port}: {command}")
                chunk = ser.read(256)
                if not chunk:
                    continue
                pending.extend(chunk)
                while b"\n" in pending:
                    line, _, pending = pending.partition(b"\n")
                    text = line.rstrip(b"\r").decode("utf-8", errors="replace")
                    out.put(f"{_timestamp()} [{name}] {text}")
            if pending:
                text = pending.decode("utf-8", errors="replace")
                out.put(f"{_timestamp()} [{name}] {text}")
    except Exception as exc:  # pragma: no cover - hardware path
        out.put(f"{_timestamp()} [{name}] {SERIAL_READ_ERROR} {port}: {exc}")


def _drain_events(events: queue.Queue[str], lines: list[str]) -> None:
    while True:
        try:
            lines.append(events.get_nowait())
        except queue.Empty:
            break


def _split_source(line: str) -> tuple[str, str]:
    """把一行日志拆成（来源, 正文）；识别不出来源时来源返回空串。

    所有落行都是 `{时间戳} [{来源}] {正文}`：串口线程用 S3/Grbl，主线程用 capture/inject。
    """
    head, sep, body = line.partition("] ")
    if not sep:
        return "", line
    open_at = head.rfind("[")
    if open_at == -1:
        return "", line
    return head[open_at + 1:], body


def _source_text(lines: list[str], source: str) -> str:
    """把指定来源的行正文拼成一段（已剥掉时间戳与 `[来源] ` 前缀）。

    `[capture]`（含 --note 自由文本）与 `[inject]`（注入命令回显）不是设备来源，
    于此天然被排除：它们都是本工具自己写的字，不能当成串口实收。
    """
    bodies = []
    for line in lines:
        src, body = _split_source(line)
        if src == source:
            bodies.append(body)
    return "\n".join(bodies)


def _summarize(lines: list[str]) -> list[str]:
    # 按来源分组匹配：每行的 `[来源]` 标签是标记归属的唯一真值。旧实现把所有行合成
    # 一段做 substring 匹配，S3 日志复述的 `M30`/`[PaperStatus]` 会在 Grbl 侧判「是」，
    # 而 Grbl 串口一个字节都没收到——这张表是人判联调结论的依据，不能凭空报正。
    s3_text = _source_text(lines, "S3")
    grbl_text = _source_text(lines, "Grbl")
    summary = ["## 标记扫描", ""]
    summary.append("| 来源 | 标记 | 是否出现 |")
    summary.append("|------|------|----------|")
    for marker in S3_MARKERS:
        summary.append(f"| S3 | `{marker}` | {'是' if marker in s3_text else '否'} |")
    for marker in GRBL_MARKERS:
        summary.append(f"| Grbl | `{marker}` | {'是' if marker in grbl_text else '否'} |")
    return summary

def _positive_duration(value: str) -> int:
    """argparse type：duration 必须 > 0，否则报参数错误（argparse 退出码 2）。"""
    n = int(value)
    if n <= 0:
        raise argparse.ArgumentTypeError(f"--duration 必须 > 0，收到 {value}")
    return n


def _nonnegative_seconds(value: str) -> int:
    """argparse type：故障注入时间必须 >= 0，避免负数绕过恢复窗口判据。"""
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError(f"秒数必须 >= 0，收到 {value}")
    return n

def _queue_wifi_restore(
    injected_off: bool,
    injected_on: bool,
    commands: queue.Queue[str] | None,
) -> bool:
    """中断/异常退出时补排 WiFi ON；返回是否已安排恢复。"""
    if injected_off and not injected_on and commands is not None:
        commands.put(WIFI_RESTORE_COMMAND)
        return True
    return injected_on


def _restore_failure_line(restore_queued: bool, lines: list[str]) -> str | None:
    """排过 WiFi 恢复但日志里没有它的发送确认时，返回要追加的显式失败行；否则 None。

    确认只认 `[inject] sent to {port}: [ESP115]ON`——该行在 ser.write 与 ser.flush()
    双双返回后才落，写超时/写异常只会落 ERROR 行。旧实现把 ON 排进队列就算完，实测
    写阻塞 3s 时报告无 ON 行、无 ERROR 行、退出码 0，「写字机被留在 WiFi 关态」隐形。
    """
    if not restore_queued:
        return None
    for line in lines:
        source, body = _split_source(line)
        if source == "inject" and body.endswith(f": {WIFI_RESTORE_COMMAND}"):
            return None
    return (
        f"{_timestamp()} [capture] {RESTORE_UNCONFIRMED_ERROR} "
        f"未见 {WIFI_RESTORE_COMMAND} 的发送确认：写字机可能仍处 WiFi 关态，"
        f"请人工连 Grbl 串口发 {WIFI_RESTORE_COMMAND} 并确认 [MSG:Client Started] 后再继续"
    )


def _exit_code(lines: list[str]) -> int:
    """按 SERIAL_ERROR_MARKERS 判退出码：任一串口侧故障标记出现即 1，否则 0。"""
    return 1 if any(marker in line for line in lines for marker in SERIAL_ERROR_MARKERS) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="采集 bringup 故障注入日志")
    parser.add_argument("--s3-port", help="S3 日志串口，如 COM9")
    parser.add_argument("--grbl-port", help="Grbl 串口，如 COM3")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--duration", type=_positive_duration, default=180, help="采集秒数（必须 > 0）")
    parser.add_argument("--device-id", default="S3-LC-01")
    parser.add_argument("--out", type=Path, help="输出 Markdown 路径")
    parser.add_argument(
        "--inject-grbl-wifi-cycle-at",
        type=_nonnegative_seconds,
        help="第 N 秒向 Grbl 串口发送 [ESP115]OFF，再按 --wifi-off-seconds 恢复 ON",
    )
    parser.add_argument("--wifi-off-seconds", type=_nonnegative_seconds, default=8)
    parser.add_argument("--note", default="", help="写入记录的人工备注")
    args = parser.parse_args()

    if not args.s3_port and not args.grbl_port:
        print("至少指定 --s3-port 或 --grbl-port", file=sys.stderr)
        return 2
    if args.inject_grbl_wifi_cycle_at is not None and not args.grbl_port:
        print("--inject-grbl-wifi-cycle-at 需要同时指定 --grbl-port", file=sys.stderr)
        return 2
    if (
        args.inject_grbl_wifi_cycle_at is not None
        and args.duration <= args.inject_grbl_wifi_cycle_at + args.wifi_off_seconds
    ):
        # 循环守卫是 elapsed < duration（整数 elapsed 最大 duration-1），而
        # [ESP115]ON 要求 elapsed >= inject_at + wifi_off：等于时 ON 同样永不发送，
        # 设备会被留在 WiFi 关态且无任何报错。启动即拒，退出码 2 同缺端口/缺 pyserial 档。
        print(
            "--duration 必须大于 --inject-grbl-wifi-cycle-at + --wifi-off-seconds，"
            "否则 [ESP115]ON 永不发送、设备会被留在 WiFi 关态",
            file=sys.stderr,
        )
        return 2
    try:
        _load_serial_module()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    DEFAULT_RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = args.out or DEFAULT_RESULTS / f"bringup-8.3-fault-capture-{stamp}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    events: queue.Queue[str] = queue.Queue()
    grbl_commands: queue.Queue[str] | None = queue.Queue() if args.grbl_port else None
    stop = threading.Event()
    threads: list[threading.Thread] = []
    if args.s3_port:
        threads.append(threading.Thread(target=_serial_worker, args=("S3", args.s3_port, args.baud, stop, events), daemon=True))
    if args.grbl_port:
        threads.append(
            threading.Thread(
                target=_serial_worker,
                args=("Grbl", args.grbl_port, args.baud, stop, events, grbl_commands),
                daemon=True,
            )
        )
    for thread in threads:
        thread.start()

    lines: list[str] = []
    started = dt.datetime.now()
    events.put(f"{_timestamp()} [capture] start device={args.device_id} duration={args.duration}s")
    if args.note:
        events.put(f"{_timestamp()} [capture] note={args.note}")

    injected_off = False
    injected_on = False
    try:
        while (dt.datetime.now() - started).total_seconds() < args.duration:
            elapsed = int((dt.datetime.now() - started).total_seconds())
            if args.inject_grbl_wifi_cycle_at is not None and not injected_off and elapsed >= args.inject_grbl_wifi_cycle_at:
                injected_off = True
                if grbl_commands is not None:
                    grbl_commands.put("[ESP115]OFF")
            if injected_off and not injected_on and elapsed >= args.inject_grbl_wifi_cycle_at + args.wifi_off_seconds:
                injected_on = True
                if grbl_commands is not None:
                    grbl_commands.put(WIFI_RESTORE_COMMAND)
            _drain_events(events, lines)
            time.sleep(0.1)
    except KeyboardInterrupt:
        events.put(f"{_timestamp()} [capture] interrupted by user")
    finally:
        restore_queued = _queue_wifi_restore(injected_off, injected_on, grbl_commands)
        stop.set()
        for thread in threads:
            # 预算必须容得下一次有界写：恢复命令可能刚在上一行才排进队列。
            thread.join(timeout=JOIN_TIMEOUT_S)
        events.put(f"{_timestamp()} [capture] stop")
        _drain_events(events, lines)
        failure = _restore_failure_line(restore_queued, lines)
        if failure is not None:
            # 恢复没确认就得在报告里显式留痕并让退出码非 0，否则「设备被留在 WiFi 关态」
            # 这个真实事故对操作员完全隐形（报告只剩 OFF 行，看起来一切正常）。
            lines.append(failure)

    header = [
        "# Bringup 8.3 故障注入采集",
        "",
        f"- 设备编号：{args.device_id}",
        f"- 开始时间：{started.isoformat(timespec='seconds')}",
        f"- S3 串口：{args.s3_port or '未采集'}",
        f"- Grbl 串口：{args.grbl_port or '未采集'}",
        f"- 采集时长：{args.duration}s",
        f"- 注入：{('[ESP115]OFF/ON at +' + str(args.inject_grbl_wifi_cycle_at) + 's') if args.inject_grbl_wifi_cycle_at is not None else '无'}",
        "",
    ]
    body = ["## 原始日志", "", "```text", *lines, "```", ""]
    out_path.write_text("\n".join(header + _summarize(lines) + [""] + body), encoding="utf-8")
    print(out_path)
    return _exit_code(lines)


if __name__ == "__main__":
    raise SystemExit(main())
