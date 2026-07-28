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
        with serial.Serial(port, baud, timeout=0.2) as ser:
            out.put(f"{_timestamp()} [{name}] opened {port} @ {baud}")
            pending = bytearray()
            while not stop.is_set():
                if commands is not None:
                    while True:
                        try:
                            command = commands.get_nowait()
                        except queue.Empty:
                            break
                        payload = (command.rstrip() + "\r\n").encode("ascii")
                        ser.write(payload)
                        ser.flush()
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
        out.put(f"{_timestamp()} [{name}] ERROR opening/reading {port}: {exc}")


def _drain_events(events: queue.Queue[str], lines: list[str]) -> None:
    while True:
        try:
            lines.append(events.get_nowait())
        except queue.Empty:
            break


def _summarize(lines: list[str]) -> list[str]:
    joined = "\n".join(lines)
    summary = ["## 标记扫描", ""]
    summary.append("| 来源 | 标记 | 是否出现 |")
    summary.append("|------|------|----------|")
    for marker in S3_MARKERS:
        summary.append(f"| S3 | `{marker}` | {'是' if marker in joined else '否'} |")
    for marker in GRBL_MARKERS:
        summary.append(f"| Grbl | `{marker}` | {'是' if marker in joined else '否'} |")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="采集 bringup 故障注入日志")
    parser.add_argument("--s3-port", help="S3 日志串口，如 COM9")
    parser.add_argument("--grbl-port", help="Grbl 串口，如 COM3")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--duration", type=int, default=180, help="采集秒数")
    parser.add_argument("--device-id", default="S3-LC-01")
    parser.add_argument("--out", type=Path, help="输出 Markdown 路径")
    parser.add_argument(
        "--inject-grbl-wifi-cycle-at",
        type=int,
        help="第 N 秒向 Grbl 串口发送 [ESP115]OFF，再按 --wifi-off-seconds 恢复 ON",
    )
    parser.add_argument("--wifi-off-seconds", type=int, default=8)
    parser.add_argument("--note", default="", help="写入记录的人工备注")
    args = parser.parse_args()

    if not args.s3_port and not args.grbl_port:
        print("至少指定 --s3-port 或 --grbl-port", file=sys.stderr)
        return 2
    if args.inject_grbl_wifi_cycle_at is not None and not args.grbl_port:
        print("--inject-grbl-wifi-cycle-at 需要同时指定 --grbl-port", file=sys.stderr)
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
                    grbl_commands.put("[ESP115]ON")
            _drain_events(events, lines)
            time.sleep(0.1)
    except KeyboardInterrupt:
        events.put(f"{_timestamp()} [capture] interrupted by user")
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=1.5)
        events.put(f"{_timestamp()} [capture] stop")
        _drain_events(events, lines)

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
    return 1 if any("ERROR opening/reading" in line for line in lines) else 0


if __name__ == "__main__":
    raise SystemExit(main())
