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

    s = serial.Serial(args.port, args.baud, timeout=3)
    time.sleep(0.5)
    failures: list[str] = []

    def send_cmd(cmd: str, desc: str) -> str:
        """发一条 ESP 命令并判定应答；未见 ok 记入 failures（决定退出码）。"""
        s.write(f"{cmd}\r\n".encode())
        time.sleep(1)
        resp = s.read(s.in_waiting or 512).decode("ascii", errors="replace").strip()
        ok = "ok" in resp.lower()
        if not ok:
            failures.append(desc)
        head = resp.splitlines()[0] if resp else "(无应答)"
        print(f"  [{'OK' if ok else 'WARN'}] {desc}: {head}")
        return resp

    print(f"配置 Grbl WiFi（{args.port}）...")
    print(f"  SSID: {args.ssid}")
    print()

    send_cmd(f"[ESP100]{args.ssid}", "设置 SSID")
    send_cmd(f"[ESP101]{password}", "设置密码")
    send_cmd("[ESP110]STA", "切换 STA 模式")
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
    s.close()

    if not connected:
        failures.append("WiFi 未确认连上（未见 Connected/IP:）")
    if failures:
        print(f"\n失败：{failures}", file=sys.stderr)
        return 1

    print("\n完成。S3 按决策 ② 连固定 IP 192.168.1.13:23，")
    print("需在路由器上为本机 MAC 绑定该地址（不依赖 mDNS）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
