"""一键配置 Grbl 写字机 WiFi（通过串口）。

用法：
    python scripts/grbl_wifi_setup.py --port COM3 --ssid YOUR_SSID --dry-run
    set GRBL_WIFI_PASSWORD=YOUR_PASSWORD
    python scripts/grbl_wifi_setup.py --port COM3 --ssid YOUR_SSID --yes

安全约定（AGENTS.md「凭据禁入库」）：
- 本文件**不得**出现任何真实 SSID / 密码，示例一律用占位符。
- 密码优先从环境变量 `GRBL_WIFI_PASSWORD` 读；`--password` 仅供临时调试，
  它会进入 shell 历史与进程列表，不推荐。

危险性：本脚本会向写字机写 NVS 并发 `[ESP444]RESTART` **重启设备**，
出图期间执行会中断任务。默认需要交互确认；`--dry-run` 只打印将发送的命令、
不开串口，`--yes` 跳过确认（仅供工厂预烧脚本串联）。

量产时建议改用 AP 模式 WebUI 配网（手机浏览器 192.168.0.1），
本脚本仅供开发/工厂预烧阶段使用。
"""
import argparse
import getpass
import os
import sys
import time


def _validate_serial_value(value: str, label: str, *, allow_empty: bool = False) -> None:
    """拒绝会把单条 ESP 配置命令拆成多条的控制字符。"""
    if not value and not allow_empty:
        raise ValueError(f"{label} 不能为空")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError(f"{label} 不得包含控制字符（含换行）")


def _resolve_password(args: argparse.Namespace) -> str:
    """按 --password → 环境变量 GRBL_WIFI_PASSWORD → 交互输入 的顺序取密码。"""
    if args.password:
        print("  [WARN] --password 会进入 shell 历史与进程列表，建议改用 GRBL_WIFI_PASSWORD")
        return args.password
    env = os.environ.get("GRBL_WIFI_PASSWORD")
    if env:
        return env
    if args.dry_run:
        return ""
    return getpass.getpass("WiFi 密码（不回显）：")


def _mask_secret(text: str, secret: str) -> str:
    """把应答回显的密码替换成 ***（固件可能把写入的命令原样回显到串口）。"""
    return text.replace(secret, "***") if secret else text


def classify_response(resp: str) -> tuple[bool, str]:
    """判定一条 ESP 配置命令的应答，返回 (是否成功, 用于展示的关键行)。

    不能用 `"ok" in resp` 子串判定：固件在 verbose 回显下把原命令和状态拼在
    同一行（`Report.cpp:506` 的 `>{line}:` 紧接 `report_status_message`），
    而 SSID/密码本身可能含 "ok"（BookCafe、Tokyo5G），于是失败应答
    `>[ESP100]Tokyo5G:error:3` 会被读成成功，脚本继续发 `[ESP444]RESTART`，
    击穿「任一命令失败即中止、不重启」的约定。

    固件真值（`Report.cpp:210/213/237/239`）：
    成功 = 独立一行 `ok`；失败 = `error:<N>` 或 `error: <文本>`。
    回显形态下二者分别以 `:ok` 结尾、含 `:error:`。
    """
    lines = [line.strip() for line in resp.splitlines() if line.strip()]
    # error 优先：固件可能先 webPrint 若干信息行再给状态，且失败必须压过任何
    # 看起来像成功的回显内容。
    for line in lines:
        low = line.lower()
        if low.startswith("error:") or ":error:" in low:
            return False, line
    for line in lines:
        low = line.lower()
        if low == "ok" or low.endswith(":ok"):
            return True, line
    return False, lines[0] if lines else "(无应答)"


def main() -> int:
    """配置写字机 WiFi；全部命令应答 ok 且确认联网才返回 0。"""
    parser = argparse.ArgumentParser(description="配置 Grbl 写字机 WiFi")
    parser.add_argument("--port", required=True, help="串口（如 COM3）")
    parser.add_argument("--ssid", required=True, help="WiFi SSID")
    parser.add_argument("--password", help="WiFi 密码（不推荐；优先用 GRBL_WIFI_PASSWORD）")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--dry-run", action="store_true", help="只打印将发送的命令，不开串口")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认（注意会重启写字机）")
    args = parser.parse_args()

    password = _resolve_password(args)

    try:
        _validate_serial_value(args.ssid, "SSID")
        if password:
            _validate_serial_value(password, "密码")
    except ValueError as exc:
        print(f"配置输入无效：{exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"[dry-run] 目标串口 {args.port} @ {args.baud}，将依次发送：")
        for cmd, desc in (
            (f"[ESP100]{args.ssid}", "设置 SSID"),
            ("[ESP101]<password>", "设置密码"),
            ("[ESP110]STA", "切换 STA 模式"),
            ("[ESP444]RESTART", "重启写字机"),
        ):
            print(f"  {desc}: {cmd}")
        print("[dry-run] 未打开串口，未发送任何命令。")
        return 0

    if not password:
        print("缺少密码：设置 GRBL_WIFI_PASSWORD 或传 --password", file=sys.stderr)
        return 2

    if not args.yes:
        print(f"即将向 {args.port} 写 WiFi 配置并**重启写字机**（出图中会中断任务）。")
        if input("确认继续？输入 yes 回车：").strip().lower() != "yes":
            print("已取消。")
            return 130

    try:
        import serial
    except ImportError:
        print("需要 pyserial：pip install pyserial", file=sys.stderr)
        return 1

    try:
        s = serial.Serial(args.port, args.baud, timeout=3)
    except (serial.SerialException, OSError) as exc:
        # 坏端口/被占用/无权限：白话报错，退出码 1（与既有串口错误口径一致）。
        print(f"无法打开串口 {args.port}：{exc}", file=sys.stderr)
        return 1

    try:
        time.sleep(0.5)
        failures: list[str] = []

        def send_cmd(cmd: str, desc: str) -> bool:
            """发一条 ESP 命令并判定应答；返回是否 ok。失败由调用方决定是否中止。"""
            s.write(f"{cmd}\r\n".encode())
            time.sleep(1)
            resp = s.read(s.in_waiting or 512).decode("ascii", errors="replace").strip()
            ok, key_line = classify_response(resp)
            # 展示 error 行而非首行：固件可能先回显命令再给状态，只印首行会把
            # 真实失败原因藏掉。设置密码的应答会回显完整命令（含密码），必须脱敏。
            print(f"  [{'OK' if ok else 'WARN'}] {desc}: {_mask_secret(key_line, password)}")
            return ok

        print(f"配置 Grbl WiFi（{args.port}）...")
        print(f"  SSID: {args.ssid}")
        print()

        # 任一配置命令失败即中止，不再发后续命令、不 RESTART：设备停在原配置，
        # 避免「SSID 已改密码没改」之类的半配置状态还被重启。
        for cmd, desc in (
            (f"[ESP100]{args.ssid}", "设置 SSID"),
            (f"[ESP101]{password}", "设置密码"),
            ("[ESP110]STA", "切换 STA 模式"),
        ):
            if not send_cmd(cmd, desc):
                print(f"  {desc} 失败，中止（未重启写字机，设备保持原配置）", file=sys.stderr)
                return 1
        print()
        print("重启写字机...")
        s.write(b"[ESP444]RESTART\r\n")
        time.sleep(5)

        data = s.read(s.in_waiting or 2048).decode("ascii", errors="replace")
        if not ("Connected" in data or "IP:" in data):
            print("等待 WiFi 连接...")
            time.sleep(5)
            data = s.read(s.in_waiting or 2048).decode("ascii", errors="replace")
        connected = "Connected" in data or "IP:" in data

        s.write(b"[ESP420]\r\n")
        time.sleep(2)
        status = s.read(s.in_waiting or 2048).decode("ascii", errors="replace")
        for line in status.splitlines():
            if any(k in line for k in ["WiFi Mode", "Connected", "IP:", "Signal"]):
                print(f"  {line.strip()}")

        if not connected:
            failures.append("WiFi 未确认连上（未见 Connected/IP:）")
        if failures:
            print(f"\n失败：{failures}", file=sys.stderr)
            return 1
    except (serial.SerialException, OSError) as exc:
        # 运行中拔线/串口复位：白话报错，finally 关端口，退出码保持 1。
        print(f"串口通信失败（设备可能已断开）：{exc}", file=sys.stderr)
        return 1
    finally:
        s.close()

    print("\n完成。S3 按决策 ② 连固定 IP 192.168.1.13:23，")
    print("需在路由器上为本机 MAC 绑定该地址（不依赖 mDNS）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
